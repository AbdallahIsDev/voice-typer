"""Qwen3-ASR-0.6B transcription engine — optional backend alongside Whisper.

This module is entirely self-contained.  If the ``qwen-asr`` package is not
installed, import still succeeds — the engine simply won't be loadable.

Key constraints:
- No auto-download: ``from_pretrained()`` reads from a local path only.
- If weights are missing or init fails → graceful fallback, no crash.
- Whisper stays as the default and fallback backend.
- Uses shared hallucination detection from voice_typer.server.hallucination.
"""

import logging
import threading
from typing import Optional

import numpy as np

from voice_typer.server.hallucination import should_reject_low_audio_hallucination

log = logging.getLogger(__name__)


class QwenEngine:
    """Wraps Qwen3-ASR-0.6B model loading and transcription.

    Provides the same ``transcribe(audio) -> str`` interface as
    ``TranscriptionEngine`` so the app can swap backends transparently.
    Implements TranscriberProtocol.
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

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully."""
        with self._lock:
            return self._model is not None

    def load(self, progress_callback=None) -> bool:
        """Load the Qwen ASR model from the local ``model_path``.

        All ``qwen_asr`` imports happen inside this method so that the
        module can be imported even when ``qwen-asr`` is not installed.

        Returns True if the model was loaded successfully, False otherwise.
        If loading fails for any reason (missing package, missing weights,
        CUDA error, etc.) the model stays ``None`` and the error is logged.
        """
        with self._lock:
            if self._model is not None:
                return True

            try:
                import qwen_asr  # type: ignore[import-untyped]

                if progress_callback:
                    progress_callback("Loading Qwen3-ASR model...")

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
            except ImportError as exc:
                log.error(
                    "[QWEN] qwen-asr package is not installed: %s", exc,
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
        with self._lock:
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

            # Use shared hallucination detection
            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            if should_reject_low_audio_hallucination(text, rms):
                log.warning("[QWEN] Rejected likely hallucination: %r", text[:80])
                return ""

            return text

    def transcribe_with_fallback(self, audio: np.ndarray) -> str:
        """Transcribe with GPU→CPU fallback on CUDA errors.

        ERR-008: Previously this method just delegated to ``transcribe``
        with no fallback at all, despite the name. If a CUDA error
        occurred the caller received the raw exception. We now detect
        CUDA errors and retry on CPU, mirroring the parakeet engine's
        behavior. Non-CUDA errors are re-raised so the caller can
        surface them via ERR-005's friendly-error path.
        """
        try:
            return self.transcribe(audio)
        except Exception as exc:
            err_str = str(exc).lower()
            if self.device == "cuda" and (
                "cuda" in err_str or "cublas" in err_str or "cudnn" in err_str
                or "out of memory" in err_str
            ):
                log.warning("[QWEN] CUDA error, retrying on CPU: %s", exc)
                try:
                    original_device = self.device
                    self.device = "cpu"
                    if self._model is not None:
                        try:
                            self._model.to("cpu")
                        except Exception:
                            # Not all model wrappers expose .to(); ignore
                            pass
                    return self.transcribe(audio)
                except Exception as cpu_exc:
                    # Restore device on failure so the next attempt starts fresh
                    self.device = original_device if 'original_device' in locals() else "cuda"
                    log.error("[QWEN] CPU fallback also failed: %s", cpu_exc)
                    raise
            # Non-CUDA error: re-raise so caller can handle
            raise

    def unload(self) -> None:
        """Free model memory."""
        with self._lock:
            self._model = None
        log.info("[QWEN] Model unloaded")

    @property
    def device_info(self) -> str:
        """Return device info string."""
        return f"qwen/{self.device}"

    @property
    def loaded_via(self) -> str:
        """Return description of how the model was loaded."""
        return f"qwen/{self.device}/{self.model_path}"
