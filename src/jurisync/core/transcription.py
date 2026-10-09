"""Optional local speech-to-text for audio documents (faster-whisper, CPU, open source).

Install with the ``transcription`` extra. Without it, audio documents report
``transcription_unavailable`` and nothing is downloaded. The model is fetched
once on first use into ``JURISYNC_WHISPER_DIR`` (default ``~/.cache/jurisync/whisper``).
Transcription is slow on CPU (minutes for a 5-minute recording), so callers that
run inside a time-limited tool should expect long calls.
"""

import io
import os
from pathlib import Path

DEFAULT_MODEL = "small"
DEFAULT_LANGUAGE = "pt"
_model = None
_model_name = None


def _model_dir():
    configured = os.environ.get("JURISYNC_WHISPER_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "jurisync" / "whisper"


def _load_model():
    global _model, _model_name
    name = os.environ.get("JURISYNC_WHISPER_MODEL", DEFAULT_MODEL)
    if _model is None or _model_name != name:
        from faster_whisper import WhisperModel

        _model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(_model_dir()))
        _model_name = name
    return _model


def transcribe_bytes(content, *, language=DEFAULT_LANGUAGE):
    """Return ``(status, text)``; never raises for missing dependencies or bad audio."""
    if not content:
        return "empty", ""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "transcription_unavailable", ""
    try:
        model = _load_model()
        segments, _ = model.transcribe(io.BytesIO(content), language=language, beam_size=1, vad_filter=True)
        lines = [f"[{segment.start:.1f}] {segment.text.strip()}" for segment in segments]
    except Exception:
        return "transcription_failed", ""
    text = "\n".join(lines)
    if not text.strip():
        return "empty", ""
    return "transcribed", text
