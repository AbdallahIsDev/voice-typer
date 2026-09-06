"""YJ-18: PIIRedactionFilter should expose the redacted message via
``record.redacted_msg`` so downstream structured consumers (metrics
exporters, a future MemoryHandler ring buffer that re-emits to a
structured backend) can read the redacted version WITHOUT having to
re-format ``record.msg`` / ``record.args``.

Backward compat: the legacy ``record.msg = msg`` / ``record.args = ()``
mutation (SEC-009 behavior) is preserved so the existing text/JSON
formatters and tests continue to work unchanged. The new
``redacted_msg`` attribute is purely additive.

Idempotence guard: the SAME ``PIIRedactionFilter`` instance is attached
to BOTH the file handler and the stderr handler (SEC-003), and Python's
logging fires handler filters once per handler on the SAME LogRecord.
The guard at the top of ``filter`` accepts an already-redacted record
(``redacted_msg`` set) WITHOUT re-running the scan — one full scan per
record instead of one per handler. The ``TestFilterIdempotence`` class
below pins that: the internal scan runs exactly once for a record
passing through the filter twice, and the second pass is a no-op that
still returns ``True``.
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


class TestFilterIdempotence:
    """A record passing through ``PIIRedactionFilter`` twice must be
    scanned exactly once.

    Regression guard for the double-handler attachment: the file and
    stderr handlers share ONE filter instance, so every record hits
    ``filter`` twice (once per handler). The idempotence guard at the
    top of ``filter`` short-circuits the second pass on the
    ``redacted_msg`` sentinel the first pass set.
    """

    def test_second_pass_does_not_rescan(self, monkeypatch):
        """The internal scan runs ONCE for a record filtered twice."""
        import voice_typer.server.security.redaction as redaction_module
        from voice_typer.server.security import PIIRedactionFilter

        scan_calls: list[str] = []
        real_redact_text = redaction_module._redact_text

        def counting_redact_text(text, *args, **kwargs):
            scan_calls.append(text)
            return real_redact_text(text, *args, **kwargs)

        monkeypatch.setattr(redaction_module, "_redact_text", counting_redact_text)

        f = PIIRedactionFilter()
        record = _make_record("User test@example.com paid 555-123-4567")
        assert f.filter(record) is True
        assert f.filter(record) is True  # second handler's pass

        assert len(scan_calls) == 1, (
            f"redaction scan must run exactly once per record (second pass "
            f"must short-circuit on the redacted_msg sentinel); got {len(scan_calls)} scans"
        )
        assert "[EMAIL]" in record.redacted_msg
        assert "[PHONE]" in record.redacted_msg

    def test_second_pass_is_byte_identical_noop(self):
        """The second pass must not mutate the record further."""
        from voice_typer.server.security import PIIRedactionFilter

        f = PIIRedactionFilter()
        record = _make_record("Contact test@example.com or 555-123-4567")
        f.filter(record)
        snapshot = (record.msg, record.args, record.redacted_msg)
        assert f.filter(record) is True
        assert (record.msg, record.args, record.redacted_msg) == snapshot
        assert "test@example.com" not in record.msg
        assert "555-123-4567" not in record.msg

    def test_redacted_msg_none_still_gets_full_scan(self):
        """A record without the sentinel attribute gets the full scan.

        The guard checks ``is not None`` — a fresh LogRecord (no
        ``redacted_msg``) must always be scrubbed, never skipped."""
        from voice_typer.server.security import PIIRedactionFilter

        f = PIIRedactionFilter()
        record = _make_record("User test@example.com logged in")
        assert not hasattr(record, "redacted_msg")
        assert f.filter(record) is True
        assert "[EMAIL]" in record.msg

    def test_exc_text_redacted_once_across_two_passes(self):
        """Traceback redaction happens on the first pass only; the
        second pass must not double-process ``exc_text``."""
        from voice_typer.server.security import PIIRedactionFilter

        f = PIIRedactionFilter()
        try:
            raise ValueError("failed with key=sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF")
        except ValueError:
            import sys

            record = _make_record("boom")
            record.exc_info = sys.exc_info()

        assert f.filter(record) is True
        first_exc_text = record.exc_text
        assert "sk-abcdef" not in first_exc_text
        assert f.filter(record) is True
        assert record.exc_text == first_exc_text, "second pass must leave the already-redacted exc_text untouched"
