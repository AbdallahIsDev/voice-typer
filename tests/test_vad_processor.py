"""Unit tests for the extracted VadProcessor class.

These tests exercise the state machine, auto-calibration, and VAD-enabled
cache in isolation — without instantiating a full ``Recorder`` (which
pulls in sounddevice, the audio worker thread, the device-health
checker, the scipy preloader, etc.).

The behavior under test was previously covered indirectly via
``tests/test_bugfix_regressions.py::TestVadGreyZonePreservesCounters``
and ``TestVadAutoCalibrationBehavior`` which drove the API through
``Recorder``'s delegation shims. These tests pin the same behavior at
the new ``VadProcessor`` API surface so future refactors of
``Recorder`` can't accidentally regress the VAD layer.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.vad_processor import (
    DEFAULT_VAD_CALIBRATION_DURATION,
    DEFAULT_VAD_HANGOVER_FRAMES,
    DEFAULT_VAD_SILENCE_FRAMES,
    DEFAULT_VAD_SILENCE_THRESHOLD_DB,
    DEFAULT_VAD_SPEECH_FRAMES,
    DEFAULT_VAD_SPEECH_THRESHOLD_DB,
    VadProcessor,
    VadState,
)

# ── Fixtures ───────────────────────────────────────────────────────────


def _config_with_vad_enabled() -> MagicMock:
    """Return a MagicMock config with at least one noise filter on.

    The ``compute_vad_enabled`` gate returns True when ANY noise_filter_*
    flag is True. Tests that exercise the state machine need this so
    ``vad_enabled`` doesn't short-circuit to UNKNOWN.
    """
    cfg = MagicMock()
    cfg.use_silero_vad = False  # force RMS path (no torch in test env)
    cfg.vad_speech_threshold = 0.5
    cfg.vad_silence_threshold = 0.3
    cfg.noise_filter_highpass = True
    cfg.noise_filter_gate = False
    cfg.noise_filter_eq = False
    cfg.noise_filter_compressor = False
    cfg.noise_filter_limiter = False
    cfg.noise_filter_notch = False
    cfg.noise_suppression_method = "none"
    return cfg


def _config_with_vad_disabled() -> MagicMock:
    """Return a MagicMock config matching the 'Off' audio preset."""
    cfg = MagicMock()
    cfg.use_silero_vad = True
    cfg.vad_speech_threshold = 0.5
    cfg.vad_silence_threshold = 0.3
    cfg.noise_filter_highpass = False
    cfg.noise_filter_gate = False
    cfg.noise_filter_eq = False
    cfg.noise_filter_compressor = False
    cfg.noise_filter_limiter = False
    cfg.noise_filter_notch = False
    cfg.noise_suppression_method = "none"
    return cfg


@pytest.fixture(autouse=True)
def _silence_vad_unavailable_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress the Silero-unavailable warning in test output.

    Tests construct VadProcessor with ``use_silero_vad=False`` so the
    lazy ``_check_vad_available`` import is skipped — but the
    auto-fixture keeps things quiet even when a config has
    ``use_silero_vad=True``.
    """
    pass


# ── __init__ / defaults ────────────────────────────────────────────────


