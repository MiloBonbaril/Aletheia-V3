# 📡 NATS topic registry (Nexus-V)

This document defines the data contracts and the message flows on the NATS bus. It is the only
contract between the services. There is no shared schema package between Rust and Python.

If you change a payload or add a topic:

1. Update this document.
2. Update each publisher and each subscriber. Use `grep` on the topic name in `services/`.
3. Keep the `correlation_id`. The cortex and the benchmark service use it to follow a request.

## 🔄 Main data flow

`io.user.msg.text` → **Cortex** → `cortex.prompt` + `hippocampe.context.build` (in parallel) →
**Hippocampe** builds the context → `hippocampe.context.ready` → **Lobe Frontal** →
`lobe.fragment_stream` → **Cortex / io_voix / io_discord**

---

## 📋 Topic details

### 📥 User inputs (ingress)

#### `io.user.msg.text`

A user sends a text message. The publishers are `io_text` and `io_discord`. Each publisher adds the
name of the speaker to the text.

- **Payload (JSON):**
  ```json
  {
    "text": "Milo said: Hello Aletheia!",
    "images": ["url_image_1", "url_image_2"]
  }
  ```
- `images` is optional. `io_discord` adds a maximum of 5 image attachments.

#### `io.user.speak`

The STT pipeline (`io_oreilles`) found a complete sentence. This applies to the local microphone and
to a Discord voice channel (`--discord`). The cortex adds the name of the speaker in front of the
transcript when the name is present.

- **Payload (JSON):**
  ```json
  {
    "text": "Text that Whisper transcribed",
    "speaker": "Milo (optional, only in --discord mode)"
  }
  ```

#### `io.user.speak.raw`

The `io_oreilles` service publishes this topic only in RAW mode (`RAW_AUDIO=true`). The audio goes
to the LLM without transcription.

The Discord mode does not use this topic. It transcribes the audio (see `io.user.speak`), because
raw audio went into the history and made the `hippocampe.context.ready` message larger than the
NATS `max_payload` limit.

- **Payload (JSON):**
  ```json
  {
    "audio": "base64...",
    "format": "wav",
    "speaker": "Milo (optional)"
  }
  ```

---

### 🎙️ Discord audio (io_discord → io_oreilles)

#### `io.discord.voice.frame`

A chunk of raw PCM audio from one speaker in a voice channel. `io_discord` publishes it continuously
after a successful `/voice join`, and until the bot leaves the channel.

Only `io_oreilles` with the `--discord` flag subscribes to this topic. The flag also stops the local
microphone capture for that run. The service runs an independent VAD pipeline for each `speaker_id`.
Thus a person who stops to speak does not cut the sentence of a different person. At the end of a
speech segment, the service transcribes it and publishes `io.user.speak` with the `speaker` field.

- **Payload (JSON):**
  ```json
  {
    "speaker_id": "123456789012345678",
    "speaker_name": "Milo",
    "pcm": "base64... (s16le, 48 kHz, stereo)"
  }
  ```

---

### 👥 Presence (io_discord)

#### `io.presence.discord_voice`

`io_discord` publishes this signal at each change of occupation of a voice channel in the configured
guild. A change is an arrival or a departure of a member that is not a bot. The signal has no decay
and no TTL.

- **Payload (JSON):**
  ```json
  {
    "occupied": true
  }
  ```

---

### 🧠 Cognition (processing)

#### `cortex.prompt`

The inference order. The cortex sends it to the lobe_frontal.

- **Payload (JSON):**
  ```json
  {
    "prompt": "Final prompt text",
    "images": [],
    "audio": "base64... (optional)",
    "correlation_id": "uuid-v4",
    "source": "proactive (optional, absent for a user message)"
  }
  ```
- For raw audio, `prompt` contains `"<name> said (voice):"`, or an empty string if the speaker is
  unknown. The hippocampe uses this empty prompt to skip the RAG search.

#### `hippocampe.context.build`

The cortex asks the hippocampe for a context. The hippocampe starts the PostgreSQL history read and
the Qdrant RAG search in parallel.

- **Payload (JSON):**
  ```json
  {
    "prompt": "User message text",
    "correlation_id": "uuid-v4",
    "n_history": 20
  }
  ```
- The RAG search runs only if `prompt` is not empty. Thus a voice message that carries audio only
  does not pay the cost of an embedding search.

#### `hippocampe.context.ready`

The context that the hippocampe calculated. The lobe_frontal waits for this message before it starts
the inference. If the memory fails, the hippocampe publishes an empty context. The lobe_frontal
continues with no history.

