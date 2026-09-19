"""Transcription paths, chunking, abort handling, fallback."""

from __future__ import annotations

import logging
import time

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.branding import APP_NAME
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination

from ._constants import _CHUNK_OVERLAP_SECONDS, _CHUNK_SECONDS, _PARAKERT_ONNX_REPO_ID
from ._helpers import _compute_overlap_skip_impl, _is_cuda_error_impl, _is_likely_english, _merge_chunks_impl
from ._shims import TranscriptionBackendError

log = logging.getLogger(__name__)


class TranscribeMixin:
    @property
    def is_loaded(self) -> bool:
        """Return ``True`` if the ONNX model is loaded."""
        with self._lock:
            return self._model is not None

    def request_abort(self) -> None:
        """Signal an in-flight transcription to stop after the current chunk."""
        self._abort_event.set()

    def clear_abort(self) -> None:
        """Clear the abort token at the start of a fresh transcription cycle."""
        self._abort_event.clear()

    def transcribe(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe audio array. Returns cleaned text string."""
        with self._lock:
            if self._model is None:
                raise RuntimeError("Parakeet model not loaded. Call load() first or check logs.")

            if len(audio) == 0:
                return ""

            duration = len(audio) / WHISPER_SAMPLE_RATE
            self._active_inference += 1

        try:
            if duration <= _CHUNK_SECONDS:
                return self._transcribe_segment(audio, audio_stats=audio_stats)

            chunks = self._split_audio(audio, _CHUNK_SECONDS, _CHUNK_OVERLAP_SECONDS)
            log.info("[PARAKEET] Splitting %.1fs audio into %d chunks", duration, len(chunks))

            results = self._transcribe_chunks(chunks)
            if not results:
                return ""

            return self._merge_chunks(results)
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()

    def _transcribe_segment(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe one audio segment via the onnx-asr adapter's ``recognize``."""
        text = self._model.recognize(audio, sample_rate=WHISPER_SAMPLE_RATE)

        # shape (mirrors the pre-migration defensive pattern).
        if isinstance(text, list):
            text = text[0] if text else ""
        text = (text or "").strip()

        # English-only filter: only active when language="en" is configured
        if self.language == "en" and not _is_likely_english(text):
            return ""

        # PERF-STATS: reuse pre-computed RMS when provided
        rms = audio_stats[0] if audio_stats is not None else float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            log_hallucination_rejection(
                "[PARAKEET]",
                text,
                reason="hallucination",
                log_transcriptions=False,
            )
            return ""
        return text

    def _transcribe_chunks(self, chunks: list[np.ndarray]) -> list[str]:
        """Transcribe each chunk via ``model.recognize()``; respect abort."""
        if not chunks:
            return []
        results: list[str] = []
        for i, chunk in enumerate(chunks):
            if self._abort_event.is_set():
                log.info(
                    "[PARAKEET] Abort requested, stopping chunk loop early (completed %d/%d chunks)",
                    i,
                    len(chunks),
                )
                break
            log.info(
                "[PARAKEET] Transcribing chunk %d/%d (%.1fs)",
                i + 1,
                len(chunks),
                len(chunk) / WHISPER_SAMPLE_RATE,
            )
            text = self._transcribe_segment(chunk)
            if text:
                results.append(text)
        return results

    def _split_audio(self, audio: np.ndarray, chunk_sec: float, overlap_sec: float) -> list[np.ndarray]:
        """Split audio into overlapping chunks."""
        from voice_typer.server.asr_utils import split_audio

        return split_audio(
            audio,
            chunk_duration=chunk_sec,
            overlap_duration=overlap_sec,
            sample_rate=WHISPER_SAMPLE_RATE,
        )

    def _merge_chunks(self, texts: list[str]) -> str:
        """Concatenate chunk transcriptions, skipping overlap text."""
        return _merge_chunks_impl(texts)

    @staticmethod
    def _compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
        """Return how many leading words of *new_words* to skip."""
        return _compute_overlap_skip_impl(prev_words, new_words)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
        local_engine: object = None,
    ) -> str:
        """Emits the ``parakeet_cpu_fallback`` event (one-time per loaded"""
        with self._lock:
            if self._model is None:
                raise TranscriptionBackendError("Parakeet model not loaded.")

            if len(audio) == 0:
                return ""

        try:
            return self.transcribe(audio, audio_stats=audio_stats)
        except Exception as exc:
            # Use the shared CUDA-error classifier (PLAN_ONNX_INTEGRATION.md
            if self.device == "cuda" and _is_cuda_error_impl(exc):
                log.warning(
                    "[PARAKEET] CUDA error, recreating session on CPU: %s",
                    exc,
                    exc_info=True,
                )
                try:
                    # Unload the GPU session, then reload with CPU providers.
                    self._unload_impl()
                    self.device = "cpu"
                    if not self._load_impl(providers=["CPUExecutionProvider"]):
                        raise TranscriptionBackendError(f"Parakeet CPU fallback load failed after CUDA error ({exc})")
                    # Claim an inference slot so a concurrent ``unload()``
                    with self._lock:
                        if self._model is None:
                            raise TranscriptionBackendError("Parakeet model not loaded after CPU fallback.")
                        self._active_inference += 1
                    try:
                        text = self._transcribe_segment(audio, audio_stats=audio_stats)
                    finally:
                        with self._inference_cond:
                            self._active_inference -= 1
                            if self._active_inference == 0:
                                self._inference_cond.notify_all()

                    # Record the fallback start so any future retry logic
                    self._cpu_fallback_since = time.monotonic()
                    self._cpu_transcribe_count = 0

                    # Emit ONE-TIME tray notification + status event.
                    if not self._cpu_fallback_notified:
                        self._cpu_fallback_notified = True
                        try:
                            from voice_typer.server import event_bus

                            event_bus.publish(
                                {
                                    "type": "notification",
                                    "data": {
                                        "title": APP_NAME,
                                        "message": (
                                            "GPU transcription failed, switched to CPU. "
                                            "Transcription will be slower until restart."
                                        ),
                                        "duration_ms": 10000,
                                    },
                                }
                            )
                            event_bus.publish(
                                {
                                    "type": "parakeet_cpu_fallback",
                                    "data": {"device": "cpu", "reason": str(exc)[:200]},
                                }
                            )
                        except Exception as notify_exc:
                            log.debug(
                                "[PARAKEET] could not publish CPU-fallback notification: %s",
                                notify_exc,
                            )
                    return text
                except TranscriptionBackendError:
                    raise
                except Exception as cpu_exc:
                    log.exception("[PARAKEET] CPU fallback also failed")
                    raise TranscriptionBackendError(
                        f"Parakeet GPU transcription failed ({exc}) and CPU fallback also failed ({cpu_exc})"
                    ) from cpu_exc
            # Non-CUDA error: surface it instead of swallowing as ""
            raise TranscriptionBackendError(f"Parakeet transcription failed: {exc}") from exc

    def unload(self) -> None:
        """Free model memory."""
        import gc

        from voice_typer.server.asr_utils import release_gpu_memory

        with self._inference_cond:
            # Wait for any active transcription to finish before nulling
            while self._active_inference > 0:
                self._inference_cond.wait()
            self._model = None
            self._processor = None
        # gc.collect() OUTSIDE the lock.
        gc.collect()
        # No-op for ORT, kept for API compat (see PLAN_ONNX_INTEGRATION.md §5.2).
        release_gpu_memory()
        log.info("[PARAKEET] Model unloaded")

    @property
    def device_info(self) -> str:
        return f"parakeet/{self.device}"

    @property
    def loaded_via(self) -> str:
        return f"parakeet/{self.device}/{_PARAKERT_ONNX_REPO_ID}"
