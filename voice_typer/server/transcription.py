"""Whisper transcription engine using faster-whisper."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

# PERF-COLDSTART-001: numpy is ~250-335ms cumulative on cold start
# and is only touched on the first transcription call (seconds after
# dictation begins). Defer the real import to first attribute access via
# the same ``lazy_module`` proxy already used for ``sounddevice`` and
# ``pystray``. The proxy re-resolves ``sys.modules`` on every access, so
# production ``np.array(...)`` calls and test
# ``monkeypatch.setattr(np, "array", ...)`` both work unchanged.
# ``from __future__ import annotations`` above is REQUIRED so the
# ``np.ndarray`` annotations below stay as unevaluated strings (PEP 563);
# otherwise the module-level def of ``transcribe`` would resolve
# ``np.ndarray`` via the proxy and trigger the eager import we are
# trying to avoid. NOTE: the local ``import numpy as np`` inside
# ``_generate_probe_audio`` / ``_warmup_engine`` (lines ~478, ~570) is
# intentional — those are hot paths that want to avoid the per-call
# proxy ``_resolve()`` overhead. They shadow the lazy proxy for the
# duration of the function.
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE as _WHISPER_SAMPLE_RATE

# ``cleanup_hf_cache_dir`` (formerly ``_cleanup_failed_whisper_cache``)
# is imported from the dedicated ``_hf_cache_cleanup`` facade module —
# the canonical entry point for HF cache-dir cleanup (previously the
# body was duplicated 3x across ``transcription.py``, ``asr_setup.py``,
# ``parakeet_engine.py``).
# ``_hf_cache_cleanup`` in turn delegates to ``asr_utils.cleanup_hf_cache_dir``
# where the actual implementation lives.  Re-exported here (with a noqa F401
# suppression on the import below)
# for backward compatibility with tests that patch
# ``voice_typer.server.transcription.cleanup_hf_cache_dir``.
from voice_typer.server._hf_cache_cleanup import (  # noqa: F401  # noqa: F401
    cleanup_failed_cache as _cleanup_failed_cache,
    cleanup_hf_cache_dir,
)
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.asr_errors import (
    ConsentRequiredError,  # noqa: F401  # re-exported for backward compat
    ModelIntegrityError,
    ModelNotDownloadedError,
)
from voice_typer.server.asr_utils import (  # noqa: F401
    _check_disk_space_for_download,
    _download_with_retry,
    _require_huggingface_consent,
    is_oom_error,
    release_gpu_memory,
)
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination
from voice_typer.server.i18n import DEFAULT_LOCALE
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

np = lazy_module("numpy")

# shared ASR helpers now live in ``asr_utils`` (canonical source).
# The re-exports below preserve backward compatibility with tests and
# service.py that import these names from ``transcription``.

# ``TranscriberProtocol`` was moved to ``transcription_load.py`` (canonical
# source). Re-exported here so existing ``from voice_typer.server.transcription
# import TranscriberProtocol`` imports resolve to the SAME class object (identity
# parity — see ``tests/test_transcriber_protocol_parity.py``).
from voice_typer.server.transcription_load import TranscriberProtocol  # noqa: F401, E402

log = logging.getLogger(__name__)


# re-exported from ``voice_typer.server._audio_constants`` for
# back-compat with callers (and tests) that read this module attribute.
# ``_WHISPER_SAMPLE_RATE`` is the canonical Whisper 16 kHz input rate.
_nvidia_dll_path_handles: list[object] = []


# ``_MODEL_SIZE_MB`` + ``_DISK_SPACE_MARGIN_MB``,
# ``release_gpu_memory()``, ``_download_with_retry()``,
# ``_check_disk_space_for_download()`` were extracted to
# ``voice_typer.server.asr_utils`` as the canonical home for shared ASR
# helpers.  ``cleanup_hf_cache_dir()`` (formerly
# ``_cleanup_failed_whisper_cache``) is now imported from the dedicated
# ``voice_typer.server._hf_cache_cleanup`` facade module which itself
# delegates to ``asr_utils.cleanup_hf_cache_dir``
# where the implementation body lives.  See the re-export block at the
# top of this module for the backward-compat imports.


# NVIDIA CUDA DLL path setup (Windows-only, gated by ``is_windows()``
# inside the implementation) was extracted to
# ``voice_typer.server.nvidia_dll_paths`` so the DLL-search logic lives
# in a single focused module. The 3 public functions are re-exported
# below for backward compatibility with callers (and tests) that import
# them from ``transcription``. The module-level state they mutate —
# ``_nvidia_dll_path_handles``, ``_nvidia_dll_paths_configured``,
# ``_nvidia_config_lock`` — STAYS here so existing tests that
# rebind/read ``transcription._nvidia_dll_path_handles`` (and similar)
# continue to work; the extracted functions access this state via late
# binding (``from voice_typer.server import transcription as _t``
# inside each function body).
from voice_typer.server.nvidia_dll_paths import (  # noqa: E402, F401
    _configure_nvidia_dll_paths,
    _configure_nvidia_dll_paths_locked,
    _cuda_runtime_available,
    _free_nvidia_dll_path_handles,
    _NvidiaDllPathManager,
)

_nvidia_dll_paths_configured = False
# RACE-029: module-level lock to serialize _configure_nvidia_dll_paths()
# calls.  Previously concurrent calls from the load path could both
# read _nvidia_dll_paths_configured==False and then both mutate
# _nvidia_dll_path_handles and os.environ["PATH"], causing duplicate
# DLL directory additions and race-conditional PATH corruption.
_nvidia_config_lock = threading.Lock()

# Singleton manager that encapsulates operations on the three
# module-level globals above. Constructed with no ``state_dict`` so it
# late-binds to this module's globals — existing tests that rebind
# ``transcription._nvidia_dll_path_handles`` (and similar) continue to
# see their replacement values reflected through
# ``_nvidia_dll_paths.handles`` / ``.configured`` / ``.lock``.
_nvidia_dll_paths = _NvidiaDllPathManager()


class TranscriptionEngine:
    """Wraps faster-whisper model loading and transcription."""

    def __init__(
        self,
        # Canonical default — see ``model_registry.DEFAULT_MODEL_SIZE``.
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = "auto",
        language: str = DEFAULT_LOCALE,
        # Speed-biased default of 1 — ~2x faster than beam_size=3-5 at
        # the cost of ~1-3% worse WER on common benchmarks (LibriSpeech,
        # Common Voice). Override via ``config.whisper_beam_size``
        # (preferred, Whisper-specific field) or this ``beam_size`` kwarg.
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
        # takes precedence over the legacy ``beam_size`` kwarg. The
        # "!= 1" check preserves backward compat for users who set the
        # legacy ``beam_size`` field but not ``whisper_beam_size`` (which
        # defaults to 1 in Config): the engine keeps using the legacy
        # value instead of clobbering it with the new field's default.
        # The effective beam_size flows to ``model.transcribe(...)`` via
        # ``self.beam_size`` in ``_transcribe_unlocked`` /
        # ``_transcribe_words_unlocked`` / ``_probe_cuda_runtime``.
        effective_beam_size = beam_size
        if config is not None:
            cfg_whisper_beam_size = getattr(config, "whisper_beam_size", None)
            if cfg_whisper_beam_size is not None and cfg_whisper_beam_size != 1:
                effective_beam_size = cfg_whisper_beam_size
        self.beam_size = effective_beam_size
        self.best_of = best_of
        self.condition_on_previous_text = condition_on_previous_text
        self._model = None
        self._lock = threading.RLock()
        # counter + Condition so transcribe() can release the model
        # lock during the (potentially long) segment-decoding loop while
        # still coordinating with unload(). unload() waits for
        # ``_active_inference == 0`` under ``_inference_cond`` before
        # nulling ``self._model`` so a concurrent transcribe() doesn't
        # dereference a freed ctranslate2 model. Mirrors the pattern in
        # ``parakeet_engine.py:236-243``.
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        self._requested_device: str | None = device  # defer CUDA detection to load()
        self._device = "cpu"
        self._compute_type = "int8"
        # Abort token shared by the dictation pipeline's cancel path
        # and the segment-iteration loop in ``_transcribe_unlocked``.
        # ``request_abort()`` sets the event from any thread (typically
        # the watchdog / ESC cancel path via the pipeline's abort
        # watcher); the segment loop checks it between iterations and
        # breaks out early, returning the partial text collected so far.
        # ``clear_abort()`` is called by the pipeline at the start of
        # each transcription cycle so a stale abort from the previous
        # cycle does NOT suppress the next one. The event is also
        # signaled to ctranslate2 via ``interrupt()`` when available
        # (ctranslate2 >= 4.x) so a mid-segment ``model.transcribe()``
        # call returns promptly instead of running to completion.
        self._abort_event = threading.Event()
        # Deferred-gc flag set by ``_with_gpu_fallback`` when the GPU
        # model is torn down; cleared by ``_run_deferred_gc`` /
        # ``_with_lock_and_deferred_gc`` after the lock is released
        # (RACE-023). Initialized here so attribute access never falls
        # back to ``getattr(..., False)`` — explicit is better than
        # implicit, and the test fixtures that bypass ``__init__`` set
        # this explicitly too.
        self._pending_gc_collect = False
        # Compact quality summary of the LAST completed transcription,
        # populated by ``_transcribe_unlocked`` from the per-segment
        # ``avg_logprob`` / ``no_speech_prob`` stats it already collects
        # (no recomputation). Read by the dictation pipeline right after
        # the transcribe call and attached to the renderer-facing
        # ``transcription_final`` push event so the UI can flag
        # low-confidence results. ``None`` when the engine has not
        # transcribed anything yet, the audio was empty, or the model
        # yielded no numeric segment stats (e.g. non-Whisper engines).
        self.last_quality_summary: dict[str, float] | None = None
        # store a reference to the app's Config dataclass so the
        # engine can read per-segment flags (e.g. ``log_transcriptions``)
        # without reaching into the app object.

        # ``config`` may be None when constructed by callers that
        # don't have access to the app's Config (e.g. service.py
        # benchmark path, or test stubs).  Feature flags that read
        # ``self.config`` must guard for None.  Note: the engine NEVER
        # downloads models (downloads are explicit user actions via the
        # Models page / onboarding), so no consent gate exists here —
        # the HuggingFace consent gate lives in the explicit download
        # path (``service/model.py``).
        self.config = config

    def _resolve_device(self, device: str) -> tuple[str, str]:
        """Auto-detect best device and compute type.

        previously the CUDA-detection try/except used bare
        ``Exception``, hiding real setup errors (driver mismatch,
        missing DLLs). Narrowed to ``(OSError, RuntimeError,
        ImportError)`` so genuine bugs propagate.
        """
        if device == "cpu":
            return "cpu", "int8"

        # Try CUDA
        if device in ("auto", "cuda"):
            try:
                _configure_nvidia_dll_paths()
                # Windows fast path: when the CUDA runtime DLLs cannot
                # be loaded (CPU-only torch, missing nvidia-* wheels),
                # skip the expensive ``import ctranslate2`` + CUDA device
                # probe — the import alone costs ~20s of CUDA
                # enumeration and the probe would fail at load time
                # anyway, forcing a CPU reload.
                if _cuda_runtime_available() is False:
                    log.warning(
                        "[MODEL] CUDA runtime DLLs unavailable on Windows — "
                        "using CPU directly (skipped ~20s CUDA probe)"
                    )
                    if device == "cuda":
                        log.warning("[MODEL] CUDA requested but DLLs unavailable, falling back to CPU")
                    log.info("[MODEL] Using CPU for transcription")
                    return "cpu", "int8"
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    log.info("[MODEL] Using CUDA device for transcription")
                    return "cuda", "float16"
            except (OSError, RuntimeError, ImportError):
                # OSError: missing DLL / driver file
                # RuntimeError: ctranslate2 internal init failure
                # ImportError: ctranslate2 not installed
                log.warning(
                    "[MODEL] CUDA detection failed, falling back to CPU",
                    exc_info=True,
                )

            if device == "cuda":
                log.warning("[MODEL] CUDA requested but not available, falling back to CPU")

        log.info("[MODEL] Using CPU for transcription")
        return "cpu", "int8"

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully."""
        return self._model is not None

    def request_abort(self) -> None:
        """Signal an in-flight transcription to abort as soon as possible.

        Sets the ``_abort_event`` checked between segment iterations in
        ``_transcribe_unlocked``. Also best-effort calls
        ``ctranslate2.Translator.interrupt()`` (ctranslate2 >= 4.x)
        via the wrapped ``WhisperModel.model`` attribute so a
        mid-segment ``model.transcribe()`` call returns promptly
        instead of running to completion. If the interrupt API is
        unavailable (older ctranslate2 / mock model), only the
        between-segments check fires — the current segment finishes
        but no further segments are produced. Either way, the
        transcription thread is unblocked in bounded time, freeing
        compute for the next dictation cycle.
        """
        self._abort_event.set()
        # Best-effort ctranslate2 interrupt. ``WhisperModel.model`` is
        # the underlying ctranslate2 ``Whisper`` translator; ctranslate2
        # >= 4.x exposes ``interrupt()`` on it. Mocked models in tests
        # may not have the attribute — guard with ``hasattr`` so the
        # abort path never raises.
        try:
            inner = getattr(self._model, "model", None)
            if inner is not None and hasattr(inner, "interrupt"):
                inner.interrupt()
        except Exception:
            log.debug("[TRANSCRIBE] ctranslate2 interrupt() failed (non-fatal)", exc_info=True)

    def clear_abort(self) -> None:
        """Clear the abort token at the start of a fresh transcription cycle.

        Called by the dictation pipeline before each transcribe so a
        stale abort from the previous cycle (e.g. the user hit ESC,
        aborted, then started a new recording) does NOT suppress the
        new transcription.
        """
        self._abort_event.clear()

    @property
    def device_info(self) -> str:
        return f"{self._device} ({self._compute_type})"

    @property
    def loaded_via(self) -> str:
        """Return a description of the device/model combo that was successfully loaded."""
        return f"{self._device}/{self._compute_type}/{self.model_size}"

    def load(self, progress_callback=None) -> None:
        """Load the Whisper model from the local cache (NEVER downloads).

        The app never downloads models automatically — the user must
        explicitly download a model (Models page Download button, or the
        onboarding wizard) before it can be loaded. If the selected model
        is not present in the local HuggingFace cache,
        :class:`~voice_typer.server.asr_errors.ModelNotDownloadedError` is
        raised so callers can direct the user to the Models page. If the
        cached files fail integrity verification,
        :class:`~voice_typer.server.asr_errors.ModelIntegrityError` is
        raised and the tampered cache is NOT deleted automatically
        (deleting a model is an explicit user action).

        Fallback chain:
          1. Configured device (e.g. CUDA/float16)
          2. CPU / int8 with original model size
          3. CPU / int8 with tiny
          4. CPU / float32 with tiny (last resort — avoids MKL int8 path)
        Fallback entries whose model is not cached locally are skipped
        (never auto-downloaded).

        Stores which path succeeded via loaded_via property.

        progress_callback: optional callable(message: str) for load status.
        """
        # Deferred CUDA detection — run now (once) near load time
        self._resolve_device_once()
        self._require_model_downloaded(self.model_size, progress_callback)
        self._load_model_outside_lock(progress_callback=progress_callback)

    def _resolve_device_once(self):
        """Resolve the CUDA device if not already resolved.

        Separated from __init__ so the expensive ``import ctranslate2`` and
        CUDA DLL loading only happens when the model is actually about to
        load, not during construction.  This saves ~20s on startup when the
        user hasn't pressed F2 yet.
        """
        if self._requested_device is None:
            return
        device = self._requested_device
        self._requested_device = None
        self._device, self._compute_type = self._resolve_device(device)

    def _load_model_outside_lock(self, progress_callback=None):
        """Load model outside the lock so downloads don't block other threads.

        extracted the common load logic into
        ``_load_transcriber_impl(acquire_lock)`` so this method and
        ``_reload_under_lock`` share one code path.
        """
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
        """Shared model-load body used by both _load_model_outside_lock
        and _reload_under_lock.

        previously two near-identical 30-line bodies that
        differed only in lock acquisition. Now a single method with
        an ``acquire_lock`` flag.

        Raises:
            RuntimeError: if every entry in the fallback chain failed.
        """
        # Map progressive verb → base form for error messages.
        verb_base = "load" if verb.lower() == "loading" else "reload"
        _configure_nvidia_dll_paths()
        from faster_whisper import WhisperModel

        last_error = None
        for device, compute_type, model_size in chain:
            try:
                # NEVER auto-download: skip fallback-chain entries whose
                # model is not present in the local HF cache. Only the
                # explicit Models-page / onboarding download populates the
                # cache. (The primary model was already verified by
                # ``_require_model_downloaded`` in ``load()``.)
                if not self._whisper_size_cached(model_size):
                    log.warning(
                        "[MODEL] %s: model '%s' not in local cache — skipping "
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
                # prewarm cache-hit effectiveness.
                _t0 = time.perf_counter()
                model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
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
                log.info(
                    "[MODEL] Model %s via %s (%s) — %.1fs",
                    verb.lower(),
                    self.loaded_via,
                    _warm_label,
                    _load_elapsed,
                )

                # CUDA probe: force a tiny transcription to smoke-test
                # cuBLAS loading at startup, so failures surface here
                # (with a clean fallback to CPU) rather than mid-recording.
                if self._device == "cuda" and acquire_lock:
                    self._probe_cuda_runtime(progress_callback)

                # PERF-007: warm-up inference with 0.5s of silence.
                # Primes CUDA kernels so the first real transcription
                # doesn't pay the kernel compilation cost (~2-5s).
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
            # not in the local cache — surface the actionable error.
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
        """Reload the model while already holding the lock (for GPU fallback).

        now delegates to ``_load_transcriber_impl`` with
        ``acquire_lock=False`` instead of duplicating the load body.
        """
        chain = self._build_fallback_chain()
        self._load_transcriber_impl(chain, acquire_lock=False, verb="Reloading")

    def _probe_cuda_runtime(self, progress_callback=None):
        """Probe CUDA with a real transcription to force early cuBLAS/cuDNN loading.

        Uses a 1s sine-wave tone and the exact same parameters as
        ``_transcribe_unlocked`` — including ``vad_filter=True`` and
        ``without_timestamps=True`` — then **iterates every segment** so
        the underlying cuBLAS kernels are actually resolved.  If the DLLs
        can't be loaded, catches the error at startup and falls back to
        CPU immediately instead of failing mid-recording.
        """
        #  (pyrefly): ``self._model`` is declared ``self._model = None``
        # in __init__ and only assigned a real model instance inside the
        # load path. The sole caller (line ~554) only invokes us after a
        # successful load, but pyrefly cannot prove that contract across
        # method boundaries — so guard explicitly. Returning early here
        # also makes the function safe to call from tests / future
        # callers that haven't loaded a model yet.
        if self._model is None:
            log.warning("[CUDA-PROBE] Skipping — no model loaded")
            return
        import numpy as np

        t = np.arange(int(_WHISPER_SAMPLE_RATE), dtype=np.float32) / _WHISPER_SAMPLE_RATE
        probe_audio: np.ndarray = np.sin(2 * np.pi * 440 * t, dtype=np.float32) * 0.1

        log.info("[CUDA-PROBE] Running CUDA runtime smoke test (1s sine wave)...")
        if progress_callback:
            progress_callback("Running CUDA runtime probe...")
        try:
            # Must exercise the same cuBLAS kernels as real dictation.
            # NOTE: vad_filter=False is deliberate — VAD would reject a
            # sine wave as non-speech, causing Whisper to be skipped.
            segments, info = self._model.transcribe(
                probe_audio,
                beam_size=self.beam_size,
                best_of=self.best_of,
                temperature=0.0,
                vad_filter=False,
                language=self.language,
                condition_on_previous_text=self.condition_on_previous_text,
                without_timestamps=True,
            )
            # Force iteration through ALL segments — model.transcribe()
            # returns lazily; the real GPU work (and DLL loading) happens
            # here.
            for _seg in segments:
                pass
            log.info("[CUDA-PROBE] CUDA runtime OK — cuBLAS/cuDNN loaded successfully")
        except Exception as exc:
            error_str = str(exc)
            log.warning(
                "[CUDA-PROBE] CUDA runtime probe FAILED: %s",
                error_str,
            )
            if any(
                kw in error_str.lower()
                for kw in [
                    "cublas",
                    "cuda",
                    "cudnn",
                    "dll",
                    "not found",
                    "cannot be loaded",
                    "load library",
                ]
            ):
                log.warning(
                    "[CUDA-PROBE] cuBLAS/cuDNN runtime error detected — falling back to CPU immediately",
                )
                # wrap the null-and-reload sequence in
                # ``with self._lock:`` so a concurrent ``transcribe()`` from
                # another thread can't observe ``self._model = None`` (or
                # worse, a half-loaded model mid-reload). Pre-fix, the probe
                # ran unlocked after the outer ``with self._lock:`` block at
                # line 551 had exited, leaving a race window where
                # ``transcribe()`` would raise ``RuntimeError("Model not
                # loaded")`` or see a partially-initialized model.
                with self._lock:
                    try:
                        del self._model
                        import gc

                        gc.collect()
                        # HU-25: ``del self._model`` + ``gc.collect()``
                        # trigger PyTorch's __del__ hook which releases the
                        # parameter tensors' CUDA blocks, but the caching
                        # allocator keeps them until ``release_gpu_memory()``
                        # (torch.cuda.empty_cache()) runs. That call is
                        # deferred OUTSIDE this lock via the RACE-023
                        # ``_pending_gc_collect`` flag — it is set
                        # EXPLICITLY in this branch, because
                        # ``_reload_under_lock()`` does NOT set it (only
                        # ``_with_gpu_fallback`` does, and this CUDA-probe
                        # path is separate). Calling release_gpu_memory()
                        # inside this lock was a no-op for VRAM release +
                        # cost ~10-100ms of sync work (empty_cache blocks
                        # the calling thread while it iterates the
                        # allocator) holding the IPC dispatch lock for no
                        # benefit.
                    except Exception:
                        log.debug("[MODEL] GPU model teardown failed", exc_info=True)
                    self._model = None
                    self._device = "cpu"
                    self._compute_type = "int8"
                    # HU-25: arm the deferred GPU release BEFORE the
                    # reload so a reload failure (model missing /
                    # ctranslate2 error) can't leak the already-freed CUDA
                    # blocks — the next caller outside the lock
                    # (transcribe / unload) runs gc.collect() +
                    # release_gpu_memory() regardless (OOMs on RTX
                    # 3060/4060 after repeated CUDA-probe-failure reloads).
                    self._pending_gc_collect = True
                    self._reload_under_lock()
                    log.warning(
                        "[CUDA-PROBE] Model reloaded on CPU after CUDA probe failure. Loaded via: %s",
                        self.loaded_via,
                    )
            else:
                raise

    def _warm_up_model(self) -> None:
        """PERF-007: Run a warm-up inference with silence to prime CUDA kernels.

        The first CUDA inference typically takes 2-5 seconds longer than
        subsequent ones because the GPU kernels need to be compiled (JIT)
        and memory allocated. This method runs a 0.5-second silence
        transcription after model load so the first real dictation is fast.

        If the model is on CPU or warm-up fails, this is a no-op.
        """
        if self._model is None or self._device != "cuda":
            return
        try:
            import numpy as np

            warmup_audio = np.zeros(int(_WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
            segments, _ = self._model.transcribe(
                warmup_audio,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                language=self.language,
                without_timestamps=True,
            )
            # Force iteration to complete the warm-up
            for _ in segments:
                pass
            log.info("[PERF] Warm-up inference completed — CUDA kernels primed")
        except Exception as exc:
            # Warm-up failure is non-critical — log and continue
            log.debug("[PERF] Warm-up inference skipped: %s", exc)

    def _probe_cache(
        self,
        snapshot_download_fn,
        repo_id: str,
        revision: str,
        allow_patterns,
        model_size: str,
        progress_callback=None,
    ) -> tuple[str | None, bool]:
        """Phase 1: probe the HuggingFace cache (local-only).

        Returns ``(local_dir, integrity_failed)``:

        * ``(path, False)`` — cache hit AND integrity verified. The
          caller can proceed to load.
        * ``(None, True)`` — cache hit BUT integrity check failed. The
          caller must refuse to load (raise ``ModelIntegrityError``)
          without deleting the tampered files — deletion is an explicit
          user action (Models page Delete button).
        * ``(None, False)`` — cache miss (or local probe raised). The
          caller raises ``ModelNotDownloadedError`` (never downloads).

        ``snapshot_download_fn`` is the ``huggingface_hub.snapshot_download``
        callable (injected so tests can pass a MagicMock). The call uses
        ``local_files_only=True`` so no network traffic is generated on
        the cache-probe path.
        """
        try:
            local_dir = snapshot_download_fn(
                repo_id=repo_id,
                revision=revision,
                allow_patterns=allow_patterns,
                local_files_only=True,
            )
        except Exception:
            log.debug("[MODEL] HF cache probe failed — will attempt download", exc_info=True)
            return None, False

        from voice_typer.server.security import verify_model_integrity

        if not verify_model_integrity(local_dir, repo_id):
            log.error(
                "[MODEL] Cached model '%s' failed integrity check (cache hit path) — "
                "refusing to load tampered files (no automatic deletion).",
                model_size,
            )
            if progress_callback:
                progress_callback("Cached model failed integrity check; delete and re-download from the Models page.")
            return None, True

        return local_dir, False

    def _require_model_downloaded(self, model_size: str, progress_callback=None) -> None:
        """Ensure the Whisper model is present in the local HF cache.

        The app never downloads models automatically: the user must
        explicitly click Download on the Models page (or the onboarding
        wizard) first. This gate refuses to load an uncached model and
        raises :class:`~voice_typer.server.asr_errors.ModelNotDownloadedError`
        so callers can point the user at the Models page. A cached-but-
        tampered model raises
        :class:`~voice_typer.server.asr_errors.ModelIntegrityError` and is
        NOT deleted automatically — deletion is an explicit user action
        (Models page Delete button).

        The probe is local-only (``local_files_only=True``) so no network
        traffic is generated and no consent is required — consent is only
        relevant for the explicit download path (``service.download_model``).
        """
        # Skip the gate for non-Whisper model sizes (e.g. "parakeet" or
        # "qwen") — those backends have their own load path.
        if not model_size or model_size in ("parakeet", "qwen"):
            log.debug(
                "[MODEL] Skipping download-required check for non-Whisper model '%s'",
                model_size,
            )
            return
        try:
            from huggingface_hub import snapshot_download

            repo_id = f"Systran/faster-whisper-{model_size}"

            # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest.
            from voice_typer.server.security import MODEL_HASHES

            whisper_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

            # Shared allow-pattern list (see ``_model_integrity``).
            from voice_typer.server._model_integrity import ALLOW_PATTERNS_WHISPER

            if progress_callback:
                progress_callback(f"Checking model cache for '{model_size}'...")
            local_dir, integrity_failed = self._probe_cache(
                snapshot_download,
                repo_id,
                whisper_revision,
                ALLOW_PATTERNS_WHISPER,
                model_size,
                progress_callback=progress_callback,
            )
            if local_dir is not None and not integrity_failed:
                log.info("[MODEL] Model '%s' already cached (integrity verified)", model_size)
                return
            if integrity_failed:
                raise ModelIntegrityError(
                    f"The cached model '{model_size}' failed integrity verification. "
                    "Delete it and download it again from the Models page to recover.",
                    model_size=model_size,
                    backend="whisper",
                    repo_id=repo_id,
                )
            raise ModelNotDownloadedError(
                f"The Whisper model '{model_size}' is not downloaded yet. "
                "Open the Models page and click Download before using it.",
                model_size=model_size,
                backend="whisper",
                repo_id=repo_id,
            )
        except ImportError:
            # huggingface_hub unavailable — we cannot verify the cache, so
            # refuse to load (never auto-download) and point at Models page.
            raise ModelNotDownloadedError(
                f"The Whisper model '{model_size}' is not downloaded yet. "
                "Open the Models page and click Download before using it.",
                model_size=model_size,
                backend="whisper",
            ) from None

    def _whisper_size_cached(self, model_size: str) -> bool:
        """Local-only probe: is ``model_size`` fully present in the HF cache?

        Used by the fallback chain in ``_load_transcriber_impl`` to skip
        entries whose model has not been downloaded (the app never
        auto-downloads). Returns ``True`` when the probe is inconclusive
        (``huggingface_hub`` unavailable) so the load attempt is allowed
        to proceed — ``WhisperModel`` will surface its own error if the
        files are genuinely missing.
        """
        try:
            from huggingface_hub import snapshot_download

            repo_id = f"Systran/faster-whisper-{model_size}"

            from voice_typer.server.security import MODEL_HASHES

            revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

            from voice_typer.server._model_integrity import ALLOW_PATTERNS_WHISPER

            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                allow_patterns=ALLOW_PATTERNS_WHISPER,
                local_files_only=True,
            )
            return True
        except ImportError:
            # Cannot probe — allow the load attempt (WhisperModel will
            # surface its own error if the files are missing).
            return True
        except Exception:
            # Cache miss (or local probe failure) — never auto-download.
            return False

    def transcribe(self, audio: np.ndarray, audio_stats: tuple[float, float, float] | None = None) -> str:
        """Transcribe audio array. Returns cleaned text string.

        ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own stats computation
        (saves 1-3 ms + 3× 1.9 MB transient memory per dictation).

        The lock is released during the segment-decoding loop.
        Previously the entire ``_transcribe_unlocked`` call (including
        the ctranslate2 generator that drives 0.5-3s per segment) ran
        under ``self._lock``, blocking ``is_loaded`` / ``unload`` /
        parallel transcribes for 10-30s per long dictation. We now
        acquire the lock only briefly to check loaded state and
        increment ``_active_inference``; ``unload()`` waits on
        ``_inference_cond`` for the counter to return to 0 before
        nulling the model, so the inference path can safely access
        ``self._model`` without holding the lock. Mirrors the pattern
        in ``parakeet_engine.py:752-779``.
        """
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
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if len(audio) == 0:
            return ""

        # Log audio statistics for diagnostics
        duration = len(audio) / _WHISPER_SAMPLE_RATE
        # reuse pre-computed stats when provided (avoids
        # 1-3 ms + 3× 1.9 MB transient memory per dictation).
        if audio_stats is not None:
            rms, peak, silence_pct = audio_stats
        else:
            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            peak = float(np.max(np.abs(audio)))
            silence_pct = float(np.sum(np.abs(audio) < 0.001) / audio.size * 100)
        log.info(
            "[TRANSCRIBE] Input audio: samples=%d, duration=%.1fs, RMS=%.6f, peak=%.6f, silence_pct=%.1f%%",
            len(audio),
            duration,
            rms,
            peak,
            silence_pct,
        )
        if rms < 0.001:
            log.warning(
                "[TRANSCRIBE] Near-silence input (RMS=%.6f). Speech detection is unlikely.",
                rms,
            )

        segments, info = self._model.transcribe(
            audio,
            beam_size=self.beam_size,
            best_of=self.best_of,
            temperature=0.0,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            language=self.language,
            condition_on_previous_text=self.condition_on_previous_text,
            without_timestamps=True,
        )

        # Collect segments and log VAD info
        text_parts = []
        segment_count = 0
        first_segment_start = None
        last_segment_end = None
        avg_logprobs = []
        no_speech_probs = []
        # Reset the renderer-facing quality summary at the START of each
        # transcription so a stale summary from a previous dictation can
        # never be attributed to this one (e.g. when the segment loop is
        # cut short by an abort and collects no numeric stats).
        self.last_quality_summary = None
        # hoist the per-segment ``log_transcriptions`` flag and
        # ``redact_pii`` import OUT of the segment loop. Pre-fix, the
        # ``getattr(self.config, 'log_transcriptions', False)`` ran once
        # per segment and the ``from voice_typer.server.security import
        # redact_pii`` ran an ``importlib`` cache lookup per segment
        # (whenever the flag was True). For a 100+ segment long-form
        # dictation with ``log_transcriptions=True``, the redundant
        # attribute access + import lookups added ~1ms of pure overhead
        # before any actual regex work. Hoisting computes the flag once
        # and reuses the imported function for every segment.
        _log_transcriptions_flag = self.config is not None and getattr(self.config, "log_transcriptions", False)
        _redact_pii = None
        if _log_transcriptions_flag:
            try:
                from voice_typer.server.security import redact_pii as _redact_pii
            except Exception:
                _redact_pii = None
        for seg in segments:
            # Check the abort token BETWEEN segment iterations. The
            # ``segments`` generator yields one segment at a time, with
            # each ``next()`` call driving a ctranslate2 decoding step
            # (typically 0.5-3s per segment). Checking here lets the
            # ESC / watchdog cancel path break out of the loop within
            # one segment of being signalled — bounded latency instead
            # of waiting for the full audio to decode. ``request_abort()``
            # also best-effort calls ``ctranslate2.Translator.interrupt()``
            # so the CURRENT segment's C-level call returns promptly.
            if self._abort_event.is_set():
                log.info(
                    "[TRANSCRIBE] Abort requested — stopping segment loop early (completed %d segments, %d text parts)",
                    segment_count,
                    len(text_parts),
                )
                break
            segment_count += 1
            start = seg.start or 0.0
            end = seg.end or start
            if first_segment_start is None:
                first_segment_start = start
            last_segment_end = end
            avg_logprob = getattr(seg, "avg_logprob", None)
            no_speech_prob = getattr(seg, "no_speech_prob", None)
            if isinstance(avg_logprob, int | float):
                avg_logprobs.append(float(avg_logprob))
            if isinstance(no_speech_prob, int | float):
                no_speech_probs.append(float(no_speech_prob))
            if seg.text.strip():
                text_parts.append(seg.text.strip())
                # SEC-009: gate the per-segment DEBUG log by
                # ``log_transcriptions`` and apply ``redact_pii`` when
                # enabled. Pre-fix, raw segment text was logged whenever
                # DEBUG logging was active (e.g. in diagnostics zips,
                # dev runs, or when a user files a bug report with
                # verbose logs) — leaking any PII the user dictated
                # (medical/financial/address/name content) even though
                # the operator had not opted into transcription logging.
                # When ``log_transcriptions`` is False (the default), we
                # log only the segment char count + timestamps — no text
                # content. When True, we apply ``redact_pii`` (the same
                # canonical helper used by ``hallucination.py`` /
                # ``llm_polish.py`` / ``crash_handler``) so the four
                # documented PII patterns (email/phone/SSN/CC) are
                # masked before the segment text hits the log file.
                _seg_text = seg.text.strip()
                if _log_transcriptions_flag and _redact_pii is not None:
                    try:
                        _safe_seg_text = _redact_pii(_seg_text)
                    except Exception:
                        # fall back to a redacted marker only — do NOT
                        # log the raw text even truncated, because the
                        # opt-in ``log_transcriptions`` flag is a privacy
                        # backstop that the user explicitly enabled, and
                        # ``_redact_pii is None`` (e.g. an import failure
                        # of the redaction engine) means PII cannot be
                        # guaranteed masked. Truncating to 80 chars does
                        # NOT redact — an 80-char window can still
                        # contain an email address, phone number, or
                        # SSN fragment. AP-11 fix: log a redacted marker
                        # + the segment boundaries only.
                        log.warning(
                            "[TRANSCRIBE] Segment: [%.1fs - %.1fs] "
                            "<redaction-engine-failed — segment text NOT "
                            "logged to preserve PII guarantee; enable "
                            "voice_typer.server._secrets.redact_pii and "
                            "retry>",
                            start,
                            end,
                        )
                        _safe_seg_text = None  # skip the log.debug below
                    if _safe_seg_text is not None:
                        log.debug(
                            "[TRANSCRIBE] Segment: [%.1fs - %.1fs] %s",
                            start,
                            end,
                            _safe_seg_text,
                        )
                # When ``log_transcriptions`` is False (the default) or
                # ``config`` is None, emit NO segment DEBUG log at all —
                # not even a char-count-only summary. The segment
                # metadata (char count, timestamps) still indirectly
                # reveals dictation content patterns (e.g. segment
                # timing → speech cadence, char count → utterance
                # length). Privacy contract: zero segment data in logs
                # unless the operator has explicitly opted in.

        log.info(
            "[TRANSCRIBE] VAD result: language=%s (prob=%.2f), "
            "segments=%d, text_segments=%d, avg_logprob=%s, no_speech_prob=%s",
            info.language,
            info.language_probability,
            segment_count,
            len(text_parts),
            _format_optional_mean(avg_logprobs),
            _format_optional_mean(no_speech_probs),
        )

        # Compact quality summary for the dictation pipeline → renderer
        # (``transcription_final`` payload). Built from the stats already
        # collected above — a handful of float ops per dictation, never
        # on the paste path. ``None`` when the loop collected no numeric
        # segment stats so downstream consumers omit the field.
        self.last_quality_summary = build_quality_summary(avg_logprobs, no_speech_probs)

        result = " ".join(text_parts).strip()
        if self._should_reject_low_audio_hallucination(
            result=result,
            rms=rms,
            peak=peak,
            silence_pct=silence_pct,
            duration=duration,
            first_segment_start=first_segment_start,
            last_segment_end=last_segment_end,
        ):
            # SEC-009: Use the PII-safe logging helper instead of raw text
            log_transcriptions = self.config is not None and getattr(self.config, "log_transcriptions", False)
            log_hallucination_rejection(
                "[TRANSCRIBE]",
                result,
                reason="low-audio hallucination",
                log_transcriptions=log_transcriptions,
            )
            log.info(
                "[TRANSCRIBE] Hallucination stats: duration=%.1fs, RMS=%.6f, peak=%.6f, silence=%.1f%%",
                duration,
                rms,
                peak,
                silence_pct,
            )
            return ""
        if result:
            log.info("[TRANSCRIBE] Result: %d chars", len(result))
        else:
            log.info(
                "[TRANSCRIBE] No speech detected (RMS=%.6f, silence=%.1f%%)",
                rms,
                silence_pct,
            )
        return result

    def _run_deferred_gc(self) -> None:
        """Run the deferred gc.collect() + release_gpu_memory() cleanup.

        RACE-023: gc.collect() and ``release_gpu_memory()`` are deferred
        to OUTSIDE the lock (the fallback path sets
        ``_pending_gc_collect = True`` while still holding the lock;
        the caller drops the lock and then calls us). This avoids
        blocking ``is_loaded`` / ``transcribe`` for the 10-100ms that
        gc.collect() + ``torch.cuda.empty_cache()`` can take.

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
        """Acquire ``self._lock`` for the body, then run deferred gc after.

        Extracts the shared ``with self._lock: ...; if _pending_gc_collect: ...``
        block that previously appeared in both ``transcribe_with_fallback``
        and ``transcribe_words``. The lock is held for the duration of the
        body; on exit (success OR exception), the lock is released and the
        deferred gc cleanup fires if the body (or a fallback it triggered)
        set ``_pending_gc_collect = True``.

        Used by ``transcribe_words`` (which holds the lock for the whole
        transcription). ``transcribe_with_fallback`` uses the
        inference-counter pattern instead (it releases the lock during the
        segment-decoding loop so ``unload()`` can drain via
        ``_inference_cond``), so it calls ``_run_deferred_gc()`` directly
        after its try/finally — same deferred-gc semantics, different lock
        structure.
        """
        with self._lock:
            yield
        self._run_deferred_gc()

    def _with_gpu_fallback(self, inner, audio, *args, **kwargs):
        """Run ``inner(audio, *args, **kwargs)`` with GPU→CPU fallback.

        Extracts the duplicate teardown sequence shared by
        ``_transcribe_with_fallback_unlocked`` (batch path) and
        ``_transcribe_words_with_fallback_unlocked`` (streaming path).

        On a GPU runtime error (per ``_is_gpu_runtime_error``):
          1. Drop the GPU model reference (``del self._model`` + set None).
          2. Switch device/compute to CPU/int8.
          3. Reload the model on CPU via ``_reload_under_lock()``.
          4. Set ``_pending_gc_collect = True`` so the caller runs
             ``gc.collect()`` + ``release_gpu_memory()`` AFTER releasing
             the lock (RACE-023).
          5. Retry ``inner(audio, *args, **kwargs)`` once on CPU.

        The previous words-path teardown called ``release_gpu_memory()``
        BEFORE ``del self._model`` — a no-op for VRAM release since the
        ctranslate2 model still held the CUDA context. The unified helper
        removes that misplaced call; VRAM release now happens in the
        deferred-gc phase AFTER the model is actually dropped.

        Non-GPU errors are re-raised unchanged. On a CPU device the
        GPU-error classifier short-circuits at the top of
        ``_is_gpu_runtime_error`` (returns False), so the fallback never
        fires — the original exception propagates.
        """
        try:
            return inner(audio, *args, **kwargs)
        except Exception as first_err:
            if not self._is_gpu_runtime_error(first_err):
                raise

            log.warning(
                "GPU transcription failed (%s), falling back to CPU",
                first_err,
            )
            # Tear down GPU model, reload on CPU.
            # RACE-023: gc.collect() and release_gpu_memory() are
            # deferred outside the lock via ``_pending_gc_collect``
            # (the caller's ``_with_lock_and_deferred_gc`` or
            # ``_run_deferred_gc`` call fires them after the lock is
            # released). Calling ``release_gpu_memory()`` here would be
            # a no-op — the ctranslate2 model still holds the CUDA
            # context until ``del self._model`` runs below.
            with contextlib.suppress(Exception):
                del self._model
            self._model = None
            self._device = "cpu"
            self._compute_type = "int8"
            self._reload_under_lock()
            self._pending_gc_collect = True
            return inner(audio, *args, **kwargs)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe with automatic CPU fallback on GPU runtime errors.

        If the first attempt fails with a CUDA/cuBLAS/runtime error and
        the model was loaded on GPU, reload on CPU and retry once.

        ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own stats computation.

        The lock is released during the segment-decoding loop (mirrors
        ``transcribe()``). ``unload()`` waits on ``_inference_cond``
        for ``_active_inference == 0`` before nulling the model, so a
        stuck backend can be torn down without waiting for the full
        segment loop to complete.
        """
        with self._lock:
            if self._model is None:
                raise RuntimeError("Model not loaded. Call load() first.")
            if len(audio) == 0:
                return ""
            self._active_inference += 1
        try:
            result = self._transcribe_with_fallback_unlocked(audio, audio_stats=audio_stats)
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()
        # RACE-023: perform deferred gc.collect() OUTSIDE the lock.
        # ``transcribe_with_fallback`` uses the inference-counter pattern
        # (lock released during transcription) so it can't use
        # ``_with_lock_and_deferred_gc`` directly — call the shared
        # helper instead.
        self._run_deferred_gc()
        return result

    def _transcribe_with_fallback_unlocked(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        # pass through the pre-computed stats to the CPU retry as well.
        return self._with_gpu_fallback(self._transcribe_unlocked, audio, audio_stats=audio_stats)

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0):
        """Transcribe audio array into word timings with a global offset.

        Uses the inference-counter pattern (mirrors ``transcribe`` and
        ``transcribe_with_fallback``): acquire ``self._lock`` only long
        enough to increment ``_active_inference``, then release before
        the (potentially multi-second) GPU inference so ``unload()`` /
        ``is_loaded`` aren't blocked. ``unload()`` waits on
        ``_inference_cond`` for the counter to drain before nulling
        ``self._model``. Deferred gc (``_pending_gc_collect``) fires
        AFTER the lock is released.
        """
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
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if len(audio) == 0:
            return []

        from voice_typer.server.streaming import WordTiming

        segments, _info = self._model.transcribe(
            audio,
            beam_size=self.beam_size,
            best_of=self.best_of,
            temperature=0.0,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            language=self.language,
            condition_on_previous_text=self.condition_on_previous_text,
            word_timestamps=True,
            without_timestamps=False,
        )

        words = []
        segment_count = 0
        for seg in segments:
            # Check the abort token BETWEEN segment iterations,
            # mirroring the batch path (``_transcribe_unlocked`` above).
            # ``segments`` is a generator that yields one segment at a
            # time, with each ``next()`` call driving a ctranslate2
            # decoding step. Without this check, an ESC / watchdog
            # cancel during streaming word-timestamp transcription would
            # only take effect after the full audio finished decoding —
            # unbounded latency instead of within-one-segment latency.
            if self._abort_event.is_set():
                log.info(
                    "[TRANSCRIBE] Abort requested — stopping streaming "
                    "words segment loop early (completed %d segments, "
                    "%d words)",
                    segment_count,
                    len(words),
                )
                break
            segment_count += 1
            for word in getattr(seg, "words", None) or []:
                text = (word.word or "").strip()
                if not text:
                    continue
                start = (word.start or 0.0) + offset_seconds
                end = (word.end or word.start or 0.0) + offset_seconds
                words.append(
                    WordTiming(
                        word=text,
                        start_seconds=start,
                        end_seconds=end,
                    )
                )
        return words

    def _is_gpu_runtime_error(self, exc: Exception) -> bool:
        """detect GPU/CUDA runtime errors via class hierarchy +
        attribute checks first, falling back to substring matching
        only for wrapped/re-raised errors. Previously the substring
        list was the primary check, misclassifying new error classes
        (e.g. ROCm) and triggering wrong fallbacks.

        Phase 1c (PLAN_ONNX_INTEGRATION.md §6.5): the
        ``isinstance(exc, torch.cuda.OutOfMemoryError)`` check was
        replaced with :func:`is_oom_error` (shared ASR utility) so
        ``transcription.py`` no longer imports ``torch``. The OOM
        classifier is kept separate from the CUDA classifier
        (:func:`voice_typer.server.asr_utils.is_cuda_error`) because
        ``"out of memory"`` alone is too broad — it matches CPU RAM
        exhaustion which should NOT trigger the GPU→CPU fallback.
        """
        if self._device == "cpu":
            return False
        # 1. OOM check (replaces torch.cuda.OutOfMemoryError isinstance).
        #    ``is_oom_error`` is the shared classifier in
        #    ``voice_typer.server.asr_utils`` — kept separate from the
        #    CUDA classifier so CPU RAM exhaustion does not false-positive.
        if is_oom_error(exc):
            return True
        # ctranslate2 errors (faster-whisper wraps these)
        try:
            import ctranslate2

            # Some ctranslate2 builds don't expose CUDAError as a class.
            # Guard with isinstance check on the attribute type.
            for attr_name in ("CUDAError", "RuntimeError"):
                cls = getattr(ctranslate2, attr_name, None)
                if isinstance(cls, type) and isinstance(exc, cls):
                    return True
        except (ImportError, AttributeError, ValueError):
            # ImportError: ctranslate2 not installed.
            # AttributeError: ctranslate2 installed but missing CUDAError/RuntimeError attrs.
            # ValueError: ctranslate2's import chain (transformers → PIL vision check)
            #   can raise ValueError("PIL.__spec__ is not set") in environments where
            #   PIL was imported via a non-standard path. This is environment noise,
            #   not a real GPU error — fall through to the substring/MRO check below.
            pass
        # 2. MRO-based class-name check (catches wrapped exceptions
        #    whose original class still appears in the MRO).
        for cls in type(exc).__mro__:
            cls_name = cls.__name__.lower()
            if any(kw in cls_name for kw in ["cudnn", "cublas", "cuda", "ctranslate2"]):
                return True
            cls_module = getattr(cls, "__module__", "") or ""
            if any(kw in cls_module.lower() for kw in ["ctranslate2", "cudnn", "cublas"]):
                return True
        # 3. Attribute check: some libraries attach a `.cuda_error`
        #    or `.device` attribute to runtime errors.
        if getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False):
            return True
        # 4. Fallback to string matching for re-raised / wrapped errors
        #    where the original class info is lost. This is a last
        #    resort, not the primary signal.
        error_str = str(exc).lower()
        return any(
            kw in error_str
            for kw in [
                "cublas",
                "cuda",
                "cudnn",
                "gpu",
                "not found or cannot be loaded",
            ]
        )

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

        also call ``torch.cuda.empty_cache()`` so the
        PyTorch caching allocator releases freed CUDA blocks back to
        the OS.  Without this, switching backends (Whisper → Parakeet
        → Whisper) accumulates cached blocks and OOMs on RTX 3060/4060
        (8–12 GB VRAM) after ~2 switches.

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
        # (they don't touch self._model state).
        try:
            _free_nvidia_dll_path_handles()
        except Exception:
            log.debug("[MODEL] Error releasing DLL handles", exc_info=True)


def _format_optional_mean(values: list[float]) -> str:
    """Format a list of floats as a 2-decimal mean, or 'n/a' if empty.

    small helper kept as-is because it has two call sites
    (line 523/524) and inlining would duplicate the empty-list check.
    Marked LOW priority in the audit — keeping for readability.
    """
    if not values:
        return "n/a"
    return f"{sum(values) / len(values):.2f}"


def build_quality_summary(avg_logprobs: list[float], no_speech_probs: list[float]) -> dict[str, float] | None:
    """Build the compact per-dictation quality summary for the renderer.

    Computed from the ``avg_logprob`` / ``no_speech_prob`` values the
    segment loop ALREADY collected — no recomputation, one small dict of
    floats allocated once per dictation (never on the paste hot path).

    Returns ``None`` when no numeric stats were collected (empty audio,
    aborted run, or an engine that reports no segment probs) so callers
    can omit the summary entirely instead of shipping an empty object.

    Keys:
      - ``mean_logprob``: mean per-segment ``avg_logprob`` (closer to 0 =
        more confident decoding).
      - ``min_logprob``: worst single-segment ``avg_logprob``.
      - ``no_speech_prob_max``: highest per-segment ``no_speech_prob``
        (high values indicate segments the model considered silent).
      - ``segments``: how many segments contributed numeric stats.
    """
    if not avg_logprobs and not no_speech_probs:
        return None
    summary: dict[str, float] = {}
    if avg_logprobs:
        summary["mean_logprob"] = sum(avg_logprobs) / len(avg_logprobs)
        summary["min_logprob"] = min(avg_logprobs)
        summary["segments"] = float(len(avg_logprobs))
    if no_speech_probs:
        summary["no_speech_prob_max"] = max(no_speech_probs)
    return summary
