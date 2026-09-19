"""Regression tests for the XZ-PII-03 / XZ-PII-05 / XZ-PII-06 fixes."""

from __future__ import annotations

import logging

from voice_typer.server.security import redact_pii


class TestRedactPiiAlsoRedactsApiSecrets:
    """XZ-PII-03: ``redact_pii()`` must call ``redact_secret`` so API"""

    def test_bearer_token_is_masked(self) -> None:
        # A typical Authorization header value that an LLM/cloud API
        text = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact_pii(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in redacted
        # The secret should be replaced with a ``<prefix>***`` / ``***``
        assert "***" in redacted or "sk-" not in redacted

    def test_long_bare_api_key_is_masked(self) -> None:
        # 40-char bare token (the threshold is 20+ chars per
        secret = "abcdefghijklmnopqrstuvwxyz0123456789abcd"
        text = f"API key rejected: {secret}"
        redacted = redact_pii(text)
        assert secret not in redacted

    def test_short_text_without_secrets_is_preserved(self) -> None:
        # Below the 20-char threshold, should pass through unchanged
        text = "Hello world"
        assert redact_pii(text) == text

    def test_pii_patterns_still_masked(self) -> None:
        assert "[EMAIL]" in redact_pii("contact john.doe@example.com")
        assert "[PHONE]" in redact_pii("call 555-123-4567")
        assert "[SSN]" in redact_pii("ssn 123-45-6789")
        assert "[CC]" in redact_pii("card 4111-1111-1111-1111")


class TestRedactPiiAlsoRedactsUrlCredentials:
    """XZ-PII-03: ``redact_pii()`` must call ``redact_url`` so"""

    def test_url_userinfo_is_stripped(self) -> None:
        # ``redact_url`` uses ``urllib.parse.urlparse``, which only
        text = "https://alice:secret@localhost:8080/v1/audio"
        redacted = redact_pii(text)
        assert "secret" not in redacted
        assert "alice:secret" not in redacted
        # The host must be preserved so the log line stays useful.
        assert "localhost" in redacted

    def test_no_at_sign_skips_url_redaction(self) -> None:
        text = "GET https://api.openai.com/v1/models"
        assert redact_pii(text) == text


class TestRedactPiiIsConsistentWithInternalRedactText:
    """XZ-PII-03: ``redact_pii`` and the internal ``_redact_text``"""

    def test_redact_pii_matches_redact_text_for_mixed_input(self) -> None:
        from voice_typer.server.security import _redact_text

        text = "Contact john.doe@example.com, auth=Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        assert redact_pii(text) == _redact_text(text)


# _apply_llm_polish failure log redacts the exception ──────


class TestLlmPolishFailureLogRedactsException:
    """XZ-PII-05: ``DictationPipeline._apply_llm_polish`` must wrap"""

    def test_apply_llm_polish_redacts_bearer_token_in_exc(self, caplog) -> None:
        from unittest.mock import MagicMock

        from voice_typer.server.dictation_pipeline import DictationPipeline

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

        boom = _BoomError(f"OpenAI API error: 401 Unauthorized, {bearer}")
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
        assert "Polish failed" in log_text


class TestCloudEnginesRedactionConsistency:
    """XZ-PII-06: source-level regression guard. The four exception"""

    def test_no_redact_secret_only_branches_in_cloud_engines(self) -> None:
        import inspect

        from voice_typer.server import cloud_engines
        from voice_typer.server.cloud import _engine

        src = inspect.getsource(_engine)
        # The  fix removed every ``redact_secret(str(exc))``
        assert "redact_secret(str(exc))" not in src, (
            "XZ-PII-06: every redaction site in cloud._engine must "
            "use ``redact_secret(redact_url(str(exc)))``, found a "
            "stale ``redact_secret(str(exc))`` site that skips URL-"
            "credential redaction."
        )
        # The facade must still re-export the engine so legacy import
        assert cloud_engines.CloudEngine is _engine.CloudEngine

    def test_all_four_branches_use_chained_redaction(self) -> None:
        import inspect

        from voice_typer.server.cloud import _engine

        src = inspect.getsource(_engine)
        # The canonical chain appears in ``_transcribe_with_retry``,
        chain_count = src.count("redact_secret(redact_url(str(exc)))")
        assert chain_count >= 4, (
            "XZ-PII-06: expected at least 4 occurrences of "
            "``redact_secret(redact_url(str(exc)))`` in cloud._engine.py "
            "(3 in the shared ``_transcribe_with_retry`` except branches "
            "+ 1 inlined in the URLError retry warning), found "
            f"{chain_count}. Every redaction site must use the chain "
            "form so a URL-embedded credential (e.g. "
            "``https://user:pass@host/...`` echoed in a 500 body) is "
            "redacted by both ``redact_url`` (URL-cred scrubbing) and "
            "``redact_secret`` (key-substring scrubbing)."
        )
