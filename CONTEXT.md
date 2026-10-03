# Aletheia (Nexus-V)

An autonomous, real-time virtual entity (a VTuber). It is a set of event-driven microservices that
communicate over NATS. This glossary gives the vocabulary that is specific to the Aletheia domain.
It does not give general programming or AI vocabulary.

## Language

**Reasoning effort**:
A parameter of one request. It controls the quantity of internal deliberation that the LLM does
before it answers. Reasoning-capable models that use the OpenAI API expose it, for example through
`reasoning_effort` in the inference call of `lobe_frontal`. The set of permitted values depends on
the model. Read the values from the inference server. Do not assume them.
_Avoid_: thinking effort, reasoning level

**Fragment**:
One part of an answer of the LLM. The `lobe_frontal` service cuts the token stream at strong
punctuation marks and publishes each part on `lobe.fragment_stream`. The TTS synthesizes one
fragment at a time. Thus Aletheia starts to speak before the LLM completes the answer.
_Avoid_: chunk, sentence, segment

**Segment**:
One period of continuous speech of one speaker, between two silences. The VAD in `io_oreilles`
delimits it, and the STT transcribes it.
_Avoid_: utterance, clip

**Passive recall**:
The RAG search that the `hippocampe` service starts automatically for each user message. The result
goes into the `<recall>` section of the system prompt. It is different from the active search, which
the LLM starts with the `get_from_memory` tool.
_Avoid_: automatic RAG, auto-recall

**Boredom**:
A gauge in the `limbic` service. It increases at each tick and goes to 0 when an interaction starts.
When it becomes higher than its threshold, and when the gates are open, Aletheia starts a
conversation without a user message.
_Avoid_: idleness, boredom score

**Gate**:
A condition that `limbic` verifies before a proactive trigger. There are two gates: the presence of
a person in a Discord voice channel, and the permitted time window.
_Avoid_: guard, condition, filter

**Mood**:
The emotional state of Aletheia: an emotion name, an intensity from 0 to 1, and an optional free
description. The `limbic` service is the only owner of this state. Without reinforcement, the
intensity decreases to a neutral baseline.
_Avoid_: emotion state, feeling

**Observation**:
One description of the screen by the VLM. `io_yeux` sends a capture to `llama-server` and publishes
the result on `io.vision.state`. A capture is not an observation: `io_yeux` captures the screen once
each second, and it observes only when the screen changes.
_Avoid_: analysis, vision call, screenshot

**Reference**:
The capture of the last successful observation, in the 160×90 grayscale form. `io_yeux` compares
each new capture with the reference, not with the previous capture. Thus a slow change, for example
a scroll, becomes an observation when the sum of the small changes is above the threshold. The
reference changes only after a successful observation.
_Avoid_: previous frame, baseline

**Correlation ID**:
The UUID that the cortex gives to one interaction. It travels on `cortex.prompt`,
`hippocampe.context.build`, `hippocampe.context.ready` and `cortex.interaction.started`. It lets the
lobe_frontal find the correct context, and it lets the cortex track the session.
_Avoid_: request id, trace id

**Daemon**:
The process of the `terminal` service that owns the managed services. It starts them, it stops
them, it reads their output and it samples their resources. It serves the console on
`127.0.0.1:7420`. It kills every managed service when it stops.
_Avoid_: supervisor, backend, server

**Console**:
The web interface that the daemon serves. It shows the state of each managed service, the resources
of the host, and the logs. It sends commands, and it makes no decision.
_Avoid_: dashboard, panel, UI, terminal

**Managed service**:
One entry of `services/terminal/services.toml`. It is a process that the daemon starts as a child,
or a Docker Compose stack that it starts detached. `llama-server` is a managed service, although it
is not in `services/`. `io_visage` and `io_chat` are not, because they have no source code.
_Avoid_: managed process, unit, target

**Profile**:
A named subset of managed services in `services/terminal/services.toml`. The daemon starts a profile
in the order of the manifest. It is different from a `gate` of the limbic, which is a condition.
_Avoid_: preset, launch set, scenario

**Ticket**:
One Markdown file in the `tickets/` folder, with a flat front matter. It is one item of the roadmap.
Its status is one of five French slugs: `en-attente`, `en-cours`, `termine`, `bloque`,
`amelioration-continue`. It is different from a GitHub issue, which is a report that comes from
outside. There is no synchronisation between the two.
_Avoid_: task, card, issue

**Watch**:
A ticket with the status `amelioration-continue`. It never ends. The project has three watches: the
LLM model, the STT model and the TTS model. Each trial adds one dated line, with a measurement, to
the body of the ticket.
_Avoid_: permanent ticket, recurring task
