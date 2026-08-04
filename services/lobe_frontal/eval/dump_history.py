"""Dump manuel de l'historique réel d'hippocampe vers les fixtures du banc.

    cd services/lobe_frontal && python eval/dump_history.py [--typique N] [--charge N]

Le cwd compte : `database.py` d'hippocampe appelle `load_dotenv()` sans chemin,
donc lancé d'ailleurs il lirait un autre `.env`. Lancé d'ici il retombe sur les
URLs Postgres/Qdrant par défaut, celles du docker-compose d'hippocampe.

Ce script est le seul point du harnais qui dépende d'hippocampe : il tire
`sqlalchemy`, `asyncpg`, `qdrant-client` et `sentence-transformers` via
`services/hippocampe/requirements.txt`, absents de celui de lobe_frontal — c'est
voulu, le banc lui-même n'en a pas besoin. Installe-les depuis hippocampe si le
dump échoue à l'import.

À relancer à la main quand on veut rafraîchir les fixtures — jamais pendant une
campagne de mesure. Le banc, lui, ne lit que le JSON produit ici : il tourne
avec Postgres et Qdrant éteints.

Le JSON produit n'est pas versionné (cf. .gitignore) : c'est un dump verbatim de
vraies conversations Discord — pseudos, identifiants et propos de tiers — et le
dépôt est public. Chacun dumpe le sien.

L'historique vient du vrai Postgres et pas d'un texte inventé : la distribution
des longueurs de tour et le mélange français/anglais/appels d'outils d'une vraie
conversation sont exactement ce qui charge le prefill, et on ne les reproduit
pas fidèlement à la main.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

LOBE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LOBE_DIR)
# On réutilise les helpers d'hippocampe plutôt que de réécrire la requête : le
# préfixe d'horodatage, la réparation des rôles `tool` et le filtrage de l'audio
# font partie de ce que la production envoie réellement au modèle.
sys.path.insert(0, os.path.join(os.path.dirname(LOBE_DIR), "hippocampe"))

from eval.fixtures import FIXTURE_PATH, SHARED_PROMPT


def strip_images(history: list[dict]) -> list[dict]:
    """Retire les images des fixtures : tous les candidats n'ont pas de projecteur
    multimodal, on ne pourrait pas les comparer sur le même jeu. L'audio est déjà
    remplacé par un marqueur en amont, par get_recent_history."""
    for msg in history:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        msg["content"] = [
            {"type": "text", "text": "[image]"} if item.get("type") == "image_url" else item
            for item in content
            if isinstance(item, dict)
        ]
    return history


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Défauts calés sur les régimes que les scénarios existent pour sonder
    # (~6k et ~16k tokens), mesurés contre un vrai tokenizer. Les tours réels de
    # cette base font ~38 tokens, bien moins que l'estimation « ~10 / ~60 tours »
    # du ticket #22 — c'est le nombre de tokens qui porte le raisonnement sur le
    # prefill, donc c'est lui qu'on vise. À réajuster si l'historique change de
    # nature : le banc rapporte prompt_tokens à chaque campagne.
    parser.add_argument("--typique", type=int, default=120, help="messages pour le scénario typique")
    parser.add_argument("--charge", type=int, default=380, help="messages pour le scénario chargé")
    args = parser.parse_args()

    from database import get_recent_history
    from rag_manager import rag_manager

    payload = {
        "dumped_at": datetime.now().isoformat(timespec="seconds"),
        "final_user_message": SHARED_PROMPT,
        "scenarios": {},
    }
    # `typique` prend le RAG tel que la production le rend (limite et seuil de
    # pertinence par défaut). `chargé` prend les 10 souvenirs les plus proches
    # sans seuil : le contenu reste réel, mais on ne dépend plus de la taille
    # actuelle du corpus pour obtenir le gros <recall> d'une longue session —
    # c'est un scénario de charge, et le seuil est enregistré dans la fixture.
    for name, messages, rag_limit, threshold in (
        ("typique", args.typique, 3, None),
        ("charge", args.charge, 10, 0.0),
    ):
        history = strip_images(await get_recent_history(messages))
        rag_results = await rag_manager.query_memory_async(SHARED_PROMPT, limit=rag_limit, threshold=threshold)
        payload["scenarios"][name] = {
            "history": history,
            "rag_results": rag_results,
            "rag_limit": rag_limit,
            "rag_threshold": threshold,
        }
        print(f"{name}: {len(history)} messages, recall {len(rag_results)} caractères")

    if not any(s["history"] for s in payload["scenarios"].values()):
        raise SystemExit("Aucun historique récupéré — Postgres est-il démarré ?")

    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"→ {FIXTURE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
