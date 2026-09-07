# 👂 I/O Oreilles (STT)

This service gives hearing to Nexus-V. It changes an audio stream into text in real time. It is
written in **Rust**.

## 🎯 Functions

- **Audio capture:** it listens continuously to the physical microphone.
- **Voice activity detection (VAD):** it uses **Silero VAD** to find the speech segments and to
  ignore the background noise. It refuses a segment that holds less than 256 ms of speech (see
  below).
- **Transcription (STT):** it changes the speech into text with **CTranslate2** and the **Whisper**
  model.
- **Long turns:** it cuts the audio at 25 s and transcribes each part immediately, but it publishes
  one text only, at the end of the turn (see below).
- **Event output:** it publishes the text on `io.user.speak` at the end of each complete sentence.
- **Discord mode:** with the `--discord` flag, it processes the audio of a Discord voice channel. It
  runs an independent VAD pipeline for each speaker. Thus a person who stops to speak does not cut
  the sentence of a different person. The events carry the name of the speaker.

## ⚙️ Configuration and start

### Prerequisites

- `libonnxruntime.so`, for Silero VAD.
- A Whisper model in the CTranslate2 format.

### Whisper model

The `model/` directory is not in git. Get the model one time:

```bash
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('deepdml/faster-whisper-large-v3-turbo-ct2', local_dir='model/whisper-large-turbo-ct2')"
```

This repository contains `preprocessor_config.json`. Do not replace it with the file of a different
model size. `large-v3-turbo` uses **128 mel bands**, and `small` uses 80. ct2rs reads the number of
bands from this file. A file that does not agree with the weights gives a shape error or a bad
transcription.

To use `whisper-small` again, get it with the commands below and set `STT_MODEL_PATH`:

```bash
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir='model/whisper-small-ct2')"
# ct2rs needs preprocessor_config.json. The Systran repository does not contain it:
python -c "from huggingface_hub import hf_hub_download; import shutil; shutil.copy(hf_hub_download('openai/whisper-small','preprocessor_config.json'), 'model/whisper-small-ct2/preprocessor_config.json')"
```

### Environment variables

| Variable | Default | Function |
|---|---|---|
| `NATS_URL` | `nats://localhost:4222` | The address of the NATS broker. |
| `STT_LANGUAGE` | `fr` | The transcription language. Set `auto` for the language detection, or a code such as `en`. |
| `STT_MODEL_PATH` | `model/whisper-large-turbo-ct2` | The path of the CTranslate2 model. |
| `RAW_AUDIO` | not set | Set it to `1` or `true` to publish the raw audio on `io.user.speak.raw` and to skip the transcription. |
| `ORT_DYLIB_PATH` | found automatically | The path of `libonnxruntime.so`. |

The `--discord` flag does not select the raw mode. Without `RAW_AUDIO`, the Discord mode
transcribes.

### Start

```bash
cargo run --release                 # local microphone
cargo run --release -- --discord    # no local microphone; per-speaker PCM from io_discord
```

The `--discord` flag stops the local microphone capture for that run. The service subscribes to
`io.discord.voice.frame`.

### GPU (CUDA)

Whisper on a CPU is too slow for real-time speech (see the numbers below). Thus the GPU is not an
option here. It is the condition to use the voice mode.

```bash
CUDAHOSTCXX=g++-15 CUDA_ARCH_LIST="7.5;12.0" cargo build --release --features cuda
```

The command builds CTranslate2 with CUDA. It takes approximately 3 minutes. Two problems can occur:

- `CUDAHOSTCXX`: nvcc refuses a compiler that is too recent (gcc 16 here). Give the path of an
  older gcc.
- `CUDA_ARCH_LIST`: the list **must contain a minimum of one architecture below 10.0**. ct2rs sends
  the architectures 10.0 and higher to raw nvcc flags, and keeps the CMake list at `Common`. That
  list contains `compute_53`, which CUDA 13 does not know (`nvcc fatal: Unsupported gpu
  architecture`). The value `"7.5;12.0"` operates correctly: `7.5` holds the list, and `12.0`
  (Blackwell, RTX 50xx) goes to the gencode flags.

Without the `cuda` feature, the binary stays on the CPU with OpenBLAS. The build selects the device
(`stt_device()`). You cannot change it during operation.

### Minimum speech duration

A segment that holds less than **256 ms of speech** does not go to Whisper. The service writes
`Speech discarded` in the log and continues.

Whisper always gives a text. For a sound that is too short, this text is an invention: `Merci.`,
`Salut !` or `Merci d'avoir regardé cette vidéo !`. The service published these texts on
`io.user.speak`, because the only filter was an empty text. The entity then answered to a cough.

The limit is 8 VAD frames of 512 samples. The service counts only the frames above the speech
threshold, not the length of the segment. Thus a pause in the middle of a word does not change the
decision. The shortest real word of the bench corpus ("Bon.") holds 13 frames, thus it stays.

To change the limit, change `MIN_SPEECH_FRAMES` in `src/segmenter.rs`. Read the `Speech discarded`
lines of the log first: they give the frame count of each rejected segment.

See `docs/audits/io_oreilles-2026-09-07.md`, finding F1.

### Long turns of speech

Whisper reads windows of 30 s. A segment of 95 s becomes 4 windows in one batch: 604 ms and
2306 MiB, and the model loses words at each joint of the windows.

The service thus cuts the audio at **25 s** and gives each part to Whisper immediately. It does not
publish these parts. It keeps the text, and it publishes one message on `io.user.speak` when the
person stops to speak. The parts are joined with a space.

