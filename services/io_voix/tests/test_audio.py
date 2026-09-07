import base64
import io
import os
import re
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio import AudioChunk, encode_wav_b64, queue_fragment

SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_encode_wav_b64_round_trips_through_soundfile():
    samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)

    encoded = encode_wav_b64(samples, 44100)
    decoded, sample_rate = sf.read(io.BytesIO(base64.b64decode(encoded)), dtype="float32")

    assert sample_rate == 44100
    np.testing.assert_allclose(decoded, samples, atol=1e-3)


def test_encode_wav_b64_returns_valid_base64():
    samples = np.zeros(100, dtype=np.float32)
    encoded = encode_wav_b64(samples, 44100)
    assert base64.b64decode(encoded)  # doesn't raise


def test_playback_rate_comes_from_the_model_not_a_literal():
    """La fréquence de lecture doit venir du modèle, jamais d'une valeur recopiée.

    Une valeur en dur avait désaccordé la lecture de 9 % (22050 contre 24000) : voix
    grave et énoncés d'autant plus longs. Le modèle rend maintenant du 44,1 kHz, donc
    le même piège coûterait bien plus cher. `engine.py` lit `codec_sample_rate` dans la
    config et `main.py` se la fait passer en paramètre : ce test échoue si quelqu'un
    recode une fréquence à la main dans l'un ou l'autre.
    """
    engine_source = open(os.path.join(SERVICE_DIR, "engine.py"), encoding="utf-8").read()
    main_source = open(os.path.join(SERVICE_DIR, "main.py"), encoding="utf-8").read()

    assert "model.config.codec_sample_rate" in engine_source
    for source, name in ((engine_source, "engine.py"), (main_source, "main.py")):
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for rate in ("44100", "22050", "24000", "48000"):
            assert not re.search(rf"\b{rate}\b", code), f"{rate} en dur dans {name}"


def _chunk(size):
    return np.full(size, 0.1, dtype=np.float32)


def test_queue_fragment_brackets_the_whole_fragment_once():
    """Un seul start et un seul end, quel que soit le nombre de tranches."""
    queued = []

    pieces = queue_fragment([_chunk(4), _chunk(8), _chunk(2)], 7, "salut", False, queued.append)

    assert len(pieces) == 3
    assert len(queued) == 4  # trois tranches + le marqueur de fin
    assert [item.first for item in queued] == [True, False, False, False]
    assert [item.final for item in queued] == [False, False, False, True]
    assert all(item.sequence == 7 and item.text == "salut" for item in queued)
    assert queued[-1].samples.size == 0


def test_queue_fragment_emits_chunks_before_the_stream_ends():
    """La mise en file doit être incrémentale : c'est tout l'intérêt du streaming.

    Si `queue_fragment` collectait d'abord toutes les tranches, le TTFA repartirait au
    niveau du fragment entier et l'on perdrait le gain mesuré (2215 ms -> 171 ms).
    """
    seen = []

    def stream():
        yield _chunk(4)
        # Au moment où la deuxième tranche est demandée, la première doit déjà être en
        # file, donc déjà jouable.
        assert len(seen) == 1
        yield _chunk(4)

    queue_fragment(stream(), 1, "x", False, seen.append)
    assert len(seen) == 3


def test_queue_fragment_still_closes_the_fragment_when_inference_fails():
    """Sans marqueur de fin, `io.voice.speak.end` manquerait et le benchmark bloquerait."""
    queued = []

    def broken():
        yield _chunk(4)
        raise RuntimeError("CUDA out of memory")

    try:
        queue_fragment(broken(), 3, "x", True, queued.append)
    except RuntimeError:
        pass

    assert queued[-1].final is True
    assert queued[-1].is_last is True
    assert sum(item.first for item in queued) == 1


def test_queue_fragment_marks_an_empty_stream_as_both_ends():
    """Une synthèse qui ne rend rien doit quand même ouvrir et fermer le fragment."""
    queued = []

    pieces = queue_fragment(iter(()), 2, "", True, queued.append)

    assert pieces == []
    assert len(queued) == 1
    assert queued[0] == AudioChunk(queued[0].samples, 2, "", True, True, True)
