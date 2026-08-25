"""regression tests for ``restart_app()`` thread-aware exit.

``restart_app()`` is invoked from the tray menu callback, which pystray
dispatches on its own worker thread (NOT the main thread). The tray's
``wrap_callback`` (``tray_menu.py``) catches ``SystemExit`` and
suppresses it so pystray doesn't print a noisy traceback. An
unconditional ``sys.exit(0)`` at the end of ``restart_app()`` is
therefore silently swallowed when called from the tray — the process
only eventually exits because ``_do_cleanup()`` called ``tray.stop()``
which breaks the pystray event loop on its next iteration (up to ~1s
later, during which the old process holds the single-instance mutex /
IPC port and the new instance may fail to bind).

The fix mirrors ``quit()``'s pattern (``shutdown_controller.py:464,497``):
only call ``sys.exit(0)`` when on the main thread; otherwise rely on
``tray.stop()`` (already called inside ``_do_cleanup()``) to break the
pystray loop so ``app.start()`` returns and ``ipc_server.main()`` falls
through to process exit.

These tests verify:
  1. When called from the MAIN thread, ``sys.exit(0)`` IS called.
  2. When called from a NON-MAIN thread, ``sys.exit(0)`` is NOT called
     (no ``SystemExit`` is raised into the calling thread).
  3. ``_do_cleanup()`` is invoked in BOTH cases — the cleanup must
     always run regardless of which thread triggered the restart,
     because that's what calls ``tray.stop()`` to break the pystray
     loop on the non-main-thread path.
"""

import contextlib
import threading
from unittest.mock import MagicMock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────
#
# The autouse ``mock_heavy_imports`` fixture from tests/conftest.py
# applies, mocking sounddevice / faster_whisper / pynput / pystray / PIL
# / pyperclip so the tests run headless. ``tmp_config_dir`` is also
# provided by tests/conftest.py — it patches both
# ``config._config_dir`` and ``app._config_dir`` so PID file writes /
# DuckCrashRecovery file writes land in ``tmp_path`` instead of the real
# ``~/.local/share/voice-typer/`` directory.


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies for restart tests.

    Teardown: ``VoiceTyperApp.__init__`` constructs a real ``HistoryDB()``
    (which spawns a writer thread) and a real ``CrashRecovery`` (which
    spawns a save thread). Both are closed in teardown so the daemon
    threads don't leak across tests. Both ``close()`` and ``shutdown()``
    are idempotent. By teardown time ``monkeypatch`` has already undone
    any per-test ``setattr`` overrides installed by
    ``_stub_restart_environment`` (which swaps ``app.history_db`` /
    ``app._crash_recovery`` for MagicMocks), so the attributes here are
    the real instances constructed in ``__init__``.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    yield instance
    # Close the real HistoryDB writer thread + CrashRecovery save thread.
    # ``contextlib.suppress`` defends against double-close after a test
    # already called ``_do_cleanup()`` (which itself flushes + closes).
    with contextlib.suppress(Exception):
        instance.history_db.close()
    with contextlib.suppress(Exception):
        instance._crash_recovery.shutdown()


def _stub_restart_environment(app, monkeypatch, *, spy_sys_exit):
    """Stub out the side-effects of restart_app() so it can run in tests
    without spawning subprocesses, sleeping, or actually exiting the
    pytest process.

    ``spy_sys_exit`` is a list that the patched ``sys.exit`` will append
    each code to, so tests can assert whether ``sys.exit(0)`` was called.
    """
    # Stub IPC push so restart_app doesn't try to write to a real TCP
    # socket.
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    # Skip the 300ms pre-exit sleep in restart_app.
    monkeypatch.setattr("time.sleep", lambda s: None)

    # Mock sys.exit: record the call AND raise SystemExit so that
    # behaviour mirrors the real sys.exit (which raises SystemExit). The
    # SystemExit is what the production code would propagate — and what
    # wrap_callback (tray_menu.py) catches in the real tray path.
    def _fake_sys_exit(code=0):
        spy_sys_exit.append(code)
        raise SystemExit(code)

    monkeypatch.setattr("sys.exit", _fake_sys_exit)

    # Belt-and-suspenders: don't let os._exit kill the pytest process
    # if a future regression reintroduces it.
    monkeypatch.setattr("os._exit", lambda code: None)

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


# ── Tests ───────────────────────────────────────────────────────────────


