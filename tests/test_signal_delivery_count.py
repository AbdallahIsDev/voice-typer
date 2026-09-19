"""Delivery counting for POSIX signal handlers and second-signal force-exit."""

from __future__ import annotations

import contextlib
import os
import signal
import threading
import time
import types

import pytest
from voice_typer.server import signal_handlers


@pytest.fixture
def no_force_exit(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Neutralize ``os._exit`` so escalation paths record instead of exiting."""
    calls: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code=0: calls.append(code))
    return calls


class _StubThread:
    """Stand-in for ``threading.Thread`` that never starts anything."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def _make_controller() -> types.SimpleNamespace:
    """Minimal controller stand-in without a preset delivery count."""
    namespace = types.SimpleNamespace(
        _shutdown_signal_event=threading.Event(),
        _shutdown_signum=None,
        _signal_watcher_started=False,
        quit_calls=[],
    )
    namespace.quit = lambda: namespace.quit_calls.append(1)
    return namespace


def test_handler_counts_deliveries_and_records_signum(
    monkeypatch: pytest.MonkeyPatch, no_force_exit: list[int]
) -> None:
    """Each handler invocation bumps ``_signal_count`` and records the signum."""
    controller = _make_controller()
    assert not hasattr(controller, "_signal_count")
    # Stub the watcher thread so the install stays hermetic (no daemon
    monkeypatch.setattr(signal_handlers.threading, "Thread", _StubThread)

    watched_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        watched_signals.append(signal.SIGHUP)
    originals = {sig: signal.getsignal(sig) for sig in watched_signals}
    try:
        signal_handlers.install_signal_handlers(controller)
        handler = signal.getsignal(signal.SIGTERM)

        handler(signal.SIGTERM, None)
        assert controller._signal_count == 1
        assert controller._shutdown_signum == signal.SIGTERM
        assert controller._shutdown_signal_event.is_set()

        controller._shutdown_signal_event.clear()
        handler(signal.SIGINT, None)
        assert controller._signal_count == 2
        assert controller._shutdown_signum == signal.SIGINT
        assert controller._shutdown_signal_event.is_set()
    finally:
        for sig, original in originals.items():
            with contextlib.suppress(Exception):
                signal.signal(sig, original)
    assert no_force_exit == []


def test_watcher_force_exits_on_second_signal(no_force_exit: list[int]) -> None:
    """A watcher woken with two deliveries recorded must force-exit."""
    controller = _make_controller()
    controller._signal_count = 2
    controller._shutdown_signum = signal.SIGTERM
    threading.Thread(
        target=signal_handlers.signal_watcher_loop,
        args=(controller,),
        name="test-signal-escalation",
        daemon=True,
    ).start()
    controller._shutdown_signal_event.set()

    deadline = time.monotonic() + 5.0
    while not no_force_exit and time.monotonic() < deadline:
        time.sleep(0.02)
    assert no_force_exit == [1]


def test_watcher_first_signal_dispatches_quit_without_force_exit(
    no_force_exit: list[int],
) -> None:
    """A single delivery dispatches ``quit()`` and must not force-exit."""
    controller = _make_controller()
    controller._signal_count = 1
    controller._shutdown_signum = signal.SIGTERM
    threading.Thread(
        target=signal_handlers.signal_watcher_loop,
        args=(controller,),
        name="test-signal-first-delivery",
        daemon=True,
    ).start()
    controller._shutdown_signal_event.set()

    deadline = time.monotonic() + 5.0
    while not controller.quit_calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert controller.quit_calls, "watcher must dispatch quit() on the first signal"
    time.sleep(0.2)
    assert no_force_exit == []
