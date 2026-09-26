"""Cached model-status computation for the Models page."""

from __future__ import annotations

import logging
import time

from ._constants import _MODEL_STATUS_CACHE_TTL_S

log = logging.getLogger(__name__)


class StatusMixin:
    def get_model_status(self) -> dict[str, object]:
        """Results are cached for ``_MODEL_STATUS_CACHE_TTL_S`` seconds."""
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
        """Extracted from :meth:`get_model_status` so the cache check and
        the (slower) status computation stay independently readable."""
        import os

        from voice_typer.server.config import _config_dir

        config = self._app.config
        status: dict[str, object] = {}

        # Whisper models. Check ALL models from the registry, using
        # PARTIAL-DOWNLOAD HONESTY: the completeness answer comes from the
        from voice_typer.server import model_availability
        from voice_typer.server.model_registry import MODEL_REGISTRY, get_model_metadata

        for meta in MODEL_REGISTRY.values():
            if meta.backend not in ("whisper", "distil-whisper"):
                continue
            downloaded = model_availability.is_available(meta.repo_id, _config_dir())
            status[meta.name] = {
                "downloaded": downloaded,
            }

        # Qwen model, check both the configured path AND the HF cache dir.
        qwen_path = getattr(config, "qwen_model_path", None)
        qwen_meta = get_model_metadata("qwen")
        if qwen_meta is not None:
            # The loader's own local-only snapshot probe (honest answer
            qwen_in_cache = model_availability.is_available(qwen_meta.repo_id, _config_dir())
        else:
            qwen_in_cache = False
        status["qwen"] = {
            "downloaded": bool(qwen_path and os.path.isdir(qwen_path)) or qwen_in_cache,
            # The Qwen backend is ONNX-only (qwen_onnx_model.py);
            "deps_ok": True,
        }

        # Parakeet model
        parakeet_path = getattr(config, "parakeet_model_path", None)
        parakeet_meta = get_model_metadata("parakeet")
        if parakeet_meta is not None:
            parakeet_in_cache = model_availability.is_available(parakeet_meta.repo_id, _config_dir())
        else:
            parakeet_in_cache = False
        status["parakeet"] = {
            "downloaded": bool(parakeet_path and os.path.isdir(parakeet_path)) or parakeet_in_cache,
            # The Parakeet backend is ONNX-only (parakeet_engine.py via
            "deps_ok": True,
        }

        return status

    def _invalidate_model_status_cache(self) -> None:
        """Drop the cached model-status dict."""
        from voice_typer.server import model_availability

        model_availability.invalidate()
        with self._model_status_cache_lock:
            self._model_status_cache = None
            self._model_status_cache_ts = 0.0
