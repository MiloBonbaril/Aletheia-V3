"""The chat tab, and the only place where the daemon touches the NATS bus.

It publishes on `io.user.msg.text`, and it subscribes to `lobe.fragment_stream`.
It subscribes to nothing else: never `>`, and never an audio topic. See
`docs/adr/0004-le-terminal-publie-et-ecoute-un-seul-sujet.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import time
from collections import deque

import nats

NATS_URL = "nats://localhost:4222"
INBOX = "io.user.msg.text"
FRAGMENTS = "lobe.fragment_stream"
# The speaker name goes in the text: this is the contract of `io.user.msg.text`,
# and `io_text` writes the same prefix.
SPEAKER = "Milo"
# The buffer stays in memory, like the log buffers. It disappears with the
# daemon. This repository is public: no conversation goes to the disk.
MESSAGES = 200
RETRY_SECONDS = 5.0


class Chat:
    """The bus side of the chat tab."""

    def __init__(self, on_change, url: str = NATS_URL) -> None:
        self._url = url
        self._on_change = on_change
        self._nc = None
        self._task: asyncio.Task | None = None
        self.messages: deque[dict] = deque(maxlen=MESSAGES)
        self._ids = itertools.count(1)
        self._pending: dict | None = None  # the answer that is being written

    @property
    def connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    # ---------- life cycle ----------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._connect_forever())

    @staticmethod
    async def _on_error(exc: Exception) -> None:
        """Replace the default handler of nats-py.

        That handler logs a full traceback for each failed attempt, thus the
        console of the daemon filled with tracebacks every 5 s until NATS
        started. One line says the same thing.
        """
        print(f"[terminal] bus: {exc}", flush=True)

    async def _connect_forever(self) -> None:
        """Connect, and keep trying.

        The daemon starts before NATS almost every time, because the daemon is
        what starts NATS. A single connect at startup would leave the tab dead
        for the whole session.
        """
        while True:
            try:
                self._nc = await nats.connect(
                    self._url,
                    name="terminal",  # /connz then shows which client this is
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=RETRY_SECONDS,
                    error_cb=self._on_error,
                )
                await self._nc.subscribe(FRAGMENTS, cb=self._on_fragment)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self._nc = None
                await asyncio.sleep(RETRY_SECONDS)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._nc is not None:
            with contextlib.suppress(Exception):
                await self._nc.close()
            self._nc = None

    # ---------- messages ----------

    def _add(self, role: str, text: str, done: bool = False) -> dict:
        message = {
            "id": next(self._ids),
            "role": role,
            "text": text,
            "t": time.time(),
            "done": done,
        }
        self.messages.append(message)
        return message

    async def _on_fragment(self, msg) -> None:
        """Rebuild one answer from its fragments.

        `lobe.fragment_stream` has no correlation_id: the sequence of fragments
        is the turn, and `is_last` closes it.
        """
        try:
            data = json.loads(msg.data.decode())
        except (ValueError, UnicodeDecodeError):
            return
        if self._pending is None:
            self._pending = self._add("aletheia", "")
        self._pending["text"] += data.get("text") or ""
        if data.get("is_last"):
            # `stay_silent` gives a last fragment with no text: the turn is a
            # silence, and the console says so.
            self._pending["done"] = True
            finished, self._pending = self._pending, None
            self._on_change(finished)
        else:
            self._on_change(self._pending)

    async def send(self, text: str) -> dict:
        if not self.connected:
            raise ConnectionError("le daemon n'est pas connecté au bus")
        await self._nc.publish(INBOX, json.dumps({"text": f"{SPEAKER}: {text}"}).encode())
        await self._nc.flush()
        message = self._add("moi", text, done=True)
        self._on_change(message)
        return message
