"""Tests for voice_typer.cloud_engines — CloudEngine factory and API."""

from unittest.mock import MagicMock, patch

import pytest


class TestCloudEngineFactory:
    def test_create_openai_engine(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        assert engine.provider == "openai"
        assert engine.api_key == "test-key"
        assert "openai" in engine.api_url

    def test_create_groq_engine(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="groq", api_key="test-key", consent_given=True)
        assert engine.provider == "groq"
        assert "groq" in engine.api_url

    def test_create_deepgram_engine(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="deepgram", api_key="test-key", consent_given=True)
        assert engine.provider == "deepgram"
        assert "deepgram" in engine.api_url

    def test_custom_api_url(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="key", api_url="https://custom.api/v1", consent_given=True)
        assert engine.api_url == "https://custom.api/v1"


class TestCloudEngineProtocol:
    def test_is_loaded_with_key(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        assert engine.is_loaded is True

    def test_is_loaded_without_key(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="", consent_given=True)
        assert engine.is_loaded is False

    def test_load_noop(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        # Should not raise
        engine.load()

    def test_device_info(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        assert "openai" in engine.device_info

    def test_loaded_via(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="groq", api_key="test-key", model="whisper-large-v3", consent_given=True)
        assert "groq" in engine.loaded_via
        assert "whisper-large-v3" in engine.loaded_via

    def test_transcribe_empty_audio(self):
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        result = engine.transcribe(np.array([], dtype=np.float32))
        assert result == ""

    def test_transcribe_with_fallback(self):
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        # Should delegate to transcribe
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(engine, "_send_request", return_value="test text"):
            result = engine.transcribe_with_fallback(audio)
            assert result == "test text"

    def test_transcribe_with_fallback_uses_local_engine(self):
        """G4-H-18: when cloud transcribe raises and a local_engine is
        passed, the local engine's ``transcribe`` MUST be called with
        the audio and ``audio_stats`` kwarg, and its return value is
        surfaced to the caller.

        Pre-fix this code path existed but no caller wired
        ``local_engine=`` — the fallback was dead code.
        """
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)

        # Cloud transcribe raises (simulates network outage / 5xx).
        with patch.object(engine, "transcribe", side_effect=RuntimeError("cloud down")):
            local_engine = MagicMock()
            local_engine.transcribe.return_value = "local fallback text"
            result = engine.transcribe_with_fallback(audio, local_engine=local_engine, audio_stats=(0.01, 0.5, 50.0))

        assert result == "local fallback text"
        local_engine.transcribe.assert_called_once_with(audio, audio_stats=(0.01, 0.5, 50.0))

    def test_transcribe_with_fallback_uses_local_engine_factory(self):
        """G4-H-18: when ``local_engine`` is NOT passed but the engine
        was constructed with ``local_engine_factory``, the factory is
        invoked lazily to construct the local engine on fallback.
        """
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        local_engine = MagicMock()
        local_engine.transcribe.return_value = "factory fallback text"
        factory = MagicMock(return_value=local_engine)

        engine = CloudEngine(
            provider="openai",
            api_key="test-key",
            consent_given=True,
            local_engine_factory=factory,
        )
        audio = np.zeros(16000, dtype=np.float32)

        with patch.object(engine, "transcribe", side_effect=RuntimeError("cloud down")):
            result = engine.transcribe_with_fallback(audio, audio_stats=(0.02, 0.6, 40.0))

        assert result == "factory fallback text"
        # Factory called exactly once, lazily (only on failure).
        factory.assert_called_once_with()
        local_engine.transcribe.assert_called_once_with(audio, audio_stats=(0.02, 0.6, 40.0))

    def test_transcribe_with_fallback_factory_returning_none_skips_fallback(self):
        """G4-H-18: if the factory returns None (e.g. cold start, no
        whisper registered), the original cloud error is re-raised
        rather than masking it with a confusing "no local engine" error.
        """
        import numpy as np
        import pytest
        from voice_typer.server.cloud_engines import CloudEngine

        factory = MagicMock(return_value=None)
        engine = CloudEngine(
            provider="openai",
            api_key="test-key",
            consent_given=True,
            local_engine_factory=factory,
        )
        audio = np.zeros(16000, dtype=np.float32)

        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("cloud down")),
            pytest.raises(RuntimeError, match="cloud down"),
        ):
            engine.transcribe_with_fallback(audio)
        # Factory was invoked (lazy construction attempted).
        factory.assert_called_once_with()

    def test_transcribe_with_fallback_no_local_engine_reraises(self):
        """G4-H-18: when no local_engine and no factory, the original
        cloud error is re-raised unchanged.
        """
        import numpy as np
        import pytest
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)

        with (
            patch.object(engine, "transcribe", side_effect=RuntimeError("cloud down")),
            pytest.raises(RuntimeError, match="cloud down"),
        ):
            engine.transcribe_with_fallback(audio)


