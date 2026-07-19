"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import json
from pathlib import Path

# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"`` (see crash_handler.py near
# the ``_vectored_handler_impl`` definition), so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
#
# The MagicMock injection here was actively harmful: it polluted
# ``sys.modules`` for any subsequent test that imported
# ``voice_typer.server.app`` (which does
# ``from voice_typer.server import crash_handler as _crash_handler``),
# causing AttributeError on real crash_handler API calls.
#
# Tests that need to mock crash_handler should do so per-test via
# ``monkeypatch.setattr`` or ``unittest.mock.patch`` (context-managed).


class TestSubprocessCrashRecoveryHandler:
    """PLAT-012: Test the Python exit handler logic."""

    def test_exit_handler_logic_exists(self):
        """Electron main process must handle Python subprocess exit.

        RW-8: KEEP — pins PLAT-012 (Electron main has pythonProcess.on('exit')
        # handler that calls app.quit). A behavioral test would need to run
        # the Electron main process and kill the Python subprocess, which
        # is heavy (requires a running Electron app); the file-content
        # check catches removal of the handler directly.

        REF-2 update: the pythonProcess.on('exit') handler was extracted from
        ``src/main/index.ts`` into ``src/main/python/start-python.ts`` as
        part of the REF-2 main-entry refactor. The handler still exists with
        the same behavior — we now look in the new module location.
        """
        # REF-2: handler now lives in start-python.ts (extracted from index.ts)
        start_python_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "main"
            / "python"
            / "start-python.ts"
        )
        main_path = (
            Path(__file__).resolve().parent.parent.parent / "voice_typer" / "client" / "src" / "main" / "index.ts"
        )
        # Search both locations — the handler may be in either depending on
        # whether the REF-2 refactor is in place.
        src = ""
        if start_python_path.exists():
            src += start_python_path.read_text(encoding="utf-8")
        if main_path.exists():
            src += "\n" + main_path.read_text(encoding="utf-8")
        assert src, "neither start-python.ts nor index.ts found"
        # REF-2 update: the variable was renamed `pythonProcess` → `proc`
        # (stored in `state.pythonProcess`). Either form satisfies the
        # invariant — "the python subprocess has an exit handler that
        # calls app.quit". Look for either.
        assert (
            'pythonProcess.on("exit"' in src
            or "pythonProcess.on('exit'" in src
            or 'proc.on("exit"' in src
            or "proc.on('exit'" in src
        ), "missing pythonProcess/proc exit handler"
        assert "app.quit" in src


class TestCrashRecoveryLoadsStaleState:
    """NEW-CQ-014: Test cleanup on abnormal termination."""

    def test_crash_recovery_loads_stale_state(self, tmp_path):
        """CrashRecovery must load stale state after abnormal termination."""
        from voice_typer.server.crash_recovery import RECOVERY_FILENAME, CrashRecovery

        # CrashRecovery takes a config_dir, not a file path
        recovery_file = tmp_path / RECOVERY_FILENAME

        recovery_file.write_text(json.dumps([{"text": "stale text", "pasted": False}]))
        cr = CrashRecovery(config_dir=tmp_path)
        # Use check_on_startup to load stale state
        cr.check_on_startup()
        items = cr.get_all()
        assert items is not None
        assert len(items) >= 1
