"""GTCRN ONNX streaming noise-suppression backend.

This module wraps the bundled ``gtcrn_simple.onnx`` model (GTCRN —
"Lightweight Target Speaker Extraction Network", ICASSP 2024, MIT
license, ~48K parameters) as a self-contained streaming denoiser. It
replaces the historical DeepFilterNet option, whose PyPI package is
unmaintained and whose processing path was never wired into this
codebase — the ``noisy_room`` audio preset now selects this backend
instead.

Streaming contract (mirrors the upstream streaming demo):
    - The model is native 16 kHz mono and consumes exactly ONE
      256-sample hop (16 ms) per inference call.
    - Per hop, a 512-sample analysis frame is assembled from the
      previous hop's 256 samples (the "tail") plus the new hop. The
      very first hop is zero-padded on its left half.
    - The frame is multiplied by a periodic sqrt-Hann window (512
      points), transformed with a real FFT into 257 bins, and fed to
      ``onnxruntime`` as ``mix[1, 257, 1, 2]`` where ``[..., 0]`` is
      the real part and ``[..., 1]`` the imaginary part.
    - The model returns the enhanced spectrum ``enh[1, 257, 1, 2]``
      plus three recurrent state caches (``conv_cache``, ``tra_cache``,
      ``inter_cache``). The caches MUST be threaded into the next
      call — they carry the GRU / conv state across hops and persist
      for the whole session.
    - The enhanced spectrum is inverse-transformed, multiplied by the
      SAME sqrt-Hann synthesis window (sqrt-Hann x sqrt-Hann = Hann,
      which satisfies COLA at the 256-sample / 50% overlap), and
      overlap-added with the previous synthesis window's tail.

Because each analysis window spans TWO hops, the block emitted by a
given ``process_hop`` call covers the PREVIOUS hop's time span: the
output stream trails the input stream by exactly one hop (16 ms at
16 kHz). That algorithmic delay is inherent to overlap-add streaming
STFT denoisers and is surfaced via ``NoiseSuppressor.latency_ms``.

Offline guarantee: the model file is BUNDLED next to
``silero_vad.onnx`` in ``voice_typer/server/`` and loaded through
``onnxruntime.InferenceSession(providers=["CPUExecutionProvider"])`` —
no network call is ever made. If ``onnxruntime`` is missing or the
bundled file is missing/corrupt, construction raises (with a
rate-limited ERROR log) and :class:`NoiseSuppressor` degrades to the
RNNoise backend at init time.
"""

from __future__ import annotations

import logging
from pathlib import Path

from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.log_rate_limit import log_rate_limited

np = lazy_module("numpy")

log = logging.getLogger(__name__)

# Path to the bundled GTCRN ONNX model — sits beside ``silero_vad.onnx``
# in ``voice_typer/server/`` so the frozen bundle's
# ``--include-package-data=voice_typer.server`` picks it up (same
# packaging story as the Silero VAD ONNX model; see MANIFEST.in).
MODEL_PATH = Path(__file__).resolve().parent.parent / "gtcrn_simple.onnx"

# Streaming STFT geometry (upstream GTCRN streaming semantics): 512-point
# window, 256-sample hop. At the model's native 16 kHz the hop is 16 ms.
N_FFT: int = 512
HOP: int = 256
NUM_BINS: int = N_FFT // 2 + 1  # 257 real-FFT bins

# Recurrent-cache shapes exported by the model graph. The three caches
# thread the conv/GRU state across hops; they persist for the whole
# session and are re-zeroed by ``reset()``. Order matches the model's
# input/output ordering: (conv_cache, tra_cache, inter_cache).
CACHE_SHAPES: tuple[tuple[int, ...], ...] = (
    (2, 1, 16, 16, 33),
    (2, 3, 1, 1, 16),
    (2, 1, 33, 16),
)

# One warmup inference at construction pays the ORT graph-optimization
# / arena-allocation cost at INIT time (not on the first audio chunk of
# the RT thread) and eagerly validates the full I/O contract (input
# names, cache shapes, output arity) so any export-variant mismatch
# surfaces as an init failure → RNNoise fallback, never as a
# mid-dictation crash.

