"""Tests for voice_typer.llm_polish: LLMPolisher presets and API."""

import json
from unittest.mock import MagicMock, patch

import pytest


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
        body = json.dumps({"choices": [{"message": {"content": "Polished text here"}}]}).encode("utf-8")
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
        body = json.dumps({"choices": [{"message": {"content": "Casual text"}}]}).encode("utf-8")
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
        body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode("utf-8")
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


class TestLLMPolishUrlAllowlist:
    """
    RELIABILITY-004: LLMPolisher must refuse to send transcribed
    This is the last-line defense against SEC-002 endpoint-swap
    """

    def test_polish_rejects_untrusted_url(self):
        """polish() must raise / return original when the URL is"""
        from voice_typer.server.llm_polish import LLMPolisher

        p = LLMPolisher(
            api_key="sk-test",
            api_url="https://evil.example.com/exfiltrate",
            enabled=True,
        )
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
        from urllib.error import URLError

        from voice_typer.server.llm_polish import LLMPolisher

        p = LLMPolisher(api_key="sk-test", enabled=True)
        # Default URL is api.openai.com, allowlist check passes,
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            side_effect=URLError("no network"),
        ):
            success, msg = p.test_connection()
        assert success is False
        assert "allowlist" not in msg

    def test_polish_redacts_key_in_log(self, caplog):
        """When polish() catches an exception, the log message must"""
        import logging

        from voice_typer.server.llm_polish import LLMPolisher

        key = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        p = LLMPolisher(api_key=key, enabled=True)
        # Force an exception by patching _call_api to raise
        with (
            patch.object(p, "_call_api", side_effect=RuntimeError(f"auth failed: {key}")),
            caplog.at_level(logging.WARNING, logger="voice_typer.server.llm_polish"),
        ):
            result = p.polish("Hello, world!")
        assert result == "Hello, world!"  # original returned
        # Verify the key does not appear in any log record
        for record in caplog.records:
            assert key not in record.getMessage()


def _make_mock_response(content: str = "Polished") -> MagicMock:
    """Build a mock HTTP response suitable for ``_read_capped``'s"""
    mock_response = MagicMock()
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
    mock_response.read.side_effect = [body, b""]
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


# MAX_INPUT_CHARS guard ──────────────────────────────────────


