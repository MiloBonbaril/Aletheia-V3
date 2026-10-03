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
from core import (MAX_ACTIVITY, MAX_APPLICATION, MAX_TEXT, MAX_TEXTS, Stats, Suspension, after_observation,
                  backoff, build_state, decide, difference, fragment_end, interaction_started, is_suspended,
                  thumbnail, vlm_metrics, voice_event)

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
# Mode debug : enregistre l'image envoyée et la réponse brute, AVANT masquage. Non défini par
# défaut. services/io_yeux/debug/ est dans .gitignore : le dépôt est public.
DEBUG_DIR = os.getenv("IO_YEUX_DEBUG_DIR")
SUMMARY_SECONDS = 60.0
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


def save_debug(observed_at: int, trigger: str, jpeg: str, raw_response: str) -> None:
    """Écrit l'image envoyée au VLM et sa réponse brute. Ne change rien d'autre : une erreur
    d'écriture ne fait pas échouer l'observation."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        base = os.path.join(DEBUG_DIR, f"{observed_at}-{trigger}")
        with open(base + ".jpg", "wb") as f:
            f.write(base64.b64decode(jpeg))
        with open(base + ".json", "w", encoding="utf-8") as f:
            f.write(raw_response)
    except OSError as e:
        print(f"[io_yeux] ⚠️ Mode debug : écriture impossible : {describe(e)}")


def describe(error: BaseException) -> str:
    """Une ligne lisible, sans stack trace : les erreurs se répètent pendant une panne."""
    text = str(error).strip().rstrip(".")
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


async def observe(http: httpx.AsyncClient, image: Image.Image, observed_at: int, trigger: str) -> tuple[dict, dict]:
    """Rend l'état à publier et les métriques VLM (vlm_metrics)."""
    # Timeout sur l'ensemble : celui de httpx s'applique à chaque opération, pas à la requête.
    # À l'échéance, la connexion se ferme et llama-server abandonne la tâche.
    async with asyncio.timeout(VLM_TIMEOUT_SECONDS):
        return await _observe(http, image, observed_at, trigger)


