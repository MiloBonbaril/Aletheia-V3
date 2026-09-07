# Audit — io_oreilles, audio segments for whisper-large-v3-turbo

Date: 2026-09-07. Machine: RTX 5070 Ti, 16303 MiB. Build: `--release --features cuda`, CUDA 13.
Model: `model/whisper-large-turbo-ct2`, compute type `int8_float16`.

## 1. Scope

The audit examines how `io_oreilles` makes the audio segments, and what these segments cost when
`whisper-large-v3-turbo` reads them.

In scope:

| Item | Reason |
|---|---|
| `services/io_oreilles/src/segmenter.rs` | It makes the segments. |
| `services/io_oreilles/src/main.rs` | It captures the audio, calls Whisper and publishes the text. |
| `services/io_oreilles/src/discord_audio.rs` | It makes the segments in the Discord mode. |
| `services/io_oreilles/src/bin/bench_stt.rs` | It measures the latency. |
| `services/io_oreilles/README.md` | It documents the model and the measurements. |
| Topic `io.user.speak` | The egress of the service. |
| Topic `io.user.speak.raw` | The egress in RAW mode. |
| Topic `io.discord.voice.frame` | The ingress in the Discord mode. |
| `STT_LANGUAGE`, `STT_MODEL_PATH`, `RAW_AUDIO`, `ORT_DYLIB_PATH`, `NATS_URL` | The configuration. |
| `ct2rs` 0.9.18, `Whisper::generate` | It cuts the audio into windows of 30 s. |
| VRAM of the service | A question of the audit. |

Out of scope:

- The quality of the Silero VAD model. The audit examines what the service does with the decisions
  of the VAD, not the decisions themselves.
- `services/io_discord`. It is the source of the Discord frames. The audit reads only the payload
  contract.
- `services/cortex`. The audit reads only the subscriber side of `io.user.speak`.
- The word error rate of the model. The audit did not run a WER measurement. See the open questions.

### Test corpus

The repository contains no WAV corpus. The exports of the hippocampe are absent from the disk. The
audit thus makes its own corpus with Kokoro (`services/io_voix/models/kokoro-v1.0.onnx`, voice
`ff_siwis`), and resamples it to 16 kHz mono with ffmpeg. The corpus has 20 French segments and
62.7 s of audio.

The corpus agrees with the corpus of the README: the median of 165.2 ms that the audit measures with
automatic language detection is the same as the 165 ms in the README table. Thus the numbers below
compare correctly with the numbers that are already in the repository.

The audit changed no file in the repository. It added the temporary flag `--beam` to `bench_stt.rs`
for the measurements, and then removed it. `git status` shows this report as the only change.

## 2. Verdicts

