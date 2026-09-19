"""regression tests for shared cleanup between quit() and restart_app()."""

import contextlib
from unittest.mock import MagicMock

import pytest

from tests.fixtures.history_test_helpers import history_plaintext_mode  # noqa: F401


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies for cleanup tests."""
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    yield instance
    # Close the real HistoryDB writer thread + CrashRecovery save thread.
    with contextlib.suppress(Exception):
        instance.history_db.close()
    with contextlib.suppress(Exception):
        instance._crash_recovery.shutdown()


def _stub_restart_environment(app, monkeypatch):
    """Stub out the side-effects of restart_app() / quit() so they can"""
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    # Skip the 300ms pre-exit sleep in restart_app.
    monkeypatch.setattr("time.sleep", lambda s: None)
    # Mock sys.exit to raise SystemExit (which we catch in tests) rather
    monkeypatch.setattr(
        "sys.exit",
        lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
    )
    # Belt-and-suspenders: don't let os._exit kill the pytest process
    monkeypatch.setattr("os._exit", lambda code: None)

    # Mock the cleanup collaborators so we can assert they were called.
    app.recorder = MagicMock()
    app.recorder.recording = True
    app.recording._transcription_thread = None
    app.hotkeys._hotkey_backend = MagicMock()
    app.hotkeys._esc_backend = MagicMock()
    app.hotkeys._repaste_backend = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app.tray = MagicMock()
    app.history_db = MagicMock()
    app._crash_recovery = MagicMock()


class TestRestartAppSharedCleanup:
    """restart_app() must run the same critical cleanup as quit()."""

    def test_restart_app_flushes_history_db(self, app, monkeypatch):
        """history_db.flush() must be called on restart so pending"""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.history_db.flush.assert_called_once()

    def test_restart_app_stops_recorder(self, app, monkeypatch):
        """restart so the PortAudio stream is closed before the new"""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert app.recorder.stop.called or app.recorder.discard.called, (
            "restart_app must call recorder.stop() or recorder.discard() to close the PortAudio stream before exiting"
        )

    def test_restart_app_clears_backend_pid_file(self, app, monkeypatch):
        """PID file doesn't claim a stale PID when the new instance starts."""
        _stub_restart_environment(app, monkeypatch)
        clear_calls = []
        # The pid-file teardown resolves through the owning
        monkeypatch.setattr(
            "voice_typer.server.backend_pid._clear_backend_pid_file",
            lambda: clear_calls.append(True),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert clear_calls == [True], (
            "restart_app must call _clear_backend_pid_file() so the next "
            "launch isn't falsely blocked by a stale PID file"
        )

    def test_restart_app_flushes_crash_recovery(self, app, monkeypatch):
        """process exits."""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app._crash_recovery.flush.assert_called_once()
        app._crash_recovery.shutdown.assert_called_once()

    def test_restart_app_shuts_down_mic_watcher(self, app, monkeypatch):
        """recorder.shutdown_mic_watcher() must be called on restart"""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.recorder.shutdown_mic_watcher.assert_called_once()

    def test_restart_app_stops_all_three_hotkey_backends(self, app, monkeypatch):
        """Sanity: restart_app must still stop _hotkey_backend,"""
        _stub_restart_environment(app, monkeypatch)
        hotkey_backend = app.hotkeys._hotkey_backend
        esc_backend = app.hotkeys._esc_backend
        repaste_backend = app.hotkeys._repaste_backend

        with contextlib.suppress(SystemExit):
            app.restart_app()

        hotkey_backend.stop.assert_called_once()
        esc_backend.stop.assert_called_once()
        repaste_backend.stop.assert_called_once()

    def test_restart_app_calls_tray_stop(self, app, monkeypatch):
        """Sanity: restart_app must still call tray.stop() (to break"""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.tray.stop.assert_called_once()

    def test_restart_app_sets_shutting_down_before_cleanup(self, app, monkeypatch):
        """restart_app must set _shutting_down=True BEFORE calling"""
        _stub_restart_environment(app, monkeypatch)

        # Capture the value of _shutting_down at the moment _do_cleanup
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


class TestDoCleanupIdempotency:
    """_do_cleanup() must be safe to call multiple times."""

    def test_do_cleanup_twice_does_not_crash(self, app, monkeypatch):
        """Calling _do_cleanup() twice (e.g. once from quit() and once"""
        _stub_restart_environment(app, monkeypatch)
        hotkey_backend = app.hotkeys._hotkey_backend

        # First call runs the full cleanup body.
        app._do_cleanup()
        # Second call must be a no-op (guarded by _cleanup_done flag).
        app._do_cleanup()

        # Each collaborator must have been called exactly once,
        app.recorder.stop.assert_called_once()
        app.recorder.shutdown_mic_watcher.assert_called_once()
        app.history_db.flush.assert_called_once()
        app._crash_recovery.flush.assert_called_once()
        app._crash_recovery.shutdown.assert_called_once()
        hotkey_backend.stop.assert_called_once()
        app.tray.stop.assert_called_once()

    def test_do_cleanup_clears_session_marker(self, app, monkeypatch, tmp_config_dir):
        """SESSION-STATE: the shared cleanup body removes the session-active"""
        from voice_typer.server import session_state

        _stub_restart_environment(app, monkeypatch)
        # Simulate a session that began (marker present).
        session_state.mark_session_active(tmp_config_dir)

        app._do_cleanup()

        assert not (tmp_config_dir / session_state.SESSION_MARKER_FILENAME).exists(), (
            "SESSION-STATE: clean shutdown must clear the session marker so the next launch is not reported as a crash"
        )

    def test_do_cleanup_idempotent_when_recorder_stop_raises(self, app, monkeypatch):
        """Idempotency must hold even when an inner operation raises."""
        _stub_restart_environment(app, monkeypatch)
        # Make recorder.stop() raise on every call.
        app.recorder.stop.side_effect = RuntimeError("PortAudio already closed")

        app.recorder.discard.side_effect = RuntimeError("already discarded")
        # Must not propagate.
        app._do_cleanup()

        # Second call must be a no-op, recorder.stop/discard are NOT
        app._do_cleanup()

        app.recorder.stop.assert_called_once()
        app.recorder.discard.assert_called_once()
        app.history_db.flush.assert_called_once()


class TestAtexitCleanupSafetyNet:
    """safety net runs the SAME audited path as quit()/restart_app()."""

    def test_atexit_cleanup_after_quit_is_noop(self, app, monkeypatch):
        """_atexit_cleanup() must early-return when _shutting_down is"""
        _stub_restart_environment(app, monkeypatch)

        # Simulate quit() having run: set the flag, run cleanup once.
        app._shutting_down = True
        app._do_cleanup()

        # Now atexit fires, it should early-return without calling
        app._atexit_cleanup()

        app.recorder.stop.assert_called_once()
        app.history_db.flush.assert_called_once()

    def test_atexit_cleanup_runs_when_not_shutting_down(self, app, monkeypatch):
        """_atexit_cleanup() must run _do_cleanup() when the process"""
        _stub_restart_environment(app, monkeypatch)
        hotkey_backend = app.hotkeys._hotkey_backend

        # _shutting_down stays False, process was killed externally.
        assert app._shutting_down is False

        app._atexit_cleanup()

        # The full _do_cleanup() body ran via _atexit_cleanup().
        app.history_db.flush.assert_called_once()
        app.recorder.stop.assert_called_once()
        app.recorder.shutdown_mic_watcher.assert_called_once()
        app._crash_recovery.flush.assert_called_once()
        app._crash_recovery.shutdown.assert_called_once()
        hotkey_backend.stop.assert_called_once()
        app.tray.stop.assert_called_once()

    def test_atexit_cleanup_never_raises(self, app, monkeypatch):
        """_atexit_cleanup() must NEVER raise, even if _do_cleanup()"""
        _stub_restart_environment(app, monkeypatch)
        # Force _do_cleanup to raise.
        monkeypatch.setattr(app, "_do_cleanup", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        # Must not raise.
        app._atexit_cleanup()


class TestQuitAppUsesSharedCleanup:
    """quit() must use _do_cleanup() (the shared path) and still"""

    def test_quit_flushes_history_db(self, app, monkeypatch):
        """Sanity: quit() (which already did this inline) must"""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.quit()

        app.history_db.flush.assert_called_once()

    def test_quit_clears_backend_pid_file(self, app, monkeypatch):
        """Sanity: quit() must still clear the backend PID file after"""
        _stub_restart_environment(app, monkeypatch)
        clear_calls = []
        # The pid-file teardown resolves through the owning
        monkeypatch.setattr(
            "voice_typer.server.backend_pid._clear_backend_pid_file",
            lambda: clear_calls.append(True),
        )

        with contextlib.suppress(SystemExit):
            app.quit()

        assert clear_calls == [True]

    def test_quit_stops_recorder(self, app, monkeypatch):
        """Sanity: quit() must still call recorder.stop() (or"""
        _stub_restart_environment(app, monkeypatch)

        with contextlib.suppress(SystemExit):
            app.quit()

        assert app.recorder.stop.called or app.recorder.discard.called

    def test_quit_calls_do_cleanup(self, app, monkeypatch):
        """Sanity: quit() must delegate to _do_cleanup() (rather than"""
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


class TestRelaunchAckEventDriven:
    """predecessor (bounded by a 2s timeout) instead of a fixed ``time.sleep(0.3)``"""

    def test_handle_relaunch_ack_sets_event(self):
        """The ``relaunch_ack`` IPC handler must set the server's"""
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
        """When predecessor acks (event already set), restart_app must NOT call"""
        _stub_restart_environment(app, monkeypatch)

        # Attach a fake IPC server exposing a real ack event, already set.
        import threading

        class _FakeServer:
            def __init__(self):
                self._relaunch_ack_event = threading.Event()

        fake = _FakeServer()
        fake._relaunch_ack_event.set()  # predecessor already acked
        app._ipc_server = fake

        sleep_calls = []
        monkeypatch.setattr(
            "time.sleep",
            lambda s: sleep_calls.append((s, threading.current_thread().name)),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # PERF-005 scope: ``restart_app`` runs on the main thread, so
        main_sleeps = [s for s, _t in sleep_calls if _t == threading.main_thread().name]
        assert main_sleeps == [], (
            "restart_app must not call the fixed 300ms sleep when the relaunch_ack "
            f"event is already set (PERF-005). Main-thread sleeps: {main_sleeps}"
        )

    def test_restart_app_falls_back_to_sleep_without_server(self, app, monkeypatch):
        """
        TY-13: when there is no IPC server (event unavailable),
        ``restart_app`` must NOT sleep at all, the previous 300ms
        """
        _stub_restart_environment(app, monkeypatch)
        app._ipc_server = None

        import threading

        sleep_calls = []
        monkeypatch.setattr(
            "time.sleep",
            lambda s: sleep_calls.append((s, threading.current_thread().name)),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # Same main-thread scoping as the PERF-005 test: only
        main_sleeps = [s for s, _t in sleep_calls if _t == threading.main_thread().name]
        assert main_sleeps == [], (
            f"TY-13: restart_app must NOT sleep when no IPC server is "
            f"available, the 300ms fallback was removed (no one is "
            f"listening for the relaunch_ack). Main-thread sleeps: {main_sleeps}"
        )


class TestRestartAppReentryGuard:
    """APP-1: ``restart_app`` must short-circuit when"""

    def test_restart_app_no_op_when_already_shutting_down(self, app, monkeypatch):
        """When _shutting_down is True, restart_app must return"""
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
        """Sanity: when _shutting_down is False, restart_app must run"""
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
        """Source-level invariant: the re-entry guard"""
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
            assert stripped.startswith("if self._shutting_down_event.is_set():"), (
                "APP-1: the first executable statement in restart_app "
                "must be 'if self._shutting_down_event.is_set():' "
                "(the re-entry guard, DE-49, using the threading.Event "
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
                "APP-1: restart_app has no executable statements after the docstring, the re-entry guard is missing"
            )


class TestRestartAppRemovesRedundantRestoreVolume:
    """APP-11: ``restart_app`` used to call ``_restore_volume(fade_ms=0)``"""

    def test_restart_app_does_not_call_restore_volume_directly(self, app, monkeypatch):
        """restart_app must NOT call ``_restore_volume`` directly —"""
        _stub_restart_environment(app, monkeypatch)

        restore_calls = []
        monkeypatch.setattr(app, "_restore_volume", lambda fade_ms=None: restore_calls.append(fade_ms))

        with contextlib.suppress(SystemExit):
            app.restart_app()

        direct_calls = [c for c in restore_calls if c is not None]
        assert len(direct_calls) <= 1, (
            "APP-11: restart_app must NOT call _restore_volume(fade_ms=0) "
            "directly, _do_cleanup() handles the volume restore via the "
            f"shared ShutdownController body. Got {len(direct_calls)} "
            f"direct calls: {direct_calls}"
        )

    def test_restart_app_source_has_no_direct_restore_volume_call(self):
        """Source-level invariant: ``_restore_volume(fade_ms=0)`` must"""
        import inspect

        from voice_typer.server.app_lifecycle import LifecycleController

        src = inspect.getsource(LifecycleController.restart_app)
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
    """The canonical user-data purge inventory"""

    @pytest.fixture(autouse=True)
    def _pin_paths_config_dir(self, tmp_path, monkeypatch):
        """Pin ``_paths._config_dir`` (the imported reference inside"""
        from voice_typer.server import _paths

        monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)
        self._tmp = tmp_path

    def test_user_data_dir_equals_config_dir(self):
        """``user_data_dir()`` returns the same path as ``config_dir()``."""
        from voice_typer.server import _paths

        assert _paths.user_data_dir() == _paths.config_dir()
        assert _paths.user_data_dir() == self._tmp

    def test_hf_cache_dir_under_user_data_dir(self):
        """``hf_cache_dir()`` returns ``<user_data_dir>/huggingface`` —"""
        from voice_typer.server import _paths

        assert _paths.hf_cache_dir() == _paths.user_data_dir() / "huggingface"

    def test_purge_inventory_has_no_duplicates(self):
        """No duplicate filenames (a duplicate would be a no-op on"""
        from voice_typer.server._user_data_files import _USER_DATA_FILES

        unique = set(_USER_DATA_FILES)
        assert len(_USER_DATA_FILES) == len(unique), (
            f"duplicate filenames in the purge inventory, "
            f"got {len(_USER_DATA_FILES)} entries but only {len(unique)} unique"
        )

    def test_purge_inventory_uses_real_filenames(self):
        """registry drifted to fictional names (``crash_recovery.json`` /"""
        from voice_typer.server._user_data_files import _USER_DATA_FILES

        fictional = {"crash_recovery.json", "onboarding.marker"}
        leaked = fictional & set(_USER_DATA_FILES)
        assert not leaked, (
            f"the purge inventory regressed to never-matching filenames: {leaked}, "
            "use the canonical *_FILENAME constants from the owning modules"
        )

    def test_purge_inventory_covers_recovery_and_onboarding(self):
        """The recovery snapshot + onboarding state MUST be in the"""
        from voice_typer.server._user_data_files import _USER_DATA_FILES

        assert "recovery.json" in _USER_DATA_FILES
        assert ".onboarding_status.json" in _USER_DATA_FILES


class TestRealHistoryDBFlushDrainsQueue:
    """actually drains the writer-thread queue to the SQLite file on disk."""

    def test_do_cleanup_drains_pending_writes_to_disk(self, app, tmp_config_dir, monkeypatch):
        """A real HistoryDB with a populated writer queue, when passed"""
        from voice_typer.server.history_db import HistoryDB

        # Use a REAL HistoryDB pointed at the tmp_config_dir's history.db
        real_db = HistoryDB(db_path=tmp_config_dir / "history.db")
        try:
            # Enqueue 5 fire-and-forget writes. They sit in the writer
            texts = [f"pending write {i}" for i in range(5)]
            for text in texts:
                row_id = real_db.add_transcription(text, duration=1.0)
                assert row_id > 0, "add_transcription should return placeholder > 0"

            # Swap the app's mock history_db for the real one. Other
            _stub_restart_environment(app, monkeypatch)
            app.history_db = real_db

            # Run the shared cleanup path. This MUST call real_db.flush()
            app._do_cleanup()

            # Open a FRESH read connection (not the writer thread's
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
            real_db.close()

    def test_real_flush_blocks_until_queue_drained(self, tmp_config_dir, monkeypatch):
        """A direct call to HistoryDB.flush() must BLOCK until"""
        from voice_typer.server.history_db import HistoryDB

        real_db = HistoryDB(db_path=tmp_config_dir / "history.db")
        try:
            texts = [f"row {i}" for i in range(20)]
            for t in texts:
                real_db.add_transcription(t)

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
    """_do_cleanup() must call recorder.stop() which actually"""

    def test_do_cleanup_closes_real_stream(self, app, tmp_config_dir, monkeypatch):
        """When the app's recorder has a real (fake-but-not-MagicMock)"""

        recorder = app.recorder

        # Install a fake-but-real stream object that records teardown
        class _FakeStream:
            def __init__(self):
                self.stop_calls = 0
                self.close_calls = 0

            def stop(self):
                self.stop_calls += 1

            def close(self):
                self.close_calls += 1

        fake_stream = _FakeStream()
        recorder._stream_lifecycle._stream = fake_stream
        # Pretend we're recording so _do_cleanup takes the stop() path.
        recorder._recording_event.set()
        # Mark not-in-callback so stop()'s poll loop exits immediately.
        recorder._is_in_audio_callback.clear()

        # Stub the rest of the cleanup collaborators (history_db etc.)
        _stub_restart_environment(app, monkeypatch)
        # Restore the real recorder (the stub overwrote it with a mock).
        app.recorder = recorder

        # Run cleanup. Must invoke recorder.stop() which drives
        app._do_cleanup()

        assert fake_stream.stop_calls == 1, "GT-38: recorder.stop() must call stream.stop() exactly once"
        assert fake_stream.close_calls == 1, "GT-38: recorder.stop() must call stream.close() exactly once"
        assert recorder._stream_lifecycle._stream is None, (
            "GT-38: recorder.stop() must set self._stream_lifecycle._stream = None after close"
        )
