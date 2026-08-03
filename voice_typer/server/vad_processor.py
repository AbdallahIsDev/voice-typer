"""VAD state machine, Silero integration, and auto-calibration.

extracted from ``voice_typer/server/recording.py`` (god-class
decomposition). The ``Recorder`` class previously owned device
resolution, VAD state machine, auto-calibration, resampling, buffer
management, xrun/clipping detection, hot-plug handling, and pre-roll
buffer — all in one 3200-line file. This module extracts the VAD-only
concerns into a cohesive unit with a narrow public API.

Scope of extraction (this module):
    * State machine (silence → speech → silence with hysteresis).
    * Silero model availability detection (lazy torch import).
    * Auto-calibration of RMS-dB thresholds from ambient noise floor.
    * Config-driven ``vad_enabled`` cache (5s TTL safety net).

Out of scope (remain in ``recording.py`` — see
``docs/rw04-recording-decomposition.md``):
    * AudioDeviceManager (device resolution, hot-plug, Bluetooth).
    * AudioBuffer (buffer mgmt, snapshot cache, 3-tier resampling).

Public API:
    VadProcessor(config)
        .update_frame(chunk_rms_db, vad_prob=None) -> VadState
        .auto_calibrate(chunk_rms, elapsed_seconds, chunk_duration=0.0) -> None
        .reset() -> None
        .compute_vad_enabled(config) -> bool
        .on_config_changed() -> None
        .vad_enabled  (cached property with 5s TTL)
        .state, .consecutive_speech_frames, .consecutive_silence_frames,
        .speech_threshold_db, .silence_threshold_db, .speech_frames,
        .silence_frames, .hangover_frames, .use_silero_vad,
        .speech_threshold, .silence_threshold, .silero_available,
        .calibration_duration, .calibration_rms_values, .calibrated,
        .vad_enabled_cached, .vad_enabled_cache_ts

The attribute names match the prior ``Recorder._vad_*`` names with the
``_vad_`` prefix stripped, so the delegation shims in ``Recorder`` are
trivial pass-throughs. Tests that historically poked
``rec._vad_state`` etc. continue to work via property delegation on
``Recorder``.
"""

from __future__ import annotations

import enum
import logging
import math
import time
from typing import Any

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

log = logging.getLogger(__name__)


# VAD state machine ───────────────────────────────────────


class VadState(enum.Enum):
    """VAD state-machine states with hysteresis transitions.

    SILENCE → SPEECH requires ``speech_frames`` consecutive loud frames.
    SPEECH → SILENCE requires ``silence_frames`` consecutive quiet frames.
    UNKNOWN is the initial state before enough frames have been observed.
    """

    SILENCE = "silence"
    SPEECH = "speech"
    UNKNOWN = "unknown"


# default VAD thresholds (overridden by auto-calibration)
DEFAULT_VAD_SPEECH_THRESHOLD_DB = -40.0  # dBFS — above this → speech candidate
DEFAULT_VAD_SILENCE_THRESHOLD_DB = -50.0  # dBFS — below this → silence candidate
# R18-F14: hard floors on the user-configurable thresholds. The
# setters below clamp ``speech_threshold_db`` / ``silence_threshold_db``
# to these values so a malformed config (or an auto-calibration
# artifact on a noisy mic) can't push speech detection into the
# noise floor (which would cause false starts on every ambient sound)
# or invert the speech/silence hysteresis. Exposed at module scope so
# tests can pin them and downstream callers can import them.
MIN_VAD_SPEECH_THRESHOLD_DB = -55.0  # dBFS — speech floor
MIN_VAD_SILENCE_THRESHOLD_DB = -65.0  # dBFS — silence floor (must be below speech floor)
DEFAULT_VAD_CALIBRATION_DURATION = 1.5  # seconds of ambient noise to sample
DEFAULT_VAD_SPEECH_FRAMES = 3  # consecutive loud frames to declare SPEECH
DEFAULT_VAD_SILENCE_FRAMES = 15  # consecutive quiet frames to declare SILENCE (hangover)
DEFAULT_VAD_HANGOVER_FRAMES = 15  # same as SILENCE_FRAMES — configurable alias

# Silero-probability auto-calibration constants. When
# ``config.vad_auto_calibrate`` is True and Silero VAD is the active
# backend, the first ``calibration_duration`` seconds of Silero
# probabilities are collected and used to derive the probability
# thresholds from the observed noise floor (instead of relying on the
# static config defaults). The math mirrors the RMS-dB calibration:
#   silence_threshold = noise_floor + MARGIN
#   speech_threshold  = silence_threshold + SPEECH_DELTA
# where ``noise_floor`` is the median of collected Silero probabilities
# (a robust estimator that ignores transient speech bursts during the
# calibration window). The margin/delta are linear deltas in
# probability space (0-1); ``SPEECH_DELTA = 0.15`` approximates the
# ~6 dB gap the finding specifies (a 2x amplitude ratio ~= 6 dB, but
# Silero probabilities don't have a natural dB scale, so a fixed
# linear delta is the pragmatic interpretation).
DEFAULT_VAD_SILERO_CALIBRATION_MARGIN: float = 0.05  # silence = noise_floor + 0.05
DEFAULT_VAD_SILERO_SPEECH_DELTA: float = 0.15  # speech = silence + 0.15 (~6 dB gap equivalent)
# Minimum separation between speech and silence Silero thresholds after
# calibration. Guards against a degenerate noise floor (e.g. all-zero
# probabilities from a silent mic) producing thresholds that are too
# close to distinguish.
MIN_VAD_SILERO_THRESHOLD_SPREAD: float = 0.10

