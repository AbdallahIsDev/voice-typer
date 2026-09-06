"""Whisper transcription engine using faster-whisper.

This module is the public facade for the Whisper ASR backend:
``TranscriptionEngine`` (constructed via the
``voice_typer.server.transcription`` / ``TranscriptionEngine`` lazy
string pair in ``asr_registry``), the module-level NVIDIA DLL-path
state, and every back-compat re-export (``TranscriberProtocol``,
``release_gpu_memory``, ``_download_with_retry``,
``_check_disk_space_for_download``, ``cleanup_hf_cache_dir``, ...).

Focused sibling modules hold the extracted bodies — the engine methods
are thin delegates so every historical monkeypatch path
(``voice_typer.server.transcription.<name>`` and instance/class method
patches) keeps working:

* ``transcription_device`` — ``_resolve_device`` /
  ``_resolve_device_once`` / ``_apply_auto_beam_size`` bodies.
* ``transcription_cuda_probe`` — ``_probe_cuda_runtime`` /
  ``_warm_up_model`` bodies.
* ``transcription_download`` — ``_probe_cache`` /
  ``_require_model_downloaded`` / ``_whisper_size_cached`` bodies.
* ``transcription_fallback`` — ``_with_gpu_fallback`` /
  ``_is_gpu_runtime_error`` / ``transcribe_with_fallback`` bodies.
* ``transcription_load`` — ``TranscriberProtocol`` (canonical home).
* ``transcription_result`` — parallel unit-test surface for the
  segment-decode helpers (see that module's docstring).

Kept inline here (deliberately): the load orchestrators
(``_load_transcriber_impl`` / ``_build_fallback_chain`` /
``_reload_under_lock``), the segment-decode loops
(``_transcribe_unlocked`` / ``_transcribe_words_unlocked``), the
lock-coupled GC choreography (``_run_deferred_gc`` /
``_with_lock_and_deferred_gc``), ``unload``, and the quality-summary
helpers.
"""

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
# ``transcription_cuda_probe.probe_cuda_runtime`` / ``.warm_up_model`` is
# intentional — those are hot paths that want to avoid the per-call
# proxy ``_resolve()`` overhead. They shadow the lazy proxy for the
# duration of the function.
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
from voice_typer.server._hf_cache_cleanup import (  # noqa: F401
    cleanup_failed_cache as _cleanup_failed_cache,
    cleanup_hf_cache_dir,
)
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.asr_errors import (
    ConsentRequiredError,  # noqa: F401  # re-exported for backward compat
    # ``ModelIntegrityError`` is raised by the extracted
    # ``transcription_download.require_model_downloaded`` body; kept as a
    # re-export so callers/tests importing it from ``transcription`` still
    # resolve.
    ModelIntegrityError,  # noqa: F401
    ModelNotDownloadedError,
)
from voice_typer.server.asr_utils import (  # noqa: F401
    _check_disk_space_for_download,
    _download_with_retry,
    _require_huggingface_consent,
    is_oom_error,
    release_gpu_memory,
)
from voice_typer.server.hallucination import should_reject_low_audio_hallucination
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
#
# The four blocks below import the extracted engine-helper bodies. The
# engine methods on ``TranscriptionEngine`` are thin delegates so every
# historical monkeypatch path (module-level ``voice_typer.server
# .transcription.<name>`` patches via late binding, and instance/class
# method patches via engine-object dispatch) keeps working.
#
# CUDA-probe + kernel warm-up bodies (``_probe_cuda_runtime`` /
# ``_warm_up_model``) — the CPU-fallback dispatches through the engine
# object so instance-level monkeypatches
# (``engine._reload_under_lock = MagicMock()``) keep taking effect.
from voice_typer.server.transcription_cuda_probe import (  # noqa: E402
    probe_cuda_runtime as _probe_cuda_runtime_impl,
    warm_up_model as _warm_up_model_impl,
)

