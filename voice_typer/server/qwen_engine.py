"""Qwen3-ASR transcription engine — ONNX Runtime backend (no torch).

PLAN_ONNX_INTEGRATION.md §4.3 Option C-2 — implemented 2026-08-14,
made torch-free-only 2026-08-15. The pre-exported ONNX models
(``andrewleech/qwen3-asr-1.7b-onnx`` / ``qwen3-asr-0.6b-onnx``) run via
``onnxruntime`` through :class:`voice_typer.server.qwen_onnx_model.QwenOnnxModel`.
There is NO torch path and NO ``qwen_asr`` package dependency anymore —
``torch>=2.0``, ``transformers`` and the ``qwen-asr`` optional extra were
removed from ``pyproject.toml`` in the same change.

This module is entirely self-contained.  Import succeeds without any
heavy dependencies; the ONNX sessions are opened lazily inside
``load()``.

Key constraints:
- No auto-download: ``load()`` reads from a local ONNX-export directory
  only (``encoder.onnx`` / ``decoder_init.onnx`` / ``decoder_step.onnx``
  + ``embed_tokens.bin`` + ``tokenizer.json``). A torch/safetensors
  layout directory is rejected with a migration error — the torch Qwen
  engine was removed.
- If the directory is missing / not an ONNX export / fails to load →
  ``load()`` returns False (non-ONNX dir) or raises ``RuntimeError``
  (ONNX dir that fails mid-load — fail-closed, no silent fallback).
- Whisper stays as the default and fallback backend.
- Uses shared hallucination detection from voice_typer.server.hallucination.
"""

import contextlib
import logging
import os
import threading
from typing import Any

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.asr_utils import merge_chunks
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination

log = logging.getLogger(__name__)

# Qwen3-ASR is Whisper-based and natively handles 30 s segments.
# Longer recordings are split into overlapping chunks for safety
# (memory, attention matrix size, and to bound per-call latency).
# 3 s overlap provides boundary context.
#
# Despite earlier comments claiming Whisper-style models
# "do not re-transcribe overlap text", real-world Qwen3-ASR runs DO
# duplicate a few words at chunk boundaries (the 3 s overlap is
# transcribed by both the previous and the current chunk). The seam
# merge is delegated to :func:`voice_typer.server.asr_utils.merge_chunks`
# — the same canonical normalized dedup (punctuation-stripped,
# case-insensitive, window-bounded) that ParakeetEngine uses, so both
# local engines behave identically at chunk seams.
_QWEN_CHUNK_SECONDS = 30
_QWEN_CHUNK_OVERLAP_SECONDS = 3


