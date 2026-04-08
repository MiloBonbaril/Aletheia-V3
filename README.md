# Nexus-V

Entité IA Virtuelle (VTubeuse) autonome, proactive et persistante basée sur une architecture orientée événements (Event-Driven) avec un broker de messages (NATS).

## Architecture

Le projet adopte une approche "mono-repo" pour faciliter le développement de ses différents micro-services. Chaque dossier dans `services/` peut être considéré comme une brique indépendante de l'entité.

- **Cortex** (`services/cortex/`) : L'Orchestrateur central (Rust).
- **Lobe Frontal** (`services/lobe_frontal/`) : Le Gestionnaire LLM (Python avec Groq Llama 3).
- **Hippocampe** (`services/hippocampe/`) : La mémoire Épisodique (Qdrant) et Sémantique (PostgreSQL).
- **I/O Oreilles** (`services/io_oreilles/`) : Speech-to-Text via Whisper.cpp.
- **I/O Yeux** (`services/io_yeux/`) : Agrégateur d'événements du chat Twitch.
- **I/O Voix** (`services/io_voix/`) : Text-to-Speech via Kokoro ONNX.
- **I/O Visage** (`services/io_visage/`) : Contrôleur WebSocket pour VTube Studio.
- **Terminal** (`services/terminal/`) : Frontend d'Administration Web pour le monitoring et la configuration.

Pour un aperçu complet des intentions architecturales, du splitting Gaming/IA, et du paradigme utilisé, veuillez vous référer à [CONCEPT.md](CONCEPT.md).