class TestCloudEngineCacheInvalidation:
    """G4-INVALIDATION: ``clear_cached_engine`` /
    ``clear_all_cached_engines`` / ``register_cached_cloud_engine`` /
    ``get_cached_cloud_engine`` provide a module-level cache of live
    CloudEngine instances so that ``delete_all_personal_data`` (and
    similar credential-revocation paths) can release stale engines
    that still hold the user's previous API key / consent flag.
    """

    def test_register_and_get_cached_engine(self):
        from voice_typer.server import cloud_engines
        from voice_typer.server.cloud_engines import (
            CloudEngine,
            get_cached_cloud_engine,
            register_cached_cloud_engine,
        )

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        try:
            register_cached_cloud_engine("openai", engine)
            assert get_cached_cloud_engine("openai") is engine
        finally:
            # Clean up module state for other tests.
            cloud_engines.clear_all_cached_engines()

    def test_clear_cached_engine_releases_instance(self):
        from voice_typer.server import cloud_engines
        from voice_typer.server.cloud_engines import (
            CloudEngine,
            clear_cached_engine,
            register_cached_cloud_engine,
        )

        engine = CloudEngine(provider="groq", api_key="test-key", consent_given=True)
        register_cached_cloud_engine("groq", engine)
        # Sanity check: cache holds the engine.
        assert cloud_engines.get_cached_cloud_engine("groq") is engine

        released = clear_cached_engine("groq")
        assert released is True
        # Cache no longer holds the engine.
        assert cloud_engines.get_cached_cloud_engine("groq") is None
        # unload() was called (CloudEngine sets _loaded=False).
        assert engine._loaded is False

    def test_clear_cached_engine_returns_false_when_not_cached(self):
        from voice_typer.server.cloud_engines import clear_cached_engine

        # Use a provider name that hasn't been registered.
        assert clear_cached_engine("deepgram-not-cached") is False

    def test_clear_all_cached_engines_returns_count(self):
        from voice_typer.server.cloud_engines import (
            CloudEngine,
            clear_all_cached_engines,
            register_cached_cloud_engine,
        )

        # Start clean.
        clear_all_cached_engines()
        try:
            register_cached_cloud_engine("openai", CloudEngine(provider="openai", api_key="k", consent_given=True))
            register_cached_cloud_engine("groq", CloudEngine(provider="groq", api_key="k", consent_given=True))
            released = clear_all_cached_engines()
            assert released == 2
            # Idempotent: second call releases nothing.
            assert clear_all_cached_engines() == 0
        finally:
            clear_all_cached_engines()

    def test_register_none_clears_single_provider(self):
        """Passing engine=None to register clears that provider only."""
        from voice_typer.server import cloud_engines
        from voice_typer.server.cloud_engines import (
            CloudEngine,
            clear_all_cached_engines,
            register_cached_cloud_engine,
        )

        clear_all_cached_engines()
        try:
            e1 = CloudEngine(provider="openai", api_key="k", consent_given=True)
            e2 = CloudEngine(provider="groq", api_key="k", consent_given=True)
            register_cached_cloud_engine("openai", e1)
            register_cached_cloud_engine("groq", e2)
            # Clear only openai via register(None).
            register_cached_cloud_engine("openai", None)
            assert cloud_engines.get_cached_cloud_engine("openai") is None
            assert cloud_engines.get_cached_cloud_engine("groq") is e2
        finally:
            clear_all_cached_engines()


class TestCloudEngineMultipart:
    def test_build_multipart_body(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", model="whisper-1", consent_given=True)
        body = engine._build_multipart_body(b"fake_wav_data", "audio.wav", "boundary123")
        assert b"fake_wav_data" in body
        assert b"whisper-1" in body
        assert b"boundary123" in body


class TestCloudEngineTestConnection:
    def test_test_connection_no_key(self):
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="", consent_given=True)
        success, msg = engine.test_connection()
        assert success is False
        assert "API key" in msg


