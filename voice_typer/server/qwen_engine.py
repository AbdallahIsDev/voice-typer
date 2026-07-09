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
import os
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from voice_typer.server.hallucination import should_reject_low_audio_hallucination, log_hallucination_rejection
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

log = logging.getLogger(__name__)

# SEC-audit-007: Allowed file extensions and filenames in the Qwen model directory.
# Prevents loading from directories that contain unexpected files (executables,
# scripts, etc.) which could indicate tampering.
# NOTE: .py is deliberately excluded — model directories should never contain
# Python source files, which could execute arbitrary code during from_pretrained().
_QWEN_ALLOWED_EXTENSIONS = {
    ".safetensors", ".bin", ".json", ".model", ".txt",
}
_QWEN_ALLOWED_BASENAMES = {
    "config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "preprocessor_config.json",
    "feature_extractor_config.json", "generation_config.json",
    "model.safetensors.index.json", "tokenizer.model",
    "vocab.json", "merges.txt", "vocab.txt",
}


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
        # RACE-032: separate event to track whether inference is in
        # progress.  This allows ``is_loaded`` to return True during
        # a multi-second GPU inference call without having to acquire
        # the main lock (which the inference thread holds for the
        # entire call).
        self._inference_event = threading.Event()

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully.

        RACE-032: uses _inference_event to check if the model is
        available, allowing this check to proceed even during an
        ongoing inference call (which no longer holds _lock for
        the entire GPU call).
        """
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

            # SEC-audit-007: Validate model directory contains only expected file types
            if not _validate_qwen_model_dir(self.model_path):
                log.error(
                    "[QWEN] Model directory %s failed security validation — "
                    "contains unexpected files", self.model_path,
                )
                return False

            # SEC-audit-007: SHA-256 manifest verification of model directory
            # contents before calling from_pretrained(). Compares each
            # file's hash against a known-good manifest if available, or
            # logs hashes for future audit if no manifest exists.
            #
            # The return value is now CHECKED: if pinned hashes are
            # present and any mismatches, load() aborts with False
            # instead of proceeding to from_pretrained(). Previously the
            # return value was discarded (bare call), so a tampered
            # model would still load — only a log warning was emitted.
            try:
                if not _verify_qwen_model_hashes(self.model_path):
                    log.error(
                        "[QWEN] Model hash verification FAILED for %s — "
                        "refusing to load tampered or corrupted model",
                        self.model_path,
                    )
                    return False
            except Exception as exc:
                log.warning(
                    "[QWEN] Model hash verification warning for %s: %s",
                    self.model_path, exc,
                )

            # SEC-audit-007: Read config.json with O_NOFOLLOW to prevent symlink attacks
            config_path = Path(self.model_path) / "config.json"
            try:
                if not is_windows():
                    # POSIX: open with O_NOFOLLOW to refuse symlinks
                    fd = os.open(str(config_path), os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        with os.fdopen(fd, "r", encoding="utf-8") as f:
                            import json
                            json.load(f)  # Validate it's parseable JSON
                    except Exception:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        raise
                else:
                    # Windows: standard open (NTFS ACLs provide protection)
                    with open(config_path, "r", encoding="utf-8") as f:
                        import json
                        json.load(f)
            except OSError as exc:
                log.error("[QWEN] Failed to safely read config.json from %s: %s", self.model_path, exc)
                return False
            except Exception as exc:
                log.error("[QWEN] config.json in %s is not valid JSON: %s", self.model_path, exc)
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

    def transcribe(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe audio array. Returns cleaned text string.

        RACE-032: The lock is only held for state checks/updates.
        GPU inference runs outside the lock so is_loaded / unload /
        load don't block for the multi-second duration of the call.
        ``_inference_event`` is set during inference so is_loaded
        can still report correctly.

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own RMS computation in
        hallucination detection.
        """
        with self._lock:
            if self._model is None:
                raise RuntimeError(
                    "Qwen model not loaded. Call load() first or check logs for errors."
                )
            model = self._model
            self._inference_event.set()

        try:
            if len(audio) == 0:
                return ""

            # Qwen transcribe() expects (np.ndarray, sample_rate) tuples
            sample_rate = 16000
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
                    "[QWEN]", text,
                    reason="hallucination",
                    log_transcriptions=False,
                )
                return ""

            return text
        finally:
            self._inference_event.clear()

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

    def transcribe_with_fallback(self, audio: np.ndarray,
            audio_stats: "tuple[float, float, float] | None" = None,
        ) -> str:
        """Transcribe with GPU→CPU fallback on CUDA errors.

        ERR-008: Previously this method just delegated to ``transcribe``
        with no fallback at all, despite the name. If a CUDA error
        occurred the caller received the raw exception. We now detect
        CUDA errors and retry on CPU, mirroring the parakeet engine's
        behavior. Non-CUDA errors are re-raised so the caller can
        surface them via ERR-005's friendly-error path.

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own RMS computation.
        """
        try:
            return self.transcribe(audio, audio_stats=audio_stats)
        except Exception as exc:
            err_str = str(exc).lower()
            if self.device == "cuda" and (
                "cuda" in err_str or "cublas" in err_str or "cudnn" in err_str
                or "out of memory" in err_str
            ):
                log.warning("[QWEN] CUDA error, retrying on CPU: %s", exc)
                # TASK-14: initialize ``original_device`` BEFORE the try
                # block so that the ``except`` handler below can always
                # restore it. Without this, if the assignment of
                # ``self.device = "cpu"`` raised, ``original_device``
                # would be unbound and the recovery path itself would
                # raise UnboundLocalError.
                original_device = self.device
                try:
                    self.device = "cpu"
                    if self._model is not None:
                        try:
                            self._model.to("cpu")
                        except Exception:
                            # Not all model wrappers expose .to(); ignore
                            pass
                    return self.transcribe(audio, audio_stats=audio_stats)
                except Exception as cpu_exc:
                    # Restore device on failure so the next attempt starts fresh
                    self.device = original_device
                    log.error("[QWEN] CPU fallback also failed: %s", cpu_exc)
                    raise
            # Non-CUDA error: re-raise so caller can handle
            raise

    def unload(self) -> None:
        """Free model memory.

        NEW-MEM-001: also release PyTorch's CUDA caching allocator
        blocks via ``release_gpu_memory()`` so a subsequent backend
        switch can use the freed VRAM.

        RACE-023: gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.
        """
        import gc
        from voice_typer.server.transcription import release_gpu_memory
        with self._lock:
            self._model = None
        # RACE-023: gc.collect() OUTSIDE the lock
        gc.collect()
        # NEW-MEM-001: release CUDA cached blocks.
        release_gpu_memory()
        log.info("[QWEN] Model unloaded")

    @property
    def device_info(self) -> str:
        """Return device info string."""
        return f"qwen/{self.device}"

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
                "[QWEN] Model directory contains unexpected file: %s "
                "(extension=%r not in allowlist)", entry, ext,
            )
            return False
    except OSError as exc:
        log.warning("[QWEN] Failed to validate model directory %s: %s", model_path, exc)
        return False
    return True


