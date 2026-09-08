# The terminal is a local process supervisor, not an observer of the bus

`services/terminal` had a specification only: "a web panel with a WebSocket connection to the event
bus". The first need in practice was different. To work on Aletheia, you start eleven things by
hand, in eleven terminals, in the correct order, and you read eleven separate outputs. The V0.1
answers that need: it starts, stops, monitors and reads the logs of every service. Three decisions
came out of it, and each one goes against the first specification.

**The daemon owns the processes, and it kills them when it stops.** `systemd --user` gives the
start, the stop, the uptime, the restart count and the persistent logs for free, and the services
survive a crash of the supervisor. We did not take it. The daemon starts each service in its own
process group and kills every group when it exits. The reason is the GPU: `llama-server`,
`io_voix` and `io_oreilles` share 16 GB of VRAM, and a process that no one owns any more keeps its
part of it until you find it and kill it by hand. A session that ends must give the GPU back. The
kernel enforces the promise: each child asks for `PR_SET_PDEATHSIG`, thus even a `SIGKILL` on the
daemon leaves nothing behind. The consequence is accepted: a crash of the daemon takes the whole
session with it, and the `docker` stacks are the exception, because they are detached by nature and
the daemon finds them again.

**Considered options**: `systemd --user` with generated units — rejected, because it needs unit
generation and reload for command lines that change often during development, and because it keeps
orphans alive. `supervisord` — rejected, it adds a dependency and its log capture goes through
files.

**The bus figures come from the monitoring endpoint of NATS, not from a subscription.** In a system
where the rule is "everything goes through NATS", a dashboard that does not subscribe to the bus is
a surprise. The reason is the payloads. A `>` subscription receives `io.discord.voice.frame`, which
is a continuous flow of PCM audio, and `io.voice.speak.audio`, which is 44.1 kHz audio for each
fragment. The daemon would pull that whole flow through Python only to increment counters. It would
also see every transcript and every answer. `docker-compose.yml` already starts NATS with `-m 8222`,
thus `/varz` gives the message rate and `/connz` gives one line for each connected client. No
payload passes. The cost is that there is no rate for each topic, and that the nine services now
give a name to their NATS connection so that `/connz` is readable.

**A service that crashes stays dead.** There is no automatic restart, with or without backoff. This
is a development tool: a crash is the information that you want to see, not to hide. An immediate
restart erases the state that produced the crash, and a restart loop on the GPU gives a cascade of
out-of-memory errors instead of a diagnosis.

**Consequences**: the README of `terminal` promised a WebSocket to the bus. It is now correct. The
console uses Server-Sent Events downward and POST for the commands, because the flow is almost
entirely downward. A later tab that needs a real two-way channel, for example a shell, will need a
WebSocket beside the SSE stream. The daemon listens on `127.0.0.1` and has no authentication,
because it runs arbitrary commands from `services.toml`; a remote access goes through an SSH
tunnel.