| Lens | Verdict | Evidence |
|---|---|---|
| Segments — maximum length | **DEFECT** | `segmenter.rs:15-52` has no limit. `ct2rs` `whisper.rs:101` makes one window of 30 s for each 30 s of audio. Measured: 604 ms and 2306 MiB for 95 s of speech. See F2. |
| Segments — minimum length | **DEFECT** | One speech frame (32 ms) makes a segment (`segmenter.rs:33-41`). Whisper invents a subtitle credit for such a segment. `main.rs:545` removes only an empty text. See F1. |
| Silence — the tail | **OK** | The silence tail of 600 ms costs 2 ms (99.5 ms → 101.6 ms) and does not change the text. Whisper fills each window to 30 s, thus the silence is free. |
| Silence — the start | **RISK** | The segment starts at the first speech frame. There is no audio before it (`segmenter.rs:33-41`). A late VAD decision removes the start of the first word. See F4. |
| Latency | **DEFECT** | `STT_LANGUAGE` has no value, thus Whisper detects the language for each segment. Measured cost: 165.2 ms against 92.4 ms, which is 73 ms and 44 % of the time. See F3. |
| VRAM | **RISK** | The floor is 1602 MiB. A long segment adds up to 704 MiB, with no limit. The memory returns to 1602 MiB after the segment. See F2 and section 5. |
| Contract | **OK** | `main.rs:560-565` publishes `{text, speaker?}`. `NATS_TOPICS.md:38-50` gives the same payload. `services/cortex/src/main.rs:376-390` reads the same two fields. The cortex makes the `correlation_id`, because `io_oreilles` is an ingress. |
| Failure | **DEFECT** | `main.rs` used `unwrap()` and `expect()` in threads: a panic stopped the hearing and not the process. Worse, a wrong `ORT_DYLIB_PATH` blocked the service for ever with no message (measured). See F5. |
| Concurrency | **RISK** | One thread does the transcription (`main.rs:534`). This is a decision. But a segment with no length limit makes the wait for the other speakers long. See F6. |
| Config | **DEFECT** | No Rust service uses a `.env` file. No `Cargo.toml` contains `dotenv`. Thus `STT_LANGUAGE` comes only from the shell, and it usually has no value. See F3 and F7. |
| Tests | **RISK** | The 12 tests pass. They cover the transitions of the segmenter, the mode selection, the event and the downmix. They do not cover a length limit, because no limit exists. See F8. |
| Docs | **RISK** | `bench_stt.rs:4-5` says the corpus is real audio of the pipeline. This is not correct now. `README.md:118` gives the 165 ms figure but does not say that `STT_LANGUAGE` removes 73 ms. `CLAUDE.md:146` says each service reads a `.env` file; the Rust services do not. See F7. |
| Dead code | **OK** | `io_oreilles` has no path that nothing reaches. |

## 3. Measurements

All the measurements use `bench_stt`, the GPU, the compute type `int8_float16` and the model
`whisper-large-turbo-ct2`. The VRAM column is the peak of the process alone, from
`nvidia-smi --query-compute-apps`.

**Compare only inside one table.** The absolute values move approximately 10 % between two sessions
of the day, with the clock state of the GPU. Each table below comes from one campaign, measured one
configuration after the other. Inside a campaign the repetition is stable: three measurements of the
same configuration give 101.5, 101.5 and 100.9 ms.

### 3.1 The language detection is the largest latency item

20 segments, 62.7 s, 3 measurements for each file.

| Configuration | Median | p90 | Max | VRAM |
|---|---|---|---|---|
| `auto` — the language detection, what the service did | 181.2 ms | 190.5 ms | 194.3 ms | 1602 MiB |
| `STT_LANGUAGE=fr` — **the default after P3** | **102.1 ms** | 109.3 ms | 113.4 ms | 1602 MiB |

The language detection is one more pass of the encoder for each segment. It costs **79 ms**, which
is 44 % of the transcription time.

The beam size is not a control here. A separate campaign gives 92.4 ms at beam 5 and 89.4 ms at
beam 1, for the same 1602 MiB: 3 ms and 0 MiB. The turbo model has only 4 decoder layers, and the
answers are short. Do not change it.

### 3.2 A segment longer than 30 s multiplies the windows

`STT_LANGUAGE=fr`, beam 5, 3 measurements for each file. One French monologue, cut to each length.

| Audio | Windows of 30 s | Median | VRAM peak |
|---|---|---|---|
| 3 s (a usual sentence) | 1 | 92 ms | 1602 MiB |
| 25 s | 1 | 249 ms | 1602 MiB |
| 29 s | 1 | 272 ms | 1602 MiB |
| 35 s | 2 | 354 ms | 1922 MiB |
| 65 s | 3 | 477 ms | 2050 MiB |
| 95 s | 4 | 604 ms | 2306 MiB |

`ct2rs` does not refuse a long segment and does not cut it. It makes one window for each 30 s
(`whisper.rs:101`), and gives all the windows to the model in one batch. Each window adds
approximately 230 MiB and approximately 90 ms. Nothing limits the number of windows.

A time series of the VRAM shows that the memory returns to 1604 MiB after the long segment. The peak
is thus a spike, not a permanent cost. But the spike is large and it is not predictable.

