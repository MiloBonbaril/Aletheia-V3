# 🎭 Limbic

This service holds the mood and the wishes of Aletheia. It is the base of her proactivity. It is
written in **Python**.

## 🎯 Functions

- **Owner of the mood:** it keeps the current mood state in memory: an emotion, an intensity from 0
  to 1, and an optional free description.
- **Mood changes:** it receives the requests on `limbic.mood.set` and applies them completely. A new
  state replaces the full previous state.
- **Natural decay:** without reinforcement, the intensity decreases at each tick, to a neutral
  baseline.
- **Broadcast:** it publishes the canonical state on `limbic.mood.update` at each change, from a set
  operation or from the decay. Thus the other services stay synchronized.
- **Boredom gauge and proactivity:** the boredom increases at each tick. It goes to 0 when an
  interaction starts (`cortex.interaction.started`). When it becomes higher than the threshold, the
  service asks the lobe frontal for a subject (`lobe.topic.generate`, request-reply, with a
  placeholder if there is no answer). Then it starts a proactive interaction
  (`limbic.proactive.trigger`).

### The gates

A proactive interaction starts only if the two gates are open:

- **Presence:** a person is in a Discord voice channel (`io.presence.discord_voice`).
- **Time:** the current hour is in the permitted window.

If one gate is closed at the critical moment, the trigger stays pending. The boredom continues to
increase. The trigger occurs when the gates open. The boredom does not have to become higher than
the threshold again.

## ⚙️ Configuration and start

```bash
pip install -r requirements.txt
python main.py
```

### Environment variables (`.env`)

| Variable | Default | Function |
|---|---|---|
| `NATS_URL` | `nats://localhost:4222` | The address of the NATS broker. |
| `MOOD_DECAY_RATE` | `0.05` | The quantity of intensity that the mood loses at each tick. |
| `TICK_INTERVAL_SECONDS` | `30` | The time between two ticks, for the mood and for the boredom. |
| `BOREDOM_INCREMENT_RATE` | `0.01` | The quantity of boredom that the gauge gets at each tick. |
| `BOREDOM_THRESHOLD` | `1.0` | The boredom value that makes a proactive trigger possible. |
| `PROACTIVE_GATE_START_HOUR` | `9` | The first hour of the permitted window (0 to 23). |
| `PROACTIVE_GATE_END_HOUR` | `23` | The last hour of the permitted window (0 to 23). |

The state is in memory only. A restart sets the mood to neutral. This is the same behavior as the
`active_sessions` map of the cortex.

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the pure decision code: `mood.py` (a set operation, the decay, the return to the
neutral baseline) and `boredom.py` (the accumulation, the reset, the two gates, and the pending
trigger behavior).

## 🔌 NATS interface

- **Subscribes to:** `limbic.mood.set`, `cortex.interaction.started`, `io.presence.discord_voice`
- **Publishes on:** `limbic.mood.update`, `limbic.proactive.trigger`
- **Requests:** `lobe.topic.generate`