# Device-resolution bodies (``_resolve_device`` / ``_resolve_device_once`` /
# ``_apply_auto_beam_size``) — read ``_configure_nvidia_dll_paths`` /
# ``_cuda_runtime_available`` / ``_auto_beam_size`` via call-time late
# binding on THIS module so the ``voice_typer.server.transcription.<name>``
# monkeypatch paths keep working.
from voice_typer.server.transcription_device import (  # noqa: E402
    apply_auto_beam_size as _apply_auto_beam_size_impl,
    resolve_device as _resolve_device_impl,
    resolve_device_once as _resolve_device_once_impl,
    whisper_cpu_threads as _whisper_cpu_threads_impl,
)

# HF cache-probe / download-gate bodies (``_probe_cache`` /
# ``_require_model_downloaded`` / ``_whisper_size_cached``) — the gate
# NEVER downloads or deletes models automatically.
from voice_typer.server.transcription_download import (  # noqa: E402
    probe_cache as _probe_cache_impl,
    require_model_downloaded as _require_model_downloaded_impl,
    whisper_size_cached as _whisper_size_cached_impl,
)

# GPU→CPU fallback orchestration + error classifier + the public
# transcribe-with-fallback wrapper — all engine-coupled state is
# dispatched through the engine object.
from voice_typer.server.transcription_fallback import (  # noqa: E402
    is_gpu_runtime_error as _is_gpu_runtime_error_impl,
    transcribe_with_fallback as _transcribe_with_fallback_impl,
    with_gpu_fallback as _with_gpu_fallback_impl,
)
from voice_typer.server.transcription_load import TranscriberProtocol  # noqa: F401, E402

# Segment-decode loop body (``_transcribe_unlocked`` delegate target) —
# canonical home is ``transcription_result``; imported under the historical
# local alias so the delegate reads like its sibling ``*_impl`` delegates.
from voice_typer.server.transcription_result import (  # noqa: E402
    transcribe_unlocked as _transcribe_unlocked_impl,
    transcribe_words_unlocked as _transcribe_words_unlocked_impl,
)

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


# Wide-beam default applied automatically on CUDA for non-tiny models.
# Beam search trades decode speed (~2x slower than greedy) for a 1-3%
# WER improvement on common benchmarks — an acceptable trade only where
# decode is fast enough that dictation stays responsive (GPU) and the
# model family is accurate enough to benefit from it.
AUTO_CUDA_BEAM_SIZE = 5


