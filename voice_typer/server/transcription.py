"""Transcription facade over ASR engines."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.asr_errors import (
    ConsentRequiredError,  # noqa: F401  # re-exported for backward compat
    ModelIntegrityError,  # noqa: F401  # re-exported for backward compat
    ModelNotDownloadedError,
)

# PERF-COLDSTART-001: defer numpy (~250-335ms) to first use via lazy proxy.
from voice_typer.server.asr_utils import (  # noqa: F401
    _check_disk_space_for_download,
    _download_with_retry,
    _require_huggingface_consent,
    cleanup_hf_cache_dir,
    is_oom_error,
    release_gpu_memory,
)
from voice_typer.server.duration import format_duration
from voice_typer.server.hallucination import should_reject_low_audio_hallucination
from voice_typer.server.i18n import DEFAULT_LOCALE
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

np = lazy_module("numpy")

# Shared ASR helpers live in ``asr_utils``; re-exported here for compat.

# ``TranscriberProtocol`` lives in ``transcription_load.py``; re-exported for identity parity.
from voice_typer.server.transcription_cuda_probe import (  # noqa: E402
    probe_cuda_runtime as _probe_cuda_runtime_impl,
    warm_up_model as _warm_up_model_impl,
)

# Device-resolution bodies stay late-bound on this module for monkeypatch paths.
from voice_typer.server.transcription_device import (  # noqa: E402
    apply_auto_beam_size as _apply_auto_beam_size_impl,
    resolve_device as _resolve_device_impl,
    resolve_device_once as _resolve_device_once_impl,
    whisper_cpu_threads as _whisper_cpu_threads_impl,
)

# Download gate never downloads or deletes models automatically.
from voice_typer.server.transcription_download import (  # noqa: E402
    probe_cache as _probe_cache_impl,
    require_model_downloaded as _require_model_downloaded_impl,
    whisper_size_cached as _whisper_size_cached_impl,
)

# GPU→CPU fallback bodies dispatch through the engine object.
from voice_typer.server.transcription_fallback import (  # noqa: E402
    is_gpu_runtime_error as _is_gpu_runtime_error_impl,
    transcribe_with_fallback as _transcribe_with_fallback_impl,
    with_gpu_fallback as _with_gpu_fallback_impl,
)
from voice_typer.server.transcription_load import TranscriberProtocol  # noqa: F401, E402

# Segment-decode body lives in ``transcription_result``; historical alias kept.
from voice_typer.server.transcription_result import (  # noqa: E402
    transcribe_unlocked as _transcribe_unlocked_impl,
    transcribe_words_unlocked as _transcribe_words_unlocked_impl,
)

log = logging.getLogger(__name__)


# re-exported from ``voice_typer.server._audio_constants`` for
_nvidia_dll_path_handles: list[object] = []


# ``_MODEL_SIZE_MB`` + ``_DISK_SPACE_MARGIN_MB``,


# NVIDIA CUDA DLL path setup (Windows-only, gated by ``is_windows()``
from voice_typer.server.nvidia_dll_paths import (  # noqa: E402, F401
    _configure_nvidia_dll_paths,
    _configure_nvidia_dll_paths_locked,
    _cuda_runtime_available,
    _free_nvidia_dll_path_handles,
    _NvidiaDllPathManager,
)

_nvidia_dll_paths_configured = False
# RACE-029: module-level lock to serialize _configure_nvidia_dll_paths()
_nvidia_config_lock = threading.Lock()

# Singleton manager that encapsulates operations on the three
_nvidia_dll_paths = _NvidiaDllPathManager()


# Wide-beam default applied automatically on CUDA for non-tiny models.
AUTO_CUDA_BEAM_SIZE = 5


def _auto_beam_size(model_size: str, device: str) -> int:
    """Beam width used when the user has not configured one explicitly.

    Returns the wide accuracy-biased beam only for non-tiny models on a
    """
    if device != "cuda":
        return 1
    if str(model_size).lower().startswith("tiny"):
        return 1
    return AUTO_CUDA_BEAM_SIZE


class TranscriptionEngine:
    """Wraps faster-whisper model loading and transcription."""

    def __init__(
        self,
        # Canonical default: see ``model_registry.DEFAULT_MODEL_SIZE``.
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = "auto",
        language: str = DEFAULT_LOCALE,
        # Speed-biased default of 1, ~2x faster than beam_size=3-5 at
        beam_size: int = 1,
        best_of: int = 1,
        condition_on_previous_text: bool = False,
        config: Any = None,
    ):
        self.model_size = model_size
        self._configured_model_size = model_size
        self._loaded_model_size: str | None = None
        self.language = language
        # ``config.whisper_beam_size`` (when set to a non-default value)
        effective_beam_size = beam_size
        beam_explicitly_configured = beam_size > 1
        if config is not None:
            cfg_whisper_beam_size = getattr(config, "whisper_beam_size", None)
            if cfg_whisper_beam_size is not None and cfg_whisper_beam_size != 1:
                effective_beam_size = cfg_whisper_beam_size
                beam_explicitly_configured = True
        self.beam_size = effective_beam_size
        # When neither the legacy ``beam_size`` kwarg nor the preferred
        self._beam_size_auto = not beam_explicitly_configured
        self.best_of = best_of
        self.condition_on_previous_text = condition_on_previous_text
        self._model = None
        self._lock = threading.RLock()
        # counter + Condition so transcribe() can release the model
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        self._requested_device: str | None = device  # defer CUDA detection to load()
        self._device = "cpu"
        self._compute_type = "int8"
        # Abort token shared by the dictation pipeline's cancel path
        self._abort_event = threading.Event()
        # (RACE-023). Initialized here so attribute access never falls
        self._pending_gc_collect = False
        # Compact quality summary of the LAST completed transcription,
        self.last_quality_summary: dict[str, float] | None = None
        # store a reference to the app's Config dataclass so the

        # ``config`` may be None when constructed by callers that
        self.config = config

    def _resolve_device(self, device: str) -> tuple[str, str]:
        """Auto-detect best device and compute type."""
        return _resolve_device_impl(self, device)

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully."""
        return self._model is not None

    def request_abort(self) -> None:
        """Signal an in-flight transcription to abort as soon as possible."""
        self._abort_event.set()
        # Best-effort ctranslate2 interrupt. ``WhisperModel.model`` is
        try:
            inner = getattr(self._model, "model", None)
            if inner is not None and hasattr(inner, "interrupt"):
                inner.interrupt()
        except Exception:
            log.debug("[TRANSCRIBE] ctranslate2 interrupt() failed (non-fatal)", exc_info=True)

    def clear_abort(self) -> None:
        """Clear the abort token at the start of a fresh transcription cycle."""
        self._abort_event.clear()

    @property
    def device_info(self) -> str:
        return f"{self._device} ({self._compute_type})"

    @property
    def loaded_via(self) -> str:
        """Return a description of the device/model combo that was successfully loaded."""
        return f"{self._device}/{self._compute_type}/{self.model_size}"

    def load(self, progress_callback=None) -> None:
        """Load the Whisper model from the local cache (NEVER downloads)."""
        # Deferred CUDA detection. Run now (once) near load time
        self._resolve_device_once()
        self._require_model_downloaded(self.model_size, progress_callback)
        self._load_model_outside_lock(progress_callback=progress_callback)

    def _resolve_device_once(self):
        """Resolve the CUDA device if not already resolved."""
        _resolve_device_once_impl(self)

    def _apply_auto_beam_size(self) -> None:
        """Re-resolve ``self.beam_size`` when the user left it on auto."""
        _apply_auto_beam_size_impl(self)

    def _load_model_outside_lock(self, progress_callback=None):
        """Load model outside the lock so downloads don't block other threads."""
        with self._lock:
            if self._model is not None:
                return
            chain = self._build_fallback_chain()

        self._load_transcriber_impl(
            chain,
            acquire_lock=True,
            progress_callback=progress_callback,
            verb="Loading",
        )

    def _load_transcriber_impl(
        self,
        chain: list[tuple[str, str, str]],
        *,
        acquire_lock: bool,
        progress_callback=None,
        verb: str = "Loading",
    ) -> None:
        """Shared model-load body used by both _load_model_outside_lock"""
        # Map progressive verb → base form for error messages.
        verb_base = "load" if verb.lower() == "loading" else "reload"
        _configure_nvidia_dll_paths()
        from faster_whisper import WhisperModel

        last_error = None
        for device, compute_type, model_size in chain:
            try:
                # NEVER auto-download: skip fallback-chain entries whose
                if not self._whisper_size_cached(model_size):
                    log.warning(
                        "[MODEL] %s: model '%s' not in local cache, skipping "
                        "fallback entry (%s/%s). Download it from the Models page first.",
                        verb,
                        model_size,
                        device,
                        compute_type,
                    )
                    continue
                log.info(
                    "[MODEL] %s Whisper model '%s' on %s (%s)...",
                    verb,
                    model_size,
                    device,
                    compute_type,
                )
                if progress_callback:
                    progress_callback(f"{verb} model '{model_size}'...")
                # time WhisperModel construction to measure
                _t0 = time.perf_counter()
                # cpu_threads: CTranslate2 silently defaults to 4
                model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=_whisper_cpu_threads_impl(),
                    num_workers=1,
                )
                _load_elapsed = time.perf_counter() - _t0
                _warm_label = "warm (page-cache)" if _load_elapsed < 5.0 else "cold (disk)"
                if acquire_lock:
                    with self._lock:
                        if self._model is not None:
                            return
                        self._model = model
                        self._device = device
                        self._compute_type = compute_type
                        self._loaded_model_size = model_size
                        self.model_size = self._configured_model_size
                else:
                    # Caller already holds the lock.
                    self._model = model
                    self._device = device
                    self._compute_type = compute_type
                    self._loaded_model_size = model_size
                    self.model_size = self._configured_model_size
                # C-LOG-2: ``format_duration`` returns the suffix WITH
                log.info(
                    "[MODEL] Model %s via %s (%s)%s",
                    verb.lower(),
                    self.loaded_via,
                    _warm_label,
                    format_duration(_load_elapsed),
                )

                # CUDA probe: force a tiny transcription to smoke-test
                if self._device == "cuda" and acquire_lock:
                    self._probe_cuda_runtime(progress_callback)

                # PERF-007: warm-up inference with 0.5s of silence.
                self._warm_up_model()
                return
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Model %s failed on %s (%s) model=%s: %s",
                    verb.lower(),
                    device,
                    compute_type,
                    model_size,
                    exc,
                )
                if not acquire_lock:
                    self._model = None

        if last_error is None:
            # Every fallback-chain entry was skipped because its model is
            raise ModelNotDownloadedError(
                f"The Whisper model '{self.model_size}' is not downloaded yet. "
                "Open the Models page and click Download before using it.",
                model_size=self.model_size,
                backend="whisper",
            )
        raise RuntimeError(
            f"Failed to {verb_base} Whisper model on any device/model. Last error: {last_error}"
        ) from last_error

    def _build_fallback_chain(self) -> list[tuple[str, str, str]]:
        """Build the fallback chain for model loading."""
        chain: list[tuple[str, str, str]] = []
        chain.append((self._device, self._compute_type, self.model_size))
        if self._device != "cpu" or self._compute_type != "int8":
            chain.append(("cpu", "int8", self.model_size))
        if self.model_size != "tiny":
            chain.append(("cpu", "int8", "tiny"))
        chain.append(("cpu", "float32", "tiny"))
        return chain

    def _reload_under_lock(self):
        """Reload the model while already holding the lock (for GPU fallback)."""
        chain = self._build_fallback_chain()
        self._load_transcriber_impl(chain, acquire_lock=False, verb="Reloading")

    def _probe_cuda_runtime(self, progress_callback=None):
        """Probe CUDA with a real transcription to force early cuBLAS/cuDNN"""
        _probe_cuda_runtime_impl(self, progress_callback)

    def _warm_up_model(self) -> None:
        """Run a warm-up inference with silence to prime CUDA kernels."""
        _warm_up_model_impl(self)

    def _probe_cache(
        self,
        snapshot_download_fn,
        repo_id: str,
        revision: str,
        allow_patterns,
        model_size: str,
        progress_callback=None,
    ) -> tuple[str | None, bool]:
        """Phase 1: probe the HuggingFace cache (local-only)."""
        return _probe_cache_impl(
            self,
            snapshot_download_fn,
            repo_id,
            revision,
            allow_patterns,
            model_size,
            progress_callback=progress_callback,
        )

    def _require_model_downloaded(self, model_size: str, progress_callback=None) -> None:
        """Ensure the Whisper model is present in the local HF cache."""
        _require_model_downloaded_impl(self, model_size, progress_callback)

    def _whisper_size_cached(self, model_size: str) -> bool:
        """Local-only probe: is ``model_size`` fully present in the HF cache?"""
        return _whisper_size_cached_impl(self, model_size)

    def transcribe(self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None) -> str:
        """Transcribe audio array. Returns cleaned text string."""
        with self._lock:
            if self._model is None:
                raise RuntimeError("Model not loaded. Call load() first.")
            if len(audio) == 0:
                return ""
            self._active_inference += 1
        try:
            return self._transcribe_unlocked(audio, audio_stats=audio_stats)
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()

    def _transcribe_unlocked(self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None) -> str:
        """Thin delegate to ``transcription_result.transcribe_unlocked``."""
        return _transcribe_unlocked_impl(self, audio, audio_stats=audio_stats)

    def _run_deferred_gc(self) -> None:
        """Run the deferred gc.collect() + release_gpu_memory() cleanup.

        RACE-023: gc.collect() and ``release_gpu_memory()`` are deferred
        to OUTSIDE the lock (the fallback path sets
        ``_pending_gc_collect = True`` while still holding the lock;
        the caller drops the lock and then calls us). This avoids
        blocking ``is_loaded`` / ``transcribe`` for the 10-100ms that
        the deferred cleanup can take.

        Shared by ``_with_lock_and_deferred_gc`` (for ``transcribe_words``)
        and ``transcribe_with_fallback`` (which uses the inference-counter
        pattern and can't use the context manager directly).
        """
        if getattr(self, "_pending_gc_collect", False):
            self._pending_gc_collect = False
            try:
                import gc

                gc.collect()
                release_gpu_memory()
            except Exception:
                log.debug("[MODEL] GPU memory release failed", exc_info=True)

    @contextlib.contextmanager
    def _with_lock_and_deferred_gc(self):
        """Acquire ``self._lock`` for the body, then run deferred gc after."""
        with self._lock:
            yield
        self._run_deferred_gc()

    def _with_gpu_fallback(self, inner, audio, *args, **kwargs):
        """Run ``inner(audio, *args, **kwargs)`` with GPU→CPU fallback."""
        return _with_gpu_fallback_impl(self, inner, audio, *args, **kwargs)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
        local_engine: Any = None,
    ) -> str:
        """Transcribe with automatic CPU fallback on GPU runtime errors."""
        return _transcribe_with_fallback_impl(self, audio, audio_stats=audio_stats)

    def _transcribe_with_fallback_unlocked(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        # pass through the pre-computed stats to the CPU retry as well.
        return self._with_gpu_fallback(self._transcribe_unlocked, audio, audio_stats=audio_stats)

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0):
        """Transcribe audio array into word timings with a global offset."""
        with self._lock:
            if self._model is None:
                raise RuntimeError("Model not loaded. Call load() first.")
            self._active_inference += 1
        try:
            return self._transcribe_words_with_fallback_unlocked(audio, offset_seconds)
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()
            # RACE-023: perform deferred gc OUTSIDE the lock.
            self._run_deferred_gc()

    def _transcribe_words_with_fallback_unlocked(
        self,
        audio: np.ndarray,
        offset_seconds: float,
    ):
        return self._with_gpu_fallback(self._transcribe_words_unlocked, audio, offset_seconds)

    def _transcribe_words_unlocked(self, audio: np.ndarray, offset_seconds: float):
        """Thin delegate to ``transcription_result.transcribe_words_unlocked``."""
        return _transcribe_words_unlocked_impl(self, audio, offset_seconds)

    def _is_gpu_runtime_error(self, exc: Exception) -> bool:
        """detect GPU/CUDA runtime errors."""
        return _is_gpu_runtime_error_impl(self, exc)

    def _should_reject_low_audio_hallucination(
        self,
        *,
        result: str,
        rms: float,
        peak: float,
        silence_pct: float,
        duration: float,
        first_segment_start: float | None,
        last_segment_end: float | None,
    ) -> bool:
        return should_reject_low_audio_hallucination(
            result,
            rms,
            peak=peak,
            silence_pct=silence_pct,
            duration=duration,
            first_segment_start=first_segment_start,
            last_segment_end=last_segment_end,
        )

    def unload(self) -> None:
        """Free model memory.

        Waits for any in-flight ``transcribe()`` / ``transcribe_with_fallback()``
        call to finish (via the ``_active_inference`` counter +
        ``_inference_cond``) BEFORE nulling ``self._model`` so the
        inference thread doesn't dereference a freed ctranslate2 model
        (use-after-free). Mirrors ``ParakeetEngine.unload`` and
        ``QwenEngine.unload``.

        PERF- also release DLL directory handles opened by
        ``_configure_nvidia_dll_paths`` so the process doesn't hold
        phantom DLL refs after the model is unloaded.

        also release GPU memory via ``release_gpu_memory()`` so freed
        CUDA blocks return to the OS. Without this, switching backends
        (Whisper → Parakeet → Whisper) accumulates cached blocks and
        OOMs on RTX 3060/4060 (8–12 GB VRAM) after ~2 switches.

        RACE-023: gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.
        """
        import gc

        with self._inference_cond:
            while self._active_inference > 0:
                self._inference_cond.wait()
            self._model = None
        # RACE-023: gc.collect() OUTSIDE the lock
        gc.collect()
        # release CUDA cached blocks.
        release_gpu_memory()
        # PERF- release DLL directory handles outside the lock
        try:
            _free_nvidia_dll_path_handles()
        except Exception:
            log.debug("[MODEL] Error releasing DLL handles", exc_info=True)


# Back-compat re-export: the canonical body lives in
from voice_typer.server.transcription_result import (  # noqa: E402, F401
    build_quality_summary,
)
