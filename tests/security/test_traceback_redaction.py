"""Pinning tests: PII redaction covers log.exception tracebacks
end-to-end.

The finding flagged ``log.exception`` callsites as a potential
source-line/PII leak vector. Investigation (2026-09-02, Windows host)
verified the framework ALREADY mitigates this at the handler layer:
``PIIRedactionFilter.filter`` redacts BOTH the formatted message AND
the pre-formatted traceback (``record.exc_text``), covering
home-directory paths (current user), emails, phones, IBAN/SSN/CC,
API keys/bearer tokens, and URL credentials — in exception text,
source lines, and file paths alike.

These tests pin that mitigation end-to-end so a future filter or
formatter refactor cannot silently drop the traceback scrub:

1. ``PIIRedactionFilter`` redacts PII/secrets appearing in an
   exception message reached via ``log.exception`` (record-level).
2. The file-log pipeline (``logging.StreamHandler`` + the filter +
   the production ``_FileFormatter``) emits a redacted traceback —
   no username, email, or API key lands in the formatted output.
"""

from __future__ import annotations

import io
import logging
import os

import pytest
from voice_typer.server.log.formatters import _FileFormatter
from voice_typer.server.security.redaction import PIIRedactionFilter


@pytest.fixture()
def _fake_home(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``~`` at a deterministic fake home so the home-path scrub
    is asserted independently of the real username."""
    home = r"C:\Users\bplogtest" if os.name == "nt" else "/home/bplogtest"
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    return home


class TestExceptionTracebackRedactionEndToEnd:
    """Pins the mitigation: tracebacks from ``log.exception``
    paths carry NO unredacted PII/secrets by the time they are
    formatted for the log file."""

    def _make_record_with_exc(self, message: str) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.ap10",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Operation failed: %s",
            args=(message,),
            exc_info=None,
            func="test_func",
        )
        try:
            raise ValueError(message)
        except ValueError:
            record.exc_info = __import__("sys").exc_info()
        return record

    def test_filter_redacts_pii_in_exception_text(self, _fake_home: str) -> None:
        record = self._make_record_with_exc(f"config at {_fake_home}\\notes.txt for alice@example.com")
        flt = PIIRedactionFilter()
        assert flt.filter(record) is True
        exc_text = record.exc_text or ""
        assert "alice@example.com" not in exc_text, exc_text
        assert _fake_home not in exc_text, exc_text

    def test_formatted_output_carries_no_pii(self, _fake_home: str) -> None:
        """Full pipeline: handler(filter) + production file formatter —
        the emitted line + traceback must be PII-free."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(PIIRedactionFilter())
        handler.setFormatter(_FileFormatter("%(levelname)s  %(message)s"))
        logger = logging.getLogger("test.ap10.pipeline")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        try:
            try:
                raise ValueError(f"path {_fake_home}\\db.sqlite mail bob@example.com token sk-bplogtest123456789012")
            except ValueError:
                logger.exception("dictation pipeline crashed")
        finally:
            logger.handlers = []

        emitted = stream.getvalue()
        assert "bob@example.com" not in emitted, emitted
        assert _fake_home not in emitted, emitted
        assert "sk-bplogtest" not in emitted, emitted
        # traceback structure preserved (diagnosability intact)
        assert "Traceback (most recent call last):" in emitted, emitted
        assert "ValueError:" in emitted, emitted
