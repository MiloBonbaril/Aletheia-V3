"""Cœur pur de l'encodage audio (sans I/O NATS/sounddevice), testable sans matériel."""

import base64
import io

import soundfile as sf


def encode_wav_b64(samples, sample_rate: int) -> str:
    """Encode un tableau de samples float32 mono en WAV base64, pour publication NATS."""
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    return base64.b64encode(buf.getvalue()).decode()
