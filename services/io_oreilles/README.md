# 👂 I/O Oreilles (STT)

This service gives hearing to Nexus-V. It changes an audio stream into text in real time. It is
written in **Rust**.

## 🎯 Functions

- **Audio capture:** it listens continuously to the physical microphone.
- **Voice activity detection (VAD):** it uses **Silero VAD** to find the speech segments and to
  ignore the background noise.
- **Transcription (STT):** it changes the speech into text with **CTranslate2** and the **Whisper**
  model.
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
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir='model/whisper-small-ct2')"
# ct2rs needs preprocessor_config.json. The Systran repository does not contain it:
python -c "from huggingface_hub import hf_hub_download; import shutil; shutil.copy(hf_hub_download('openai/whisper-small','preprocessor_config.json'), 'model/whisper-small-ct2/preprocessor_config.json')"
```

### Environment variables

| Variable | Default | Function |
|---|---|---|
| `NATS_URL` | `nats://localhost:4222` | The address of the NATS broker. |
| `STT_LANGUAGE` | — | The transcription language, for example `fr`. |
| `STT_MODEL_PATH` | `model/whisper-small-ct2` | The path of the CTranslate2 model. |
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

### STT benchmark

```bash
cargo run --release --bin bench_stt -- --dir /path/to/wavs --device cpu
cargo run --release --features cuda --bin bench_stt -- --dir /path/to/wavs --device cuda
```

Measured on 28 real segments (61.9 s of audio), with `whisper-small`, on an RTX 5070 Ti and 32
cores:

| Device | Median | p90 | Max | Real-time factor |
|---|---|---|---|---|
| CPU (OpenBLAS, 16 threads) | 10038 ms | 12935 ms | 20123 ms | 4.64× |
| GPU (CUDA) | **97 ms** | 117 ms | 180 ms | **0.042×** |

Whisper fills each segment to a window of 30 s. Thus the length of the segment has almost no effect
on the cost. The CPU runs at 4.6 times the real time, which is not usable for a conversation. The
GPU gives the necessary margin for the end-to-end budget.

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
