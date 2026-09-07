"""Interruptible retry-wait tests for the cloud engine's retry skeleton.

``CloudEngine._transcribe_with_retry`` sleeps between retries in two
places: the 429 ``Retry-After`` branch (capped at 60s by
``_parse_retry_after``) and the ``URLError`` exponential-backoff branch.
Both waits use ``self._abort_event.wait(timeout=...)`` instead of a
plain ``time.sleep`` so the user's ESC-abort (``request_abort()``) takes
effect the moment the event is set — not at the top of the NEXT attempt
(which, with a hostile ``Retry-After: 60``, could lag up to 60 seconds).

These tests pin:
  1. an abort raised during a 60s ``Retry-After`` wait breaks out of the
     wait immediately (wall-clock bound proves no full sleep happened);
  2. an abort raised during the ``URLError`` backoff wait does the same;
  3. the raised error is the engine's existing abort error —
     ``CloudEngineError`` with the ``transcription aborted by user``
     message (the same type/wording as the top-of-attempt abort check);
  4. only ONE HTTP attempt is issued in both cases.
"""

from __future__ import annotations

import io
import time
from email.message import Message
from urllib.error import HTTPError, URLError

import numpy as np
import pytest
from voice_typer.server.asr_errors import CloudEngineError
from voice_typer.server.cloud_engines import CloudEngine

_ABORT_MESSAGE = "transcription aborted by user"


def _make_engine() -> CloudEngine:
    return CloudEngine(
        provider="openai",
        api_key="valid-key",
        api_url="https://api.openai.com/v1/audio/transcriptions",
        consent_given=True,
    )


def _audio() -> np.ndarray:
    return np.ones(16000, dtype=np.float32) * 0.05


class TestRetryAfterWaitIsInterruptible:
    def test_abort_during_retry_after_wait_returns_immediately(self) -> None:
        engine = _make_engine()
        attempt_count = {"n": 0}

        def rate_limited_open(*args, **kwargs):
            attempt_count["n"] += 1
            # Simulate the user hitting ESC while the engine is about to
            # honor a hostile 60-second Retry-After.
            if attempt_count["n"] == 1:
                engine.request_abort()
            hdrs = Message()
            hdrs["Retry-After"] = "60"
            raise HTTPError(
                url="https://api.openai.com/v1/audio/transcriptions",
                code=429,
                msg="Too Many Requests",
                hdrs=hdrs,
                fp=io.BytesIO(b'{"error": "rate_limit_exceeded"}'),
            )

        mock_opener_open = rate_limited_open
        t0 = time.perf_counter()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "voice_typer.server.cloud_engines._opener.open",
                mock_opener_open,
            )
            with pytest.raises(CloudEngineError) as exc_info:
                engine.transcribe(_audio())
        elapsed = time.perf_counter() - t0

        # The abort error must be the engine's canonical abort error
        # (same class + message pattern as the top-of-attempt check).
        assert _ABORT_MESSAGE in str(exc_info.value), f"expected the abort error message, got {exc_info.value!r}"
        # A plain time.sleep(60) would take 60s; the interruptible wait
        # returns the moment the event is set.
        assert elapsed < 5.0, f"abort during Retry-After wait must return immediately; took {elapsed:.2f}s"
        # Only one HTTP attempt was issued before the abort.
        assert attempt_count["n"] == 1


class TestBackoffWaitIsInterruptible:
    def test_abort_during_urlerror_backoff_returns_immediately(self) -> None:
        engine = _make_engine()
        attempt_count = {"n": 0}

        def network_down_open(*args, **kwargs):
            attempt_count["n"] += 1
            if attempt_count["n"] == 1:
                engine.request_abort()
            raise URLError("network-down")

        t0 = time.perf_counter()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "voice_typer.server.cloud_engines._opener.open",
                network_down_open,
            )
            with pytest.raises(CloudEngineError) as exc_info:
                engine.transcribe(_audio())
        elapsed = time.perf_counter() - t0

        assert _ABORT_MESSAGE in str(exc_info.value), f"expected the abort error message, got {exc_info.value!r}"
        assert elapsed < 5.0, f"abort during backoff wait must return immediately; took {elapsed:.2f}s"
        assert attempt_count["n"] == 1

    def test_abort_during_backoff_wait_deepgram_path(self) -> None:
        """Same contract on the Deepgram leg (both provider paths share
        ``_transcribe_with_retry``, the wait lives there)."""
        engine = CloudEngine(
            provider="deepgram",
            api_key="valid-key",
            api_url="https://api.deepgram.com/v1/listen",
            model="nova-2",
            consent_given=True,
        )
        attempt_count = {"n": 0}

        def network_down_open(*args, **kwargs):
            attempt_count["n"] += 1
            if attempt_count["n"] == 1:
                engine.request_abort()
            raise URLError("network-down")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "voice_typer.server.cloud_engines._opener.open",
                network_down_open,
            )
            with pytest.raises(CloudEngineError) as exc_info:
                engine.transcribe(_audio())

        assert _ABORT_MESSAGE in str(exc_info.value)
        assert attempt_count["n"] == 1


class TestNoAbortStillWaitsFullBudget:
    def test_unset_abort_does_not_raise_from_wait(self) -> None:
        """Sanity: an unset abort event must let the wait run its course
        (returning False) and NOT raise — the retry loop keeps its
        normal backoff semantics when the user does not cancel."""
        engine = _make_engine()
        # A zero-second Retry-After means the wait completes instantly
        # but must still return False (no abort) → the engine proceeds
        # to attempt 2, which fails non-retryably (429 already retried).
        hdrs = Message()
        hdrs["Retry-After"] = "0"
        http_err = HTTPError(
            url="https://api.openai.com/v1/audio/transcriptions",
            code=429,
            msg="Too Many Requests",
            hdrs=hdrs,
            fp=io.BytesIO(b"{}"),
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "voice_typer.server.cloud_engines._opener.open",
                lambda *a, **k: (_ for _ in ()).throw(http_err),
            )
            # Second 429 is NOT retried (retry budget spent) → the typed
            # rate-limit error, not an abort error.
            from voice_typer.server.asr_errors import CloudRateLimitError

            with pytest.raises(CloudRateLimitError):
                engine.transcribe(_audio())
        assert not engine._abort_event.is_set()
