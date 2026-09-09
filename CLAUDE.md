# CLAUDE.md

This file gives guidance to Claude Code (claude.ai/code) for work in this repository.

Write all documentation in this repository in ASD-STE100 Simplified Technical English: simple
present tense, active voice, short sentences, one instruction per sentence, and the same word for
the same thing.

## Project overview

Aletheia (internal codename "Nexus-V") is an autonomous, real-time virtual entity (a VTuber). It
hears, sees, thinks, remembers and speaks. It is an event-driven microservice system. The services
never block each other: the I/O services publish events and react when the results become
available.

This is a hard real-time system. The targets are a time to first audio below 300 ms, and an LLM time
to first token below 200 ms, on consumer hardware. Measure each latency-sensitive change with
`services/benchmark`. Do not measure by hand.

The services communicate only through a central **NATS** bus. It is fire-and-forget publish and
subscribe, plus request-reply for four topics. There is no direct call between two services.

Read `NATS_TOPICS.md` for the full topic and payload contract, and `PROMPTING.md` for the XML schema
of the system prompt, before you change a flow between services. `CONCEPT.md` gives the architecture
reasons.

## Architecture

The services are in `services/<name>`. Their names come from a brain metaphor.

- **cortex** (`services/cortex`, Rust) — the central orchestrator and router. It subscribes to the
  ingress topics (`io.user.msg.text`, `io.user.speak`, `io.user.speak.raw`,
  `limbic.proactive.trigger`), dispatches `cortex.prompt` and `hippocampe.context.build` in
  parallel, publishes `cortex.interaction.started`, and tracks the sessions with `correlation_id`
  and `session_id`.
- **lobe_frontal** (`services/lobe_frontal`, Python) — the LLM engine. It waits for
  `hippocampe.context.ready` before the inference. It builds the XML system prompt with
  `src/prompt_builder.py`. It cuts the answer at punctuation marks and streams the fragments on
  `lobe.fragment_stream`. It controls the function calling (`save_to_memory`, `get_from_memory`,
  `stay_silent`, `set_mood`). It answers `lobe.topic.generate` for the proactivity of `limbic`. An
  embedded Textual TUI (`tui.py`) shows the prompt, the output and the tool activity. The persona,
  the knowledge and the user data are Markdown files in `config/` (`PERSONA.md`, `MEMORY.md`,
  `USER.md`). An edit changes the behavior with no code change and with no restart:
  `PromptBuilder` compares the date of each file before each prompt, and it reads the file again
  only when the date changes. The `Config` tab of `services/terminal` edits the three files.
  - The inference goes to a local llama.cpp server through `OpenAI/interface.py`. `main.py` selects
    this interface at import time. The `Groq/` and `Mistral/` interfaces and the `INTERFACE`
    environment variable are not connected. This is a decision, not a defect: see
    `docs/adr/0002-lobe-frontal-stays-llama-cpp-only.md`. Do not "repair" it.
  - `eval/` is an isolated multi-model bench. It speaks HTTP directly to `llama-server`. It does not
    use NATS. Run it on an idle GPU. Run `python eval/dump_history.py` first, or the bench measures
    the `froid` scenario only.
- **hippocampe** (`services/hippocampe`, Python) — the memory. PostgreSQL (`database.py`) keeps the
  conversation history. Qdrant (`rag_manager.py`) keeps the vector memory. The RAG recall is
  **passive**: it runs automatically for each `hippocampe.context.build` request, in parallel with
  the history read, and goes into the `<recall>` section of the prompt. It runs only when the prompt
  is not empty, thus a voice-only message skips it. `get_from_memory` and `save_to_memory` stay
  available as active LLM tools. This service has its own `docker-compose.yml` for PostgreSQL and
  Qdrant. The root file starts NATS only.
- **limbic** (`services/limbic`, Python) — the mood and the proactivity. It owns the mood state
  (`limbic.mood.set` in, `limbic.mood.update` out, with a decay to a neutral baseline). It holds a
  boredom gauge that starts a proactive interaction (`limbic.proactive.trigger`) behind a presence
  gate and a time gate. It asks the lobe_frontal for the subject.
- **io_oreilles** (Rust) — STT: microphone capture → Silero VAD → CTranslate2 and Whisper →
  `io.user.speak`. With `--discord`, it processes the per-speaker PCM audio from `io_discord`
  instead of the local microphone. The GPU build (`--features cuda`) is necessary for real-time
  speech.
- **io_voix** (Python) — TTS with Audio8 TTS Preview 0.6B on the GPU (torch, INT8 weight-only). It
  consumes `lobe.fragment_stream`. It publishes the audio on `io.voice.speak.audio`, and the time
  references on `io.voice.speak.start` and `.end`. It downloads the model (approximately 1.3 GB)
  into `models/audio8-tts-0.6b/` at the first start. The model is autoregressive, thus `engine.py`
  gives the audio of one fragment in chunks that become longer, to keep the time to first audio low
  (120 ms end to end, real-time factor 0.22). The GPU is necessary, and the CUDA graphs of
  `engine.py` are necessary too: without them the real-time factor is 2.2 and the service holds no
  target. The service handles one fragment at a time, because the KV caches and the captured graphs
  are shared. The output is 44.1 kHz. The voice comes from a reference recording (`A8_VOICE_WAV`,
  `A8_VOICE_TEXT`), because Audio8 has no voice presets. Without a reference the model
  invents a new voice for each fragment: give one.
- **io_discord** (Python) — the Discord gateway (`bot.py`, cogs in `cogs/`). It bridges Discord and
  `io.user.msg.text` / `lobe.fragment_stream`. It also streams the voice audio in the two
  directions, publishes the voice presence, and holds an independent bets function.
