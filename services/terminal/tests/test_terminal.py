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
    # io_visage, io_chat and terminal have no source code: they are not managed.
    assert not {"io_visage", "io_chat", "terminal"} & set(names)
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
    """io_voix and io_voix_muet share the speaker; the io_oreilles pair shares the audio input;
    the llama-server entries share port 8080."""
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    for name, members in manifest.profiles.items():
        for group in ({"io_voix", "io_voix_muet"}, {"io_oreilles", "io_oreilles_discord"},
                      {"llama-server", "llama-server-qwen", "llama-server-bonsai"}):
            assert len(group & set(members)) <= 1, f"le profil {name} lance {group} ensemble"


def test_every_profile_names_known_services():
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    known = {e.name for e in manifest.entries}
    for name, members in manifest.profiles.items():
        assert set(members) <= known, f"le profil {name} nomme un service inconnu"


def test_io_yeux_is_in_no_profile():
    """The screen capture stays a deliberate act: see docs/adr/0006."""
    manifest = Manifest.load(HERE.parent / "services.toml", ROOT)
    assert "io_yeux" in {e.name for e in manifest.entries}
    for name, members in manifest.profiles.items():
        assert "io_yeux" not in members, f"le profil {name} lance io_yeux"


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
updated: 2026-09-11
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


# ---------- board: écriture ----------

import datetime  # noqa: E402

from tickets import Conflict, TicketError  # noqa: E402

TODAY = datetime.date.today().isoformat()


def test_deplacer_un_ticket_ne_touche_que_le_statut_et_la_date(tmp_path):
    path = _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    ticket = board.tickets()[0]
    board.update(7, {"status": "termine"}, ticket.mtime)

    avant = TICKET.split("\n")
    apres = path.read_text(encoding="utf-8").split("\n")
    differences = [(a, b) for a, b in zip(avant, apres) if a != b]
    assert len(avant) == len(apres)
    assert differences == [
        ("status: en-cours", "status: termine"),
        ("updated: 2026-09-11", f"updated: {TODAY}"),
    ]


def test_un_statut_inconnu_est_refuse_et_le_fichier_ne_bouge_pas(tmp_path):
    path = _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    mtime = board.tickets()[0].mtime
    with pytest.raises(TicketError, match="statut"):
        board.update(7, {"status": "fantome"}, mtime)
    assert path.read_text(encoding="utf-8") == TICKET


def test_un_champ_inconnu_est_refuse(tmp_path):
    _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    mtime = board.tickets()[0].mtime
    with pytest.raises(TicketError, match="champ"):
        board.update(7, {"assignee": "milo"}, mtime)


def test_une_empreinte_perimee_donne_un_conflit_et_laisse_le_disque_tranquille(tmp_path):
    """Un agent a écrit entre l'ouverture de la carte et le dépôt de la carte."""
    path = _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    perimee = board.tickets()[0].mtime
    board.update(7, {"status": "bloque"}, perimee)  # l'autre écrivain passe en premier

    with pytest.raises(Conflict) as levee:
        board.update(7, {"status": "termine"}, perimee)
    # La console doit pouvoir montrer la version du disque sans relire elle-même.
    assert levee.value.ticket.status == "bloque"
    assert levee.value.ticket.mtime != perimee
    assert "status: bloque" in path.read_text(encoding="utf-8")


def test_un_id_inconnu_ne_cree_rien(tmp_path):
    _write(tmp_path, "0007-voix.md", TICKET)
    with pytest.raises(LookupError):
        Board(tmp_path).update(4242, {"status": "termine"}, "0")
    assert [p.name for p in tmp_path.iterdir()] == ["0007-voix.md"]


def test_lecriture_ajoute_updated_quand_le_fichier_ne_lavait_pas(tmp_path):
    _write(
        tmp_path,
        "0011-t.md",
        "---\nid: 11\ntitle: T\nstatus: en-attente\ncreated: 2026-09-01\n---\n\nCorps.\n",
    )
    board = Board(tmp_path)
    ticket = board.update(11, {"status": "en-cours"}, board.tickets()[0].mtime)
    assert ticket.updated == TODAY
    assert board.tickets()[0].fields["updated"] == TODAY


