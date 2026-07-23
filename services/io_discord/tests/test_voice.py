import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice import (
    build_recording_attachments,
    decode_speak_audio,
    encode_voice_frame,
    format_recording_summary,
    pcm_to_mp3,
)


def fake_encoder(pcm: bytes) -> bytes:
    return b"mp3:" + bytes(pcm)


def test_build_recording_attachments_names_files_by_user_id():
    pcm_by_user = {111: b"aa", 222: b"bb"}

    result = build_recording_attachments(pcm_by_user, encoder=fake_encoder)

    assert [(name, f.read()) for name, f in result] == [
        ("111.mp3", b"mp3:aa"),
        ("222.mp3", b"mp3:bb"),
    ]


def test_build_recording_attachments_empty_when_nobody_spoke():
    assert build_recording_attachments({}) == []


def test_pcm_to_mp3_encodes_silence():
    # 0.1s of 48kHz s16le stereo silence through the real ffmpeg
    mp3 = pcm_to_mp3(b"\x00" * (48000 * 2 * 2 // 10))
    assert len(mp3) > 0


def test_format_recording_summary_lists_all_speakers():
    assert format_recording_summary([111, 222]) == "Recorded audio for: <@111>, <@222>."


def test_format_recording_summary_handles_no_speakers():
    assert format_recording_summary([]) == "No audio captured — did anyone speak?"


def test_encode_voice_frame_base64_encodes_pcm():
    assert encode_voice_frame(111, "Alice", b"\x01\x02") == {
        "speaker_id": "111",
        "speaker_name": "Alice",
        "pcm": "AQI=",
    }


def test_decode_speak_audio_decodes_base64_wav():
    assert decode_speak_audio({"audio": "AQI=", "sequence": 1}) == b"\x01\x02"


def test_decode_speak_audio_none_when_audio_absent():
    assert decode_speak_audio({"sequence": 1, "is_last": True}) is None
