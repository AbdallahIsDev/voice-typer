"""Teardown helper for the three hotkey backends.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_hotkeys`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import contextlib
import logging
import time

# ``_run_parallel_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_parallel_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401
from voice_typer.server.duration import format_duration


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
    # C-LOG-2: the parallel stop is a timed operation — report its
    # duration on the completion line.
    _t0 = time.perf_counter()
    try:
        _hk_info = (
            f"dictation={app.hotkeys._hotkey_backend.hotkey_str if app.hotkeys._hotkey_backend else 'none'}, "
            f"esc={app.hotkeys._esc_backend.hotkey_str if app.hotkeys._esc_backend else 'none'}, "
            f"repaste={app.hotkeys._repaste_backend.hotkey_str if app.hotkeys._repaste_backend else 'none'}"
        )
        log.info("[HOTKEY] Stopping hotkey listeners (%s)", _hk_info)

        # the three hotkey backends touch disjoint OS resources
        # and are safe to stop in parallel. On the pooled backends
        # (ESC / repaste delegated onto the dictation backend's native
        # subprocess) ALL THREE attributes reference the SAME object —
        # stopping it three times logged ``Stopping Windows backend``
        # three times per quit and re-ran the OS-handle teardown. Dedupe
        # by object identity so each backend is stopped exactly once.
        parallel_stops: list[tuple[str, object, float]] = []
        _seen_backends: set[int] = set()
        for _attr, _desc in (
            ("_hotkey_backend", "hotkey_backend.stop"),
            ("_esc_backend", "esc_backend.stop"),
            ("_repaste_backend", "repaste_backend.stop"),
        ):
            _backend = getattr(app.hotkeys, _attr, None)
            if _backend is None or id(_backend) in _seen_backends:
                continue
            _seen_backends.add(id(_backend))
            parallel_stops.append((_desc, _backend.stop, 5.0))
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

        if not _degraded_hotkeys:
            log.info(
                "[HOTKEY] All hotkey listeners stopped%s",
                format_duration(time.perf_counter() - _t0),
            )
    except Exception:
        log.debug("[CLEANUP] hotkey backend stop failed", exc_info=True)


__all__ = ["teardown_hotkeys"]
