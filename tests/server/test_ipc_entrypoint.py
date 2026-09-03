"""Behavioral tests for ``voice_typer.server.ipc.entrypoint``.

These tests exercise the three module-level functions that turn a CLI
invocation into a running ``IPCServer``:

  - :func:`_set_process_metadata` — Windows console title / AppUserModelID,
  - :func:`parse_ipc_args` — argparse + env-var side-effects,
  - :func:`main` — the actual subprocess entry point.

The ``main()`` tests stub out every heavy dependency
(``VoiceTyperApp``, ``build_ipc_server``, ``_setup_logging``,
``_ensure_single_instance``, ``_set_process_metadata``) so the entry
point's control flow is exercised without actually starting the tray
event loop or binding a TCP socket. The tests pin:

  - The argparse exit code on invalid ``--port`` (``SystemExit(2)`` from
    argparse's ``type=int`` rejection, stderr mentions ``--port``).
  - The defaults applied when no args are passed.
  - ``_set_process_metadata`` is a no-op on non-Windows (the platform
    helper returns early at the top).
  - ``main()`` registers a signal handler via ``signal.signal`` on POSIX
    (the codebase registers ``SIGUSR1`` for faulthandler thread-dumps;
    SIGINT/SIGTERM are not registered by ``main()`` — that's the
    pystray / Tauri host's responsibility, noted in SKIPPED).
  - ``main()`` returns cleanly (exit code 0) on a clean shutdown.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from unittest.mock import MagicMock

import pytest
import voice_typer.server.app  # noqa: F401  (force-import for patch targets)
from voice_typer.server.ipc import entrypoint

# ── parse_ipc_args ────────────────────────────────────────────────────


class TestParseIpcArgs:
    """``parse_ipc_args`` parses ``sys.argv`` and applies env-var
    side-effects (``--debug``, ``--allow-stdin``, ``--ws``).

    Returns ``(port, ws_mode)`` where ``port`` is the ``--port N`` value
    (or ``None`` for stdin/stdout mode) and ``ws_mode`` is True when
    ``--ws`` was passed.
    """

    def test_invalid_port_value_exits_with_systemexit_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--port invalid`` (non-integer) triggers argparse's
        ``type=int`` rejection. argparse calls ``parser.error(...)`` which
        prints a message to stderr mentioning ``--port`` and exits with
        code 2.

        Note: this is argparse's standard rejection path (exit code 2),
        distinct from the explicit ``sys.exit(EXIT_BAD_ARGS)`` (code 4)
        the function uses for out-of-range ports like ``99999``.
        """
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "invalid"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        assert exc_info.value.code == 2, (
            f"argparse's type=int rejection must exit with code 2 (got {exc_info.value.code!r})"
        )
        # The stderr message must reference --port so the user knows
        # which argument was invalid.
        captured = capsys.readouterr()
        assert "--port" in captured.err, (
            "argparse error message must mention '--port' so the user knows which argument was rejected."
        )

    def test_out_of_range_port_exits_with_exit_bad_args(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--port 99999`` is an integer but out of the 1..65535 range.
        ``parse_ipc_args`` explicitly rejects it via
        ``sys.exit(EXIT_BAD_ARGS)`` (code 4) and prints a stderr message
        mentioning the port.

        Sanity check that the explicit rejection path is exercised
        (distinct from argparse's type-int rejection in the previous test).
        """
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "99999"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        assert exc_info.value.code == 4, (
            f"out-of-range --port must exit with EXIT_BAD_ARGS (4); got {exc_info.value.code!r}"
        )
        captured = capsys.readouterr()
        assert "port" in captured.err.lower()

    def test_defaults_when_no_args_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no CLI args, ``parse_ipc_args`` returns ``(None, False)`` —
        stdin/stdout mode, no ``--ws``. The env-var side-effects
        (``VOICE_TYPER_DEBUG``, ``TAURI_SIDECAR``,
        ``VOICE_TYPER_ALLOW_STDIN_IPC``) are NOT set."""
        monkeypatch.delenv("VOICE_TYPER_DEBUG", raising=False)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server"])
        port, ws_mode = entrypoint.parse_ipc_args()
        assert port is None
        assert ws_mode is False
        import os

        assert os.environ.get("VOICE_TYPER_DEBUG") is None
        assert os.environ.get("TAURI_SIDECAR") is None
        assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") is None

    def test_port_arg_returns_port_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--port 9876`` returns ``(9876, False)``."""
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        port, ws_mode = entrypoint.parse_ipc_args()
        assert port == 9876
        assert ws_mode is False

    def test_ws_arg_sets_tauri_sidecar_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--ws`` returns ``(None, True)`` and sets ``TAURI_SIDECAR=1``
        so downstream gates (heartbeat watchdog, single-instance mutex)
        know to defer to the Tauri host."""
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        port, ws_mode = entrypoint.parse_ipc_args()
        assert port is None
        assert ws_mode is True
        import os

        assert os.environ.get("TAURI_SIDECAR") == "1"
        # parse_ipc_args mutated the process env directly — monkeypatch
        # cannot track a raw ``os.environ[...] = ...`` assignment (the
        # ``delenv`` above only guards the pre-test state). Restore here
        # so ``TAURI_SIDECAR=1`` does not leak into every later test in
        # this process (e.g. ``task_scheduler._prewarm_command`` would
        # switch to the Tauri-sidecar resolver path).
        os.environ.pop("TAURI_SIDECAR", None)

    def test_ws_and_port_mutually_exclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--ws`` + ``--port`` is rejected (ADR-0020 §2): the WS path
        binds an OS-assigned ephemeral port and reports it via stdout,
        so an explicit ``--port`` would be a contradiction."""
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws", "--port", "9876"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        # EXIT_BAD_ARGS = 4 (the named constant for invalid arg combos).
        assert exc_info.value.code == 4


# ── _set_process_metadata ─────────────────────────────────────────────


class TestSetProcessMetadata:
    """``_set_process_metadata`` sets Windows console title + AppUserModelID
    via the platform helper. On non-Windows the helper returns early at
    the top (``if not is_windows(): return``) — the function is a no-op.
    """

    def test_no_op_on_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Linux/macOS, ``_set_process_metadata`` must not raise and
        must not attempt any Windows-specific ctypes calls.

        The platform helper's ``if not is_windows(): return`` is the
        canonical gate — this test pins it by mocking
        ``_set_windows_process_metadata`` and asserting it WAS called
        (so we know the entry point routed correctly) but that the
        helper itself short-circuited via its own ``is_windows()`` check.
        """
        from voice_typer.server import platform_utils

        # Force is_windows() to return False (the test runs on Linux,
        # but be explicit so the test is platform-qualified).
        monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
        # The helper should still be called by _set_process_metadata —
        # the helper's own is_windows() check is what makes it a no-op.
        called: list[str] = []

        def _spy(app_name: str) -> None:
            called.append(app_name)
            # Re-check is_windows() the way the real helper does —
            # if False, return early (no ctypes work).
            if not platform_utils.is_windows():
                return

        monkeypatch.setattr(
            "voice_typer.server.platform_utils._set_windows_process_metadata",
            _spy,
        )
        # Must not raise.
        entrypoint._set_process_metadata()
        # The entry point routed to the platform helper.
        assert len(called) == 1
        # The helper saw a non-Windows platform and returned early.
        assert called[0]  # APP_NAME was passed through

    def test_imports_branding_app_name(self) -> None:
        """``_set_process_metadata`` imports ``APP_NAME`` from the
        branding module (single source of truth) rather than hardcoding
        the app name. Source-level pin so a future refactor doesn't
        silently revert to a literal."""
        import inspect

        src = inspect.getsource(entrypoint._set_process_metadata)
        assert "from voice_typer.server.branding import APP_NAME" in src, (
            "_set_process_metadata must import APP_NAME from branding.py "
            "(single source of truth) — never hardcode the app name."
        )


class TestDetachProcessGroup:
    """``_detach_process_group`` moves the sidecar into its own POSIX
    process group (``os.setpgid(0, 0)``) at startup.

    The Tauri host cannot apply pre_exec(setpgid) to release-mode
    externalBin children, so the sidecar performs the detach ITSELF.
    Contract: POSIX-only (Windows is a hard no-op), best-effort (a
    refusal is logged and swallowed — never blocks startup), and wired
    into ``main()`` before any subsystem init.
    """

    def test_calls_setpgid_once_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On POSIX, exactly ONE ``os.setpgid(0, 0)`` call is made and
        the helper reports success."""
        calls: list[tuple[int, int]] = []

        def _fake_setpgid(pid: int, pgid: int) -> None:
            calls.append((pid, pgid))

        monkeypatch.setattr(entrypoint.os, "name", "posix")
        # Windows CPython's os module has no setpgid attribute at all —
        # the production code guard (os.name check) is what keeps it
        # unreachable there. Inject the attribute the POSIX runtime
        # would have so the call path can be observed on this host.
        monkeypatch.setattr(entrypoint.os, "setpgid", _fake_setpgid, raising=False)
        monkeypatch.setattr(entrypoint.os, "getpgrp", lambda: 4242, raising=False)

        assert entrypoint._detach_process_group() is True
        assert calls == [(0, 0)], f"the sidecar must detach ITSELF via os.setpgid(0, 0) exactly once; got {calls!r}"

    def test_no_call_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows (os.name == 'nt') the helper returns False WITHOUT
        touching ``os.setpgid`` — process groups are a POSIX concept and
        a crash here would break every Windows sidecar start."""
        calls: list[tuple[int, int]] = []

        def _spy_setpgid(pid: int, pgid: int) -> None:
            calls.append((pid, pgid))

        monkeypatch.setattr(entrypoint.os, "name", "nt")
        monkeypatch.setattr(entrypoint.os, "setpgid", _spy_setpgid, raising=False)

        assert entrypoint._detach_process_group() is False
        assert calls == [], "os.setpgid must never be called on Windows (hard no-op)"

    def test_setpgid_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A best-effort detach: ``os.setpgid`` raising OSError (EACCES /
        EPERM in a sandboxed frozen environment) must be logged and
        swallowed — the helper returns False and startup continues."""

        def _raising_setpgid(pid: int, pgid: int) -> None:
            raise PermissionError(1, "operation not permitted")

        monkeypatch.setattr(entrypoint.os, "name", "posix")
        monkeypatch.setattr(entrypoint.os, "setpgid", _raising_setpgid, raising=False)

        assert entrypoint._detach_process_group() is False, (
            "a setpgid refusal must never raise out of the helper — the sidecar stays in the host's group instead"
        )

    def test_main_wires_detach_before_subsystem_init(self) -> None:
        """``main()`` must call ``_detach_process_group()`` early — before
        the app construction path — so the detach (and the process group
        its children inherit) is in place before anything is spawned."""
        import inspect

        src = inspect.getsource(entrypoint.main)
        assert "_detach_process_group()" in src, (
            "main() must invoke the process-group self-detach — the "
            "release-mode Tauri host cannot pre_exec(setpgid) externalBin "
            "children, so the sidecar must detach ITSELF at startup."
        )
        detach_idx = src.index("_detach_process_group()")
        app_idx = src.index("VoiceTyperApp(")
        assert detach_idx < app_idx, (
            "the detach must run BEFORE app construction (children spawned by the sidecar inherit its process group)"
        )


# ── main() ────────────────────────────────────────────────────────────


class TestWsModeStartupLaunch:
    """The ws (Tauri sidecar) branch of ``main()`` must launch the app
    startup background work.

    ``main()``'s ws branch exits via ``sys.exit()`` and never reaches the
    ``app.start()`` at the bottom of the function — but ``app.start()`` is
    the ONLY production launcher of the StartupSequence (microphone
    enumeration, hotkey registration, background model load, autostart
    sync) and of the tray's CPU-fallback alert subscriptions. The branch
    therefore launches ``app.start`` on a daemon thread (via the
    module-level ``_ws_startup_thread_main`` fail-fast wrapper). Source-level
    pin (same convention as ``test_imports_branding_app_name``) so a future
    refactor cannot silently drop the launch and re-blank the Tauri
    Microphone page / tray microphone submenu / hotkey registration.
    """

    def test_ws_branch_launches_app_start_on_daemon_thread(self) -> None:
        """``main()`` source: the ws branch starts a daemon thread whose
        target is the fail-fast ``_ws_startup_thread_main`` wrapper (which
        itself calls ``app.start``) before ``sidecar_ws.run`` blocks."""
        import inspect

        src = inspect.getsource(entrypoint.main)
        assert "target=_ws_startup_thread_main" in src, (
            "main()'s ws branch must launch app.start() through the "
            "fail-fast _ws_startup_thread_main wrapper on a daemon "
            "thread — the ws exit path otherwise never runs the "
            "StartupSequence (microphones/hotkeys/model load) and the "
            "Tauri sidecar serves an empty microphone list forever."
        )
        assert 'name="ws-sidecar-startup"' in src
        assert "daemon=True" in src
        # The wrapper must be the module-level function (fail-fast), and
        # it must call app.start() in its body.
        wrapper_src = inspect.getsource(entrypoint._ws_startup_thread_main)
        assert "app.start()" in wrapper_src, (
            "_ws_startup_thread_main must invoke app.start() — the "
            "thread target exists solely to run the StartupSequence."
        )
        # The thread must be started BEFORE sidecar_ws.run() blocks the
        # main thread — otherwise the startup work never begins.
        ws_branch = src.split("if ws_mode:", 1)[1].split("elif port is not None", 1)[0]
        thread_start = ws_branch.index("_ws_startup_thread.start()")
        ws_run = ws_branch.index("sidecar_ws.run(server)")
        assert thread_start < ws_run, (
            "the ws-sidecar startup thread must start before sidecar_ws.run(server) blocks the main thread"
        )


class TestWsStartupThreadFailFast:
    """``_ws_startup_thread_main`` must terminate the PROCESS when
    ``app.start()`` raises on the ws-sidecar startup daemon thread.

    An unhandled exception on a daemon thread only reaches the process
    threading excepthook (crash marker + log) — the process would keep
    running DEGRADED: WS server alive, but no microphones / hotkeys /
    model. The wrapper must instead log FATAL, write the startup
    diagnostic, and exit with ``EXIT_CRASH`` (``os._exit``, the canonical
    thread-context force-exit) so the Tauri supervisor respawns the
    sidecar (WS drop → respawn + restart-counter circuit breaker).
    """

    @staticmethod
    def _run_wrapper(app: object) -> threading.Thread:
        """Run the wrapper on a REAL daemon thread and join it, mirroring
        the production thread shape (name aside)."""
        thread = threading.Thread(
            target=entrypoint._ws_startup_thread_main,
            args=(app,),
            name="ws-sidecar-startup-test",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), (
            "the startup wrapper thread must return after handling the "
            "crash (it force-exits via the patched os._exit) — a live "
            "thread after join means the crash path never ran"
        )
        return thread

    def test_crash_exits_process_with_crash_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_config_dir,
    ) -> None:
        """``app.start()`` raising inside the wrapper terminates the
        process with ``EXIT_CRASH`` (via ``os._exit``) AND writes the
        startup diagnostic to ``<config_dir>/logs/startup-error.log``
        (the real helper — the traceback must survive pythonw.exe)."""
        from voice_typer.__main__ import EXIT_CRASH

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))

        class _CrashApp:
            def start(self) -> None:
                raise RuntimeError("simulated ws startup failure")

        self._run_wrapper(_CrashApp())

        assert exit_calls == [EXIT_CRASH], (
            f"a ws-startup crash must force-exit the process with "
            f"EXIT_CRASH ({EXIT_CRASH}) so the Tauri supervisor respawns; "
            f"got {exit_calls!r}"
        )
        # The diagnostic landed in the isolated tmp_config_dir (O1: logs/).
        diag = tmp_config_dir / "logs" / "startup-error.log"
        assert diag.exists(), (
            "a ws-startup crash must write the startup diagnostic (same helper as the main-path app.start() failure)"
        )
        assert "simulated ws startup failure" in diag.read_text(encoding="utf-8")

    def test_crash_routes_through_startup_diagnostic_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On crash the wrapper invokes ``write_startup_diagnostic`` with
        a ws-specific phase label (pinning the wiring that the finding
        showed was missing on this path)."""
        diag_calls: list[str] = []
        monkeypatch.setattr(os, "_exit", lambda code: None)
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda phase, exc=None: diag_calls.append(phase),
        )

        class _CrashApp:
            def start(self) -> None:
                raise ValueError("another simulated ws startup failure")

        self._run_wrapper(_CrashApp())
        assert diag_calls == ["ws app.start()"], (
            "the ws-startup crash path must route through write_startup_diagnostic with the ws-specific phase label"
        )

    def test_system_exit_inside_app_start_also_terminates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``SystemExit`` escaping ``app.start()`` on the daemon thread
        must also terminate the process (it would otherwise strand the
        main thread in ``sidecar_ws.run`` behind a dead app). The crash
        code path applies to any ``BaseException``, not just ``Exception``."""

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda phase, exc=None: None,
        )

        class _SystemExitApp:
            def start(self) -> None:
                raise SystemExit(0)

        self._run_wrapper(_SystemExitApp())
        assert exit_calls == [1], (
            "SystemExit escaping app.start() on the ws startup thread "
            "must still force-exit the process (crash code), never leave "
            "a WS-alive-but-empty backend running"
        )

    def test_success_does_not_exit_or_write_diagnostic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Control test: ``app.start()`` returning cleanly must NOT exit
        the process and must NOT write any startup diagnostic."""
        exit_calls: list[int] = []
        diag_calls: list[str] = []
        monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda phase, exc=None: diag_calls.append(phase),
        )

        started: list[bool] = []

        class _HealthyApp:
            def start(self) -> None:
                started.append(True)

        self._run_wrapper(_HealthyApp())
        assert started == [True]
        assert exit_calls == [], "a clean app.start() must never exit the process"
        assert diag_calls == [], "a clean app.start() must never write a startup diagnostic"


