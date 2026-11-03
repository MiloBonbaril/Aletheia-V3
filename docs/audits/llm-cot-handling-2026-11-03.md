# Audit: the LLM calls and their reasoning (CoT) control — 2026-11-03

## 1. Scope

The subject is each call that goes to an LLM, and how each call controls the reasoning (the chain
of thought, "CoT") of the model.

### In scope

| Item | Kind | Role |
|---|---|---|
| `services/lobe_frontal/OpenAI/interface.py` | code | `get_stream` (the conversation) and `get_completion` (the topic) |
| `services/lobe_frontal/main.py` | code | `run_turn`, `topic_generate_handler`, the effort value |
| `services/lobe_frontal/tui.py` | code | the `e` key that changes the effort |
| `services/lobe_frontal/eval/main.py`, `eval/stream.py`, `eval/models.json` | code, config | the bench |
| `services/io_yeux/main.py` | code | the screen description |
| `services/terminal/services.toml` | config | the five `llama-server*` entries and their reasoning flags |
| `services/lobe_frontal/.env` | config | `REASONING_EFFORT=none` |
| `/mnt/forge/ai/models/Gemma-4/MTP-QAT-12B/chat_template.jinja` | config | the template of the production model |
| `lobe.topic.generate`, `lobe.fragment_stream`, `hippocampe.history.add` | NATS topics | the topic request, the spoken output, the history |
| `REASONING_EFFORT`, `IO_YEUX_MAX_TOKENS`, `IO_YEUX_LLAMA_URL` | env | the effort, the vision budget, the vision server |

### Out of scope

- `services/lobe_frontal/Groq/` and `Mistral/`: they are not connected (`docs/adr/0002`). One line
  in the table only.
- `hippocampe`: the RAG embeddings do not use a chat model and have no reasoning.
- `cortex`, `limbic`, `io_voix`, `io_oreilles`, `io_discord`, `benchmark`: they do not call an LLM.
  `limbic` sends `lobe.topic.generate` only.

### The llama.cpp rules that this audit uses

The production binary is `llama-server` version `0.8.0-dev (build 12062, commit 29c720eaf)`. Its
source is not on the disk. The source of the prism-ml fork (`/home/milo/Forge/dev/tools/prism-ml/llama.cpp`,
2026-10-02) is on the disk, and the verdicts below use it. The `--help` text of the production
binary agrees with it on every flag below.

- R1. With `--reasoning auto` (the default), the server enables the thinking when the template
  supports it (`tools/server/server-context.cpp:1416-1420`). Thus a request with no
  `enable_thinking` and no `reasoning_effort` **thinks** on a thinking template.
- R2. The `chat_template_kwargs` of the request go over the kwargs of the command line
  (`tools/server/server-common.cpp:1296-1311`). A request with `enable_thinking: true` thinks also
  on a server that started with `--reasoning off`.
- R3. `reasoning_effort: "none"` disables the thinking. Any other value goes to the template as the
  `reasoning_effort` kwarg (`server-common.cpp:1313-1322`).
- R4. With `--reasoning-format auto` (the default), the server puts the thoughts in
  `delta.reasoning_content`, not in `delta.content`.
- R5. `--reasoning-budget` is `-1` by default: no limit on the thinking tokens.

The Gemma-4 template sets `enable_thinking | default(false)` (`chat_template.jinja:186`), but R1
gives it the value `true` before the template reads it.

## 2. Verdicts

### 2.1 The CoT control of each call

| # | Call | `enable_thinking` sent | `reasoning_effort` sent | Where the thoughts go | Verdict |
|---|---|---|---|---|---|
| C1 | `lobe_frontal` conversation (`interface.py:162-177`) | `effort != "none"` | the effort | `reasoning_content`, never read (`main.py:412`) | `OK` with `.env`; `RISK` F2, F3 |
| C2 | `lobe_frontal` topic (`interface.py:150-160`) | nothing | nothing | `reasoning_content`, never read | `DEFECT` F1 |
| C3 | `io_yeux` screen (`io_yeux/main.py:100-117`) | `false` | nothing | none | `OK` |
| C4 | eval bench (`eval/main.py:122-143`) | `effort != "none"` | the effort | `reasoning_content`, never read (`stream.py:78`) | `RISK` F4, F5 |
| C5 | `Groq/`, `Mistral/` interfaces | — | — | — | `OK` (not connected, `docs/adr/0002`) |

