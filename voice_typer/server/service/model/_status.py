"""Cached model-status computation for the Models page."""

from __future__ import annotations

import logging
import time

from ._constants import _MODEL_STATUS_CACHE_TTL_S

log = logging.getLogger(__name__)


class StatusMixin:
    def get_model_status(self) -> dict[str, object]:
        """Return the model download/dependency status for each ASR backend.

        PERF-10 / SVC-9: results are cached for ``_MODEL_STATUS_CACHE_TTL_S``
        seconds so the renderer's ~2s poll doesn't re-stat the filesystem for
        every model on every call. The cache is invalidated immediately on any
        download/delete that changes on-disk model state via
        :meth:`_invalidate_model_status_cache`, so correctness is preserved
        (a completed download or deletion is reflected on the next poll, well
        within the TTL window). Returns the *same* cached dict object within
        the TTL to satisfy callers that compare identity.
        """
        now = time.monotonic()
        with self._model_status_cache_lock:
            if self._model_status_cache is not None and (now - self._model_status_cache_ts) < _MODEL_STATUS_CACHE_TTL_S:
                return self._model_status_cache
        status = self._compute_model_status()
        with self._model_status_cache_lock:
            self._model_status_cache = status
            self._model_status_cache_ts = now
        return status

    def _compute_model_status(self) -> dict[str, object]:
        """Compute the model status from the filesystem (no caching).

        PERF-10 / SVC-9: extracted from :meth:`get_model_status` so the
        expensive per-model directory checks + dependency probes run at most
        once per TTL window.
        """
        import os

        from voice_typer.server.config import _config_dir

        config = self._app.config
        status = {}

        # Whisper models — check ALL models from the registry, using
        # the same cache directory that download_model writes to.
        from voice_typer.server.model_registry import MODEL_REGISTRY, get_model_metadata

        cache_dir = os.path.join(str(_config_dir()), "huggingface", "hub")
        # SVC-9 / PERF-10: stat the cache_dir ROOT once (hoisted above the
        # loop) instead of re-statting it on every model iteration.
        cache_dir_exists = os.path.isdir(cache_dir)
        # PARTIAL-DOWNLOAD HONESTY: the completeness answer comes from the
        # loader's own local-only snapshot probe — a bare ``models--<repo>``
        # directory is created at download START, so a paused / cancelled /
        # killed download must NOT report ``downloaded: True`` (the user
        # would see a "Select" button for a model that cannot load).
        from voice_typer.server.transcription_download import (
            is_model_snapshot_complete,
        )

        for meta in MODEL_REGISTRY.values():
            if meta.backend not in ("whisper", "distil-whisper"):
                continue
            downloaded = cache_dir_exists and is_model_snapshot_complete(meta.repo_id)
            status[meta.name] = {
                "downloaded": downloaded,
                "deps_ok": True,  # faster-whisper is always available
            }

        # Qwen model — check both the configured path AND the HF cache dir.
        qwen_path = getattr(config, "qwen_model_path", None)
        qwen_meta = get_model_metadata("qwen")
        if qwen_meta is not None:
            # The loader's own local-only snapshot probe (honest answer
            # for paused / cancelled / killed downloads).
            qwen_in_cache = cache_dir_exists and is_model_snapshot_complete(qwen_meta.repo_id)
        else:
            qwen_in_cache = False
        status["qwen"] = {
            "downloaded": bool(qwen_path and os.path.isdir(qwen_path)) or qwen_in_cache,
            # The Qwen backend is ONNX-only (qwen_onnx_model.py);
            # onnxruntime is a base dependency, so there is no pip
            # package gate anymore (the old qwen_asr/torch probe was
            # removed with the torch engine, 2026-08-15).
            "deps_ok": True,
        }

        # Parakeet model
        parakeet_path = getattr(config, "parakeet_model_path", None)
        parakeet_meta = get_model_metadata("parakeet")
        if parakeet_meta is not None:
            parakeet_in_cache = cache_dir_exists and is_model_snapshot_complete(parakeet_meta.repo_id)
        else:
            parakeet_in_cache = False
        status["parakeet"] = {
            "downloaded": bool(parakeet_path and os.path.isdir(parakeet_path)) or parakeet_in_cache,
            # The Parakeet backend is ONNX-only (parakeet_engine.py via
            # onnx-asr); onnxruntime + onnx-asr are base dependencies,
            # so there is no pip package gate anymore (the old torch
            # probe was removed with the torch engine, 2026-08-15).
            "deps_ok": True,
        }

        return status

    def _invalidate_model_status_cache(self) -> None:
        """PERF-10 / SVC-9: drop the cached model-status dict.

        Called whenever on-disk model state may have changed (model
        downloaded or deleted). The next :meth:`get_model_status` call
        recomputes from the filesystem and re-arms the TTL cache. Safe to
        call when no cache is populated yet.
        """
        with self._model_status_cache_lock:
            self._model_status_cache = None
            self._model_status_cache_ts = 0.0
