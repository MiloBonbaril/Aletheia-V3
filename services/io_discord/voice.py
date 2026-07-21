"""Cœur pur de la logique d'enregistrement vocal (sans I/O Discord), testable sans discord.py."""


def build_recording_attachments(audio_data: dict, encoding: str) -> list[tuple[str, object]]:
    """Construit les paires (nom_de_fichier, objet_fichier) à uploader depuis sink.audio_data."""
    return [(f"{user_id}.{encoding}", audio.file) for user_id, audio in audio_data.items()]


def format_recording_summary(user_ids) -> str:
    """Message récapitulatif listant les locuteurs enregistrés, ou l'absence de capture."""
    mentions = [f"<@{user_id}>" for user_id in user_ids]
    if not mentions:
        return "No audio captured — did anyone speak?"
    return f"Recorded audio for: {', '.join(mentions)}."
