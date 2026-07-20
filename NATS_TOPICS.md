# 📡 Registre des Topics NATS (Nexus-V)

Ce document définit les contrats de données et les flux de messages circulant sur le broker NATS.

## 🔄 Flux de Données Principal

`io.user.msg.text` $\rightarrow$ **Cortex** $\rightarrow$ `cortex.prompt` + `hippocampe.context.build` (parallèle) $\rightarrow$ **Hippocampe** construit le contexte $\rightarrow$ `hippocampe.context.ready` $\rightarrow$ **Lobe Frontal** $\rightarrow$ `lobe.fragment_stream` $\rightarrow$ **Cortex / I/O Voix**

---

## 📋 Détail des Topics

### 📥 Entrées Utilisateurs (Ingress)

#### `io.user.msg.text`
Événement déclenché lorsqu'un utilisateur envoie un message textuel (via `io_text`, `io_discord` ou STT converti).
- **Payload (JSON) :**
  ```json
  {
    "text": "Bonjour Aletheia !",
    "images": ["url_image_1", "url_image_2"]
  }
  ```

#### `io.user.speak`
Événement déclenché par le pipeline STT (`io_oreilles`) lors de la détection d'une phrase complète (mode Whisper).
- **Payload (JSON) :**
  ```json
  {
    "text": "Contenu transcrit par Whisper",
    "confidence": 0.98
  }
  ```

#### `io.user.speak.raw`
Événement déclenché par le service `io_oreilles` lors de la détection de parole en mode RAW (`RAW_AUDIO=true`).
- **Payload (JSON) :**
  ```json
  {
    "audio": "base64...",
    "format": "wav"
  }
  ```

---

### 👥 Présence (I/O Discord)

#### `io.presence.discord_voice`
Signal temps réel (pas de décroissance/TTL) publié par `io_discord` à chaque changement d'occupation d'un salon vocal du serveur configuré (arrivée/départ d'un membre non-bot).
- **Payload (JSON) :**
  ```json
  {
    "occupied": true
  }
  ```

---

### 🧠 Cognition (Processing)

#### `cortex.prompt`
L'ordre d'inférence envoyé par le Cortex au Lobe Frontal. Inclut un `correlation_id` pour corréler avec le contexte mémoire.
- **Payload (JSON) :**
  ```json
  {
    "prompt": "Texte brut du prompt final",
    "images": [],
    "audio": "base64... (optionnel)",
    "correlation_id": "uuid-v4",
    "source": "proactive (optionnel, absent si origine utilisateur)"
  }
  ```

#### `hippocampe.context.build`
Demande de construction de contexte envoyée par le Cortex à l'Hippocampe. Déclenche la récupération parallèle de l'historique PostgreSQL et de la recherche RAG Qdrant.
- **Payload (JSON) :**
  ```json
  {
    "prompt": "Texte du message utilisateur",
    "correlation_id": "uuid-v4",
    "n_history": 20
  }
  ```

#### `hippocampe.context.ready`
Contexte pré-calculé publié par l'Hippocampe. Contient l'historique de conversation et les souvenirs RAG pertinents. Le Lobe Frontal attend ce message avant de lancer l'inférence LLM.
- **Payload (JSON) :**
  ```json
  {
    "correlation_id": "uuid-v4",
    "history": [{"role": "user", "content": "..."}, ...],
    "rag_results": "Souvenir 1\nSouvenir 2",
    "context_summary": ""
  }
  ```

#### `lobe.fragment_stream`
Flux de tokens/fragments renvoyés par le Lobe Frontal pour permettre un TTS temps réel.
- **Payload (JSON) :**
  ```json
  {
    "sequence": 1,
    "text": "Bonjour ",
    "is_last": false
  }
  ```

---

### 📚 Mémoire (Hippocampe)

#### `hippocampe.history.add`
Ajout d'un message dans l'historique PostgreSQL (fire-and-forget).
- **Payload (JSON) :**
  ```json
  {
    "role": "user|assistant|tool",
    "content": "Contenu du message"
  }
  ```

#### `hippocampe.rag.query`
Recherche active dans la mémoire RAG (utilisé par le tool `get_from_memory` du LLM). Mode request-reply.
- **Payload (JSON) :**
  ```json
  {
    "prompt": "Requête de recherche"
  }
  ```
- **Réponse :**
  ```json
  {
    "result": "Résultats de la recherche"
  }
  ```

#### `hippocampe.rag.add`
Ajout d'un souvenir dans la mémoire RAG (utilisé par le tool `save_to_memory` du LLM). Mode request-reply.
- **Payload (JSON) :**
  ```json
  {
    "content": "Information à mémoriser"
  }
  ```
- **Réponse :**
  ```json
  {
    "result": "Successfully saved: ..."
  }
  ```

---

### 🔁 Proactivité (Limbic ↔ Cortex)

#### `limbic.proactive.trigger`
Déclencheur d'interaction proactive (fire-and-forget), publié par `limbic` (implémentation à venir). Traité par le Cortex exactement comme `io.user.msg.text` (même fan-out `cortex.prompt` + `hippocampe.context.build`), avec `source: "proactive"` sur le payload `cortex.prompt` dispatché.
- **Payload (JSON) :**
  ```json
  {
    "prompt": "Sujet ou amorce de conversation à évoquer"
  }
  ```

#### `cortex.interaction.started`
Signal fire-and-forget publié par le Cortex à chaque dispatch d'un événement d'ingress (`io.user.msg.text`, `io.user.speak.raw`, `limbic.proactive.trigger`, ...), quelle que soit son origine.
- **Payload (JSON) :**
  ```json
  {
    "correlation_id": "uuid-v4",
    "source": "user|proactive"
  }
  ```

---

### 🎭 Envies & Humeur (Limbic)

#### `limbic.mood.set`
Demande de changement d'humeur (fire-and-forget). Remplace intégralement l'état courant.
- **Payload (JSON) :**
  ```json
  {
    "emotion": "taquine",
    "intensity": 0.7,
    "description": "un peu moqueuse, sourire en coin (optionnel)"
  }
  ```

#### `limbic.mood.update`
État d'humeur canonique rediffusé par `limbic` à chaque changement (set ou décroissance périodique vers la baseline neutre).
- **Payload (JSON) :**
  ```json
  {
    "emotion": "taquine",
    "intensity": 0.7,
    "description": "un peu moqueuse, sourire en coin (optionnel)"
  }
  ```

---

### 🔊 Sorties & Actions (Egress)

#### `io.voice.tts` (Prévu)
Envoi de texte au service de synthèse vocale.
- **Payload (JSON) :**
  ```json
  {
    "text": "Fragment à synthétiser",
    "emotion": "happy"
  }
  ```

#### `io.face.emotion` (Prévu)
Commande d'expression faciale pour VTube Studio.
- **Payload (JSON) :**
  ```json
  {
    "emotion": "surprised",
    "intensity": 0.8
  }
  ```

---

## 🛠️ Contrats Internes (Cortex)

Le Cortex utilise une enveloppe interne pour le tracking des sessions et la corrélation :

```rust
pub struct EventEnvelope {
    pub correlation_id: Uuid,
    pub session_id: String,
    pub timestamp_ms: u128,
    pub payload: EventPayload,
}
```
