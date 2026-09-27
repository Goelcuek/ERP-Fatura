"""Optional offline speech recognition with Whisper (faster-whisper), running on the shop PC.

Install with `pip install -r requirements-voice.txt`. The model is downloaded once on first
use (≈ 470 MB for "small"), then everything runs locally without internet. Without it, the
browser's built-in speech recognition is used instead (Chrome/Edge; needs internet).
"""

import os
import tempfile
import threading

_lock = threading.Lock()
# vocabulary hints that improve recognition of workshop terms
PROMPTS = {"tr": "Servis kaydı, SRV, onarım, parça bekliyor, teslim ettim, hazır, fatura, tahsilat, kömür, rotor, şalter.",
           "en": "Service order, SRV, repair, waiting for parts, delivered, ready, invoice, payment."}
_models = {}


class SpeechError(Exception):
    pass


def available():
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _model(size, download_root):
    with _lock:
        if size not in _models:
            from faster_whisper import WhisperModel

            _models[size] = WhisperModel(size, device="cpu", compute_type="int8", download_root=download_root)
        return _models[size]


def transcribe(audio_bytes, language="tr", size="small", download_root=None):
    if not available():
        raise SpeechError("Local speech recognition is not installed.")
    if not audio_bytes or len(audio_bytes) > 20 * 1024 * 1024:
        raise SpeechError("No audio received.")
    fd, path = tempfile.mkstemp(suffix=".webm")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(audio_bytes)
        segments, _info = _model(size, download_root).transcribe(
            path, language=language, beam_size=1, vad_filter=True,
            initial_prompt=PROMPTS.get(language))
        return " ".join(s.text.strip() for s in segments).strip()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
