"""Noise suppressor backend."""

from __future__ import annotations

import logging
import math

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE, WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import AudioFilter, _get_lfilter  # noqa: E402

log = logging.getLogger(__name__)

# Once-per-process latch for backend-ready INFO lines. Each fresh
_backend_ready_logged: set[str] = set()


def _log_backend_ready_once(key: str, msg: str, *args: object) -> None:
    """Log a backend-ready line once at INFO, repeats at DEBUG."""
    if key in _backend_ready_logged:
        log.debug(msg + " (repeat, already logged)", *args)
        return
    _backend_ready_logged.add(key)
    log.info(msg, *args)


# RNNoise requires 48kHz, 480-sample frames (10ms at 48kHz).
_RNNOISE_FRAME_SIZE: int = 480

# GTCRN (the bundled ONNX streaming model) is native 16 kHz and
_GTCRN_HOP_SIZE: int = 256

# float32 -> int16 conversion constants. ``_FLOAT_TO_INT16_MAX`` is the
_FLOAT_TO_INT16_MAX: float = float(2**15 - 1)  # 32767.0
_INT16_SCALE: float = _FLOAT_TO_INT16_MAX


def _resampler_fir_num_taps(up: int, down: int) -> int:
    """FIR length used by :class:`_StreamingResampler` for an ``up``/``down`` ratio."""
    rate_gcd = math.gcd(up, down)
    up_reduced = up // rate_gcd
    down_reduced = down // rate_gcd
    taps = 10 * max(up_reduced, down_reduced) + 1
    if taps % 2 == 0:
        taps += 1
    return taps


def _resampler_group_delay_ms(up: int, down: int, input_rate: int) -> float:
    """Group delay (ms) of one :class:`_StreamingResampler` FIR stage."""
    delay_samples = (_resampler_fir_num_taps(up, down) - 1) / 2
    intermediate_rate = input_rate * up
    return delay_samples / intermediate_rate * 1000.0


class _StreamingResampler:
    """Streaming polyphase resampler ( / )."""

    def __init__(self, up: int, down: int) -> None:
        if up <= 0:
            raise ValueError(f"up must be > 0, got {up!r}")
        if down <= 0:
            raise ValueError(f"down must be > 0, got {down!r}")
        self._up = int(up)
        self._down = int(down)

        # Design the FIR filter ONCE at construction (). Cutoff is the
        from scipy.signal import firwin

        rate_gcd = math.gcd(self._up, self._down)
        up_reduced = self._up // rate_gcd
        down_reduced = self._down // rate_gcd
        cutoff = 1.0 / max(up_reduced, down_reduced)
        num_taps = _resampler_fir_num_taps(self._up, self._down)
        self._h = np.asarray(firwin(num_taps, cutoff, fs=2.0), dtype=np.float64)

        # Persistent lfilter state (len = len(h) - 1 for an FIR filter).
        self._zi: np.ndarray = np.zeros(max(len(self._h) - 1, 0), dtype=np.float64)

        # Counters and phase for the output-length invariant () and for
        self._in_total: int = 0
        self._out_total: int = 0
        self._phase: int = 0
        # pre-allocated upsample buffer (zero-filled, reused across
        self._x_up_buf: np.ndarray | None = None

    def process(self, x: np.ndarray) -> np.ndarray:
        """Resample ``x`` (float32/float64) by ``up / down``."""
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        x64 = np.asarray(x, dtype=np.float64)
        n_in = x64.size
        up = self._up
        down = self._down

        # Upsample: insert up-1 zeros between samples.
        up_len = n_in * up
        if self._x_up_buf is None or self._x_up_buf.shape[0] < up_len:
            self._x_up_buf = np.zeros(max(up_len, 1024), dtype=np.float64)
        x_up = self._x_up_buf[:up_len]
        # Only the stride slots are ever written (``x_up[::up] = x64``);
        x_up[::up] = x64

        # Apply FIR filter with persistent state.
        y, self._zi = _get_lfilter()(self._h, [1.0], x_up, zi=self._zi)

        # Downsample: pick every `down`-th sample starting at current phase.
        m = y.size  # == n_in * up
        idx = np.arange(self._phase, m, down)
        out = y[idx]

        # Advance phase: the next chunk's first downsample sample is offset by
        self._phase = (self._phase + m) % down

        self._in_total += n_in
        self._out_total += out.size
        return out.astype(np.float32, copy=False)

    def reset(self) -> None:
        """Clear all internal state (zeros ``_zi`` in place)."""
        #  (mirrored from NoiseSuppressor.reset): zero the existing
        if self._zi.size > 0:
            self._zi.fill(0)
        self._in_total = 0
        self._out_total = 0
        self._phase = 0
        # Zero the pre-allocated upsample working buffer so the
        if self._x_up_buf is not None:
            self._x_up_buf.fill(0)


