"""Tests for XV-75 and XV-76 fixes in voice_typer.server.llm_polish.

XV-75 (High): ``_call_api`` previously hard-coded ``_opener.open(req,
timeout=30)`` which blocked the transcription thread for up to 30s. The
fix introduces a configurable ``timeout_s`` parameter (default 10s,
``DEFAULT_TIMEOUT_S``) on both ``polish()`` and ``_call_api``.

XV-76 (Medium): ``polish()`` had no upper bound on input size — 30k+
char dictations would ship in full to the LLM endpoint, and the
``max_tokens = min(4096, len(text) * 2 + 256)`` formula was dead code
above ~1920 chars (always hit the 4096 ceiling). The fix introduces a
``MAX_INPUT_CHARS = 8000`` module-level constant that short-circuits
``polish()`` before the API call, and flattens ``max_tokens`` to a flat
``1024``.

These tests mock ``urllib`` (via ``_opener.open``) — no real network
calls are made.
"""

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


def _make_mock_response(content: str = "Polished") -> MagicMock:
    """Build a mock HTTP response suitable for ``_read_capped``'s
    read-in-a-loop pattern (returns body on first call, b"" after)."""
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
        """XV-76 (a): input > MAX_INPUT_CHARS → return original text,
        NO API call made (otherwise 30k+ char dictations ship in full)."""
        from voice_typer.server.llm_polish import MAX_INPUT_CHARS

        oversized = "word " * ((MAX_INPUT_CHARS // 5) + 1)  # ~8005 chars
        assert len(oversized) > MAX_INPUT_CHARS

        with patch("voice_typer.server.llm_polish._opener.open") as mock_open:
            result = polisher.polish(oversized)

        # Must return the input unchanged — no API call, no transformation.
        assert result == oversized
        # CRITICAL: no API call must have been made.
        mock_open.assert_not_called()

    def test_polish_still_calls_api_at_exactly_cap(self, polisher):
        """Boundary: input length == MAX_INPUT_CHARS is allowed (the
        guard is strict-greater-than, so the boundary is inclusive on
        the lower side). This guards against an off-by-one regression
        that would let through exactly-MAX_INPUT_CHARS inputs but block
        MAX_INPUT_CHARS+1 (correct) vs. block MAX_INPUT_CHARS (wrong,
        would skip legitimate inputs at the boundary)."""
        from voice_typer.server.llm_polish import MAX_INPUT_CHARS

        # Build a text of exactly MAX_INPUT_CHARS that also passes the
        # 5-char-stripped minimum guard.
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
        """XV-76: the skip must be logged at INFO level with the input
        length and the cap, so operators can see why polish was skipped."""
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


# configurable timeout (default 10s) ────────────────────────


class TestConfigurableTimeout:
    """XV-75: _call_api must use a configurable timeout, defaulting to
    DEFAULT_TIMEOUT_S (10s), instead of the previous hard-coded 30s."""

    def test_default_timeout_s_constant(self):
        from voice_typer.server.llm_polish import LLMPolisher

        assert LLMPolisher.DEFAULT_TIMEOUT_S == 10

    def test_call_api_uses_10s_default_timeout(self, polisher):
        """XV-75 (b): when polish() / _call_api() is invoked without an
        explicit timeout_s, the underlying _opener.open must receive
        timeout=10 (not 30)."""
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
        """The default timeout flows from polish() (no timeout_s kwarg)
        down to _opener.open(req, timeout=10)."""
        with patch(
            "voice_typer.server.llm_polish._opener.open",
            return_value=_make_mock_response("Polished"),
        ) as mock_open:
            polisher.polish("This is some raw transcribed text that needs polishing")

        mock_open.assert_called_once()
        _pos_args, kwargs = mock_open.call_args
        assert kwargs.get("timeout") == 10

    def test_polish_timeout_s_override_is_propagated(self, polisher):
        """XV-75: an explicit timeout_s kwarg on polish() must reach
        _opener.open. (dictation_pipeline.py is responsible for wiring
        a user-configurable value — this test only verifies the
        plumbing.)"""
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
        """Regression guard: the old hard-coded 30s value must NEVER
        appear as the timeout kwarg, regardless of how _call_api is
        invoked (default or override)."""
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


# flat max_tokens=1024 ──────────────────────────────────────


class TestFlatMaxTokens:
    """XV-76: max_tokens is now a flat 1024 (the previous
    ``min(4096, len(text) * 2 + 256)`` formula was dead code above
    ~1920 chars)."""

    def test_max_tokens_is_flat_1024_for_short_text(self, polisher):
        """XV-76 (c): a short input (well below the old formula's
        ceiling) must produce max_tokens=1024 in the request payload —
        not the old formula's smaller value (e.g. 266 for a 5-char
        input)."""
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
        assert payload["max_tokens"] == 1024, f"Expected flat max_tokens=1024, got {payload['max_tokens']!r}"

    def test_max_tokens_is_flat_1024_for_long_text(self, polisher):
        """XV-76 (c): a long input (well above the old formula's
        ~1920-char ceiling where it always returned 4096) must now
        produce max_tokens=1024 — proving the formula was removed, not
        just capped differently."""
        # 5000 chars — well above the old formula's 4096 ceiling
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
        assert payload["max_tokens"] == 1024, (
            f"Expected flat max_tokens=1024 (old formula would have yielded 4096 here), got {payload['max_tokens']!r}"
        )

    def test_max_tokens_no_longer_depends_on_input_length(self, polisher):
        """XV-76 (c) robustness: max_tokens must be identical for
        inputs of different lengths (proving the formula is gone)."""
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
            observed_values.add(payload["max_tokens"])

        assert observed_values == {1024}, (
            f"max_tokens varied with input length: {observed_values!r} (expected constant {{1024}})"
        )


# redact_pii fail-closed ─────────────────────────────


class TestRedactPiiFailClosed:
    """When ``redact_pii`` raises inside ``_call_api``, the pre-API
    PII redaction gate must fail CLOSED — i.e. it must NOT send the
    un-redacted user-content to the LLM endpoint. The user-content
    may contain PII injected via template ``{clipboard}`` substitution
    (passwords, 2FA codes, private messages).

    Previously the ``except Exception`` branch swallowed the failure
    at DEBUG level and shipped the original text to the LLM anyway
    (fail-OPEN). The fix: (1) log at WARNING level so operators see
    the failure, and (2) return the original text UNPOLISHED, skipping
    the API call entirely.

    These tests monkeypatch ``redact_pii`` to raise and assert that
    ``_opener.open`` is NOT called.
    """

    def test_call_api_skips_api_call_when_redact_pii_raises(self, polisher):
        """when ``redact_pii`` raises, ``_call_api`` must
        return the original text WITHOUT calling ``_opener.open`` —
        the un-redacted text must NOT be sent to the LLM."""
        from voice_typer.server import security

        original = "Hello world this is a test of the polish path"
        with (
            patch.object(security, "redact_pii", side_effect=RuntimeError("boom")),
            patch("voice_typer.server.llm_polish._opener.open") as mock_open,
        ):
            result = polisher._call_api(original, "You are a text editor.")

        # Must return the original text UNPOLISHED.
        assert result == original
        # CRITICAL: no API call must have been made — un-redacted
        # text must NOT be sent to the LLM endpoint.
        mock_open.assert_not_called()

    def test_polish_skips_api_call_when_redact_pii_raises(self, polisher):
        """the fail-closed behavior must propagate through
        ``polish()``. The user still gets their original transcription
        pasted (so the dictation isn't dropped), but no LLM API call
        is made — no un-redacted PII leaves the device."""
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
        """the ``redact_pii`` failure must be logged at
        WARNING level (not DEBUG) so operators can detect when the
        fail-closed path fires. A DEBUG-level log is invisible in the
        default production log level — which previously meant a
        silent PII leak was possible for the entire lifetime of a
        broken ``security`` module."""
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
        """regression guard: when ``redact_pii`` succeeds (even
        if it makes no changes to the text), the API call must still
        proceed normally. This guards against an over-broad fix that
        accidentally skips the API call unconditionally."""
        from voice_typer.server import security

        original = "Hello world this is a test of the polish path"
        # ``redact_pii`` returns the input unchanged (no PII found)
        # — the API call must still happen.
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
        """robustness: even if ``_opener.open`` is mocked to
        return a successful response (which would normally produce
        "Polished"), the fail-closed gate must prevent the call from
        happening at all. This guards against a regression where the
        ``return text`` is accidentally placed after the API call
        instead of inside the ``except`` block."""
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
        # response — because the API call was never made.
        assert result == original
        assert result != "SHOULD NOT BE REACHED"
        mock_open.assert_not_called()


# HTTPError / URLError / generic-exception branch coverage ───────


class TestCallApiHttpErrorBranches:
    """``_call_api`` HTTPError / URLError / generic-exception
    branches (llm_polish.py:368-394). All 4 ``raise`` statements in
    the ``except`` blocks must map to the correct typed exception so
    the IPC layer can ``isinstance``-narrow and surface a distinct
    error code to the renderer (``server.cloud_server_error`` /
    ``server.cloud_engine_error`` / ``server.cloud_network_error``).

    Mapping (llm_polish.py:368-394):
      HTTPError(5xx)   → CloudServerError  (lines 379-380)
      HTTPError(other) → CloudEngineError  (lines 381)  — includes 4xx
      URLError         → CloudNetworkError (lines 382-388)
      Exception (any)  → CloudEngineError  (lines 389-394)

    NOTE: ``HTTPError`` is a subclass of ``URLError``, so the
    ``except HTTPError`` branch MUST appear before ``except URLError``
    in the source — these tests guard that ordering invariant.
    """

    def test_call_api_http_500_raises_cloud_server_error(self, polisher):
        """5xx → ``CloudServerError`` (llm_polish.py:379-380).

        The renderer surfaces "Cloud provider server error" and may
        retry with exponential backoff. Distinct from the 4xx mapping
        so the user sees an actionable message ("the LLM provider is
        having issues, try again") rather than "your API key is
        invalid" for a 5xx.
        """
        from urllib.error import HTTPError

        from voice_typer.server.asr_errors import CloudServerError

        err = HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        with patch("voice_typer.server.llm_polish._opener.open", side_effect=err), pytest.raises(
            CloudServerError, match=r"HTTP 500"
        ):
            polisher._call_api("Hello world test", "You are a text editor.")

    def test_call_api_http_401_raises_cloud_engine_error(self, polisher):
        """4xx (401) → ``CloudEngineError`` (llm_polish.py:381).

        The LLM polish path does NOT map 4xx to ``CloudAuthError``
        (unlike ``cloud_engines``); it uses the base ``CloudEngineError``
        for all non-5xx HTTP errors. The LLM polish path is best-effort
        (``polish()`` swallows exceptions and returns the original
        text), so a finer-grained auth code is unnecessary here —
        ``test_connection`` surfaces the message to the user.

        This test also guards the ``HTTPError``-before-``URLError``
        ordering invariant: ``HTTPError`` IS a subclass of
        ``URLError``, so if the order were swapped the 401 would be
        mis-mapped to ``CloudNetworkError``.
        """
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
        with patch("voice_typer.server.llm_polish._opener.open", side_effect=err), pytest.raises(
            CloudEngineError, match=r"HTTP 401"
        ) as exc_info:
            polisher._call_api("Hello world test", "You are a text editor.")

        # CloudServerError is a subclass of CloudEngineError; assert
        # we got the BASE class, not the 5xx-specific subclass.
        assert not isinstance(exc_info.value, CloudServerError), (
            "HTTP 401 must raise the base CloudEngineError, NOT CloudServerError "
            "(CloudServerError is reserved for 5xx). A regression here would "
            "cause the renderer to show 'server error, retry' for an auth "
            "failure, hiding the real cause (invalid API key)."
        )
        # And it must NOT be mis-mapped to CloudNetworkError (which
        # would happen if the except URLError branch caught HTTPError
        # first — HTTPError is a subclass of URLError).
        assert not isinstance(exc_info.value, CloudNetworkError), (
            "HTTP 401 (HTTPError) must NOT be caught by the except URLError "
            "branch — the except HTTPError branch MUST appear first in the "
            "source. A regression here would mis-map 4xx HTTP errors to "
            "CloudNetworkError (network error) instead of CloudEngineError."
        )

    def test_call_api_url_error_raises_cloud_network_error(self, polisher):
        """URLError → ``CloudNetworkError`` (llm_polish.py:382-388).

        URLError covers timeouts, DNS failures, connection resets —
        the renderer surfaces "Network error contacting cloud provider"
        and may retry (the cloud engine itself already retries 3× with
        exponential backoff before raising, so by the time this
        reaches the IPC layer the retry budget is exhausted).
        """
        from urllib.error import URLError

        from voice_typer.server.asr_errors import CloudNetworkError

        err = URLError("getaddrinfo failed")
        with patch("voice_typer.server.llm_polish._opener.open", side_effect=err), pytest.raises(
            CloudNetworkError, match=r"LLM API error"
        ):
            polisher._call_api("Hello world test", "You are a text editor.")

    def test_call_api_generic_exception_raises_cloud_engine_error(self, polisher):
        """Generic ``Exception`` → ``CloudEngineError``
        (llm_polish.py:389-394).

        Any exception that is NOT ``HTTPError`` / ``URLError`` (e.g.
        ``ValueError`` from a JSON decode failure, ``AttributeError``
        from a malformed response object, ``TypeError`` from a None
        where a dict was expected) must be re-raised as
        ``CloudEngineError`` so the IPC layer still maps to a
        cloud-specific code rather than the generic
        ``server.internal_error``. Without this branch, a JSON decode
        bug in the LLM response would surface as a generic internal
        error instead of "LLM API error".
        """
        from voice_typer.server.asr_errors import (
            CloudEngineError,
            CloudNetworkError,
            CloudServerError,
        )

        with patch(
            "voice_typer.server.llm_polish._opener.open",
            side_effect=ValueError("unexpected decode error"),
        ), pytest.raises(CloudEngineError, match=r"LLM API error") as exc_info:
            polisher._call_api("Hello world test", "You are a text editor.")

        # The generic exception must NOT be mis-mapped to a more
        # specific subclass — CloudNetworkError and CloudServerError
        # both have distinct IPC codes and renderer UX, and a generic
        # exception (ValueError, AttributeError, etc.) is by
        # definition NOT a network or 5xx-server error.
        assert not isinstance(exc_info.value, CloudServerError), (
            "A generic ValueError must be mapped to the base CloudEngineError, "
            "not CloudServerError (which is reserved for HTTP 5xx)."
        )
        assert not isinstance(exc_info.value, CloudNetworkError), (
            "A generic ValueError must be mapped to the base CloudEngineError, "
            "not CloudNetworkError (which is reserved for URLError)."
        )
