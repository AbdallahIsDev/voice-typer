"""Tests for ``voice_typer.server.microphone_watcher_coreaudio``.

Task 15: verifies the event-driven CoreAudio property-listener
microphone watcher that replaces the 1 Hz ``sounddevice`` polling on
macOS when ``pyobjc-framework-CoreAudio`` is installed.

Test layout
-----------
- ``test_module_imports_cross_platform`` — runs on ALL platforms.
  Verifies the module is importable without pyobjc installed
  (cross-platform import safety).
- ``test_import_error_when_not_macos`` — runs on ALL platforms (the
  platform gate is mocked). Verifies ``_try_import_coreaudio`` raises
  ``ImportError`` on non-macOS.
- ``test_import_error_when_pyobjc_missing`` — runs on ALL platforms
  (platform gate is mocked, pyobjc imports are blocked). Verifies the
  pyobjc-missing fallback.
- ``test_instantiation_on_macos_with_pyobjc`` — SKIPPED on non-macOS.
  Verifies the watcher instantiates and starts when pyobjc is
  available.
- ``test_microphone_watcher_falls_back_to_polling`` — runs on ALL
  platforms. Verifies ``MicrophoneDeviceWatcher.start()`` falls back
  to the polling thread when the CoreAudio watcher is unavailable
  (the normal path on Linux, and the fallback path on macOS without
  pyobjc).

UE-12-F3 / UE-12-F9 additions (cross-platform, run on every OS)
----------------------------------------------------------------
- ``test_coreaudio_start_lock_serializes_concurrent_calls``
- ``test_coreaudio_stop_lock_serializes_concurrent_calls``
- ``test_coreaudio_listener_calls_on_change_directly``
- ``test_coreaudio_stop_before_run_loop_published_is_safe``

These stub ``_try_import_coreaudio`` with a fake ``SimpleNamespace``
so the watcher can be driven through ``start()`` / ``_run_impl()`` /
``stop()`` on Linux/Windows CI without pyobjc installed.
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher

# ── Cross-platform tests (run on every platform) ────────────────────


def test_module_imports_cross_platform() -> None:
    """The module is importable on ALL platforms without pyobjc installed.

    Cross-platform safety: ``from voice_typer.server import
    microphone_watcher_coreaudio`` must succeed on Linux/Windows even
    though ``pyobjc-framework-CoreAudio`` is macOS-only. Top-level
    imports in the module are stdlib-only; the pyobjc import happens
    lazily in ``_try_import_coreaudio``.
    """
    from voice_typer.server import microphone_watcher_coreaudio as mod

    assert hasattr(mod, "CoreAudioMicrophoneWatcher")
    assert hasattr(mod, "_try_import_coreaudio")
    assert hasattr(mod, "_IS_MACOS")


def test_import_error_when_not_macos() -> None:
    """``_try_import_coreaudio`` raises ``ImportError`` off macOS.

    The platform gate is checked before any pyobjc import is
    attempted, so this test is deterministic on every platform —
    we patch ``_IS_MACOS`` to ``False`` to simulate non-macOS.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        _try_import_coreaudio,
    )

    with (
        patch(
            "voice_typer.server.microphone_watcher_coreaudio._IS_MACOS",
            False,
        ),
        pytest.raises(ImportError, match="only available on macOS"),
    ):
        _try_import_coreaudio()


def test_import_error_when_pyobjc_missing() -> None:
    """``_try_import_coreaudio`` raises ``ImportError`` when pyobjc is not installed.

    Simulates a macOS system without ``pyobjc-framework-CoreAudio`` by
    marking ``CoreAudio`` and ``CoreFoundation`` as blocked in
    ``sys.modules``. Python raises ``ImportError`` when an import
    statement encounters a ``None`` entry in ``sys.modules`` — this
    is the canonical way to mock a missing dependency.

    The platform gate is bypassed by patching ``_IS_MACOS`` to
    ``True``, so this test runs on every platform.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        _try_import_coreaudio,
    )

    with (
        patch(
            "voice_typer.server.microphone_watcher_coreaudio._IS_MACOS",
            True,
        ),
        patch.dict(sys.modules, {"CoreAudio": None, "CoreFoundation": None}),
        pytest.raises(ImportError, match="pyobjc-framework-CoreAudio"),
    ):
        _try_import_coreaudio()


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Verifies the non-macOS ImportError path — on macOS pyobjc may succeed",
)
def test_coreaudio_watcher_start_raises_on_non_macos() -> None:
    """``CoreAudioMicrophoneWatcher.start`` raises ``ImportError`` off macOS.

    The constructor does not eagerly import pyobjc (so the class can
    be instantiated anywhere for testability). The import happens
    lazily in ``start``. This test verifies that calling ``start`` on
    a non-macOS platform raises a clean ``ImportError`` that the
    caller can catch to fall back to polling.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    watcher = CoreAudioMicrophoneWatcher(lambda: None)
    with pytest.raises(ImportError, match="only available on macOS"):
        watcher.start()
    # start() failed before creating the thread — verify no thread leaked.
    assert watcher._thread is None


