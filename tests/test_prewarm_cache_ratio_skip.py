"""AB-17: prewarm pipeline skips re-warming files already in the OS cache.

The warming loop in ``voice_typer.server.prewarm.pipeline._run_warming_pipeline``
calls ``_pkg._warm_file(f)`` for every model-cache file. AB-17: when a
file is ≥ ``_CACHE_RATIO_PROBE_MIN_BYTES`` (100 MB) AND
``_pkg._cache_ratio(f, samples=10)`` reports ≥
``_CACHE_RATIO_SKIP_WARMING_THRESHOLD`` (0.9), the loop must SKIP the
``_warm_file`` call (the OS standby cache already holds ~all of it —
re-reading a 2.4 GB ``model.safetensors`` that the OS already has
wastes ~5-15 s of disk I/O per re-fire).

These tests pin the AB-17 skip-warming behaviour so a future revert
(removing the cache-ratio check) fails loudly.
"""

from __future__ import annotations

import time
from pathlib import Path

from voice_typer.server import prewarm
from voice_typer.server.prewarm import pipeline as pipeline_mod

# ─── Test fixture: a tiny fake HF cache ─────────────────────────────────


def _make_fake_cache(tmp_path: Path) -> Path:
    """Create a fake HF cache layout with a small ``model.safetensors``.

    The cache layout matches what ``_active_model_cache_dirs()`` expects:
      <tmp>/models--<repo>/snapshots/<hash>/model.safetensors
    """
    model_dir = tmp_path / "models--nvidia--parakeet"
    snap = model_dir / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    # A few KB is plenty — the probe-min-bytes threshold is overridden
    # per-test so the cache-ratio branch fires for our tiny file.
    (snap / "model.safetensors").write_bytes(b"\xab" * 4096)
    return model_dir


def _mock_pipeline_helpers(monkeypatch, model_dir: Path) -> None:
    """Mock the cross-submodule helpers ``_run_warming_pipeline`` looks up."""
    monkeypatch.setattr(prewarm, "_warm_imports", lambda: None)
    monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [model_dir])
    monkeypatch.setattr(prewarm, "_mark_warmed", lambda elapsed: None)


# high cache ratio → skip warming ─────────────────────────────


