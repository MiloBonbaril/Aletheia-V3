# Audit — io_oreilles, parallel recording of the Discord speakers

Date: 2026-10-12. Mode: read only. No live pipeline: NATS did not operate on the machine during the
audit (`curl localhost:8222/varz` gave no answer), thus the audit ran no benchmark.

## 0. The target

The user gives this target:

1. `io_oreilles` records each member of the Discord voice channel at the same time.
2. The transcripts go into a queue in `lobe_frontal` while the LLM is busy.
3. When the LLM is free, it answers **only the most recent message** of the queue. The other
   messages of the queue go into the memory as usual, with no answer.
4. The chain after the LLM stays sequential: LLM → TTS → Discord.

After the first version of this report, the user gave two decisions (Q6):

5. A proactive prompt always gives its place to a user message. A proactive prompt from the boredom
   gauge that waited in the queue is dropped: an interaction ran during the wait, thus the boredom
   that made the prompt is gone.
6. The LLM is "busy" until Aletheia stops **speaking**, not only until the LLM stops. The recording
   continues during her speech, and the messages wait in the queue. Reason: a member who answers
   her first reply speaks while she still speaks. If the LLM starts the next answer before she
   stops, that next answer misses the member's reaction.

The audit compares the code with this target. Item 1 operates now. Item 2 and item 3 do not exist.
The current code does a different thing, and that thing is a defect (F1): a message that arrives
while the LLM is busy loses its context, gets a fixed error sentence as its answer, and does not go
into the memory. Item 4 operates now.

## 1. Scope

In scope:

| Item | Reason |
|---|---|
| `services/io_oreilles/src/main.rs` | The Discord ingress, the VAD thread, the STT thread, the publication. |
| `services/io_oreilles/src/discord_audio.rs` | One VAD pipeline for each speaker. |
| `services/io_oreilles/src/segmenter.rs` | The end of a segment (silence of 600 ms). |
| `services/io_discord/cogs/voice.py`, `voice.py` | The source of the frames, and the playback of the answer. |
| `discord.ext.voice_recv` `SilenceGeneratorSink` (`venv/.../voice_recv/sinks.py:612`, `silence.py:35`) | It makes the silence frames that close a segment. |
| `services/cortex/src/main.rs` | The subscriber of `io.user.speak`, the dispatch of the prompt and of the context. |
| `services/lobe_frontal/main.py` | The place of the target queue. The handler of `cortex.prompt`. |
| `nats-py` 2.9.0 (`venv/.../nats/aio/subscription.py:302`) | It decides how the handlers of `lobe_frontal` run. |
| `services/hippocampe/main.py` (handlers only) | The context and the history writes. |
| `services/benchmark/main.py` (matching only) | It must measure the new flow. |
| Topics `io.discord.voice.frame`, `io.user.speak`, `cortex.prompt`, `hippocampe.context.build`, `hippocampe.context.ready`, `hippocampe.history.add`, `lobe.fragment_stream`, `io.voice.speak.audio` | The chain from a voice to an answer. |
| `NATS_URL`, `STT_LANGUAGE`, `STT_MODEL_PATH`, `RAW_AUDIO`, `ORT_DYLIB_PATH`, `MAX_CONCURRENT_INFERENCE` | The configuration of the chain. |

Out of scope:

- The quality of Whisper and of Silero. The audit `io_oreilles-2026-09-07.md` covers them.
- The local microphone mode. It has one speaker only.
- `io.user.speak.raw`. The Discord mode does not use it.
- `io_voix` inside. The target keeps it as it is. The audit reads only that it takes one fragment
  at a time.
- The interruption of Aletheia while she speaks (barge-in). The target does not ask for it.
- `limbic`, except the `source` field of a proactive prompt, because it shares the queue.

## 2. Map

The real flow, from a voice to an answer:

| # | Topic | Publisher | Subscriber |
|---|---|---|---|
| 1 | `io.discord.voice.frame` | `io_discord` `_DiscordAudioBridge.write` (`cogs/voice.py:54-63`), 20 ms of PCM for each frame, for each member | `io_oreilles` (`main.rs:459-486`) |
| 2 | `io.user.speak` | `io_oreilles` (`main.rs:666-676`), one message at the end of each turn of each speaker | `cortex` (`main.rs:378-408`) |
| 3 | `cortex.prompt` | `cortex` (`main.rs:236-239`), at once, for each message | `lobe_frontal` (`main.py:384`) |
| 4 | `hippocampe.context.build` | `cortex` (`main.rs:236-239`), in parallel with #3 | `hippocampe` (`main.py:126`) |
| 5 | `cortex.interaction.started` | `cortex` (`main.rs:251`) | `limbic` |
| 6 | `hippocampe.context.ready` | `hippocampe` (`main.py:84`) | `lobe_frontal` (`main.py:186-198`) |
| 7 | `hippocampe.history.add` | `lobe_frontal` (`main.py:277`, `363`, `380`), the only writer | `hippocampe` (`main.py:96-102`) |
| 8 | `lobe.fragment_stream` | `lobe_frontal` (`main.py:74-82`) | `io_voix`, `io_discord` text cog, `cortex`, `benchmark` |
| 9 | `io.voice.speak.audio` | `io_voix` (`main.py:116`) | `io_discord` `_on_speak_audio` → `speak_queue` → `_playback_loop` (`cogs/voice.py:111-134`) |

