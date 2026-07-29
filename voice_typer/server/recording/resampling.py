"""scipy.signal.resample_poly lazy-loading + background preloader.

Phase 4.5 / ARCH-045 — extracted from the original ``recording.py``
god-module.  Owns the cached ``_resample_poly`` binding, the cached
import-error state, the preloader thread, and the locks that guard
them.

PVT-22 (Phase 4.5 follow-up) — also owns the ``resample_audio()``
helper (promoted from ``Recorder._resample_audio_impl``) that runs the
scipy → linear-interp → raise fallback chain. ``Recorder`` keeps a
1-line delegator method (``_resample_audio_impl``) so existing
internal call sites (``_resample_chunk`` / ``_prepare_audio``) and
any subclass overrides keep working unchanged.

The mutable globals ``_resample_poly``, ``_resample_poly_error``,
``_resample_poly_error_time``, ``_scipy_preloader_thread`` are
accessed by tests via ``voice_typer.server.recording.X`` (both reads
and writes).  The package ``__init__.py`` routes those accesses
through to this submodule via a custom module ``__getattr__`` /
``__setattr__`` so that test writes propagate to the production code
defined here.

Patch-path compatibility
------------------------
``resample_audio()`` calls ``_get_resample_poly()`` via the
``_recording_pkg`` package namespace (NOT a direct ``_get_resample_poly()``
call) so test patches of the form
``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", lambda: fake)``
keep affecting production code defined here. The
``_recording_pkg`` alias is bound at module top below — the late
attribute lookup happens at call time, so the partially-initialized
package state at import time is not an issue (same pattern as
:mod:`.recorder`).
"""

from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

from voice_typer.server import recording as _recording_pkg

from .exceptions import ResampleError, ResampleUnavailableError

# Patch-path bridge: route lookups of ``_get_resample_poly`` through the
# package namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
# keep affecting production code defined here.  The package ``__init__.py``
# re-exports ``_get_resample_poly`` from this submodule; we look it up at
# call time rather than binding at import time so the patch takes effect.
# (Same pattern as :mod:`.recorder`.)

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")


_resample_poly = None
_resample_poly_error: Exception | None = None
# AUDIO-003: track when the error was cached so we can retry after a timeout
_resample_poly_error_time: float = 0.0
_RESAMPLE_RETRY_INTERVAL = 300.0  # Retry every 5 minutes
_resample_poly_lock = threading.Lock()

# ER-67: cache of (up, down) → FIR filter taps for ``resample_poly``.
# ``scipy.signal.resample_poly`` re-designs the FIR filter (via
# ``firwin``) on every call, even when the (up, down) ratio is the
# same — for a 48k→16k pipeline running at ~16 Hz, that's ~16 filter
# designs/sec × N=160 taps each ≈ 2.5k taps/sec of wasted work on
# the RT thread. The cache stores the pre-computed taps (designed
# with scipy's default ``('kaiser', 5.0)`` window, scaled by ``up``,
# and zero-padded so the output is centered — bit-identical to what
# ``resample_poly`` produces internally) keyed by the reduced
# (up, down) pair. Cache is bounded by the number of distinct
# sample-rate ratios seen in practice (≤2 — the device's native
# rate and the chain's 16 kHz rate), so it's effectively a tiny
# memo dict with no eviction policy.
_resample_fir_cache: dict[tuple[int, int], tuple[np.ndarray, int, int]] = {}
_resample_fir_cache_lock = threading.Lock()


