import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice import build_recording_attachments, format_recording_summary


class FakeAudioData:
    def __init__(self, file):
        self.file = file


def test_build_recording_attachments_names_files_by_user_id():
    audio_data = {111: FakeAudioData(file="fileobj-a"), 222: FakeAudioData(file="fileobj-b")}

    result = build_recording_attachments(audio_data, "mp3")

    assert result == [("111.mp3", "fileobj-a"), ("222.mp3", "fileobj-b")]


def test_build_recording_attachments_empty_when_nobody_spoke():
    assert build_recording_attachments({}, "mp3") == []


def test_format_recording_summary_lists_all_speakers():
    assert format_recording_summary([111, 222]) == "Recorded audio for: <@111>, <@222>."


def test_format_recording_summary_handles_no_speakers():
    assert format_recording_summary([]) == "No audio captured — did anyone speak?"