async def _observe(http: httpx.AsyncClient, image: Image.Image, observed_at: int, trigger: str) -> tuple[dict, dict]:
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
    if DEBUG_DIR:
        # Dans un thread, sans l'attendre : un disque lent ne doit pas consommer le timeout.
        asyncio.get_running_loop().run_in_executor(None, save_debug, observed_at, trigger, jpeg, response.text)
    response.raise_for_status()
    body = response.json()
    choice = body["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError(f"JSON coupé à {MAX_TOKENS} jetons : augmenter IO_YEUX_MAX_TOKENS")
    return build_state(json.loads(choice["message"]["content"]), observed_at, trigger), vlm_metrics(body)


async def main():
    print("👁️ Démarrage du service io_yeux...")
    print(f"   - Écran : {SCREEN}, {CAPTURE_FPS} capture/s, seuil={CHANGE_THRESHOLD}")
    print(f"   - Heartbeat : {HEARTBEAT_SECONDS} s, écran identique sous {IDENTICAL_THRESHOLD}")
    if DEBUG_DIR:
        print(f"   - Mode debug : images et réponses brutes, non masquées, dans {os.path.abspath(DEBUG_DIR)}")

    # llama-server tourne en --parallel 1 : la vision cède la place à la conversation.
    suspension = Suspension()
    observation = None  # la tâche de l'observation en vol

    def is_last(msg) -> bool:
        try:
            return bool(json.loads(msg.data.decode()).get("is_last"))
        except Exception:
            return False

    async def interaction_started_handler(msg):
        nonlocal suspension
        suspension = interaction_started(suspension, time.monotonic())
        if observation is not None and not observation.done():
            # Fermer la connexion suffit : llama-server abandonne la tâche et sert lobe_frontal.
            observation.cancel()

    async def voice_start_handler(msg):
        nonlocal suspension
        suspension = voice_event(suspension, time.monotonic())

    async def voice_end_handler(msg):
        nonlocal suspension
        suspension = voice_event(suspension, time.monotonic(), is_last_end=is_last(msg))

    async def fragment_handler(msg):
        nonlocal suspension
        if is_last(msg):
            suspension = fragment_end(suspension, time.monotonic())

    # NATS peut manquer au démarrage : la connexion se fait en tâche de fond, et la capture
    # commence sans l'attendre. max_reconnect_attempts=-1 réessaie aussi la première connexion.
    nc = nats.NATS()
    last_nats_error = None

    async def nats_error(e):
        nonlocal last_nats_error
        if describe(e) != last_nats_error:  # une ligne par erreur nouvelle, pas une toutes les 2 s
            print(f"[io_yeux] ⚠️ NATS : {describe(e)}")
            last_nats_error = describe(e)

    async def nats_disconnected():
        print("[io_yeux] ⚠️ NATS déconnecté, reconnexion en cours.")

    async def nats_reconnected():
        nonlocal last_nats_error
        last_nats_error = None
        print("[io_yeux] ✅ NATS reconnecté.")

    async def connect_nats():
        try:
            await _connect_nats()
        except Exception as e:  # une NATS_URL invalide, par exemple : sans ça, l'erreur se perd
            print(f"[io_yeux] ❌ NATS abandonné : {describe(e)}. Aucune observation ne sera publiée.")

    async def _connect_nats():
        nonlocal last_nats_error
        await nc.connect(NATS_URL, name="io_yeux", max_reconnect_attempts=-1, reconnect_time_wait=2,
                         error_cb=nats_error, disconnected_cb=nats_disconnected,
                         reconnected_cb=nats_reconnected)
        last_nats_error = None
        print("✅ Connecté au système nerveux (NATS).")
        await nc.subscribe("cortex.interaction.started", cb=interaction_started_handler)
        # Pas io.voice.speak.audio : start et end suffisent pour savoir que io_voix vit.
        await nc.subscribe("io.voice.speak.start", cb=voice_start_handler)
        await nc.subscribe("io.voice.speak.end", cb=voice_end_handler)
        await nc.subscribe("lobe.fragment_stream", cb=fragment_handler)

    nats_task = asyncio.create_task(connect_nats())

    bus = None
    last_capture_error = None
    reference = None
    state = None  # le dernier état publié
    last_check = time.monotonic()  # dernière publication : observation ou republication
    failures = 0  # observations échouées de suite
    retry_at = 0.0  # pas d'observation avant cet instant (backoff)
    period = 1.0 / CAPTURE_FPS
    stats = Stats(started=time.monotonic())
    previous = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=VLM_TIMEOUT_SECONDS) as http:
            while True:
                started = time.monotonic()
                if is_suspended(suspension, started):
                    stats.suspended(started - previous)
                previous = started
                if started - stats.started >= SUMMARY_SECONDS:
                    print(f"[io_yeux] 📊 {stats.summary(started)}")
                    stats = Stats(started=started)
                try:
                    if bus is None:
                        bus = await capture.connect()
                    image = await capture.capture(bus, SCREEN)
                except Exception as e:
                    stats.error()
                    # Une ligne par erreur nouvelle : un écran inconnu ne remplit pas les logs.
                    if str(e) != last_capture_error:
                        print(f"[io_yeux] ⚠️ Capture impossible : {describe(e)}")
                        last_capture_error = str(e)
                    if bus is not None and not bus.connected:
                        bus = None  # KWin ou le bus de session a redémarré : on se reconnecte.
                    await asyncio.sleep(period)
                    continue
                last_capture_error = None
                captured = time.monotonic()
                observed_at = int(time.time() * 1000)
                thumb = await asyncio.to_thread(thumbnail, image)
                diff = difference(reference, thumb) if reference is not None else None

                now = time.monotonic()
                # Sans NATS, observer ne sert à rien : personne ne reçoit l'état.
                paused = is_suspended(suspension, now) or not nc.is_connected
                trigger = decide(reference, thumb, CHANGE_THRESHOLD, now - last_check, HEARTBEAT_SECONDS,
                                 IDENTICAL_THRESHOLD, paused)
                if trigger in ("change", "heartbeat") and now < retry_at:
                    trigger = None  # backoff : llama-server ne répondait pas
                stats.capture(captured - started, time.monotonic() - captured)
                if trigger == "republish":
                    # Écran identique à la référence : la description reste vraie, pas de VLM.
                    fresh = {**state, "checked_at": observed_at}
                    payload = json.dumps(fresh).encode()
                    try:
                        await nc.publish("io.vision.state", payload)
                        state, last_check = fresh, time.monotonic()
                        stats.republish(len(payload))
                    except Exception as e:
                        stats.error()
                        print(f"[io_yeux] ⚠️ Republication échouée : {describe(e)}")
                elif trigger:
                    # Une seule observation en vol : la boucle l'attend, puis repart sur la frame
                    # la plus récente (latest-frame-wins par construction).
                    ok = False
                    observation = asyncio.create_task(observe(http, image, observed_at, trigger))
                    try:
                        new_state, metrics = await observation
                    except asyncio.CancelledError:
                        if asyncio.current_task().cancelling():
                            raise  # arrêt du service, pas une interaction
                        print("[io_yeux] ⏸️ Observation annulée : une interaction commence.")
                    except Exception as e:
                        stats.error()
                        failures += 1
                        wait = backoff(failures)
                        retry_at = time.monotonic() + wait
                        print(f"[io_yeux] ⚠️ Observation échouée : {describe(e)}. Prochain essai dans {wait:.0f} s.")
                    else:
                        if failures:
                            print(f"[io_yeux] ✅ llama-server répond de nouveau, après {failures} échec(s).")
                        failures, retry_at = 0, 0.0
                        # Une panne de NATS n'est pas une panne de llama-server : pas de backoff ici.
                        payload = json.dumps(new_state).encode()
                        try:
                            await nc.publish("io.vision.state", payload)
                            state, last_check, ok = new_state, time.monotonic(), True
                            vlm_s = time.monotonic() - now
                            stats.observation(trigger, vlm_s, metrics, len(payload))
                            # Ni activity ni visible_text dans les logs : ils peuvent contenir un secret.
                            shown = "première observation" if diff is None else f"différence {diff:.5f}"
                            print(f"[io_yeux] 👁️ Observation ({trigger}, {shown}, {vlm_s:.1f} s) : "
                                  f"{state['application']}")
                        except Exception as e:
                            stats.error()
                            print(f"[io_yeux] ⚠️ Publication échouée : {describe(e)}")
                    finally:
                        observation = None
                    reference = after_observation(reference, thumb, ok)

                await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))
    finally:
        nats_task.cancel()
        if bus is not None:
            bus.disconnect()
        try:
            await (nc.drain() if nc.is_connected else nc.close())
        except Exception as e:
            print(f"[io_yeux] ⚠️ Arrêt de NATS : {describe(e)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