The windows have no shared context. `whisper.rs:152` gives the same start prompt to each window, and
`main.rs:542` joins the results with a space. Thus the model loses the text at each cut. Measured on
the 35 s file, the joint gives `…ynthies Free Vocal.  Donc si quelqu'un parle…` for the words
"synthèse vocale". On the 65 s file, one full clause disappears at the joint.

### 3.3 The silence at the end is free

`STT_LANGUAGE=fr`, beam 5, 5 measurements for each file. The same sentence, with different silence.

| File | Audio | Median | Text |
|---|---|---|---|
| Speech only | 4.6 s | 99.5 ms | correct |
| Speech + 0.7 s of silence (what the VAD adds today) | 5.3 s | 101.6 ms | correct |
| Speech + 10 s of silence | 14.6 s | 110.3 ms | correct |
| 5 s of silence + speech + 20 s of silence | 29.6 s | 128.4 ms | correct |
| 3 s of silence only | 3.0 s | 84.1 ms | **"Sous-titrage ST' 501"** |

The silence tail of the VAD costs 2 ms. Do not remove it. Whisper fills the window to 30 s in all
cases, thus a short segment and a long segment have almost the same cost.

But a segment with no speech gives an invented text.

### 3.4 A short sound makes the entity speak

The audit first made synthetic noise: digital silence, white noise at −50 dB and −35 dB, and a short
click. **Silero refuses all four.** The maximum probability stays between 0.01 and 0.10, thus the
segmenter never opens a segment. A door or a key on a keyboard is therefore not sufficient. The
first version of this audit said the opposite; the measurement corrects it.

The real path is a short sound that has the shape of speech: a cough, a throat clear, an isolated
syllable, one word of a person in the room. Silero gives 1.00 to these. The audit cut such bursts
from a real segment of the corpus.

| Input | Silero maximum | Text that Whisper gives |
|---|---|---|
| 100 ms of speech | 1.00 | `Merci.` |
| 160 ms of speech | 1.00 | `Merci d'avoir regardé cette vidéo !` |
| 224 ms of speech | 1.00 | `Salut !` |
| 320 ms of speech | 1.00 | `Salut !` |
| 480 ms of speech | 1.00 | `Salut !` |

`main.rs:545` removes only an empty text. These texts are not empty. Thus the service publishes them
on `io.user.speak`. The cortex then starts a full interaction: the LLM answers, the TTS speaks, and
the hippocampe writes the exchange to the history.

A segment with no speech at all gives the same result when it reaches Whisper (section 3.3, the
last row). But Silero does not send such a segment, thus this case stays theoretical.

### 3.5 The VAD opens the segment after the start of the word

The segmenter used to start the segment at the first frame above the threshold. It kept no audio
before it. The audit measured how late that frame is, against the acoustic start of the word.

On the corpus with no added noise, Silero is fast: 0 frames in the median, 3 frames (96 ms) in the
worst case. But these files start on the word. A real microphone gives room tone first, and a word
that starts with a consonant comes up over some tens of milliseconds. With 1 s of room tone and an
attack of 150 ms, the delay grows:

| Room tone | Median | p90 | Max |
|---|---|---|---|
| −48 dB | 2 frames | 4 frames | 4 frames (128 ms) |
| −36 dB | 1 frame | 4 frames | 4 frames (128 ms) |
| −30 dB | 2 frames | 4 frames | 5 frames (160 ms) |
| −24 dB | 2 frames | 4 frames | 5 frames (160 ms) |

The audio in this interval is lost. What it costs is visible when it is removed from a correct
segment:

| Cut at the start | Text that Whisper gives |
|---|---|
| 0 ms | `J'élance le N-Benchmark FR. La mediane est à 165 millisecondes.` |
| 100 ms | `J'élance le NBenchMock FR. La mediane est à 165 millisecondes.` |
| 200 ms | `et lance le NBenchMockF. La mediane est à 165 millisecondes.` |
| 300 ms | `Lance le NBenchMockF, la mediane est à 165 millisecondes.` |

The rest of the sentence stays correct. Only the start is damaged.

## 4. Findings

### F1 — DEFECT — A short sound makes an invented sentence, and the entity answers it

**Status: corrected. See P1.**

