import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import _strip_audio_blobs


def test_audio_blob_replaced_by_marker():
    content = [
        {"type": "text", "text": "Milo said (voice):"},
        {"type": "input_audio", "input_audio": {"data": "U" * 200_000, "format": "wav"}},
    ]
    stripped = _strip_audio_blobs(content)
    assert stripped[0] == {"type": "text", "text": "Milo said (voice):"}
    assert stripped[1] == {"type": "text", "text": "[message vocal]"}


def test_twenty_voice_turns_stay_under_nats_max_payload():
    """Le cas qui bloquait tout le pipeline : 20 tours vocaux en historique."""
    turn = [
        {"type": "text", "text": "Milo said (voice):"},
        {"type": "input_audio", "input_audio": {"data": "U" * 150_000, "format": "wav"}},
    ]
    history = [{"role": "user", "content": _strip_audio_blobs(turn)} for _ in range(20)]
    payload = json.dumps({"correlation_id": "x" * 36, "history": history}).encode()
    assert len(payload) < 1_048_576


def test_images_and_text_untouched():
    content = [
        {"type": "text", "text": "regarde"},
        {"type": "image_url", "image_url": {"url": "https://cdn.discordapp.com/x.png"}},
    ]
    assert _strip_audio_blobs(content) == content
