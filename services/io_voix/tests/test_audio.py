import base64
import io
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio import encode_wav_b64


def test_encode_wav_b64_round_trips_through_soundfile():
    samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)

    encoded = encode_wav_b64(samples, 22050)
    decoded, sample_rate = sf.read(io.BytesIO(base64.b64decode(encoded)), dtype="float32")

    assert sample_rate == 22050
    np.testing.assert_allclose(decoded, samples, atol=1e-3)


def test_encode_wav_b64_returns_valid_base64():
    samples = np.zeros(100, dtype=np.float32)
    encoded = encode_wav_b64(samples, 22050)
    assert base64.b64decode(encoded)  # doesn't raise
