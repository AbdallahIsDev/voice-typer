"""VAD state machine, Silero integration, and auto-calibration.

extracted from ``voice_typer/server/recording.py`` (god-class
decomposition). The ``Recorder`` class previously owned device
resolution, VAD state machine, auto-calibration, resampling, buffer
management, xrun/clipping detection, hot-plug handling, and pre-roll
buffer, all in one 3200-line file. This module extracts the VAD-only
concerns into a cohesive unit with a narrow public API.

Scope of extraction (this module):
    * State machine (silence → speech → silence with hysteresis).
    * Silero model availability detection (lazy torch import).
    * Auto-calibration of RMS-dB thresholds from ambient noise floor.
    * Config-driven ``vad_enabled`` cache (5s TTL safety net).

Out of scope (remain in ``recording.py``: see
``docs/history/rw04-recording-decomposition.md``):
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
``_vad_`` prefix stripped. The ``Recorder`` delegation shims were
removed, ``VadProcessor`` owns the state and tests / consumers access
it via ``recorder._vad.<attr>`` (e.g. ``recorder._vad.state``).
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
DEFAULT_VAD_SPEECH_THRESHOLD_DB = -40.0  # dBFS, above this → speech candidate
DEFAULT_VAD_SILENCE_THRESHOLD_DB = -50.0  # dBFS, below this → silence candidate
# R18-F14: hard floors on the user-configurable thresholds. The
MIN_VAD_SPEECH_THRESHOLD_DB = -55.0  # dBFS, speech floor
MIN_VAD_SILENCE_THRESHOLD_DB = -65.0  # dBFS, silence floor (must be below speech floor)
DEFAULT_VAD_CALIBRATION_DURATION = 1.5  # seconds of ambient noise to sample
DEFAULT_VAD_SPEECH_FRAMES = 3  # consecutive loud frames to declare SPEECH
DEFAULT_VAD_SILENCE_FRAMES = 15  # consecutive quiet frames to declare SILENCE (hangover)
DEFAULT_VAD_HANGOVER_FRAMES = 15  # same as SILENCE_FRAMES, configurable alias

# Silero-probability auto-calibration constants. When
DEFAULT_VAD_SILERO_CALIBRATION_MARGIN: float = 0.05  # silence = noise_floor + 0.05
DEFAULT_VAD_SILERO_SPEECH_DELTA: float = 0.15  # speech = silence + 0.15 (~6 dB gap equivalent)
# Minimum separation between speech and silence Silero thresholds after
MIN_VAD_SILERO_THRESHOLD_SPREAD: float = 0.10

# Silero VAD probability thresholds. These must match the canonical
try:  # pragma: no cover - import shim, exercised at runtime
    from voice_typer.server.config import Config as _Config

    DEFAULT_VAD_SPEECH_PROB_THRESHOLD: float = _Config.vad_speech_threshold
    DEFAULT_VAD_SILENCE_PROB_THRESHOLD: float = _Config.vad_silence_threshold
except Exception:  # pragma: no cover - defensive fallback for partial imports
    DEFAULT_VAD_SPEECH_PROB_THRESHOLD = 0.5
    DEFAULT_VAD_SILENCE_PROB_THRESHOLD = 0.3


def _make_vad_property(attr: str, doc: str | None = None) -> property:
    """Factory: build a read/write property backed by ``self._<attr>``.

    Reduces boilerplate for the pure pass-through state accessors on
    :class:`VadProcessor` (state, speech/silence frame counters, plain
    thresholds, calibration samples, cache flags). Properties with real
    logic, the clamping floors on ``speech_threshold_db`` /
    ``silence_threshold_db`` (R18-F14), stay hand-written below.

    Mirrors the factory in
    :mod:`voice_typer.server.recording.vad_helpers` but delegates to
    ``self._<attr>`` (the local backing attribute) instead of
    ``self._vad.<attr>`` (the Recorder's delegation target).
    """

    backing = "_" + attr

    def getter(self: Any) -> Any:
        return getattr(self, backing)

    def setter(self: Any, value: Any) -> None:
        setattr(self, backing, value)

    return property(getter, setter, doc=doc)


class VadProcessor:
    """Encapsulates the VAD state machine, Silero integration, and
        auto-calibration.

        Stateless w.r.t. the audio buffer: callers pass in per-frame
        RMS (and optional Silero probability) and read back the new state.
        The processor owns its own counters and threshold state, refreshed
        by :meth:`reset` between recording sessions.

    pure extraction from ``Recorder``. Behavior is preserved
        bit-for-bit, the state-machine logic, hysteresis, grey-zone
        pass-through, and auto-calibration math are identical to the
        pre-refactor ``Recorder._vad_update`` /
        ``Recorder._vad_auto_calibrate`` implementations.
    """

    # PERF-02 (c-review): max age in seconds before the cached vad_enabled
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
                ``voice_typer.server.vad.is_available`` lazily
                (preserving the prior deferred-import behavior).
        """
        self._config: Any = config

        # State machine counters
        self._state: VadState = VadState.UNKNOWN
        self._consecutive_speech_frames: int = 0
        self._consecutive_silence_frames: int = 0

        # grey-zone hold bounding. Without this, a long run of
        self._consecutive_grey_frames: int = 0
        # grey-zone hold limit is now configurable so soft-spoken
        _grey_override = getattr(config, "vad_grey_zone_hold_limit", None)
        if isinstance(_grey_override, int):
            self._grey_zone_hold_limit: int = _grey_override
        else:
            self._grey_zone_hold_limit: int = 30  # ~1s at the ~31 Hz chunk cadence

        # RMS-dB thresholds (overridden by auto-calibration)
        self._speech_threshold_db: float = DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._silence_threshold_db: float = DEFAULT_VAD_SILENCE_THRESHOLD_DB

        # Hysteresis frame counts
        self._speech_frames: int = DEFAULT_VAD_SPEECH_FRAMES
        self._silence_frames: int = DEFAULT_VAD_SILENCE_FRAMES
        self._hangover_frames: int = DEFAULT_VAD_HANGOVER_FRAMES

        # Silero VAD integration: when use_silero_vad is
        self._use_silero_vad: bool = getattr(config, "use_silero_vad", True)
        # getattr fallbacks now reference the canonical Config
        self._speech_threshold: float = getattr(config, "vad_speech_threshold", DEFAULT_VAD_SPEECH_PROB_THRESHOLD)
        self._silence_threshold: float = getattr(config, "vad_silence_threshold", DEFAULT_VAD_SILENCE_PROB_THRESHOLD)
        self._silero_available: bool = False
        if self._use_silero_vad:
            try:
                if vad_check_available_fn is None:
                    from voice_typer.server.vad import (
                        is_available as _vad_check_available,
                    )

                    vad_check_available_fn = _vad_check_available
                self._silero_available = bool(vad_check_available_fn())
                if not self._silero_available:
                    log.warning(
                        "[VAD] use_silero_vad=True but Silero VAD "
                        "unavailable (torch missing or bundled silero_vad.jit "
                        "not found), falling back to RMS"
                    )
            except Exception:
                log.debug("[VAD] Silero init failed, falling back to RMS", exc_info=True)
                self._silero_available = False

        # auto-calibration state
        self._calibration_duration: float = DEFAULT_VAD_CALIBRATION_DURATION
        self._calibration_rms_values: list[float] = []
        # Silero-probability samples collected during the
        self._calibration_prob_values: list[float] = []
        self._calibrated: bool = False
        # explicit, inspectable calibration status so a no-op skip
        self._calibration_status: str = "pending"

        # Opt-in flag for Silero-probability auto-calibration.
        _vad_ac_override = getattr(config, "vad_auto_calibrate", False)
        self._vad_auto_calibrate: bool = isinstance(_vad_ac_override, bool) and _vad_ac_override

        # VAD-GATE (Task 4): gate ALL VAD processing on whether any audio
        self._vad_enabled_cached: bool | None = None
        self._vad_enabled_cache_ts: float = 0.0

    def update_frame(
        self,
        chunk_rms_db: float,
        vad_prob: float | None = None,
    ) -> VadState:
        """Update the VAD state machine based on the current frame's signal.

        Uses hysteresis, transitioning from SILENCE to SPEECH
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
        if not self.vad_enabled:
            return VadState.UNKNOWN
        if vad_prob is not None and self._use_silero_vad and self._silero_available:
            # Silero VAD path: use probability thresholds
            is_loud = vad_prob >= self._speech_threshold
            is_quiet = vad_prob < self._silence_threshold
        else:
            # RMS dB path, traditional threshold-based detection
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
            self._consecutive_grey_frames += 1
            if self._consecutive_grey_frames >= self._grey_zone_hold_limit:
                if self._state == VadState.SPEECH:
                    # Sustained grey after speech => the soft tail has ended.
                    self._consecutive_speech_frames = 0
                    self._consecutive_silence_frames = self._hangover_frames
                elif self._state == VadState.SILENCE:
                    # SILENCE grey-zone PROMOTE.
                    self._consecutive_silence_frames = 0
                    self._consecutive_speech_frames = self._speech_frames - 1
                else:
                    # UNKNOWN state: decay both counters by 1 so stale
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
                # Promote mode, the limit-hit branch above
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
        if not self.vad_enabled:
            self._calibration_status = "skipped_disabled"
            return
        if self._calibrated:
            return

        # when Silero VAD is the active backend, dB-threshold
        if self._use_silero_vad and self._silero_available:
            if self._vad_auto_calibrate and vad_prob is not None:
                self._calibrate_silero_thresholds(vad_prob, elapsed_seconds)
                return
            if self._vad_auto_calibrate and vad_prob is None:
                # the flag is on but the caller didn't pass
                self._calibration_status = "skipped_no_prob"
                self._calibrated = True  # prevent re-entry / log spam
                log.warning(
                    "[VAD] vad_auto_calibrate=True but vad_prob not "
                    "provided, Silero thresholds left at config defaults "
                    "[status=skipped_no_prob]"
                )
                return
            # Default (flag off): preserve the previous skip behavior.
            self._calibration_status = "skipped_silero"
            self._calibrated = True  # prevent re-entry
            log.info(
                "[VAD] auto-calibration skipped. Silero VAD active "
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

        # Set thresholds relative to noise floor, written through the
        self.silence_threshold_db = noise_db + 6.0  # 6 dB above noise -> silence
        self.speech_threshold_db = noise_db + 18.0  # 18 dB above noise -> speech
        self._calibrated = True
        self._calibration_status = "calibrated"

        # VAD auto-calibration runs every recording start (the dB thresholds
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
        noise_prob = float(np.median(self._calibration_prob_values))

        # silence = noise_floor + MARGIN
        silence = noise_prob + DEFAULT_VAD_SILERO_CALIBRATION_MARGIN
        # speech = silence + SPEECH_DELTA (the finding's "silence + 6dB"
        speech = silence + DEFAULT_VAD_SILERO_SPEECH_DELTA

        # Enforce a minimum spread + clamp to [0, 1].
        if speech - silence < MIN_VAD_SILERO_THRESHOLD_SPREAD:
            speech = silence + MIN_VAD_SILERO_THRESHOLD_SPREAD
        silence = max(0.0, min(1.0, silence))
        speech = max(0.0, min(1.0, speech))
        # Final guard: if clamping inverted the order (only possible
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
        self._calibration_prob_values = []
        self._speech_threshold = float(getattr(self._config, "vad_speech_threshold", DEFAULT_VAD_SPEECH_PROB_THRESHOLD))
        self._silence_threshold = float(
            getattr(self._config, "vad_silence_threshold", DEFAULT_VAD_SILENCE_PROB_THRESHOLD)
        )
        self._calibrated = False
        self._calibration_status = "pending"

        # reset Silero LSTM hidden state at session boundaries.
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
        chunk at the ~31 Hz chunk cadence of rate-scaled ~32 ms blocks ≈
        560 getattr/sec for a value that only changes
        when the user toggles a Settings UI switch). Now returns a
        cached value refreshed by ``on_config_changed()`` (the explicit
        hook) with a 5-second TTL safety net so a missed config-change
        notification cannot permanently wedge the cache.
        """
        cached = self._vad_enabled_cached
        if cached is not None:
            # Safety-net refresh: if the explicit on_config_changed()
            now = time.perf_counter()
            if now - self._vad_enabled_cache_ts >= self.VAD_ENABLED_CACHE_TTL_S:
                # (item 2): reassign the narrowed local ``cached``
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

        Note: ``use_silero_vad`` is intentionally NOT checked here, it controls
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

    # The 18 pure pass-through properties below are generated by the

    state = _make_vad_property("state")
    consecutive_speech_frames = _make_vad_property("consecutive_speech_frames")
    consecutive_silence_frames = _make_vad_property("consecutive_silence_frames")
    speech_frames = _make_vad_property("speech_frames")
    silence_frames = _make_vad_property("silence_frames")
    hangover_frames = _make_vad_property("hangover_frames")
    use_silero_vad = _make_vad_property("use_silero_vad")
    speech_threshold = _make_vad_property("speech_threshold")
    silence_threshold = _make_vad_property("silence_threshold")
    silero_available = _make_vad_property("silero_available")
    calibration_duration = _make_vad_property("calibration_duration")
    calibration_rms_values = _make_vad_property("calibration_rms_values")
    calibration_prob_values = _make_vad_property(
        "calibration_prob_values",
        doc=(
            "Silero-probability samples collected during the calibration "
            "window when ``vad_auto_calibrate`` is enabled. Read/write "
            "property for testability + inspection (mirrors "
            "``calibration_rms_values``)."
        ),
    )
    vad_auto_calibrate = _make_vad_property(
        "vad_auto_calibrate",
        doc=("Whether Silero-probability auto-calibration is enabled (``config.vad_auto_calibrate``, default False)."),
    )
    calibrated = _make_vad_property("calibrated")
    calibration_status = _make_vad_property(
        "calibration_status",
        doc=(
            "Explicit, inspectable reason for the current calibration state. "
            "Makes a no-op skip (Silero active / VAD disabled / no samples) "
            "explicit rather than a silent early-return. Values: "
            '"pending" (not yet run), "calibrated" (RMS-dB thresholds '
            'computed), "calibrated_silero" (Silero probability thresholds '
            'computed from observed noise floor), "skipped_silero" (Silero '
            'active, uses probability thresholds), "skipped_disabled" (VAD '
            'off), "skipped_no_samples" (calibration window elapsed with no '
            'RMS samples), "skipped_no_prob" (flag on but the caller did not '
            "pass ``vad_prob``)."
        ),
    )
    vad_enabled_cached = _make_vad_property("vad_enabled_cached")
    vad_enabled_cache_ts = _make_vad_property("vad_enabled_cache_ts")

    @property
    def speech_threshold_db(self) -> float:
        return self._speech_threshold_db

    @speech_threshold_db.setter
    def speech_threshold_db(self, value: float) -> None:
        # R18-F14: clamp to the speech threshold floor so a noisy
        self._speech_threshold_db = max(float(value), MIN_VAD_SPEECH_THRESHOLD_DB)

    @property
    def silence_threshold_db(self) -> float:
        return self._silence_threshold_db

    @silence_threshold_db.setter
    def silence_threshold_db(self, value: float) -> None:
        # R18-F14: clamp to the silence threshold floor.
        self._silence_threshold_db = max(float(value), MIN_VAD_SILENCE_THRESHOLD_DB)
