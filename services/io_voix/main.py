import asyncio
import contextlib
import json
import os
import urllib.request
import queue
from concurrent.futures import ThreadPoolExecutor
import nats
import numpy as np
import sounddevice as sd
import onnxruntime as ort
from kokoro_onnx import Kokoro

from audio import encode_wav_b64

# ================= Configuration =================
MODELS_DIR = os.getenv("KOKORO_MODELS_DIR", os.path.join(os.path.dirname(__file__), "models"))
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

VOICE_NAME = os.getenv("KOKORO_VOICE", "ff_siwis")
SPEECH_SPEED = float(os.getenv("KOKORO_SPEED", "1.0"))
SAMPLE_RATE = 22050  # Fréquence native de Kokoro
MUTE_LOCAL_PLAYBACK = os.getenv("MUTE_LOCAL_PLAYBACK", "false").lower() in ("1", "true")

# Isolation totale de l'inférence (1 seul thread Python pour piloter ONNX)
inference_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="KokoroInference")
audio_sync_queue = queue.Queue()

def ensure_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    model_path = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
    voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")
    if not os.path.exists(model_path): 
        print("📥 Téléchargement du modèle ONNX...")
        urllib.request.urlretrieve(MODEL_URL, model_path)
    if not os.path.exists(voices_path): 
        print("📥 Téléchargement des voix...")
        urllib.request.urlretrieve(VOICES_URL, voices_path)
    return model_path, voices_path

# ================= Configuration ONNX Spécial Zen 2 =================
def build_optimized_kokoro(model_path, voices_path):
    """
    Configure ONNX Runtime spécifiquement pour l'architecture du Ryzen 5 5500U.
    """
    opts = ort.SessionOptions()
    
    # 6 cœurs physiques = 6 threads intra-op. On évite le SMT (12 threads) qui sature le cache L3.
    opts.intra_op_num_threads = 6
    opts.inter_op_num_threads = 1
    
    # Mode d'exécution séquentiel pour réduire l'overhead de scheduling interne
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    
    # Optimisations matérielles maximales (AVX2/FMA présents sur ton 5500U)
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    
    # Stratégie d'allocation mémoire agressive
    opts.add_session_config_entry("session.allocator.alloc_granularity", "1")
    
    # Initialisation de Kokoro avec notre session customisée
    session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, voices_path)

# ================= Worker Audio Ultra-Basse Latence =================
def native_audio_player_worker(loop, nc):
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
        else sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32')
    )
    with stream_ctx as stream:
        while True:
            item = audio_sync_queue.get()
            if item is None:
                break

            samples, sequence, text, is_last = item
            samples = samples.astype(np.float32)

            if nc:
                asyncio.run_coroutine_threadsafe(
                    nc.publish("io.voice.speak.start", json.dumps({
                        "sequence": sequence, "text": text, "is_last": is_last
                    }).encode()), loop
                )

            # Écriture directe et synchrone dans le buffer de la carte son
            if stream is not None:
                stream.write(samples.reshape(-1, 1))

            if nc:
                asyncio.run_coroutine_threadsafe(
                    nc.publish("io.voice.speak.end", json.dumps({
                        "sequence": sequence, "is_last": is_last
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

    print("⚙️ Vérification des artifacts...")
    model_path, voices_path = await loop.run_in_executor(inference_executor, ensure_models)
    
    # Parallélisation du chargement du moteur et de la connexion réseau NATS
    print("🧠 Initialisation du moteur ONNX & Connexion NATS...")
    
    async def load_engine():
        return await loop.run_in_executor(inference_executor, build_optimized_kokoro, model_path, voices_path)

    kokoro_task = asyncio.create_task(load_engine())
    nats_task = asyncio.create_task(nats.connect("nats://localhost:4222"))
    
    kokoro, nc = await asyncio.gather(kokoro_task, nats_task)
    print("🚀 Matériel synchronisé et connecté à NATS.")

    # Warmup thermique du modèle
    print("🔥 Exécution du cycle de Pre-warming...")
    await loop.run_in_executor(
        inference_executor, 
        kokoro.create, ".", VOICE_NAME, SPEECH_SPEED, "fr-fr"
    )
    print("⚡ Moteur brûlant. Prêt à foudroyer le TTFS.")

    # Lancement du thread audio natif
    audio_thread = loop.run_in_executor(None, native_audio_player_worker, loop, nc)

    # Tâches d'encodage/publication en arrière-plan : gardées en vie ici pour éviter
    # qu'asyncio ne les garbage-collecte en cours de route (piège classique de
    # create_task sans référence conservée).
    background_tasks: set[asyncio.Task] = set()

    async def encode_and_publish_audio(samples, sequence, is_last):
        try:
            audio_b64 = await loop.run_in_executor(None, encode_wav_b64, samples, SAMPLE_RATE)
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
                    audio_sync_queue.put((np.zeros(100), sequence, "", is_last))
                return

            # Inférence poussée direct dans notre exécuteur calibré à 6 threads
            def inference_job():
                try:
                    return kokoro.create(text, voice=VOICE_NAME, speed=SPEECH_SPEED, lang="fr-fr")
                except Exception as e:
                    print(f"⚠️ Échec d'inférence : {e}")
                    return None

            result = await loop.run_in_executor(inference_executor, inference_job)

            if result:
                samples, _ = result
                samples = samples.astype(np.float32)
                # Mise en file immédiate pour la lecture/le timing — l'encodage WAV/base64
                # tourne à côté (thread pool par défaut), pour ne jamais retarder le TTFA.
                audio_sync_queue.put((samples, sequence, text, is_last))
                if nc:
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