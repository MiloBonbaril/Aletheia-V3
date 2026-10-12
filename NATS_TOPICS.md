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

A user sends a text message. The publishers are `io_text`, `io_discord` and the chat tab of
`terminal`. Each publisher adds the name of the speaker to the text.

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

A chunk of raw PCM audio from one speaker in a voice channel. After a successful `/voice join`,
`io_discord` publishes it for each member, from the first packet of that member until that member
leaves the channel. During a silence, the frames hold zeros (`SilenceGeneratorSink`).

Only `io_oreilles` with the `--discord` flag subscribes to this topic. The flag also stops the local
microphone capture for that run. The service runs an independent VAD pipeline for each `speaker_id`.
Thus a person who stops to speak does not cut the sentence of a different person. At the end of a
speech segment, the service transcribes it and publishes `io.user.speak` with the `speaker` field.
When no frame comes from a speaker for 1 s, the service decides that the speaker left. It closes the
open turn of that speaker, publishes it, and removes the pipeline.

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
- **The queue of the lobe_frontal ("the last message wins").** The lobe_frontal answers one message
  at a time. It is busy from the start of a turn until Aletheia stops speaking
  (`io.voice.speak.end` with `is_last: true`). The messages that arrive while it is busy wait.
  When it is free, it takes all the waiting messages:
  - It answers the last user message only.
  - It writes the other user messages to the history (`hippocampe.history.add`, acknowledged), with
    no answer.
  - A proactive prompt (`source` present) gives its place to any user message. A proactive prompt
    that waited is dropped, because a turn ran during the wait.
  - It builds the context of the answered message again (see `hippocampe.context.build`), because
    the context from its arrival time does not hold the last answer and the skipped messages.
  Two guards release the queue: 1 s after the last fragment if no `io.voice.speak.start` came (no
  TTS runs), and 60 s after it in all cases.

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
- The cortex publishes it for each message. The lobe_frontal publishes it again, with the same
  `correlation_id`, for a message that waited in its queue.

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
    "is_last": false,
    "correlation_id": "uuid-v4"
  }
  ```
- `correlation_id` is the one of the `cortex.prompt` that the fragment answers. `io_voix` copies it
  to `io.voice.speak.*`. The benchmark uses it to separate two requests that overlap.
- The fragment with `sequence` 0 gives the time to first token.
- The last fragment has `is_last` set to `true`. Its `text` can be empty, for example after a
  `stay_silent` tool call.

---

### 📚 Memory (hippocampe)

#### `hippocampe.history.add`

Add one message to the PostgreSQL history. Fire-and-forget, or request-reply: when the message has
a reply subject, the hippocampe answers an empty message after the write. The lobe_frontal uses the
reply for the skipped messages of its queue and for the final answer of a turn, because it reads
the history again a few milliseconds later. The hippocampe does the writes in the order of arrival,
thus the reply also confirms the earlier writes of the same publisher.

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

### 👁️ Vision (io_yeux)

#### `io.vision.state`

The description of the screen of the streamer. Fire-and-forget, with no `correlation_id`.
`io_yeux` publishes it after each observation. It also publishes it again at each heartbeat
(`IO_YEUX_HEARTBEAT_SECONDS`, 30 s) when the screen is identical to the reference: this
republication does not call the VLM, keeps `observed_at`, and changes only `checked_at`. Thus
`checked_at` stays less than approximately 30 s old while `io_yeux` operates.

The `lobe_frontal` keeps the last state in memory and injects it in a `<vision>` block at the start
of the last user message. It ignores a state when `checked_at` is older than 90 s: this rule
detects only a stopped `io_yeux`, not a screen that does not change.

`io_yeux` makes no observation during an interaction, because `llama-server` runs with
`--parallel 1`. `cortex.interaction.started` suspends the observations and cancels the observation
in progress. The suspension stops at the first of these events:

- `io.voice.speak.end` with `is_last: true`, if `io_voix` is active (an `io.voice.speak.start` or
  `.end` in the last 10 minutes);
- `lobe.fragment_stream` with `is_last: true`, if `io_voix` is not active;
- 30 s after the start of the suspension.

- **Payload (JSON):**
  ```json
  {
    "observed_at": 1759490000123,
    "checked_at": 1759490000123,
    "trigger": "change",
    "application": "Blender",
    "activity": "Ajuste le rig du bras droit d'un personnage en Pose Mode.",
    "visible_text": ["Pose Mode", "Armature"]
  }
  ```
- `observed_at`: the time of the capture that the VLM described, in milliseconds since the epoch.
- `checked_at`: the last time that the local comparison confirmed that the description is correct.
  It is equal to `observed_at` after an observation, and more recent after a republication.
- `trigger`: the cause of the observation. `change`: the screen is different from the reference,
  above the threshold. `heartbeat`: the heartbeat is due, and the screen is a little different
  from the reference, below the threshold. A republication keeps the `trigger` of the
  observation.
- `application`: the name of the application in the foreground, 80 characters maximum.
- `activity`: one sentence, 200 characters maximum.
- `visible_text`: 5 items maximum, 80 characters maximum each, in the language of the screen.
- `io_yeux` replaces each probable secret with `[masqué]` in `application`, `activity` and
  `visible_text`, before the publication. See the README of `io_yeux`.

---

### 🔊 Outputs and actions (egress)

#### `io.voice.speak.start`

The speaker starts to play a fragment. **The process that plays the sound publishes it**: `io_voix`
when it plays on the local sound card, `io_discord` when it plays in the Discord voice channel. With
`MUTE_LOCAL_PLAYBACK`, `io_voix` plays nothing and does not publish it. It contains no audio.

If `io_discord` cannot play a fragment (no voice connection, or no audio), it publishes
`io.voice.speak.start` and `io.voice.speak.end` at once. Thus the end of an answer is never lost.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "text": "Hello ",
    "is_last": false,
    "correlation_id": "uuid-v4"
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

A fragment with no text gives a message only if it is the last fragment (`is_last: true`). Then
`audio` is an empty string. This message tells the player that the answer ended. A synthesis that
fails on the last fragment gives the same empty message.

The `voice` cog of `io_discord` subscribes to this topic. It plays each fragment in the voice channel
that the bot joined, and it publishes `io.voice.speak.start` and `.end` around the playback.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "audio": "base64... (WAV, mono, 44100 Hz), or an empty string",
    "format": "wav",
    "is_last": false,
    "text": "Hello ",
    "correlation_id": "uuid-v4"
  }
  ```