**Evidence:** `segmenter.rs:33-41` starts a segment on one frame of 32 ms. `main.rs:545` removes only
an empty text. Section 3.4 shows the invented texts.

**When it breaks:** each time a sound that has the shape of speech lasts less than a word. A cough,
a throat clear, an "ah", one word of a different person in the room. Silero gives 1.00 to these
sounds, thus the segment opens. The entity then answers to nothing, speaks, and writes the exchange
to the memory.

Silero refuses digital silence, white noise and a click (section 3.4). Thus the trigger is more
narrow than the first version of this audit said, but it is not rare: a cough is sufficient.

**Severity:** the highest. It is visible to the public in a live stream, and it makes the memory
dirty.

### F2 — DEFECT — The segment has no maximum length

**Status: corrected. See P2.**

**Evidence:** `segmenter.rs:15-52` has no limit on `speech_buffer`. `ct2rs` `whisper.rs:101` makes
one window for each 30 s. Section 3.2 gives the cost.

**When it breaks:** a person speaks and makes no pause longer than 672 ms. The buffer grows without
a limit. At 95 s the service needs 604 ms and 2306 MiB. At 4 minutes it needs 8 windows. The GPU is
shared with the LLM and with the TTS, thus a spike can make the memory full.

The model does not lose the audio, but it loses the text at each joint of 30 s (section 3.2).

**Severity:** high. It is the answer to the question "do the segments agree with the 30 s of the
model": today they do not, and nothing tells the operator.

### F3 — DEFECT — `STT_LANGUAGE` has no value, and the transcription costs 44 % more

**Status: corrected. See P3.**

**Evidence:** `main.rs:524` reads the variable and gives `None` to Whisper when it is absent. No Rust
service reads a `.env` file: no `Cargo.toml` in `services/` contains `dotenv`. Section 3.1 measures
165.2 ms against 92.4 ms.

**When it breaks:** always. The service starts with `cargo run --release` and no variable.

**Severity:** high, because the correction is one line.

### F4 — RISK — There is no audio before the first speech frame

**Status: corrected. See P4.**

**Evidence:** `segmenter.rs:33-41`. Section 3.5 measures a delay of up to 5 frames (160 ms) with
room tone, and shows the damage that this quantity of missing audio makes.

**When it breaks:** the VAD needs a small time to pass 0.5. A word that starts with a weak sound (a
vowel, an "f", an "s") loses its start. The user hears a wrong first word.

### F5 — DEFECT — A panic in a thread makes the service deaf with no message

**Status: corrected. See P5.** The audit raised this finding from RISK to DEFECT: the test of the
correction found a case where the service blocks for ever, which is worse than a dead thread.

**Evidence:** `main.rs` used `unwrap()` on `blocking_send` in the microphone VAD thread, and
`expect()` on the load of the Silero model inside the Discord thread. `discord_audio.rs` used
`expect()` on the construction of the resampler of each speaker.

**When it breaks:** the STT thread stops, thus the channel closes, thus the VAD thread panics on the
`unwrap()`. The process continues. NATS stays connected. The service looks correct and hears
nothing. Nothing publishes an error on the bus.

**And a worse case, found during the test of P5:** with a wrong `ORT_DYLIB_PATH`, the service does
not stop and does not give an error. It **blocks for ever**. The `ort` crate loads the library on
the first use, and a wrong path makes its single initialization fail with no way back. Measured:
after 25 s, all the threads are in `futex_do_wait`, the last line of the log is
`Initializing Silero VAD Model...`, and the process holds its connection to NATS. A supervisor sees
a healthy process. This is the exact fault this finding is about, in its worst form.

### F6 — RISK — One long segment stops all the other speakers

**Evidence:** `main.rs:534` starts one STT thread. The comment says the serial order is a decision.
Section 3.2 measures 604 ms for one segment of 95 s.

**When it breaks:** in the Discord mode, with more than one speaker. Speaker B waits for the full
transcription of speaker A. With F2, this wait has no limit.

### F7 — RISK — The documentation does not agree with the code

**Evidence:**

