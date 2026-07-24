"""XV-14 / XV-18 / XV-19: targeted regression tests for the GROUP 2
performance fixes applied to the prewarm package by sub-agent FA3.

These tests pin the specific behavior changes so a future refactor
that reverts any of them fails loudly.  They complement the broader
suites in ``tests/test_prewarm.py`` and ``tests/test_prewarm_process_tracker.py``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import prewarm
from voice_typer.server.prewarm import cache_probe as _cache_probe_mod
from voice_typer.server.prewarm import process_tracker
from voice_typer.server.prewarm import process_tracker as _process_tracker_mod

# ─── XV-19 / XV-18: cache-clearing autouse fixture ──────────────────────


@pytest.fixture(autouse=True)
def _clear_prewarm_caches():
    """Clear ``@lru_cache`` / TTL caches before each test (same as the
    fixture in test_prewarm.py — duplicated here so this file is
    self-contained)."""
    _cache_probe_mod._resolve_hf_cache_dir.cache_clear()
    if hasattr(_cache_probe_mod, "_cached_active_config"):
        _cache_probe_mod._cached_active_config.cache_clear()
    if hasattr(_process_tracker_mod, "_invalidate_cache_probe_cache"):
        _process_tracker_mod._invalidate_cache_probe_cache()
    yield


# ─── XV-14: per-file warm log demoted to DEBUG ──────────────────────────


class TestXV14WarmFileLogLevel:
    """XV-14: ``_warm_file`` per-file log is DEBUG; the per-package summary
    in ``_warm_package_files`` stays at INFO.

    A large package warm emits thousands of per-file log lines (torch has
    ~40k files).  At INFO they flood ``prewarm.log`` and obscure the
    useful per-package summary.  Demoting to DEBUG keeps the summary
    visible while letting operators opt into per-file detail via
    ``--debug``.
    """

    def test_warm_file_logs_at_debug_not_info(self, tmp_path, caplog):
        """``_warm_file`` emits its per-file summary at DEBUG, not INFO."""
        f = tmp_path / "weights.bin"
        f.write_bytes(b"\x00" * 1024)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.prewarm"):
            prewarm._warm_file(f)
        # The per-file "warmed X: ... MB/s" message must be present…
        warm_msgs = [r for r in caplog.records if "warmed" in r.message and "MB/s" in r.message]
        assert warm_msgs, "expected a per-file warm log message"
        # …and it must be at DEBUG level (NOT INFO).
        assert all(r.levelno == logging.DEBUG for r in warm_msgs), (
            "XV-14: per-file warm log must be DEBUG, got levels "
            f"{[r.levelname for r in warm_msgs]}"
        )

    def test_warm_file_no_info_log(self, tmp_path, caplog):
        """No per-file warm message appears at INFO level."""
        f = tmp_path / "weights.bin"
        f.write_bytes(b"\x00" * 1024)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.prewarm"):
            prewarm._warm_file(f)
        info_warm_msgs = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "warmed" in r.message and "MB/s" in r.message
        ]
        assert not info_warm_msgs, (
            "XV-14: per-file warm log must NOT appear at INFO level "
            "(should be DEBUG). Found: " + ", ".join(r.message for r in info_warm_msgs)
        )

    def test_warm_package_files_summary_stays_at_info(self, monkeypatch, tmp_path, caplog):
        """The per-package summary in ``_warm_package_files`` stays at INFO.

        XV-14 only demotes the *per-file* log; the per-package summary
        (one line per package, ~5 packages total) remains at INFO so
        operators can see what was warmed without enabling DEBUG.
        """
        (tmp_path / "a.pyc").write_bytes(b"x" * 1024)

        class _FakeSpec:
            submodule_search_locations = [str(tmp_path)]
            origin = None

        monkeypatch.setattr(prewarm.importlib.util, "find_spec", lambda name: _FakeSpec())
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: 1024)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.prewarm"):
            prewarm._warm_package_files("fakepkg")

        summary_msgs = [
            r for r in caplog.records if "file-warmed" in r.message and "fakepkg" in r.message
        ]
        assert summary_msgs, (
            "XV-14: per-package summary must still be at INFO — "
            "only the per-file log was demoted."
        )
        assert all(r.levelno == logging.INFO for r in summary_msgs)


# ─── XV-16: suffix filter on _warm_package_files ────────────────────────


class TestXV16SuffixFilter:
    """XV-16: ``_warm_package_files`` only warms .pyc/.so/.pyd/.dll/.json/.txt."""

    def test_pyc_so_pyd_dll_json_txt_are_warmed(self, monkeypatch, tmp_path):
        """All six suffixes in _WARM_PACKAGE_SUFFIXES are warmed."""
        suffixes = [".pyc", ".so", ".pyd", ".dll", ".json", ".txt"]
        for i, suffix in enumerate(suffixes):
            (tmp_path / f"file{i}{suffix}").write_bytes(b"x" * 16)

        class _FakeSpec:
            submodule_search_locations = [str(tmp_path)]
            origin = None

        reads: list[Path] = []
        monkeypatch.setattr(prewarm.importlib.util, "find_spec", lambda name: _FakeSpec())
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: reads.append(Path(p)) or 0)

        prewarm._warm_package_files("fakepkg")

        warmed_suffixes = {p.suffix for p in reads}
        assert warmed_suffixes == set(suffixes), (
            f"XV-16: expected all six suffixes warmed, got {warmed_suffixes}"
        )

    def test_non_whitelisted_suffixes_are_skipped(self, monkeypatch, tmp_path):
        """Files with suffixes NOT in the whitelist are skipped."""
        # These should all be skipped.
        for name in ("image.png", "data.csv", "weights.bin", "model.pt", "readme.md"):
            (tmp_path / name).write_bytes(b"x" * 16)
        # This should be warmed.
        (tmp_path / "keep.pyc").write_bytes(b"x" * 16)

        class _FakeSpec:
            submodule_search_locations = [str(tmp_path)]
            origin = None

        reads: list[Path] = []
        monkeypatch.setattr(prewarm.importlib.util, "find_spec", lambda name: _FakeSpec())
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: reads.append(Path(p)) or 0)

        prewarm._warm_package_files("fakepkg")

        assert reads == [tmp_path / "keep.pyc"], (
            "XV-16: only .pyc should be warmed; got " + str(reads)
        )

    def test_warm_file_does_not_del_chunk(self):
        """XV-16: the ``del chunk`` line was removed from ``_warm_file``.

        We verify by inspecting the source — the ``del chunk`` statement
        must NOT appear in the function body.  CPython's reference
        counting frees the bytes object on the next ``chunk = f.read(...)``
        assignment, so the explicit ``del`` was a no-op that added
        line-noise.
        """
        import inspect

        source = inspect.getsource(prewarm._warm_file)
        assert "del chunk" not in source, (
            "XV-16: the `del chunk` line should have been removed from "
            "_warm_file (it was a no-op — CPython frees the bytes object "
            "on the next assignment)."
        )


# ─── XV-15: no sorted() wrapper in _warm_package_files ──────────────────


class TestXV15NoSortedWrapper:
    """XV-15: ``_warm_package_files`` iterates ``root.rglob("*")`` directly."""

    def test_no_sorted_call_in_source(self):
        """The ``sorted(...)`` wrapper must not appear in the function body."""
        import inspect

        source = inspect.getsource(prewarm._warm_package_files)
        # The rglob line must NOT use sorted().
        assert "sorted(root.rglob" not in source, (
            "XV-15: sorted(root.rglob('*')) should be replaced with "
            "direct iteration — sorted() forces a full directory walk "
            "into memory before the first read."
        )
        # Direct rglob iteration must be present.
        assert "root.rglob(" in source, "XV-15: root.rglob('*') iteration must still be present"


# ─── XV-18: TTL memoization of cache-ratio probe ────────────────────────


class TestXV18CacheProbeMemoization:
    """XV-18: ``_probe_cache_status`` memoizes with a 30s TTL keyed on
    directory mtime fingerprint."""

    def _build_fake_cache(self, tmp_path: Path) -> list[Path]:
        """Build a fake HF cache with one model dir; return [model_dir]."""
        cache = tmp_path / "huggingface" / "hub"
        model_dir = cache / "models--test--model"
        snap = model_dir / "snapshots" / "abc"
        snap.mkdir(parents=True)
        weights = snap / "model.safetensors"
        weights.write_bytes(b"\x00" * (1024 * 1024))  # 1 MB
        return [model_dir]

    def test_second_call_within_ttl_returns_cached_value(self, monkeypatch, tmp_path):
        """Two calls within the TTL window return the same cached values
        WITHOUT re-invoking ``_cache_ratio`` on the second call."""
        active_dirs = self._build_fake_cache(tmp_path)

        call_count = {"n": 0}

        def counting_cache_ratio(path, samples=20):
            call_count["n"] += 1
            return 0.5

        monkeypatch.setattr(prewarm, "_cache_ratio", counting_cache_ratio)

        # First call → computes fresh, caches, calls _cache_ratio once.
        r1 = process_tracker._probe_cache_status(active_dirs)
        assert call_count["n"] == 1, f"first call should invoke _cache_ratio once, got {call_count['n']}"

        # Second call within TTL → returns cached, does NOT call _cache_ratio.
        r2 = process_tracker._probe_cache_status(active_dirs)
        assert call_count["n"] == 1, (
            "XV-18: second call within TTL must NOT re-invoke _cache_ratio "
            f"(got {call_count['n']} calls)"
        )
        assert r1 == r2, "cached value must match the freshly-computed value"

    def test_invalidate_cache_clears_entry(self, monkeypatch, tmp_path):
        """``_invalidate_cache_probe_cache`` forces the next call to recompute."""
        active_dirs = self._build_fake_cache(tmp_path)

        call_count = {"n": 0}

        def counting_cache_ratio(path, samples=20):
            call_count["n"] += 1
            return 0.5

        monkeypatch.setattr(prewarm, "_cache_ratio", counting_cache_ratio)

        process_tracker._probe_cache_status(active_dirs)
        assert call_count["n"] == 1

        process_tracker._invalidate_cache_probe_cache()

        process_tracker._probe_cache_status(active_dirs)
        assert call_count["n"] == 2, (
            "XV-18: after _invalidate_cache_probe_cache, the next call "
            "must recompute (re-invoke _cache_ratio)."
        )

    def test_empty_dirs_returns_zero_without_caching(self):
        """An empty active_dirs list returns (0.0, 0, 0) and doesn't cache."""
        process_tracker._invalidate_cache_probe_cache()
        result = process_tracker._probe_cache_status([])
        assert result == (0.0, 0, 0)
        # Cache must remain empty (nothing to cache for empty list).
        assert process_tracker._cache_probe_cache == {}, (
            "XV-18: empty active_dirs must not pollute the cache"
        )

    def test_fingerprint_changes_on_mtime_change(self, monkeypatch, tmp_path):
        """When a directory's mtime changes, the cache misses and recomputes."""
        active_dirs = self._build_fake_cache(tmp_path)

        call_count = {"n": 0}

        def counting_cache_ratio(path, samples=20):
            call_count["n"] += 1
            return 0.5

        monkeypatch.setattr(prewarm, "_cache_ratio", counting_cache_ratio)

        # First call → cache miss → compute.
        process_tracker._probe_cache_status(active_dirs)
        assert call_count["n"] == 1

        # Bump the mtime on the model dir (simulates a new snapshot
        # download — same path, different mtime → different fingerprint).
        model_dir = active_dirs[0]
        original_mtime = model_dir.stat().st_mtime_ns
        # Set mtime to the future so it's definitely different.
        future_time = time.time() + 100
        import os

        os.utime(model_dir, (future_time, future_time))
        new_mtime = model_dir.stat().st_mtime_ns
        assert new_mtime != original_mtime, "test setup: mtime must change"

        # Second call → cache miss (fingerprint changed) → recompute.
        process_tracker._probe_cache_status(active_dirs)
        assert call_count["n"] == 2, (
            "XV-18: mtime change must invalidate the cache (fingerprint "
            f"changed but _cache_ratio was only called {call_count['n']} times)"
        )

    def test_get_prewarm_status_uses_cached_probe(self, monkeypatch, tmp_path):
        """``get_prewarm_status`` returns cached probe values on the second call."""
        active_dirs = self._build_fake_cache(tmp_path)

        sentinel = tmp_path / "sentinel"
        sentinel.write_text("1700000000\n20.5\n2026-01-02T03:04:05\n")
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: active_dirs)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        call_count = {"n": 0}

        def counting_cache_ratio(path, samples=20):
            call_count["n"] += 1
            return 0.5

        monkeypatch.setattr(prewarm, "_cache_ratio", counting_cache_ratio)

        # First status call → fresh probe.
        s1 = prewarm.get_prewarm_status()
        assert call_count["n"] == 1
        assert s1["total_bytes"] == 1024 * 1024

        # Second status call within TTL → cached probe (no _cache_ratio call).
        s2 = prewarm.get_prewarm_status()
        assert call_count["n"] == 1, (
            "XV-18: get_prewarm_status second call must use cached probe "
            f"(got {call_count['n']} _cache_ratio calls)"
        )
        assert s2["cache_ratio"] == s1["cache_ratio"]
        assert s2["cached_bytes"] == s1["cached_bytes"]


