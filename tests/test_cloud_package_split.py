"""Tests for the cloud transport/retry/provider package split.

``voice_typer/server/cloud_engines.py`` keeps the ``CloudEngine``
orchestration (engine class, shared retry loop, connection probe) and
re-exports the stateless plumbing that now lives in
``voice_typer/server/cloud/``:

- ``_transport``  — pooled opener, response-body cap, WAV encoding,
  streaming multipart body.
- ``_retry``      — Retry-After parsing, HTTP-status → typed-error
  mapping.
- ``_defaults``   — per-provider endpoint/model defaults.
- ``_providers.openai``   — OpenAI-compatible multipart shaping.
- ``_providers.deepgram`` — listen-URL building + token validation.

These tests pin BOTH sides of the split:

1. the leaf modules behave correctly in isolation (pure functions, no
   network), and
2. the facade contract still holds — every legacy name resolves from
   ``voice_typer.server.cloud_engines``, the facade's ``_opener``
   attribute is the SAME object the transport owns (so instance-level
   ``patch("...cloud_engines._opener.open")`` keeps working), and
   REBINDING facade attributes (``_opener``, ``assert_url_allowed``)
   still steers the engine — the resolution path the abort/allowlist
   regression suites rely on.

All network I/O is mocked; no test here contacts a provider.
"""

from __future__ import annotations

import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from voice_typer.server.cloud import _defaults, _retry, _transport
from voice_typer.server.cloud._providers import deepgram as deepgram_provider, openai as openai_provider

# ── _transport: response-body cap ──────────────────────────────────


class TestReadCappedLeaf:
    def test_streams_chunks_until_eof(self):
        resp = MagicMock()
        resp.read.side_effect = [b"abc", b"de", b""]
        assert _transport._read_capped(resp, max_bytes=1024) == b"abcde"

    def test_cap_aborts_with_oom_message(self):
        resp = MagicMock()
        resp.read.side_effect = [b"x" * 65536, b"x" * 65536]
        with pytest.raises(RuntimeError, match="aborting to prevent OOM"):
            _transport._read_capped(resp, max_bytes=100_000)


# ── _transport: WAV encoding ───────────────────────────────────────


class TestAudioToWavBytesLeaf:
    def test_empty_input_is_44_byte_header(self):
        wav = _transport._audio_to_wav_bytes(np.zeros(0, dtype=np.float32))
        assert len(wav) == 44

    def test_header_parses_with_stdlib_wave(self):
        wav = _transport._audio_to_wav_bytes(np.zeros(0, dtype=np.float32), sample_rate=16000)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert (wf.getnchannels(), wf.getsampwidth(), wf.getnframes()) == (1, 2, 0)


# ── _transport: streaming multipart body ───────────────────────────


class TestStreamingMultipartBodyLeaf:
    PARTS = [b"AAA", b"BBBBB", b"CC"]

    def test_len_and_contains_without_consuming(self):
        body = _transport._StreamingMultipartBody(self.PARTS)
        assert len(body) == 10
        assert b"BBBBB" in body
        assert b"ZZ" not in body
        # __contains__ must not consume the stream.
        assert len(body) == 10

    def test_sized_reads_span_part_boundaries(self):
        body = _transport._StreamingMultipartBody(self.PARTS)
        assert body.read(4) == b"AAAB"
        assert len(body) == 6  # Content-Length tracks consumed bytes
        assert body.read(4) == b"BBBB"
        assert body.read(4) == b"CC"
        assert body.read(4) == b""  # EOF

    def test_read_all_and_readline(self):
        body = _transport._StreamingMultipartBody(self.PARTS)
        assert body.readline(-1) == b"AAABBBBBCC"
        fresh = _transport._StreamingMultipartBody(self.PARTS)
        assert fresh.read() == b"AAABBBBBCC"


# ── _retry: policy primitives ──────────────────────────────────────


class TestRetryPolicyLeaf:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (401, "CloudAuthError"),
            (403, "CloudAuthError"),
            (429, "CloudRateLimitError"),
            (500, "CloudServerError"),
            (599, "CloudServerError"),
            (404, "CloudEngineError"),
        ],
    )
    def test_http_status_maps_to_typed_error(self, code, expected):
        assert _retry._cloud_http_error_class(code).__name__ == expected

    def test_parse_retry_after_defaults_and_cap(self):
        assert _retry._parse_retry_after(None) == 2.0
        assert _retry._parse_retry_after("") == 2.0
        assert _retry._parse_retry_after("120") == 60.0
        assert _retry._parse_retry_after("7") == 7.0


