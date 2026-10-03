import asyncio
import base64
import io
import json
import os
import time

import httpx
import nats
from dotenv import load_dotenv
from PIL import Image, ImageOps

import capture
from core import MAX_ACTIVITY, MAX_APPLICATION, MAX_TEXT, MAX_TEXTS, after_observation, build_state, decide, thumbnail

load_dotenv()

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
LLAMA_URL = os.getenv("IO_YEUX_LLAMA_URL", "http://127.0.0.1:8080/v1/chat/completions")
CAPTURE_FPS = float(os.getenv("IO_YEUX_CAPTURE_FPS", "1"))
SCREEN = os.getenv("IO_YEUX_SCREEN", "active")
CHANGE_THRESHOLD = float(os.getenv("IO_YEUX_CHANGE_THRESHOLD", "0.02"))
HEARTBEAT_SECONDS = float(os.getenv("IO_YEUX_HEARTBEAT_SECONDS", "30"))
# Mesuré sur un écran 2560×1440 : écran inchangé 0, curseur qui clignote ~0,00004, une ligne
# d'erreur de terminal ~0,00012. Au-dessus du seuil, le heartbeat observe au lieu de republier.
IDENTICAL_THRESHOLD = float(os.getenv("IO_YEUX_IDENTICAL_THRESHOLD", "0.00005"))
MAX_TOKENS = int(os.getenv("IO_YEUX_MAX_TOKENS", "500"))
# ponytail: constantes plutôt qu'un .env — la spec les fixe, rien à accorder.
VLM_SIZE = (1280, 720)
JPEG_QUALITY = 85
VLM_TIMEOUT_SECONDS = 10.0

PROMPT = (
    "Tu décris l'écran d'un ordinateur pour un autre programme. Sois factuel et bref. "
    "application : le nom de l'application au premier plan. "
    "activity : une phrase qui dit ce que fait l'utilisateur. "
    "visible_text : au plus 5 textes utiles lus à l'écran, recopiés dans la langue de l'écran. "
    "Ne converse pas. Ne recopie jamais un mot de passe, une clé, un token ou un identifiant secret."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "application": {"type": "string", "maxLength": MAX_APPLICATION},
        "activity": {"type": "string", "maxLength": MAX_ACTIVITY},
        "visible_text": {
            "type": "array",
            "maxItems": MAX_TEXTS,
            "items": {"type": "string", "maxLength": MAX_TEXT},
        },
    },
    "required": ["application", "activity", "visible_text"],
    "additionalProperties": False,
}


def encode_jpeg(image: Image.Image) -> str:
    small = ImageOps.contain(image, VLM_SIZE)  # garde le ratio de l'écran
    buffer = io.BytesIO()
    small.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode()


async def observe(http: httpx.AsyncClient, image: Image.Image, observed_at: int, trigger: str) -> dict:
    jpeg = await asyncio.to_thread(encode_jpeg, image)
    response = await http.post(LLAMA_URL, json={
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Décris cet écran."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg}"}},
            ]},
        ],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        # Sans ça, Gemma-4 raisonne d'abord et épuise max_tokens avant le JSON.
        # Qwen3.5 lit la même variable de template.
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "vision_state", "schema": SCHEMA, "strict": True},
        },
    })
    response.raise_for_status()
    choice = response.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError(f"JSON coupé à {MAX_TOKENS} jetons : augmenter IO_YEUX_MAX_TOKENS")
    return build_state(json.loads(choice["message"]["content"]), observed_at, trigger)


async def main():
    print("👁️ Démarrage du service io_yeux...")
    try:
        nc = await nats.connect(NATS_URL, name="io_yeux")
        print("✅ Connecté au système nerveux (NATS).")
    except Exception as e:
        print(f"❌ Erreur de connexion à NATS: {e}")
        return

    print(f"   - Écran : {SCREEN}, {CAPTURE_FPS} capture/s, seuil={CHANGE_THRESHOLD}")
    print(f"   - Heartbeat : {HEARTBEAT_SECONDS} s, écran identique sous {IDENTICAL_THRESHOLD}")

    bus = None
    last_capture_error = None
    reference = None
    state = None  # le dernier état publié
    last_check = time.monotonic()  # dernière publication : observation ou republication
    period = 1.0 / CAPTURE_FPS
    try:
        async with httpx.AsyncClient(timeout=VLM_TIMEOUT_SECONDS) as http:
            while True:
                started = time.monotonic()
                try:
                    if bus is None:
                        bus = await capture.connect()
                    image = await capture.capture(bus, SCREEN)
                except Exception as e:
                    # Une ligne par erreur nouvelle : un écran inconnu ne remplit pas les logs.
                    if str(e) != last_capture_error:
                        print(f"[io_yeux] ⚠️ Capture impossible : {type(e).__name__}: {e}")
                        last_capture_error = str(e)
                    if bus is not None and not bus.connected:
                        bus = None  # KWin ou le bus de session a redémarré : on se reconnecte.
                    await asyncio.sleep(period)
                    continue
                last_capture_error = None
                observed_at = int(time.time() * 1000)
                thumb = await asyncio.to_thread(thumbnail, image)

                trigger = decide(reference, thumb, CHANGE_THRESHOLD,
                                 time.monotonic() - last_check, HEARTBEAT_SECONDS, IDENTICAL_THRESHOLD)
                if trigger == "republish":
                    # Écran identique à la référence : la description reste vraie, pas de VLM.
                    fresh = {**state, "checked_at": observed_at}
                    try:
                        await nc.publish("io.vision.state", json.dumps(fresh).encode())
                        state, last_check = fresh, time.monotonic()
                    except Exception as e:
                        print(f"[io_yeux] ⚠️ Republication échouée : {type(e).__name__}: {e}")
                elif trigger:
                    # Une seule observation en vol : la boucle l'attend, puis repart sur la frame
                    # la plus récente (latest-frame-wins par construction).
                    ok = False
                    try:
                        new_state = await observe(http, image, observed_at, trigger)
                        await nc.publish("io.vision.state", json.dumps(new_state).encode())
                        state, last_check, ok = new_state, time.monotonic(), True
                        # Ni activity ni visible_text dans les logs : ils peuvent contenir un secret.
                        latency = time.monotonic() - started
                        print(f"[io_yeux] 👁️ Observation ({trigger}, {latency:.1f} s) : {state['application']}")
                    except Exception as e:
                        print(f"[io_yeux] ⚠️ Observation échouée : {type(e).__name__}: {e}")
                    reference = after_observation(reference, thumb, ok)

                await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))
    finally:
        if bus is not None:
            bus.disconnect()
        await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
