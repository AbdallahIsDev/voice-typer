"""Session/provider selection, availability checks, model load/unload."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

from ._constants import (
    _PARAKERT_ONNX_CACHE_DIR,
    _PARAKERT_ONNX_MODEL_NAME,
    _PARAKERT_ONNX_REPO_ID,
    _PARAKERT_QUANTIZATION,
    _PARAKERT_WEIGHTS_MB,
)

log = logging.getLogger(__name__)


class LoadMixin:
    # ── Import management ────────────────────────────────────────────

    @classmethod
    def _ensure_imports(cls) -> bool:
        """Lazily import ``onnx_asr`` + ``onnxruntime``.

        Returns ``True`` on success, ``False`` if either package is not
        installed. The lazy import keeps this module importable on
        systems without ``onnx-asr`` (the optional-deps pattern used
        throughout the project).

        Idempotent: re-entering after a successful import is a fast
        flag-check under the lock. Re-entering after a FAILED import
        re-attempts the import (so installing the package after the
        engine was first constructed takes effect on the next
        ``load()``).
        """
        with cls._imports_lock:
            if cls._imports_loaded:
                return True
            _t0 = time.perf_counter()
            try:
                import onnx_asr  # type: ignore[import-untyped, import-not-found]
                import onnxruntime as ort  # type: ignore[import-untyped]

                cls._onnx_asr = onnx_asr
                cls._ort = ort
                cls._imports_loaded = True
                _elapsed = time.perf_counter() - _t0
                log.info(
                    "[PARAKEET] onnx_asr %s + onnxruntime %s imported (%.2fs)",
                    getattr(onnx_asr, "__version__", "?"),
                    getattr(ort, "__version__", "?"),
                    _elapsed,
                )
                return True
            except ImportError as exc:
                cls._imports_loaded = False
                log.warning(
                    "[PARAKEET] onnx_asr/onnxruntime import failed — install onnx-asr + onnxruntime: %s",
                    exc,
                )
                return False

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if the ONNX backend can be loaded.

        Quick probe used by the registry / model_manager to decide
        whether the parakeet backend is usable on the current install
        (i.e. ``onnx_asr`` + ``onnxruntime`` are importable). Does NOT
        probe the model cache — that's :meth:`_is_cached`.
        """
        try:
            import onnx_asr  # type: ignore[import-untyped]  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    # ── Provider selection ──────────────────────────────────────────

    def _select_providers(self, device: str) -> list[str]:
        """Map a device string to an ORT ``providers=`` list.

        ``CUDAExecutionProvider`` is tried first when ``device == "cuda"``;
        if it's not available (CPU-only onnxruntime wheel, no GPU, no
        CUDA Toolkit DLLs on Windows), falls back to
        ``CPUExecutionProvider``. The fallback at *load time* is
        distinct from the *runtime* GPU→CPU fallback in
        :meth:`transcribe_with_fallback` — the latter recreates the
        session after a CUDA error during inference.
        """
        if device == "cuda":
            try:
                available = self._ort.get_available_providers() if self._ort is not None else []
            except Exception:
                available = []
            if "CUDAExecutionProvider" in available:
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]
            log.warning(
                "[PARAKEET] CUDAExecutionProvider not in available providers (%s) — using CPU",
                available,
            )
            return ["CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    # ── Disk-space / cache probes ───────────────────────────────────

    @staticmethod
    def _should_force_cpu() -> bool:
        """Check disk space on system drive — if under 500MB, force CPU.

        CUDA on Windows needs pagefile space to back GPU memory
        allocations. When the system drive is nearly full, Windows
        can't grow the pagefile, causing error 1455. This check avoids
        that error and gives a clean warning instead.

        Platform-qualified: the pagefile/CUDA-error-1455 failure mode
        is Windows-only (Linux/macOS don't use a Windows-style pagefile
        for GPU memory).
        """
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
                    "[PARAKEET] Only %d MB free on %s — forcing CPU (CUDA needs pagefile space to allocate GPU memory)",
                    free_mb,
                    system_drive,
                )
                return True
        except Exception:
            log.debug("[PARAKEET] _should_force_cpu disk space check failed (non-fatal)", exc_info=True)
        return False

    @staticmethod
    def _is_cached() -> bool:
        """Quick check if the Parakeet ONNX model is in the HF cache.

        Walks the ONNX repo's snapshot dir
        (``models--grikdotnet--parakeet-tdt-0.6b-fp16/``) for a
        ``*.onnx`` file. The engine is ONNX-only post-migration — the
        torch/safetensors cache (``nvidia/parakeet-tdt-0.6b-v3``) is no
        longer loadable and does NOT count as cached.
        """
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
        """Load the Parakeet ONNX model via ``onnx_asr.load_model(...)``.

        The app never downloads models automatically — the user must
        explicitly download the Parakeet weights (Models page Download
        button, or the onboarding wizard) before they can be loaded. If
        the model is not in the local HuggingFace cache, a
        ``ModelNotDownloadedError`` is raised so callers can direct the
        user to the Models page. A cached-but-tampered model raises
        ``ModelIntegrityError`` and is NOT deleted automatically.

        See PLAN_ONNX_INTEGRATION.md §3.3 (Option B-1). onnx-asr 0.12.0
        exports ``load_model(...)`` — there is NO ``onnx_asr.Model``
        class in any release (verified against 0.12.0 and main; only
        ``load_model`` + ``load_vad`` are exported).
        """
        log.info("[PARAKEET] load() entered — importing onnx-asr if needed")
        if not self._ensure_imports():
            if progress_callback:
                progress_callback("Missing dependencies: onnx-asr + onnxruntime")
            return False

        with self._lock:
            if self._model is not None:
                return True

            # Reset the one-time CPU-fallback notification flag on
            # every fresh ``load()``. A fallback that fired during a
            # previous transcription session must not silently suppress
            # the next session's notification — the user may have
            # restarted their GPU driver or freed VRAM in the meantime.
            self._cpu_fallback_notified = False
            self._cpu_fallback_since = None
            self._cpu_transcribe_count = 0

            # Quick cache check — avoids calling onnx_asr.load_model(...)
            # entirely when the model isn't on disk.
            _cache_t0 = time.perf_counter()
            _cached = self._is_cached()
            log.info(
                "[PARAKEET] model cache check: cached=%s (%.2fs)",
                _cached,
                time.perf_counter() - _cache_t0,
            )
            if not _cached:
                # The app NEVER auto-downloads models — downloading is
                # an explicit user action (Models page Download button,
                # or the onboarding wizard). Refuse to load and raise
                # the actionable error so the tray / IPC layer can
                # point the user at the Models page.
                from voice_typer.server.asr_errors import ModelNotDownloadedError

                raise ModelNotDownloadedError(
                    "The Parakeet model is not downloaded yet. "
                    "Open the Models page and click Download before using it.",
                    model_size="parakeet",
                    backend="parakeet",
                    repo_id=_PARAKERT_ONNX_REPO_ID,
                )

            # Verify model integrity (hash check) — UNCONDITIONALLY on
            # every load. The ~1-3s SHA-256 cost is acceptable vs the
            # multi-second ORT load time. On failure we hard-fail —
            # WITHOUT deleting the tampered files (deletion is an
            # explicit user action via the Models page Delete button).
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

            # Load ONNX model via onnx_asr.load_model(...) — by TYPE
            # name (``nemo-conformer-tdt``) + the verified local
            # snapshot dir (PLAN_ONNX_INTEGRATION.md §3.3 Option B-1).
            try:
                if progress_callback:
                    progress_callback("Loading Parakeet TDT v3 ONNX model...")

                log.info("[PARAKEET] Loading ONNX model (device=%s)...", self.device)
                effective_device = self.device
                if effective_device == "cuda" and self._should_force_cpu():
                    effective_device = "cpu"

                providers = self._select_providers(effective_device)
                _load_start = time.perf_counter()

                # onnx-asr 0.12.0 exports ``load_model(...)`` — there is
                # NO ``onnx_asr.Model`` class in any onnx-asr release
                # (verified against 0.12.0 and main; only ``load_model``
                # + ``load_vad`` are exported). We load by TYPE name
                # (``nemo-conformer-tdt``) + the verified local snapshot
                # dir so onnx-asr loads the integrity-verified files
                # instead of re-resolving the repo BY NAME.
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
                    "[PARAKEET] ONNX model loaded (%s) — total=%.1fs (%.0f MB/s)",
                    _warm_label,
                    _elapsed,
                    _read_speed_mbs,
                )
                if progress_callback:
                    progress_callback("Parakeet model ready")
                # Stash the effective providers so the GPU→CPU fallback
                # path knows what to switch FROM.
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

        Used by the GPU→CPU fallback path (§3.4) to recreate the session
        on CPU. Does NOT re-check the cache or run the integrity check
        — those already passed in the original :meth:`load` call. The
        model files are still on disk (the GPU session was loaded from
        them, at ``self._onnx_model_dir``); we just rebuild the ORT
        session with new providers.

        Returns ``True`` on success, ``False`` if the new session could
        not be created (logged at ERROR — caller raises
        ``TranscriptionBackendError``).
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
        """Drop the loaded onnx-asr model reference (without acquiring
        ``_inference_cond``).

        Used by the GPU→CPU fallback path. The full :meth:`unload` also
        waits for ``_active_inference == 0`` and runs gc / GPU memory
        release; this lighter variant is safe to call from inside the
        fallback path (which already holds the inference slot via
        ``_active_inference``).
        """
        with self._lock:
            self._model = None
