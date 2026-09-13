"""The terminal daemon: it owns the services and it serves the console.

Start it from this directory:

    ../../venv/bin/python main.py

Then open http://127.0.0.1:7420.

The daemon listens on the loopback interface only. It runs arbitrary commands
from `services.toml`, thus it must never be reachable from the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.web_runner import GracefulExit

from chat import Chat
from issues import Unavailable
from issues import board as issues_board
from issues import fetch as issues_fetch
from metrics import BusMonitor, Sampler
from supervisor import Manifest, Supervisor
from tickets import Board, Conflict

VERSION = "0.1.0"
HOST, PORT = "127.0.0.1", 7420
SAMPLE_INTERVAL = 2.0

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WEB = HERE / "web"


class Daemon:
    def __init__(self) -> None:
        self.manifest = Manifest.load(HERE / "services.toml", ROOT)
        self.supervisor = Supervisor(self.manifest, self._on_log)
        self.subscribers: set[asyncio.Queue] = set()
        self.session: aiohttp.ClientSession | None = None
        self.bus = BusMonitor(lambda: self.session)
        self.sampler = Sampler(self.supervisor, self.bus, SAMPLE_INTERVAL)
        self.chat = Chat(self._on_chat)
        self.board = Board(ROOT / "tickets")
        self.started_at = time.time()

    # ---------- broadcast ----------

    def _on_log(self, service_name: str, line: dict) -> None:
        self._broadcast({"type": "log", "service": service_name, "line": line})

    def _on_chat(self, message: dict) -> None:
        # The whole message goes out at each fragment, not the difference. A
        # browser that misses one event catches up at the next one.
        self._broadcast({"type": "chat", "message": message})

    def _broadcast(self, payload: dict) -> None:
        message = json.dumps(payload)
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A slow browser must never block the supervisor. It re-syncs on
                # the next state snapshot.
                pass

    # ---------- snapshot ----------

    def snapshot(self) -> dict:
        services = []
        for name in self.supervisor.order:
            service = self.supervisor.get(name)
            entry = service.entry
            services.append(
                {
                    "name": name,
                    "group": entry.group,
                    "kind": entry.kind,
                    "status": service.status,
                    "pid": service.pid,
                    "uptime": service.uptime,
                    "cpu": service.cpu,
                    "rss": service.rss,
                    "vram": service.vram,
                    "restarts": service.restarts,
                    "exit_code": service.exit_code,
                    "depends_on": entry.depends_on,
                    "note": entry.note,
                    "can_rebuild": bool(entry.rebuild),
                    "stale": service.is_stale(),
                    "history": list(service.history),
                }
            )
        return {
            "type": "state",
            "daemon": {"version": VERSION, "uptime": time.time() - self.started_at},
            "host": self.sampler.host,
            "bus": self.bus.state,
            "chat": {"connected": self.chat.connected},
            "profiles": self.manifest.profiles,
            "services": services,
        }

    # ---------- loops ----------

    async def sample_loop(self) -> None:
        while True:
            try:
                await self.sampler.tick()
                self._broadcast(self.snapshot())
                # The tickets do not move at the rhythm of the services: they go
                # out on their own event, and only when the folder changed.
                if self.board.changed():
                    self._broadcast(self.board.state())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a sampling failure must not stop the daemon
                print(f"[terminal] sampling error: {exc!r}")
            await asyncio.sleep(SAMPLE_INTERVAL)


# ---------- routes ----------

routes = web.RouteTableDef()


def daemon(request: web.Request) -> Daemon:
    return request.app["daemon"]


@routes.get("/")
async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "index.html")


@routes.get("/api/state")
async def api_state(request: web.Request) -> web.Response:
    return web.json_response(daemon(request).snapshot())


@routes.get("/api/services/{name}/logs")
async def api_logs(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    try:
        service = daemon(request).supervisor.get(name)
    except KeyError:
        raise web.HTTPNotFound(text=f"service inconnu : {name}")
    return web.json_response({"service": name, "lines": list(service.logs)})


@routes.get("/api/stream")
async def api_stream(request: web.Request) -> web.StreamResponse:
    app_daemon = daemon(request)
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)
    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    app_daemon.subscribers.add(queue)
    try:
        await response.write(b": ok\n\n")
        await response.write(f"data: {json.dumps(app_daemon.snapshot())}\n\n".encode())
        await response.write(f"data: {json.dumps(app_daemon.board.state())}\n\n".encode())
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), 20.0)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            await response.write(f"data: {message}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        app_daemon.subscribers.discard(queue)
    return response


@routes.post("/api/services/{name}/{action}")
async def api_service_action(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    action = request.match_info["action"]
    app_daemon = daemon(request)
    try:
        service = app_daemon.supervisor.get(name)
    except KeyError:
        raise web.HTTPNotFound(text=f"service inconnu : {name}")

    if action == "start":
        asyncio.create_task(app_daemon.supervisor.start_sequence([name]))
    elif action == "stop":
        asyncio.create_task(service.stop())
    elif action == "restart":
        asyncio.create_task(service.restart())
    elif action == "rebuild":
        if not service.entry.rebuild:
            raise web.HTTPBadRequest(text=f"{name} n'a pas de commande de compilation")
        asyncio.create_task(service.rebuild())
    elif action == "clear":
        service.clear_logs()
    else:
        raise web.HTTPBadRequest(text=f"action inconnue : {action}")
    return web.json_response({"ok": True})


@routes.post("/api/profiles/{name}/{action}")
async def api_profile_action(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    action = request.match_info["action"]
    app_daemon = daemon(request)
    if name == "all":
        members = list(app_daemon.supervisor.order)
    else:
        members = app_daemon.manifest.profiles.get(name)
        if members is None:
            raise web.HTTPNotFound(text=f"profil inconnu : {name}")
    if action == "start":
        asyncio.create_task(app_daemon.supervisor.start_sequence(members))
    elif action == "stop":
        asyncio.create_task(app_daemon.supervisor.stop_sequence(members))
    else:
        raise web.HTTPBadRequest(text=f"action inconnue : {action}")
    return web.json_response({"ok": True, "services": members})


# ---------- tickets ----------


def int_id(request: web.Request) -> int:
    try:
        return int(request.match_info["id"])
    except ValueError:
        raise web.HTTPBadRequest(text="id non numérique")


@routes.get("/api/tickets")
async def api_tickets_list(request: web.Request) -> web.Response:
    return web.json_response(daemon(request).board.state())


async def _ticket_request(request: web.Request) -> tuple[dict, str, str | None]:
    """Read the body that the console sends: les champs, la version, le corps."""
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="le corps doit être du JSON")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="le corps doit être un objet JSON")
    changes = body.get("changes", {})
    if not isinstance(changes, dict):
        raise web.HTTPBadRequest(text="`changes` doit être un objet")
    text = body.get("body")
    return changes, str(body.get("mtime") or ""), text if isinstance(text, str) else None


def _board_call(call, *args, **kwargs):
    """Run one operation of the board and translate its refusals into answers.

    The handlers hold no logic of their own: the board decides, they speak HTTP.
    """
    try:
        return call(*args, **kwargs)
    except ValueError as exc:  # un id non numérique, ou un champ refusé
        raise web.HTTPBadRequest(text=str(exc))
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc))
    except Conflict as exc:
        # Le ticket a changé sur le disque depuis que la carte a été lue. La
        # console reçoit la version de l'autre écrivain, elle ne l'écrase pas.
        raise web.HTTPConflict(
            text=json.dumps({"error": "conflit", "ticket": exc.ticket.state()}),
            content_type="application/json",
        )


@routes.post("/api/tickets")
async def api_ticket_create(request: web.Request) -> web.Response:
    changes, _, body = await _ticket_request(request)
    app_daemon = daemon(request)
    ticket = _board_call(app_daemon.board.create, changes, body or "")
    return web.json_response({"ok": True, "ticket": ticket.state()}, status=201)


@routes.put("/api/tickets/{id}")
async def api_ticket_update(request: web.Request) -> web.Response:
    changes, mtime, body = await _ticket_request(request)
    ticket = _board_call(
        daemon(request).board.update, int_id(request), changes, mtime, body
    )
    return web.json_response({"ok": True, "ticket": ticket.state()})


@routes.delete("/api/tickets/{id}")
async def api_ticket_delete(request: web.Request) -> web.Response:
    _, mtime, _ = await _ticket_request(request)
    _board_call(daemon(request).board.delete, int_id(request), mtime)
    return web.json_response({"ok": True})


# ---------- issues github ----------


@routes.get("/api/issues")
async def api_issues(request: web.Request) -> web.Response:
    """Le board des issues GitHub, en lecture.

    C'est la seule route de ce board, et elle lit. Aucune route d'écriture ne
    l'accompagne: même un client hostile sur 127.0.0.1 ne peut rien écrire sur
    GitHub par ce chemin.
    """
    try:
        raw = await issues_fetch(ROOT)
    except Unavailable as exc:
        # Un board vide ressemblerait à un dépôt sans issue. La console dit
        # pourquoi elle ne montre rien.
        state = issues_board([])
        state["error"] = str(exc)
        return web.json_response(state)
    return web.json_response(issues_board(raw))


# ---------- chat ----------


@routes.get("/api/chat")
async def api_chat_read(request: web.Request) -> web.Response:
    app_daemon = daemon(request)
    return web.json_response(
        {"connected": app_daemon.chat.connected, "messages": list(app_daemon.chat.messages)}
    )


@routes.post("/api/chat")
async def api_chat_send(request: web.Request) -> web.Response:
    app_daemon = daemon(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="message vide")
    try:
        message = await app_daemon.chat.send(text)
    except ConnectionError as exc:
        raise web.HTTPServiceUnavailable(text=str(exc))
    return web.json_response({"ok": True, "message": message})


# ---------- config files ----------


def _stamp(path: Path) -> str:
    """The version of a file on disk, as a string.

    st_mtime_ns is above 2^53, thus a JSON number would lose its last digits in
    the browser. The console only compares it, never reads it.
    """
    try:
        return str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        return ""


def _config_file(request: web.Request):
    name = request.match_info["name"]
    for entry in daemon(request).manifest.config_files:
        if entry.name == name:
            return entry
    raise web.HTTPNotFound(text=f"fichier inconnu : {name}")


@routes.get("/api/config")
async def api_config_list(request: web.Request) -> web.Response:
    files = [
        {
            "name": c.name,
            "path": str(c.path.relative_to(ROOT)),
            "service": c.service,
            "hot_reload": c.hot_reload,
            "note": c.note,
            "size": c.path.stat().st_size if c.path.exists() else 0,
            "mtime": _stamp(c.path),
        }
        for c in daemon(request).manifest.config_files
    ]
    return web.json_response({"files": files})


@routes.get("/api/config/{name}")
async def api_config_read(request: web.Request) -> web.Response:
    entry = _config_file(request)
    text = entry.path.read_text(encoding="utf-8") if entry.path.exists() else ""
    return web.json_response({"name": entry.name, "text": text, "mtime": _stamp(entry.path)})


@routes.put("/api/config/{name}")
async def api_config_write(request: web.Request) -> web.Response:
    entry = _config_file(request)
    body = await request.json()
    text = body.get("text")
    if not isinstance(text, str):
        raise web.HTTPBadRequest(text="le corps doit contenir un champ texte")

    # The file also lives in an editor and in git. Refuse to overwrite a version
    # that the browser never saw, and give it back, or an edit disappears in silence.
    current = _stamp(entry.path)
    if body.get("mtime") != current:
        return web.json_response(
            {
                "error": "conflit",
                "mtime": current,
                "text": entry.path.read_text(encoding="utf-8") if entry.path.exists() else "",
            },
            status=409,
        )

    if entry.path.exists():
        backup = entry.path.with_name(entry.path.name + ".bak")
        backup.write_bytes(entry.path.read_bytes())
    # Write beside the file and rename: a daemon that dies in the middle of this
    # leaves the old file whole, never a truncated persona.
    temp = entry.path.with_name(entry.path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, entry.path)
    return web.json_response(
        {
            "ok": True,
            "mtime": _stamp(entry.path),
            "service": entry.service if not entry.hot_reload else None,
            "applied": entry.hot_reload,
        }
    )


# ---------- wiring ----------


async def on_startup(app: web.Application) -> None:
    app_daemon: Daemon = app["daemon"]
    app_daemon.session = aiohttp.ClientSession()
    await app_daemon.chat.start()
    app["sampler_task"] = asyncio.create_task(app_daemon.sample_loop())


async def on_cleanup(app: web.Application) -> None:
    """Stop every service. A dead daemon must leave no orphan and no busy GPU."""
    app_daemon: Daemon = app["daemon"]
    task = app.get("sampler_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await app_daemon.chat.close()
    print("[terminal] arrêt des services…", flush=True)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(
            app_daemon.supervisor.stop_sequence(list(app_daemon.supervisor.order)), 60.0
        )
    app_daemon.supervisor.kill_all_now()
    if app_daemon.session:
        await app_daemon.session.close()
    print("[terminal] arrêté.", flush=True)


def build_app() -> web.Application:
    app = web.Application()
    app["daemon"] = Daemon()
    app.add_routes(routes)
    app.router.add_static("/web/", WEB)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


async def serve() -> None:
    """Drive the runner directly.

    `web.run_app` installs no SIGTERM handler in aiohttp 3.14, thus a SIGTERM
    left the daemon alive and every service orphaned. These explicit lines are
    more reliable than the signal plumbing of the framework.
    """
    app = build_app()
    # shutdown_timeout: cleanup() waits for the open connections before it runs
    # on_cleanup, and the SSE stream of the console never closes on its own.
    # Waiting the default 60 s would delay the shutdown of every service.
    runner = web.AppRunner(app, handle_signals=False, shutdown_timeout=1.0)
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    print(f"[terminal] daemon {VERSION} · http://{HOST}:{PORT}", flush=True)
    print(f"[terminal] {len(app['daemon'].supervisor.order)} services au manifeste", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()  # this runs on_cleanup


def main() -> None:
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