# ── RELIABILITY-004: URL allowlist + API key redaction ───────────────────


class TestCloudEngineUrlAllowlist:
    """RELIABILITY-004: CloudEngine must refuse to send audio to any
    URL whose host is not in the trusted allowlist.  This is the
    last-line defense against SEC-002 endpoint-swap attacks: even
    if an attacker finds a way to write ``config.cloud_api_url``,
    the cloud engine itself refuses to send audio to an untrusted
    host."""

    def test_openai_compatible_rejects_untrusted_url(self):
        """_send_openai_compatible raises ValueError before any HTTP
        request is made when api_url points to an untrusted host."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="sk-test",
            api_url="https://evil.example.com/exfiltrate",
            model="whisper-1",
            consent_given=True,
        )
        with pytest.raises(ValueError, match="not in the trusted allowlist"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_deepgram_rejects_untrusted_url(self):
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://evil.example.com/v1/listen",
            model="nova-2",
            consent_given=True,
        )
        with pytest.raises(ValueError, match="not in the trusted allowlist"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_openai_default_url_allowed(self):
        """The default provider URL must pass the allowlist check.

        WR-9: patch ``_opener.open`` so the test never makes a real
        network egress. The patch raises ``URLError`` so the engine
        raises ``RuntimeError`` (the existing assertion). We then
        assert the mock was called with a Request whose ``full_url`` is
        the OpenAI default — proving the allowlist let the URL through.
        """
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="sk-test", consent_given=True)
        # Patch _opener.open to raise URLError so the engine raises
        # RuntimeError (no real network egress).
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                side_effect=URLError("test-isolated"),
            ) as mock_open,
            pytest.raises(RuntimeError),
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        # The mock must have been called — proving the allowlist let
        # the default OpenAI URL through. The engine retries 3x on
        # URLError, so call_count is >= 1 (typically 3).
        assert mock_open.call_count >= 1
        req = mock_open.call_args[0][0]
        assert req.full_url == "https://api.openai.com/v1/audio/transcriptions"

    def test_localhost_self_hosted_allowed(self):
        """Local self-hosted endpoints (Ollama, vLLM) must work.

        WR-9: patch ``_opener.open`` so the test never makes a real
        network egress. The patch raises ``URLError`` so the engine
        raises ``RuntimeError`` (the existing assertion). We then
        assert the mock was called with a Request whose ``full_url`` is
        the localhost URL — proving the allowlist let it through.
        """
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="sk-test",
            api_url="http://localhost:11434/v1/audio/transcriptions",
            consent_given=True,
        )
        # Patch _opener.open to raise URLError (no real network egress).
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                side_effect=URLError("test-isolated"),
            ) as mock_open,
            pytest.raises(RuntimeError),
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        # The mock must have been called — proving the allowlist let
        # the localhost URL through. The engine retries 3x on URLError,
        # so call_count is >= 1 (typically 3).
        assert mock_open.call_count >= 1
        req = mock_open.call_args[0][0]
        assert req.full_url == "http://localhost:11434/v1/audio/transcriptions"

    def test_test_connection_rejects_untrusted_url(self):
        """test_connection returns (False, msg) for untrusted URLs."""
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="sk-test",
            api_url="https://evil.example.com/exfiltrate",
            consent_given=True,
        )
        success, msg = engine.test_connection()
        assert success is False
        assert "not in the trusted allowlist" in msg


class TestCloudEngineKeyRedaction:
    """RELIABILITY-004: error messages from the HTTP layer must not
    leak the API key.  ``URLError`` exceptions can include the full
    request URL (with query string) in some Python versions; the
    cloud engine must redact any secret-looking substring before
    logging or returning the message."""

    def test_runtime_error_message_excludes_key(self):
        """The RuntimeError raised on HTTP failure must not contain
        the API key, even if the underlying URLError did."""
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF",
            consent_given=True,
        )
        # Patch _opener.open to raise a URLError whose str includes the
        # request URL (which the engine constructs with the key in
        # the Authorization header — not the URL, but if a future
        # change puts the key in the URL this test will catch it).
        # SEC-audit-006: cloud_engines now uses ``_opener.open()`` (no
        # redirect handler) instead of ``urlopen()`` for all HTTP egress
        # paths, so we patch the opener's open method directly.
        key = engine.api_key
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                side_effect=URLError("connection refused to https://api.openai.com/"),
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert key not in str(exc_info.value)

    def test_test_connection_redacts_key_in_message(self):
        """test_connection's failure message must not contain the key."""
        from urllib.error import URLError

        from voice_typer.server.cloud_engines import CloudEngine

        key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        engine = CloudEngine(provider="openai", api_key=key, consent_given=True)
        # SEC-audit-006: test_connection now uses ``_opener.open()`` too.
        with patch(
            "voice_typer.server.cloud_engines._opener.open",
            side_effect=URLError(f"refused: Bearer {key}"),
        ):
            success, msg = engine.test_connection()
        assert success is False
        assert key not in msg


