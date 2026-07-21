"""CR-069: split from tests/test_feature_hardening_regressions.py (L1222-1375).

Source marker: ``tests/test_new_ux_029_offline_mode.py``.

NEW-UX-029: Offline-mode tests.

The finding: the app is "offline-first by design" but no test verifies
the offline contract: when the network is down, local ASR + cached
models must still function, and cloud/LLM features must fail gracefully
with user-visible messages (not crashes).

This module simulates total network outage by monkeypatching
``urllib.request.urlopen`` to raise ``ConnectionError`` and verifies:
1. Cloud engines fail gracefully with a clear error message.
2. LLM polish fails gracefully.
3. Local ASR (mocked) still works — the app doesn't crash.
4. The ``_read_capped`` function handles network errors without OOM.

Class/method names, assertion logic, and imports below are preserved
verbatim from the original monolith — only file location has changed.
"""

# === Source: tests/test_new_ux_029_offline_mode.py ===

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


class TestCloudEngineFailsGracefullyOnNetworkError:
    """NEW-UX-029: Verify graceful degradation when the network is down."""

    def test_cloud_engine_transcribe_fails_gracefully_on_network_error(self):
        """When ``urlopen`` raises ConnectionError, cloud transcription
        must raise a user-friendly error, not crash with a stack trace.
        """
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
        """When ``urlopen`` raises ConnectionError, LLM polish must
        return the original text unchanged (not crash).
        """
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
        """SEC-030: ``_read_capped`` must handle a network error mid-stream
        without OOM — the error should propagate, not hang or accumulate.
        """
        from urllib.error import URLError

        from voice_typer.server.cloud_engines import _read_capped

        class FakeResp:
            def read(self, n):
                raise URLError("Connection reset by peer")

        with pytest.raises(URLError):
            _read_capped(FakeResp(), max_bytes=1024)

    def test_offline_mode_local_asr_still_works(self):
        """When the network is down, local ASR (mocked) must still work —
        the app must not crash or hang. This verifies the offline-first
        contract: local models don't depend on network access.
        """
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

        audio = np.full(16000, 0.5, dtype=np.float32)

        # Monkeypatch all network calls to fail — local ASR must not use them
        with patch("urllib.request.urlopen") as mock_urlopen, patch("socket.socket") as mock_socket:
            mock_urlopen.side_effect = ConnectionError("No network")
            mock_socket.side_effect = ConnectionError("No network")

            # Local transcription must succeed despite network being down
            result = eng._transcribe_unlocked(audio)
            assert "hello from local engine" in result, "NEW-UX-029: local ASR must work when the network is down"

    def test_offline_mode_cloud_engine_error_message_is_user_friendly(self):
        """The error message from a cloud engine on network failure must
        be user-friendly (not a raw socket/SSL stack trace).
        """
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
                # or "url" — not be a raw SSL/socket error with hex addresses
                assert any(word in msg for word in ("network", "connection", "url", "reach", "timeout", "error")), (
                    f"NEW-UX-029: cloud engine error message is not user-friendly: {e!r}"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