Inside `io_oreilles --discord`:

```
NATS task ──std mpsc (no limit)──▶ VAD thread ──tokio mpsc (32)──▶ STT thread ──mpsc (32)──▶ publish
            main.rs:454                main.rs:495-524   main.rs:236        main.rs:622-664          main.rs:666
                                       one SpeakerPipeline              one Whisper, one segment
                                       for each speaker_id              at a time
```

## 3. Verdicts

| Lens | Verdict | Evidence |
|---|---|---|
| Concurrency — `lobe_frontal`, a second message during an inference | **DEFECT** | `nats-py` runs the callbacks of one subscription one after the other (`subscription.py:311-316`: `await self._cb(msg)` in one loop). Thus the second `prompt_handler` starts only after the first one ends. Its `hippocampe.context.ready` arrives before its future exists (`main.py:191-194`), and the handler then waits 0.5 s for nothing (`main.py:254-261`). Seen in `lobe_frontal.log:4337-4341` and `:4357-4361`: "Contexte reçu pour correlation_id inconnu" during a stream, then "Timeout contexte" 0.5 s after the stream ends. 7 of these pairs in the log. See F1. |
| Concurrency — the target queue (latest message wins) | **DEFECT** | It does not exist. The cortex dispatches each message at once (`cortex/main.rs:378-408`, `236-239`). The lobe_frontal answers each message, one after the other (see the row above). See F1 and P1. |
| Concurrency — `io_oreilles`, one VAD pipeline for each speaker | **OK** | `main.rs:497-510` keys the pipelines on `speaker_id`. Each `SpeakerPipeline` has its own resampler, Silero state and segmenter (`discord_audio.rs:47-67`). The Silero `Session` is shared, and one thread uses it, thus no lock is necessary. |
| Concurrency — the text of a long turn, keyed on the display name | **RISK** | `main.rs:630-631`, `642`, `656` key the held text on `Option<String>`, which is the display name (`main.rs:518`, `cogs/voice.py:59`). Two members with the same display name share one entry. See F2. |
| Concurrency — one STT thread for all the speakers | **RISK** | `main.rs:619-622`: one thread, one Whisper. When k speakers stop at the same time, the k-th waits for k−1 transcriptions (102 ms each in the README bench, with no GPU load). The GPU is shared with llama-server and Audio8. See F4. |
| Latency | **UNKNOWN** | No live run. `io.user.speak` is an ingress of `graphs/E2E.json`, thus the STT queue time of F4 is not in the measured budget. The benchmark also mixes overlapping requests (F5). See Q1 and Q3. |
| Failure — a member leaves during speech | **RISK** | `SilenceGeneratorSink` stops the silence of a member at the disconnect (`sinks.py:629-631`). The segment of that member never gets its 21 silence frames (`segmenter.rs:5`), thus it stays open, and the text stays held. `/voice leave` (`cogs/voice.py:205-215`) does the same for all the members. See F3. |
| Contract — `io.voice.speak.end` in the Discord mode | **RISK** | The `discord` profile runs `io_voix_muet` (`terminal/services.toml:217`, `262`). With `MUTE_LOCAL_PLAYBACK`, `io_voix` plays nothing and publishes `io.voice.speak.end` at the end of the synthesis (`io_voix/main.py:42-67`). `io_discord` plays the audio later, from its own queue (`cogs/voice.py:119-134`), and publishes nothing. An empty last fragment gives no `io.voice.speak.audio` (`io_voix/main.py:129-132`), thus `io_discord` cannot know that an answer ended. The benchmark step `voice_playback_end` (`graphs/E2E.json:68-73`) and the pause of `io_yeux` (`io_yeux/main.py:159-163`) read a false end of speech. Decision 6 needs a true one. See F8. |
| Failure — `io_discord` starts before NATS | **RISK** | `setup_nats` (`cogs/voice.py:103-109`) tries one time. On a failure, `self.nc` stays `None`, and `_DiscordAudioBridge.write` drops each frame with no log (`cogs/voice.py:55-57`). The bot stays in the channel and nobody hears. See F6. |
| Failure — NATS absent for `io_oreilles` | **OK** | `main.rs:221-224` stops the process with a cause. `main.rs:459-464` stops it if the subscription fails. |
| Failure — hippocampe absent | **OK** | Out of the target, unchanged: `lobe_frontal/main.py:258-261` speaks a fixed sentence after 0.5 s. |
| Contract — `io.discord.voice.frame` | **OK** | `voice.py:38-44` gives `{speaker_id, speaker_name, pcm}`. `discord_audio.rs:18-23` reads the same three fields. `NATS_TOPICS.md:74-91` agrees. |
| Contract — `io.user.speak` | **OK** | `main.rs:17-23` gives `{text, speaker?}`. `cortex/main.rs:381-386` reads the same. `NATS_TOPICS.md:38-50` agrees. |
| Contract — `correlation_id` through the chain | **RISK** | `lobe.fragment_stream` carries no `correlation_id` (`lobe_frontal/main.py:77-82`, `NATS_TOPICS.md:163-178`). The cortex makes a new UUID for each fragment (`cortex/main.rs:454`) and uses one `global_session` for all requests (`main.rs:336`, `427`, `455`). The benchmark gives each event to the newest open transaction (`benchmark/main.py:372-376`). With two speakers, the requests overlap and the measurements mix. See F5. |
| Config | **RISK** | `io_oreilles` reads `NATS_URL`, `STT_LANGUAGE`, `STT_MODEL_PATH`, `RAW_AUDIO`, `ORT_DYLIB_PATH` (`main.rs:194`, `220`, `527`, `596`, `608`). They agree with `CLAUDE.md` and the README. `io_discord` has `nats://localhost:4222` in its source (`cogs/voice.py:105`), as `CLAUDE.md` says. But `MAX_CONCURRENT_INFERENCE` (`lobe_frontal/main.py:28`) has no effect on `cortex.prompt`: `nats-py` already runs one `prompt_handler` at a time. A value of 2 changes nothing. P1 makes the variable real again, and the target needs the value 1. |
| Tests | **RISK** | `io_oreilles` tests cover the segmenter, the downmix and the event (`segmenter.rs`, `discord_audio.rs:115-134`, `main.rs:64-101`). Untested: the held text for each speaker in the STT thread (`main.rs:630-662`), because it is inside a thread closure; the life of the pipeline map (`main.rs:497-510`). `lobe_frontal/tests/` has no test of `prompt_handler`: the overlap of F1 is untested. `io_discord`: 18 tests pass (`pytest tests/ -q`, 0.30 s). `cargo test` did not run: rustup has no default toolchain on this machine. See Q5. |
| Docs | **RISK** | (a) `io_oreilles/README.md` says "it publishes the text on `io.user.speak` at the end of each complete sentence". The code publishes at the end of a turn (`main.rs:653-660`). (b) `NATS_TOPICS.md:76-77` says `io_discord` publishes frames "continuously after a successful `/voice join`". The code publishes for a member from the first packet of that member, until that member leaves (`silence.py:50-63`, `sinks.py:629-631`). (c) `CLAUDE.md` lists `MAX_CONCURRENT_INFERENCE` as a working control (see Config). See F7. |
| Dead code | **OK** | In `io_oreilles`, each path is reachable. Out of scope, for a later change: in `cortex/main.rs`, the trait `NeuralRouter` (`:152-155`) has no implementation, `CortexDispatcher::new` (`:158`) has no caller, and `SessionContext` (`:140-143`) is written but never read. |

