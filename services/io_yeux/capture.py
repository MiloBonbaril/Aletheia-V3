"""Capture de l'écran par l'interface D-Bus KWin ScreenShot2. Voir docs/adr/0006.

KWin écrit l'image brute dans un pipe que l'on lit en mémoire : aucune capture sur disque.
"""

import asyncio
import os

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus
from PIL import Image

# Valeurs de l'enum QImage::Format -> mode brut de PIL (little-endian).
_RAW_MODES = {
    4: "BGRX",   # Format_RGB32
    5: "BGRX",   # Format_ARGB32 (l'écran est opaque : l'alpha est ignoré)
    6: "BGRX",   # Format_ARGB32_Premultiplied
    16: "RGBX",  # Format_RGBX8888
    17: "RGBX",  # Format_RGBA8888
    18: "RGBX",  # Format_RGBA8888_Premultiplied
}


class CaptureError(RuntimeError):
    pass


def _read_image(fd: int, raw_mode: str, size: tuple[int, int], stride: int) -> Image.Image:
    """Lit le pipe jusqu'à la fin et décode l'image. Tourne dans un thread, et ferme `fd` lui-même :
    si la tâche est annulée, le thread finit sa lecture sur un descripteur encore ouvert."""
    try:
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
    finally:
        os.close(fd)
    return Image.frombuffer("RGB", size, b"".join(chunks), "raw", raw_mode, stride, 1)


async def connect() -> MessageBus:
    return await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()


async def capture(bus: MessageBus, screen: str = "active") -> Image.Image:
    """Capture l'écran actif, ou l'écran nommé (`DP-1`, `HDMI-A-1`…)."""
    if screen == "active":
        member, signature, args = "CaptureActiveScreen", "a{sv}h", []
    else:
        member, signature, args = "CaptureScreen", "sa{sv}h", [screen]

    read_fd, write_fd = os.pipe()
    try:
        try:
            reply = await bus.call(Message(
                destination="org.kde.KWin",
                path="/org/kde/KWin/ScreenShot2",
                interface="org.kde.KWin.ScreenShot2",
                member=member,
                signature=signature,
                body=[*args, {"native-resolution": Variant("b", True)}, 0],
                unix_fds=[write_fd],
            ))
        finally:
            # KWin garde sa copie du descripteur : sans cette fermeture, la lecture ne finit jamais.
            os.close(write_fd)
        if reply.message_type == MessageType.ERROR:
            raise CaptureError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
        meta = {key: value.value for key, value in reply.body[0].items()}
        raw_mode = _RAW_MODES.get(meta.get("format"))
        if raw_mode is None:
            raise CaptureError(f"format d'image KWin non géré : {meta.get('format')}")
    except BaseException:
        os.close(read_fd)
        raise
    return await asyncio.to_thread(_read_image, read_fd, raw_mode, (meta["width"], meta["height"]), meta["stride"])