### 2.2 The reasoning flags of each server

| Entry (`services.toml`) | `--reasoning` | Other CoT flag | Result for a request with no kwarg | Verdict |
|---|---|---|---|---|
| `llama-server` (Gemma-4 12B) | auto (default) | custom template, `enable_thinking` default false | thinks (R1) | `DEFECT` F1 (through C2) |
| `llama-server-qwen` | `off` | `--chat-template-kwargs '{"enable_thinking":false}'` | does not think | `RISK` F3, docs F6 |
| `llama-server-bonsai` | `auto` | — | thinks if the template supports it | `UNKNOWN` Q3 |
| `llama-server-luth` | auto (default) | — | thinks if the template supports it | `UNKNOWN` Q3 |
| `llama-server-lfm3-vl` | auto (default) | — | C3 sends `false` | `OK` |

### 2.3 The lenses

| Lens | Verdict | Evidence |
|---|---|---|
| Contract | `OK` | No CoT text goes on `lobe.fragment_stream` or `hippocampe.history.add`: only `delta.content` is read (`lobe_frontal/main.py:412`) and stored (`main.py:491`). `correlation_id` is not touched by this subject. `lobe.topic.generate` returns `{"topic": ...}` from `message.content` only (`interface.py:160`). |
| Contract (template) | `RISK` F7 | The tool loop sends the assistant message back with no `reasoning_content` (`main.py:457-461`). The Gemma template renders the thoughts of the current turn when they are present (`chat_template.jinja:238-242`). With the thinking on, the model loses its own reasoning between two tool calls. |
| Latency | `DEFECT` F1 | The topic call thinks with no limit (R1, R5), with no `max_tokens`, and it holds `INFERENCE_SEMAPHORE` (`main.py:232-233`). A user turn that arrives then waits at `main.py:390`. Not measured: see Q1. |
| Latency | `RISK` F2 | When the effort is not `none`, the first fragment waits for the end of the thinking. No budget (R5), no `max_tokens` (`interface.py:169-177`). |
| Failure | `RISK` F8 | When the server does not extract the thoughts (`--reasoning-format none`, or a template that the parser does not know), the thoughts arrive in `delta.content`. `take_fragment` has no filter for `<think>` or `<|channel>` (`src/fragments.py:9-23`): `io_voix` speaks them, and the history keeps them. |
| Failure | `OK` | C3 detects the cut JSON with `finish_reason == "length"` (`io_yeux/main.py:124-125`). C2 returns `""` on an error, and `limbic` uses its placeholder (`limbic/main.py:102-109`). |
| Concurrency | `DEFECT` F1 | Same evidence as the latency row: one slot (`--parallel 1`), one semaphore, and a topic call with an unlimited thinking in it. The limit is the httpx timeout of 30 s (`interface.py:107`). |
| Config | `RISK` F2 | The code default of `REASONING_EFFORT` is `"default"` (`main.py:37`, `eval/main.py:60`), and `"default" != "none"` gives `enable_thinking: true` (`interface.py:167`). Only `.env` keeps the thinking off. `lobe_frontal/README.md:54` documents `default` as the default. |
| Config | `RISK` F3 | The `e` key cycles to `low`, `medium`, `high` (`tui.py:120-122`, fallback list `interface.py:12`). R2 makes these values turn the thinking on also on `llama-server-qwen`, which declares "no thinking". |
| Config | `RISK` F5 | The bench Gemma-4 12B entry has no `--chat-template-file` (`eval/models.json:7-33`). Production uses the custom template (`services.toml`, entry `llama-server`). The bench thus measures a different CoT template. |
| Config | `OK` | `--chat-template-kwargs` with `enable_thinking` on `llama-server-qwen` is deprecated (`common/arg.cpp:3513-3516`) but `--reasoning off` already does the same work. |
| Tests | `RISK` F9 | No test covers the effort → `enable_thinking` mapping (`interface.py:167`, `eval/main.py:61`), the payload of `get_completion`, or a thought tag in `delta.content`. `grep -rn "reasoning\|enable_thinking" lobe_frontal/tests io_yeux/tests` gives no result. `interface.py:_self_check` covers `parse_model_metadata` only. |
| Docs | `DEFECT` F6 | `interface.py:164` says "reasoning_effort est ignoré par llama.cpp (vérifié en direct)". The server reads it (R3). `lobe_frontal/README.md:37` says that Qwen has "no thinking", which is false when the effort is not `none` (R2). No document of the repository (`CLAUDE.md`, `NATS_TOPICS.md`, `PROMPTING.md`, `docs/adr/`) says how each call controls the thinking. |
| Dead code | `OK` | `supported_reasoning_efforts` from the server (`interface.py:72-79`) is never filled by llama.cpp; the comment says so, and the fallback is used. `Groq/` and `Mistral/` are an ADR choice. |

