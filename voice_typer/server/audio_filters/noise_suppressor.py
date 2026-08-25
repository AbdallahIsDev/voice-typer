"""Multi-backend noise suppressor (RNNoise / GTCRN)."""

from __future__ import annotations

import logging
import math

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE, WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import AudioFilter, _get_lfilter  # noqa: E402

log = logging.getLogger(__name__)

# RNNoise requires 48kHz, 480-sample frames (10ms at 48kHz).
_RNNOISE_FRAME_SIZE: int = 480

# GTCRN (the bundled ONNX streaming model) is native 16 kHz and
# consumes one 256-sample hop per inference call (16 ms at 16 kHz).
# See ``gtcrn_backend.py`` for the full streaming contract.
_GTCRN_HOP_SIZE: int = 256

# float32 -> int16 conversion constants. ``_FLOAT_TO_INT16_MAX`` is the
# maximum representable int16 value (32767). ``_INT16_SCALE`` is the multiplier
# applied to float32 audio (after clipping to [-1, 1]) to bring it into the
# int16 amplitude range. Both names are exposed for testability and so that the
# clip+scale+cast pipeline in ``_process_rnnoise`` uses one source of truth.
_FLOAT_TO_INT16_MAX: float = float(2**15 - 1)  # 32767.0
_INT16_SCALE: float = _FLOAT_TO_INT16_MAX


class _StreamingResampler:
    """Streaming polyphase resampler ( / ).

    The FIR anti-imaging / anti-aliasing filter is designed ONCE at
    construction via ``scipy.signal.firwin`` and reused across every
    ``process()`` call (). Internal filter state (``_zi``) is persisted
    between calls so that chunked processing produces output identical to
    one-shot processing () — there are no edge artifacts at chunk
    boundaries.

    Output length invariant: after consuming ``N`` input samples, the
    cumulative output length is exactly ``floor((N * up + phase) / down)``
    where ``phase`` is the residual downsampling phase carried across calls.
    For an up/down roundtrip (e.g. 16k -> 48k -> 16k) the cumulative output
    length matches the cumulative input length ().

    The implementation upsamples by inserting ``up - 1`` zeros between
    samples, applies the FIR filter via ``scipy.signal.lfilter`` (which
    preserves the persistent state), then downsamples by selecting every
    ``down``-th sample starting at the current phase offset.
    """

    def __init__(self, up: int, down: int) -> None:
        if up <= 0:
            raise ValueError(f"up must be > 0, got {up!r}")
        if down <= 0:
            raise ValueError(f"down must be > 0, got {down!r}")
        self._up = int(up)
        self._down = int(down)

        # Design the FIR filter ONCE at construction (). Cutoff is the
        # lower Nyquist of input / output (1.0 == Nyquist in firwin's fs=2.0
        # convention). Filter length is chosen as ``10 * max(up, down) + 1``
        # (odd, so the polyphase decomposition is symmetric). The exact length
        # is not load-bearing for the tests; only the "designed once" and
        # "stable identity" invariants matter.
        from scipy.signal import firwin

        gcd = math.gcd(self._up, self._down)
        up_reduced = self._up // gcd
        down_reduced = self._down // gcd
        cutoff = 1.0 / max(up_reduced, down_reduced)
        num_taps = 10 * max(up_reduced, down_reduced) + 1
        if num_taps % 2 == 0:
            num_taps += 1
        self._h = np.asarray(firwin(num_taps, cutoff, fs=2.0), dtype=np.float64)

        # Persistent lfilter state (len = len(h) - 1 for an FIR filter).
        self._zi: np.ndarray = np.zeros(max(len(self._h) - 1, 0), dtype=np.float64)

        # Counters and phase for the output-length invariant () and for
        # reset() verification.
        self._in_total: int = 0
        self._out_total: int = 0
        self._phase: int = 0
        # pre-allocated upsample buffer (zero-filled, reused across
        # process() calls) to avoid allocating a fresh (n_in * up) float64
        # array per chunk. Lazy-resized to the largest chunk seen.
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
        # reuse a pre-allocated zero-filled buffer instead of
        # allocating a fresh (n_in * up) float64 array per chunk. The
        # buffer is zero-filled once at allocation; subsequent calls
        # only need to overwrite the up-spaced samples (the zeros between
        # them are preserved because we never write to those indices).
        # This drops ~12 KB/chunk allocation on the 16k->48k path.
        up_len = n_in * up
        if self._x_up_buf is None or self._x_up_buf.shape[0] < up_len:
            self._x_up_buf = np.zeros(max(up_len, 1024), dtype=np.float64)
        x_up = self._x_up_buf[:up_len]
        # Zero out only the slots we're about to write — actually we
        # write to x_up[::up], which spans indices [0, up, 2*up, ...].
        # The non-stride slots retain zeros from the initial np.zeros
        # allocation OR from a previous call where they were already
        # zero (we never write to non-stride slots). But across calls
        # with different up_len, the buffer may have stale non-zero
        # data in slots beyond the previous up_len. To be safe, zero
        # the active region before writing the strided samples.
        x_up.fill(0)
        x_up[::up] = x64

        # Apply FIR filter with persistent state.
        y, self._zi = _get_lfilter()(self._h, [1.0], x_up, zi=self._zi)

        # Downsample: pick every `down`-th sample starting at current phase.
        m = y.size  # == n_in * up
        idx = np.arange(self._phase, m, down)
        out = y[idx]

        # Advance phase: the next chunk's first downsample sample is offset by
        # (phase + m) mod down from the start of the next upsampled chunk.
        self._phase = (self._phase + m) % down

        self._in_total += n_in
        self._out_total += out.size
        return out.astype(np.float32, copy=False)

    def reset(self) -> None:
        """Clear all internal state (zeros ``_zi`` in place)."""
        #  (mirrored from NoiseSuppressor.reset): zero the existing
        # state array IN PLACE so the buffer contents are securely cleared
        # before the next allocation, then reset counters / phase.
        if self._zi.size > 0:
            self._zi.fill(0)
        self._in_total = 0
        self._out_total = 0
        self._phase = 0
        # Zero the pre-allocated upsample working buffer so the
        # last chunk's raw input samples (strided into ``_x_up_buf[::up]``)
        # do not linger in process memory until the numpy allocator
        # reuses the block. Guarded for None because ``_x_up_buf`` is
        # lazy-allocated on the first ``process()`` call.
        if self._x_up_buf is not None:
            self._x_up_buf.fill(0)


