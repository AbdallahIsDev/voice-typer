"""Session/provider selection, availability checks, model load/unload."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

from voice_typer.server.duration import format_duration

from ._constants import (
    _PARAKERT_ONNX_CACHE_DIR,
    _PARAKERT_ONNX_MODEL_NAME,
    _PARAKERT_ONNX_REPO_ID,
    _PARAKERT_QUANTIZATION,
    _PARAKERT_WEIGHTS_MB,
)

log = logging.getLogger(__name__)


class LoadMixin:
    @classmethod
    def _ensure_imports(cls) -> bool:
        """Lazily import ``onnx_asr`` + ``onnxruntime``.

        Returns ``True`` on success, ``False`` if either package is not
        """
        with cls._imports_lock:
            if cls._imports_loaded:
                return True
            _t0 = time.perf_counter()
            try:
                import onnx_asr
                import onnxruntime as ort

                cls._onnx_asr = onnx_asr
                cls._ort = ort
                cls._imports_loaded = True
                _elapsed = time.perf_counter() - _t0
                log.info(
                    "[PARAKEET] onnx_asr %s + onnxruntime %s imported%s",
                    getattr(onnx_asr, "__version__", "?"),
                    getattr(ort, "__version__", "?"),
                    format_duration(_elapsed),
                )
                return True
            except ImportError as exc:
                cls._imports_loaded = False
                log.warning(
                    "[PARAKEET] onnx_asr/onnxruntime import failed. Install onnx-asr + onnxruntime: %s",
                    exc,
                )
                return False

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if the ONNX backend can be loaded."""
        try:
            import onnx_asr  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def _select_providers(self, device: str) -> list[str]:
        """Map a device string to an ORT ``providers=`` list."""
        if device == "cuda":
            try:
                available = self._ort.get_available_providers() if self._ort is not None else []
            except Exception:
                available = []
            if "CUDAExecutionProvider" in available:
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]
            log.warning(
                "[PARAKEET] CUDAExecutionProvider not in available providers (%s), using CPU",
                available,
            )
            return ["CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    @staticmethod
    def _should_force_cpu() -> bool:
        """Check disk space on system drive, if under 500MB, force CPU."""
        from voice_typer.server.platform_utils import is_windows

        if not is_windows():
            return False
        try:
            import psutil

            system_drive = os.environ.get("SYSTEMDRIVE", "C:") + "\\"
            usage = psutil.disk_usage(system_drive)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < 500:
                log.warning(
                    "[PARAKEET] Only %d MB free on %s, forcing CPU (CUDA needs pagefile space to allocate GPU memory)",
                    free_mb,
                    system_drive,
                )
                return True
        except Exception:
            log.debug("[PARAKEET] _should_force_cpu disk space check failed (non-fatal)", exc_info=True)
        return False

    @staticmethod
    def _is_cached() -> bool:
        """Quick check if the Parakeet ONNX model is in the HF cache."""
        from voice_typer.server.config import _config_dir

        cache_root = _config_dir() / "huggingface" / "hub"
        model_dir = cache_root / _PARAKERT_ONNX_CACHE_DIR
        snapshots = model_dir / "snapshots"
        if not snapshots.is_dir():
            return False
        try:
            for entry in snapshots.iterdir():
                if not entry.is_dir():
                    continue
                if any(entry.glob("*.onnx")):
                    return True
        except OSError:
            log.debug("[PARAKEET] _is_cached snapshot iterdir failed (non-fatal)", exc_info=True)
        return False

    def load(self, progress_callback: Callable[[str], None] | None = None) -> bool:
        """Load the Parakeet ONNX model via ``onnx_asr.load_model(...)``."""
        log.info("[PARAKEET] load() entered, importing onnx-asr if needed")
        if not self._ensure_imports():
            if progress_callback:
                progress_callback("Missing dependencies: onnx-asr + onnxruntime")
            return False

        with self._lock:
            if self._model is not None:
                return True

            # Reset the one-time CPU-fallback notification flag on
            self._cpu_fallback_notified = False
            self._cpu_fallback_since = None
            self._cpu_transcribe_count = 0

            # Quick cache check, avoids calling onnx_asr.load_model(...)
            _cache_t0 = time.perf_counter()
            _cached = self._is_cached()
            log.info(
                "[PARAKEET] model cache check: cached=%s%s",
                _cached,
                format_duration(time.perf_counter() - _cache_t0),
            )
            if not _cached:
                # The app NEVER auto-downloads models, downloading is
                from voice_typer.server.asr_errors import ModelNotDownloadedError

                raise ModelNotDownloadedError(
                    "The Parakeet model is not downloaded yet. "
                    "Open the Models page and click Download before using it.",
                    model_size="parakeet",
                    backend="parakeet",
                    repo_id=_PARAKERT_ONNX_REPO_ID,
                )

            # Verify model integrity (hash check), UNCONDITIONALLY on
            from voice_typer.server.config import _config_dir
            from voice_typer.server.security import verify_model_integrity

            cache_root = _config_dir() / "huggingface" / "hub"
            model_dir = cache_root / _PARAKERT_ONNX_CACHE_DIR
            verified_snapshot: str | None = None
            if model_dir.is_dir():
                verified = False
                verify_exc: Exception | None = None
                try:
                    for snapshot in (model_dir / "snapshots").iterdir():
                        if snapshot.is_dir() and verify_model_integrity(str(snapshot), _PARAKERT_ONNX_REPO_ID):
                            verified = True
                            verified_snapshot = str(snapshot)
                            break
                except OSError as exc:
                    verify_exc = exc
                if not verified:
                    log.error(
                        "[PARAKEET] Model integrity check failed%s for %s at %s. "
                        "Refusing to load tampered model. To fix: delete it from the Models page.",
                        f" (OSError: {verify_exc})" if verify_exc else "",
                        _PARAKERT_ONNX_REPO_ID,
                        model_dir,
                    )
                    if progress_callback:
                        progress_callback("Model integrity check failed; delete and re-download from the Models page.")
                    from voice_typer.server.asr_errors import ModelIntegrityError

                    raise ModelIntegrityError(
                        "The cached Parakeet model failed integrity verification. "
                        "Delete it and download it again from the Models page to recover.",
                        model_size="parakeet",
                        backend="parakeet",
                        repo_id=_PARAKERT_ONNX_REPO_ID,
                    )

            # Load ONNX model via onnx_asr.load_model(...), by TYPE
            try:
                if progress_callback:
                    progress_callback("Loading Parakeet TDT v3 ONNX model...")

                log.info("[PARAKEET] Loading ONNX model (device=%s)...", self.device)
                effective_device = self.device
                if effective_device == "cuda" and self._should_force_cpu():
                    effective_device = "cpu"

                providers = self._select_providers(effective_device)
                _load_start = time.perf_counter()

                # onnx-asr 0.12.0 exports ``load_model(...)``: there is
                self._onnx_model_dir = verified_snapshot
                self._model = self._onnx_asr.load_model(
                    _PARAKERT_ONNX_MODEL_NAME,
                    path=verified_snapshot,
                    quantization=_PARAKERT_QUANTIZATION,
                    providers=providers,
                )

                _elapsed = time.perf_counter() - _load_start
                _warm_label = "warm (page-cache)" if _elapsed < 5.0 else "cold (disk)"
                _read_speed_mbs = _PARAKERT_WEIGHTS_MB / max(_elapsed, 0.1)
                log.info(
                    "[PARAKEET] ONNX model loaded (%s, %.0f MB/s)%s",
                    _warm_label,
                    _read_speed_mbs,
                    format_duration(_elapsed),
                )
                if progress_callback:
                    progress_callback("Parakeet model ready")
                # Stash the effective providers so the GPU→CPU fallback
                self._effective_providers = providers
                return True

            except ImportError as exc:
                log.exception("[PARAKEET] onnx_asr package not installed")
                if progress_callback:
                    progress_callback(f"Missing dependency: {exc}")
                return False
            except KeyboardInterrupt:
                log.warning("[PARAKEET] Loading interrupted by user")
                if progress_callback:
                    progress_callback("Loading cancelled")
                return False
            except Exception as exc:
                log.exception("[PARAKEET] Failed to load model")
                if progress_callback:
                    progress_callback(f"Model load failed: {exc}")
                return False

    def _load_impl(self, *, providers: list[str]) -> bool:
        """Re-create the ONNX session (``onnx_asr.load_model``) with the given providers.

        Returns ``True`` on success, ``False`` if the new session could
        """
        if not self._ensure_imports():
            return False
        try:
            self._model = self._onnx_asr.load_model(
                _PARAKERT_ONNX_MODEL_NAME,
                path=self._onnx_model_dir,
                quantization=_PARAKERT_QUANTIZATION,
                providers=providers,
            )
            self._effective_providers = providers
            return True
        except Exception:
            log.exception(
                "[PARAKEET] Failed to recreate ONNX session with providers=%s",
                providers,
            )
            return False

    def _unload_impl(self) -> None:
        """Drop the loaded onnx-asr model reference (without acquiring"""
        with self._lock:
            self._model = None