## 4. Findings

### F1 — DEFECT — a message that arrives while the LLM is busy gets no real answer and no memory

When it occurs: each time that a second `cortex.prompt` arrives before the first inference ends.
With several Discord speakers, this is the normal case. A text message on Discord or a proactive
trigger during a voice answer does the same.

What breaks, step by step:

1. Message A starts `prompt_handler` (`lobe_frontal/main.py:239`). The inference runs for seconds.
2. Message B arrives. The cortex sends `cortex.prompt` and `hippocampe.context.build` at once
   (`cortex/main.rs:236-239`).
3. `nats-py` keeps `cortex.prompt` B in the queue of the subscription. It calls the callback only
   when callback A returns (`subscription.py:311-316`).
4. `hippocampe.context.ready` B arrives on a different subscription. No future exists for B, thus
   `context_ready_handler` drops it (`main.py:191-194`).
5. Callback A ends. Callback B creates its future and waits 0.5 s (`main.py:254`). The context is
   gone, thus the wait ends on a timeout.
6. B publishes "Il semblerait que j'ai des trous de mémoire..." (`main.py:260`) and returns.
   It returns **before** the history write (`main.py:276-279`). Thus B is not in the memory.

Evidence in the log, `lobe_frontal.log:4337-4341`:

```
21:40:54,662  receive_response_body.started        (stream of A)
21:40:54,888  WARNING Contexte reçu pour correlation_id inconnu ou expiré: 23e30b0f...
21:40:55,175  response_closed.complete             (end of A)
21:40:55,676  ERROR   Timeout contexte hippocampe (corr=23e30b0f...)   (0.5 s later)
```

