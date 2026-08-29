"""Real-time audio cleaning for the dictation pipeline.

ADR 0007: This module is now a thin wrapper around the
:class:`~voice_typer.server.audio_filters.FilterChain`. The monolithic
filter methods (high-pass, noise gate, RNNoise, peak normalizer) have
been moved to individual filter classes in
:mod:`voice_typer.server.audio_filters`. The duplicate AGC
(``_apply_normalization`` / ``_agc_update``) has been replaced by the
:class:`~voice_typer.server.audio_filters.Compressor` filter.

The filter chain is rebuilt on every config change via
:meth:`VoiceTyperApp._rebuild_audio_processor` (see ``service.py``),
so Settings UI changes take effect immediately in dictation -- no
restart required.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from math import gcd

# (PERF-COLDSTART-001): numpy is ~250-335ms cumulative on cold start
# and is NOT touched during ``VoiceTyperApp.__init__`` or ``start()`` — it
# is only needed on the first ``process_chunk`` call (>=1s after dictation
# begins). Defer the real import to first attribute access via the same
# ``lazy_module`` proxy already used for ``sounddevice`` and ``pystray``.
# The proxy re-resolves ``sys.modules`` on every access, so production
# ``np.array(...)`` calls and test ``monkeypatch.setattr(np, "array", ...)``
# both work unchanged. ``from __future__ import annotations`` above is
# REQUIRED so the ``np.ndarray`` annotations below stay as unevaluated
# strings (PEP 563); otherwise the module-level def of ``process_chunk``
# would resolve ``np.ndarray`` via the proxy and trigger the eager import
# we are trying to avoid.
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.audio_chain_builder import build_chain
from voice_typer.server.audio_filters import FilterChain
from voice_typer.server.audio_filters.noise_suppressor import _RNNOISE_FRAME_SIZE

np = lazy_module("numpy")

# CRIT-6: lazy-imported resampler to avoid pulling scipy into every
# test that constructs an AudioProcessor. The first ``process_chunk``
# call that actually needs to resample will import it. The
# ``_get_resample_poly`` and ``_get_resample_fir_taps`` helpers (with
# their import-error caching + retry-on-error logic) live in
# ``voice_typer.server.recording.resampling`` — importing them here at
# module top would pull numpy + scipy into every test that touches this
# module, so all three lookups (``_get_resample_poly``, ``upfirdn``,
# ``_get_resample_fir_taps``) are deferred to first use via the cached
# lazy helpers below. Mirrors the ``_get_lfilter`` pattern in
# ``audio_filters/base.py``.

# Cached lazy import of scipy.signal.upfirdn. The hot path is a single
# module-level variable lookup; the first call pays the (one-time)
# scipy import. If the import fails the error is cached and re-raised.
_upfirdn = None
_upfirdn_import_error: Exception | None = None


def _get_upfirdn():
    """Lazy import of scipy.signal.upfirdn.

    Caches the function reference after the first successful import so
    the RT-thread hot path (~16 Hz) pays only a module-level variable
    lookup. If the import fails, the error is cached and re-raised on
    every subsequent call so callers see consistent behavior.
    """
    global _upfirdn, _upfirdn_import_error
    if _upfirdn is not None:
        return _upfirdn
    if _upfirdn_import_error is not None:
        raise _upfirdn_import_error
    try:
        from scipy.signal import upfirdn as _uf

        _upfirdn = _uf
        return _uf
    except ImportError as exc:
        _upfirdn_import_error = exc
        raise


# Cached lazy lookup of ``_get_resample_fir_taps`` from
# ``voice_typer.server.recording.resampling``. The resampling module
# imports numpy at module top, so importing it eagerly here would
# defeat the cold-start lazy-numpy strategy. After the first call the
# function reference is cached, so the hot path is a single variable
# read.
_resample_fir_taps_fn = None


def _get_resample_fir_taps_fn():
    """Lazy accessor for ``_get_resample_fir_taps`` from resampling.py."""
    global _resample_fir_taps_fn
    if _resample_fir_taps_fn is None:
        from voice_typer.server.recording.resampling import _get_resample_fir_taps

        _resample_fir_taps_fn = _get_resample_fir_taps
    return _resample_fir_taps_fn


# Cached lazy lookup of ``_get_resample_poly`` from
# ``voice_typer.server.recording.resampling``. The resampling module's
# version centralizes the 5-minute retry-on-error caching and the
# import-error state — using it here (instead of a duplicate local
# copy) means a fix to one propagates to both call sites and there's
# exactly one import-error cache to inspect during cold-start
# profiling.
_resample_poly_fn = None


def _get_resample_poly_fn():
    """Lazy accessor for ``_get_resample_poly`` from resampling.py.

    Defers the resampling-module import (which transitively imports
    numpy) until the first ``process_chunk`` call that actually needs
    to resample. After the first call the function reference is cached,
    so the RT-thread hot path is a single variable lookup.
    """
    global _resample_poly_fn
    if _resample_poly_fn is None:
        from voice_typer.server.recording.resampling import _get_resample_poly

        _resample_poly_fn = _get_resample_poly
    return _resample_poly_fn


log = logging.getLogger(__name__)

QualityCallback = Callable[[float, float], None]

# every noise_filter_* / noise_suppression_* / audio_preset
# field that ``build_chain`` consults. Used to compute a stable
# signature so ``rebuild_from_config`` can short-circuit when nothing
# relevant changed. MUST be kept in sync with
# :func:`voice_typer.server.audio_chain_builder.build_chain`.
_CONFIG_SIGNATURE_FIELDS: tuple[str, ...] = (
    "audio_preset",
    "noise_filter_highpass",
    "noise_filter_highpass_cutoff_hz",
    "noise_suppression_method",
    "noise_filter_gate",
    "noise_filter_gate_open_threshold_db",
    "noise_filter_gate_close_threshold_db",
    "noise_filter_gate_attack_ms",
    "noise_filter_gate_hold_ms",
    "noise_filter_gate_release_ms",
    "noise_filter_eq",
    "noise_filter_eq_low_db",
    "noise_filter_eq_mid_db",
    "noise_filter_eq_high_db",
    "noise_filter_compressor",
    "noise_filter_compressor_threshold_db",
    "noise_filter_compressor_ratio",
    "noise_filter_compressor_attack_ms",
    "noise_filter_compressor_release_ms",
    "noise_filter_compressor_output_gain_db",
    "noise_filter_limiter",
    "noise_filter_limiter_ceiling_db",
    "noise_filter_limiter_release_ms",
    "noise_filter_notch",
    "noise_filter_notch_frequency_hz",
)


def _config_signature(config: object, sample_rate: int) -> tuple:
    """compute a stable signature tuple for ``config``.

    The signature includes every ``noise_filter_*`` /
    ``noise_suppression_method`` / ``audio_preset`` field the chain
    builder reads, plus the current sample rate (so a rate change
    invalidates the cache). Missing fields fall back to ``None`` (the
    chain builder's ``getattr(config, name, default)`` would then
    apply its own default -- the signature just needs to be stable,
    not exhaustive).
    """
    return (sample_rate,) + tuple(getattr(config, name, None) for name in _CONFIG_SIGNATURE_FIELDS)


class AudioProcessor:
    """Real-time audio cleaning via a filter chain.

    Wraps a :class:`FilterChain` built from the current config. The
    chain is rebuilt by :meth:`rebuild_from_config` when settings change.

    The processor is stateful: each filter in the chain maintains its
    own state (IIR filter state, envelope followers, gate openness)
    across ``process_chunk()`` calls. Call :meth:`reset` at the start
    of each recording session.

    Quality callback: :meth:`set_quality_callback` wires a per-chunk
    ``(rms, peak)`` callback for clipping/noise/SNR reporting (feeds
    :class:`~voice_typer.server.audio_quality.AudioQualityAnalyzer`).
    """

    def __init__(
        self,
        config: object,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        *,
        quiet: bool = False,
    ) -> None:
        # ``quiet`` suppresses the ``[AUDIO-CHAIN] Built chain`` /
        # ``[AUDIO-PROC] chain built`` INFO lines (and the
        # NoiseSuppressor backend-init lines). Used when the processor
        # is built for a SECONDARY consumer — the level-monitor
        # processor in ``update_level_processor`` — where the primary
        # dictation processor already logged the same build for the
        # same config. See ``build_chain(..., quiet=...)``.
        self._config = config
        self._sample_rate = int(sample_rate)
        self._chain: FilterChain = build_chain(config, sample_rate, quiet=quiet)
        self._quality_callback: QualityCallback | None = None
        # cache of the config signature that produced the current
        # chain. ``rebuild_from_config`` short-circuits when the new
        # config's signature matches -- avoids tearing down and rebuilding
        # the entire chain (including reloading RNNoise) on a no-op
        # ``apply_config_side_effects`` pass.
        self._config_signature: tuple = _config_signature(config, self._sample_rate)
        # track (input_sr, chain_sr) pairs already logged at
        # WARNING so a sustained resample fallback doesn't spam the log.
        self._resample_warned_pairs: set[tuple[int, int]] = set()
        # Latched flag set when the RT-thread resample fallback
        # path is taken (scipy missing OR resample_poly raises). When
        # set, the chain is being fed audio at the WRONG rate — every
        # IIR coefficient (high-pass cutoff, notch frequency, EQ
        # crossovers) and every ballistic time constant (compressor
        # attack/release) is mistuned. Previously this was invisible
        # to the UI (only a log WARNING); now ``is_degraded`` returns
        # True and ``degraded_reasons`` surfaces a clear message so
        # the user knows to call ``set_sample_rate`` or install scipy.
        # Cleared by ``reset`` (new session) and ``set_sample_rate``
        # (the corrective action — the chain is retuned to the input
        # rate, so the resample path is no longer taken).
        self._resample_degraded: bool = False
        self._resample_degraded_reason: str = ""
        # Zero-frame prewarm: run a short silence buffer through the
        # chain so stateful filters (RNNoise first-frame JIT/model
        # warmup, streaming resampler FIR state) initialize BEFORE the
        # first real audio chunk arrives. Without this, the first
        # RNNoise frame returns ``None`` (buffering until 480 samples
        # at 48 kHz are collected) and ``FilterChain.process``
        # propagates the ``None`` — ``process_chunk`` then falls back
        # to the unfiltered input, so the first 1-3 words of every
        # session bypass the gate / EQ / compressor / limiter
        # downstream of the suppressor. The prewarm feeds exactly one
        # RNNoise frame (480 samples) of silence at the chain's
        # sample rate; at 16 kHz this resamples to 3 RNNoise frames,
        # at 48 kHz to 1 frame — either way the carry buffer is left
        # empty (clean state) and the first ``denoise_frame`` call
        # has already happened. The output is discarded. Safe to call
        # multiple times (idempotent). Wrapped in try/except so a
        # prewarm failure (e.g. degraded backend) does not break
        # construction — the chain still works, just without the
        # warmup benefit.
        self._prewarm_chain()
        if not quiet:
            log.info(
                "[AUDIO-PROC] chain built: %s (latency=%.1fms, degraded=%s)",
                self._chain.filter_names or "none",
                self._chain.total_latency_ms,
                self._chain.is_degraded,
            )

    def _prewarm_chain(self) -> None:
        """Feed one RNNoise frame of silence through the chain.

        Initializes stateful filters (RNNoise backend, streaming
        resamplers) so the first real audio chunk does not pay the
        first-frame warmup cost on the RT thread. See the
        ``__init__`` docstring for the full rationale. The output is
        discarded — only the side effect (filter state
        initialization) matters.

        Only runs when the chain actually contains a
        ``NoiseSuppressor`` — that's the only filter that buffers
        (returns ``None`` until a full 480-sample frame is
        collected) and benefits from first-frame warmup. Other
        filters (HighPass, Gate, EQ, Compressor, Limiter) process
        sample-by-sample and don't need prewarming. Skipping the
        prewarm for chains without a NoiseSuppressor also avoids
        forcing the numpy import for the all-filters-off
        (``PRESET_OFF``) edge case where no filter would otherwise
        touch numpy during construction.
        """
        try:
            names = self._chain.filter_names
            if not any("NoiseSuppressor" in n for n in names):
                return
            silence = np.zeros(_RNNOISE_FRAME_SIZE, dtype=np.float32)
            self._chain.process(silence, self._sample_rate)
        except Exception:
            log.debug(
                "[AUDIO-PROC] prewarm failed (non-fatal — chain still usable)",
                exc_info=True,
            )

    # ── Lifecycle ───────────────────────────────────────────────────

    def rebuild_from_config(self, config: object) -> None:
        """Rebuild the filter chain from a new config.

        short-circuits when the new config's signature (the
                relevant ``noise_filter_*`` / ``noise_suppression_method`` /
                ``audio_preset`` field values plus the current sample rate)
                matches the previously-built chain's signature. Avoids tearing
                down the entire chain (including reloading the RNNoise model
                and re-running ``scipy.signal.butter``) on a no-op config
                reload -- the controller calls this on every
                ``apply_config_side_effects`` pass, and config writes that
                don't touch audio fields would otherwise pay the full rebuild
                cost. The chain object identity is preserved either way.
        """
        new_sig = _config_signature(config, self._sample_rate)
        if new_sig == self._config_signature:
            # No-op rebuild -- keep the existing chain (with its live
            # filter state) intact. Still record the latest config
            # reference so subsequent set_sample_rate calls see the
            # newest config object.
            self._config = config
            log.debug("[AUDIO-PROC] rebuild skipped -- config signature unchanged")
            return
        self._config = config
        self._config_signature = new_sig
        new_chain = build_chain(config, self._sample_rate)
        # ``filters`` is FilterChain's sanctioned public accessor (a
        # lock-consistent snapshot); ``swap`` copies the list again, so
        # this is identical to handing it the private list directly.
        self._chain.swap(new_chain.filters)
        log.info(
            "[AUDIO-PROC] chain rebuilt: %s (degraded=%s)",
            self._chain.filter_names or "none",
            self._chain.is_degraded,
        )

    def reset(self) -> None:
        """Reset all filter states for a new recording session."""
        self._chain.reset()
        # Clear the latched resample-degraded flag — a new
        # recording session starts with a clean slate. If the resample
        # path fails again, the flag will be re-set on the next chunk.
        self._resample_degraded = False
        self._resample_degraded_reason = ""
        self._resample_warned_pairs.clear()

    def set_sample_rate(self, sr: int) -> None:
        """Update the chain's sample rate and rebuild the filter chain.

        (CRITICAL) /  (High) /  (Medium): called by
                :meth:`AudioQualityController._rebuild_audio_processor` when
                ``force_sr`` is provided (e.g. on hot-plug or when the recorder
                resolves a new ``candidate_sr`` that differs from
                ``config.sample_rate``).

                Without this, all filter coefficients stay tuned to the original
                sample rate and a hot-plugged device at a different native rate
                silently mistunes the entire chain: an 80 Hz high-pass built at
                16 kHz actually cuts at 240 Hz when fed 48 kHz audio (removing
                male speech fundamentals); notch frequencies, EQ crossovers,
                and compressor attack/release ballistics all drift in lockstep.

        explicitly rebuilds (does NOT go through
                ``rebuild_from_config``'s short-circuit) because a rate change
                always invalidates the cached signature, and we want a single
                rebuild -- not a skip followed by a redundant rebuild on the
                next ``rebuild_from_config`` call.

                Clears the latched resample-degraded flag because the
                chain is now retuned to the new rate — the corrective action
                for a resample fallback has been taken.

                Args:
                    sr: new sample rate in Hz.
        """
        new_sr = int(sr)
        self._sample_rate = new_sr
        self._config_signature = _config_signature(self._config, new_sr)
        new_chain = build_chain(self._config, new_sr)
        # Sanctioned public accessor — see ``rebuild_from_config``.
        self._chain.swap(new_chain.filters)
        # The chain is now tuned to ``new_sr``; if the next chunk
        # arrives at ``new_sr``, the resample path is not taken. Clear
        # the latched flag so the UI stops showing the resample warning.
        # (If chunks keep arriving at a different rate, the flag will
        # be re-set on the next failed resample.)
        self._resample_degraded = False
        self._resample_degraded_reason = ""
        self._resample_warned_pairs.clear()
        log.info(
            "[AUDIO-PROC] chain rebuilt on rate change: %s (sr=%d, degraded=%s)",
            self._chain.filter_names or "none",
            new_sr,
            self._chain.is_degraded,
        )

    def set_quality_callback(self, cb: QualityCallback) -> None:
        """Wire a quality detector callback."""
        self._quality_callback = cb

    def set_filter_enabled(self, name: str, enabled: bool) -> bool:
        """Toggle a filter's ``enabled`` flag at runtime (no chain rebuild).

        Thin wrapper around :meth:`FilterChain.set_filter_enabled`. The
        IPC handler layer can call this to toggle a filter without a
        full config reload — useful for A/B comparisons and
        "temporarily bypass RNNoise" controls. Toggling preserves
        filter state (IIR zi, envelope follower, gate openness)
        across the bypass window, so a momentarily-disabled filter
        re-engages with its prior state intact (no transient click
        from a cold IIR re-initialization).

        NOTE: the IPC command that exposes this to the renderer is
        NOT wired in this change — the IPC handler files are owned by
        a different sub-agent. This method (plus
        :func:`voice_typer.server.audio_chain_builder.set_filter_enabled`
        and :meth:`FilterChain.set_filter_enabled`) is the
        server-side API surface; the IPC handler can call this
        directly with the active AudioProcessor.

        Args:
            name: filter display name (e.g. ``"HighPass(80Hz)"``,
                ``"NoiseSuppressor(rnnoise)"``, ``"Compressor"``).
                Matches ``filter.name`` on each filter in the chain.
            enabled: True to enable, False to bypass.

        Returns:
            True if at least one filter matched ``name`` and was
            toggled, False otherwise (so callers can detect a no-op
            / typo).
        """
        return self._chain.set_filter_enabled(name, enabled)

    # ── Real-time processing (called from the audio worker thread) ───

    def process_chunk(self, chunk: np.ndarray, input_sample_rate: int | None = None) -> np.ndarray | None:
        """Apply the filter chain to a single audio chunk.

                Returns the filtered chunk (same shape/dtype). If the chain
                returns ``None`` because a downstream filter is buffering (e.g.
                RNNoise needs a full 480-sample frame), the ORIGINAL unfiltered
                chunk is returned so callers that don't propagate ``None``
                (e.g. ``level_monitor``'s live level bar, and the recording
                callback's RMS / silence-detection path) keep running without
                special-casing.

                **Must be non-blocking.** Only pre-allocated buffers and fast
                numpy/scipy operations are used.

        CRIT-6: if ``input_sample_rate`` is provided
                and differs from the chain's construction sample rate, the
                chunk is resampled to the chain's rate before processing.
        """
        # wrap the entire RT-thread body in a try/except so a
        # transient numpy/scipy error or a buggy filter does NOT crash
        # the PortAudio recorder thread (which would silently kill all
        # future audio capture for the session). The original chunk is
        # returned as a passthrough fallback -- the user hears a brief
        # unfiltered glitch instead of losing the recording entirely.
        try:
            return self._process_chunk_impl(chunk, input_sample_rate)
        except Exception:
            log.exception(
                "[AUDIO-PROC] process_chunk raised on RT thread -- returning original chunk unfiltered (input_sr=%s)",
                input_sample_rate,
            )
            try:
                if chunk.dtype != np.float32:
                    return chunk.astype(np.float32)
                return chunk
            except Exception:
                return chunk

    def _process_chunk_impl(self, chunk: np.ndarray, input_sample_rate: int | None) -> np.ndarray | None:
        """Actual chunk processing -- see :meth:`process_chunk` for the contract."""
        if chunk.size == 0:
            return chunk

        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        # CRIT-6: resample to the chain's rate if the
        # input rate differs.  Filters were built at ``self._sample_rate``
        # (16 kHz) -- feeding them audio at a different rate silently
        # mistunes every coefficient (high-pass, notch, EQ crossovers,
        # compressor attack/release).
        if input_sample_rate is not None and int(input_sample_rate) != self._sample_rate:
            # resample_poly allocates per call and runs on the RT
            # thread. The correct long-term fix is for callers (the
            # recorder) to invoke ``set_sample_rate`` with the device's
            # native rate so this branch is never taken ( mitigation).
            # When the rates do differ, we still need to resample here to
            # avoid silently mistuning every filter coefficient. Bump the
            # log level for the fallback path so operators see the
            # mistune ( -- was DEBUG, invisible in default logs).
            try:
                # Use the shared ``_get_resample_poly`` from
                # ``recording.resampling`` (with 5-minute retry-on-error
                # caching) instead of a duplicate local copy. See
                # ``recording/resampling.py`` for the rationale.
                resample_poly = _get_resample_poly_fn()()
                # scipy.signal.resample_poly uses integer up/down ratios.
                # Compute the greatest common divisor to keep the ratio
                # in reduced form (smaller FFT sizes, faster). ``gcd``
                # is hoisted to the module top — per-call ``from math
                # import gcd`` was ~48 dict lookups/sec on the RT
                # thread; the top-level import is a single bytecode
                # LOAD_GLOBAL at call time.
                up = self._sample_rate
                down = int(input_sample_rate)
                g = gcd(up, down)
                up //= g
                down //= g
                # use cached FIR taps + upfirdn instead of
                # resample_poly re-designing the filter on every call.
                # Both ``upfirdn`` and ``_get_resample_fir_taps`` are
                # resolved through cached lazy helpers (``_get_upfirdn``
                # / ``_get_resample_fir_taps_fn``) so the hot path is a
                # single variable lookup — no per-call ``from ... import``
                # statements on the RT thread.
                try:
                    taps = _get_resample_fir_taps_fn()(up, down)
                    chunk = _get_upfirdn()(taps, chunk, up=up, down=down).astype(np.float32, copy=False)
                except Exception:
                    chunk = resample_poly(chunk, up, down).astype(np.float32, copy=False)
                # one-shot WARNING (rate-limited) so operators can
                # spot devices that haven't been routed through
                # ``set_sample_rate``. Subsequent calls at the same
                # (input_sr, chain_sr) pair are logged at DEBUG to avoid
                # log spam -- the warning is informational, not an error.
                self._log_resample_once(int(input_sample_rate))
            except Exception:
                # Fall back to the original chunk -- better to filter at
                # the wrong rate than to drop the chunk entirely.
                log.warning(
                    "[AUDIO-PROC] resample failed (input_sr=%d, chain_sr=%d); "
                    "filtering at wrong rate -- call set_sample_rate to retune",
                    input_sample_rate,
                    self._sample_rate,
                    exc_info=True,
                )
                self._resample_warned_pairs.add((int(input_sample_rate), int(self._sample_rate)))
                # Latch the resample-degraded flag so the UI can
                # surface a warning. Without this, the user has NO signal
                # that their high-pass / notch / EQ / compressor are all
                # mistuned (an 80 Hz high-pass built at 16 kHz actually
                # cuts at 240 Hz when fed 48 kHz audio). Cleared by
                # ``reset`` (new session) or ``set_sample_rate`` (the
                # corrective action — retune the chain to the input rate).
                if not self._resample_degraded:
                    self._resample_degraded = True
                    self._resample_degraded_reason = (
                        f"resample failed (input_sr={int(input_sample_rate)}, "
                        f"chain_sr={self._sample_rate}) — filtering at wrong rate; "
                        "call set_sample_rate(input_sr) to retune"
                    )

        # run the quality check on the PRE-filter chunk (the
        # resampled input). The default Limiter (ceiling_db=-6.0 ~ 0.50
        # linear) clamps every sample's envelope to <=0.50, so the
        # post-filter peak fed to the clipping detector (threshold 0.99)
        # could never reach 0.99 when the limiter was active -- users were
        # never warned about mic clipping as long as the limiter was ON
        # (the default). Running on the pre-filter audio lets the
        # clipping detector see the actual input peaks. RMS for low-volume
        # detection is also more accurate on pre-filter audio (the gate
        # would otherwise suppress low-level input). The pre-filter
        # ``chunk`` here is the resampled input -- i.e. the audio the user
        # actually fed in, at the chain's sample rate.
        if self._quality_callback is not None:
            self._run_quality_check(chunk)

        result = self._chain.process(chunk, self._sample_rate)

        # Option B (E.1 fix, Round 0 forward-port): propagate the filtered
        # result when present, otherwise fall back to the original chunk.
        # True ``None`` propagation would break level_monitor (out of this
        # module's scope) and the recording callback's downstream RMS /
        # silence math, which assume a concrete ndarray. Documented here so
        # future callers know the contract.
        return result if result is not None else chunk

    def _run_quality_check(self, chunk: np.ndarray) -> None:
        """Compute lightweight quality metrics and fire the callback.

        Uses allocation-free reductions: np.max/min for peak (no np.abs
        allocation) and np.dot for RMS (no np.square allocation).
        """
        if chunk.size == 0:
            return
        flat = chunk.ravel()
        peak = max(float(flat.max()), -float(flat.min()))
        rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
        try:
            if self._quality_callback is not None:
                self._quality_callback(rms, peak)
        except Exception:
            log.debug("[AUDIO-PROC] quality callback raised", exc_info=True)

    def _log_resample_once(self, input_sr: int) -> None:
        """log the resample fallback at WARNING once per (input_sr, chain_sr) pair.

        The resample path on the RT thread is acceptable when the device
        rate genuinely differs from the chain rate (it keeps audio
        flowing), but it indicates the recorder hasn't called
        ``set_sample_rate`` to retune the chain. A single WARNING per
        pair surfaces the mistune in default logs without spamming on
        every chunk (~16 Hz).
        """
        pair = (int(input_sr), int(self._sample_rate))
        if pair in self._resample_warned_pairs:
            log.debug(
                "[AUDIO-PROC] resample ongoing (input_sr=%d, chain_sr=%d)",
                input_sr,
                self._sample_rate,
            )
            return
        self._resample_warned_pairs.add(pair)
        log.warning(
            "[AUDIO-PROC] resampling on RT thread (input_sr=%d, chain_sr=%d) -- "
            "call set_sample_rate(input_sr) to retune the chain and skip resampling",
            input_sr,
            self._sample_rate,
        )

    # ── Introspection ───────────────────────────────────────────────

    @property
    def sample_rate(self) -> int:
        """The chain's current sample rate (Hz).

        updated by :meth:`set_sample_rate` (e.g. on hot-plug)
                so callers (and tests) can read the effective rate the chain
                is currently tuned to. Reads return the same value as
                ``self._sample_rate``.
        """
        return self._sample_rate

    @property
    def chain(self) -> FilterChain:
        """The current filter chain."""
        return self._chain

    @property
    def filter_names(self) -> list[str]:
        """Display names of active filters."""
        return self._chain.filter_names

    @property
    def is_degraded(self) -> bool:
        """True if any filter is in degraded mode (missing library, etc.).

        Also True when the RT-thread resample fallback path has
        been taken (scipy missing OR resample_poly raised). In that
        state every IIR coefficient and ballistic time constant is
        mistuned because the chain is being fed audio at the wrong
        rate — the UI must surface a warning so the user knows to
        call ``set_sample_rate`` or install scipy.
        """
        return self._chain.is_degraded or self._resample_degraded

    @property
    def degraded_reasons(self) -> list[str]:
        """List of degradation reasons from all filters and the resample fallback."""
        reasons = list(self._chain.degraded_reasons)
        # Append the resample-degraded reason LAST so the UI
        # shows it after per-filter reasons (the resample issue is
        # processor-level, not filter-level, so it reads naturally
        # at the end of the list).
        if self._resample_degraded and self._resample_degraded_reason:
            reasons.append(self._resample_degraded_reason)
        return reasons

    @property
    def total_latency_ms(self) -> float:
        """Total added latency from all filters."""
        return self._chain.total_latency_ms
