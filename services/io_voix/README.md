# 🔊 I/O Voix (TTS)

This service gives a voice to the VTuber. It is written in **Python**.

## 🎯 Functions

- It subscribes to the text fragments of the lobe frontal (`lobe.fragment_stream`).
- It synthesizes a continuous audio stream with `Kokoro ONNX`.
- It sends the audio to the default output device, or to a virtual audio cable such as VB-Cable. It
  plays the fragments one after the other with no interruption.
- It publishes the audio of each fragment on `io.voice.speak.audio`, and the time references on
  `io.voice.speak.start` and `io.voice.speak.end`. Thus a different service, for example the Discord
  bridge, can play the audio without the local loudspeaker.

## ⚙️ Configuration and start

```bash
pip install -r requirements.txt
pip uninstall -y onnxruntime && pip install onnxruntime-gpu   # GPU synthesis
python main.py
MUTE_LOCAL_PLAYBACK=1 python main.py   # no local playback, NATS only
```

The service downloads the Kokoro model files (approximately 350 MB) into the `models/` directory at
the first start. The NATS address is in the source code. The service does not read `NATS_URL`.

### GPU (CUDA)

The service uses `CUDAExecutionProvider` when it is available. It goes back to the CPU when the
session fails to start. The message at the start tells you the device: `🟢 Kokoro sur GPU` or
`🔵 Kokoro sur CPU`.

The `kokoro-onnx` package needs `onnxruntime`, which is the CPU package. You must replace it with
`onnxruntime-gpu` after the installation of `requirements.txt`. The two packages give the same
`onnxruntime` module, thus you must remove the first one.

The CUDA provider needs cuDNN 9. `main.py` calls `onnxruntime.preload_dlls()`, which finds the CUDA
and cuDNN libraries in the `nvidia` pip packages. These packages come with PyTorch. Thus you do not
need to set `LD_LIBRARY_PATH`.

Synthesis of one fragment, on an RTX 5070 Ti and a Ryzen 9 5950X:

| Audio length | CPU (6 threads) | GPU (CUDA) |
|---|---|---|
| 0.68 s | 227 ms | **35 ms** |
| 1.90 s | 444 ms | **51 ms** |
| 4.71 s | 969 ms | **88 ms** |

The synthesis of the first fragment is the largest part of the time to first audio. Thus the GPU
gives the necessary margin for the end-to-end budget.

### Environment variables

| Variable | Default | Function |
|---|---|---|
| `KOKORO_VOICE` | `ff_siwis` | The voice. The default is a French female voice. |
| `KOKORO_SPEED` | `1.0` | The speech speed. |
| `KOKORO_MODELS_DIR` | `models/` | The directory of the model files. |
| `KOKORO_CPU_THREADS` | `6` | The number of intra-op threads of the CPU session. It has no effect on the GPU. |
| `MUTE_LOCAL_PLAYBACK` | `false` | Set it to `1` or `true` to stop the local playback. The service opens no `sd.OutputStream`. The NATS publication continues. |

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the audio conversion functions.

## 🔌 NATS interface

- **Subscribes to:** `lobe.fragment_stream`
- **Publishes on:** `io.voice.speak.start`, `io.voice.speak.audio`, `io.voice.speak.end`
