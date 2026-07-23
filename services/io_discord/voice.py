"""Cœur pur de la logique d'enregistrement vocal (sans I/O Discord), testable sans discord.py."""

import base64
import io
import subprocess


def pcm_to_mp3(pcm: bytes, sample_rate: int = 48000, channels: int = 2) -> bytes:
    """Encode du PCM s16le en MP3 via ffmpeg."""
    return subprocess.run(
        [
            "ffmpeg", "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels),
            "-i", "pipe:0", "-f", "mp3", "pipe:1",
        ],
        input=bytes(pcm),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    ).stdout


def build_recording_attachments(pcm_by_user: dict, encoder=pcm_to_mp3) -> list[tuple[str, object]]:
    """Construit les paires (nom_de_fichier, objet_fichier) à uploader depuis le PCM par utilisateur."""
    return [
        (f"{user_id}.mp3", io.BytesIO(encoder(pcm)))
        for user_id, pcm in pcm_by_user.items()
    ]


def format_recording_summary(user_ids) -> str:
    """Message récapitulatif listant les locuteurs enregistrés, ou l'absence de capture."""
    mentions = [f"<@{user_id}>" for user_id in user_ids]
    if not mentions:
        return "No audio captured — did anyone speak?"
    return f"Recorded audio for: {', '.join(mentions)}."


def encode_voice_frame(speaker_id: int, speaker_name: str, pcm: bytes) -> dict:
    """Construit le payload `io.discord.voice.frame` pour un chunk de PCM d'un locuteur."""
    return {
        "speaker_id": str(speaker_id),
        "speaker_name": speaker_name,
        "pcm": base64.b64encode(bytes(pcm)).decode(),
    }