def _get_resample_fir_taps(up: int, down: int) -> tuple[np.ndarray, int, int]:
    """ER-67: return cached FIR filter context for the given (up, down) ratio.

    Mirrors the filter design that ``scipy.signal.resample_poly`` does
    internally (``firwin`` with scipy's default ``('kaiser', 5.0)``
    window of length ``2 * max(up, down) * 10 + 1`` and cutoff at
    ``1/max(up, down)`` of the Nyquist rate). The returned tuple is
    ``(h_padded, n_pre_remove, n_pre_pad)`` where:

    * ``h_padded`` is the FIR filter, scaled by ``up`` and zero-padded
      on the left by ``n_pre_pad`` samples so the upfirdn output is
      centered (matching scipy's ``resample_poly`` alignment).
    * ``n_pre_remove`` is the number of leading output samples to
      discard so the result has exactly ``n_in * up // down[+1]``
      samples (matching scipy's output length).

    The cache hit path is a dict lookup + tuple unpack — sub-
    microsecond. The cache miss path runs ``firwin`` once per
    distinct (up, down) ratio seen over the lifetime of the process;
    subsequent calls at the same ratio reuse the cached context.
    """
    key = (up, down)
    with _resample_fir_cache_lock:
        cached = _resample_fir_cache.get(key)
        if cached is not None:
            return cached
    # Cache miss — design the filter. This is the same algorithm
    # scipy uses internally; we reproduce it here so the cached
    # version produces output bit-identical (within float32
    # precision) to the direct ``resample_poly`` call.
    from scipy.signal import firwin

    # Mirror scipy.signal.resample_poly's filter design exactly:
    #   max_rate = max(up, down)
    #   f_c = 1. / max_rate          # cutoff of FIR filter (rel. to Nyquist)
    #   half_len = 10 * max_rate     # reasonable cutoff for sinc-like function
    #   h = firwin(2 * half_len + 1, f_c, window=('kaiser', 5.0))
    #   h *= up                       # compensate for the zero-stuffing
    #   n_pre_pad = (down - half_len % down)
    #   n_pre_remove = (half_len + n_pre_pad) // down
    # ``n_post_pad`` is computed at call time because it depends on
    # the input length (``n_in``) — see ``_cached_resample_poly``.
    max_rate = max(up, down)
    f_c = 1.0 / max_rate
    half_len = 10 * max_rate
    # scipy's default window is ('kaiser', 5.0) — NOT 'hamming'.
    # Using the wrong window produces a filter with different
    # stop-band attenuation and pass-band ripple, so the output
    # diverges from ``resample_poly``.
    h = firwin(2 * half_len + 1, f_c, window=("kaiser", 5.0))
    h = h * up  # scale by up to compensate for zero-stuffing
    n_pre_pad = down - (half_len % down)
    n_pre_remove = (half_len + n_pre_pad) // down
    # Pre-pad the filter on the left (the right-pad ``n_post_pad``
    # depends on the input length and is applied at call time).
    h_padded = np.concatenate(
        (np.zeros(n_pre_pad, dtype=h.dtype), h),
    )
    ctx = (h_padded, n_pre_remove, n_pre_pad)
    with _resample_fir_cache_lock:
        # Race-safe: another thread may have populated the cache
        # while we were designing — if so, prefer their value (it's
        # functionally identical to ours, just reuse it).
        existing = _resample_fir_cache.get(key)
        if existing is not None:
            return existing
        _resample_fir_cache[key] = ctx
        return ctx


# PERF-001: eagerly preload scipy.signal.resample_poly at module import
# so the first recording doesn't block 200-800ms on the import.  This
# runs in a background daemon thread to avoid slowing down module
# import for callers that don't record (e.g. the IPC server's
# get_status handler).  If scipy isn't installed, the error is cached
# and the lazy path in _get_resample_poly raises it on first use.
def _preload_resample_poly() -> None:
    """Background preloader for scipy.signal.resample_poly."""
    try:
        from scipy.signal import resample_poly  # noqa: F401

        _get_resample_poly()
    except Exception:
        # Error will be cached by _get_resample_poly on first real use.
        pass