- `bench_stt.rs:4-5` says the WAV files of `--dir` are real extracts of the pipeline. The files are
  absent, and the README table now comes from synthetic audio.
- `README.md:118` says "with automatic language detection" but does not say that this choice costs
  73 ms.
- `CLAUDE.md:146` says each service reads its own `.env` file. The Rust services do not.

### F8 — RISK — No test covers the length of a segment

**Evidence:** `segmenter.rs:62-128`. The tests cover the transitions and the independence of the
instances. They do not cover a maximum length or a minimum length, because the code has neither.

## 5. Proposals

### P1 — for F1 — Ask for a minimum quantity of speech — **APPLIED 2026-09-07**

**Change:** `VadSegmenter` counts the frames with a probability above the threshold. `SpeechEnded`
comes only when this count is 8 or more, which is 256 ms of speech. Below this, the segmenter clears
the buffer and returns `Idle`.

**Files:** `src/segmenter.rs` (approximately 8 lines), plus 2 tests in the same file.

**New risk:** a very short word ("oui", "non") that lasts less than 256 ms is lost. Measure with the
corpus: `seg_07.wav` is the word "Bon." and has 416 ms of speech, which is 13 frames, thus it
stays. Start at 8 frames and lower the value if a real word disappears.

**Proof, measured after the change:**

- `cargo test --release --features cuda`: 15 tests, 0 failures. Four of them are new and cover the
  minimum, the limit value, and the state after a rejection.
- The full pipeline (the real Silero model, then `VadSegmenter`) on the 20 segments of the corpus:
  **20 segments sent, 0 rejected**. The change removes no real speech.
- The same pipeline on the bursts of section 3.4: the bursts of 100, 160, 224 and 320 ms are
  **rejected** (4 to 7 speech frames). The burst of 480 ms passes, which is correct: 480 ms of real
  speech is a word.

`bench_stt` cannot prove this change. It reads the WAV files and calls `whisper.generate` directly,
thus it does not use the segmenter.

**The other solution, and why the audit does not recommend it:** CTranslate2 gives
`no_speech_prob`, and `WhisperOptions` has `return_no_speech_prob` (`ct2rs` `sys/whisper.rs:72`).
But `ct2rs::Whisper::generate` keeps only `res.sequences` and removes the rest
(`ct2rs` `whisper.rs:157-167`). To read this probability, the service must leave the high-level API
and make the mel spectrogram itself. This is a large change for the same result.

### P2 — for F2 — Cut the audio at 25 s, but publish only one text — **APPLIED 2026-09-07**

**The first version of this proposal was wrong.** It made the segmenter return `SpeechEnded` at the
ceiling, thus the service published each part of 25 s on `io.user.speak`. The cortex then started an
interaction on half a sentence, and the entity spoke while the person continued to speak. The
correction separates two things that the old code held together: the end of an **audio chunk** and
the end of a **turn of speech**.

**Change:**

1. `VadSegmenter` holds a maximum of 400 000 samples, which is 25 s at 16 kHz. Above this,
   `push_frame` returns the new outcome `SpeechContinues(chunk)`, keeps `is_speaking` at `true`, and
   keeps the remainder of the buffer for the next chunk.
2. The cut prefers the most recent pause of the buffer (`last_pause_at`), if this pause is above
   15 s. Thus no word is cut, and no audio is repeated. If the person made no pause, the cut is flat
   and it carries 500 ms into the next chunk, thus the cut word is complete in that chunk.
3. The channel to the STT thread carries a flag: is this chunk the last one of the turn?
4. The STT thread transcribes each chunk immediately, and keeps the text in a map with the speaker
   as the key. It publishes one message only, when the flag says the turn is complete. The text is
   the parts joined with a space.
5. `MIN_SPEECH_FRAMES` (P1) does not apply to the last chunk of a long turn. Two words after a
   forced cut are a correct end, and to reject them would also lose the text of the full turn.

**Files:** `src/segmenter.rs` (approximately 45 lines with 6 tests), `src/main.rs` (approximately
30 lines), `src/discord_audio.rs` (approximately 8 lines), plus the README.

