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

**Correlation ID**:
The UUID that the cortex gives to one interaction. It travels on `cortex.prompt`,
`hippocampe.context.build`, `hippocampe.context.ready` and `cortex.interaction.started`. It lets the
lobe_frontal find the correct context, and it lets the cortex track the session.
_Avoid_: request id, trace id