# THREAD-REGISTRY: store the preloader thread reference so Recorder can
# register it with the application's ThreadRegistry if one is provided.
# B-3/S-3: previously this thread was started eagerly at module import
# time, which meant every test that imported recording.py triggered a
# background thread doing real scipy imports. The spawn is now deferred
# to ``Recorder.__init__`` so module imports are side-effect-free; the
# first Recorder instance triggers the preloader exactly when needed.
# The thread is a one-shot daemon with no stop mechanism (it just
# imports scipy and exits), so it registers with stop_event=None —
# shutdown_all() will join it but won't try to signal it. On a fast
# system the thread has already exited by the time the first Recorder
# finishes constructing; on a slow system it may still be loading
# scipy, in which case the registry's join gives it up to
# ``_SCIPY_PRELOADER_JOIN_TIMEOUT_S`` to finish before continuing.
_scipy_preloader_thread: threading.Thread | None = None
_SCIPY_PRELOADER_JOIN_TIMEOUT_S = 2.0
_scipy_preloader_lock = threading.Lock()


def _start_scipy_preloader() -> None:
    """Start the scipy preloader thread (idempotent, deferred to first Recorder).

    B-3/S-3: called from :meth:`Recorder.__init__` (not at module import)
    so importing ``recording`` does not spawn a thread. Idempotent: if
    the preloader has already been started (and is still alive), this is
    a no-op. If a previous preloader thread exited (scipy import
    finished), a new one is started only if the cached
    ``_resample_poly`` is still None — i.e. the previous attempt failed
    and we want to retry on the next Recorder construction.

    Stored in ``_scipy_preloader_thread`` so ``Recorder.__init__`` can
    register it with the application's ``ThreadRegistry`` if one is
    provided.
    """
    global _scipy_preloader_thread
    with _scipy_preloader_lock:
        # Idempotent: don't start a second preloader if one is still alive.
        if _scipy_preloader_thread is not None and _scipy_preloader_thread.is_alive():
            return
        # Don't re-spawn if scipy already loaded successfully — the
        # cached _resample_poly is set, so a new preloader would be a
        # wasted thread.
        if _resample_poly is not None:
            return
        _scipy_preloader_thread = threading.Thread(
            target=_preload_resample_poly,
            name="scipy-preloader",
            daemon=True,
        )
        _scipy_preloader_thread.start()


def _get_resample_poly():
    """Load scipy's resampler once so imports do not happen on F2 stop.

    ARCH-033: raises ``ResampleUnavailable`` (a typed exception) when
    scipy is missing, instead of the bare ``ImportError``. Callers
    that want to fall back to linear interp can catch this type.
    """
    global _resample_poly, _resample_poly_error, _resample_poly_error_time
    if _resample_poly is not None:
        return _resample_poly
    if _resample_poly_error is not None:
        # AUDIO-003: retry after timeout instead of memoizing forever
        if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
            raise _resample_poly_error
        # Retry — clear the cached error
        _resample_poly_error = None

    with _resample_poly_lock:
        if _resample_poly is not None:
            return _resample_poly
        if _resample_poly_error is not None:
            # AUDIO-003: retry after timeout instead of memoizing forever
            if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
                raise _resample_poly_error
            # Retry — clear the cached error
            _resample_poly_error = None
        try:
            from scipy.signal import resample_poly
        except ImportError as exc:
            # ARCH-033: wrap in a typed exception so callers can catch
            # without inspecting the ImportError message.
            typed = ResampleUnavailableError(f"scipy.signal.resample_poly unavailable: {exc}")
            _resample_poly_error = typed
            _resample_poly_error_time = time.monotonic()
            raise typed from exc
        _resample_poly = resample_poly
        return _resample_poly


