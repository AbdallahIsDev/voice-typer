"""DJ-46: stat-count regression test for ``cache_probe._iter_warmable_files``.

The previous ``root.rglob('*')`` + ``path.is_file()`` pattern issued a
fresh ``stat()`` syscall per file (~40 k stats for torch alone) even
though ``readdir`` already returned the d_type for each entry. DJ-46
replaces this with ``os.scandir`` + ``DirEntry.is_file()`` which uses
the cached d_type — no per-file ``stat()`` on filesystems that
populate d_type (ext4/tmpfs on Linux, APFS on macOS, NTFS on Windows).

These tests pin the fix so a future revert fails loudly.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from voice_typer.server.prewarm import cache_probe

# ─── Test tree ────────────────────────────────────────────────────────────


def _build_tree(root: Path, n_subdirs: int = 5, files_per_dir: int = 10) -> int:
    """Build a tree with ``n_subdirs`` subdirectories, each containing
    ``files_per_dir`` .pyc files (warmable) AND ``files_per_dir`` .py
    files (non-warmable). Returns the total number of warmable files
    created (i.e. the expected count from ``_iter_warmable_files``).
    """
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


# ─── Tests ────────────────────────────────────────────────────────────────


class TestCacheProbeStatCount:
    """DJ-46: walking a package tree must NOT issue a stat() per file."""

    def test_walk_does_not_stat_per_file(self, tmp_path):
        """``_iter_warmable_files`` must NOT issue a ``stat()`` per file.

        We build a tree with 5 subdirs × 10 .pyc files = 50 warmable
        files, plus 50 non-warmable .py files (100 files total). We then
        count ``os.stat`` calls during the walk.

        Old code (``root.rglob('*')`` + ``path.is_file()``): one stat
        per file = ~100 stats.

        New code (``os.scandir`` + ``DirEntry.is_file()``): zero stats
        on filesystems that populate d_type (ext4/tmpfs on Linux). We
        allow up to 10 stats for filesystems with DT_UNKNOWN, which is
        still 10x less than the old code's 100.
        """
        warmable_count = _build_tree(tmp_path, n_subdirs=5, files_per_dir=10)
        assert warmable_count == 50

        # Count os.stat calls during the walk. We patch the GLOBAL os.stat
        # (cache_probe imports os as a module, so cache_probe.os.stat IS
        # os.stat — same module object).
        real_os_stat = os.stat
        stat_calls = {"n": 0}

        def counting_stat(*args, **kwargs):
            stat_calls["n"] += 1
            return real_os_stat(*args, **kwargs)

        # Patch via the cache_probe module's os binding so the patch
        # propagates to pathlib.Path.is_file() (which calls os.stat
        # indirectly via the pathlib module's os binding — same object).
        with patch("voice_typer.server.prewarm.cache_probe.os.stat", counting_stat):
            files = list(cache_probe._iter_warmable_files(tmp_path))

        # Sanity: 50 .pyc files should be discovered.
        assert len(files) == warmable_count, (
            f"expected {warmable_count} warmable files, got {len(files)} — "
            f"the walk is missing files (a perf bug that could leave cold "
            f"pages unwarmed)."
        )

        # DJ-46: stat calls should be bounded by the number of DIRECTORIES
        # (not files). With 6 dirs (root + 5 subdirs) and 100 files total,
        # the old code would issue ~100 stats; the new code should issue
        # 0-10 (zero on filesystems that populate d_type, up to 10 for
        # DT_UNKNOWN fallbacks).
        assert stat_calls["n"] <= 10, (
            f"DJ-46: _iter_warmable_files issued {stat_calls['n']} stat() "
            f"calls for 100 files in 6 dirs — expected <=10 (no per-file "
            f"stat). The old root.rglob('*') + path.is_file() pattern would "
            f"have issued ~100 stats (one per file). If this number is "
            f"close to 100, the fix was reverted."
        )

    def test_walk_filters_non_warmable_suffixes_without_stat(self, tmp_path):
        """Files whose suffix is NOT in ``_WARM_PACKAGE_SUFFIXES`` must be
        filtered out by a free string comparison BEFORE any ``is_file()``
        call — so a non-warmable file never triggers a ``stat()`` even on
        filesystems with DT_UNKNOWN.
        """
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
        assert files == [], (
            f"expected zero warmable files (only .py files in tree), got {len(files)}"
        )
        # DJ-46: the suffix filter should run BEFORE is_file(), so no
        # stat() calls at all for non-warmable entries.
        assert stat_calls["n"] <= 1, (
            f"DJ-46: non-warmable files triggered {stat_calls['n']} stat() "
            f"calls — the suffix filter should run BEFORE is_file() so "
            f"non-matching entries never trigger a stat."
        )

    def test_walk_handles_symlinks_without_infinite_loop(self, tmp_path):
        """A symlink loop must NOT cause an infinite walk.

        ``follow_symlinks=False`` on ``is_dir`` and ``is_file`` ensures
        symlinked directories are not descended into, preventing loops.
        """
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
                f"{f.suffix!r} not in _WARM_PACKAGE_SUFFIXES — the suffix "
                f"filter is broken."
            )

    def test_walk_handles_nested_directories(self, tmp_path):
        """The walk must descend into nested subdirectories (iterative
        stack-based, not recursive — so deep trees don't hit the recursion
        limit)."""
        # Build a 10-level deep tree.
        current = tmp_path
        for i in range(10):
            current = current / f"lvl{i}"
            current.mkdir()
            (current / f"f{i}.pyc").write_bytes(b"x")

        files = list(cache_probe._iter_warmable_files(tmp_path))
        assert len(files) == 10, (
            f"expected 10 .pyc files across 10 nested dirs, got {len(files)} — "
            f"the walk did not descend into all subdirectories."
        )
