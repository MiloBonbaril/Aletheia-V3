"""The board of the Kanban tab: the `tickets/` folder, read as tickets.

One Markdown file is one ticket. The front matter is flat: one key, a colon, one
scalar value. `docs/agents/tickets.md` is the contract, and
`docs/adr/0005-tickets-are-files-in-the-repository.md` says why the tickets are
files and not rows.

The daemon is not the owner of these files. The developer and the agents write
them at the same time, thus this module never holds a lock and never caches a
truth: it reads the folder again each time the dates change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# The five statuses, as ASCII French slugs. No accent: a `grep` or a `sed` of an
# agent then needs no escape. The console shows the accented label.
STATUSES = ("en-attente", "en-cours", "termine", "bloque", "amelioration-continue")
PRIORITIES = ("haute", "normale", "basse")
# A ticket with no priority takes the place of a normal one.
RANK = {"haute": 0, "normale": 1, "basse": 2}
REQUIRED = ("id", "title", "status", "created")
# The console changes these fields and no other. `id` and `created` never move,
# and `updated` is written by the board itself.
WRITABLE = ("title", "status", "service", "priority")
MARK = "---"


class TicketError(ValueError):
    """A file, or a change, that does not follow the contract."""


class Conflict(Exception):
    """The file changed on the disk since the browser read it.

    It carries the ticket as the disk holds it now, thus the console shows the
    other version without reading the folder again.
    """

    def __init__(self, ticket: "Ticket") -> None:
        super().__init__("le ticket a changé sur le disque")
        self.ticket = ticket


@dataclass
class Ticket:
    path: Path
    fields: dict[str, str]
    body: str
    mtime: str

    @property
    def id(self) -> int:
        return int(self.fields["id"])

    @property
    def title(self) -> str:
        return self.fields["title"]

    @property
    def status(self) -> str:
        return self.fields["status"]

    @property
    def updated(self) -> str:
        # `updated` is necessary in a new ticket, but a file written by hand can
        # miss it. The date of creation is the honest answer, not today.
        return self.fields.get("updated") or self.fields["created"]

    def to_text(self) -> str:
        """The text of the file, in exact round trip with `parse`."""
        lines = "".join(f"{key}: {value}\n" for key, value in self.fields.items())
        return f"{MARK}\n{lines}{MARK}\n{self.body}"

    def state(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "service": self.fields.get("service"),
            "priority": self.fields.get("priority"),
            "created": self.fields["created"],
            "updated": self.updated,
            "body": self.body,
            "file": self.path.name,
            # st_mtime_ns is above 2^53: a JSON number would lose its last digits
            # in the browser. The console only compares this value.
            "mtime": self.mtime,
        }


def parse(text: str, path: Path, mtime: str) -> Ticket:
    lines = text.split("\n")
    if not lines or lines[0].strip() != MARK:
        raise TicketError("pas de frontmatter")
    try:
        close = lines.index(MARK, 1)
    except ValueError:
        raise TicketError("frontmatter non fermé")

    fields: dict[str, str] = {}
    for line in lines[1:close]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise TicketError(f"ligne sans deux-points : {line!r}")
        fields[key.strip()] = value.strip()

    missing = [key for key in REQUIRED if not fields.get(key)]
    if missing:
        raise TicketError(f"champ manquant : {', '.join(missing)}")
    if not fields["id"].isdigit():
        raise TicketError(f"id non numérique : {fields['id']!r}")
    if fields["status"] not in STATUSES:
        raise TicketError(f"statut inconnu : {fields['status']!r}")

    return Ticket(path=path, fields=fields, body="\n".join(lines[close + 1:]), mtime=mtime)


class Board:
    """The folder of tickets, read on demand."""

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)
        self._fingerprint: tuple | None = None

    # ---------- reading ----------

    def tickets(self) -> list[Ticket]:
        found: list[Ticket] = []
        seen: dict[int, str] = {}
        for path in sorted(self._dir.glob("*.md")) if self._dir.is_dir() else []:
            try:
                stat = path.stat()
                ticket = parse(path.read_text(encoding="utf-8"), path, str(stat.st_mtime_ns))
                # Two agents that both take "the largest id plus one" write the
                # same id. The board keeps the first file in name order, and says
                # the other one aloud: a silent drop looks like a lost ticket.
                if ticket.id in seen:
                    raise TicketError(f"id {ticket.id} déjà pris par {seen[ticket.id]}")
                seen[ticket.id] = path.name
                found.append(ticket)
            except (TicketError, OSError, UnicodeDecodeError) as exc:
                # One bad file must never hide the whole board, and it must never
                # disappear in silence either.
                print(f"[terminal] ticket ignoré · {path.name} : {exc}", flush=True)
        found.sort(key=lambda t: (RANK.get(t.fields.get("priority", ""), RANK["normale"]), t.id))
        return found

    def state(self) -> dict:
        return {"type": "tickets", "tickets": [ticket.state() for ticket in self.tickets()]}

    # ---------- freshness ----------

    def changed(self) -> bool:
        """Has the folder moved since the last call?

        The sampling loop of the daemon asks this every two seconds. A `stat` of a
        few dozen files is nothing beside the process sampling it already does.
        The first call always answers yes: the console needs a first state.
        """
        current = self._fingerprint_now()
        if current == self._fingerprint:
            return False
        self._fingerprint = current
        return True

    def _fingerprint_now(self) -> tuple:
        if not self._dir.is_dir():
            return ()
        marks = []
        for path in sorted(self._dir.glob("*.md")):
            try:
                stat = path.stat()
            except OSError:  # deleted between the glob and the stat
                continue
            marks.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(marks)

    def get(self, ticket_id: int) -> Ticket:
        for ticket in self.tickets():
            if ticket.id == ticket_id:
                return ticket
        raise LookupError(f"ticket inconnu : {ticket_id}")

    # ---------- writing ----------

    def update(self, ticket_id: int, changes: dict[str, str], mtime: str) -> Ticket:
        """Change some fields of one ticket, and write the file.

        `mtime` is the version that the browser read. A version that the browser
        never saw is never overwritten: the developer edits the same file in an
        editor, and an agent writes it from another session.
        """
        ticket = self.get(ticket_id)
        if ticket.mtime != mtime:
            raise Conflict(ticket)

        clean = {}
        for key, value in changes.items():
            if key not in WRITABLE:
                raise TicketError(f"champ non modifiable : {key!r}")
            value = str(value).strip()
            # The front matter is flat, one line for one key. A value with a line
            # break would write a second key, or close the block.
            if "\n" in value or "\r" in value:
                raise TicketError(f"valeur sur plusieurs lignes : {key!r}")
            if key == "status" and value not in STATUSES:
                raise TicketError(f"statut inconnu : {value!r}")
            if key == "priority" and value and value not in PRIORITIES:
                raise TicketError(f"priorité inconnue : {value!r}")
            if key == "title" and not value:
                raise TicketError("titre vide")
            clean[key] = value

        ticket.fields.update(clean)
        ticket.fields["updated"] = date.today().isoformat()
        return self._save(ticket)

    def _save(self, ticket: Ticket) -> Ticket:
        """Write beside the file, then rename.

        A daemon that dies in the middle leaves the old ticket whole, never a
        half written one. There is no `.bak` copy: the folder is in git.
        """
        temp = ticket.path.with_name(ticket.path.name + ".tmp")
        temp.write_text(ticket.to_text(), encoding="utf-8")
        os.replace(temp, ticket.path)
        ticket.mtime = str(ticket.path.stat().st_mtime_ns)
        return ticket