# ── _defaults ──────────────────────────────────────────────────────


class TestProviderDefaultsLeaf:
    @pytest.mark.parametrize("provider", ["openai", "groq", "deepgram"])
    def test_known_providers_have_https_defaults(self, provider):
        entry = _defaults._PROVIDER_DEFAULTS[provider]
        assert entry["url"].startswith("https://")
        assert entry["model"]


# ── _providers.openai: multipart shaping ───────────────────────────


class TestOpenAIProviderShaping:
    def test_parts_order_and_fields(self):
        parts = openai_provider.build_multipart_parts(b"WAVDATA", "audio.wav", "BND", "whisper-1", "en-US")
        body = b"".join(parts)
        assert body.index(b'name="file"') < body.index(b"WAVDATA") < body.index(b'name="model"')
        assert b"whisper-1" in body
        assert b"en-US" in body
        assert b'name="response_format"' in body
        assert body.endswith(b"--BND--\r\n")

    def test_body_streams_without_materializing_list(self):
        body = openai_provider.build_multipart_body(b"WAVDATA", "audio.wav", "BND", "m", "en")
        assert isinstance(body, _transport._StreamingMultipartBody)
        assert b"WAVDATA" in body
        assert body.read() == b"".join(openai_provider.build_multipart_parts(b"WAVDATA", "audio.wav", "BND", "m", "en"))


# ── _providers.deepgram: listen-URL building ───────────────────────


class TestDeepgramProviderShaping:
    def test_url_encodes_query_parameters(self):
        url = deepgram_provider.build_listen_url("https://api.deepgram.com/v1/listen", "nova-2", "en-US")
        assert url == "https://api.deepgram.com/v1/listen?model=nova-2&language=en-US&punctuate=true"

    def test_injection_tokens_are_rejected(self):
        with pytest.raises(
            RuntimeError, match="Deepgram model name 'no&va=punctuate=false' contains invalid characters"
        ):
            deepgram_provider.build_listen_url("https://api.deepgram.com/v1/listen", "no&va=punctuate=false", "en")
        with pytest.raises(RuntimeError, match="Deepgram language 'e n' contains invalid characters"):
            deepgram_provider.build_listen_url("https://api.deepgram.com/v1/listen", "nova-2", "e n")


# ── facade contract ────────────────────────────────────────────────


class TestFacadeReExports:
    def test_every_legacy_name_resolves_from_cloud_engines(self):
        import voice_typer.server.cloud_engines as facade

        for name in (
            "CloudEngine",
            "CloudEngineError",
            "ConsentRequiredError",
            "CloudAuthError",
            "CloudRateLimitError",
            "CloudServerError",
            "CloudNetworkError",
            "CloudEmptyResponseError",
            "CloudConfigError",
            "CloudConsentRequiredError",
            "_opener",
            "_read_capped",
            "_parse_retry_after",
            "_cloud_http_error_class",
            "_audio_to_wav_bytes",
            "_StreamingMultipartBody",
            "_PROVIDER_DEFAULTS",
            "assert_url_allowed",
            "redact_secret",
            "redact_url",
            "time",
            "wave",
            "datetime",
            "timezone",
        ):
            assert hasattr(facade, name), f"facade lost legacy name {name!r}"

    def test_facade_shares_transport_singletons(self):
        import voice_typer.server.cloud_engines as facade

        # Same OBJECT (not a copy): patching ``facade._opener.open``
        # mutates the opener the retry loop uses.
        assert facade._opener is _transport._opener
        assert facade._read_capped is _transport._read_capped
        assert facade._parse_retry_after is _retry._parse_retry_after
        assert facade._PROVIDER_DEFAULTS is _defaults._PROVIDER_DEFAULTS

    def test_engine_class_is_defined_in_the_facade_module(self):
        import voice_typer.server.cloud_engines as facade

        assert facade.CloudEngine.__module__ == "voice_typer.server.cloud_engines"


