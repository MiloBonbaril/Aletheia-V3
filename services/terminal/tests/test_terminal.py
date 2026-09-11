"""Checks for the pure functions of the terminal daemon.

Run: cd services/terminal && ../../venv/bin/python -m pytest tests/
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from supervisor import Manifest, guess_level, strip_ansi  # noqa: E402

ROOT = HERE.parent.parent.parent


def test_strip_ansi_removes_colour_and_cursor_moves():
    assert strip_ansi("\x1b[31mrouge\x1b[0m") == "rouge"
    assert strip_ansi("\x1b[2J\x1b[Hécran") == "écran"
    assert strip_ansi("\x1b]0;titre\x07texte") == "texte"
    assert strip_ansi("sans échappement") == "sans échappement"


def test_guess_level_reads_the_common_shapes():
    assert guess_level("INFO connexion établie") == "info"
    assert guess_level("2026-09-08 WARNING file d'attente pleine") == "warn"
    assert guess_level("Traceback (most recent call last):") == "err"
    assert guess_level("thread 'main' panicked at src/main.rs") == "err"
    assert guess_level("tout va bien") == "info"


def test_manifest_loads_and_resolves_paths():
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    names = [e.name for e in manifest.entries]
    assert "cortex" in names and "llama-server" in names
    # io_visage, io_yeux and terminal have no source code: they are not managed.
    assert not {"io_visage", "io_yeux", "terminal"} & set(names)
    cortex = next(e for e in manifest.entries if e.name == "cortex")
    assert cortex.cwd == (ROOT / "services/cortex").resolve()
    assert cortex.rebuild == ["cargo", "build", "--release"]


def test_the_two_io_voix_entries_differ_only_by_the_local_playback():
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    entries = {e.name: e for e in manifest.entries}
    loud, muted = entries["io_voix"], entries["io_voix_muet"]
    assert loud.cmd == muted.cmd and loud.cwd == muted.cwd
    assert muted.env["MUTE_LOCAL_PLAYBACK"] == "1"
    assert "MUTE_LOCAL_PLAYBACK" not in loud.env
    for entry in (loud, muted):
        assert entry.env["A8_VOICE_TEXT"].strip()
        # engine.py exits when A8_VOICE_WAV has no A8_VOICE_TEXT beside it.
        assert (entry.cwd / entry.env["A8_VOICE_WAV"]).is_file()


def test_no_profile_starts_two_services_that_share_a_device():
    """io_voix and io_voix_muet share the speaker; the io_oreilles pair shares the audio input."""
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    for name, members in manifest.profiles.items():
        for pair in ({"io_voix", "io_voix_muet"}, {"io_oreilles", "io_oreilles_discord"}):
            assert not pair <= set(members), f"le profil {name} lance {pair} ensemble"


def test_every_profile_names_known_services():
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    known = {e.name for e in manifest.entries}
    for name, members in manifest.profiles.items():
        assert set(members) <= known, f"le profil {name} nomme un service inconnu"


def test_manifest_rejects_a_duplicate_name(tmp_path):
    bad = tmp_path / "dup.toml"
    bad.write_text(
        '[[service]]\nname = "a"\ncwd = "."\n[[service]]\nname = "a"\ncwd = "."\n'
    )
    with pytest.raises(ValueError, match="same name"):
        Manifest.load(bad, ROOT)


def test_manifest_rejects_a_profile_with_an_unknown_service(tmp_path):
    bad = tmp_path / "ghost.toml"
    bad.write_text(
        '[[service]]\nname = "a"\ncwd = "."\n\n[profile.p]\nservices = ["a", "fantome"]\n'
    )
    with pytest.raises(ValueError, match="unknown services"):
        Manifest.load(bad, ROOT)


def test_config_files_point_at_files_that_exist():
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    names = {c.name for c in manifest.config_files}
    assert {"PERSONA.md", "MEMORY.md", "USER.md"} <= names
    services = {e.name for e in manifest.entries}
    for entry in manifest.config_files:
        assert entry.path.is_file(), f"{entry.name} ne pointe sur aucun fichier"
        # The console offers a restart button with this name.
        assert entry.service in services


def test_manifest_rejects_a_config_file_outside_the_repository(tmp_path):
    bad = tmp_path / "escape.toml"
    bad.write_text(
        '[[config_file]]\nname = "passwd"\npath = "../../../../etc/passwd"\n'
    )
    with pytest.raises(ValueError, match="outside the repository"):
        Manifest.load(bad, ROOT)


def test_manifest_rejects_a_config_file_that_names_an_unknown_service(tmp_path):
    bad = tmp_path / "ghost.toml"
    bad.write_text(
        '[[service]]\nname = "a"\ncwd = "."\n\n'
        '[[config_file]]\nname = "x"\npath = "README.md"\nservice = "fantome"\n'
    )
    with pytest.raises(ValueError, match="unknown service"):
        Manifest.load(bad, ROOT)


def test_manifest_rejects_two_config_files_with_the_same_name(tmp_path):
    bad = tmp_path / "dup.toml"
    bad.write_text(
        '[[config_file]]\nname = "x"\npath = "README.md"\n'
        '[[config_file]]\nname = "x"\npath = "CONTEXT.md"\n'
    )
    with pytest.raises(ValueError, match="same name"):
        Manifest.load(bad, ROOT)


# ---------- chat ----------

import asyncio  # noqa: E402
import json as _json  # noqa: E402

from chat import Chat  # noqa: E402


class _Msg:
    """Ce que nats-py passe au rappel: un objet avec un attribut `data`."""

    def __init__(self, payload: dict):
        self.data = _json.dumps(payload).encode()


def _feed(chat, fragments):
    async def run():
        for fragment in fragments:
            await chat._on_fragment(_Msg(fragment))
    asyncio.run(run())


def test_les_fragments_se_recollent_en_un_seul_message():
    # Le rappel reçoit le message en cours d'écriture, toujours le même objet.
    # On copie le texte tout de suite, comme _broadcast() qui sérialise sur place.
    vus = []
    chat = Chat(lambda message: vus.append(message["text"]))
    _feed(chat, [
        {"sequence": 0, "text": "Salut ", "is_last": False},
        {"sequence": 1, "text": "Milo", "is_last": False},
        {"sequence": 2, "text": " !", "is_last": True},
    ])
    assert len(chat.messages) == 1
    message = chat.messages[0]
    assert message["role"] == "aletheia" and message["text"] == "Salut Milo !"
    assert message["done"] is True
    # La console reçoit l'état après chaque fragment, pour l'affichage progressif.
    assert vus == ["Salut ", "Salut Milo", "Salut Milo !"]


def test_un_nouveau_tour_ouvre_un_nouveau_message():
    chat = Chat(lambda _: None)
    _feed(chat, [
        {"sequence": 0, "text": "un", "is_last": True},
        {"sequence": 0, "text": "deux", "is_last": True},
    ])
    assert [m["text"] for m in chat.messages] == ["un", "deux"]
    assert len({m["id"] for m in chat.messages}) == 2


def test_stay_silent_donne_un_tour_vide_mais_termine():
    """Le dernier fragment peut n'avoir aucun texte: c'est un silence, pas une perte."""
    chat = Chat(lambda _: None)
    _feed(chat, [{"sequence": 0, "text": "", "is_last": True}])
    assert len(chat.messages) == 1
    assert chat.messages[0]["text"] == "" and chat.messages[0]["done"] is True


def test_un_fragment_illisible_ne_casse_pas_le_fil():
    chat = Chat(lambda _: None)

    class Casse:
        data = b"{ceci n'est pas du JSON"

    async def run():
        await chat._on_fragment(Casse())
        await chat._on_fragment(_Msg({"sequence": 0, "text": "ok", "is_last": True}))
    asyncio.run(run())
    assert [m["text"] for m in chat.messages] == ["ok"]


def test_envoyer_sans_bus_echoue_au_lieu_de_perdre_le_message():
    chat = Chat(lambda _: None)
    assert chat.connected is False
    with pytest.raises(ConnectionError):
        asyncio.run(chat.send("coucou"))
    assert len(chat.messages) == 0


# ---------- board ----------

from tickets import Board, Ticket  # noqa: E402


def _write(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


TICKET = """---
id: 7
title: Voix plus naturelle sur les fins de phrase
status: en-cours
service: io_voix
priority: haute
created: 2026-09-11
updated: 2026-09-12
---

