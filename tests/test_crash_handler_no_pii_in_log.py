"""YJ-19 regression: the crash excepthook's CRITICAL log line must NOT
contain ``exc_value`` text (which can embed dictated speech / PII).

Pre-fix, ``_crash_excepthook`` logged ``"[CRASH] Unhandled Python
exception: %s: %s" % (exc_type.__name__, _redacted_value)``. Even though
``_redacted_value`` was passed through ``redact_secret(redact_pii(...))``,
``PIIRedactionFilter`` only catches STRUCTURED PII patterns (email,
phone, IBAN, SSN, CC) and API-key-shaped tokens — plain user speech like
``"my name is John Smith"`` passes through verbatim into the rotating
log file.

Post-fix, the CRITICAL line logs ONLY ``exc_type.__name__``. The redacted
``exc_value`` is persisted ONLY to the marker file (already 0o600).

This test triggers the excepthook with ``ValueError("my name is John
Smith")`` and asserts the CRITICAL log line contains "ValueError" but NOT
"John Smith".
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest
from voice_typer.server import crash_handler


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state():
    """Reset module-level globals between tests (mirrors
    ``tests/test_crash_handler.py``'s autouse fixture)."""
    keys = (
        "_crash_file_path",
        "_PID",
        "_handler_handle",
        "_kernel32",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    crash_handler._crash_file_path = ""
    crash_handler._PID = 0
    crash_handler._handler_handle = None
    crash_handler._kernel32 = None
    crash_handler._crash_written = False
    crash_handler._python_crash_dir = None
    crash_handler._crash_header_bytes = b""
    yield
    for k, v in saved.items():
        if v is _UNSET:
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)


_UNSET = object()


@pytest.fixture
def restore_excepthook():
    """Snapshot ``sys.excepthook`` so a test can restore it."""
    saved = sys.excepthook
    saved_orig_attr = crash_handler._original_excepthook
    yield
    sys.excepthook = saved
    crash_handler._original_excepthook = saved_orig_attr


# ── Tests ────────────────────────────────────────────────────────────────


class TestCrashExcepthookNoPIIInLog:
    """YJ-19: the CRITICAL log line must contain ONLY ``exc_type.__name__``,
    never ``exc_value`` text (which can embed dictated speech)."""

    def test_critical_log_contains_only_exc_type_name(self, restore_excepthook, caplog):
        """Trigger the excepthook with ``ValueError("my name is John
        Smith")`` and assert the CRITICAL log line contains "ValueError"
        but NOT "John Smith" (the PII in exc_value).
        """
        # Install the crash excepthook.
        crash_handler.install_python_excepthook()
        assert sys.excepthook is crash_handler._crash_excepthook

        try:
            raise ValueError("my name is John Smith")
        except ValueError as exc:
            with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                sys.excepthook(type(exc), exc, exc.__traceback__)

        # The CRITICAL log line MUST be emitted.
        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert critical_records, "excepthook must emit at least one CRITICAL log record"

        # Find the "Unhandled Python exception" record ( main line).
        unhandled_records = [r for r in critical_records if "Unhandled Python exception" in r.message]
        assert unhandled_records, "excepthook must emit a CRITICAL log line containing 'Unhandled Python exception'"

        # the CRITICAL log line MUST contain "ValueError"
        # (exc_type.__name__).
        combined_critical_text = " ".join(r.getMessage() for r in critical_records)
        assert "ValueError" in combined_critical_text, (
            f"YJ-19: CRITICAL log line must contain exc_type.__name__ ('ValueError'); got: {combined_critical_text!r}"
        )

        # the CRITICAL log line MUST NOT contain "John Smith"
        # (the PII embedded in exc_value). This is the key assertion —
        # pre-fix, the redacted_value was logged and PII like names
        # passed through verbatim because PIIRedactionFilter only
        # catches STRUCTURED PII patterns (email/phone/IBAN/SSN/CC).
        assert "John Smith" not in combined_critical_text, (
            "YJ-19: CRITICAL log line must NOT contain exc_value text "
            "('John Smith' is PII embedded in ValueError's str()); "
            f"got: {combined_critical_text!r}"
        )
        # Also assert against the lowercased form for robustness.
        assert "john smith" not in combined_critical_text.lower(), (
            "YJ-19: CRITICAL log line must NOT contain exc_value text "
            "(case-insensitive check); "
            f"got: {combined_critical_text!r}"
        )

    def test_critical_log_uses_exc_type_name_not_value(self, restore_excepthook, caplog):
        """Targeted assertion: the CRITICAL 'Unhandled Python exception'
        record's message format is ``"[CRASH] Unhandled Python exception:
        %s" % exc_type.__name__`` — NOT the old ``"%s: %s" %
        (exc_type.__name__, _redacted_value)`` format.
        """
        crash_handler.install_python_excepthook()

        try:
            raise RuntimeError("secret api key abc123 and SSN 123-45-6789")
        except RuntimeError as exc:
            with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                sys.excepthook(type(exc), exc, exc.__traceback__)

        unhandled_records = [
            r for r in caplog.records if r.levelno >= logging.CRITICAL and "Unhandled Python exception" in r.message
        ]
        assert unhandled_records

        msg = unhandled_records[0].getMessage()

        # + : the main CRITICAL line must be exactly
        # "[CRASH] Unhandled Python exception: RuntimeError" (no ": <value>"
        # suffix). We use ``startswith`` to be robust against logger
        # prefix differences.
        assert msg.startswith("[CRASH] Unhandled Python exception: RuntimeError"), (
            f"YJ-19: CRITICAL log line must start with "
            f"'[CRASH] Unhandled Python exception: RuntimeError' (only "
            f"exc_type.__name__); got: {msg!r}"
        )
        # The secret / SSN embedded in exc_value must NOT appear.
        assert "abc123" not in msg, f"YJ-19: exc_value's secret must NOT appear in CRITICAL log; got: {msg!r}"
        assert "123-45-6789" not in msg, f"YJ-19: exc_value's SSN must NOT appear in CRITICAL log; got: {msg!r}"

    def test_redacted_traceback_emitted_unconditionally(self, restore_excepthook, caplog):
        """YJ-14: the PII-safe redacted traceback must be emitted
        UNCONDITIONALLY — not gated on ``VOICE_TYPER_DEBUG=1``.

        ``_format_redacted_traceback`` strips all source-line text and
        argument values, emitting only file basename + line number +
        function name. That's PII-safe, so it can ship to the rotating
        log without operator opt-in.
        """
        crash_handler.install_python_excepthook()

        try:
            raise ValueError("boom")
        except ValueError as exc:
            with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                sys.excepthook(type(exc), exc, exc.__traceback__)

        # the redacted traceback CRITICAL record must be present
        # (even though VOICE_TYPER_DEBUG is NOT set in the env).
        redacted_tb_records = [
            r for r in caplog.records if r.levelno >= logging.CRITICAL and "Redacted traceback" in r.getMessage()
        ]
        assert redacted_tb_records, (
            "YJ-14: redacted traceback must be emitted UNCONDITIONALLY (not gated on VOICE_TYPER_DEBUG=1)"
        )


class TestCrashDumpFileContentRedacted:
    """HU-38: the ON-DISK crash marker file (``python_crash.<PID>.txt``)
    must not contain raw PII embedded in the exception value.

    The existing YJ-19 tests above verify only the caplog LOG output is
    redacted. This class drives the REAL excepthook (which writes the
    marker via ``_write_crash_marker``) and reads the file content back.

    The crash archive is high-risk: it is retained ~30 days, included
    verbatim in ``export_gdpr_bundle``, and uploaded to support tickets
    via ``export_diagnostics``. A regression that logs the raw
    ``exc_value`` (e.g. ``'Last transcription: <text>'`` for triage)
    would silently leak dictated speech + PII.

    ``crash_diagnostics.<PID>.txt`` is written by the Windows-native
    VEH handler (not the Python excepthook), so it cannot be driven
    portably — but the ``_redact_exc_value`` pipeline it shares with
    the marker writer is exercised here end-to-end.
    """

    def _trigger_crash(self, tmp_path: Path) -> Path:
        """Install the excepthook, raise a PII-bearing exception through
        it, and return the written marker path."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler.install_python_excepthook()
        assert sys.excepthook is crash_handler._crash_excepthook
        try:
            raise ValueError("contact john.doe@example.com for biopsy")
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists(), f"crash marker must be written to {marker}"
        return marker

    def test_python_crash_marker_has_no_raw_pii(self, tmp_path: Path, restore_excepthook):
        """The marker file content must NOT contain the raw PII string
        from the exception value."""
        marker = self._trigger_crash(tmp_path)
        content = marker.read_text(encoding="utf-8")
        assert "john.doe@example.com" not in content, (
            f"raw PII must not appear in crash marker; got: {content!r}"
        )
        assert "john.doe" not in content, f"partial PII (email local part) must not appear; got: {content!r}"
        # The redacted form (or a safe sentinel) must appear instead.
        assert "[EMAIL]" in content or "<redacted:" in content, (
            f"crash marker must carry the redacted value; got: {content!r}"
        )

    def test_python_crash_marker_redacts_secret_shaped_value(self, tmp_path: Path, restore_excepthook):
        """API-key-shaped secrets embedded in the exception value are
        masked via ``redact_secret(aggressive=True)`` before persisting."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler.install_python_excepthook()
        try:
            raise RuntimeError("api key sk-abcdefghijklmnopqrstuvwx leaked")
        except RuntimeError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        assert "sk-abcdefghijklmnopqrstuvwx" not in content, (
            f"secret must not appear in crash marker; got: {content!r}"
        )
        assert "***" in content or "<redacted:" in content, (
            f"crash marker must mask the secret; got: {content!r}"
        )

    def test_marker_keeps_exc_type_for_deduplication(self, tmp_path: Path, restore_excepthook):
        """The marker still carries the exception TYPE name (safe) so
        crash dedup / triage works even though the value is redacted."""
        marker = self._trigger_crash(tmp_path)
        content = marker.read_text(encoding="utf-8")
        assert "exc_type=ValueError" in content, f"exc_type must be preserved; got: {content!r}"