**New risk:** on the flat cut, the joint repeats a fraction of a word ("la synthè synthèse vocale").
The path is rare: it needs 15 s with no pause that Silero sees. A `ponytail:` comment marks it, with
the upgrade path (align the end of chunk N on the start of chunk N+1).

**Proof, measured on the monologue of 95 s (`STT_LANGUAGE=fr`, the real Silero model, the real
segmenter, then Whisper for each chunk):**

| | One call (before) | Chunks of 25 s (after) |
|---|---|---|
| Chunks | 1 of 95 s | 23.2 s, 24.6 s, 25.2 s, 22.8 s |
| Windows of 30 s in one batch | 4 | 1 |
| **Time after the person stops** | **658 ms** | **256 ms** |
| **VRAM peak** | **2306 MiB** | **1602 MiB** |
| Text | 2096 characters | 2151 characters |

The first three chunks are transcribed while the person continues to speak. Only the last chunk
(256 ms) is between the end of the speech and the message on `io.user.speak`.

The text of the chunks is more complete, because the joints of the windows of 30 s lose words. The
one-call version gives `…un nouveau segment demeure immédiatement derrière.  reste prévisible en
toutes circonstances.`: one full clause is absent. The chunk version keeps it. The audit sees no
repeated word in the chunk version, thus all the cuts landed in a pause.

`cargo test --release --features cuda`: 21 tests, 0 failures. Six of them are new: the ceiling, the
cut in the pause, the overlap of the flat cut, the maximum size of a chunk on 90 s of speech, the
last chunk that P1 must not reject, and the return to the P1 rule after the turn.

### P3 — for F3 — Give `STT_LANGUAGE` a default value — **APPLIED 2026-09-07**

**Change:** the service resolves the language with the same pattern as `STT_MODEL_PATH`. `auto`
keeps the language detection, an empty value takes the default.

```rust
let stt_language: Option<String> = match std::env::var("STT_LANGUAGE").as_deref() {
    Ok("auto") => None,
    Ok(lang) if !lang.is_empty() => Some(lang.to_string()),
    _ => Some("fr".to_string()),
};
```

`bench_stt` uses the same resolution. To measure with the language detection while the service runs
in French is what hid these 79 ms. The bench also writes the language in its header line.

**Files:** `src/main.rs` (5 lines), `src/bin/bench_stt.rs` (5 lines), plus the README.

**New risk:** the service transcribes English speech as French. Aletheia speaks French with a French
user, thus this is correct. `STT_LANGUAGE=auto` gives the old behaviour, and `STT_LANGUAGE=en` forces
English. The variable stays, because a build of CTranslate2 with CUDA takes a quarter of an hour: it
must not be the price of a change of language.

**Proof, measured on the 20 segments of the corpus, one campaign:**

| Language | Median | p90 | Max | VRAM |
|---|---|---|---|---|
| `auto` | 181.2 ms | 190.5 ms | 194.3 ms | 1602 MiB |
| `fr` (the default, with no variable in the environment) | **102.1 ms** | 109.3 ms | 113.4 ms | 1602 MiB |

79 ms, which is 44 %. Three repetitions of the `fr` configuration give 101.5, 101.5 and 100.9 ms,
thus the measurement is stable. The header line of the bench shows `langue=fr` with no variable in
the environment, and `langue=auto` with `STT_LANGUAGE=auto`: the two paths work.

**The other solution:** add the `dotenvy` crate and a `.env.example` file. This agrees with
`CLAUDE.md:146`, but it adds a dependency to three Rust services for one variable. The audit
recommends the default value, and a correction of `CLAUDE.md` (see P7).

### P4 — for F4 — Keep 224 ms of audio before the speech — **APPLIED 2026-09-07**

**Change:** `VadSegmenter` holds the last 7 frames of idle audio in a ring. On the first speech
frame, it puts this ring in front of the buffer. The ring is empty at the end of each turn, thus the
audio of one turn never goes into the next one.

The pre-roll is not speech: it does not count in `speech_frames`, thus it cannot make a cough pass
the `MIN_SPEECH_FRAMES` rule of P1.

