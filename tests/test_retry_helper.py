"""Focused tests for the shared retry helper and its five migrated call sites."""

from __future__ import annotations

import threading
import time
from email.message import Message
from email.utils import formatdate
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.retry import (
    RetryAbortedError,
    delay_for_attempt,
    parse_retry_after,
    run_with_retry,
    sleep_interruptible,
)


class TestParseRetryAfter:
    def test_missing_value_returns_default(self):
        assert parse_retry_after(None) == 0.0
        assert parse_retry_after("") == 0.0
        assert parse_retry_after(None, default=2.0) == 2.0

    def test_seconds_string_and_cap(self):
        assert parse_retry_after("7") == 7.0
        assert parse_retry_after("120") == 60.0
        assert parse_retry_after("-5") == 0.0

    def test_garbage_returns_default(self):
        assert parse_retry_after("not-a-date", default=2.0) == 2.0

    def test_http_date_in_future(self):
        header = formatdate(time.time() + 30, usegmt=True)
        assert parse_retry_after(header, default=2.0) == pytest.approx(30, abs=2.0)

    def test_http_date_in_past_returns_default(self):
        header = formatdate(time.time() - 100, usegmt=True)
        assert parse_retry_after(header, default=2.0) == 2.0

    def test_http_date_capped(self):
        header = formatdate(time.time() + 3600, usegmt=True)
        assert parse_retry_after(header, default=2.0) == 60.0


class TestDelayForAttempt:
    def test_within_range(self):
        assert delay_for_attempt((1.0, 2.0, 4.0), 0) == 1.0
        assert delay_for_attempt((1.0, 2.0, 4.0), 2) == 4.0

    def test_overflow_reuses_last(self):
        assert delay_for_attempt((1.0, 2.0), 5) == 2.0

    def test_empty_delays(self):
        assert delay_for_attempt((), 0) == 0.0


