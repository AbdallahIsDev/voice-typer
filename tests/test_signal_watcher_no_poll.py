"""AB-32 + UE-1-F4: signal watcher must block indefinitely (no 1s poll
loop) AND survive multiple signal deliveries.

Previously ``signal_watcher_loop`` polled ``Event.wait(timeout=1.0)``
in a ``while`` loop, causing 60 kernel wakeups/minute for the entire
app lifetime and preventing deep C-states on battery. After AB-32 it
calls ``Event.wait()`` with no timeout — ``Event.set()`` from the
signal handler wakes it immediately. UE-1-F4 then wrapped the body in
``while True:`` so the watcher SURVIVES multiple signal deliveries
(a user double-tapping Ctrl+C because the first one was slow to take
effect would otherwise fall through to Python's default handler —
immediate termination with no cleanup).

This test asserts that the watcher responds promptly (well under 1s)
when the shutdown event is set, which would NOT be the case if the
old poll loop were still in place (it would block for the remainder
of whatever 1s window it was in — up to ~1s — and could exceed the
0.2s threshold). Because the watcher now loops forever (UE-1-F4), the
thread is a daemon and will be torn down at interpreter exit; the
test does NOT assert the thread exits — only that it dispatches
``quit()`` promptly on each ``Event.set()``.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from voice_typer.server.signal_handlers import signal_watcher_loop


def test_signal_watcher_exits_quickly_on_set():
    # Mock controller with the attributes signal_watcher_loop reads:
    # ``_shutdown_signal_event`` (blocked on), ``_shutdown_signum``
    # (logged), and ``quit`` (spawned on a worker thread).
    event = threading.Event()
    quit_called = threading.Event()

    def _fake_quit():
        quit_called.set()

    controller = SimpleNamespace(
        _shutdown_signal_event=event,
        _shutdown_signum=2,  # SIGINT
        quit=_fake_quit,
    )

    # Start the watcher thread. It should block on event.wait().
    t = threading.Thread(
        target=signal_watcher_loop,
        args=(controller,),
        name="test-signal-watcher",
        daemon=True,
    )
    t.start()

    # Give the watcher a moment to reach event.wait(). A tiny sleep
    # here is only for test determinism — the watcher itself does no
    # polling once it reaches the wait().
    time.sleep(0.05)

    # Signal shutdown and measure how quickly the watcher dispatches
    # ``quit()``. : the watcher loops forever, so we do NOT
    # join the thread (it's still alive, waiting for the next signal).
    # The  contract is: ``Event.set()`` wakes the watcher
    # promptly, the watcher calls ``controller.quit()`` promptly, and
    # the whole dispatch finishes well under 1s.
    start = time.perf_counter()
    event.set()
    # Wait for the quit() worker to be invoked by the watcher.
    assert quit_called.wait(timeout=0.5), "controller.quit() was not invoked"
    elapsed = time.perf_counter() - start

    assert elapsed < 0.2, (
        f"Watcher took {elapsed:.3f}s to dispatch quit() after event.set() — "
        "should be < 0.2s (AB-32: indefinite wait, not 1s poll)"
    )
    # the watcher thread is still alive, waiting for the
    # next signal. It is a daemon so it will not block process exit.
    assert t.is_alive(), (
        "Watcher thread exited after a single signal — UE-1-F4 requires it to survive multiple signal deliveries."
    )


if __name__ == "__main__":
    test_signal_watcher_exits_quickly_on_set()
    print("OK")