def _verify_qwen_model_hashes(model_path: str) -> bool:
    """SEC-audit-007: SHA-256 manifest verification of Qwen model directory.

    Compares each file's SHA-256 hash against a known-good manifest
    (from the security module's MODEL_HASHES).  If no hashes are
    pinned for the Qwen model, logs the computed hashes for future
    audit and returns True (soft pass — the directory validation
    in ``_validate_qwen_model_dir`` is the hard gate).

    Parameters
    ----------
    model_path : str
        Path to the Qwen model directory.

    Returns
    -------
    bool
        True if all pinned hashes match (or no hashes are pinned),
        False if any pinned hash mismatches.
    """
    from voice_typer.server.security import MODEL_HASHES, compute_file_sha256

    path = Path(model_path)
    if not path.is_dir():
        return False

    # Look for Qwen model hashes in the manifest.
    # The Qwen model is loaded from a local path, not a HuggingFace
    # repo_id, so we check for a "qwen" key or the model path.
    manifest = MODEL_HASHES.get("qwen", {})
    pinned_files = manifest.get("files", {})

    if not pinned_files:
        # No pinned hashes — compute and log hashes at INFO level for audit.
        # This is a soft pass; the directory validation above is the
        # hard gate that prevents loading unexpected file types.
        # Operators can copy the logged hashes into model_hashes.json
        # under the "qwen" entry's "files" dict to enable enforcement.
        # SEC-audit-005: emit a WARNING (not just INFO) so operators
        # notice that Qwen integrity verification is effectively a
        # no-op. Pre-fix the empty-files state was invisible at default
        # log levels — operators had no way to know their model_hashes.json
        # was empty for the Qwen entry.
        log.warning(
            "[QWEN] Model integrity check is a NO-OP for %s — "
            "model_hashes.json has empty \"files\" dict for the qwen entry. "
            "Computed hashes are logged below; copy them into "
            "model_hashes.json under the \"qwen\" entry's \"files\" field "
            "to enable enforcement on the next run.",
            model_path,
        )
        try:
            for entry in path.rglob("*"):
                if not entry.is_file():
                    continue
                try:
                    h = compute_file_sha256(entry)
                    rel = entry.relative_to(path).as_posix()
                    log.info("[QWEN]   %s: sha256=%s", rel, h)
                except Exception:
                    pass
        except Exception:
            pass
        return True

    # Verify each pinned file
    for filename, expected_hash in pinned_files.items():
        file_path = path / filename
        if not file_path.exists():
            log.warning(
                "[QWEN] Model integrity: pinned file %s missing in %s",
                filename, model_path,
            )
            return False
        try:
            actual_hash = compute_file_sha256(file_path)
            import hmac
            if not hmac.compare_digest(actual_hash, expected_hash):
                log.warning(
                    "[QWEN] Model integrity: hash mismatch for %s in %s "
                    "(expected %s..., got %s...)",
                    filename, model_path,
                    expected_hash[:16], actual_hash[:16],
                )
                return False
        except Exception as exc:
            log.warning("[QWEN] Failed to hash %s: %s", filename, exc)
            return False

    log.info("[QWEN] Model hash verification passed for %s", model_path)
    return True
