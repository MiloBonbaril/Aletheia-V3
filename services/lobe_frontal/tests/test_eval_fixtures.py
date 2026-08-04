import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.dump_history import strip_images
from eval.fixtures import FIXTURE_PATH, SHARED_PROMPT, _build, load_scenarios
from src.prompt_builder import PromptBuilder


def last_user_text(messages: list[dict]) -> str:
    """Le dernier message utilisateur, que build() l'ait laissé en texte ou
    compacté en liste de blocs avec le tour précédent."""
    content = messages[-1]["content"]
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content)
    return content


def test_building_twice_from_the_same_fixture_gives_an_identical_prompt():
    pb = PromptBuilder()
    # Un rôle `tool` déclenche la réparation d'historique de PromptBuilder, qui
    # réécrit les dicts qu'on lui passe — et y replie le message courant.
    history = [
        {"role": "user", "content": "tu te souviens ?"},
        {"role": "tool", "content": "souvenir récupéré"},
        {"role": "assistant", "content": "oui."},
    ]
    first = _build(pb, history, "un rappel")
    second = _build(pb, history, "un rappel")

    # Sans copie défensive, le run 2 repartirait d'un historique déjà réécrit par
    # le run 1 : le prompt gonflerait à chaque itération et prompt_tokens avec.
    assert json.dumps(first) == json.dumps(second)
    assert history[1] == {"role": "tool", "content": "souvenir récupéré"}


def test_every_scenario_ends_on_the_same_user_message():
    pb = PromptBuilder()
    scenarios, _ = load_scenarios()
    assert "froid" in scenarios
    for name, build in scenarios.items():
        assert last_user_text(build(pb)).endswith(SHARED_PROMPT), name


def test_loaded_scenarios_grow_strictly_with_context_length():
    pb = PromptBuilder()
    scenarios, meta = load_scenarios()
    if meta is None:
        return  # dépôt sans fixtures dumpées : seul `froid` existe
    sizes = [len(json.dumps(scenarios[n](pb))) for n in ("froid", "typique", "charge")]
    assert sizes[0] < sizes[1] < sizes[2], sizes


def test_dumped_fixtures_carry_no_image_or_audio():
    if not os.path.exists(FIXTURE_PATH):
        return
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        raw = f.read()
    # Tous les candidats n'ont pas de projecteur multimodal : un jeu contenant
    # une image ne serait pas comparable d'un modèle à l'autre.
    assert "image_url" not in raw
    assert "input_audio" not in raw


def test_strip_images_replaces_them_but_keeps_text():
    history = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "regarde ça"},
            {"type": "image_url", "image_url": {"url": "https://cdn.discordapp.com/x.png"}},
        ],
    }]
    assert strip_images(history) == [{
        "role": "user",
        "content": [{"type": "text", "text": "regarde ça"}, {"type": "text", "text": "[image]"}],
    }]
