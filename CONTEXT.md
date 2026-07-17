# Aletheia (Nexus-V)

Autonomous, real-time virtual entity (VTuber) built as event-driven microservices communicating over NATS. This glossary covers vocabulary specific to Aletheia's domain, not general programming or AI concepts.

## Language

**Reasoning effort**:
A per-request parameter controlling how much internal deliberation the LLM performs before responding, exposed by OpenAI-compatible reasoning-capable models (e.g. via `reasoning_effort` on `lobe_frontal`'s inference call). The set of valid values is model-dependent and must be discovered from the inference server, not assumed.
_Avoid_: thinking effort, reasoning level
