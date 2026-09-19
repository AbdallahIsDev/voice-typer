"""Pinning tests: PII redaction covers log.exception tracebacks"""

from __future__ import annotations

import io
import logging
import os

import pytest
from voice_typer.server.log.formatters import _FileFormatter
from voice_typer.server.security.redaction import PIIRedactionFilter


@pytest.fixture()
def _fake_home(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``~`` at a deterministic fake home so the home-path scrub"""
    home = r"C:\Users\bplogtest" if os.name == "nt" else "/home/bplogtest"
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    return home


class TestExceptionTracebackRedactionEndToEnd:
    """Pins the mitigation: tracebacks from ``log.exception``"""

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
        """Full pipeline: handler(filter) + production file formatter —"""
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
        assert "Traceback (most recent call last):" in emitted, emitted
        assert "ValueError:" in emitted, emitted
