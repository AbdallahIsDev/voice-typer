"""Tests for SEC-009: PII redaction in logs."""
import logging


def test_pii_redaction_email():
    """Email addresses are redacted from log messages."""
    from voice_typer.server.app import _PIIRedactionFilter
    f = _PIIRedactionFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, 
                               "User test@example.com logged in", (), None)
    f.filter(record)
    assert "[EMAIL]" in record.msg
    assert "test@example.com" not in record.msg


def test_pii_redaction_phone():
    """Phone numbers are redacted from log messages."""
    from voice_typer.server.app import _PIIRedactionFilter
    f = _PIIRedactionFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0,
                               "Call 555-123-4567 now", (), None)
    f.filter(record)
    assert "[PHONE]" in record.msg
    assert "555-123-4567" not in record.msg


def test_pii_redaction_ssn():
    """SSN-like patterns are redacted from log messages."""
    from voice_typer.server.app import _PIIRedactionFilter
    f = _PIIRedactionFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0,
                               "SSN: 123-45-6789", (), None)
    f.filter(record)
    assert "[SSN]" in record.msg
    assert "123-45-6789" not in record.msg


def test_pii_redaction_no_false_positives():
    """Normal text passes through unchanged."""
    from voice_typer.server.app import _PIIRedactionFilter
    f = _PIIRedactionFilter()
    original = "The quick brown fox jumped over the lazy dog"
    record = logging.LogRecord("test", logging.INFO, "", 0, original, (), None)
    f.filter(record)
    assert record.msg == original
