"""CR-069: split from tests/test_feature_hardening_regressions.py (L227-356).

Source marker: ``tests/test_new_cli_003_exit_codes.py``.

Regression tests for NEW-CLI-003: standardized exit codes.

Previously:
- ``ipc_server.main()`` imported ``EXIT_CRASH`` but never used it,
  falling back to ``sys.exit(1)`` on the crash path.
- The docstring of ``main()`` was placed AFTER the import line,
  meaning it wasn't actually a docstring at all — it was a string
  expression that did nothing.

These tests verify:
1. ``EXIT_CRASH`` is actually used by ``main()`` on the crash path.
2. ``EXIT_BAD_ARGS`` is used on the bad-port path.
3. ``main.__doc__`` is the real docstring (not None).

Class/method names, assertion logic, and imports below are preserved
verbatim from the original monolith — only file location has changed.
"""

# === Source: tests/test_new_cli_003_exit_codes.py ===

from __future__ import annotations

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
        """When ``app.start()`` raises an Exception, ``main()`` must
        exit with ``EXIT_CRASH`` (1), and that 1 must come from the
        named constant — not a raw literal.
        """
        # Isolate the crash-diagnostic writer.  ``main()`` appends the
        # traceback to ``_config_dir() / "startup-error.log"``; without
        # this, the test pollutes the *real* config dir (e.g. the
        # developer's ~/.voice-typer/startup-error.log) with fake
        # "simulated crash" entries.

        # Set up the argv so argparse doesn't bail.
        monkeypatch.setattr(sys, "argv", ["voice-typer"])

        # Avoid actually starting the IPC server / app — make start() raise.
        app_mock = MagicMock()
        app_mock.start.side_effect = RuntimeError("simulated crash")

        # Stub out heavy pieces of main().
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: object(),
        )
        # Stub IPCServer so it doesn't try to bind or spawn threads.
        fake_server = MagicMock()
        monkeypatch.setattr(ipc_server, "IPCServer", lambda app: fake_server)

        # Stub sys.modules registration so main()'s self-registration
        # of the canonical name doesn't overwrite the real module.
        # (main() only sets it if missing, so this is a no-op when
        # the test runner has already imported it.)

        # Stub out the inner import by pre-populating sys.modules with
        # the constants — main() does `from voice_typer.__main__ import
        # EXIT_BAD_ARGS, EXIT_CRASH`, which works without monkeypatching.

        with pytest.raises(SystemExit) as exc_info:
            ipc_server.main()

        assert exc_info.value.code == EXIT_CRASH

        # The diagnostic must land in the isolated temp dir, not the
        # developer's real startup-error.log (O1: logs/).
        diag = tmp_config_dir / "logs" / "startup-error.log"
        assert diag.exists()
        assert "simulated crash" in diag.read_text(encoding="utf-8")

    def test_bad_port_uses_exit_bad_args(self, monkeypatch):
        """When --port is out of range, ``main()`` must exit with
        ``EXIT_BAD_ARGS`` (4)."""
        monkeypatch.setattr(sys, "argv", ["voice-typer", "--port", "99999"])

        # main() constructs VoiceTyperApp() before parsing --port (an
        # existing ordering quirk), so we mock it to a no-op MagicMock.
        # We then assert that app.start() is NEVER called because main()
        # exits before reaching that point.
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
        # The raw literal must NOT appear (we use the named constant).
        assert "sys.exit(1)" not in source, "main() still uses raw sys.exit(1) instead of EXIT_CRASH"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