## 3. Findings

Most severe first.

### F1 — `DEFECT`: the topic call thinks with no limit, and it blocks the conversation

`get_completion` (`interface.py:150-160`) sends no `enable_thinking`, no `reasoning_effort` and no
`max_tokens`. On the production server (`llama-server`, Gemma-4, `--reasoning auto`), rule R1
turns the thinking on. The comment of `io_yeux/main.py:110` confirms this behaviour on Gemma-4:
"Sans ça, Gemma-4 raisonne d'abord". The call runs inside `INFERENCE_SEMAPHORE`
(`main.py:232-233`). A user message that arrives during the topic call waits at `main.py:390` until
the thinking ends, up to the httpx timeout of 30 s. `limbic` waits 30 s too
(`limbic/main.py:22`). The operator chose `REASONING_EFFORT=none`, and this call does not obey it.

What breaks: the time to first audio of a user turn that follows a proactive trigger. When: each
time `limbic` asks a topic while the user speaks.

### F2 — `RISK`: the conversation thinks when `REASONING_EFFORT` is absent or the effort changes

`main.py:37` defaults to `"default"`. `interface.py:167` maps each value that is not `none` to
`enable_thinking: true`. No `--reasoning-budget` and no `max_tokens` limit the thinking. The
condition: the `.env` file is absent or has no `REASONING_EFFORT`, or the operator presses `e`. Then
the time to first token includes the full chain of thought, and the 200 ms target is not possible.
The thoughts are lost (never shown in the TUI, never stored), thus the operator sees a slow answer
with no cause on the screen.

### F8 — `RISK`: thoughts in `delta.content` go to the voice

The pipeline reads `delta.content` only, and expects the server to remove the thoughts (R4). The
condition: a model whose template the llama.cpp parser does not know, a fork with a different
parser (`llama-server-bonsai`), or `--reasoning-format none`. Then `<think>…</think>` or
`<|channel>thought…<channel|>` goes to `lobe.fragment_stream`, to `io_voix`, and to the history.

### F3 — `RISK`: the `e` key overrides `--reasoning off`

Rule R2: the kwarg of the request goes over the command line. On `llama-server-qwen`, the values
`low`, `medium` and `high` turn the thinking on. The condition: the operator presses `e` on that
server.

### F7 — `RISK`: the reasoning is dropped inside the tool loop

When the thinking is on, the assistant message with `tool_calls` goes back with no
`reasoning_content` (`main.py:457-461`). The Gemma template can render it (`chat_template.jinja:238-242`),
and it opens a new thought channel after a tool response (`chat_template.jinja:387-388`). The model
thus thinks again from zero after each tool result. The condition: thinking on, and a tool call.

### F4 — `RISK`: the bench gives wrong figures when the thinking is on

`eval/stream.py:78-80` starts `ttft` at the first `content` token, and `stream.py:120-126` divides
`completion_tokens` (thoughts included) by the duration of the content only. Thus `decode_tps` is
too high. `max_tokens` is 200 for the thoughts and the answer together (`eval/main.py:130`): a long
chain of thought gives an empty answer, and the quality checks count it as a model fault. The
condition: `REASONING_EFFORT` is not `none` during a bench run.

### F5 — `RISK`: the bench Gemma-4 12B entry uses a different template

`eval/models.json:7-33` has no `--chat-template-file`, thus the bench uses the template inside the
GGUF file. Production uses `chat_template.jinja`, whose header says that it fixes "thinking
content-ordering" (`chat_template.jinja:5`). The condition: any bench run of this entry. The CoT
behaviour that the bench measures is not the production one.