def test_lecriture_est_atomique_et_ne_laisse_aucun_fichier_temporaire(tmp_path):
    _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    board.update(7, {"status": "termine"}, board.tickets()[0].mtime)
    assert [p.name for p in tmp_path.iterdir()] == ["0007-voix.md"]


# ---------- board: création et suppression ----------


def test_le_prochain_id_part_de_un_sur_un_dossier_vide(tmp_path):
    ticket = Board(tmp_path).create({"title": "Premier ticket"})
    assert ticket.id == 1
    assert ticket.path.name == "0001-premier-ticket.md"
    assert ticket.status == "en-attente"
    assert ticket.fields["created"] == TODAY and ticket.updated == TODAY


def test_le_prochain_id_suit_le_plus_grand_meme_avec_un_trou(tmp_path):
    for number in (1, 7):
        _write(
            tmp_path,
            f"{number:04d}-t.md",
            f"---\nid: {number}\ntitle: T\nstatus: en-attente\ncreated: 2026-09-01\n---\n",
        )
    assert Board(tmp_path).create({"title": "Suivant"}).id == 8


def test_le_prochain_id_compte_aussi_un_fichier_que_le_board_ne_lit_pas(tmp_path):
    """Un fichier cassé garde son numéro: le réutiliser ferait deux tickets #12."""
    _write(tmp_path, "0012-casse.md", "pas de frontmatter\n")
    assert Board(tmp_path).create({"title": "Suivant"}).id == 13


def test_le_nom_de_fichier_est_un_slug_ascii_du_titre(tmp_path):
    ticket = Board(tmp_path).create({"title": "Réduire la latence du préfill (TTFT) !"})
    assert ticket.path.name == "0001-reduire-la-latence-du-prefill-ttft.md"


def test_un_ticket_cree_est_relu_par_le_board(tmp_path):
    board = Board(tmp_path)
    board.create({
        "title": "Voix plus naturelle",
        "status": "en-cours",
        "service": "io_voix",
        "priority": "haute",
    }, body="Le corps du ticket.\n")
    relu = board.tickets()[0]
    assert relu.title == "Voix plus naturelle" and relu.status == "en-cours"
    assert relu.fields["service"] == "io_voix" and relu.fields["priority"] == "haute"
    assert relu.body.strip() == "Le corps du ticket."
    # Une ligne vide sépare le frontmatter du corps, comme dans un ticket écrit
    # à la main.
    assert relu.path.read_text(encoding="utf-8").count("---\n\n") == 1


def test_un_titre_vide_ou_une_priorite_inconnue_sont_refuses_a_la_creation(tmp_path):
    board = Board(tmp_path)
    with pytest.raises(TicketError):
        board.create({"title": "   "})
    with pytest.raises(TicketError, match="priorité"):
        board.create({"title": "T", "priority": "urgente"})
    assert list(tmp_path.iterdir()) == []


def test_le_nom_de_fichier_ne_bouge_pas_quand_le_titre_change(tmp_path):
    """Un lien vers un ticket doit rester valide."""
    board = Board(tmp_path)
    ticket = board.create({"title": "Ancien titre"})
    board.update(ticket.id, {"title": "Nouveau titre"}, ticket.mtime)
    assert [p.name for p in tmp_path.iterdir()] == ["0001-ancien-titre.md"]
    assert board.tickets()[0].title == "Nouveau titre"


def test_le_corps_sedite_sans_toucher_aux_autres_champs(tmp_path):
    path = _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    board.update(7, {}, board.tickets()[0].mtime, body="\nUn corps tout neuf.\n")
    relu = board.tickets()[0]
    assert relu.body == "\nUn corps tout neuf.\n"
    assert relu.status == "en-cours" and relu.title.startswith("Voix plus naturelle")
    assert path.read_text(encoding="utf-8").endswith("---\n\nUn corps tout neuf.\n")


def test_supprimer_enleve_le_fichier(tmp_path):
    board = Board(tmp_path)
    ticket = board.create({"title": "À jeter"})
    board.delete(ticket.id, ticket.mtime)
    assert list(tmp_path.iterdir()) == []
    assert board.tickets() == []


def test_supprimer_une_version_perimee_leve_un_conflit(tmp_path):
    board = Board(tmp_path)
    ticket = board.create({"title": "Occupé"})
    board.update(ticket.id, {"status": "en-cours"}, ticket.mtime)  # un agent écrit
    with pytest.raises(Conflict):
        board.delete(ticket.id, ticket.mtime)
    assert board.tickets()[0].status == "en-cours"


