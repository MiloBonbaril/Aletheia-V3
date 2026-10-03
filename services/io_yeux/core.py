"""Cœur de décision pur de io_yeux (sans I/O) : facile à tester, aucune dépendance NATS ni D-Bus."""

from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

THUMB_SIZE = (160, 90)
# Au plus tard 30 s après cortex.interaction.started, la vision reprend même sans signal de fin.
SUSPENSION_TIMEOUT = 30.0
# io_voix compte comme actif s'il a publié un io.voice.speak.* dans les 10 dernières minutes.
VOICE_ACTIVE_WINDOW = 600.0
MAX_APPLICATION = 80
MAX_ACTIVITY = 200
MAX_TEXTS = 5
MAX_TEXT = 80


def thumbnail(image: Image.Image) -> np.ndarray:
    """Réduit la capture en niveaux de gris 160×90, valeurs sur 0..1."""
    small = image.resize(THUMB_SIZE, Image.Resampling.BOX).convert("L")
    return np.asarray(small, dtype=np.float32) / 255.0


def difference(a: np.ndarray, b: np.ndarray) -> float:
    """Différence absolue moyenne entre deux vignettes, sur 0..1."""
    return float(np.abs(a - b).mean())


def decide(reference: np.ndarray | None, thumb: np.ndarray, threshold: float,
           since_check: float, heartbeat: float, identical: float, suspended: bool) -> str | None:
    """Rend l'action à faire pour cette capture, ou None :

    - "change" : l'écran diffère de la référence au-delà du seuil → observer ;
    - "heartbeat" : heartbeat échu, écran légèrement différent → observer quand même, car une
      ligne d'erreur dans un terminal peut rester sous le seuil ;
    - "republish" : heartbeat échu, écran identique → pas de VLM, republier le dernier état avec
      un checked_at neuf.

    `reference` est la vignette de la dernière observation réussie (None avant la première).
    `since_check` est le temps écoulé depuis la dernière publication (observation ou republication).
    Sous `identical`, l'écran est identique à la référence : la description reste vraie.
    Pendant une suspension, rien : ni observation, ni heartbeat. À la fin, la capture suivante
    est comparée à la référence, donc une seule observation part, avec la frame la plus récente.
    """
    if suspended:
        return None
    if reference is None:
        return "change"
    diff = difference(reference, thumb)
    if diff > threshold:
        return "change"
    if since_check < heartbeat:
        return None
    return "republish" if diff < identical else "heartbeat"


def after_observation(reference: np.ndarray | None, thumb: np.ndarray, ok: bool) -> np.ndarray | None:
    """La référence ne change qu'après une observation réussie : après un échec, l'écran reste
    « différent » et la capture suivante sert de nouvel essai."""
    return thumb if ok else reference


@dataclass(frozen=True)
class Suspension:
    """La vision cède la place à la conversation : llama-server tourne en --parallel 1.

    Les temps sont en secondes, sur une horloge monotone.
    """
    started: float | None = None  # début de la suspension en cours, None sans suspension
    voice_seen: float | None = None  # dernier io.voice.speak.start ou .end reçu


def interaction_started(state: Suspension, now: float) -> Suspension:
    """cortex.interaction.started : suspend les observations."""
    return replace(state, started=now)


def voice_event(state: Suspension, now: float, is_last_end: bool = False) -> Suspension:
    """io.voice.speak.start ou .end. La fin de parole (`.end` avec is_last) lève la suspension :
    io_voix la publie toujours, même après stay_silent."""
    state = replace(state, voice_seen=now)
    return replace(state, started=None) if is_last_end else state


def fragment_end(state: Suspension, now: float) -> Suspension:
    """lobe.fragment_stream avec is_last. Lève la suspension seulement sans io_voix : sinon, le
    GPU synthétise encore la voix, et la fin de parole viendra plus tard."""
    voice_active = state.voice_seen is not None and now - state.voice_seen <= VOICE_ACTIVE_WINDOW
    return state if voice_active else replace(state, started=None)


def is_suspended(state: Suspension, now: float) -> bool:
    return state.started is not None and now - state.started < SUSPENSION_TIMEOUT


def build_state(answer: dict, observed_at: int, trigger: str) -> dict:
    """Construit le payload io.vision.state depuis la réponse du VLM.

    Le json_schema borne déjà la sortie ; on réapplique les bornes au cas où le serveur
    l'ignorerait. Une réponse de mauvaise forme lève KeyError ou TypeError : l'observation échoue.
    """
    texts = answer["visible_text"]
    if not isinstance(texts, list):
        raise TypeError("visible_text n'est pas une liste")
    return {
        "observed_at": observed_at,
        "checked_at": observed_at,
        "trigger": trigger,
        "application": str(answer["application"])[:MAX_APPLICATION],
        "activity": str(answer["activity"])[:MAX_ACTIVITY],
        "visible_text": [str(text)[:MAX_TEXT] for text in texts[:MAX_TEXTS]],
    }
