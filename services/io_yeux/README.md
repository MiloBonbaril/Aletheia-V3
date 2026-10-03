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
- **Heartbeat:** when no publication occurred for `IO_YEUX_HEARTBEAT_SECONDS`, the service looks at
  the difference again. If it is below `IO_YEUX_IDENTICAL_THRESHOLD`, the screen is identical: the
  service publishes the last state again with a new `checked_at`, and it does not call the VLM. If
  it is above, the service observes with `trigger: heartbeat`. Thus a small change, for example one
  error line in a terminal, becomes an observation in 30 s maximum.
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

### The vision gives way to the conversation

`llama-server` runs with `--parallel 1`. An observation (approximately 1000 image tokens) that
occurs during a message makes the `lobe_frontal` wait, and it uses the GPU while `io_voix`
synthesizes the voice. Thus:

- `cortex.interaction.started` suspends the observations. It also cancels the observation in
  progress: the service closes the HTTP connection, and `llama-server` stops the task. A test on
  `llama-server` showed that this operates without `stream: true`.
- If `io_voix` is active (an `io.voice.speak.start` or `.end` in the last 10 minutes), the
  suspension stops at `io.voice.speak.end` with `is_last: true`. `io_voix` always publishes it,
  also after `stay_silent`.
- If `io_voix` is not active, the suspension stops at `lobe.fragment_stream` with `is_last: true`.
- In all cases, the suspension stops 30 s after its start.

During the suspension, the capture continues, but there is no observation and no heartbeat. At the
end, the next capture is compared with the reference: one observation only, with the most recent
capture, if the screen changed.

### Resilience

A failure of `io_yeux` never stops the conversation, and you do not have to start it again by hand.

- The service starts and captures without NATS and without `llama-server`. It connects to NATS in
  the background, and it tries again every 2 s. It makes no observation while NATS is absent,
  because nobody receives the state. After a NATS restart, it connects again automatically.
- An observation that takes more than 10 s stops. The service closes the connection, and
  `llama-server` stops the task.
- There is no explicit retry: the next capture is the new attempt, because the reference does not
  change after a failure. After each failure in sequence, the service waits 5 s, then 10, 20 and
  40 s, and 60 s maximum. The first successful observation sets the normal rate again. Thus a
  restart of `llama-server`, or a change from Gemma-4 to Qwen3.5, needs no action.
- Each failure gives one log line, with no stack trace. A NATS error that does not change gives
  one line only.

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
| `IO_YEUX_HEARTBEAT_SECONDS` | `30` | The time without publication after which the service looks at the screen again. |
| `IO_YEUX_IDENTICAL_THRESHOLD` | `0.00005` | Below this difference, the screen is identical to the reference. On a 2560×1440 screen, a blinking cursor gives approximately 0.00004, and one error line in a terminal gives approximately 0.00012. |
| `IO_YEUX_MAX_TOKENS` | `500` | The token limit of the answer. A limit that is too low cuts the JSON. |
| `IO_YEUX_LLAMA_URL` | `http://127.0.0.1:8080/v1/chat/completions` | The chat endpoint of `llama-server`. |

### Privacy

The risk: a key is visible in a terminal, the VLM copies it, Aletheia reads it aloud on the stream,
and the `hippocampe` keeps the answer. The instruction of the perception prompt is not sufficient: in
a test, Qwen3.5 copied an `sk-…` key exactly three times out of three.

Thus the service masks the secrets before each publication, in `application`, `activity` and each
item of `visible_text`. Each match becomes `[masqué]`:

- the known prefixes: `sk-`, `ghp_`, `github_pat_`, `AKIA`, `xoxb-`, `xoxp-`, and the JWT (`eyJ…`);
- the password of a connection URL (`postgres://admin:[masqué]@db`);
- a sequence of 25 characters or more without a space, if it has an alphanumeric block of 20
  characters or more with digits and letters (a hash, a base62 key), or if its entropy is 4.4 bits
  per character or more (a base64 key). The entropy rule does not apply to a sequence that starts
  with `/`, `~` or `.`: it is a path. A long build path with a hash can be masked completely.

The masking cannot find all the secrets: a short password outside a URL, or a key that the VLM
reads with errors, can pass. **Pause procedure:** before you open a password manager or a `.env` file, stop
`io_yeux` from the console of `services/terminal`. The `<vision>` block disappears from the prompt
90 s after the stop, at the latest. Start the service again when the screen shows no secret.

No capture goes to the disk. The logs show the application name only, never the activity or the
visible text.

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the pure functions of `core.py`: the reduction, the difference, the decision to
observe (threshold, heartbeat, republication, suspension), the reference, the masking of the
secrets, and the construction of the payload. They need no server and no screen.

## 🔌 NATS interface

- **Subscribes to:** `cortex.interaction.started`, `io.voice.speak.start`, `io.voice.speak.end`,
  `lobe.fragment_stream`
- **Publishes on:** `io.vision.state`
