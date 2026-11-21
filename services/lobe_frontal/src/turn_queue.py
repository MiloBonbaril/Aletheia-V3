"""Choix du message auquel le LLM répond quand plusieurs attendent (file « le dernier gagne »).

Vit hors de main.py pour être testable sans NATS, sans TUI et sans interface d'inférence.
Voir docs/audits/io_oreilles-discord-multi-speaker-2026-10-12.md, P1.
"""


# La voix compte comme active si un io.voice.speak.* est arrivé dans les 10 dernières minutes.
# Même règle que io_yeux (io_yeux/core.py, VOICE_ACTIVE_WINDOW) : les deux services doivent
# s'accorder sur « Aletheia a une voix ».
VOICE_ACTIVE_WINDOW = 600.0


def voice_active(last_seen: float | None, now: float) -> bool:
    """Un lecteur joue-t-il la voix d'Aletheia ? Si oui, le tour attend la fin de sa parole,
    même quand le premier `io.voice.speak.start` tarde (synthèse d'un fragment entier en
    profil discord, file de lecture d'io_discord)."""
    return last_seen is not None and now - last_seen <= VOICE_ACTIVE_WINDOW


def apply_voice_activity(floor: set[str], event: dict) -> None:
    """Met à jour l'ensemble des locuteurs qui ont la parole avec un `io.user.voice.activity`.
    La parole est libre quand l'ensemble est vide. La clé est `speaker_id` : un pseudo change,
    l'identifiant Discord non. Le micro local n'a pas d'identifiant : il compte comme ""."""
    speaker = event.get("speaker_id") or ""
    if event.get("speaking"):
        floor.add(speaker)
    else:
        floor.discard(speaker)


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
