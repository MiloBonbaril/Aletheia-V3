# 👤 I/O Visage (VTube Studio controller)

The actuator that controls the live avatar.

> **Status: specification only.** This directory contains this file. There is no source code.

## 🎯 Planned functions

- Read the audio output of the TTS and generate the lip-sync.
- Read the emotional data: the mood state on `limbic.mood.update`, or the emotion tags that the LLM
  puts in the text (for example `<joy>`).
- Send the commands and the triggers to VTube Studio through a WebSocket. The expressions must stay
  synchronized with the audio.

## 🔌 Planned NATS interface

- **Subscribes to:** `lobe.fragment_stream`, `io.voice.speak.audio`, `limbic.mood.update`
- **Publishes on:** `io.face.emotion`
