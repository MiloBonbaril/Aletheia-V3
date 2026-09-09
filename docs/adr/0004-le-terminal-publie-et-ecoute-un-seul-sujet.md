# The terminal speaks to the bus for the chat tab, on two topics and no more

ADR 0003 says that the daemon never subscribes to the NATS bus. The `Chat` tab breaks that rule.
This document says what changes, and what does not.

**The daemon publishes on `io.user.msg.text` and subscribes to `lobe.fragment_stream`.** It does the
same work as `io_text`, from the browser. The tab that sends but does not show the answer is not a
chat: it is a send button, and `io_text` itself subscribes to read the answer.

**The reason of ADR 0003 stays true, and it is why the list has two topics.** That decision refused
a `>` subscription, because `>` receives `io.discord.voice.frame`, which is a continuous flow of PCM
audio, and `io.voice.speak.audio`, which is 44.1 kHz audio for each fragment. The daemon would pull
that flow through Python to increment counters. `lobe.fragment_stream` is text, and it is the text
that the tab must show. The volume argument does not apply to it. The rule that remains, and that
must remain, is this one: **the daemon subscribes to a named topic, never to a pattern, and never to
an audio topic.** The bus figures still come from `/varz` and `/connz`, not from a subscription.

**What changes in exchange:** conversation content now goes through the daemon. It stays in memory,
in a buffer of 200 messages, and it disappears with the daemon. Nothing goes to the disk. This
repository is public, thus this limit is not a detail: the log buffers already work in the same way,
for the same reason.

**Considered options**: a tab that only publishes — rejected, it gives no answer and it is not a
chat. A subscription that goes through `io_text` — rejected, `io_text` is an interactive terminal
program with no API. A WebSocket between the console and the daemon — not necessary: the flow is
downward, and the Server-Sent Events stream that already exists carries the fragments.

**Consequences**: `services/terminal` now depends on `nats-py`. The daemon appears in `/connz` under
the name `terminal`, with one subscription; that number is the check of this decision. `io_text`
stays in the manifest and in the repository: it works when the console does not run. A future tab
that needs another topic must extend this document, and each addition must answer the same question:
is this payload text, or is it audio?