class TestVadProcessorInit:
    def test_default_state_is_unknown(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.state == VadState.UNKNOWN

    def test_default_counters_are_zero(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.consecutive_speech_frames == 0
        assert vp.consecutive_silence_frames == 0

    def test_default_thresholds_match_module_constants(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.speech_threshold_db == DEFAULT_VAD_SPEECH_THRESHOLD_DB
        assert vp.silence_threshold_db == DEFAULT_VAD_SILENCE_THRESHOLD_DB

    def test_default_frame_counts_match_module_constants(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.speech_frames == DEFAULT_VAD_SPEECH_FRAMES
        assert vp.silence_frames == DEFAULT_VAD_SILENCE_FRAMES
        assert vp.hangover_frames == DEFAULT_VAD_HANGOVER_FRAMES

    def test_default_calibration_state(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.calibrated is False
        assert vp.calibration_rms_values == []
        assert vp.calibration_duration == DEFAULT_VAD_CALIBRATION_DURATION

    def test_use_silero_vad_read_from_config(self) -> None:
        cfg = _config_with_vad_enabled()
        cfg.use_silero_vad = False
        vp = VadProcessor(cfg)
        assert vp.use_silero_vad is False
        # When use_silero_vad is False, the lazy torch import is skipped
        # and silero_available stays False.
        assert vp.silero_available is False

    def test_silero_thresholds_read_from_config(self) -> None:
        cfg = _config_with_vad_enabled()
        cfg.vad_speech_threshold = 0.7
        cfg.vad_silence_threshold = 0.2
        vp = VadProcessor(cfg)
        assert vp.speech_threshold == 0.7
        assert vp.silence_threshold == 0.2


# ── State machine transitions ──────────────────────────────────────────


class TestStateTransitions:
    """AUDIO-013 + AUDIO-018: hysteresis transitions."""

    def test_loud_chunk_increments_speech_counter(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.update_frame(-20.0)  # above -30 → loud
        assert vp.consecutive_speech_frames == 1
        assert vp.consecutive_silence_frames == 0

    def test_quiet_chunk_increments_silence_counter(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.update_frame(-60.0)  # below -50 → quiet
        assert vp.consecutive_silence_frames == 1
        assert vp.consecutive_speech_frames == 0

    def test_grey_zone_chunk_preserves_counters(self) -> None:
        """AUDIO-013: between thresholds, counters must NOT change."""
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        # Loud first
        vp.update_frame(-20.0)
        assert vp.consecutive_speech_frames == 1
        # Grey zone
        vp.update_frame(-40.0)
        assert vp.consecutive_speech_frames == 1, "AUDIO-013: grey-zone chunk must not reset the speech counter"
        assert vp.consecutive_silence_frames == 0

    def test_unknown_to_speech_after_n_consecutive_loud_frames(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.speech_frames = 3
        # 2 loud frames: not enough
        vp.update_frame(-20.0)
        vp.update_frame(-20.0)
        assert vp.state == VadState.UNKNOWN
        # 3rd loud frame: transition
        vp.update_frame(-20.0)
        assert vp.state == VadState.SPEECH

    def test_unknown_to_silence_after_n_consecutive_quiet_frames(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.silence_frames = 2
        vp.update_frame(-60.0)
        assert vp.state == VadState.UNKNOWN
        vp.update_frame(-60.0)
        assert vp.state == VadState.SILENCE

    def test_speech_to_silence_requires_hangover_frames(self) -> None:
        """AUDIO-018: SPEECH → SILENCE requires ``hangover_frames``
        consecutive quiet frames (not ``silence_frames``)."""
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.speech_frames = 1
        vp.hangover_frames = 3
        # Drive to SPEECH
        vp.update_frame(-20.0)
        assert vp.state == VadState.SPEECH
        # 2 quiet frames: not enough hangover
        vp.update_frame(-60.0)
        vp.update_frame(-60.0)
        assert vp.state == VadState.SPEECH, "AUDIO-018: at hangover-1 frames, state must remain SPEECH"
        # 3rd quiet frame: transition to SILENCE
        vp.update_frame(-60.0)
        assert vp.state == VadState.SILENCE

    def test_silence_to_speech_requires_speech_frames(self) -> None:
        """AUDIO-018: SILENCE → SPEECH requires ``speech_frames``
        consecutive loud frames (not ``hangover_frames``)."""
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.speech_frames = 3
        vp.silence_frames = 1
        vp.hangover_frames = 1
        # Drive to SILENCE
        vp.update_frame(-60.0)
        assert vp.state == VadState.SILENCE
        # 2 loud frames: not enough
        vp.update_frame(-20.0)
        vp.update_frame(-20.0)
        assert vp.state == VadState.SILENCE, "AUDIO-018: at speech_frames-1 frames, state must remain SILENCE"
        # 3rd loud frame: transition
        vp.update_frame(-20.0)
        assert vp.state == VadState.SPEECH

    def test_returns_unknown_when_vad_disabled(self) -> None:
        """VAD-GATE: returns UNKNOWN without updating any state when VAD
        is disabled (all audio enhancements off)."""
        vp = VadProcessor(_config_with_vad_disabled())
        # The vad_enabled cache may already be set from __init__'s
        # vad-check; force-refresh and confirm it's disabled.
        assert vp.vad_enabled is False
        result = vp.update_frame(-20.0)
        assert result == VadState.UNKNOWN
        # Counters must not have changed
        assert vp.consecutive_speech_frames == 0
        assert vp.consecutive_silence_frames == 0
        assert vp.state == VadState.UNKNOWN


# Grey-zone decay () ─────────────────────────────────────────


class TestGreyZoneDecay:
    """AUDIO-5: bound grey-zone hold so soft-speech tails don't stall the
    silence timer indefinitely. After ``_grey_zone_hold_limit`` (30)
    consecutive grey-zone frames, both counters decay by 1; the cycle
    repeats so the grey-zone hold is bounded to ~1s at 30 Hz."""

    def test_grey_zone_decay_after_hold_limit(self) -> None:
        """First ``hold_limit - 1`` grey-zone chunks must NOT decay; the
        30th triggers a single decay cycle (speech/silence each -= 1)."""
        vp = VadProcessor(_config_with_vad_enabled())
        # Default thresholds: silence=-50 dB, speech=-40 dB → -45 dB is grey.
        assert vp.silence_threshold_db == DEFAULT_VAD_SILENCE_THRESHOLD_DB
        assert vp.speech_threshold_db == DEFAULT_VAD_SPEECH_THRESHOLD_DB
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0  # -45 dB
        # Pre-load history so decay is observable (grey-zone preserves
        # counters unchanged, so this baseline holds until the 30th frame).
        vp.consecutive_speech_frames = 5
        vp.consecutive_silence_frames = 5

        # 29 grey-zone chunks: still under the hold limit — no decay.
        for _ in range(29):
            vp.update_frame(grey_db)
        assert vp.consecutive_speech_frames == 5, "AUDIO-5: first 29 grey-zone chunks must NOT decay speech counter"
        assert vp.consecutive_silence_frames == 5, "AUDIO-5: first 29 grey-zone chunks must NOT decay silence counter"
        # The grey-frame counter is at 29 (one short of the trigger).
        assert vp._consecutive_grey_frames == 29

        # 6 more grey-zone chunks (total 35): the 30th triggers one decay
        # cycle (speech 5→4, silence 5→4, grey reset to 0). Frames 31-35
        # then re-accumulate grey to 5 — no second decay yet.
        for _ in range(6):
            vp.update_frame(grey_db)
        assert vp.consecutive_speech_frames == 4, (
            "AUDIO-5: after the 30th grey-zone chunk, speech counter must decay by 1"
        )
        assert vp.consecutive_silence_frames == 4, (
            "AUDIO-5: after the 30th grey-zone chunk, silence counter must decay by 1"
        )
        # Grey counter reset to 0 at frame 30, then accumulated 5 more.
        assert vp._consecutive_grey_frames == 5

    def test_grey_zone_resets_on_clear_frame(self) -> None:
        """A clear loud (or quiet) frame resets ``_consecutive_grey_frames``
        to 0 so the decay cycle restarts from scratch on the next grey run."""
        vp = VadProcessor(_config_with_vad_enabled())
        # Default thresholds: silence=-50 dB, speech=-40 dB → -45 dB is grey.
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0

        # 20 grey-zone chunks: grey counter accumulates to 20.
        for _ in range(20):
            vp.update_frame(grey_db)
        assert vp._consecutive_grey_frames == 20

        # One clear loud chunk (-20 dB > speech_threshold -40 dB) resets grey.
        vp.update_frame(-20.0)
        assert vp._consecutive_grey_frames == 0, "AUDIO-5: a clear loud frame must reset the grey-zone counter"
        # And the speech counter advanced (loud frame increments speech).
        assert vp.consecutive_speech_frames == 1
        assert vp.consecutive_silence_frames == 0

    def test_grey_zone_decay_is_periodic(self) -> None:
        """AUDIO-5: decay repeats every ``hold_limit`` frames — 60 grey
        chunks (2 cycles) must decay each counter by 2 from the baseline."""
        vp = VadProcessor(_config_with_vad_enabled())
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0
        vp.consecutive_speech_frames = 5
        vp.consecutive_silence_frames = 5

        # 60 grey-zone chunks → 2 full decay cycles (frames 30 and 60).
        for _ in range(60):
            vp.update_frame(grey_db)
        assert vp.consecutive_speech_frames == 3, "AUDIO-5: 60 grey chunks (2 cycles) must decay speech by 2"
        assert vp.consecutive_silence_frames == 3, "AUDIO-5: 60 grey chunks (2 cycles) must decay silence by 2"

    def test_grey_zone_soft_tail_exits_speech(self) -> None:
        """AUDIO-5 (root cause): a sustained grey tail after SPEECH must
        transition to SILENCE, not stay locked in SPEECH.

        The recorder's silence timer only advances when ``update_frame``
        returns SILENCE (recorder.py:2420). Without this, a soft-spoken
        phrase ending (audio hovering in the grey zone) keeps returning
        SPEECH, holding the silence timer at 0 — so auto-stop never fires
        and the tail is held/cut off. After the grey-hold limit (~1s) the
        state must flip to SILENCE so the timer can advance/trigger.
        """
        vp = VadProcessor(_config_with_vad_enabled())
        # Clearly loud to drive into SPEECH (need >= speech_frames loud frames).
        loud_db = vp.speech_threshold_db + 5.0
        for _ in range(vp.speech_frames + 2):
            vp.update_frame(loud_db)
        assert vp.state == VadState.SPEECH, f"precondition: must be in SPEECH, got {vp.state}"
        # Now a sustained grey tail (between the thresholds).
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0
        last_state = vp.state
        for _ in range(vp._grey_zone_hold_limit):
            last_state = vp.update_frame(grey_db)
        assert last_state == VadState.SILENCE, (
            "AUDIO-5: sustained grey tail after speech must transition to "
            f"SILENCE (so silence timer advances), got {last_state}"
        )

    def test_grey_zone_soft_tail_resumes_on_loud(self) -> None:
        """AUDIO-5: if the speaker resumes (a loud frame) during/after the
        grey tail, the state must flip back to SPEECH and the silence timer
        would reset — i.e. we don't permanently wedge in SILENCE.
        """
        vp = VadProcessor(_config_with_vad_enabled())
        loud_db = vp.speech_threshold_db + 5.0
        for _ in range(vp.speech_frames + 2):
            vp.update_frame(loud_db)
        grey_db = (vp.silence_threshold_db + vp.speech_threshold_db) / 2.0
        for _ in range(vp._grey_zone_hold_limit):
            vp.update_frame(grey_db)
        assert vp.state == VadState.SILENCE
        # Speaker resumes — needs >= speech_frames consecutive loud frames
        # to flip back to SPEECH (hysteresis), same as the initial onset.
        for _ in range(vp.speech_frames + 2):
            vp.update_frame(loud_db)
        assert vp.state == VadState.SPEECH, "AUDIO-5: loud frames after the grey tail must resume SPEECH"


# ── Auto-calibration ───────────────────────────────────────────────────


class TestAutoCalibration:
    """AUDIO-014: ambient noise floor detection."""

    def test_calibration_collects_rms_until_duration_elapsed(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_duration = 1.5
        # 0.5s elapsed — still collecting
        vp.auto_calibrate(0.01, elapsed_seconds=0.5)
        vp.auto_calibrate(0.011, elapsed_seconds=0.6)
        assert vp.calibrated is False
        assert len(vp.calibration_rms_values) == 2

    def test_calibration_sets_thresholds_relative_to_noise_floor(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_duration = 1.5
        target_rms = 0.01  # -40 dBFS
        # Feed enough samples to exceed the calibration window (1.5s).
        # 50 samples at 0.05s spacing → last sample at 2.5s elapsed.
        for i in range(50):
            vp.auto_calibrate(target_rms, elapsed_seconds=0.05 * i + 0.05)
        assert vp.calibrated is True
        # noise_db = 20 * log10(0.01) = -40 dB
        # silence = noise + 6 = -34 dB
        # speech = noise + 18 = -22 dB
        assert abs(vp.silence_threshold_db - (-34.0)) < 0.1
        assert abs(vp.speech_threshold_db - (-22.0)) < 0.1
        assert vp.speech_threshold_db > vp.silence_threshold_db

    def test_calibration_is_idempotent_after_calibrated(self) -> None:
        """Once calibrated, subsequent calls are no-ops."""
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_duration = 1.5
        for i in range(50):
            vp.auto_calibrate(0.01, elapsed_seconds=0.05 * i + 0.05)
        assert vp.calibrated is True
        silence_after_first = vp.silence_threshold_db
        speech_after_first = vp.speech_threshold_db
        # More samples with very different RMS — should NOT change thresholds
        for i in range(50):
            vp.auto_calibrate(0.5, elapsed_seconds=3.0 + 0.05 * i)
        assert vp.silence_threshold_db == silence_after_first
        assert vp.speech_threshold_db == speech_after_first

    def test_calibration_skipped_when_vad_disabled(self) -> None:
        """VAD-GATE: auto_calibrate is a no-op when VAD is disabled."""
        vp = VadProcessor(_config_with_vad_disabled())
        assert vp.vad_enabled is False
        vp.auto_calibrate(0.01, elapsed_seconds=10.0)
        assert vp.calibrated is False
        assert vp.calibration_rms_values == []

    def test_calibration_handles_zero_rms(self) -> None:
        """Zero RMS would cause log10(0) — must fall back to -90 dB."""
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_duration = 0.1
        for i in range(20):
            vp.auto_calibrate(0.0, elapsed_seconds=0.01 * i + 0.01)
        assert vp.calibrated is True
        # noise_db = -90 (fallback)
        # silence = -90 + 6 = -84
        # speech = -90 + 18 = -72
        assert vp.silence_threshold_db == pytest.approx(-84.0, abs=0.1)
        assert vp.speech_threshold_db == pytest.approx(-72.0, abs=0.1)

    def test_calibration_skipped_when_silero_active(self, caplog: pytest.LogCaptureFixture) -> None:
        """AUDIO-4: when Silero VAD is the active backend, dB-threshold
        calibration has no effect (update_frame uses probability thresholds).
        ``auto_calibrate`` must skip with a one-time INFO log and not collect
        any RMS values.
        """
        cfg = _config_with_vad_enabled()
        cfg.use_silero_vad = True
        # Inject a stub ``vad_check_available_fn`` that returns True so
        # ``__init__`` sets ``silero_available=True`` without importing torch.
        vp = VadProcessor(cfg, vad_check_available_fn=lambda: True)
        assert vp.use_silero_vad is True
        assert vp.silero_available is True
        assert vp.vad_enabled is True  # noise_filter_highpass=True

        with caplog.at_level(logging.INFO, logger="voice_typer.server.vad_processor"):
            vp.auto_calibrate(0.01, elapsed_seconds=10.0)

        # calibrated is True (set by the skip branch to prevent re-entry)
        assert vp.calibrated is True
        # no RMS values were collected (skip happened before append)
        assert vp.calibration_rms_values == []
        # the one-time INFO log was emitted
        assert any("auto-calibration skipped" in record.getMessage() for record in caplog.records), (
            f"expected skip log, got: {[r.getMessage() for r in caplog.records]}"
        )
        # status is explicit + inspectable (not a silent no-op)
        assert vp.calibration_status == "skipped_silero"

        # Re-entry is prevented (calibrated flag short-circuits).
        caplog.clear()
        vp.auto_calibrate(0.01, elapsed_seconds=20.0)
        assert not any("auto-calibration skipped" in record.getMessage() for record in caplog.records), (
            "skip log must fire only once (re-entry guarded by _calibrated)"
        )
        assert vp.calibration_status == "skipped_silero"


# ── Silero-probability auto-calibration ───────────────────────────────


def _config_with_silero_and_auto_calibrate() -> MagicMock:
    """Return a MagicMock config with Silero VAD + vad_auto_calibrate
    enabled. The Silero backend is stubbed via vad_check_available_fn
    in the test body (no torch import required)."""
    cfg = MagicMock()
    cfg.use_silero_vad = True
    cfg.vad_speech_threshold = 0.5  # static default - calibration overrides
    cfg.vad_silence_threshold = 0.3
    cfg.vad_auto_calibrate = True  # opt-in flag
    cfg.noise_filter_highpass = True  # so vad_enabled gate returns True
    cfg.noise_filter_gate = False
    cfg.noise_filter_eq = False
    cfg.noise_filter_compressor = False
    cfg.noise_filter_limiter = False
    cfg.noise_filter_notch = False
    cfg.noise_suppression_method = "none"
    return cfg


class TestSileroAutoCalibrationEr42:
    """When vad_auto_calibrate=True and Silero is the active
    backend, the probability thresholds are derived from the first few
    seconds of Silero probabilities (noise floor) instead of relying on
    the static config defaults. Default off for backwards compat.
    """

    def test_flag_defaults_off(self) -> None:
        """Backwards compat: when the config doesn't set
        vad_auto_calibrate, the flag is False (existing skipped_silero
        behavior preserved). MagicMock auto-creates attributes as
        MagicMock instances (not bools), so the isinstance guard in
        __init__ treats them as False."""
        cfg = _config_with_vad_enabled()
        cfg.use_silero_vad = True
        # Don't set vad_auto_calibrate - getattr default is False,
        # and MagicMock auto-attr would be caught by isinstance guard.
        vp = VadProcessor(cfg, vad_check_available_fn=lambda: True)
        assert vp.vad_auto_calibrate is False

    def test_flag_read_from_config(self) -> None:
        """When config.vad_auto_calibrate=True, the flag is True."""
        cfg = _config_with_silero_and_auto_calibrate()
        vp = VadProcessor(cfg, vad_check_available_fn=lambda: True)
        assert vp.vad_auto_calibrate is True

    def test_silero_calibration_collects_probs_until_duration_elapsed(self) -> None:
        """Before the calibration window elapses, samples are collected
        but thresholds are NOT yet derived (calibrated stays False)."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 1.5
        vp.auto_calibrate(0.01, elapsed_seconds=0.5, vad_prob=0.05)
        vp.auto_calibrate(0.011, elapsed_seconds=0.6, vad_prob=0.06)
        assert vp.calibrated is False
        assert len(vp.calibration_prob_values) == 2
        # Thresholds unchanged from config defaults.
        assert vp.speech_threshold == 0.5
        assert vp.silence_threshold == 0.3

    def test_silero_calibration_sets_thresholds_relative_to_noise_floor(self) -> None:
        """After the calibration window, thresholds are derived from
        the median of collected probabilities:
            silence = noise_floor + MARGIN (0.05)
            speech  = silence + SPEECH_DELTA (0.15)
        """
        from voice_typer.server.vad_processor import (
            DEFAULT_VAD_SILERO_CALIBRATION_MARGIN,
            DEFAULT_VAD_SILERO_SPEECH_DELTA,
        )

        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 1.5
        for i in range(50):
            vp.auto_calibrate(0.01, elapsed_seconds=0.05 * i + 0.05, vad_prob=0.10)
        assert vp.calibrated is True
        assert vp.calibration_status == "calibrated_silero"
        # noise_floor = 0.10, silence = 0.15, speech = 0.30
        assert vp.silence_threshold == pytest.approx(0.10 + DEFAULT_VAD_SILERO_CALIBRATION_MARGIN, abs=0.001)
        assert vp.speech_threshold == pytest.approx(
            0.10 + DEFAULT_VAD_SILERO_CALIBRATION_MARGIN + DEFAULT_VAD_SILERO_SPEECH_DELTA,
            abs=0.001,
        )
        assert vp.speech_threshold > vp.silence_threshold

    def test_silero_calibration_is_idempotent_after_calibrated(self) -> None:
        """Once calibrated, subsequent calls with new vad_prob samples
        are no-ops (thresholds don't change)."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 1.5
        for i in range(50):
            vp.auto_calibrate(0.01, elapsed_seconds=0.05 * i + 0.05, vad_prob=0.10)
        assert vp.calibrated is True
        silence_after_first = vp.silence_threshold
        speech_after_first = vp.speech_threshold
        for i in range(50):
            vp.auto_calibrate(0.5, elapsed_seconds=3.0 + 0.05 * i, vad_prob=0.90)
        assert vp.silence_threshold == silence_after_first
        assert vp.speech_threshold == speech_after_first

    def test_silero_calibration_uses_median_not_mean(self) -> None:
        """Median (not mean) is used so a few transient speech bursts
        during the calibration window don't pull the floor up."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 1.5
        for i in range(40):
            vp.auto_calibrate(0.01, elapsed_seconds=0.05 * i + 0.05, vad_prob=0.10)
        for i in range(10):
            vp.auto_calibrate(0.5, elapsed_seconds=0.05 * (40 + i) + 0.05, vad_prob=0.80)
        assert vp.calibrated is True
        # noise_floor (median) = 0.10 -> silence = 0.15, speech = 0.30.
        assert vp.silence_threshold == pytest.approx(0.15, abs=0.01)
        assert vp.speech_threshold == pytest.approx(0.30, abs=0.01)

    def test_silero_calibration_thresholds_clamped_to_unit_interval(self) -> None:
        """A very high noise floor clamps thresholds to 1.0 and the
        minimum-spread guard kicks in so speech >= silence."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 1.5
        for i in range(50):
            vp.auto_calibrate(0.5, elapsed_seconds=0.05 * i + 0.05, vad_prob=0.95)
        assert vp.calibrated is True
        assert vp.silence_threshold <= 1.0
        assert vp.speech_threshold <= 1.0
        assert vp.speech_threshold >= vp.silence_threshold

    def test_silero_calibration_no_vad_prob_emits_warning_and_skips(self, caplog: pytest.LogCaptureFixture) -> None:
        """When the flag is on but the caller doesn't pass
        vad_prob, the calibration is skipped with a WARNING so the
        misconfiguration is visible (not silent)."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.vad_processor"):
            vp.auto_calibrate(0.01, elapsed_seconds=10.0)
        assert vp.calibration_status == "skipped_no_prob"
        assert vp.calibrated is True
        assert vp.speech_threshold == 0.5
        assert vp.silence_threshold == 0.3
        assert any(
            "vad_auto_calibrate=True" in record.getMessage() and "vad_prob" in record.getMessage()
            for record in caplog.records
        )

    def test_flag_off_preserves_existing_skipped_silero_behavior(self, caplog: pytest.LogCaptureFixture) -> None:
        """Backwards compat: with the flag OFF (default), the existing
        skipped_silero behavior is preserved - even if the caller
        passes vad_prob, no calibration runs."""
        cfg = _config_with_silero_and_auto_calibrate()
        cfg.vad_auto_calibrate = False  # explicitly off
        vp = VadProcessor(cfg, vad_check_available_fn=lambda: True)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.vad_processor"):
            vp.auto_calibrate(0.01, elapsed_seconds=10.0, vad_prob=0.05)
        assert vp.calibration_status == "skipped_silero"
        assert vp.calibrated is True
        assert vp.speech_threshold == 0.5
        assert vp.silence_threshold == 0.3
        assert vp.calibration_prob_values == []

    def test_silero_calibration_reset_restores_config_defaults(self) -> None:
        """reset() restores the Silero probability thresholds to the
        config defaults and clears the collected prob samples."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 1.5
        for i in range(50):
            vp.auto_calibrate(0.01, elapsed_seconds=0.05 * i + 0.05, vad_prob=0.10)
        assert vp.calibrated is True
        assert vp.speech_threshold != 0.5
        assert vp.silence_threshold != 0.3
        # 30 samples are collected (calibration fires at elapsed=1.5s,
        # the 30th sample; remaining 20 calls are no-ops once calibrated).
        assert len(vp.calibration_prob_values) > 0
        vp.reset()
        assert vp.speech_threshold == 0.5
        assert vp.silence_threshold == 0.3
        assert vp.calibration_prob_values == []
        assert vp.calibrated is False
        assert vp.calibration_status == "pending"

    def test_silero_calibration_status_calibrated_silero(self) -> None:
        """The new calibrated_silero status is set after a successful
        Silero-probability calibration."""
        vp = VadProcessor(
            _config_with_silero_and_auto_calibrate(),
            vad_check_available_fn=lambda: True,
        )
        vp.calibration_duration = 0.1
        for i in range(20):
            vp.auto_calibrate(0.01, elapsed_seconds=0.01 * i + 0.01, vad_prob=0.08)
        assert vp.calibration_status == "calibrated_silero"
        assert vp.calibrated is True


# calibration_status () ──────────────────────────────────────


class TestCalibrationStatus:
    """AUDIO-4: calibration_status must be explicit + inspectable so a
    no-op skip is never silent. Covered across every auto_calibrate branch.
    """

    def test_status_pending_until_run(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.calibration_status == "pending"

    def test_status_skipped_silero(self) -> None:
        cfg = _config_with_vad_enabled()
        cfg.use_silero_vad = True
        vp = VadProcessor(cfg, vad_check_available_fn=lambda: True)
        vp.auto_calibrate(0.01, elapsed_seconds=10.0)
        assert vp.calibration_status == "skipped_silero"
        assert vp.calibrated is True

    def test_status_skipped_disabled(self) -> None:
        vp = VadProcessor(_config_with_vad_disabled())
        assert vp.vad_enabled is False
        vp.auto_calibrate(0.01, elapsed_seconds=10.0)
        assert vp.calibration_status == "skipped_disabled"
        assert vp.calibrated is False
        assert vp.calibration_rms_values == []

    def test_status_calibrated(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_duration = 1.5
        for i in range(50):
            vp.auto_calibrate(0.01, elapsed_seconds=0.05 * i + 0.05)
        assert vp.calibration_status == "calibrated"
        assert vp.calibrated is True

    def test_status_reset_to_pending(self) -> None:
        cfg = _config_with_vad_enabled()
        cfg.use_silero_vad = True
        vp = VadProcessor(cfg, vad_check_available_fn=lambda: True)
        vp.auto_calibrate(0.01, elapsed_seconds=10.0)
        assert vp.calibration_status == "skipped_silero"
        vp.reset()
        assert vp.calibration_status == "pending"
        assert vp.calibrated is False


# ── reset() ────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_restores_unknown_state(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.state = VadState.SPEECH
        vp.reset()
        assert vp.state == VadState.UNKNOWN

    def test_reset_zeroes_counters(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.consecutive_speech_frames = 5
        vp.consecutive_silence_frames = 3
        vp.reset()
        assert vp.consecutive_speech_frames == 0
        assert vp.consecutive_silence_frames == 0

    def test_reset_restores_default_thresholds(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -22.0
        vp.silence_threshold_db = -34.0
        vp.reset()
        assert vp.speech_threshold_db == DEFAULT_VAD_SPEECH_THRESHOLD_DB
        assert vp.silence_threshold_db == DEFAULT_VAD_SILENCE_THRESHOLD_DB

    def test_reset_clears_calibration_state(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_rms_values = [0.1, 0.2, 0.3]
        vp.calibrated = True
        vp.reset()
        assert vp.calibration_rms_values == []
        assert vp.calibrated is False


# ── vad_enabled cache + on_config_changed ─────────────────────────────


class TestVadEnabledCache:
    def test_vad_enabled_true_when_any_filter_on(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.vad_enabled is True

    def test_vad_enabled_false_when_all_filters_off(self) -> None:
        vp = VadProcessor(_config_with_vad_disabled())
        assert vp.vad_enabled is False

    def test_vad_enabled_true_when_suppression_method_not_none(self) -> None:
        cfg = _config_with_vad_disabled()
        cfg.noise_suppression_method = "rnnoise"
        vp = VadProcessor(cfg)
        assert vp.vad_enabled is True

    def test_vad_enabled_uses_cache_after_first_call(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        first = vp.vad_enabled
        assert first is True
        # The cache fields should now be populated.
        assert vp.vad_enabled_cached is True
        assert vp.vad_enabled_cache_ts > 0.0

    def test_on_config_changed_refreshes_cache(self) -> None:
        cfg = _config_with_vad_enabled()
        vp = VadProcessor(cfg)
        assert vp.vad_enabled is True
        # User toggles all filters off
        cfg.noise_filter_highpass = False
        cfg.noise_suppression_method = "none"
        vp.on_config_changed()
        assert vp.vad_enabled is False

    def test_vad_enabled_ttl_safety_net(self) -> None:
        """If on_config_changed() is not called, the 5s TTL forces a
        re-evaluation on the next read."""
        cfg = _config_with_vad_enabled()
        vp = VadProcessor(cfg)
        assert vp.vad_enabled is True
        # Simulate a config change WITHOUT calling on_config_changed()
        cfg.noise_filter_highpass = False
        cfg.noise_suppression_method = "none"
        # Backdate the cache timestamp so the TTL triggers
        vp.vad_enabled_cache_ts = time.perf_counter() - 10.0
        assert vp.vad_enabled is False


# ── compute_vad_enabled (direct) ──────────────────────────────────────


class TestComputeVadEnabled:
    def test_returns_true_for_highpass(self) -> None:
        vp = VadProcessor(_config_with_vad_disabled())
        cfg = MagicMock(
            noise_filter_highpass=True,
            noise_filter_gate=False,
            noise_filter_eq=False,
            noise_filter_compressor=False,
            noise_filter_limiter=False,
            noise_filter_notch=False,
            noise_suppression_method="none",
        )
        assert vp.compute_vad_enabled(cfg) is True

    def test_returns_true_for_rnnoise(self) -> None:
        vp = VadProcessor(_config_with_vad_disabled())
        cfg = MagicMock(
            noise_filter_highpass=False,
            noise_filter_gate=False,
            noise_filter_eq=False,
            noise_filter_compressor=False,
            noise_filter_limiter=False,
            noise_filter_notch=False,
            noise_suppression_method="rnnoise",
        )
        assert vp.compute_vad_enabled(cfg) is True

    def test_returns_false_for_off_preset(self) -> None:
        vp = VadProcessor(_config_with_vad_disabled())
        cfg = MagicMock(
            noise_filter_highpass=False,
            noise_filter_gate=False,
            noise_filter_eq=False,
            noise_filter_compressor=False,
            noise_filter_limiter=False,
            noise_filter_notch=False,
            noise_suppression_method="none",
        )
        assert vp.compute_vad_enabled(cfg) is False

    def test_use_silero_vad_does_not_force_vad_enabled(self) -> None:
        """VAD-GATE: use_silero_vad controls WHICH path (Silero vs RMS),
        not WHETHER VAD runs. So setting it True with all filters off
        must still return False."""
        vp = VadProcessor(_config_with_vad_disabled())
        cfg = MagicMock(
            noise_filter_highpass=False,
            noise_filter_gate=False,
            noise_filter_eq=False,
            noise_filter_compressor=False,
            noise_filter_limiter=False,
            noise_filter_notch=False,
            noise_suppression_method="none",
            use_silero_vad=True,
        )
        assert vp.compute_vad_enabled(cfg) is False


# ── Property delegation (read/write) ──────────────────────────────────


class TestPropertyDelegation:
    """VadProcessor exposes its private state via read/write properties
    so the Recorder delegation shims (``rec._vad_state = X``) work
    transparently. Pin that contract here."""

    def test_state_read_write(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        assert vp.state == VadState.UNKNOWN
        vp.state = VadState.SPEECH
        assert vp.state == VadState.SPEECH

    def test_thresholds_read_write(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -22.0
        vp.silence_threshold_db = -34.0
        assert vp.speech_threshold_db == -22.0
        assert vp.silence_threshold_db == -34.0

    def test_calibration_rms_values_read_write(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        vp.calibration_rms_values = [0.1, 0.2]
        assert vp.calibration_rms_values == [0.1, 0.2]

    def test_silero_available_write(self) -> None:
        vp = VadProcessor(_config_with_vad_enabled())
        # Default is False in test env (use_silero_vad=False)
        assert vp.silero_available is False
        vp.silero_available = True
        assert vp.silero_available is True


# ── Thread-safety (concurrent access) ─────────────────────────────────


class TestThreadSafety:
    """VadProcessor is called from multiple threads: the audio worker
    thread (``update_frame``, ``auto_calibrate``), the main thread
    (``reset``, ``on_config_changed``), and tests. Verify no crashes or
    corruption under concurrent access.

    The VadProcessor does NOT use internal locks — it relies on CPython's
    GIL for atomic attribute reads/writes. The state-machine counters are
    only mutated from the audio worker thread (single producer), and the
    ``vad_enabled`` cache fields are simple Python attributes whose
    reads/writes are atomic under the GIL. The worst case race (audio
    worker reads cache while main thread refreshes it) yields a
    one-chunk-stale value, which is acceptable.
    """

    def test_concurrent_update_frame_and_on_config_changed_no_crash(self) -> None:
        """A config-change hook firing while the audio worker is updating
        the state machine must not crash or corrupt internal state."""
        import threading

        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        vp.speech_frames = 3
        vp.hangover_frames = 3
        stop = threading.Event()
        errors: list[Exception] = []

        def worker_update() -> None:
            try:
                i = 0
                while not stop.is_set():
                    # Alternate loud / quiet to drive state transitions
                    db = -20.0 if i % 4 < 2 else -60.0
                    vp.update_frame(db)
                    i += 1
            except Exception as exc:
                errors.append(exc)

        def worker_config() -> None:
            try:
                while not stop.is_set():
                    vp.on_config_changed()
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker_update, name="vad-update")
        t2 = threading.Thread(target=worker_config, name="vad-config")
        t1.start()
        t2.start()
        # Run briefly to interleave accesses
        time.sleep(0.05)
        stop.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        assert errors == [], f"concurrent access raised: {errors}"
        # The state must be a valid VadState member after concurrent access
        assert vp.state in (VadState.UNKNOWN, VadState.SILENCE, VadState.SPEECH)

    def test_concurrent_update_frame_and_reset_no_crash(self) -> None:
        """``Recorder.start()`` calls ``reset()`` on the main thread. If
        the audio worker is still mid-``update_frame`` (e.g. during a
        stop→start transition with a slow join), the reset must not
        corrupt the worker's in-flight mutation."""
        import threading

        vp = VadProcessor(_config_with_vad_enabled())
        vp.speech_threshold_db = -30.0
        vp.silence_threshold_db = -50.0
        stop = threading.Event()
        errors: list[Exception] = []

        def worker_update() -> None:
            try:
                i = 0
                while not stop.is_set():
                    db = -20.0 if i % 4 < 2 else -60.0
                    vp.update_frame(db)
                    i += 1
            except Exception as exc:
                errors.append(exc)

        def worker_reset() -> None:
            try:
                while not stop.is_set():
                    vp.reset()
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker_update, name="vad-update")
        t2 = threading.Thread(target=worker_reset, name="vad-reset")
        t1.start()
        t2.start()
        time.sleep(0.05)
        stop.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        assert errors == [], f"concurrent access raised: {errors}"
        # After reset wins the final race, state must be UNKNOWN and
        # counters zeroed. After update_frame wins, state may be any
        # valid member. Either way, no exception.
        assert vp.state in (VadState.UNKNOWN, VadState.SILENCE, VadState.SPEECH)
