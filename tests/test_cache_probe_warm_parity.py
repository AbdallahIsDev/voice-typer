"""Prewarm file-warming wiring + behavioral-parity pins.

``_iter_warmable_files`` (os.scandir, cached d_type — no per-file stat)
was built and tested long before production actually called it: the
``_warm_package_files`` loop kept using the old ``rglob('*')`` +
``is_file()`` pattern, so the stat-count regression test guarded a
helper that production never executed.

These tests pin the completed wiring:
1. ``_warm_package_files`` actually routes through ``_iter_warmable_files``
   (source-level wiring pin, so the wiring cannot silently regress again);
2. the warmed-file SET is byte-identical between the old consumer-filtered
   rglob pattern and the new walker (the skip-dir pruning now lives inside
   the walker — deleting it outright would warm MORE files than the old
   pattern, a startup regression);
3. skip directories (``tests/``, ``docs/``, ``__pycache__``,
   ``*.dist-info``/``*.egg-info``) are pruned by the walker itself.
"""

from __future__ import annotations

import inspect
from importlib.machinery import ModuleSpec
from pathlib import Path

from voice_typer.server.prewarm import cache_probe


def _old_pattern_warmable_files(root: Path) -> set[Path]:
    """Replicate the OLD ``_warm_package_files`` inner loop verbatim
    (rglob + is_file + suffix filter + consumer-side skip-dir filter)
    so the parity check compares against the historical behavior."""
    warmable: set[Path] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in cache_probe._WARM_PACKAGE_SUFFIXES:
            continue
        rel = path.relative_to(root)
        if any(
            part in cache_probe._WARM_PACKAGE_SKIP_DIRS or part.endswith(".dist-info") or part.endswith(".egg-info")
            for part in rel.parts[:-1]
        ):
            continue
        warmable.add(path)
    return warmable


def _build_mixed_tree(root: Path) -> None:
    """Build a package-like tree: warmable files in normal dirs AND in
    every skip-dir variant, plus non-warmable files everywhere."""
    pkg = root / "fakepkg"
    normal = pkg / "sub" / "deep"
    normal.mkdir(parents=True)
    # Warmable + non-warmable in a normal subtree.
    (normal / "core.pyc").write_bytes(b"x")
    (normal / "lib.so").write_bytes(b"x")
    (normal / "meta.json").write_bytes(b"x")
    (normal / "readme.txt").write_bytes(b"x")
    (normal / "source.py").write_text("x")
    # Warmable files inside every skip-dir variant — must NOT be warmed.
    for skip in ("tests", "test", "docs", "__pycache__"):
        d = pkg / "sub" / skip
        d.mkdir(parents=True)
        (d / f"in_{skip}.pyc").write_bytes(b"x")
    for info in ("fakepkg-1.0.dist-info", "fakepkg-1.0.egg-info"):
        d = pkg / info
        d.mkdir(parents=True)
        (d / "METADATA.json").write_bytes(b"x")
    (pkg / "top.pyc").write_bytes(b"x")


def _run_production_warm(pkg_root: Path, monkeypatch) -> set[Path]:
    """Drive ``_warm_package_files`` against ``pkg_root`` with a
    recording ``_warm_file`` stub; return the warmed path set."""
    warmed: set[Path] = set()
    monkeypatch.setattr(cache_probe, "_warm_file", lambda p: warmed.add(p) or 1)

    fake_spec = ModuleSpec(name="fakepkg", loader=None, is_package=True)
    fake_spec.submodule_search_locations = [str(pkg_root)]
    real_find_spec = cache_probe.importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "fakepkg":
            return fake_spec
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cache_probe.importlib.util, "find_spec", fake_find_spec)
    total = cache_probe._warm_package_files("fakepkg")
    assert total == len(warmed), "returned byte total must match the warmed file count"
    return warmed


class TestWarmPackageFilesWiring:
    def test_warm_package_files_routes_through_iter_warmable_files(self):
        """Source pin: the production loop must reference
        ``_iter_warmable_files`` and must NOT contain the old rglob
        pattern — the wiring cannot silently regress again."""
        src = inspect.getsource(cache_probe._warm_package_files)
        assert "_iter_warmable_files(" in src, (
            "wiring regression: _warm_package_files no longer calls "
            "_iter_warmable_files — production is back on the slow "
            "rglob path (or never calls the optimized walker)"
        )
        assert ".rglob(" not in src, (
            "wiring regression: _warm_package_files still uses an rglob walk instead of _iter_warmable_files"
        )