The same pattern is at `:4357-4361` and `:4936-4940`. The log holds 7 such pairs.

The target asks for the opposite on two points: B must go into the memory, and the LLM must answer
the last message only.

### F8 — RISK — in the Discord mode, nothing says when Aletheia stops speaking

Decision 6 needs a signal at the true end of her speech. The topic `io.voice.speak.end` with
`is_last: true` has this meaning in the local mode: `io_voix` publishes it after the sound card
plays the last chunk (`io_voix/main.py:47-67`). In the Discord mode it does not:

- `io_voix_muet` plays nothing. It publishes `io.voice.speak.end` as soon as the synthesis ends
  (`io_voix/main.py:42-67`, `stream` is `None`).
- `io_discord` plays each WAV from `speak_queue`, one after the other (`cogs/voice.py:119-134`). It
  is the only process that knows the true end, and it publishes no event.
- An empty last fragment (the end of most answers, and `stay_silent`) gives no
  `io.voice.speak.audio` (`io_voix/main.py:129-132`). `io_discord` thus never sees `is_last`.

When it breaks: now, the benchmark reports the end of the synthesis as "Fin Lecture Voix" in the
Discord mode, and `io_yeux` ends its pause before Aletheia stops. With P1 and decision 6, the queue
would open while she still speaks.

### F2 — RISK — two members with the same display name share their held text

`main.rs:518` sends the display name to the STT thread, not the `speaker_id`. `main.rs:630-631`
keeps the text of a long turn in a map keyed on that name.

When it breaks: two members have the same display name (Discord allows it), member A speaks for
more than 25 s (a forced cut, `segmenter.rs:22`), and member B ends a turn first. B's final segment
takes A's held text (`main.rs:656`). One message then holds the start of A's speech and all of B's
speech, with B's name.

The VAD stays correct: it keys on `speaker_id` (`main.rs:502`).

### F3 — RISK — a member who leaves during speech loses the turn

`SilenceGeneratorSink` makes silence frames for each member who spoke one time, until that member
leaves (`silence.py:50-63`, `sinks.py:629-631`). The silence frames are the only thing that closes
a segment: the segmenter needs 21 frames under the threshold (`segmenter.rs:5`, `:133`).

When it breaks: a member leaves the channel, or the bot leaves with `/voice leave`, while a member
speaks. No frame comes after that. The segment stays open in `pipelines` (`main.rs:497`). The text
of a long turn stays held in `pending` (`main.rs:630`). If the member comes back, the old audio
joins the new turn.

The map `pipelines` also never removes an entry. One entry is small (two buffers, one resampler),
thus the memory is not the problem. The lost turn is the problem.

### F4 — RISK — one STT thread for all the speakers

`main.rs:619-622` runs one Whisper on one thread. The code says it is a deliberate ceiling
(`ponytail:` at `main.rs:619-620`).

When it breaks: k members stop at the same moment. The k-th segment waits for k−1 transcriptions.
The README bench gives 102 ms for one segment on an idle GPU. During an inference, the GPU also runs
llama-server and Audio8, thus the real time is unknown (Q3). The time is not in the benchmark,
because `io.user.speak` is the ingress (`graphs/E2E.json`).

With the target (the last message wins), this ceiling costs less: the queue is in the order of the
end of speech, thus the last speaker to stop is still the last message, and the earlier messages
only need to reach the memory.

A second load comes from F3's mechanism: each member who spoke one time sends 50 silence frames
each second until they leave. The single VAD thread resamples and scores all of them (`main.rs:500-522`).
The frame channel has no limit (`main.rs:454`). If the VAD thread is too slow, the delay grows with
no limit and with no log. The audit did not measure the CPU cost (Q2).

### F5 — RISK — the benchmark and the cortex cannot separate two overlapping requests

`lobe.fragment_stream` has no `correlation_id`. The benchmark gives each non-ingress event to the
newest open transaction (`benchmark/main.py:372-376`). The cortex keeps one `global_session` and
removes it on any `is_last` (`cortex/main.rs:265-275`).

When it breaks: speaker B's `io.user.speak` arrives during the answer to A. The benchmark then
gives the fragments of A to transaction B, and reports a false latency. The project rule "measure
with `services/benchmark`" then gives false numbers for the multi-speaker mode.

### F6 — RISK — `io_discord` started before NATS stays deaf

