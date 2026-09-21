"""GTCRN ONNX streaming noise-suppression backend."""

from __future__ import annotations

import logging
from pathlib import Path

from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.log_rate_limit import log_rate_limited

np = lazy_module("numpy")

log = logging.getLogger(__name__)

# Path to the bundled GTCRN ONNX model, sits beside ``silero_vad.onnx``
MODEL_PATH = Path(__file__).resolve().parent.parent / "gtcrn_simple.onnx"

# Streaming STFT geometry (upstream GTCRN streaming semantics): 512-point
N_FFT: int = 512
HOP: int = 256
NUM_BINS: int = N_FFT // 2 + 1  # 257 real-FFT bins

# Recurrent-cache shapes exported by the model graph. The three caches
CACHE_SHAPES: tuple[tuple[int, ...], ...] = (
    (2, 1, 16, 16, 33),
    (2, 3, 1, 1, 16),
    (2, 1, 33, 16),
)

# One warmup inference at construction pays the ORT graph-optimization

# Rate-limit cadence for the construction failure logs. The suppressor
_LOAD_FAILURE_EVERY_N: int = 0


def is_available() -> bool:
    """Check if the GTCRN backend can load (onnxruntime + bundled model)."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return MODEL_PATH.exists()


class GtcrnBackend:
    """Streaming GTCRN denoiser over a bundled ONNX ``InferenceSession``."""

    def __init__(self) -> None:
        try:
            import onnxruntime as _ort
        except ImportError as exc:
            log_rate_limited(
                log,
                logging.ERROR,
                "[GTCRN] onnxruntime not importable. GTCRN noise suppression unavailable",
                every_n=_LOAD_FAILURE_EVERY_N,
            )
            raise RuntimeError("onnxruntime not importable, cannot load the GTCRN model") from exc

        if not MODEL_PATH.exists():
            log_rate_limited(
                log,
                logging.ERROR,
                "[GTCRN] bundled model not found at %s. GTCRN noise suppression "
                "unavailable (no network fetch is attempted)",
                MODEL_PATH,
                every_n=_LOAD_FAILURE_EVERY_N,
            )
            raise RuntimeError(f"bundled GTCRN model not found at {MODEL_PATH}")

        try:
            # CPU-only by design (mirrors vad.py): the model is tiny and runs
            # per 512-sample hop, so single-threaded pools avoid spin-wait
            # costing more than the sub-millisecond forward pass saves.
            session_options = _ort.SessionOptions()
            session_options.intra_op_num_threads = 1
            session_options.inter_op_num_threads = 1
            session = _ort.InferenceSession(
                str(MODEL_PATH),
                sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            log_rate_limited(
                log,
                logging.ERROR,
                "[GTCRN] bundled ONNX model load failed: %s. GTCRN noise "
                "suppression unavailable (no network fetch is attempted)",
                exc,
                every_n=_LOAD_FAILURE_EVERY_N,
            )
            raise RuntimeError(f"failed to load the bundled GTCRN model: {exc}") from exc

        # Discover the input/output names from the graph instead of
        self._input_names = [i.name for i in session.get_inputs()]
        if len(self._input_names) != 1 + len(CACHE_SHAPES):
            raise RuntimeError(f"unexpected GTCRN graph input arity: {self._input_names!r}")
        self._session = session

        # sqrt-Hann analysis/synthesis window, designed ONCE. Periodic
        from scipy.signal import get_window

        self._window = np.sqrt(get_window("hann", N_FFT, fftbins=True)).astype(np.float32)

        # ── Pre-allocated hot-path buffers (one allocation each, reused
        self._frame_buf = np.zeros(N_FFT, dtype=np.float32)
        # Model input tensor mix[1, 257, 1, 2].
        self._mix_buf = np.zeros((1, NUM_BINS, 1, 2), dtype=np.float32)
        # Previous hop's 256 samples (the analysis-frame left half).
        self._prev_tail_buf = np.zeros(HOP, dtype=np.float32)
        # Overlap-add accumulator: the previous synthesis window's right
        self._olap_buf = np.zeros(HOP, dtype=np.float32)
        # The 256-sample output block emitted per hop.
        self._out_buf = np.zeros(HOP, dtype=np.float32)

        # Recurrent caches, zeroed at construction, threaded across
        self._caches: tuple[np.ndarray, ...] = tuple(np.zeros(shape, dtype=np.float32) for shape in CACHE_SHAPES)

        # Warmup hop (zeros): validates the full I/O contract at init
        self.process_hop(np.zeros(HOP, dtype=np.float32))
        self.reset()

    def process_hop(
        self,
        hop: np.ndarray,
        caches: tuple[np.ndarray, ...] | None = None,
    ) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
        """Denoise one 256-sample hop; return ``(enhanced_hop, new_caches)``.

        Args:
            hop: float32 array of 256 samples at the model's native
                16 kHz. Shorter inputs are zero-padded, longer ones
                truncated (defensive, the suppressor always feeds
                exactly-256 hops via its carry accumulator).
            caches: explicit recurrent-cache tuple to thread in. When
                ``None`` (the production path) the backend's internal
                caches are used and updated in place.

        Returns:
            ``(enhanced_hop, new_caches)`` where ``enhanced_hop`` is a
            fresh 256-sample float32 array covering the PREVIOUS hop's
            time span (one hop of algorithmic delay) and
            ``new_caches`` is the cache tuple to feed the next call.
        """
        if caches is None:
            caches = self._caches

        hop = np.asarray(hop, dtype=np.float32).reshape(-1)
        # Defensive normalization, never crash the audio thread on a
        normalized = hop if hop.size == HOP else self._normalize_hop(hop)

        # 1. Assemble the 512-sample analysis frame: previous hop's tail
        frame = self._frame_buf
        frame[:HOP] = self._prev_tail_buf
        frame[HOP:] = normalized

        # 2. Analysis window (in-place) + real FFT → 257 bins.
        np.multiply(frame, self._window, out=frame)
        spec = np.fft.rfft(frame)
        mix = self._mix_buf
        mix[0, :, 0, 0] = spec.real
        mix[0, :, 0, 1] = spec.imag

        # 3. Inference, thread the recurrent caches through. Arity is
        outputs = self._session.run(
            None,
            {name: value for name, value in zip(self._input_names, (mix, *caches), strict=True)},
        )
        enh = outputs[0]
        new_caches = (outputs[1], outputs[2], outputs[3])
        self._caches = new_caches

        # 4. ISTFT: inverse transform the enhanced spectrum, apply the
        synth = np.fft.irfft(enh[0, :, 0, 0] + 1j * enh[0, :, 0, 1], n=N_FFT)
        np.multiply(synth, self._window, out=synth)
        out = self._out_buf
        np.add(synth[:HOP], self._olap_buf, out=out)
        np.copyto(self._olap_buf, synth[HOP:])

        # 5. The consumed hop becomes the next analysis frame's tail.
        np.copyto(self._prev_tail_buf, normalized)

        # Return a FRESH array, ``self._out_buf`` is reused on the next
        return out.copy(), new_caches

    def reset(self) -> None:
        """Clear ALL streaming state (caches, tails, buffers)."""
        for cache in self._caches:
            cache.fill(0)
        self._frame_buf.fill(0)
        self._prev_tail_buf.fill(0)
        self._olap_buf.fill(0)
        self._out_buf.fill(0)
        self._mix_buf.fill(0)

    def _normalize_hop(self, hop: np.ndarray) -> np.ndarray:
        """Zero-pad / truncate ``hop`` to exactly ``HOP`` samples."""
        if hop.size < HOP:
            padded = np.zeros(HOP, dtype=np.float32)
            padded[: hop.size] = hop
            return padded
        return hop[:HOP]

    @property
    def caches(self) -> tuple[np.ndarray, ...]:
        """The current recurrent-cache tuple (read-only convenience)."""
        return self._caches
