"""RW-3: regression tests for shared cleanup between quit() and restart_app().

These tests verify that ``restart_app()`` runs the SAME critical cleanup
that ``quit()`` does — flushing ``history_db``, stopping the recorder /
mic watcher, flushing crash recovery, clearing the backend PID file,
etc.  Previously ``restart_app()`` did only a PARTIAL cleanup (cancel
timers + stop hotkey backends + stop tray) and skipped the rest,
silently losing pending DB writes and leaking PortAudio streams + the
Win32 mutex on EVERY restart.

The fix (RW-3) extracts the shared cleanup body into ``_do_cleanup()``
so ``quit()``, ``restart_app()``, and ``_atexit_cleanup()`` all run the
SAME audited shutdown path.  ``_do_cleanup()`` is idempotent (guarded
by ``_cleanup_done``) so the atexit safety net can call it
unconditionally without double-flushing.
"""

import contextlib
import sys
from unittest.mock import MagicMock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────
#
# These mirror the fixtures in tests/test_app.py but are kept local so
# this file can run independently (and so a failure here doesn't depend
# on test_app.py's fixture setup).  The autouse ``mock_heavy_imports``
# fixture from tests/conftest.py applies, mocking sounddevice /
# faster_whisper / pynput / pystray / PIL / pyperclip so the tests run
# headless.


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Point config to a temp directory (so PID file writes are isolated)."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies for cleanup tests.

    Minimal setup — we only need the app instance so we can mock its
    cleanup collaborators (recorder, history_db, etc.) and call
    ``restart_app()`` / ``quit()`` / ``_do_cleanup()`` on it.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    return instance


def _stub_restart_environment(app, monkeypatch):
    """Stub out the side-effects of restart_app() / quit() so they can
    run in tests without spawning subprocesses, sleeping, or actually
    exiting the pytest process.

    Also installs MagicMock collaborators so the tests can assert that
    cleanup methods (history_db.flush, recorder.stop, etc.) were called.
    """
    # Stub IPC push so restart_app / quit_app doesn't try to write to a
    # real TCP socket.
    # B-1: production code now calls event_bus.publish directly.
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    # Skip the 300ms pre-exit sleep in restart_app.
    monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
    # Mock sys.exit to raise SystemExit (which we catch in tests) rather
    # than actually exiting the pytest process.
    monkeypatch.setattr(
        "voice_typer.server.app.sys.exit",
        lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
    )
    # Belt-and-suspenders: don't let os._exit kill the pytest process
    # if a future regression reintroduces it.
    monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)

    # Mock the cleanup collaborators so we can assert they were called.
    # recorder.recording=True so _do_cleanup calls recorder.stop().
    app.recorder = MagicMock()
    app.recorder.recording = True
    # RecordingController._transcription_thread defaults to None, but
    # set it explicitly so the join branch is skipped deterministically.
    app.recording._transcription_thread = None
    app.hotkeys._hotkey_backend = MagicMock()
    app.hotkeys._esc_backend = MagicMock()
    app.hotkeys._repaste_backend = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app.tray = MagicMock()
    app.history_db = MagicMock()
    app._crash_recovery = MagicMock()


# ── restart_app() must share cleanup with quit() ───────────────────────


