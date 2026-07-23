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

### Variables d'Environnement
- `STT_LANGUAGE` : Langue de transcription (ex: `fr`).
- `NATS_URL` : URL du broker NATS.
- `RAW_AUDIO=1` : publie l'audio brut (WAV/base64) sur `io.user.speak.raw` au lieu de transcrire via Whisper.

### Lancement
```bash
cargo run --release            # microphone local
cargo run --release -- --discord   # désactive le micro local ; écoute le PCM par locuteur
                                    # publié par io_discord sur io.discord.voice.frame
                                    # (une pipeline VAD indépendante par locuteur ;
                                    # implique RAW_AUDIO, Whisper n'est pas chargé)
```

## 🔌 Interface NATS
- **Publie sur** : `io.user.speak`, `io.user.speak.raw`
- **Écoute** (`--discord` uniquement) : `io.discord.voice.frame`
