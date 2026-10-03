"""Cœur de décision pur de io_yeux (sans I/O) : facile à tester, aucune dépendance NATS ni D-Bus."""

import math
import re
from collections import Counter
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


MASK = "[masqué]"

# Préfixes connus. Le lookbehind évite « task-runner » (s, k, - au milieu d'un mot).
_KNOWN_SECRET = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[A-Z0-9]{12,}"
    r"|xox[bp]-[A-Za-z0-9\-]{10,}"
    r"|eyJ[A-Za-z0-9_\-.]{16,}"
    r")"
)
# Le mot de passe d'une URL de connexion : postgres://admin:<mot de passe>@hôte.
_URL_PASSWORD = re.compile(r"(://[^:/\s@]+:)[^@\s]+(?=@)")
# Candidat à forte entropie : une suite de 25 caractères au moins, sans espace.
_RUN = re.compile(r"[A-Za-z0-9_\-+/=]{25,}")
_ALNUM_BLOCK = re.compile(r"[A-Za-z0-9]+")


def _entropy(text: str) -> float:
    """Entropie de Shannon, en bits par caractère."""
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _looks_random(run: str) -> bool:
    """Mesuré : une clé base62 de 25 caractères donne 4,29, un nom de paquet 4,24. L'entropie
    seule ne les sépare pas. D'où deux règles : un bloc alphanumérique de 20 caractères au moins
    qui mêle chiffres et lettres (hash, clé base62), ou une entropie de 4,4 au moins (base64)."""
    for block in _ALNUM_BLOCK.findall(run):
        if len(block) >= 20 and any(c.isdigit() for c in block) and any(c.isalpha() for c in block):
            return True
    if run.startswith(("/", "~", ".")):
        return False  # un chemin : son entropie ne dit rien
    return _entropy(run) >= 4.4


def redact(text: str) -> str:
    """Remplace chaque secret probable par [masqué]. Le risque visé : la VTubeuse lit une clé
    à voix haute en stream, et hippocampe la persiste. Un texte ordinaire passe intact."""
    text = _KNOWN_SECRET.sub(MASK, text)
    text = _URL_PASSWORD.sub(lambda m: m.group(1) + MASK, text)
    return _RUN.sub(lambda m: MASK if _looks_random(m.group()) else m.group(), text)


def build_state(answer: dict, observed_at: int, trigger: str) -> dict:
    """Construit le payload io.vision.state depuis la réponse du VLM.

    Le json_schema borne déjà la sortie ; on réapplique les bornes au cas où le serveur
    l'ignorerait. Les secrets sont masqués ici, avant toute publication. Une réponse de mauvaise forme lève KeyError ou TypeError : l'observation échoue.
    """
    texts = answer["visible_text"]
    if not isinstance(texts, list):
        raise TypeError("visible_text n'est pas une liste")
    return {
        "observed_at": observed_at,
        "checked_at": observed_at,
        "trigger": trigger,
        # Masquer avant de couper : une clé coupée en deux échapperait aux motifs.
        "application": redact(str(answer["application"]))[:MAX_APPLICATION],
        "activity": redact(str(answer["activity"]))[:MAX_ACTIVITY],
        "visible_text": [redact(str(text))[:MAX_TEXT] for text in texts[:MAX_TEXTS]],
    }