class TestRestartAppSharedCleanup:
    """RW-3: restart_app() must run the same critical cleanup as quit().

    Each test asserts that a specific cleanup operation — previously
    SKIPPED by restart_app() — is now invoked via the shared
    _do_cleanup() body.
    """

    def test_restart_app_flushes_history_db(self, app, monkeypatch):
        """history_db.flush() must be called on restart so pending
        fire-and-forget INSERTs are not silently lost when the daemon
        writer thread is killed by sys.exit.

        Regression: before RW-3, restart_app() skipped this call,
        losing any transcription history writes that hadn't been
        drained from the writer-thread queue at exit time.
        """
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.history_db.flush.assert_called_once()

    def test_restart_app_stops_recorder(self, app, monkeypatch):
        """recorder.stop() (or discard() as fallback) must be called on
        restart so the PortAudio stream is closed before the new
        instance tries to claim the microphone.

        Regression: before RW-3, restart_app() left the PortAudio
        stream open, and on Windows the next instance could fail to
        open the mic because the OS still considered it in use.
        """
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # _do_cleanup calls recorder.stop() when recorder.recording is
        # truthy, falling back to recorder.discard() if stop() raises.
        # Either path closes the PortAudio stream — assert at least one
        # was invoked.
        assert app.recorder.stop.called or app.recorder.discard.called, (
            "restart_app must call recorder.stop() or recorder.discard() to close the PortAudio stream before exiting"
        )

    def test_restart_app_clears_backend_pid_file(self, app, monkeypatch):
        """_clear_backend_pid_file() must be called on restart so the
        PID file doesn't claim a stale PID when the new instance starts.

        Regression: before RW-3, restart_app() skipped this call,
        leaving a stale backend.pid pointing at the dying process. The
        next launch's _ensure_single_instance check would then think
        the old instance was still alive (race during the kill window)
        and refuse to start, stranding the user with no backend.
        """
        _stub_restart_environment(app, monkeypatch)
        clear_calls = []
        monkeypatch.setattr(
            "voice_typer.server.app._clear_backend_pid_file",
            lambda: clear_calls.append(True),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert clear_calls == [True], (
            "restart_app must call _clear_backend_pid_file() so the next "
            "launch isn't falsely blocked by a stale PID file"
        )

    def test_restart_app_flushes_crash_recovery(self, app, monkeypatch):
        """_crash_recovery.flush() + shutdown() must be called on
        restart so the latest recovery state is persisted before the
        process exits.

        Regression: before RW-3, restart_app() skipped these calls,
        losing the in-flight crash recovery snapshot. If the new
        instance then crashed before writing its own snapshot, the
        user could be presented with a stale "did you mean to paste
        this?" recovery prompt on the next launch.
        """
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app._crash_recovery.flush.assert_called_once()
        app._crash_recovery.shutdown.assert_called_once()

    def test_restart_app_shuts_down_mic_watcher(self, app, monkeypatch):
        """recorder.shutdown_mic_watcher() must be called on restart
        so the OS-event device watcher daemon thread exits cleanly
        before the process tears down.

        Regression: before RW-3, restart_app() skipped this call. The
        watcher thread is a daemon and would die on process exit
        anyway, but explicit stop() avoids a 2s join race during GC
        that could log spurious "device changed" events as the
        process was dying.
        """
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.recorder.shutdown_mic_watcher.assert_called_once()

    @pytest.mark.skipif(
        sys.platform != "win32" and sys.platform != "darwin",
        reason="hotkey backend is None on Linux — native backends are Windows/macOS only",
    )
    def test_restart_app_stops_all_three_hotkey_backends(self, app, monkeypatch):
        """Sanity: restart_app must still stop _hotkey_backend,
        _esc_backend, and _repaste_backend (RELIABILITY-003) after the
        RW-3 refactor extracts them into _do_cleanup()."""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.hotkeys._hotkey_backend.stop.assert_called_once()
        app.hotkeys._esc_backend.stop.assert_called_once()
        app.hotkeys._repaste_backend.stop.assert_called_once()

    def test_restart_app_calls_tray_stop(self, app, monkeypatch):
        """Sanity: restart_app must still call tray.stop() (to break
        the pystray loop) after the RW-3 refactor moves it into
        _do_cleanup()."""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.tray.stop.assert_called_once()

    def test_restart_app_sets_shutting_down_before_cleanup(self, app, monkeypatch):
        """RW-3: restart_app must set _shutting_down=True BEFORE calling
        _do_cleanup(), so the atexit safety net's _shutting_down guard
        short-circuits instead of double-cleaning up.

        This invariant was already present pre-RW-3 (RELIABILITY-006);
        the test guards against a future regression that reorders the
        flag-set relative to the cleanup call.
        """
        _stub_restart_environment(app, monkeypatch)

        # Capture the value of _shutting_down at the moment _do_cleanup
        # is entered — it must already be True.
        flag_values_at_cleanup_entry = []
        original_do_cleanup = app._do_cleanup

        def spy_do_cleanup():
            flag_values_at_cleanup_entry.append(app._shutting_down)
            return original_do_cleanup()

        monkeypatch.setattr(app, "_do_cleanup", spy_do_cleanup)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert flag_values_at_cleanup_entry == [True], (
            "restart_app must set _shutting_down=True BEFORE calling "
            "_do_cleanup(); got sequence: "
            f"{flag_values_at_cleanup_entry}"
        )


# ── _do_cleanup() idempotency ──────────────────────────────────────────


class TestDoCleanupIdempotency:
    """RW-3: _do_cleanup() must be safe to call multiple times.

    The _cleanup_done flag is the hard guarantee — once True, every
    subsequent call returns immediately without re-running any cleanup
    operation. This lets _atexit_cleanup() call _do_cleanup()
    unconditionally without double-flushing history_db / double-stopping
    the recorder / double-closing the Win32 mutex handle.
    """

    @pytest.mark.skipif(
        sys.platform != "win32" and sys.platform != "darwin",
        reason="hotkey backend is None on Linux — native backends are Windows/macOS only",
    )
    def test_do_cleanup_twice_does_not_crash(self, app, monkeypatch):
        """Calling _do_cleanup() twice (e.g. once from quit() and once
        from _atexit_cleanup) must not crash, and the second call must
        be a true no-op — every cleanup collaborator is invoked
        exactly ONCE, not twice."""
        _stub_restart_environment(app, monkeypatch)

        # First call runs the full cleanup body.
        app._do_cleanup()
        # Second call must be a no-op (guarded by _cleanup_done flag).
        app._do_cleanup()

        # Each collaborator must have been called exactly once,
        # proving the second _do_cleanup() call was a no-op.
        app.recorder.stop.assert_called_once()
        app.recorder.shutdown_mic_watcher.assert_called_once()
        app.history_db.flush.assert_called_once()
        app._crash_recovery.flush.assert_called_once()
        app._crash_recovery.shutdown.assert_called_once()
        app.hotkeys._hotkey_backend.stop.assert_called_once()
        app.tray.stop.assert_called_once()

    def test_do_cleanup_idempotent_when_recorder_stop_raises(self, app, monkeypatch):
        """Idempotency must hold even when an inner operation raises.

        If recorder.stop() raises on the first call, _do_cleanup
        catches it and continues. The _cleanup_done flag is set at the
        TOP of _do_cleanup (before any operation), so a second call
        is still a no-op — we don't retry the failed operation, and
        we don't double-call any operation that already succeeded.
        """
        _stub_restart_environment(app, monkeypatch)
        # Make recorder.stop() raise on every call.
        app.recorder.stop.side_effect = RuntimeError("PortAudio already closed")

        # First call: recorder.stop() raises, discard() is called as
        # fallback (also raises — both are caught by try-except).
        app.recorder.discard.side_effect = RuntimeError("already discarded")
        # Must not propagate.
        app._do_cleanup()

        # Second call must be a no-op — recorder.stop/discard are NOT
        # retried (would be called twice if _cleanup_done wasn't set).
        app._do_cleanup()

        app.recorder.stop.assert_called_once()
        app.recorder.discard.assert_called_once()
        # history_db.flush still ran exactly once on the first call,
        # despite the recorder errors.
        app.history_db.flush.assert_called_once()


# ── _atexit_cleanup() safety net ───────────────────────────────────────


class TestAtexitCleanupSafetyNet:
    """RW-3: _atexit_cleanup() must delegate to _do_cleanup() so the
    safety net runs the SAME audited path as quit()/restart_app().

    The _shutting_down guard is preserved (early-return when
    quit/restart already ran) to avoid spurious log noise on
    intentional shutdowns. When the process is killed externally
    (_shutting_down stays False), the safety net runs the FULL
    _do_cleanup() body — flushing history_db, stopping the recorder,
    clearing the PID file, etc.
    """

    def test_atexit_cleanup_after_quit_is_noop(self, app, monkeypatch):
        """_atexit_cleanup() must early-return when _shutting_down is
        already True (set by quit() or restart_app()), so it doesn't
        double-call _do_cleanup().

        The _cleanup_done flag inside _do_cleanup() would make a
        second call a no-op anyway, but the early-return avoids the
        spurious "[ATEXIT] Running emergency cleanup" log line on
        every intentional shutdown.
        """
        _stub_restart_environment(app, monkeypatch)

        # Simulate quit() having run: set the flag, run cleanup once.
        app._shutting_down = True
        app._do_cleanup()

        # Now atexit fires — it should early-return without calling
        # _do_cleanup() a second time.
        app._atexit_cleanup()

        # recorder.stop() called exactly once (from the explicit
        # _do_cleanup() call, NOT from _atexit_cleanup).
        app.recorder.stop.assert_called_once()
        app.history_db.flush.assert_called_once()

    @pytest.mark.skipif(
        sys.platform != "win32" and sys.platform != "darwin",
        reason="hotkey backend is None on Linux — native backends are Windows/macOS only",
    )
    def test_atexit_cleanup_runs_when_not_shutting_down(self, app, monkeypatch):
        """_atexit_cleanup() must run _do_cleanup() when the process
        is killed externally (_shutting_down stays False), so critical
        cleanup (volume restore, hotkey release, DB flush, recorder
        stop, PID file clear) happens even on a forced exit.

        Regression: before RW-3, _atexit_cleanup() ran an ad-hoc
        SUBSET of cleanup (volume restore + hotkey stop + crash
        recovery flush) that diverged from quit(). It skipped
        history_db.flush, recorder.stop, mic watcher shutdown, bubble
        level worker stop, PID file clear, and mutex handle close —
        leaking the same resources that the OLD restart_app() leaked.
        """
        _stub_restart_environment(app, monkeypatch)

        # _shutting_down stays False — process was killed externally.
        assert app._shutting_down is False

        app._atexit_cleanup()

        # The full _do_cleanup() body ran via _atexit_cleanup().
        app.history_db.flush.assert_called_once()
        app.recorder.stop.assert_called_once()
        app.recorder.shutdown_mic_watcher.assert_called_once()
        app._crash_recovery.flush.assert_called_once()
        app._crash_recovery.shutdown.assert_called_once()
        app.hotkeys._hotkey_backend.stop.assert_called_once()
        app.tray.stop.assert_called_once()

    def test_atexit_cleanup_never_raises(self, app, monkeypatch):
        """_atexit_cleanup() must NEVER raise — even if _do_cleanup()
        raises an unhandled exception. A raise out of an atexit handler
        would mask the original exit cause and produce confusing
        tracebacks in the user's log."""
        _stub_restart_environment(app, monkeypatch)
        # Force _do_cleanup to raise.
        monkeypatch.setattr(app, "_do_cleanup", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        # Must not raise.
        app._atexit_cleanup()


# ── quit() still works after the refactor ──────────────────────────────


class TestQuitAppUsesSharedCleanup:
    """RW-3: quit() must use _do_cleanup() (the shared path) and still
    exit via sys.exit(0). These are sanity tests guarding against a
    future regression that removes the _do_cleanup() call from quit().
    """

    def test_quit_flushes_history_db(self, app, monkeypatch):
        """Sanity: quit() (which already did this inline pre-RW-3) must
        still flush history_db after the refactor extracts cleanup
        into _do_cleanup()."""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.quit()

        app.history_db.flush.assert_called_once()

    def test_quit_clears_backend_pid_file(self, app, monkeypatch):
        """Sanity: quit() must still clear the backend PID file after
        the refactor."""
        _stub_restart_environment(app, monkeypatch)
        clear_calls = []
        monkeypatch.setattr(
            "voice_typer.server.app._clear_backend_pid_file",
            lambda: clear_calls.append(True),
        )

        with contextlib.suppress(SystemExit):
            app.quit()

        assert clear_calls == [True]

    def test_quit_stops_recorder(self, app, monkeypatch):
        """Sanity: quit() must still call recorder.stop() (or
        discard()) after the refactor."""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.quit()

        assert app.recorder.stop.called or app.recorder.discard.called

    def test_quit_calls_do_cleanup(self, app, monkeypatch):
        """Sanity: quit() must delegate to _do_cleanup() (rather than
        inlining the cleanup body, which would re-introduce the
        divergence bug that RW-3 fixes)."""
        _stub_restart_environment(app, monkeypatch)
        do_cleanup_calls = []
        original = app._do_cleanup

        def spy():
            do_cleanup_calls.append(True)
            return original()

        monkeypatch.setattr(app, "_do_cleanup", spy)

        with contextlib.suppress(SystemExit):
            app.quit()

        assert do_cleanup_calls == [True], "quit() must call _do_cleanup() exactly once"


# ── PERF-005: event-driven relaunch ack (no fixed 300ms tray block) ────


class TestRelaunchAckEventDriven:
    """PERF-005: restart_app must wait on the ``relaunch_ack`` event from
    Electron (bounded by a 2s timeout) instead of a fixed ``time.sleep(0.3)``
    that always blocks the tray thread for 300ms.
    """

    def test_handle_relaunch_ack_sets_event(self):
        """The ``relaunch_ack`` IPC handler must set the server's
        ``_relaunch_ack_event`` so restart_app's wait returns early.
        """
        from voice_typer.server.ipc_server import IPCServer

        # Minimal stand-in for the app attribute the handler doesn't use.
        class _FakeApp:
            pass

        server = IPCServer(_FakeApp())
        assert server._relaunch_ack_event is not None
        server._relaunch_ack_event.clear()
        assert not server._relaunch_ack_event.is_set()
        # Handler returns None (no response body).
        assert server._handle_relaunch_ack({}, {}) is None
        assert server._relaunch_ack_event.is_set(), "relaunch_ack handler must set the ack event"

    def test_restart_app_waits_on_ack_event_not_fixed_sleep(self, app, monkeypatch):
        """When Electron acks (event already set), restart_app must NOT call
        the fixed 300ms sleep — it should return as soon as the event is
        observed, unblocking the tray thread.
        """
        _stub_restart_environment(app, monkeypatch)

        # Attach a fake IPC server exposing a real ack event, already set.
        import threading

        class _FakeServer:
            def __init__(self):
                self._relaunch_ack_event = threading.Event()

        fake = _FakeServer()
        fake._relaunch_ack_event.set()  # Electron already acked
        app._ipc_server = fake

        sleep_calls = []
        monkeypatch.setattr(
            "voice_typer.server.app.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert sleep_calls == [], (
            "restart_app must not call the fixed 300ms sleep when the relaunch_ack event is already set (PERF-005)"
        )

    def test_restart_app_falls_back_to_sleep_without_server(self, app, monkeypatch):
        """TY-13: when there is no IPC server (event unavailable),
        ``restart_app`` must NOT sleep at all — the previous 300ms
        fallback was removed because no IPC server means no one is
        listening for the relaunch event, so waiting accomplishes
        nothing and blocks the tray callback thread for nothing.

        Pre-TY-13 behaviour: ``time.sleep(0.3)`` fallback when no IPC
        server was attached (PERF-005's belt-and-suspenders pause).
        Post-TY-13 behaviour: skip the wait entirely (0ms). The
        ``relaunch_app`` event is delivered via ``event_bus.publish``
        BEFORE ``_wait_for_relaunch_ack`` is called, so even with no
        IPC server the host's ``pythonProcess.on("exit")`` handler
        still triggers the same relaunch as a fallback when the
        process exits — the 300ms sleep contributed nothing.
        """
        _stub_restart_environment(app, monkeypatch)
        app._ipc_server = None

        sleep_calls = []
        monkeypatch.setattr(
            "voice_typer.server.app.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert sleep_calls == [], (
            f"TY-13: restart_app must NOT sleep when no IPC server is "
            f"available — the 300ms fallback was removed (no one is "
            f"listening for the relaunch_ack). Got sleep_calls: {sleep_calls}"
        )


# restart_app re-entry guard ──────────────────────────────────


class TestRestartAppReentryGuard:
    """APP-1: ``restart_app`` must short-circuit when
    ``_shutting_down`` is already True. Without this guard, a second
    restart_app call (e.g. user double-clicks the tray restart item,
    or a tray restart races with a SIGTERM-triggered quit) would:

    1. Re-push a duplicate ``relaunch_electron`` event to Electron.
    2. Re-enter ``_do_cleanup()`` (mitigated by ``_cleanup_done`` but
       still wasteful — and the second ``sys.exit(0)`` could fire
       while the first call's finally blocks are still draining).
    3. Re-acquire ``_config_mutation_lock`` (an RLock, so technically
       re-entrant — but the toctou window re-opens).

    The guard short-circuits BEFORE any side effect so a duplicate
    call is a true no-op.
    """

    def test_restart_app_no_op_when_already_shutting_down(self, app, monkeypatch):
        """When _shutting_down is True, restart_app must return
        immediately without pushing events, saving config, or calling
        _do_cleanup()."""
        _stub_restart_environment(app, monkeypatch)

        publish_calls = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: publish_calls.append(msg),
        )
        save_calls = []
        monkeypatch.setattr(app.config, "save", lambda: save_calls.append(True) or True)
        do_cleanup_calls = []
        original_do_cleanup = app._do_cleanup

        def spy_do_cleanup():
            do_cleanup_calls.append(True)
            return original_do_cleanup()

        monkeypatch.setattr(app, "_do_cleanup", spy_do_cleanup)

        # the re-entry guard in restart_app now checks
        # ``_shutting_down_event.is_set()`` (threading.Event version)
        # instead of the plain boolean.  Setting only the boolean no
        # longer short-circuits the guard; the Event must be set too.
        app._shutting_down = True
        app._shutting_down_event.set()

        # Must NOT raise (no SystemExit, no other exception).
        app.restart_app()

        assert publish_calls == [], (
            "APP-1: restart_app must NOT push events when _shutting_down "
            "is already True; got pushes: " + repr(publish_calls)
        )
        assert save_calls == [], "APP-1: restart_app must NOT call config.save() when _shutting_down is already True"
        assert do_cleanup_calls == [], (
            "APP-1: restart_app must NOT call _do_cleanup() when _shutting_down is already True"
        )

    def test_restart_app_runs_normally_when_not_shutting_down(self, app, monkeypatch):
        """Sanity: when _shutting_down is False, restart_app must run
        the full sequence (push event, set flag, _do_cleanup, sys.exit)."""
        _stub_restart_environment(app, monkeypatch)

        publish_calls = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: publish_calls.append(msg),
        )

        assert app._shutting_down is False

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert any(msg.get("type") == "relaunch_app" for msg in publish_calls), (
            "APP-1: when _shutting_down is False, restart_app must push "
            f"the relaunch_app event; got pushes: {publish_calls}"
        )
        assert app._shutting_down is True, (
            "APP-1: when _shutting_down is False, restart_app must set it to True as part of its normal flow"
        )

    def test_restart_app_guard_is_first_statement_in_method(self):
        """Source-level invariant: the re-entry guard
        (``if self._shutting_down_event.is_set():``) must be the FIRST
        executable statement in restart_app (before the log.info call,
        before the config.save(), before any push)."""
        import inspect

        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        doc_end = src.find('"""', src.find('"""') + 3)
        assert doc_end != -1, "restart_app must have a docstring"
        body = src[doc_end + 3 :].lstrip()

        lines = body.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # the guard uses the threading.Event version
            # (``_shutting_down_event.is_set()``) instead of the plain
            # boolean — see the rationale in restart_app's inline
            # comment.
            assert stripped.startswith("if self._shutting_down_event.is_set():"), (
                "APP-1: the first executable statement in restart_app "
                "must be 'if self._shutting_down_event.is_set():' "
                "(the re-entry guard, DE-49 — using the threading.Event "
                "version for cross-thread memory ordering). "
                f"Got: {stripped!r}"
            )
            # Log message is on a subsequent line of the guard block
            guard_body = "\n".join(lines[i:])
            assert "duplicate restart_app call" in guard_body, (
                "APP-1: the re-entry guard must log a debug message mentioning the duplicate restart_app call"
            )
            break
        else:
            pytest.fail(
                "APP-1: restart_app has no executable statements after the docstring — the re-entry guard is missing"
            )


