"""YJ-18: PIIRedactionFilter should expose the redacted message via
``record.redacted_msg`` so downstream structured consumers (metrics
exporters, a future MemoryHandler ring buffer that re-emits to a
structured backend) can read the redacted version WITHOUT having to
re-format ``record.msg`` / ``record.args``.

Backward compat: the legacy ``record.msg = msg`` / ``record.args = ()``
mutation (SEC-009 behavior) is preserved so the existing text/JSON
formatters and tests continue to work unchanged. The new
``redacted_msg`` attribute is purely additive.
"""

import logging


def _make_record(msg: str, args: tuple = ()) -> logging.LogRecord:
    return logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        msg,
        args,
        None,
    )


def test_redacted_msg_is_set_after_filter():
    """``record.redacted_msg`` is set to the redacted message after the filter runs."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    record = _make_record("User test@example.com logged in")
    assert not hasattr(record, "redacted_msg"), "pre-condition: redacted_msg not set before filter"
    f.filter(record)
    assert hasattr(record, "redacted_msg"), "post-condition: redacted_msg must be set after filter"
    assert "[EMAIL]" in record.redacted_msg
    assert "test@example.com" not in record.redacted_msg


def test_redacted_msg_matches_record_msg_for_backward_compat():
    """``record.redacted_msg`` equals ``record.msg`` (the legacy mutation is preserved)."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    record = _make_record("Call 555-123-4567 now")
    f.filter(record)
    assert record.redacted_msg == record.msg
    assert "[PHONE]" in record.redacted_msg


def test_redacted_msg_set_for_non_pii_messages():
    """``record.redacted_msg`` is set (unchanged) for messages without PII."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    original = "The quick brown fox jumped over the lazy dog"
    record = _make_record(original)
    f.filter(record)
    assert record.redacted_msg == original
    assert record.msg == original


def test_redacted_msg_set_for_messages_with_args():
    """``record.redacted_msg`` captures the FORMATTED + redacted message (args interpolated)."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    # The getMessage() call interpolates args BEFORE redaction, so the
    # redacted_msg carries the interpolated + redacted text.
    record = _make_record("User %s logged in from %s", ("test@example.com", "+1-415-555-2671"))
    f.filter(record)
    assert "[EMAIL]" in record.redacted_msg
    assert "[PHONE]" in record.redacted_msg
    assert "test@example.com" not in record.redacted_msg
    assert "415-555-2671" not in record.redacted_msg


def test_redacted_msg_set_for_api_key_messages():
    """``record.redacted_msg`` captures the redacted API key."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
    record = _make_record(f"Using API key: {api_key}")
    f.filter(record)
    assert api_key not in record.redacted_msg
    assert "sk-abcdef" not in record.redacted_msg
    assert "***" in record.redacted_msg
