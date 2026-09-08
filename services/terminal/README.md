# 🎛️ Terminal (administration frontend)

The local control panel of the project.

> **Status: V0.1.** The terminal starts, stops, monitors and reads the logs of every service of the
> project. It does not observe the content of the NATS bus.

## 🎯 What it does

The service has two parts:

- The **daemon** (`main.py`) owns the processes. It starts each service in its own process group,
  it reads the output, and it samples the resources. It serves an HTTP API on `127.0.0.1:7420`.
- The **console** (`web/`) is the web interface that the daemon serves. It shows one tab,
  `Services`.

Read `docs/adr/0003-terminal-superviseur-de-processus-local.md` for the reasons of the design.

## 🚀 Start

```bash
cd services/terminal
../../venv/bin/python main.py
```

Then open <http://127.0.0.1:7420>.

The daemon listens on the loopback interface only, and it has no authentication. It runs the
commands of `services.toml`, thus it must never listen on another interface. Use an SSH tunnel for
a remote access.

## ⚠️ The daemon owns the services

The daemon kills every service when it stops. The kernel enforces it: each child asks for
`PR_SET_PDEATHSIG`, thus a `SIGKILL` on the daemon kills the child too. This is a decision, not a
defect:

- It leaves no orphan process, and it frees the VRAM. `llama-server`, `io_voix` and `io_oreilles`
  each hold a part of the 16 GB of the GPU.
- A service that crashes stays dead. The console shows its exit code and its last log lines. There
  is no automatic restart, because an automatic restart hides the cause of a crash, and because a
  restart loop on the GPU gives a cascade of out-of-memory errors.

## 📋 The manifest

`services.toml` is the only source of truth. The order of the `[[service]]` blocks is the start
order.

| Field        | Function                                                                     |
| ------------ | ---------------------------------------------------------------------------- |
| `name`       | The identity of the service. It must be unique.                              |
| `group`      | The group in the sidebar of the console.                                     |
| `kind`       | `process` (a child of the daemon) or `docker` (a compose stack).             |
| `cwd`        | The working directory, relative to the root of the repository.               |
| `cmd`        | The command line, as a list of arguments. `{python}` gives the venv of root. |
| `log_file`   | A log file to read in place of the output of the process.                    |
| `rebuild`    | A command that rebuilds the service.                                         |
| `stale_src`  | A source directory. The console shows "périmé" when a file is newer.         |
| `ready_tcp`  | A `host:port`. The start sequence waits until the port answers.              |
| `env`        | A `[service.env]` table of variables for the process.                        |
| `depends_on` | Documentation only. The daemon does not apply it.                            |
| `note`       | A remark that the console shows on the card.                                 |

A `[profile.<name>]` block gives a named subset of services. The console starts a profile in the
order of the file.

`io_visage`, `io_yeux` and `terminal` are not in the manifest, because they have no source code.

## 🔍 What the console shows

- **Host**: CPU, RAM, GPU, VRAM and temperature, each 2 s (`psutil` and `nvidia-smi`).
- **Each service**: state, uptime, PID, and the CPU, the RAM and the VRAM of its process tree. The
  daemon sums the tree, because a service can start its own children.
- **The bus**: the daemon reads the monitoring endpoint of NATS (`/varz` and `/connz`) on port 8222.
  It gives the message rate and the list of the connected clients. **The daemon does not subscribe
  to the bus.** No payload goes through it: no audio frame, and no conversation content. Each
  service gives a name to its NATS connection, thus `/connz` shows which service is connected.
- **The logs**: a buffer of 2000 lines for each service, in memory. The buffer stays across a
  restart of the service. It disappears with the daemon. Nothing goes to the disk.

## 🖥️ The services that are terminal applications

The daemon gives a pseudo-terminal to each child, because two services need one:

- `lobe_frontal` runs a Textual TUI. Its output is a sequence of screen repaints, not log lines.
  The manifest gives `log_file = "lobe_frontal.log"`, thus the console reads the file that ADR 0001
  already writes. The daemon still reads the pseudo-terminal and discards it, or the service blocks
  when the terminal buffer becomes full.
- `io_text` is an interactive editor. Start it in your own terminal. The console shows its state
  only.

## 🔀 The pairs that share a device

Four entries come in two pairs. Each pair is the same program with a different environment, and the
two members of a pair must never run together. A test holds that rule for every profile.

- `io_oreilles` / `io_oreilles_discord`: the local microphone, or the per-speaker audio of a
  Discord voice channel (`--discord`).
- `io_voix` / `io_voix_muet`: the local speaker, or `MUTE_LOCAL_PLAYBACK=1`, which publishes the
  audio on NATS only. Use the muted one with `io_discord`, because the bot plays the audio itself.

Both `io_voix` entries carry `A8_VOICE_WAV` and `A8_VOICE_TEXT` in their `[service.env]`. Without a
reference voice, Audio8 invents a new voice for each fragment. The recording lives in
`services/io_voix/voices/`, and `A8_VOICE_TEXT` must be its exact transcript.

## 🦀 The Rust services

The manifest points at `target/release/<binary>`, not at `cargo run`. Thus the start is immediate,
the uptime is correct, and there is no `cargo` process in the tree. `cargo run --release` on
`io_oreilles` can compile 579 MB of CUDA code in silence before it starts.

Use the `Rebuild` button after a change of the source. The console shows a "périmé" badge when a
source file is newer than the binary.

## 🧪 Tests

```bash
cd services/terminal
../../venv/bin/python -m pytest tests/
```

## 🔮 Not in V0.1

The console has a tab bar with one tab. The message rate, the end-to-end latency and the p95 stay
the property of `services/benchmark`, which measures them correctly. A browser shell, the launch of
`io_visage` and `io_yeux`, and the persistence of the logs are not present.