# Silero VAD probability thresholds. These must match the canonical
# defaults declared on the ``Config`` dataclass
# (``voice_typer.server.config.Config.vad_speech_threshold`` /
# ``vad_silence_threshold``). Importing the class attribute here keeps the
# getattr fallback literals in lockstep with the Config default — previously
# a drift in one would silently change behavior for stub-config tests.
# A lazy import (inside a try/except) avoids a hard import cycle:
# ``config`` does not import ``vad_processor``, but importing it eagerly at
# module top would still couple the two modules; the deferred form is safe.
try:  # pragma: no cover - import shim, exercised at runtime
    from voice_typer.server.config import Config as _Config

    DEFAULT_VAD_SPEECH_PROB_THRESHOLD: float = _Config.vad_speech_threshold
    DEFAULT_VAD_SILENCE_PROB_THRESHOLD: float = _Config.vad_silence_threshold
except Exception:  # pragma: no cover - defensive fallback for partial imports
    DEFAULT_VAD_SPEECH_PROB_THRESHOLD = 0.5
    DEFAULT_VAD_SILENCE_PROB_THRESHOLD = 0.3


class VadProcessor:
    """Encapsulates the VAD state machine, Silero integration, and
        auto-calibration.

        Stateless w.r.t. the audio buffer: callers pass in per-frame
        RMS (and optional Silero probability) and read back the new state.
        The processor owns its own counters and threshold state, refreshed
        by :meth:`reset` between recording sessions.

    pure extraction from ``Recorder``. Behavior is preserved
        bit-for-bit — the state-machine logic, hysteresis, grey-zone
        pass-through, and auto-calibration math are identical to the
        pre-refactor ``Recorder._vad_update`` /
        ``Recorder._vad_auto_calibrate`` implementations.
    """

    # PERF-02 (c-review): max age in seconds before the cached vad_enabled
    # value is re-evaluated even if on_config_changed() has not fired.
    # Safety net so a missed config-change notification cannot permanently
    # wedge the cache.
    VAD_ENABLED_CACHE_TTL_S: float = 5.0

    def __init__(
        self,
        config: Any,
        vad_check_available_fn: Any | None = None,
    ) -> None:
        """Initialize the VAD processor.

        Args:
            config: the Config object. Read once for ``use_silero_vad``,
                ``vad_speech_threshold``, ``vad_silence_threshold``. The
                ``vad_enabled`` decision is computed lazily from
                ``config`` on first access (and re-cached), so subsequent
                config field changes are reflected after
                :meth:`on_config_changed` or the 5s TTL fallback.
            vad_check_available_fn: optional callable returning bool
                (Silero available?). When None, imports
                ``voice_typer.server.vad._check_vad_available`` lazily
                (preserving the prior deferred-import behavior).
        """
        self._config: Any = config

        # State machine counters
        self._state: VadState = VadState.UNKNOWN
        self._consecutive_speech_frames: int = 0
        self._consecutive_silence_frames: int = 0

        # grey-zone hold bounding. Without this, a long run of
        # grey-zone chunks (between speech and silence thresholds) pins
        # both counters indefinitely — soft-speech tails can stall the
        # silence timer. After ``_grey_zone_hold_limit`` consecutive
        # grey-zone frames, decay both counters by 1 so the state machine
        # can transition on the next clear frame.
        self._consecutive_grey_frames: int = 0
        # grey-zone hold limit is now configurable so soft-spoken
        # users (whose speech legitimately hovers in the 0.3-0.5 prob
        # band) can extend the bound beyond the default ~1s instead of
        # being force-transitioned to SILENCE mid-phrase. Reads
        # ``config.vad_grey_zone_hold_limit`` when explicitly set as an
        # int; falls back to 30 frames (~1s at 30 Hz) otherwise. The
        # isinstance guard avoids tripping on MagicMock configs in tests
        # (which auto-create attributes as MagicMock instances, not ints).
        _grey_override = getattr(config, "vad_grey_zone_hold_limit", None)
        if isinstance(_grey_override, int):
            self._grey_zone_hold_limit: int = _grey_override
        else:
            self._grey_zone_hold_limit: int = 30  # ~1s at 30 Hz

        # RMS-dB thresholds (overridden by auto-calibration)
        self._speech_threshold_db: float = DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._silence_threshold_db: float = DEFAULT_VAD_SILENCE_THRESHOLD_DB

        # Hysteresis frame counts
        self._speech_frames: int = DEFAULT_VAD_SPEECH_FRAMES
        self._silence_frames: int = DEFAULT_VAD_SILENCE_FRAMES
        self._hangover_frames: int = DEFAULT_VAD_HANGOVER_FRAMES

        # Silero VAD integration — when use_silero_vad is
        # enabled in config, the recording callback uses Silero VAD
        # probability instead of RMS dB thresholds for the state machine.
        # impl-vad-fix: ADR 0007 §4.1 changed the config.py default to
        # True. The getattr fallback here must match, otherwise removing
        # the attribute from a Config dataclass instance (e.g. in tests
        # or partial configs) silently disables VAD even though the
        # documented default is True.
        self._use_silero_vad: bool = getattr(config, "use_silero_vad", True)
        # getattr fallbacks now reference the canonical Config
        # defaults via DEFAULT_VAD_SPEECH_PROB_THRESHOLD /
        # DEFAULT_VAD_SILENCE_PROB_THRESHOLD (imported from Config at module
        # top) so a stub config exercises the same threshold as production.
        self._speech_threshold: float = getattr(config, "vad_speech_threshold", DEFAULT_VAD_SPEECH_PROB_THRESHOLD)
        self._silence_threshold: float = getattr(config, "vad_silence_threshold", DEFAULT_VAD_SILENCE_PROB_THRESHOLD)
        self._silero_available: bool = False
        if self._use_silero_vad:
            try:
                if vad_check_available_fn is None:
                    from voice_typer.server.vad import (
                        _check_vad_available as _vad_check_available,
                    )

                    vad_check_available_fn = _vad_check_available
                self._silero_available = bool(vad_check_available_fn())
                if not self._silero_available:
                    log.warning(
                        "[VAD] use_silero_vad=True but Silero VAD "
                        "unavailable (torch missing or bundled silero_vad.jit "
                        "not found) — falling back to RMS"
                    )
            except Exception:
                log.debug("[VAD] Silero init failed — falling back to RMS", exc_info=True)
                self._silero_available = False

        # auto-calibration state
        self._calibration_duration: float = DEFAULT_VAD_CALIBRATION_DURATION
        self._calibration_rms_values: list[float] = []
        # Silero-probability samples collected during the
        # calibration window when ``vad_auto_calibrate`` is enabled.
        # Separate from ``_calibration_rms_values`` because the two
        # are different scales (0-1 probability vs linear RMS amplitude)
        # and only one path runs per session (Silero OR RMS, not both).
        self._calibration_prob_values: list[float] = []
        self._calibrated: bool = False
        # explicit, inspectable calibration status so a no-op skip
        # (Silero active / VAD disabled / no samples) is never silent.
        # Values: "pending" | "calibrated" | "calibrated_silero" |
        # "skipped_silero" | "skipped_disabled" | "skipped_no_samples" |
        # "skipped_no_prob".
        self._calibration_status: str = "pending"

        # Opt-in flag for Silero-probability auto-calibration.
        # Default False for backwards compat. The isinstance guard
        # avoids tripping on MagicMock configs in tests (which
        # auto-create attributes as MagicMock instances, not bools).
        # Same pattern as the vad_grey_zone_hold_limit guard above.
        _vad_ac_override = getattr(config, "vad_auto_calibrate", False)
        self._vad_auto_calibrate: bool = isinstance(_vad_ac_override, bool) and _vad_ac_override

        # VAD-GATE (Task 4): gate ALL VAD processing on whether any audio
        # enhancement is active. See ``vad_enabled`` property below for
        # the caching strategy (5s TTL safety net + explicit refresh via
        # on_config_changed()).
        self._vad_enabled_cached: bool | None = None
        self._vad_enabled_cache_ts: float = 0.0

    # ── Public API: state machine + calibration ──────────────────────

    def update_frame(
        self,
        chunk_rms_db: float,
        vad_prob: float | None = None,
    ) -> VadState:
        """Update the VAD state machine based on the current frame's signal.

        Uses hysteresis — transitioning from SILENCE to SPEECH
                requires N consecutive loud frames, while SPEECH to SILENCE
                requires M consecutive quiet frames (hangover period). This
                prevents rapid toggling at the boundary.

                When Silero VAD is enabled and a probability is provided, uses
                the VAD probability for speech/silence determination instead of
                RMS dB. Falls back to RMS-based detection if ``vad_prob`` is
                None.

                VAD-GATE (Task 4): returns ``VadState.UNKNOWN`` immediately when
                VAD is disabled (all audio enhancements off). The caller's
                silence-timer logic sees UNKNOWN and treats it as "not silence"
                (no silence warnings, no VAD-based auto-stop).
        """
        # VAD-GATE (Task 4): skip the full state machine when VAD is
        # disabled. Returning UNKNOWN (without updating any state or
        # logging) means no silence warnings and no VAD-based auto-stop.
        if not self.vad_enabled:
            return VadState.UNKNOWN
        if vad_prob is not None and self._use_silero_vad and self._silero_available:
            # Silero VAD path — use probability thresholds
            is_loud = vad_prob >= self._speech_threshold
            is_quiet = vad_prob < self._silence_threshold
        else:
            # RMS dB path — traditional threshold-based detection
            is_loud = chunk_rms_db >= self._speech_threshold_db
            is_quiet = chunk_rms_db < self._silence_threshold_db

        if is_loud:
            self._consecutive_speech_frames += 1
            self._consecutive_silence_frames = 0
            # a clear loud frame breaks the grey-zone run.
            self._consecutive_grey_frames = 0
        elif is_quiet:
            self._consecutive_silence_frames += 1
            self._consecutive_speech_frames = 0
            # a clear quiet frame breaks the grey-zone run.
            self._consecutive_grey_frames = 0
        else:
            # Grey zone (between speech and silence thresholds).
            # bound the grey-zone hold so a soft-speech tail can't
            # lock the state machine in SPEECH and starve the silence timer.
            self._consecutive_grey_frames += 1
            if self._consecutive_grey_frames >= self._grey_zone_hold_limit:
                if self._state == VadState.SPEECH:
                    # Sustained grey after speech => the soft tail has ended.
                    # Drop the stale speech history and seed the silence
                    # counter so the SPEECH->SILENCE transition below fires.
                    # Without this, a soft phrase ending (audio hovering in
                    # the grey zone) keeps returning SPEECH, which holds the
                    # recorder's silence timer at 0 — so auto-stop never
                    # triggers and the tail is held/cut off. Bounding the hold
                    # to ~1s lets the silence timer start advancing ~1s after
                    # speech actually ends.
                    self._consecutive_speech_frames = 0
                    self._consecutive_silence_frames = self._hangover_frames
                elif self._state == VadState.SILENCE:
                    # SILENCE grey-zone PROMOTE.
                    # Pre-fix this branch only decayed both counters by 1,
                    # which meant a user speaking softly (audio hovering in
                    # the grey zone between speech and silence thresholds)
                    # was NEVER promoted to SPEECH — the recorder stayed in
                    # SILENCE, the silence timer kept advancing, and the
                    # recording auto-stopped even though the user was
                    # actively speaking. Mirror the SPEECH->SILENCE
                    # force-transition pattern: seed ``speech_frames`` to
                    # ``_speech_frames - 1`` so the NEXT grey frame (handled
                    # by the ``elif`` below) tips the state machine into
                    # SPEECH. Clear ``silence_frames`` so the SILENCE->SPEECH
                    # transition check is unambiguous.
                    #
                    # Note: UNKNOWN state is intentionally NOT seeded here.
                    # The UNKNOWN->SPEECH transition check below requires
                    # ``is_loud AND speech_frames >= _speech_frames``; grey
                    # frames have ``is_loud == False``, so seeding
                    # ``speech_frames`` would never trigger a transition on
                    # grey frames in UNKNOWN. UNKNOWN keeps the original
                    # decay behavior (bounded grey-zone hold for stale
                    # history), which the AUDIO-5 regression tests in
                    # ``test_vad_processor.py::TestGreyZoneDecay`` pin.
                    self._consecutive_silence_frames = 0
                    self._consecutive_speech_frames = self._speech_frames - 1
                else:
                    # UNKNOWN state: decay both counters by 1 so stale
                    # history can't pin the machine. Bounds the hold to ~1s.
                    # (Preserved from AUDIO-5; see note above.)
                    if self._consecutive_speech_frames > 0:
                        self._consecutive_speech_frames -= 1
                    if self._consecutive_silence_frames > 0:
                        self._consecutive_silence_frames -= 1
                self._consecutive_grey_frames = 0  # reset so decay is periodic
            elif (
                self._state == VadState.SILENCE
                and self._consecutive_speech_frames > 0
                and self._consecutive_speech_frames < self._speech_frames
            ):
                # Promote mode — the limit-hit branch above
                # seeded ``speech_frames`` to ``_speech_frames - 1``. Each
                # subsequent grey frame increments it by 1 so the NEXT grey
                # frame tips the state machine into SPEECH (the state-
                # transition check below fires when ``speech_frames >=
                # _speech_frames``). Without this increment, the seed would
                # sit at ``_speech_frames - 1`` forever and the transition
                # would never fire — reproducing the original "stuck in
                # SILENCE" bug. The ``> 0`` guard ensures we only increment
                # AFTER the seed (not on every grey frame in SILENCE); the
                # ``< _speech_frames`` guard caps the count so we don't
                # overshoot once the transition has fired.
                self._consecutive_speech_frames += 1

        # State transitions with hysteresis
        old_state = self._state
        if self._state == VadState.UNKNOWN:
            if is_loud and self._consecutive_speech_frames >= self._speech_frames:
                self._state = VadState.SPEECH
            elif is_quiet and self._consecutive_silence_frames >= self._silence_frames:
                self._state = VadState.SILENCE
        elif self._state == VadState.SILENCE and self._consecutive_speech_frames >= self._speech_frames:
            self._state = VadState.SPEECH
        elif self._state == VadState.SPEECH and self._consecutive_silence_frames >= self._hangover_frames:
            self._state = VadState.SILENCE

        if self._state != old_state:
            log.debug(
                "[VAD] %s -> %s (rms_db=%.1f, speech_frames=%d, silence_frames=%d)",
                old_state.value,
                self._state.value,
                chunk_rms_db,
                self._consecutive_speech_frames,
                self._consecutive_silence_frames,
            )

        return self._state

    def auto_calibrate(
        self,
        chunk_rms: float,
        elapsed_seconds: float,
        chunk_duration: float = 0.0,
        vad_prob: float | None = None,
    ) -> None:
        """Auto-calibrate VAD thresholds based on ambient noise floor.

        During the first ``calibration_duration`` seconds of
                recording, we collect RMS values to determine the ambient noise
                floor. Then we set speech/silence thresholds relative to it.

                Args:
                    chunk_rms: RMS amplitude of the current chunk (linear).
                    elapsed_seconds: time since recording start (used to gate the
                        calibration window). Caller computes this from
                        ``time.perf_counter() - recording_start_time`` so this
                        module stays clock-agnostic and testable.
                    chunk_duration: duration of the chunk in seconds (reserved
                        for future per-chunk weighting; currently unused, kept
                        for signature compatibility with the prior
                        ``Recorder._vad_auto_calibrate(chunk_rms, chunk_duration)``
                        API).
                    vad_prob: Silero VAD probability for the current
                        chunk (0-1). When ``config.vad_auto_calibrate`` is
                        True AND Silero is the active backend, this is
                        collected during the calibration window and used to
                        derive the probability thresholds from the observed
                        noise floor. When None (the default), the Silero
                        path falls through to the existing ``skipped_silero``
                        behavior, preserving backwards compat.
        """
        # VAD-GATE (Task 4): skip calibration entirely when VAD is
        # disabled. The prior fix only demoted the log level; this gate
        # prevents the calibration work and the RMS-value list growth
        # that would otherwise happen on every chunk in raw mode.
        if not self.vad_enabled:
            self._calibration_status = "skipped_disabled"
            return
        if self._calibrated:
            return

        # when Silero VAD is the active backend, dB-threshold
        # calibration has no effect (update_frame uses probability
        # thresholds). Two sub-paths:
        #   1. ``vad_auto_calibrate`` enabled AND a vad_prob
        #      sample is provided -> collect Silero probabilities and
        #      derive thresholds from the observed noise floor.
        #   2. Otherwise -> skip with a one-time INFO log so the
        #      operator knows calibration is intentionally not running
        #      (preserves the pre-calibration behavior for backwards compat).
        if self._use_silero_vad and self._silero_available:
            if self._vad_auto_calibrate and vad_prob is not None:
                self._calibrate_silero_thresholds(vad_prob, elapsed_seconds)
                return
            if self._vad_auto_calibrate and vad_prob is None:
                # the flag is on but the caller didn't pass
                # vad_prob. Surface a WARNING so the misconfiguration
                # is visible (not silent).
                self._calibration_status = "skipped_no_prob"
                self._calibrated = True  # prevent re-entry / log spam
                log.warning(
                    "[VAD] vad_auto_calibrate=True but vad_prob not "
                    "provided — Silero thresholds left at config defaults "
                    "[status=skipped_no_prob]"
                )
                return
            # Default (flag off): preserve the previous skip behavior.
            self._calibration_status = "skipped_silero"
            self._calibrated = True  # prevent re-entry
            log.info(
                "[VAD] auto-calibration skipped — Silero VAD active "
                "(uses probability thresholds, not RMS-dB) "
                "[status=skipped_silero]"
            )
            return

        self._calibration_rms_values.append(chunk_rms)

        if elapsed_seconds < self._calibration_duration:
            return  # still collecting samples

        if not self._calibration_rms_values:
            self._calibration_status = "skipped_no_samples"
            self._calibrated = True
            return

        # Compute noise floor from collected samples
        noise_rms = float(np.median(self._calibration_rms_values))
        # Convert to dBFS (approximately)
        noise_db = 20.0 * math.log10(noise_rms) if noise_rms > 0 else -90.0

        # Set thresholds relative to noise floor
        self._silence_threshold_db = noise_db + 6.0  # 6 dB above noise -> silence
        self._speech_threshold_db = noise_db + 18.0  # 18 dB above noise -> speech
        self._calibrated = True
        self._calibration_status = "calibrated"

        # VAD auto-calibration runs every recording start (the dB thresholds
        # are a Silero-fallback used for silence/speech detection). Log the
        # result at INFO so operators can verify the measured noise floor and
        # thresholds from the default app logs — this is genuine per-session
        # operational state (logged once per session, not per audio frame).
        log.info(
            "[VAD] auto-calibrated: noise_floor=%.1f dBFS, silence_threshold=%.1f dBFS, speech_threshold=%.1f dBFS",
            noise_db,
            self._silence_threshold_db,
            self._speech_threshold_db,
        )

    def _calibrate_silero_thresholds(
        self,
        vad_prob: float,
        elapsed_seconds: float,
    ) -> None:
        """Collect Silero probabilities and derive thresholds.

        Mirrors the RMS-dB calibration math but in linear probability
        space: collect ``vad_prob`` samples during the calibration
        window, then set::

            noise_floor       = median(collected probs)
            silence_threshold = noise_floor + MARGIN
            speech_threshold  = silence_threshold + SPEECH_DELTA

        The thresholds are clamped to ``[0, 1]`` and a minimum spread
        (``MIN_VAD_SILERO_THRESHOLD_SPREAD``) is enforced so a
        degenerate noise floor (silent mic) doesn't produce
        indistinguishable thresholds.

        This is a private helper invoked from ``auto_calibrate`` when
        ``vad_auto_calibrate`` is True and Silero is the active
        backend. It mutates ``_speech_threshold`` /
        ``_silence_threshold`` / ``_calibrated`` /
        ``_calibration_status`` and appends to
        ``_calibration_prob_values``.
        """
        self._calibration_prob_values.append(float(vad_prob))

        if elapsed_seconds < self._calibration_duration:
            return  # still collecting samples

        if not self._calibration_prob_values:
            self._calibration_status = "skipped_no_samples"
            self._calibrated = True
            return

        # noise_floor = median of collected Silero probabilities.
        # Median (not mean) is robust to transient speech bursts
        # during the calibration window.
        noise_prob = float(np.median(self._calibration_prob_values))

        # silence = noise_floor + MARGIN
        silence = noise_prob + DEFAULT_VAD_SILERO_CALIBRATION_MARGIN
        # speech = silence + SPEECH_DELTA (the finding's "silence + 6dB"
        # gap, interpreted as a linear delta in probability space).
        speech = silence + DEFAULT_VAD_SILERO_SPEECH_DELTA

        # Enforce a minimum spread + clamp to [0, 1].
        if speech - silence < MIN_VAD_SILERO_THRESHOLD_SPREAD:
            speech = silence + MIN_VAD_SILERO_THRESHOLD_SPREAD
        silence = max(0.0, min(1.0, silence))
        speech = max(0.0, min(1.0, speech))
        # Final guard: if clamping inverted the order (only possible
        # if silence hit 1.0), force speech to silence + spread.
        if speech <= silence:
            speech = min(1.0, silence + MIN_VAD_SILERO_THRESHOLD_SPREAD)

        self._silence_threshold = silence
        self._speech_threshold = speech
        self._calibrated = True
        self._calibration_status = "calibrated_silero"

        log.info(
            "[VAD] auto-calibrated Silero: noise_floor=%.3f, "
            "silence_threshold=%.3f, speech_threshold=%.3f "
            "[status=calibrated_silero]",
            noise_prob,
            self._silence_threshold,
            self._speech_threshold,
        )

    def reset(self) -> None:
        """Reset VAD state machine + auto-calibration to defaults.

                Called by ``Recorder.start()`` at the beginning of each session
                so counters and thresholds from the prior session don't bleed
                into the new one.

        also resets the Silero LSTM hidden state (if the model
                is loaded) so prior-session speech patterns don't bias the
                first probabilities of the new session.
        """
        self._state = VadState.UNKNOWN
        self._consecutive_speech_frames = 0
        self._consecutive_silence_frames = 0
        # reset grey-zone hold counter on session reset.
        self._consecutive_grey_frames = 0
        self._speech_threshold_db = DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._silence_threshold_db = DEFAULT_VAD_SILENCE_THRESHOLD_DB
        self._calibration_rms_values = []
        # Clear Silero-probability calibration samples too so
        # the next session re-collects from scratch. Also restore the
        # Silero probability thresholds to the config defaults.
        self._calibration_prob_values = []
        self._speech_threshold = float(getattr(self._config, "vad_speech_threshold", DEFAULT_VAD_SPEECH_PROB_THRESHOLD))
        self._silence_threshold = float(
            getattr(self._config, "vad_silence_threshold", DEFAULT_VAD_SILENCE_PROB_THRESHOLD)
        )
        self._calibrated = False
        self._calibration_status = "pending"

        # reset Silero LSTM hidden state at session boundaries.
        # No-op if the model isn't loaded (avoids triggering a load just
        # to reset state — the model starts fresh on first load).
        try:
            from voice_typer.server.vad import reset_states as _vad_reset_states

            _vad_reset_states()
        except Exception:
            log.debug("[VAD] Silero reset_states unavailable", exc_info=True)

    # ── VAD-enabled cache (VAD-GATE Task 4 + PERF-02) ────────────────

    @property
    def vad_enabled(self) -> bool:
        """Whether VAD should run based on current audio enhancement state.

        VAD-GATE (Task 4): ensures that if the user changes the audio
        preset to "Off" while the Recorder exists (or mid-session), the
        VAD gate reflects the current config state.

        PERF-02 (c-review): previously a dynamic @property that
        re-evaluated 6 ``getattr()`` calls on every access (read 3× per
        chunk × 16 Hz = 288 getattr/sec for a value that only changes
        when the user toggles a Settings UI switch). Now returns a
        cached value refreshed by ``on_config_changed()`` (the explicit
        hook) with a 5-second TTL safety net so a missed config-change
        notification cannot permanently wedge the cache.
        """
        cached = self._vad_enabled_cached
        if cached is not None:
            # Safety-net refresh: if the explicit on_config_changed()
            # hook has not fired recently, fall back to re-evaluating at
            # most once per VAD_ENABLED_CACHE_TTL_S. Cheap: one
            # perf_counter call + one comparison.
            now = time.perf_counter()
            if now - self._vad_enabled_cache_ts >= self.VAD_ENABLED_CACHE_TTL_S:
                # (item 2): reassign the narrowed local ``cached``
                # rather than re-reading ``self._vad_enabled_cached`` so
                # pyrefly's null-safety check tracks the narrowed
                # ``bool`` type through the return (an attribute access
                # on ``self`` is treated as ``bool | None`` since the
                # checker can't rule out a concurrent mutation).
                cached = self.compute_vad_enabled(self._config)
                self._vad_enabled_cached = cached
                self._vad_enabled_cache_ts = now
            return cached
        # First access (cache cold): compute + cache.
        self._vad_enabled_cached = self.compute_vad_enabled(self._config)
        self._vad_enabled_cache_ts = time.perf_counter()
        return self._vad_enabled_cached

    def on_config_changed(self) -> None:
        """Refresh cached config-derived state after a config change.

                PERF-02 (c-review): called by ``app._rebuild_audio_processor``
                (wiring owned by Sub-Agent H in app.py) whenever any
                ``noise_filter_*``, ``audio_preset``, or
                ``noise_suppression_method`` config field changes. Refreshes
                the cached ``vad_enabled`` value so the next audio chunk's VAD
                gate decision uses the new config without re-running 6
                ``getattr()`` calls per access.

        when VAD transitions enabled → disabled mid-session
                (user selected the "Off" audio preset, or manually turned off
                every noise filter), the Silero model is unloaded so the ~2MB
                JIT graph isn't pinned in RAM for the rest of the process
                lifetime. Reload happens lazily via ``vad._load_model`` on the
                next VAD-enabled chunk.

                Safe to call from any thread (only reads ``self._config`` and
                writes two atomic Python attributes under the GIL). No-op if
                the processor has not been initialized yet.
        """
        was_enabled = self._vad_enabled_cached
        new_enabled = self.compute_vad_enabled(self._config)
        self._vad_enabled_cached = new_enabled
        self._vad_enabled_cache_ts = time.perf_counter()

        # release the Silero model when VAD transitions to
        # disabled. ``unload`` is a no-op if the model isn't loaded.
        if was_enabled and not new_enabled:
            try:
                from voice_typer.server.vad import unload as _vad_unload

                _vad_unload()
                log.info("[VAD] Silero model unloaded (VAD disabled mid-session)")
            except Exception:
                log.debug("[VAD] unload on config-change failed", exc_info=True)

    def compute_vad_enabled(self, config: Any) -> bool:
        """Compute whether VAD should run based on audio enhancement state.

        VAD-GATE (Task 4): VAD is part of the audio enhancement pipeline.
        When the user selects the "Off" audio preset (or manually disables
        every noise filter), they are opting into raw recording. Running
        VAD in that mode produces log spam and wastes CPU on a feature
        the user explicitly turned off.

        VAD is enabled when ANY of:
        - Any noise filter toggle is True (highpass/gate/eq/compressor/limiter/notch)
        - ``noise_suppression_method`` is not "none"

        Note: ``use_silero_vad`` is intentionally NOT checked here — it controls
        WHETHER to use the Silero ML model vs RMS thresholds when VAD IS enabled,
        not whether VAD runs at all. Previously it was checked first and always
        returned True (since use_silero_vad defaults to True), which defeated the
        VAD-GATE and caused VAD auto-calibration and state-transition logs to appear
        even when all audio enhancements were disabled (the "Off" preset).
        """
        filter_flags = (
            getattr(config, "noise_filter_highpass", False),
            getattr(config, "noise_filter_gate", False),
            getattr(config, "noise_filter_eq", False),
            getattr(config, "noise_filter_compressor", False),
            getattr(config, "noise_filter_limiter", False),
            getattr(config, "noise_filter_notch", False),
        )
        if any(filter_flags):
            return True
        return str(getattr(config, "noise_suppression_method", "none")).lower() != "none"

    # ── State accessors (used by Recorder delegation shims + tests) ──
    # These are plain attributes (not properties) — Recorder exposes
    # them through property shims named ``_vad_*`` so historical tests
    # that do ``rec._vad_state = X`` keep working.

    @property
    def state(self) -> VadState:
        return self._state

    @state.setter
    def state(self, value: VadState) -> None:
        self._state = value

    @property
    def consecutive_speech_frames(self) -> int:
        return self._consecutive_speech_frames

    @consecutive_speech_frames.setter
    def consecutive_speech_frames(self, value: int) -> None:
        self._consecutive_speech_frames = value

    @property
    def consecutive_silence_frames(self) -> int:
        return self._consecutive_silence_frames

    @consecutive_silence_frames.setter
    def consecutive_silence_frames(self, value: int) -> None:
        self._consecutive_silence_frames = value

    @property
    def speech_threshold_db(self) -> float:
        return self._speech_threshold_db

    @speech_threshold_db.setter
    def speech_threshold_db(self, value: float) -> None:
        # R18-F14: clamp to the speech threshold floor so a noisy
        # auto-calibration (or a malformed config) can't push speech
        # detection into the noise floor.
        self._speech_threshold_db = max(float(value), MIN_VAD_SPEECH_THRESHOLD_DB)

    @property
    def silence_threshold_db(self) -> float:
        return self._silence_threshold_db

    @silence_threshold_db.setter
    def silence_threshold_db(self, value: float) -> None:
        # R18-F14: clamp to the silence threshold floor.
        self._silence_threshold_db = max(float(value), MIN_VAD_SILENCE_THRESHOLD_DB)

    @property
    def speech_frames(self) -> int:
        return self._speech_frames

    @speech_frames.setter
    def speech_frames(self, value: int) -> None:
        self._speech_frames = value

    @property
    def silence_frames(self) -> int:
        return self._silence_frames

    @silence_frames.setter
    def silence_frames(self, value: int) -> None:
        self._silence_frames = value

    @property
    def hangover_frames(self) -> int:
        return self._hangover_frames

    @hangover_frames.setter
    def hangover_frames(self, value: int) -> None:
        self._hangover_frames = value

    @property
    def use_silero_vad(self) -> bool:
        return self._use_silero_vad

    @use_silero_vad.setter
    def use_silero_vad(self, value: bool) -> None:
        self._use_silero_vad = value

    @property
    def speech_threshold(self) -> float:
        return self._speech_threshold

    @speech_threshold.setter
    def speech_threshold(self, value: float) -> None:
        self._speech_threshold = value

    @property
    def silence_threshold(self) -> float:
        return self._silence_threshold

    @silence_threshold.setter
    def silence_threshold(self, value: float) -> None:
        self._silence_threshold = value

    @property
    def silero_available(self) -> bool:
        return self._silero_available

    @silero_available.setter
    def silero_available(self, value: bool) -> None:
        self._silero_available = value

    @property
    def calibration_duration(self) -> float:
        return self._calibration_duration

    @calibration_duration.setter
    def calibration_duration(self, value: float) -> None:
        self._calibration_duration = value

    @property
    def calibration_rms_values(self) -> list[float]:
        return self._calibration_rms_values

    @calibration_rms_values.setter
    def calibration_rms_values(self, value: list[float]) -> None:
        self._calibration_rms_values = value

    @property
    def calibration_prob_values(self) -> list[float]:
        """Silero-probability samples collected during the
        calibration window when ``vad_auto_calibrate`` is enabled.
        Read/write property for testability + inspection (mirrors
        ``calibration_rms_values``).
        """
        return self._calibration_prob_values

    @calibration_prob_values.setter
    def calibration_prob_values(self, value: list[float]) -> None:
        self._calibration_prob_values = value

    @property
    def vad_auto_calibrate(self) -> bool:
        """Whether Silero-probability auto-calibration is
        enabled (``config.vad_auto_calibrate``, default False)."""
        return self._vad_auto_calibrate

    @vad_auto_calibrate.setter
    def vad_auto_calibrate(self, value: bool) -> None:
        self._vad_auto_calibrate = bool(value)

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    @calibrated.setter
    def calibrated(self, value: bool) -> None:
        self._calibrated = value

    @property
    def calibration_status(self) -> str:
        """Explicit, inspectable reason for the current calibration state.

        makes a no-op skip (Silero active / VAD disabled / no
                samples) explicit rather than a silent early-return. Values:
                ``"pending"`` (not yet run), ``"calibrated"`` (RMS-dB thresholds
                computed), ``"calibrated_silero"`` (Silero probability
                thresholds computed from observed noise floor),
                ``"skipped_silero"`` (Silero active — uses probability
                thresholds), ``"skipped_disabled"`` (VAD off),
                ``"skipped_no_samples"`` (calibration window elapsed with no
                RMS samples), ``"skipped_no_prob"`` (flag on but the
                caller did not pass ``vad_prob``).
        """
        return self._calibration_status

    @calibration_status.setter
    def calibration_status(self, value: str) -> None:
        self._calibration_status = value

    @property
    def vad_enabled_cached(self) -> bool | None:
        return self._vad_enabled_cached

    @vad_enabled_cached.setter
    def vad_enabled_cached(self, value: bool | None) -> None:
        self._vad_enabled_cached = value

    @property
    def vad_enabled_cache_ts(self) -> float:
        return self._vad_enabled_cache_ts

    @vad_enabled_cache_ts.setter
    def vad_enabled_cache_ts(self, value: float) -> None:
        self._vad_enabled_cache_ts = value
