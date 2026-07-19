"""scipy.signal.resample_poly lazy-loading + background preloader.

Phase 4.5 / ARCH-045 — extracted from the original ``recording.py``
god-module.  Owns the cached ``_resample_poly`` binding, the cached
import-error state, the preloader thread, and the locks that guard
them.

The mutable globals ``_resample_poly``, ``_resample_poly_error``,
``_resample_poly_error_time``, ``_scipy_preloader_thread`` are
accessed by tests via ``voice_typer.server.recording.X`` (both reads
and writes).  The package ``__init__.py`` routes those accesses
through to this submodule via a custom module ``__getattr__`` /
``__setattr__`` so that test writes propagate to the production code
defined here.
"""

from __future__ import annotations

import logging
import threading
import time

from .exceptions import ResampleUnavailableError

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
