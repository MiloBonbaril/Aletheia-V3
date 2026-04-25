# 📡 Registre des Topics NATS (Nexus-V)

Ce document définit les contrats de données et les flux de messages circulant sur le broker NATS.

## 🔄 Flux de Données Principal

`io.user.msg.text` $\rightarrow$ **Cortex** $\rightarrow$ `cortex.prompt` $\rightarrow$ **Lobe Frontal** $\rightarrow$ `lobe.fragment_stream` $\rightarrow$ **Cortex / I/O Voix**

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
Événement déclenché par le pipeline STT (`io_oreilles`) lors de la détection d'une phrase complète.
- **Payload (JSON) :**
  ```json
  {
    "text": "Contenu transcrit par Whisper",
    "confidence": 0.98
  }
  ```

---

### 🧠 Cognition (Processing)

#### `cortex.prompt`
L'ordre d'inférence envoyé par le Cortex au Lobe Frontal.
- **Payload (JSON) :**
  ```json
  {
    "prompt": "Texte brut du prompt final",
    "images": []
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
