"""Tests for voice_typer.llm_polish — LLMPolisher presets and API."""

import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def polisher():
    from voice_typer.server.llm_polish import LLMPolisher
    return LLMPolisher(
        api_key="test-key",
        api_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o-mini",
        preset="professional",
        enabled=True,
    )


class TestLLMPolisherPresets:
    def test_all_presets_exist(self):
        from voice_typer.server.llm_polish import _PRESETS
        assert "professional" in _PRESETS
        assert "casual" in _PRESETS
        assert "email" in _PRESETS
        assert "code" in _PRESETS


class TestLLMPolisherDefaults:
    def test_default_url(self):
        from voice_typer.server.llm_polish import _DEFAULT_URL
        assert "openai" in _DEFAULT_URL

    def test_default_model(self):
        from voice_typer.server.llm_polish import _DEFAULT_MODEL
        assert _DEFAULT_MODEL == "gpt-4o-mini"


class TestLLMPolisherPolish:
    def test_disabled_returns_original(self):
        from voice_typer.server.llm_polish import LLMPolisher
        p = LLMPolisher(enabled=False, api_key="key")
        result = p.polish("Hello world")
        assert result == "Hello world"

    def test_no_key_returns_original(self):
        from voice_typer.server.llm_polish import LLMPolisher
        p = LLMPolisher(enabled=True, api_key="")
        result = p.polish("Hello world")
        assert result == "Hello world"

    def test_short_text_returns_original(self, polisher):
        result = polisher.polish("Hi")
        assert result == "Hi"

    def test_polish_success(self, polisher):
        mock_response = MagicMock()
        # SEC-030: _read_capped calls read(64*1024) in a loop. Configure
        # the mock to return the body on the first call and b"" after.
        body = json.dumps({
            "choices": [{"message": {"content": "Polished text here"}}]
        }).encode("utf-8")
        mock_response.read.side_effect = [body, b""]
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("voice_typer.server.llm_polish._opener.open", return_value=mock_response):
            result = polisher.polish("This is some raw transcribed text that needs polishing")
            assert result == "Polished text here"

    def test_polish_failure_returns_original(self, polisher):
        with patch("voice_typer.server.llm_polish._opener.open", side_effect=Exception("API error")):
            result = polisher.polish("This is some raw transcribed text that needs polishing")
            assert result == "This is some raw transcribed text that needs polishing"

    def test_polish_with_custom_preset(self, polisher):
        mock_response = MagicMock()
        # SEC-030: same side_effect pattern as test_polish_success.
        body = json.dumps({
            "choices": [{"message": {"content": "Casual text"}}]
        }).encode("utf-8")
        mock_response.read.side_effect = [body, b""]
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("voice_typer.server.llm_polish._opener.open", return_value=mock_response):
            result = polisher.polish("Raw text here", preset="casual")
            assert result == "Casual text"


class TestLLMPolisherTestConnection:
    def test_test_connection_no_key(self):
        from voice_typer.server.llm_polish import LLMPolisher
        p = LLMPolisher(api_key="")
        success, msg = p.test_connection()
        assert success is False

    def test_test_connection_success(self, polisher):
        mock_response = MagicMock()
        # SEC-030: use side_effect to terminate the _read_capped loop.
        body = json.dumps({
            "choices": [{"message": {"content": "OK"}}]
        }).encode("utf-8")
        mock_response.read.side_effect = [body, b""]
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("voice_typer.server.llm_polish._opener.open", return_value=mock_response):
            success, msg = polisher.test_connection()
            assert success is True

    def test_test_connection_failure(self, polisher):
        with patch("voice_typer.server.llm_polish._opener.open", side_effect=Exception("timeout")):
            success, msg = polisher.test_connection()
            assert success is False


# ── RELIABILITY-004: LLM polish URL allowlist + redaction ────────────────


class TestLLMPolishUrlAllowlist:
    """RELIABILITY-004: LLMPolisher must refuse to send transcribed
    text to any URL whose host is not in the trusted allowlist.
    This is the last-line defense against SEC-002 endpoint-swap
    attacks against the LLM polish endpoint — an attacker who
    manages to set ``llm_api_url`` to an exfiltration endpoint
    would otherwise receive the user's transcribed speech text in
    cleartext."""

    def test_polish_rejects_untrusted_url(self):
        """polish() must raise / return original when the URL is
        untrusted (polish catches exceptions and returns original)."""
        from voice_typer.server.llm_polish import LLMPolisher
        p = LLMPolisher(
            api_key="sk-test",
            api_url="https://evil.example.com/exfiltrate",
            enabled=True,
        )
        # polish() catches exceptions and returns the original text,
        # so we just verify the input is returned unchanged (no
        # network call made).
        result = p.polish("Hello, world!")
        assert result == "Hello, world!"

    def test_test_connection_rejects_untrusted_url(self):
        """test_connection returns (False, msg) for untrusted URLs."""
        from voice_typer.server.llm_polish import LLMPolisher
        p = LLMPolisher(
            api_key="sk-test",
            api_url="https://evil.example.com/exfiltrate",
            enabled=True,
        )
        success, msg = p.test_connection()
        assert success is False
        assert "not in the trusted allowlist" in msg

    def test_default_openai_url_allowed(self):
        """The default OpenAI URL must pass the allowlist check."""
        from voice_typer.server.llm_polish import LLMPolisher
        from urllib.error import URLError
        p = LLMPolisher(api_key="sk-test", enabled=True)
        # Default URL is api.openai.com — allowlist check passes,
        # but HTTP fails (no network).  We just verify the error is
        # NOT a ValueError from the allowlist.
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            side_effect=URLError("no network"),
        ):
            success, msg = p.test_connection()
        assert success is False
        assert "allowlist" not in msg

    def test_polish_redacts_key_in_log(self, caplog):
        """When polish() catches an exception, the log message must
        not contain the API key."""
        from voice_typer.server.llm_polish import LLMPolisher
        import logging

        key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        p = LLMPolisher(api_key=key, enabled=True)
        # Force an exception by patching _call_api to raise
        with patch.object(
            p, "_call_api", side_effect=RuntimeError(f"auth failed: {key}")
        ):
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.llm_polish"):
                result = p.polish("Hello, world!")
        assert result == "Hello, world!"  # original returned
        # Verify the key does not appear in any log record
        for record in caplog.records:
            assert key not in record.getMessage()
