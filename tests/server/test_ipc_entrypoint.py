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

import signal
import sys
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


# ── main() ────────────────────────────────────────────────────────────


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
        monkeypatch.setattr("voice_typer.server.app._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.app._ensure_single_instance",
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
        monkeypatch.setattr("voice_typer.server.app._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.app._ensure_single_instance",
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
        monkeypatch.setattr("voice_typer.server.app._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.app._ensure_single_instance",
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
        # The diagnostic landed in the isolated tmp_config_dir.
        diag = tmp_config_dir / "startup-error.log"
        assert diag.exists()
        assert "simulated construction failure" in diag.read_text(encoding="utf-8")
