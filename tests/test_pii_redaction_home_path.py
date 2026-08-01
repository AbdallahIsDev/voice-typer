"""Regression test: home-directory path leak in ``PIIRedactionFilter``.

Background
----------
``PIIRedactionFilter._redact_text`` (in ``voice_typer/server/security.py``)
previously matched structured PII (email / phone / SSN / CC / IBAN),
API-secret patterns, and URL-embedded credentials — but did NOT call
:func:`voice_typer.server._secrets._redact_home_path`.  Filesystem paths
containing the user's home directory (``/home/alice/…``,
``/Users/alice/…``, ``C:\\Users\\alice\\…``) therefore leaked the OS
username to ``voice-typer.log``.

The fix calls ``_redact_home_path`` at the top of ``_redact_text``,
BEFORE the fast-path trigger check.  This is critical because a bare
path like ``/home/alice/.voice-typer/foo.log`` contains none of the
fast-path triggers (no ``@``, ``+``, 3+ consecutive digits, ``Bearer``,
``Token``, ``sk-``, ``key=``, 20+ char token) — the trigger scan would
otherwise return the input unchanged and the username would leak.

The ``_redact_home_path`` helper is the same one already used by
``diagnostics_export.py`` for the diagnostic-bundle path fields; this
test ensures the log-filter path now shares that protection.

These tests cover:
  * Linux-style home path in a log message (``/home/testuser/…``).
  * Windows-style home path in a log message (``C:\\Users\\testuser\\…``).
  * Home path embedded in a formatted traceback (``record.exc_text``).
"""

from __future__ import annotations

import logging
import sys

import pytest
from voice_typer.server.security import PIIRedactionFilter


def _make_record(msg: str, level: int = logging.INFO, exc_info=None) -> logging.LogRecord:
    """Build a minimal ``LogRecord`` for filter testing."""
    return logging.LogRecord("test", level, "", 0, msg, (), exc_info)


# ── Linux-style home path ──────────────────────────────────────────────


def test_home_path_linux_style_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Linux-style home path in a log message must not leak the username.

    ``os.path.expanduser("~")`` respects the ``HOME`` env var on POSIX,
    so setting ``HOME=/home/testuser`` makes ``_redact_home_path`` treat
    that as the home prefix and replace it with ``~``.
    """
    monkeypatch.setenv("HOME", "/home/testuser")
    record = _make_record("Opening log file: /home/testuser/.voice-typer/foo.log")
    PIIRedactionFilter().filter(record)
    assert "testuser" not in record.msg, f"OS username leaked via Linux home path in log message: {record.msg!r}"
    assert "~" in record.msg, f"Home prefix was not replaced with '~': {record.msg!r}"


# ── Windows-style home path ────────────────────────────────────────────


def test_home_path_windows_style_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Windows-style home path in a log message must not leak the username.

    On POSIX, ``os.path.expanduser("~")`` honours the ``HOME`` env var,
    so setting it to a Windows-style path simulates the Windows home
    directory layout.  The ``_redact_home_path`` logic is string-prefix
    driven (it normalises both the candidate path and the home dir, then
    compares prefixes), so this exercises the same code path that runs
    natively on Windows.  On Windows itself, ``HOME`` is also consulted
    by ``expanduser`` (alongside ``USERPROFILE``), so the test is
    portable.
    """
    monkeypatch.setenv("HOME", "C:\\Users\\testuser")
    record = _make_record("Opening log file: C:\\Users\\testuser\\.voice-typer\\foo.log")
    PIIRedactionFilter().filter(record)
    assert "testuser" not in record.msg, f"OS username leaked via Windows home path in log message: {record.msg!r}"
    assert "~" in record.msg, f"Home prefix was not replaced with '~': {record.msg!r}"


# ── Home path in a formatted traceback ─────────────────────────────────


def test_home_path_in_traceback_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A home path embedded in a formatted traceback must not leak the username.

    ``PIIRedactionFilter.filter`` pre-formats ``record.exc_info`` into a
    string and caches the redacted result on ``record.exc_text``.  The
    home-path redaction must apply to that string too — otherwise an
    exception whose message or traceback frames reference a home path
    (e.g. ``FileNotFoundError("/home/testuser/.voice-typer/foo.log")``)
    would leak the username via the traceback block appended to the log
    record.
    """
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


# ── No false-positive: non-home path is untouched ──────────────────────


def test_non_home_path_not_mangled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that is NOT under the home dir must pass through unchanged.

    Guards against an over-broad implementation that might redact any
    path-like string.  ``/var/log/voice-typer.log`` does not start with
    the home prefix (``/home/testuser``), so ``_redact_home_path`` must
    return it verbatim.
    """
    monkeypatch.setenv("HOME", "/home/testuser")
    original = "System log at /var/log/voice-typer.log"
    record = _make_record(original)
    PIIRedactionFilter().filter(record)
    assert "/var/log/voice-typer.log" in record.msg, f"Non-home path was incorrectly redacted: {record.msg!r}"
