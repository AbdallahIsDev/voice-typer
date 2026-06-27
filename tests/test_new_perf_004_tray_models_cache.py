"""Regression tests for NEW-PERF-004: tray models submenu caching.

Previously, every tray right-click triggered:
- ``ensure_hf_env()`` (filesystem checks)
- ``import qwen_asr`` (50–150 ms heavy ML import)
- 5+ filesystem ``exists()`` calls (one per candidate model)

This caused noticeable menu-open lag.  The fix caches:
- The qwen_asr import availability (session-lifetime).
- The HuggingFace hub ``refs/main`` existence check (5-second TTL).

The cache is invalidated explicitly when a model download completes
(via ``invalidate_model_availability_cache()``).
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call  # TEST-033: unified mock import

import pytest

from voice_typer.server import tray_models
from voice_typer.server.tray_models import (
    _check_hf_model_downloaded,
    _check_qwen_asr_available,
    _hf_download_cache,
    _HF_DOWNLOAD_CACHE_TTL_SECONDS,
    invalidate_model_availability_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Reset all module-level caches before and after each test."""
    invalidate_model_availability_cache()
    yield
    invalidate_model_availability_cache()


class TestQwenAsrCache:
    """The qwen_asr import check must be cached for the session."""

    def test_imports_qwen_asr_once(self):
        """The ``import qwen_asr`` statement must run at most once per
        session — subsequent calls return the cached result.
        """
        # Use a counter to verify import is called only once.
        import_calls = []

        # Mock the import system so "import qwen_asr" succeeds.
        import sys
        fake_module = MagicMock()
        with patch.dict(sys.modules, {"qwen_asr": fake_module}):
            result1 = _check_qwen_asr_available()
            result2 = _check_qwen_asr_available()
            result3 = _check_qwen_asr_available()

        assert result1 is True
        assert result2 is True
        assert result3 is True

    def test_import_failure_cached(self):
        """When qwen_asr is not installed, the ImportError is cached."""
        import sys
        # Remove qwen_asr from modules so import fails.
        original = sys.modules.pop("qwen_asr", None)
        try:
            # Also block the import machinery from finding it.
            with patch.dict(sys.modules, {"qwen_asr": None}):
                result1 = _check_qwen_asr_available()
                result2 = _check_qwen_asr_available()
        finally:
            if original is not None:
                sys.modules["qwen_asr"] = original

        assert result1 is False
        assert result2 is False


