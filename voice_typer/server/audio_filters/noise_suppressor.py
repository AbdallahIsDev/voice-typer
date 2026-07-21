"""Multi-backend noise suppressor (RNNoise / DeepFilterNet)."""

from __future__ import annotations

import logging

import numpy as np

from voice_typer.server.audio_filters.base import AudioFilter

log = logging.getLogger(__name__)

# RNNoise requires 48kHz, 480-sample frames (10ms at 48kHz).
_RNNOISE_SAMPLE_RATE: int = 48000
_RNNOISE_FRAME_SIZE: int = 480


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
        sample_rate: int = 16000,
    ) -> None:
        self.name = f"NoiseSuppressor({method})"
        self._method = method
        self._source_sample_rate = int(sample_rate)
        self._backend: object | None = None
        self._degraded: bool = False
        self._degraded_reason: str = ""

        # Frame buffering: carry holds partial frames between process() calls.
        self._carry: np.ndarray = np.array([], dtype=np.float32)

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

            self._backend = RNNoise(sample_rate=_RNNOISE_SAMPLE_RATE)
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
        try:
            import torch  # noqa: F401  — check torch available first
            from df import enhance, init_df  # type: ignore[import-not-found]

            self._backend = {
                "init_df": init_df,
                "enhance": enhance,
                "model": None,
                "df_state": None,
            }
            # Initialize lazily on first process() call (slow import)
            log.info("[NOISE-SUPPRESS] DeepFilterNet backend ready (lazy init)")
        except ImportError:
            log.warning(
                "[NOISE-SUPPRESS] deepfilternet not installed — "
                "falling back to rnnoise. Install with: pip install 'voice-typer[deepfilternet]'"
            )
            self._degraded = True
            self._degraded_reason = "deepfilternet not installed, falling back to rnnoise"
            self._method = "rnnoise"
            self._init_rnnoise()
        except Exception as exc:
            log.warning("[NOISE-SUPPRESS] DeepFilterNet init failed: %s — falling back to rnnoise", exc)
            self._degraded = True
            self._degraded_reason = f"deepfilternet init failed: {exc}"
            self._method = "rnnoise"
            self._init_rnnoise()

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if self._method == "none" or self._backend is None or audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)

        if self._method == "rnnoise":
            return self._process_rnnoise(samples, sample_rate, original_shape)
        # CR-6: DeepFilterNet and Speex backends are initialized but not
        # yet wired to actual processing. Previously this was a silent
        # passthrough — users selecting `noisy_room` preset (which picks
        # `deepfilternet`) got ZERO noise suppression with no UI signal.
        # Now we mark the filter as degraded so the UI surfaces the gap
        # and the user knows their audio is not being processed.
        if not self._degraded:
            self._degraded = True
            self._degraded_reason = (
                f"{self._method} backend not yet implemented — using passthrough "
                "(fall back to rnnoise for actual noise suppression)"
            )
            log.warning("[AUDIO] NoiseSuppressor: %s", self._degraded_reason)
        return audio.reshape(original_shape)

    def _process_rnnoise(
        self,
        samples: np.ndarray,
        sample_rate: int,
        original_shape: tuple,
    ) -> np.ndarray | None:
        """Process through RNNoise with frame buffering.

        RNNoise requires 48kHz / 480-sample frames. If source is 16kHz,
        we round-trip resample. Uses input/output deques like OBS.
        """
        # NF-R20-5: assert the backend is non-None at the top so the
        # subsequent ``self._backend.<attr>`` accesses can drop their
        # ``# type: ignore[union-attr]`` suppressions. The process()
        # entry point already guards on ``self._backend is None``, so
        # this assertion is documentation-as-code — pyrefly/mypy infer
        # the narrowed type for the rest of the method body.
        assert self._backend is not None
        from scipy.signal import resample_poly

        # Resample to 48kHz if needed
        if sample_rate != _RNNOISE_SAMPLE_RATE:
            # Upsample to 48k
            up = resample_poly(samples, _RNNOISE_SAMPLE_RATE, sample_rate)
        else:
            up = samples

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
                # pyrnnoise uses int16 internally; convert from float32.
                frame_i16 = (frame * 32767).astype(np.int16)
                # pyrnnoise expects [num_channels, 480]; returns (speech_prob, cleaned) as int16
                _, cleaned_i16 = self._backend.denoise_frame(frame_i16[np.newaxis, :])
                output_frames.append(cleaned_i16[0].astype(np.float32) / 32767)
            except Exception as exc:
                log.debug("[NOISE-SUPPRESS] RNNoise frame failed: %s", exc)
                output_frames.append(frame)

        # Save remainder for next call
        if remainder > 0:
            self._carry = combined[n_full * _RNNOISE_FRAME_SIZE :]
        else:
            self._carry = np.array([], dtype=np.float32)

        result_48k = np.concatenate(output_frames)

        # Resample back to source rate
        if sample_rate != _RNNOISE_SAMPLE_RATE:
            result = resample_poly(result_48k, sample_rate, _RNNOISE_SAMPLE_RATE)
        else:
            result = result_48k

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
        self._carry = np.array([], dtype=np.float32)

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
