import asyncio
import json
import os
import urllib.request
import threading
import nats
import numpy as np
import sounddevice as sd
from kokoro_onnx import Kokoro

# ================= Configuration =================
# Chemin de téléchargement des modèles
MODELS_DIR = os.getenv("KOKORO_MODELS_DIR", os.path.join(os.path.dirname(__file__), "models"))
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# "ff_siwis" est une voix féminine française (French Female)
VOICE_NAME = os.getenv("KOKORO_VOICE", "ff_siwis")
SPEECH_SPEED = float(os.getenv("KOKORO_SPEED", "1.0"))
# =================================================

def download_file(url, dest_path):
    print(f"Téléchargement de {os.path.basename(dest_path)} (cela peut prendre du temps...)")
    urllib.request.urlretrieve(url, dest_path)
    print(f"✅ {os.path.basename(dest_path)} téléchargé.")

def ensure_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    model_path = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
    voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")
    
    if not os.path.exists(model_path):
        download_file(MODEL_URL, model_path)
    if not os.path.exists(voices_path):
        download_file(VOICES_URL, voices_path)
        
    return model_path, voices_path

# ================= Worker Audio =================
# Queue pour stocker les morceaux audio (matrices numpy) en attente de lecture
audio_queue = asyncio.Queue()

async def audio_player_worker():
    """
    Worker asynchrone qui lit la queue en continu et joue le son.
    Puisque sd.wait() bloque le thread, on doit scrupuleusement l'appeler 
    dans un executor, ou bloquer ce worker spécifique.
    Cependant sd.play() est non-bloquant et s'exécute en arrière plan. 
    On peut faire une boucle qui attend la fin de sd.wait().
    """
    print("🔊 Worker audio prêt.")
    loop = asyncio.get_running_loop()
    
    while True:
        audio, sample_rate = await audio_queue.get()
        if audio is None:
            break
            
        print("▶️ Lecture d'un fragment audio...")
        # Lancer la lecture (ne bloque pas)
        sd.play(audio, sample_rate)
        # Attendre la fin de la lecture de ce morceau (bloquant pour le son, mais exécuté de manière à garder loop free)
        await loop.run_in_executor(None, sd.wait)
        
        audio_queue.task_done()

# ================= Programme Principal =================
async def main():
    print("⚙️ Vérification des modèles Kokoro ONNX...")
    model_path, voices_path = await asyncio.to_thread(ensure_models)
    
    print("🧠 Chargement de Kokoro (cela peut prendre quelques secondes)...")
    kokoro = await asyncio.to_thread(Kokoro, model_path, voices_path)
    print("✅ Modèle chargé !")

    # Démarrage du worker
    player_task = asyncio.create_task(audio_player_worker())

    print("🔌 Connexion au serveur NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        print(f"❌ Erreur de connexion à NATS: {e}")
        return
    print("✅ Connecté au système nerveux (NATS).")

    async def fragment_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            text = data.get("text", "")
            
            if not text.strip():
                return
            
            # La génération TTS prend du temps et est CPU bound. 
            # On l'exécute dans un thread pool pour ne pas bloquer les autres messages NATS.
            def generate_audio():
                print(f"[io_voix] ⏳ Synthèse de : '{text}'")
                try:
                    # lang "fr-fr" pour les voix fr (Kokoro gère le choix de la langue en fonction de la syntaxe attendue)
                    return kokoro.create(text, voice=VOICE_NAME, speed=SPEECH_SPEED, lang="fr-fr")
                except Exception as e:
                    print(f"⚠️ Erreur de synthèse : {e}")
                    return None
                    
            result = await asyncio.to_thread(generate_audio)
            
            if result:
                samples, sample_rate = result
                # Ajouter à la queue pour que le player l'enchaîne
                await audio_queue.put((samples, sample_rate))
                
        except Exception as e:
            print(f"⚠️ Erreur dans le handler fragment: {e}")

    await nc.subscribe("lobe.fragment_stream", cb=fragment_handler)
    print("👂 I/O Voix en écoute sur 'lobe.fragment_stream'...")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt du service I/O Voix...")
    finally:
        await audio_queue.put((None, None)) # Stopper le worker
        if not player_task.done():
            await player_task
        await nc.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
