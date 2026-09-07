# 🔊 I/O Voix (TTS)

This service gives a voice to the VTuber. It is written in **Python**.

## 🎯 Functions

- It subscribes to the text fragments of the lobe frontal (`lobe.fragment_stream`).
- It synthesizes a continuous audio stream with **Audio8 TTS Preview 0.6B** (Apache-2.0, 44.1 kHz).
- It sends the audio to the default output device, or to a virtual audio cable such as VB-Cable. It
  plays the fragments one after the other with no interruption.
- It publishes the audio of each fragment on `io.voice.speak.audio`, and the time references on
  `io.voice.speak.start` and `io.voice.speak.end`. Thus a different service, for example the Discord
  bridge, can play the audio without the local loudspeaker.

## 🧠 Why the service streams each fragment in chunks

Audio8 is autoregressive. It makes the speech frame by frame, at 21.5 frames per second. Kokoro, the
previous engine, made the full fragment in one pass. The cost is now proportional to the length of
the speech: a fragment of 4 seconds needs approximately 1.4 seconds of synthesis. If the service
waits for the full fragment, the time to first audio goes above 2 seconds.

`engine.py` thus gives the audio in chunks. The first chunk is short, because it sets the time to
first audio. The next chunks become longer, because each call has a fixed cost. Each chunk gives
2.2 times more speech than the time it needs, thus the playback buffer always grows.

The decoder of the codec is fully causal. The service decodes the frames from frame 0 each time, and
sends only the new tail. The part that is already played does not change, thus the joint between two
chunks is clean. The service needs no cross-fade.

Measured on an RTX 5070 Ti, with INT8 weights, for one fragment of 4.5 seconds:

| Chunk | Arrives at | Gives | Buffer margin |
|---|---|---|---|
| 0 | **133 ms** | 372 ms | — |
| 1 | 319 ms | 557 ms | +185 ms |
| 2 | 651 ms | 1115 ms | +411 ms |
| 3 | 1299 ms | 2229 ms | +877 ms |

End to end, through NATS, the first `io.voice.speak.start` comes **120 ms** after the fragment. The
real-time factor is **0.32**.

## ⚙️ Configuration and start

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu130   # CUDA wheel first
pip install -r requirements.txt
python main.py
MUTE_LOCAL_PLAYBACK=1 python main.py   # no local playback, NATS only
```

The service downloads the model (approximately 1.3 GB) into `models/audio8-tts-0.6b/` at the first
start. The revision is pinned in `engine.py`, because the model runs with `trust_remote_code=True`.
The NATS address is in the source code. The service does not read `NATS_URL`.

The first start compiles the CUDA graphs. This needs approximately 50 seconds. The next starts are
faster, because the Inductor cache keeps the result. The warmup does this work before the first
fragment.

### Voice

Audio8 has no voice presets. It clones the voice of a reference recording. Give a clean recording of
5 to 10 seconds and its exact transcript:

```bash
A8_VOICE_WAV=voice/aletheia.wav A8_VOICE_TEXT="The exact words of the recording." python main.py
```

The service encodes the reference one time at the start. Without a reference, the model uses its own
default voice.

### GPU (CUDA)

The GPU is necessary. The model is autoregressive, thus the real-time factor on the CPU goes far
above 1 and the service holds no latency target. For a CPU machine, use the ONNX INT4 package of
Audio8 instead of this service.

Three optimizations are necessary, not decorative. Without them the real-time factor is 2.2:

1. **CUDA graphs** on the two hot loops. The model is limited by kernel launches, not by
   computation. This changes 93.6 ms per frame to 18.5 ms per frame.
2. **A fixed-width attention mask.** `generate()` makes the mask longer by one token for each frame.
   Without a pad before the call, Dynamo compiles again for each frame and the start never ends.
3. **KV caches that are allocated one time.** `generate()` allocates them again at each call. The
   addresses move and the captured CUDA graphs become invalid.

### INT8

`A8_QUANT=int8` is the default, as requested. Measured cost and gain on this hardware:

| Weights | ms per frame | Peak VRAM |
|---|---|---|
| bfloat16 | 18.5 | 2.02 GiB |
| INT8 | 20.3 | **1.64 GiB** |

INT8 saves 380 MiB and costs 9 % of latency. The model is too small (896 wide) to be limited by
memory bandwidth, thus the dequantization adds work and unblocks nothing. Set `A8_QUANT=bf16` to
compare on your own machine.

Do not use INT8 without the CUDA graphs. In eager mode it costs 30 % of latency (122 ms per frame
against 94 ms).

### Environment variables

| Variable | Default | Function |
|---|---|---|
| `A8_QUANT` | `int8` | `int8` for torchao weight-only INT8. Any other value keeps bfloat16. |
| `A8_VOICE_WAV` | *(empty)* | The path of the reference recording. Empty gives the default voice. |
| `A8_VOICE_TEXT` | *(empty)* | The exact transcript of the reference recording. Necessary with `A8_VOICE_WAV`. |
| `A8_MODEL_DIR` | `models/audio8-tts-0.6b` | The directory of the model files. |
| `A8_CHUNK_SCHEDULE` | `8,12,24,48,96` | The size in frames of each chunk. The service keeps the last value for the following chunks. One frame is 46.4 ms of speech. |
| `A8_MAX_FRAMES` | `512` | The maximum number of frames for one fragment (23.8 s). |
| `A8_TEMPERATURE` | `0.7` | The sampling temperature. |
| `A8_TOP_P` | `0.9` | The nucleus sampling threshold. |
| `A8_TOP_K` | `50` | The top-k sampling threshold. |
| `MUTE_LOCAL_PLAYBACK` | `false` | Set it to `1` or `true` to stop the local playback. The service opens no `sd.OutputStream`. The NATS publication continues. |

## ⚠️ Limits

- **One fragment at a time.** The engine keeps persistent KV caches and captured CUDA graphs. Two
  fragments in parallel damage both. `main.py` uses one inference thread. Keep it.
- **The payload of `io.voice.speak.audio` is larger.** 44.1 kHz gives approximately 86 KiB of base64
  for each second of speech. The NATS limit of 1 MB comes at approximately 11 seconds of speech in
  one fragment. The lobe frontal cuts at punctuation marks, thus the fragments stay far below.
- **The RAS window restarts at each chunk.** The anti-repetition guard of the model forgets the last
  10 frames at each boundary. There are 4 or 5 boundaries for each fragment. To remove this, you must
  write the generation loop again.

## 🧪 Tests

```bash
pytest tests/            # pure functions, no GPU
python engine.py         # self-check on the GPU: latency, buffer margin, continuity
```

`python engine.py` synthesizes one sentence. It fails if a chunk comes too late for the playback, or
if the signal has a discontinuity at a joint.

## 🔌 NATS interface

- **Subscribes to:** `lobe.fragment_stream`
- **Publishes on:** `io.voice.speak.start`, `io.voice.speak.audio`, `io.voice.speak.end`

The contract does not change. The service sends one WAV for each fragment, not one for each chunk.
`io_discord` and `io_visage` need no modification: `FFmpegPCMAudio` finds the rate in the WAV header.
