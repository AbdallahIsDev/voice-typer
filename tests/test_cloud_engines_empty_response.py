"""CloudEngine: HTTP 200 with an empty/blank body must raise a typed error.

A provider returning 200 with an empty body (or ``{}`` / a missing
transcript field) is an anomaly - the pipeline must not ship an empty
transcript as if it were valid. These tests pin the contract:

1. 200 empty / whitespace-only body raises ``CloudEmptyResponseError``
   (OpenAI-compatible AND Deepgram paths).
2. 200 with ``{}`` or a missing transcript field raises the same typed
   error ("empty transcript" variant).
3. 200 with a valid transcript is returned unchanged.
4. The empty-response error is NOT retried (a provider-side anomaly,
   not a transient network error).
5. ``test_connection`` (SEC-011 probe) is unaffected - a 200 with an
   empty body still reports success=True; the probe never parses a
   transcript body.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from voice_typer.server.asr_errors import CloudEmptyResponseError
from voice_typer.server.cloud_engines import CloudEngine


def _make_fake_resp(body: bytes):
    """Context-managed fake HTTP response.

    ``_read_capped`` calls ``resp.read(64*1024)`` in a loop until it
    returns an empty chunk, so ``read`` yields ``body`` once then EOF.
    ``test_connection`` never reads the body - it only checks
    ``resp.status``.
    """
    calls = {"n": 0}

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size: int = -1) -> bytes:
            if calls["n"] == 0:
                calls["n"] += 1
                return body
            return b""

    return _FakeResp()


def _engine(provider: str = "openai") -> CloudEngine:
    if provider == "deepgram":
        return CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            consent_given=True,
        )
    return CloudEngine(
        provider="openai",
        api_key="test-key",
        api_url="https://api.openai.com/v1/audio/transcriptions",
        model="whisper-1",
        consent_given=True,
    )


class TestCloudEmptyResponseError:
    """HTTP 200 with an empty body / empty transcript raises the typed
    error instead of returning an empty string as valid text."""

    def test_200_empty_body_raises(self):
        engine = _engine("openai")
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                return_value=_make_fake_resp(b""),
            ),
            pytest.raises(CloudEmptyResponseError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert "openai" in str(exc_info.value)
        assert "empty body" in str(exc_info.value)

    def test_200_whitespace_only_body_raises(self):
        engine = _engine("openai")
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                return_value=_make_fake_resp(b"   \n\t "),
            ),
            pytest.raises(CloudEmptyResponseError),
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_200_empty_body_raises_deepgram(self):
        engine = _engine("deepgram")
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                return_value=_make_fake_resp(b""),
            ),
            pytest.raises(CloudEmptyResponseError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert "deepgram" in str(exc_info.value)

    def test_200_empty_json_raises(self):
        """``{}`` on a 200 is the "empty transcript" anomaly."""
        engine = _engine("openai")
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                return_value=_make_fake_resp(b"{}"),
            ),
            pytest.raises(CloudEmptyResponseError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert "empty transcript" in str(exc_info.value)

    def test_200_missing_transcript_field_raises(self):
        engine = _engine("openai")
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                return_value=_make_fake_resp(b'{"foo": "bar"}'),
            ),
            pytest.raises(CloudEmptyResponseError),
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_200_valid_transcript_unchanged(self):
        """A valid 200 response must return the text - no behavior
        change on the success path."""
        engine = _engine("openai")
        with patch(
            "voice_typer.server.cloud_engines._opener.open",
            return_value=_make_fake_resp(b'{"text": "hello world"}'),
        ):
            result = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert result == "hello world"

    def test_200_valid_transcript_unchanged_deepgram(self):
        engine = _engine("deepgram")
        body = b'{"results": {"channels": [{"alternatives": [{"transcript": "hello from deepgram"}]}]}}'
        with patch(
            "voice_typer.server.cloud_engines._opener.open",
            return_value=_make_fake_resp(body),
        ):
            result = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert result == "hello from deepgram"

    def test_empty_body_is_not_retried(self):
        """An empty body is a provider-side anomaly, not a transient
        network error - the request must be issued exactly ONCE."""
        engine = _engine("openai")
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                return_value=_make_fake_resp(b""),
            ) as mock_open,
            pytest.raises(CloudEmptyResponseError),
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert mock_open.call_count == 1, (
            "An empty-body 200 must NOT be retried (URLError retries 3x; empty-body must not)."
        )


class TestCloudEmptyResponseTestConnectionUnaffected:
    """The SEC-011 probe (``test_connection``) sends empty audio and
    EXPECTS 400 or 200; a 200 with an empty body must report success,
    not raise ``CloudEmptyResponseError``."""

    def test_test_connection_200_empty_body_success(self):
        engine = _engine("openai")
        with patch(
            "voice_typer.server.cloud_engines._opener.open",
            return_value=_make_fake_resp(b""),
        ):
            success, msg = engine.test_connection()
        assert success is True
        assert "200" in msg

    def test_test_connection_200_empty_body_success_deepgram(self):
        engine = _engine("deepgram")
        with patch(
            "voice_typer.server.cloud_engines._opener.open",
            return_value=_make_fake_resp(b""),
        ):
            success, msg = engine.test_connection()
        assert success is True
        assert "200" in msg