class TestMaxInputChars:
    """XV-76: polish() must short-circuit when input > MAX_INPUT_CHARS."""

    def test_max_input_chars_constant(self):
        from voice_typer.server.llm_polish import MAX_INPUT_CHARS

        assert MAX_INPUT_CHARS == 8000

    def test_polish_returns_text_unchanged_when_input_exceeds_cap(self, polisher):
        """XV-76 (a): input > MAX_INPUT_CHARS → return original text,"""
        from voice_typer.server.llm_polish import MAX_INPUT_CHARS

        oversized = "word " * ((MAX_INPUT_CHARS // 5) + 1)  # ~8005 chars
        assert len(oversized) > MAX_INPUT_CHARS

        with patch("voice_typer.server.llm_polish._opener.open") as mock_open:
            result = polisher.polish(oversized)

        # Must return the input unchanged, no API call, no transformation.
        assert result == oversized
        # CRITICAL: no API call must have been made.
        mock_open.assert_not_called()

    def test_polish_still_calls_api_at_exactly_cap(self, polisher):
        """the lower side). This guards against an off-by-one regression"""
        from voice_typer.server.llm_polish import MAX_INPUT_CHARS

        boundary_text = "a" * MAX_INPUT_CHARS
        assert len(boundary_text) == MAX_INPUT_CHARS

        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("Polished"),
        ) as mock_open:
            result = polisher.polish(boundary_text)

        assert result == "Polished"
        mock_open.assert_called_once()

    def test_polish_logs_skip_when_oversized(self, polisher, caplog):
        """XV-76: the skip must be logged at INFO level with the input"""
        import logging

        from voice_typer.server.llm_polish import MAX_INPUT_CHARS

        oversized = "x" * (MAX_INPUT_CHARS + 100)

        with (
            patch("voice_typer.server.llm_polish._opener.open"),
            caplog.at_level(logging.INFO, logger="voice_typer.server.llm_polish"),
        ):
            result = polisher.polish(oversized)

        assert result == oversized
        skip_records = [r for r in caplog.records if "Skipping polish" in r.getMessage()]
        assert len(skip_records) == 1
        msg = skip_records[0].getMessage()
        # Must include the actual input length and the cap.
        assert str(len(oversized)) in msg
        assert str(MAX_INPUT_CHARS) in msg


class TestConfigurableTimeout:
    """XV-75: _call_api must use a configurable timeout, defaulting to"""

    def test_default_timeout_s_constant(self):
        from voice_typer.server.llm_polish import LLMPolisher

        assert LLMPolisher.DEFAULT_TIMEOUT_S == 10

    def test_call_api_uses_10s_default_timeout(self, polisher):
        """explicit timeout_s, the underlying _opener.open must receive"""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            polisher._call_api(
                "Hello world this is a test of the polish path",
                "You are a text editor.",
            )

        mock_open.assert_called_once()
        _pos_args, kwargs = mock_open.call_args
        assert kwargs.get("timeout") == 10, (
            f"Expected timeout=10 (DEFAULT_TIMEOUT_S), got timeout={kwargs.get('timeout')!r}"
        )

    def test_polish_passes_default_timeout_to_call_api(self, polisher):
        """The default timeout flows from polish() (no timeout_s kwarg)"""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("Polished"),
        ) as mock_open:
            polisher.polish("This is some raw transcribed text that needs polishing")

        mock_open.assert_called_once()
        _pos_args, kwargs = mock_open.call_args
        assert kwargs.get("timeout") == 10

    def test_polish_timeout_s_override_is_propagated(self, polisher):
        """XV-75: an explicit timeout_s kwarg on polish() must reach"""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("Polished"),
        ) as mock_open:
            polisher.polish(
                "This is some raw transcribed text that needs polishing",
                timeout_s=4.5,
            )

        mock_open.assert_called_once()
        _pos_args, kwargs = mock_open.call_args
        assert kwargs.get("timeout") == 4.5

    def test_call_api_timeout_s_override_is_propagated(self, polisher):
        """Same as above but exercising _call_api directly."""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            polisher._call_api(
                "Hello world this is a test of the polish path",
                "You are a text editor.",
                timeout_s=7.0,
            )

        mock_open.assert_called_once()
        _pos_args, kwargs = mock_open.call_args
        assert kwargs.get("timeout") == 7.0

    def test_call_api_does_not_use_30s_hardcoded_timeout(self, polisher):
        """appear as the timeout kwarg, regardless of how _call_api is"""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            polisher._call_api(
                "Hello world this is a test of the polish path",
                "You are a text editor.",
            )

        _pos_args, kwargs = mock_open.call_args
        assert kwargs.get("timeout") != 30, "XV-75 regression: _call_api still uses the old hard-coded 30s timeout"


class TestFlatMaxTokens:
    """XV-76: max_tokens is now a flat 1024 (the previous"""

    @staticmethod
    def _completion_budget(payload: dict) -> int:
        if "max_completion_tokens" in payload:
            return payload["max_completion_tokens"]
        return payload["max_tokens"]

    def test_max_tokens_is_flat_1024_for_short_text(self, polisher):
        """ceiling) must produce max_tokens=1024 in the request payload —"""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            polisher._call_api(
                "Hello world test",  # 16 chars -> old formula: 16*2+256=288
                "You are a text editor.",
            )

        mock_open.assert_called_once()
        req = mock_open.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        assert self._completion_budget(payload) == 1024, f"Expected flat completion budget=1024, got {payload!r}"

    def test_max_tokens_is_flat_1024_for_long_text(self, polisher):
        """~1920-char ceiling where it always returned 4096) must now"""
        # (which kicks in at len(text) = (4096 - 256) / 2 = 1920 chars).
        long_text = "word " * 1000  # 5000 chars
        assert len(long_text) > 1920

        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            polisher._call_api(long_text, "You are a text editor.")

        mock_open.assert_called_once()
        req = mock_open.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        assert self._completion_budget(payload) == 1024, (
            f"Expected flat completion budget=1024 (old formula would have yielded 4096 here), got {payload!r}"
        )

    def test_max_tokens_no_longer_depends_on_input_length(self, polisher):
        """XV-76 (c) robustness: max_tokens must be identical for"""
        observed_values = set()
        for text in (
            "a" * 10,
            "b" * 1000,
            "c" * 5000,
        ):
            with patch(
                "voice_typer.server.llm_polish._opener.open",
                return_value=_make_mock_response("OK"),
            ) as mock_open:
                polisher._call_api(text, "You are a text editor.")
            req = mock_open.call_args.args[0]
            payload = json.loads(req.data.decode("utf-8"))
            observed_values.add(self._completion_budget(payload))

        assert observed_values == {1024}, (
            f"max_tokens varied with input length: {observed_values!r} (expected constant {{1024}})"
        )


