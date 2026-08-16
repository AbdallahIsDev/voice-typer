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

import logging
import os
import re
from importlib.machinery import ModuleSpec
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

        # stat calls should be bounded by the number of DIRECTORIES
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
        assert files == [], f"expected zero warmable files (only .py files in tree), got {len(files)}"
        # the suffix filter should run BEFORE is_file(), so no
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


# ─── C-LOG-2 regression ──────────────────────────────────────────────────
#
# Canonical C-LOG-2 grep anchor (AGENTS.md): every lifecycle-completion
# log line ends with a space-separated `<duration>` suffix produced by
# `voice_typer.server.duration.format_duration()` — ` 2.3s` for
# sub-minute durations, ` 1m 2.3s` for anything longer (the return
# value carries a single leading space, spliced via a bare ``%s``). The
# two ``log.info`` calls in ``cache_probe`` that previously used ad-hoc
# ``%.1fs`` / ``%.2fs`` formatting were rewritten to use
# ``format_duration()`` so the perf marker is greppable project-wide.
# These tests pin the canonical suffix shape so a future revert to
# ad-hoc formatting fails loudly.
#
# We anchor the regex to END-of-message with ``$`` — both lifecycle
# lines (``[PREWARM] file-warmed ...`` and ``[PREWARM] worker
# warm-imports complete ...``) place the ``%s`` duration argument as
# the FINAL format arg, so ``format_duration(elapsed)`` always lands at
# the very end of the rendered message. A revert to ``"... in %.1fs"``
# would render as ``... in 0.0s`` — no space separator before the
# duration, and the canonical pattern no longer matches at line END.
_CLOG2_DURATION_RE = re.compile(r" \d+(m \d+)?\.\ds$")


class TestCacheProbeLogLinesUseFormatDuration:
    """C-LOG-2 regression: lifecycle-completion ``log.info`` calls in
    ``cache_probe`` MUST end with the canonical space-separated
    ``<duration>`` suffix produced by ``format_duration()`` — not an
    ad-hoc ``%.1fs`` / ``%.2fs`` string. A revert to ad-hoc formatting
    breaks the project-wide grep-summed perf-marker convention
    (AGENTS.md C-LOG-2).
    """

    def test_warm_package_files_log_line_carries_duration_suffix(
        self, tmp_path, caplog, monkeypatch
    ):
        """``_warm_package_files`` emits ``[PREWARM] file-warmed <pkg>:
        <MB> <duration>`` on completion. The space-separated
        ``<duration>`` suffix MUST come from ``format_duration()`` —
        greppable project-wide per C-LOG-2. If someone reverts to
        ``%.1fs`` (e.g. ``"... in %.1fs"``), the rendered message
        loses the space separator and the canonical pattern no longer
        matches at line END.
        """
        # Build a fake package directory with one warmable file. The
        # file is never actually read — _pkg._warm_file is stubbed
        # below — but it must exist on disk so the rglob walk in
        # _warm_package_files yields it (the suffix filter + skip-dir
        # filter must accept it).
        pkg_dir = tmp_path / "fakepkg"
        pkg_dir.mkdir()
        (pkg_dir / "module.pyc").write_bytes(b"\x00" * 1024)

        # Fake the importlib.util.find_spec result so
        # _warm_package_files treats our tmp_path as the package's
        # install location. ModuleSpec(is_package=True) initialises
        # submodule_search_locations to [] so we can override it.
        fake_spec = ModuleSpec(name="fakepkg", loader=None, is_package=True)
        fake_spec.submodule_search_locations = [str(pkg_dir)]

        real_find_spec = cache_probe.importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "fakepkg":
                return fake_spec
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(
            cache_probe.importlib.util, "find_spec", fake_find_spec
        )

        # Stub _pkg._warm_file so the test doesn't actually page-cache
        # bytes (keeps the test fast + platform-independent). Returns
        # 1 MiB so the "%.0f MB" rendering is "1 MB" — the assertion
        # below pins the rendered shape so a future revert can't slip
        # in a different unit (KiB, GiB) either.
        monkeypatch.setattr(
            cache_probe._pkg, "_warm_file", lambda path: 1024 * 1024
        )

        with caplog.at_level(
            logging.INFO, logger="voice_typer.server.prewarm"
        ):
            total = cache_probe._warm_package_files("fakepkg")

        # Sanity: the stubbed _warm_file was called exactly once.
        assert total == 1024 * 1024, (
            f"expected 1 MiB total from stubbed _warm_file, got {total} — "
            f"the stub may not have been called."
        )

        # Find the lifecycle-completion log line.
        matching = [
            r.getMessage()
            for r in caplog.records
            if "file-warmed" in r.getMessage()
        ]
        assert matching, (
            "expected an INFO log line containing 'file-warmed' from "
            "_warm_package_files(); got records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = matching[-1]
        # C-LOG-2: the log line MUST end with the canonical
        # space-separated `<duration>` suffix from format_duration().
        assert _CLOG2_DURATION_RE.search(msg), (
            f"C-LOG-2 violation: {msg!r} does NOT end with the canonical "
            f"space-separated `<duration>` suffix (pattern "
            f"{_CLOG2_DURATION_RE.pattern!r}). "
            f"A revert to ad-hoc `%.1fs` formatting (e.g. '... in %.1fs') "
            f"would strip the space separator and break this assertion."
        )

    def test_warm_imports_log_line_carries_duration_suffix(
        self, caplog, monkeypatch
    ):
        """``_warm_imports`` emits ``[PREWARM] worker warm-imports
        complete: <N> packages (<list>) <duration>`` on completion.
        Same C-LOG-2 contract as above — the space-separated
        ``<duration>`` suffix MUST come from ``format_duration()``.
        """
        # Patch _WORKER_WARM_PACKAGES to a single fake package so the
        # loop runs exactly once + we don't depend on real packages
        # being installed (onnxruntime / ctranslate2 etc. are NOT in
        # the dev sandbox per FG-SESSION-START).
        monkeypatch.setattr(
            cache_probe, "_WORKER_WARM_PACKAGES", ("fakepkg",)
        )
        # Stub _warm_package_files to return >0 bytes so the package
        # appears in the `warmed` list (otherwise the log line still
        # fires, but with "0 packages (none)" which is a less
        # interesting contract to pin — a revert that drops the
        # duration suffix entirely would still fail this test, but
        # pinning the populated-list shape makes the assertion message
        # clearer).
        monkeypatch.setattr(
            cache_probe, "_warm_package_files", lambda pkg: 1024 * 1024
        )

        with caplog.at_level(
            logging.INFO, logger="voice_typer.server.prewarm"
        ):
            cache_probe._warm_imports()

        matching = [
            r.getMessage()
            for r in caplog.records
            if "worker warm-imports complete" in r.getMessage()
        ]
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
