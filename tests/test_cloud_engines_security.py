"""regression tests for ``cloud_engines._read_capped`` (SEC-030
OOM protection) and ``cloud_engines._parse_retry_after`` (RFC 7231
Retry-After parsing).

Both helpers are pure-Python functions in
``voice_typer/server/cloud_engines.py:152-214`` with no class state —
they are the security boundary between untrusted server responses and
the dictation thread's memory budget / sleep budget.

  * ``_read_capped`` caps response body size so a malicious or buggy
    server cannot exhaust RAM by returning a 5 GB body.
  * ``_parse_retry_after`` caps sleep duration so a hostile server
    cannot stall the dictation thread indefinitely via a
    ``Retry-After: 999999`` header.

Pre-fix both were completely untested. These five tests pin the contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

# ── _read_capped ───────────────────────────────────────────────────


class TestReadCapped:
    """#1-#2: ``_read_capped`` body-size cap.

    The function streams ``resp.read(64 * 1024)`` until EOF, aborting
    with ``RuntimeError`` if the running total exceeds ``max_bytes``.
    """

    def test_read_capped_under_limit(self):
        """#1: a body whose total size is below ``max_bytes`` must
        be returned verbatim, concatenated from all chunks.

        The mock ``resp.read`` returns ``b"abc"`` on the first call and
        ``b""`` on the second (the EOF signal — ``if not chunk: break``).
        With ``max_bytes=1024`` the cap is never hit, so the function
        returns ``b"abc"``.
        """
        from voice_typer.server.cloud_engines import _read_capped

        resp = MagicMock()
        # First read returns a small chunk; second read returns b""
        # (EOF). The production code breaks the loop on a falsy chunk.
        resp.read.side_effect = [b"abc", b""]

        result = _read_capped(resp, max_bytes=1024)

        assert result == b"abc", (
            f"expected b'abc', got {result!r} — _read_capped must concatenate all chunks below the cap."
        )
        # Both reads happened (the second one is the EOF probe).
        assert resp.read.call_count == 2
        # Each read call passed 64 * 1024 (the chunk size).
        for call_args in resp.read.call_args_list:
            assert call_args.args == (64 * 1024,) or call_args[0] == (64 * 1024,)

    def test_read_capped_over_limit_raises_runtimeerror(self):
        """#2: a body whose total exceeds ``max_bytes`` must
        raise ``RuntimeError`` BEFORE the next chunk is appended.

        With ``max_bytes=100_000`` and two 65536-byte chunks:

          * After chunk 1: total = 65536 (≤ 100_000, OK).
          * After chunk 2: total = 131072 (> 100_000, raise).

        The error message must name the cap so the user / log reader
        can tell this is the OOM-abort path, not a generic network
        failure.
        """
        from voice_typer.server.cloud_engines import _read_capped

        resp = MagicMock()
        # Two 64 KB chunks, then EOF (the EOF is never reached because
        # the cap fires after the second chunk).
        resp.read.side_effect = [b"x" * 65536, b"x" * 65536, b""]

        with pytest.raises(RuntimeError, match="100000") as exc_info:
            _read_capped(resp, max_bytes=100_000)

        # The error message must mention both the cap and "OOM" so the
        # server-side ERROR log is self-explanatory.
        msg = str(exc_info.value)
        assert "100000" in msg, (
            "RuntimeError message must name the cap (100000) so the abort reason is unambiguous in logs."
        )
        assert "OOM" in msg or "exceeded" in msg.lower(), (
            f"RuntimeError message must mention OOM / exceeded — got {msg!r}."
        )
        # Exactly 2 reads happened (the third, EOF-probing read never
        # fired because the cap raised on the second).
        assert resp.read.call_count == 2, f"expected 2 reads before cap abort, got {resp.read.call_count}"


# ── _parse_retry_after ─────────────────────────────────────────────


class TestParseRetryAfter:
    """#3-#5: ``_parse_retry_after`` RFC 7231 §7.1.3 parser.

    Accepts either an integer number of seconds OR an HTTP-date, caps
    the wait at 60 seconds, and falls back to a 2s default for
    unparseable / negative values.
    """

    def test_parse_retry_after_integer_seconds(self):
        """#3: a plain integer string is parsed as float seconds.

        ``"30"`` → ``30.0``. The ``float(header_value)`` path at line
        193 must succeed and the result must NOT be capped (30 ≤ 60).
        """
        from voice_typer.server.cloud_engines import _parse_retry_after

        result = _parse_retry_after("30")
        assert result == 30.0, f"expected 30.0 for integer '30', got {result!r}"
        # Sanity: the return type is float (so ``time.sleep`` accepts it
        # without an int→float coercion that could mask a regression).
        assert isinstance(result, float)

    def test_parse_retry_after_http_date(self, monkeypatch):
        """#4: an HTTP-date string is parsed via
        ``email.utils.parsedate_to_datetime`` and the delta from
        ``datetime.now(timezone.utc)`` is returned as seconds.

        Setup:
          * Freeze ``datetime.now`` inside ``cloud_engines`` to a fixed
            UTC instant (``2025-01-01T00:00:00Z``).
          * Construct an HTTP-date 30 seconds in the future.
          * Assert the parser returns ~30.0 (within a small tolerance
            for the float arithmetic).

        Pre-fix the HTTP-date branch was completely untested — meaning
        a refactor that dropped the ``parsedate_to_datetime`` import or
        swapped ``timezone.utc`` for ``None`` (yielding a naive
        datetime that can't be subtracted from a tz-aware one) would
        silently regress to the 2s default with no test failure.
        """
        from voice_typer.server import cloud_engines

        frozen_now = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        # The production code calls ``datetime.now(timezone.utc)`` —
        # patch the ``datetime`` CLASS inside cloud_engines so ``.now``
        # returns our frozen instant. ``timezone`` is also imported
        # from the same module so it stays real.
        real_datetime = cloud_engines.datetime

        class _FrozenDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is timezone.utc else frozen_now.replace(tzinfo=None)

        monkeypatch.setattr(cloud_engines, "datetime", _FrozenDatetime)

        # HTTP-date 30 seconds in the future (RFC 7231 §7.1.3 format).
        future_dt = frozen_now + timedelta(seconds=30)
        http_date = future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

        result = cloud_engines._parse_retry_after(http_date)

        # Allow a small tolerance (1s) for any rounding in the
        # date-format → datetime → delta pipeline.
        assert abs(result - 30.0) < 1.0, f"expected ~30.0s for HTTP-date 30s in the future, got {result!r}"

    def test_parse_retry_after_caps_at_60s(self):
        """#5: a Retry-After value larger than 60s must be capped
        at 60.0 — never sleep for longer.

        The cap at line 214 (``max(0.0, min(seconds, 60.0))``) is the
        sole defense against a hostile / misconfigured server that
        sends ``Retry-After: 999999`` to stall the dictation thread.

        ``"120"`` parses cleanly to ``120.0`` via the integer-seconds
        branch, but the cap clamps it to ``60.0``.
        """
        from voice_typer.server.cloud_engines import _parse_retry_after

        result = _parse_retry_after("120")
        assert result == 60.0, (
            f"expected 60.0 (cap) for '120', got {result!r} — the 60s "
            "cap at line 214 must clamp oversized Retry-After values."
        )

    def test_parse_retry_after_none_returns_default(self):
        """Defense-in-depth: ``None`` (missing header) returns the 2s
        default so the caller still waits briefly before retrying.

        This is the documented contract at line 189-190 — pin it so a
        refactor that changes the default (e.g. to 0s, which would
        hammer the server) doesn't silently regress.
        """
        from voice_typer.server.cloud_engines import _parse_retry_after

        assert _parse_retry_after(None) == 2.0
        assert _parse_retry_after("") == 2.0
