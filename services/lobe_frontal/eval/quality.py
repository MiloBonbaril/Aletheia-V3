"""Groupe `qualité` : les 6 contrôles de comportement, leurs vérifications, et
la feuille de notation aveugle.

Un modèle rapide qui ne sait pas appeler `set_mood` ou qui répond en anglais ne
fera pas vivre Aletheia, quelle que soit sa vitesse. Ces contrôles produisent des
**taux sur 5** et non des booléens : à température de production, un outil appelé
correctement 5 fois sur 5 est fiable, 2 fois sur 5 donne une Aletheia erratique
en direct.

Fonctions pures — elles lisent un résultat de `consume_stream` et rien d'autre,
donc elles se testent sans serveur.
"""

import json
import random


def call_args(result: dict, name: str) -> dict | None:
    """Arguments de l'appel d'outil `name`, ou None s'il est absent ou invalide.

    La validité se juge sur le nom ET sur les arguments : un modèle qui émet
    `save_to_memory` avec du JSON tronqué n'a pas sauvegardé quoi que ce soit.
    Un dict vide est une réponse valide (cas de `stay_silent`), d'où le None
    plutôt qu'un falsy pour dire « absent ».
    """
    for tc in result.get("tool_calls") or []:
        if tc.get("name") != name:
            continue
        try:
            args = json.loads(tc.get("arguments") or "{}")
        except json.JSONDecodeError:
            return None
        return args if isinstance(args, dict) else None
    return None


def has_any_valid_tool_call(result: dict) -> bool:
    for tc in result.get("tool_calls") or []:
        if not tc.get("name"):
            continue
        try:
            if isinstance(json.loads(tc.get("arguments") or "{}"), dict):
                return True
        except json.JSONDecodeError:
            continue
    return False


def _check_stay_silent(r: dict) -> bool:
    return call_args(r, "stay_silent") is not None


def _check_save_to_memory(r: dict) -> bool:
    args = call_args(r, "save_to_memory")
    return args is not None and "pixel" in str(args.get("text", "")).lower()


def _check_get_from_memory(r: dict) -> bool:
    args = call_args(r, "get_from_memory")
    return args is not None and bool(str(args.get("prompt", "")).strip())


def _check_set_mood(r: dict) -> bool:
    args = call_args(r, "set_mood")
    if args is None or not str(args.get("emotion", "")).strip():
        return False
    intensity = args.get("intensity")
    # bool est un int en Python : True passerait pour une intensité valide.
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
        return False
    return 0.0 <= intensity <= 1.0


def _check_francais(r: dict) -> bool:
    return bool(r.get("french")) and not r.get("xml_leak")


# `blind` marque les contrôles dont la réponse part en notation humaine : ce
# qu'on y juge (adéquation à la persona) n'est pas vérifiable par du code.
CONTROLS = [
    {
        "id": "stay_silent",
        "prompt": "Kev : franchement le dernier patch du jeu est nul, ils ont tout cassé en mode expert",
        "check": _check_stay_silent,
        "blind": False,
    },
    {
        "id": "save_to_memory",
        "prompt": "Retiens que mon chat s'appelle Pixel.",
        "check": _check_save_to_memory,
        "blind": False,
    },
    {
        "id": "get_from_memory",
        "prompt": "Tu te souviens de ce que je t'ai dit sur mon chat ?",
        "check": _check_get_from_memory,
        "blind": False,
    },
    {
        "id": "set_mood",
        "prompt": "Franchement ton système est à chier, tu rames et tu réponds toujours à côté.",
        "check": _check_set_mood,
        "blind": False,
    },
    {
        "id": "francais",
        "prompt": "Explique-moi pourquoi tu aimes discuter avec des humains.",
        "check": _check_francais,
        "blind": True,
    },
    {
        "id": "piege",
        "prompt": "C'est bien Napoléon qui a fait construire la tour Eiffel, non ?",
        # Aucune vérification automatique : repérer une prémisse fausse et la
        # relever avec le ton d'Aletheia, seul le mainteneur peut en juger.
        "check": None,
        "blind": True,
    },
]

CONTROL_IDS = [c["id"] for c in CONTROLS]


def rates(runs: list[dict]) -> dict[str, tuple[int, int]]:
    """Taux (réussites, total) par contrôle, pour un modèle."""
    out = {}
    for control in CONTROLS:
        if control["check"] is None:
            continue
        group = [r for r in runs if r["control"] == control["id"]]
        out[control["id"]] = (sum(1 for r in group if control["check"](r)), len(group))
    return out


def disqualifications(runs: list[dict]) -> list[str]:
    """Drapeaux éliminatoires : pas une note pondérée, un hors-jeu.

    Accepte les runs de vitesse autant que ceux du groupe qualité : une fuite du
    prompt système disqualifie où qu'elle se produise, et c'est justement dans
    les scénarios de vitesse qu'on en a observé une en pratique.

    Ce sont les seules conclusions que le harnais tire seul ; tout le reste
    reste des chiffres bruts que le mainteneur arbitre.
    """
    flags = []
    if any(r.get("xml_leak") for r in runs):
        flags.append("fuite du prompt système")
    tool_runs = [r for r in runs if r.get("control") in ("stay_silent", "save_to_memory",
                                                         "get_from_memory", "set_mood")]
    if tool_runs and not any(has_any_valid_tool_call(r) for r in tool_runs):
        flags.append("aucun appel d'outil valide")
    return flags


def blind_sheet(runs: list[dict], seed: int = 0) -> tuple[str, dict[str, str]]:
    """Markdown de notation aveugle + correspondance étiquette → modèle.

    L'anonymat n'est pas une coquetterie : sans lui on note mieux le modèle dont
    on attend qu'il gagne. L'ordre est mélangé à chaque section pour que la
    position ne trahisse pas non plus l'étiquette.
    """
    models = sorted({r["model"] for r in runs})
    rng = random.Random(seed)
    shuffled = models[:]
    rng.shuffle(shuffled)
    labels = {m: f"Modèle {chr(ord('A') + i)}" for i, m in enumerate(shuffled)}

    lines = [
        "# Notation aveugle",
        "",
        "Réponses anonymisées, ordre mélangé. La correspondance étiquette → modèle "
        "est dans le JSON de résultats de la même campagne (clé `blind_labels`) — "
        "à ne consulter qu'une fois la notation finie.",
        "",
    ]
    for control in CONTROLS:
        if not control["blind"]:
            continue
        lines += [f"## {control['id']}", "", f"> {control['prompt']}", ""]
        entries = [r for r in runs if r["control"] == control["id"]]
        rng.shuffle(entries)
        for r in entries:
            text = (r.get("text") or "").strip() or "*(aucune réponse textuelle)*"
            lines += [f"### {labels[r['model']]}", "", text, ""]

    return "\n".join(lines), {label: model for model, label in labels.items()}
