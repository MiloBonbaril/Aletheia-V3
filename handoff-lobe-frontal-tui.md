# Handoff: lobe_frontal embedded Textual TUI

## Goal for next session

Implement the ticket breakdown below. **Work one ticket at a time via `/implement`, clearing context between tickets** — this keeps each session in Claude Code's "smart zone" (under ~128k tokens) instead of carrying the full planning history (this session used ~130k tokens just to plan; implementation shouldn't inherit that).

## Where this came from (don't re-derive — read these instead)

- **Spec / PRD**: [GitHub issue #2](https://github.com/MiloBonbaril/Aletheia-V3/issues/2) — problem statement, 18 user stories, full implementation/testing/out-of-scope decisions.
- **Tickets** (all labeled `ready-for-agent`, native GitHub blocking edges already wired):
  - [#3](https://github.com/MiloBonbaril/Aletheia-V3/issues/3) Fix & isolate `get_model_details()` metadata — **no blockers**
  - [#4](https://github.com/MiloBonbaril/Aletheia-V3/issues/4) Embedded TUI shell (input panel + streamed output) — **no blockers**
  - [#5](https://github.com/MiloBonbaril/Aletheia-V3/issues/5) Enrich output panel (tool-calls + turn metadata) — blocked by #4
  - [#6](https://github.com/MiloBonbaril/Aletheia-V3/issues/6) Logging split + in-TUI warning/error surfacing — blocked by #4
  - [#7](https://github.com/MiloBonbaril/Aletheia-V3/issues/7) Reasoning-effort cycling + status bar — blocked by #3, #4
  - [#8](https://github.com/MiloBonbaril/Aletheia-V3/issues/8) Reconnect keybinding + non-fatal cold start — blocked by #3, #4
  - **Current frontier (no open blockers): #3 and #4** — start here, can be done in parallel/either order.
- **ADRs**: `docs/adr/0001-embedded-tui-in-lobe-frontal.md` (why embedded, not standalone; the NATS-vs-llama.cpp reconnect asymmetry is deliberate), `docs/adr/0002-lobe-frontal-stays-llama-cpp-only.md` (why `INTERFACE`/Groq/Mistral stay unwired — don't "fix" this).
- **Glossary**: `CONTEXT.md` (repo root) — canonical term "Reasoning effort" (not "thinking effort").
- **Repo agent-skill config** (from `/setup-matt-pocock-skills`, run this session): `CLAUDE.md`'s `## Agent skills` section + `docs/agents/{issue-tracker,triage-labels,domain}.md`. Issue tracker is GitHub via `gh` CLI, already authenticated as `MiloBonbaril` in this environment.

## Key facts about the code (services/lobe_frontal/) — established during this session's exploration

- `main.py` hardcodes `interface = OpenAIInterface()` at import time; ignores `INTERFACE` env var. `OpenAIInterface` (`OpenAI/interface.py`) targets `base_url="http://127.0.0.1:8080/v1"` — this is the local llama.cpp server.
- `get_model_details()` in `OpenAI/interface.py` combines SDK `models.list()` + a raw HTTP `GET /v1/models` to extract `context_length`/`supported_reasoning_efforts`, falling back to `["none","low","medium","high"]`. **User suspects this is buggy** — ticket #3 needs to actually diagnose it, not just assume a fix; root cause wasn't determined during planning.
- `REASONING_EFFORT` is a module-level global read once from `.env`, passed into `interface.get_stream(...)` per request — mutating it at runtime is naturally picked up next turn, no extra plumbing needed there.
- `Groq/interface.py` and `Mistral/interface.py` have working `get_stream()` but are dead code (never imported by `main.py`) — leave them dead, per ADR-0002.
- `prompt_handler()` builds `messages` via `PromptBuilder.build(...)`, then runs `while turn_count < 10` which can append tool results and loop — the input panel (ticket #4) needs to reflect `messages` as it mutates across these iterations.
- Logging: currently one `logging.StreamHandler` to stdout, logger name `"LobeFrontal"` with child loggers per module (e.g. `"LobeFrontal.OpenAIInterface"`) — a single handler attached at `"LobeFrontal"` catches everything (ticket #6).
- No TUI library in `requirements.txt` yet — Textual needs adding.
- `services/io_text` already owns interactive prompt injection — this is why the new TUI's input panel is spec'd as strictly read-only (don't add a second text-entry path).
- Startup currently hard-crashes (`raise Exception(...)` in `main()`) if llama.cpp isn't reachable — ticket #8 replaces this with a non-fatal, visibly-disconnected state. The equivalent NATS-failure path is deliberately left untouched (out of scope).
- This repo has **no test suite/linter/formatter configured** (per root `CLAUDE.md`) and this work doesn't introduce one — only tickets #3 and #7 have a specced pure-function seam with a lightweight assert-based self-check (no pytest).

## Suggested skills for next session

- **`mattpocock-skills:implement`** — the primary workflow. Feed it one ticket at a time (start with #3 or #4), let it work the frontier, clear context between tickets as the ticket-breakdown instructions specify.
- **`mattpocock-skills:tdd`** — specifically when implementing ticket #3 (metadata-parsing pure function) and ticket #7 (effort-cycling pure function) — these are the two seams the spec calls out for actual test coverage.
- **`ponytail:ponytail`** (full mode — was active all this session, carry it forward) — keep the lazy-ladder discipline, especially since this pass adds a new dependency (Textual) and touches `main.py`'s TTFT-critical hot path; question any abstraction beyond what each ticket's acceptance criteria actually require.
- **`verify`** (built-in) — after each ticket, actually run `lobe_frontal` against llama.cpp and drive the behavior (there's no test suite to lean on for the TUI/glue parts — acceptance criteria are manual by design).
- **`code-review`** (built-in) — review each ticket's diff before marking it done, given the lack of CI/test-suite safety net.

## Redactions

`services/lobe_frontal/.env` contains live `GROQ_API_KEY`/`MISTRAL_API_KEY` values — it was `cat`'d in this session's tool output (not reproduced here) to inspect config. It's gitignored and was never committed or pasted into any spec/ticket/doc. No key values appear in this handoff or in any artifact produced this session; don't paste them into future ones either.
