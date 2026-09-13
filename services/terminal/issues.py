"""The `Issues` board of the Kanban tab: the GitHub issues, read through `gh`.

This board is a window. It reads and it shows, and it writes nothing: no button
of the console closes an issue, applies a label, or copies an issue into
`tickets/`. `docs/agents/issue-tracker.md` says why the repo keeps two trackers
with no synchronisation between them.

The columns come from the triage vocabulary of `docs/agents/triage-labels.md`.
There is no `needs-triage` label: an open issue with no triage label is an issue
to triage, thus the first column fills itself.

`board()` is the only place that holds a decision. The call to `gh` below it
holds none, and the routes of the daemon hold none.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

# The five columns, in the order in which a report goes through them. Each one
# holds the key that the console uses, the label that it shows, and the GitHub
# label that sends an issue there. `None` marks the two columns that a state
# fills, not a label: the first one takes an open issue with no triage label,
# the last one takes every closed issue.
COLUMNS = (
    ("a-trier", "À trier", None),
    ("info-manquante", "Info manquante", "needs-info"),
    ("pret-agent", "Prêt agent", "ready-for-agent"),
    ("pret-humain", "Prêt humain", "ready-for-human"),
    ("fermees", "Fermées", None),
)
# The labels that a column already says. The card does not repeat them as a tag.
# `wontfix` is not one of them: it has no column, thus its tag carries the only
# sign that a decision was made.
COLUMN_LABELS = frozenset(label for _, _, label in COLUMNS if label)
# `gh` knows the repo from the git remote. `issue list` leaves out the pull
# requests, which is what this board wants.
COMMAND = (
    "gh", "issue", "list",
    "--state", "all",
    "--limit", "200",
    "--json", "number,title,state,labels,updatedAt,url",
)
# `gh` parle au réseau. Sans limite, un appel qui ne rend jamais la main retient
# la requête du navigateur pour toujours, et chaque affichage du board en ajoute
# un autre. Une mesure locale de cet appel donne 0,6 s.
TIMEOUT = 20


class Unavailable(Exception):
    """`gh` did not answer. The console says it instead of showing an empty board."""


def _names(issue: dict) -> list[str]:
    """The label names of an issue, whatever shape `gh` gives them."""
    found = []
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str) and name:
            found.append(name)
    return found


def _column_of(issue: dict, names: list[str]) -> str:
    """The one column that holds this issue.

    The closed state wins over every label. For an open issue, the columns are
    read in their display order and the first label that matches wins: an issue
    that waits for its reporter is blocked, whatever its other label says. One
    pass, one answer, thus a card is never in two places and the counters sum.
    """
    if str(issue.get("state", "OPEN")).upper() == "CLOSED":
        return COLUMNS[-1][0]
    for key, _, label in COLUMNS:
        if label and label in names:
            return key
    return COLUMNS[0][0]


def _card(issue: dict, names: list[str]) -> dict:
    updated = str(issue.get("updatedAt") or "")
    return {
        "number": issue.get("number") or 0,
        "title": str(issue.get("title") or ""),
        "url": str(issue.get("url") or ""),
        # The column already says the triage label. The others tell the nature
        # of a report before it is opened.
        "labels": [name for name in names if name not in COLUMN_LABELS],
        # The date is enough. The hour is noise on a card.
        "updated": updated[:10],
        "closed": str(issue.get("state", "OPEN")).upper() == "CLOSED",
    }


def board(raw: list) -> dict:
    """Turn what `gh` prints into the state of the board.

    An entry that is not an object is left out: `gh` is a moving target and a
    board that falls is worse than a board that misses a line. A missing key
    gives an empty value, never an error.
    """
    issues = [entry for entry in raw if isinstance(entry, dict)]
    # An ISO 8601 UTC timestamp sorts as a string. The most recently touched
    # issue goes to the top of its column.
    issues.sort(key=lambda issue: str(issue.get("updatedAt") or ""), reverse=True)
    columns = {key: [] for key, _, _ in COLUMNS}
    for issue in issues:
        names = _names(issue)
        columns[_column_of(issue, names)].append(_card(issue, names))
    return {
        "type": "issues",
        "columns": [
            {"key": key, "label": label, "issues": columns[key]} for key, label, _ in COLUMNS
        ],
        "total": len(issues),
    }


def _stop(process) -> None:
    """Kill a `gh` that outlived its request. A dead process does no harm."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()


async def fetch(cwd) -> list:
    """Run `gh` and read the JSON it prints.

    No token enters the repo: `gh` holds the authentication of the machine, and
    it finds the repo from the git remote. This wrapper holds no decision.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *COMMAND,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:  # `gh` is not on this machine
        raise Unavailable(f"gh introuvable: {exc}") from exc
    try:
        out, err = await asyncio.wait_for(process.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        _stop(process)
        raise Unavailable(f"gh n'a pas répondu en {TIMEOUT} s") from None
    except asyncio.CancelledError:
        # Le navigateur a coupé. Sans cela le processus `gh` survit à la requête
        # qui l'a lancé, et un board rafraîchi souvent en laisse une pile.
        _stop(process)
        raise
    if process.returncode != 0:
        raise Unavailable((err.decode("utf-8", "replace").strip() or "gh a échoué")[:300])
    try:
        found = json.loads(out.decode("utf-8", "replace") or "[]")
    except ValueError as exc:
        raise Unavailable(f"gh a répondu autre chose que du JSON: {exc}") from exc
    if not isinstance(found, list):
        raise Unavailable("gh a répondu autre chose qu'une liste")
    return found
