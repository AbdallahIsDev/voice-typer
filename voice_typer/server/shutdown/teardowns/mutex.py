"""Teardown helper for the single-instance mutex handle.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_mutex_handle`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def teardown_mutex_handle(controller) -> None:
    """release the single-instance mutex handle.

    PLAT-HLEAK: on Windows, ``CloseHandle`` releases the named mutex
    so a subsequent launch can claim it. On POSIX, the
    ``_mutex_handle`` is a ``_PosixSingleInstanceHandle`` wrapping
    the lockfile fd — its ``release()`` closes the fd (releasing the
    ``fcntl.flock``) and unlinks the ``backend.lock``. Without this
    branch, the Windows-only ``ctypes.windll.kernel32.CloseHandle``
    call would raise ``AttributeError`` on POSIX
    (``ctypes.windll`` is Windows-only), which was swallowed by the
    try/except, leaving the lockfile fd dangling until process exit
    and racing a fast re-launch. ``contextlib.suppress(Exception)``
    mirrors the Windows branch's best-effort contract: cleanup must
    never propagate failures.
    """
    app = controller._app
    try:
        if hasattr(app, "_mutex_handle") and app._mutex_handle:
            if is_windows():
                import ctypes

                ctypes.windll.kernel32.CloseHandle(app._mutex_handle)
            else:
                # POSIX: release the flock-based single-instance
                # handle (closes the fd + unlinks the lockfile).
                app._mutex_handle.release()
            app._mutex_handle = None
    except Exception:
        log.debug("[CLEANUP] mutex handle release failed", exc_info=True)


__all__ = ["teardown_mutex_handle"]