class NoiseSuppressor(AudioFilter):
    """Neural noise suppression with multiple backends.

    Backends (runtime-switchable):
    - ``"rnnoise"`` — ``pyrnnoise`` package, 480-sample frames at 48kHz.
      BSD-licensed, ~1ms per frame. Default.
    - ``"gtcrn"`` — bundled GTCRN ONNX streaming model (16 kHz,
      256-sample hops, ~2 ms per hop on CPU). Higher quality than
      RNNoise; selected by the ``noisy_room`` preset.
    - ``"none"`` — passthrough (no suppression).

    If the selected backend is unavailable (missing library / model),
    falls back to ``rnnoise`` (or ``none``) and sets ``is_degraded=True``
    so the UI can warn the user.

    Frame buffering: uses input/output deques (like OBS) to handle
    arbitrary chunk sizes. Returns ``None`` when the output buffer is
    underfilled — callers should propagate ``None``.
    """

    def __init__(
        self,
        method: str = "rnnoise",
        sample_rate: int = WHISPER_SAMPLE_RATE,
    ) -> None:
        self.name = f"NoiseSuppressor({method})"
        self._method = method
        self._source_sample_rate = int(sample_rate)
        self._backend: object | None = None
        self._degraded: bool = False
        self._degraded_reason: str = ""

        # Frame buffering: carry holds partial frames between process() calls.
        self._carry: np.ndarray = np.array([], dtype=np.float32)

        # pre-allocated per-frame conversion buffers for the RNNoise
        # loop. Reused across every 480-sample frame to avoid ~5 small
        # allocations (clip, mul, astype, astype, div) per frame — at
        # 48 RNNoise frames/sec that's ~240 allocations/sec saved.
        self._frame_f32_buf: np.ndarray = np.zeros(_RNNOISE_FRAME_SIZE, dtype=np.float32)
        self._frame_i16_buf: np.ndarray = np.zeros(_RNNOISE_FRAME_SIZE, dtype=np.int16)
        # pre-allocated float64 output buffer for the concatenated RNNoise
        # output (``result_48k``). Before this, ``np.concatenate(output_frames)``
        # allocated a fresh float64 array per ``process()`` call. Now each
        # frame's cleaned output is written directly into the appropriate
        # 480-sample slice of this buffer (via ``np.copyto`` + in-place
        # ``np.divide``), eliminating the per-frame ``astype(np.float32) /
        # _FLOAT_TO_INT16_MAX`` allocations AND the final concatenate.
        # float64 (not float32) preserves the original arithmetic precision
        # (``int16.astype(float32) / float64_scalar`` promotes to float64;
        # ``np.copyto(f64, int16)`` then ``f64 /= float64`` is byte-identical).
        # Lazy-resized to the largest ``n_full * _RNNOISE_FRAME_SIZE`` seen.
        self._result_48k_buf: np.ndarray | None = None
        # pre-allocated float32 padding buffer for the length-match path
        # (when the downsampler produces fewer samples than the input).
        # Before this, ``np.zeros(target_len, dtype=np.float32)`` allocated
        # a fresh array per call on that rare path. Lazy-resized to the
        # largest ``target_len`` seen.
        self._padded_buf: np.ndarray | None = None

        # streaming resamplers created lazily by
        # ``_ensure_resamplers``. At the native RNNoise rate (48kHz) both stay
        # ``None`` (no resampling needed).
        self._upsampler: _StreamingResampler | None = None
        self._downsampler: _StreamingResampler | None = None
        self._resampler_rate: int | None = None

        # GTCRN is native 16 kHz (not 48 kHz like RNNoise), so it gets
        # its OWN lazily-created resampler pair — created by
        # ``_ensure_gtcrn_resamplers``. At 16 kHz both stay ``None``.
        self._gtcrn_upsampler: _StreamingResampler | None = None
        self._gtcrn_downsampler: _StreamingResampler | None = None
        self._gtcrn_resampler_rate: int | None = None
        # pre-allocated float32 output buffer for the concatenated GTCRN
        # hop outputs — the same lazy-resize pattern as
        # ``_result_48k_buf`` on the RNNoise path (each hop's enhanced
        # output is written directly into its 256-sample slice, no
        # per-hop list + final ``np.concatenate``).
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
            # by the bundled GTCRN ONNX streaming model (the upstream
            # PyPI package is unmaintained and its processing path was
            # never wired here). ``Config.load()`` remaps the on-disk
            # value to ``"gtcrn"`` before validation; this alias covers
            # direct construction (tests, embedders) still passing the
            # old name so it degrades to the live backend instead of
            # silently passthroughing.
            log.info("[NOISE-SUPPRESS] legacy method 'deepfilternet' — using gtcrn")
            self._method = "gtcrn"
            self._init_gtcrn()
        else:
            log.warning("[NOISE-SUPPRESS] unknown method %r — using none", method)
            self._method = "none"
            self._backend = None

    def _init_rnnoise(self) -> None:
        try:
            from pyrnnoise import RNNoise  # type: ignore[import-not-found]

            self._backend = RNNoise(sample_rate=RNNOISE_SAMPLE_RATE)
            log.info("[NOISE-SUPPRESS] RNNoise backend ready")
        except ImportError:
            log.warning(
                "[NOISE-SUPPRESS] pyrnnoise not installed — falling back to none. Install with: pip install pyrnnoise"
            )
            self._degraded = True
            self._degraded_reason = "rnnoise library not installed"
            self._method = "none"
        except Exception as exc:
            log.warning("[NOISE-SUPPRESS] RNNoise init failed: %s — falling back to none", exc)
            self._degraded = True
            self._degraded_reason = f"rnnoise init failed: {exc}"
            self._method = "none"

    def _init_gtcrn(self) -> None:
        """Initialize the GTCRN backend (bundled ONNX streaming model).

        On ANY failure — ``onnxruntime`` missing, the bundled
        ``gtcrn_simple.onnx`` missing / corrupt, or the session failing
        its warmup — degrade to ``rnnoise`` at INIT time (the same
        contract the retired DeepFilterNet placeholder had, except the
        GTCRN path actually processes audio when it loads):

          1. The UI sees ``is_degraded == True`` immediately on
             construction — before the first audio chunk — and can
             surface a warning to the user.
          2. The user still gets neural noise suppression via RNNoise
             (when RNNoise is available).
          3. ``process()`` reaches the rnnoise branch directly on
             every call (no per-chunk fallback overhead, no surprise
             ``_method`` mutation on the RT thread).

        If ``rnnoise`` is *also* unavailable, ``_init_rnnoise`` will
        further degrade to ``"none"``. In that case the GTCRN context
        is preserved in ``_degraded_reason`` (so the user knows BOTH
        that the GTCRN model couldn't load AND that the RNNoise
        fallback also failed) — the rnnoise install hint is the more
        actionable part, but the GTCRN context explains why the
        ``noisy_room`` preset didn't hold its first choice.
        """
        try:
            from voice_typer.server.audio_filters.gtcrn_backend import GtcrnBackend

            self._backend = GtcrnBackend()
        except Exception as exc:
            log.warning("[NOISE-SUPPRESS] GTCRN init failed: %s — falling back to rnnoise", exc)
            # Mark degraded BEFORE calling _init_rnnoise so the flag is
            # set even if _init_rnnoise succeeds (which would otherwise
            # leave _degraded == False, hiding the GTCRN failure from
            # the UI). Save the reason so we can preserve context if
            # _init_rnnoise further degrades to "none".
            self._degraded = True
            gtcrn_reason = f"gtcrn init failed: {exc} — falling back to rnnoise"
            self._degraded_reason = gtcrn_reason

            # Switch to rnnoise at init time so process() uses the
            # rnnoise branch directly. ``_init_rnnoise`` may further
            # degrade to ``"none"`` if pyrnnoise is also missing.
            self._method = "rnnoise"
            self._init_rnnoise()

            if self._method == "none":
                # _init_rnnoise overwrote _degraded_reason with the
                # rnnoise failure. Prepend the GTCRN context so the
                # user sees both problems at once: "gtcrn init failed;
                # rnnoise fallback also unavailable: <rnnoise reason>".
                rnnoise_reason = self._degraded_reason
                self._degraded_reason = f"{gtcrn_reason}; rnnoise fallback also unavailable: {rnnoise_reason}"
            else:
                # _init_rnnoise succeeded — restore gtcrn_reason as the
                # degraded_reason (don't let _init_rnnoise's success
                # path accidentally clear _degraded).
                self._degraded = True
                self._degraded_reason = gtcrn_reason
        else:
            log.info("[NOISE-SUPPRESS] GTCRN backend ready (bundled ONNX streaming model)")

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
        # narrow every known method to one of ``"rnnoise"`` /
        # ``"gtcrn"`` / ``"none"`` at construction time (see
        # ``_init_gtcrn`` for the gtcrn → rnnoise fallback). Reaching this
        # branch means a future backend was added without an init-time
        # fallback — instead of silently passthroughing (the original
        # Critical bug), we fall back to rnnoise here and surface
        # ``is_degraded`` so the UI can warn the user. This branch is
        # not reachable for any currently-supported method
        # (``rnnoise`` / ``gtcrn`` / ``none``) — it exists
        # purely to prevent a regression of silent passthrough.
        if not self._degraded:
            self._degraded = True
            self._degraded_reason = f"{self._method} backend not yet implemented — falling back to rnnoise"
            log.warning(
                "[AUDIO] NoiseSuppressor: %s backend not yet wired; "
                "falling back to rnnoise for neural noise suppression",
                self._method,
            )
        try:
            self._method = "rnnoise"
            # The defensive ``_backend is None or not _backend.get(...)``
            # check tolerates both a None backend (init failed) and a
            # dict-style backend from a future lazy-init path.
            if self._backend is None or (hasattr(self._backend, "get") and not self._backend.get("rnnoise")):
                self._init_rnnoise()
            return self._process_rnnoise(samples, sample_rate, original_shape)
        except Exception:
            # If rnnoise also fails, return the original audio (last resort).
            return audio.reshape(original_shape)

    def _ensure_resamplers(self, sample_rate: int) -> None:
        """Lazily create (or recreate) the streaming resamplers for ``sample_rate``.

         the FIR filter inside each ``_StreamingResampler`` is
        designed once at construction and reused across every ``process()``
        call. At the native RNNoise rate (48kHz) both resamplers stay ``None``
        (no resampling needed). When the source rate changes, both resamplers
        are recreated so the up/down ratio matches the new rate.
        """
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
        """Process through RNNoise with frame buffering.

        RNNoise requires 48kHz / 480-sample frames. If source is 16kHz,
        we round-trip resample using the streaming resamplers
        (``_StreamingResampler``). Uses input/output deques like OBS.
        """
        # assert the backend is non-None at the top so the
        # subsequent ``self._backend.<attr>`` accesses can drop their
        # ``# type: ignore[union-attr]`` suppressions. The process()
        # entry point already guards on ``self._backend is None``, so
        # this assertion is documentation-as-code — pyrefly/mypy infer
        # the narrowed type for the rest of the method body.
        assert self._backend is not None

        #  ensure streaming resamplers exist for this rate.
        # At the native RNNoise rate (48kHz) both stay ``None``.
        self._ensure_resamplers(sample_rate)

        # Resample to 48kHz if needed (using streaming resamplers).
        up = self._upsampler.process(samples) if self._upsampler is not None else samples

        # Prepend carry from previous call
        combined = np.concatenate([self._carry, up])
        n_full = len(combined) // _RNNOISE_FRAME_SIZE
        remainder = len(combined) - n_full * _RNNOISE_FRAME_SIZE

        if n_full == 0:
            # Not enough for a full frame — buffer it
            self._carry = combined
            return None  # signal caller to skip this chunk

        # Set channel info once for pyrnnoise (mono).
        self._backend.channels = 1

        # pre-allocate the float64 output buffer for the concatenated
        # RNNoise output. Each frame's cleaned output is written directly
        # into the appropriate 480-sample slice — eliminates the
        # ``output_frames`` list + final ``np.concatenate`` (which allocated
        # a fresh float64 array per call) AND the per-frame
        # ``cleaned_i16[0].astype(np.float32) / _FLOAT_TO_INT16_MAX``
        # (which allocated a fresh float64 array per frame).
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
                # so out-of-range floats (e.g. from upstream gain stages) do not
                # wrap around the int16 range. ``_INT16_SCALE`` is the float ->
                # int16 multiplier (= 32767.0). pyrnnoise uses int16 internally.
                # reuse pre-allocated conversion buffers — np.clip +
                # np.multiply + astype each into the same float32 buffer, then
                # copy into the int16 buffer. Saves 3-5 allocations per frame.
                frame_f32 = self._frame_f32_buf
                np.clip(frame, -1.0, 1.0, out=frame_f32)
                np.multiply(frame_f32, _INT16_SCALE, out=frame_f32)
                frame_i16 = self._frame_i16_buf
                frame_i16[:] = frame_f32.astype(np.int16)
                # pyrnnoise expects [num_channels, 480]; returns (speech_prob, cleaned) as int16
                _, cleaned_i16 = self._backend.denoise_frame(frame_i16[np.newaxis, :])
                # write the cleaned frame directly into the result buffer
                # slice: int16 -> float64 (exact, both fit in float64
                # mantissa) then in-place divide by _FLOAT_TO_INT16_MAX.
                # Byte-identical to ``cleaned_i16[0].astype(np.float32) /
                # _FLOAT_TO_INT16_MAX`` because the float32 intermediate is
                # exactly upcast to float64 before the float64 division.
                np.copyto(out_slice, cleaned_i16[0], casting="same_kind")
                np.divide(out_slice, _FLOAT_TO_INT16_MAX, out=out_slice)
            except Exception as exc:
                log.debug("[NOISE-SUPPRESS] RNNoise frame failed: %s", exc)
                # fall back to the original frame (upcast float32 -> float64
                # to match the success-path dtype so the downstream
                # concatenate-free buffer is homogeneous float64).
                np.copyto(out_slice, frame, casting="same_kind")

        # Save remainder for next call
        if remainder > 0:
            self._carry = combined[n_full * _RNNOISE_FRAME_SIZE :]
        else:
            self._carry = np.array([], dtype=np.float32)

        # Resample back to source rate (using streaming resamplers).
        result = self._downsampler.process(result_48k) if self._downsampler is not None else result_48k

        # The resampling may produce slightly different length than input.
        # Match the input length by padding/truncating.
        target_len = len(samples)
        if len(result) >= target_len:
            result = result[:target_len]
        else:
            # pre-allocated padding buffer (lazy-resized). The tail beyond
            # ``len(result)`` is zeroed so stale data from a previous call
            # (with a larger ``target_len``) does not leak into the output.
            if self._padded_buf is None or self._padded_buf.shape[0] < target_len:
                self._padded_buf = np.zeros(max(target_len, 1024), dtype=np.float32)
            padded = self._padded_buf[:target_len]
            padded[: len(result)] = result
            padded[len(result) :] = 0.0
            result = padded

        return result.astype(np.float32, copy=False).reshape(original_shape)

    def _ensure_gtcrn_resamplers(self, sample_rate: int) -> None:
        """Lazily create (or recreate) the GTCRN streaming resamplers.

        GTCRN is native 16 kHz (unlike RNNoise's 48 kHz), so it needs
        its OWN resampler pair — separate state from the RNNoise pair
        so the RNNoise processing path stays untouched. At 16 kHz both
        resamplers stay ``None`` (no resampling needed); at any other
        source rate an up/down pair round-trips the audio through
        16 kHz for the model and back.
        """
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
        """Process through the GTCRN streaming model with hop buffering.

        GTCRN is native 16 kHz and consumes 256-sample hops. If the
        source rate differs, round-trip resample with the GTCRN
        streaming resamplers. Mirrors the RNNoise frame-buffering
        structure: partial hops are carried in ``self._carry`` between
        calls; ``None`` is returned while too little audio is buffered.

        Each hop's enhanced block trails the input by ONE hop (the
        512-point analysis window spans two hops — see
        ``gtcrn_backend``), so the emitted stream is the denoised
        input delayed by 16 ms; ``latency_ms`` reports that.
        """
        assert self._backend is not None

        self._ensure_gtcrn_resamplers(sample_rate)

        # Resample to the model's native 16 kHz if needed.
        up = self._gtcrn_upsampler.process(samples) if self._gtcrn_upsampler is not None else samples

        # Prepend carry from previous call and slice into full hops.
        combined = np.concatenate([self._carry, up])
        n_full = len(combined) // _GTCRN_HOP_SIZE
        remainder = len(combined) - n_full * _GTCRN_HOP_SIZE

        if n_full == 0:
            # Not enough for a full hop — buffer it.
            self._carry = combined
            return None  # signal caller to skip this chunk

        # pre-allocated float32 output buffer: each hop's enhanced
        # output is written directly into its 256-sample slice (no
        # per-hop list + final concatenate — the same pattern as the
        # RNNoise ``_result_48k_buf``).
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
                # (never crash the audio thread); the init-time
                # warmup makes this path effectively unreachable.
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
        # input. Match the input length by padding/truncating (same
        # contract as the RNNoise path — the shared ``_padded_buf`` is
        # safe because only one backend is ever active per instance).
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
        # so partial-frame samples (which can hold ~10ms of the user's
        # voice between process() calls) are securely cleared rather
        # than left in process memory until the numpy allocator reuses
        # the block.  ``self._carry`` is reassigned to a fresh empty
        # array immediately after, but the OLD array's contents are
        # zeroed first by reference.
        if self._carry.size > 0:
            self._carry.fill(0)
        self._carry = np.array([], dtype=np.float32)
        # Zero the pre-allocated per-frame conversion buffers
        # so the last frame's raw-audio-derived samples (clip+scale+cast
        # into ``_frame_f32_buf`` / ``_frame_i16_buf`` before being handed
        # to RNNoise) do not linger in process memory until the numpy
        # allocator reuses the blocks. Both buffers are non-None
        # ``np.zeros(...)`` allocations in ``__init__`` so the unguarded
        # ``.fill(0)`` is safe.
        self._frame_f32_buf.fill(0)
        self._frame_i16_buf.fill(0)
        # zero the concatenated-output + padding buffers for the same
        # privacy rationale. ``_result_48k_buf`` holds the cleaned RNNoise
        # output (derived from the user's voice); ``_padded_buf`` holds the
        # length-matched output. Guarded for None because they are
        # lazy-allocated on the first ``process()`` call.
        if self._result_48k_buf is not None:
            self._result_48k_buf.fill(0)
        if self._padded_buf is not None:
            self._padded_buf.fill(0)
        #  also reset the streaming resamplers' filter state
        # so a stale tail from the previous session doesn't bleed into the
        # next processing window.
        if self._upsampler is not None:
            self._upsampler.reset()
        if self._downsampler is not None:
            self._downsampler.reset()
        # GTCRN state: the model's recurrent caches + overlap-add tail
        # (a stale tail from the previous session would bleed into the
        # next one), the 16 kHz resampler pair's filter state, and the
        # concatenated-output buffer (privacy — same rationale as
        # ``_result_48k_buf`` above). Gated on the METHOD so the
        # RNNoise reset path stays byte-identical (a degraded-to-
        # rnnoise instance holds an RNNoise backend, not a GTCRN one).
        if self._method == "gtcrn" and self._backend is not None:
            self._backend.reset()
        if self._result_16k_buf is not None:
            self._result_16k_buf.fill(0)
        if self._gtcrn_upsampler is not None:
            self._gtcrn_upsampler.reset()
        if self._gtcrn_downsampler is not None:
            self._gtcrn_downsampler.reset()

    @property
    def latency_ms(self) -> float:
        # ~10ms (one RNNoise frame)
        if self._method == "rnnoise":
            return 10.0
        # ~16ms (one 256-sample GTCRN hop at 16 kHz — the streaming
        # STFT's overlap-add algorithmic delay; see gtcrn_backend.py)
        if self._method == "gtcrn":
            return 16.0
        return 0.0

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason
