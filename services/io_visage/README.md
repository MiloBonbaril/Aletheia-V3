# 👤 I/O Visage (2D avatar)

The actuator that shows the face and the body of Aletheia: a vector 2D avatar, animated in a
browser, with no Live2D and no VTube Studio.

> **The code is not in this repository.** It is in the sibling repository `../2D_Aletheia`. This
> directory holds this file only. See `docs/adr/0007-io-visage-vit-dans-2d-aletheia.md`.

## 🎯 Functions

- It shows the **mood** of Aletheia. `limbic` owns the mood. When the label is unknown, the avatar
  keeps the current mood.
- It moves the mouth with the voice. It finds the vowels in the audio of each fragment, and it
  follows `io.voice.speak.start` and `.end`. The player of the sound publishes these two events, thus
  the mouth follows the sound that the audience hears, on the local sound card and on Discord.
- It adds **punctuation**: short gestures that the avatar places in a sentence from the text of the
  sentence. For example, it tilts the head on a question and it laughs on "haha". The LLM does not
  choose them.
- It takes a **listening posture** while a speaker has the floor: the eyes go to the camera, and the
  ears go up.
- It raises the ears when an interaction starts.

## 🔌 NATS interface

- **Subscribes to:** `limbic.mood.update`, `io.voice.speak.start`, `io.voice.speak.audio`,
  `io.voice.speak.end`, `cortex.interaction.started`, `io.user.voice.activity`
- **Publishes on:** nothing

## 🚀 Start

The daemon starts it: the service `io_visage` in `services/terminal/services.toml`, in the profiles
`complet` and `discord`. The two repositories must be side by side. By hand:

```bash
cd ../2D_Aletheia && ../Aletheia-V3/venv/bin/python server/avatar_server.py --nats nats://localhost:4222
```

The server does not animate the avatar. The browser does. **Add a browser source in OBS** on
`http://127.0.0.1:7430/obs?view=bust`. With no browser source, nothing moves. The browser source
connects again by itself when the service starts again.

- Preview and control panel: `http://127.0.0.1:7430/`
- State: `GET http://127.0.0.1:7430/api/health` gives the number of renderers and the state of NATS.
- Manual control (HTTP and WebSocket): `2D_Aletheia/docs/api.md`.

## ⚠️ Limits

- After a restart of `io_visage`, the mood stays neutral until the next `limbic.mood.update`.
- The end-to-end latency is not measured yet on the real bus.
