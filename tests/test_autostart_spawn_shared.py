"""Shared login-child spawn recipe + backend PID port record.

The five autostart spawn sites route ``Popen`` through
``autostart._spawn._spawn_login_child`` (unified try/except/finally
cleanup); the backend PID file carries a ``port=`` line once the IPC
server has bound (writer half of the MED-Y contract).
"""

from __future__ import annotations

import subprocess

import pytest
from voice_typer.server import single_instance as si_mod
from voice_typer.server.autostart import _spawn as spawn_mod, pid_file as pid_file_mod


class _FakeHandle:
    """Minimal file-handle stand-in tracking close() calls."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _kwargs(handle):
    return {"stdout": handle, "stderr": handle}


def test_popen_raise_still_closes_parent_handles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The built-Electron leak: Popen-raise must still close handles."""
    handle = _FakeHandle()

    def _boom(cmd, **kwargs):
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    child = spawn_mod._spawn_login_child(
        ["C:/bin/app.exe"],
        env={},
        spawn_kwargs=_kwargs(handle),
        describe="probe child",
    )
    assert child is None
    assert handle.closed, "parent log handles must close even when Popen raises"


def test_success_closes_parent_handles_and_returns_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success path keeps prior behavior: child returned, handles closed."""
    handle = _FakeHandle()

    class _FakeProc:
        pid = 4321

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: _FakeProc())
    child = spawn_mod._spawn_login_child(
        ["C:/bin/app.exe"],
        env={},
        spawn_kwargs=_kwargs(handle),
        describe="probe child",
    )
    assert isinstance(child, _FakeProc)
    assert handle.closed


def test_record_port_roundtrip(tmp_config_dir) -> None:
    """Record writes ``{pid}\\nport=``; the reader picks the port up."""
    si_mod._record_backend_ipc_port(19876)
    assert pid_file_mod._read_ipc_port_from_pid_file() == 19876


def test_record_port_rejects_out_of_range(tmp_config_dir) -> None:
    """Out-of-range ports are skipped — the file stays pid-only."""
    si_mod._record_backend_ipc_port(0)
    si_mod._record_backend_ipc_port(70000)
    assert pid_file_mod._read_ipc_port_from_pid_file() is None


def test_stale_reader_tolerates_port_line(tmp_config_dir) -> None:
    """The legacy first-line PID parse survives the ``port=`` line."""
    si_mod._record_backend_ipc_port(19876)
    # Current process is alive → not stale → None; the point is no raise.
    assert si_mod._read_stale_backend_pid() is None