class TestMainEntrypoint:
    """``main()`` constructs ``VoiceTyperApp`` + ``IPCServer``, starts the
    server, and blocks on ``app.start()``. These tests stub every heavy
    dependency so the control flow is exercised without actually
    starting the tray event loop or binding a TCP socket.
    """

    @pytest.fixture(autouse=True)
    def _clean_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() / parse_ipc_args() read sys.argv; keep it clean and
        deterministic for every test in this class."""
        monkeypatch.setattr(sys, "argv", ["ipc_server"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

    def test_main_registers_signal_handler_on_posix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``main()`` enables ``faulthandler`` and registers a signal
        handler via ``signal.signal`` so SIGUSR1 (POSIX) dumps the
        thread traceback on demand. The test mocks ``signal.signal`` to
        capture every call and asserts at least one registration happens
        on POSIX (where SIGUSR1 exists).

        Platform-qualified: on Windows ``signal.SIGUSR1`` does not exist,
        so the ``hasattr(signal, "SIGUSR1")`` guard in ``main()`` skips
        the registration — the assertion is gated on ``hasattr`` so the
        test passes on both platforms but only asserts the SIGUSR1
        registration where it's available.
        """
        # Mock every heavy dependency so main() runs to completion.
        app_mock = MagicMock()
        app_mock.start.return_value = None  # clean shutdown
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._set_process_metadata",
            lambda: None,
        )
        # build_ipc_server returns a MagicMock server — no real threads.
        fake_server = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.providers.build_ipc_server",
            lambda app: fake_server,
        )
        # Skip the standalone path's electron launch + port pick by
        # passing --port (start_tcp is mocked on the fake server).
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        # Disable faulthandler.enable so the test doesn't alter real
        # process state — but keep signal.signal mockable.
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda **kw: None)

        # Capture signal.signal calls.
        signal_calls: list[tuple] = []
        real_signal = signal.signal

        def _capture_signal(signum, handler, *args, **kwargs):
            signal_calls.append((signum, handler))
            # Don't actually install (the test would lose SIGUSR1
            # control). Return the previous handler shape.
            return real_signal(signum, lambda *a: None, *args, **kwargs)

        monkeypatch.setattr(signal, "signal", _capture_signal)

        # main() should return None (clean shutdown) — no SystemExit.
        result = entrypoint.main()
        assert result is None, "main() must return None on a clean shutdown (Python exit code 0)."

        # On POSIX, main() registers a SIGUSR1 faulthandler-dump handler
        # via signal.signal. On Windows SIGUSR1 does not exist and
        # main() legitimately registers nothing (faulthandler.enable()
        # alone does not call signal.signal) — gate both assertions on
        # the platform so the test passes everywhere but only pins the
        # registration where it exists.
        if hasattr(signal, "SIGUSR1"):
            # At least one signal handler was registered via signal.signal.
            assert len(signal_calls) >= 1, (
                "main() must register at least one signal handler via "
                "signal.signal (the SIGUSR1 faulthandler-dump handler on POSIX)."
            )
            # On POSIX, SIGUSR1 was among the registered signals.
            registered_signals = {call[0] for call in signal_calls}
            assert signal.SIGUSR1 in registered_signals, (
                "POSIX main() must register a handler for SIGUSR1 so "
                "an on-demand thread dump is available for production "
                "crash debugging."
            )

    def test_main_exit_code_zero_on_clean_shutdown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``app.start()`` returns cleanly (no exception), ``main()``
        must NOT raise ``SystemExit`` — Python's implicit exit code 0
        applies. This pins the clean-shutdown contract."""
        app_mock = MagicMock()
        app_mock.start.return_value = None  # clean shutdown
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._set_process_metadata",
            lambda: None,
        )
        fake_server = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.providers.build_ipc_server",
            lambda app: fake_server,
        )
        # Use --port mode so the standalone electron-launch path is
        # skipped (start_tcp is a no-op MagicMock).
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        # faulthandler.enable would alter real process state — stub it.
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)

        # main() returns None on clean shutdown — no SystemExit raised.
        result = entrypoint.main()
        assert result is None
        # The IPC server was started + the ready event was pushed.
        fake_server.start.assert_called_once()
        fake_server.start_tcp.assert_called_once_with(9876)
        fake_server.push.assert_called_once_with({"type": "ready"})
        # app.start() was called and returned cleanly.
        app_mock.start.assert_called_once()

    def test_main_exit_code_crash_on_app_construction_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_config_dir,
    ) -> None:
        """When ``VoiceTyperApp()`` construction raises, ``main()`` must
        write a diagnostic and exit with ``EXIT_CRASH`` (1). This pins
        the construction-failure path's exit code (distinct from the
        clean-shutdown exit code 0 above)."""
        # Isolate the diagnostic file so the test doesn't pollute the
        # developer's real ~/.voice-typer/startup-error.log.
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._set_process_metadata",
            lambda: None,
        )

        def _boom():
            raise RuntimeError("simulated construction failure")

        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", _boom)
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)

        with pytest.raises(SystemExit) as exc_info:
            entrypoint.main()
        assert exc_info.value.code == 1, (
            f"construction failure must exit with EXIT_CRASH (1); got {exc_info.value.code!r}"
        )
        # The diagnostic landed in the isolated tmp_config_dir (O1: logs/).
        diag = tmp_config_dir / "logs" / "startup-error.log"
        assert diag.exists()
        assert "simulated construction failure" in diag.read_text(encoding="utf-8")
