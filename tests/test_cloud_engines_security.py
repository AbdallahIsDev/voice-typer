"""regression tests for ``cloud_engines._read_capped`` (SEC-030"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


class TestReadCapped:
    """#1-#2: ``_read_capped`` body-size cap."""

    def test_read_capped_under_limit(self):
        """#1: a body whose total size is below ``max_bytes`` must"""
        from voice_typer.server.cloud_engines import _read_capped

        resp = MagicMock()
        # First read returns a small chunk; second read returns b""
        resp.read.side_effect = [b"abc", b""]

        result = _read_capped(resp, max_bytes=1024)

        assert result == b"abc", (
            f"expected b'abc', got {result!r}, _read_capped must concatenate all chunks below the cap."
        )
        # Both reads happened (the second one is the EOF probe).
        assert resp.read.call_count == 2
        # Each read call passed 64 * 1024 (the chunk size).
        for call_args in resp.read.call_args_list:
            assert call_args.args == (64 * 1024,) or call_args[0] == (64 * 1024,)

    def test_read_capped_over_limit_raises_runtimeerror(self):
        """#2: a body whose total exceeds ``max_bytes`` must"""
        from voice_typer.server.cloud_engines import _read_capped

        resp = MagicMock()
        # Two 64 KB chunks, then EOF (the EOF is never reached because
        resp.read.side_effect = [b"x" * 65536, b"x" * 65536, b""]

        with pytest.raises(RuntimeError, match="100000") as exc_info:
            _read_capped(resp, max_bytes=100_000)

        msg = str(exc_info.value)
        assert "100000" in msg, (
            "RuntimeError message must name the cap (100000) so the abort reason is unambiguous in logs."
        )
        assert "OOM" in msg or "exceeded" in msg.lower(), (
            f"RuntimeError message must mention OOM / exceeded, got {msg!r}."
        )
        # Exactly 2 reads happened (the third, EOF-probing read never
        assert resp.read.call_count == 2, f"expected 2 reads before cap abort, got {resp.read.call_count}"


class TestParseRetryAfter:
    """#3-#5: ``_parse_retry_after`` RFC 7231 §7.1.3 parser."""

    def test_parse_retry_after_integer_seconds(self):
        """#3: a plain integer string is parsed as float seconds."""
        from voice_typer.server.cloud_engines import _parse_retry_after

        result = _parse_retry_after("30")
        assert result == 30.0, f"expected 30.0 for integer '30', got {result!r}"
        # Sanity: the return type is float (so ``time.sleep`` accepts it
        assert isinstance(result, float)

    def test_parse_retry_after_http_date(self, monkeypatch):
        """#4: an HTTP-date string is parsed via"""
        from voice_typer.server.cloud import _retry

        frozen_now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        # The production code calls ``datetime.now(timezone.utc)`` —
        real_datetime = _retry.datetime

        class _FrozenDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is timezone.utc else frozen_now.replace(tzinfo=None)

        monkeypatch.setattr(_retry, "datetime", _FrozenDatetime)

        # HTTP-date 30 seconds in the future (RFC 7231 §7.1.3 format).
        future_dt = frozen_now + timedelta(seconds=30)
        http_date = future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

        # Call through the FACADE name to prove the re-exported
        from voice_typer.server import cloud_engines

        result = cloud_engines._parse_retry_after(http_date)

        assert abs(result - 30.0) < 1.0, f"expected ~30.0s for HTTP-date 30s in the future, got {result!r}"

    def test_parse_retry_after_caps_at_60s(self):
        """#5: a Retry-After value larger than 60s must be capped"""
        from voice_typer.server.cloud_engines import _parse_retry_after

        result = _parse_retry_after("120")
        assert result == 60.0, (
            f"expected 60.0 (cap) for '120', got {result!r}, the 60s "
            "cap at line 214 must clamp oversized Retry-After values."
        )

    def test_parse_retry_after_none_returns_default(self):
        """Defense-in-depth: ``None`` (missing header) returns the 2s"""
        from voice_typer.server.cloud_engines import _parse_retry_after

        assert _parse_retry_after(None) == 2.0
        assert _parse_retry_after("") == 2.0