`setup_nats` (`cogs/voice.py:103-109`) connects one time, and logs one error on a failure. After
that, `self.nc` stays `None`, and each frame is dropped in silence (`cogs/voice.py:55-57`). The
bot is in the channel, the `/voice join` answer is "Joined", and `io_oreilles` receives nothing.
The `terminal` manifest starts NATS first (`services.toml:189`, `depends_on`), thus the risk is for
a manual start or a NATS restart during the bot start.

### F7 — RISK — the docs do not agree with the code

See the Docs row of the table: the README sentence about "each complete sentence", the
`NATS_TOPICS.md` sentence about "continuously after a successful `/voice join`", and the
`MAX_CONCURRENT_INFERENCE` entry of `CLAUDE.md`.

## 5. Proposals

### P1 — for F1 and the target: a "last message wins" queue in `lobe_frontal`

Recommended. It removes F1 at its root, and it is the target.

**The change.** `prompt_handler` stops doing the inference. It does three things and returns at
once, thus `nats-py` never blocks on it again:

1. It registers the context future **at the arrival** of the message. The `context.ready` of a
   waiting message is then never lost.
2. It marks the message as "stale" when the worker is busy at that moment: its context will not
   contain the answer that runs now. The worker is busy from the start of a turn until Aletheia
   stops speaking (decision 6), not only until the last fragment.
3. It adds the message to a list, and wakes one worker task.

One worker task does the inference, one turn at a time:

```python
inbox: list[dict] = []          # cortex.prompt that wait for the LLM, oldest first
inbox_ready = asyncio.Event()
busy = False

def take_latest(batch: list[dict]) -> tuple[list[dict], dict | None]:
    """The last user message wins (decision 5). A proactive prompt never goes into the history,
    and a proactive prompt that waited is dropped: a turn ran during the wait, thus the boredom
    that made it is gone."""
    users = [m for m in batch if not m.get("source")]
    if users:
        return users[:-1], users[-1]
    fresh = [m for m in batch if not m["_stale"]]
    return [], (fresh[-1] if fresh else None)

async def prompt_handler(msg):
    data = json.loads(msg.data.decode())
    data["_context"] = pending_contexts[data["correlation_id"]] = loop.create_future()
    data["_stale"] = busy
    inbox.append(data)
    inbox_ready.set()

async def inference_worker():
    global busy
    while True:
        await inbox_ready.wait()
        inbox_ready.clear()
        batch, inbox[:] = inbox[:], []
        skipped, latest = take_latest(batch)
        busy = True
        try:
            for m in skipped:                       # memory only, no answer
                await history_add("user", m)        # acknowledged write, see below
            if latest is None:                      # only stale proactive prompts: drop them
                continue
            context = await wait_context(latest)    # the context from the cortex, 0.5 s, as today
            if latest["_stale"] or skipped:
                context = await rebuild_context(latest)  # same correlation_id
            await run_turn(latest, context)         # the present body of prompt_handler
            await speech_finished()                 # decision 6, see "End of the speech"
        finally:
            for m in batch:
                pending_contexts.pop(m["correlation_id"], None)
            busy = False
            if inbox:
                inbox_ready.set()
```

**Why the context must be built again.** A message that waited has a context from its arrival
time. That context does not hold the answer that ran during the wait, and it does not hold the
skipped messages. Thus the worker asks the hippocampe again, with the same `correlation_id`, after
the writes. It waits for the first context before, thus a late first context cannot fill the
second future.

**Why the writes must be acknowledged.** `hippocampe.history.add` is fire-and-forget. The worker
reads the history a few milliseconds after the writes. Thus it must know that the writes are in
PostgreSQL. The change in `hippocampe/main.py:96-102` is two lines: after `add_message`, answer the
request when `msg.reply` is set. The lobe uses `nc.request(..., timeout=0.5)` for the skipped
messages and for the final assistant write (`main.py:379-382`), and catches the timeout: a failed
write must not stop the turn. The user write at the start of a turn and the tool writes can stay
fire-and-forget: `nats-py` runs the history handler of the hippocampe one message at a time, in the
order of arrival, thus the acknowledgement of the final assistant write proves that the earlier
writes of the same turn are done.

**End of the speech (decision 6).** `speech_finished()` waits for `io.voice.speak.end` with
`is_last: true`. P8 makes this event true in the Discord mode. Two guards stop the worker from
waiting for ever:

- No TTS runs (the `sans-vocal` profile, `terminal/services.toml:258-259`): no
  `io.voice.speak.start` comes during the turn. The worker then waits 1 s after its last fragment,
  and continues. The first fragment goes out long before the last one, thus a running TTS has
  always sent `io.voice.speak.start` by then. A silent answer also sends it: `io_voix` publishes
  start and end for an empty last fragment (`io_voix/main.py:129-131`).
- The player stops (a crash of `io_discord`): a ceiling of 60 s after the last fragment, with one
  warning in the log.