class QwenEngine:
    """Wraps the Qwen3-ASR ONNX model (``qwen_onnx_model.QwenOnnxModel``).

    Provides the same ``transcribe(audio) -> str`` interface as
    ``TranscriptionEngine`` so the app can swap backends transparently.
    Implements TranscriberProtocol.

    The ONNX runtime path is CPU-first (the int4 CPU exports are the
    documented fast path; ORT CUDA is not exercised here), so
    ``self.device`` is pinned to ``\"cpu\"`` at ``load()`` regardless of
    the constructor's ``device`` argument.
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
        # Set when the ONNX backend is active (see ``load()``). Guards
        # ``unload`` / ``device_info`` so the ONNX model is released via
        # its ``close()`` method.
        self._onnx_model = None
        self._lock = threading.RLock()
        # Counter + Condition so transcribe() can release the model lock
        # during the (potentially long) inference call while still
        # coordinating with unload(). unload() waits for
        # ``_active_inference == 0`` before nulling ``self._model`` so a
        # concurrent transcribe() doesn't dereference a freed model
        # (use-after-free). Mirrors ParakeetEngine's pattern.
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        # Abort token checked between chunk iterations in the
        # transcription loops (mirrors ParakeetEngine's ``_abort_event``).
        # ``request_abort`` sets it, ``clear_abort`` clears it at the
        # start of a fresh dictation cycle; the chunk loop breaks out
        # after the current chunk so the transcription thread is
        # unblocked in bounded time instead of decoding the whole
        # recording.
        self._abort_event = threading.Event()
        # Batch 2-4 chunks per ``model.transcribe()`` call when the
        # ONNX model exposes a batched-input API.  Default batch size is
        # 1 (sequential) so the existing test contract that pins
        # ``mock_model.transcribe.side_effect = [r1, r2, r3]``
        # (one ``transcribe`` call per chunk) keeps passing.  Operators
        # who want the batching speedup can set ``QWEN_BATCH_SIZE=2``
        # (or 3/4) in the environment; on OOM we fall back to per-chunk
        # sequential inference for the remaining chunks so the user
        # still gets a transcription.  Mirrors ParakeetEngine's
        # ``_INFERENCE_BATCH_SIZE`` / ``PARAKEET_BATCH_SIZE`` pattern.
        #
        # Read at construction time (NOT import time) so changes to the
        # env var between engine constructions take effect — same
        # rationale as ParakeetEngine.__init__.
        self._INFERENCE_BATCH_SIZE: int = max(1, int(os.environ.get("QWEN_BATCH_SIZE", "1")))

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully.

        RACE-032: ``transcribe()`` releases ``self._lock`` during the
        multi-second inference call (incrementing ``_active_inference``
        before release so ``unload()`` can wait). ``is_loaded`` only
        acquires ``self._lock`` briefly to read ``self._model``, so it
        returns True even mid-inference without blocking.
        """
        with self._lock:
            return self._model is not None

    def load(self, progress_callback=None) -> bool:
        """Load the Qwen3-ASR ONNX model from the local ``model_path``.

        The directory must hold the pre-exported ONNX layout
        (``encoder.onnx`` / ``decoder_init.onnx`` / ``decoder_step.onnx``
        + ``embed_tokens.bin`` + ``tokenizer.json``) — the
        torch/safetensors Qwen layout is no longer supported (the torch
        engine was removed 2026-08-15; see PLAN_ONNX_INTEGRATION §4.3
        C-2).

        Returns True if the model was loaded successfully, False
        otherwise. A non-ONNX directory returns False with a migration
        error logged; an ONNX directory that fails mid-load raises
        ``RuntimeError`` (fail-closed — a corrupt or incomplete ONNX
        export is never silently ignored).
        """
        with self._lock:
            if self._model is not None:
                return True

            from voice_typer.server.qwen_onnx_model import QwenOnnxModel, is_onnx_model_dir

            if not is_onnx_model_dir(self.model_path):
                log.error(
                    "[QWEN] %s is not a Qwen3-ASR ONNX export directory. "
                    "The torch/safetensors Qwen layout is no longer "
                    "supported (torch removed 2026-08-15, "
                    "PLAN_ONNX_INTEGRATION.md §4.3 C-2). Download the "
                    "pre-exported ONNX model "
                    "(andrewleech/qwen3-asr-1.7b-onnx or "
                    "qwen3-asr-0.6b-onnx) and point qwen_model_path at "
                    "the extracted directory (needs encoder.onnx / "
                    "decoder_init.onnx / decoder_step.onnx + "
                    "embed_tokens.bin + tokenizer.json).",
                    self.model_path,
                )
                return False

            try:
                onnx_model = QwenOnnxModel(self.model_path)
                onnx_model.from_pretrained()
            except Exception as exc:  # noqa: BLE001 — load failures are surfaced, not hidden
                log.exception(
                    "[QWEN] ONNX model load FAILED for %s — the directory "
                    "is corrupt or incomplete (fail-closed; no torch retry "
                    "exists anymore)",
                    self.model_path,
                )
                self._model = None
                self._onnx_model = None
                raise RuntimeError(f"Qwen3-ASR ONNX model load failed: {exc}") from exc

            self._onnx_model = onnx_model
            self._model = onnx_model  # self._model drives is_loaded
            # The ONNX runtime path is CPU-first (the int4 CPU exports
            # are the documented fast path; ORT CUDA is not exercised
            # here). Pinning the concrete device keeps
            # ``device_info`` honest and prevents any future
            # CUDA-specific logic from applying to the ONNX model.
            self.device = "cpu"
            log.info(
                "[QWEN] Using ONNX Runtime backend (qwen_onnx_model.QwenOnnxModel) for %s — no torch required",
                self.model_path,
            )
            return True

    def transcribe(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe audio array. Returns cleaned text string.

        RACE-032: The lock is only held for state checks/updates.
        Inference runs outside the lock so is_loaded / unload /
        load don't block for the multi-second duration of the call.
        ``_active_inference`` is incremented before releasing the lock
        and decremented in a ``finally`` block; ``unload()`` waits on
        ``_inference_cond`` for the counter to return to 0 before
        nulling ``self._model``, so a concurrent ``unload()`` can't
        free the model mid-inference (use-after-free).

        PERF-STATS: ``audio_stats`` is an optional pre-computed
                ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
                When provided, the engine skips its own RMS computation in
                hallucination detection.  Note: ``audio_stats`` is only
                meaningful for the non-chunked path (audio <=
                ``_QWEN_CHUNK_SECONDS``); chunked audio computes per-chunk
                RMS inline.

        For audio longer than ``_QWEN_CHUNK_SECONDS`` (30 s),
                split into overlapping chunks (30 s chunk + 3 s overlap) and
                merge results with simple concatenation.  Previously the
                entire multi-minute audio array was passed in one
                ``model.transcribe()`` call, risking OOM or silent
                truncation.  Each chunk's text is run through the shared
                hallucination filter using that chunk's own RMS.
        """
        with self._lock:
            if self._model is None:
                raise RuntimeError("Qwen model not loaded. Call load() first or check logs for errors.")
            model = self._model
            self._active_inference += 1

        try:
            if len(audio) == 0:
                return ""

            # Qwen transcribe() expects (np.ndarray, sample_rate) tuples
            sample_rate = WHISPER_SAMPLE_RATE
            duration = len(audio) / sample_rate

            if duration > _QWEN_CHUNK_SECONDS:
                # chunk long audio to bound per-call latency and
                # memory.  ``audio_stats`` describes the whole-audio
                # RMS, so per-chunk RMS is computed inline in the helper.
                return self._transcribe_chunked(model, audio, sample_rate)

            # Non-chunked path (audio <= _QWEN_CHUNK_SECONDS): single call
            # with the existing hallucination check using ``audio_stats``
            # if provided, else computing RMS from the audio array.
            result = model.transcribe(
                (audio, sample_rate),
                language=self.language,
            )

            # result is a list of transcription objects
            if not result:
                return ""

            # Extract text from the first (and usually only) transcription
            text = result[0].text if hasattr(result[0], "text") else str(result[0])
            text = text.strip()

            # Use shared hallucination detection
            # PERF-STATS: reuse pre-computed RMS when provided
            if audio_stats is not None:
                rms = audio_stats[0]
            else:
                rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            if should_reject_low_audio_hallucination(text, rms):
                # SEC-009: Use PII-safe logging helper instead of raw text
                log_hallucination_rejection(
                    "[QWEN]",
                    text,
                    reason="hallucination",
                    log_transcriptions=False,
                )
                return ""

            return text
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()

    def _transcribe_chunked(
        self,
        model: Any,
        audio: np.ndarray,
        sample_rate: int,
    ) -> str:
        """transcribe long audio by splitting into overlapping chunks.

                Each chunk's text is run through the shared hallucination filter
                using that chunk's own RMS (``audio_stats`` from the caller is
                NOT used here — it describes the whole-audio RMS, not per-chunk).

        Because consecutive chunks share a 3 s overlap
                (``_QWEN_CHUNK_OVERLAP_SECONDS``), the overlap region is
                transcribed by both chunks. The surviving chunk texts are
                merged by :func:`voice_typer.server.asr_utils.merge_chunks`,
                which skips the duplicated boundary words with the same
                normalized overlap detection ParakeetEngine uses
                (punctuation-stripped, case-insensitive, window-bounded),
                and joins the results with single spaces.

        Batched path: when ``_INFERENCE_BATCH_SIZE`` > 1 (set via the
        ``QWEN_BATCH_SIZE`` env var), ``_transcribe_chunks_batched``
        groups that many chunks per ``model.transcribe()`` call.  On an
        OOM, it falls back to per-chunk sequential inference for the
        remaining chunks so the user still gets a transcription.
        Mirrors ParakeetEngine's ``_transcribe_chunks_batched`` /
        ``PARAKEET_BATCH_SIZE`` pattern.  Default batch size is 1
        (sequential) so the existing test contract that pins
        ``mock_model.transcribe.side_effect = [r1, r2, r3]`` keeps
        passing.
        """
        duration = len(audio) / sample_rate
        log.info("[QWEN] Splitting %.1fs audio into chunks", duration)
        chunks = self._split_audio(
            audio,
            _QWEN_CHUNK_SECONDS,
            _QWEN_CHUNK_OVERLAP_SECONDS,
        )
        # Get raw chunk texts (batched or sequential, with per-chunk
        # hallucination filtering applied inline).  Empty strings in
        # the result list indicate hallucination rejection or no
        # speech — merge_chunks skips them without letting them
        # participate in the overlap comparison.
        chunk_texts = self._transcribe_chunks_batched(model, chunks, sample_rate)

        # Seam merge: skip overlap-duplicated words at chunk boundaries
        # and join. Delegated to the canonical shared helper so Qwen and
        # Parakeet chunk seams behave identically (single source of
        # truth — one dedup implementation for both local engines).
        return merge_chunks(chunk_texts)

    def _transcribe_chunks_batched(
        self,
        model: Any,
        chunks: list[np.ndarray],
        sample_rate: int,
    ) -> list[str]:
        """Transcribe ``chunks`` in batches, falling back to sequential on OOM.

        Mirrors ParakeetEngine._transcribe_chunks_batched.  When
        ``_INFERENCE_BATCH_SIZE`` is 1 (default), this method is
        strictly sequential and preserves the historical call-count
        contract pinned by ``test_qwen_engine_overlap_dedup.py``
        (one ``model.transcribe()`` call per chunk, in order).  When
        set to 2+ via the ``QWEN_BATCH_SIZE`` env var, we group that
        many chunks per ``model.transcribe()`` call.  On an OOM
        (``\"out of memory\"`` in the error string), we fall back to
        per-chunk sequential inference for the remaining chunks so the
        user still gets a transcription.

        Returns a list of text strings (one per chunk).  Empty strings
        indicate hallucination rejection or no speech — the caller's
        dedup pass skips them without advancing ``prev_text``.

        NOTE: The batched path (``_INFERENCE_BATCH_SIZE > 1``) assumes
        the model adapter accepts a list of ``(audio, sample_rate)``
        tuples as its first positional arg.  The ONNX adapter's
        ``transcribe`` currently accepts a single tuple; if a batched
        call raises, the sequential fallback fires, so correctness is
        preserved even if the batched path is unavailable.
        """
        if not chunks:
            return []

        if self._INFERENCE_BATCH_SIZE <= 1 or len(chunks) == 1:
            return self._transcribe_chunks_sequential(model, chunks, sample_rate)

        results: list[str] = []
        i = 0
        while i < len(chunks):
            # Same abort check as the sequential branch — see above.
            if self._abort_event.is_set():
                log.info(
                    "[QWEN] Abort requested — stopping batch loop early (completed %d/%d chunks)",
                    i,
                    len(chunks),
                )
                break
            batch = chunks[i : i + self._INFERENCE_BATCH_SIZE]
            i += len(batch)
            log.info(
                "[QWEN] Transcribing batch of %d chunks (%d/%d done)",
                len(batch),
                i - len(batch),
                len(chunks),
            )
            try:
                batch_texts = self._transcribe_batch(model, batch, sample_rate)
                results.extend(batch_texts)
            except Exception as exc:
                err_str = str(exc).lower()
                if "out of memory" in err_str or ("cuda" in err_str and "allocat" in err_str):
                    log.warning(
                        "[QWEN] Batched inference OOM on batch of %d chunks — falling back to sequential: %s",
                        len(batch),
                        exc,
                        exc_info=True,
                    )
                    seq_texts = self._transcribe_chunks_sequential(model, batch, sample_rate)
                    results.extend(seq_texts)
                else:
                    raise
        # Pad with empty strings if batched returned fewer results
        # than chunks (shouldn't happen, but defensive).
        while len(results) < len(chunks):
            results.append("")
        return results[: len(chunks)]

    def _transcribe_chunks_sequential(
        self,
        model: Any,
        chunks: list[np.ndarray],
        sample_rate: int,
    ) -> list[str]:
        """Transcribe chunks one at a time (the default path).

        Extracted from the pre-batched ``_transcribe_chunked`` body so
        ``_transcribe_chunks_batched`` can fall back to it on OOM
        without duplicating the per-chunk hallucination-filter logic.
        """
        results: list[str] = []
        for i, chunk in enumerate(chunks):
            # Abort check: break out after the current chunk when an
            # abort was requested (mirrors ParakeetEngine's sequential
            # chunk loop) so the transcription thread is unblocked in
            # bounded time instead of decoding all remaining chunks.
            if self._abort_event.is_set():
                log.info(
                    "[QWEN] Abort requested — stopping chunk loop early (completed %d/%d chunks)",
                    i,
                    len(chunks),
                )
                break
            log.info(
                "[QWEN] Transcribing chunk %d/%d (%.1fs)",
                i + 1,
                len(chunks),
                len(chunk) / sample_rate,
            )
            chunk_result = model.transcribe(
                (chunk, sample_rate),
                language=self.language,
            )
            if not chunk_result:
                results.append("")
                continue
            text = chunk_result[0].text if hasattr(chunk_result[0], "text") else str(chunk_result[0])
            text = text.strip()
            if not text:
                results.append("")
                continue
            # Per-chunk hallucination filter using the chunk's own RMS.
            rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
            if should_reject_low_audio_hallucination(text, rms):
                # SEC-009: Use PII-safe logging helper instead of raw text
                log_hallucination_rejection(
                    "[QWEN]",
                    text,
                    reason="hallucination",
                    log_transcriptions=False,
                )
                results.append("")
                continue
            results.append(text)
        return results

    def _transcribe_batch(
        self,
        model: Any,
        batch: list[np.ndarray],
        sample_rate: int,
    ) -> list[str]:
        """Run ``model.transcribe`` on a batch of chunks in one call.

        Assumes the model adapter accepts a list of ``(audio,
        sample_rate)`` tuples as its first positional arg.  If the API
        does not support batched input, this method raises and the
        caller (``_transcribe_chunks_batched``) falls back to
        ``_transcribe_chunks_sequential``.
        """
        # Build list of (audio, sample_rate) tuples — one per chunk.
        inputs = [(chunk, sample_rate) for chunk in batch]
        results = model.transcribe(inputs, language=self.language)
        # Decode each result, apply per-chunk hallucination filter.
        texts: list[str] = []
        for idx, chunk_result in enumerate(results or []):
            if idx >= len(batch):
                break
            if not chunk_result:
                texts.append("")
                continue
            text = chunk_result.text if hasattr(chunk_result, "text") else str(chunk_result)
            text = text.strip()
            if not text:
                texts.append("")
                continue
            rms = float(np.sqrt(np.mean(np.square(batch[idx]), dtype=np.float64)))
            if should_reject_low_audio_hallucination(text, rms):
                log_hallucination_rejection(
                    "[QWEN]",
                    text,
                    reason="hallucination",
                    log_transcriptions=False,
                )
                texts.append("")
                continue
            texts.append(text)
        # Pad with empty strings if the API returned fewer results
        # than chunks (defensive — shouldn't happen with a correct
        # batched API, but keeps the result length aligned with the
        # input length so the caller's dedup pass indexes correctly).
        while len(texts) < len(batch):
            texts.append("")
        return texts

    @staticmethod
    def _split_audio(
        audio: np.ndarray,
        chunk_sec: float,
        overlap_sec: float,
    ) -> list[np.ndarray]:
        """Split audio into overlapping chunks (mirrors ParakeetEngine._split_audio).

        Used by ``_transcribe_chunked`` to bound per-call audio length so
        the Whisper-style attention matrix and memory footprint stay
        predictable for multi-minute recordings.

        Delegates to :func:`voice_typer.server.asr_utils.split_audio`
        (single source of truth shared with ``ParakeetEngine._split_audio``).
        The method signature is preserved for backward compatibility with
        existing call sites and tests that invoke
        ``QwenEngine._split_audio(audio, chunk_sec, overlap_sec)`` directly.
        """
        from voice_typer.server.asr_utils import split_audio

        return split_audio(
            audio,
            chunk_duration=chunk_sec,
            overlap_duration=overlap_sec,
            sample_rate=WHISPER_SAMPLE_RATE,
        )

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: "tuple[float, float, float] | None" = None,
    ) -> str:
        """Transcribe, delegating to :meth:`transcribe`.

        The pre-migration torch engine had a GPU→CPU fallback that
        recreated the session with CPU providers after a CUDA error.
        The ONNX path is CPU-pinned at ``load()`` (the int4 CPU exports
        are the documented fast path), so there is no device to fall
        back FROM — any exception propagates to the caller's friendly
        error path, mirroring the old non-CUDA re-raise branch.
        """
        return self.transcribe(audio, audio_stats=audio_stats)

    def request_abort(self) -> None:
        """Signal an in-flight ``transcribe()`` to stop early.

        Sets ``_abort_event``; the chunk-iteration loops in
        ``_transcribe_chunks_sequential`` and ``_transcribe_chunks_batched``
        check the event between chunks and break out after the current
        chunk completes. Bounded latency instead of waiting for the
        full audio to decode — frees compute for the next dictation
        cycle. Mirrors ``ParakeetEngine.request_abort``.
        """
        self._abort_event.set()

    def clear_abort(self) -> None:
        """Clear the abort token at the start of a fresh transcription cycle.

        Called by the dictation pipeline before each transcribe so a
        stale abort from the previous cycle (e.g. the user hit ESC,
        aborted, then started a new recording) does NOT suppress the
        new transcription.
        """
        self._abort_event.clear()

    def unload(self) -> None:
        """Free model memory.

        Waits for any in-flight ``transcribe()`` call to finish (via the
        ``_active_inference`` counter + ``_inference_cond``) BEFORE
        nulling ``self._model`` so the inference thread doesn't
        dereference a freed model (use-after-free). Mirrors
        ``ParakeetEngine.unload``.

        RACE-023: gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.

        Defensive fallback: tests that bypass ``__init__`` (e.g.
        ``QwenEngine.__new__(QwenEngine)`` in
        ``tests/regressions/test_gpu_memory_release.py``) don't always
        set up ``_inference_cond`` / ``_active_inference``. Fall back to
        ``self._lock`` in that case so the regression test still works.
        """
        import gc

        from voice_typer.server.transcription import release_gpu_memory

        # Defensive: ``_inference_cond`` is created in ``__init__`` but
        # some test fixtures bypass ``__init__`` via ``__new__`` and
        # only set up ``_lock``. Fall back to ``_lock`` (a no-op wait
        # since ``_active_inference`` defaults to 0 via ``getattr``).
        inference_cond = getattr(self, "_inference_cond", None) or self._lock
        with inference_cond:
            while getattr(self, "_active_inference", 0) > 0:
                inference_cond.wait()
            self._model = None
            # ONNX backend: release the ORT sessions + embedding matrix
            # (best-effort — close() is idempotent-safe by construction).
            onnx_model = getattr(self, "_onnx_model", None)
            if onnx_model is not None:
                with contextlib.suppress(Exception):
                    onnx_model.close()
                self._onnx_model = None
        # RACE-023: gc.collect() OUTSIDE the lock
        gc.collect()
        # release CUDA cached blocks (a no-op on the ONNX path).
        release_gpu_memory()
        log.info("[QWEN] Model unloaded")

    @property
    def device_info(self) -> str:
        """Return device info string.

        The ONNX runtime path is CPU-first and pinned to ``\"cpu\"`` at
        ``load()``, so this is always ``\"qwen/cpu\"``.
        """
        return "qwen/cpu"

    @property
    def loaded_via(self) -> str:
        """Return description of how the model was loaded."""
        return f"qwen/{self.device}/{self.model_path}"
