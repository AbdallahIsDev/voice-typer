"""Qwen3-ASR-0.6B transcription engine — optional backend alongside Whisper.

This module is entirely self-contained.  If the ``qwen-asr`` package is not
installed, import still succeeds — the engine simply won't be loadable.

Key constraints:
- No auto-download: ``from_pretrained()`` reads from a local path only.
- If weights are missing or init fails → graceful fallback, no crash.
- Whisper stays as the default and fallback backend.
"""

import logging
import re
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_KNOWN_LOW_AUDIO_HALLUCINATIONS = {
    "thanks for watching",
    "thank you for watching",
    "see you next time",
    "bye",
}


def _normalize_hallucination_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


class QwenEngine:
    """Wraps Qwen3-ASR-0.6B model loading and transcription.

    Provides the same ``transcribe(audio) -> str`` interface as
    ``TranscriptionEngine`` so the app can swap backends transparently.
    """

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        language: str = "en",
    ):
        self.model_path = model_path
        self.device = device
        self.language = language
        self._model = None

    # ── Public interface ──────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully."""
        return self._model is not None

    def load(self) -> bool:
        """Load the Qwen ASR model from the local ``model_path``.

        All ``qwen_asr`` imports happen inside this method so that the
        module can be imported even when ``qwen-asr`` is not installed.

        If loading fails for any reason (missing package, missing weights,
        CUDA error, etc.) the model stays ``None`` and the error is logged.
        The caller can check ``is_loaded`` or catch ``RuntimeError`` from
        ``transcribe()``.

        Returns True on success, False on failure.
        """
        if self._model is not None:
            return True

        try:
            import qwen_asr  # type: ignore[import-untyped]

            log.info(
                "[QWEN] Loading Qwen3-ASR model from %s (device=%s)...",
                self.model_path,
                self.device,
            )
            self._model = qwen_asr.Qwen3ASRModel.from_pretrained(
                self.model_path,
            )
            log.info("[QWEN] Model loaded successfully from %s", self.model_path)
            return True
        except ImportError:
            log.error(
                "[QWEN] qwen-asr package is not installed. "
                "Install it with: pip install qwen-asr"
            )
            self._model = None
            return False
        except Exception as exc:
            log.error(
                "[QWEN] Failed to load Qwen3-ASR model from %s: %s",
                self.model_path,
                exc,
            )
            self._model = None
            return False

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array. Returns cleaned text string.

        Raises ``RuntimeError`` if the model is not loaded.
        """
        if self._model is None:
            raise RuntimeError(
                "Qwen model not loaded. Call load() first or check logs for errors."
            )

        if len(audio) == 0:
            return ""

        # Qwen transcribe() expects (np.ndarray, sample_rate) tuples
        sample_rate = 16000
        result = self._model.transcribe(
            (audio, sample_rate),
            language=self.language,
        )

        # result is a list of ASRTranscription objects
        if not result:
            return ""

        # Extract text from the first (and usually only) transcription
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        text = text.strip()

        # M13: Reject low-audio hallucinations
        if self._should_reject_low_audio_hallucination(audio, text):
            return ""

        return text

    def _should_reject_low_audio_hallucination(
        self, audio: np.ndarray, text: str
    ) -> bool:
        """Check if transcription is likely a hallucination from near-silence."""
        if not text:
            return False
        key = _normalize_hallucination_key(text)
        if key not in _KNOWN_LOW_AUDIO_HALLUCINATIONS:
            return False
        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if rms < 0.001:
            return True
        return False

    def unload(self):
        """Free model memory."""
        self._model = None
        log.info("[QWEN] Model unloaded")
