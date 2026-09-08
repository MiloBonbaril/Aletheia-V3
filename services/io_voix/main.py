import asyncio
import contextlib
import json
import os
import queue
from concurrent.futures import ThreadPoolExecutor

import nats
import numpy as np
import sounddevice as sd

from audio import AudioChunk, EMPTY, encode_wav_b64, queue_fragment
from engine import build_engine, stream_fragment, warmup

# ================= Configuration =================
MUTE_LOCAL_PLAYBACK = os.getenv("MUTE_LOCAL_PLAYBACK", "false").lower() in ("1", "true")

# Un seul thread d'inférence. Ce n'est pas un réglage de performance : le moteur garde
# des caches KV persistants et des graphes CUDA capturés, tous deux liés à un seul flux
# de génération. Deux fragments synthétisés en parallèle corrompraient les deux.
inference_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Audio8Inference")
audio_sync_queue = queue.Queue()




# ================= Worker Audio Ultra-Basse Latence =================
def native_audio_player_worker(loop, nc, sample_rate):
    """
    Maintient le flux ALSA/PulseAudio/PipeWire ouvert en permanence.
    Zéro allocation dynamique au moment de jouer le son.
    """
    if MUTE_LOCAL_PLAYBACK:
        print("🔇 Lecture locale coupée (MUTE_LOCAL_PLAYBACK) — audio publié sur NATS uniquement.")
    else:
        print("🔊 Flux audio matériel persistant [OK]")

    # ponytail: nullcontext() plutôt qu'un `if` séparé pour ouvrir/sauter le stream —
    # `stream` vaut None des deux côtés du `with`, donc le reste de la boucle ne
    # branche que sur `if stream is not None`.
    stream_ctx = (
        contextlib.nullcontext()
        if MUTE_LOCAL_PLAYBACK
        else sd.OutputStream(samplerate=sample_rate, channels=1, dtype='float32')
    )
    with stream_ctx as stream:
        while True:
            item = audio_sync_queue.get()
            if item is None:
                break

            if nc and item.first:
                asyncio.run_coroutine_threadsafe(
                    nc.publish("io.voice.speak.start", json.dumps({
                        "sequence": item.sequence, "text": item.text, "is_last": item.is_last
                    }).encode()), loop
                )

            # Écriture directe et synchrone dans le buffer de la carte son
            if stream is not None and item.samples.size:
                stream.write(item.samples.reshape(-1, 1))

            if nc and item.final:
                asyncio.run_coroutine_threadsafe(
                    nc.publish("io.voice.speak.end", json.dumps({
                        "sequence": item.sequence, "is_last": item.is_last
                    }).encode()), loop
                )
            audio_sync_queue.task_done()

# ================= Programme Principal =================
async def main():
    loop = asyncio.get_running_loop()

    # Optimisation Linux : On s'assure que le process a une priorité décente
    try:
        os.nice(-5)
        print("⚡ Priorité processus ajustée (Nice -5)")
    except PermissionError:
        print("ℹ️ Lance en sudo ou configure un-security pour débloquer la priorité max.")

    # Parallélisation du chargement du moteur et de la connexion réseau NATS
    print("🧠 Chargement du moteur Audio8 & Connexion NATS...")

    async def load_engine():
        return await loop.run_in_executor(inference_executor, build_engine)

    engine_task = asyncio.create_task(load_engine())
    nats_task = asyncio.create_task(nats.connect("nats://localhost:4222", name="io_voix"))

    engine, nc = await asyncio.gather(engine_task, nats_task)
    print(f"🚀 Matériel synchronisé et connecté à NATS ({engine.sample_rate} Hz).")

    # Warmup : il compile les boucles chaudes et capture les graphes CUDA. Il dure une
    # trentaine de secondes au premier lancement, quelques secondes ensuite grâce au
    # cache Inductor. Sans lui, le premier fragment réel paierait toute la compilation.
    print("🔥 Compilation des graphes CUDA (~30 s au premier lancement)...")
    await loop.run_in_executor(inference_executor, warmup, engine)
    print("⚡ Moteur brûlant. Prêt à foudroyer le TTFS.")

    # Lancement du thread audio natif
    audio_thread = loop.run_in_executor(
        None, native_audio_player_worker, loop, nc, engine.sample_rate
    )

    # Tâches d'encodage/publication en arrière-plan : gardées en vie ici pour éviter
    # qu'asyncio ne les garbage-collecte en cours de route (piège classique de
    # create_task sans référence conservée).
    background_tasks: set[asyncio.Task] = set()

    async def encode_and_publish_audio(samples, sequence, is_last):
        try:
            audio_b64 = await loop.run_in_executor(
                None, encode_wav_b64, samples, engine.sample_rate
            )
            await nc.publish("io.voice.speak.audio", json.dumps({
                "sequence": sequence, "audio": audio_b64, "format": "wav", "is_last": is_last,
            }).encode())
        except Exception as e:
            print(f"⚠️ Échec de publication audio : {e}")

    async def fragment_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            text = data.get("text", "")
            sequence = data.get("sequence", 0)
            is_last = data.get("is_last", False)

            if not text.strip():
                if is_last:
                    audio_sync_queue.put(AudioChunk(EMPTY, sequence, "", True, True, True))
                return

            def inference_job():
                """Pousse chaque tranche dès qu'elle est prête, sans attendre la fin.

                Tourne dans le thread d'inférence ; `audio_sync_queue` est thread-safe.
                """
                try:
                    pieces = queue_fragment(
                        stream_fragment(engine, text), sequence, text, is_last,
                        audio_sync_queue.put,
                    )
                except Exception as e:
                    print(f"⚠️ Échec d'inférence : {e}")
                    return None
                return np.concatenate(pieces) if pieces else None

            samples = await loop.run_in_executor(inference_executor, inference_job)

            # Le fragment entier part en un seul WAV, comme avant : le contrat
            # `io.voice.speak.audio` reste « un message par fragment », donc io_discord
            # et io_visage ne changent pas.
            if samples is not None and nc:
                task = asyncio.create_task(encode_and_publish_audio(samples, sequence, is_last))
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)

        except Exception as e:
            print(f"⚠️ Erreur Stream Handler: {e}")

    await nc.subscribe("lobe.fragment_stream", cb=fragment_handler)
    print("👂 Écoute réseau active sur 'lobe.fragment_stream'. Donnez-moi du texte.")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\nArrêt propre du pipeline...")
    finally:
        audio_sync_queue.put(None)
        await nc.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
