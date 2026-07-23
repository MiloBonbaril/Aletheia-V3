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
Événement déclenché par le service `io_oreilles` lors de la détection de parole en mode RAW (`RAW_AUDIO=true`) ou en mode Discord (`--discord`).
- **Payload (JSON) :**
  ```json
  {
    "audio": "base64...",
    "format": "wav",
    "speaker": "Milo (optionnel, présent uniquement en mode --discord)"
  }
  ```

---

### 🎙️ Audio Discord (I/O Discord → io_oreilles)

#### `io.discord.voice.frame`
Chunk de PCM brut d'un locuteur du salon vocal, publié en continu par `io_discord` dès que le bot a rejoint un salon (`/voice join`), tant qu'il y reste. Consommé uniquement par `io_oreilles` lancé avec `--discord` (le flag désactive la capture micro locale pour ce run), qui fait tourner une détection de parole (VAD) indépendante par locuteur (`speaker_id`) — pour qu'une personne qui se tait ne coupe pas la phrase d'une autre. Une fois qu'un segment de parole se termine, il ressort sur `io.user.speak.raw` avec `speaker` renseigné.
- **Payload (JSON) :**
  ```json
  {
    "speaker_id": "123456789012345678",
    "speaker_name": "Milo",
    "pcm": "base64... (s16le, 48kHz, stéréo)"
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
Déclencheur d'interaction proactive (fire-and-forget), publié par `limbic` quand sa jauge de boredom (`BOREDOM_INCREMENT_RATE`/tick) franchit `BOREDOM_THRESHOLD` **et** que deux gates sont ouvertes : présence (dernier `io.presence.discord_voice` reçu) et horaire (`PROACTIVE_GATE_START_HOUR`-`PROACTIVE_GATE_END_HOUR`). Si un gate est fermé au moment critique, le déclenchement reste en attente (le boredom continue d'accumuler sans jamais redescendre tout seul) et se déclenche dès que les gates s'ouvrent, sans re-franchir le seuil. Le sujet est obtenu via `lobe.topic.generate` (voir ci-dessous) juste avant l'envoi de ce message — les gates ne sont donc pas re-vérifiés après cet appel (jusqu'à quelques secondes) : un changement de présence/horaire pendant l'attente n'annule pas un déclenchement déjà décidé. Traité par le Cortex exactement comme `io.user.msg.text` (même fan-out `cortex.prompt` + `hippocampe.context.build`), avec `source` sur le payload `cortex.prompt` dispatché repris tel quel depuis ce message (ou `"proactive"` par défaut si absent).

`limbic` remet aussi son boredom à 0 localement dès l'émission de ce message (en plus du reset via `cortex.interaction.started` ci-dessous) : ça évite de re-déclencher à chaque tick si le Cortex est injoignable et que l'écho n'arrive jamais.
- **Payload (JSON) :**
  ```json
  {
    "prompt": "Sujet ou amorce de conversation à évoquer",
    "source": "proactive"
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

#### `lobe.topic.generate`
Requête request-reply publiée par `limbic` juste avant `limbic.proactive.trigger`, pour obtenir un sujet réfléchi par le LLM (persona + core_memory, sans historique/RAG) plutôt que le placeholder de #14. Aucune contrainte de latence (rien d'autre n'attend la réponse). Cet appel n'est **jamais** relié à `lobe.fragment_stream` — c'est une réflexion silencieuse, jamais dite à voix haute ni envoyée à Discord. Si `limbic` n'obtient pas de réponse (timeout ou erreur), il retombe sur `PLACEHOLDER_TOPIC`.
- **Requête (JSON) :** `{}` (aucun champ nécessaire, le sujet est dérivé de l'état interne du Lobe Frontal).
- **Réponse (JSON) :**
  ```json
  {
    "topic": "Sujet ou idée que Aletheia a envie d'évoquer"
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

#### `io.voice.speak.start`
Publié par `io_voix` juste avant de jouer/publier l'audio d'un fragment synthétisé (timing, pas d'audio).
- **Payload (JSON) :**
  ```json
  {
    "sequence": 1,
    "text": "Bonjour ",
    "is_last": false
  }
  ```

#### `io.voice.speak.audio`
Audio synthétisé par `io_voix` pour un fragment, publié entre `io.voice.speak.start` et `io.voice.speak.end`, en plus de (et non à la place de) la lecture locale sur la carte son. Absent pour les fragments sans texte (silence de fin de flux). Consommé par `io_discord` (cog `voice`), qui rejoue chaque fragment dans le salon vocal actuellement rejoint (ignoré silencieusement si le bot n'est dans aucun salon) — sans dépendre du haut-parleur local de `io_voix`.
- **Payload (JSON) :**
  ```json
  {
    "sequence": 1,
    "audio": "base64... (WAV, mono, 22050Hz)",
    "format": "wav",
    "is_last": false
  }
  ```

#### `io.voice.speak.end`
Publié par `io_voix` juste après avoir joué/publié l'audio d'un fragment synthétisé (timing, pas d'audio).
- **Payload (JSON) :**
  ```json
  {
    "sequence": 1,
    "is_last": false
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
