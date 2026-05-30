"""Qwen3-ASR-0.6B transcription engine — optional backend alongside Whisper.

This module is entirely self-contained.  If the ``qwen-asr`` package is not
installed, import still succeeds — the engine simply won't be loadable.

Key constraints:
- No auto-download: ``from_pretrained()`` reads from a local path only.
- If weights are missing or init fails → graceful fallback, no crash.
- Whisper stays as the default and fallback backend.
"""

import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


class QwenEngine:
    """Wraps Qwen3-ASR-0.6B model loading and transcription.

    Provides the same ``transcribe(audio) -> str`` interface as
    ``TranscriptionEngine`` so the app can swap backends transparently.
    Thread-safe: all public methods are guarded by ``self._lock``.
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
        self._lock = threading.RLock()
        self._configured_device = device

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
        Returns ``True`` if the model was loaded, ``False`` otherwise.
        Falls back from CUDA to CPU if the requested device fails.
        """
        with self._lock:
            if self._model is not None:
                return True

            return self._load_unlocked()

    def _load_unlocked(self) -> bool:
        """Load model, trying CUDA then CPU fallback."""
        try:
            import qwen_asr  # type: ignore[import-untyped]
        except ImportError:
            log.error(
                "[QWEN] qwen-asr package is not installed. "
                "Install it with: pip install qwen-asr"
            )
            self._model = None
            return False

        devices_to_try = [self._configured_device]
        if self._configured_device != "cpu":
            devices_to_try.append("cpu")

        last_exc = None
        for device in devices_to_try:
            try:
                log.info(
                    "[QWEN] Loading Qwen3-ASR model from %s (device=%s)...",
                    self.model_path,
                    device,
                )
                self._model = qwen_asr.Qwen3ASRModel.from_pretrained(
                    self.model_path,
                    device=device,
                )
                log.info(
                    "[QWEN] Model loaded successfully from %s (device=%s)",
                    self.model_path, device,
                )
                return True
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "[QWEN] Failed to load on device=%s: %s", device, exc,
                )
                self._model = None

        log.error(
            "[QWEN] All device attempts failed for %s. Last error: %s",
            self.model_path, last_exc,
        )
        return False

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array. Returns cleaned text string.

        Raises ``RuntimeError`` if the model is not loaded.
        """
        with self._lock:
            if self._model is None:
                raise RuntimeError(
                    "Qwen model not loaded. Call load() first or check logs for errors."
                )

            if len(audio) == 0:
                return ""

            duration = len(audio) / 16000
            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            silence_pct = float(np.sum(np.abs(audio) < 0.001) / audio.size * 100)
            log.info(
                "[QWEN] Transcribing: duration=%.1fs, RMS=%.6f, silence=%.1f%%",
                duration, rms, silence_pct,
            )

            if rms < 0.005:
                log.info(
                    "[QWEN] Near-silence detected (RMS=%.6f), skipping transcription",
                    rms,
                )
                return ""

            sample_rate = 16000
            result = self._model.transcribe(
                (audio, sample_rate),
                language=self.language,
            )

            if not result:
                return ""

            text = result[0].text if hasattr(result[0], "text") else str(result[0])
            return text.strip()

    def unload(self):
        """Free model memory."""
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                log.info("[QWEN] Model unloaded")

    def transcribe_with_fallback(self, audio: np.ndarray) -> str:
        """Transcribe audio — for Qwen, equivalent to transcribe()."""
        return self.transcribe(audio)

    @property
    def device_info(self) -> str:
        """Return human-readable device info string."""
        if self._model is not None:
            return f"Qwen ASR ({self.device})"
        return "Qwen ASR (not loaded)"

    @property
    def loaded_via(self) -> str:
        """Return a string describing how the model was loaded."""
        if self._model is not None:
            return f"qwen/{self.device}"
        return "not loaded"