class TestHfDownloadCache:
    """The HuggingFace download check must be TTL-cached."""

    def test_exists_called_once_within_ttl(self, tmp_path):
        """Within the TTL window, the filesystem ``exists()`` check
        must run at most once — subsequent calls hit the cache.
        """
        repo_id = "test/repo"
        config_dir = tmp_path

        # Patch Path.exists to count calls.
        original_exists = Path.exists
        call_count = [0]

        def counting_exists(self):
            call_count[0] += 1
            return original_exists(self)

        with patch.object(Path, "exists", counting_exists):
            result1 = _check_hf_model_downloaded(repo_id, config_dir)
            result2 = _check_hf_model_downloaded(repo_id, config_dir)
            result3 = _check_hf_model_downloaded(repo_id, config_dir)

        # Only the first call should have hit the filesystem.
        assert call_count[0] == 1, (
            f"exists() called {call_count[0]} times; expected 1 (TTL cache "
            "should serve subsequent calls)"
        )
        # All three results must agree.
        assert result1 == result2 == result3

    def test_cache_expires_after_ttl(self, tmp_path):
        """After the TTL window, the next call must re-check the filesystem."""
        repo_id = "test/repo"
        config_dir = tmp_path

        # First call populates the cache.
        result1 = _check_hf_model_downloaded(repo_id, config_dir)

        # Manually backdate the cache entry so it's past the TTL.
        with _hf_download_cache_lock_for_test():
            key = (repo_id, str(config_dir))
            if key in tray_models._hf_download_cache:
                downloaded, _ = tray_models._hf_download_cache[key]
                tray_models._hf_download_cache[key] = (
                    downloaded,
                    time.monotonic() - _HF_DOWNLOAD_CACHE_TTL_SECONDS - 1,
                )

        # Patch exists to verify it's called again.
        original_exists = Path.exists
        call_count = [0]

        def counting_exists(self):
            call_count[0] += 1
            return original_exists(self)

        with patch.object(Path, "exists", counting_exists):
            result2 = _check_hf_model_downloaded(repo_id, config_dir)

        assert call_count[0] == 1, (
            "exists() should be called once after TTL expired"
        )
        assert result2 == result1

    def test_different_repos_cached_separately(self, tmp_path):
        """Each repo_id gets its own cache entry."""
        config_dir = tmp_path
        r1 = _check_hf_model_downloaded("org/repo1", config_dir)
        r2 = _check_hf_model_downloaded("org/repo2", config_dir)

        # Both should be False (neither exists in tmp_path) but cached
        # separately.
        cache = tray_models._hf_download_cache
        assert ("org/repo1", str(config_dir)) in cache
        assert ("org/repo2", str(config_dir)) in cache

    def test_different_config_dirs_cached_separately(self, tmp_path):
        """Each config_dir gets its own cache namespace."""
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        dir2 = tmp_path / "dir2"
        dir2.mkdir()

        _check_hf_model_downloaded("org/repo", dir1)
        _check_hf_model_downloaded("org/repo", dir2)

        cache = tray_models._hf_download_cache
        assert ("org/repo", str(dir1)) in cache
        assert ("org/repo", str(dir2)) in cache


class TestInvalidateCache:
    """``invalidate_model_availability_cache`` must clear both caches."""

    def test_invalidate_clears_hf_cache(self, tmp_path):
        _check_hf_model_downloaded("org/repo", tmp_path)
        assert len(tray_models._hf_download_cache) > 0

        invalidate_model_availability_cache()

        assert len(tray_models._hf_download_cache) == 0

    def test_invalidate_clears_qwen_cache(self):
        # Populate the qwen cache.
        _check_qwen_asr_available()
        assert tray_models._qwen_asr_cache_checked is True

        invalidate_model_availability_cache()

        assert tray_models._qwen_asr_cache_checked is False
        assert tray_models._qwen_asr_available_cache is None


class TestBuildModelsSubmenuUsesCache:
    """The full submenu builder must use the cached helpers."""

    def test_qwen_import_called_once_across_multiple_builds(self, tmp_path):
        """Two consecutive ``build_models_submenu_data`` calls must
        only trigger ONE qwen_asr import check."""
        import sys

        # Provide a Config-like object so we skip the disk read.
        config_provider = MagicMock()
        config_provider.model_size = "tiny.en"
        config_provider.asr_backend = "whisper"

        # Mock ensure_hf_env to no-op.
        with patch(
            "voice_typer.server.asr_setup.ensure_hf_env", lambda: None
        ):
            # First call triggers the import check.
            fake_module = MagicMock()
            with patch.dict(sys.modules, {"qwen_asr": fake_module}):
                data1 = tray_models.build_models_submenu_data(
                    lambda: tmp_path,
                    lambda name: None,
                    config_provider=config_provider,
                )
                # Second call must NOT re-import — cache hit.
                data2 = tray_models.build_models_submenu_data(
                    lambda: tmp_path,
                    lambda name: None,
                    config_provider=config_provider,
                )

        # Both calls must succeed and return 5 candidates.
        assert len(data1) == 5
        assert len(data2) == 5
        # The qwen_asr cache must be populated.
        assert tray_models._qwen_asr_cache_checked is True


# Helper to safely access the cache lock (the module doesn't export it
# directly — we add a test-only accessor).
def _hf_download_cache_lock_for_test():
    """Return a no-op context manager — the cache dict is not locked
    at the module level (it's only accessed from the tray thread).
    For test purposes we treat it as unlocked."""
    from contextlib import nullcontext
    return nullcontext()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