### F6 — `DEFECT`: the documentation is wrong about the reasoning

- `interface.py:164-166` says that llama.cpp ignores `reasoning_effort`. Rule R3 says the opposite.
- `lobe_frontal/README.md:37` says that Qwen has "no thinking". Rule R2 makes this false.
- No document gives the CoT policy of each call.

### F9 — `RISK`: no test holds the CoT policy

The mapping of `interface.py:167` and `eval/main.py:61`, and the payload of `get_completion`, have
no test. A change to one of them breaks F1 or F2 again with no failure.

## 4. Proposals

### P1 (F1) — turn the thinking off in the topic call

- Change: in `get_completion`, add `reasoning_effort="none"` and
  `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`, and a `max_tokens` (for
  example 64: the answer is one short sentence).
- Files: `services/lobe_frontal/OpenAI/interface.py`.
- Size: 3 lines.
- New risk: a topic that is less original. The task is one short sentence, thus the risk is low.
- Proof: a unit test that gives a fake `openai_client` to `OpenAIInterface` and reads the
  arguments of `create` (see P6). Then a manual step: start `llama-server` and `lobe_frontal`, send
  `nats req lobe.topic.generate '{}'`, and read `timings.predicted_n` in the server log.
- Alternative: pass `tui_app.current_effort` to `get_completion`, as `get_stream` does. That keeps
  one policy for the two calls, but the topic then thinks when the operator chooses a thinking
  effort for the conversation. Recommended: the fixed `none`, because the topic has no quality
  need for a chain of thought, and it holds the only slot.

### P2 (F2) — make `none` the code default, and limit the thinking

- Change: set the default of `REASONING_EFFORT` to `"none"` in `main.py:37` and
  `eval/main.py:60`, and in `lobe_frontal/README.md:54`. Add `--reasoning-budget` (for example
  `512`) to the `llama-server` entry of `services.toml`, so that a thinking effort cannot hold the
  slot with no limit.
- Files: `main.py`, `eval/main.py`, `README.md`, `services/terminal/services.toml`.
- Size: 4 lines.
- New risk: the budget cuts a long reasoning when the operator wants one. The `--reasoning-budget-message`
  flag can close it cleanly.
- Proof: `services/benchmark` with `graphs/T2T.json`, once with the effort `none` and once with
  `low`, to show the cost of the thinking. Then the same run without `.env` to show the default.

### P3 (F8) — remove the thought tags before the voice

- Two solutions:
  - A. Add `"reasoning_format": "deepseek"` to the body of `get_stream` and of the bench. The server
    then must extract, and the code does not depend on the default `auto`. Size: 1 line in each
    call. Risk: a model whose parser does not exist still leaks.
  - B. In `take_fragment`, drop the text between a known open tag (`<think>`, `<|channel>thought`)
    and its close tag. Size: about 15 lines and a test. Risk: a cost on the hot path, and a list of
    tags to keep correct.
- Recommended: A now. B only when a model in `services.toml` shows the leak (see Q3).
- Proof: a manual step with `curl` and `enable_thinking: true` on each server: the `content`
  field must contain no tag.

### P4 (F3) — limit the effort values to what the server permits

- Two solutions:
  - A. Keep it, and write in the note of `llama-server-qwen` and in `README.md` that the `e` key
    turns the thinking on also on this server. Size: 2 lines.
  - B. Read `chat_template_caps` and the reasoning state in `/props` at `refresh_inference_connection`,
    and give the TUI only `["none"]` when the server has the thinking off. Size: about 20 lines and
    a test of the parser.
- Recommended: A. The `e` key is a debug tool, and the operator presses it on purpose.
- Proof: A is a docs change. For B, a unit test on a captured `/props` response.

### P5 (F7) — keep the reasoning inside the tool loop

- Change: collect `delta.reasoning_content` in `run_turn`, and put it as `reasoning_content` on
  the assistant message with `tool_calls` (`main.py:457-461`). Do not store it in the history.
