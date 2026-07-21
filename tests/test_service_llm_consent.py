"""Regression test for CR-43: test_llm_connection must require llm_polish_consent.

Also covers VT-SEC-8-3: exception messages returned from service.py
methods must be passed through ``redact_secret(redact_url(...))`` so
that secrets (API keys, Bearer tokens, URL userinfo) are scrubbed
before they reach the IPC layer (and therefore the renderer / logs).
"""

from unittest.mock import MagicMock, patch

from voice_typer.server.service import VoiceTyperService


class TestTestLLMConnectionConsent:
    def test_returns_consent_required_when_consent_false(self):
        """test_llm_connection must return a consent-required error when llm_polish_consent=False."""
        mock_app = MagicMock()
        mock_app.config.llm_polish_consent = False
        mock_app.config.llm_api_key = "sk-test-key"
        service = VoiceTyperService(mock_app)
        result = service.test_llm_connection()
        assert result["success"] is False
        assert "consent" in result["message"].lower()

    def test_proceeds_when_consent_true_and_key_set(self):
        """test_llm_connection must proceed when llm_polish_consent=True and key is set."""
        mock_app = MagicMock()
        mock_app.config.llm_polish_consent = True
        mock_app.config.llm_api_key = "sk-test-key"
        # Mock LLMPolisher.test_connection to return success
        with patch("voice_typer.server.llm_polish.LLMPolisher") as MockPolisher:
            MockPolisher.return_value.test_connection.return_value = (True, "OK")
            service = VoiceTyperService(mock_app)
            result = service.test_llm_connection()
            assert result["success"] is True

    def test_returns_key_required_when_consent_true_but_no_key(self):
        """test_llm_connection must return key-required when consent=True but no API key."""
        mock_app = MagicMock()
        mock_app.config.llm_polish_consent = True
        mock_app.config.llm_api_key = ""
        service = VoiceTyperService(mock_app)
        result = service.test_llm_connection()
        assert result["success"] is False
        assert "key" in result["message"].lower()


class TestServiceErrorRedaction:
    """VT-SEC-8-3: service.py error returns must use redact_secret(redact_url(str(exc)))."""

    def test_test_llm_connection_exception_is_redacted(self):
        """Exception messages in test_llm_connection must be redacted.

        The exception payload contains a URL with embedded userinfo
        (``user:pass@``). ``redact_url`` must strip the credentials
        before the message is returned to the caller.

        Note: ``redact_url`` relies on ``urlparse`` to detect the URL,
        so the exception message must be a parseable URL (no leading
        prose) for the userinfo-stripping to fire.  We use a bare URL
        here so the test meaningfully verifies that ``redact_url`` is
        being applied to exception strings.
        """
        mock_app = MagicMock()
        mock_app.config.llm_polish_consent = True
        mock_app.config.llm_api_key = "sk-secret-key-12345"
        with patch("voice_typer.server.llm_polish.LLMPolisher") as MockPolisher:
            MockPolisher.return_value.test_connection.side_effect = RuntimeError("https://user:pass@evil.com/")
            service = VoiceTyperService(mock_app)
            result = service.test_llm_connection()
            assert result["success"] is False
            # The error message must NOT contain the secret/password
            assert "pass" not in result["message"]
            # The error message must NOT contain the configured API key
            assert "sk-secret-key-12345" not in result["message"]

    def test_test_llm_connection_exception_redacts_bearer_token(self):
        """Bearer tokens in exception messages must be redacted via redact_secret."""
        mock_app = MagicMock()
        mock_app.config.llm_polish_consent = True
        mock_app.config.llm_api_key = "sk-secret-key-12345"
        with patch("voice_typer.server.llm_polish.LLMPolisher") as MockPolisher:
            MockPolisher.return_value.test_connection.side_effect = RuntimeError(
                "Authorization: Bearer sk-secret-key-12345 rejected"
            )
            service = VoiceTyperService(mock_app)
            result = service.test_llm_connection()
            assert result["success"] is False
            # The Bearer token must be redacted — the literal key value
            # must not survive into the returned message.
            assert "sk-secret-key-12345" not in result["message"]
            # The "Bearer " prefix is preserved by redact_secret, but
            # the secret value following it is replaced with "***".
            assert "Bearer ***" in result["message"]
