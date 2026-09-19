"""§8.7: GitHub rate limit: exponential backoff + ``X-RateLimit-Reset``."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack
from voice_typer.server.service.offline_pack import _RateLimitedError


def _make_rate_limited_transport(
    *,
    fail_count: int,
    reset_at: float | None = None,
    full_body: bytes = b"",
):
    """Build a fake transport that returns 403 ``fail_count`` times, then 200."""
    expected_sha = hashlib.sha256(full_body).hexdigest()
    calls = {"n": 0, "sleeps": []}

    def fake(url, *, offset=0):
        calls["n"] += 1
        if calls["n"] <= fail_count:
            raise _RateLimitedError(
                f"GitHub rate limit (attempt {calls['n']})",
                reset_at=reset_at,
            )
        remaining = full_body[offset:]

        def iter_chunks(chunk_bytes):
            yield remaining

        return {"content_length": len(remaining), "iter_chunks": iter_chunks}

    return fake, calls, expected_sha


class TestRateLimitConstants:
    """§8.7, backoff schedule + max attempts."""

    def test_backoff_schedule(self):
        assert offline_pack.OFFLINE_PACK_RATE_LIMIT_BACKOFF_S == (1.0, 2.0, 4.0)

    def test_max_attempts_is_3(self):
        assert offline_pack.OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS == 3


class TestRateLimitRetry:
    """§8.7, exponential backoff + ``X-RateLimit-Reset``."""

    def test_three_failures_raise_pack_rate_limit_error(self, tmp_path: Path, monkeypatch):
        """3 consecutive 403s → PackRateLimitError."""
        # Skip the actual sleeps for test speed.
        sleeps: list[float] = []
        monkeypatch.setattr(offline_pack.time, "sleep", lambda s: sleeps.append(s))
        full = b"pack-content" * 100
        fake, calls, expected = _make_rate_limited_transport(fail_count=10, reset_at=None, full_body=full)
        dest = tmp_path / "pack-v1.partial"
        with pytest.raises(offline_pack.OfflinePackRateLimitError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=fake,
            )
        # 1 initial + 3 retries = 4 attempts total (then raise).
        assert calls["n"] == offline_pack.OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS + 1
        # The backoff schedule was honored (1, 2, 4).
        assert sleeps[:3] == [1.0, 2.0, 4.0]

    def test_second_attempt_succeeds(self, tmp_path: Path, monkeypatch):
        """1 failure then success → download completes on attempt 2."""
        monkeypatch.setattr(offline_pack.time, "sleep", lambda s: None)
        full = b"pack-content" * 100
        expected = hashlib.sha256(full).hexdigest()
        fake, calls, _ = _make_rate_limited_transport(fail_count=1, reset_at=None, full_body=full)
        dest = tmp_path / "pack-v1.partial"
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        assert ok
        assert calls["n"] == 2  # 1 fail + 1 success
        assert dest.read_bytes() == full

    def test_x_ratelimit_reset_extends_sleep(self, tmp_path: Path, monkeypatch):
        """When ``X-RateLimit-Reset`` is in the future, the sleep is at"""
        sleeps: list[float] = []
        monkeypatch.setattr(offline_pack.time, "sleep", lambda s: sleeps.append(s))
        reset_at = time.time() + 10.0
        full = b"x" * 100
        expected = hashlib.sha256(full).hexdigest()
        fake, _, _ = _make_rate_limited_transport(fail_count=10, reset_at=reset_at, full_body=full)
        dest = tmp_path / "pack-v1.partial"
        with pytest.raises(offline_pack.OfflinePackRateLimitError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=fake,
            )
        # The first sleep should be >= ~10 (the reset_at delta).
        assert sleeps[0] >= 9.0  # allow small clock skew

    def test_reset_at_in_past_uses_default_backoff(self, tmp_path: Path, monkeypatch):
        """If ``X-RateLimit-Reset`` is in the past, the default backoff is used."""
        sleeps: list[float] = []
        monkeypatch.setattr(offline_pack.time, "sleep", lambda s: sleeps.append(s))
        reset_at = time.time() - 100  # past
        full = b"x" * 100
        expected = hashlib.sha256(full).hexdigest()
        fake, _, _ = _make_rate_limited_transport(fail_count=10, reset_at=reset_at, full_body=full)
        dest = tmp_path / "pack-v1.partial"
        with pytest.raises(offline_pack.OfflinePackRateLimitError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=fake,
            )
        assert sleeps[0] == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
