"""AB-32 + UE-1-F4: signal watcher must block indefinitely (no 1s poll"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from voice_typer.server.signal_handlers import signal_watcher_loop


def test_signal_watcher_exits_quickly_on_set():
    # Mock controller with the attributes signal_watcher_loop reads:
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
    time.sleep(0.05)

    # ``quit()``. : the watcher loops forever, so we do NOT
    # The  contract is: ``Event.set()`` wakes the watcher
    start = time.perf_counter()
    event.set()
    # Wait for the quit() worker to be invoked by the watcher.
    assert quit_called.wait(timeout=0.5), "controller.quit() was not invoked"
    elapsed = time.perf_counter() - start

    assert elapsed < 0.2, (
        f"Watcher took {elapsed:.3f}s to dispatch quit() after event.set(), "
        "should be < 0.2s (AB-32: indefinite wait, not 1s poll)"
    )
    assert t.is_alive(), (
        "Watcher thread exited after a single signal. UE-1-F4 requires it to survive multiple signal deliveries."
    )


if __name__ == "__main__":
    test_signal_watcher_exits_quickly_on_set()
    print("OK")