class TestSleepInterruptible:
    def test_zero_delay_calls_gate_once_and_returns(self):
        calls = []
        assert sleep_interruptible(0.0, gate_check=lambda: calls.append(1)) is False
        assert calls == [1]

    def test_no_gate_single_sleep_call(self):
        sleeps = []
        assert sleep_interruptible(1.5, sleep_fn=sleeps.append) is False
        assert sleeps == [1.5]

    def test_abort_event_set_returns_true_without_sleeping(self):
        event = threading.Event()
        event.set()
        sleeps = []
        assert sleep_interruptible(60.0, abort_event=event, sleep_fn=sleeps.append) is True
        assert sleeps == []

    def test_abort_event_unset_waits(self):
        event = threading.Event()
        assert sleep_interruptible(0.0, abort_event=event) is False

    def test_gate_abort_propagates(self):
        def _gate():
            raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            sleep_interruptible(1.0, gate_check=_gate, sleep_fn=lambda s: None)

    def test_chunked_gate_sleep_uses_fake_clock(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(time, "monotonic", lambda: now[0])

        def _sleep(s):
            now[0] += s

        gates = []
        assert sleep_interruptible(0.5, gate_check=lambda: gates.append(1), sleep_fn=_sleep) is False
        assert len(gates) >= 2


class TestRunWithRetry:
    def test_success_on_first_try(self):
        assert run_with_retry(lambda: "ok", sleep_fn=lambda s: None) == "ok"

    def test_retries_then_succeeds_with_per_call_delays(self):
        attempts = []
        sleeps = []

        def _flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient")
            return "recovered"

        assert (
            run_with_retry(_flaky, max_attempts=3, delays=(0.1, 0.2), sleep_fn=sleeps.append) == "recovered"
        )
        assert len(attempts) == 3
        assert sleeps == [0.1, 0.2]

    def test_exhaustion_reraises_last_error_and_calls_hooks(self):
        seen = {}

        def _always():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_with_retry(
                _always,
                max_attempts=2,
                delays=(0.0,),
                sleep_fn=lambda s: None,
                on_retry=lambda e, a, d: seen.setdefault("retry", []).append((a, d)),
                on_give_up=lambda e, n: seen.setdefault("give_up", n),
            )
        assert seen["retry"] == [(0, 0.0)]
        assert seen["give_up"] == 2

    def test_should_retry_false_raises_immediately(self):
        attempts = []
        with pytest.raises(OSError, match="fatal"):
            run_with_retry(
                lambda: (attempts.append(1), (_ for _ in ()).throw(OSError("fatal")))[1],
                should_retry=lambda exc, attempt: isinstance(exc, PermissionError),
                sleep_fn=lambda s: None,
            )
        assert len(attempts) == 1

    def test_base_exception_never_retried(self):
        attempts = []

        class _Abort(BaseException):
            pass

        def _cancelled():
            attempts.append(1)
            raise _Abort()

        with pytest.raises(_Abort):
            run_with_retry(_cancelled, max_attempts=3, delays=(0.0,), sleep_fn=lambda s: None)
        assert len(attempts) == 1

    def test_abort_before_attempt_raises(self):
        event = threading.Event()
        event.set()
        with pytest.raises(RetryAbortedError):
            run_with_retry(lambda: "never", abort_event=event, sleep_fn=lambda s: None)

    def test_abort_during_wait_raises_with_cause(self):
        event = threading.Event()
        event.set()

        def _always():
            raise RuntimeError("down")

        with pytest.raises(RetryAbortedError):
            run_with_retry(_always, max_attempts=3, delays=(60.0,), abort_event=event)

    def test_retry_after_override_wins_over_schedule(self):
        sleeps = []
        attempts = []

        def _limited():
            attempts.append(1)
            exc = RuntimeError("limited")
            exc.status = 429
            raise exc

        with pytest.raises(RuntimeError):
            run_with_retry(
                _limited,
                max_attempts=2,
                delays=(1.0,),
                retry_after_fn=lambda exc: 9.0 if getattr(exc, "status", None) == 429 else None,
                sleep_fn=sleeps.append,
            )
        assert sleeps == [9.0]
        assert len(attempts) == 2


class TestAsrDownloadRetryMigrated:
    def test_retries_plain_errors_then_succeeds(self, monkeypatch):
        from voice_typer.server.asr_utils import _download_with_retry

        monkeypatch.setattr(time, "sleep", lambda s: None)
        attempts = []

        def _flaky(**kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient")
            return "/cache/path"

        assert _download_with_retry(_flaky, max_attempts=3, delays=(0.0, 0.0)) == "/cache/path"
        assert len(attempts) == 3

    def test_abort_propagates_without_retry(self):
        from voice_typer.server.asr_setup import ModelDownloadAborted
        from voice_typer.server.asr_utils import _download_with_retry

        attempts = []

        def _cancelled(**kwargs):
            attempts.append(1)
            raise ModelDownloadAborted("aborted")

        with pytest.raises(ModelDownloadAborted):
            _download_with_retry(_cancelled, max_attempts=3, delays=(0.0, 0.0))
        assert len(attempts) == 1


class TestCloudRetryMigrated:
    def _engine(self):
        from voice_typer.server.cloud_engines import CloudEngine

        return CloudEngine(
            provider="openai",
            api_key="test-key",
            api_url="https://api.openai.com/v1/audio/transcriptions",
            consent_given=True,
        )

    def _response(self, body: bytes) -> MagicMock:
        resp = MagicMock()
        resp.status = 200
        resp.fp = None
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        state = {"n": 0}

        def _read(size=-1):
            if state["n"] == 0:
                state["n"] += 1
                return body
            return b""

        resp.read.side_effect = _read
        return resp

    def test_429_then_success(self):
        import io
        from urllib.error import HTTPError

        import numpy as np

        engine = self._engine()
        hdrs = Message()
        hdrs["Retry-After"] = "0"
        err = HTTPError(
            url=engine.api_url,
            code=429,
            msg="Too Many Requests",
            hdrs=hdrs,
            fp=io.BytesIO(b"{}"),
        )
        calls = {"n": 0}

        def _open(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise err
            return self._response(b'{"text": "recovered"}')

        with patch("voice_typer.server.cloud_engines._opener.open", _open):
            assert engine.transcribe(np.zeros(1600, dtype=np.float32)) == "recovered"
        assert calls["n"] == 2

    def test_urlerror_then_success_uses_backoff_schedule(self, monkeypatch):
        from urllib.error import URLError

        import numpy as np

        engine = self._engine()
        waits = []
        real_wait = engine._abort_event.wait
        monkeypatch.setattr(engine._abort_event, "wait", lambda timeout=None: waits.append(timeout) or False)
        calls = {"n": 0}

        def _open(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise URLError("network-down")
            return self._response(b'{"text": "back online"}')

        with patch("voice_typer.server.cloud_engines._opener.open", _open):
            assert engine.transcribe(np.zeros(1600, dtype=np.float32)) == "back online"
        assert calls["n"] == 2
        assert waits == [0.5]
        assert real_wait is not None

    def test_abort_message_preserved(self):
        from urllib.error import URLError

        import numpy as np
        from voice_typer.server.asr_errors import CloudEngineError

        engine = self._engine()

        def _open(req, timeout=None):
            engine.request_abort()
            raise URLError("down")

        with (
            patch("voice_typer.server.cloud_engines._opener.open", _open),
            pytest.raises(CloudEngineError, match="aborted by user"),
        ):
            engine.transcribe(np.zeros(1600, dtype=np.float32))


class TestSegmentedRetryMigrated:
    def test_parse_retry_after_cap_preserved(self):
        from voice_typer.server import segmented_download as seg

        assert seg._parse_retry_after(None) == 0.0
        assert seg._parse_retry_after("120") == 60.0
        assert seg._parse_retry_after("bogus") == 0.0

    def test_transient_then_success(self, tmp_path, monkeypatch):
        from voice_typer.server import segmented_download as seg

        monkeypatch.setattr(seg, "RETRY_BACKOFF_S", (0.0,))

        class _Resp:
            def __init__(self, status, headers, body):
                self.status = status
                self.headers = dict(headers)
                self._body = body

            def getheader(self, name, default=None):
                for k, v in self.headers.items():
                    if k.lower() == name.lower():
                        return v
                return default

            def read(self, amt=-1):
                data, self._body = self._body, b""
                return data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Opener:
            def __init__(self):
                self.n = 0

            def open(self, request, timeout=None):
                self.n += 1
                if self.n == 1:
                    return _Resp(503, {"Retry-After": "0"}, b"")
                return _Resp(206, {}, b"0123456789")

        part = tmp_path / "seg0.part"
        written = seg._fetch_segment(
            opener=_Opener(),
            url="https://example.invalid/model.bin",
            seg=seg.SegmentRange(index=0, start=0, end=9),
            part_path=part,
            headers={},
            timeout_s=5.0,
            gate_check=None,
            on_bytes=lambda _n: None,
        )
        assert written == 10
        assert part.read_bytes() == b"0123456789"

    def test_gate_abort_propagates_without_retry(self, tmp_path, monkeypatch):
        from voice_typer.server import segmented_download as seg

        monkeypatch.setattr(seg, "RETRY_BACKOFF_S", (0.0,))
        calls = {"n": 0}

        class _Opener:
            def open(self, request, timeout=None):
                calls["n"] += 1
                raise OSError("transport down")

        def _gate():
            raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            seg._fetch_segment(
                opener=_Opener(),
                url="https://example.invalid/model.bin",
                seg=seg.SegmentRange(index=0, start=0, end=9),
                part_path=tmp_path / "seg0.part",
                headers={},
                timeout_s=5.0,
                gate_check=_gate,
                on_bytes=lambda _n: None,
            )
        assert calls["n"] == 0


class TestOfflinePackRetryMigrated:
    def test_rate_limit_then_success(self, tmp_path, monkeypatch):
        import hashlib

        from voice_typer.server.service import offline_pack

        sleeps = []
        monkeypatch.setattr(offline_pack.time, "sleep", sleeps.append)
        full = b"pack-content" * 100
        expected = hashlib.sha256(full).hexdigest()
        calls = {"n": 0}

        def _fake(url, *, offset=0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise offline_pack._RateLimitedError("limited", reset_at=None)
            remaining = full[offset:]

            def _iter(chunk_bytes):
                yield remaining

            return {"content_length": len(remaining), "iter_chunks": _iter}

        dest = tmp_path / "pack-v1.partial"
        assert offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=_fake,
        )
        assert calls["n"] == 2
        assert sleeps == [1.0]

    def test_future_reset_extends_wait(self, tmp_path, monkeypatch):
        from voice_typer.server.service import offline_pack

        sleeps = []
        monkeypatch.setattr(offline_pack.time, "sleep", sleeps.append)
        reset_at = time.time() + 5.0

        def _fake(url, *, offset=0):
            raise offline_pack._RateLimitedError("limited", reset_at=reset_at)

        dest = tmp_path / "pack-v1.partial"
        with pytest.raises(offline_pack.OfflinePackRateLimitError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256="0" * 64,
                version="v1",
                http_get=_fake,
            )
        assert sleeps[0] >= 4.5


class TestVocabularySaveRetryMigrated:
    def _vm(self, tmp_path):
        from voice_typer.server.vocabulary import VocabularyManager

        return VocabularyManager(config_dir=tmp_path)

    def test_transient_permission_error_then_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        def _flaky(path, content, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("transient lock")
            path.write_text(content, encoding="utf-8")

        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", _flaky)
        self._vm(tmp_path)._save_user()
        assert calls["n"] == 2

    def test_non_permission_oserror_not_retried(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def _fatal(path, content, **kwargs):
            calls["n"] += 1
            raise OSError("disk full")

        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", _fatal)
        with pytest.raises(OSError, match="disk full"):
            self._vm(tmp_path)._save_user()
        assert calls["n"] == 1

    def test_persistent_permission_error_retried_then_raised(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls = {"n": 0}

        def _locked(path, content, **kwargs):
            calls["n"] += 1
            raise PermissionError("locked")

        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", _locked)
        with pytest.raises(PermissionError, match="locked"):
            self._vm(tmp_path)._save_user()
        assert calls["n"] == 3
