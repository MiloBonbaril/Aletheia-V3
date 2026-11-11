# Audit: the speed of the main pipeline — 2026-11-04

Date: 2026-11-04. Mode: read only. The subject is the execution speed of the main pipeline:
`io_discord`, `io_oreilles`, `cortex`, `hippocampe`, `lobe_frontal` and `io_voix`.

The audit ran no benchmark. NATS operated, but `llama-server` and the services did not, and a game
used 9.2 GiB of the 16 GiB of the GPU (`nvidia-smi`, pid 600666). A benchmark on a shared GPU gives
numbers that do not apply. Thus each latency number below comes from an earlier measurement: the
eval bench of `lobe_frontal` (`eval/results/*.json`, without the prompt cache), the README of
`io_voix`, and the audit `docs/audits/io_oreilles-2026-09-07.md`. Section 5 gives the
`services/benchmark` runs that settle the end-to-end numbers.

## 1. Scope

### 1.1 In scope

| Item | Kind | Role in the pipeline |
|---|---|---|
| `services/io_oreilles/src/main.rs`, `segmenter.rs`, `discord_audio.rs` | code | VAD, end of speech, Whisper, `io.user.speak` |
| `services/cortex/src/main.rs` | code | ingress, fan-out to `cortex.prompt` and `hippocampe.context.build` |
| `services/hippocampe/main.py`, `database.py`, `rag_manager.py` | code | history read, RAG recall, `hippocampe.context.ready` |
| `services/lobe_frontal/main.py`, `src/prompt_builder.py`, `src/fragments.py`, `src/turn_queue.py`, `OpenAI/interface.py`, `tui.py` | code | queue, prompt, LLM stream, fragments |
| `services/io_voix/main.py`, `engine.py`, `audio.py` | code | TTS, local playback, `io.voice.speak.*` |
| `services/io_discord/cogs/voice.py`, `cogs/text.py`, `voice.py` | code | voice bridge, playback in the voice channel, text output |
| `services/terminal/services.toml` (`llama-server`, profiles `complet` and `discord`) | config | the flags of the LLM server |
| `services/lobe_frontal/.env`, `services/limbic/.env` | config | `REASONING_EFFORT=high`, the limbic tick |
| `services/benchmark/graphs/E2E.json`, `T2T.json` | config | what the benchmark can measure |
| `NATS_TOPICS.md`, `PROMPTING.md`, `CLAUDE.md` | docs | the contract and the claims about speed |
| The llama.cpp server source of the prism-ml fork (`/home/milo/Forge/dev/tools/prism-ml/llama.cpp`) | external | the prompt cache rules. The production binary is a different build of the same project. |

### 1.2 Out of scope

- `limbic`: in scope only where its topic request holds the LLM (F8).
- `io_yeux`: in scope only for the `<vision>` block of the prompt.
- `terminal`, `io_text`, `io_chat`, `io_visage`: not on the hot path.
- The quality of the answers and of the RAG recall. Two observations outside the subject are in
  section 1.4.

### 1.3 The flow and its topics

The hot path of a voice turn in the `discord` profile:

| Step | Topic | Publisher | Subscribers |
|---|---|---|---|
| 1 | `io.discord.voice.frame` | `io_discord` (`cogs/voice.py:55-64`) | `io_oreilles --discord` (`main.rs:459-485`) |
| 2 | `io.user.speak` | `io_oreilles` (`main.rs:705`) | `cortex` (`main.rs:375-407`) |
| 3 | `cortex.prompt` | `cortex` (`main.rs:236-240`) | `lobe_frontal` (`main.py:511`) |
| 3 | `hippocampe.context.build` | `cortex` (`main.rs:236-240`), `lobe_frontal` for a message that waited (`main.py:273`) | `hippocampe` (`main.py:130`) |
| 3 | `cortex.interaction.started` | `cortex` (`main.rs:248-256`) | `limbic`, `io_yeux` |
| 4 | `hippocampe.context.ready` | `hippocampe` (`main.py:84`) | `lobe_frontal` (`main.py:201`) |
| 5 | `lobe.fragment_stream` | `lobe_frontal` (`main.py:80`) | `io_voix`, `cortex`, `io_discord` (text cog), `io_yeux`, `terminal` |
| 6 | `io.voice.speak.audio` | `io_voix` (`main.py:122`) | `io_discord` (`cogs/voice.py:110`) |
| 7 | `io.voice.speak.start`, `.end` | `io_discord` (`cogs/voice.py:141,152`), or `io_voix` with local playback (`main.py:56-74`) | `lobe_frontal`, `io_yeux`, `benchmark` |
| — | `hippocampe.history.add` | `lobe_frontal` (`main.py:254-264`) | `hippocampe` (`main.py:131`) |
| — | `lobe.topic.generate` | `limbic` (request) | `lobe_frontal` (`main.py:241`) |

