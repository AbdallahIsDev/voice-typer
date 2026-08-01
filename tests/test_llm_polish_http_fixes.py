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