class TestCacheRatioSkipWarming:
    """AB-17: ``_warm_file`` is NOT called when the cache ratio is ≥ threshold."""

    def test_high_cache_ratio_skips_warm_file(self, monkeypatch, tmp_path):
        """When ``_cache_ratio`` returns ≥ threshold, ``_warm_file`` is NOT called."""
        model_dir = _make_fake_cache(tmp_path)
        _mock_pipeline_helpers(monkeypatch, model_dir)

        # Force the probe-min-bytes threshold down so the tiny test
        # file (4 KB) triggers the cache-ratio branch.
        monkeypatch.setattr(pipeline_mod, "_CACHE_RATIO_PROBE_MIN_BYTES", 1)
        # Cache ratio ≥ threshold → skip warming.
        monkeypatch.setattr(prewarm, "_cache_ratio", lambda path, samples=10: 0.95)

        warm_calls: list[Path] = []
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: warm_calls.append(p) or 0)

        exit_code = pipeline_mod._run_warming_pipeline(min_ram_mb=0, force=True, t_start=time.perf_counter())

        assert exit_code == pipeline_mod.EXIT_OK, f"AB-17: expected EXIT_OK when cache ratio is hot; got {exit_code}"
        assert not warm_calls, (
            f"AB-17: _warm_file must NOT be called when cache ratio ≥ threshold; got calls: {warm_calls!r}"
        )

    def test_low_cache_ratio_still_warms(self, monkeypatch, tmp_path):
        """When ``_cache_ratio`` returns < threshold, ``_warm_file`` IS called.

        Pins the complementary branch of AB-17: the skip must NOT
        prevent warming when the cache is cold.
        """
        model_dir = _make_fake_cache(tmp_path)
        _mock_pipeline_helpers(monkeypatch, model_dir)

        monkeypatch.setattr(pipeline_mod, "_CACHE_RATIO_PROBE_MIN_BYTES", 1)
        # Cache ratio well below threshold → warm normally.
        monkeypatch.setattr(prewarm, "_cache_ratio", lambda path, samples=10: 0.1)

        warm_calls: list[Path] = []
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: warm_calls.append(p) or 0)

        exit_code = pipeline_mod._run_warming_pipeline(min_ram_mb=0, force=True, t_start=time.perf_counter())

        assert exit_code == pipeline_mod.EXIT_OK
        assert warm_calls, "AB-17: _warm_file must be called when cache ratio < threshold"

    def test_small_file_skips_probe_and_warms(self, monkeypatch, tmp_path):
        """Files below ``_CACHE_RATIO_PROBE_MIN_BYTES`` skip the probe entirely.

        The probe itself costs a few random reads — pure overhead for
        small files that ``_warm_file`` would warm in a single ``read()``.
        So small files must always go through ``_warm_file`` regardless
        of what ``_cache_ratio`` would say.
        """
        model_dir = _make_fake_cache(tmp_path)
        _mock_pipeline_helpers(monkeypatch, model_dir)

        # Use the real default threshold (100 MB) — our 4 KB test file
        # is well below it, so the probe must NOT run.
        # Restore default (in case prior test in same session lowered it).
        monkeypatch.setattr(
            pipeline_mod,
            "_CACHE_RATIO_PROBE_MIN_BYTES",
            100 * 1024 * 1024,
        )

        # _cache_ratio should NOT be called at all for small files.
        cache_ratio_calls: list[Path] = []
        monkeypatch.setattr(
            prewarm,
            "_cache_ratio",
            lambda path, samples=10: cache_ratio_calls.append(path) or 0.99,
        )

        warm_calls: list[Path] = []
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: warm_calls.append(p) or 0)

        exit_code = pipeline_mod._run_warming_pipeline(min_ram_mb=0, force=True, t_start=time.perf_counter())

        assert exit_code == pipeline_mod.EXIT_OK
        assert warm_calls, "AB-17: _warm_file must be called for small files (no probe)"
        assert not cache_ratio_calls, (
            "AB-17: _cache_ratio must NOT be called for files below _CACHE_RATIO_PROBE_MIN_BYTES"
        )

    def test_probe_failure_falls_through_to_warm(self, monkeypatch, tmp_path):
        """If ``_cache_ratio`` raises, ``_warm_file`` is still called.

        ``_cache_ratio`` may raise on some files (locked, perm-denied,
        special files). The AB-17 wrap must NOT propagate the
        exception — fall through to ``_warm_file`` so the file is
        warmed unconditionally.
        """
        model_dir = _make_fake_cache(tmp_path)
        _mock_pipeline_helpers(monkeypatch, model_dir)

        monkeypatch.setattr(pipeline_mod, "_CACHE_RATIO_PROBE_MIN_BYTES", 1)

        def _raising_cache_ratio(path, samples=10):
            raise OSError("simulated probe failure")

        monkeypatch.setattr(prewarm, "_cache_ratio", _raising_cache_ratio)

        warm_calls: list[Path] = []
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: warm_calls.append(p) or 0)

        exit_code = pipeline_mod._run_warming_pipeline(min_ram_mb=0, force=True, t_start=time.perf_counter())

        assert exit_code == pipeline_mod.EXIT_OK, "AB-17: probe failure must not abort the pipeline"
        assert warm_calls, "AB-17: probe failure must fall through to _warm_file"


# constants are defined and exported (regression guard) ──────


class TestCacheRatioConstants:
    """The skip-warming constants must exist and have the spec values."""

    def test_skip_warming_threshold_is_0_9(self):
        """ER-68 spec: skip when cache ratio ≥ 0.9."""
        assert pipeline_mod._CACHE_RATIO_SKIP_WARMING_THRESHOLD == 0.9

    def test_probe_min_bytes_is_100mb(self):
        """ER-68 spec: probe only files ≥ 100 MB."""
        assert pipeline_mod._CACHE_RATIO_PROBE_MIN_BYTES == 100 * 1024 * 1024