# ── SEC-005: Deepgram URL parameter injection ────────────────────────────


class TestDeepgramUrlParameterInjection:
    """SEC-005: previously the Deepgram URL was built via f-string
    interpolation of ``self.model_name`` and ``self.language``,
    allowing an attacker to inject extra query parameters via
    ``config.cloud_model`` (e.g. ``"nova-2&punctuate=false"``).

    The fix URL-encodes the parameters AND enforces a conservative
    alphanumeric allowlist, since Deepgram's identifiers never
    legitimately contain special characters."""

    def test_rejects_model_name_with_ampersand(self):
        """A model_name containing '&' must be rejected before any
        HTTP request is made."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2&punctuate=false",  # injection attempt
            consent_given=True,
        )
        with pytest.raises(RuntimeError, match="invalid characters"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_rejects_language_with_special_chars(self):
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            language="en&smart_format=true",  # injection attempt
            consent_given=True,
        )
        with pytest.raises(RuntimeError, match="invalid characters"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_accepts_valid_model_name(self):
        """Valid model names like 'nova-2' must pass validation.

        WR-9: patch ``_opener.open`` so the test never makes a real
        network egress. The patch raises ``URLError`` so the engine
        raises ``RuntimeError`` (the existing assertion). We then
        assert the mock was called with a Request whose ``full_url`` is
        the Deepgram URL — proving both validation and the allowlist
        let the request through.
        """
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            language="en",
            consent_given=True,
        )
        # Patch _opener.open to raise URLError (no real network egress).
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                side_effect=URLError("test-isolated"),
            ) as mock_open,
            pytest.raises(RuntimeError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        # The mock must have been called — proving validation passed
        # and the allowlist let the Deepgram URL through. The engine
        # retries 3x on URLError, so call_count is >= 1 (typically 3).
        assert mock_open.call_count >= 1
        req = mock_open.call_args[0][0]
        # Deepgram appends query parameters (model, language, punctuate)
        # to the base URL. Assert the base URL is present (prefix check).
        assert req.full_url.startswith("https://api.deepgram.com/v1/listen")
        # "invalid characters" appears only when validation fails;
        # a URLError-based RuntimeError means validation passed.
        assert "invalid characters" not in str(exc_info.value)

    def test_rejects_path_traversal_in_model(self):
        """Model name containing '../' must be rejected."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="deepgram",
            api_key="test-token",
            api_url="https://api.deepgram.com/v1/listen",
            model="../../etc/passwd",
            consent_given=True,
        )
        with pytest.raises(RuntimeError, match="invalid characters"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))


# ── PI-17: typed cloud/LLM exception hierarchy ───────────────────────────


