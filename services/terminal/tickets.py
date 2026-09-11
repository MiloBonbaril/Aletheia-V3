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

from dataclasses import dataclass
from pathlib import Path

# The five statuses, as ASCII French slugs. No accent: a `grep` or a `sed` of an
# agent then needs no escape. The console shows the accented label.
STATUSES = ("en-attente", "en-cours", "termine", "bloque", "amelioration-continue")
PRIORITIES = ("haute", "normale", "basse")
# A ticket with no priority takes the place of a normal one.
RANK = {"haute": 0, "normale": 1, "basse": 2}
REQUIRED = ("id", "title", "status", "created")
MARK = "---"


class TicketError(ValueError):
    """A file that does not follow the contract. The board leaves it out."""


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
