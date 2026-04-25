# Nexus-V

Nexus-V est une entité IA virtuelle (VTubeuse) autonome, proactive et persistante. Elle repose sur une **architecture orientée événements (Event-Driven)** utilisant **NATS** comme broker de messages central, permettant un découplage total entre la perception (I/O) et la cognition.

## 🧩 Architecture

Le projet est organisé en micro-services spécialisés, chacun gérant un aspect spécifique du système :

### 🧠 Noyau Cognitif
- **Cortex** (`services/cortex/`) : L'orchestrateur central écrit en Rust. Il route les flux d'événements et gère la logique de coordination globale.
- **Lobe Frontal** (`services/lobe_frontal/`) : Le moteur de réflexion (Python). Il pilote l'LLM (via Groq/Llama 3) en utilisant une structuration de prompt XML sophistiquée.
- **Hippocampe** (`services/hippocampe/`) : Le système de mémoire, gérant le stockage sémantique (PostgreSQL) et vectoriel (Qdrant) pour le RAG.

### 👂 Perception & Action (I/O)
- **I/O Oreilles** (`services/io_oreilles/`) : Pipeline STT (Speech-to-Text) avec VAD (Silero) et Whisper (CTranslate2).
- **I/O Voix** (`services/io_voix/`) : Synthèse vocale (TTS) via Kokoro ONNX.
- **I/O Yeux** (`services/io_yeux/`) : Interface avec le monde extérieur (ex: Twitch chat).
- **I/O Discord** (`services/io_discord/`) : Passerelle de communication via un Bot Discord.
- **I/O Visage** (`services/io_visage/`) : Contrôle moteur et expressions via VTube Studio.
- **I/O Texte** (`services/io_text/`) : Interface textuelle directe pour le debug et l'interaction rapide.

### 🛠️ Outils
- **Terminal** (`services/terminal/`) : Interface d'administration et de monitoring du système.

## 📚 Documentation Technique

Pour approfondir le fonctionnement du projet, consultez les documents suivants :

- [**CONCEPT.md**](CONCEPT.md) : Vision architecturale et principes de conception.
- [**NATS_TOPICS.md**](NATS_TOPICS.md) : Registre complet des topics NATS et contrats de données.
- [**PROMPTING.md**](PROMPTING.md) : Détails sur la structure XML des prompts du Lobe Frontal.

Chaque service dispose également de son propre `README.md` pour son installation et sa configuration spécifique.