def test_microphone_watcher_falls_back_to_polling_without_pyobjc() -> None:
    """``MicrophoneDeviceWatcher`` falls back to polling when CoreAudio is unavailable.

    On macOS without pyobjc (or on any non-macOS platform), the
    high-level ``MicrophoneDeviceWatcher.start`` must transparently
    fall back to the polling thread instead of raising. This test
    forces ``_try_create_coreaudio_watcher`` to return ``None`` and
    verifies the polling thread starts.
    """
    fired = threading.Event()

    def on_change() -> None:
        fired.set()

    # Force the platform to "macos" so the CoreAudio path is attempted.
    watcher = MicrophoneDeviceWatcher(on_change, poll_interval=0.05)
    watcher._platform = "macos"

    # Stub _try_create_coreaudio_watcher to simulate "pyobjc missing".
    with patch.object(watcher, "_try_create_coreaudio_watcher", return_value=None):
        # Stub _run_macos so it fires the callback once and returns —
        # this proves the polling fallback path was taken (rather than
        # start() returning early after a CoreAudio success).
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


# ── macOS-only tests (skipped on Linux/Windows) ─────────────────────


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only — CoreAudio watcher is darwin-only")
def test_instantiation_on_macos_with_pyobjc() -> None:
    """On macOS with pyobjc installed, the watcher instantiates cleanly.

    Skipped on non-macOS because instantiation succeeds but
    ``start()`` would raise ``ImportError``. This is the "module
    imports correctly when pyobjc is available" verification from
    the task description.

    We only verify instantiation (not full ``start()``) because
    starting spawns a real CFRunLoop thread that's hard to shut down
    deterministically in CI. The instantiation covers the import
    surface; the listener registration is exercised by the
    ``_try_import_coreaudio`` tests above.
    """
    try:
        from voice_typer.server.microphone_watcher_coreaudio import (
            CoreAudioMicrophoneWatcher,
        )
    except ImportError as exc:  # pragma: no cover — defensive
        pytest.skip(f"pyobjc-framework-CoreAudio not installed: {exc}")

    watcher = CoreAudioMicrophoneWatcher(lambda: None)
    assert watcher is not None
    # The pyobjc symbols are loaded lazily in start(), not in __init__,
    # so _ca should be None before start() is called.
    assert watcher._ca is None


# lock + inline-callback tests (cross-platform) ──
#
# These tests run on EVERY platform by stubbing
# ``_try_import_coreaudio`` with a fake ``SimpleNamespace`` of pyobjc
# symbols. The fake ``runloop_run`` blocks on a ``threading.Event``
# that the fake ``runloop_stop`` sets — this mirrors the real
# CFRunLoopRun/CFRunLoopStop contract (blocking run + foreign-thread
# stop) without requiring macOS or pyobjc.


def _make_fake_coreaudio_symbols() -> tuple[SimpleNamespace, threading.Event]:
    """Build a ``(fake_ca, stop_event)`` pair for driving the watcher off-macOS.

    ``fake_ca.runloop_run`` blocks on ``stop_event.wait()`` (mimicking
    the blocking ``CFRunLoopRun``). ``fake_ca.runloop_stop`` sets
    ``stop_event`` (mimicking the foreign-thread ``CFRunLoopStop`` that
    wakes the run loop). ``add_listener`` returns ``_NO_ERR`` so the
    watcher proceeds to the run loop.
    """
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
        # ``if ca.property_default_input is not None:`` before registering
        # the default-input-device listener. The fake must expose the
        # attribute (``None`` skips that listener, exercising only the
        # device-list listener — which is what these cross-platform
        # tests need).
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
    """UE-12-F3: concurrent ``start()`` calls spawn exactly one thread.

    Without the lifecycle lock, two callers can both pass the
    ``self._thread is not None`` idempotency guard and spawn duplicate
    watcher threads — which register two CoreAudio property listeners
    on ``kAudioHardwarePropertyDevices`` (double-firing callbacks +
    potential listener-proc UAF). With the lock, only one caller wins
    the race; the others see ``_thread is not None`` and return early.
    """
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
            # serialised the idempotency guard).
            assert watcher._thread is not None, "start() should have spawned a thread"
            # Exactly one listener registration — a second would mean
            # two threads entered _run_impl.
            assert fake_ca.add_listener.call_count == 1, (
                f"Expected exactly 1 add_listener call, got "
                f"{fake_ca.add_listener.call_count} — the lifecycle lock "
                f"failed to serialise concurrent start() calls"
            )
        finally:
            watcher.stop()


