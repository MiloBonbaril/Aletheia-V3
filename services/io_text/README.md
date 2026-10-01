# ⌨️ I/O Text

A command-line tool that sends multi-line text messages to the system. Use it to simulate the voice
input or a different sensor input.

## 🎯 Functions

- An interactive multi-line interface in the terminal.
- Commands that operate as in a text editor.
- It publishes the messages on `io.user.msg.text`. It puts the name of the sender in front of the
  text.

## ⚙️ Start

```bash
pip install -r requirements.txt
python main.py
```

The NATS address is in the source code. The service does not read `NATS_URL`.

The `Chat` tab of `services/terminal` does the same work from the browser. Keep `io_text` for a
session with no console. The manifest of the terminal lists `io_text`, but do not start it from
the console, because it is an interactive editor. Start it in your own terminal.

## 📖 How to use it

1. Start the service.
2. Type your message on one or more lines.
3. Type `:w` or `:send` on an empty line to send the message.
4. Type `:c` or `:clear` to erase the local buffer. The service sends nothing.
5. Type `:q` or `:quit` to stop the service.

## 🔌 NATS interface

- **Publishes on:** `io.user.msg.text`
