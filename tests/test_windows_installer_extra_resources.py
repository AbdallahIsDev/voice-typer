"""Cutover pin: the Windows Electron installer packaging is GONE.

Historical context (pre-2026-09-17): these tests pinned the Electron
NSIS packaging chain (electron-builder.yml extraResources + build.yml
``build-windows`` job + Electron main ``pythonArgs()``). That entire
path was removed with the Electron host on 2026-09-17.

This module now asserts the cutover: no electron-builder.yml, no
Electron main process, no ``build-windows`` electron-builder job in
build.yml. Installer production is tauri-build.yml +
tauri-windows-build.yml only.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ELECTRON_BUILDER_YML = REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml"
BUILD_YML = REPO_ROOT / ".github" / "workflows" / "build.yml"
ELECTRON_MAIN = REPO_ROOT / "voice_typer" / "client" / "src" / "main"


def test_electron_builder_yml_removed() -> None:
    assert not ELECTRON_BUILDER_YML.is_file(), "electron-builder.yml must not exist (Electron packaging retired)."


def test_electron_main_process_removed() -> None:
    assert not ELECTRON_MAIN.exists(), "client/src/main must not exist (Electron host removed)."


def test_build_yml_has_no_electron_packaging_job() -> None:
    config = yaml.safe_load(BUILD_YML.read_text(encoding="utf-8"))
    jobs = config.get("jobs") or {}
    for job in ("build-windows", "build-macos", "build-linux", "build-macos-universal"):
        assert job not in jobs, f"build.yml must not define the Electron packaging job `{job}`."


def test_build_yml_does_not_invoke_electron_builder() -> None:
    text = BUILD_YML.read_text(encoding="utf-8")
    assert "electron-builder --" not in text, "build.yml must not invoke electron-builder (Electron packaging retired)."


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
