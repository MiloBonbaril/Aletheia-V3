"""Sampling of the host, of each managed service, and of the NATS bus.

The bus figures come from the monitoring endpoint of NATS (`nats -m 8222`), not
from a subscription. Thus no payload passes through the daemon: no audio frame,
and no conversation content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import psutil

NATS_MONITOR = "http://localhost:8222"
DOCKER_POLL_EVERY = 5  # ticks


async def _run(*cmd: str, timeout: float = 5.0, cwd=None) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except FileNotFoundError:
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return ""
    return out.decode("utf-8", "replace")


async def read_gpu() -> dict:
    """Host-wide GPU figures. Empty when there is no NVIDIA driver."""
    out = await _run(
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    )
    line = out.strip().splitlines()
    if not line:
        return {}
    parts = [p.strip() for p in line[0].split(",")]
    if len(parts) < 5:
        return {}
    try:
        return {
            "name": parts[0],
            "util": float(parts[1]),
            "vram_used": float(parts[2]) * 1024 * 1024,
            "vram_total": float(parts[3]) * 1024 * 1024,
            "temp": float(parts[4]),
        }
    except ValueError:
        return {}


async def read_vram_by_pid() -> dict[int, int]:
    out = await _run(
        "nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"
    )
    result: dict[int, int] = {}
    for raw in out.strip().splitlines():
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 2:
            continue
        try:
            result[int(parts[0])] = int(float(parts[1]) * 1024 * 1024)
        except ValueError:
            continue
    return result


class BusMonitor:
    """Reads /varz and /connz. Counts only, never payloads."""

    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._last: tuple[float, int] | None = None
        self.state = {"up": False, "msgs_per_min": 0.0, "connections": 0, "clients": []}

    async def sample(self) -> dict:
        session = self._session_factory()
        try:
            async with session.get(f"{NATS_MONITOR}/varz", timeout=2) as resp:
                varz = await resp.json(content_type=None)
            async with session.get(f"{NATS_MONITOR}/connz", timeout=2) as resp:
                connz = await resp.json(content_type=None)
        except Exception:
            self._last = None
            self.state = {"up": False, "msgs_per_min": 0.0, "connections": 0, "clients": []}
            return self.state

        now = time.monotonic()
        total = int(varz.get("in_msgs", 0)) + int(varz.get("out_msgs", 0))
        rate = 0.0
        if self._last is not None:
            elapsed = now - self._last[0]
            if elapsed > 0:
                rate = max(0.0, (total - self._last[1]) / elapsed * 60.0)
        self._last = (now, total)

        clients = []
        for conn in connz.get("connections", []):
            clients.append(
                {
                    "name": conn.get("name") or "(anonyme)",
                    "in": conn.get("in_msgs", 0),
                    "out": conn.get("out_msgs", 0),
                }
            )
        self.state = {
            "up": True,
            "msgs_per_min": round(rate, 1),
            "connections": len(clients),
            "clients": clients,
            "uptime": varz.get("uptime", ""),
            "version": varz.get("version", ""),
        }
        return self.state


class Sampler:
    """Fills the live figures of the host and of every running service."""

    def __init__(self, supervisor, bus: BusMonitor, interval: float = 2.0):
        self.supervisor = supervisor
        self.bus = bus
        self.interval = interval
        self.host: dict = {}
        self._procs: dict[int, psutil.Process] = {}
        self._tick = 0

    def _tree(self, pid: int) -> list[psutil.Process]:
        """The process and its descendants. `cargo run` and shell wrappers add levels."""
        root = self._procs.get(pid)
        if root is None or not root.is_running():
            try:
                root = psutil.Process(pid)
            except psutil.Error:
                self._procs.pop(pid, None)
                return []
            root.cpu_percent(None)  # prime the delta
            self._procs[pid] = root
        try:
            return [root, *root.children(recursive=True)]
        except psutil.Error:
            return [root]

    async def _sample_host(self) -> None:
        mem = psutil.virtual_memory()
        gpu = await read_gpu()
        self.host = {
            "cpu": psutil.cpu_percent(None),
            "ram_used": mem.total - mem.available,
            "ram_total": mem.total,
            "gpu": gpu,
        }

    async def _sample_services(self) -> None:
        vram = await read_vram_by_pid()
        for service in self.supervisor.services.values():
            if service.pid is None or service.status != "running":
                service.cpu, service.rss, service.vram = 0.0, 0, 0
                service.history.append({"cpu": 0.0, "rss": 0})
                continue
            cpu = 0.0
            rss = 0
            used_vram = 0
            for proc in self._tree(service.pid):
                try:
                    cpu += proc.cpu_percent(None)
                    rss += proc.memory_info().rss
                    used_vram += vram.get(proc.pid, 0)
                except psutil.Error:
                    continue
            service.cpu = round(cpu, 1)
            service.rss = rss
            service.vram = used_vram
            service.history.append({"cpu": service.cpu, "rss": rss})

    async def _poll_docker(self) -> None:
        """A stack can be stopped from outside the console. Re-read the truth."""
        for service in self.supervisor.services.values():
            if service.entry.kind != "docker" or service.status in ("starting", "stopping"):
                continue
            out = await _run(
                "docker", "compose", "ps", "--format", "json",
                timeout=8.0, cwd=service.entry.cwd,
            )
            running = 0
            for chunk in out.strip().splitlines():
                with contextlib.suppress(json.JSONDecodeError):
                    entry = json.loads(chunk)
                    rows = entry if isinstance(entry, list) else [entry]
                    running += sum(1 for r in rows if r.get("State") == "running")
            if running and service.status != "running":
                service.status = "running"
                service.started_at = service.started_at or time.monotonic()
            elif not running and service.status == "running":
                service.status = "stopped"
                service.started_at = None

    async def tick(self) -> None:
        self._tick += 1
        await self._sample_host()
        await self._sample_services()
        await self.bus.sample()
        if self._tick % DOCKER_POLL_EVERY == 1:
            await self._poll_docker()
