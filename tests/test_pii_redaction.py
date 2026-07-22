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
    record = logging.LogRecord("test", logging.INFO, "", 0, "User test@example.com logged in", (), None)
    f.filter(record)
    assert "[EMAIL]" in record.msg
    assert "test@example.com" not in record.msg


def test_pii_redaction_phone():
    """Phone numbers are redacted from log messages."""
    from voice_typer.server.app import _PIIRedactionFilter

    f = _PIIRedactionFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "Call 555-123-4567 now", (), None)
    f.filter(record)
    assert "[PHONE]" in record.msg
    assert "555-123-4567" not in record.msg


def test_pii_redaction_ssn():
    """SSN-like patterns are redacted from log messages."""
    from voice_typer.server.app import _PIIRedactionFilter

    f = _PIIRedactionFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "SSN: 123-45-6789", (), None)
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
        "test",
        logging.INFO,
        "",
        0,
        f"Using API key: {api_key}",
        (),
        None,
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
        "test",
        logging.INFO,
        "",
        0,
        f"Authorization: Bearer {api_key}",
        (),
        None,
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
        "test",
        logging.INFO,
        "",
        0,
        f"Token {secret}",
        (),
        None,
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
        "test",
        logging.INFO,
        "",
        0,
        url,
        (),
        None,
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
        "test",
        logging.ERROR,
        __file__,
        1,
        "request failed",
        (),
        exc_info,
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
        "test",
        logging.ERROR,
        __file__,
        1,
        "request failed",
        (),
        exc_info,
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
        "test",
        logging.ERROR,
        __file__,
        1,
        "chained failure",
        (),
        exc_info,
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


# ─── G4-M-26: International phone + IBAN patterns ─────────────────────────


