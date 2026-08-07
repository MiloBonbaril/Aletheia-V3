# ⚡ Continuous benchmark service

This service measures the latency of Aletheia continuously on the NATS bus. Use it to verify that
the system keeps its real-time requirements.

The service is passive. It subscribes to the topics and it publishes nothing. It cannot have an
effect on the measurement.

An event graph in JSON format (for example `graphs/E2E.json`) gives the topics. The service
subscribes to them and rebuilds the full life cycle of each message.

## 📈 Available graphs

| Graph | Function |
|---|---|
| `graphs/E2E.json` | The full path, from the user message to the end of the speech. |
| `graphs/T2T.json` | The text path only, from the user message to the last token of the lobe frontal. |

## 📊 Measured metrics (end-to-end)

The default graph `graphs/E2E.json` gives these latencies:

1. **Cortex link:** the time that the cortex needs to receive the user message (`io.user.msg.text`,
   `io.user.speak` or `io.user.speak.raw`) and to send it to the lobe frontal (`cortex.prompt`).
2. **Hippocampe memory:** the time to build the context (`hippocampe.context.ready`).
3. **LLM inference (TTFT, time to first token):** the time before the first fragment of the answer
   (`lobe.fragment_stream` with sequence 0).
4. **Audio synthesis time:** the time between the first text token and the start of the speech
   (`io.voice.speak.start`).
5. **LLM generation time:** the full text generation time, from the first fragment to the fragment
   with `is_last` set to `true`.
6. **Speech time:** the time when Aletheia speaks (`io.voice.speak.start` to `io.voice.speak.end`).
7. **Total end-to-end latency:** the full time, from the user request to the end of the speech.

## 🛠️ Installation

The service needs the packages in `requirements.txt`, principally `nats-py` and `rich`.

```bash
pip install -r requirements.txt
```

## 🚀 Start

Start the NATS broker and the services before this command.

```bash
python main.py                     # default graph: graphs/E2E.json
python main.py graphs/T2T.json     # a different graph
python main.py /path/to/graph.json
```

The service reads `NATS_URL`. The default value is `nats://localhost:4222`.

## 📊 Example output

The service shows the steps in real time. Then it draws a summary table and a horizontal timeline.
The labels are in French.

```
⌛ TIMELINE VISUELLE DE LA LATENCE ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
████░░░░░░░░░░░░░░░░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓

 ■ Liaison Cortex: 15.2 ms   ■ Inférence LLM (TTFT): 824.5 ms   ■ Synthèse TTS: 345.1 ms   ■ Lecture Audio: 2.12 s

⏱️  Latence End-to-End Totale: 3.30 s
```

## ✍️ How to write a graph

A graph file contains:

- `name` and `description` — the identification of the graph.
- `ingress_points` — the topics that start a new flow.
- `steps` — a list of steps. Each step has an `id`, a `name`, a `description`, a list of `topics`, a
  `display_color`, and an optional `filter`.

A `filter` is a Python expression on the `payload` variable. Use it to select one message among many
on the same topic, for example `payload.get('sequence') == 0`.
