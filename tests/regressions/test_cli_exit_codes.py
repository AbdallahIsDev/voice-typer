"""CR-069: split from tests/test_feature_hardening_regressions.py (L227-356).

Source marker: ``tests/test_new_cli_003_exit_codes.py``.

Regression tests for NEW-CLI-003: standardized exit codes.

Previously:
- ``ipc_server.main()`` imported ``EXIT_CRASH`` but never used it,
  falling back to ``sys.exit(1)`` on the crash path.
- The docstring of ``main()`` was placed AFTER the import line,
  meaning it wasn't actually a docstring at all, it was a string
  expression that did nothing.

These tests verify:
1. ``EXIT_CRASH`` is actually used by ``main()`` on the crash path.
2. ``EXIT_BAD_ARGS`` is used on the bad-port path.
3. ``main.__doc__`` is the real docstring (not None).

Class/method names, assertion logic, and imports below are preserved
verbatim from the original monolith, only file location has changed.
"""

# === Source: tests/test_new_cli_003_exit_codes.py ===

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.__main__ import (
    EXIT_BAD_ARGS,
    EXIT_CLEAN,
    EXIT_CRASH,
    EXIT_DUPLICATE_INSTANCE,
    EXIT_PORT_CONFLICT,
)
from voice_typer.server import ipc_server


class TestExitCodeConstants:
    """Sanity-check the constants exist and have the documented values."""

    def test_constants_have_documented_values(self):
        assert EXIT_CLEAN == 0
        assert EXIT_CRASH == 1
        assert EXIT_PORT_CONFLICT == 2
        assert EXIT_DUPLICATE_INSTANCE == 3
        assert EXIT_BAD_ARGS == 4

    def test_constants_are_distinct(self):
        values = {
            EXIT_CLEAN,
            EXIT_CRASH,
            EXIT_PORT_CONFLICT,
            EXIT_DUPLICATE_INSTANCE,
            EXIT_BAD_ARGS,
        }
        assert len(values) == 5


class TestMainDocstringRestored:
    """NEW-CLI-003 side-fix: the docstring of ``main`` was misplaced
    (after the import line), so ``main.__doc__`` was None.  Verify the
    docstring is now properly attached.
    """

    def test_main_has_docstring(self):
        assert ipc_server.main.__doc__ is not None
        assert "VoiceTyperApp" in ipc_server.main.__doc__


class TestCrashPathUsesExitCrash:
    """NEW-CLI-003 main fix: the crash path must call ``sys.exit(EXIT_CRASH)``,
    not ``sys.exit(1)``.
    """

    def test_crash_path_uses_exit_crash(self, monkeypatch, tmp_config_dir):
        """WS-mode ``app.start()`` crash must exit with EXIT_CRASH (1).

        Post-TCP cutover the supported transport is ``--ws``: ``main()``
        launches ``_ws_startup_thread_main`` on a daemon thread (which
        runs ``app.start()``) and the process fail-exits via ``os._exit``
        when start() raises so the Tauri supervisor can respawn.
        """
        monkeypatch.setattr(sys, "argv", ["voice-typer", "--ws"])

        app_mock = MagicMock()
        app_mock.start.side_effect = RuntimeError("simulated crash")

        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: object(),
        )
        fake_server = MagicMock()
        monkeypatch.setattr(ipc_server, "IPCServer", lambda app: fake_server)
        monkeypatch.setattr(
            "voice_typer.server.providers.build_ipc_server",
            lambda app: fake_server,
        )
        # WS transport: return immediately so main() does not block.
        monkeypatch.setattr("voice_typer.server.sidecar_ws.run", lambda server: 0)

        # Run daemon-thread targets inline so the crash path executes
        # inside this test process (not on a real background thread).
        class _ImmediateThread:
            def __init__(self, target=None, args=(), kwargs=None, **_kw):
                self._target = target
                self._args = args or ()
                self._kwargs = kwargs or {}

            def start(self):
                if self._target is not None:
                    self._target(*self._args, **self._kwargs)

        import threading as _threading

        monkeypatch.setattr(_threading, "Thread", _ImmediateThread)
        # os._exit would kill the test runner; surface it as SystemExit.
        monkeypatch.setattr(
            os,
            "_exit",
            lambda code: (_ for _ in ()).throw(SystemExit(code)),
        )
        monkeypatch.setattr("faulthandler.enable", lambda: None)

        with pytest.raises(SystemExit) as exc_info:
            ipc_server.main()

        assert exc_info.value.code == EXIT_CRASH

        diag = tmp_config_dir / "logs" / "startup-error.log"
        assert diag.exists()
        assert "simulated crash" in diag.read_text(encoding="utf-8")

    def test_bad_port_uses_exit_bad_args(self, monkeypatch):
        """When --port is out of range, ``main()`` must exit with
        ``EXIT_BAD_ARGS`` (4)."""
        monkeypatch.setattr(sys, "argv", ["voice-typer", "--port", "99999"])

        # main() validates --port via parse_ipc_args() before any
        # VoiceTyperApp construction, but we still mock the constructor
        # to a no-op MagicMock for safety. We then assert that
        # app.start() is NEVER called because main() exits before
        # reaching that point.
        app_mock = MagicMock()
        app_mock.start.side_effect = AssertionError("app.start() should not be called when --port is invalid")
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: object(),
        )

        with pytest.raises(SystemExit) as exc_info:
            ipc_server.main()

        assert exc_info.value.code == EXIT_BAD_ARGS
        # Sanity: app.start() really was never called.
        app_mock.start.assert_not_called()


class TestNoRawSysExitOneInMain:
    """The crash-path ``sys.exit(1)`` literal must be gone from
    ``main()``.  We grep the source of ``main()`` to confirm.
    """

    def test_no_raw_sys_exit_one_in_main_source(self):
        import inspect

        source = inspect.getsource(ipc_server.main)
        # The constant reference is allowed.
        assert "sys.exit(EXIT_CRASH)" in source
        # A raw `sys.exit(1)` crash-path literal must not appear; the
        # "no transport specified" diagnostic may use EXIT_BAD_ARGS via
        # the named constant only.
        assert "sys.exit(EXIT_BAD_ARGS)" in source or "sys.exit(EXIT_CRASH)" in source
        # Forbidden: raw crash-path literal (not a named constant).
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("sys.exit(1)"):
                raise AssertionError(f"main() still uses raw {stripped} instead of a named EXIT_* constant")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
