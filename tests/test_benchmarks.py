"""Tests for pytest-benchmark benchmarks."""

from __future__ import annotations

import importlib.util
import os
import warnings

import numpy as np
import pytest

pytest.importorskip("pytest_benchmark")

_has_xdist_installed = importlib.util.find_spec("xdist") is not None
_has_xdist_workers = _has_xdist_installed and bool(os.environ.get("PYTEST_XDIST_WORKER"))
pytestmark = [
    pytest.mark.skipif(
        _has_xdist_workers,
        reason=(
            "pytest-benchmark is unreliable under pytest-xdist workers "
            "(emits PytestBenchmarkWarning + no-op timing); run without -n "
            "or with -n 0 for real benchmark numbers"
        ),
    ),
    pytest.mark.filterwarnings("ignore::pytest_benchmark.logger.PytestBenchmarkWarning"),
]

try:
    from pytest_benchmark.logger import PytestBenchmarkWarning as _PBW  # noqa: N814
except ImportError:  # pragma: no cover, pytest_benchmark is importorskip'd
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

    def test_benchmark_config_parse(self, benchmark, tmp_config_dir):
        """Benchmark parsing a config file from disk."""
        from voice_typer.server.config import Config

        c = Config()
        c.save()

        benchmark(Config.load)
