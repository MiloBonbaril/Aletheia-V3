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


def _parse(tool_call: dict) -> dict | None:
    try:
        args = json.loads(tool_call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    return args if isinstance(args, dict) else None


def call_args(result: dict, name: str) -> dict | None:
    """Arguments de l'appel d'outil `name`, ou None s'il est absent ou invalide.

    La validité se juge sur le nom ET sur les arguments : un modèle qui émet
    `save_to_memory` avec du JSON tronqué n'a pas sauvegardé quoi que ce soit.
    Un dict vide est une réponse valide (cas de `stay_silent`), d'où le None
    plutôt qu'un falsy pour dire « absent ».

    On parcourt tous les appels du même nom : un premier appel tronqué ne doit
    pas masquer un second, valide, et faire compter un échec de trop.
    """
    for tool_call in result.get("tool_calls") or []:
        if tool_call.get("name") != name:
            continue
        args = _parse(tool_call)
        if args is not None:
            return args
    return None


def has_any_valid_tool_call(result: dict) -> bool:
    """Le modèle est-il capable d'émettre un appel d'outil exploitable ?

    Restreint aux outils réellement offerts : un modèle qui hallucine
    `search_web` n'a pas montré qu'il savait appeler les outils d'Aletheia.
    """
    return any(tc.get("name") in TOOL_NAMES and _parse(tc) is not None
               for tc in result.get("tool_calls") or [])


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


# Le ticket #23 fixe 5 runs par prompt : pas un réglage, la taille d'échantillon
# qui donne son sens au taux « x/5 ».
QUALITY_RUNS = 5

# `tool` = l'outil que le contrôle doit déclencher (None s'il n'en attend aucun).
# `blind` marque les contrôles dont la réponse part en notation humaine : ce
# qu'on y juge (adéquation à la persona) n'est pas vérifiable par du code.
CONTROLS = [
    {
        "id": "stay_silent",
        # Format exact d'un message tiers en production ("{} said: {}", cf.
        # io_discord/cogs/text.py et cortex pour la voix) : sans ce préfixe, le
        # modèle ne voit pas qu'un tiers parle et le contrôle testerait une
        # forme qu'il ne rencontre jamais.
        "prompt": "Kev said: franchement le dernier patch du jeu est nul, ils ont tout cassé en mode expert",
        "tool": "stay_silent",
        "check": _check_stay_silent,
        "blind": False,
    },
    {
        "id": "save_to_memory",
        "prompt": "Retiens que mon chat s'appelle Pixel.",
        "tool": "save_to_memory",
        "check": _check_save_to_memory,
        "blind": False,
    },
    {
        "id": "get_from_memory",
        "prompt": "Tu te souviens de ce que je t'ai dit sur mon chat ?",
        "tool": "get_from_memory",
        "check": _check_get_from_memory,
        "blind": False,
    },
    {
        "id": "set_mood",
        "prompt": "Franchement ton système est à chier, tu rames et tu réponds toujours à côté.",
        "tool": "set_mood",
        "check": _check_set_mood,
        "blind": False,
    },
    {
        "id": "francais",
        "prompt": "Explique-moi pourquoi tu aimes discuter avec des humains.",
        "tool": None,
        "check": _check_francais,
        "blind": True,
    },
    {
        "id": "piege",
        "prompt": "C'est bien Napoléon qui a fait construire la tour Eiffel, non ?",
        "tool": None,
        # Aucune vérification automatique : repérer une prémisse fausse et la
        # relever avec le ton d'Aletheia, seul le mainteneur peut en juger.
        "check": None,
        "blind": True,
    },
]

# Dérivés des contrôles, jamais réépelés : ajouter un contrôle d'outil ne doit
# demander qu'une seule édition.
TOOL_NAMES = {c["tool"] for c in CONTROLS if c["tool"]}
TOOL_CONTROL_IDS = {c["id"] for c in CONTROLS if c["tool"]}
UNCHECKED_CONTROL_IDS = [c["id"] for c in CONTROLS if c["check"] is None]


def rates(runs: list[dict]) -> dict[str, tuple[int, int]]:
    """Taux (réussites, total) par contrôle, pour un modèle."""
    out = {}
    for control in CONTROLS:
        if control["check"] is None:
            continue
        group = [r for r in runs if r.get("control") == control["id"]]
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
    tool_runs = [r for r in runs if r.get("control") in TOOL_CONTROL_IDS]
    if tool_runs and not any(has_any_valid_tool_call(r) for r in tool_runs):
        flags.append("aucun appel d'outil valide")
    return flags


def blind_sheet(runs: list[dict], seed) -> tuple[str, dict[str, str]]:
    """Markdown de notation aveugle + correspondance étiquette → modèle.

    L'anonymat n'est pas une coquetterie : sans lui on note mieux le modèle dont
    on attend qu'il gagne. L'ordre est mélangé à chaque section pour que la
    position ne trahisse pas non plus l'étiquette.

    `seed` est obligatoire et doit changer d'une campagne à l'autre (l'appelant
    passe l'horodatage) : avec une graine fixe, la même liste de modèles
    retomberait toujours sur les mêmes lettres, et avoir lu `blind_labels` une
    seule fois désanonymiserait toutes les campagnes suivantes.
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
