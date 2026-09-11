"""BP-127: shared autostart interpreter-resolution core.

``_prefer_pythonw`` + ``_probe_system_python`` (in
``server_platform.autostart``) own the recipe all three platform
registrars shared verbatim (pythonw preference → venv probe →
can-import check). These tests pin the shared core directly so a
future interpreter-handling fix is verified once, not per platform.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from voice_typer.server.server_platform import autostart as autostart_mod


def test_prefer_pythonw_uses_sibling_when_present(tmp_path: Path) -> None:
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_bytes(b"fake")
    assert autostart_mod._prefer_pythonw(str(tmp_path / "python.exe")) == str(pythonw)


def test_prefer_pythonw_keeps_input_when_absent(tmp_path: Path) -> None:
    interpreter = str(tmp_path / "python.exe")
    assert autostart_mod._prefer_pythonw(interpreter) == interpreter


def test_venv_guard_stays_in_callers_not_helper() -> None:
    """The ``sys.prefix`` venv guard is owned by the three registrars.

    The shared probe deliberately has no guard (it answers "is this
    candidate swappable?"). If a future cleanup drops a caller's guard,
    venv users would get swap warnings on every registration, pin the
    guard text in all three call sites.
    """
    import inspect

    for mod_name in (
        "voice_typer.server.server_platform.autostart",
        "voice_typer.server.server_platform.autostart_windows",
        "voice_typer.server.server_platform.autostart_macos",
    ):
        src = inspect.getsource(__import__(mod_name, fromlist=["x"]))
        assert "if sys.prefix != sys.base_prefix:" in src, f"{mod_name} must keep its venv guard around the probe call"


def test_probe_returns_none_when_no_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "base_prefix", sys.base_prefix)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert autostart_mod._probe_system_python("python3") is None


def test_probe_returns_none_when_import_check_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "base_prefix", sys.base_prefix)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/python3")
    monkeypatch.setattr(autostart_mod, "_system_python_can_import_launcher", lambda p: False)
    assert autostart_mod._probe_system_python("python3") is None


def test_probe_returns_candidate_when_import_check_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "base_prefix", sys.base_prefix)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/python3")
    monkeypatch.setattr(autostart_mod, "_system_python_can_import_launcher", lambda p: True)
    assert autostart_mod._probe_system_python("python3") == "/usr/bin/python3"
