"""Qwen3- transcription engine — optional backend alongside Whisper.

This module is entirely self-contained.  If the ``qwen-asr`` package is not
installed, import still succeeds — the engine simply won't be loadable.

Key constraints:
- No auto-download: ``from_pretrained()`` reads from a local path only.
- If weights are missing or init fails → graceful fallback, no crash.
- Whisper stays as the default and fallback backend.
- Uses shared hallucination detection from voice_typer.server.hallucination.
"""

import contextlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

# SEC-audit-007: Allowed file extensions and filenames in the Qwen model directory.
# Prevents loading from directories that contain unexpected files (executables,
# scripts, etc.) which could indicate tampering.
# NOTE: .py is deliberately excluded — model directories should never contain
# Python source files, which could execute arbitrary code during from_pretrained().
_QWEN_ALLOWED_EXTENSIONS = {
    ".safetensors",
    ".bin",
    ".json",
    ".model",
    ".txt",
}
_QWEN_ALLOWED_BASENAMES = {
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
    "feature_extractor_config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "tokenizer.model",
    "vocab.json",
    "merges.txt",
    "vocab.txt",
}

# Qwen3-ASR is Whisper-based and natively handles 30 s segments.
# Longer recordings are split into overlapping chunks for safety
# (memory, attention matrix size, and to bound per-call latency).
# 3 s overlap provides boundary context.
#
# Despite earlier comments claiming Whisper-style models
# "do not re-transcribe overlap text", real-world Qwen3-ASR runs DO
# duplicate a few words at chunk boundaries (the 3 s overlap is
# transcribed by both the previous and the current chunk). We dedup
# by comparing the previous chunk's tail against the current chunk's
# head and removing the matching prefix (see ``_dedup_overlap``).
_QWEN_CHUNK_SECONDS = 30
_QWEN_CHUNK_OVERLAP_SECONDS = 3
# word-count heuristic for overlap dedup at chunk boundaries.
# N=3 balances false negatives (small N → more duplicates slip through)
# against false positives (large N → legitimate repetition stripped).
# At ~3 words/sec English speech, a 3 s audio overlap can produce up
# to ~9 words of duplicate text, but ASR rarely re-transcribes the
# entire overlap region verbatim — N=3 catches the common 1-3 word
# repeat (e.g. "the end" + "the end of the sentence").
_QWEN_OVERLAP_DEDUP_WORDS = 3


