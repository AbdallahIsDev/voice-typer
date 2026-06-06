"""Tests for voice_typer.llm_polish — LLMPolisher presets and API."""

import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def polisher():
    from voice_typer.llm_polish import LLMPolisher
    return LLMPolisher(
        api_key="test-key",
        api_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o-mini",
        preset="professional",
        enabled=True,
    )


class TestLLMPolisherPresets:
    def test_all_presets_exist(self):
        from voice_typer.llm_polish import _PRESETS
        assert "professional" in _PRESETS
        assert "casual" in _PRESETS
        assert "email" in _PRESETS
        assert "code" in _PRESETS


class TestLLMPolisherDefaults:
    def test_default_url(self):
        from voice_typer.llm_polish import _DEFAULT_URL
        assert "openai" in _DEFAULT_URL

    def test_default_model(self):
        from voice_typer.llm_polish import _DEFAULT_MODEL
        assert _DEFAULT_MODEL == "gpt-4o-mini"


class TestLLMPolisherPolish:
    def test_disabled_returns_original(self):
        from voice_typer.llm_polish import LLMPolisher
        p = LLMPolisher(enabled=False, api_key="key")
        result = p.polish("Hello world")
        assert result == "Hello world"

    def test_no_key_returns_original(self):
        from voice_typer.llm_polish import LLMPolisher
        p = LLMPolisher(enabled=True, api_key="")
        result = p.polish("Hello world")
        assert result == "Hello world"

    def test_short_text_returns_original(self, polisher):
        result = polisher.polish("Hi")
        assert result == "Hi"

    def test_polish_success(self, polisher):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Polished text here"}}]
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("voice_typer.llm_polish.urlopen", return_value=mock_response):
            result = polisher.polish("This is some raw transcribed text that needs polishing")
            assert result == "Polished text here"

    def test_polish_failure_returns_original(self, polisher):
        with patch("voice_typer.llm_polish.urlopen", side_effect=Exception("API error")):
            result = polisher.polish("This is some raw transcribed text that needs polishing")
            assert result == "This is some raw transcribed text that needs polishing"

    def test_polish_with_custom_preset(self, polisher):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "Casual text"}}]
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("voice_typer.llm_polish.urlopen", return_value=mock_response):
            result = polisher.polish("Raw text here", preset="casual")
            assert result == "Casual text"


class TestLLMPolisherTestConnection:
    def test_test_connection_no_key(self):
        from voice_typer.llm_polish import LLMPolisher
        p = LLMPolisher(api_key="")
        success, msg = p.test_connection()
        assert success is False

    def test_test_connection_success(self, polisher):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "OK"}}]
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("voice_typer.llm_polish.urlopen", return_value=mock_response):
            success, msg = polisher.test_connection()
            assert success is True

    def test_test_connection_failure(self, polisher):
        with patch("voice_typer.llm_polish.urlopen", side_effect=Exception("timeout")):
            success, msg = polisher.test_connection()
            assert success is False
