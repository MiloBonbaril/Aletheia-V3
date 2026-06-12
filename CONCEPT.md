# 🚀 DOCUMENT DE CONCEPTION ARCHITECTURALE : PROJET "NEXUS-V"

**À l'attention de l'équipe d'ingénierie.**
**Statut :** En production / Évolutif.

## 1. VISION DU PROJET
Création d'une Entité IA Virtuelle (VTubeuse) autonome, proactive et persistante, capable d'interagir en temps réel avec un environnement complexe (voix, texte, chat Twitch, système d'exploitation). Le système garantit une latence ultra-faible, une résilience totale aux crashs isolés, et s'exécute sur une configuration matérielle hybride (Local CPU/GPU + API Cloud).

## 2. PARADIGME ARCHITECTURAL
Ce n'est **pas** un monolithe séquentiel. C'est une **Architecture Orientée Événements (Event-Driven)**.
* **Message Broker Central :** `NATS` (Fire-and-forget, Pub/Sub).
* **Désynchronisation Intelligente :** Les I/O n'attendent jamais le LLM. L'état du monde est bufferisé, et l'IA réagit aux événements dès qu'elle est disponible.
* **Exécution Asynchrone :** Streaming de tokens et génération audio chunkée pour masquer la latence d'inférence.

---

## 3. TOPOLOGIE DU MATÉRIEL (Le Split "Gaming / IA")
Afin de préserver la RTX pour le jeu en direct et le rendu OBS/VTube Studio, l'intelligence est éclatée :
* **CPU Local :** STT (Whisper via CTranslate2), Vector Database, Orchestrateur (Rust), TTS (via ONNX).
* **GPU Local :** Jeu vidéo, VTube Studio, OBS (Encodage NVENC).
* **Cloud (LPU Groq) :** Inférence LLM principale (vitesse extrême, zéro VRAM locale consommée).

---

## 4. DÉCOUPLAGE DES MICRO-SERVICES (Les Acteurs)

### 🧠 A. LE CORTEX (Orchestrateur)
* **Langage :** Rust.
* **Rôle :** Le système nerveux central. Il route les flux d'événements entre les capteurs (I/O) et le noyau cognitif. 
* **État actuel :** Implémente le routage strict des messages. La *Boucle de Proactivité* et la *Préemption* sont prévues dans la roadmap.

### 💬 B. LE LOBE FRONTAL (Gestionnaire LLM)
* **Langage :** Python.
* **Rôle :** Pilote l'API Groq (Llama 3). 
* **Ingénierie de Prompt :** Utilise une structure **XML sophistiquée** pour séparer strictement le Persona, la Mémoire Core, les informations Utilisateurs et le Contexte.
* **Streaming :** Bufferise les tokens, coupe sur la ponctuation forte, et publie les fragments de texte sur le Bus NATS.

### 📚 C. L'HIPPOCAMPE (Mémoire)
* **Mémoire Épisodique (RAG) :** Qdrant en local. Recherche passive déclenchée automatiquement à la réception de chaque message utilisateur, avec possibilité de recherche active via Function Calling pour les requêtes complexes.
* **Mémoire Sémantique :** PostgreSQL (Paramètres, relations viewers, état global).

### 🎙️ D. LES CORTEX SENSORIELS ET MOTEURS (I/O)
* **Oreilles (STT) :** Pipeline optimisé : Capture Audio $\rightarrow$ **VAD (Silero)** $\rightarrow$ **STT (CTranslate2/Whisper)**. Crée des événements `io.user.speak`.
* **Clavier (Text I/O) :** Service d'entrée textuelle directe. Crée des événements `io.user.msg.text`.
* **Yeux (Twitch) :** Agrégateur de chat pour optimiser la fenêtre de contexte. Crée des événements `io.chat.msg`.
* **Cordes Vocales (TTS) :** `Kokoro ONNX`. Transforme les fragments de texte en flux audio temps réel.
* **Visage (VTube Controller) :** Synchronisation labiale (Lip-Sync) et contrôle des expressions via WebSocket vers VTube Studio.

### 🎛️ E. LE TERMINAL (Frontend d'Administration)
* **Rôle :** Panneau de contrôle passif pour le monitoring et la modification des paramètres globaux.

---

## 5. RÉFÉRENCES TECHNIQUES
Pour des détails d'implémentation précis, se référer aux documents suivants :
- [**NATS_TOPICS.md**](NATS_TOPICS.md) : Contrats de messages et flux.
- [**PROMPTING.md**](PROMPTING.md) : Schéma XML du Lobe Frontal.
