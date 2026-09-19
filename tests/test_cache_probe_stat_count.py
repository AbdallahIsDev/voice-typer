"""DJ-46: stat-count regression test for ``cache_probe._iter_warmable_files``."""

from __future__ import annotations

import logging
import os
import re
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import patch

import pytest
from voice_typer.server.prewarm import cache_probe


def _build_tree(root: Path, n_subdirs: int = 5, files_per_dir: int = 10) -> int:
    """Build a tree with ``n_subdirs`` subdirectories, each containing"""
    warmable = 0
    for i in range(n_subdirs):
        d = root / f"d{i}"
        d.mkdir()
        for j in range(files_per_dir):
            (d / f"f{j}.pyc").write_bytes(b"x")
            warmable += 1
            # Non-warmable: should be skipped without stat.
            (d / f"skip{j}.py").write_text("x")
    return warmable


class TestCacheProbeStatCount:
    """DJ-46: walking a package tree must NOT issue a stat() per file."""

    def test_walk_does_not_stat_per_file(self, tmp_path):
        """``_iter_warmable_files`` must NOT issue a ``stat()`` per file."""
        warmable_count = _build_tree(tmp_path, n_subdirs=5, files_per_dir=10)
        assert warmable_count == 50

        # Count os.stat calls during the walk. We patch the GLOBAL os.stat
        real_os_stat = os.stat
        stat_calls = {"n": 0}

        def counting_stat(*args, **kwargs):
            stat_calls["n"] += 1
            return real_os_stat(*args, **kwargs)

        # Patch via the cache_probe module's os binding so the patch
        with patch("voice_typer.server.prewarm.cache_probe.os.stat", counting_stat):
            files = list(cache_probe._iter_warmable_files(tmp_path))

        # Sanity: 50 .pyc files should be discovered.
        assert len(files) == warmable_count, (
            f"expected {warmable_count} warmable files, got {len(files)}, "
            f"the walk is missing files (a perf bug that could leave cold "
            f"pages unwarmed)."
        )

        assert stat_calls["n"] <= 10, (
            f"DJ-46: _iter_warmable_files issued {stat_calls['n']} stat() "
            f"calls for 100 files in 6 dirs, expected <=10 (no per-file "
            f"stat). The old root.rglob('*') + path.is_file() pattern would "
            f"have issued ~100 stats (one per file). If this number is "
            f"close to 100, the fix was reverted."
        )

    def test_walk_filters_non_warmable_suffixes_without_stat(self, tmp_path):
        """Files whose suffix is NOT in ``_WARM_PACKAGE_SUFFIXES`` must be"""
        # Build a tree with ONLY non-warmable files.
        d = tmp_path / "d"
        d.mkdir()
        for j in range(20):
            (d / f"skip{j}.py").write_text("x")

        real_os_stat = os.stat
        stat_calls = {"n": 0}

        def counting_stat(*args, **kwargs):
            stat_calls["n"] += 1
            return real_os_stat(*args, **kwargs)

        with patch("voice_typer.server.prewarm.cache_probe.os.stat", counting_stat):
            files = list(cache_probe._iter_warmable_files(tmp_path))

        # No warmable files should be discovered.
        assert files == [], f"expected zero warmable files (only .py files in tree), got {len(files)}"
        assert stat_calls["n"] <= 1, (
            f"DJ-46: non-warmable files triggered {stat_calls['n']} stat() "
            f"calls, the suffix filter should run BEFORE is_file() so "
            f"non-matching entries never trigger a stat."
        )

    def test_walk_handles_symlinks_without_infinite_loop(self, tmp_path):
        """A symlink loop must NOT cause an infinite walk."""
        d = tmp_path / "d"
        d.mkdir()
        # Create a .pyc file.
        (d / "f.pyc").write_bytes(b"x")
        # Create a symlink loop: d/loop -> d (itself).
        try:
            (d / "loop").symlink_to(d, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported on this platform")

        # If the walk doesn't terminate, the test will time out.
        files = list(cache_probe._iter_warmable_files(tmp_path))
        # Should find the one .pyc file.
        pyc_files = [f for f in files if f.suffix == ".pyc"]
        assert len(pyc_files) == 1

    def test_walk_yields_paths_with_correct_suffixes(self, tmp_path):
        """All yielded paths must have a suffix in ``_WARM_PACKAGE_SUFFIXES``."""
        _build_tree(tmp_path, n_subdirs=3, files_per_dir=5)

        files = list(cache_probe._iter_warmable_files(tmp_path))
        for f in files:
            assert f.suffix in cache_probe._WARM_PACKAGE_SUFFIXES, (
                f"DJ-46: _iter_warmable_files yielded {f} with suffix "
                f"{f.suffix!r} not in _WARM_PACKAGE_SUFFIXES, the suffix "
                f"filter is broken."
            )

    def test_walk_handles_nested_directories(self, tmp_path):
        """The walk must descend into nested subdirectories (iterative"""
        # Build a 10-level deep tree.
        current = tmp_path
        for i in range(10):
            current = current / f"lvl{i}"
            current.mkdir()
            (current / f"f{i}.pyc").write_bytes(b"x")

        files = list(cache_probe._iter_warmable_files(tmp_path))
        assert len(files) == 10, (
            f"expected 10 .pyc files across 10 nested dirs, got {len(files)}, "
            f"the walk did not descend into all subdirectories."
        )


# ─── C-LOG-2 regression ──────────────────────────────────────────────────
# Canonical C-LOG-2 grep anchor (AGENTS.md): every lifecycle-completion
_CLOG2_DURATION_RE = re.compile(r" \d+(m \d+)?\.\ds$")


class TestCacheProbeLogLinesUseFormatDuration:
    """``cache_probe`` MUST end with the canonical space-separated"""

    def test_warm_package_files_log_line_carries_duration_suffix(self, tmp_path, caplog, monkeypatch):
        """``_warm_package_files`` emits ``[PREWARM] file-warmed <pkg>:"""
        pkg_dir = tmp_path / "fakepkg"
        pkg_dir.mkdir()
        (pkg_dir / "module.pyc").write_bytes(b"\x00" * 1024)

        fake_spec = ModuleSpec(name="fakepkg", loader=None, is_package=True)
        fake_spec.submodule_search_locations = [str(pkg_dir)]

        real_find_spec = cache_probe.importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "fakepkg":
                return fake_spec
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(cache_probe.importlib.util, "find_spec", fake_find_spec)

        monkeypatch.setattr(cache_probe, "_warm_file", lambda path: 1024 * 1024)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.prewarm"):
            total = cache_probe._warm_package_files("fakepkg")

        # Sanity: the stubbed _warm_file was called exactly once.
        assert total == 1024 * 1024, (
            f"expected 1 MiB total from stubbed _warm_file, got {total}, the stub may not have been called."
        )

        # Find the lifecycle-completion log line.
        matching = [r.getMessage() for r in caplog.records if "file-warmed" in r.getMessage()]
        assert matching, (
            "expected an INFO log line containing 'file-warmed' from "
            "_warm_package_files(); got records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = matching[-1]
        # C-LOG-2: the log line MUST end with the canonical
        assert _CLOG2_DURATION_RE.search(msg), (
            f"C-LOG-2 violation: {msg!r} does NOT end with the canonical "
            f"space-separated `<duration>` suffix (pattern "
            f"{_CLOG2_DURATION_RE.pattern!r}). "
            f"A revert to ad-hoc `%.1fs` formatting (e.g. '... in %.1fs') "
            f"would strip the space separator and break this assertion."
        )

    def test_warm_imports_log_line_carries_duration_suffix(self, caplog, monkeypatch):
        """
        ``_warm_imports`` emits ``[PREWARM] worker warm-imports
        Same C-LOG-2 contract as above, the space-separated
        """
        monkeypatch.setattr(cache_probe, "_WORKER_WARM_PACKAGES", ("fakepkg",))
        monkeypatch.setattr(cache_probe, "_warm_package_files", lambda pkg: 1024 * 1024)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.prewarm"):
            cache_probe._warm_imports()

        matching = [r.getMessage() for r in caplog.records if "worker warm-imports complete" in r.getMessage()]
        assert matching, (
            "expected an INFO log line containing 'worker warm-imports "
            "complete' from _warm_imports(); got records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = matching[-1]
        assert _CLOG2_DURATION_RE.search(msg), (
            f"C-LOG-2 violation: {msg!r} does NOT end with the canonical "
            f"space-separated `<duration>` suffix (pattern "
            f"{_CLOG2_DURATION_RE.pattern!r}). "
            f"A revert to ad-hoc `%.2fs` formatting would strip the space "
            f"separator and break this assertion."
        )


class TestWarmModelWeights:
    """Weight-file warming: the warm phase must cover the multi-GB model"""

    def test_warm_model_weights_reads_cold_files(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "hub" / "models--test--model"
        snap = cache_dir / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))

        read_paths: list = []
        monkeypatch.setattr(cache_probe, "_warm_file", lambda p: read_paths.append(p) or 2 * 1024 * 1024)
        monkeypatch.setattr(cache_probe, "_cache_ratio", lambda p, samples=20: 0.0)

        total = cache_probe._warm_model_weights([cache_dir])

        assert total == 2 * 1024 * 1024
        assert read_paths == [snap / "model.bin"]

    def test_warm_model_weights_skips_hot_files(self, tmp_path, monkeypatch):
        """A file at/above the hot threshold is NOT re-read."""
        cache_dir = tmp_path / "hub" / "models--test--model"
        snap = cache_dir / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))

        monkeypatch.setattr(cache_probe, "_warm_file", lambda p: pytest.fail("hot file must not be re-read"))
        monkeypatch.setattr(cache_probe, "_cache_ratio", lambda p, samples=20: 1.0)

        assert cache_probe._warm_model_weights([cache_dir]) == 0

    def test_warm_model_weights_empty_dirs(self, tmp_path):
        assert cache_probe._warm_model_weights([]) == 0

    def test_warm_imports_includes_model_weights(self, monkeypatch):
        """_warm_imports must call the weight-warm pass (the warm list"""
        calls: list[str] = []
        monkeypatch.setattr(cache_probe, "_WORKER_WARM_PACKAGES", ("fakepkg",))
        monkeypatch.setattr(cache_probe, "_warm_package_files", lambda pkg: 1024)
        monkeypatch.setattr(cache_probe, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(cache_probe, "_warm_model_weights", lambda dirs: calls.append("weights") or 0)

        cache_probe._warm_imports()

        assert calls == ["weights"]