- Files: `services/lobe_frontal/main.py`.
- Size: about 5 lines.
- New risk: more prompt tokens in the second iteration.
- Proof: a manual step with the effort `low` and a message that calls `get_from_memory`: the
  `Prompt` panel of the TUI shows the reasoning in the second request. Alternative: keep it, and
  write an ADR that says the thinking is off in production and the tool loop does not need it.
  Recommended: the ADR, while P2 keeps `none` as the default.

### P6 (F9, and the proof of P1) — test the CoT payload of each call

- Change: one test file `tests/test_interface_reasoning.py` with a fake `chat.completions.create`
  that records its arguments. Check: `get_completion` sends `enable_thinking: False`;
  `get_stream` maps `none` → `False` and `low` → `True`.
- Files: `services/lobe_frontal/tests/test_interface_reasoning.py`.
- Size: about 30 lines.
- New risk: none.
- Proof: `cd services/lobe_frontal && pytest tests/`.

### P7 (F4) — make the bench honest when the thinking is on

- Change: in `consume_stream`, read `delta.reasoning_content`; record the time of the first
  reasoning token and the number of reasoning chunks; compute `decode_tps` on all the generated
  tokens over all the generation time. Report `reasoning_chunks` in the result.
- Files: `services/lobe_frontal/eval/stream.py`, `tests/test_eval_stream.py`.
- Size: about 15 lines and one test case.
- New risk: the old results are not comparable with the new `decode_tps` when the thinking is on.
  With `none`, the figures do not change.
- Proof: `pytest tests/test_eval_stream.py` with fake chunks that hold `reasoning_content`.
- Alternative: refuse to start the bench when `ENABLE_THINKING` is true. Size: 2 lines. Recommended
  if nobody wants to measure a thinking mode.

### P8 (F5) — use the production template in the bench

- Change: add `"--chat-template-file", "/mnt/forge/ai/models/Gemma-4/MTP-QAT-12B/chat_template.jinja"`
  to the first entry of `eval/models.json`.
- Files: `services/lobe_frontal/eval/models.json`.
- Size: 1 line.
- New risk: the new results are not comparable with the old ones for this model.
- Proof: the `argv` in the result file shows the flag.

### P9 (F6) — correct the documentation

- Change: correct the comment of `interface.py:164-166` (the server reads `reasoning_effort`;
  `"none"` disables the thinking). Correct `lobe_frontal/README.md:37`. Add one short section
  "Reasoning" to `services/lobe_frontal/README.md` with the table 2.1 of this report. Consider an ADR
  "the thinking is off in production" if P5 takes the ADR solution.
- Files: `interface.py`, `services/lobe_frontal/README.md`, maybe `docs/adr/0007-…`.
- Size: about 25 lines.
- New risk: none.
- Proof: a review of the text.

## 5. Open questions

| # | Question | What settles it |
|---|---|---|
| Q1 | How long does the topic call take on Gemma-4 with the thinking on, and how much does it add to the time to first audio of the next user turn? | Start the `sans-vocal` profile. Run `services/benchmark` with `graphs/E2E.json`. Send `nats req lobe.topic.generate '{}'` and one user message 100 ms later. Compare with the same run after P1. |
| Q2 | Does the production binary (`commit 29c720eaf`) apply R1 to R3 as the fork source does? | `curl -s 127.0.0.1:8080/v1/chat/completions -d '{"messages":[{"role":"user","content":"2+2?"}],"max_tokens":300}' \| jq '.choices[0].message \| {reasoning_content, content}'`. A non-empty `reasoning_content` confirms R1. Repeat with `"reasoning_effort":"none"` for R3. |
| Q3 | Do the templates of Luth 2, Bonsai 2 and LFM3-VL support the thinking, and does the parser extract it? | For each server: `curl -s 127.0.0.1:<port>/props \| jq .chat_template_caps`, then the request of Q2 with `"chat_template_kwargs":{"enable_thinking":true}`. The `content` field must contain no thought tag. |
| Q4 | Does `--reasoning-format auto` extract the `<|channel>thought` channel of the custom Gemma template with tools? | The request of Q2 with `enable_thinking: true` and the `tools` array of `PromptBuilder.tools_schema`. Read `content` and `tool_calls`. |
| Q5 | The latency lens: are the targets held with the effort `none`? | `services/benchmark` with `graphs/T2T.json` on the `sans-vocal` profile. No server was up during this audit: no number is given here. |
