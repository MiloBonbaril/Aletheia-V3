# 👁️ I/O Yeux (Twitch and YouTube)

The sensor that observes the virtual world.

> **Status: specification only.** This directory contains this file. There is no source code.

## 🎯 Planned functions

- Connect to the chat API of Twitch or YouTube.
- Aggregate the messages and the events (subscriptions, bits, and more). Aggregation prevents an
  overflow of the context window when the chat is very active.
- Publish a summary event (`io.chat.msg`) to the orchestrator.

## 🔌 Planned NATS interface

- **Publishes on:** `io.chat.msg`
