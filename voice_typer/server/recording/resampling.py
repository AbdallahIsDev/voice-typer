"""scipy.signal.resample_poly lazy-loading + background preloader.

Phase 4.5 / , extracted from the original ``recording.py``
god-module.  Owns the cached ``_resample_poly`` binding, the cached
import-error state, the preloader thread, and the locks that guard
them.

(Phase 4.5 follow-up), also owns the ``resample_audio()``
helper (promoted from ``Recorder._resample_audio_impl``) that runs the
scipy → linear-interp → raise fallback chain. The historical
``Recorder._resample_audio_impl`` delegator was removed, callers
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
``resample_audio()`` resolves ``_get_resample_poly()`` as a module
global at call time, so a test patch of the OWNING module —
``monkeypatch.setattr("voice_typer.server.recording.resampling._get_resample_poly", lambda: fake)``
— takes effect. The historical package-namespace indirection was
removed per C-ARCH-2 (the recording package now matches the canonical
shape of its ``server_platform``/``prewarm`` siblings).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from voice_typer.server._lazy_import import lazy_module

from .exceptions import ResampleError, ResampleUnavailableError

# All submodules use the package-level logger so log records propagate
log = logging.getLogger("voice_typer.server.recording")


_resample_poly = None
_resample_poly_error: Exception | None = None
# track when the error was cached so we can retry after a timeout
_resample_poly_error_time: float = 0.0
_RESAMPLE_RETRY_INTERVAL = 300.0  # Retry every 5 minutes
_resample_poly_lock = threading.Lock()

# cache of (up, down) → FIR filter taps for ``resample_poly``.
_resample_fir_cache: dict[tuple[int, int], tuple[np.ndarray, int, int]] = {}
_resample_fir_cache_lock = threading.Lock()
# Soft cap on the FIR-tap cache size. The original comment assumed
_RESAMPLE_FIR_CACHE_MAX_ENTRIES: int = 32

# Cap on the FIR filter ``half_len`` for "ugly" (low-GCD) sample-rate
_RESAMPLE_FIR_HALF_LEN_CAP: int = 256


# Anti-aliasing FIR filter cache for the no-scipy linear-interp
_antialias_fir_cache: dict[tuple[int, int], np.ndarray] = {}
_antialias_fir_cache_lock = threading.Lock()
_ANTIALIAS_FIR_TAPS = 31  # odd; ~31 taps, short enough for RT, sufficient for anti-aliasing
# Soft cap on the anti-alias FIR cache (mirrors
_ANTIALIAS_FIR_CACHE_MAX_ENTRIES: int = 32

# One-time warning flag so the linear-interp fallback surfaces
_linear_interp_warned: bool = False
_linear_interp_warn_lock = threading.Lock()


def _get_antialias_fir(effective_sr: int, target_sr: int) -> np.ndarray | None:
    """Return a cached anti-aliasing FIR low-pass filter for the
    ``(effective_sr, target_sr)`` pair, or ``None`` if no filter is
    needed (i.e. when UPSAMPLING, linear interp's natural sinc
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
        return None
    key = (effective_sr, target_sr)
    cached = _antialias_fir_cache.get(key)
    if cached is not None:
        return cached
    cutoff = 0.5 * target_sr / effective_sr  # normalized to source Nyquist
    n = np.arange(_ANTIALIAS_FIR_TAPS) - (_ANTIALIAS_FIR_TAPS - 1) / 2
    sinc = np.sinc(2.0 * cutoff * n)
    # Hamming window, ~53 dB stop-band attenuation, narrow transition.
    window = 0.54 - 0.46 * np.cos(2.0 * np.pi * np.arange(_ANTIALIAS_FIR_TAPS) / (_ANTIALIAS_FIR_TAPS - 1))
    fir = (sinc * window).astype(np.float32)
    fir = fir / fir.sum()  # normalize DC gain to 1
    with _antialias_fir_cache_lock:
        existing = _antialias_fir_cache.get(key)
        if existing is not None:
            return existing
        # Soft cap: clear the whole cache if it has grown past the cap
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

    The cache hit path is a lock-free ``dict.get``, GIL-atomic in
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
    cached = _resample_fir_cache.get(key)
    if cached is not None:
        return cached
    # Cache miss, design the filter. This is the same algorithm
    from scipy.signal import firwin

    # Mirror scipy.signal.resample_poly's filter design exactly:
    max_rate = max(up, down)
    f_c = 1.0 / max_rate
    half_len = 10 * max_rate
    # Cap ``half_len`` for "ugly" ratios where ``max(up, down)`` is
    if half_len > _RESAMPLE_FIR_HALF_LEN_CAP:
        half_len = _RESAMPLE_FIR_HALF_LEN_CAP
    # scipy's default window is ('kaiser', 5.0). NOT 'hamming'.
    h = firwin(2 * half_len + 1, f_c, window=("kaiser", 5.0))
    # Pre-cast to float32 at design time: ``firwin`` returns float64.
    h = (h * up).astype(np.float32)  # scale by up + cast in one pass
    n_pre_pad = down - (half_len % down)
    n_pre_remove = (half_len + n_pre_pad) // down
    # Pre-pad the filter on the left (the right-pad ``n_post_pad``
    h_padded = np.concatenate(
        (np.zeros(n_pre_pad, dtype=np.float32), h),
    )
    ctx = (h_padded, n_pre_remove, n_pre_pad)
    with _resample_fir_cache_lock:
        # Race-safe: another thread may have populated the cache
        existing = _resample_fir_cache.get(key)
        if existing is not None:
            return existing
        # Soft cap: clear the whole cache if it has grown past the
        if len(_resample_fir_cache) > _RESAMPLE_FIR_CACHE_MAX_ENTRIES:
            _resample_fir_cache.clear()
        _resample_fir_cache[key] = ctx
        return ctx


def _upfirdn_output_len(len_h: int, n_in: int, up: int, down: int) -> int:
    """Output length of ``scipy.signal.upfirdn`` for the given shapes.

    Mirrors scipy's private ``_output_len`` helper, the formula
    ``((n_in - 1) * up + len_h - 1) // down + 1``, so the cached-taps
    fast path can reproduce ``resample_poly``'s padding decisions
    without reaching into scipy internals.
    """
    return ((n_in - 1) * up + len_h - 1) // down + 1


def _resample_via_cached_taps(
    taps: tuple[np.ndarray, int, int],
    audio: np.ndarray,
    up: int,
    down: int,
    upfirdn_fn: Any,
) -> np.ndarray:
    """Run the cached-FIR-taps fast path (scipy ``resample_poly`` equivalent).

    ``taps`` is the ``(h_padded, n_pre_remove, n_pre_pad)`` 3-tuple from
    :func:`_get_resample_fir_taps`; ``upfirdn_fn`` is the caller's
    resolved ``scipy.signal.upfirdn`` callable (kept a parameter so each
    call site's lazy-import / patch seam is preserved).

    Mirrors ``resample_poly``'s output handling exactly:

    * pad the taps with the trailing zeros scipy computes as
      ``n_post_pad`` so the raw ``upfirdn`` output has at least
      ``n_pre_remove + n_out`` samples, and
    * slice ``raw[n_pre_remove : n_pre_remove + n_out]`` where
      ``n_out = ceil(n_in * up / down)``, the leading ``n_pre_remove``
      samples are the filter's spin-up transient.

    Returns float32 (the cached taps are pre-cast float32, so a float32
    input yields a float32 output with no copy; a float64 input is
    converted, matching the previous ``np.asarray(..., dtype=np.float32)``
    contract at every call site).

    NOTE: the output equals ``resample_poly(audio, up, down)`` whenever
    the shared filter design is identical: i.e. for ratios where the
    ``_RESAMPLE_FIR_HALF_LEN_CAP`` below does not bite (max(up, down)
    ≤ 25: 8k↔16k, 16k↔48k, 48k→16k, 96k→16k, ..., verified
    bit-identical for float32 input). For capped "ugly" ratios
    (44.1k→16k, 22.05k→16k) the shorter FIR intentionally trades a
    wider transition band for ~30× fewer MACs; the output is then
    close to, but not identical with, ``resample_poly`` (length and
    finiteness still pinned by the numeric-equivalence test).
    """
    h_padded, n_pre_remove, _n_pre_pad = taps
    n_in = audio.size
    n_out = n_in * up // down + bool(n_in * up % down)
    # Replicate scipy's ``n_post_pad`` loop: append trailing zeros until
    n_post_pad = 0
    while _upfirdn_output_len(h_padded.size + n_post_pad, n_in, up, down) < n_out + n_pre_remove:
        n_post_pad += 1
    if n_post_pad:
        h_padded = np.concatenate((h_padded, np.zeros(n_post_pad, dtype=h_padded.dtype)))
    raw = upfirdn_fn(h_padded, audio, up=up, down=down)
    return np.asarray(raw[n_pre_remove : n_pre_remove + n_out], dtype=np.float32)


# PERF-001: eagerly preload scipy.signal.resample_poly at module import
def _preload_resample_poly() -> None:
    """Background preloader for scipy.signal.resample_poly."""
    try:
        from scipy.signal import resample_poly  # noqa: F401

        _get_resample_poly()
    except Exception:
        # Error will be cached by _get_resample_poly on first real use.
        pass


# THREAD-REGISTRY: store the preloader thread reference so Recorder can
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
    ``_resample_poly`` is still None: i.e. the previous attempt failed
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
        # Don't re-spawn if scipy already loaded successfully, the
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
        # Retry, clear the cached error
        _resample_poly_error = None

    with _resample_poly_lock:
        if _resample_poly is not None:
            return _resample_poly
        if _resample_poly_error is not None:
            # retry after timeout instead of memoizing forever
            if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
                raise _resample_poly_error
            # Retry, clear the cached error
            _resample_poly_error = None
        try:
            from scipy.signal import resample_poly
        except ImportError as exc:
            # wrap in a typed exception so callers can catch
            typed = ResampleUnavailableError(f"scipy.signal.resample_poly unavailable: {exc}")
            _resample_poly_error = typed
            _resample_poly_error_time = time.monotonic()
            raise typed from exc
        _resample_poly = resample_poly
        return _resample_poly


def warm_up_resampler(recorder: Any) -> None:
    """Import and initialize the high-quality resampler before recording stops.

    Promoted from ``Recorder.warm_up_resampler`` (Phase 4.5 completion) —
    the body is unchanged. Callers invoke ``recorder.warm_up_resampler()``
    (a documented 1-line delegator on ``Recorder``); instance-level
    ``MagicMock`` patches of ``recorder.warm_up_resampler`` keep working
    because the delegator itself is replaced.

    Patch-path compatibility: resolves ``_get_resample_poly`` as a
    module-global at call time, so a test patch of the OWNING module
    (``monkeypatch.setattr("voice_typer.server.recording.resampling._get_resample_poly", ...)``)
    takes effect (C-ARCH-2, the package-namespace indirection was removed).
    """
    try:
        resample_poly = _get_resample_poly()
        # ``_get_resample_poly()`` may legitimately return
        if resample_poly is None:
            log.warning("[RECORDING] scipy not available, will use linear interp resampling")
            return
        resample_poly(np.zeros(32, dtype=np.float32), 160, 441)
        log.debug("[RECORDING] Resampler warmed up")
    except ImportError:
        log.warning("[RECORDING] scipy not available, will use linear interp resampling")
    except Exception as e:
        log.warning("[RECORDING] Resampler warm-up failed: %s", e)


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

    Phase 4.5, promoted from ``Recorder._resample_audio_impl``
        (the body is unchanged). The historical ``Recorder`` delegator
        was removed; callers invoke this function directly.

    PERF-: previously the scipy → linear interp → raise
        fallback chain was duplicated between the two methods. This
    helper centralizes it so bug fixes (, , )
        only need to be applied once.

    narrows exceptions to ``(ValueError, OSError, TypeError)``
        so genuine bugs (``AttributeError``, ``MemoryError``) propagate
        instead of being silently masked as "resampling failed".

        Patch-path compatibility: resolves ``_get_resample_poly`` as a
        module-global at call time, so a test patch of the OWNING module
        (``monkeypatch.setattr("voice_typer.server.recording.resampling._get_resample_poly", ...)``)
        takes effect (C-ARCH-2, the package-namespace indirection was removed).
    """
    if log is None:
        log = logging.getLogger("voice_typer.server.recording")
    # Short-circuit on empty input. ``np.interp`` raises
    if audio.size == 0:
        return audio
    orig_len = len(audio)
    resampled = False
    last_error: Exception | None = None
    try:
        resample_poly = _get_resample_poly()
        gcd = math.gcd(effective_sr, target_sr)
        up = target_sr // gcd
        down = effective_sr // gcd
        # use cached FIR taps + ``upfirdn`` instead of the
        try:
            from scipy.signal import upfirdn

            taps = _get_resample_fir_taps(up, down)
            # ``_resample_via_cached_taps`` trims the spin-up prefix and
            audio = _resample_via_cached_taps(taps, audio, up, down, upfirdn)
        except Exception:
            # Fall back to ``resample_poly`` if ``upfirdn`` fails for
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
        # scipy missing, fall through to linear interp.
        last_error = exc
        if log_resample:
            log.warning("[RECORDING] scipy not available, using linear interp resampling")
    except (ValueError, OSError, TypeError) as exc:
        # narrow to expected scipy/numpy failure modes.
        last_error = exc
        if log_resample:
            log.exception("[RECORDING] scipy resample_poly failed: %s", exc)

    if not resampled:
        try:
            # PERF-017: numpy linear interpolation fallback, used when
            fir = _get_antialias_fir(effective_sr, target_sr)
            src_audio = audio
            if fir is not None and src_audio.size >= fir.size:
                # ``mode="same"`` returns an output the same length as
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
            global _linear_interp_warned
            if not _linear_interp_warned:
                with _linear_interp_warn_lock:
                    if not _linear_interp_warned:
                        _linear_interp_warned = True
                        log.warning(
                            "[RECORDING] scipy.signal.resample_poly unavailable, "
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
                log.exception(
                    "[RECORDING] All resampling failed: %s. Audio at %d Hz cannot be used by Whisper.",
                    exc,
                    effective_sr,
                )

    if not resampled:
        # previously returned the native-rate audio here,
        raise ResampleError(
            f"Cannot resample audio from {effective_sr} Hz to {target_sr} Hz (last error: {last_error!r})"
        )
    return audio


np = lazy_module("numpy")