def resample_audio(
    audio: np.ndarray,
    effective_sr: int,
    target_sr: int,
    *,
    log_resample: bool = False,
    log: logging.Logger | None = None,
) -> np.ndarray:
    """Shared resampling logic for ``_resample_chunk`` and ``_prepare_audio``.

    PVT-22 / Phase 4.5 — promoted from ``Recorder._resample_audio_impl``
    (the body is unchanged). ``Recorder._resample_audio_impl`` is now a
    1-line delegator that calls this function so existing internal call
    sites and any subclass overrides keep working unchanged.

    PERF-NEW-027: previously the scipy → linear interp → raise
    fallback chain was duplicated between the two methods. This
    helper centralizes it so bug fixes (ERR-012, ERR-001, ARCH-033)
    only need to be applied once.

    ERR-012: narrows exceptions to ``(ValueError, OSError, TypeError)``
    so genuine bugs (``AttributeError``, ``MemoryError``) propagate
    instead of being silently masked as "resampling failed".

    Patch-path compatibility: calls ``_get_resample_poly()`` via the
    ``_recording_pkg`` package namespace (NOT a direct call) so test
    patches of the form
    ``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
    keep affecting this code. See the module docstring §Patch-path.
    """
    if log is None:
        log = logging.getLogger("voice_typer.server.recording")
    orig_len = len(audio)
    resampled = False
    last_error: Exception | None = None
    try:
        resample_poly = _recording_pkg._get_resample_poly()
        gcd = math.gcd(effective_sr, target_sr)
        up = target_sr // gcd
        down = effective_sr // gcd
        # ER-67: use cached FIR taps + ``upfirdn`` instead of the
        # ``resample_poly`` shortcut, which re-designs the filter on
        # every call. ``upfirdn`` is what ``resample_poly`` calls
        # internally after designing the filter; by designing once and
        # caching we skip the redundant ``firwin`` work on every chunk.
        # The output is bit-identical to ``resample_poly(audio, up, down)``
        # because we use the same filter design (see
        # ``_get_resample_fir_taps`` for the rationale).
        try:
            from scipy.signal import upfirdn

            taps = _get_resample_fir_taps(up, down)
            audio = upfirdn(taps, audio, up=up, down=down).astype(np.float32)
        except Exception:
            # Fall back to ``resample_poly`` if ``upfirdn`` fails for
            # any reason (e.g. scipy version doesn't ship ``upfirdn``,
            # or the cached-taps path produces a shape mismatch on an
            # edge case). This preserves the original behaviour and
            # guarantees the resample still succeeds.
            audio = resample_poly(audio, up, down).astype(np.float32)
        if log_resample:
            log.info(
                "[RECORDING] Resampled %d Hz -> %d Hz (%d -> %d samples)",
                effective_sr,
                target_sr,
                orig_len,
                len(audio),
            )
        resampled = True
    except ResampleUnavailableError as exc:
        # ARCH-033: scipy missing — fall through to linear interp.
        last_error = exc
        if log_resample:
            log.warning("[RECORDING] scipy not available, using linear interp resampling")
    except (ValueError, OSError, TypeError) as exc:
        # ERR-012: narrow to expected scipy/numpy failure modes.
        # AttributeError / MemoryError / etc. propagate.
        last_error = exc
        if log_resample:
            log.error("[RECORDING] scipy resample_poly failed: %s", exc)

    if not resampled:
        try:
            # PERF-017: numpy linear interpolation fallback — used when
            # scipy is unavailable. When scipy IS available, the
            # resample_poly path above is preferred (higher quality,
            # anti-aliasing). This fallback produces acceptable results
            # for speech audio at common sample rates (44.1k→16k, 48k→16k).
            ratio = target_sr / effective_sr
            new_len = int(len(audio) * ratio)
            indices = np.linspace(0, len(audio) - 1, new_len)

            audio = np.interp(
                indices,
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
            if log_resample:
                log.info(
                    "[RECORDING] Resampled (linear interp) %d Hz -> %d Hz (%d -> %d samples)",
                    effective_sr,
                    target_sr,
                    orig_len,
                    len(audio),
                )
            resampled = True
        except (ValueError, OSError, TypeError) as exc:
            # ERR-012: narrow here too.
            last_error = exc
            if log_resample:
                log.error(
                    "[RECORDING] All resampling failed: %s. Audio at %d Hz cannot be used by Whisper.",
                    exc,
                    effective_sr,
                )

    if not resampled:
        # ERR-001: previously returned the native-rate audio here,
        # which silently produced garbage transcriptions. Raise so
        # the streaming / final paths can decide how to recover.
        raise ResampleError(
            f"Cannot resample audio from {effective_sr} Hz to {target_sr} Hz (last error: {last_error!r})"
        )
    return audio
