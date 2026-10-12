"""Choix du message auquel le LLM répond quand plusieurs attendent (file « le dernier gagne »).

Vit hors de main.py pour être testable sans NATS, sans TUI et sans interface d'inférence.
Voir docs/audits/io_oreilles-discord-multi-speaker-2026-10-12.md, P1.
"""


def take_latest(batch: list[dict]) -> tuple[list[dict], dict | None]:
    """Sépare la file en (messages à écrire en mémoire sans réponse, message à répondre).

    Le dernier message utilisateur gagne. Un prompt proactif cède toujours sa place à un
    message utilisateur et n'entre jamais dans l'historique comme message sauté. Un prompt
    proactif qui a attendu (`_stale`) est jeté : un tour a eu lieu pendant l'attente, donc
    l'ennui qui l'a produit ne tient plus.
    """
    users = [m for m in batch if not m.get("source")]
    if users:
        return users[:-1], users[-1]
    fresh = [m for m in batch if not m.get("_stale")]
    return [], (fresh[-1] if fresh else None)


def is_for_turn(event: dict, correlation_id: str) -> bool:
    """Un événement `io.voice.speak.*` appartient-il au tour en cours ?

    Un événement sans `correlation_id` (un publieur pas encore à jour) compte pour le tour
    en cours : mieux vaut libérer la file un peu tôt que l'attendre jusqu'au plafond.
    """
    return event.get("correlation_id", "") in ("", correlation_id)
