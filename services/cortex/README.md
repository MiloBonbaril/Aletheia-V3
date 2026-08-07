# 🧠 Cortex (the orchestrator)

The cortex is the central nervous system of Nexus-V. It is written in **Rust**. It routes the event
flows between the I/O services and the cognitive core.

## 🎯 Functions

- **Event routing:** it receives the ingress messages, and it dispatches `cortex.prompt` and
  `hippocampe.context.build` in parallel. It does not wait for the memory.
- **Session tracking:** it follows each interaction with a `correlation_id` and a `session_id`. All
  ingress events use the session `global_session`. The sessions are in memory only.
- **Speaker attribution:** it puts the name of the speaker in front of a voice transcript. For raw
  audio, it builds the text `"<name> said (voice):"`, or an empty text if the name is unknown. An
  empty prompt tells the hippocampe to skip the RAG search.
- **Proactivity:** it processes `limbic.proactive.trigger` exactly as a user message. It keeps the
  `source` field to identify the origin.
- **Interaction signal:** it publishes `cortex.interaction.started` for each dispatch. The `limbic`
  service uses this signal to reset its boredom gauge.
- **Fragment monitoring:** it receives `lobe.fragment_stream` and counts the fragments of the
  session.

## ⚙️ Configuration and start

### Prerequisites

- A [NATS server](https://nats.io/) on `localhost:4222`.
- The Rust toolchain (Cargo), edition 2021.

### Configuration

The NATS address is `nats://localhost:4222` in the source code (`src/main.rs`). The service reads no
environment variable. Change the address in the code if your broker is at a different address.

Use `RUST_LOG` to select the quantity of log output, for example `RUST_LOG=debug`.

### Start

```bash
cargo run --release
```

## 🧪 Tests

```bash
cargo test
```

The tests cover the pure functions: the label of the interaction source, and the construction of the
prompt text for a voice message with or without a speaker name.

## 🔌 NATS interface

- **Subscribes to:** `io.user.msg.text`, `io.user.speak`, `io.user.speak.raw`,
  `limbic.proactive.trigger`, `lobe.fragment_stream`
- **Publishes on:** `cortex.prompt`, `hippocampe.context.build`, `cortex.interaction.started`