class TestRestartAppThreadAwareExit:
    """``restart_app()`` must only ``sys.exit(0)`` on the main
    thread; on a non-main thread it must rely on ``tray.stop()``
    (called inside ``_do_cleanup()``) to break the pystray loop."""

    def test_restart_app_calls_sys_exit_on_main_thread(self, app, monkeypatch):
        """When called from the main thread, ``restart_app()`` must call
        ``sys.exit(0)`` so the process actually exits (the tray callback
        wrapper isn't in the call stack in this case — main-thread
        callers include ``ipc_server`` restart routes and programmatic
        restarts from the main loop)."""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        # Sanity: we're running on the main thread in pytest.
        assert threading.current_thread() is threading.main_thread()

        # Spy on _do_cleanup to verify it ran (via the delegate on the
        # app instance — see shutdown_controller.py:489-495 note about
        # why tests must patch app._do_cleanup, not controller._do_cleanup).
        cleanup_calls = []
        monkeypatch.setattr(
            app,
            "_do_cleanup",
            lambda: cleanup_calls.append(1),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert spy_sys_exit == [0], (
            f"restart_app must call sys.exit(0) when invoked on the main thread; got sys.exit calls: {spy_sys_exit}"
        )
        assert cleanup_calls == [1], (
            "restart_app must run _do_cleanup before exiting, even on "
            "the main thread (the main-thread exit path still needs to "
            "flush history_db / stop PortAudio / release the mutex)."
        )

    def test_restart_app_does_not_call_sys_exit_off_main_thread(self, app, monkeypatch):
        """When called from a non-main thread (the real-world tray
        case — pystray dispatches menu callbacks on its own worker
        thread), ``restart_app()`` must NOT call ``sys.exit(0)``.

        CPython's ``sys.exit()`` raises ``SystemExit`` in the CALLING
        thread only — on a non-main thread the process does NOT exit.
        The tray's ``wrap_callback`` (``tray_menu.py:77-93``) catches
        ``SystemExit`` and suppresses it, so an unconditional
        ``sys.exit(0)`` here would be silently swallowed and the
        process would only exit ~1s later when pystray polls and
        notices ``tray.stop()`` was called.

        The fix mirrors ``quit()``'s pattern: on a non-main thread,
        rely on ``tray.stop()`` (already called inside ``_do_cleanup``)
        to break the pystray loop so ``app.start()`` returns and
        ``ipc_server.main()`` falls through to process exit.
        """
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        # Spy on _do_cleanup to verify it ran (this is the CRITICAL
        # assertion for the non-main-thread path — _do_cleanup() is
        # what calls tray.stop() at shutdown_controller.py:348, which
        # is what actually breaks the pystray loop).
        cleanup_calls = []
        monkeypatch.setattr(
            app,
            "_do_cleanup",
            lambda: cleanup_calls.append(1),
        )

        # Run restart_app() on a worker thread and capture any
        # SystemExit that escapes (there shouldn't be one). The
        # contextlib.suppress inside _run_spy is belt-and-suspenders:
        # the fix means sys.exit(0) is never reached on this path, so
        # no SystemExit should be raised. If a future regression
        # reintroduces an unconditional sys.exit(0), the SystemExit
        # would be raised in THIS worker thread, caught here, and
        # recorded — the assertion below would then fail.
        errors: list = []
        worker = threading.Thread(target=lambda: _run_spy(app, errors))
        worker.start()
        worker.join(timeout=5.0)
        assert not worker.is_alive(), "restart_app worker thread did not exit"

        # No SystemExit should have escaped into the worker thread.
        assert errors == [], (
            "restart_app must NOT raise SystemExit on a non-main thread "
            "(tray.wrap_callback would swallow it, but the process "
            f"would still linger ~1s); got errors: {errors}"
        )

        # sys.exit(0) must NOT have been called.
        assert spy_sys_exit == [], (
            f"restart_app must NOT call sys.exit(0) on a non-main thread; got sys.exit calls: {spy_sys_exit}"
        )

        # CRITICAL: _do_cleanup() MUST have been called even on the
        # non-main-thread path — that's what calls tray.stop() at
        # shutdown_controller.py:348, which is what actually breaks
        # the pystray loop so the process can exit.
        assert cleanup_calls == [1], (
            "restart_app must run _do_cleanup() on the non-main-thread "
            "path too — tray.stop() (called inside _do_cleanup) is what "
            "breaks the pystray event loop so app.start() can return. "
            f"cleanup_calls={cleanup_calls}"
        )

        # And tray.stop() must have been called (it's invoked inside
        # _do_cleanup, but we stubbed _do_cleanup, so verify our stub
        # ran and trust _do_cleanup's contract — separately asserted in
        # test_app_cleanup.py::TestDoCleanup*).
        # NOTE: because we stubbed _do_cleanup, app.tray.stop is NOT
        # called in this test. That's intentional — the goal here is to
        # verify restart_app() delegates to _do_cleanup, not to re-test
        # _do_cleanup's body. tray.stop() coverage lives in
        # tests/test_app.py::TestRestartAppCleanShutdown::test_restart_app_calls_tray_stop.

    def test_restart_app_off_main_thread_preserves_cleanup_and_tray_stop(self, app, monkeypatch):
        """End-to-end (no _do_cleanup stub): on a non-main thread,
        ``restart_app()`` must still invoke the real ``_do_cleanup()``
        which in turn calls ``tray.stop()``. This is the actual
        mechanism by which the process exits on the tray-restart path.

        Without this assertion, a regression that skips _do_cleanup()
        on the non-main-thread path would silently break the restart
        exit mechanism (tray.stop() never runs → pystray loop never
        breaks → process hangs).
        """
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        errors: list = []
        worker = threading.Thread(
            target=lambda: _run_spy(app, errors),
        )
        worker.start()
        worker.join(timeout=5.0)
        assert not worker.is_alive(), "restart_app worker thread did not exit"
        assert errors == [], f"restart_app must NOT raise SystemExit on a non-main thread; got errors: {errors}"
        assert spy_sys_exit == [], (
            f"restart_app must NOT call sys.exit(0) on a non-main thread; got sys.exit calls: {spy_sys_exit}"
        )

        # tray.stop() is called inside _do_cleanup at
        # shutdown_controller.py:348 — verify it ran.
        app.tray.stop.assert_called_once()


class TestRestartAppInPlaceStandalone:
    """``restart_app()`` in standalone/terminal mode must signal an
    IN-PLACE restart: the process stays alive and the entrypoint loop
    re-initializes the app in the same terminal.  This is the user's
    expectation when running ``voice-typer`` from a terminal: Restart
    tears the app down but does NOT exit the process (no home-prompt
    return), and a fresh instance starts in the same window.
    """

    def test_standalone_restart_sets_in_place_flag(self, app, monkeypatch):
        """In standalone mode (``_electron_pid`` set — Python spawned
        Electron as a child), ``restart_app()`` must set
        ``app._in_place_restart = True`` so the entrypoint loop knows to
        re-run the startup sequence instead of exiting."""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        # Standalone mode: Python spawned Electron as a child.
        app._electron_pid = 12345
        monkeypatch.setattr(app, "_do_cleanup", lambda: None)

        app.restart_app()

        assert app._in_place_restart is True, (
            "standalone restart must set _in_place_restart=True so the "
            "entrypoint loop re-initializes instead of exiting"
        )
        assert app._is_restarting is True, "standalone restart must still mark _is_restarting=True for cleanup"
        # The in-place path must NOT call sys.exit(0) — the process stays
        # alive to re-initialize.
        assert spy_sys_exit == [], (
            f"standalone restart must NOT call sys.exit(0) (in-place restart keeps "
            f"the process alive); got sys.exit calls: {spy_sys_exit}"
        )

    def test_non_standalone_restart_does_not_set_in_place_flag(self, app, monkeypatch):
        """In dev mode (no ``_electron_pid`` — Electron spawned Python),
        ``restart_app()`` must NOT set ``_in_place_restart``: the old
        out-of-process relaunch (sys.exit + Electron respawn) is the
        correct behaviour there."""
        spy_sys_exit = []
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=spy_sys_exit)

        app._electron_pid = None  # dev mode — Electron is the parent
        monkeypatch.setattr(app, "_do_cleanup", lambda: None)

        # The out-of-process path calls sys.exit(0) on the main thread.
        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert app._in_place_restart is False, "non-standalone restart must NOT set _in_place_restart"
        assert spy_sys_exit == [0], (
            "non-standalone restart must still call sys.exit(0) on the main thread "
            f"(out-of-process relaunch); got sys.exit calls: {spy_sys_exit}"
        )

    def test_in_place_restart_runs_full_cleanup(self, app, monkeypatch):
        """The in-place path must still run the full ``_do_cleanup()``
        (which kills the Electron child, stops hotkeys/tray, flushes DBs,
        releases the mutex) so the re-initialized app starts clean."""
        _stub_restart_environment(app, monkeypatch, spy_sys_exit=[])

        app._electron_pid = 12345  # standalone mode
        cleanup_calls = []
        monkeypatch.setattr(
            app,
            "_do_cleanup",
            lambda: cleanup_calls.append(1),
        )

        app.restart_app()

        assert cleanup_calls == [1], (
            "in-place restart must run _do_cleanup (full teardown) so the "
            f"re-initialized app starts clean; cleanup_calls={cleanup_calls}"
        )


def _run_spy(app, errors):
    """Helper: run ``app.restart_app()`` and capture any exception
    (especially ``SystemExit``) into ``errors`` so the calling test
    can assert no exception escaped into the worker thread."""
    try:
        app.restart_app()
    except BaseException as exc:  # noqa: BLE001 — we want to capture EVERYTHING
        errors.append(exc)