def test_vider_un_champ_optionnel_enleve_la_ligne_du_frontmatter(tmp_path):
    """`service: ` sans rien après est du bruit dans un fichier que l'on lit."""
    path = _write(tmp_path, "0007-voix.md", TICKET)
    board = Board(tmp_path)
    board.update(7, {"service": "", "priority": ""}, board.tickets()[0].mtime)
    texte = path.read_text(encoding="utf-8")
    assert "service:" not in texte and "priority:" not in texte
    assert board.tickets()[0].state()["service"] is None


def test_un_champ_optionnel_vide_nest_pas_ecrit_a_la_creation(tmp_path):
    ticket = Board(tmp_path).create({"title": "T", "service": "", "priority": ""})
    assert "service:" not in ticket.path.read_text(encoding="utf-8")


# ---------- board des issues GitHub ----------

from issues import COLUMNS, board  # noqa: E402


def _issue(number, *, state="OPEN", labels=(), updated="2026-09-01T10:00:00Z", title=None):
    """Une issue telle que `gh issue list --json ...` l'imprime."""
    return {
        "number": number,
        "title": title or f"issue {number}",
        "state": state,
        "labels": [{"name": name} for name in labels],
        "updatedAt": updated,
        "url": f"https://github.com/MiloBonbaril/Aletheia-V3/issues/{number}",
    }


def _column(state, key):
    return next(c for c in state["columns"] if c["key"] == key)


def _where(state, number):
    """La clé de la colonne où la carte se trouve, ou la liste s'il y en a plusieurs."""
    found = [c["key"] for c in state["columns"] if any(i["number"] == number for i in c["issues"])]
    return found[0] if len(found) == 1 else found


def test_les_colonnes_sont_les_cinq_etats_de_triage_dans_lordre():
    keys = [c["key"] for c in board([])["columns"]]
    assert keys == ["a-trier", "info-manquante", "pret-agent", "pret-humain", "fermees"]


def test_une_liste_vide_donne_cinq_colonnes_vides_et_pas_une_erreur():
    state = board([])
    assert state["total"] == 0
    assert all(c["issues"] == [] for c in state["columns"])


def test_une_issue_ouverte_sans_label_de_triage_tombe_a_trier():
    """L'absence de label est l'état de triage: la colonne se remplit toute seule."""
    state = board([_issue(1), _issue(2, labels=["bug", "enhancement"])])
    assert _where(state, 1) == "a-trier"
    assert _where(state, 2) == "a-trier"


@pytest.mark.parametrize(
    "label,colonne",
    [
        ("needs-info", "info-manquante"),
        ("ready-for-agent", "pret-agent"),
        ("ready-for-human", "pret-humain"),
    ],
)
def test_une_issue_ouverte_tombe_dans_la_colonne_de_son_label(label, colonne):
    assert _where(board([_issue(1, labels=[label])]), 1) == colonne


def test_deux_labels_de_triage_donnent_la_premiere_colonne_qui_correspond():
    """Une issue qui attend le rapporteur est bloquée, quoi qu'en dise l'autre label."""
    state = board([_issue(1, labels=["ready-for-agent", "needs-info"])])
    assert _where(state, 1) == "info-manquante"


def test_une_carte_napparait_que_dans_une_seule_colonne():
    issues = [_issue(1, labels=["ready-for-agent", "needs-info", "ready-for-human"])]
    places = [c["key"] for c in board(issues)["columns"] if c["issues"]]
    assert len(places) == 1


def test_une_issue_fermee_va_dans_fermees_meme_avec_un_label_de_triage():
    state = board([_issue(1, state="CLOSED", labels=["ready-for-agent"])])
    assert _where(state, 1) == "fermees"


def test_une_issue_wontfix_ouverte_na_pas_de_traitement_particulier():
    """`wontfix` n'a pas de colonne: elle finit fermée, et sa pastille le dit."""
    state = board([_issue(1, labels=["wontfix"])])
    assert _where(state, 1) == "a-trier"
    assert _column(state, "a-trier")["issues"][0]["labels"] == ["wontfix"]