def test_g4_m_26_international_phone_redacted():
    """G4-M-26: international phone numbers (E.164-ish) are redacted."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    test_cases = [
        # US with country code
        "+1 (415) 555-2671",
        # UK with country code
        "+44 20 7946 0958",
        # China with country code
        "+86 10 1234 5678",
        # Germany with country code
        "+49 30 12345678",
        # Bare + and no parens
        "+14155552671",
    ]
    for phone in test_cases:
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            f"Calling user at {phone} now",
            (),
            None,
        )
        f.filter(record)
        assert phone not in record.msg, f"phone {phone!r} should be redacted; got {record.msg!r}"
        assert "[PHONE]" in record.msg, f"[PHONE] token missing for {phone!r}; got {record.msg!r}"


def test_g4_m_26_iban_redacted():
    """G4-M-26: IBAN (international bank account number) is redacted."""
    from voice_typer.server.security import PIIRedactionFilter

    f = PIIRedactionFilter()
    test_cases = [
        # UK IBAN
        "GB82WEST12345698765432",
        # German IBAN
        "DE89370400440532013000",
        # French IBAN
        "FR1420041010050500013M02606",
        # Swiss IBAN
        "CH9300762011623852957",
    ]
    for iban in test_cases:
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "",
            0,
            f"Wire transfer to IBAN {iban} confirmed",
            (),
            None,
        )
        f.filter(record)
        assert iban not in record.msg, f"IBAN {iban!r} should be redacted; got {record.msg!r}"
        assert "[IBAN]" in record.msg, f"[IBAN] token missing for {iban!r}; got {record.msg!r}"


def test_g4_m_26_us_routing_number_not_redacted():
    """G4-M-26: 9-digit US ABA routing numbers are NOT matched
    (too high a false-positive rate on ordinary numeric text)."""
    from voice_typer.server.security import redact_pii

    # A 9-digit routing number like ``021000021`` (Chase) is bare
    # digits with no country prefix or check-digit structure. The
    # PIIRedactionFilter patterns deliberately omit it because the
    # pattern would also match every 9-digit order ID, zip+4, and
    # timestamp fragment in operator logs.
    text = "Routing number 021000021 for the wire"
    redacted = redact_pii(text)
    # The routing number must survive redaction unchanged.
    assert "021000021" in redacted
    # No redaction token should appear.
    assert "[PHONE]" not in redacted
    assert "[IBAN]" not in redacted


def test_g4_m_26_redact_pii_helper_covers_new_patterns():
    """G4-M-26: the standalone ``redact_pii`` helper also redacts the
    new international phone + IBAN patterns (it shares the same
    ``_PATTERNS`` list as ``PIIRedactionFilter``)."""
    from voice_typer.server.security import redact_pii

    # International phone
    text = "Call me at +1 (415) 555-2671"
    redacted = redact_pii(text)
    assert "415" not in redacted
    assert "[PHONE]" in redacted

    # IBAN
    text = "IBAN: GB82WEST12345698765432"
    redacted = redact_pii(text)
    assert "GB82WEST" not in redacted
    assert "[IBAN]" in redacted


# ─── G4-H-03: PIIRedactionFilter on logging.lastResort ────────────────────


def test_g4_h_03_lastresort_is_stream_handler_with_pii_filter():
    """G4-H-03: ``logging.lastResort`` is a ``StreamHandler`` carrying
    a ``PIIRedactionFilter`` so third-party logger output (keyring,
    urllib3, websockets) is redacted."""
    import logging

    from voice_typer.server.security import PIIRedactionFilter

    # The default lastResort is a _StderrHandler (a private subclass of
    # StreamHandler). After security.py is imported, it should be a
    # plain StreamHandler with our filter attached.
    assert isinstance(logging.lastResort, logging.StreamHandler)
    has_filter = any(isinstance(f, PIIRedactionFilter) for f in logging.lastResort.filters)
    assert has_filter, (
        "logging.lastResort must carry a PIIRedactionFilter so third-party "
        "logger output (keyring, urllib3, websockets) is redacted before "
        "reaching stderr."
    )


def test_g4_h_03_lastresort_filter_redacts_pii():
    """G4-H-03: the PIIRedactionFilter attached to ``logging.lastResort``
    actually redacts PII when applied to a record (sanity check that
    the filter is functional, not just attached)."""
    import logging

    from voice_typer.server.security import PIIRedactionFilter

    filters = [f for f in logging.lastResort.filters if isinstance(f, PIIRedactionFilter)]
    assert len(filters) >= 1
    f = filters[0]

    record = logging.LogRecord(
        "keyring.backend",
        logging.WARNING,
        __file__,
        1,
        "Loaded credentials for test@example.com from keychain",
        (),
        None,
    )
    assert f.filter(record) is True
    assert "test@example.com" not in record.msg
    assert "[EMAIL]" in record.msg


def test_g4_h_03_third_party_logger_output_redacted_via_lastresort():
    """G4-H-03: end-to-end — a third-party logger with NO handlers
    routes through ``logging.lastResort``, which redacts PII.

    This simulates the production scenario: a buggy keyring backend
    logs ``"Loaded API key sk-..."`` and the record flows to stderr
    via ``lastResort``. Without the G4-H-03 fix, the API key would
    appear unredacted in stderr (and any captured stderr buffer).
    """
    import logging
    from io import StringIO

    # Save the original stream so we can restore it after the test.
    # lastResort's stream is captured at construction time, so we
    # swap it out for a StringIO to capture the emitted output.
    original_stream = logging.lastResort.stream
    original_level = logging.lastResort.level

    captured = StringIO()
    logging.lastResort.stream = captured
    # lastResort's default level is WARNING; lower it so DEBUG/INFO
    # records also flow through (in case the test logger emits at
    # those levels).
    logging.lastResort.setLevel(logging.DEBUG)
    try:
        # Create a third-party logger that mimics keyring/urllib3:
        # no handlers of its own, no propagation to the root logger
        # (so root's handlers — if any — don't catch it). This forces
        # the record to flow through ``lastResort``.
        logger = logging.getLogger("test_g4_h_03_fake_third_party_lib")
        # Clear any handlers a previous test may have left behind.
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.DEBUG)

        api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        email = "leak@example.com"
        # Mimic a buggy keyring backend logging a credential.
        logger.warning("Loaded credentials for %s with key %s", email, api_key)

        # Flush to ensure the record is written to the StringIO.
        logging.lastResort.flush()
        output = captured.getvalue()

        # The API key and email must NOT appear in the captured output.
        assert api_key not in output, f"API key leaked through lastResort:\n{output!r}"
        assert "sk-abcdef" not in output
        assert email not in output, f"Email leaked through lastResort:\n{output!r}"
        # The redaction token should appear instead.
        assert "[EMAIL]" in output or "***" in output, f"expected redaction token in output, got:\n{output!r}"
    finally:
        logging.lastResort.stream = original_stream
        logging.lastResort.setLevel(original_level)
        # Clean up the test logger so it doesn't leak to other tests.
        logging.getLogger("test_g4_h_03_fake_third_party_lib").handlers.clear()


def test_g4_h_03_install_lastresort_pii_filter_idempotent():
    """G4-H-03: ``install_lastresort_pii_filter()`` is idempotent —
    calling it multiple times replaces the prior handler rather than
    stacking duplicate filters."""
    import logging

    from voice_typer.server.security import PIIRedactionFilter, install_lastresort_pii_filter

    # Install multiple times.
    install_lastresort_pii_filter()
    install_lastresort_pii_filter()
    install_lastresort_pii_filter()

    # The handler should still be a StreamHandler.
    assert isinstance(logging.lastResort, logging.StreamHandler)
    # Count PIIRedactionFilter instances — should be exactly 1
    # (each install replaces the handler, so no duplicates accumulate).
    pii_filters = [f for f in logging.lastResort.filters if isinstance(f, PIIRedactionFilter)]
    assert len(pii_filters) == 1, (
        f"expected exactly 1 PIIRedactionFilter after multiple installs, got {len(pii_filters)}"
    )