The local profile `complet` replaces steps 1 and 6: `io_oreilles` reads the microphone, and `io_voix`
plays each chunk on the sound card as soon as the chunk is ready.

### 1.4 Observations outside the subject

- The history shows UTC times, and the live message shows local times. `database.py:27` stores
  `datetime.utcnow`. `database.py:94` prints it in the history. `prompt_builder.py:263` prints
  `datetime.now()`. The machine is in `Europe/Paris` (CET, +0100). Thus the LLM sees a time jump of
  one hour (two hours in summer) between the history and the current message.
- The RAG encoder is `all-MiniLM-L6-v2` (`rag_manager.py:17`), an English model. The conversation
  is in French. The recall quality is not part of this audit.

## 2. Verdicts

### 2.1 The latency budget of one voice turn

This table adds the known parts of one voice turn in the `discord` profile, from the end of the
user's speech to the first sound in the voice channel. Each line names its source. The total is an
estimate, not a measurement.

| # | Part | Time | Source |
|---|---|---|---|
| 1 | VAD silence tail before the segment closes | 640 ms | `segmenter.rs:5`: 20 frames of 32 ms |
| 2 | Whisper, one segment, `STT_LANGUAGE=fr` | 102 ms | `io_oreilles-2026-09-07.md`, section 3.1 (idle GPU) |
| 3 | NATS hops and the cortex | not measured, < 5 ms expected | loopback; `cortex/main.rs:213-260` |
| 4 | Context (history read and RAG, in parallel with step 3) | not measured; the CPU encode alone is 9.9 ms | `rag_manager.py:15-16` |
| 5 | LLM prefill of a typical prompt (6045 tokens), no cache | **2061 ms** median | eval `typique`, `gemma-4-12B-it-qat-UD-Q4_K_XL`, 15 runs |
| 6 | Reasoning before the first content token (`REASONING_EFFORT=high`) | approximately 1000 ms (estimate) | `lobe_frontal.log`: median 486 characters of reasoning in 9 of the last 17 iterations; eval decode rate 118–124 tokens/s |
| 7 | Decode of the first sentence (first fragment) | 185–257 ms | eval: `ttff − ttft`, `froid` and `typique` |
| 8 | TTS of the **whole** first fragment (mute mode) | RTF × fragment length: 0.9–2.0 s for a fragment of 4.2 s | `io_voix/README.md`: RTF 0.22 idle, 0.45 with a game |
| 9 | WAV, base64, NATS, ffmpeg start, Opus | not measured | `cogs/voice.py:166` |
| | **Total (estimate)** | **approximately 5 s** | lines 1, 2, 5, 6, 7, 8 |

The targets are 200 ms to the first token and 300 ms to the first audio. Lines 5, 6 and 8 each
exceed the full target alone. In the local profile `complet`, line 8 becomes 98–200 ms (the first
chunk), and line 9 does not apply.

### 2.2 The lenses

