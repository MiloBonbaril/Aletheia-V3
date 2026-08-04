"""Ce que le banc envoie au modèle : le message final partagé, les fixtures
d'historique réel, et l'empreinte des fichiers de config qui pèsent sur le prefill.

Séparé de l'orchestration (eval/main.py) pour que dump_history.py puisse s'en
servir sans réveiller rich, openai ni la lecture d'environnement.
"""

import copy
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.prompt_builder import PromptBuilder

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(EVAL_DIR, "fixtures", "history.json")
CONFIG_DIR = os.path.join(os.path.dirname(EVAL_DIR), "config")

# Les trois scénarios partagent ce message : la seule variable entre eux doit
# être la longueur du contexte, pas ce qu'on demande au modèle.
# Question à rappel plutôt que salutation : une salutation générique ne passe
# pas le seuil de pertinence du RAG et donnerait un <recall> vide, donc un
# prefill plus léger que la réalité.
SHARED_PROMPT = "C'était quoi déjà le sujet de ton premier stream ?"

# Ordre d'affichage voulu : du plancher de latence au régime de fin de stream.
SCENARIO_ORDER = ("froid", "typique", "charge")


def config_hashes() -> dict[str, str]:
    """Empreinte de la persona, de la mémoire de base et des fiches utilisateur.

    Le prompt système est construit à la volée : éditer ces fichiers change le
    prefill. Sans ces hashes, deux campagnes menées à des semaines d'écart se
    compareraient comme si elles avaient mesuré la même chose.
    """
    hashes = {}
    for name in ("PERSONA.md", "MEMORY.md", "USER.md"):
        path = os.path.join(CONFIG_DIR, name)
        try:
            with open(path, "rb") as f:
                hashes[name] = hashlib.sha256(f.read()).hexdigest()[:16]
        except FileNotFoundError:
            hashes[name] = None
    return hashes


def build_messages(pb: PromptBuilder, prompt: str = SHARED_PROMPT,
                   history: list[dict] | None = None, rag_results: str = "") -> list[dict]:
    # deepcopy obligatoire : PromptBuilder.build répare les rôles `tool` et
    # compacte les tours consécutifs en mutant les dicts qu'on lui passe. Sans
    # copie, le run 2 partirait d'un historique déjà réécrit par le run 1 et on
    # mesurerait un prompt différent à chaque itération.
    return pb.build(prompt, history=copy.deepcopy(history), rag_results=rag_results)


def _read_fixture() -> dict | None:
    try:
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def typique_context() -> tuple[list[dict], str] | None:
    """Historique et <recall> du scénario `typique`, sur lesquels le groupe
    `qualité` monte ses 6 prompts : un contrôle de comportement joué hors des
    conditions réelles ne prédit rien."""
    data = _read_fixture()
    if not data or "typique" not in data.get("scenarios", {}):
        return None
    entry = data["scenarios"]["typique"]
    return entry["history"], entry["rag_results"]


def load_scenarios() -> tuple[dict, dict | None]:
    """Retourne (scénarios, méta-fixtures ou None).

    `froid` ne dépend d'aucune fixture. Les autres viennent du dump — qui n'est
    pas versionné (données Discord de tiers, dépôt public), donc le banc doit
    rester lançable sur un clone frais, avec `froid` seul.
    """
    scenarios = {"froid": lambda pb: build_messages(pb)}

    data = _read_fixture()
    if data is None:
        return scenarios, None

    # On itère ce que la fixture contient plutôt que de réépeler les noms de
    # scénarios ici : une fixture d'une autre génération donnerait un KeyError
    # là où l'absence de fichier est déjà gérée proprement.
    for name, entry in data["scenarios"].items():
        # Argument par défaut plutôt que fermeture : sinon tous les scénarios
        # captureraient la dernière valeur de la boucle.
        scenarios[name] = lambda pb, e=entry: build_messages(pb, SHARED_PROMPT, e["history"], e["rag_results"])

    meta = {
        "dumped_at": data.get("dumped_at"),
        "final_user_message": data.get("final_user_message"),
        "messages": {k: len(v["history"]) for k, v in data["scenarios"].items()},
        "recall_chars": {k: len(v["rag_results"]) for k, v in data["scenarios"].items()},
    }
    if data.get("final_user_message") != SHARED_PROMPT:
        # Le <recall> des fixtures a été interrogé avec le message d'alors :
        # changer SHARED_PROMPT sans redumper rend les scénarios incohérents.
        meta["stale_prompt"] = True
    return scenarios, meta
