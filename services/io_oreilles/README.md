# I/O Oreilles (STT)

Le système de transcription audio en temps réel.

### Rôle principal :
- Écouter en permanence le microphone de l'utilisateur (via `Whisper.cpp`).
- Transcrire la voix en texte.
- Publier des événements `USER_SPOKE` sur le réseau NATS dès qu'une phrase est complétée.
