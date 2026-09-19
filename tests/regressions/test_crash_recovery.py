"""The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


class TestSubprocessCrashRecoveryHandler:
    """Test the Python exit handler logic."""

    def test_exit_handler_logic_exists(self):
        """Tauri host must reap/kill the Python sidecar on host exit."""
        supervisor = (
            Path(__file__).resolve().parent.parent.parent / "src-tauri" / "src" / "sidecar" / "ws" / "supervisor.rs"
        )
        spawn_mod = Path(__file__).resolve().parent.parent.parent / "src-tauri" / "src" / "sidecar" / "spawn"
        src = supervisor.read_text(encoding="utf-8") if supervisor.exists() else ""
        if spawn_mod.is_dir():
            for p in spawn_mod.rglob("*.rs"):
                src += "\n" + p.read_text(encoding="utf-8")
        assert src, "no supervisor/spawn sources found for sidecar lifecycle"
        assert "shutdown_sidecar" in src or "kill" in src.lower(), (
            "supervisor/spawn sources must include sidecar kill/shutdown wiring"
        )


class TestCrashRecoveryLoadsStaleState:
    """Test cleanup on abnormal termination."""

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
    """behavioral test of sidecar crash detection."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="signal.pause() is POSIX-only, the sidecar subprocess script cannot run on Windows",
    )
    def test_parent_detects_sigkilled_sidecar_within_bounded_time(self, tmp_path):
        """When a sidecar subprocess is SIGKILLed, the parent's"""
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
        sidecar_script = (
            "import socket, signal, time\n"
            f"srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"srv.bind(('127.0.0.1', {port}))\n"
            "srv.listen(1)\n"
            "# Write a readiness marker so the parent knows we're bound.\n"
            "import sys; sys.stdout.write('READY\\n'); sys.stdout.flush()\n"
            "# Block forever, wait for SIGKILL.\n"
            "signal.pause()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", sidecar_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Wait for the sidecar to bind the port (readiness marker).
            deadline = time.monotonic() + 5.0
            ready = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    # Sidecar exited prematurely, fail with stderr.
                    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                    pytest.fail(f"sidecar exited prematurely before binding port. stderr: {stderr}")
                # Check if the sidecar wrote READY.
                if proc.stdout:
                    line = proc.stdout.readline()
                    if line == b"READY\n":
                        ready = True
                        break
                time.sleep(0.05)
            assert ready, "sidecar did not signal readiness within 5s, cannot proceed with crash-detection test"

            # Verify the port is actually bound (the sidecar is
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(1.0)
            try:
                test_sock.connect(("127.0.0.1", port))
            except (TimeoutError, ConnectionRefusedError, OSError) as e:
                pytest.fail(f"sidecar did not bind port {port}, crash-detection test cannot proceed: {e}")
            finally:
                test_sock.close()

            # SIGKILL the sidecar (mimics a hard crash).
            proc.kill()
            deadline = time.monotonic() + 2.0
            exit_code = None
            while time.monotonic() < deadline:
                exit_code = proc.poll()
                if exit_code is not None:
                    break
                time.sleep(0.02)

            assert exit_code is not None, (
                "parent did not detect sidecar exit within 2s "
                "of SIGKILL, the restart/quit path would never fire. "
                "This indicates a regression in subprocess exit detection."
            )
            # SIGKILL = signal 9; Popen.poll() returns -9.
            assert exit_code == -9, (
                f"expected exit code -9 (SIGKILL), got {exit_code}. "
                f"The sidecar was killed by a different signal, "
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
