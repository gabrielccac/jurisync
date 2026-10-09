import os
import unittest
from unittest.mock import patch

from jurisync.core import transcription
from jurisync.core.document_text import extract_document_text


class TranscriptionTests(unittest.TestCase):
    def test_missing_extra_reports_unavailable_without_download(self):
        with patch.dict("sys.modules", {"faster_whisper": None}):
            self.assertEqual(transcription.transcribe_bytes(b"\xff\xfbdata"), ("transcription_unavailable", ""))

    def test_empty_audio_is_empty(self):
        self.assertEqual(transcription.transcribe_bytes(b""), ("empty", ""))

    def test_audio_content_type_routes_to_transcriber(self):
        with patch("jurisync.core.document_text.transcribe_bytes", return_value=("transcribed", "[0.0] oi")) as spy:
            status, text = extract_document_text(b"\x00\x01audio", "audio/mpeg", "0000000-00.2026.4.05.8303")
        spy.assert_called_once()
        self.assertEqual((status, text), ("transcribed", "[0.0] oi"))

    def test_mp3_bytes_without_content_type_are_routed_by_signature(self):
        with patch("jurisync.core.document_text.transcribe_bytes", return_value=("transcribed", "x")) as spy:
            extract_document_text(b"ID3\x04\x00payload", "", "")
        spy.assert_called_once()

    def test_html_is_not_routed_to_transcriber(self):
        with patch("jurisync.core.document_text.transcribe_bytes") as spy:
            extract_document_text(b"<p>Laudo pericial</p>", "text/html", "")
        spy.assert_not_called()


@unittest.skipUnless(os.environ.get("JURISYNC_TEST_AUDIO"), "set JURISYNC_TEST_AUDIO to an audio file to run")
class LiveTranscriptionTests(unittest.TestCase):
    def test_real_audio_is_transcribed_in_portuguese(self):
        with open(os.environ["JURISYNC_TEST_AUDIO"], "rb") as handle:
            status, text = transcription.transcribe_bytes(handle.read())
        self.assertEqual(status, "transcribed")
        self.assertTrue(text.strip())