- **Payload (JSON):**
  ```json
  {
    "correlation_id": "uuid-v4",
    "history": [{"role": "user", "content": "..."}],
    "rag_results": "Memory 1\nMemory 2",
    "context_summary": ""
  }
  ```

#### `lobe.fragment_stream`

The stream of text fragments from the lobe_frontal. The fragments make real-time TTS possible.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "text": "Hello ",
    "is_last": false
  }
  ```
- The fragment with `sequence` 0 gives the time to first token.
- The last fragment has `is_last` set to `true`. Its `text` can be empty, for example after a
  `stay_silent` tool call.

---

### 📚 Memory (hippocampe)

#### `hippocampe.history.add`

Add one message to the PostgreSQL history. Fire-and-forget.

- **Payload (JSON):**
  ```json
  {
    "role": "user|assistant|tool",
    "content": "Message content"
  }
  ```

#### `hippocampe.rag.query`

An active search in the RAG memory. The `get_from_memory` tool of the LLM uses it. Request-reply,
with a timeout of 5 s.

- **Payload (JSON):**
  ```json
  {
    "prompt": "Search query"
  }
  ```
- **Reply:**
  ```json
  {
    "result": "Search results"
  }
  ```

#### `hippocampe.rag.add`

Add one memory to the RAG store. The `save_to_memory` tool of the LLM uses it. Request-reply, with a
timeout of 5 s.

- **Payload (JSON):**
  ```json
  {
    "content": "Information to memorize"
  }
  ```
- **Reply:**
  ```json
  {
    "result": "Successfully saved: ..."
  }
  ```

---

### 🔁 Proactivity (limbic ↔ cortex)

#### `limbic.proactive.trigger`

A proactive interaction starts. Fire-and-forget. `limbic` publishes this message when its boredom
gauge (`BOREDOM_INCREMENT_RATE` for each tick) becomes higher than `BOREDOM_THRESHOLD`, and when the
two gates are open:

- the presence gate — the last `io.presence.discord_voice` message shows an occupied channel;
- the time gate — the current hour is between `PROACTIVE_GATE_START_HOUR` and
  `PROACTIVE_GATE_END_HOUR`.

If one gate is closed at the critical moment, the trigger stays pending. The boredom continues to
increase and does not decrease alone. The trigger occurs when the gates open, and the boredom does
not have to become higher than the threshold again.

`limbic` gets the subject from `lobe.topic.generate` immediately before it publishes this message.
It does not check the gates again after that call. Thus a change of presence or of hour during the
call (some seconds) does not cancel a decided trigger.

The cortex processes this message exactly as `io.user.msg.text`. It does the same fan-out to
`cortex.prompt` and `hippocampe.context.build`. It copies the `source` field into the `cortex.prompt`
payload, or uses `"proactive"` if the field is absent.

`limbic` also sets its boredom to 0 immediately after it publishes this message. This is in addition
to the reset through `cortex.interaction.started`. Thus `limbic` does not trigger again at each tick
if the cortex is not available and the echo does not come back.

- **Payload (JSON):**
  ```json
  {
    "prompt": "Subject or conversation opener",
    "source": "proactive"
  }
  ```

#### `cortex.interaction.started`

The cortex publishes this signal for each ingress event that it dispatches (`io.user.msg.text`,
`io.user.speak`, `io.user.speak.raw`, `limbic.proactive.trigger`), independently of the origin.
Fire-and-forget.

- **Payload (JSON):**
  ```json
  {
    "correlation_id": "uuid-v4",
    "source": "user|proactive"
  }
  ```

#### `lobe.topic.generate`

`limbic` sends this request immediately before `limbic.proactive.trigger`. The lobe_frontal answers
with a subject that the LLM selected. The LLM uses the persona and the core memory only. It does not
use the history or the RAG. Request-reply.

There is no latency constraint, because no other service waits for the answer. This call is never
connected to `lobe.fragment_stream`. It is a silent thought. Aletheia does not speak it and
`io_discord` does not receive it. If `limbic` gets no answer (timeout or error), it uses
`PLACEHOLDER_TOPIC`.

- **Request (JSON):** `{}` — no field is necessary. The subject comes from the internal state of the
  lobe_frontal.
- **Reply (JSON):**
  ```json
  {
    "topic": "Subject or idea that Aletheia wants to speak about"
  }
  ```

---

### 🎭 Mood (limbic)

#### `limbic.mood.set`

A request to change the mood. Fire-and-forget. It replaces the full current state. The `set_mood`
tool of the LLM publishes it.

- **Payload (JSON):**
  ```json
  {
    "emotion": "teasing",
    "intensity": 0.7,
    "description": "a little mocking, with a small smile (optional)"
  }
  ```

#### `limbic.mood.update`

The canonical mood state. `limbic` publishes it again at each change. A change is a set operation or
a periodic decay to the neutral baseline.

- **Payload (JSON):**
  ```json
  {
    "emotion": "teasing",
    "intensity": 0.7,
    "description": "a little mocking, with a small smile (optional)"
  }
  ```

---

### 🔊 Outputs and actions (egress)

#### `io.voice.speak.start`

`io_voix` publishes this message immediately before it plays or publishes the audio of a synthesized
fragment. It gives a time reference. It contains no audio.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "text": "Hello ",
    "is_last": false
  }
  ```

#### `io.voice.speak.audio`

The synthesized audio of one fragment. `io_voix` publishes it between `io.voice.speak.start` and
`io.voice.speak.end`. The local playback on the sound card continues in parallel, if
`MUTE_LOCAL_PLAYBACK` is not set.

`io_voix` synthesizes each fragment in chunks and plays them as they become available, but it
publishes **one message for each fragment**, not one for each chunk. Thus this topic keeps its
meaning. The audio is 44.1 kHz since the change to the Audio8 engine. Consumers read the rate in the
WAV header. They must not assume a rate.

There is no message for a fragment that has no text, for example the silent fragment at the end of a
stream.

The `voice` cog of `io_discord` subscribes to this topic. It plays each fragment in the voice channel
that the bot joined. If the bot is in no channel, it ignores the message.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "audio": "base64... (WAV, mono, 44100 Hz)",
    "format": "wav",
    "is_last": false
  }
  ```

#### `io.voice.speak.end`

`io_voix` publishes this message immediately after it plays or publishes the audio of a synthesized
fragment. It gives a time reference. It contains no audio.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "is_last": false
  }
  ```

---

### 🔮 Planned topics

These topics have a specification, but no publisher and no subscriber.

#### `io.voice.tts` (planned)

Send text to a different speech synthesis service.

```json
{
  "text": "Fragment to synthesize",
  "emotion": "happy"
}
```

#### `io.face.emotion` (planned)

Send a facial expression command to VTube Studio (`io_visage`).

```json
{
  "emotion": "surprised",
  "intensity": 0.8
}
```

#### `io.chat.msg` (planned)

An aggregated summary of the Twitch or YouTube chat (`io_yeux`).

---

## 🛠️ Internal contracts (cortex)

The cortex uses an internal envelope to track the sessions and the correlation:

```rust
pub struct EventEnvelope {
    pub correlation_id: Uuid,
    pub session_id: String,
    pub timestamp_ms: u128,
    pub payload: EventPayload,
}
```

All ingress events use the session identifier `global_session`. The sessions are in memory only. A
restart of the cortex clears them.

---

## 📊 Topic map by service

| Service | Subscribes to | Publishes on |
|---|---|---|
| cortex | `io.user.msg.text`, `io.user.speak`, `io.user.speak.raw`, `limbic.proactive.trigger`, `lobe.fragment_stream` | `cortex.prompt`, `hippocampe.context.build`, `cortex.interaction.started` |
| lobe_frontal | `cortex.prompt`, `hippocampe.context.ready`, `limbic.mood.update`, `lobe.topic.generate` (reply) | `lobe.fragment_stream`, `hippocampe.history.add`, `limbic.mood.set`, `hippocampe.rag.query` (request), `hippocampe.rag.add` (request) |
| hippocampe | `hippocampe.context.build`, `hippocampe.history.add`, `hippocampe.rag.query`, `hippocampe.rag.add` | `hippocampe.context.ready` |
| limbic | `limbic.mood.set`, `cortex.interaction.started`, `io.presence.discord_voice` | `limbic.mood.update`, `limbic.proactive.trigger`, `lobe.topic.generate` (request) |
| io_oreilles | `io.discord.voice.frame` (`--discord` only) | `io.user.speak`, `io.user.speak.raw` |
| io_voix | `lobe.fragment_stream` | `io.voice.speak.start`, `io.voice.speak.audio`, `io.voice.speak.end` |
| io_discord | `lobe.fragment_stream`, `io.voice.speak.audio` | `io.user.msg.text`, `io.presence.discord_voice`, `io.discord.voice.frame` |
| io_text | — | `io.user.msg.text` |
| benchmark | all topics of the loaded graph | — |