Le corps, en Markdown libre.

```markdown
status: termine
```
"""


def test_un_ticket_relu_puis_reecrit_rend_le_meme_texte(tmp_path):
    """L'aller-retour est la seule preuve que la lecture n'a rien perdu."""
    _write(tmp_path, "0007-voix.md", TICKET)
    ticket = Board(tmp_path).tickets()[0]
    assert ticket.to_text() == TICKET


def test_le_board_lit_les_champs_du_frontmatter(tmp_path):
    _write(tmp_path, "0007-voix.md", TICKET)
    ticket = Board(tmp_path).tickets()[0]
    assert ticket.id == 7
    assert ticket.title == "Voix plus naturelle sur les fins de phrase"
    assert ticket.status == "en-cours"
    assert ticket.fields["service"] == "io_voix"
    assert ticket.fields["priority"] == "haute"
    # Le corps garde sa ligne `status:` citée: elle n'est pas du frontmatter.
    assert "status: termine" in ticket.body


def test_un_fichier_casse_est_ignore_sans_emporter_le_dossier(tmp_path, capsys):
    _write(tmp_path, "0007-voix.md", TICKET)
    _write(tmp_path, "0008-sans-frontmatter.md", "juste du texte\n")
    _write(tmp_path, "0009-statut-inconnu.md", "---\nid: 9\ntitle: X\nstatus: fantome\ncreated: 2026-09-12\n---\n")
    _write(tmp_path, "0010-sans-titre.md", "---\nid: 10\nstatus: en-attente\ncreated: 2026-09-12\n---\n")
    board = Board(tmp_path)
    assert [t.id for t in board.tickets()] == [7]
    # Le daemon dit lesquels il a laissés de côté, sinon ils disparaissent en silence.
    said = capsys.readouterr().out
    for name in ("0008", "0009", "0010"):
        assert name in said


def test_les_tickets_sont_tries_par_priorite_puis_par_id(tmp_path):
    def ticket(number, priority):
        line = f"priority: {priority}\n" if priority else ""
        _write(
            tmp_path,
            f"{number:04d}-t.md",
            f"---\nid: {number}\ntitle: T{number}\nstatus: en-attente\n{line}"
            f"created: 2026-09-12\nupdated: 2026-09-12\n---\n",
        )

    ticket(1, "normale")
    ticket(2, "haute")
    ticket(3, None)  # sans priorité: la même place que `normale`
    ticket(4, "basse")
    ticket(5, "haute")
    assert [t.id for t in Board(tmp_path).tickets()] == [2, 5, 1, 3, 4]


def test_deux_fichiers_qui_reclament_le_meme_id_ne_donnent_quun_ticket(tmp_path, capsys):
    """Deux agents qui prennent « le plus grand id plus un » écrivent le même id."""
    frontmatter = "---\nid: 12\ntitle: {}\nstatus: en-attente\ncreated: 2026-09-12\n---\n"
    _write(tmp_path, "0012-premier.md", frontmatter.format("Premier"))
    _write(tmp_path, "0012-second.md", frontmatter.format("Second"))
    tickets = Board(tmp_path).tickets()
    assert [t.title for t in tickets] == ["Premier"]
    assert "0012-second.md" in capsys.readouterr().out


def test_un_ticket_sans_updated_prend_sa_date_de_creation(tmp_path):
    _write(
        tmp_path,
        "0011-t.md",
        "---\nid: 11\ntitle: T\nstatus: en-attente\ncreated: 2026-09-01\n---\n",
    )
    ticket = Board(tmp_path).tickets()[0]
    assert ticket.updated == "2026-09-01"


def test_le_board_ne_signale_un_changement_que_lorsque_le_dossier_bouge(tmp_path):
    board = Board(tmp_path)
    assert board.changed() is True   # le premier appel doit servir l'état initial
    assert board.changed() is False
    _write(tmp_path, "0007-voix.md", TICKET)
    assert board.changed() is True
    assert board.changed() is False
    (tmp_path / "0007-voix.md").unlink()
    assert board.changed() is True


def test_un_dossier_absent_donne_un_board_vide(tmp_path):
    """Le dépôt peut ne pas avoir de dossier tickets/: le daemon démarre quand même."""
    board = Board(tmp_path / "jamais-cree")
    assert board.tickets() == []
    assert board.state()["tickets"] == []


def test_letat_du_board_est_serialisable_pour_la_console(tmp_path):
    _write(tmp_path, "0007-voix.md", TICKET)
    state = Board(tmp_path).state()
    assert state["type"] == "tickets"
    entry = state["tickets"][0]
    assert entry["id"] == 7 and entry["status"] == "en-cours"
    assert entry["service"] == "io_voix" and entry["priority"] == "haute"
    assert entry["file"] == "0007-voix.md"
    # L'empreinte de version voyage en chaîne: st_mtime_ns dépasse la précision
    # entière du navigateur.
    assert isinstance(entry["mtime"], str) and entry["mtime"]
    _json.dumps(state)