Two reasons for this:

- The entity must not answer half a sentence. To publish a part of 25 s makes Aletheia speak while
  the person continues, and she does not have the full statement.
- The transcription of the first parts happens during the speech. Thus only the last part is between
  the end of the speech and the message.

Measured on a monologue of 95 s, with `STT_LANGUAGE=fr`:

| | One segment (before) | Parts of 25 s (after) |
|---|---|---|
| Windows of 30 s in one batch | 4 | 1 |
| Time after the person stops | 658 ms | **256 ms** |
| VRAM peak | 2306 MiB | **1602 MiB** |
| Text | 2096 characters | 2151 characters |

The cut goes to the most recent pause of the buffer, if this pause is above 15 s. Thus the part ends
on a silence and no word is cut. If the person made no pause, the cut is flat and the service keeps
500 ms for the next part, thus the cut word is complete in that part. The joint can then repeat a
fraction of a word.

The limits are `MAX_CHUNK_SAMPLES`, `MIN_CHUNK_SAMPLES` and `HARD_CUT_OVERLAP_SAMPLES` in
`src/segmenter.rs`.

See `docs/audits/io_oreilles-2026-09-07.md`, finding F2.

### Compute type

The service asks for `INT8_FLOAT16` (`src/main.rs`). CTranslate2 quantizes the weights to 8-bit
integers when it loads the model, and it computes in 16-bit floating point. The model on disk stays
in float16.

On an RTX 5070 Ti, `AUTO` selects `int8_float16` too. Thus this setting changes nothing on this
card. It makes the VRAM budget the same on each card, because `AUTO` can select a different type on
different hardware. The difference is large: see the table below.

### STT benchmark

```bash
cargo run --release --bin bench_stt -- --dir /path/to/wavs --device cpu
cargo run --release --features cuda --bin bench_stt -- --dir /path/to/wavs --device cuda
```

| Option | Default | Function |
|---|---|---|
| `--dir` | — | The directory of the 16 kHz mono WAV files. |
| `--device` | `cpu` | `cpu` or `cuda`. |
| `--model` | `model/whisper-large-turbo-ct2` | The path of the model. |
| `--compute` | `int8_float16` | `auto`, `float16`, `int8` or `int8_float16`. |
| `--repeats` | 3 | The number of measurements for each file. |
| `--threads` | 16 | The CPU threads for each replica. |

Measured on 20 French segments (62.7 s of audio), on an RTX 5070 Ti, with `STT_LANGUAGE=fr`, and
3 measurements for each file. The VRAM column is the peak of the process alone, from `nvidia-smi`.
The four rows come from one campaign, one after the other:

| Model | Compute type | Language | Median | p90 | Max | Real-time factor | VRAM |
|---|---|---|---|---|---|---|---|
| `large-v3-turbo` | int8_float16 | `fr` (the default) | **102 ms** | 109 ms | 113 ms | 0.032× | 1602 MiB |
| `large-v3-turbo` | int8_float16 | `auto` | 181 ms | 191 ms | 194 ms | 0.058× | 1602 MiB |
| `large-v3-turbo` | float16 | `fr` | 111 ms | 119 ms | 141 ms | 0.036× | 2658 MiB |
| `whisper-small` | int8_float16 | `fr` | 68 ms | 83 ms | 87 ms | 0.021× | 770 MiB |

The language detection is one more pass of the encoder for each segment. It costs **79 ms**, which
is 44 % of the transcription time. This is the reason for the default value `fr`.

`large-v3-turbo` costs 34 ms and 832 MiB more than `small`. It has the encoder of `large-v3` (32
layers) but only 4 decoder layers, thus it stays far from the cost of the full `large-v3`. The int8
weights save 1056 MiB and 9 ms against float16.

The beam size is not a control here. A measurement at `beam_size = 1` gives 0 MiB and 3 ms, because
the turbo model has only 4 decoder layers and the answers are short. Keep the default of 5.

Compare only inside one table. The absolute values move approximately 10 % between two sessions,
with the clock state of the GPU. Inside one campaign, three measurements of the same configuration
stay in 1 % (101.5, 101.5 and 100.9 ms).

An older measurement on 28 real segments (61.9 s), with `whisper-small`, gives 10038 ms as the
median on the CPU (OpenBLAS, 16 threads), which is 4.64 times the real time. The CPU is not usable
for a conversation.

Whisper fills each segment to a window of 30 s. Thus the length of the segment has almost no effect
on the cost.

These times are not in the 300 ms end-to-end budget. `io.user.speak` is an ingress point of the
benchmark graph, thus the budget starts after the transcription. The VAD adds 600 ms of silence
before the transcription starts (`SILENCE_FRAMES_TO_END` in `src/segmenter.rs`).

## 📁 Source files

| File | Function |
|---|---|
| `src/main.rs` | Audio capture, mode selection, transcription and publication. |
| `src/segmenter.rs` | The VAD segmentation, with a key for each audio source. |
| `src/discord_audio.rs` | The Discord frame decoding and one pipeline for each speaker. |
| `src/bin/bench_stt.rs` | The STT benchmark. |

## 🧪 Tests

```bash
cargo test
```

The tests cover the mode selection and the construction of the transcription event.

## 🔌 NATS interface

- **Publishes on:** `io.user.speak` (transcript, with `speaker` in the Discord mode),
  `io.user.speak.raw` (only with `RAW_AUDIO`)
- **Subscribes to:** `io.discord.voice.frame` (only with `--discord`)
