"""``VoiceTyperService.get_model_status`` TTL cache tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock


class TestModelStatusCache:
    """``VoiceTyperService.get_model_status`` caches result for 5 s."""

    def test_cache_hits_within_ttl(self, tmp_config_dir, monkeypatch):
        """Within TTL, second call returns cached result without re-querying FS."""
        from voice_typer.server.service import VoiceTyperService

        # Build a mock app whose config doesn't claim a qwen/parakeet
        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None

        service = VoiceTyperService(app)

        # Counting proxy around os.path.isdir, the cache hit/miss
        real_isdir = os.path.isdir
        isdir_calls = {"n": 0}

        def _counting_isdir(path):
            isdir_calls["n"] += 1
            return real_isdir(path)

        monkeypatch.setattr("os.path.isdir", _counting_isdir)

        # First call: populates the cache, must touch the filesystem.
        first_status = service.get_model_status()
        first_calls = isdir_calls["n"]
        assert first_calls > 0, (
            "First get_model_status() should have queried the filesystem "
            f"(expected >0 os.path.isdir calls, got {first_calls})"
        )

        # Second call within TTL: must NOT touch the filesystem.
        isdir_calls["n"] = 0
        second_status = service.get_model_status()
        assert isdir_calls["n"] == 0, (
            "Second get_model_status() within TTL should hit the cache "
            f"(expected 0 os.path.isdir calls, got {isdir_calls['n']})"
        )

        assert second_status is first_status, (
            "Cached get_model_status() should return the same dict object identity, not a freshly-computed copy"
        )

    def test_cache_invalidated_after_delete(self, tmp_config_dir, monkeypatch):
        """After ``delete_model``, the next ``get_model_status`` re-queries FS."""
        from voice_typer.server.service import VoiceTyperService

        # Pre-create the HF cache directory with a "tiny" model
        cache_dir = tmp_config_dir / "huggingface" / "hub"
        cache_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = cache_dir / "models--Systran--faster-whisper-tiny"
        repo_dir.mkdir(parents=True, exist_ok=True)

        # Active model is set to large-v3-turbo (NOT tiny) so delete_model
        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None
        app.config.asr_backend = "whisper"
        app.config.model_size = "large-v3-turbo"

        service = VoiceTyperService(app)

        # The status layer decides "downloaded" via the partial-download
        def _probe_complete(repo_id):
            return (cache_dir / f"models--{repo_id.replace('/', '--')}").is_dir()

        monkeypatch.setattr(
            "voice_typer.server.transcription_download.is_model_snapshot_complete",
            _probe_complete,
        )

        # First call: populates the cache.  tiny should be reported
        first_status = service.get_model_status()
        assert first_status["tiny"]["downloaded"] is True, "Pre-condition: tiny should be downloaded before delete"

        # Now wrap os.path.isdir with a counting proxy so we can
        real_isdir = os.path.isdir
        isdir_calls = {"n": 0}

        def _counting_isdir(path):
            isdir_calls["n"] += 1
            return real_isdir(path)

        monkeypatch.setattr("os.path.isdir", _counting_isdir)

        # Delete the model, must invalidate the cache.
        result = service.delete_model("tiny")
        assert result["success"] is True, f"delete_model should succeed, got: {result}"
        # Sanity: the on-disk directory was actually removed.
        assert not repo_dir.exists(), "shutil.rmtree should have removed the dir"

        # Next get_model_status: cache was invalidated, must re-query.
        isdir_calls["n"] = 0
        second_status = service.get_model_status()
        assert isdir_calls["n"] > 0, (
            "After delete_model invalidated the cache, the next "
            "get_model_status should re-query the filesystem "
            f"(expected >0 os.path.isdir calls, got {isdir_calls['n']})"
        )
        # And the new status must reflect the deletion.
        assert second_status["tiny"]["downloaded"] is False, (
            "After delete_model, get_model_status should report tiny as not downloaded (stale cache would say True)"
        )

    def test_cache_expires_after_ttl(self, tmp_config_dir, monkeypatch):
        """After ``_MODEL_STATUS_CACHE_TTL_S`` elapses, the cache is bypassed."""
        from voice_typer.server.service import (
            _MODEL_STATUS_CACHE_TTL_S,
            VoiceTyperService,
        )

        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None

        service = VoiceTyperService(app)

        fake_now = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.service.model._status.time.monotonic",
            lambda: fake_now[0],
        )

        # Counting proxy around os.path.isdir.
        real_isdir = os.path.isdir
        isdir_calls = {"n": 0}

        def _counting_isdir(path):
            isdir_calls["n"] += 1
            return real_isdir(path)

        monkeypatch.setattr("os.path.isdir", _counting_isdir)

        service.get_model_status()
        assert isdir_calls["n"] > 0, "First call should query the filesystem"

        isdir_calls["n"] = 0
        fake_now[0] = _MODEL_STATUS_CACHE_TTL_S - 0.1
        service.get_model_status()
        assert isdir_calls["n"] == 0, (
            "Within TTL, get_model_status should hit the cache "
            f"(expected 0 os.path.isdir calls, got {isdir_calls['n']})"
        )

        isdir_calls["n"] = 0
        fake_now[0] = _MODEL_STATUS_CACHE_TTL_S + 0.1
        service.get_model_status()
        assert isdir_calls["n"] > 0, (
            "After TTL expires, get_model_status should re-query the "
            f"filesystem (expected >0 os.path.isdir calls, got {isdir_calls['n']})"
        )