The cost of decision 6: the answer to a waiting message starts after Aletheia stops. The person
then waits one LLM time to first token and one TTS first chunk, which is the normal path of a
message that arrives when she is silent (target: below 300 ms with the LLM part below 200 ms).

**What the LLM sees.** The history ends with the skipped messages (role `user`). The prompt is the
last message (role `user`). `prompt_builder.py:288-301` already joins two `user` messages in a row
into one. Thus the LLM reads "Bob: … Alice: …" in one turn and answers Alice, with Bob in its view.

**Files.** `services/lobe_frontal/main.py` (about +70 and −15 lines, with the subscriptions to
`io.voice.speak.start` and `io.voice.speak.end`),
`services/hippocampe/main.py` (+2 lines), `NATS_TOPICS.md` (`hippocampe.history.add` takes an
optional reply; `lobe_frontal` is a second publisher of `hippocampe.context.build`; the queue rule
under `cortex.prompt`), `CLAUDE.md` and `AGENTS.md` (the queue in the `lobe_frontal` entry).
`MAX_CONCURRENT_INFERENCE` must stay 1: write it in `CLAUDE.md`. The cortex does not change.

**Latency.** The path with no wait does not change: the worker uses the context of the cortex, as
today. The path with a wait adds one acknowledged write for each skipped message and one context
build. The person has already waited for a full answer at that moment.

**New risks.**

- A message that arrives a few milliseconds after the end of an answer is not "stale", but its
  context can miss the assistant write. This race exists today. Its window is the time of one
  PostgreSQL insert.
- The worker is one task. An exception outside the `try` stops the inference for good. Keep the
  existing `except Exception` of the turn, and log in the `finally`.
- The guard of 1 s of the end of the speech is a guess. If the TTS is slow to start, a message can
  start a turn while Aletheia still speaks. The log of the worker must say which guard released it.

**Proof.**

1. A unit test on `take_latest`: `[A, B, C]` gives `([A, B], C)`; `[user, proactive]` gives
   `([], user)`; `[proactive]` that is not stale gives `([], proactive)`; `[proactive]` that is
   stale gives `([], None)`.
2. A manual test: two members speak one after the other during one answer. The log of
   `lobe_frontal` shows no "correlation_id inconnu" line and no "Timeout contexte" line. The
   `messages` table of PostgreSQL holds both user messages. Aletheia speaks one answer.
3. `services/benchmark` on a single speaker, before and after: the steps `hippocampe_ready` and the
   first fragment do not move.
4. A manual test of decision 6: a member speaks while Aletheia speaks. The log of `lobe_frontal`
   shows the start of the next turn after `io.voice.speak.end` with `is_last`, not before.

**Alternative, not recommended: the queue in the cortex.** The cortex can hold the prompts until
the `is_last` of the current answer, then dispatch the last one. The context is then fresh by
construction. But the lobe writes the assistant answer **after** `is_last` (`main.py:379`), thus the
context build of the cortex races with that write each time, not by exception. The cortex would
also become a writer of the history for the skipped messages. And F1 would stay for any other
publisher of `cortex.prompt`.

### P8 — for F8: the process that plays the audio publishes the start and the end

Necessary for decision 6. Do it with P1.

**The change.** One meaning for `io.voice.speak.start` and `io.voice.speak.end`: "the speaker plays
now" and "the speaker stopped". The process that plays the sound publishes them.

- `io_voix`: with `MUTE_LOCAL_PLAYBACK`, do not publish `io.voice.speak.start` and `.end`, because
  it plays nothing. For an empty last fragment, publish `io.voice.speak.audio` with
  `"audio": ""` and `"is_last": true`, so the player knows that the answer ended. Add the `text`
  of the fragment to `io.voice.speak.audio`, because `io.voice.speak.start` carries it.
- `io_discord`: put `(audio or None, sequence, text, is_last)` in `speak_queue`.
  `_playback_loop` publishes `io.voice.speak.start` before `vc.play` and `io.voice.speak.end` after
  `done.wait()`. With no audio, or with no voice connection, it publishes the two events at once,
  so the end of the answer is never lost.

**Files.** `services/io_voix/main.py` (about 8 lines), `services/io_discord/cogs/voice.py` and
`voice.py` (about 20 lines), `NATS_TOPICS.md` (the publisher of `.start` and `.end` is the
player; the new fields of `io.voice.speak.audio`), `services/io_voix/README.md`.

**New risk.** `io.voice.speak.audio` then carries an empty audio. `decode_speak_audio`
(`voice.py:47-52`) already returns `None` for it. Any future subscriber must accept it too.

**Proof.** A unit test in `io_discord/tests/test_voice_cog.py`: an item with no audio and
`is_last` publishes `.start` and `.end`. A manual test in the Discord mode: in the benchmark,
`voice_playback_end` comes after the sound stops in Discord, not when the synthesis ends.
The benchmark and `io_yeux` change nothing: they then read the true end.

