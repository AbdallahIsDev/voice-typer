"""Teardown helper for the three hotkey backends.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_hotkeys`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import contextlib
import logging

# ``_run_parallel_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_parallel_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_parallel_with_timeout(*args, **kwargs):
    return _sc._run_parallel_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_hotkeys(controller) -> None:
    """stop all three hotkey backends (dictation / ESC / repaste)
    in a nested parallel batch.

    The three backends touch disjoint OS resources (RegisterHotKey
    handles on Windows, evdev/X11 sockets on Linux, CGEventTap on
    macOS) and are safe to stop in parallel. Sequential stop() took
    up to 15s (3x5s) worst case; parallel stop() finishes in ≤5s.
    """
    app = controller._app
    try:
        _hk_info = (
            f"dictation={app.hotkeys._hotkey_backend.hotkey_str if app.hotkeys._hotkey_backend else 'none'}, "
            f"esc={app.hotkeys._esc_backend.hotkey_str if app.hotkeys._esc_backend else 'none'}, "
            f"repaste={app.hotkeys._repaste_backend.hotkey_str if app.hotkeys._repaste_backend else 'none'}"
        )
        log.info("[HOTKEY] Stopping hotkey listeners (%s)", _hk_info)

        # the three hotkey backends touch disjoint OS resources
        # and are safe to stop in parallel.
        parallel_stops: list[tuple[str, object, float]] = []
        if app.hotkeys._hotkey_backend:
            parallel_stops.append(("hotkey_backend.stop", app.hotkeys._hotkey_backend.stop, 5.0))
        # RELIABILITY-003: also stop ESC cancel and repaste hotkey
        # backends so their RegisterHotKey / GlobalHotKeys registrations
        # are released before the next instance tries to claim them.
        if app.hotkeys._esc_backend:
            parallel_stops.append(("esc_backend.stop", app.hotkeys._esc_backend.stop, 5.0))
        if app.hotkeys._repaste_backend:
            parallel_stops.append(("repaste_backend.stop", app.hotkeys._repaste_backend.stop, 5.0))
        # same pattern as the ``_do_cleanup`` parallel batch.
        # Per-helper failures (BaseException) are already logged at
        # WARNING here (each backend's ``stop()`` does its own
        # logging). The TIMEOUT branch uses the  message format
        # ("worker thread leaked as daemon"). A summary WARNING is
        # emitted after the loop if any backend raised or timed out.
        _degraded_hotkeys: list[str] = []
        for _desc, _result in _run_parallel_with_timeout(parallel_stops):
            if isinstance(_result, BaseException):
                log.warning("[SHUTDOWN] %s failed: %s", _desc, _result)
                _degraded_hotkeys.append(f"{_desc} (failed: {_result})")
            elif _result is TIMEOUT:
                log.warning(
                    "[SHUTDOWN] %s timed out — worker thread leaked as daemon",
                    _desc,
                )
                _degraded_hotkeys.append(f"{_desc} (timeout)")
        if _degraded_hotkeys:
            log.warning(
                "[SHUTDOWN] %d/%d hotkey backend stops degraded: %s",
                len(_degraded_hotkeys),
                len(parallel_stops),
                ", ".join(_degraded_hotkeys),
            )

        # null the hotkey backend refs after stop() so a
        # subsequent _do_cleanup pass does NOT re-enter stop() on an
        # already-torn-down backend. stop_all() on HotkeyDispatcher
        # nulls these refs, but the shutdown path calls individual
        # backends in parallel (); mirror the nulling here.
        for _attr in ("_hotkey_backend", "_esc_backend", "_repaste_backend"):
            with contextlib.suppress(Exception):
                setattr(app.hotkeys, _attr, None)

        log.info("[HOTKEY] All hotkey listeners stopped")
    except Exception:
        log.debug("[CLEANUP] hotkey backend stop failed", exc_info=True)


__all__ = ["teardown_hotkeys"]
