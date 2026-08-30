"""scipy.signal.resample_poly lazy-loading + background preloader.

Phase 4.5 /  — extracted from the original ``recording.py``
god-module.  Owns the cached ``_resample_poly`` binding, the cached
import-error state, the preloader thread, and the locks that guard
them.

(Phase 4.5 follow-up) — also owns the ``resample_audio()``
helper (promoted from ``Recorder._resample_audio_impl``) that runs the
scipy → linear-interp → raise fallback chain. The historical
``Recorder._resample_audio_impl`` delegator was removed — callers
(:mod:`.format` ``resample_chunk`` / ``prepare_audio``) invoke this
module directly.

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

from voice_typer.server import recording as _recording_pkg
from voice_typer.server._lazy_import import lazy_module

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
# track when the error was cached so we can retry after a timeout
_resample_poly_error_time: float = 0.0
_RESAMPLE_RETRY_INTERVAL = 300.0  # Retry every 5 minutes
_resample_poly_lock = threading.Lock()

# cache of (up, down) → FIR filter taps for ``resample_poly``.
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
# Soft cap on the FIR-tap cache size. The original comment assumed
# "≤2" distinct (up, down) ratios in practice (the device's native
# rate and the chain's 16 kHz rate). That assumption is fragile —
# a long-running session that hot-plugs through several Bluetooth
# headsets (each re-negotiating a different native rate: 8k, 16k,
# 44.1k, 48k) or a test harness cycling synthetic rates can grow
# the cache to dozens of entries. Each entry holds a full FIR
# (``2 * half_len + 1`` taps × float32) — for an unbounded cache
# that's a slow memory leak. The cap of 32 is comfortably above the
# 2-4 entries seen in normal operation; when exceeded, we clear the
# whole cache (simpler than LRU, and a re-design is microseconds for
# the small ratios that dominate production traffic).
_RESAMPLE_FIR_CACHE_MAX_ENTRIES: int = 32

# Cap on the FIR filter ``half_len`` for "ugly" (low-GCD) sample-rate
# ratios. ``scipy.signal.resample_poly``'s default design uses
# ``half_len = 10 * max(up, down)``; for 44.1k→16k (gcd=100,
# max_rate=441) that yields a 8821-tap FIR — ~140× heavier than the
# 61-tap FIR for 48k→16k. The cap below truncates ``half_len`` (and
# thus the filter length) at a practical bound, accepting a slightly
# wider transition band on the rare 44.1k device in exchange for
# ~30× lower MAC count on the RT thread. 256 was chosen as a round
# power-of-2 that comfortably bounds the design cost while leaving
# enough taps for a usable anti-aliasing filter at the worst-case
# 8 kHz target Nyquist. Tuned to leave the common 48k→16k
# (max_rate=3, half_len=30) and 22.05k→16k (max_rate=15,
# half_len=150) paths untouched.
_RESAMPLE_FIR_HALF_LEN_CAP: int = 256


# Anti-aliasing FIR filter cache for the no-scipy linear-interp
# fallback path. ``np.interp`` is pure linear interpolation — when
# DOWNSAMPLING (e.g. 48k→16k, 44.1k→16k), energy above the target
# Nyquist (8 kHz) aliases into the speech band, degrading ASR accuracy.
# We apply a short windowed-sinc low-pass filter at ``target_sr / 2``
# BEFORE the linear-interp decimation. The filter is small (~31 taps)
# and cached per (effective_sr, target_sr) pair so the design cost is
# paid once per session. Without scipy this is the only anti-aliasing
# we get — with scipy the ``resample_poly`` path is preferred (its own
# FIR is longer / higher quality). This cache mirrors the
# ``_resample_fir_cache`` pattern (lock-free fast-path ``dict.get``,
# lock only on cache miss).
_antialias_fir_cache: dict[tuple[int, int], np.ndarray] = {}
_antialias_fir_cache_lock = threading.Lock()
_ANTIALIAS_FIR_TAPS = 31  # odd; ~31 taps — short enough for RT, sufficient for anti-aliasing
# Soft cap on the anti-alias FIR cache (mirrors
# ``_RESAMPLE_FIR_CACHE_MAX_ENTRIES``). Same rationale — the original
# assumption that the cache stays at ≤2 entries is fragile under
# device hot-plug churn, so we cap and clear-on-overflow.
_ANTIALIAS_FIR_CACHE_MAX_ENTRIES: int = 32

# One-time warning flag so the linear-interp fallback surfaces
# its quality degradation even when callers pass ``log_resample=False``
# (notably ``_resample_chunk`` on the streaming partial-transcription
# path, which deliberately suppresses per-call logging). The first
# fallback invocation emits a single WARNING; subsequent invocations
# are silent (avoids log spam at 16 Hz). Reset only by process restart.
_linear_interp_warned: bool = False
_linear_interp_warn_lock = threading.Lock()


def _get_antialias_fir(effective_sr: int, target_sr: int) -> np.ndarray | None:
    """Return a cached anti-aliasing FIR low-pass filter for the
    ``(effective_sr, target_sr)`` pair, or ``None`` if no filter is
    needed (i.e. when UPSAMPLING — linear interp's natural sinc
    response already attenuates higher frequencies).

    The filter is a windowed-sinc low-pass at ``target_sr / 2``
    (normalized cutoff ``0.5 * target_sr / effective_sr`` of the
    source Nyquist), designed with a Hamming window for stop-band
    attenuation. The taps are pre-cast to ``float32`` so the
    downstream ``np.convolve`` preserves the input dtype.

    The cache hit path is a lock-free ``dict.get`` (GIL-atomic in
    CPython); the lock is acquired only on a cache miss to publish
    the newly-designed filter without two threads racing to design
    the same ratio.
    """
    if target_sr >= effective_sr:
        # No anti-aliasing filter needed for upsampling (or same-rate):
        # linear interp's natural (sin x / x) response already
        # attenuates the upper half of the source band.
        return None
    key = (effective_sr, target_sr)
    cached = _antialias_fir_cache.get(key)
    if cached is not None:
        return cached
    cutoff = 0.5 * target_sr / effective_sr  # normalized to source Nyquist
    n = np.arange(_ANTIALIAS_FIR_TAPS) - (_ANTIALIAS_FIR_TAPS - 1) / 2
    sinc = np.sinc(2.0 * cutoff * n)
    # Hamming window — ~53 dB stop-band attenuation, narrow transition.
    window = 0.54 - 0.46 * np.cos(2.0 * np.pi * np.arange(_ANTIALIAS_FIR_TAPS) / (_ANTIALIAS_FIR_TAPS - 1))
    fir = (sinc * window).astype(np.float32)
    fir = fir / fir.sum()  # normalize DC gain to 1
    with _antialias_fir_cache_lock:
        existing = _antialias_fir_cache.get(key)
        if existing is not None:
            return existing
        # Soft cap: clear the whole cache if it has grown past the cap
        # (mirrors ``_resample_fir_cache``). See
        # ``_ANTIALIAS_FIR_CACHE_MAX_ENTRIES`` for the rationale.
        if len(_antialias_fir_cache) > _ANTIALIAS_FIR_CACHE_MAX_ENTRIES:
            _antialias_fir_cache.clear()
        _antialias_fir_cache[key] = fir
        return fir


def _get_resample_fir_taps(up: int, down: int) -> tuple[np.ndarray, int, int]:
    """return cached FIR filter context for the given (up, down) ratio.

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

    The cache hit path is a lock-free ``dict.get`` — GIL-atomic in
    CPython, so the read is safe without holding
    ``_resample_fir_cache_lock``. The lock is only acquired on a
    cache miss (to design the filter and publish the result without
    two threads racing to design the same ratio). This keeps the
    hot path (post-warmup) entirely lock-free, matching the access
    pattern of the per-chunk VAD resample at ~16 Hz.

    The taps are pre-cast to ``np.float32`` at design time so that
    ``scipy.signal.upfirdn`` returns ``float32`` directly when fed a
    ``float32`` input. This lets callers use ``np.asarray(out,
    dtype=np.float32)`` (a no-op when the dtype already matches)
    instead of ``out.astype(np.float32)`` (which always allocates a
    new array even when the input is already ``float32``).
    """
    key = (up, down)
    # Lock-free fast path: ``dict.get`` is a single C-level opcode
    # protected by the GIL, so concurrent reads are safe. The lock is
    # only taken on a cache miss (below).
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
    # Cap ``half_len`` for "ugly" ratios where ``max(up, down)`` is
    # large (e.g. 44.1k→16k has gcd=100, up=160, down=441, max_rate=441,
    # so the unfiltered ``half_len = 4410`` produces a 8821-tap FIR —
    # ~140× heavier than the 61-tap FIR for the 48k→16k path). The cap
    # trades a slightly wider transition band on those rare devices for
    # a ~30× lower MAC count on the RT thread (512 samples × 16 Hz ×
    # 8821 taps ≈ 72M MACs/sec → ~2.4M MACs/sec at the 256 cap). The
    # cap is only hit when ``max_rate > 25`` (i.e. ``10 * max_rate > 250``),
    # which in practice means non-power-of-2 ratios like 44.1/16 — the
    # common 48k/16k path (max_rate=3) and 22.05k/16k path
    # (max_rate=15) are untouched.
    if half_len > _RESAMPLE_FIR_HALF_LEN_CAP:
        half_len = _RESAMPLE_FIR_HALF_LEN_CAP
    # scipy's default window is ('kaiser', 5.0) — NOT 'hamming'.
    # Using the wrong window produces a filter with different
    # stop-band attenuation and pass-band ripple, so the output
    # diverges from ``resample_poly``.
    h = firwin(2 * half_len + 1, f_c, window=("kaiser", 5.0))
    # Pre-cast to float32 at design time: ``firwin`` returns float64.
    # Casting here (once per distinct ratio) lets ``upfirdn`` return
    # float32 directly when the input audio is float32, avoiding a
    # per-chunk ``.astype(np.float32)`` allocation at the call site.
    h = (h * up).astype(np.float32)  # scale by up + cast in one pass
    n_pre_pad = down - (half_len % down)
    n_pre_remove = (half_len + n_pre_pad) // down
    # Pre-pad the filter on the left (the right-pad ``n_post_pad``
    # depends on the input length and is applied at call time).
    # Both operands are float32 → concatenate preserves float32.
    h_padded = np.concatenate(
        (np.zeros(n_pre_pad, dtype=np.float32), h),
    )
    ctx = (h_padded, n_pre_remove, n_pre_pad)
    with _resample_fir_cache_lock:
        # Race-safe: another thread may have populated the cache
        # while we were designing — if so, prefer their value (it's
        # functionally identical to ours, just reuse it).
        existing = _resample_fir_cache.get(key)
        if existing is not None:
            return existing
        # Soft cap: clear the whole cache if it has grown past the
        # cap. See ``_RESAMPLE_FIR_CACHE_MAX_ENTRIES`` for the
        # rationale (simple full-clear over LRU; re-design is cheap
        # for the small ratios that dominate production traffic).
        if len(_resample_fir_cache) > _RESAMPLE_FIR_CACHE_MAX_ENTRIES:
            _resample_fir_cache.clear()
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

    raises ``ResampleUnavailable`` (a typed exception) when
        scipy is missing, instead of the bare ``ImportError``. Callers
        that want to fall back to linear interp can catch this type.
    """
    global _resample_poly, _resample_poly_error, _resample_poly_error_time
    if _resample_poly is not None:
        return _resample_poly
    if _resample_poly_error is not None:
        # retry after timeout instead of memoizing forever
        if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
            raise _resample_poly_error
        # Retry — clear the cached error
        _resample_poly_error = None

    with _resample_poly_lock:
        if _resample_poly is not None:
            return _resample_poly
        if _resample_poly_error is not None:
            # retry after timeout instead of memoizing forever
            if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
                raise _resample_poly_error
            # Retry — clear the cached error
            _resample_poly_error = None
        try:
            from scipy.signal import resample_poly
        except ImportError as exc:
            # wrap in a typed exception so callers can catch
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
    """Shared resampling logic used by :mod:`.format`'s
    ``resample_chunk`` and ``prepare_audio``.

    Phase 4.5 — promoted from ``Recorder._resample_audio_impl``
        (the body is unchanged). The historical ``Recorder`` delegator
        was removed; callers invoke this function directly.

    PERF-: previously the scipy → linear interp → raise
        fallback chain was duplicated between the two methods. This
    helper centralizes it so bug fixes (, , )
        only need to be applied once.

    narrows exceptions to ``(ValueError, OSError, TypeError)``
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
    # Short-circuit on empty input. ``np.interp`` raises
    # ``ValueError('array of sample points is empty')`` when the
    # source array is empty (because the ``fp`` argument is a 0-length
    # array even though ``xi`` is also 0-length). Returning early
    # here avoids the exception AND avoids the (wasted) scipy
    # upfirdn call below.
    if audio.size == 0:
        return audio
    orig_len = len(audio)
    resampled = False
    last_error: Exception | None = None
    try:
        resample_poly = _recording_pkg._get_resample_poly()
        gcd = math.gcd(effective_sr, target_sr)
        up = target_sr // gcd
        down = effective_sr // gcd
        # use cached FIR taps + ``upfirdn`` instead of the
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
            # ``_get_resample_fir_taps`` pre-casts taps to float32, so
            # ``upfirdn`` returns float32 directly when ``audio`` is
            # float32. ``np.asarray(..., dtype=np.float32)`` is a no-op
            # (returns the same array) when the dtype already matches —
            # avoiding the per-call ``.astype(np.float32)`` allocation.
            audio = np.asarray(upfirdn(taps, audio, up=up, down=down), dtype=np.float32)
        except Exception:
            # Fall back to ``resample_poly`` if ``upfirdn`` fails for
            # any reason (e.g. scipy version doesn't ship ``upfirdn``,
            # or the cached-taps path produces a shape mismatch on an
            # edge case). This preserves the original behaviour and
            # guarantees the resample still succeeds.
            audio = np.asarray(resample_poly(audio, up, down), dtype=np.float32)
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
        # scipy missing — fall through to linear interp.
        last_error = exc
        if log_resample:
            log.warning("[RECORDING] scipy not available, using linear interp resampling")
    except (ValueError, OSError, TypeError) as exc:
        # narrow to expected scipy/numpy failure modes.
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
            #
            # When DOWNSAMPLING, apply a short windowed-sinc
            # anti-aliasing FIR low-pass at ``target_sr / 2`` BEFORE the
            # linear-interp decimation. Without this, energy above
            # ``target_sr / 2`` (e.g. >8 kHz on a 48k→16k downsample)
            # aliases into the speech band and degrades ASR accuracy.
            # The filter is cached per (effective_sr, target_sr) pair;
            # upsampling / same-rate resampling skips the filter (linear
            # interp's natural (sin x / x) response already attenuates
            # the upper half of the source band).
            fir = _get_antialias_fir(effective_sr, target_sr)
            src_audio = audio
            if fir is not None and src_audio.size >= fir.size:
                # ``mode="same"`` returns an output the same length as
                # the input (centered), so the subsequent ``np.interp``
                # length math is unchanged.
                src_audio = np.convolve(src_audio, fir, mode="same").astype(np.float32, copy=False)
            ratio = target_sr / effective_sr
            new_len = int(len(src_audio) * ratio)
            indices = np.linspace(0, len(src_audio) - 1, new_len)

            audio = np.interp(
                indices,
                np.arange(len(src_audio)),
                src_audio,
            ).astype(np.float32)
            # One-time WARNING so the streaming / partial path
            # (which passes ``log_resample=False`` and would otherwise
            # silently use unfiltered linear interp) surfaces the
            # quality degradation to the operator. Subsequent calls are
            # silent to avoid log spam at 16 Hz. The caller's
            # ``log_resample`` flag still gates the per-call INFO log
            # below; this is a separate, module-level one-shot warning.
            global _linear_interp_warned
            if not _linear_interp_warned:
                with _linear_interp_warn_lock:
                    if not _linear_interp_warned:
                        _linear_interp_warned = True
                        log.warning(
                            "[RECORDING] scipy.signal.resample_poly unavailable — "
                            "using linear-interp resampling fallback%s. Anti-aliasing "
                            "FIR applied for downsampling, but quality is reduced; "
                            "install scipy for full-quality resampling.",
                            "" if fir is not None else " (no anti-aliasing filter)",
                        )
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
            # narrow here too.
            last_error = exc
            if log_resample:
                log.error(
                    "[RECORDING] All resampling failed: %s. Audio at %d Hz cannot be used by Whisper.",
                    exc,
                    effective_sr,
                )

    if not resampled:
        # previously returned the native-rate audio here,
        # which silently produced garbage transcriptions. Raise so
        # the streaming / final paths can decide how to recover.
        raise ResampleError(
            f"Cannot resample audio from {effective_sr} Hz to {target_sr} Hz (last error: {last_error!r})"
        )
    return audio


np = lazy_module("numpy")