### P2 — for F2: key the held text on `speaker_id`

**The change.** Carry the `speaker_id` with the name from the VAD thread (`main.rs:518`) to the STT
thread. Key `pending` on the id (`main.rs:630`, `642`, `656`). Publish the name, as today. The
payload of `io.user.speak` does not change.

To test the held text at the same time (Tests row), move the logic of `main.rs:642-660` into a
small struct with two methods (`push(id, text)` and `finish(id) -> Option<String>`), and test it
with two speakers that share one name.

**Files.** `services/io_oreilles/src/main.rs`. About 30 lines with the struct and its test, 8
lines without it.

**New risk.** None for the payload. The tuple type of the channel changes at three places.

**Proof.** `cargo test`: two ids, one name, a partial for the first and a final for the second,
give two separate texts.

### P3 — for F3: close a segment when the frames of a speaker stop

**The change.** In the Discord VAD thread (`main.rs:500`), use `rx_frame.recv_timeout(1 s)` in
place of `recv()`. Keep the time of the last frame of each speaker. When a speaker has no frame for
more than 1 s, push 21 silence frames into that pipeline, and remove it from the map. A gap in the
frames means a departure, because `SilenceGeneratorSink` fills the silence while the member is
present.

**Files.** `services/io_oreilles/src/main.rs`, `services/io_oreilles/src/discord_audio.rs` (one
`flush` method that returns the last segment). About 25 lines.

**New risk.** A network gap of more than 1 s closes a turn early. The person's next words then
become a second message. With P1, the two messages go into the memory, and only the last one gets
an answer.

**Proof.** A unit test on `SpeakerPipeline::flush`: an open segment with enough speech frames gives
`SpeechEnded`. A manual test: a member leaves the channel during a sentence, and the log shows
`Speech ended for <name>`.

Alternative: `io_discord` publishes 700 ms of zero frames when a member leaves. It does not cover a
crash of `io_discord` or `/voice leave`. Not recommended.

### P4 — for F4: measure first, keep the single thread

Keep the single STT thread and the single VAD thread. The `ponytail:` note at `main.rs:619-620`
already names the ceiling. Do Q2 and Q3 first.

Change only if a number says so:

- If the STT queue time (Q3) is above 300 ms with three speakers: a second Whisper replica
  (`device_indices: vec![0, 0]` in `main.rs:588`, about +1.6 GB of VRAM) and a second STT thread.
  A batch through the low-level `ct2rs::sys::Whisper` is the other path; the high-level
  `Whisper::generate` takes one segment only (`ct2rs` `src/whisper.rs:89`).
- If the VAD thread uses more than half of one core (Q2): give Silero a probability of 0 for a
  frame of exact zeros, with no inference. `SilenceGeneratorSink` sends exact zeros. Measure the
  effect on the first word of the next turn before you keep it, because the Silero state then does
  not see the silence.
- Put a limit on the frame channel (`main.rs:454`, `sync_channel`) and log a drop, so a VAD thread
  that is too slow becomes visible.

### P5 — for F5: `correlation_id` on `lobe.fragment_stream`

**The change.** `publish_fragment` (`lobe_frontal/main.py:74-82`) adds the `correlation_id` of the
turn. The benchmark matches an event on its `correlation_id` when the event has one, and on the
newest transaction when it has none (the ingress topics). The cortex can then close the correct
session.

**Files.** `services/lobe_frontal/main.py` (the turn passes the id to `publish_fragment` and to
`execute_tool_calls`), `services/benchmark/main.py`, `NATS_TOPICS.md`. About 20 lines. `io_voix`,
`io_discord` and the cortex read JSON fields by name and ignore a new field.

**New risk.** The ingress topics have no `correlation_id` (the cortex makes it), thus the benchmark
still matches `io.user.speak` to `cortex.prompt` by time. That step is short.

**Proof.** Run the benchmark with two overlapping text messages from `io_text`. Each report shows
its own fragments.

### P6 — for F6: try the NATS connection again

**The change.** In `setup_nats` (`cogs/voice.py:103-109`), try again every 2 s until the connection
succeeds. Log one line for each failure. The `presence` and `text` cogs have the same code
(`cogs/presence.py:33-35`, `cogs/text.py:43-45`): change the three, or move the loop into one
helper of `bot.py`.

**Files.** `services/io_discord/cogs/voice.py` (and the two other cogs). About 8 lines.

**Proof.** Start `io_discord` with NATS stopped, then start NATS. `/voice join`, speak, and see
`Speech ended` in the log of `io_oreilles`.

### P7 — for F7: correct the docs

- `services/io_oreilles/README.md`: "at the end of each turn of speech", not "at the end of each
  complete sentence".
