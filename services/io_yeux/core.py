"""Cœur de décision pur de io_yeux (sans I/O) : facile à tester, aucune dépendance NATS ni D-Bus."""

import numpy as np
from PIL import Image

THUMB_SIZE = (160, 90)
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


def decide(reference: np.ndarray | None, thumb: np.ndarray, threshold: float) -> str | None:
    """Rend le déclencheur de l'observation, ou None s'il n'y a rien à observer.

    `reference` est la vignette de la dernière observation réussie (None avant la première).
    """
    if reference is None or difference(reference, thumb) > threshold:
        return "change"
    return None


def after_observation(reference: np.ndarray | None, thumb: np.ndarray, ok: bool) -> np.ndarray | None:
    """La référence ne change qu'après une observation réussie : après un échec, l'écran reste
    « différent » et la capture suivante sert de nouvel essai."""
    return thumb if ok else reference


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
