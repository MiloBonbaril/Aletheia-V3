"""Cœur pur de la mise en file et de l'encodage audio (sans I/O NATS/sounddevice/GPU)."""

import base64
import io
from typing import NamedTuple

import numpy as np
import soundfile as sf

EMPTY = np.zeros(0, dtype=np.float32)


class AudioChunk(NamedTuple):
    """Une tranche d'un fragment, dans l'ordre de lecture.

    `first` et `final` bornent le fragment : ils portent `io.voice.speak.start` et
    `io.voice.speak.end`, qui encadrent la lecture entière et non chaque tranche.
    Le chunk final est vide — il ne sert qu'à publier la fin une fois le son écoulé.
    """

    samples: np.ndarray
    sequence: int
    text: str
    is_last: bool
    first: bool
    final: bool


def queue_fragment(chunks, sequence: int, text: str, is_last: bool, put) -> list:
    """Met en file les tranches d'un fragment au fur et à mesure, bornées start/end.

    `put` est appelé dès qu'une tranche est prête, donc la carte son commence à jouer
    avant que la synthèse du fragment soit finie. Rend les tranches émises, que
    l'appelant concatène pour publier le WAV complet sur NATS.
    """
    pieces = []
    try:
        for chunk in chunks:
            put(AudioChunk(chunk, sequence, text, is_last, not pieces, False))
            pieces.append(chunk)
    finally:
        # Le marqueur de fin part même si la synthèse casse en cours de route. Sinon
        # `io.voice.speak.end` manquerait, et le benchmark attendrait un événement qui
        # n'arrive jamais.
        put(AudioChunk(EMPTY, sequence, text, is_last, not pieces, True))
    return pieces


def encode_wav_b64(samples, sample_rate: int) -> str:
    """Encode un tableau de samples float32 mono en WAV base64, pour publication NATS."""
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    return base64.b64encode(buf.getvalue()).decode()
