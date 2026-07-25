# 👂 I/O Oreilles (STT)

Ce service assure la perception auditive de Nexus-V en transformant le flux audio en texte en temps réel.

## 🎯 Rôle & Responsabilités

- **Capture Audio** : Écoute continue du microphone physique.
- **Détection d'Activité Vocale (VAD)** : Utilise **Silero VAD** pour identifier les segments de parole et ignorer le bruit de fond.
- **Transcription (STT)** : Transforme la parole en texte via **CTranslate2** et le modèle **Whisper**.
- **Émission d'Événements** : Publie le texte transcrit sur le topic NATS `io.user.speak` dès qu'une phrase complète est détectée.

## ⚙️ Configuration & Lancement

### Dépendances
- `libonnxruntime.so` (pour Silero VAD).
- Modèles Whisper convertis pour CTranslate2.

### Modèle Whisper
Non versionné (`model/` est ignoré par git). À récupérer une fois :
```bash
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir='model/whisper-small-ct2')"
# ct2rs exige preprocessor_config.json, absent du dépôt Systran :
python -c "from huggingface_hub import hf_hub_download; import shutil; shutil.copy(hf_hub_download('openai/whisper-small','preprocessor_config.json'), 'model/whisper-small-ct2/preprocessor_config.json')"
```

### Variables d'Environnement
- `STT_LANGUAGE` : Langue de transcription (ex: `fr`).
- `NATS_URL` : URL du broker NATS.
- `STT_MODEL_PATH` : chemin du modèle CTranslate2 (défaut `model/whisper-small-ct2`).
- `RAW_AUDIO=1` : publie l'audio brut (WAV/base64) sur `io.user.speak.raw` au lieu de transcrire via Whisper.

### Lancement
```bash
cargo run --release            # microphone local
cargo run --release -- --discord   # désactive le micro local ; écoute le PCM par locuteur
                                    # publié par io_discord sur io.discord.voice.frame
                                    # (une pipeline VAD indépendante par locuteur ;
                                    # transcrit comme le micro local, avec le nom du locuteur)
```

### GPU (CUDA)
Whisper sur CPU ne tient pas la cadence temps réel (voir chiffres plus bas) : le GPU
n'est pas une optimisation optionnelle ici, c'est ce qui rend le mode vocal utilisable.

```bash
CUDAHOSTCXX=g++-15 CUDA_ARCH_LIST="7.5;12.0" cargo build --release --features cuda
```
Compile CTranslate2 avec CUDA (~3 min). Deux pièges :
- `CUDAHOSTCXX` : nvcc refuse les gcc trop récents (gcc 16 ici) ; pointer un gcc plus ancien.
- `CUDA_ARCH_LIST` : **doit contenir au moins une archi < 10.0**. ct2rs route les archis
  ≥ 10 vers des flags nvcc bruts et laisse la liste CMake à `Common`, laquelle inclut
  `compute_53` que CUDA 13 ne connaît plus (`nvcc fatal: Unsupported gpu architecture`).
  `"7.5;12.0"` marche : `7.5` ancre la liste, `12.0` (Blackwell/RTX 50xx) part en gencode.

Sans la feature `cuda`, le binaire reste CPU/OpenBLAS — le choix est figé à la
compilation (`stt_device()`), pas de bascule à l'exécution.

### Benchmark STT
```bash
cargo run --release --bin bench_stt -- --dir /chemin/vers/wavs --device cpu
cargo run --release --features cuda --bin bench_stt -- --dir /chemin/vers/wavs --device cuda
```
Mesuré sur 28 segments réels (61,9 s d'audio), whisper-small, RTX 5070 Ti / 32 cœurs :

| | médiane | p90 | max | RTF |
|---|---|---|---|---|
| CPU (OpenBLAS, 16 threads) | 10038 ms | 12935 ms | 20123 ms | 4,64× |
| GPU (CUDA) | **97 ms** | 117 ms | 180 ms | **0,042×** |

Whisper complète chaque segment à une fenêtre de 30 s : la durée du segment
n'influe presque pas sur le coût. Le CPU tourne à ~4,6× le temps réel — inutilisable
pour de la conversation ; le GPU laisse la marge nécessaire au budget bout-en-bout.

## 🔌 Interface NATS
- **Publie sur** : `io.user.speak` (transcription + `speaker` en mode Discord), `io.user.speak.raw` (`RAW_AUDIO=1` uniquement)
- **Écoute** (`--discord` uniquement) : `io.discord.voice.frame`
