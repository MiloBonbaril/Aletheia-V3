# 🚀 Architecture concept: project "Nexus-V"

**Audience:** the engineering team.
**Status:** in operation, and in development.

## 1. Project vision

Build an autonomous virtual AI entity (a VTuber). The entity is proactive and persistent. It
interacts in real time with a complex environment: voice, text, Twitch chat and the operating
system. The system keeps a very low latency. It continues to operate when one service fails. It runs
on a mixed hardware configuration: a local CPU, a local GPU and, if necessary, a cloud API.

## 2. Architecture paradigm

The system is **not** a sequential monolith. It is an **event-driven architecture**.

- **Central message broker:** `NATS`, with fire-and-forget publish/subscribe. Four topics use
  request-reply.
- **Controlled decoupling:** an I/O service never waits for the LLM. The system buffers the state of
  the world. The AI reacts to the events when it becomes available.
- **Asynchronous execution:** the LLM streams its tokens, and the TTS synthesizes small chunks. This
  hides the inference latency.

## 3. Hardware topology

The intelligence is divided between the devices. This keeps the GPU available for the game and for
the OBS and VTube Studio rendering.

- **Local CPU:** the orchestrator (Rust), the vector database, the TTS (ONNX).
- **Local GPU:** the LLM inference (llama.cpp), the STT (CTranslate2 with CUDA), the game, VTube
  Studio and the OBS encoding (NVENC).
- **Cloud:** not in use. The Groq and Mistral interfaces are present in the code but they are not
  connected. See `docs/adr/0002-lobe-frontal-stays-llama-cpp-only.md`.

The first design used the Groq LPU API for the inference. The project moved to a local llama.cpp
server, for the cost, the privacy and the control of the model.

## 4. The microservices

### 🧠 A. The cortex (orchestrator)

- **Language:** Rust.
- **Function:** the central nervous system. It routes the event flows between the sensors (I/O) and
  the cognitive core.
- **Status:** it routes the messages, it tracks the sessions with a `correlation_id`, and it routes
  the proactive triggers from `limbic`. Preemption is in the roadmap.

### 💬 B. The frontal lobe (LLM manager)

- **Language:** Python.
- **Function:** it controls the local llama.cpp server through an OpenAI-compatible interface.
- **Prompt engineering:** it uses an XML structure. The structure keeps the persona, the core
  memory, the user data and the context separate.
- **Streaming:** it buffers the tokens, cuts the text at strong punctuation marks, and publishes the
  text fragments on the NATS bus.
- **Debug TUI:** a Textual interface shows what goes to the LLM, what comes back, and the tool-call
  activity. See `docs/adr/0001-embedded-tui-in-lobe-frontal.md`.

### 📚 C. The hippocampe (memory)

- **Episodic memory:** PostgreSQL. It keeps the full conversation history.
- **Semantic memory (RAG):** Qdrant, on the local machine. The search is passive: it starts
  automatically for each user message. Function calling gives an active search for complex queries.

### 🎭 D. The limbic system (mood and proactivity)

- **Language:** Python.
- **Function:** it holds the mood state and the boredom gauge. The boredom increases at each tick.
  It goes to 0 when an interaction starts. When the boredom becomes higher than the threshold,
  `limbic` asks the frontal lobe for a subject and starts a proactive interaction.
- **Gates:** a proactive interaction starts only if a person is in a Discord voice channel, and only
  during the permitted hours.

### 🎙️ E. The sensory and motor services (I/O)

- **Ears (STT):** the pipeline is: audio capture → **Silero VAD** → **CTranslate2 and Whisper**. It
  creates `io.user.speak` events. It processes the local microphone, and also each speaker of a
  Discord voice channel.
- **Keyboard (text I/O):** a direct text input service. It creates `io.user.msg.text` events.
- **Discord:** the gateway to the users. It sends the text, it streams the voice audio in the two
  directions, and it publishes the presence signal.
- **Eyes (Twitch):** a chat aggregator that limits the quantity of context. It will create
  `CHAT_SUMMARY` events. Not implemented.
- **Vocal cords (TTS):** `Audio8 TTS 0.6B`. It changes the text fragments into a real-time audio
  stream, in chunks that become longer, to keep the time to first audio low.
- **Face (VTube controller):** lip-sync and expression control through a WebSocket to VTube Studio.
  Not implemented.

### 🎛️ F. The terminal (administration frontend)

- **Function:** a passive control panel for the monitoring and for the global parameters. Not
  implemented.

### ⚡ G. The benchmark (measurement)

- **Function:** it subscribes to the bus and rebuilds the life cycle of a message. It gives the
  latency of each step. It is the only correct way to measure a change of performance.

## 5. Technical references

- [**NATS_TOPICS.md**](NATS_TOPICS.md) — message contracts and flows.
- [**PROMPTING.md**](PROMPTING.md) — the XML schema of the frontal lobe.
- [**docs/adr/**](docs/adr/) — the architecture decisions and their reasons.
