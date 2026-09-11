# Project board: the `tickets/` folder

The roadmap of this project lives in `tickets/`, at the root of the repository. Each ticket is one
Markdown file. There is no database and no server. You read a ticket with `cat`, you find a ticket
with `grep`, and you move a ticket with one line edit.

The `Kanban` tab of `services/terminal` shows the same files as five columns. The tab is a view. The
files are the truth.

## The format of a ticket

The file name is `<id on 4 digits>-<title as a slug>.md`. The name stays the same for the life of
the ticket, also when the title changes. A link to a ticket thus stays correct.

Each file starts with a flat front matter: one key, a colon, one scalar value. There is no list and
no nested value.

```markdown
---
id: 7
title: Voix plus naturelle sur les fins de phrase
status: en-cours
service: io_voix
priority: haute
created: 2026-09-11
updated: 2026-09-11
---

The body, in free Markdown.
```

| Key | Necessary | Value |
| --- | --- | --- |
| `id` | yes | The number of the ticket, without the leading zeros of the file name. |
| `title` | yes | The title that the board shows. This key is the truth, not the file name. |
| `status` | yes | One of the five values below. |
| `created` | yes | The date of creation, as `YYYY-MM-DD`. |
| `service` | no | The service that the ticket touches: `cortex`, `io_voix`, `lobe_frontal`… |
| `priority` | no | `haute`, `normale` or `basse`. |
| `updated` | yes | The date of the last change, as `YYYY-MM-DD`. The console sorts the `termine` column with it. |

`service` is not compared to `services.toml`. A ticket can speak about a part of the project that is
not yet a service.

## The five statuses

The values are French slugs in ASCII. There is no accent, thus a `grep` or a `sed` needs no escape.
The console shows the accented label.

| Value | Meaning |
| --- | --- |
| `en-attente` | The work is specified. Nobody works on it. |
| `en-cours` | Somebody works on it now. |
| `termine` | The work is complete. |
| `bloque` | The work cannot continue. The body says what blocks it. |
| `amelioration-continue` | A permanent watch. See the rule below. |

## The rule of `amelioration-continue`

A ticket with this status has no end. The three watches of the project are the LLM model, the STT
model and the TTS model.

1. Move the ticket to `en-cours` when you make a trial.
2. Add one dated line to the trial log in the body: the model, the measurement, the verdict.
3. Move the ticket back to `amelioration-continue` when the trial ends.

**Never move such a ticket to `termine`.** The watch stays open.

Measure each trial with `services/benchmark`. A line with no measurement has no value.

## Operations

List the board:

```bash
ls tickets/
```

Find the tickets of one status:

```bash
grep -l '^status: en-attente' tickets/*.md
```

Read one ticket:

```bash
cat tickets/0007-voix-plus-naturelle.md
```

Create a ticket. The identifier is the largest identifier in the folder, plus one. Write the file
with the front matter above. Use the same date in `created` and in `updated`. Always write the
`updated` line, also on a new ticket: the move command replaces that line, and it cannot add one
that is absent.

Move a ticket. Change the `status` line, and change the `updated` line in the same edit. The range
`2,/^---$/` keeps the edit inside the front matter: a body that quotes a `status:` line stays
untouched.

```bash
sed -i "2,/^---$/{s/^status: .*/status: en-cours/;s/^updated: .*/updated: $(date +%F)/}" tickets/0007-voix-plus-naturelle.md
```

Close a ticket: move it to `termine`. The file stays in the folder. Nothing is archived, thus the
path of an old ticket never changes, and the git history keeps one line.

## Language

Write the ticket itself in French: the console is in French, and the board is a French surface. This
page, and every other document of the repository, stays in Simplified Technical English.

## The two trackers

`tickets/` holds the roadmap of the project. GitHub Issues stay the door for a report that comes
from outside. **There is no synchronisation between the two, in any direction.**

When you accept an external report, write a new ticket by hand and put one line at the top of the
body: `Origine : #42`.

See `docs/agents/issue-tracker.md`.

## This repository is public

A ticket is committed. It must contain no real conversation data, no key, and no private path.