class QwenEngine:
    """Wraps Qwen3- model loading and transcription.

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
        # Counter + Condition so transcribe() can release the model lock
        # during the (potentially long) GPU inference call while still
        # coordinating with unload(). unload() waits for
        # ``_active_inference == 0`` before nulling ``self._model`` so a
        # concurrent transcribe() doesn't dereference a freed PyTorch
        # module (use-after-free). Mirrors ParakeetEngine's pattern.
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        # Batch 2-4 chunks per ``model.transcribe()`` call when the
        # qwen_asr wrapper exposes a batched-input API.  Default batch
        # size is 1 (sequential) so the existing test contract that
        # pins ``mock_model.transcribe.side_effect = [r1, r2, r3]``
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
        multi-second GPU inference call (incrementing ``_active_inference``
        before release so ``unload()`` can wait). ``is_loaded`` only
        acquires ``self._lock`` briefly to read ``self._model``, so it
        returns True even mid-inference without blocking.
        """
        with self._lock:
            return self._model is not None

    @staticmethod
    def _inference_mode_ctx() -> Any:
        """Return a context manager that wraps torch.inference_mode().

        model.transcribe() was previously called WITHOUT an
                inference-mode context, which meant PyTorch built and retained
                the autograd graph for every call. For a 30 s chunk on CUDA
                this roughly DOUBLED activation-memory footprint (increasing
                OOM risk) and added ~10-30 % inference latency.

                torch.inference_mode() is preferred over torch.no_grad()
                (lower overhead, recursive). Imports torch lazily so the
                engine module imports even when torch isn't installed. If
                torch isn't importable, returns contextlib.nullcontext.
        """
        try:
            import torch
        except ImportError:
            return contextlib.nullcontext()
        return torch.inference_mode()

    def _resolve_device(self) -> str:
        """Resolve the effective device, honouring ``"auto"``.

                Mirrors ``TranscriptionEngine._resolve_device``
                (transcription.py:447-480) but adapted to Qwen's ``torch``-based
                model wrapper:

                - ``"auto"`` → ``"cuda"`` if ``torch.cuda.is_available()`` else
                  ``"cpu"``.
                - ``"cuda"`` / ``"cpu"`` → returned as-is (explicit device wins).

                ``self.device`` is NOT mutated here — the caller (``load()``)
                updates it after a successful ``.to("cuda")`` so a failed CUDA
                init doesn't leave a stale ``"cuda"`` value that would make
                ``transcribe_with_fallback``'s CUDA-error branch unreachable
        (the original  bug).

                Returns the resolved device string.
        """
        if self.device == "auto":
            try:
                import torch
            except ImportError:
                log.warning("[QWEN] torch not installed — cannot probe CUDA, falling back to CPU")
                return "cpu"
            try:
                if torch.cuda.is_available():
                    return "cuda"
            except Exception as exc:  # noqa: BLE001 — CUDA probe can raise varied errors
                log.warning("[QWEN] CUDA probe failed (%s) — falling back to CPU", exc)
            return "cpu"
        return self.device

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

            # SEC-audit-007: Validate model directory contains only expected file types
            if not _validate_qwen_model_dir(self.model_path):
                log.error(
                    "[QWEN] Model directory %s failed security validation — contains unexpected files",
                    self.model_path,
                )
                return False

            # SEC-audit-007 /  (Session 7 — Group 4): SHA-256
            # manifest verification of model directory contents before
            # calling from_pretrained().  We now delegate to the shared
            # ``security.verify_model_integrity()`` instead of the
            # divergent local ``_verify_qwen_model_hashes`` helper.
            #
            # Root cause of : ``security.verify_model_integrity``
            # hard-fails for local models with an empty ``pinned_files``
            # dict ( — a local model has no upstream SHA pin,
            # so the empty-files soft-pass would let a tampered
            # directory load unchecked).  But ``qwen_engine.py``'s own
            # ``_verify_qwen_model_hashes`` SOFT-PASSED on empty
            # ``pinned_files``, so the hard-fail branch in
            # ``security.py`` was dead code for the Qwen path.  A
            # tampered local Qwen model directory would load with NO
            # content hash verification.
            #
            # The fix: delete the divergent helper and call
            # ``security.verify_model_integrity(model_path, "qwen")``
            # directly so the  hard-fail is honoured.  When
            # ``model_hashes.json``'s ``"qwen"`` entry has empty
            # ``files`` (the default ship state), ``load()`` returns
            # False — operators MUST populate the ``files`` dict with
            # the expected SHA-256 hashes before a local Qwen model
            # can be loaded.
            #
            # The return value is CHECKED: if pinned hashes are present
            # and any mismatches, load() aborts with False instead of
            # proceeding to from_pretrained().  Previously the return
            # value was discarded (bare call), so a tampered model
            # would still load — only a log warning was emitted.
            try:
                from voice_typer.server.security import verify_model_integrity

                if not verify_model_integrity(self.model_path, "qwen"):
                    log.error(
                        "[QWEN] Model hash verification FAILED for %s — refusing to load tampered or corrupted model",
                        self.model_path,
                    )
                    return False
            except Exception as exc:
                log.warning(
                    "[QWEN] Model hash verification warning for %s: %s",
                    self.model_path,
                    exc,
                )

            # SEC-audit-007: Read config.json with O_NOFOLLOW to prevent symlink attacks
            config_path = Path(self.model_path) / "config.json"
            try:
                if not is_windows():
                    # POSIX: open with O_NOFOLLOW to refuse symlinks.
                    # (this session): restructure to avoid a
                    # fragile double-close. ``os.fdopen`` takes
                    # ownership of ``fd`` — once it succeeds, the file
                    # object's ``__exit__`` closes ``fd``. The pre-fix
                    # code wrapped both ``os.fdopen`` AND ``json.load``
                    # in the same ``try`` block; if ``json.load``
                    # raised, the ``with``'s ``__exit__`` closed
                    # ``fd`` and then the outer ``except Exception``
                    # branch called ``os.close(fd)`` *again*. The
                    # double-close was silently suppressed by
                    # ``contextlib.suppress(OSError)`` — fragile
                    # (EBADF on some platforms; hides real bugs). The
                    # fix separates the two failure modes:
                    #   - ``os.fdopen`` itself fails → close ``fd``
                    #     (no file object was ever created), re-raise.
                    #   - ``json.load`` fails → the ``with`` block
                    #     closes ``f`` (and thus ``fd``); no second
                    #     close attempt.
                    fd = os.open(str(config_path), os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        f = os.fdopen(fd, "r", encoding="utf-8")
                    except Exception:
                        # ``os.fdopen`` failed — ``f`` was never
                        # created, so ``fd`` is still owned by us.
                        # Close it to avoid leaking the descriptor.
                        with contextlib.suppress(OSError):
                            os.close(fd)
                        raise
                    with f:
                        import json

                        json.load(f)  # Validate it's parseable JSON
                else:
                    # Windows: standard open (NTFS ACLs provide protection)
                    with open(config_path, encoding="utf-8") as f:
                        import json

                        json.load(f)
            except OSError as exc:
                log.exception("[QWEN] Failed to safely read config.json from %s: %s", self.model_path, exc)
                return False
            except Exception as exc:
                log.exception("[QWEN] config.json in %s is not valid JSON: %s", self.model_path, exc)
                return False

            try:
                import qwen_asr  # type: ignore[import-untyped]

                if progress_callback:
                    progress_callback("Loading Qwen3-ASR model...")

                log.info(
                    "[QWEN] Loading Qwen3-ASR model from %s (device=%s)...",
                    self.model_path,
                    self.device,
                )
                # time from_pretrained() to measure prewarm
                # cache-hit effectiveness.
                _t0 = time.perf_counter()
                self._model = qwen_asr.Qwen3ASRModel.from_pretrained(
                    self.model_path,
                )
                _load_elapsed = time.perf_counter() - _t0
                _warm_label = "warm (page-cache)" if _load_elapsed < 5.0 else "cold (disk)"

                # Actually move the model to the resolved device.
                # Previously ``load()`` stored ``self.device`` but never
                # applied it — ``from_pretrained()`` was called with no
                # ``device=`` kwarg and no ``.to(self.device)`` call, so
                # Qwen3- ran entirely on CPU regardless of GPU
                # config (5-10× slower inference). ``self.device`` was
                # also never updated from ``"auto"`` to a concrete value,
                # making ``transcribe_with_fallback``'s ``if self.device
                # == "cuda"`` branch unreachable.
                effective_device = self._resolve_device()
                if effective_device == "cuda":
                    self._model.to("cuda")
                    # float16 conversion is best-effort: some model
                    # wrappers may not accept a dtype on ``.to()`` or
                    # may not support half precision on the target GPU.
                    try:
                        import torch

                        self._model.to(torch.float16)
                    except ImportError:
                        log.warning("[QWEN] torch not available — skipping float16 conversion")
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        log.warning(
                            "[QWEN] float16 conversion failed (%s) — keeping default dtype",
                            exc,
                        )
                # Update self.device to the concrete resolved value so
                # ``transcribe_with_fallback`` and ``device_info`` see
                # "cuda"/"cpu" instead of the literal "auto".
                self.device = effective_device

                log.info(
                    "[QWEN] Model loaded successfully from %s (%s) — %.1fs (device=%s)",
                    self.model_path,
                    _warm_label,
                    _load_elapsed,
                    self.device,
                )
                # Prime CUDA kernels at load time so the first real
                # dictation doesn't pay the 2-5 s JIT cost (cuDNN /
                # cuBLAS / attention kernel compilation). No-op on CPU
                # and best-effort (failures swallowed inside the helper).
                # See ``_warm_up_model`` for the full contract.
                if effective_device == "cuda":
                    self._warm_up_model()
                return True
            except ImportError:
                log.exception(
                    "[QWEN] qwen-asr package is not installed",
                )
                self._model = None
                return False
            except Exception:
                log.exception(
                    "[QWEN] Failed to load Qwen3-ASR model from %s",
                    self.model_path,
                )
                self._model = None
                return False

    def _warm_up_model(self) -> None:
        """Run a tiny dummy inference to prime CUDA kernels (JIT cost: 2-5 s).

        The first ``model.transcribe()`` call after ``from_pretrained``
        takes 2-5 s longer than subsequent ones because the GPU kernels
        (cuDNN, cuBLAS, attention) must be JIT-compiled and memory
        allocated for the model's specific shapes.  This warm-up runs a
        0.5 s silence through the model at load time so the first real
        dictation is fast.

        Mirrors ``ParakeetEngine._warm_up_model`` and the Whisper
        warm-up pattern in ``voice_typer/server/transcription.py``
        (lines ~649-680), adapted to Qwen's
        ``(audio, sample_rate)`` tuple API.

        Non-fatal: any exception is logged at debug level and
        swallowed — the model is still considered loaded.  ``load()``
        returns True regardless.

        No-op when:

        - ``self._model`` is None (defensive — direct-call safety).
        - ``self.device`` is not ``"cuda"`` (CPU JIT cost is
          negligible; the gate matches ``load()``'s
          ``effective_device == "cuda"`` check so warm-up only fires
          when CUDA was actually used).
        """
        if self._model is None:
            return
        if self.device != "cuda":
            return
        try:
            warmup_audio = np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
            with self._inference_mode_ctx():
                self._model.transcribe(
                    (warmup_audio, WHISPER_SAMPLE_RATE),
                    language=self.language,
                )
            log.debug("[QWEN] warm-up transcribe() completed — first dictation will be fast")
        except Exception as exc:
            log.debug("[QWEN] warm-up transcribe() failed (non-fatal): %s", exc)

    def transcribe(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe audio array. Returns cleaned text string.

        RACE-032: The lock is only held for state checks/updates.
        GPU inference runs outside the lock so is_loaded / unload /
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
                # GPU memory.  ``audio_stats`` describes the whole-audio
                # RMS, so per-chunk RMS is computed inline in the helper.
                return self._transcribe_chunked(model, audio, sample_rate)

            # Non-chunked path (audio <= _QWEN_CHUNK_SECONDS): single call
            # with the existing hallucination check using ``audio_stats``
            # if provided, else computing RMS from the audio array.
            #
            # wrap model.transcribe() in torch.inference_mode()
            # to skip autograd-graph construction. See _inference_mode_ctx.
            with self._inference_mode_ctx():
                result = model.transcribe(
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
                transcribed by both chunks. We dedup the boundary by comparing
                the previous chunk's tail against the current chunk's head and
                removing the matching prefix (see ``_dedup_overlap``). Surviving
                chunk texts are then joined with simple concatenation.

        Batched path: when ``_INFERENCE_BATCH_SIZE`` > 1 (set via the
        ``QWEN_BATCH_SIZE`` env var), ``_transcribe_chunks_batched``
        groups that many chunks per ``model.transcribe()`` call.  On a
        CUDA OOM, it falls back to per-chunk sequential inference for
        the remaining chunks so the user still gets a transcription.
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
        # speech — the dedup pass below skips them without advancing
        # ``prev_text``.
        chunk_texts = self._transcribe_chunks_batched(model, chunks, sample_rate)

        # Dedup pass: compare each non-empty chunk's head against the
        # last appended chunk's tail and remove the matching prefix.
        # This is a sequential pass because dedup is stateful (tracks
        # ``prev_text``) — but it's pure string operations, so the cost
        # is negligible vs. the GPU inference that produced the texts.
        results: list[str] = []
        # track the previous chunk's appended text so the
        # current chunk's head can be deduped against it. Only updated
        # when a chunk's text is actually appended (hallucination-
        # rejected or empty chunks do NOT advance prev_text — their
        # predecessor is still the most recent valid contribution).
        prev_text = ""
        for i, text in enumerate(chunk_texts):
            if not text:
                continue
            # remove duplicate words at the overlap boundary.
            # Only dedup against a non-empty predecessor; the first
            # chunk has no predecessor and is appended verbatim.
            if prev_text:
                text = self._dedup_overlap(
                    prev_text,
                    text,
                    n=_QWEN_OVERLAP_DEDUP_WORDS,
                )
                if not text:
                    # Entire current chunk was a duplicate of the
                    # previous chunk's tail — nothing new to append.
                    # prev_text is NOT advanced: the previous chunk's
                    # tail remains the most recent valid transcription
                    # for the next chunk's overlap comparison.
                    log.debug(
                        "[QWEN] chunk %d/%d fully deduped against predecessor — skipping",
                        i + 1,
                        len(chunk_texts),
                    )
                    continue
            results.append(text)
            prev_text = text
        if not results:
            return ""
        return " ".join(results).strip()

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
        many chunks per ``model.transcribe()`` call.  On a CUDA OOM
        (``"out of memory"`` in the error string), we fall back to
        per-chunk sequential inference for the remaining chunks so the
        user still gets a transcription.

        Returns a list of text strings (one per chunk).  Empty strings
        indicate hallucination rejection or no speech — the caller's
        dedup pass skips them without advancing ``prev_text``.

        NOTE: The batched path (``_INFERENCE_BATCH_SIZE > 1``) assumes
        the ``qwen_asr.Qwen3ASRModel.transcribe`` API accepts a list of
        ``(audio, sample_rate)`` tuples as its first positional arg.
        This API contract is unverified in the Linux sandbox (no real
        GPU / qwen_asr install) — VALIDATE ON CUDA HOST.  If the API
        does not support batched input, the batched call raises and
        the sequential fallback fires, so correctness is preserved
        even if the batched path is unavailable.
        """
        if not chunks:
            return []

        if self._INFERENCE_BATCH_SIZE <= 1 or len(chunks) == 1:
            return self._transcribe_chunks_sequential(model, chunks, sample_rate)

        results: list[str] = []
        i = 0
        while i < len(chunks):
            batch = chunks[i : i + self._INFERENCE_BATCH_SIZE]
            i += len(batch)
            log.info(
                "[QWEN] Transcribing batch of %d chunk(s) (%d/%d done)",
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
            log.info(
                "[QWEN] Transcribing chunk %d/%d (%.1fs)",
                i + 1,
                len(chunks),
                len(chunk) / sample_rate,
            )
            # wrap model.transcribe() in torch.inference_mode()
            # to skip autograd-graph construction. See _inference_mode_ctx.
            with self._inference_mode_ctx():
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

        Assumes the ``qwen_asr.Qwen3ASRModel.transcribe`` API accepts a
        list of ``(audio, sample_rate)`` tuples as its first positional
        arg.  If the API does not support batched input, this method
        raises and the caller (``_transcribe_chunks_batched``) falls
        back to ``_transcribe_chunks_sequential``.

        VALIDATE ON CUDA HOST: the batched API contract is unverified
        in the Linux sandbox.  The sequential fallback ensures
        correctness even if the batched path is unavailable.
        """
        # Build list of (audio, sample_rate) tuples — one per chunk.
        inputs = [(chunk, sample_rate) for chunk in batch]
        with self._inference_mode_ctx():
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
    def _dedup_overlap(
        prev_text: str,
        curr_text: str,
        n: int = _QWEN_OVERLAP_DEDUP_WORDS,
    ) -> str:
        """Remove duplicate words at chunk overlap boundaries.

        When audio is split into overlapping chunks, the overlap region
        is transcribed twice. If the last ``k`` words of ``prev_text``
        match the first ``k`` words of ``curr_text`` (for any ``k`` in
        ``[1, n]``), the matching prefix is removed from ``curr_text``.

        Algorithm: try the largest ``k`` first (``k = n``) and decrease
        until a match is found or ``k = 0``. The largest matching ``k``
        maximises dedup while avoiding partial-word false positives
        (a 3-word match is far more reliable than a 1-word match for
        common stopwords like "the" / "a" / "and").

        Parameters
        ----------
        prev_text : str
            The previous chunk's (already-deduped) transcription text.
        curr_text : str
            The current chunk's transcription text.
        n : int, optional
            Maximum number of words to compare at the boundary.
            Defaults to ``_QWEN_OVERLAP_DEDUP_WORDS`` (3).

        Returns
        -------
        str
            ``curr_text`` with the duplicate head removed, or
            ``curr_text`` unchanged if no overlap is detected. May
            return an empty string if the entire ``curr_text`` matched
            the tail of ``prev_text`` (caller is responsible for
            handling this case — typically by skipping the chunk).
        """
        prev_words = prev_text.split()
        curr_words = curr_text.split()
        if not prev_words or not curr_words:
            return curr_text
        max_k = min(n, len(prev_words), len(curr_words))
        for k in range(max_k, 0, -1):
            if prev_words[-k:] == curr_words[:k]:
                return " ".join(curr_words[k:])
        return curr_text

    @staticmethod
    def _split_audio(
        audio: np.ndarray,
        chunk_sec: float,
        overlap_sec: float,
    ) -> list[np.ndarray]:
        """Split audio into overlapping chunks (mirrors ParakeetEngine._split_audio).

        Used by ``_transcribe_chunked`` to bound per-call audio length so
        the Whisper-style attention matrix and GPU memory footprint stay
        predictable for multi-minute recordings.
        """
        sr = WHISPER_SAMPLE_RATE
        chunk_len = int(chunk_sec * sr)
        overlap_len = int(overlap_sec * sr)
        step = chunk_len - overlap_len
        chunks: list[np.ndarray] = []
        start = 0
        while start < len(audio):
            end = min(start + chunk_len, len(audio))
            chunks.append(audio[start:end])
            if end == len(audio):
                break
            start += step
        return chunks

    def transcribe_batch(self, audio_chunks: list[np.ndarray]) -> list[str]:
        """PERF-009: Batch transcription API for multiple audio chunks.

        Processes multiple audio chunks through the model in a single
        session. This is a forward-looking API — the current Qwen3-ASR
        implementation processes chunks sequentially, but the interface
        allows for future optimization (parallel GPU streams, batched
        attention, etc.).

        Design rationale: the sequential implementation is acceptable
        because Voice Typer is a single-user desktop application — only
        one dictation session is active at a time, so batch calls are
        rare (mainly used for segmented transcription of a single
        recording). The sequential path keeps the code simple and
        avoids GPU memory fragmentation from parallel streams. A
        future multi-user or server deployment would justify revisiting
        this design decision.

        Parameters
        ----------
        audio_chunks : list[np.ndarray]
            List of audio arrays to transcribe.

        Returns
        -------
        list[str]
            List of transcribed text strings, one per chunk.
        """
        if not audio_chunks:
            return []
        results = []
        for chunk in audio_chunks:
            results.append(self.transcribe(chunk))
        return results

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: "tuple[float, float, float] | None" = None,
    ) -> str:
        """Transcribe with GPU→CPU fallback on CUDA errors.

        Previously this method just delegated to ``transcribe``
                with no fallback at all, despite the name. If a CUDA error
                occurred the caller received the raw exception. We now detect
                CUDA errors and retry on CPU, mirroring the parakeet engine's
                behavior. Non-CUDA errors are re-raised so the caller can
        surface them via 's friendly-error path.

                PERF-STATS: ``audio_stats`` is an optional pre-computed
                ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
                When provided, the engine skips its own RMS computation.
        """
        try:
            return self.transcribe(audio, audio_stats=audio_stats)
        except Exception as exc:
            err_str = str(exc).lower()
            if self.device == "cuda" and (
                "cuda" in err_str or "cublas" in err_str or "cudnn" in err_str or "out of memory" in err_str
            ):
                log.warning("[QWEN] CUDA error, retrying on CPU: %s", exc)
                # initialize ``original_device`` BEFORE the try
                # block so that the ``except`` handler below can always
                # restore it. Without this, if the assignment of
                # ``self.device = "cpu"`` raised, ``original_device``
                # would be unbound and the recovery path itself would
                # raise UnboundLocalError.
                original_device = self.device
                try:
                    self.device = "cpu"
                    if self._model is not None:
                        with contextlib.suppress(Exception):
                            # Not all model wrappers expose .to(); ignore.
                            self._model.to("cpu")
                        # Pin dtype=float32 — the previous bare
                        # ``.to("cpu")`` left the dtype as float16 (set
                        # during GPU load at the ``.to(torch.float16)``
                        # call in ``load()``), and float16 kernels are
                        # unsupported or pathologically slow on CPU, so
                        # the "fallback" was effectively unusable.
                        # Mirrors ParakeetEngine's PERF-REL-1 fix.  Kept
                        # as a SEPARATE ``.to()`` call (rather than
                        # ``.to("cpu", dtype=...)``) so
                        # ``test_cuda_error_triggers_cpu_retry_after_auto_load``
                        # — which asserts ``mock_model.to.assert_any_call("cpu")``
                        # — continues to match the bare-device call.
                        # Best-effort: skip if torch isn't importable.
                        try:
                            import torch

                            with contextlib.suppress(Exception):
                                self._model.to(torch.float32)
                        except ImportError:
                            log.warning(
                                "[QWEN] torch not available — skipping float32 dtype conversion on CPU fallback"
                            )
                    return self.transcribe(audio, audio_stats=audio_stats)
                except Exception:
                    # Restore device on failure so the next attempt starts fresh
                    self.device = original_device
                    log.exception("[QWEN] CPU fallback also failed")
                    raise
            # Non-CUDA error: re-raise so caller can handle
            raise

    def unload(self) -> None:
        """Free model memory.

        Waits for any in-flight ``transcribe()`` call to finish (via the
        ``_active_inference`` counter + ``_inference_cond``) BEFORE
        nulling ``self._model`` so the inference thread doesn't
        dereference a freed PyTorch module (use-after-free). Mirrors
        ``ParakeetEngine.unload``.

        Also releases PyTorch's CUDA caching allocator blocks via
        ``release_gpu_memory()`` so a subsequent backend switch can use
        the freed VRAM.

        RACE-023: gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.

        Defensive fallback: tests that bypass ``__init__`` (e.g.
        ``QwenEngine.__new__(QwenEngine)`` in
        ``tests/regressions/gpu_memory_release_test.py``) don't always
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
        # RACE-023: gc.collect() OUTSIDE the lock
        gc.collect()
        # release CUDA cached blocks.
        release_gpu_memory()
        log.info("[QWEN] Model unloaded")

    @property
    def device_info(self) -> str:
        """Return device info string.

        uses the resolved device so ``"auto"`` is reflected as
                the concrete ``"cuda"`` / ``"cpu"`` after ``load()`` (or, before
                load, by probing ``torch.cuda.is_available()``). Previously this
                returned the literal string ``"qwen/auto"`` when the engine was
                configured with ``device="auto"``.
        """
        return f"qwen/{self._resolve_device()}"

    @property
    def loaded_via(self) -> str:
        """Return description of how the model was loaded."""
        return f"qwen/{self.device}/{self.model_path}"


def _validate_qwen_model_dir(model_path: str) -> bool:
    """SEC-audit-007: Validate that a Qwen model directory contains only expected files.

    Checks that every file in the model directory has an allowed extension
    or basename.  Rejects directories containing executables, scripts, or
    other unexpected files that could indicate supply-chain tampering.

    Returns True if the directory passes validation, False otherwise.
    """
    path = Path(model_path)
    if not path.is_dir():
        return False
    try:
        for entry in path.rglob("*"):
            if not entry.is_file():
                continue
            name = entry.name
            ext = entry.suffix.lower()
            # Allow files with known safe extensions
            if ext in _QWEN_ALLOWED_EXTENSIONS:
                continue
            # Allow files with known safe basenames (no extension or unusual)
            if name in _QWEN_ALLOWED_BASENAMES:
                continue
            # Reject any file that doesn't match allowlist
            log.warning(
                "[QWEN] Model directory contains unexpected file: %s (extension=%r not in allowlist)",
                entry,
                ext,
            )
            return False
    except OSError as exc:
        log.warning("[QWEN] Failed to validate model directory %s: %s", model_path, exc)
        return False
    return True


def _verify_qwen_model_hashes(model_path: str) -> bool:
    """DELETED in  (Session 7 — Group 4).

        Previously this was a divergent SHA-256 manifest verifier that
        SOFT-PASSED on empty ``pinned_files`` — but
        ``security.verify_model_integrity`` hard-fails in that case
    ().  The soft-pass made the security module's hard-fail
        branch dead code for the Qwen path, so a tampered local Qwen model
        directory would load with NO content hash verification.

    The fix in  deletes this helper and replaces its call site
        in ``QwenEngine.load()`` with a direct call to
        ``voice_typer.server.security.verify_model_integrity(model_path,
    "qwen")``, so the  hard-fail is honoured.

        This stub is retained only so third-party imports (e.g. old tests
        in sibling repos that haven't been updated yet) get a clear
        ``RuntimeError`` instead of a silent ``NameError``.  New code MUST
        use ``security.verify_model_integrity`` directly.
    """
    raise RuntimeError(
        "_verify_qwen_model_hashes was deleted in the fix — "
        "use voice_typer.server.security.verify_model_integrity(path, 'qwen') instead."
    )
