"""regression tests for the ``AudioQualityController`` extraction.

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
        """recorder's ``_vad_enabled`` cache is refreshed
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
        call, no tray notification.

        ER-44: ``reset()`` IS still called in the ``finally:`` block so
        per-chunk accumulator state (clip_count, peak, rms_ema,
        low_volume_chunks) doesn't leak across recording sessions when
        warnings are disabled. The early-return path used to skip reset,
        which carried the previous session's clipping/low-volume stats
        into the next session's report.
        """
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
        # reset() MUST be called even on the early-return path so
        # accumulator state doesn't leak across sessions.
        app._audio_quality.reset.assert_called_once()
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
        """the tray notification that used
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

        app.tray.notify.assert_not_called()
        # tray.notify must NEVER be called from _finalize_audio_quality_report

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
        """If analyze_full_audio raises, reset() MUST still be called
        (reset is in the ``finally:`` block so it ALWAYS runs,
        even on exception — preventing state leakage across sessions
        when the analyzer crashes)."""
        ctrl, app = _make_controller()
        app.config.audio_quality_warnings = True
        mock_aq = MagicMock()
        mock_aq.analyze_full_audio.side_effect = RuntimeError("boom")
        app._audio_quality = mock_aq

        ctrl._finalize_audio_quality_report(np.ones(16, dtype=np.float32))

        # reset() is in finally: so it runs even on exception.
        mock_aq.reset.assert_called_once()


# live RMS EMA wiring ──────────────────────────────────────


class TestOnAudioQualityChunkRmsEma:
    """the per-chunk ``rms`` value passed to
    ``_on_audio_quality_chunk`` must be fed into the analyzer's
    ``update_live_rms`` EMA accumulator. When the EMA stays below
    ``LOW_VOLUME_THRESHOLD`` for ``LOW_VOLUME_SUSTAINED_CHUNKS``
    consecutive chunks, a single WARNING is logged."""

    def test_live_rms_ema_advances_per_chunk(self):
        """each call to _on_audio_quality_chunk must advance
        the analyzer's ``_rms_ema`` by the EMA formula."""
        ctrl, app = _make_controller()
        aq = app._audio_quality
        assert aq.rms_ema == 0.0

        ctrl._on_audio_quality_chunk(rms=0.1, peak=0.3)
        # After 1 chunk: 0.05 * 0.1 + 0.95 * 0.0 = 0.005
        assert aq.rms_ema == pytest.approx(0.005, rel=1e-6)

        ctrl._on_audio_quality_chunk(rms=0.1, peak=0.3)
        # After 2 chunks: 0.05 * 0.1 + 0.95 * 0.005 = 0.00975
        assert aq.rms_ema == pytest.approx(0.00975, rel=1e-6)

    def test_normal_rms_does_not_log_warning(self, caplog):
        """normal RMS (above LOW_VOLUME_THRESHOLD) must NOT
        log a low-volume warning, even after many chunks."""
        ctrl, app = _make_controller()
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.audio_quality_controller"):
            for _ in range(100):
                ctrl._on_audio_quality_chunk(rms=0.05, peak=0.3)
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert warn_msgs == [], f"Normal RMS must not fire low-volume warning, got: {warn_msgs}"

    def test_sustained_low_rms_logs_single_warning(self, caplog):
        """sustained low RMS for LOW_VOLUME_SUSTAINED_CHUNKS
        consecutive chunks logs exactly ONE WARNING containing
        'low input level — increase mic gain'."""
        ctrl, app = _make_controller()
        aq = app._audio_quality
        aq.LOW_VOLUME_SUSTAINED_CHUNKS = 5  # speed up test
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.audio_quality_controller"):
            for _ in range(10):
                ctrl._on_audio_quality_chunk(rms=0.001, peak=0.001)
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) == 1, f"Expected 1 latched warning, got {len(warn_records)}"
        msg = warn_records[0].getMessage()
        assert "low input level" in msg and "increase mic gain" in msg, (
            f"Warning must say 'low input level — increase mic gain', got: {msg}"
        )
        assert "rms_ema=" in msg, f"Warning must include rms_ema diagnostic: {msg}"

    def test_warning_resets_on_recovery(self, caplog):
        """after EMA recovers above threshold, a future
        low-volume episode must fire the warning again."""
        ctrl, app = _make_controller()
        aq = app._audio_quality
        aq.LOW_VOLUME_SUSTAINED_CHUNKS = 3
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.audio_quality_controller"):
            for _ in range(5):
                ctrl._on_audio_quality_chunk(rms=0.001, peak=0.001)
            # Use 1 chunk of recovery (not 20) so EMA ends at ~0.025
            # (alpha=0.05, rms=0.5 → ema = 0.05*0.5 = 0.025). With 20
            # chunks, EMA would converge to ~0.32 and the second
            # low-volume episode (5 chunks of 0.001) wouldn't be enough
            # to bring EMA back below 0.005 — the warning wouldn't fire.
            ctrl._on_audio_quality_chunk(rms=0.5, peak=0.5)
            for _ in range(50):
                ctrl._on_audio_quality_chunk(rms=0.001, peak=0.001)
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) == 2, f"Expected 2 warnings (one per episode), got {len(warn_records)}"

    def test_rms_ema_update_does_not_block_callback(self):
        """the EMA update must remain non-blocking — 10k calls
        must complete well under 2s."""
        ctrl, app = _make_controller()
        start = time.perf_counter()
        for _ in range(10_000):
            ctrl._on_audio_quality_chunk(rms=0.05, peak=0.3)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"EMA update must not block the audio callback; 10k calls took {elapsed:.3f}s"


# + : force_sr parameter ────────────────────────────


class TestRebuildAudioProcessorForceSr:
    """``_rebuild_audio_processor``
    accepts an optional ``force_sr`` parameter that rebuilds the chain
    at a specific sample rate before applying config changes."""

    def test_force_sr_calls_set_sample_rate(self):
        """when force_sr is provided, the controller calls
        ``set_sample_rate`` BEFORE the config-driven rebuild."""
        ctrl, app = _make_controller()
        ctrl._rebuild_audio_processor(force_sr=48000)
        app._audio_processor.set_sample_rate.assert_called_once_with(48000)
        app._audio_processor.rebuild_from_config.assert_called_once_with(app.config)

    def test_force_sr_none_does_not_call_set_sample_rate(self):
        """when force_sr is None (the default), set_sample_rate
        must NOT be called — preserves backward compatibility."""
        ctrl, app = _make_controller()
        ctrl._rebuild_audio_processor()
        app._audio_processor.set_sample_rate.assert_not_called()
        app._audio_processor.rebuild_from_config.assert_called_once_with(app.config)

    def test_force_sr_with_real_processor_updates_rate(self):
        """AUDIO-6 + AUDIO-9: end-to-end — force_sr with a REAL
        AudioProcessor rebuilds the chain at the new rate."""
        from voice_typer.server.audio_processor import AudioProcessor

        ctrl, app = _make_controller()
        cfg = type("C", (), {})()
        cfg.audio_preset = "custom"
        cfg.noise_filter_highpass = True
        cfg.noise_filter_highpass_cutoff_hz = 80.0
        cfg.noise_filter_gate = True
        cfg.noise_filter_gate_open_threshold_db = -26.0
        cfg.noise_filter_gate_close_threshold_db = -32.0
        cfg.noise_filter_gate_attack_ms = 25.0
        cfg.noise_filter_gate_hold_ms = 200.0
        cfg.noise_filter_gate_release_ms = 150.0
        cfg.noise_suppression_method = "none"
        cfg.noise_filter_eq = True
        cfg.noise_filter_eq_low_db = -3.0
        cfg.noise_filter_eq_mid_db = 3.0
        cfg.noise_filter_eq_high_db = 2.0
        cfg.noise_filter_compressor = True
        cfg.noise_filter_compressor_threshold_db = -18.0
        cfg.noise_filter_compressor_ratio = 3.0
        cfg.noise_filter_compressor_attack_ms = 6.0
        cfg.noise_filter_compressor_release_ms = 60.0
        cfg.noise_filter_compressor_output_gain_db = 0.0
        cfg.noise_filter_limiter = True
        cfg.noise_filter_limiter_ceiling_db = -6.0
        cfg.noise_filter_limiter_release_ms = 60.0
        cfg.noise_filter_notch = False
        cfg.noise_filter_notch_frequency_hz = 0.0
        cfg.sample_rate = 16000

        app._audio_processor = AudioProcessor(cfg, sample_rate=16000)
        app.config = cfg
        assert app._audio_processor.sample_rate == 16000

        ctrl._rebuild_audio_processor(force_sr=48000)
        assert app._audio_processor.sample_rate == 48000, "force_sr must rebuild the chain at the new rate"

    def test_force_sr_skips_set_sample_rate_when_processor_lacks_it(self, caplog):
        """if the audio processor lacks ``set_sample_rate``,
        the controller must skip gracefully and still run the rebuild."""
        ctrl, app = _make_controller()
        app._audio_processor = MagicMock(spec=["rebuild_from_config", "filter_names"])
        app._audio_processor.filter_names = ["highpass"]
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.audio_quality_controller"):
            ctrl._rebuild_audio_processor(force_sr=48000)
        app._audio_processor.rebuild_from_config.assert_called_once_with(app.config)
