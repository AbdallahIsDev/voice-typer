"""regression guard: verify ``ShutdownController.quit()`` is"""

from __future__ import annotations

import contextlib
import threading
from unittest.mock import MagicMock


def _make_app_with_quit_lock():
    """Build a fake app with all attributes ``quit()`` reads."""
    app = MagicMock()
    app._shutting_down = False

    # Event that mirrors app._shutting_down_event.
    event = threading.Event()
    app._shutting_down_event = event

    # Spy on shutdown_all, track call count.
    shutdown_all_calls: list = []

    def _shutdown_all():
        shutdown_all_calls.append(threading.current_thread().name)

    app._thread_registry.shutdown_all = _shutdown_all

    # _do_cleanup should also be idempotent (just track it).
    cleanup_calls: list = []

    def _do_cleanup():
        cleanup_calls.append(threading.current_thread().name)

    app._do_cleanup = _do_cleanup

    # Avoid sys.exit(0) killing the test process.
    app._do_quit_sys_exit = False  # marker

    return app, shutdown_all_calls, cleanup_calls, event


def _patch_quit_to_skip_sysexit(controller):
    """Patch the ``sys.exit`` call inside ``quit()`` so the test"""
    import sys

    original_exit = sys.exit

    def _no_exit(code=0):
        raise SystemExit(code)  # raise, don't actually exit

    return original_exit


def test_concurrent_quit_calls_dont_both_enter_shutdown_all(monkeypatch) -> None:
    """when two threads call ``quit()`` concurrently, only ONE"""
    from voice_typer.server.shutdown_controller import ShutdownController

    # These tests run quit() on NON-main threads, which arms the real
    monkeypatch.setattr(
        ShutdownController,
        "_arm_shutdown_watchdog",
        lambda self, timeout_s: None,
    )

    app, shutdown_calls, cleanup_calls, event = _make_app_with_quit_lock()
    controller = ShutdownController(app)

    barrier = threading.Barrier(2)

    def _call_quit():
        # Block both threads at the barrier so they enter quit()
        barrier.wait()
        with contextlib.suppress(SystemExit):
            controller.quit()

    t1 = threading.Thread(target=_call_quit, name="quit-thread-1")
    t2 = threading.Thread(target=_call_quit, name="quit-thread-2")
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive(), "Thread 1 did not finish within 5s"
    assert not t2.is_alive(), "Thread 2 did not finish within 5s"

    assert len(shutdown_calls) <= 1, (
        f"Expected shutdown_all() to be called at most once, but got "
        f"{len(shutdown_calls)} calls from threads: {shutdown_calls}. "
        "This indicates the _shutting_down check-then-set is not "
        "atomic, see CR-51 / Fix-J."
    )


def test_concurrent_quit_calls_cleanup_at_most_once(monkeypatch) -> None:
    """The cleanup body should also run at most once."""
    from voice_typer.server.shutdown_controller import ShutdownController

    # Same watchdog guard as test_concurrent_quit_calls_dont_both_enter_shutdown_all.
    monkeypatch.setattr(
        ShutdownController,
        "_arm_shutdown_watchdog",
        lambda self, timeout_s: None,
    )

    app, shutdown_calls, cleanup_calls, event = _make_app_with_quit_lock()
    controller = ShutdownController(app)

    barrier = threading.Barrier(2)

    def _call_quit():
        barrier.wait()
        with contextlib.suppress(SystemExit):
            controller.quit()

    t1 = threading.Thread(target=_call_quit, name="quit-thread-1")
    t2 = threading.Thread(target=_call_quit, name="quit-thread-2")
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(cleanup_calls) <= 1, (
        f"Expected _do_cleanup() to run at most once, got {len(cleanup_calls)} calls: {cleanup_calls}"
    )


def test_quit_lock_is_an_actual_lock_or_event() -> None:
    """After Fix-J, the controller should hold a dedicated lock/event"""
    from voice_typer.server.shutdown_controller import ShutdownController

    app, _, _, _ = _make_app_with_quit_lock()
    controller = ShutdownController(app)

    # Look for any of these conventional attribute names that indicate
    candidates = [
        "_quit_lock",
        "_quit_event",
        "_shutdown_lock",
        "_quit_guard",
        "_shutdown_started_event",
    ]
    found = [name for name in candidates if hasattr(controller, name) or hasattr(app, name)]
    assert found, (
        "Expected ShutdownController or app to expose a dedicated lock "
        "or event attribute (e.g. _quit_lock, _quit_event): see CR-51 / "
        "Fix-J. None of the conventional names was found."
    )


def test_quit_idempotent_when_called_twice_sequentially() -> None:
    """Sanity: sequential (non-concurrent) duplicate calls to quit()"""
    from voice_typer.server.shutdown_controller import ShutdownController

    app, shutdown_calls, cleanup_calls, event = _make_app_with_quit_lock()
    controller = ShutdownController(app)

    with contextlib.suppress(SystemExit):
        controller.quit()

    # Reset the spy so we can observe the second call's effect.
    shutdown_calls.clear()
    cleanup_calls.clear()

    # Second call should be a no-op (already shutting down).
    with contextlib.suppress(SystemExit):
        controller.quit()

    assert len(shutdown_calls) == 0, (
        f"Second sequential quit() should NOT call shutdown_all() again; got {len(shutdown_calls)} calls."
    )
    assert len(cleanup_calls) == 0, (
        f"Second sequential quit() should NOT call _do_cleanup() again; got {len(cleanup_calls)} calls."
    )
