---
name: audit
description: Read-only audit of one service or one feature of Aletheia. Give the subject when you invoke the skill.
disable-model-invocation: true
---

# /audit

```
/audit io_voix                     # a service
/audit the RAG recall path         # a feature that crosses services
/audit correlation_id propagation  # a contract
```

The subject is the argument. The deliverable is one report file. The working tree is the same
before and after: you read, you measure, you propose. A proposal stays on paper until the user asks
for it.

Every check ends on a **verdict** with **evidence**. A verdict is one of:

- `OK` — you read the code and it holds.
- `DEFECT` — it is wrong now. Say what breaks and when.
- `RISK` — it holds now and breaks under a named condition.
- `UNKNOWN` — you could not settle it. Say what would settle it.

Evidence is a `file:line` reference, a command output, or a benchmark number. A verdict with no
evidence is not a verdict; it becomes `UNKNOWN`.

## Steps

### 1. Scope

Turn the subject into a written list: the files, the NATS topics, the config files and the env
variables that belong to it. `grep` the topic names across `services/`, because there is no shared
schema package between Rust and Python.

Done when: the list exists, and each item is either "in scope" or "out of scope". If the subject
reads two ways and the two readings give different work, ask the user before you continue.

### 2. Map

Read the code in scope, end to end. Follow the real flow, not the documented flow: the ingress
event, the branch, the state, the egress event.

Done when: every topic that the subject publishes and every topic it subscribes to is written down,
with the publisher side and the subscriber side of each one.

### 3. Check

Apply every lens below to the scope. A lens that does not apply gets one line that says so.

- **Contract** — the payloads against `NATS_TOPICS.md`, on both the publisher side and the
  subscriber side. The `correlation_id` and the `session_id` stay through the chain.
- **Latency** — what sits on the hot path against the targets: time to first audio below 300 ms,
  LLM time to first token below 200 ms. Measure with `services/benchmark`. A hand measurement is
  `UNKNOWN`.
- **Failure** — the behaviour when NATS, PostgreSQL, Qdrant, the GPU or a model file is absent,
  slow, or gives an error. The bus is fire-and-forget: name where a lost event stops a flow with no
  message.
- **Concurrency** — the shared state, the single-fragment constraints, the locks, and what a second
  request does to them.
- **Config** — the env variables that the code reads against the ones that `CLAUDE.md` and the
  `.env` files list. Note the hardcoded `nats://localhost:4222`.
- **Tests** — what `tests/` covers and what it does not. Name the untested branch, not the
  coverage number.
- **Docs** — the drift between the code and `CLAUDE.md`, `NATS_TOPICS.md`, `CONCEPT.md`,
  `PROMPTING.md` and `docs/adr/`.
- **Dead code** — the paths that nothing reaches. A choice that an ADR records is not a defect:
  `docs/adr/0002` keeps the `Groq/` and `Mistral/` interfaces disconnected on purpose.

Done when: every lens carries a verdict, and every `DEFECT` and `RISK` verdict carries its evidence.

### 4. Propose

Give one proposal for each `DEFECT` and each `RISK`. A proposal names: the change, the files it
touches, the cost in size, the new risk it adds, and the way to prove it works (a test, a benchmark
run, a manual step).

When two solutions exist, give both and recommend one. When the correct answer is "keep it and
write an ADR", say that.

Done when: no `DEFECT` and no `RISK` is without a proposal.

### 5. Report

Write the report to `docs/audits/<subject>-<YYYY-MM-DD>.md` in ASD-STE100 Simplified Technical
English, with these sections:

1. **Scope** — what you audited, and what you left out.
2. **Verdicts** — a table: lens, verdict, evidence.
3. **Findings** — the `DEFECT` and `RISK` rows in full, most severe first.
4. **Proposals** — one for each finding, from step 4.
5. **Open questions** — the `UNKNOWN` rows, each with the command, the measurement or the answer
   that would settle it.

Done when: the file exists, every lens from step 3 appears in the table, and `git status` shows the
report as the only change.