class NoiseSuppressor(AudioFilter):
    """Neural noise suppression with multiple backends."""

    def __init__(
        self,
        method: str = "rnnoise",
        sample_rate: int = WHISPER_SAMPLE_RATE,
        quiet: bool = False,
    ) -> None:
        self.name = f"NoiseSuppressor({method})"
        self._method = method
        self._source_sample_rate = int(sample_rate)
        self._backend: object | None = None
        self._degraded: bool = False
        self._degraded_reason: str = ""
        # ``quiet`` suppresses the backend-init INFO/WARNING lines
        self._quiet = quiet

        # Frame buffering: carry holds partial frames between process() calls.
        self._carry: np.ndarray = np.array([], dtype=np.float32)

        # pre-allocated per-frame conversion buffers for the RNNoise
        self._frame_f32_buf: np.ndarray = np.zeros(_RNNOISE_FRAME_SIZE, dtype=np.float32)
        self._frame_i16_buf: np.ndarray = np.zeros(_RNNOISE_FRAME_SIZE, dtype=np.int16)
        # pre-allocated float64 output buffer for the concatenated RNNoise
        self._result_48k_buf: np.ndarray | None = None
        # pre-allocated float32 padding buffer for the length-match path
        self._padded_buf: np.ndarray | None = None

        # streaming resamplers created lazily by
        self._upsampler: _StreamingResampler | None = None
        self._downsampler: _StreamingResampler | None = None
        self._resampler_rate: int | None = None

        # GTCRN is native 16 kHz (not 48 kHz like RNNoise), so it gets
        self._gtcrn_upsampler: _StreamingResampler | None = None
        self._gtcrn_downsampler: _StreamingResampler | None = None
        self._gtcrn_resampler_rate: int | None = None
        # pre-allocated float32 output buffer for the concatenated GTCRN
        self._result_16k_buf: np.ndarray | None = None

        self._init_backend()

    def _init_backend(self) -> None:
        """Initialize the selected backend. Fall back to 'none' on failure."""
        method = self._method
        if method == "none":
            self._backend = None
            return

        if method == "rnnoise":
            self._init_rnnoise()
        elif method == "gtcrn":
            self._init_gtcrn()
        elif method == "deepfilternet":
            # Legacy config value: the DeepFilterNet backend was replaced
            if not self._quiet:
                log.info("[NOISE-SUPPRESS] legacy method 'deepfilternet', using gtcrn")
            self._method = "gtcrn"
            self._init_gtcrn()
        else:
            if not self._quiet:
                log.warning("[NOISE-SUPPRESS] unknown method %r, using none", method)
            self._method = "none"
            self._backend = None

    def _init_rnnoise(self) -> None:
        try:
            from pyrnnoise import RNNoise

            self._backend = RNNoise(sample_rate=RNNOISE_SAMPLE_RATE)
            if not self._quiet:
                _log_backend_ready_once("rnnoise", "[NOISE-SUPPRESS] RNNoise backend ready")
        except ImportError:
            if not self._quiet:
                log.warning(
                    "[NOISE-SUPPRESS] pyrnnoise not installed, falling back to none. "
                    "Install with: pip install pyrnnoise"
                )
            self._degraded = True
            self._degraded_reason = "rnnoise library not installed"
            self._method = "none"
        except Exception as exc:
            if not self._quiet:
                log.warning("[NOISE-SUPPRESS] RNNoise init failed: %s, falling back to none", exc)
            self._degraded = True
            self._degraded_reason = f"rnnoise init failed: {exc}"
            self._method = "none"

    def _init_gtcrn(self) -> None:
        """Initialize the GTCRN backend (bundled ONNX streaming model)."""
        try:
            from voice_typer.server.audio_filters.gtcrn_backend import GtcrnBackend

            self._backend = GtcrnBackend()
        except Exception as exc:
            if not self._quiet:
                log.warning("[NOISE-SUPPRESS] GTCRN init failed: %s, falling back to rnnoise", exc)
            # Mark degraded BEFORE calling _init_rnnoise so the flag is
            self._degraded = True
            gtcrn_reason = f"gtcrn init failed: {exc}, falling back to rnnoise"
            self._degraded_reason = gtcrn_reason

            # Switch to rnnoise at init time so process() uses the
            self._method = "rnnoise"
            self._init_rnnoise()

            if self._method == "none":
                # _init_rnnoise overwrote _degraded_reason with the
                rnnoise_reason = self._degraded_reason
                self._degraded_reason = f"{gtcrn_reason}; rnnoise fallback also unavailable: {rnnoise_reason}"
            else:
                # _init_rnnoise succeeded, restore gtcrn_reason as the
                self._degraded = True
                self._degraded_reason = gtcrn_reason
        else:
            if not self._quiet:
                _log_backend_ready_once("gtcrn", "[NOISE-SUPPRESS] GTCRN backend ready (bundled ONNX streaming model)")

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if self._method == "none" or self._backend is None or audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)

        if self._method == "rnnoise":
            return self._process_rnnoise(samples, sample_rate, original_shape)
        if self._method == "gtcrn":
            return self._process_gtcrn(samples, sample_rate, original_shape)
        #  defensive guard: ``_init_backend`` is supposed to
        if not self._degraded:
            self._degraded = True
            self._degraded_reason = f"{self._method} backend not yet implemented, falling back to rnnoise"
            log.warning(
                "[AUDIO] NoiseSuppressor: %s backend not yet wired; "
                "falling back to rnnoise for neural noise suppression",
                self._method,
            )
        try:
            self._method = "rnnoise"
            # The defensive ``_backend is None or not _backend.get(...)``
            if self._backend is None or (hasattr(self._backend, "get") and not self._backend.get("rnnoise")):
                self._init_rnnoise()
            return self._process_rnnoise(samples, sample_rate, original_shape)
        except Exception:
            # If rnnoise also fails, return the original audio (last resort).
            return audio.reshape(original_shape)

    def _ensure_resamplers(self, sample_rate: int) -> None:
        """Lazily create (or recreate) the streaming resamplers for ``sample_rate``."""
        if sample_rate == RNNOISE_SAMPLE_RATE:
            if self._resampler_rate != sample_rate:
                self._upsampler = None
                self._downsampler = None
                self._resampler_rate = sample_rate
            return
        if self._resampler_rate == sample_rate and self._upsampler is not None:
            return  # already configured for this rate
        gcd = math.gcd(RNNOISE_SAMPLE_RATE, int(sample_rate))
        up = RNNOISE_SAMPLE_RATE // gcd
        down = int(sample_rate) // gcd
        self._upsampler = _StreamingResampler(up, down)
        self._downsampler = _StreamingResampler(down, up)
        self._resampler_rate = int(sample_rate)

    def _process_rnnoise(
        self,
        samples: np.ndarray,
        sample_rate: int,
        original_shape: tuple,
    ) -> np.ndarray | None:
        """Process through RNNoise with frame buffering."""
        # ``# type: ignore[union-attr]`` suppressions. The process()
        assert self._backend is not None

        #  ensure streaming resamplers exist for this rate.
        self._ensure_resamplers(sample_rate)

        # Resample to 48kHz if needed (using streaming resamplers).
        up = self._upsampler.process(samples) if self._upsampler is not None else samples

        # Prepend carry from previous call
        combined = np.concatenate([self._carry, up])
        n_full = len(combined) // _RNNOISE_FRAME_SIZE
        remainder = len(combined) - n_full * _RNNOISE_FRAME_SIZE

        if n_full == 0:
            # Not enough for a full frame, buffer it
            self._carry = combined
            return None  # signal caller to skip this chunk

        # Set channel info once for pyrnnoise (mono).
        self._backend.channels = 1

        # pre-allocate the float64 output buffer for the concatenated
        total_out = n_full * _RNNOISE_FRAME_SIZE
        if self._result_48k_buf is None or self._result_48k_buf.shape[0] < total_out:
            self._result_48k_buf = np.empty(max(total_out, 1024), dtype=np.float64)
        result_48k = self._result_48k_buf[:total_out]

        for i in range(n_full):
            start = i * _RNNOISE_FRAME_SIZE
            frame = combined[start : start + _RNNOISE_FRAME_SIZE]
            out_slice = result_48k[i * _RNNOISE_FRAME_SIZE : (i + 1) * _RNNOISE_FRAME_SIZE]
            try:
                # clip float32 input to [-1, 1] BEFORE scaling to int16
                frame_f32 = self._frame_f32_buf
                np.clip(frame, -1.0, 1.0, out=frame_f32)
                np.multiply(frame_f32, _INT16_SCALE, out=frame_f32)
                frame_i16 = self._frame_i16_buf
                frame_i16[:] = frame_f32.astype(np.int16)
                # pyrnnoise expects [num_channels, 480]; returns (speech_prob, cleaned) as int16
                _, cleaned_i16 = self._backend.denoise_frame(frame_i16[np.newaxis, :])
                # write the cleaned frame directly into the result buffer
                np.copyto(out_slice, cleaned_i16[0], casting="same_kind")
                np.divide(out_slice, _FLOAT_TO_INT16_MAX, out=out_slice)
            except Exception as exc:
                log.debug("[NOISE-SUPPRESS] RNNoise frame failed: %s", exc)
                # fall back to the original frame (upcast float32 -> float64
                np.copyto(out_slice, frame, casting="same_kind")

        # Save remainder for next call
        if remainder > 0:
            self._carry = combined[n_full * _RNNOISE_FRAME_SIZE :]
        else:
            self._carry = np.array([], dtype=np.float32)

        # Resample back to source rate (using streaming resamplers).
        result = self._downsampler.process(result_48k) if self._downsampler is not None else result_48k

        # The resampling may produce slightly different length than input.
        target_len = len(samples)
        if len(result) >= target_len:
            result = result[:target_len]
        else:
            # pre-allocated padding buffer (lazy-resized). The tail beyond
            if self._padded_buf is None or self._padded_buf.shape[0] < target_len:
                self._padded_buf = np.zeros(max(target_len, 1024), dtype=np.float32)
            padded = self._padded_buf[:target_len]
            padded[: len(result)] = result
            padded[len(result) :] = 0.0
            result = padded

        return result.astype(np.float32, copy=False).reshape(original_shape)

    def _ensure_gtcrn_resamplers(self, sample_rate: int) -> None:
        """Lazily create (or recreate) the GTCRN streaming resamplers."""
        if sample_rate == WHISPER_SAMPLE_RATE:
            if self._gtcrn_resampler_rate != sample_rate:
                self._gtcrn_upsampler = None
                self._gtcrn_downsampler = None
                self._gtcrn_resampler_rate = sample_rate
            return
        if self._gtcrn_resampler_rate == sample_rate and self._gtcrn_upsampler is not None:
            return  # already configured for this rate
        gcd = math.gcd(WHISPER_SAMPLE_RATE, int(sample_rate))
        up = WHISPER_SAMPLE_RATE // gcd
        down = int(sample_rate) // gcd
        self._gtcrn_upsampler = _StreamingResampler(up, down)
        self._gtcrn_downsampler = _StreamingResampler(down, up)
        self._gtcrn_resampler_rate = int(sample_rate)

    def _process_gtcrn(
        self,
        samples: np.ndarray,
        sample_rate: int,
        original_shape: tuple,
    ) -> np.ndarray | None:
        """Process through the GTCRN streaming model with hop buffering."""
        assert self._backend is not None

        self._ensure_gtcrn_resamplers(sample_rate)

        # Resample to the model's native 16 kHz if needed.
        up = self._gtcrn_upsampler.process(samples) if self._gtcrn_upsampler is not None else samples

        # Prepend carry from previous call and slice into full hops.
        combined = np.concatenate([self._carry, up])
        n_full = len(combined) // _GTCRN_HOP_SIZE
        remainder = len(combined) - n_full * _GTCRN_HOP_SIZE

        if n_full == 0:
            # Not enough for a full hop, buffer it.
            self._carry = combined
            return None  # signal caller to skip this chunk

        # pre-allocated float32 output buffer: each hop's enhanced
        total_out = n_full * _GTCRN_HOP_SIZE
        if self._result_16k_buf is None or self._result_16k_buf.shape[0] < total_out:
            self._result_16k_buf = np.empty(max(total_out, 1024), dtype=np.float32)
        result_16k = self._result_16k_buf[:total_out]

        for i in range(n_full):
            start = i * _GTCRN_HOP_SIZE
            hop = combined[start : start + _GTCRN_HOP_SIZE]
            out_slice = result_16k[i * _GTCRN_HOP_SIZE : (i + 1) * _GTCRN_HOP_SIZE]
            try:
                enhanced, _ = self._backend.process_hop(hop)
                np.copyto(out_slice, enhanced)
            except Exception as exc:
                # A single failed hop falls back to the ORIGINAL hop
                log.debug("[NOISE-SUPPRESS] GTCRN hop failed: %s", exc)
                np.copyto(out_slice, hop)

        # Save remainder for next call.
        if remainder > 0:
            self._carry = combined[n_full * _GTCRN_HOP_SIZE :]
        else:
            self._carry = np.array([], dtype=np.float32)

        # Resample back to the source rate if needed.
        result = self._gtcrn_downsampler.process(result_16k) if self._gtcrn_downsampler is not None else result_16k

        # The resampling may produce slightly different length than
        target_len = len(samples)
        if len(result) >= target_len:
            result = result[:target_len]
        else:
            if self._padded_buf is None or self._padded_buf.shape[0] < target_len:
                self._padded_buf = np.zeros(max(target_len, 1024), dtype=np.float32)
            padded = self._padded_buf[:target_len]
            padded[: len(result)] = result
            padded[len(result) :] = 0.0
            result = padded

        return result.astype(np.float32, copy=False).reshape(original_shape)

    def reset(self) -> None:
        # zero the existing state array BEFORE replacing it
        if self._carry.size > 0:
            self._carry.fill(0)
        self._carry = np.array([], dtype=np.float32)
        # Zero the pre-allocated per-frame conversion buffers
        self._frame_f32_buf.fill(0)
        self._frame_i16_buf.fill(0)
        # zero the concatenated-output + padding buffers for the same
        if self._result_48k_buf is not None:
            self._result_48k_buf.fill(0)
        if self._padded_buf is not None:
            self._padded_buf.fill(0)
        #  also reset the streaming resamplers' filter state
        if self._upsampler is not None:
            self._upsampler.reset()
        if self._downsampler is not None:
            self._downsampler.reset()
        # GTCRN state: the model's recurrent caches + overlap-add tail
        if self._method == "gtcrn" and self._backend is not None:
            self._backend.reset()
        if self._result_16k_buf is not None:
            self._result_16k_buf.fill(0)
        if self._gtcrn_upsampler is not None:
            self._gtcrn_upsampler.reset()
        if self._gtcrn_downsampler is not None:
            self._gtcrn_downsampler.reset()

    def _resampler_pair_delay_ms(self, native_rate: int) -> float:
        """Round-trip group delay (ms) of this suppressor's streaming resampler pair."""
        source_rate = self._source_sample_rate
        if source_rate <= 0 or source_rate == native_rate:
            return 0.0
        rate_gcd = math.gcd(native_rate, source_rate)
        up = native_rate // rate_gcd
        down = source_rate // rate_gcd
        # Upsampler: input at the source rate. Downsampler: constructed as
        return _resampler_group_delay_ms(up, down, source_rate) + _resampler_group_delay_ms(down, up, native_rate)

    @property
    def latency_ms(self) -> float:
        # ~10ms (one RNNoise frame) / ~16ms (one 256-sample GTCRN hop at
        if self._method == "rnnoise":
            return 10.0 + self._resampler_pair_delay_ms(RNNOISE_SAMPLE_RATE)
        if self._method == "gtcrn":
            return 16.0 + self._resampler_pair_delay_ms(WHISPER_SAMPLE_RATE)
        return 0.0

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason
