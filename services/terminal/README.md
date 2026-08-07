# 🎛️ Terminal (administration frontend)

The passive control interface.

> **Status: specification only.** This directory contains this file. There is no source code.

## 🎯 Planned functions

- A web panel with a WebSocket connection to the event bus.
- Monitoring of the state of the system: the services, the message flows and the latencies.
- Manual adjustment of the global parameters in PostgreSQL.
- Transmission of direct commands when necessary.

The panel contains no AI logic. It observes the bus and it sends commands. It does not make
decisions.