**Files:** `src/segmenter.rs` (approximately 15 lines with 3 tests).

**Why 7 frames:** section 3.5 measures a delay of 5 frames (160 ms) in the worst case, with room
tone at −24 dB. 7 frames is 224 ms, which is 1.4 times this worst case. The margin costs
approximately 2 ms, because Whisper fills each segment to 30 s in all cases (section 3.3).

**New risk:** none for the latency. With a loud loudspeaker in the room, the pre-roll can hold the
end of the answer of the entity. This is an echo problem, and the pre-roll does not make it.

**Proof, measured on the 20 segments of the corpus with room tone at −36 dB, the real Silero model
and the real segmenter, then Whisper on the segment with and without its pre-roll:**

- **4 segments of 20** give a different start. **2 of them are corrected**, **0 are damaged**:

  | Segment | Without the pre-roll | With the pre-roll |
  |---|---|---|
  | 15 | `On vient de` | `Combien de mémoire` (correct) |
  | 16 | `Comment dire, c'est` | `Euh, comment dire,` (correct) |
  | 14 | `Le busnat ne` | `Le bus NAC` (both are wrong) |
  | 0 | `Salut Eneles et` | `Salut Pénalès et` (both are wrong) |

- The 16 other segments do not change.

**Be honest about the size of the gain:** on the corpus with no added noise, the pre-roll changes
nothing, because Silero fires at the same frame as the word. The gain appears with room tone, which
is the real condition. `cargo test --release --features cuda`: 24 tests, 0 failures.

### P5 — for F5 — Stop the process with an explicit cause — **APPLIED 2026-09-07**

**Change, in three parts:**

1. **What can fail at start-up now fails at start-up, with `?`.** The resampler of the microphone
   and the Silero model of the Discord mode are built before their thread, thus their cause goes to
   `main` and the process stops with the message of `anyhow`.
2. **What can fail in a thread calls `fatal()`.** A background thread cannot return an error, thus
   this function writes two lines and stops the process with the code 1: the cause, and the reason
   why the service cannot continue. Nine sites use it: the two sends of the microphone VAD thread,
   the subscription to `io.discord.voice.frame`, the send to the Discord VAD thread, the
   construction of the pipeline of a speaker, the send of the Discord VAD thread, the send of the
   STT thread, and the two resampler outputs. After this change, no thread of this service can die
   in silence.
3. **`ORT_DYLIB_PATH` is verified before the first Silero session.** This is the new part, and it
   comes from the test. `ort` gives no error on a wrong path: it blocks. The service now stops with
   the path that is wrong and what to do.

`SpeakerPipeline::new()` gives a `Result` for this, and the last two `unwrap()` of the audio path
(`out.pop()` on the resampler output) give `fatal()` too. They are true invariants — the number of
channels is fixed at the construction — but an invariant that breaks inside a thread is exactly the
fault of this finding.

The two main loops no longer end with `Ok(())`. They end with an error: to leave these loops means
that nothing can publish any more.

**Files:** `src/main.rs` (approximately 60 lines), `src/discord_audio.rs` (approximately 12 lines).

**New risk:** the service stops instead of continuing without hearing. This is the correct
behaviour: a supervisor restarts it, and the operator sees the cause. `fatal()` uses
`std::process::exit`, thus it runs no destructor. There is nothing to write and nothing to drain
here, and `tracing` writes on stderr with no buffer, thus the message goes out. A `ponytail:`
comment marks this limit and gives the upgrade path (a stop channel).

**Proof, measured with the service started for real (NATS up, then stopped again):**

| Fault | Before | After |
|---|---|---|
| NATS absent | code 1, `Failed to connect to NATS` | no change, already correct |
| `STT_MODEL_PATH` wrong | code 1, `Failed to load Whisper model from …` | no change, already correct |
| `ORT_DYLIB_PATH` wrong | **blocks for ever, no message** | **code 1**, `ORT_DYLIB_PATH points at '…', which is not a readable file.` |
| `ORT_DYLIB_PATH` wrong, `--discord` | **blocks for ever, no message** | **code 1**, the same message |