# restart_app no longer calls _restore_volume(fade_ms=0) ─────


class TestRestartAppRemovesRedundantRestoreVolume:
    """APP-11: ``restart_app`` used to call ``_restore_volume(fade_ms=0)``
    right after setting ``_shutting_down = True``. This was redundant —
    ``_do_cleanup()`` (called later in restart_app via the shared
    ShutdownController body) already invokes the volume-restore path.

    The redundant call could also race with the in-flight cleanup if
    the volume backend wasn't reentrant (some backends hold a
    subprocess lock during fade). Removing it eliminates the
    double-restore and the potential race.
    """

    def test_restart_app_does_not_call_restore_volume_directly(self, app, monkeypatch):
        """restart_app must NOT call ``_restore_volume`` directly —
        the cleanup path inside _do_cleanup handles it."""
        _stub_restart_environment(app, monkeypatch)

        restore_calls = []
        monkeypatch.setattr(app, "_restore_volume", lambda fade_ms=None: restore_calls.append(fade_ms))

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # _do_cleanup calls app._restore_volume(fade_ms=0) via the
        # shutdown_controller (line ~412), so the spy will catch that
        # legitimate call.  We allow exactly one call (from _do_cleanup);
        # ≥2 would indicate an additional redundant direct call from
        # restart_app itself.
        direct_calls = [c for c in restore_calls if c is not None]
        assert len(direct_calls) <= 1, (
            "APP-11: restart_app must NOT call _restore_volume(fade_ms=0) "
            "directly — _do_cleanup() handles the volume restore via the "
            f"shared ShutdownController body. Got {len(direct_calls)} "
            f"direct calls: {direct_calls}"
        )

    def test_restart_app_source_has_no_direct_restore_volume_call(self):
        """Source-level invariant: ``_restore_volume(fade_ms=0)`` must
        not appear in restart_app's source as an executable call (it's
        redundant — _do_cleanup handles the restore)."""
        import inspect

        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        direct_call_idx = src.find("self._restore_volume(fade_ms=0)")
        if direct_call_idx != -1:
            line_start = src.rfind("\n", 0, direct_call_idx) + 1
            line = src[line_start : src.find("\n", direct_call_idx)]
            stripped = line.strip()
            assert stripped.startswith("#") or stripped.startswith('"""'), (
                "APP-11: restart_app must not call "
                "self._restore_volume(fade_ms=0) directly. The only "
                "allowed occurrence is inside a comment (explaining why "
                f"it was removed). Found executable line: {line!r}"
            )
        # Also confirm the docstring/comment mentions the removal.
        assert "_restore_volume" in src, (
            "APP-11: restart_app source must mention _restore_volume "
            "(in a comment explaining why the redundant call was removed)"
        )


