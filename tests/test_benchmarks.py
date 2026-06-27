"""Tests for pytest-benchmark benchmarks.

TEST-012: Basic benchmarks for text_cleanup performance, audio RMS computation,
and config load/parse.
"""

from __future__ import annotations

import json
import pytest
import numpy as np

from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections


@pytest.fixture(autouse=True)
def _configure_corrections():
    """Initialize corrections before each benchmark."""
    configure_corrections()


# Only run benchmarks if pytest-benchmark is installed
pytestmark = pytest.mark.skipif(
    not hasattr(pytest, "benchmark"),
    reason="pytest-benchmark not installed — install with: pip install pytest-benchmark",
)


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