class TestRedactPiiFailClosed:
    """When ``redact_pii`` raises inside ``_call_api``, the pre-API"""

    def test_call_api_skips_api_call_when_redact_pii_raises(self, polisher):
        """when ``redact_pii`` raises, ``_call_api`` must"""
        from voice_typer.server import security

        original = "Hello world this is a test of the polish path"
        with (
            patch.object(security, "redact_pii", side_effect=RuntimeError("boom")),
            patch("voice_typer.server.llm_polish._opener.open") as mock_open,
        ):
            result = polisher._call_api(original, "You are a text editor.")

        # Must return the original text UNPOLISHED.
        assert result == original
        # text must NOT be sent to the LLM endpoint.
        mock_open.assert_not_called()

    def test_polish_skips_api_call_when_redact_pii_raises(self, polisher):
        """the fail-closed behavior must propagate through"""
        from voice_typer.server import security

        original = "Hello world this is a test of the polish path"
        with (
            patch.object(security, "redact_pii", side_effect=RuntimeError("boom")),
            patch("voice_typer.server.llm_polish._opener.open") as mock_open,
        ):
            result = polisher.polish(original)

        assert result == original
        mock_open.assert_not_called()

    def test_redact_pii_failure_logs_at_warning_not_debug(self, polisher, caplog):
        """the ``redact_pii`` failure must be logged at"""
        import logging

        from voice_typer.server import security

        with (
            patch.object(security, "redact_pii", side_effect=RuntimeError("boom")),
            patch("voice_typer.server.llm_polish._opener.open"),
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.llm_polish"),
        ):
            polisher._call_api(
                "Hello world this is a test of the polish path",
                "You are a text editor.",
            )

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "redact_pii" in r.getMessage()]
        assert len(warning_records) == 1, (
            f"Expected exactly 1 WARNING record about redact_pii failure, got {warning_records!r}"
        )

    def test_redact_pii_success_still_calls_api(self, polisher):
        """regression guard: when ``redact_pii`` succeeds (even"""
        from voice_typer.server import security

        original = "Hello world this is a test of the polish path"
        # ``redact_pii`` returns the input unchanged (no PII found)
        with (
            patch.object(security, "redact_pii", return_value=original),
            patch(
                "voice_typer.server.llm_polish._opener.open",
                return_value=_make_mock_response("Polished"),
            ) as mock_open,
        ):
            result = polisher._call_api(original, "You are a text editor.")

        assert result == "Polished"
        mock_open.assert_called_once()

    def test_redact_pii_failure_returns_original_even_with_response_mocked(self, polisher):
        """return a successful response (which would normally produce"""
        from voice_typer.server import security

        original = "Hello world this is a test of the polish path"
        with (
            patch.object(security, "redact_pii", side_effect=RuntimeError("boom")),
            patch(
                "voice_typer.server.llm_polish._opener.open",
                return_value=_make_mock_response("SHOULD NOT BE REACHED"),
            ) as mock_open,
        ):
            result = polisher._call_api(original, "You are a text editor.")

        # Must return the original text, NOT the mocked "Polished"
        assert result == original
        assert result != "SHOULD NOT BE REACHED"
        mock_open.assert_not_called()


# HTTPError / URLError / generic-exception branch coverage ───────


