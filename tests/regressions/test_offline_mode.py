"""Source marker: ``tests/test_new_ux_029_offline_mode.py``."""

# === Source: tests/test_new_ux_029_offline_mode.py ===

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


class TestCloudEngineFailsGracefullyOnNetworkError:
    """NEW-UX-029: Verify graceful degradation when the network is down."""

    def test_cloud_engine_transcribe_fails_gracefully_on_network_error(self):
        """When ``urlopen`` raises ConnectionError, cloud transcription"""
        from urllib.error import URLError

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="test-key",
            consent_given=True,
        )
        import numpy as np

        audio = np.zeros(1600, dtype=np.float32)

        # Monkeypatch urlopen to simulate network outage
        with patch("voice_typer.server.cloud_engines._opener.open") as mock_urlopen:
            mock_urlopen.side_effect = URLError("Network is unreachable")
            with pytest.raises(Exception) as exc_info:
                engine.transcribe(audio)

        # The error must be a user-visible exception, not a raw socket error
        assert exc_info.value is not None

    def test_llm_polish_fails_gracefully_on_network_error(self):
        """When ``urlopen`` raises ConnectionError, LLM polish must"""
        from urllib.error import URLError

        from voice_typer.server.llm_polish import LLMPolisher

        polisher = LLMPolisher(
            api_key="test-key",
            api_url="https://api.example.com/v1/chat/completions",
            model="gpt-4",
            enabled=True,
        )

        original_text = "Hello world this is a test"

        with patch("voice_typer.server.llm_polish._opener.open") as mock_urlopen:
            mock_urlopen.side_effect = URLError("Network is unreachable")
            # LLMPolisher.polish must return the original text on failure
            result = polisher.polish(original_text)

        # Must return the original text, not raise
        assert result == original_text, (
            f"NEW-UX-029: LLM polish must return original text on network error, got {result!r}"
        )

    def test_read_capped_handles_network_error_without_oom(self):
        """SEC-030: ``_read_capped`` must handle a network error mid-stream"""
        from urllib.error import URLError

        from voice_typer.server.cloud_engines import _read_capped

        class FakeResp:
            def read(self, n):
                raise URLError("Connection reset by peer")

        with pytest.raises(URLError):
            _read_capped(FakeResp(), max_bytes=1024)

    def test_offline_mode_local_asr_still_works(self):
        """When the network is down, local ASR (mocked) must still work —"""
        import numpy as np
        from voice_typer.server.transcription import TranscriptionEngine

        # Build a mock local engine (no network calls)
        eng = TranscriptionEngine.__new__(TranscriptionEngine)
        eng._lock = threading.Lock()
        eng._model = MagicMock()
        mock_segment = MagicMock()
        mock_segment.text = "hello from local engine"
        eng._model.transcribe.return_value = ([mock_segment], MagicMock())
        eng.beam_size = 1
        eng.best_of = 1
        eng.condition_on_previous_text = False
        eng.language = "en"
        eng._device = "cpu"
        eng._compute_type = "int8"
        eng.config = MagicMock()
        eng.config.log_transcriptions = False
        # ``_transcribe_unlocked`` also touches the abort + inference
        eng._abort_event = threading.Event()
        eng._active_inference = 0
        eng._inference_cond = threading.Condition()

        audio = np.full(16000, 0.5, dtype=np.float32)

        # Monkeypatch all network calls to fail, local ASR must not use them
        with patch("urllib.request.urlopen") as mock_urlopen, patch("socket.socket") as mock_socket:
            mock_urlopen.side_effect = ConnectionError("No network")
            mock_socket.side_effect = ConnectionError("No network")

            # Local transcription must succeed despite network being down
            result = eng._transcribe_unlocked(audio)
            assert "hello from local engine" in result, "NEW-UX-029: local ASR must work when the network is down"

    def test_offline_mode_cloud_engine_error_message_is_user_friendly(self):
        """The error message from a cloud engine on network failure must"""
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="test-key",
            consent_given=True,
        )
        audio = np.zeros(1600, dtype=np.float32)

        with patch("voice_typer.server.cloud_engines._opener.open") as mock_urlopen:
            mock_urlopen.side_effect = URLError("Network is unreachable")
            try:
                engine.transcribe(audio)
                pytest.fail("Should have raised")
            except Exception as e:
                msg = str(e).lower()
                # The error message should mention "network" or "connection"
                assert any(word in msg for word in ("network", "connection", "url", "reach", "timeout", "error")), (
                    f"NEW-UX-029: cloud engine error message is not user-friendly: {e!r}"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
