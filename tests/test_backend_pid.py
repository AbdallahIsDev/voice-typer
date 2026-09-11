"""Tests for the runtime-neutral backend PID-file leaf module.

Covers the three helpers extracted from the single-instance subsystem
(the symbols that stay LIVE in Tauri mode):

- ``_backend_pid_file``, path resolution through the owning
  ``voice_typer.server.config`` module (honors
  ``voice_typer.server.config._config_dir`` monkeypatching)
- ``_is_pid_alive``, PID liveness contract on POSIX
- ``_clear_backend_pid_file``, best-effort removal (idempotent)
"""

from __future__ import annotations

import os

import pytest
from voice_typer.server import backend_pid


@pytest.fixture
def _tmp_config_dir(tmp_path, monkeypatch):
    """Redirect the owning config module's ``_config_dir`` to a tmp dir."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestBackendPidFile:
    """``_backend_pid_file`` path resolution contract."""

    def test_path_is_run_subdir_of_config_dir(self, _tmp_config_dir):
        pid_file = backend_pid._backend_pid_file()
        assert pid_file == _tmp_config_dir / "run" / "backend.pid"
        assert pid_file.parent == _tmp_config_dir / "run"

    def test_resolves_through_owning_config_module(self, _tmp_config_dir, monkeypatch):
        """Call-time resolution through ``voice_typer.server.config``:
        re-patching the owning module's ``_config_dir`` mid-flight is
        honored by the next call (C-ARCH-2 owning-module contract)."""
        other = _tmp_config_dir / "other"
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: other)
        assert backend_pid._backend_pid_file() == other / "run" / "backend.pid"


class TestIsPidAlive:
    """``_is_pid_alive`` liveness contract (POSIX branch in this sandbox)."""

    def test_own_pid_is_alive(self):
        assert backend_pid._is_pid_alive(os.getpid()) is True

    def test_nonpositive_pid_is_dead(self):
        assert backend_pid._is_pid_alive(0) is False
        assert backend_pid._is_pid_alive(-1) is False

    def test_reaped_pid_is_dead(self):
        """A short-lived subprocess that has been reaped reports dead.

        Uses ``subprocess.run`` (not ``os.fork``, fork in the
        multi-threaded pytest process trips a DeprecationWarning). The
        child is fully reaped by ``run`` returning, so its PID slot is
        free; PID recycling within microseconds is the accepted
        theoretical hazard shared by every PID-liveness probe.
        """
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-c", "import os; print(os.getpid())"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        child_pid = int(proc.stdout.strip())
        assert child_pid != os.getpid()
        assert backend_pid._is_pid_alive(child_pid) is False


class TestClearBackendPidFile:
    """``_clear_backend_pid_file`` best-effort removal contract."""

    def test_removes_existing_file(self, _tmp_config_dir):
        pid_file = backend_pid._backend_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{os.getpid()}\n")
        backend_pid._clear_backend_pid_file()
        assert not pid_file.exists()

    def test_missing_file_is_silent_noop(self, _tmp_config_dir):
        # No parent dir, no file, must not raise.
        backend_pid._clear_backend_pid_file()
        assert not backend_pid._backend_pid_file().exists()