- **io_text** — a terminal CLI that injects `io.user.msg.text` events. It is a multi-line editor
  with the `:w`, `:q` and `:c` commands.
- **benchmark** (`services/benchmark`) — it subscribes passively to NATS with an event graph
  (`graphs/E2E.json`, `graphs/T2T.json`) and rebuilds the end-to-end latency of a message.
- **terminal** (`services/terminal`, Python) — the local control panel. A **daemon** (aiohttp) owns
  the processes of the other services and serves a **console** (Preact, no build step) on
  `127.0.0.1:7420`. It starts, stops, rebuilds and monitors each service, and it keeps a buffer of
  2000 log lines for each one in memory. `services.toml` is the manifest: it declares the command,
  the group, the start order, the named profiles and the text files that the `Config` tab edits.
  That tab writes the three prompt files of `lobe_frontal`. The daemon kills every service when it
  stops, and it never restarts a crashed one. It reads the bus figures through the monitoring
  endpoint of NATS (`/varz`, `/connz`), never through a subscription. A `Chat` tab does the work of
  `io_text` from the browser: it is the only part that touches the bus, and it uses two topics only
  (`io.user.msg.text` out, `lobe.fragment_stream` in). See
  `docs/adr/0003-terminal-superviseur-de-processus-local.md` and
  `docs/adr/0004-le-terminal-publie-et-ecoute-un-seul-sujet.md`.
- **io_yeux**, **io_visage** — Twitch and YouTube chat aggregation, and VTube Studio control. These
  two contain a README file only. There is no source code.

Each service starts independently. Each one has its own `requirements.txt` (Python) or `Cargo.toml`
(Rust). There is no shared build system and no workspace.

## Commands

The `terminal` service starts and stops everything else from one web page. Prefer it to the manual
commands below:

```bash
cd services/terminal && ../../venv/bin/python main.py   # then open http://127.0.0.1:7420
```

It owns the processes it starts, thus it kills every service when you stop it.

Start the event bus. All the other services need it:

```bash
docker compose up -d          # NATS: 4222 (clients), 8222 (monitoring)
```

Start the datastores of the hippocampe:

```bash
cd services/hippocampe && docker compose up -d   # PostgreSQL 5432, Qdrant 6333/6334
```

Start a Python service. Each one has its own `requirements.txt`, but in practice they all run
from the single virtual environment at the root, `venv/`, which holds every dependency:

```bash
cd services/<name>
pip install -r requirements.txt
python main.py     # bot.py for io_discord
```

Start a Rust service:

```bash
cd services/cortex        # or services/io_oreilles
cargo run --release
```

Run the benchmark on a live pipeline (NATS and the services must operate):

```bash
cd services/benchmark
pip install -r requirements.txt
python main.py                       # default graph: graphs/E2E.json
python main.py /path/to/graph.json   # a different event graph
```

Stop everything:

```bash
docker compose down
```

### Tests

There is no repository-level test runner, no linter and no formatter. Some services have their own
tests, and they cover the pure functions only:

```bash
cd services/<name> && pytest tests/     # io_discord, io_voix, limbic, lobe_frontal, hippocampe
cd services/cortex && cargo test        # cortex (also io_oreilles)
cd services/terminal && ../../venv/bin/python -m pytest tests/   # terminal
```

### Key environment variables

Each service reads its own `.env` file. The Python services use `python-dotenv`.

**Important:** only `benchmark`, `io_oreilles` and `limbic` read `NATS_URL`. The other services have
the address `nats://localhost:4222` in their source code.

- lobe_frontal: `LLM_MODEL`, `TEMPERATURE`, `TOP_P`, `REASONING_EFFORT`, `MAX_CONCURRENT_INFERENCE`
- hippocampe: `POSTGRES_URL`, `QDRANT_URL`, `QDRANT_PORT`, `RAG_SCORE_THRESHOLD`
- limbic: `MOOD_DECAY_RATE`, `TICK_INTERVAL_SECONDS`, `BOREDOM_INCREMENT_RATE`, `BOREDOM_THRESHOLD`,
  `PROACTIVE_GATE_START_HOUR`, `PROACTIVE_GATE_END_HOUR`
- io_discord: `DISCORD_TOKEN`, `DISCORD_USER_ID`, `DISCORD_GUILD_ID`, `TEXT_CHANNEL_ID`,
  `COMMAND_PREFIX`
- io_voix: `A8_QUANT`, `A8_VOICE_WAV`, `A8_VOICE_TEXT`, `A8_CHUNK_SCHEDULE`, `A8_MODEL_DIR`,
  `MUTE_LOCAL_PLAYBACK`
- io_oreilles: `STT_LANGUAGE`, `STT_MODEL_PATH`, `RAW_AUDIO`, `ORT_DYLIB_PATH`

## Work between services

The services interact only through NATS. When you change a payload or add a topic:

1. Update `NATS_TOPICS.md` to keep the contract correct.
2. Update each publisher and each subscriber of that topic. Use `grep` on the topic name in
   `services/`, because there is no shared schema package between Rust and Python.
3. Keep the `correlation_id`. The benchmark service and the session tracking of the cortex use it to
   rebuild the life cycle of a request.

## Repository rules

This repository is public. Do not commit real conversation data. The history dumps of the eval bench
(`services/lobe_frontal/eval/fixtures/`) and the measurement results
(`services/lobe_frontal/eval/results/`) are in `.gitignore`. Keep them there.

## Agent skills

### Issue tracker

The issues are GitHub Issues on `MiloBonbaril/Aletheia-V3`. Use the `gh` CLI. See
`docs/agents/issue-tracker.md`.

### Triage labels

The default label set is `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single context: `CONTEXT.md` and `docs/adr/` at the root of the repository. See
`docs/agents/domain.md`.
