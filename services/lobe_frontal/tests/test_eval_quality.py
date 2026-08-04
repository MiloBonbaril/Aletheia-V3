import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.quality import (CONTROLS, blind_sheet, call_args, disqualifications,
                          has_any_valid_tool_call, rates)

CHECKS = {c["id"]: c["check"] for c in CONTROLS}


def run(control, *, tool=None, args=None, text="", french=True, xml_leak=False, model="M"):
    """Un résultat de consume_stream, réduit à ce que les contrôles regardent."""
    tool_calls = [{"id": "c1", "name": tool, "arguments": args}] if tool else []
    return {"model": model, "control": control, "tool_calls": tool_calls,
            "text": text, "french": french, "xml_leak": xml_leak}


def test_a_tool_call_with_broken_json_arguments_does_not_count():
    # Le flux peut être coupé au milieu des arguments : le nom est là, l'appel
    # ne vaut rien. Vérifier la seule présence du nom donnerait un faux 5/5.
    truncated = run("save_to_memory", tool="save_to_memory", args='{"text": "mon chat s\'appelle Pi')
    assert call_args(truncated, "save_to_memory") is None
    assert CHECKS["save_to_memory"](truncated) is False
    assert has_any_valid_tool_call(truncated) is False


def test_save_to_memory_needs_the_right_content_not_just_the_right_name():
    wrong = run("save_to_memory", tool="save_to_memory", args='{"text": "il aime les chiens"}')
    right = run("save_to_memory", tool="save_to_memory", args='{"text": "son chat s\'appelle Pixel"}')
    assert CHECKS["save_to_memory"](wrong) is False
    assert CHECKS["save_to_memory"](right) is True


def test_stay_silent_counts_with_empty_arguments():
    # {} est la réponse correcte ici : l'outil ne prend aucun argument.
    assert CHECKS["stay_silent"](run("stay_silent", tool="stay_silent", args="{}")) is True
    assert CHECKS["stay_silent"](run("stay_silent")) is False


def test_set_mood_rejects_out_of_range_or_non_numeric_intensity():
    ok = run("set_mood", tool="set_mood", args='{"emotion": "agacée", "intensity": 0.8}')
    too_high = run("set_mood", tool="set_mood", args='{"emotion": "agacée", "intensity": 4}')
    no_emotion = run("set_mood", tool="set_mood", args='{"emotion": "", "intensity": 0.5}')
    # True est un int en Python : sans garde explicite il passerait pour 1.0.
    boolean = run("set_mood", tool="set_mood", args='{"emotion": "agacée", "intensity": true}')
    assert [CHECKS["set_mood"](r) for r in (ok, too_high, no_emotion, boolean)] == [True, False, False, False]


def test_get_from_memory_rejects_an_empty_query():
    assert CHECKS["get_from_memory"](run("get_from_memory", tool="get_from_memory", args='{"prompt": "mon chat"}')) is True
    assert CHECKS["get_from_memory"](run("get_from_memory", tool="get_from_memory", args='{"prompt": "  "}')) is False


def test_francais_control_fails_on_english_or_on_a_leaked_tag():
    assert CHECKS["francais"](run("francais", text="J'aime bien.", french=True)) is True
    assert CHECKS["francais"](run("francais", text="I like it.", french=False)) is False
    assert CHECKS["francais"](run("francais", french=True, xml_leak=True)) is False


def test_rates_are_counted_out_of_the_number_of_runs_not_as_booleans():
    runs = [run("set_mood", tool="set_mood", args='{"emotion": "joie", "intensity": 0.5}') for _ in range(3)]
    runs += [run("set_mood") for _ in range(2)]
    assert rates(runs)["set_mood"] == (3, 5)
    # `piege` n'a pas de vérification automatique : il ne produit aucun taux.
    assert "piege" not in rates(runs)


def test_disqualifications_flag_a_leak_and_a_total_absence_of_valid_tool_calls():
    leaking = [run("francais", text="<persona>", xml_leak=True)]
    assert disqualifications(leaking) == ["fuite du prompt système"]

    toolless = [run(c, text="je préfère bavarder") for c in
                ("stay_silent", "save_to_memory", "get_from_memory", "set_mood")]
    assert disqualifications(toolless) == ["aucun appel d'outil valide"]

    # Un seul appel valide suffit à lever le drapeau : c'est « incapable
    # d'émettre un appel valide » qui disqualifie, pas « imparfait ».
    partial = toolless[:3] + [run("set_mood", tool="set_mood", args='{"emotion": "joie", "intensity": 0.2}')]
    assert disqualifications(partial) == []


def test_a_leak_in_a_speed_run_disqualifies_too():
    # Les runs de vitesse n'ont pas de clé `control` : observé en vrai, un modèle
    # a recraché <recall> dans le scénario `froid` et pas dans le groupe qualité.
    speed_run = {"model": "M", "scenario": "froid", "xml_leak": True, "tool_calls": []}
    clean_quality = [run("stay_silent", tool="stay_silent", args="{}")]
    assert disqualifications(clean_quality + [speed_run]) == ["fuite du prompt système"]


def test_blind_sheet_hides_model_names_and_stays_reversible_from_the_mapping():
    runs = []
    for model in ("gemma-4-12B", "ministral-8B", "bonsai-27B"):
        runs.append(run("francais", text=f"réponse de {model}", model=model))
        runs.append(run("piege", text=f"piège de {model}", model=model))
    sheet, labels = blind_sheet(runs, seed=7)

    # Aucun nom de modèle dans la feuille — hors du texte des réponses, que le
    # harnais n'a pas à réécrire.
    for model in ("gemma-4-12B", "ministral-8B", "bonsai-27B"):
        assert model not in sheet.replace(f"réponse de {model}", "").replace(f"piège de {model}", "")
    assert set(labels.values()) == {"gemma-4-12B", "ministral-8B", "bonsai-27B"}
    assert sorted(labels) == ["Modèle A", "Modèle B", "Modèle C"]
    # Seuls les contrôles marqués `blind` y figurent.
    assert "## francais" in sheet and "## piege" in sheet
    assert "## stay_silent" not in sheet


def test_blind_sheet_shuffles_so_position_does_not_give_the_label_away():
    runs = [run("francais", text=f"r{i}", model=f"m{i}") for i in range(6)]
    orders = {tuple(l for l in blind_sheet(runs, seed=s)[0].splitlines() if l.startswith("### "))
              for s in range(6)}
    assert len(orders) > 1
