# 👁️ I/O Yeux (screen vision)

This service lets Aletheia see the screen of the streamer. It is written in **Python**. It observes
only: it never speaks, it never starts the TTS, and it does not control the computer.

## 🎯 Functions

- **Capture:** it captures the active screen once each second through the KWin D-Bus interface
  `org.kde.KWin.ScreenShot2`. The image goes through a pipe into memory. No capture goes to the
  disk. See `docs/adr/0006-io-yeux-capture-par-kwin-screenshot2.md`.
- **Change detection:** it reduces each capture to 160×90 in grayscale. It compares this copy with
  the **reference**: the capture of the last successful observation, not the previous capture.
  When the mean absolute difference is above `IO_YEUX_CHANGE_THRESHOLD`, it observes.
- **Observation:** it sends the screen (JPEG, 1280×720 maximum, quality 85) to the `llama-server`
  on port 8080, with `temperature: 0`, no reasoning, and a fixed `json_schema`. The model of the
  server has no importance: Gemma-4 and Qwen3.5 are both multimodal. The prompt is factual, in
  French, with no persona. It tells the model to never copy a password, a key or a token.
- **Publication:** it publishes the description on `io.vision.state`. The `lobe_frontal` injects
  the last state in a `<vision>` block at the start of the last user message. No image goes into
  the conversation.

There is one observation at a time. The loop waits for the answer, then it continues with the most
recent capture. After a failed observation, the reference does not change, thus the next capture
tries again.

## 🔐 Authorization (one time, by hand)

KWin refuses the capture from an executable that no `.desktop` file declares. The file
`io_yeux.desktop` declares `/usr/bin/python3.14`, which is the target of `venv/bin/python`.

**Warning:** this file authorizes every Python 3.14 script of the session to capture the screen,
not only `io_yeux`. Read the ADR 0006 before you install it.

```bash
cp services/io_yeux/io_yeux.desktop ~/.local/share/applications/
```

To remove the authorization, delete `~/.local/share/applications/io_yeux.desktop`.

If the system Python changes version, change the `Exec` line of the file. Without this change, the
capture fails with `org.kde.KWin.ScreenShot2.Error.NoAuthorized`. To find the correct path:

```bash
readlink -f venv/bin/python
```

## ⚙️ Configuration and start

The service is in the manifest of `services/terminal`, in no profile. Start it by hand from the
console. To stop the capture, stop the service.

```bash
pip install -r requirements.txt
python main.py
```

### Environment variables (`.env`)

| Variable | Default | Function |
|---|---|---|
| `NATS_URL` | `nats://localhost:4222` | The address of the NATS broker. |
| `IO_YEUX_SCREEN` | `active` | `active` follows the screen that has the focus. A name (`DP-1`, `HDMI-A-1`) fixes one screen. |
| `IO_YEUX_CAPTURE_FPS` | `1` | The number of captures each second. |
| `IO_YEUX_CHANGE_THRESHOLD` | `0.02` | The mean absolute difference (0 to 1) above which the service observes. |
| `IO_YEUX_MAX_TOKENS` | `500` | The token limit of the answer. A limit that is too low cuts the JSON. |
| `IO_YEUX_LLAMA_URL` | `http://127.0.0.1:8080/v1/chat/completions` | The chat endpoint of `llama-server`. |

### Privacy

The service does not mask the secrets yet. Stop it before you open a password manager or a `.env`
file. The `<vision>` block disappears from the prompt 90 s after the stop. The logs show the
application name only, never the activity or the visible text.

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the pure functions of `core.py`: the reduction, the difference, the decision to
observe, the reference, and the construction of the payload. They need no server and no screen.

## 🔌 NATS interface

- **Publishes on:** `io.vision.state`
