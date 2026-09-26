"""AUTOSTART-CMD-VALIDATE backport: macOS/Linux autostart path validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from voice_typer.server.server_platform.autostart_linux import (
    _desktop_exec_path_exists,
)
from voice_typer.server.server_platform.autostart_macos import (
    _plist_program_arguments_exist,
)


def _write_plist(path: Path, program_args: list[str] | None) -> None:
    """Write a minimal LaunchAgent plist with optional ProgramArguments."""
    args_xml = ""
    if program_args is not None:
        args_xml = (
            "<key>ProgramArguments</key><array>" + "".join(f"<string>{a}</string>" for a in program_args) + "</array>"
        )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<plist version="1.0"><dict>'
        "<key>Label</key><string>com.Lausu</string>"
        f"{args_xml}"
        "</dict></plist>",
        encoding="utf-8",
    )


def test_plist_missing_program_path_reports_stale(tmp_path: Path) -> None:
    """A plist pointing at a deleted interpreter must be flagged stale."""
    plist = tmp_path / "com.Lausu.plist"
    _write_plist(plist, ["/nonexistent/venv/bin/python", "/nonexistent/launcher.py"])
    assert _plist_program_arguments_exist(plist) is False


def test_plist_existing_program_paths_valid(tmp_path: Path) -> None:
    """A plist pointing at real paths must remain valid."""
    real_file = tmp_path / "real-program"
    real_file.write_text("#!/bin/sh\n", encoding="utf-8")
    plist = tmp_path / "com.Lausu.plist"
    _write_plist(plist, [sys.executable, str(real_file)])
    assert _plist_program_arguments_exist(plist) is True


def test_plist_no_program_arguments_conservative_valid(tmp_path: Path) -> None:
    """A plist with no ProgramArguments is unparseable-ambiguous, the"""
    plist = tmp_path / "com.Lausu.plist"
    _write_plist(plist, None)
    assert _plist_program_arguments_exist(plist) is True


def test_plist_malformed_conservative_valid(tmp_path: Path) -> None:
    """A corrupt (non-XML) plist must be treated as valid, not stale —"""
    plist = tmp_path / "com.Lausu.plist"
    plist.write_text("not xml at all", encoding="utf-8")
    assert _plist_program_arguments_exist(plist) is True


def test_plist_missing_file_conservative_valid(tmp_path: Path) -> None:
    """A nonexistent plist path is unreadable, conservatively valid"""
    assert _plist_program_arguments_exist(tmp_path / "nope.plist") is True


def _write_desktop(path: Path, exec_line: str | None) -> None:
    body = "[Desktop Entry]\nType=Application\nName=Lausu\n"
    if exec_line is not None:
        body += f"Exec={exec_line}\n"
    path.write_text(body, encoding="utf-8")


def test_desktop_missing_exec_program_reports_stale(tmp_path: Path) -> None:
    """A .desktop whose Exec= points at a deleted interpreter must be"""
    desktop = tmp_path / "lausu.desktop"
    _write_desktop(desktop, "/nonexistent/venv/bin/python /nonexistent/launcher.py --hidden")
    assert _desktop_exec_path_exists(desktop) is False


def test_desktop_existing_exec_program_valid(tmp_path: Path) -> None:
    """A .desktop whose Exec= points at a real program must be valid."""
    real_file = tmp_path / "real-program"
    real_file.write_text("#!/bin/sh\n", encoding="utf-8")
    desktop = tmp_path / "lausu.desktop"
    _write_desktop(desktop, f"{real_file} --hidden")
    assert _desktop_exec_path_exists(desktop) is True


def test_desktop_no_exec_line_conservative_valid(tmp_path: Path) -> None:
    """A .desktop without an Exec= line is ambiguous, conservatively"""
    desktop = tmp_path / "lausu.desktop"
    _write_desktop(desktop, None)
    assert _desktop_exec_path_exists(desktop) is True


def test_desktop_malformed_exec_conservative_valid(tmp_path: Path) -> None:
    """An unparseable Exec= line (unbalanced double-quote) must be"""
    desktop = tmp_path / "lausu.desktop"
    _write_desktop(desktop, '"unclosed quote')
    assert _desktop_exec_path_exists(desktop) is True


def test_desktop_missing_file_conservative_valid(tmp_path: Path) -> None:
    """A nonexistent .desktop path is unreadable, conservatively valid"""
    assert _desktop_exec_path_exists(tmp_path / "nope.desktop") is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sanity")
def test_windows_probe_runs_on_this_host() -> None:
    """Sanity: the validators import and run on the Windows host (they"""
    assert callable(_plist_program_arguments_exist)
    assert callable(_desktop_exec_path_exists)
