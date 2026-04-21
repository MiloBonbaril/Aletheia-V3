import asyncio
import json
import os
import urllib.request
import numpy as np
import sounddevice as sd
import nats
import torch
from whisper_cpp_python import Whisper
import silero_vad
import dotenv

dotenv.load_dotenv()

# ================= Configuration =================
MODELS_DIR = os.getenv("OREILLES_MODELS_DIR", os.path.join(os.path.dirname(__file__), "models"))
# Whisper model: GGUF base
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
# Silero VAD model will be handled by the library, but let's ensure we have a place if needed

# ================= Utils =================
def download_file(url, dest_path):
    print(f"Téléchargement de {os.path.basename(dest_path)}...")
    urllib.request.urlretrieve(url, dest_path)
    print(f"✅ {os.path.basename(dest_path)} téléchargé.")

def ensure_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    whisper_path = os.path.join(MODELS_DIR, "ggml-base.en.bin")
    
    if not os.path.exists(whisper_path):
        download_file(WHISPER_MODEL_URL, whisper_path)
        
    return whisper_path

# ================= Main =================
async def main():
    print("⚙️ Initialisation de io_oreilles...")
    
    # 1. Préparation Modèles
    whisper_path = await asyncio.to_thread(ensure_models)
    
    # Charger Whisper et Silero VAD
    print("🧠 Chargement des modèles...")
    # Whisper-cpp initialization
    w = Whisper(model_path=whisper_path)
    # Silero VAD initialization
    model = silero_vad.load_silero_vad(onnx=True)
    
    # 2. Connexion NATS
    print("🔌 Connexion NATS...")
    nc = await nats.connect("nats://localhost:4222")
    
    # 3. Boucle Audio
    print("👂 Écoute active...")
    
    # Configuration flux audio (16kHz pour Whisper/VAD)
    sample_rate = 16000
    
    # Buffer pour accumuler l'audio quand VAD détecte la parole
    audio_buffer = []
    is_speaking = False
    
    def audio_callback(indata, frames, time, status):
        nonlocal audio_buffer, is_speaking
        
        # Simple VAD avec silero
        # Indata est un numpy array (frames, 1)
        audio_chunk = torch.from_numpy(indata.flatten())
        
        # On utilise une logique simplifiée pour l'exemple, 
        # une VAD plus robuste nécessiterait un meilleur traitement de buffer.
        # Pour cet exemple:
        speech_prob = model(audio_chunk, sample_rate).item()
        
        if speech_prob > 0.3:
            is_speaking = True
            audio_buffer.append(indata.flatten())
        elif is_speaking:
            # Fin de parole
            is_speaking = False
            # Traiter le buffer
            full_audio = np.concatenate(audio_buffer)
            audio_buffer = []
            
            # Lancer STT (via une task asyncio)
            asyncio.run_coroutine_threadsafe(process_stt(full_audio), loop)

    loop = asyncio.get_running_loop()

    async def process_stt(audio_data):
        print("📝 Transcription en cours...")
        # Whisper attend du float32
        audio_data = np.asarray(audio_data, dtype=np.float32)
        res = w._full(audio_data)
        text = res.get("text", "")
        
        if text:
            print(f"🗣️ Utilisateur a dit : '{text}'")
            # Publier
            await nc.publish("io.user.speak", json.dumps({"text": text}).encode())

    # Démarrer le stream audio
    with sd.InputStream(samplerate=sample_rate, channels=1, blocksize=512, callback=audio_callback):
        while True:
            await asyncio.sleep(1)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Arrêt du service io_oreilles.")
