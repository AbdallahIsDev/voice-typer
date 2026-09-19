"""Tests for ``voice_typer.server.microphone_watcher_coreaudio``."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher


def test_module_imports_cross_platform() -> None:
    """The module is importable on ALL platforms without pyobjc installed."""
    from voice_typer.server import microphone_watcher_coreaudio as mod

    assert hasattr(mod, "CoreAudioMicrophoneWatcher")
    assert hasattr(mod, "_try_import_coreaudio")
    assert hasattr(mod, "_IS_MACOS")


def test_import_error_when_not_macos() -> None:
    """``_try_import_coreaudio`` raises ``ImportError`` off macOS."""
    from voice_typer.server import microphone_watcher_coreaudio as mod

    with (
        patch.object(mod, "is_macos", return_value=False),
        pytest.raises(ImportError, match="only available on macOS"),
    ):
        mod._try_import_coreaudio()


def test_import_error_when_pyobjc_missing() -> None:
    """``_try_import_coreaudio`` raises ``ImportError`` when pyobjc is not installed."""
    from voice_typer.server import microphone_watcher_coreaudio as mod

    with (
        patch.object(mod, "is_macos", return_value=True),
        patch.dict(sys.modules, {"CoreAudio": None, "CoreFoundation": None}),
        pytest.raises(ImportError, match="pyobjc-framework-CoreAudio"),
    ):
        mod._try_import_coreaudio()


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Verifies the non-macOS ImportError path, on macOS pyobjc may succeed",
)
def test_coreaudio_watcher_start_raises_on_non_macos() -> None:
    """``CoreAudioMicrophoneWatcher.start`` raises ``ImportError`` off macOS."""
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    watcher = CoreAudioMicrophoneWatcher(lambda: None)
    with pytest.raises(ImportError, match="only available on macOS"):
        watcher.start()
    assert watcher._thread is None


def test_microphone_watcher_falls_back_to_polling_without_pyobjc() -> None:
    """``MicrophoneDeviceWatcher`` falls back to polling when CoreAudio is unavailable."""
    fired = threading.Event()

    def on_change() -> None:
        fired.set()

    # Force the platform to "macos" so the CoreAudio path is attempted.
    watcher = MicrophoneDeviceWatcher(on_change, poll_interval=0.05)
    watcher._platform = "macos"

    # Stub _try_create_coreaudio_watcher to simulate "pyobjc missing".
    with patch.object(watcher, "_try_create_coreaudio_watcher", return_value=None):
        # Stub _run_macos so it fires the callback once and returns —
        def fake_run_macos(self_arg):
            self_arg._invoke_callback()
            # Stop immediately so the test doesn't hang.
            self_arg._stop_event.set()

        with patch.object(MicrophoneDeviceWatcher, "_run_macos", fake_run_macos):
            watcher.start()
            assert fired.wait(timeout=2.0), "polling fallback did not fire"
            watcher.stop()

    assert watcher._coreaudio_watcher is None
    # Polling thread was used (and cleaned up).
    assert watcher._thread is None  # stop() clears it


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only, CoreAudio watcher is darwin-only")
def test_instantiation_on_macos_with_pyobjc() -> None:
    """On macOS with pyobjc installed, the watcher instantiates cleanly."""
    try:
        from voice_typer.server.microphone_watcher_coreaudio import (
            CoreAudioMicrophoneWatcher,
        )
    except ImportError as exc:  # pragma: no cover, defensive
        pytest.skip(f"pyobjc-framework-CoreAudio not installed: {exc}")

    watcher = CoreAudioMicrophoneWatcher(lambda: None)
    assert watcher is not None
    # The pyobjc symbols are loaded lazily in start(), not in __init__,
    assert watcher._ca is None


# CFRunLoopRun/CFRunLoopStop contract (blocking run + foreign-thread


def _make_fake_coreaudio_symbols() -> tuple[SimpleNamespace, threading.Event]:
    """Build a ``(fake_ca, stop_event)`` pair for driving the watcher off-macOS."""
    from voice_typer.server.microphone_watcher_coreaudio import _NO_ERR

    stop_event = threading.Event()

    fake_ca = SimpleNamespace(
        add_listener=MagicMock(return_value=_NO_ERR),
        remove_listener=MagicMock(),
        property_devices=1,
        scope_global=2,
        element_master=3,
        system_object=4,
        # Production at microphone_watcher_coreaudio.py:420 does
        property_default_input=None,
        runloop_get_current=MagicMock(return_value="fake-runloop"),
        runloop_run=stop_event.wait,
        runloop_stop=lambda rl: stop_event.set(),
    )
    return fake_ca, stop_event


def _wait_for(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll ``predicate`` until it returns truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_coreaudio_start_lock_serializes_concurrent_calls() -> None:
    """UE-12-F3: concurrent ``start()`` calls spawn exactly one thread."""
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    fake_ca, _stop_event = _make_fake_coreaudio_symbols()

    with patch(
        "voice_typer.server.microphone_watcher_coreaudio._try_import_coreaudio",
        return_value=fake_ca,
    ):
        watcher = CoreAudioMicrophoneWatcher(lambda: None)
        try:
            # Fire 8 concurrent start() calls.
            threads = [threading.Thread(target=watcher.start) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Exactly one watcher thread was spawned (the lock
            assert watcher._thread is not None, "start() should have spawned a thread"
            # Exactly one listener registration, a second would mean
            assert fake_ca.add_listener.call_count == 1, (
                f"Expected exactly 1 add_listener call, got "
                f"{fake_ca.add_listener.call_count}, the lifecycle lock "
                f"failed to serialise concurrent start() calls"
            )
        finally:
            watcher.stop()


def test_coreaudio_stop_lock_serializes_concurrent_calls() -> None:
    """UE-12-F3: concurrent ``stop()`` calls are safe and idempotent."""
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    fake_ca, _stop_event = _make_fake_coreaudio_symbols()

    with patch(
        "voice_typer.server.microphone_watcher_coreaudio._try_import_coreaudio",
        return_value=fake_ca,
    ):
        watcher = CoreAudioMicrophoneWatcher(lambda: None)
        watcher.start()
        # Wait for the watcher thread to publish _run_loop (confirms
        assert _wait_for(lambda: watcher._run_loop is not None), "watcher thread did not publish _run_loop"

        # Fire 8 concurrent stop() calls, only one should call
        threads = [threading.Thread(target=watcher.stop) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # _thread cleared by stop().
        assert watcher._thread is None
        # _run_loop cleared by stop().
        assert watcher._run_loop is None


def test_coreaudio_listener_calls_on_change_directly() -> None:
    """UE-12-F9: the listener proc calls ``_on_change`` directly."""
    from voice_typer.server.microphone_watcher_coreaudio import (
        _NO_ERR,
        CoreAudioMicrophoneWatcher,
    )

    on_change_calls = {"count": 0}

    def on_change() -> None:
        on_change_calls["count"] += 1

    fake_ca, _stop_event = _make_fake_coreaudio_symbols()

    with patch(
        "voice_typer.server.microphone_watcher_coreaudio._try_import_coreaudio",
        return_value=fake_ca,
    ):
        watcher = CoreAudioMicrophoneWatcher(on_change)
        assert not hasattr(watcher, "_invoke_callback"), (
            "_invoke_callback should be deleted (UE-12-F9); the outer "
            "MicrophoneDeviceWatcher._invoke_callback already has "
            "try/except + debounce + active-mic-lost"
        )

        watcher.start()
        try:
            # Wait for the listener proc to be published.
            assert _wait_for(lambda: watcher._listener_proc is not None), (
                "watcher thread did not publish _listener_proc"
            )

            # Invoke the listener proc directly, this is what CoreAudio
            result = watcher._listener_proc(None, 0, None, None)

            # The proc returns noErr (0) and fires _on_change exactly once.
            assert result == _NO_ERR
            assert on_change_calls["count"] == 1, (
                f"Expected _on_change to be called once, got {on_change_calls['count']}"
            )
        finally:
            watcher.stop()


def test_coreaudio_stop_before_run_loop_published_is_safe() -> None:
    """
    UE-12-F3: ``stop()`` called before the watcher thread publishes
    ``_run_loop`` must not deadlock and must still join the thread.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    fake_ca, stop_event = _make_fake_coreaudio_symbols()

    def slow_add_listener(*args, **kwargs):
        time.sleep(0.3)  # hold the watcher thread in add_listener
        return fake_ca.add_listener.return_value

    fake_ca.add_listener.side_effect = slow_add_listener

    with patch(
        "voice_typer.server.microphone_watcher_coreaudio._try_import_coreaudio",
        return_value=fake_ca,
    ):
        watcher = CoreAudioMicrophoneWatcher(lambda: None)
        watcher.start()
        # Capture the thread object before stop() clears the ref, we
        watcher_thread = watcher._thread
        try:
            # Don't wait, call stop() immediately while the watcher
            watcher.stop()
            # If we get here, stop() did not deadlock. _thread is cleared.
            assert watcher._thread is None
        finally:
            # Manually unblock the fake CFRunLoopRun so the watcher
            stop_event.set()
            if watcher_thread is not None:
                watcher_thread.join(timeout=2.0)