The correct path does not change: the service starts, and gives
`STT ready (language: fr). Waiting for speech events...`.

### P6 — for F6 — Keep one thread. Write an ADR.

The serial order is correct: CTranslate2 has one replica, and the segments of one turn must stay in
order. P2 gives the limit that makes the wait predictable (272 ms and not more). A second replica
adds 1602 MiB of VRAM for a case with two speakers that speak at the same time.

**Change:** none in the code. Write `docs/adr/0003-io-oreilles-one-stt-thread.md` and record the
decision, the limit of 272 ms that P2 gives, and the condition to change it (a Discord channel with
more than two active speakers).

### P7 — for F7 — Correct the three documents

- `bench_stt.rs:4-5`: say that the corpus is the choice of the operator, and that the numbers of the
  README come from synthetic French audio (Kokoro, voice `ff_siwis`).
- `README.md`: add a row for `STT_LANGUAGE=fr` in the measurement table, and the table of
  section 3.2 for the long segments.
- `CLAUDE.md:146`: say that the Python services read a `.env` file, and that the Rust services read
  the shell environment only.

### P8 — for F8 — Add the tests with P1, P2 and P4

Each proposal above carries its own test in `src/segmenter.rs`. No new file and no framework. The
tests are: a segment that is too short gives `Idle`; a buffer that is full gives `SpeechEnded` and
keeps `is_speaking`; the audio before the speech is in the segment.

### VRAM — the full answer

The floor of 1602 MiB is the model and the CUDA context. To go below it, the choices are:

| Action | Gain | Cost | Recommendation |
|---|---|---|---|
| P2, a limit on the segment | Up to **704 MiB** on the peak | 10 lines | **Do it.** It is the only real gain. |
| `beam_size = 1` | **0 MiB**, 3 ms | 1 line | No. Measured, section 3.1. |
| `CT2_CUDA_CACHING_ALLOCATOR_CONFIG` | Up to 200 MiB | More calls to `cudaMalloc`, thus more latency | No. The cache is already limited to 200 MB (`CTranslate2/src/cuda/allocator.cc:39`). |
| Return to `whisper-small` | 832 MiB | The transcription quality that the last change bought | No. |
| Load the model only when it is necessary | 1602 MiB when idle | 1882 ms to load, measured | No. The system is real time. |
| `num_threads_per_replica: 16` on a GPU build | 0 MiB | — | It changes no VRAM. It starts 16 CPU threads that the GPU path does not use. Lower it to 4 when you touch this file. |

The answer to "how do we make this service use less VRAM" is thus: **the model already uses the
minimum, and the only true gain is to stop a long segment from making more than one window.**

## 6. Open questions

| Question | Verdict | What settles it |
|---|---|---|
| How many times each hour does the VAD make a false decision in the real room of the user? | UNKNOWN | Start the service with `RAW_AUDIO=1` for one hour in a quiet room. Count the messages on `io.user.speak.raw`. Each message is a false decision. |
| Is 8 frames (256 ms) the correct minimum for P1? | UNKNOWN | Record 30 short real answers ("oui", "non", "ok", "hmm"). Measure the number of speech frames of each one. Take the minimum, and remove 2 frames. |
| Does the pre-roll of P4 lower the word error rate? | Partly answered | On 20 synthetic segments with room tone, it corrects the start of 2 and damages 0 (P4). A true WER needs a corpus of 50 real segments with the transcription by hand. `bench_stt` measures the latency only. |
| What is the true VRAM budget of the three services together? | UNKNOWN | Start `lobe_frontal`, `io_voix` and `io_oreilles`, and read `nvidia-smi --query-compute-apps` while the three work. This audit measures `io_oreilles` alone. |
| Does the end-to-end latency change with P2 and P3? | UNKNOWN | `io.user.speak` is an ingress point of `services/benchmark/graphs/E2E.json`. Thus the benchmark does not see the STT. Add a step before this topic in the graph, or accept `bench_stt` as the only measurement of this service. |
