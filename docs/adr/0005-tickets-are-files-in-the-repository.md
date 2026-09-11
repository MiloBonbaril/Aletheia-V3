# The tickets are files in the repository, not rows in a database

The project needs a board: what waits, what runs, what is blocked, and the three model watches that
never end. The 23 GitHub issues of this repository are all closed, and nobody opens new ones,
because a browser, a form and a label cost more than they give on a project with one developer. The
`Kanban` tab of `services/terminal` answers that need. This document says where the tickets live,
and why.

**A ticket is one Markdown file in `tickets/`, with a flat front matter.** The folder is at the root
of the repository, and it is committed.

**The reason is the agents.** A Claude Code session must read the board, take a ticket and close it.
With files, it already can: `cat` reads a ticket, `grep` finds one, one line edit moves one. A
database, or an HTTP API, gives the same thing only after we write a command line tool, document it,
and keep it correct. The laziest storage is the one that needs no access layer at all.

**The daemon is not the owner of the tickets.** It reads and writes the same files as the developer
and the agents. It holds no lock and no cache of truth. A ticket that changes outside the console
appears in the console in less than two seconds, because the sampling loop that already runs every
two seconds compares the dates of the folder.

**Considered options**: SQLite in `services/terminal` — rejected, because an agent then needs a tool
to read a ticket, and git shows nothing. GitHub Issues as the board, with the five statuses as
labels — rejected, because the tab becomes an API client, it stops working with no network, and the
order of a column needs GitHub Projects and its GraphQL API. One single `TICKETS.md` file —
rejected, because each write rewrites the whole file, thus the console and an agent overwrite each
other.

**Consequences.** There is no transaction: two writers can collide, thus each write carries the
version of the file that it changes, and a stale version gets a conflict and the current text, as
the `Config` tab already does. There is no query language: `grep` is the query. There is no stored
rank in a column: the order comes from the priority and the identifier. The front matter stays flat,
thus the daemon parses it in ten lines and `services/terminal` adds no YAML dependency; a key with a
list or a nested value breaks that promise and is not allowed. This repository is public, thus a
ticket contains no conversation data, no key and no private path. A file with a broken front matter
is ignored and reported, never fatal, like a broken fragment in the chat.
