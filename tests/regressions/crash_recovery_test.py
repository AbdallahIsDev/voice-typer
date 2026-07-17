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
import sys
from pathlib import Path
from unittest.mock import MagicMock

# ─── Linux test-env shim (RW-8) ──────────────────────────────────────────
# ``voice_typer.server.crash_handler`` uses ``ctypes.WINFUNCTYPE`` as a
# decorator at module load time. That attribute only exists on Windows,
# so importing ``voice_typer.server.app`` (which does
# ``from voice_typer.server import crash_handler``) raises
# ``AttributeError`` on Linux. Many tests in this file introspect
# ``VoiceTyperApp`` source via ``inspect.getsource``; without this
# shim, those tests would fail non-deterministically depending on
# whether some earlier test happened to pre-load ``app``. The same
# pattern is used in ``tests/test_api_doc_accuracy.py:42-57``. This is
# a *test-only* shim — production code never monkey-patches ctypes.
if sys.platform != "win32" and "voice_typer.server.crash_handler" not in sys.modules:
    sys.modules["voice_typer.server.crash_handler"] = MagicMock()


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
