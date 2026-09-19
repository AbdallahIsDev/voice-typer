"""Regression test: home-directory path leak in ``PIIRedactionFilter``."""

from __future__ import annotations

import logging
import sys

import pytest
from voice_typer.server.security import PIIRedactionFilter


def _make_record(msg: str, level: int = logging.INFO, exc_info=None) -> logging.LogRecord:
    """Build a minimal ``LogRecord`` for filter testing."""
    return logging.LogRecord("test", level, "", 0, msg, (), exc_info)


def test_home_path_linux_style_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Linux-style home path in a log message must not leak the username."""
    monkeypatch.setenv("HOME", "/home/testuser")
    record = _make_record("Opening log file: /home/testuser/.voice-typer/foo.log")
    PIIRedactionFilter().filter(record)
    assert "testuser" not in record.msg, f"OS username leaked via Linux home path in log message: {record.msg!r}"
    assert "~" in record.msg, f"Home prefix was not replaced with '~': {record.msg!r}"


def test_home_path_windows_style_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Windows-style home path in a log message must not leak the username."""
    monkeypatch.setenv("HOME", "C:\\Users\\testuser")
    record = _make_record("Opening log file: C:\\Users\\testuser\\.voice-typer\\foo.log")
    PIIRedactionFilter().filter(record)
    assert "testuser" not in record.msg, f"OS username leaked via Windows home path in log message: {record.msg!r}"
    assert "~" in record.msg, f"Home prefix was not replaced with '~': {record.msg!r}"


def test_home_path_in_traceback_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A home path embedded in a formatted traceback must not leak the username."""
    monkeypatch.setenv("HOME", "/home/testuser")
    try:
        raise FileNotFoundError("/home/testuser/.voice-typer/foo.log")
    except FileNotFoundError:
        exc_info = sys.exc_info()
    record = _make_record("Failed to open config file", level=logging.ERROR, exc_info=exc_info)
    PIIRedactionFilter().filter(record)
    assert record.exc_text is not None, (
        "PIIRedactionFilter did not populate record.exc_text for an exc_info-bearing record"
    )
    assert "testuser" not in record.exc_text, f"OS username leaked via home path in traceback: {record.exc_text!r}"


def test_non_home_path_not_mangled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that is NOT under the home dir must pass through unchanged."""
    monkeypatch.setenv("HOME", "/home/testuser")
    original = "System log at /var/log/voice-typer.log"
    record = _make_record(original)
    PIIRedactionFilter().filter(record)
    assert "/var/log/voice-typer.log" in record.msg, f"Non-home path was incorrectly redacted: {record.msg!r}"