class TestCloudEngineTypedExceptions:
    """PI-17: ``CloudEngine`` raises typed ``CloudEngineError`` subclasses
    (``CloudAuthError`` / ``CloudRateLimitError`` / ``CloudServerError`` /
    ``CloudNetworkError`` / ``CloudConfigError``) instead of generic
    ``RuntimeError``, so the IPC layer can ``isinstance``-check and emit
    a distinct IPC error code for each category.

    These tests pin the contract: the typed exception is raised, AND the
    underlying ``HTTPError`` / ``URLError`` is preserved on ``__cause__``
    so server-side logging retains the full traceback for diagnosis.
    """

    def test_cloud_engine_raises_auth_error_on_401(self):
        """A 401 HTTPError from the cloud provider raises
        ``CloudAuthError`` (PI-17). The IPC layer maps this to
        ``server.cloud_auth_failed`` so the renderer can prompt the
        user to re-enter their API key.
        """
        import io
        from urllib.error import HTTPError

        import numpy as np
        from voice_typer.server.asr_errors import CloudAuthError
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="revoked-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        http_err = HTTPError(
            url="https://api.openai.com/v1/audio/transcriptions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "invalid_api_key"}'),
        )
        with (
            patch("voice_typer.server.cloud_engines._opener.open", side_effect=http_err),
            pytest.raises(CloudAuthError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        # The original HTTPError must be chained on __cause__ so the
        # server-side ERROR log (with exc_info=True) retains the full
        # traceback for diagnosis.
        assert isinstance(exc_info.value.__cause__, HTTPError)
        assert exc_info.value.__cause__.code == 401

    def test_cloud_engine_raises_rate_limit_on_429(self):
        """A 429 HTTPError from the cloud provider raises
        ``CloudRateLimitError`` (PI-17) — AFTER the retry budget is
        exhausted (the engine retries 429 once honoring Retry-After).
        """
        import io
        from email.message import Message
        from urllib.error import HTTPError

        import numpy as np
        from voice_typer.server.asr_errors import CloudRateLimitError
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="valid-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        # Use a real Message as headers so ``exc.headers.get(...)`` works
        # (the production code reads ``Retry-After``).
        hdrs = Message()
        hdrs["Retry-After"] = "0"
        http_err = HTTPError(
            url="https://api.openai.com/v1/audio/transcriptions",
            code=429,
            msg="Too Many Requests",
            hdrs=hdrs,
            fp=io.BytesIO(b'{"error": "rate_limit_exceeded"}'),
        )
        # All 3 retry attempts return 429 → CloudRateLimitError after
        # the retry budget is exhausted. Patch sleep so the test
        # doesn't wait for the Retry-After backoff.
        with (
            patch("voice_typer.server.cloud_engines._opener.open", side_effect=http_err),
            patch("time.sleep"),
            pytest.raises(CloudRateLimitError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert isinstance(exc_info.value.__cause__, HTTPError)
        assert exc_info.value.__cause__.code == 429

    def test_cloud_engine_raises_server_error_on_500(self):
        """A 5xx HTTPError from the cloud provider raises
        ``CloudServerError`` (PI-17).
        """
        import io
        from urllib.error import HTTPError

        import numpy as np
        from voice_typer.server.asr_errors import CloudServerError
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="valid-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        http_err = HTTPError(
            url="https://api.openai.com/v1/audio/transcriptions",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"error": "server_error"}'),
        )
        with (
            patch("voice_typer.server.cloud_engines._opener.open", side_effect=http_err),
            pytest.raises(CloudServerError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert isinstance(exc_info.value.__cause__, HTTPError)
        assert exc_info.value.__cause__.code == 503

    def test_cloud_engine_raises_network_error_on_urlerror(self):
        """A ``URLError`` (timeout / DNS / connection reset) raises
        ``CloudNetworkError`` (PI-17) — AFTER the 3-attempt retry budget
        is exhausted.
        """
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.asr_errors import CloudNetworkError
        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(
            provider="openai",
            api_key="valid-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        # All 3 retry attempts fail with URLError → CloudNetworkError.
        # Patch sleep so the test doesn't wait for the exponential
        # backoff (0.5s + 1.0s = 1.5s real time).
        with (
            patch(
                "voice_typer.server.cloud_engines._opener.open",
                side_effect=URLError("test-isolated-network-error"),
            ),
            patch("time.sleep"),
            pytest.raises(CloudNetworkError) as exc_info,
        ):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert isinstance(exc_info.value.__cause__, URLError)

    def test_cloud_engine_raises_config_error_on_missing_key(self):
        """A cloud engine constructed without an API key raises
        ``CloudConfigError`` at transcribe time (PI-17 / PI-24). The
        cross-field validator at
        ``config_validators._check_cross_field_cloud_config`` catches
        the common case at save time; this runtime check stays as
        defense-in-depth for the case where the key was revoked
        between save and transcribe.
        """
        import numpy as np
        from voice_typer.server.asr_errors import CloudConfigError
        from voice_typer.server.cloud_engines import CloudEngine

        # consent_given=True but api_key="" → is_loaded=False →
        # transcribe() raises CloudConfigError.
        engine = CloudEngine(
            provider="openai",
            api_key="",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )
        with pytest.raises(CloudConfigError, match="missing API key"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))