def test_la_somme_des_colonnes_vaut_le_nombre_dissues_recues():
    issues = [
        _issue(1),
        _issue(2, labels=["needs-info"]),
        _issue(3, labels=["ready-for-agent"]),
        _issue(4, labels=["ready-for-human"]),
        _issue(5, state="CLOSED"),
        _issue(6, state="CLOSED", labels=["wontfix"]),
    ]
    state = board(issues)
    assert sum(len(c["issues"]) for c in state["columns"]) == 6
    assert state["total"] == 6


def test_les_cartes_dune_colonne_vont_de_la_plus_recente_a_la_plus_ancienne():
    issues = [
        _issue(1, updated="2026-01-01T00:00:00Z"),
        _issue(2, updated="2026-09-13T00:00:00Z"),
        _issue(3, updated="2026-05-05T00:00:00Z"),
    ]
    assert [i["number"] for i in _column(board(issues), "a-trier")["issues"]] == [2, 3, 1]


def test_les_labels_de_triage_ne_sont_pas_des_pastilles_les_autres_si():
    state = board([_issue(1, labels=["bug", "needs-info", "documentation"])])
    assert _column(state, "info-manquante")["issues"][0]["labels"] == ["bug", "documentation"]


def test_une_carte_porte_le_numero_le_titre_lurl_et_la_date():
    state = board([_issue(7, title="le bus tombe", updated="2026-09-13T18:58:39Z")])
    carte = _column(state, "a-trier")["issues"][0]
    assert carte["number"] == 7
    assert carte["title"] == "le bus tombe"
    assert carte["url"].endswith("/issues/7")
    assert carte["updated"] == "2026-09-13"  # la date suffit, l'heure est du bruit
    assert carte["closed"] is False


def test_un_champ_absent_ne_fait_pas_tomber_le_board():
    """`gh` change, une clé disparaît: le board montre ce qu'il a."""
    state = board([{"number": 1}, {}, _issue(2)])
    assert state["total"] == 3
    assert sum(len(c["issues"]) for c in state["columns"]) == 3


def test_une_entree_qui_nest_pas_un_objet_est_ignoree():
    state = board([_issue(1), "texte", None, 17])
    assert state["total"] == 1


def test_letat_du_board_est_serialisable_pour_la_console():
    import json

    state = board([_issue(1, labels=["bug"]), _issue(2, state="CLOSED")])
    assert json.loads(json.dumps(state)) == state
    assert state["type"] == "issues"


def test_chaque_colonne_porte_son_libelle_accentue():
    libelles = [c["label"] for c in board([])["columns"]]
    assert libelles == ["À trier", "Info manquante", "Prêt agent", "Prêt humain", "Fermées"]
    assert len(COLUMNS) == 5


# ---------- board des issues: cache et échecs ----------

import asyncio  # noqa: E402
import re  # noqa: E402

import issues as issues_module  # noqa: E402
from issues import MISSING, MUTE, UNAUTHORIZED, Issues, Unavailable, explain  # noqa: E402


