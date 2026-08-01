"""Regression tests for the XZ-PII-03 / XZ-PII-05 / XZ-PII-06 fixes.

Background
----------
Three related findings from the XZ review:

* **XZ-PII-03 (Medium)** — ``redact_pii()`` in ``security.py`` only
  applied the four PII patterns (email / phone / SSN / CC). API keys,
  bearer tokens, and URL-embedded credentials passed through verbatim.
  The ``llm_polish.py`` docstring claimed API keys were covered,
  which was false. Internal ``_redact_text`` already chained PII +
  ``redact_secret`` + ``redact_url`` — the standalone ``redact_pii``
  helper was inconsistent.

* **XZ-PII-05 (Low)** — ``DictationPipeline._apply_llm_polish``'s
  failure log line (``log.warning("[LLM_POLISH] Polish failed: %s",
  exc)``) didn't apply explicit redaction. LLM API errors can echo
  the request URL + Authorization header (which carries the API key)
  back in their body; the log line was a credential-leak vector.

* **XZ-PII-06 (Low)** — ``cloud_engines.py`` had inconsistent
  redaction across its four exception branches. The
  ``HTTPError`` / ``URLError`` branches used
  ``redact_secret(redact_url(str(exc)))`` but the generic
  ``Exception`` branches used only ``redact_secret(str(exc))`` — a
  URL-embedded credential in a 500-response body would leak.

These tests pin all three fixes.
"""

from __future__ import annotations

import logging

from voice_typer.server.security import redact_pii

# redact_pii now also redacts API keys + URL credentials ──


