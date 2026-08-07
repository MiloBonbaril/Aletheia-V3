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
python main.py
MUTE_LOCAL_PLAYBACK=1 python main.py   # no local playback, NATS only
```

The service downloads the Kokoro model files (approximately 350 MB) into the `models/` directory at
the first start. The NATS address is in the source code. The service does not read `NATS_URL`.

### Environment variables

| Variable | Default | Function |
|---|---|---|
| `KOKORO_VOICE` | `ff_siwis` | The voice. The default is a French female voice. |
| `KOKORO_SPEED` | `1.0` | The speech speed. |
| `KOKORO_MODELS_DIR` | `models/` | The directory of the model files. |
| `MUTE_LOCAL_PLAYBACK` | `false` | Set it to `1` or `true` to stop the local playback. The service opens no `sd.OutputStream`. The NATS publication continues. |

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the audio conversion functions.

## 🔌 NATS interface

- **Subscribes to:** `lobe.fragment_stream`
- **Publishes on:** `io.voice.speak.start`, `io.voice.speak.audio`, `io.voice.speak.end`
