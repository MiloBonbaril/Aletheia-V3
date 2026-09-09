"""Process ownership for the terminal daemon.

The daemon is the parent of each managed service. It starts each one in its own
process group, and it kills every group when it stops. Thus a dead daemon leaves
no orphan, and it frees the VRAM.

See docs/adr/0003-terminal-superviseur-de-processus-local.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import pty
import re
import shlex
import signal
import struct
import fcntl
import termios
import time
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

LOG_LINES = 2000
HISTORY_POINTS = 60
# A PTY merges stdout and stderr, and it keeps the colours. Textual needs it.
PTY_COLS, PTY_ROWS = 200, 50
STOP_GRACE_SECONDS = 8.0

_PR_SET_PDEATHSIG = 1
try:
    _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:  # not Linux: the explicit shutdown of the daemon stays the only guard
    _LIBC = None


def _die_with_the_daemon() -> None:
    """Ask the kernel to SIGKILL this child when the daemon dies.

    `stop()` already kills the group on a clean exit. This covers the case that
    no Python code can cover: a SIGKILL on the daemon. Without it, a killed
    daemon leaves llama-server and io_voix holding the VRAM.
    """
    if _LIBC is not None:
        _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()][AB0-2]"
    r"|\x1b[=><]"
)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text).replace("\x0f", "")


def guess_level(line: str) -> str:
    """Find a log level in a line. The services have no common log format."""
    upper = line[:200].upper()
    if any(k in upper for k in ("ERROR", " ERR ", "CRITICAL", "FATAL", "TRACEBACK", "PANIC")):
        return "err"
    if any(k in upper for k in ("WARN", "WARNING")):
        return "warn"
    return "info"


@dataclass
class Entry:
    name: str
    group: str
    kind: str  # "process" or "docker"
    cwd: Path
    cmd: list[str] = field(default_factory=list)
    log_file: str | None = None
    rebuild: list[str] | None = None
    stale_src: str | None = None
    ready_tcp: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class ConfigFile:
    """A text file that the console can edit.

    The daemon writes no path that this list does not name, thus the API takes a
    `name` and never a path from the browser.
    """

    name: str
    path: Path
    service: str | None = None  # the service to restart so that the edit applies
    note: str | None = None


@dataclass
class Manifest:
    entries: list[Entry]
    profiles: dict[str, list[str]]
    config_files: list[ConfigFile] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, root: Path) -> "Manifest":
        raw = tomllib.loads(path.read_text())
        # `{python}` keeps the manifest free of an absolute path, and an absolute
        # interpreter keeps Python 3.14 from warning about sys.prefix. Do not
        # resolve it: venv/bin/python is a symlink to the system interpreter, and
        # following it would drop the site-packages of the venv.
        interpreter = str(Path(root).absolute() / "venv" / "bin" / "python")

        def subst(argv):
            return [a.replace("{python}", interpreter) for a in argv]
        entries = [
            Entry(
                name=s["name"],
                group=s.get("group", "Autres"),
                kind=s.get("kind", "process"),
                cwd=(root / s.get("cwd", ".")).resolve(),
                cmd=subst(s.get("cmd", [])),
                log_file=s.get("log_file"),
                rebuild=subst(s["rebuild"]) if s.get("rebuild") else None,
                stale_src=s.get("stale_src"),
                ready_tcp=s.get("ready_tcp"),
                env={k: str(v) for k, v in s.get("env", {}).items()},
                depends_on=s.get("depends_on", []),
                note=s.get("note"),
            )
            for s in raw.get("service", [])
        ]
        names = [e.name for e in entries]
        if len(names) != len(set(names)):
            raise ValueError("the manifest has two services with the same name")
        profiles = {k: v["services"] for k, v in raw.get("profile", {}).items()}
        for pname, members in profiles.items():
            unknown = set(members) - set(names)
            if unknown:
                raise ValueError(f"profile {pname} names unknown services: {sorted(unknown)}")

        inside = root.resolve()
        config_files = []
        for c in raw.get("config_file", []):
            target = (root / c["path"]).resolve()
            if not target.is_relative_to(inside):
                raise ValueError(f"config file {c['name']} is outside the repository")
            service = c.get("service")
            if service and service not in names:
                raise ValueError(f"config file {c['name']} names an unknown service: {service}")
            config_files.append(
                ConfigFile(name=c["name"], path=target, service=service, note=c.get("note"))
            )
        labels = [c.name for c in config_files]
        if len(labels) != len(set(labels)):
            raise ValueError("the manifest has two config files with the same name")
        return cls(entries, profiles, config_files)


class Service:
    """One managed service: its process, its state and its log buffer."""

    def __init__(self, entry: Entry, on_log):
        self.entry = entry
        self._on_log = on_log
        self.status = "stopped"  # stopped starting running stopping crashed building
        self.proc: asyncio.subprocess.Process | None = None
        self.pid: int | None = None
        self.started_at: float | None = None
        self.exit_code: int | None = None
        self.restarts = 0
        self.logs: deque[dict] = deque(maxlen=LOG_LINES)
        self.history: deque[dict] = deque(maxlen=HISTORY_POINTS)
        self.cpu = 0.0
        self.rss = 0
        self.vram = 0
        self._tasks: set[asyncio.Task] = set()
        self._seq = 0

    # ---------- logs ----------

    def push(self, text: str, level: str | None = None) -> None:
        self._seq += 1
        line = {
            "seq": self._seq,
            "t": time.time(),
            "level": level or guess_level(text),
            "text": text,
        }
        self.logs.append(line)
        self._on_log(self.entry.name, line)

    def clear_logs(self) -> None:
        self.logs.clear()

    # ---------- lifecycle ----------

    @property
    def uptime(self) -> float | None:
        return None if self.started_at is None else time.monotonic() - self.started_at

    def _track(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(self) -> None:
        if self.status in ("running", "starting", "building"):
            return
        if self.restarts or self.logs:
            self.push("── redémarrage ──", "info")
        self.status = "starting"
        self.exit_code = None
        try:
            if self.entry.kind == "docker":
                await self._start_docker()
            else:
                await self._start_process()
        except Exception as exc:  # a bad path or a missing binary must not kill the daemon
            self.status = "crashed"
            self.exit_code = -1
            self.push(f"impossible de démarrer : {exc}", "err")

    async def _start_process(self) -> None:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", PTY_ROWS, PTY_COLS, 0, 0))
        env = dict(os.environ, TERM="xterm-256color", PYTHONUNBUFFERED="1", **self.entry.env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.entry.cmd,
                cwd=self.entry.cwd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,  # own process group, so we can kill the whole tree
                preexec_fn=_die_with_the_daemon,
                env=env,
            )
        finally:
            os.close(slave)
        self.proc = proc
        self.pid = proc.pid
        self.started_at = time.monotonic()
        self.status = "running"
        self.push(f"$ {shlex.join(self.entry.cmd)}", "info")
        # The PTY must be drained even when the readable logs come from a file,
        # or the child blocks once the terminal buffer is full.
        self._track(self._pump_pty(master, discard=bool(self.entry.log_file)))
        if self.entry.log_file:
            self._track(self._tail_file(self.entry.cwd / self.entry.log_file))
        self._track(self._reap())

    async def _pump_pty(self, master_fd: int, discard: bool) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=1 << 20)
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(master_fd, "rb", 0)
        )
        pending = ""
        try:
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                if discard:
                    continue
                pending += chunk.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
                *lines, pending = pending.split("\n")
                for raw in lines:
                    clean = strip_ansi(raw).rstrip()
                    if clean:
                        self.push(clean)
        except OSError:
            pass  # EIO on Linux once the last slave closes: the child has exited
        finally:
            transport.close()
            if not discard and pending.strip():
                self.push(strip_ansi(pending).rstrip())

    async def _tail_file(self, path: Path) -> None:
        for _ in range(50):  # the service may not have created it yet
            if path.exists():
                break
            await asyncio.sleep(0.2)
        else:
            self.push(f"fichier de log absent : {path}", "warn")
            return
        with path.open("r", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while self.status in ("running", "starting"):
                line = handle.readline()
                if not line:
                    await asyncio.sleep(0.25)
                    continue
                clean = strip_ansi(line).rstrip()
                if clean:
                    self.push(clean)

    async def _reap(self) -> None:
        assert self.proc is not None
        code = await self.proc.wait()
        self.exit_code = code
        was_stopping = self.status == "stopping"
        self.status = "stopped" if (was_stopping or code == 0) else "crashed"
        self.pid = None
        self.started_at = None
        self.cpu, self.rss, self.vram = 0.0, 0, 0
        if self.status == "crashed":
            self.push(f"processus terminé, code de sortie {code}", "err")
        else:
            self.push(f"processus arrêté (code {code})", "info")

    async def _start_docker(self) -> None:
        ok = await self._run_docker(["up", "-d", "--wait"])
        if not ok:
            self.status = "crashed"
            self.exit_code = -1
            return
        self.started_at = time.monotonic()
        self.status = "running"
        self._track(self._follow_docker_logs())

    async def _run_docker(self, args: list[str]) -> bool:
        cmd = ["docker", "compose", *args]
        self.push(f"$ {shlex.join(cmd)}", "info")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.entry.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        for raw in out.decode("utf-8", "replace").splitlines():
            if raw.strip():
                self.push(strip_ansi(raw).rstrip())
        return proc.returncode == 0

    async def _follow_docker_logs(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "logs", "-f", "--tail=100", "--no-color",
            cwd=self.entry.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._log_follower = proc
        assert proc.stdout is not None
        try:
            async for raw in proc.stdout:
                clean = strip_ansi(raw.decode("utf-8", "replace")).rstrip()
                if clean:
                    self.push(clean)
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def stop(self) -> None:
        if self.entry.kind == "docker":
            if self.status == "stopped":
                return
            self.status = "stopping"
            follower = getattr(self, "_log_follower", None)
            if follower and follower.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    follower.kill()
            await self._run_docker(["down"])
            self.status = "stopped"
            self.started_at = None
            return

        proc = self.proc
        if proc is None or proc.returncode is not None:
            self.status = "stopped"
            return
        self.status = "stopping"
        self.kill_group(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            self.push(f"pas d'arrêt après {STOP_GRACE_SECONDS:.0f} s, SIGKILL", "warn")
            self.kill_group(signal.SIGKILL)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 3.0)

    def kill_group(self, sig: int) -> None:
        """Signal the whole process group. Used at shutdown, from a sync context too."""
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), sig)

    async def restart(self) -> None:
        await self.stop()
        self.restarts += 1
        await self.start()

    async def rebuild(self) -> bool:
        if not self.entry.rebuild:
            return False
        was_running = self.status == "running"
        await self.stop()
        self.status = "building"
        self.push(f"$ {shlex.join(self.entry.rebuild)}", "info")
        proc = await asyncio.create_subprocess_exec(
            *self.entry.rebuild,
            cwd=self.entry.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            clean = strip_ansi(raw.decode("utf-8", "replace")).rstrip()
            if clean:
                self.push(clean)
        await proc.wait()
        ok = proc.returncode == 0
        self.push(f"compilation terminée, code {proc.returncode}", "info" if ok else "err")
        self.status = "stopped"
        if ok and was_running:
            await self.start()
        return ok

    # ---------- staleness ----------

    def is_stale(self) -> bool:
        """True when a source file is newer than the built binary."""
        if not self.entry.stale_src or not self.entry.cmd:
            return False
        binary = self.entry.cwd / self.entry.cmd[0]
        src = self.entry.cwd / self.entry.stale_src
        if not binary.exists() or not src.is_dir():
            return False
        built = binary.stat().st_mtime
        return any(f.stat().st_mtime > built for f in src.rglob("*") if f.is_file())


async def wait_tcp(target: str, timeout: float = 90.0) -> bool:
    """Wait until a host:port accepts a connection."""
    host, _, port = target.rpartition(":")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.open_connection(host, int(port))
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.5)
    return False


class Supervisor:
    def __init__(self, manifest: Manifest, on_log):
        self.manifest = manifest
        self.services: dict[str, Service] = {
            e.name: Service(e, on_log) for e in manifest.entries
        }
        self.order = [e.name for e in manifest.entries]

    def get(self, name: str) -> Service:
        if name not in self.services:
            raise KeyError(name)
        return self.services[name]

    async def start_sequence(self, names: list[str]) -> None:
        """Start services in manifest order, waiting for the ones that declare it."""
        for name in [n for n in self.order if n in set(names)]:
            service = self.services[name]
            if service.status == "running":
                continue
            await service.start()
            if service.entry.ready_tcp and service.status == "running":
                service.push(f"attente de {service.entry.ready_tcp}…", "info")
                if not await wait_tcp(service.entry.ready_tcp):
                    service.push("le port ne répond pas, on continue quand même", "warn")

    async def stop_sequence(self, names: list[str]) -> None:
        """Stop in reverse manifest order."""
        wanted = set(names)
        for name in reversed([n for n in self.order if n in wanted]):
            await self.services[name].stop()

    def kill_all_now(self) -> None:
        """Synchronous last resort, called when the daemon exits."""
        for service in self.services.values():
            service.kill_group(signal.SIGKILL)