| Lens | Item | Verdict | Evidence |
|---|---|---|---|
| Contract | `correlation_id` through the chain | `OK` | Created by the cortex for each ingress event (`cortex/main.rs:335,361,426`), put in both payloads at `main.rs:214-232`; copied to `lobe.fragment_stream` (`lobe_frontal/main.py:80-82`), to `io.voice.speak.*` (`io_voix/main.py:58-73`, `io_discord/voice.py:61-66`). The rebuild keeps it (`lobe_frontal/main.py:270-275`). |
| Contract | Payloads against `NATS_TOPICS.md` on the hot path | `OK` | `cortex.prompt`, `hippocampe.context.build`, `.ready`, `lobe.fragment_stream`, `io.voice.speak.audio` match their sections. |
| Latency | LLM prefill: the prompt layout and the prefix cache | `DEFECT` | F1 |
| Latency | Reasoning on the path to the first token | `DEFECT` | F2 |
| Latency | TTS and playback in the `discord` profile | `DEFECT` | F3 |
| Latency | VAD silence tail | `RISK` | F4 |
| Latency | First fragment waits for a full sentence | `RISK` | F5 |
| Latency | Debug work on the event loop of `lobe_frontal` | `RISK` | F10 |
| Latency | Cortex: one extra hop, one actor | `OK` | `main.rs:213-260`: one mpsc send, then two publishes in `tokio::join!`. No I/O wait. |
| Latency | Hippocampe: history query | `OK` | `database.py:89` uses `ORDER BY timestamp DESC LIMIT n` on the index `ix_messages_timestamp` (`database.py:31-33`). |
| Latency | `io_voix` engine (local profile) | `OK` | CUDA graphs, pinned KV caches, chunk schedule (`engine.py:14-24,165-190`). First chunk 98 ms idle, buffer margin grows (`README.md:37-46`). |
| Latency | `io_oreilles` Whisper settings | `OK` | `STT_LANGUAGE` defaults to `fr` (`main.rs:643-647`); the 25 s chunk ceiling (`segmenter.rs:22`). Measured in `io_oreilles-2026-09-07.md`. |
| Failure | `io.voice.speak.audio` larger than the NATS `max_payload` | `RISK` | F7 |
| Failure | A slow Qdrant or PostgreSQL drops the whole turn | `RISK` | F9 |
| Concurrency | The topic request holds the only LLM slot | `RISK` | F8 |
| Concurrency | `io_voix` one fragment at a time | `OK` | `main.py:18-21`: one executor thread, required by the shared KV caches and graphs. |
| Concurrency | GPU shared by `llama-server`, Whisper, Audio8 (and a game) | `UNKNOWN` | Q4 |
| Concurrency | One STT thread for all the Discord speakers | `UNKNOWN` | Open in `io_oreilles-discord-multi-speaker-2026-10-12.md` (F4, Q2, Q3). Not repeated here. |
| Config | Env variables against `CLAUDE.md` | `OK` | `lobe_frontal`, `hippocampe`, `io_voix` and `io_oreilles` read the variables that `CLAUDE.md` lists. `nats://localhost:4222` is hardcoded in `cortex`, `lobe_frontal`, `hippocampe`, `io_voix` and `io_discord`, as `CLAUDE.md` says. |
| Config | `REASONING_EFFORT=high` in `lobe_frontal/.env:4` | `DEFECT` | F2 |
| Config | The latency constants have no knob | `RISK` | `segmenter.rs:5` (640 ms), `cortex/main.rs:227` and `lobe_frontal/main.py:274` (`n_history` 20, written two times), `lobe_frontal/main.py:283` (0.5 s). See F4. |
| Tests | Prompt prefix stability | `RISK` | No test checks that two turns give the same prefix. `tests/test_prompt_builder_vision.py` checks only the `<vision>` position. See P1. |
| Tests | What the benchmark can see | `RISK` | F6 |
| Tests | `hippocampe` context build, `io_discord` text cog | `RISK` | `hippocampe/tests/` covers `_strip_audio_blobs` only. `io_discord/tests/` has no test for `cogs/text.py`. |
| Docs | `PROMPTING.md`, `NATS_TOPICS.md` | `RISK` | F12 |
| Dead code | `cortex` session tracking | `RISK` | F13 |
| Dead code | `Groq/`, `Mistral/`, `context_summary` | `OK` | `docs/adr/0002`. `context_summary` is a documented stub (`hippocampe/main.py:77`). |