# (): user-data-dir purge helpers ──────────────────────


class TestUserDataPurgeHelpers:
    """S2-CR-70 (SA-6): the ``_paths.user_data_subpaths_for_purge()``
    helper exposes the exhaustive list of subpaths an uninstaller
    should remove when purging user data.

    These tests pin the contract so a future code change that adds a
    new file inside the config dir (e.g. a new SQLite DB, a new
    cache directory) is forced to update the purge list — otherwise
    the uninstaller would silently leak the new file.
    """

    @pytest.fixture(autouse=True)
    def _pin_paths_config_dir(self, tmp_path, monkeypatch):
        """Pin ``_paths._config_dir`` (the imported reference inside
        ``_paths``) to a tmp path so the helpers' delegation chain
        resolves to a deterministic location.

        Mirrors the fixture pattern in ``tests/test_paths.py``: we
        patch ``_paths._config_dir`` (NOT ``config._config_dir``) so
        the helpers' actual delegation chain is exercised — every
        helper should call ``_config_dir()`` (the imported function)
        at least once.
        """
        from voice_typer.server import _paths

        monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)
        self._tmp = tmp_path

    def test_user_data_dir_equals_config_dir(self):
        """``user_data_dir()`` returns the same path as ``config_dir()``.

        They're semantically distinct (the former is the "root of user
        data" the latter is "where the config dir is") but happen to be
        the same path today. The alias exists so uninstallers / factory-
        reset features can call ``user_data_dir()`` for self-documenting
        code.
        """
        from voice_typer.server import _paths

        assert _paths.user_data_dir() == _paths.config_dir()
        assert _paths.user_data_dir() == self._tmp

    def test_hf_cache_dir_under_user_data_dir(self):
        """``hf_cache_dir()`` returns ``<user_data_dir>/huggingface`` —
        the canonical HF model cache location (potentially GBs)."""
        from voice_typer.server import _paths

        assert _paths.hf_cache_dir() == _paths.user_data_dir() / "huggingface"

    def test_user_data_subpaths_for_purge_returns_list(self):
        """The helper returns a list of Path objects (not a generator).

        The uninstaller iterates the list multiple times (once to
        stat-check existence, once to remove) — a generator would be
        exhausted on the first iteration."""
        from pathlib import Path

        from voice_typer.server import _paths

        subpaths = _paths.user_data_subpaths_for_purge()
        assert isinstance(subpaths, list)
        assert all(isinstance(p, Path) for p in subpaths)

    def test_user_data_subpaths_for_purge_includes_hf_cache(self):
        """The HF model cache (GBs) MUST be in the purge list."""
        from voice_typer.server import _paths

        subpaths = _paths.user_data_subpaths_for_purge()
        assert _paths.user_data_dir() / "huggingface" in subpaths, (
            "S2-CR-70: the HF model cache (potentially GBs) MUST be in "
            "the purge list — without it, an uninstall leaves the model "
            "weights behind"
        )

    def test_user_data_subpaths_for_purge_includes_venv(self):
        """The Python venv (hundreds of MB) MUST be in the purge list."""
        from voice_typer.server import _paths

        subpaths = _paths.user_data_subpaths_for_purge()
        assert _paths.user_data_dir() / "venv" in subpaths, (
            "S2-CR-70: the venv (hundreds of MB) MUST be in the purge "
            "list — without it, an uninstall leaves the bundled Python "
            "environment behind"
        )

    def test_user_data_subpaths_for_purge_includes_history_db(self):
        """The SQLite history DB MUST be in the purge list (contains
        transcribed text — privacy-relevant)."""
        from voice_typer.server import _paths

        subpaths = _paths.user_data_subpaths_for_purge()
        base = _paths.user_data_dir()
        # The DB + its WAL/SHM sidecar files.
        assert base / "history.db" in subpaths
        assert base / "history.db-wal" in subpaths
        assert base / "history.db-shm" in subpaths

    def test_user_data_subpaths_for_purge_includes_lockfiles(self):
        """The single-instance lockfile + PID file MUST be in the purge
        list (otherwise a reinstall hits a stale lock)."""
        from voice_typer.server import _paths

        subpaths = _paths.user_data_subpaths_for_purge()
        base = _paths.user_data_dir()
        assert base / "backend.lock" in subpaths
        assert base / "backend.pid" in subpaths

    def test_user_data_subpaths_for_purge_all_under_user_data_dir(self):
        """Every subpath MUST be under ``user_data_dir()`` — otherwise
        the purge would delete unrelated user files."""
        from voice_typer.server import _paths

        base = _paths.user_data_dir()
        subpaths = _paths.user_data_subpaths_for_purge()
        for sub in subpaths:
            # ``is_relative_to`` is Python 3.9+; the project floor is
            # 3.10 per pyproject.toml so this is always available.
            assert sub.is_relative_to(base), (
                "S2-CR-70: every purge subpath MUST be under "
                f"user_data_dir() ({base}); got {sub} which is NOT — "
                "this would let the purge delete unrelated user files"
            )

    def test_user_data_subpaths_for_purge_no_duplicates(self):
        """No duplicate subpaths (a duplicate would be a no-op on
        removal but signals a copy-paste error)."""
        from voice_typer.server import _paths

        subpaths = _paths.user_data_subpaths_for_purge()
        unique = set(subpaths)
        assert len(subpaths) == len(unique), (
            f"S2-CR-70: duplicate subpaths in purge list — got {len(subpaths)} entries but only {len(unique)} unique"
        )