def _fake_response(body: bytes) -> MagicMock:
    """Context-manager fake HTTP response for ``_opener.open``."""
    calls = {"n": 0}

    resp = MagicMock()
    resp.status = 200
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    def _read(size=-1):
        if calls["n"] == 0:
            calls["n"] += 1
            return body
        return b""

    resp.read.side_effect = _read
    return resp


class TestFacadeNamespaceStillSteersEngine:
    """Rebinding facade attributes must reach the engine — the contract
    the abort / allowlist / asr-setup regression suites depend on."""

    def test_facade_opener_rebinding_drives_transcribe(self, monkeypatch):
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(
            provider="openai",
            api_key="test-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            model="whisper-1",
            consent_given=True,
        )
        fake_opener = MagicMock()
        fake_opener.open.return_value = _fake_response(b'{"text": "hello facade"}')
        monkeypatch.setattr(facade, "_opener", fake_opener)

        result = engine.transcribe(np.zeros(1600, dtype=np.float32))

        assert result == "hello facade"
        assert fake_opener.open.call_count == 1

    def test_facade_assert_url_allowed_patch_reaches_send_path(self):
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(
            provider="openai",
            api_key="test-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            model="whisper-1",
            consent_given=True,
        )
        with (
            patch("voice_typer.server.cloud_engines.assert_url_allowed") as mock_allow,
            patch("voice_typer.server.cloud_engines._opener.open", return_value=_fake_response(b'{"text": "hi"}')),
        ):
            engine.transcribe(np.zeros(1600, dtype=np.float32))
        assert mock_allow.called, "allowlist check must resolve via the facade namespace"

    def test_opener_object_patch_via_facade_is_seen_by_transport_opener(self):
        """``patch('...cloud_engines._opener.open')`` mutates the SHARED
        opener object, so the transport-owned opener sees it too."""
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(
            provider="openai",
            api_key="test-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            model="whisper-1",
            consent_given=True,
        )
        with patch("voice_typer.server.cloud_engines._opener.open", return_value=_fake_response(b'{"text": "shared"}')):
            assert engine.transcribe(np.zeros(1600, dtype=np.float32)) == "shared"


class TestEngineDelegatesToProviderModules:
    def test_multipart_methods_delegate(self):
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(
            provider="openai", api_key="k", model="whisper-1", language="en", consent_given=True
        )
        body = engine._build_multipart_body(b"fake_wav_data", "audio.wav", "boundary123")
        assert isinstance(body, _transport._StreamingMultipartBody)
        assert b"fake_wav_data" in body
        assert b"whisper-1" in body
        assert b"boundary123" in body
        parts = engine._multipart_parts(b"fake_wav_data", "audio.wav", "boundary123")
        assert parts == openai_provider.build_multipart_parts(
            b"fake_wav_data", "audio.wav", "boundary123", "whisper-1", "en"
        )

    def test_deepgram_send_uses_provider_url_builder(self):
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            language="en-US",
            consent_given=True,
        )
        with patch(
            "voice_typer.server.cloud_engines._opener.open",
            return_value=_fake_response(b'{"results": {"channels": [{"alternatives": [{"transcript": "dg text"}]}]}}'),
        ) as mock_open:
            assert engine.transcribe(np.zeros(1600, dtype=np.float32)) == "dg text"
        requested_url = mock_open.call_args.args[0].full_url
        assert requested_url == "https://api.deepgram.com/v1/listen?model=nova-2&language=en-US&punctuate=true"

    def test_deepgram_model_injection_rejected_before_network(self):
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2&punctuate=false",
            consent_given=True,
        )
        with (
            patch("voice_typer.server.cloud_engines._opener.open", side_effect=AssertionError("no network expected")),
            # Token validation fires in the send path BEFORE the retry
            # loop, so the raw RuntimeError propagates (unchanged from
            # the pre-split behavior).
            pytest.raises(RuntimeError, match="invalid characters"),
        ):
            engine.transcribe(np.zeros(1600, dtype=np.float32))

    def test_provider_defaults_drive_engine_construction(self):
        import voice_typer.server.cloud_engines as facade

        engine = facade.CloudEngine(provider="groq", api_key="k", consent_given=True)
        assert engine.api_url == _defaults._PROVIDER_DEFAULTS["groq"]["url"]
        assert engine.model_name == _defaults._PROVIDER_DEFAULTS["groq"]["model"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