## 3. Findings

### F1 — DEFECT — The prompt layout defeats the prefix cache of `llama-server`

**What breaks:** each turn processes most of the prompt again. For a typical prompt of 6045 tokens,
a full prefill costs 2061 ms (eval `typique`, cache off). A prefill of the system prompt alone (1554
tokens) costs 520 ms (eval `froid`). The target for the first token is 200 ms.

**Evidence:** three causes, each one sufficient.

1. **The history window slides.** The cortex asks for the last 20 messages (`cortex/main.rs:227`).
   `database.py:89` returns the last `n`. Each turn adds a user message and an answer, thus the
   first two messages of the window change at each turn. The prompt changes just after the system
   prompt.
2. **`<recall>` and `<mood>` are in the system prompt.** `prompt_builder.py:188-191` puts them in
   the system prompt, before the history. The RAG result changes with each message. The mood text
   changes when the intensity changes: `limbic` decays it each tick (`limbic/main.py:89-92`;
   `TICK_INTERVAL_SECONDS=5` in `limbic/.env`), and `prompt_builder.py:145` prints it with one
   decimal.
3. **Gemma-4 uses a sliding-window KV cache (iSWA).** `llama-model.cpp:2867-2906` creates
   `llama_kv_cache_iswa`, and `services.toml` does not give `--swa-full`. When the prompt changes
   at position P, the server must restore the state at P from a checkpoint. Without one, it
   processes the full prompt again (`server-context.cpp:3270-3305`, "forcing full prompt
   re-processing"). The first checkpoint is at the start of the first user message
   (`server-context.cpp:3472-3478`). Thus a change inside the system prompt (cause 2) gives a full
   prefill, and a change in the history (cause 1) gives a prefill of the full history.

`PROMPTING.md:56-58` says that the system prompt and the history "stay the same from one turn to
the next". This is the intention of the `<vision>` change. It is not true for the rest of the
prompt.

### F2 — DEFECT — The reasoning runs before the first spoken word

**What breaks:** `lobe_frontal/.env:4` sets `REASONING_EFFORT=high`. `OpenAI/interface.py:171-180`
then sends `enable_thinking: true`. The model writes its reasoning in `reasoning_content` before the
first `content` token (`main.py:418-428`). The fragments, thus the voice, wait for the full
reasoning. In the log, 9 of the last 17 iterations (2026-11-03, 20:05–20:09) carry a reasoning with
a median of 486 characters. At the eval decode rate (118–124 tokens/s), this is approximately 1 s.

The audit `llm-cot-handling-2026-11-03.md` read `REASONING_EFFORT=none`. The value changed after
that audit. The `.env` file is not in git, thus no commit records the decision.

### F3 — DEFECT — The `discord` profile loses the chunk streaming of `io_voix`

**What breaks:** in the `discord` profile, the first sound waits for the synthesis of the **whole**
first fragment, then for one new ffmpeg process.

**Evidence:**

- `io_voix/main.py:165` waits for the full synthesis of the fragment. `main.py:172-173` then
  publishes one WAV for the fragment. `NATS_TOPICS.md` (`io.voice.speak.audio`) asks for "one
  message for each fragment, not one for each chunk".
- With `MUTE_LOCAL_PLAYBACK`, the chunks go to `audio_sync_queue`, but nothing plays them
  (`main.py:41-46,65`).
- `io_discord/cogs/voice.py:166` starts one `FFmpegPCMAudio` process for each fragment, and only
  after the previous fragment ended (`cogs/voice.py:138-153`).

For a fragment of 4.2 s, the synthesis takes 0.9 s (RTF 0.22, idle GPU) to 2.0 s (RTF 0.45, with a
game). In the local profile, the first chunk plays after 98–200 ms (`io_voix/README.md:37-46,140`).
The ffmpeg start also adds a gap between two sentences (Q5).

### F4 — RISK — The end of speech costs 640 ms, and nothing can tune it

**When it breaks:** each voice turn waits 640 ms of silence before Whisper starts. This is more than
twice the full target of 300 ms.

**Evidence:** `segmenter.rs:5` (`SILENCE_FRAMES_TO_END = 20`, 32 ms each). The audit
`io_oreilles-2026-09-07.md` shows that the tail costs Whisper only 2 ms (section 3.3). It did not
measure the wait itself. The value is a constant: a change needs a CUDA rebuild of `io_oreilles`.
A shorter tail cuts a sentence at a natural pause and makes two messages. The correct value depends
on the speakers, thus it needs a knob and a measurement, not a guess.

### F5 — RISK — The first fragment waits for the end of the first sentence

**When it breaks:** each answer. `fragments.py:9` cuts only at `.`, `!`, `?` and a new line. The TTS
starts only after the full first sentence. The eval bench, which uses the same function, measures
185 ms (`froid`) and 257 ms (`typique`) between the first token and the first fragment.

### F6 — RISK — The benchmark cannot check the two targets

**When it breaks:** a latency change in three places stays invisible.

- `graphs/E2E.json` starts at `io.user.speak`. The VAD tail (F4) and Whisper are before it.
- The step "Premier Fragment (TTFT)" (`E2E.json`, `T2T.json`) and `NATS_TOPICS.md:196` measure the
  first **fragment**, which includes the first sentence (F5) and the reasoning (F2). No event marks
  the first token.
- In the `discord` profile, `io_discord` publishes `io.voice.speak.start` before `vc.play` starts
  ffmpeg (`cogs/voice.py:141` against `:146`). The step "Début Lecture Voix" is thus early by the
  ffmpeg start time.

### F7 — RISK — A long fragment exceeds the NATS `max_payload`

**When it breaks:** a fragment longer than approximately 8.9 s of speech. `audio.py:60` writes a
16-bit WAV at 44.1 kHz (88.2 kB/s). Base64 adds a third: 117.6 kB/s. The server limit is 1048576
bytes (`/varz`, `max_payload`). `engine.py:67` allows 512 frames, 23.8 s. The publish fails in
`main.py:126-127` with a print only. In the `discord` profile the sentence is then not spoken. If it
is the last fragment, no `io.voice.speak.end` with `is_last` comes, and `lobe_frontal` waits for its
60 s guard (`lobe_frontal/main.py:307`) before it answers the next message.

### F8 — RISK — The topic request blocks a user turn

**When it breaks:** a user speaks while `lobe.topic.generate` runs. `main.py:232` holds
`INFERENCE_SEMAPHORE` for the topic call, and `main.py:392` waits for the same semaphore. The user
turn waits for the full topic call. `OpenAI/interface.py:154-160` sends no `enable_thinking` and no
`max_tokens`, thus the call thinks (finding F1 of `llm-cot-handling-2026-11-03.md`) and has no
length limit. With `limbic/.env` (tick 5 s, +0.5 for each tick, threshold 1.0), a topic call can
start approximately 10 s after each interaction when the voice channel is occupied.

### F9 — RISK — A slow memory drops the whole turn

**When it breaks:** Qdrant or PostgreSQL answers slowly (for example a paused container, or a CPU
under load). `hippocampe/main.py:59-63` publishes only when the two sources end. `lobe_frontal`
waits 0.5 s (`main.py:283`), then speaks a fixed sentence and drops the turn (`main.py:357-358`),
also when the history is ready. Also, `rag_manager.py:64` runs the encoder (9.9 ms on CPU) on the
event loop. During that time, the other handlers of `hippocampe` wait.

### F10 — RISK — Debug work runs inside the first-token window

**When it breaks:** each LLM request, and more with a large prompt.

- `lobe_frontal/main.py:10` sets the root logger to `DEBUG`. The OpenAI SDK then formats the full
  request body and writes it to `lobe_frontal.log` before it sends the request
  (`openai/_base_client.py:504-506`). Measured in the current log: 144 such lines, median 9009
  bytes, maximum 1116806 bytes. The file is 6.9 MB and has no rotation.
- `main.py:387` schedules `tui_app.update_messages` with `call_soon`. The callback runs at the next
  yield of the loop. The first yield is in the connection of `get_stream` (`main.py:403`). The log
  shows a new TCP connection for 227 of 252 requests, because `llama-server` closes idle
  connections. Thus the callback (`json.dumps(indent=2)` and `TextArea.load_text` with JSON
  highlight, `tui.py:105-108`) runs before the request goes out. The comment at `main.py:384-386`
  says the opposite.

The cost for a text-only prompt is probably small. It is not measured (Q1).

### F11 — RISK — The text output sends one Discord message for each fragment

**When it breaks:** a long answer in text mode. `cogs/text.py:60` calls `send` for each fragment.
Discord limits a channel to approximately 5 messages each 5 s, and discord.py waits on the limit.
The handler waits too, thus the next fragments wait in the subscription. This is not on the voice
path.

### F12 — RISK — The docs promise a speed property that the code does not have

- `PROMPTING.md:56-58`: the system prompt and the history "stay the same from one turn to the next".
  See F1.
- `NATS_TOPICS.md:196`: "The fragment with `sequence` 0 gives the time to first token." It gives the
  time to the first fragment. See F6.

### F13 — RISK — The session tracking of the cortex is never read

`CLAUDE.md` says the cortex "tracks the sessions". `cortex/main.rs:209-212,265-274` writes
`active_sessions`, and nothing reads it. `SessionContext.start_time` and `total_fragments`, the
`NeuralRouter` trait (`main.rs:152-156`) and `CortexDispatcher::new` (`main.rs:158-165`) have no
caller. Each fragment also creates one UUID for nothing (`main.rs:454`). The cost is negligible. The
risk is a reader who trusts a state that does not exist.

## 4. Proposals

### P1 — for F1 and F12 — Keep the prompt prefix stable

Two changes. Do both.

1. **Move `<recall>` and `<mood>` to the last user message**, in front of `<vision>`. The system
   prompt then holds only `PERSONA.md`, `MEMORY.md`, `USER.md` and the tools. These change only on a
   file edit. Files: `prompt_builder.py` (approximately 15 lines), `PROMPTING.md`.
2. **Move the history window in steps, not one message at a time.** In `database.py`, return
   `n + total % step` messages instead of `n`. With `n = 20` and `step = 10`, the window holds 20 to
   29 messages, and its start moves once each 10 messages (5 turns). Four turns of five then reuse
   the cache. Files: `database.py` (approximately 5 lines, one `COUNT` query), `NATS_TOPICS.md`
   (`n_history` becomes a minimum).

**Alternative, not recommended:** `--swa-full` and `--cache-reuse 256` on `llama-server`. No code
change, but more VRAM for the full SWA cache, and the audit did not confirm that `--cache-reuse`
operates with the iSWA cache (Q9).

**New risk:** the recall in a user message has less authority than in the system prompt. The
context grows by up to 9 messages: check it against `-c 15000` with the `charge` fixture of the eval
bench. The previous user message still changes at each turn, because the `<vision>` block and the
time stamp are not stored. This costs the prefill of two messages only.

**Proof:**

- A test in `tests/`: two `build()` calls with a different `rag_results`, `mood` and `vision` give
  the same `messages[0]` and the same history messages.
- The `llama-server` log for five turns: `prompt eval time = … / N tokens` shows N in the hundreds,
  not in the thousands, and no "forcing full prompt re-processing" line.
- `services/benchmark` in the `sans-vocal` profile, before and after.

### P2 — for F2 — Choose the reasoning effort of the voice, and write it down

Set `REASONING_EFFORT=none` in `lobe_frontal/.env`. The `e` key of the TUI still switches it for a
test. Cost: one line. **New risk:** answers of lower quality. If the quality is worth the second,
keep `high` and write an ADR that accepts approximately 1 s more for each answer, and change the
target in `CLAUDE.md`.

**Proof:** the benchmark step "Premier Fragment", before and after. The TUI line "fin du
raisonnement" shows `(vide)`.

### P3 — for F3 and F7 — Stream the chunks to `io_discord`

**Recommended:** a new topic, `io.voice.speak.chunk`. In mute mode, `io_voix` publishes each chunk
when `stream_fragment` gives it: `{sequence, chunk, pcm (base64, s16le mono 44.1 kHz), is_final,
is_last, correlation_id}`. `io_discord` starts one ffmpeg for each fragment at the first chunk, with
raw input (`-f s16le -ar 44100 -ac 1`, thus no probe), and writes the next chunks into its input.
`io.voice.speak.start` and `.end` keep their meaning: one pair for each fragment. A chunk is at most
96 frames (4.5 s, 524 kB in base64), thus F7 also disappears. Keep `io.voice.speak.audio` only if a
consumer needs it; today only `io_discord` reads it.

Files: `io_voix/main.py` (approximately 20 lines), `io_discord/cogs/voice.py` (approximately 40
lines), `NATS_TOPICS.md`, `CLAUDE.md`. **New risk:** if the synthesis becomes slower than real time
(RTF above 1, for example under a heavy game), ffmpeg waits and Discord plays silence inside the
sentence. Today the same case gives a longer wait before the sentence.

**Smaller alternative:** keep one WAV for each fragment, but cut the fragments shorter (P5). This
reduces the wait but does not remove it.

**Proof:** the benchmark step "Début Lecture Voix" in the `discord` profile, before and after; a
test in `tests/test_voice_cog.py` that sends three chunks and checks one `.start` and one `.end`; a
listening test for gaps.

### P4 — for F4 — Give the silence tail a knob, then measure

Read `STT_SILENCE_MS` (default 640) in `io_oreilles` instead of the constant. Files: `segmenter.rs`,
`main.rs`, `discord_audio.rs` (approximately 10 lines), `CLAUDE.md`. Then measure 640, 480 and 400 ms
on the bench corpus and on 20 real turns: count the sentences that become two messages.

A later step, only if the knob is not sufficient: start Whisper at half the tail, and drop the
result if the speech continues. This hides the 102 ms of Whisper. It costs one more transcription
for each pause.

**Proof:** the cut count above; P6 shows the gain in the benchmark.

### P5 — for F5 — Cut the first fragment earlier

For `sequence` 0 only, also cut at `,`, `;` and `:` when the buffer holds at least 20 characters.
Files: `fragments.py` and its call at `main.py:432` (approximately 5 lines). The eval bench uses the
same function, thus it measures the gain with no other change. **New risk:** a different intonation
at the joint, because Audio8 synthesizes each fragment alone.

**Proof:** `python eval/main.py` on an idle GPU: `ttff − ttft` before and after; a listening test.

### P6 — for F6 and F12 — Let the benchmark see the full path

1. `io_oreilles` adds `speech_end_ms` (epoch ms of the last speech frame) to `io.user.speak`. The
   benchmark then shows the VAD tail and Whisper. Optional field: the cortex ignores it.
2. Rename the step "TTFT" to "first fragment" in `E2E.json` and `T2T.json`, and correct
   `NATS_TOPICS.md:196`.
3. Write in `NATS_TOPICS.md` that `io_discord` publishes `.start` before ffmpeg plays. P3 can move
   the publication to the first chunk that ffmpeg reads.

Files: `io_oreilles/src/main.rs` (approximately 5 lines), `benchmark/graphs/*.json`, `benchmark/main.py`
(one more step), `NATS_TOPICS.md`. **Proof:** `benchmark/tests/test_matching.py` with one payload
that holds `speech_end_ms`; one live run.

### P7 — for F8 — Make the topic call short and bounded

Send `enable_thinking: false` and `max_tokens: 60` in `get_completion`
(`OpenAI/interface.py:154-160`). The call then takes a fraction of a second. Cost: two lines.

**Alternative:** let a user message cancel the topic call. This needs a task handle in `main.py` and
a cancel in `prompt_handler`. Use it only if the short call is still too long.

**Proof:** the time between the topic request and the reply in `lobe_frontal.log`.

### P8 — for F9 — Give the RAG its own deadline

In `hippocampe/main.py`, wrap the RAG branch in `asyncio.wait_for(..., 0.25)`. On a timeout, publish
the history with an empty recall. In `rag_manager.py:64`, call the encoder with
`asyncio.to_thread`. Cost: approximately 5 lines. **New risk:** a recall that comes after 250 ms is
lost for that turn.

**Proof:** `docker compose pause qdrant` in `services/hippocampe`, send one message: the answer
comes with its history, not with the fixed sentence. Then `docker compose unpause qdrant`.

### P9 — for F10 — Take the debug work out of the first-token window

Set `level=logging.INFO` at `lobe_frontal/main.py:10`. Call `tui_app.update_messages` after the first
chunk of the stream, not before the request. Cost: approximately 3 lines. **New risk:** none for
the service. The SDK request dump is gone from the log; set the `openai` logger to `DEBUG` by hand
when it is necessary.

**Proof:** the benchmark step "Premier Fragment", before and after; the size of `lobe_frontal.log`
after ten turns.

### P10 — for F11 — One Discord message for each answer

In `cogs/text.py`, keep the fragments of one `correlation_id` and send them at `is_last`. Cost:
approximately 8 lines. **New risk:** the text appears at the end of the answer only. **Proof:** a new
test in `io_discord/tests/` with three fragments and one `send`.

### P11 — for F13 — Delete the dead session state of the cortex

Delete `active_sessions`, `SessionContext`, `NeuralRouter`, `CortexDispatcher::new`, the UUID of
each fragment and the `async-trait` dependency. Correct `CLAUDE.md`, `AGENTS.md` and the internal
contract section of `NATS_TOPICS.md`. If a session state becomes necessary, add it back with a
reader. Cost: approximately −40 lines. **Proof:** `cargo test` in `services/cortex`; one message
through the pipeline.

### Order

P1, P2 and P3 give most of the gain (section 2.1, lines 5, 6 and 8). P9 and P7 are each a few
lines. Do P6 before P4 and P5, so the benchmark can show their gain.

## 5. Open questions

Run each measurement on an idle GPU: not during a game.

| # | Question | What settles it |
|---|---|---|
| Q1 | The end-to-end latency of each profile today, and the cost of F10. | Start the `sans-vocal` profile in `services/terminal`, then `cd services/benchmark && python main.py`. Send 10 text messages. Do it again with the `discord` profile and 10 voice turns. |
| Q2 | The real cache hit of the production `llama-server` (F1). | Start `llama-server`, run 5 turns, read its log: `prompt eval time = … / N tokens`, "restored context checkpoint", "forcing full prompt re-processing". |
| Q3 | The prefill rate with `-ub 1024` (production) against `-ub 4096` (eval). | Run the eval bench with the `llama-server` argv of `services.toml`. |
| Q4 | The RTF of `io_voix` while `llama-server` decodes the next sentences. | Log the chunk arrival times of `io_voix` during 10 long answers. The margin must stay positive (`engine.py:394-400`). |
| Q5 | The ffmpeg start time and the gap between two fragments in `io_discord`. | Log the time from `vc.play` to the first `read()` of the audio source, for 20 fragments. |
| Q6 | The effect of the reference voice length on the first chunk. The reference text is three sentences (`services.toml`, `A8_VOICE_TEXT`). | `python engine.py` in `services/io_voix` with the current reference and with a 3 s cut of it. Compare the TTFA line. |
| Q7 | The context build time of `hippocampe`. | Read its line "Contexte publié (… ms)" during Q1. |
| Q8 | The CPU of the per-speaker VAD thread in Discord mode. | Open since `io_oreilles-discord-multi-speaker-2026-10-12.md`, Q2. |
| Q9 | Does `--cache-reuse` operate with the iSWA cache of Gemma-4? | Start `llama-server` with `--cache-reuse 256`, run 5 turns with the current prompt layout, read `n_past` in the log. |
