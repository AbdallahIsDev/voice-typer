"""Multi-backend noise suppressor (RNNoise / DeepFilterNet)."""

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


class NoiseSuppressor(AudioFilter):
    """Neural noise suppression with multiple backends.

    Backends (runtime-switchable):
    - ``"rnnoise"`` — ``pyrnnoise`` package, 480-sample frames at 48kHz.
      BSD-licensed, ~1ms per frame. Default.
    - ``"deepfilternet"`` — ``deepfilternet`` package (requires torch).
      Higher quality, 2-3x CPU. Premium option.
    - ``"none"`` — passthrough (no suppression).

    If the selected backend's library is missing, falls back to ``"none"``
    and sets ``is_degraded=True`` so the UI can warn the user.

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

        # streaming resamplers created lazily by
        # ``_ensure_resamplers``. At the native RNNoise rate (48kHz) both stay
        # ``None`` (no resampling needed).
        self._upsampler: _StreamingResampler | None = None
        self._downsampler: _StreamingResampler | None = None
        self._resampler_rate: int | None = None

        self._init_backend()

    def _init_backend(self) -> None:
        """Initialize the selected backend. Fall back to 'none' on failure."""
        method = self._method
        if method == "none":
            self._backend = None
            return

        if method == "rnnoise":
            self._init_rnnoise()
        elif method == "deepfilternet":
            self._init_deepfilternet()
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

    def _init_deepfilternet(self) -> None:
        """Initialize DeepFilterNet backend.

         (Critical): the DeepFilterNet *processing* path is not
        yet implemented in :meth:`process` — only ``rnnoise`` is wired.
        Previously, when ``deepfilternet`` was selected (e.g. via the
        ``noisy_room`` preset) AND the ``df`` package was importable,
        ``__init__`` left ``self._method == "deepfilternet"`` and
        ``is_degraded == False``. The first ``process()`` call then
        silently fell through to passthrough — users in noisy
        environments got ZERO noise suppression with no UI signal.

        Mitigation (chosen from the finding's option (b)): mark the
        filter as degraded AND fall back to ``rnnoise`` at *init* time
        (not at process() time) so:

          1. The UI sees ``is_degraded == True`` immediately on
             construction — before the first audio chunk — and can
             surface a warning to the user.
          2. The user gets actual neural noise suppression via
             RNNoise instead of nothing (when RNNoise is available).
          3. ``process()`` reaches the rnnoise branch directly on
             every call (no per-chunk fallback overhead, no
             surprise ``_method`` mutation on the RT thread).

        If ``rnnoise`` is *also* unavailable, ``_init_rnnoise`` will
        further degrade to ``"none"``. In that case we preserve the
        DeepFilterNet context in ``_degraded_reason`` (so the user
        knows BOTH that DeepFilterNet isn't wired AND that the
        RNNoise fallback also failed) — this is more actionable than
        silently overwriting with just the RNNoise message.
        """
        try:
            # Probe whether the ``df`` package is importable so the
            # degraded_reason can tell the user whether installing
            # deepfilternet would help (it wouldn't — processing is
            # not wired — but the message distinguishes the two cases
            # for diagnostics).
            import df  # noqa: F401  # type: ignore[import-not-found]

            df_available = True
        except ImportError:
            df_available = False
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("[NOISE-SUPPRESS] DeepFilterNet probe failed: %s", exc)
            df_available = False

        if df_available:
            log.warning(
                "[NOISE-SUPPRESS] DeepFilterNet processing is not yet wired; "
                "falling back to rnnoise so users in noisy environments still "
                "get neural noise suppression"
            )
        else:
            log.warning(
                "[NOISE-SUPPRESS] deepfilternet not installed and processing "
                "is not yet wired; falling back to rnnoise. Install with: "
                "pip install 'voice-typer[deepfilternet]'"
            )

        # Mark degraded BEFORE calling _init_rnnoise so the flag is set
        # even if _init_rnnoise succeeds (which would otherwise leave
        # _degraded == False, hiding the deepfilternet-not-wired state
        # from the UI). Save the reason so we can preserve context if
        # _init_rnnoise further degrades to "none".
        self._degraded = True
        df_reason = (
            "deepfilternet backend not yet implemented — falling back to rnnoise"
            if df_available
            else "deepfilternet not installed and not yet implemented — falling back to rnnoise"
        )
        self._degraded_reason = df_reason

        # Switch to rnnoise at init time so process() uses the rnnoise
        # branch directly. ``_init_rnnoise`` may further degrade to
        # ``"none"`` if pyrnnoise is also missing.
        self._method = "rnnoise"
        self._init_rnnoise()

        # If _init_rnnoise succeeded (method is still "rnnoise"), keep
        # the deepfilternet reason — the user should know they're
        # getting rnnoise instead of their selected deepfilternet.
        # If _init_rnnoise failed (method is now "none"), preserve the
        # deepfilternet context AND surface the rnnoise failure so the
        # user knows BOTH problems (the more actionable one is the
        # rnnoise install hint, but the deepfilternet context explains
        # why the noisy_room preset didn't help).
        if self._method == "none":
            # _init_rnnoise overwrote _degraded_reason with the rnnoise
            # failure. Prepend the deepfilternet context so the user
            # sees both: "deepfilternet not wired; rnnoise fallback
            # also failed: <rnnoise reason>".
            rnnoise_reason = self._degraded_reason
            self._degraded_reason = f"{df_reason}; rnnoise fallback also unavailable: {rnnoise_reason}"
        else:
            # _init_rnnoise succeeded — restore df_reason as the
            # degraded_reason (don't let _init_rnnoise's success path
            # accidentally clear _degraded).
            self._degraded = True
            self._degraded_reason = df_reason

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if self._method == "none" or self._backend is None or audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)

        if self._method == "rnnoise":
            return self._process_rnnoise(samples, sample_rate, original_shape)
        #  defensive guard: ``_init_backend`` is supposed to
        # narrow every known method to either ``"rnnoise"`` or
        # ``"none"`` at construction time (see ``_init_deepfilternet``
        # for the deepfilternet → rnnoise fallback). Reaching this
        # branch means a future backend was added without an init-time
        # fallback — instead of silently passthroughing (the original
        # Critical bug), we fall back to rnnoise here and surface
        # ``is_degraded`` so the UI can warn the user. This branch is
        # not reachable for any currently-supported method
        # (``rnnoise`` / ``deepfilternet`` / ``none``) — it exists
        # purely to prevent a regression of
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

        output_frames = []
        for i in range(n_full):
            start = i * _RNNOISE_FRAME_SIZE
            frame = combined[start : start + _RNNOISE_FRAME_SIZE]
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
                output_frames.append(cleaned_i16[0].astype(np.float32) / _FLOAT_TO_INT16_MAX)
            except Exception as exc:
                log.debug("[NOISE-SUPPRESS] RNNoise frame failed: %s", exc)
                output_frames.append(frame)

        # Save remainder for next call
        if remainder > 0:
            self._carry = combined[n_full * _RNNOISE_FRAME_SIZE :]
        else:
            self._carry = np.array([], dtype=np.float32)

        result_48k = np.concatenate(output_frames)

        # Resample back to source rate (using streaming resamplers).
        result = self._downsampler.process(result_48k) if self._downsampler is not None else result_48k

        # The resampling may produce slightly different length than input.
        # Match the input length by padding/truncating.
        target_len = len(samples)
        if len(result) >= target_len:
            result = result[:target_len]
        else:
            padded = np.zeros(target_len, dtype=np.float32)
            padded[: len(result)] = result
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
        #  also reset the streaming resamplers' filter state
        # so a stale tail from the previous session doesn't bleed into the
        # next processing window.
        if self._upsampler is not None:
            self._upsampler.reset()
        if self._downsampler is not None:
            self._downsampler.reset()

    @property
    def latency_ms(self) -> float:
        # ~10ms (one RNNoise frame)
        if self._method == "rnnoise":
            return 10.0
        return 0.0

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason
