"""Tests for voice_typer.cloud_engines — CloudEngine factory and API."""

import json
import pytest
from unittest.mock import MagicMock, patch


class TestCloudEngineFactory:
    def test_create_openai_engine(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="test-key")
        assert engine.provider == "openai"
        assert engine.api_key == "test-key"
        assert "openai" in engine.api_url

    def test_create_groq_engine(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="groq", api_key="test-key")
        assert engine.provider == "groq"
        assert "groq" in engine.api_url

    def test_create_deepgram_engine(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="deepgram", api_key="test-key")
        assert engine.provider == "deepgram"
        assert "deepgram" in engine.api_url

    def test_custom_api_url(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="key", api_url="https://custom.api/v1")
        assert engine.api_url == "https://custom.api/v1"


class TestCloudEngineProtocol:
    def test_is_loaded_with_key(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="test-key")
        assert engine.is_loaded is True

    def test_is_loaded_without_key(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="")
        assert engine.is_loaded is False

    def test_load_noop(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="test-key")
        # Should not raise
        engine.load()

    def test_device_info(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="test-key")
        assert "openai" in engine.device_info

    def test_loaded_via(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="groq", api_key="test-key", model="whisper-large-v3")
        assert "groq" in engine.loaded_via
        assert "whisper-large-v3" in engine.loaded_via

    def test_transcribe_empty_audio(self):
        from voice_typer.cloud_engines import CloudEngine
        import numpy as np
        engine = CloudEngine(provider="openai", api_key="test-key")
        result = engine.transcribe(np.array([], dtype=np.float32))
        assert result == ""

    def test_transcribe_with_fallback(self):
        from voice_typer.cloud_engines import CloudEngine
        import numpy as np
        engine = CloudEngine(provider="openai", api_key="test-key")
        # Should delegate to transcribe
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(engine, '_send_request', return_value="test text"):
            result = engine.transcribe_with_fallback(audio)
            assert result == "test text"


class TestCloudEngineMultipart:
    def test_build_multipart_body(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="test-key", model="whisper-1")
        body = engine._build_multipart_body(b"fake_wav_data", "audio.wav", "boundary123")
        assert b"fake_wav_data" in body
        assert b"whisper-1" in body
        assert b"boundary123" in body


class TestCloudEngineTestConnection:
    def test_test_connection_no_key(self):
        from voice_typer.cloud_engines import CloudEngine
        engine = CloudEngine(provider="openai", api_key="")
        success, msg = engine.test_connection()
        assert success is False
        assert "API key" in msg
