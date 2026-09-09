"""Le prompt système suit les fichiers de config sans redémarrage du service."""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.prompt_builder import PromptBuilder  # noqa: E402


@pytest.fixture
def config(tmp_path):
    for name, text in (("PERSONA.md", "PERSONA-V1"), ("MEMORY.md", "MEM"), ("USER.md", "USER")):
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_une_edition_apparait_dans_le_prompt_suivant(config):
    builder = PromptBuilder(str(config))
    assert "PERSONA-V1" in builder.build_system_prompt()

    # os.replace: c'est ainsi que le daemon terminal écrit le fichier.
    tmp = config / "PERSONA.md.tmp"
    tmp.write_text("PERSONA-V2", encoding="utf-8")
    os.replace(tmp, config / "PERSONA.md")

    prompt = builder.build_system_prompt()
    assert "PERSONA-V2" in prompt and "PERSONA-V1" not in prompt


def test_un_fichier_inchange_nest_pas_relu(config, monkeypatch):
    builder = PromptBuilder(str(config))
    lectures = 0
    vrai_open = open

    def compte(*args, **kwargs):
        nonlocal lectures
        lectures += 1
        return vrai_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", compte)
    for _ in range(5):
        builder.build_system_prompt()
    assert lectures == 0


def test_un_fichier_efface_garde_le_dernier_contenu_connu(config):
    """Un prompt système amputé est pire qu'un prompt périmé."""
    builder = PromptBuilder(str(config))
    (config / "PERSONA.md").unlink()
    assert "PERSONA-V1" in builder.build_system_prompt()


def test_le_cout_des_stat_reste_negligeable(config):
    """La cible est 200 ms de TTFT: trois stat() ne doivent pas peser."""
    builder = PromptBuilder(str(config))
    builder.build_system_prompt()
    depart = time.perf_counter()
    for _ in range(1000):
        builder.build_system_prompt()
    par_appel_us = (time.perf_counter() - depart) / 1000 * 1e6
    assert par_appel_us < 200, f"{par_appel_us:.0f} µs par prompt"