class TestCallApiHttpErrorBranches:
    """``_call_api`` HTTPError / URLError / generic-exception"""

    def test_call_api_http_500_raises_cloud_server_error(self, polisher):
        """5xx → ``CloudServerError`` (llm_polish.py:379-380)."""
        from urllib.error import HTTPError

        from voice_typer.server.asr_errors import CloudServerError

        err = HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        with (
            patch("voice_typer.server.llm_polish._opener.open", side_effect=err),
            pytest.raises(CloudServerError, match=r"HTTP 500"),
        ):
            polisher._call_api("Hello world test", "You are a text editor.")

    def test_call_api_http_401_raises_cloud_engine_error(self, polisher):
        """4xx (401) → ``CloudEngineError`` (llm_polish.py:381)."""
        from urllib.error import HTTPError

        from voice_typer.server.asr_errors import (
            CloudEngineError,
            CloudNetworkError,
            CloudServerError,
        )

        err = HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        with (
            patch("voice_typer.server.llm_polish._opener.open", side_effect=err),
            pytest.raises(CloudEngineError, match=r"HTTP 401") as exc_info,
        ):
            polisher._call_api("Hello world test", "You are a text editor.")

        # CloudServerError is a subclass of CloudEngineError; assert
        assert not isinstance(exc_info.value, CloudServerError), (
            "HTTP 401 must raise the base CloudEngineError, NOT CloudServerError "
            "(CloudServerError is reserved for 5xx). A regression here would "
            "cause the renderer to show 'server error, retry' for an auth "
            "failure, hiding the real cause (invalid API key)."
        )
        # And it must NOT be mis-mapped to CloudNetworkError (which
        assert not isinstance(exc_info.value, CloudNetworkError), (
            "HTTP 401 (HTTPError) must NOT be caught by the except URLError "
            "branch, the except HTTPError branch MUST appear first in the "
            "source. A regression here would mis-map 4xx HTTP errors to "
            "CloudNetworkError (network error) instead of CloudEngineError."
        )

    def test_call_api_url_error_raises_cloud_network_error(self, polisher):
        """URLError → ``CloudNetworkError`` (llm_polish.py:382-388)."""
        from urllib.error import URLError

        from voice_typer.server.asr_errors import CloudNetworkError

        err = URLError("getaddrinfo failed")
        with (
            patch("voice_typer.server.llm_polish._opener.open", side_effect=err),
            pytest.raises(CloudNetworkError, match=r"LLM API error"),
        ):
            polisher._call_api("Hello world test", "You are a text editor.")

    def test_call_api_generic_exception_raises_cloud_engine_error(self, polisher):
        """Generic ``Exception`` → ``CloudEngineError``"""
        from voice_typer.server.asr_errors import (
            CloudEngineError,
            CloudNetworkError,
            CloudServerError,
        )

        with (
            patch(
                "voice_typer.server.llm_polish._opener.open",
                side_effect=ValueError("unexpected decode error"),
            ),
            pytest.raises(CloudEngineError, match=r"LLM API error") as exc_info,
        ):
            polisher._call_api("Hello world test", "You are a text editor.")

        # The generic exception must NOT be mis-mapped to a more
        assert not isinstance(exc_info.value, CloudServerError), (
            "A generic ValueError must be mapped to the base CloudEngineError, "
            "not CloudServerError (which is reserved for HTTP 5xx)."
        )
        assert not isinstance(exc_info.value, CloudNetworkError), (
            "A generic ValueError must be mapped to the base CloudEngineError, "
            "not CloudNetworkError (which is reserved for URLError)."
        )


class TestEndpointAwareSamplingParams:
    """non-default ``temperature``, first-party endpoints get"""

    def _sent_body(self, mock_open):
        req = mock_open.call_args[0][0]
        return json.loads(req.data.decode("utf-8"))

    def test_openai_endpoint_uses_max_completion_tokens(self, polisher):
        """api.openai.com (the fixture URL): no ``max_tokens``, no"""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            polisher._call_api("Hello world this is a test", "You are a text editor.")

        body = self._sent_body(mock_open)
        assert body["max_completion_tokens"] == 1024
        assert "max_tokens" not in body
        assert body["temperature"] == 1

    def test_third_party_endpoint_keeps_legacy_params(self):
        """Groq/Ollama-style endpoints keep ``max_tokens`` + 0.3."""
        from voice_typer.server.llm_polish import LLMPolisher

        proxy = LLMPolisher(
            api_key="test-key",
            api_url="https://api.groq.com/openai/v1/chat/completions",
            model="llama-3.3-70b-versatile",
            preset="professional",
            enabled=True,
        )
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("OK"),
        ) as mock_open:
            proxy._call_api("Hello world this is a test", "You are a text editor.")

        body = self._sent_body(mock_open)
        assert body["max_tokens"] == 1024
        assert "max_completion_tokens" not in body
        assert body["temperature"] == 0.3

    def test_endpoint_detection(self):
        from voice_typer.server.llm_polish import _is_openai_first_party_endpoint

        assert _is_openai_first_party_endpoint("https://api.openai.com/v1/chat/completions")
        assert _is_openai_first_party_endpoint("https://API.OPENAI.COM/v1")
        assert not _is_openai_first_party_endpoint("https://api.groq.com/openai/v1/chat/completions")
        assert not _is_openai_first_party_endpoint("http://localhost:11434/v1/chat/completions")
        assert not _is_openai_first_party_endpoint("not a url at all")