- `NATS_TOPICS.md`, `io.discord.voice.frame`: "for each member, from the first packet of that
  member until that member leaves the channel. During a silence, the frames hold zeros."
- `CLAUDE.md` and `AGENTS.md`: with P1, "`MAX_CONCURRENT_INFERENCE` must stay 1: the queue of
  `lobe_frontal` answers one message at a time." Without P1, remove the variable from the list,
  because it has no effect.

## 6. Open questions

| # | Question | What settles it |
|---|---|---|
| Q1 | The end-to-end latency in the Discord mode, one speaker. | Start the `discord` profile of `services/terminal`, then `cd services/benchmark && python main.py`, and speak 10 times. Read the time from `io.user.speak` to the first `io.voice.speak.audio`. |
| Q2 | The CPU cost of the VAD thread with N members present. | Join with 1, 3 and 5 members (silent after one word each). Run `pidstat -t -p $(pgrep -f 'io_oreilles --discord') 1 30` and read the VAD thread line. |
| Q3 | The STT queue time when several members stop together, during an inference. | Three members stop at the same moment while Aletheia answers. In the log of `io_oreilles`, take the time between each `Speech ended for <name>` line and its `Transcribed in` line. |
| Q4 | Does Aletheia's voice come back through the microphone of a member who uses speakers? | One member uses speakers, with the echo cancellation of Discord on, then off. Look for her own sentences in `io.user.speak`. With P1, an echo becomes the "last message" and gets an answer. |
| Q5 | Do the Rust tests of `io_oreilles` pass? | **Settled**: 24 tests pass before the changes, 27 after (`cargo test --release`). The toolchain is in `/mnt/forge/.rustup`; a shell that does not read `~/.zshrc` must set `RUSTUP_HOME` and `CARGO_HOME`. |
| Q6 | Must a waiting proactive prompt lose to a user message? Must the queue also cover the time when Aletheia still speaks? | **Settled** by the user: yes to the two (decisions 5 and 6 in section 0). P1 and P8 apply them. |
| Q7 | Is the guard of 1 s (no `io.voice.speak.start` during the turn means no TTS) long enough? | In the `discord` profile, log the time from the first fragment to `io.voice.speak.start` for 20 answers. Keep the guard above the worst value. |

## 7. Implementation status

On 2026-10-12, after this audit, the user asked for P1, P8, P3, P5 and P7. P2, P4 and P6 stay on
paper.

| Proposal | Status | Files | Proof |
|---|---|---|---|
| P1 | Done | `lobe_frontal/main.py`, `lobe_frontal/src/turn_queue.py`, `hippocampe/main.py` | `tests/test_turn_queue.py` (7 tests). A smoke test of the real `main.py` on a throwaway NATS, with a fake hippocampe, LLM and player: see below. |
| P8 | Done | `io_voix/main.py`, `io_voix/audio.py`, `io_discord/cogs/voice.py`, `io_discord/voice.py` | `io_voix`: 9 tests (2 new). `io_discord`: 19 tests (the playback tests now check `.start` and `.end`, also with no voice connection). |
| P3 | Done | `io_oreilles/src/main.rs`, `io_oreilles/src/discord_audio.rs`, `io_oreilles/src/segmenter.rs` | `cargo test --release`: 27 tests (3 new on `SpeakerPipeline::flush`). The CUDA binary is rebuilt (`target/release/io_oreilles`). |
| P5 | Done | `lobe_frontal/main.py`, `io_voix`, `io_discord`, `benchmark/main.py` | `benchmark/tests/test_matching.py` (4 tests). The `correlation_id` goes from `lobe.fragment_stream` to `io.voice.speak.*`. |
| P7 | Done | `NATS_TOPICS.md`, `CLAUDE.md`, `AGENTS.md`, the READMEs of `io_oreilles`, `io_voix`, `io_discord`, `io_yeux`, `lobe_frontal`, `benchmark` | Read. |

The smoke test of P1 sends 8 `cortex.prompt` messages. The results:

- Message B arrives during the answer to A. It is written to the history with no answer. The LLM
  sees "Bob: …" and "Alice: …" in one turn, and answers C.
- The two proactive prompts that waited are dropped. They are not in the history.
- The context of C is built again after the end of A's speech: it holds A's answer and B.
- A message that arrives while Aletheia speaks the answer to C waits for the `io.voice.speak.end`
  of C (5.47 s), and its first fragment comes after it (6.68 s).
- A turn with no player releases the queue 1 s after its last fragment.
- No "correlation_id inconnu" line and no "Timeout contexte" line in the log.

Not done: a live run with Discord, the GPU and the benchmark (Q1 to Q4, Q7). The cortex still
uses one `global_session`; P5 makes the correct session visible to it, but the cortex does not use
it yet.
