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

import contextlib
import json
from pathlib import Path

import pytest

# the previous Linux test-env shim that aliased
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


class TestSidecarCrashDetectionBehavioral:
    """GT-40: behavioral test of sidecar crash detection.

    The pre-existing ``test_exit_handler_logic_exists`` above is a
    source-string check — it asserts the literal ``proc.on('exit'``
    substring is present in ``start-python.ts``. A refactor that keeps
    the substring while breaking the behavior would still pass.

    This class adds a BEHAVIORAL test that spawns a REAL Python
    subprocess (mimicking the sidecar), binds a real TCP port (the
    IPC port), sends SIGKILL, and verifies the parent detects the
    exit within a bounded time. A regression in the OS-level process
    exit detection (or in ``subprocess.Popen.poll()`` semantics) would
    fail this test.

    NOTE: This test exercises the OS-level subprocess-exit detection
    contract that ``pythonProcess.on('exit')`` in start-python.ts
    relies on. A full Electron-side behavioral test (spawning the
    actual Electron main process + Python sidecar together) is
    deferred — it requires a running Electron app and is too heavy
    for unit-test CI. This test provides the behavioral coverage at
    the subprocess level.
    """

    def test_parent_detects_sigkilled_sidecar_within_bounded_time(self, tmp_path):
        """GT-40: When a sidecar subprocess is SIGKILLed, the parent's
        ``Popen.poll()`` must return the (negative) signal within a
        bounded time (≤ 2s on Linux). This is the contract
        ``pythonProcess.on('exit')`` relies on — if the OS took
        unbounded time to deliver the exit signal, the restart/quit
        path would never fire.
        """
        import socket
        import subprocess
        import sys
        import time

        # Find a free port for the "sidecar" to bind.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        # Spawn a REAL Python subprocess that binds the port and waits
        # for SIGKILL. This mimics the voice-typer sidecar's IPC
        # socket bind. We use ``-c`` so the test is self-contained
        # (no fixture file needed).
        sidecar_script = (
            "import socket, signal, time\n"
            f"srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"srv.bind(('127.0.0.1', {port}))\n"
            "srv.listen(1)\n"
            "# Write a readiness marker so the parent knows we're bound.\n"
            "import sys; sys.stdout.write('READY\\n'); sys.stdout.flush()\n"
            "# Block forever — wait for SIGKILL.\n"
            "signal.pause()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", sidecar_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Wait for the sidecar to bind the port (readiness marker).
            deadline = time.time() + 5.0
            ready = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    # Sidecar exited prematurely — fail with stderr.
                    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                    pytest.fail(f"GT-40: sidecar exited prematurely before binding port. stderr: {stderr}")
                # Check if the sidecar wrote READY.
                if proc.stdout:
                    line = proc.stdout.readline()
                    if line == b"READY\n":
                        ready = True
                        break
                time.sleep(0.05)
            assert ready, "GT-40: sidecar did not signal readiness within 5s — cannot proceed with crash-detection test"

            # Verify the port is actually bound (the sidecar is
            # listening, mimicking the real IPC server).
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(1.0)
            try:
                test_sock.connect(("127.0.0.1", port))
            except (TimeoutError, ConnectionRefusedError, OSError) as e:
                pytest.fail(f"GT-40: sidecar did not bind port {port} — crash-detection test cannot proceed: {e}")
            finally:
                test_sock.close()

            # SIGKILL the sidecar (mimics a hard crash).
            proc.kill()
            # Poll for exit detection within a bounded time. The
            # contract: Popen.poll() must return the (negative) signal
            # code within 2s on Linux. If it returns None forever,
            # the parent would never detect the crash.
            deadline = time.time() + 2.0
            exit_code = None
            while time.time() < deadline:
                exit_code = proc.poll()
                if exit_code is not None:
                    break
                time.sleep(0.02)

            assert exit_code is not None, (
                "GT-40: parent did not detect sidecar exit within 2s "
                "of SIGKILL — the restart/quit path would never fire. "
                "This indicates a regression in subprocess exit detection."
            )
            # SIGKILL = signal 9; Popen.poll() returns -9.
            assert exit_code == -9, (
                f"GT-40: expected exit code -9 (SIGKILL), got {exit_code}. "
                f"The sidecar was killed by a different signal — "
                f"crash-detection contract is unexpected."
            )
        finally:
            # Reap the subprocess to avoid zombie processes.
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            with contextlib.suppress(Exception):
                proc.stdout.close() if proc.stdout else None
            with contextlib.suppress(Exception):
                proc.stderr.close() if proc.stderr else None