class TestWarmedFileSetParity:
    def test_walker_set_equals_old_pattern_set(self, tmp_path, monkeypatch):
        """The warmed-file SET must be identical between the old
        rglob+consumer-filter pattern and the new walker-driven
        production loop — behavior-preserving wiring."""
        _build_mixed_tree(tmp_path)
        pkg_root = tmp_path / "fakepkg"

        old_set = _old_pattern_warmable_files(pkg_root)
        new_set = _run_production_warm(pkg_root, monkeypatch)

        assert new_set == old_set, (
            f"warmed-file set drifted:\n"
            f"  only-old (no longer warmed): {sorted(old_set - new_set)}\n"
            f"  only-new (newly warmed):     {sorted(new_set - old_set)}"
        )
        # Sanity: the tree actually exercises the interesting cases.
        assert old_set, "tree must contain warmable files"
        assert any("__pycache__" not in str(p) and "tests" not in str(p) for p in old_set)

    def test_skip_dirs_pruned_inside_walker(self, tmp_path):
        """``_iter_warmable_files`` itself must prune skip dirs — files
        under ``tests/`` / ``docs/`` / ``__pycache__`` /
        ``*.dist-info``/``*.egg-info`` never reach the consumer."""
        pkg = tmp_path / "pkg"
        keep = pkg / "keep"
        keep.mkdir(parents=True)
        (keep / "good.pyc").write_bytes(b"x")
        (pkg / "tests").mkdir()
        (pkg / "tests" / "bad.pyc").write_bytes(b"x")
        (pkg / "docs").mkdir()
        (pkg / "docs" / "bad.json").write_bytes(b"x")
        (pkg / "__pycache__").mkdir()
        (pkg / "__pycache__" / "bad.pyc").write_bytes(b"x")
        (pkg / "pkg-1.dist-info").mkdir()
        (pkg / "pkg-1.dist-info" / "bad.txt").write_bytes(b"x")
        (pkg / "pkg-1.egg-info").mkdir()
        (pkg / "pkg-1.egg-info" / "bad.json").write_bytes(b"x")
        # A skip-dir NESTED under a normal dir is pruned too.
        nested = pkg / "keep" / "tests"
        nested.mkdir()
        (nested / "bad.pyc").write_bytes(b"x")

        files = sorted(cache_probe._iter_warmable_files(pkg))

        assert files == [keep / "good.pyc"], f"walker yielded non-warmable-pruned files: {files}"

    def test_hidden_dotfile_is_not_warmed_same_as_old_pattern(self, tmp_path):
        """A hidden file named exactly ``.json`` has NO ``Path.suffix``
        — the old pattern skipped it, the walker must too (the walker's
        suffix test intentionally keeps ``Path.suffix`` semantics rather
        than a raw ``endswith``)."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / ".json").write_bytes(b"x")
        (pkg / ".pyc").write_bytes(b"x")
        (pkg / "real.json").write_bytes(b"x")

        files = sorted(cache_probe._iter_warmable_files(pkg))
        old = _old_pattern_warmable_files(pkg)

        assert set(files) == old, f"hidden-dotfile handling drifted: walker={files} old={old}"
        assert files == [pkg / "real.json"]

    def test_root_named_like_skip_dir_is_not_pruned(self, tmp_path):
        """The old consumer filter checked ``rel.parts[:-1]`` — parts
        BELOW the root — so a root literally named ``tests`` was NOT
        skipped. The walker must preserve that (root is never pruned)."""
        root = tmp_path / "tests"
        root.mkdir()
        (root / "keep.pyc").write_bytes(b"x")

        files = list(cache_probe._iter_warmable_files(root))
        old = _old_pattern_warmable_files(root)

        assert set(files) == old == {root / "keep.pyc"}, (
            f"root named like a skip dir was wrongly pruned: walker={files} old={old}"
        )


def test_stat_count_test_file_still_pins_the_walker():
    """Meta-pin: the original stat-count regression test file must keep
    existing (it guards the walker's no-per-file-stat contract that this
    wiring now actually exercises in production)."""
    tests_dir = Path(__file__).parent
    assert (tests_dir / "test_cache_probe_stat_count.py").exists()
