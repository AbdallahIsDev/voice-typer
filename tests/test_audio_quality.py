"""Tests for voice_typer.audio_quality — AudioQualityAnalyzer and AudioQualityReport."""

import numpy as np
import pytest


@pytest.fixture
def analyzer():
    from voice_typer.server.audio_quality import AudioQualityAnalyzer
    return AudioQualityAnalyzer()


class TestAudioQualityReport:
    def test_report_defaults(self):
        from voice_typer.server.audio_quality import AudioQualityReport
        report = AudioQualityReport()
        assert report.clipping_detected is False
        assert report.low_volume_detected is False
        assert report.high_noise_detected is False
        assert report.has_issues is False

    def test_report_has_issues(self):
        from voice_typer.server.audio_quality import AudioQualityReport
        report = AudioQualityReport(clipping_detected=True)
        assert report.has_issues is True

    def test_report_summary_no_issues(self):
        from voice_typer.server.audio_quality import AudioQualityReport
        report = AudioQualityReport()
        assert "OK" in report.get_summary()

    def test_report_summary_with_clipping(self):
        from voice_typer.server.audio_quality import AudioQualityReport
        report = AudioQualityReport(clipping_detected=True, clipping_count=5, clipping_peak=0.99)
        summary = report.get_summary()
        assert "Clipping" in summary

    def test_report_summary_with_low_volume(self):
        from voice_typer.server.audio_quality import AudioQualityReport
        report = AudioQualityReport(low_volume_detected=True, low_volume_rms=0.002)
        summary = report.get_summary()
        assert "Low volume" in summary

    def test_report_summary_with_noise(self):
        from voice_typer.server.audio_quality import AudioQualityReport
        report = AudioQualityReport(high_noise_detected=True, noise_ratio=0.8)
        summary = report.get_summary()
        assert "noise" in summary.lower()


class TestAudioQualityAnalyzerReset:
    def test_reset_clears_state(self, analyzer):
        analyzer._clip_count = 5
        analyzer._peak = 0.99
        analyzer.reset()
        assert analyzer._clip_count == 0
        assert analyzer._peak == 0.0


class TestAudioQualityAnalyzerAnalyzeChunk:
    def test_normal_chunk(self, analyzer):
        chunk = np.random.randn(1600).astype(np.float32) * 0.1
        result = analyzer.analyze_chunk(chunk)
        assert result is None  # No warning for normal audio

    def test_clipping_chunk(self, analyzer):
        chunk = np.ones(1600, dtype=np.float32)  # All at max
        analyzer.reset()
        for _ in range(51):  # Need 50+ chunks to trigger warning
            analyzer.analyze_chunk(chunk)
        # After 50 chunks, should get a warning
        assert analyzer._clip_count > 0

    def test_analyze_chunk_updates_peak(self, analyzer):
        chunk = np.array([0.5], dtype=np.float32)
        analyzer.analyze_chunk(chunk)
        assert analyzer._peak >= 0.5


class TestAudioQualityAnalyzerAnalyzeFullAudio:
    def test_normal_audio(self, analyzer):
        audio = np.random.randn(16000).astype(np.float32) * 0.1
        report = analyzer.analyze_full_audio(audio)
        assert isinstance(report.clipping_detected, bool)

    def test_empty_audio(self, analyzer):
        audio = np.array([], dtype=np.float32)
        report = analyzer.analyze_full_audio(audio)
        assert len(report.warnings) > 0

    def test_clipping_audio(self, analyzer):
        audio = np.ones(16000, dtype=np.float32)
        report = analyzer.analyze_full_audio(audio)
        assert report.clipping_detected is True

    def test_low_volume_audio(self, analyzer):
        audio = np.random.randn(16000).astype(np.float32) * 0.001
        report = analyzer.analyze_full_audio(audio)
        assert report.low_volume_detected is True


class TestAudioQualityAnalyzerProperties:
    def test_clip_count_property(self, analyzer):
        assert analyzer.clip_count == 0

    def test_peak_property(self, analyzer):
        assert analyzer.peak == 0.0
