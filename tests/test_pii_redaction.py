"""Tests for SEC-009: PII redaction in logs, and RW-6: API-key redaction.

RW-6 extends ``PIIRedactionFilter`` to also redact:
  - API keys / bearer tokens (via ``_secrets.redact_secret``)
  - URL-embedded credentials (via ``_secrets.redact_url``)
  - Traceback text (when ``record.exc_info`` is set)
"""
import logging
import sys

# ─── SEC-009: existing PII patterns (unchanged behavior) ──────────────────


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


# ─── RW-6: API-key redaction in log messages ──────────────────────────────


def test_rw6_api_key_redaction_openai_style():
    """RW-6: OpenAI-style keys (sk-...) are redacted from log messages."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
    record = logging.LogRecord(
        "test", logging.INFO, "", 0,
        f"Using API key: {api_key}", (), None,
    )
    f.filter(record)
    assert api_key not in record.msg
    assert "sk-abcdef" not in record.msg
    assert "***" in record.msg


def test_rw6_api_key_redaction_bearer_token():
    """RW-6: Bearer tokens are redacted; 'Bearer' prefix is preserved."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
    record = logging.LogRecord(
        "test", logging.INFO, "", 0,
        f"Authorization: Bearer {api_key}", (), None,
    )
    f.filter(record)
    assert api_key not in record.msg
    assert "sk-abcdef" not in record.msg
    assert "***" in record.msg
    # redact_secret preserves the "Bearer " prefix
    assert "Bearer" in record.msg


def test_rw6_api_key_redaction_token_keyword():
    """RW-6: 'Token <secret>' auth is redacted; 'Token' prefix preserved."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    secret = "abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
    record = logging.LogRecord(
        "test", logging.INFO, "", 0,
        f"Token {secret}", (), None,
    )
    f.filter(record)
    assert secret not in record.msg
    assert "***" in record.msg
    assert "Token" in record.msg


def test_rw6_url_credential_redaction():
    """RW-6: URL-embedded credentials (user:pass@) are stripped."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    # Use ``localhost:8080`` (no dot in the host) so the existing
    # email-PII pattern — which requires a dotted domain after ``@`` —
    # does NOT fire and consume the userinfo.  This lets us verify
    # ``redact_url`` actually strips the credentials.  The password is
    # 32+ chars so ``redact_secret`` also fires on it.
    password = "password1234567890ABCDEFpassword1234567890ABCDEF"
    url = f"https://user:{password}@localhost:8080/v1"
    record = logging.LogRecord(
        "test", logging.INFO, "", 0,
        url, (), None,
    )
    f.filter(record)
    assert password not in record.msg
    # The host should still be visible (only userinfo is stripped)
    assert "localhost:8080" in record.msg
    # No userinfo should remain
    assert "user:" not in record.msg


def test_rw6_short_messages_unchanged():
    """RW-6: Short messages without secrets pass through unchanged."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    original = "hello world"
    record = logging.LogRecord("test", logging.INFO, "", 0, original, (), None)
    f.filter(record)
    assert record.msg == original


# ─── RW-6: API-key redaction in tracebacks ────────────────────────────────


def test_rw6_traceback_redaction_exc_text():
    """RW-6: API keys in exception messages are redacted in record.exc_text."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"

    try:
        raise Exception(f"Connection to https://api.openai.com/?key={api_key} failed")
    except Exception:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1,
        "request failed", (), exc_info,
    )
    f.filter(record)

    # The redacted traceback should be cached on record.exc_text
    assert record.exc_text is not None
    assert api_key not in record.exc_text
    assert "sk-abcdef" not in record.exc_text
    assert "***" in record.exc_text
    # The exception type name should still be present (for debugging)
    assert "Exception" in record.exc_text


def test_rw6_traceback_redaction_via_default_formatter():
    """RW-6: Default logging.Formatter output has redacted traceback."""
    from voice_typer.server.security import PIIRedactionFilter

    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"

    try:
        raise Exception(f"Connection to https://api.openai.com/?key={api_key} failed")
    except Exception:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1,
        "request failed", (), exc_info,
    )

    f = PIIRedactionFilter()
    f.filter(record)

    # The default logging.Formatter appends record.exc_text to the
    # formatted message — this verifies the redacted traceback is what
    # gets emitted.
    formatter = logging.Formatter("%(message)s")
    output = formatter.format(record)

    assert api_key not in output
    assert "sk-abcdef" not in output
    assert "***" in output


def test_rw6_traceback_redaction_chained_exception():
    """RW-6: API keys in chained exception __cause__ messages are redacted."""
    from voice_typer.server.security import PIIRedactionFilter
    f = PIIRedactionFilter()
    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"

    try:
        try:
            raise ValueError(f"inner cause: key={api_key}")
        except ValueError as inner:
            raise RuntimeError("outer wrapper") from inner
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1,
        "chained failure", (), exc_info,
    )
    f.filter(record)

    assert record.exc_text is not None
    assert api_key not in record.exc_text
    assert "sk-abcdef" not in record.exc_text
    assert "***" in record.exc_text


# ─── RW-6: End-to-end — actual log file does not contain the key ──────────


def test_rw6_end_to_end_log_file_no_api_key(tmp_path):
    """RW-6: An actual log file (via setup_logging) contains no API key.

    This exercises the full pipeline: ``get_logger('voice_typer.<x>')``
    → handler filter → ``_FileFormatter`` → log file on disk.  This
    verifies the filter is attached to the *handler* (not just the
    logger) so records from child loggers are also redacted.
    """
    from voice_typer.server.log import reset, setup_logging

    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)

    # Use a child logger — the common case via get_logger(__name__)
    log = logging.getLogger("voice_typer.server.fake_module")

    api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
    try:
        raise Exception(f"Connection to https://api.openai.com/?key={api_key} failed")
    except Exception as e:
        # Mirrors the real call-site pattern: log.error("...: %s", e, exc_info=True)
        log.error("[IPC] request failed: %s", e, exc_info=True)

    log_file = config_dir / "voice-typer.log"
    content = log_file.read_text(encoding="utf-8")
    assert api_key not in content
    assert "sk-abcdef" not in content
    assert "***" in content

    # Cleanup
    reset()


def test_rw6_end_to_end_log_file_pii_still_redacted(tmp_path):
    """RW-6: Existing PII patterns still fire through the full pipeline."""
    from voice_typer.server.log import reset, setup_logging

    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)

    log = logging.getLogger("voice_typer.server.fake_module")
    log.error("User test@example.com logged in")

    log_file = config_dir / "voice-typer.log"
    content = log_file.read_text(encoding="utf-8")
    assert "test@example.com" not in content
    assert "[EMAIL]" in content

    reset()