class TestRedactPiiAlsoRedactsApiSecrets:
    """XZ-PII-03: ``redact_pii()`` must call ``redact_secret`` so API
    keys, bearer tokens, and ``sk-``-prefixed secrets are masked the
    same way they are by the internal ``_redact_text`` helper.
    """

    def test_bearer_token_is_masked(self) -> None:
        # A typical Authorization header value that an LLM/cloud API
        # might echo back in an error message.
        text = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact_pii(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in redacted
        # The secret should be replaced with a ``<prefix>***`` / ``***``
        # mask rather than left bare.
        assert "***" in redacted or "sk-" not in redacted

    def test_long_bare_api_key_is_masked(self) -> None:
        # 40-char bare token (the threshold is 20+ chars per
        # ``_secrets._FAST_TRIGGER``).
        secret = "abcdefghijklmnopqrstuvwxyz0123456789abcd"
        text = f"API key rejected: {secret}"
        redacted = redact_pii(text)
        assert secret not in redacted

    def test_short_text_without_secrets_is_preserved(self) -> None:
        # Below the 20-char threshold — should pass through unchanged
        # (matches the ``_FAST_TRIGGER`` short-circuit semantics).
        text = "Hello world"
        assert redact_pii(text) == text

    def test_pii_patterns_still_masked(self) -> None:
        # Regression guard: the four PII patterns must still work
        # after the  change (already pinned by
        # security_test.py::TestTranscriptionLoggingRedactsPii, but
        # duplicated here so this test file is self-contained).
        assert "[EMAIL]" in redact_pii("contact john.doe@example.com")
        assert "[PHONE]" in redact_pii("call 555-123-4567")
        assert "[SSN]" in redact_pii("ssn 123-45-6789")
        assert "[CC]" in redact_pii("card 4111-1111-1111-1111")


class TestRedactPiiAlsoRedactsUrlCredentials:
    """XZ-PII-03: ``redact_pii()`` must call ``redact_url`` so
    ``user:pass@host`` URL-embedded credentials are stripped.
    """

    def test_url_userinfo_is_stripped(self) -> None:
        # ``redact_url`` uses ``urllib.parse.urlparse``, which only
        # recognises userinfo when the string IS the URL (not embedded
        # in surrounding text). Call ``redact_pii`` on a bare URL.
        text = "https://alice:secret@localhost:8080/v1/audio"
        redacted = redact_pii(text)
        assert "secret" not in redacted
        assert "alice:secret" not in redacted
        # The host must be preserved so the log line stays useful.
        assert "localhost" in redacted

    def test_no_at_sign_skips_url_redaction(self) -> None:
        # The ``"@" in text`` gate skips the ``urlparse`` call for the
        # common case (most log lines carry no ``@``). A URL with no
        # userinfo is preserved verbatim.
        text = "GET https://api.openai.com/v1/models"
        assert redact_pii(text) == text


class TestRedactPiiIsConsistentWithInternalRedactText:
    """XZ-PII-03: ``redact_pii`` and the internal ``_redact_text``
    helper (used by ``PIIRedactionFilter``) must produce the same
    output for inputs that carry both PII and a secret. Pre-fix,
    ``_redact_text`` masked the secret but ``redact_pii`` did not —
    a consumer that swapped one for the other would see a credential
    leak.
    """

    def test_redact_pii_matches_redact_text_for_mixed_input(self) -> None:
        from voice_typer.server.security import _redact_text

        text = "Contact john.doe@example.com — auth=Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        assert redact_pii(text) == _redact_text(text)


# _apply_llm_polish failure log redacts the exception ──────


class TestLlmPolishFailureLogRedactsException:
    """XZ-PII-05: ``DictationPipeline._apply_llm_polish`` must wrap
    the exception message with ``redact_secret`` before logging so an
    LLM API error that echoes the Authorization header does not leak
    the API key into the log file.
    """

    def test_apply_llm_polish_redacts_bearer_token_in_exc(self, caplog) -> None:
        from unittest.mock import MagicMock

        from voice_typer.server.dictation_pipeline import DictationPipeline

        # Build a minimal app mock whose LLM polisher raises an
        # exception whose ``str(exc)`` contains a Bearer token.
        app = MagicMock()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        app.config.openai_api_key = ""
        app.config.llm_api_url = "https://api.openai.com/v1/chat/completions"
        app.config.llm_model = "gpt-4o-mini"
        app.config.llm_preset = "professional"
        app.config.llm_polish_consent = True

        bearer = "Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"

        class _BoomError(Exception):
            pass

        boom = _BoomError(f"OpenAI API error: 401 Unauthorized — {bearer}")
        polisher = MagicMock()
        polisher.polish.side_effect = boom
        app._llm_polisher = polisher

        pipeline = DictationPipeline(app)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._apply_llm_polish("hello world")

        # The polish failure is swallowed (returns the input text).
        assert result == "hello world"

        # The Bearer token MUST NOT appear in the captured log.
        log_text = caplog.text
        assert bearer not in log_text, (
            "XZ-PII-05: the Bearer token must be redacted from the "
            "LLM-polish failure log line. The raw token was found."
        )
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in log_text, (
            "XZ-PII-05: the raw API key must not appear in the log."
        )
        # And the redacted form MUST appear so the failure is still
        # surfaced to operators.
        assert "Polish failed" in log_text


# cloud_engines redaction is consistent across branches ────


class TestCloudEnginesRedactionConsistency:
    """XZ-PII-06: source-level regression guard. The four exception
    branches in ``cloud_engines.py`` (two OpenAI-compatible, two
    Deepgram) must all use ``redact_secret(redact_url(str(exc)))``.
    Pre-fix, the two generic ``Exception`` branches used only
    ``redact_secret(str(exc))`` — a URL-embedded credential in a
    500-response body would leak into the log.
    """

    def test_no_redact_secret_only_branches_in_cloud_engines(self) -> None:
        import inspect

        from voice_typer.server import cloud_engines

        src = inspect.getsource(cloud_engines)
        # The  fix removed every ``redact_secret(str(exc))``
        # occurrence in favour of ``redact_secret(redact_url(str(exc)))``.
        # The pre-fix pattern was ``safe_msg = redact_secret(str(exc))``
        # — a single ``redact_secret`` call wrapping ``str(exc)``
        # without the inner ``redact_url``.
        assert "redact_secret(str(exc))" not in src, (
            "XZ-PII-06: every redaction site in cloud_engines.py must "
            "use ``redact_secret(redact_url(str(exc)))`` — found a "
            "stale ``redact_secret(str(exc))`` site that skips URL-"
            "credential redaction."
        )

    def test_all_four_branches_use_chained_redaction(self) -> None:
        import inspect

        from voice_typer.server import cloud_engines

        src = inspect.getsource(cloud_engines)
        # The canonical chain appears in both the OpenAI-compatible
        # path and the Deepgram path. Count occurrences — pre-fix had
        # 4 (two in each path's HTTPError/URLError branches); the fix
        # adds 2 more (the generic Exception branches) for a total of
        # at least 6.
        chain_count = src.count("redact_secret(redact_url(str(exc)))")
        assert chain_count >= 6, (
            "XZ-PII-06: expected at least 6 occurrences of "
            "``redact_secret(redact_url(str(exc)))`` in cloud_engines.py "
            f"(2 HTTPError + 2 URLError + 2 generic Exception), found {chain_count}."
        )