# real-collaborator integration tests ─────────────────────────
#
# The tests above use MagicMock collaborators (history_db, recorder,
# crash_recovery). They verify CALL ROUTING only — they do NOT verify
# that a real history_db.flush() actually drains pending SQLite writes,
# or that recorder.stop() actually closes a real PortAudio stream. A
# regression where flush() becomes fire-and-forget would still pass
# those tests but silently lose data in production.
#
# These integration tests use a REAL HistoryDB (tmp_path SQLite file)
# and a fake-but-real PortAudio stream stub to verify the side effects
# of _do_cleanup() / restart_app() actually land.


class TestRealHistoryDBFlushDrainsQueue:
    """GT-38: _do_cleanup() must call a real history_db.flush() that
    actually drains the writer-thread queue to the SQLite file on disk.

    A regression where flush() is changed to a fire-and-forget
    non-blocking call would still pass the MagicMock-based tests above
    (they only assert ``flush.assert_called_once()``) but would silently
    lose pending writes on restart. This test catches that regression
    by inspecting the actual SQLite file on disk.
    """

    def test_do_cleanup_drains_pending_writes_to_disk(self, app, tmp_config_dir, monkeypatch):
        """A real HistoryDB with a populated writer queue, when passed
        through _do_cleanup(), must end up with all rows persisted to
        the SQLite file on disk — proving flush() is blocking, not
        fire-and-forget."""
        from voice_typer.server.history_db import HistoryDB

        # Use a REAL HistoryDB pointed at the tmp_config_dir's history.db
        real_db = HistoryDB(db_path=tmp_config_dir / "history.db")
        try:
            # Enqueue 5 fire-and-forget writes. They sit in the writer
            # thread's queue (or are being drained async) — at this
            # point we cannot guarantee they're on disk yet.
            texts = [f"pending write {i}" for i in range(5)]
            for text in texts:
                row_id = real_db.add_transcription(text, duration=1.0)
                assert row_id > 0, "add_transcription should return placeholder > 0"

            # Swap the app's mock history_db for the real one. Other
            # collaborators stay mocked (recorder, hotkeys, tray) so we
            # don't need real audio hardware.
            _stub_restart_environment(app, monkeypatch)
            app.history_db = real_db

            # Run the shared cleanup path. This MUST call real_db.flush()
            # which blocks until the writer thread has drained the queue.
            app._do_cleanup()

            # Open a FRESH read connection (not the writer thread's
            # connection) and verify all 5 rows landed on disk.
            import sqlite3

            with sqlite3.connect(str(tmp_config_dir / "history.db")) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT text FROM transcriptions ORDER BY id").fetchall()
                on_disk_texts = [r["text"] for r in rows]

            assert on_disk_texts == texts, (
                f"GT-38: _do_cleanup() must drain pending HistoryDB writes "
                f"to disk. Expected {texts}, got {on_disk_texts}. A "
                f"fire-and-forget flush() regression would leave this empty "
                f"or partial."
            )
        finally:
            # close() is idempotent; safe to call after _do_cleanup.
            real_db.close()

    def test_real_flush_blocks_until_queue_drained(self, tmp_config_dir, monkeypatch):
        """GT-38: A direct call to HistoryDB.flush() must BLOCK until
        all queued writes are durable on disk. This is the contract
        _do_cleanup() relies on — if flush() becomes non-blocking, the
        restart path silently loses data.

        We verify this by enqueuing N writes, calling flush(), then
        immediately reading from a fresh connection and asserting all
        N rows are visible.
        """
        from voice_typer.server.history_db import HistoryDB

        real_db = HistoryDB(db_path=tmp_config_dir / "history.db")
        try:
            texts = [f"row {i}" for i in range(20)]
            for t in texts:
                real_db.add_transcription(t)

            # flush() must block until the writer drains the queue.
            real_db.flush()

            import sqlite3

            with sqlite3.connect(str(tmp_config_dir / "history.db")) as conn:
                count = conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0]

            assert count == 20, (
                f"GT-38: flush() must block until all queued writes are "
                f"durable. Expected 20 rows on disk, got {count}. A "
                f"non-blocking flush() regression would return before the "
                f"writer thread finished draining."
            )
        finally:
            real_db.close()