class _Gh:
    """Un `gh` de laboratoire: il compte ses appels et obéit au scénario."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def __call__(self, cwd):
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run(coro):
    return asyncio.run(coro)


def test_gh_qui_demande_une_authentification_donne_son_propre_message():
    texte = "To get started with GitHub CLI, please run:  gh auth login"
    assert explain(texte) == UNAUTHORIZED


def test_une_autre_panne_de_gh_garde_ce_que_gh_a_dit():
    message = explain("dial tcp: lookup api.github.com: no such host")
    assert message != UNAUTHORIZED and "no such host" in message


def test_gh_absent_de_la_machine_donne_le_message_qui_le_dit(monkeypatch):
    async def pas_de_gh(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(issues_module.asyncio, "create_subprocess_exec", pas_de_gh)
    with pytest.raises(Unavailable) as levee:
        _run(issues_module.fetch("."))
    assert str(levee.value) == MISSING


def test_un_second_affichage_dans_la_fenetre_ne_relance_pas_gh():
    gh = _Gh([])
    board_issues = Issues(".", call=gh)
    _run(board_issues.state())
    _run(board_issues.state())
    assert gh.calls == 1


def test_rafraichir_relance_gh_meme_quand_le_cache_est_valide():
    gh = _Gh([])
    board_issues = Issues(".", call=gh)
    _run(board_issues.state())
    _run(board_issues.state(force=True))
    assert gh.calls == 2


def test_le_cache_perime_relance_gh():
    horloge = [0.0]
    gh = _Gh([])
    board_issues = Issues(".", ttl=300, call=gh, clock=lambda: horloge[0])
    _run(board_issues.state())
    horloge[0] = 301.0
    _run(board_issues.state())
    assert gh.calls == 2


def test_un_appel_rate_garde_le_board_precedent_et_ajoute_le_message():
    gh = _Gh([_issue(1)], Unavailable("le réseau est coupé"))
    board_issues = Issues(".", call=gh)
    _run(board_issues.state())
    apres = _run(board_issues.state(force=True))
    assert apres["total"] == 1                       # les issues restent à l'écran
    assert apres["error"] == "le réseau est coupé"
    assert _where(apres, 1) == "a-trier"


def test_un_appel_rate_ne_remplace_pas_le_cache():
    gh = _Gh([_issue(1)], Unavailable("panne"), Unavailable("panne"))
    board_issues = Issues(".", call=gh)
    premier = _run(board_issues.state())
    _run(board_issues.state(force=True))
    assert _run(board_issues.state())["columns"] == premier["columns"]


def test_un_echec_des_le_premier_appel_donne_un_board_vide_et_le_message():
    board_issues = Issues(".", call=_Gh(Unavailable(MISSING)))
    etat = _run(board_issues.state())
    assert etat["total"] == 0
    assert etat["error"] == MISSING
    assert etat["checked"] is None                   # aucun succès à dater
    assert len(etat["columns"]) == 5                 # le board garde sa forme


def test_un_succes_date_la_lecture_et_efface_le_message():
    gh = _Gh(Unavailable("panne"), [_issue(1)])
    board_issues = Issues(".", call=gh)
    _run(board_issues.state())
    etat = _run(board_issues.state(force=True))
    assert etat["error"] is None
    assert re.fullmatch(r"\d{2}:\d{2}", etat["checked"])


def test_lheure_du_dernier_succes_survit_a_un_echec():
    gh = _Gh([_issue(1)], Unavailable("panne"))
    board_issues = Issues(".", call=gh)
    heure = _run(board_issues.state())["checked"]
    assert _run(board_issues.state(force=True))["checked"] == heure


def test_deux_affichages_simultanes_ne_lancent_quun_seul_gh():
    """Sans verrou, ouvrir deux onglets lance deux sous-processus pour rien."""
    gh = _Gh([])
    board_issues = Issues(".", call=gh)

    async def les_deux():
        await asyncio.gather(board_issues.state(), board_issues.state())

    _run(les_deux())
    assert gh.calls == 1


def test_letat_du_cache_est_serialisable_pour_la_console():
    import json

    etat = _run(Issues(".", call=_Gh([_issue(1)])).state())
    assert json.loads(json.dumps(etat)) == etat


def test_un_gh_qui_echoue_sans_rien_dire_a_quand_meme_un_message():
    assert explain("") == MUTE


def test_une_panne_ne_relance_pas_gh_a_chaque_affichage():
    """Sinon chaque aller-retour redemande à un GitHub qui vient de refuser."""
    gh = _Gh(Unavailable("panne"))
    board_issues = Issues(".", call=gh)
    _run(board_issues.state())
    _run(board_issues.state())
    _run(board_issues.state())
    assert gh.calls == 1


def test_rafraichir_passe_outre_la_pause_qui_suit_une_panne():
    gh = _Gh(Unavailable("panne"))
    board_issues = Issues(".", call=gh)
    _run(board_issues.state())
    _run(board_issues.state(force=True))
    assert gh.calls == 2


def test_la_pause_qui_suit_une_panne_finit_par_expirer():
    horloge = [0.0]
    gh = _Gh(Unavailable("panne"))
    board_issues = Issues(".", call=gh, clock=lambda: horloge[0])
    _run(board_issues.state())
    horloge[0] = 31.0
    _run(board_issues.state())
    assert gh.calls == 2


def test_un_succes_leve_la_pause():
    gh = _Gh(Unavailable("panne"), [_issue(1)], [_issue(1), _issue(2)])
    board_issues = Issues(".", ttl=0, call=gh)
    _run(board_issues.state())                       # panne
    _run(board_issues.state(force=True))             # succès: la pause tombe
    assert _run(board_issues.state())["total"] == 2  # l'appel suivant repart
