# 💬 Lobe Frontal (Gestionnaire LLM)

Le Lobe Frontal est le moteur cognitif de Nexus-V. Écrit en **Python**, il pilote l'intelligence artificielle et structure la pensée de l'entité.

## 🎯 Rôle & Responsabilités

- **Inférence LLM** : Interagit avec l'API Groq (modèle Llama 3) pour générer des réponses.
- **Structuration du Prompt** : Construit un prompt système complexe au format XML pour garantir la cohérence du personnage.
- **Streaming de Fragments** : Découpe les réponses de l'LLM en fragments logiques (basés sur la ponctuation) et les publie sur `lobe.fragment_stream` pour minimiser la latence perçue.
- **Pilotage d'Outils** : Gère le Function Calling pour interagir avec la mémoire (RAG) et le silence.

## ⚙️ Configuration & Lancement

### Dépendances
- Python 3.10+
- API Key Groq.

### Variables d'Environnement
- `GROQ_API_KEY` : Clé d'accès à l'API Groq.
- `NATS_URL` : URL du broker NATS.

### Lancement
```bash
python main.py
```

## 🧠 Configuration du Cerveau (Éditable)

Le comportement de l'IA est piloté par des fichiers Markdown situés dans le dossier `config/`. Ces fichiers sont injectés directement dans le prompt système :

- **`PERSONA.md`** : Définit qui est l'IA, son ton, ses tics de langage et ses règles de conduite.
- **`MEMORY.md`** : Contient les faits immuables et les connaissances fondamentales du monde.
- **`USER.md`** : Stocke les informations sur les utilisateurs et leurs relations avec l'IA.

*L'édition de ces fichiers modifie le comportement de l'entité sans nécessiter de redémarrage du code.*

## 🔌 Interface NATS
- **S'abonne à** : `cortex.prompt`, `lobe.topic.generate` (request-reply, réflexion silencieuse pour la proactivité de `limbic` — voir #15, jamais reliée à `lobe.fragment_stream`)
- **Publie sur** : `lobe.fragment_stream`
