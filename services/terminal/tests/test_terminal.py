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
