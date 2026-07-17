"""RW-9 regression tests for the ``AudioQualityController`` extraction.

The three audio-quality methods (``_on_audio_quality_chunk``,
``_rebuild_audio_processor``, ``_finalize_audio_quality_report``) were
extracted from ``VoiceTyperApp`` to
``voice_typer/server/audio_quality_controller.py``. ``VoiceTyperApp``
keeps thin delegate methods so the ``AudioProcessor`` quality-callback
wiring in ``__init__``, ``service.apply_config_side_effects``, and
``RecordingController.stop()`` keep calling ``app._on_audio_quality_chunk``
/ ``app._rebuild_audio_processor`` / ``app._finalize_audio_quality_report``
unchanged.

These tests pin the contract of the extraction without requiring a real
``VoiceTyperApp`` instance (which would need the ``self.audio_quality``
wiring the primary agent adds separately). Mirrors the
``RecordingController.__new__`` + MagicMock ``_app`` pattern used in
``tests/test_recording_and_audio.py``.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.audio_quality_controller import AudioQualityController


def _make_controller() -> tuple[AudioQualityController, MagicMock]:
    """Build an AudioQualityController with a MagicMock app + real analyzer.

    Returns ``(controller, app)`` so individual tests can tweak app
    state before invoking a controller method.
    """
    app = MagicMock()
    app._audio_quality = AudioQualityAnalyzer()
    app._audio_quality.reset()
    app._audio_processor = MagicMock()
    app._audio_processor.filter_names = ["highpass", "gate"]
    app.config = MagicMock()
    app.config.audio_quality_warnings = False
    app.recorder = MagicMock()
    # on_config_changed defaults to a callable MagicMock.

    ctrl = AudioQualityController.__new__(AudioQualityController)
    ctrl._app = app
    return ctrl, app


# ── Existence + back-reference ─────────────────────────────────────────


class TestAudioQualityControllerWiring:
    def test_methods_exist_and_are_callable(self):
        ctrl, _ = _make_controller()
        for name in (
            "_on_audio_quality_chunk",
            "_rebuild_audio_processor",
            "_finalize_audio_quality_report",
        ):
            assert hasattr(ctrl, name), f"AudioQualityController must expose {name}"
            assert callable(getattr(ctrl, name)), f"{name} must be callable"

    def test_init_stores_back_reference(self):
        app = MagicMock()
        ctrl = AudioQualityController(app)
        assert ctrl._app is app, "AudioQualityController must store the app back-reference as self._app"


# ── _on_audio_quality_chunk ────────────────────────────────────────────


class TestOnAudioQualityChunk:
    """Per-chunk callback runs in the PortAudio thread — MUST be non-blocking."""

    def test_updates_accumulators_without_io(self, caplog):
        ctrl, app = _make_controller()
        aq = app._audio_quality
        assert aq._chunk_count == 0
        assert aq._peak == 0.0
        assert aq._clip_count == 0

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_quality_controller"):
            ctrl._on_audio_quality_chunk(rms=0.05, peak=0.3)

        assert aq._chunk_count == 1
        assert aq._peak == pytest.approx(0.3)
        assert aq._clip_count == 0
        # No per-chunk logging on the happy path (non-blocking contract).
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records == [], (
            "_on_audio_quality_chunk must not log on the happy path (PortAudio audio-callback thread is non-blocking)"
        )

    def test_increments_clip_count_at_clipping_threshold(self):
        ctrl, app = _make_controller()
        aq = app._audio_quality
        # CLIPPING_THRESHOLD is 0.99 per AudioQualityAnalyzer.
        ctrl._on_audio_quality_chunk(rms=0.5, peak=0.99)
        assert aq._clip_count == 1
        assert aq._peak == pytest.approx(0.99)

    def test_peak_only_grows_never_shrinks(self):
        ctrl, app = _make_controller()
        aq = app._audio_quality
        ctrl._on_audio_quality_chunk(rms=0.1, peak=0.9)
        ctrl._on_audio_quality_chunk(rms=0.1, peak=0.1)
        assert aq._peak == pytest.approx(0.9), "peak must be max-seen, not last-seen"

    def test_non_blocking_runs_fast_for_many_chunks(self):
        """The per-chunk callback must complete in bounded time — no I/O,
        no allocation of large structures. 10k calls must finish well under
        1 second (true even on a slow CI box); we assert <2s for headroom."""
        ctrl, app = _make_controller()
        start = time.perf_counter()
        for _ in range(10_000):
            ctrl._on_audio_quality_chunk(rms=0.05, peak=0.3)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"_on_audio_quality_chunk must be non-blocking; 10k calls took {elapsed:.3f}s"

    def test_swallows_exceptions_from_analyzer(self, caplog):
        """If ``app._audio_quality`` is missing or raises, the callback must
        swallow the error — quality analysis must NEVER break the audio
        callback."""
        ctrl, app = _make_controller()
        # Remove the analyzer entirely → AttributeError inside the try/except.
        del app._audio_quality
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_quality_controller"):
            # Must not raise.
            ctrl._on_audio_quality_chunk(rms=0.05, peak=0.3)
        # The failure is logged at debug level (no per-chunk noise on the
        # happy path, but errors are observable for diagnostics).
        debug_msgs = [r.getMessage() for r in caplog.records]
        assert any("per-chunk update failed" in m for m in debug_msgs), (
            "Analyzer failures must be logged at debug level for diagnostics"
        )


# ── _rebuild_audio_processor ───────────────────────────────────────────


class TestRebuildAudioProcessor:
    def test_calls_rebuild_from_config_with_app_config(self):
        ctrl, app = _make_controller()
        ctrl._rebuild_audio_processor()
        app._audio_processor.rebuild_from_config.assert_called_once_with(app.config)

    def test_calls_recorder_on_config_changed(self):
        """PERF-02 (R8): recorder's ``_vad_enabled`` cache is refreshed
        immediately after a chain rebuild so the next chunk sees the new
        VAD config without waiting for the 5s TTL."""
        ctrl, app = _make_controller()
        ctrl._rebuild_audio_processor()
        app.recorder.on_config_changed.assert_called_once()

    def test_skips_on_config_changed_when_recorder_lacks_it(self):
        """Some recorders (mocks in tests, or stubs) may not expose
        ``on_config_changed`` — the rebuild must skip the refresh
        gracefully via the ``getattr(... None)`` + ``callable`` guard."""
        ctrl, app = _make_controller()
        # Replace recorder with a plain object lacking on_config_changed.
        app.recorder = object()
        # Must not raise.
        ctrl._rebuild_audio_processor()
        app._audio_processor.rebuild_from_config.assert_called_once_with(app.config)

    def test_logs_filter_names_after_rebuild(self, caplog):
        ctrl, app = _make_controller()
        with caplog.at_level(logging.INFO, logger="voice_typer.server.audio_quality_controller"):
            ctrl._rebuild_audio_processor()
        info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("Audio processor rebuilt" in m for m in info_msgs), (
            "Successful rebuild must log at info level with the new filter_names"
        )
        assert any("highpass" in m and "gate" in m for m in info_msgs), (
            "Log must include the post-rebuild filter_names for diagnostics"
        )

    def test_swallows_exceptions_from_rebuild(self, caplog):
        """If ``rebuild_from_config`` raises, the controller must not
        propagate — service.apply_config_side_effects calls this in a
        try/except already, but the controller must be self-contained."""
        ctrl, app = _make_controller()
        app._audio_processor.rebuild_from_config.side_effect = RuntimeError("chain boom")
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.audio_quality_controller"):
            # Must not raise.
            ctrl._rebuild_audio_processor()
        err_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("Failed to rebuild audio processor" in m for m in err_msgs), (
            "Rebuild failures must be logged via log.exception for diagnostics"
        )


# ── _finalize_audio_quality_report ─────────────────────────────────────


class TestFinalizeAudioQualityReport:
    def test_short_circuits_when_warnings_disabled(self):
        """``audio_quality_warnings=False`` (the default) means we skip
        the analysis entirely for efficiency — no ``analyze_full_audio``
        call, no tray notification, no reset."""
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = False
        audio = np.ones(16000, dtype=np.float32)

        ctrl._finalize_audio_quality_report(audio)

        # analyze_full_audio lives on the analyzer (a real
        # AudioQualityAnalyzer here) — spy on it via a wrapper.
        # Easier: replace the analyzer with a MagicMock and assert no
        # analyze_full_audio call.
        app._audio_quality = MagicMock()
        ctrl._finalize_audio_quality_report(audio)
        app._audio_quality.analyze_full_audio.assert_not_called()
        app._audio_quality.reset.assert_not_called()
        # And tray.notify must NEVER be called (FIX-HOTKEY-AND-NOTIFICATION).
        app.tray.notify.assert_not_called()

    def test_runs_analysis_when_warnings_enabled(self):
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = True
        # Replace the analyzer with a MagicMock so we can assert the
        # analysis was invoked.
        mock_aq = MagicMock()
        mock_aq.analyze_full_audio.return_value.has_issues = False
        app._audio_quality = mock_aq

        audio = np.ones(16000, dtype=np.float32)
        ctrl._finalize_audio_quality_report(audio)

        mock_aq.analyze_full_audio.assert_called_once_with(audio)
        # Reset for the next session.
        mock_aq.reset.assert_called_once()

    def test_logs_summary_when_issues_detected(self, caplog):
        """When ``audio_quality_warnings=True`` AND the report has issues,
        the summary is logged at info level (but NOT surfaced via tray)."""
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = True
        mock_aq = MagicMock()
        report = MagicMock()
        report.has_issues = True
        report.get_summary.return_value = "Low volume (RMS=0.001). Increase mic gain."
        mock_aq.analyze_full_audio.return_value = report
        app._audio_quality = mock_aq

        with caplog.at_level(logging.INFO, logger="voice_typer.server.audio_quality_controller"):
            ctrl._finalize_audio_quality_report(np.ones(16000, dtype=np.float32))

        info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("Issues detected" in m and "Low volume" in m for m in info_msgs), (
            "When has_issues=True the summary must be logged at info level"
        )
        # Reset for next session even when issues were detected.
        mock_aq.reset.assert_called_once()

    def test_never_calls_tray_notify_even_with_issues(self):
        """FIX-HOTKEY-AND-NOTIFICATION: the tray notification that used
        to fire here was deemed annoying. Even when
        ``audio_quality_warnings=True`` AND issues are detected, we
        MUST NOT call ``app.tray.notify``. Analysis runs for internal
        logging only."""
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = True
        mock_aq = MagicMock()
        report = MagicMock()
        report.has_issues = True
        report.get_summary.return_value = "Clipping detected"
        mock_aq.analyze_full_audio.return_value = report
        app._audio_quality = mock_aq

        ctrl._finalize_audio_quality_report(np.ones(16000, dtype=np.float32))

        app.tray.notify.assert_not_called(), ("tray.notify must NEVER be called from _finalize_audio_quality_report")

    def test_swallows_exceptions_from_analyzer(self, caplog):
        """If ``analyze_full_audio`` raises, the controller must not
        propagate — RecordingController.stop() calls this in a
        try/except, but the controller must be self-contained."""
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = True
        mock_aq = MagicMock()
        mock_aq.analyze_full_audio.side_effect = RuntimeError("numpy boom")
        app._audio_quality = mock_aq

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_quality_controller"):
            # Must not raise.
            ctrl._finalize_audio_quality_report(np.ones(16, dtype=np.float32))
        debug_msgs = [r.getMessage() for r in caplog.records]
        assert any("finalize report failed" in m for m in debug_msgs), (
            "Analyzer failures during finalize must be logged at debug level"
        )

    def test_reset_not_called_when_analyze_raises(self):
        """If analyze_full_audio raises, reset() must NOT be called
        (matches the original try/except scope: reset is inside the
        try block, after the analyze call)."""
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = True
        mock_aq = MagicMock()
        mock_aq.analyze_full_audio.side_effect = RuntimeError("boom")
        app._audio_quality = mock_aq

        ctrl._finalize_audio_quality_report(np.ones(16, dtype=np.float32))

        mock_aq.reset.assert_not_called()
