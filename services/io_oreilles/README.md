# I/O Oreilles (STT)

Le système de transcription audio en temps réel.

### Rôle principal :
- Écouter en permanence le microphone de l'utilisateur.
- Transcrire la voix en texte via Ctranslate2-rs et Whisper.
- Publier des événements `user.msg.speak` sur le réseau NATS dès qu'une phrase est complétée.
