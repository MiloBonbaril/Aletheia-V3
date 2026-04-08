# 🚀 DOCUMENT DE CONCEPTION ARCHITECTURALE : PROJET "NEXUS-V"

**À l'attention de l'équipe d'ingénierie.**
**Rédigé par :** Ria, Architecte Système.
**Statut :** Approuvé pour développement.

## 1. VISION DU PROJET
Création d'une Entité IA Virtuelle (VTubeuse) autonome, proactive et persistante, capable d'interagir en temps réel avec un environnement complexe (voix, chat Twitch, système d'exploitation). Le système doit garantir une latence sous la seconde (< 1000ms), une résilience totale aux crashs isolés, et s'exécuter sur une configuration matérielle hybride (Local CPU/GPU + API Cloud).

## 2. PARADIGME ARCHITECTURAL
Ce n'est **pas** un monolithe séquentiel. C'est une **Architecture Orientée Événements (Event-Driven) couplée à un Modèle Acteur**.
* **Message Broker Central :** `NATS` (Fire-and-forget, microseconde, Pub/Sub).
* **Désynchronisation Intelligente :** Les I/O (voix, chat) n'attendent jamais le LLM. L'état du monde est bufferisé, et l'IA réagit au monde tel qu'il est au moment où elle est disponible.
* **Exécution Asynchrone :** Streaming de tokens et génération audio chunkée pour masquer la latence d'inférence.

---

## 3. TOPOLOGIE DU MATÉRIEL (Le Split "Gaming / IA")
Afin de préserver la RTX 5070 Ti pour le jeu en direct et le rendu OBS/VTube Studio, l'intelligence est éclatée :
* **CPU Local (Ryzen 9 5950X) :** STT (Whisper.cpp), Vector Database, Orchestrateur, TTS (via ONNX).
* **GPU Local (RTX 5070 Ti) :** Jeu vidéo, VTube Studio, OBS (Encodage NVENC).
* **Cloud (LPU Groq) :** Inférence LLM principale (vitesse extrême, zéro VRAM locale consommée).

---

## 4. DÉCOUPLAGE DES MICRO-SERVICES (Les Acteurs)

### 🧠 A. LE CORTEX (Orchestrateur)
* **Langage :** Rust (ou Go).
* **Rôle :** Le maître du temps. Il s'abonne à tous les topics NATS. Il gère la *Boucle de Proactivité* (envoie un prompt caché si aucun événement depuis X secondes) et gère la *Préemption* (interruption d'urgence du LLM en cas de coupure de parole par l'humain).

### 💬 B. LE LOBE FRONTAL (Gestionnaire LLM)
* **Langage :** Python.
* **Rôle :** Reçoit les ordres de prompt de l'Orchestrateur. Appelle l'API Groq (Llama 3 8B). Gère le **Context Window** (en injectant le Persona ou en mode raw LLM selon les paramètres).
* **Streaming :** Bufferise les tokens, coupe sur la ponctuation forte, et publie les fragments de texte sur le Bus.

### 📚 C. L'HIPPOCAMPE (Mémoire Tri-partite)
* **Mémoire Épisodique (RAG) :** Qdrant/Milvus en local. Alimentée *uniquement* via Function Calling par le LLM (Mémoire Explicite).
* **Mémoire Sémantique :** PostgreSQL (Paramètres, relations viewers, état global).

### 🎙️ D. LES CORTEX SENSORIELS ET MOTEURS (I/O)
* **Oreilles (STT) :** `Whisper.cpp` ciblant matériellement le micro physique. Crée des événements `USER_SPOKE`.
* **Yeux (Twitch) :** Agrégateur de chat pour éviter l'inondation de tokens. Crée des événements `CHAT_SUMMARY`.
* **Cordes Vocales (TTS) :** `Kokoro ONNX`. Transforme les fragments de texte du Bus en `.wav` en mémoire et les joue dans le `Câble-IA` virtuel.
* **Visage (VTube Controller) :** Capte l'audio du `Câble-IA` pour le Lip-Sync, et envoie les tags d'émotion générés par le LLM via WebSocket à VTube Studio.

### 🎛️ E. LE TERMINAL (Frontend d'Administration)
* **Stack :** React/Vue + WebSocket.
* **Rôle :** Panneau de contrôle passif. Modifie les variables dans PostgreSQL. Ne contient **aucune logique métier**. Si le frontend crash, l'IA continue de vivre.

---

## 5. FEUILLE DE ROUTE IMMÉDIATE (Phase 1)
1.  **Semaine 1 :** Installation de NATS. Développement de l'Orchestrateur (Rust) et du Lobe Frontal (Python + Groq). Tests de ping/pong événementiels.
2.  **Semaine 2 :** Intégration de Kokoro (TTS) et Whisper.cpp (STT). Mise en place de la tuyauterie audio virtuelle (VB-Cable).
3.  **Semaine 3 :** Intégration VTube Studio (Lip-Sync audio + WebSocket pour les émotions).
4.  **Semaine 4 :** Développement de l'Hippocampe (Base Vectorielle) et du module d'agrégation Twitch.
5.  **Phase 2 (Ultérieure) :** Implémentation de la "Sandbox" (Module Exécuteur Docker pour donner à l'IA la capacité d'écrire et d'exécuter son propre code).