#### `io.voice.speak.end`

The speaker stopped playing a fragment. The process that plays the sound publishes it, as for
`io.voice.speak.start`. With `is_last: true`, Aletheia stopped speaking: the lobe_frontal then
opens its queue, and `io_yeux` ends its pause. It contains no audio.

- **Payload (JSON):**
  ```json
  {
    "sequence": 1,
    "is_last": false,
    "correlation_id": "uuid-v4"
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

An aggregated summary of the Twitch or YouTube chat (`io_chat`).

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
| lobe_frontal | `cortex.prompt`, `hippocampe.context.ready`, `limbic.mood.update`, `io.vision.state`, `io.voice.speak.start`, `io.voice.speak.end`, `lobe.topic.generate` (reply) | `lobe.fragment_stream`, `hippocampe.history.add` (also request), `hippocampe.context.build` (a message that waited), `limbic.mood.set`, `hippocampe.rag.query` (request), `hippocampe.rag.add` (request) |
| hippocampe | `hippocampe.context.build`, `hippocampe.history.add` (optional reply), `hippocampe.rag.query`, `hippocampe.rag.add` | `hippocampe.context.ready` |
| limbic | `limbic.mood.set`, `cortex.interaction.started`, `io.presence.discord_voice` | `limbic.mood.update`, `limbic.proactive.trigger`, `lobe.topic.generate` (request) |
| io_oreilles | `io.discord.voice.frame` (`--discord` only) | `io.user.speak`, `io.user.speak.raw` |
| io_voix | `lobe.fragment_stream` | `io.voice.speak.audio`; `io.voice.speak.start`, `io.voice.speak.end` (local playback only) |
| terminal | `lobe.fragment_stream` | `io.user.msg.text` |
| io_discord | `lobe.fragment_stream`, `io.voice.speak.audio` | `io.user.msg.text`, `io.presence.discord_voice`, `io.discord.voice.frame`, `io.voice.speak.start`, `io.voice.speak.end` |
| io_text | — | `io.user.msg.text` |
| io_yeux | `cortex.interaction.started`, `io.voice.speak.start`, `io.voice.speak.end`, `lobe.fragment_stream` | `io.vision.state` |
| benchmark | all topics of the loaded graph | — |