def _auto_beam_size(model_size: str, device: str) -> int:
    """Beam width used when the user has not configured one explicitly.

    Returns the wide accuracy-biased beam only for non-tiny models on a
    resolved CUDA device; tiny models and every CPU path keep the fast
    greedy default of 1 so low-end hardware stays responsive.
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
        beam_explicitly_configured = beam_size > 1
        if config is not None:
            cfg_whisper_beam_size = getattr(config, "whisper_beam_size", None)
            if cfg_whisper_beam_size is not None and cfg_whisper_beam_size != 1:
                effective_beam_size = cfg_whisper_beam_size
                beam_explicitly_configured = True
        self.beam_size = effective_beam_size
        # When neither the legacy ``beam_size`` kwarg nor the preferred
        # ``whisper_beam_size`` field was raised by the user, the beam
        # width is resolved automatically once the real device is known
        # (see ``_apply_auto_beam_size``): accuracy-biased wide beams on
        # CUDA for non-tiny models, the snappy greedy default everywhere
        # else (tiny models, all CPU paths).
        self._beam_size_auto = not beam_explicitly_configured
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

        Thin delegate to ``transcription_device.resolve_device`` — the
        try/except is narrowed to ``(OSError, RuntimeError, ImportError)``
        so genuine setup bugs propagate.
        """
        return _resolve_device_impl(self, device)

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

        Thin delegate to ``transcription_device.resolve_device_once`` —
        defers the expensive ``import ctranslate2`` / CUDA DLL load to
        load time (~20s startup savings).
        """
        _resolve_device_once_impl(self)

    def _apply_auto_beam_size(self) -> None:
        """Re-resolve ``self.beam_size`` when the user left it on auto.

        Thin delegate to ``transcription_device.apply_auto_beam_size`` —
        re-runs on every resolved-device change so wide beams apply only
        on CUDA with non-tiny models; explicit beams are never touched.
        """
        _apply_auto_beam_size_impl(self)

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
                # cpu_threads: CTranslate2 silently defaults to 4
                # intra-op threads when the option is omitted — the
                # CPU path (and the whole GPU→CPU fallback chain) would
                # leave most cores idle on wide machines. Pass a
                # hardware-derived budget capped at a safe ceiling
                # (ignored on CUDA, applied automatically when the
                # fallback chain lands on CPU). num_workers maps to
                # CTranslate2's inter_threads — pinned to 1 (the
                # library default) so the single-decoder contract is
                # explicit and no thread budget is doubled up.
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
        """Probe CUDA with a real transcription to force early cuBLAS/cuDNN
        loading.

        Thin delegate to ``transcription_cuda_probe.probe_cuda_runtime`` —
        1s sine-wave smoke test right after a CUDA load so DLL failures
        surface at startup (with a clean CPU fallback), not mid-recording.
        """
        _probe_cuda_runtime_impl(self, progress_callback)

    def _warm_up_model(self) -> None:
        """Run a warm-up inference with silence to prime CUDA kernels.

        Thin delegate to ``transcription_cuda_probe.warm_up_model`` — a
        0.5s silence transcription after model load so the first real
        dictation skips the 2-5s JIT kernel-compilation cost. No-op on
        CPU or on failure.
        """
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
        """Phase 1: probe the HuggingFace cache (local-only).

        Thin delegate to ``transcription_download.probe_cache`` — returns
        ``(local_dir, integrity_failed)`` for the caller to turn into
        ``ModelNotDownloadedError`` / ``ModelIntegrityError``.
        """
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
        """Ensure the Whisper model is present in the local HF cache.

        Thin delegate to ``transcription_download.require_model_downloaded``
        — the NEVER-auto-download gate (raises ``ModelNotDownloadedError``
        on miss / ``ModelIntegrityError`` on a tampered hit, no deletion).
        """
        _require_model_downloaded_impl(self, model_size, progress_callback)

    def _whisper_size_cached(self, model_size: str) -> bool:
        """Local-only probe: is ``model_size`` fully present in the HF cache?

        Thin delegate to ``transcription_download.whisper_size_cached`` —
        used by the fallback chain to skip undownloaded entries; returns
        ``True`` when the probe is inconclusive so the load proceeds.
        """
        return _whisper_size_cached_impl(self, model_size)

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
        """Thin delegate to ``transcription_result.transcribe_unlocked``.

        The segment-decode loop body lives canonically in
        ``transcription_result`` (also unit-tested directly there with
        stub engines). This method keeps the historical monkeypatch seam:
        instance/class-level patches of ``_transcribe_unlocked``
        intercept before delegation.
        """
        return _transcribe_unlocked_impl(self, audio, audio_stats=audio_stats)

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

        Thin delegate to ``transcription_fallback.with_gpu_fallback`` —
        the shared teardown/reload/retry orchestration for the batch and
        streaming transcribe paths. Non-GPU errors re-raise unchanged.
        """
        return _with_gpu_fallback_impl(self, inner, audio, *args, **kwargs)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe with automatic CPU fallback on GPU runtime errors.

        Thin delegate to ``transcription_fallback.transcribe_with_fallback``
        — uses the inference-counter lock pattern so ``unload()`` is never
        blocked by the segment-decoding loop; deferred gc fires outside
        the lock afterwards.

        ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own stats computation.
        """
        return _transcribe_with_fallback_impl(self, audio, audio_stats=audio_stats)

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
        """Thin delegate to ``transcription_result.transcribe_words_unlocked``.

        The streaming word-timestamp loop lives canonically there
        (unit-tested directly with stub engines). This method keeps
        the historical monkeypatch seam: instance/class-level patches
        of ``_transcribe_words_unlocked`` intercept before delegation.
        """
        return _transcribe_words_unlocked_impl(self, audio, offset_seconds)

    def _is_gpu_runtime_error(self, exc: Exception) -> bool:
        """detect GPU/CUDA runtime errors.

        Thin delegate to ``transcription_fallback.is_gpu_runtime_error``
        — class hierarchy + attribute checks first, substring matching
        only as a last resort for wrapped/re-raised errors.
        """
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


# Back-compat re-export: the canonical body lives in
# ``transcription_result`` (single source); tests and any external
# callers historically imported it from THIS facade module.
from voice_typer.server.transcription_result import (  # noqa: E402, F401
    build_quality_summary,
)
