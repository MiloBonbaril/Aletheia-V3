# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Aletheia (internal codename "Nexus-V") is an autonomous, real-time virtual entity (VTuber) that hears, sees, thinks, remembers, and speaks. It's built as an event-driven microservices system where independent services never block on each other — I/O publishes events and reacts asynchronously as results become available. This is a hard real-time system: target is time-to-first-audio under ~300ms and LLM time-to-first-token under ~200ms, running on consumer hardware. Latency-sensitive changes should be validated against `services/benchmark`.

Services communicate exclusively through a central **NATS** event bus (fire-and-forget pub/sub, plus request-reply for a few RAG topics). There is no direct service-to-service RPC. See `NATS_TOPICS.md` for the full topic/payload contract and `PROMPTING.md` for the LLM system-prompt XML schema — read both before touching cross-service message flow. `CONCEPT.md` has the original architecture rationale (in French).

## Architecture

Services live under `services/<name>` and map to a "brain" metaphor:

- **cortex** (`services/cortex`, Rust) — central orchestrator/router. Subscribes to ingress topics (`io.user.msg.text`, etc.), dispatches `cortex.prompt` + `hippocampe.context.build` in parallel, tracks sessions via `correlation_id`/`session_id`.
- **lobe_frontal** (`services/lobe_frontal`, Python) — LLM engine. Waits on `hippocampe.context.ready` before running inference (Groq/OpenAI-compatible/Mistral interfaces under `Groq/`, `OpenAI/`, `Mistral/`), builds the XML system prompt via `src/prompt_builder.py`, streams response fragments split on punctuation to `lobe.fragment_stream`, and drives function calling (`save_to_memory`, `get_from_memory`, `stay_silent`). Persona/knowledge/user data are hot-editable markdown files in `config/` (`PERSONA.md`, `MEMORY.md`, `USER.md`) — no code change or restart needed to alter behavior.
- **hippocampe** (`services/hippocampe`, Python) — memory. Postgres (`database.py`) for episodic conversation history, Qdrant (`rag_manager.py`) for vector/RAG memory. RAG recall is **passive**: it runs automatically on every `hippocampe.context.build` request in parallel with history lookup and is injected into the `<recall>` prompt section; `get_from_memory`/`save_to_memory` remain available as active LLM tools for targeted queries. Has its own `docker-compose.yml` for Postgres + Qdrant, separate from the root one (NATS only).
- **io_oreilles** (Rust) — STT: mic capture → Silero VAD → CTranslate2/Whisper → `io.user.speak`.
- **io_voix** (Python) — TTS via Kokoro ONNX, consumes `lobe.fragment_stream`. Downloads model weights (~350MB) to `models/` on first run.
- **io_discord** (Python) — Discord bot gateway (`bot.py`, cogs in `cogs/`), bridges Discord ↔ `io.user.msg.text` / `lobe.fragment_stream`.
- **io_text** — terminal CLI for manually injecting `io.user.msg.text` events (multi-line editor with `:w`/`:q`/`:c` commands).
- **io_yeux**, **io_visage**, **terminal** — Twitch/YouTube chat aggregation, VTube Studio lip-sync/expression control, and the admin dashboard (NextJS/TS), respectively; these are stubs/not fully implemented (READMEs describe intended role only, no source yet).
- **benchmark** (`services/benchmark`) — passively subscribes to NATS per an event graph (`graphs/E2E.json`) to reconstruct end-to-end latency for a message across the whole pipeline. Use this to verify performance-sensitive changes, not manual timing.

Each service is independently runnable and has its own `requirements.txt` (Python) or `Cargo.toml` (Rust) — there is no shared build system or workspace.

## Commands

Bring up the event bus (required for anything else to work):
```bash
docker compose up -d          # starts NATS (ports 4222 client, 8222 monitoring)
```

Hippocampe additionally needs its own datastores:
```bash
cd services/hippocampe && docker compose up -d   # Postgres (5432) + Qdrant (6333/6334)
```

Run a Python service (each has its own `requirements.txt`, no shared venv):
```bash
cd services/<name>
pip install -r requirements.txt
python main.py     # or bot.py for io_discord
```

Run a Rust service:
```bash
cd services/cortex        # or services/io_oreilles
cargo run --release
```

Run the benchmark harness against a live pipeline (services + NATS must already be running):
```bash
cd services/benchmark
pip install -r requirements.txt
python main.py                       # default graph: graphs/E2E.json
python main.py /path/to/graph.json   # custom event graph
```

Stop everything:
```bash
docker compose down
```

There is no test suite, linter, or formatter configured in this repository at present.

### Key environment variables
Each service loads its own `.env` (via `python-dotenv` for Python services). Notable ones: `NATS_URL` (all services, default `nats://localhost:4222`), `GROQ_API_KEY`/`LLM_MODEL`/`TEMPERATURE`/`TOP_P`/`REASONING_EFFORT`/`MAX_CONCURRENT_INFERENCE` (lobe_frontal), `DISCORD_TOKEN`/`DISCORD_USER_ID`/`DISCORD_GUILD_ID`/`TEXT_CHANNEL_ID` (io_discord), `KOKORO_VOICE`/`KOKORO_MODELS_DIR` (io_voix), `STT_LANGUAGE` (io_oreilles).

## Working across services

Since services only interact via NATS, when changing a message payload or adding a new topic:
1. Update `NATS_TOPICS.md` to keep the contract documented.
2. Update every publisher and subscriber of that topic — grep across `services/` for the topic string, since there's no shared schema/types package between Rust and Python.
3. Preserve `correlation_id` propagation; it's what lets the benchmark service and cortex's session tracking reconstruct a request's lifecycle across services.