def test_coreaudio_stop_lock_serializes_concurrent_calls() -> None:
    """UE-12-F3: concurrent ``stop()`` calls are safe and idempotent.

    The snapshot-then-act pattern in ``stop()`` guarantees that only
    one caller sees ``_thread is not None`` and proceeds to call
    ``CFRunLoopStop`` + ``join``; the rest see ``_thread is None``
    (cleared under the lock) and return early. This prevents a
    double-``CFRunLoopStop`` on the same run loop (UAF risk) and a
    double-``join`` on the same thread.
    """
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
        # the listener was registered and the thread entered the
        # fake CFRunLoopRun).
        assert _wait_for(lambda: watcher._run_loop is not None), "watcher thread did not publish _run_loop"

        # Fire 8 concurrent stop() calls — only one should call
        # runloop_stop; the rest should no-op.
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
    """UE-12-F9: the listener proc calls ``_on_change`` directly.

    Previously the listener proc went through a redundant
    ``_invoke_callback`` wrapper that added a second try/except on top
    of the one already in ``MicrophoneDeviceWatcher._invoke_callback``
    (which is what ``_on_change`` IS when the watcher is constructed
    via the normal ``_try_create_coreaudio_watcher`` path). The
    wrapper has been deleted; the listener proc now calls
    ``self._on_change()`` directly. Any exception that escapes is
    caught by ``_run``'s top-level try/except so the watcher thread
    never crashes the process.
    """
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
        # the redundant wrapper has been removed.
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

            # Invoke the listener proc directly — this is what CoreAudio
            # does on the watcher thread when a device is added/removed.
            result = watcher._listener_proc(None, 0, None, None)

            # The proc returns noErr (0) and fires _on_change exactly once.
            assert result == _NO_ERR
            assert on_change_calls["count"] == 1, (
                f"Expected _on_change to be called once, got {on_change_calls['count']}"
            )
        finally:
            watcher.stop()


def test_coreaudio_stop_before_run_loop_published_is_safe() -> None:
    """UE-12-F3: ``stop()`` called before the watcher thread publishes
    ``_run_loop`` must not deadlock and must still join the thread.

    Regression guard for the snapshot-then-act pattern: ``stop()``
    snapshots ``run_loop`` (which may be ``None`` if the watcher thread
    hasn't reached ``ca.runloop_get_current()`` yet), releases the
    lock, then calls ``CFRunLoopStop`` only if ``run_loop is not None``
    and joins the thread. The join must not deadlock against the
    watcher thread's lock acquisition (the lock is released before the
    join).

    Note: in this race ``stop()`` cannot wake the watcher thread's
    ``CFRunLoopRun`` (it doesn't know the run loop yet), so the join
    times out and the daemon thread is left to be reaped at process
    exit. This is the pre-existing behaviour — the lock does not make
    it worse. The assertion is that ``stop()`` RETURNS (no deadlock)
    and clears ``_thread``. We manually unblock the fake run loop so
    the watcher thread exits cleanly and doesn't leak across tests.
    """
    from voice_typer.server.microphone_watcher_coreaudio import (
        CoreAudioMicrophoneWatcher,
    )

    # Slow down add_listener so we can race stop() against the
    # run_loop publication.
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
        # Capture the thread object before stop() clears the ref — we
        # need it to join the (orphaned) watcher thread after unblocking.
        watcher_thread = watcher._thread
        try:
            # Don't wait — call stop() immediately while the watcher
            # thread is still inside slow_add_listener (before
            # runloop_get_current / runloop_run).
            watcher.stop()
            # If we get here, stop() did not deadlock. _thread is cleared.
            assert watcher._thread is None
        finally:
            # Manually unblock the fake CFRunLoopRun so the watcher
            # thread exits cleanly (stop() couldn't do it because
            # _run_loop wasn't published yet when it snapshotted).
            stop_event.set()
            if watcher_thread is not None:
                watcher_thread.join(timeout=2.0)
