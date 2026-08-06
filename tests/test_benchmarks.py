"""Tests for pytest-benchmark benchmarks.

TEST-012: Basic benchmarks for text_cleanup performance, audio RMS computation,
and config load/parse.
"""

from __future__ import annotations

import importlib.util
import os
import warnings

import numpy as np
import pytest

# pytest-benchmark exposes a ``benchmark`` *fixture* (not a
# ``pytest.benchmark`` attribute), so ``hasattr(pytest, "benchmark")``
# is always ``False`` and the previous ``skipif`` guard silently
# skipped the whole module even when the plugin was installed.
# ``importorskip`` is the canonical idiom — it skips the whole module
# only if ``pytest_benchmark`` is genuinely missing.
pytest.importorskip("pytest_benchmark")

# pytest-benchmark is unreliable under ``pytest-xdist`` when
# there are >= 2 workers — the plugin emits
# ``PytestBenchmarkWarning: Benchmarks are automatically disabled
# because xdist plugin is active`` and the benchmarks become no-op
# (the per-worker subprocess can't acquire the shared benchmark
# storage lock, and the resulting timings are meaningless — workers
# compete for CPU). Detect xdist via the ``PYTEST_XDIST_WORKER`` env
# var (set by xdist on every worker process; absent on the
# controller when ``-n 0``). Skip the whole module in that case —
# the benchmarks still run in the default single-process invocation
# (``-n auto`` with 1 worker on a 1-CPU box sets
# ``PYTEST_XDIST_WORKER`` too, but that's fine: a single xdist
# worker still hits the storage-lock warning path).
#
# NOTE: ``hasattr(pytest, "xdist")`` is ALWAYS ``False`` (xdist
# doesn't set a ``pytest.xdist`` attribute — it registers as a
# plugin via entry points). The reliable detection is
# ``importlib.util.find_spec("xdist")`` (is xdist installed?) +
# ``os.environ.get("PYTEST_XDIST_WORKER")`` (are we inside a
# worker?). Both must be true: xdist installed + worker env var set
# → we're inside an xdist worker process.
_has_xdist_installed = importlib.util.find_spec("xdist") is not None
_has_xdist_workers = _has_xdist_installed and bool(
    os.environ.get("PYTEST_XDIST_WORKER")
)
pytestmark = [
    pytest.mark.skipif(
        _has_xdist_workers,
        reason=(
            "pytest-benchmark is unreliable under pytest-xdist workers "
            "(emits PytestBenchmarkWarning + no-op timing); run without -n "
            "or with -n 0 for real benchmark numbers"
        ),
    ),
    # Belt-and-suspenders: also filter any PytestBenchmarkWarning that
    # leaks through the plugin's own pytest_configure path so the test
    # output stays clean. The plugin emits the warning once per worker
    # process during pytest_configure (before this module is imported),
    # so this filter only catches warnings emitted DURING test
    # execution — but that's the only layer we can control from a test
    # module without touching conftest.py / pyproject.toml.
    pytest.mark.filterwarnings(
        "ignore::pytest_benchmark.logger.PytestBenchmarkWarning"
    ),
]

# Proactively install a process-wide warning filter for
# PytestBenchmarkWarning so that IF this module is imported before the
# plugin's warning fires (e.g. when run without xdist, or when collected
# in the controller process), the filter is already in place. Under
# xdist, each worker fires the warning during its own pytest_configure
# (before this module is imported in the worker), so this filter is
# belt-and-suspenders only — the authoritative suppression for the
# under-xdist case is the ``skipif`` above (the tests don't run, so the
# benchmark fixture is never invoked and no per-test warning is
# emitted).
try:
    from pytest_benchmark.logger import PytestBenchmarkWarning as _PBW  # noqa: N814
except ImportError:  # pragma: no cover — pytest_benchmark is importorskip'd
    _PBW = None  # type: ignore[assignment]
if _PBW is not None:
    warnings.filterwarnings(
        "ignore",
        category=_PBW,
        message=".*automatically disabled because xdist plugin is active.*",
    )

from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections  # noqa: E402


@pytest.fixture(autouse=True)
def _configure_corrections():
    """Initialize corrections before each benchmark."""
    configure_corrections()


class TestTextCleanupBenchmarks:
    """Benchmarks for text cleanup performance."""

    def test_benchmark_short_text(self, benchmark):
        """Benchmark cleanup on a short transcription (~10 words)."""
        text = "hello world this is a test of the text cleanup system"
        benchmark(clean_transcribed_text, text)

    def test_benchmark_medium_text(self, benchmark):
        """Benchmark cleanup on a medium transcription (~50 words)."""
        text = (
            "Right now the application is working successfully I just restart "
            "the device and it works successfully automatically I didn't have "
            "to start it from scratch or with any commands it just started "
            "itself with startup after I looked in and I tried it looks like "
            "they working successfully but I haven't tested it fully yet"
        )
        benchmark(clean_transcribed_text, text)

    def test_benchmark_long_text(self, benchmark):
        """Benchmark cleanup on a long transcription (~200 words)."""
        text = " ".join(["hello world this is a test"] * 50)
        benchmark(clean_transcribed_text, text)

    def test_benchmark_misspelling_correction(self, benchmark):
        """Benchmark cleanup with misspelling corrections."""
        text = "infestigate the goverment developement"
        benchmark(clean_transcribed_text, text)


class TestAudioRMSBenchmarks:
    """Benchmarks for audio RMS computation."""

    def test_benchmark_rms_short(self, benchmark):
        """Benchmark RMS on a short audio buffer (~0.5s at 16kHz)."""
        audio = np.random.randn(8000).astype(np.float32) * 0.01

        def compute_rms(a):
            return float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))

        benchmark(compute_rms, audio)

    def test_benchmark_rms_long(self, benchmark):
        """Benchmark RMS on a long audio buffer (~10s at 16kHz)."""
        audio = np.random.randn(160000).astype(np.float32) * 0.01

        def compute_rms(a):
            return float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))

        benchmark(compute_rms, audio)


class TestConfigLoadBenchmarks:
    """Benchmarks for config load/parse."""

    def test_benchmark_config_parse(self, benchmark, tmp_path, monkeypatch):
        """Benchmark parsing a config file from disk."""
        from voice_typer.server.config import Config

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        c.save()

        benchmark(Config.load)
