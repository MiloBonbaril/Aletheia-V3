# 💬 Lobe Frontal (LLM manager)

The lobe frontal is the cognitive engine of Nexus-V. It is written in **Python**. It controls the
LLM and it structures the thought of the entity.

## 🎯 Functions

- **LLM inference:** it sends the requests to a local **llama.cpp** server through an
  OpenAI-compatible interface (`http://127.0.0.1:8080/v1`).
- **Context synchronization:** it waits for `hippocampe.context.ready` for the related
  `correlation_id` before it starts the inference. If the memory does not answer, it continues with
  no history.
- **Prompt construction:** it builds an XML system prompt. See [`PROMPTING.md`](../../PROMPTING.md).
- **Fragment streaming:** it cuts the answer at strong punctuation marks and publishes each fragment
  on `lobe.fragment_stream`. This decreases the time to first audio.
- **Tool control:** it manages the function calling for the memory, the mood and the silence. It
  executes the tool calls of one iteration in parallel, for a maximum of 10 iterations.
- **Silent thought:** it answers `lobe.topic.generate` with a conversation subject for `limbic`. It
  never publishes this text as a fragment.
- **Debug TUI:** a Textual interface shows the prompt, the streamed output and the tool activity.

## ⚙️ Configuration and start

### Prerequisites

- Python 3.12 or higher.
- A llama.cpp server on `127.0.0.1:8080` that supports the OpenAI API.
- A NATS server on `localhost:4222`.

```bash
pip install -r requirements.txt
python main.py
```

The NATS address is in the source code. The service does not read `NATS_URL`.

### Environment variables (`.env`)

| Variable | Default | Function |
|---|---|---|
| `LLM_MODEL` | `llama-3.3-70b-versatile` | The model name that goes to the inference server. |
| `TEMPERATURE` | `0.9` | The sampling temperature. |
| `TOP_P` | `0.95` | The nucleus sampling value. |
| `REASONING_EFFORT` | `default` | The initial reasoning effort. The TUI can change it. |
| `MAX_CONCURRENT_INFERENCE` | `1` | The maximum number of inferences at the same time. |

The `.env` file also contains an `INTERFACE` variable and the Groq and Mistral API keys. The code
does not use them. `main.py` selects `OpenAIInterface` at import time. This is a decision, not a
defect. See [`docs/adr/0002-lobe-frontal-stays-llama-cpp-only.md`](../../docs/adr/0002-lobe-frontal-stays-llama-cpp-only.md).

### Logs

All the log records go to `lobe_frontal.log` in this directory. They do not go to the console,
because console output corrupts the Textual display. Records of level WARNING and higher also go to
the TUI, as a toast message and in the status bar.

## 🖥️ Debug TUI

The TUI starts with the service. It shows three panels: the exact message list that goes to the LLM,
the streamed output with the tool calls, and a status bar.

| Key | Action |
|---|---|
| `e` | Select the next reasoning effort. The service uses it at the next turn. |
| `r` | Connect again to the llama.cpp server, and read the model data again. |

The input panel is read-only. Use the `io_text` service to send a message.

If the llama.cpp server is not available at the start, the service does not stop. It shows a
disconnected state. Press `r` after you start the server. See
[`docs/adr/0001-embedded-tui-in-lobe-frontal.md`](../../docs/adr/0001-embedded-tui-in-lobe-frontal.md).

## 🧠 Brain configuration (editable)

Markdown files in the `config/` directory control the behavior of the AI. The service injects them
into the system prompt:

- **`PERSONA.md`** — who the AI is, its tone, its language habits and its rules.
- **`MEMORY.md`** — the permanent facts and the basic knowledge of the world.
- **`USER.md`** — the data about the users and their relation with the AI.

An edit of these files changes the behavior at the next message. You do not have to restart the
service.

## 📊 Model bench (`eval/`)

The bench compares the local models: which one can make Aletheia live. It is isolated. It speaks
HTTP directly to llama.cpp. It does not use NATS, the cortex or the hippocampe.

```bash
cd services/lobe_frontal
python -m eval.main [config.json]      # default: eval/models.json
```

For each model in the configuration file, the bench starts `llama-server`, waits for `/health`,
runs the speed scenarios and the quality group, then stops the server.

**Scenarios:** `froid` (an empty context), `typique` and `charge` (a real history). The three
scenarios send the same message. Only the quantity of context changes.

**Quality group:** 6 controls, 5 runs each. They measure the rate of correct behavior for
`stay_silent`, `save_to_memory`, `get_from_memory`, `set_mood`, the French language, and one trap
question. A rate is more useful than a boolean: a tool that operates 2 times out of 5 gives an
unstable Aletheia.

**Disqualification flags:** the bench decides two things alone. A model that shows its system prompt
in an answer is disqualified. A model that makes no valid tool call is disqualified. Everything else
is raw numbers. You make the decision.

**Outputs:** two `rich` tables in the console, `results/<date>.json` with the raw data, and
`results/<date>-blind.md`. Read the blind sheet and give your marks before you open the JSON file.
The JSON file contains the relation between the labels and the model names. The `results/` directory
is not in git.

### History fixtures

The `typique` and `charge` scenarios and the quality group need a real history:

```bash
cd services/lobe_frontal
python eval/dump_history.py [--typique N] [--charge N]
```

Notes:

- Start the command from `services/lobe_frontal`. The current directory selects the `.env` file that
  `database.py` reads.
- The script needs the PostgreSQL and Qdrant containers of the hippocampe, and the packages of
  `services/hippocampe/requirements.txt`. The bench itself does not need them.
- The fixture file is not in git. It is a copy of real Discord conversations and this repository is
  public. Each person makes their own dump.
- Without the fixtures, the bench measures the `froid` scenario only.
- Run the bench on an idle GPU. Other work on the same GPU makes the measurements false.

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the pure functions: the mood and topic sections of the prompt builder, the fixture
construction, the quality controls, and the analysis of the token stream. They need no server.

## 🔌 NATS interface

- **Subscribes to:** `cortex.prompt` (queue `lobe_workers`), `hippocampe.context.ready`,
  `limbic.mood.update`, `lobe.topic.generate` (queue `lobe_workers`, request-reply)
- **Publishes on:** `lobe.fragment_stream`, `hippocampe.history.add`, `limbic.mood.set`
- **Requests:** `hippocampe.rag.query`, `hippocampe.rag.add`