class TestRealRecorderStopClosesStream:
    """GT-38: _do_cleanup() must call recorder.stop() which actually
    closes the underlying PortAudio stream. The MagicMock-based tests
    only verify ``recorder.stop.assert_called_once()`` — they do NOT
    verify that a real recorder.stop() actually invokes stream.stop()
    + stream.close().

    We use a fake stream object that records stop()/close() calls
    (standing in for a real sd.InputStream) so we can verify the
    teardown sequence is exercised end-to-end.
    """

    def test_do_cleanup_closes_real_stream(self, app, tmp_config_dir, monkeypatch):
        """When the app's recorder has a real (fake-but-not-MagicMock)
        PortAudio stream, _do_cleanup() must drive stop() + close() on
        it, leaving _stream = None."""

        # Reuse the app's existing recorder instance (constructed in
        # __init__ with mocked sounddevice via mock_heavy_imports).
        recorder = app.recorder

        # Install a fake-but-real stream object that records teardown
        # calls. This is NOT a MagicMock — we want to verify the
        # production stop()/close() sequence is actually invoked.
        class _FakeStream:
            def __init__(self):
                self.stop_calls = 0
                self.close_calls = 0

            def stop(self):
                self.stop_calls += 1

            def close(self):
                self.close_calls += 1

        fake_stream = _FakeStream()
        recorder._stream = fake_stream
        # Pretend we're recording so _do_cleanup takes the stop() path.
        # ``recording`` is a read-only property backed by an Event.
        recorder._recording_event.set()
        # Mark not-in-callback so stop()'s poll loop exits immediately.
        recorder._is_in_audio_callback.clear()

        # Stub the rest of the cleanup collaborators (history_db etc.)
        # so _do_cleanup only needs to succeed on the recorder path.
        _stub_restart_environment(app, monkeypatch)
        # Restore the real recorder (the stub overwrote it with a mock).
        app.recorder = recorder

        # Run cleanup. Must invoke recorder.stop() which drives
        # stream.stop() + stream.close() + self._stream = None.
        app._do_cleanup()

        assert fake_stream.stop_calls == 1, "GT-38: recorder.stop() must call stream.stop() exactly once"
        assert fake_stream.close_calls == 1, "GT-38: recorder.stop() must call stream.close() exactly once"
        assert recorder._stream is None, "GT-38: recorder.stop() must set self._stream = None after close"