# ─── XV-19: @lru_cache on _resolve_hf_cache_dir + cached Config ─────────


class TestXV19LruCache:
    """XV-19: ``_resolve_hf_cache_dir`` is cached with ``@lru_cache(maxsize=1)``
    and ``Config.load()`` is shared via ``_cached_active_config``."""

    def test_resolve_hf_cache_dir_has_lru_cache(self):
        """``_resolve_hf_cache_dir`` must be wrapped with ``@lru_cache``."""
        func = prewarm._resolve_hf_cache_dir
        assert hasattr(func, "cache_clear"), (
            "XV-19: _resolve_hf_cache_dir must be wrapped with @lru_cache "
            "(missing cache_clear attribute)"
        )
        assert hasattr(func, "cache_info"), (
            "XV-19: _resolve_hf_cache_dir must be wrapped with @lru_cache "
            "(missing cache_info attribute)"
        )

    def test_resolve_hf_cache_dir_cached_across_calls(self, monkeypatch, tmp_path):
        """Two calls with the same _config_dir return the cached value
        WITHOUT re-invoking _config_dir on the second call."""
        fake_config = tmp_path / "config"
        (fake_config / "huggingface").mkdir(parents=True)

        call_count = {"n": 0}

        def counting_config_dir():
            call_count["n"] += 1
            return fake_config

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            counting_config_dir,
        )

        # First call → _config_dir invoked.
        r1 = prewarm._resolve_hf_cache_dir()
        assert call_count["n"] == 1, f"first call should invoke _config_dir once, got {call_count['n']}"

        # Second call → cached, _config_dir NOT invoked.
        r2 = prewarm._resolve_hf_cache_dir()
        assert call_count["n"] == 1, (
            "XV-19: second call must NOT re-invoke _config_dir "
            f"(got {call_count['n']} calls)"
        )
        assert r1 == r2

    def test_cached_active_config_exists(self):
        """``_cached_active_config`` helper exists and is lru_cached."""
        assert hasattr(_cache_probe_mod, "_cached_active_config"), (
            "XV-19: _cached_active_config helper must exist in cache_probe"
        )
        func = _cache_probe_mod._cached_active_config
        assert hasattr(func, "cache_clear"), (
            "XV-19: _cached_active_config must be wrapped with @lru_cache"
        )

    def test_cached_active_config_shared_between_warm_imports_and_active_dirs(
        self, monkeypatch, tmp_path
    ):
        """``_warm_imports`` and ``_active_model_cache_dirs`` share the same
        cached Config.load() result (Config.load runs at most once)."""
        # Build a fake HF cache.
        hf_cache = tmp_path / "huggingface" / "hub"
        (hf_cache / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "abc").mkdir(parents=True)

        fake_cfg = MagicMock(asr_backend="whisper", model_size="tiny.en")
        call_count = {"n": 0}

        def counting_load():
            call_count["n"] += 1
            return fake_cfg

        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: counting_load()),
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )
        # Stub _warm_package_files so _warm_imports doesn't actually read files.
        monkeypatch.setattr(prewarm, "_warm_package_files", lambda pkg: 0)

        # Call _warm_imports (invokes _cached_active_config → Config.load).
        prewarm._warm_imports()
        assert call_count["n"] == 1, (
            f"_warm_imports should trigger one Config.load, got {call_count['n']}"
        )

        # Call _active_model_cache_dirs (reuses cached Config, no new load).
        dirs = prewarm._active_model_cache_dirs()
        assert call_count["n"] == 1, (
            "XV-19: _active_model_cache_dirs must reuse the cached Config "
            f"from _warm_imports (Config.load called {call_count['n']} times, expected 1)"
        )
        assert dirs, "expected at least one cache dir for whisper-tiny.en"

    def test_cache_clear_resets_resolve_hf_cache_dir(self, monkeypatch, tmp_path):
        """``cache_clear()`` forces the next call to re-resolve."""
        fake_config = tmp_path / "config"
        (fake_config / "huggingface").mkdir(parents=True)

        call_count = {"n": 0}

        def counting_config_dir():
            call_count["n"] += 1
            return fake_config

        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            counting_config_dir,
        )

        prewarm._resolve_hf_cache_dir()
        assert call_count["n"] == 1

        prewarm._resolve_hf_cache_dir.cache_clear()

        prewarm._resolve_hf_cache_dir()
        assert call_count["n"] == 2, (
            "XV-19: after cache_clear(), the next call must re-invoke _config_dir"
        )
