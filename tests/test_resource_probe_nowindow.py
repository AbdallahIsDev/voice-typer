"""Windows console-flash regression tests for the ``nvidia-smi`` probe."""

from __future__ import annotations

import os
import subprocess

from voice_typer.server import resource_probe
from voice_typer.server.resource_probe import _probe_gpu_memory_via_nvidia_smi


def _install_no_pynvml(monkeypatch) -> None:
    """Force the ``nvidia-smi`` subprocess path (skip the in-process one)."""
    monkeypatch.setattr(
        resource_probe,
        "_probe_gpu_memory_via_pynvml",
        lambda: (None, None),
    )


def _install_capturing_run(monkeypatch, *, stdout_text: str = "8192, 5120\n") -> dict:
    """Replace ``subprocess.run`` with a fake that records its kwargs."""
    captured: dict = {}

    # Built via type() (not a class statement) so the stdout payload
    fake_completed_cls = type("_FakeCompleted", (), {"returncode": 0, "stdout": stdout_text})

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return fake_completed_cls()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return captured


class TestNvidiaSmiNoWindow:
    def test_passes_create_no_window_on_windows(self, monkeypatch):
        """On Windows the probe hides the console window and still parses."""
        _install_no_pynvml(monkeypatch)
        captured = _install_capturing_run(monkeypatch)
        monkeypatch.setattr(os, "name", "nt")

        assert _probe_gpu_memory_via_nvidia_smi() == (8192.0, 5120.0)

        kwargs = captured["kwargs"]
        assert "creationflags" in kwargs, "Windows nvidia-smi spawn must hide its console window"
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        assert kwargs["creationflags"] != 0
        # Pre-existing call shape is preserved.
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 10
        assert kwargs["check"] is False

    def test_passes_no_creationflags_on_posix(self, monkeypatch):
        """On Linux/macOS no ``creationflags`` kwarg is passed at all."""
        _install_no_pynvml(monkeypatch)
        captured = _install_capturing_run(monkeypatch)
        monkeypatch.setattr(os, "name", "posix")

        assert _probe_gpu_memory_via_nvidia_smi() == (8192.0, 5120.0)

        assert "creationflags" not in captured["kwargs"], (
            "POSIX nvidia-smi spawn must not receive creationflags (no behavior change off Windows)"
        )
