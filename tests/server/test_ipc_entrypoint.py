"""Behavioral tests for ``voice_typer.server.ipc.entrypoint``."""

from __future__ import annotations

import os
import signal
import sys
import threading
from unittest.mock import MagicMock

import pytest
import voice_typer.server.app  # noqa: F401  (force-import for patch targets)
from voice_typer.server.ipc import entrypoint


class TestParseIpcArgs:
    """``parse_ipc_args`` parses ``sys.argv`` and applies env-var"""

    def test_invalid_port_value_exits_with_systemexit_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--port invalid`` (non-integer) triggers argparse's"""
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "invalid"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        assert exc_info.value.code == 2, (
            f"argparse's type=int rejection must exit with code 2 (got {exc_info.value.code!r})"
        )
        # The stderr message must reference --port so the user knows
        captured = capsys.readouterr()
        assert "--port" in captured.err, (
            "argparse error message must mention '--port' so the user knows which argument was rejected."
        )

    def test_out_of_range_port_exits_with_exit_bad_args(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--port 99999`` is an integer but out of the 1..65535 range."""
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "99999"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        assert exc_info.value.code == 4, (
            f"out-of-range --port must exit with EXIT_BAD_ARGS (4); got {exc_info.value.code!r}"
        )
        captured = capsys.readouterr()
        assert "port" in captured.err.lower()

    def test_defaults_when_no_args_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no CLI args, ``parse_ipc_args`` returns ``(None, False)`` —"""
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

    def test_allow_stdin_flag_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The inert ``--allow-stdin`` CLI flag is gone; only the env var gates stdin.

        ``main`` hard-sets ``_tcp_mode = True`` for every transport, so the
        flag could never enable the stdin listener. Passing it must now be a
        no-op that leaves the env var unset (the direct-API/test seam stays).
        """
        import os

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--allow-stdin"])
        entrypoint.parse_ipc_args()
        assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") is None, (
            "--allow-stdin must no longer set VOICE_TYPER_ALLOW_STDIN_IPC; the flag was "
            "removed because main() forces _tcp_mode = True so it could never take effect."
        )

    def test_port_arg_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--port`` is unsupported after TCP removal: EXIT_BAD_ARGS (4)."""
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        assert exc_info.value.code == 4

    def test_ws_arg_sets_tauri_sidecar_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--ws`` returns ``(None, True)`` and sets ``TAURI_SIDECAR=1``"""
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        port, ws_mode = entrypoint.parse_ipc_args()
        assert port is None
        assert ws_mode is True
        import os

        assert os.environ.get("TAURI_SIDECAR") == "1"
        os.environ.pop("TAURI_SIDECAR", None)

    def test_ws_and_port_mutually_exclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--ws`` + ``--port`` is rejected (ADR-0020 §2): the WS path"""
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws", "--port", "9876"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        # EXIT_BAD_ARGS = 4 (the named constant for invalid arg combos).
        assert exc_info.value.code == 4


class TestLazyVersionResolution:
    """The installed-package version is resolved ONLY when ``--version``"""

    def test_no_version_flag_skips_metadata_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A normal boot (``--ws``, no ``--version``) must not call"""
        import importlib.metadata

        calls: list[str] = []

        def _spy_version(name: str) -> str:
            calls.append(name)
            return "9.9.9-test"

        monkeypatch.setattr(importlib.metadata, "version", _spy_version)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        entrypoint.parse_ipc_args()

        assert calls == [], (
            "parse_ipc_args must not resolve the installed package version "
            "when --version is absent from argv, a normal boot pays no "
            "dist-metadata scan. Observed lookups: "
            f"{calls}"
        )
        # Restore the env side-effect the --ws path applies (raw
        os.environ.pop("TAURI_SIDECAR", None)

    def test_version_flag_prints_resolved_version_and_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With ``--version`` present, the real installed version is"""
        import importlib.metadata

        monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9-test")
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--version"])

        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()

        assert exc_info.value.code == 0, "--version must exit with code 0"
        captured = capsys.readouterr()
        assert captured.out.strip() == "voice_typer.server.ipc_server 9.9.9-test", (
            f"--version output must stay identical when requested: {captured.out!r}"
        )

    def test_version_flag_falls_back_when_metadata_lookup_fails(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When ``importlib.metadata.version`` raises (package metadata"""
        import importlib.metadata

        def _boom(name: str) -> str:
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, "version", _boom)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--version"])

        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "voice_typer.server.ipc_server 1.0.0", (
            f"--version must print the 1.0.0 placeholder on lookup failure: {captured.out!r}"
        )

    def test_abbreviated_version_flag_still_resolves(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unambiguous argparse prefix abbreviation (``--vers``)"""
        import importlib.metadata

        monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9-test")
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--vers"])

        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "voice_typer.server.ipc_server 9.9.9-test", (
            f"abbreviated --vers must resolve the real version: {captured.out!r}"
        )


class TestVersionRequestedBareDashGuard:
    """version request."""

    def test_bare_double_dash_is_not_a_version_request(self) -> None:
        assert entrypoint._version_requested(["--"]) is False, (
            "a bare `--` (end-of-options marker) must not be treated as a "
            "--version request: `'version'.startswith('')` is vacuously true"
        )

    def test_bare_dash_does_not_trigger_metadata_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A boot whose argv carries a bare ``--`` token (e.g. a launcher"""
        import importlib.metadata

        calls: list[str] = []

        def _spy_version(name: str) -> str:
            calls.append(name)
            return "9.9.9-test"

        monkeypatch.setattr(importlib.metadata, "version", _spy_version)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws", "--"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        entrypoint.parse_ipc_args()

        assert calls == [], (
            f"a bare `--` in argv must not resolve the installed package version, observed lookups: {calls}"
        )
        os.environ.pop("TAURI_SIDECAR", None)

    def test_exact_flag_and_real_prefixes_still_match(self) -> None:
        """The exactness guard must not break the intended matches: the"""
        assert entrypoint._version_requested(["--version"]) is True
        assert entrypoint._version_requested(["--vers"]) is True
        assert entrypoint._version_requested(["--v"]) is True

    def test_non_matching_flags_are_not_version_requests(self) -> None:
        assert entrypoint._version_requested(["--verbose"]) is False, (
            "`--verbose` is not a prefix of `--version` and must not match"
        )
        assert entrypoint._version_requested(["--ws"]) is False
        assert entrypoint._version_requested([]) is False


class TestSetProcessMetadata:
    """``_set_process_metadata`` sets Windows console title + AppUserModelID"""

    def test_no_op_on_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """must not attempt any Windows-specific ctypes calls."""
        from voice_typer.server import platform_utils

        # Force is_windows() to return False (the test runs on Linux,
        monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
        # The helper should still be called by _set_process_metadata —
        called: list[str] = []

        def _spy(app_name: str) -> None:
            called.append(app_name)
            # Re-check is_windows() the way the real helper does —
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
        """branding module (single source of truth) rather than hardcoding"""
        import inspect

        src = inspect.getsource(entrypoint._set_process_metadata)
        assert "from voice_typer.server.branding import APP_NAME" in src, (
            "_set_process_metadata must import APP_NAME from branding.py "
            "(single source of truth), never hardcode the app name."
        )


class TestDetachProcessGroup:
    """``_detach_process_group`` moves the sidecar into its own POSIX"""

    def test_calls_setpgid_once_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On POSIX, exactly ONE ``os.setpgid(0, 0)`` call is made and"""
        calls: list[tuple[int, int]] = []

        def _fake_setpgid(pid: int, pgid: int) -> None:
            calls.append((pid, pgid))

        monkeypatch.setattr(entrypoint.os, "name", "posix")
        # Windows CPython's os module has no setpgid attribute at all —
        monkeypatch.setattr(entrypoint.os, "setpgid", _fake_setpgid, raising=False)
        monkeypatch.setattr(entrypoint.os, "getpgrp", lambda: 4242, raising=False)

        assert entrypoint._detach_process_group() is True
        assert calls == [(0, 0)], f"the sidecar must detach ITSELF via os.setpgid(0, 0) exactly once; got {calls!r}"

    def test_no_call_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows (os.name == 'nt') the helper returns False WITHOUT"""
        calls: list[tuple[int, int]] = []

        def _spy_setpgid(pid: int, pgid: int) -> None:
            calls.append((pid, pgid))

        monkeypatch.setattr(entrypoint.os, "name", "nt")
        monkeypatch.setattr(entrypoint.os, "setpgid", _spy_setpgid, raising=False)

        assert entrypoint._detach_process_group() is False
        assert calls == [], "os.setpgid must never be called on Windows (hard no-op)"

    def test_setpgid_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A best-effort detach: ``os.setpgid`` raising OSError (EACCES /"""

        def _raising_setpgid(pid: int, pgid: int) -> None:
            raise PermissionError(1, "operation not permitted")

        monkeypatch.setattr(entrypoint.os, "name", "posix")
        monkeypatch.setattr(entrypoint.os, "setpgid", _raising_setpgid, raising=False)

        assert entrypoint._detach_process_group() is False, (
            "a setpgid refusal must never raise out of the helper, the sidecar stays in the host's group instead"
        )

    def test_main_wires_detach_before_subsystem_init(self) -> None:
        """``main()`` must call ``_detach_process_group()`` early, before"""
        import inspect

        src = inspect.getsource(entrypoint.main)
        assert "_detach_process_group()" in src, (
            "main() must invoke the process-group self-detach, the "
            "release-mode Tauri host cannot pre_exec(setpgid) externalBin "
            "children, so the sidecar must detach ITSELF at startup."
        )
        detach_idx = src.index("_detach_process_group()")
        # Anchor on the construction DELEGATION (the single LausuApp
        app_idx = src.index("_construct_app_with_diagnostics()")
        assert detach_idx < app_idx, (
            "the detach must run BEFORE app construction (children spawned by the sidecar inherit its process group)"
        )


class TestWsModeStartupLaunch:
    """The ws (Tauri sidecar) branch of ``main()`` must launch the app"""

    def test_ws_branch_launches_app_start_on_daemon_thread(self) -> None:
        """``main()`` source: the ws branch starts a daemon thread whose"""
        import inspect

        src = inspect.getsource(entrypoint.main)
        assert "target=_ws_startup_thread_main" in src, (
            "main()'s ws branch must launch app.start() through the "
            "fail-fast _ws_startup_thread_main wrapper on a daemon "
            "thread, the ws exit path otherwise never runs the "
            "StartupSequence (microphones/hotkeys/model load) and the "
            "Tauri sidecar serves an empty microphone list forever."
        )
        assert 'name="ws-sidecar-startup"' in src
        assert "daemon=True" in src
        wrapper_src = inspect.getsource(entrypoint._ws_startup_thread_main)
        assert "app.start()" in wrapper_src, (
            "_ws_startup_thread_main must invoke app.start(), the "
            "thread target exists solely to run the StartupSequence."
        )
        ws_branch = src.split("if ws_mode:", 1)[1].split("elif port is not None", 1)[0]
        thread_start = ws_branch.index("_ws_startup_thread.start()")
        ws_run = ws_branch.index("sidecar_ws.run(server)")
        assert thread_start < ws_run, (
            "the ws-sidecar startup thread must start before sidecar_ws.run(server) blocks the main thread"
        )


class TestWsStartupThreadFailFast:
    """``_ws_startup_thread_main`` must terminate the PROCESS when"""

    @staticmethod
    def _run_wrapper(app: object) -> threading.Thread:
        """Run the wrapper on a REAL daemon thread and join it, mirroring"""
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
            "crash (it force-exits via the patched os._exit), a live "
            "thread after join means the crash path never ran"
        )
        return thread

    def test_crash_exits_process_with_crash_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_config_dir,
    ) -> None:
        """startup diagnostic to ``<config_dir>/logs/startup-error.log``"""
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
        """a ws-specific phase label (pinning the wiring that the finding"""
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
        """A ``SystemExit`` escaping ``app.start()`` on the daemon thread"""

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
        """Control test: ``app.start()`` returning cleanly must NOT exit"""
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
    """server, and blocks on ``app.start()``. These tests stub every heavy"""

    @pytest.fixture(autouse=True)
    def _clean_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() / parse_ipc_args() read sys.argv; keep it clean and"""
        monkeypatch.setattr(sys, "argv", ["ipc_server"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

    def test_main_registers_signal_handler_on_posix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``main()`` enables ``faulthandler`` and registers a signal"""
        # Mock every heavy dependency so main() runs to completion.
        app_mock = MagicMock()
        app_mock.start.return_value = None  # clean shutdown
        monkeypatch.setattr("voice_typer.server.app.LausuApp", lambda: app_mock)
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
        # Skip the standalone path's frontend launch by passing --ws
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.setattr("voice_typer.server.sidecar_ws.run", lambda server: 0)
        # Disable faulthandler.enable so the test doesn't alter real
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda **kw: None)
        os.environ.pop("TAURI_SIDECAR", None)

        # Capture signal.signal calls.
        signal_calls: list[tuple] = []
        real_signal = signal.signal

        def _capture_signal(signum, handler, *args, **kwargs):
            signal_calls.append((signum, handler))
            # Don't actually install (the test would lose SIGUSR1
            return real_signal(signum, lambda *a: None, *args, **kwargs)

        monkeypatch.setattr(signal, "signal", _capture_signal)

        # WS-mode main() exits via sys.exit(sidecar_ws.run's code).
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.main()
        assert exc_info.value.code in (0, None)
        os.environ.pop("TAURI_SIDECAR", None)

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
        """When ``app.start()`` returns cleanly (no exception), ``main()``"""
        app_mock = MagicMock()
        app_mock.start.return_value = None  # clean shutdown
        monkeypatch.setattr("voice_typer.server.app.LausuApp", lambda: app_mock)
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
        # Use --ws (TCP transport removed).
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.setattr("voice_typer.server.sidecar_ws.run", lambda server: 0)
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        os.environ.pop("TAURI_SIDECAR", None)

        # Run daemon-thread targets inline so app.start() is invoked
        import threading as _threading

        class _ImmediateThread:
            def __init__(self, target=None, args=(), kwargs=None, **_kw):
                self._target = target
                self._args = args or ()
                self._kwargs = kwargs or {}

            def start(self):
                if self._target is not None:
                    self._target(*self._args, **self._kwargs)

        monkeypatch.setattr(_threading, "Thread", _ImmediateThread)

        # WS-mode main() exits via sys.exit(0) after sidecar_ws.run(0).
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.main()
        assert exc_info.value.code in (0, None)
        os.environ.pop("TAURI_SIDECAR", None)
        # The IPC server was started; WS path does not push TCP `ready`.
        fake_server.start.assert_called_once()
        app_mock.start.assert_called_once()

    def test_main_exit_code_crash_on_app_construction_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_config_dir,
    ) -> None:
        """When ``LausuApp()`` construction raises, ``main()`` must"""
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

        monkeypatch.setattr("voice_typer.server.app.LausuApp", _boom)
        monkeypatch.setattr("voice_typer.server.sidecar_ws.run", lambda server: 0)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)

        with pytest.raises(SystemExit) as exc_info:
            entrypoint.main()
        assert exc_info.value.code == 1, (
            f"construction failure must exit with EXIT_CRASH (1); got {exc_info.value.code!r}"
        )
        os.environ.pop("TAURI_SIDECAR", None)
        # The diagnostic landed in the isolated tmp_config_dir (O1: logs/).
        diag = tmp_config_dir / "logs" / "startup-error.log"
        assert diag.exists()
        assert "simulated construction failure" in diag.read_text(encoding="utf-8")