# Rate-limit cadence for the construction failure logs. The suppressor
# constructs a backend once per config change / session; first-only
# (``every_n=0``) keeps a single diagnostic line without spam when the
# model is permanently unavailable (onnxruntime missing, file deleted).
_LOAD_FAILURE_EVERY_N: int = 0


def is_available() -> bool:
    """Check if the GTCRN backend can load (onnxruntime + bundled model).

    Mirrors ``vad.is_available``: returns ``True`` only when (a)
    ``onnxruntime`` is importable AND (b) the bundled
    ``gtcrn_simple.onnx`` exists on disk — so constructing a
    :class:`GtcrnBackend` will succeed without a network round-trip.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return MODEL_PATH.exists()


class GtcrnBackend:
    """Streaming GTCRN denoiser over a bundled ONNX ``InferenceSession``.

    Usage::

        backend = GtcrnBackend()          # raises on any load failure
        for hop in hop_generator:         # 256-sample float32 hops @16 kHz
            enhanced = backend.process_hop(hop)   # 256 float32 samples
        backend.reset()                   # between sessions

    ``process_hop`` returns the enhanced PREVIOUS hop (one hop of
    algorithmic delay — see the module docstring). The recurrent caches
    are managed internally; pass ``caches=`` explicitly only to thread
    state yourself (tests). The tuple returned alongside the audio is
    the updated cache tuple, exposed for state-persistence assertions.
    """

    def __init__(self) -> None:
        try:
            import onnxruntime as _ort
        except ImportError as exc:
            log_rate_limited(
                log,
                logging.ERROR,
                "[GTCRN] onnxruntime not importable — GTCRN noise suppression unavailable",
                every_n=_LOAD_FAILURE_EVERY_N,
            )
            raise RuntimeError("onnxruntime not importable — cannot load the GTCRN model") from exc

        if not MODEL_PATH.exists():
            log_rate_limited(
                log,
                logging.ERROR,
                "[GTCRN] bundled model not found at %s — GTCRN noise suppression "
                "unavailable (no network fetch is attempted)",
                MODEL_PATH,
                every_n=_LOAD_FAILURE_EVERY_N,
            )
            raise RuntimeError(f"bundled GTCRN model not found at {MODEL_PATH}")

        try:
            # CPU-only by design (mirrors vad.py): the model is tiny
            # (~48K params, ~0.5 MB) and runs in ~2 ms per hop on CPU —
            # a GPU round-trip per 16 ms hop would cost more than the
            # inference itself. Pinning the provider also keeps the
            # onnxruntime-gpu default (CUDA first) from silently
            # routing this hot path through a device copy.
            session = _ort.InferenceSession(
                str(MODEL_PATH),
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            log_rate_limited(
                log,
                logging.ERROR,
                "[GTCRN] bundled ONNX model load failed: %s — GTCRN noise "
                "suppression unavailable (no network fetch is attempted)",
                exc,
                every_n=_LOAD_FAILURE_EVERY_N,
            )
            raise RuntimeError(f"failed to load the bundled GTCRN model: {exc}") from exc

        # Discover the input/output names from the graph instead of
        # hardcoding them, so an export variant that renames a slot
        # fails loudly at the warmup below rather than silently
        # mis-binding a cache.
        self._input_names = [i.name for i in session.get_inputs()]
        if len(self._input_names) != 1 + len(CACHE_SHAPES):
            raise RuntimeError(f"unexpected GTCRN graph input arity: {self._input_names!r}")
        self._session = session

        # sqrt-Hann analysis/synthesis window, designed ONCE. Periodic
        # (fftbins=True) Hann: sqrt(Hann) analysis + sqrt(Hann)
        # synthesis == Hann, and Hann sums to 1 at 50% overlap, so the
        # pair is perfectly reconstructing (verified by the identity
        # round-trip tests).
        from scipy.signal import get_window

        self._window = np.sqrt(get_window("hann", N_FFT, fftbins=True)).astype(np.float32)

        # ── Pre-allocated hot-path buffers (one allocation each, reused
        # across every hop) ────────────────────────────────────────────
        # 512-sample analysis frame (previous tail || new hop).
        self._frame_buf = np.zeros(N_FFT, dtype=np.float32)
        # Model input tensor mix[1, 257, 1, 2].
        self._mix_buf = np.zeros((1, NUM_BINS, 1, 2), dtype=np.float32)
        # Previous hop's 256 samples (the analysis-frame left half).
        self._prev_tail_buf = np.zeros(HOP, dtype=np.float32)
        # Overlap-add accumulator: the previous synthesis window's right
        # half, added into the next emitted block.
        self._olap_buf = np.zeros(HOP, dtype=np.float32)
        # The 256-sample output block emitted per hop.
        self._out_buf = np.zeros(HOP, dtype=np.float32)

        # Recurrent caches — zeroed at construction, threaded across
        # every ``process_hop`` call, re-zeroed by ``reset()``.
        self._caches: tuple[np.ndarray, ...] = tuple(np.zeros(shape, dtype=np.float32) for shape in CACHE_SHAPES)

        # Warmup hop (zeros): validates the full I/O contract at init
        # and pays the first-inference cost here rather than on the
        # audio thread. State is reset right after so the warmup cannot
        # bleed into real audio.
        self.process_hop(np.zeros(HOP, dtype=np.float32))
        self.reset()

    # ── Public API ────────────────────────────────────────────────────

    def process_hop(
        self,
        hop: np.ndarray,
        caches: tuple[np.ndarray, ...] | None = None,
    ) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
        """Denoise one 256-sample hop; return ``(enhanced_hop, new_caches)``.

        Args:
            hop: float32 array of 256 samples at the model's native
                16 kHz. Shorter inputs are zero-padded, longer ones
                truncated (defensive — the suppressor always feeds
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
        # Defensive normalization — never crash the audio thread on a
        # malformed hop length (short → zero-padded, long → truncated).
        normalized = hop if hop.size == HOP else self._normalize_hop(hop)

        # 1. Assemble the 512-sample analysis frame: previous hop's tail
        #    (zeros on the very first hop — the zero-padded edge) + hop.
        frame = self._frame_buf
        frame[:HOP] = self._prev_tail_buf
        frame[HOP:] = normalized

        # 2. Analysis window (in-place) + real FFT → 257 bins.
        np.multiply(frame, self._window, out=frame)
        spec = np.fft.rfft(frame)
        mix = self._mix_buf
        mix[0, :, 0, 0] = spec.real
        mix[0, :, 0, 1] = spec.imag

        # 3. Inference — thread the recurrent caches through. Arity is
        # validated at init (len(input_names) == 1 + len(CACHE_SHAPES)),
        # so a length mismatch here is impossible by construction.
        outputs = self._session.run(
            None,
            {name: value for name, value in zip(self._input_names, (mix, *caches), strict=True)},
        )
        enh = outputs[0]
        new_caches = (outputs[1], outputs[2], outputs[3])
        self._caches = new_caches

        # 4. ISTFT: inverse transform the enhanced spectrum, apply the
        #    same sqrt-Hann synthesis window, overlap-add with the
        #    previous synthesis window's tail.
        synth = np.fft.irfft(enh[0, :, 0, 0] + 1j * enh[0, :, 0, 1], n=N_FFT)
        np.multiply(synth, self._window, out=synth)
        out = self._out_buf
        np.add(synth[:HOP], self._olap_buf, out=out)
        np.copyto(self._olap_buf, synth[HOP:])

        # 5. The consumed hop becomes the next analysis frame's tail.
        #    Copied into the persistent buffer so a caller-owned
        #    transient array is never retained by reference.
        np.copyto(self._prev_tail_buf, normalized)

        # Return a FRESH array — ``self._out_buf`` is reused on the next
        # call, so handing it out by reference would alias.
        return out.copy(), new_caches

    def reset(self) -> None:
        """Clear ALL streaming state (caches, tails, buffers).

        Between recording sessions the recurrent caches and the
        overlap-add tail of the previous session's audio would bleed
        into the next one; zeroing them in place also keeps derived
        (voice-adjacent) samples from lingering in process memory
        until the numpy allocator reuses the blocks — the same
        privacy rationale as ``NoiseSuppressor.reset``.
        """
        for cache in self._caches:
            cache.fill(0)
        self._frame_buf.fill(0)
        self._prev_tail_buf.fill(0)
        self._olap_buf.fill(0)
        self._out_buf.fill(0)
        self._mix_buf.fill(0)

    # ── Internals ─────────────────────────────────────────────────────

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
