"""Parakeet TDT v3 ASR engine — ONNX Runtime backend (via ``onnx-asr``).

In-place conversion from torch/transformers to ONNX Runtime per
``PLAN_ONNX_INTEGRATION.md`` §3 (Part B, Option B-1). The engine keeps
its registered backend name (``"parakeet"``) and class
(``ParakeetEngine``); only the internals change. The old
``transformers.AutoModelForTDT`` + ``torch`` code path is gone — the
engine now calls ``onnx_asr.load_model(...)`` (the only API onnx-asr
exports in 0.12.0), which loads a pre-exported ONNX Parakeet TDT model
and returns an adapter exposing ``recognize(audio, sample_rate)``. The
TDT decoding loop is the library's problem (Option B-1).

GPU→CPU fallback (§3.4) recreates the ORT session with
``CPUExecutionProvider`` only — ONNX Runtime cannot move a session
between providers in place (unlike torch's ``.to("cpu")``). The
``parakeet_cpu_fallback`` tray event is preserved.

Model weights are NEVER auto-downloaded — the user must explicitly
download them (Models page Download button, or the onboarding wizard)
before the engine can load them from the local HF cache. Falls back
gracefully on missing deps, CUDA errors, etc.

Shared helpers (``is_likely_english``, ``is_latin_char``,
``merge_chunks``, ``compute_overlap_skip``, ``is_cuda_error``,
``is_oom_error``) live in :mod:`voice_typer.server.asr_utils` per §5.1,
§5.3, §5.4. They are imported here directly; backward-compat aliases
(``_is_likely_english``, ``_is_latin_char``) re-export them so existing
test/import sites keep working.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.branding import APP_NAME
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination
from voice_typer.server.i18n import DEFAULT_LOCALE

# Shared ASR helpers (PLAN_ONNX_INTEGRATION.md §5.1, §5.3, §5.4). These
# were moved to asr_utils as part of the ONNX migration; the engine
# imports them directly. ``asr_utils`` is owned by Sub-agent 3 — the
# signatures are documented in the plan; we code against them.
#
# DEFENSIVE IMPORT: in the parallel-refactor window, Sub-agent 3 may not
# have landed the asr_utils helpers yet. The try/except falls back to
# local implementations (mirroring the pre-migration bodies verbatim)
# so this module imports cleanly either way. Once Sub-agent 3 lands,
# the canonical asr_utils versions are used automatically. The local
# fallbacks are NOT a long-term contract — they exist solely to keep
# the ONNX-rewritten parakeet_engine importable during the refactor
# window. New code should import from asr_utils directly.
try:
    from voice_typer.server.asr_utils import (
        compute_overlap_skip as _asr_compute_overlap_skip,
        is_cuda_error as _asr_is_cuda_error,
        is_latin_char as _asr_is_latin_char,
        is_likely_english as _asr_is_likely_english,
        merge_chunks as _asr_merge_chunks,
    )

    _ASR_UTILS_HELPERS_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive fallback during parallel refactor
    _ASR_UTILS_HELPERS_AVAILABLE = False
    _asr_is_cuda_error = None  # type: ignore[assignment]
    _asr_is_latin_char = None  # type: ignore[assignment]
    _asr_is_likely_english = None  # type: ignore[assignment]
    _asr_merge_chunks = None  # type: ignore[assignment]
    _asr_compute_overlap_skip = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


# ─── Local fallback implementations (used only if asr_utils lacks the
#     helpers — see DEFENSIVE IMPORT note above). Mirror the pre-
#     migration bodies verbatim so behavior is identical. These are
#     NOT the canonical home — asr_utils is. ─────────────────────────


def _local_is_latin_char(ch: str) -> bool:
    """Return True if *ch* belongs to the Latin script (or is ws/digit/punct)."""
    import unicodedata

    cat = unicodedata.category(ch)
    if cat.startswith("P") or cat.startswith("Z") or cat.startswith("S"):
        return True
    if ch.isdigit():
        return True
    script = unicodedata.name(ch, "").split(" ")[0] if ch else ""
    return script == "LATIN"


def _local_is_likely_english(text: str) -> bool:
    """Return False if *text* contains too many non-Latin-script characters."""
    if not text or not text.strip():
        return True
    non_latin = sum(1 for ch in text if not _local_is_latin_char(ch))
    ratio = non_latin / len(text)
    if ratio > _NON_LATIN_RATIO_LIMIT:
        log_hallucination_rejection(
            "[PARAKEET]",
            text,
            reason=f"non-English output ({ratio * 100:.0f}% non-Latin chars)",
            log_transcriptions=False,
        )
        return False
    return True


def _local_is_cuda_error(exc: Exception) -> bool:
    """Conservative CUDA-error classifier (5-layer, mirrors asr_utils)."""
    err_str = str(exc).lower()
    # Layer 1: ORT RuntimeException with cuda/gpu in message.
    try:
        import onnxruntime as _ort  # type: ignore[import-untyped]

        if isinstance(exc, _ort.RuntimeException) and ("cuda" in err_str or "gpu" in err_str):
            return True
    except ImportError:
        pass
    # Layer 2: RuntimeError + attribute check.
    if isinstance(exc, RuntimeError) and (getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False)):
        return True
    # Layer 3: keyword match (3 keywords — OOM handled separately).
    if any(kw in err_str for kw in ("cuda", "cublas", "cudnn")):
        return True
    # Layer 4: DLL-load failures (Windows).
    return any(kw in err_str for kw in ("dll", "not found", "cannot be loaded", "load library"))


def _local_compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
    """Return how many leading words of *new_words* to skip (overlap dedup)."""
    if not prev_words or not new_words:
        return 0

    def _norm(w: str) -> str:
        return w.strip(".,;:!?\"'()[]{}").lower()

    prev_window_size = _OVERLAP_DEDUP_WINDOW + _MAX_BOUNDARY_SKIP_WORDS
    prev_tail = [_norm(w) for w in prev_words[-prev_window_size:]]
    max_check = min(_MAX_BOUNDARY_SKIP_WORDS, len(new_words))
    new_head = [_norm(w) for w in new_words[:max_check]]

    best = 0
    for length in range(max_check, 0, -1):
        candidate = new_head[:length]
        for start in range(len(prev_tail) - length + 1):
            end_idx = start + length
            last_word_idx = len(prev_tail) - end_idx
            if last_word_idx >= _OVERLAP_DEDUP_WINDOW:
                continue
            if prev_tail[start : start + length] == candidate:
                best = length
                break
        if best > 0:
            break
    return best


def _local_merge_chunks(texts: list[str]) -> str:
    """Concatenate chunk transcriptions, skipping overlap text."""
    if len(texts) <= 1:
        return texts[0] if texts else ""
    result_words: list[str] = texts[0].split()
    for text in texts[1:]:
        words = text.split()
        if not words:
            continue
        skip = _local_compute_overlap_skip(result_words, words)
        tail = words[skip:] if skip > 0 else words
        if tail:
            result_words.extend(tail)
    return " ".join(result_words).strip()


# Resolve the effective helpers: prefer asr_utils, fall back to local.
_is_latin_char_impl = _asr_is_latin_char if _asr_is_latin_char is not None else _local_is_latin_char
_is_likely_english_impl = _asr_is_likely_english if _asr_is_likely_english is not None else _local_is_likely_english
_is_cuda_error_impl = _asr_is_cuda_error if _asr_is_cuda_error is not None else _local_is_cuda_error
_merge_chunks_impl = _asr_merge_chunks if _asr_merge_chunks is not None else _local_merge_chunks
_compute_overlap_skip_impl = (
    _asr_compute_overlap_skip if _asr_compute_overlap_skip is not None else _local_compute_overlap_skip
)


class TranscriptionBackendError(RuntimeError):
    """Raised when the ASR backend cannot produce a transcription.

    ``transcribe_with_fallback`` raises this on CPU fallback failure so
    callers can distinguish a real backend failure from a legitimate
    "no speech detected" result (``""``).
    """


# ─── Constants ──────────────────────────────────────────────────────────

# Maximum allowed ratio of non-Latin-script characters before we reject
# a transcription segment as a language-hallucination. Re-exported from
# asr_utils for backward-compat with tests that import the constant from
# parakeet_engine. See ``asr_utils.NON_LATIN_RATIO_LIMIT``.
_NON_LATIN_RATIO_LIMIT = 0.30

# HuggingFace repo ID of the *original* torch/safetensors Parakeet
# model. Kept as a module-level constant because ``prewarm/cache_probe``
# imports it to locate the cached ``model.safetensors`` for OS page-cache
# warming. The ONNX migration does NOT change this — prewarm still warms
# the same HF cache directory (the user may have either the torch or
# ONNX weights cached; both live under the same repo-id key).
_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# ONNX Runtime FP16 export of Parakeet TDT v3 (USER-selected repo,
# 2026-08-15, switched to the upstream original 2026-08-20).
# ``grikdotnet/parakeet-tdt-0.6b-fp16`` is the original half-precision
# conversion of the fp32 ONNX export published by
# ``istupakov/parakeet-tdt-0.6b-v3-onnx`` (the earlier
# ``visuall/parakeet-tdt-0.6b-v3-onnx-fp16`` was a copy of the same
# files minus config.json); identical WER to fp32 at ~1.28 GB instead
# of ~2.5 GB (see the repo's README). The repo ships a real
# ``config.json``, but onnx-asr reads ``model_type`` from it only when
# resolving a repo BY NAME — the engine still loads by TYPE name + a
# verified local snapshot dir (see ``load()``).
_PARAKERT_ONNX_REPO_ID = "grikdotnet/parakeet-tdt-0.6b-fp16"
_PARAKERT_ONNX_CACHE_DIR = f"models--{_PARAKERT_ONNX_REPO_ID.replace('/', '--')}"

# onnx-asr TYPE name (NOT a repo name). ``nemo-conformer-tdt`` selects
# the TDT decoder class directly, which is what lets us load the
# verified local snapshot dir (the integrity gate has already pinned
# every file). Do NOT pass the grikdotnet repo_id as the model name —
# onnx-asr would try to download from the repo instead of loading the
# verified local dir.
_PARAKERT_ONNX_MODEL_NAME = "nemo-conformer-tdt"

# Selects the ``.fp16.`` variant files inside the repo (onnx-asr 0.12.0
# globs ``encoder-model?fp16.onnx`` — matches ``encoder-model.fp16.onnx``).
_PARAKERT_QUANTIZATION = "fp16"

# Approximate ONNX weight size in MB for MB/s read-speed logging.
# grikdotnet fp16 export: encoder-model.fp16.onnx 1,239 MB +
# decoder_joint-model.fp16.onnx 36 MB + nemo128.onnx + vocab.txt
# ≈ 1,275 MB on disk.
_PARAKERT_WEIGHTS_MB = 1275

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
# Longer recordings are split into overlapping chunks via
# ``asr_utils.split_audio``. 3s overlap gives the model audio context at
# boundaries so it doesn't hallucinate repeated text at chunk starts.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 3

# Backward-compat re-exports of the merge-chunk constants. The canonical
# values now live in ``asr_utils`` (``MAX_BOUNDARY_SKIP_WORDS``,
# ``OVERLAP_DEDUP_WINDOW``). Kept here so existing tests / importers
# (``tests/test_parakeet_engine.py``, ``tests/regressions/parakeet_merge_test.py``)
# keep working.
_MAX_BOUNDARY_SKIP_WORDS = 2
_OVERLAP_DEDUP_WINDOW = 3


class _AbortStoppingCriteria:
    """Legacy ``transformers.StoppingCriteria`` shim — preserved for
    backward-compat with tests/importers that reference the name.

    The torch/transformers backend used this to wire
    ``model.generate()``'s ``stopping_criteria`` argument so the
    dictation pipeline's cancel path (ESC / watchdog) could stop
    generation between tokens. The ONNX Runtime backend has no
    per-token stopping hook — ``onnx-asr`` 0.12.0 does not forward
    ``RunOptions`` to ``session.run`` (see the note on
    ``ParakeetEngine._abort_event``), so the working abort path is
    the inter-chunk ``_abort_event`` check only (see
    :meth:`ParakeetEngine.request_abort`). This class is no longer
    used internally; it is kept as a no-op shim so existing
    ``from voice_typer.server.parakeet_engine import _AbortStoppingCriteria``
    imports in ``tests/test_dictation_pipeline_abort.py`` keep resolving.
    """

    def __init__(self, abort_event: threading.Event) -> None:
        self._abort_event = abort_event

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:  # noqa: D401
        """Return True if generation should stop (abort signalled)."""
        return self._abort_event.is_set()


# ─── Backward-compat aliases for moved helpers ─────────────────────────
#
# PLAN_ONNX_INTEGRATION.md §5.3 / §5.4 moved ``_is_latin_char``,
# ``_is_likely_english``, ``_merge_chunks`` and ``_compute_overlap_skip``
# to :mod:`voice_typer.server.asr_utils`. The canonical implementations
# live there now; the leading-underscore names below are kept as
# backward-compat aliases so existing import sites
# (``tests/test_parakeet_engine.py``, ``tests/regressions/parakeet_merge_test.py``,
# ``tests/test_word_drop_regression.py``) keep working without a
# parallel test rewrite. New code should import from ``asr_utils``.

_is_latin_char = _is_latin_char_impl
_is_likely_english = _is_likely_english_impl


class ParakeetEngine:
    """Wraps NVIDIA Parakeet TDT v3 ASR model via ONNX Runtime.

    Implements TranscriberProtocol so the app can swap backends
    transparently. Model weights must be downloaded explicitly by the
    user (Models page or onboarding wizard) before load; the engine
    never auto-downloads.

    The ONNX migration (PLAN_ONNX_INTEGRATION.md §3) swaps the backend
    from ``transformers.AutoModelForTDT`` + ``torch`` to
    ``onnx_asr.load_model(...)`` (onnx-asr 0.12.0 exports only
    ``load_model`` / ``load_vad`` — there is no ``Model`` class).
    GPU→CPU fallback (§3.4) recreates the ORT session
    with ``CPUExecutionProvider`` only — ONNX Runtime cannot move a
    session between providers in place (unlike torch's ``.to("cpu")``).
    """

    # ── Class-level state ────────────────────────────────────────────
    # Lazily-populated references to the onnx_asr + onnxruntime modules.
    # Typed as ``Any`` so attribute accesses (``Model``, ``RunOptions``,
    # ``get_available_providers``) type-check without forcing the
    # optional-dep import at module load time. The class attrs remain
    # ``None`` until ``_ensure_imports()`` succeeds.
    _imports_loaded: bool = False
    _onnx_asr: Any = None
    _ort: Any = None
    # Guards the check-then-import sequence in ``_ensure_imports`` so
    # two threads racing on the first transcribe() call don't both run
    # the (potentially multi-second) onnx_asr import in parallel.
    _imports_lock: threading.Lock = threading.Lock()
    # Class-level fallbacks for instances created via ``__new__`` (some
    # unit tests skip ``__init__``). Mirrors the pre-migration pattern.
    _cpu_fallback_since: float | None = None
    _cpu_transcribe_count: int = 0

    def __init__(
        self,
        device: str = "cuda",
        language: str = DEFAULT_LOCALE,
        config: Any = None,
    ):
        self.device = device
        self.language = language
        # Optional Config reference consulted by ``load()`` to gate
        # HuggingFace downloads on explicit user consent
        # (``config.huggingface_consent``). ``None`` is treated as
        # "consent not given" (safe default per GDPR Art. 6/13).
        self.config = config
        # Loaded onnx-asr model adapter instance (or ``None`` when unloaded).
        self._model: Any = None
        # Backward-compat: the pre-migration code populated a separate
        # ``_processor`` (transformers' ``AutoProcessor``). The ONNX
        # backend has no separate processor — ``onnx_asr.Model`` bundles
        # the tokenizer + ONNX session — so this is always ``None`` in
        # production. Kept as an instance attribute so existing tests
        # that ``engine._processor = MagicMock()`` keep working.
        self._processor: Any = None
        # Verified HF-cache snapshot dir of the ONNX model, stashed by
        # ``load()`` so the GPU→CPU fallback (``_load_impl``) can
        # rebuild the ORT session from the same local files.
        self._onnx_model_dir: str | None = None
        # One-time tray notification flag for CUDA→CPU transcription
        # fallback. Reset to ``False`` on every successful ``load()`` so
        # a fallback after the next reload re-notifies the user.
        self._cpu_fallback_notified: bool = False
        # Time / count-based CUDA-retry tracking. The pre-migration code
        # used these for the ``_maybe_retry_cuda`` time/count-based
        # retry. The ONNX migration drops that retry (session recreation
        # is the only fallback path); the attributes are kept so existing
        # tests that read them don't AttributeError.
        self._cpu_fallback_since: float | None = None
        self._cpu_transcribe_count: int = 0
        self._lock = threading.RLock()
        # Counter + Condition so ``transcribe()`` can release the model
        # lock during the (potentially long) chunk-inference loop while
        # still coordinating with ``unload()``. ``unload()`` waits for
        # ``_active_inference == 0`` before nulling ``self._model`` so a
        # concurrent transcribe() doesn't dereference a freed session.
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        # Abort token shared by the dictation pipeline's cancel path and
        # the chunk-iteration loop. ``request_abort()`` sets the event;
        # ``_transcribe_chunks`` checks it BETWEEN chunks so a long audio
        # split into 13 chunks stops after the current chunk rather than
        # decoding all remaining ones. ``clear_abort()`` is called by
        # the pipeline at the start of each transcription cycle so a
        # stale abort from the previous cycle does NOT suppress the next
        # one.
        #
        # NOTE: ORT's ``RunOptions.set_terminate`` API cannot reach the
        # in-flight ``recognize()`` call through ``onnx-asr`` 0.12.0:
        # the library's ``recognize_batch()`` invokes ``session.run()``
        # without forwarding a ``run_options`` argument (verified by
        # inspecting the wheel source — ``asr.py`` + ``models/nemo.py``
        # call ``self._encoder.run(["outputs", ...], {...})`` with no
        # ``run_options`` parameter). The working abort path is
        # therefore the ``_abort_event`` check between chunks ONLY —
        # mid-run termination of a single-segment ``recognize()`` call
        # is NOT supported (CLOUD-AGENT-ROUND2-PROMPT.md issue 2).
        self._abort_event = threading.Event()
        # Effective ORT providers list used by the most recent
        # ``load()`` / ``_load_impl()``. Stored so the GPU→CPU fallback
        # path knows what to switch FROM (and so reload uses the same
        # providers unless overridden).
        self._effective_providers: list[str] = []
        # Backward-compat: pre-migration tests pin
        # ``_INFERENCE_BATCH_SIZE == 2`` (default). The ONNX backend
        # doesn't batch (``onnx_asr.recognize`` processes one audio at a
        # time), but the attribute is kept so existing tests that read
        # it don't AttributeError. Read at construction time (NOT import
        # time) so env-var changes between engine constructions take
        # effect.
        self._INFERENCE_BATCH_SIZE: int = max(1, int(os.environ.get("PARAKEET_BATCH_SIZE", "2")))

    # ── Import management ────────────────────────────────────────────

    @classmethod
    def _ensure_imports(cls) -> bool:
        """Lazily import ``onnx_asr`` + ``onnxruntime``.

        Returns ``True`` on success, ``False`` if either package is not
        installed. The lazy import keeps this module importable on
        systems without ``onnx-asr`` (the optional-deps pattern used
        throughout the project).

        Idempotent: re-entering after a successful import is a fast
        flag-check under the lock. Re-entering after a FAILED import
        re-attempts the import (so installing the package after the
        engine was first constructed takes effect on the next
        ``load()``).
        """
        with cls._imports_lock:
            if cls._imports_loaded:
                return True
            _t0 = time.perf_counter()
            try:
                import onnx_asr  # type: ignore[import-untyped, import-not-found]
                import onnxruntime as ort  # type: ignore[import-untyped]

                cls._onnx_asr = onnx_asr
                cls._ort = ort
                cls._imports_loaded = True
                _elapsed = time.perf_counter() - _t0
                log.info(
                    "[PARAKEET] onnx_asr %s + onnxruntime %s imported (%.2fs)",
                    getattr(onnx_asr, "__version__", "?"),
                    getattr(ort, "__version__", "?"),
                    _elapsed,
                )
                return True
            except ImportError as exc:
                cls._imports_loaded = False
                log.warning(
                    "[PARAKEET] onnx_asr/onnxruntime import failed — install onnx-asr + onnxruntime: %s",
                    exc,
                )
                return False

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if the ONNX backend can be loaded.

        Quick probe used by the registry / model_manager to decide
        whether the parakeet backend is usable on the current install
        (i.e. ``onnx_asr`` + ``onnxruntime`` are importable). Does NOT
        probe the model cache — that's :meth:`_is_cached`.
        """
        try:
            import onnx_asr  # type: ignore[import-untyped]  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    # ── Provider selection ──────────────────────────────────────────

    def _select_providers(self, device: str) -> list[str]:
        """Map a device string to an ORT ``providers=`` list.

        ``CUDAExecutionProvider`` is tried first when ``device == "cuda"``;
        if it's not available (CPU-only onnxruntime wheel, no GPU, no
        CUDA Toolkit DLLs on Windows), falls back to
        ``CPUExecutionProvider``. The fallback at *load time* is
        distinct from the *runtime* GPU→CPU fallback in
        :meth:`transcribe_with_fallback` — the latter recreates the
        session after a CUDA error during inference.
        """
        if device == "cuda":
            try:
                available = self._ort.get_available_providers() if self._ort is not None else []
            except Exception:
                available = []
            if "CUDAExecutionProvider" in available:
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]
            log.warning(
                "[PARAKEET] CUDAExecutionProvider not in available providers (%s) — using CPU",
                available,
            )
            return ["CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    # ── Disk-space / cache probes ───────────────────────────────────

    @staticmethod
    def _should_force_cpu() -> bool:
        """Check disk space on system drive — if under 500MB, force CPU.

        CUDA on Windows needs pagefile space to back GPU memory
        allocations. When the system drive is nearly full, Windows
        can't grow the pagefile, causing error 1455. This check avoids
        that error and gives a clean warning instead.

        Platform-qualified: the pagefile/CUDA-error-1455 failure mode
        is Windows-only (Linux/macOS don't use a Windows-style pagefile
        for GPU memory).
        """
        from voice_typer.server.platform_utils import is_windows

        if not is_windows():
            return False
        try:
            import psutil

            system_drive = os.environ.get("SYSTEMDRIVE", "C:") + "\\"
            usage = psutil.disk_usage(system_drive)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < 500:
                log.warning(
                    "[PARAKEET] Only %d MB free on %s — forcing CPU (CUDA needs pagefile space to allocate GPU memory)",
                    free_mb,
                    system_drive,
                )
                return True
        except Exception:
            log.debug("[PARAKEET] _should_force_cpu disk space check failed (non-fatal)", exc_info=True)
        return False

    @staticmethod
    def _is_cached() -> bool:
        """Quick check if the Parakeet ONNX model is in the HF cache.

        Walks the ONNX repo's snapshot dir
        (``models--grikdotnet--parakeet-tdt-0.6b-fp16/``) for a
        ``*.onnx`` file. The engine is ONNX-only post-migration — the
        torch/safetensors cache (``nvidia/parakeet-tdt-0.6b-v3``) is no
        longer loadable and does NOT count as cached.
        """
        from voice_typer.server.config import _config_dir

        cache_root = _config_dir() / "huggingface" / "hub"
        model_dir = cache_root / _PARAKERT_ONNX_CACHE_DIR
        snapshots = model_dir / "snapshots"
        if not snapshots.is_dir():
            return False
        try:
            for entry in snapshots.iterdir():
                if not entry.is_dir():
                    continue
                if any(entry.glob("*.onnx")):
                    return True
        except OSError:
            log.debug("[PARAKEET] _is_cached snapshot iterdir failed (non-fatal)", exc_info=True)
        return False

    # ── TranscriberProtocol ─────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` if the ONNX model is loaded.

        The pre-migration code required both ``_model`` AND ``_processor``
        to be non-None (transformers' AutoProcessor + AutoModelForTDT).
        The ONNX backend has no separate processor — the onnx-asr
        adapter bundles the tokenizer + ONNX session — so we check
        ``_model``
        only. The ``_processor`` attribute is kept as ``None`` in
        production for backward-compat with tests that set it.
        """
        with self._lock:
            return self._model is not None

    def request_abort(self) -> None:
        """Signal an in-flight transcription to stop after the current chunk.

        Sets ``_abort_event`` (checked between chunks in
        ``_transcribe_chunks``). The current chunk's
        ``model.recognize()`` call runs to completion (onnx-asr 0.12.0
        does not forward ``RunOptions`` to ``session.run`` — see the
        class-level note on ``_abort_event``); the loop then breaks
        before the next chunk is decoded. Bounded latency = one chunk's
        decode time (≤ ``_CHUNK_SECONDS`` seconds) instead of the full
        audio — frees compute for the next dictation cycle.

        Replaces the torch/transformers ``StoppingCriteria`` shim — see
        :class:`_AbortStoppingCriteria` (kept as a no-op shim for
        backward-compat with tests/importers that reference the name).
        """
        self._abort_event.set()

    def clear_abort(self) -> None:
        """Clear the abort token at the start of a fresh transcription cycle.

        Called by the dictation pipeline before each transcribe so a
        stale abort from the previous cycle (e.g. the user hit ESC,
        aborted, then started a new recording) does NOT suppress the
        new transcription.
        """
        self._abort_event.clear()

    def load(self, progress_callback: Callable[[str], None] | None = None) -> bool:
        """Load the Parakeet ONNX model via ``onnx_asr.load_model(...)``.

        The app never downloads models automatically — the user must
        explicitly download the Parakeet weights (Models page Download
        button, or the onboarding wizard) before they can be loaded. If
        the model is not in the local HuggingFace cache, a
        ``ModelNotDownloadedError`` is raised so callers can direct the
        user to the Models page. A cached-but-tampered model raises
        ``ModelIntegrityError`` and is NOT deleted automatically.

        See PLAN_ONNX_INTEGRATION.md §3.3 (Option B-1). onnx-asr 0.12.0
        exports ``load_model(...)`` — there is NO ``onnx_asr.Model``
        class in any release (verified against 0.12.0 and main; only
        ``load_model`` + ``load_vad`` are exported).
        """
        log.info("[PARAKEET] load() entered — importing onnx-asr if needed")
        if not self._ensure_imports():
            if progress_callback:
                progress_callback("Missing dependencies: onnx-asr + onnxruntime")
            return False

        with self._lock:
            if self._model is not None:
                return True

            # Reset the one-time CPU-fallback notification flag on
            # every fresh ``load()``. A fallback that fired during a
            # previous transcription session must not silently suppress
            # the next session's notification — the user may have
            # restarted their GPU driver or freed VRAM in the meantime.
            self._cpu_fallback_notified = False
            self._cpu_fallback_since = None
            self._cpu_transcribe_count = 0

            # Quick cache check — avoids calling onnx_asr.load_model(...)
            # entirely when the model isn't on disk.
            _cache_t0 = time.perf_counter()
            _cached = self._is_cached()
            log.info(
                "[PARAKEET] model cache check: cached=%s (%.2fs)",
                _cached,
                time.perf_counter() - _cache_t0,
            )
            if not _cached:
                # The app NEVER auto-downloads models — downloading is
                # an explicit user action (Models page Download button,
                # or the onboarding wizard). Refuse to load and raise
                # the actionable error so the tray / IPC layer can
                # point the user at the Models page.
                from voice_typer.server.asr_errors import ModelNotDownloadedError

                raise ModelNotDownloadedError(
                    "The Parakeet model is not downloaded yet. "
                    "Open the Models page and click Download before using it.",
                    model_size="parakeet",
                    backend="parakeet",
                    repo_id=_PARAKERT_ONNX_REPO_ID,
                )

            # Verify model integrity (hash check) — UNCONDITIONALLY on
            # every load. The ~1-3s SHA-256 cost is acceptable vs the
            # multi-second ORT load time. On failure we hard-fail —
            # WITHOUT deleting the tampered files (deletion is an
            # explicit user action via the Models page Delete button).
            from voice_typer.server.config import _config_dir
            from voice_typer.server.security import verify_model_integrity

            cache_root = _config_dir() / "huggingface" / "hub"
            model_dir = cache_root / _PARAKERT_ONNX_CACHE_DIR
            verified_snapshot: str | None = None
            if model_dir.is_dir():
                verified = False
                verify_exc: Exception | None = None
                try:
                    for snapshot in (model_dir / "snapshots").iterdir():
                        if snapshot.is_dir() and verify_model_integrity(str(snapshot), _PARAKERT_ONNX_REPO_ID):
                            verified = True
                            verified_snapshot = str(snapshot)
                            break
                except OSError as exc:
                    verify_exc = exc
                if not verified:
                    log.error(
                        "[PARAKEET] Model integrity check failed%s for %s at %s. "
                        "Refusing to load tampered model. To fix: delete it from the Models page.",
                        f" (OSError: {verify_exc})" if verify_exc else "",
                        _PARAKERT_ONNX_REPO_ID,
                        model_dir,
                    )
                    if progress_callback:
                        progress_callback("Model integrity check failed; delete and re-download from the Models page.")
                    from voice_typer.server.asr_errors import ModelIntegrityError

                    raise ModelIntegrityError(
                        "The cached Parakeet model failed integrity verification. "
                        "Delete it and download it again from the Models page to recover.",
                        model_size="parakeet",
                        backend="parakeet",
                        repo_id=_PARAKERT_ONNX_REPO_ID,
                    )

            # Load ONNX model via onnx_asr.load_model(...) — by TYPE
            # name (``nemo-conformer-tdt``) + the verified local
            # snapshot dir (PLAN_ONNX_INTEGRATION.md §3.3 Option B-1).
            try:
                if progress_callback:
                    progress_callback("Loading Parakeet TDT v3 ONNX model...")

                log.info("[PARAKEET] Loading ONNX model (device=%s)...", self.device)
                effective_device = self.device
                if effective_device == "cuda" and self._should_force_cpu():
                    effective_device = "cpu"

                providers = self._select_providers(effective_device)
                _load_start = time.perf_counter()

                # onnx-asr 0.12.0 exports ``load_model(...)`` — there is
                # NO ``onnx_asr.Model`` class in any onnx-asr release
                # (verified against 0.12.0 and main; only ``load_model``
                # + ``load_vad`` are exported). We load by TYPE name
                # (``nemo-conformer-tdt``) + the verified local snapshot
                # dir so onnx-asr loads the integrity-verified files
                # instead of re-resolving the repo BY NAME.
                self._onnx_model_dir = verified_snapshot
                self._model = self._onnx_asr.load_model(
                    _PARAKERT_ONNX_MODEL_NAME,
                    path=verified_snapshot,
                    quantization=_PARAKERT_QUANTIZATION,
                    providers=providers,
                )

                _elapsed = time.perf_counter() - _load_start
                _warm_label = "warm (page-cache)" if _elapsed < 5.0 else "cold (disk)"
                _read_speed_mbs = _PARAKERT_WEIGHTS_MB / max(_elapsed, 0.1)
                log.info(
                    "[PARAKEET] ONNX model loaded (%s) — total=%.1fs (%.0f MB/s)",
                    _warm_label,
                    _elapsed,
                    _read_speed_mbs,
                )
                if progress_callback:
                    progress_callback("Parakeet model ready")
                # Stash the effective providers so the GPU→CPU fallback
                # path knows what to switch FROM.
                self._effective_providers = providers
                return True

            except ImportError as exc:
                log.exception("[PARAKEET] onnx_asr package not installed")
                if progress_callback:
                    progress_callback(f"Missing dependency: {exc}")
                return False
            except KeyboardInterrupt:
                log.warning("[PARAKEET] Loading interrupted by user")
                if progress_callback:
                    progress_callback("Loading cancelled")
                return False
            except Exception as exc:
                log.exception("[PARAKEET] Failed to load model")
                if progress_callback:
                    progress_callback(f"Model load failed: {exc}")
                return False

    # ── Transcription ───────────────────────────────────────────────

    def transcribe(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe audio array. Returns cleaned text string.

        Long audio (>CHUNK_SECONDS) is split into overlapping chunks
        via :func:`voice_typer.server.asr_utils.split_audio` to stay
        within the Conformer encoder's input-length limit. Each chunk is
        transcribed via the onnx-asr adapter's ``recognize`` method;
        results are merged via
        :func:`voice_typer.server.asr_utils.merge_chunks`.

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own RMS computation in
        hallucination detection.

        The lock is released during the chunk-inference loop (same
        pattern as the pre-migration code) so ``is_loaded`` / ``unload``
        / parallel transcribes are not blocked for the full ~13s of a
        long dictation. ``unload()`` waits on ``_inference_cond`` for
        the counter to return to 0 before nulling the model.
        """
        with self._lock:
            if self._model is None:
                raise RuntimeError("Parakeet model not loaded. Call load() first or check logs.")

            if len(audio) == 0:
                return ""

            duration = len(audio) / WHISPER_SAMPLE_RATE
            self._active_inference += 1

        try:
            if duration <= _CHUNK_SECONDS:
                return self._transcribe_segment(audio, audio_stats=audio_stats)

            chunks = self._split_audio(audio, _CHUNK_SECONDS, _CHUNK_OVERLAP_SECONDS)
            log.info("[PARAKEET] Splitting %.1fs audio into %d chunks", duration, len(chunks))

            results = self._transcribe_chunks(chunks)
            if not results:
                return ""

            return self._merge_chunks(results)
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()

    def _transcribe_segment(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe one audio segment via the onnx-asr adapter's ``recognize``.

        Assumes the segment is within the model's input-length limit
        (caller enforces this via chunking). Applies the English-only
        filter and the low-audio-hallucination filter to the result.

        NOTE: this call runs to completion — onnx-asr 0.12.0 does not
        forward ``RunOptions`` to ``session.run`` (verified by wheel
        source inspection), so ``request_abort()`` cannot terminate a
        single-segment decode mid-flight. Abort is only effective
        between chunks (see :meth:`_transcribe_chunks`).
        """
        text = self._model.recognize(audio, sample_rate=WHISPER_SAMPLE_RATE)

        # ``recognize`` returns a single str for single audio;
        # defensively handle list[str] in case the library changes
        # shape (mirrors the pre-migration defensive pattern).
        if isinstance(text, list):
            text = text[0] if text else ""
        text = (text or "").strip()

        # English-only filter: only active when language="en" is configured
        if self.language == "en" and not _is_likely_english(text):
            return ""

        # PERF-STATS: reuse pre-computed RMS when provided
        rms = audio_stats[0] if audio_stats is not None else float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            log_hallucination_rejection(
                "[PARAKEET]",
                text,
                reason="hallucination",
                log_transcriptions=False,
            )
            return ""
        return text

    def _transcribe_chunks(self, chunks: list[np.ndarray]) -> list[str]:
        """Transcribe each chunk via ``model.recognize()``; respect abort.

        Checks ``_abort_event`` BETWEEN chunks so a long audio split
        into 13 chunks stops after the current chunk rather than
        decoding all remaining ones. The current chunk's
        ``model.recognize()`` call runs to completion (onnx-asr 0.12.0
        does not forward ``RunOptions`` to ``session.run`` — see the
        class-level note on ``_abort_event``).
        """
        if not chunks:
            return []
        results: list[str] = []
        for i, chunk in enumerate(chunks):
            if self._abort_event.is_set():
                log.info(
                    "[PARAKEET] Abort requested — stopping chunk loop early (completed %d/%d chunks)",
                    i,
                    len(chunks),
                )
                break
            log.info(
                "[PARAKEET] Transcribing chunk %d/%d (%.1fs)",
                i + 1,
                len(chunks),
                len(chunk) / WHISPER_SAMPLE_RATE,
            )
            text = self._transcribe_segment(chunk)
            if text:
                results.append(text)
        return results

    def _split_audio(self, audio: np.ndarray, chunk_sec: float, overlap_sec: float) -> list[np.ndarray]:
        """Split audio into overlapping chunks.

        Delegates to :func:`voice_typer.server.asr_utils.split_audio`
        (single source of truth shared with ``QwenEngine._split_audio``).
        The method signature is preserved for backward compatibility
        with existing call sites and tests that invoke
        ``engine._split_audio(audio, chunk_sec, overlap_sec)`` directly.
        """
        from voice_typer.server.asr_utils import split_audio

        return split_audio(
            audio,
            chunk_duration=chunk_sec,
            overlap_duration=overlap_sec,
            sample_rate=WHISPER_SAMPLE_RATE,
        )

    def _merge_chunks(self, texts: list[str]) -> str:
        """Concatenate chunk transcriptions, skipping overlap text.

        Delegates to :func:`voice_typer.server.asr_utils.merge_chunks`
        (PLAN_ONNX_INTEGRATION.md §5.4 — the canonical home for this
        algorithm post-migration). The instance-method signature is
        preserved for backward compat with existing call sites and tests
        (``engine._merge_chunks([...])``).
        """
        return _merge_chunks_impl(texts)

    @staticmethod
    def _compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
        """Return how many leading words of *new_words* to skip.

        Delegates to :func:`voice_typer.server.asr_utils.compute_overlap_skip`
        (PLAN_ONNX_INTEGRATION.md §5.4). The ``@staticmethod`` signature
        is preserved for backward compat with tests that call
        ``ParakeetEngine._compute_overlap_skip(prev, new)`` directly.
        """
        return _compute_overlap_skip_impl(prev_words, new_words)

    # ── GPU→CPU fallback (session recreation) ───────────────────────

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Transcribe with GPU→CPU fallback on CUDA errors.

        ONNX Runtime cannot move a session between providers in place
        (unlike torch's ``.to("cpu")``). The fallback recreates the
        session with ``CPUExecutionProvider`` only
        (PLAN_ONNX_INTEGRATION.md §3.4). This is multi-second latency
        (session recreation + weight reload) — NOT a free swap.

        Emits the ``parakeet_cpu_fallback`` event (one-time per loaded
        session) so the tray can show "(CPU fallback)" status. The
        ``notification`` event surfaces a user-facing toast.

        Raises:
            TranscriptionBackendError: if both the GPU path and the CPU
                fallback fail.
        """
        with self._lock:
            if self._model is None:
                raise TranscriptionBackendError("Parakeet model not loaded.")

            if len(audio) == 0:
                return ""

        try:
            return self.transcribe(audio, audio_stats=audio_stats)
        except Exception as exc:
            # Use the shared CUDA-error classifier (PLAN_ONNX_INTEGRATION.md
            # §5.1) — 5-layer check, NOT the lossy 4-keyword frozenset.
            if self.device == "cuda" and _is_cuda_error_impl(exc):
                log.warning(
                    "[PARAKEET] CUDA error, recreating session on CPU: %s",
                    exc,
                    exc_info=True,
                )
                try:
                    # Unload the GPU session, then reload with CPU providers.
                    # This is the only correct ORT fallback — see §3.4.
                    self._unload_impl()
                    self.device = "cpu"
                    if not self._load_impl(providers=["CPUExecutionProvider"]):
                        raise TranscriptionBackendError(f"Parakeet CPU fallback load failed after CUDA error ({exc})")
                    # Claim an inference slot so a concurrent ``unload()``
                    # waits for the CPU-fallback transcription to finish
                    # before nulling the model.
                    with self._lock:
                        if self._model is None:
                            raise TranscriptionBackendError("Parakeet model not loaded after CPU fallback.")
                        self._active_inference += 1
                    try:
                        text = self._transcribe_segment(audio, audio_stats=audio_stats)
                    finally:
                        with self._inference_cond:
                            self._active_inference -= 1
                            if self._active_inference == 0:
                                self._inference_cond.notify_all()

                    # Record the fallback start so any future retry logic
                    # has a reference point (currently a no-op stub — ORT
                    # session recreation is the only fallback path).
                    self._cpu_fallback_since = time.monotonic()
                    self._cpu_transcribe_count = 0

                    # Emit ONE-TIME tray notification + status event.
                    # The ``_cpu_fallback_notified`` flag is reset to
                    # ``False`` at the top of ``load()`` so a fallback
                    # after the next reload re-notifies. Coordinate with
                    # the tray: ``"type": "parakeet_cpu_fallback"`` is the
                    # contract for the tray "(CPU fallback)" status
                    # suffix; the ``"notification"`` event surfaces the
                    # user-facing toast.
                    if not self._cpu_fallback_notified:
                        self._cpu_fallback_notified = True
                        try:
                            from voice_typer.server import event_bus

                            event_bus.publish(
                                {
                                    "type": "notification",
                                    "data": {
                                        "title": APP_NAME,
                                        "message": (
                                            "GPU transcription failed — switched to CPU. "
                                            "Transcription will be slower until restart."
                                        ),
                                        "duration_ms": 10000,
                                    },
                                }
                            )
                            event_bus.publish(
                                {
                                    "type": "parakeet_cpu_fallback",
                                    "data": {"device": "cpu", "reason": str(exc)[:200]},
                                }
                            )
                        except Exception as notify_exc:
                            log.debug(
                                "[PARAKEET] could not publish CPU-fallback notification: %s",
                                notify_exc,
                            )
                    return text
                except TranscriptionBackendError:
                    raise
                except Exception as cpu_exc:
                    log.exception("[PARAKEET] CPU fallback also failed")
                    raise TranscriptionBackendError(
                        f"Parakeet GPU transcription failed ({exc}) and CPU fallback also failed ({cpu_exc})"
                    ) from cpu_exc
            # Non-CUDA error: surface it instead of swallowing as ""
            raise TranscriptionBackendError(f"Parakeet transcription failed: {exc}") from exc

    def _load_impl(self, *, providers: list[str]) -> bool:
        """Re-create the ONNX session (``onnx_asr.load_model``) with the given providers.

        Used by the GPU→CPU fallback path (§3.4) to recreate the session
        on CPU. Does NOT re-check the cache or run the integrity check
        — those already passed in the original :meth:`load` call. The
        model files are still on disk (the GPU session was loaded from
        them, at ``self._onnx_model_dir``); we just rebuild the ORT
        session with new providers.

        Returns ``True`` on success, ``False`` if the new session could
        not be created (logged at ERROR — caller raises
        ``TranscriptionBackendError``).
        """
        if not self._ensure_imports():
            return False
        try:
            self._model = self._onnx_asr.load_model(
                _PARAKERT_ONNX_MODEL_NAME,
                path=self._onnx_model_dir,
                quantization=_PARAKERT_QUANTIZATION,
                providers=providers,
            )
            self._effective_providers = providers
            return True
        except Exception:
            log.exception(
                "[PARAKEET] Failed to recreate ONNX session with providers=%s",
                providers,
            )
            return False

    def _unload_impl(self) -> None:
        """Drop the loaded onnx-asr model reference (without acquiring
        ``_inference_cond``).

        Used by the GPU→CPU fallback path. The full :meth:`unload` also
        waits for ``_active_inference == 0`` and runs gc / GPU memory
        release; this lighter variant is safe to call from inside the
        fallback path (which already holds the inference slot via
        ``_active_inference``).
        """
        with self._lock:
            self._model = None

    def unload(self) -> None:
        """Free model memory.

        ONNX Runtime has no ``empty_cache()`` API — the CUDA arena is
        freed when the session is destroyed (PLAN_ONNX_INTEGRATION.md
        §5.2). The ``release_gpu_memory()`` helper in ``asr_utils`` is a
        no-op for ORT (kept for API compatibility with the existing
        call sites).

        ``gc.collect()`` is run OUTSIDE the lock to avoid blocking
        ``is_loaded`` / ``transcribe`` for 10-100ms.
        """
        import gc

        from voice_typer.server.asr_utils import release_gpu_memory

        with self._inference_cond:
            # Wait for any active transcription to finish before nulling
            # the model. ``transcribe()`` increments ``_active_inference``
            # under this lock and decrements it in a ``finally`` block;
            # without this wait a concurrent ``unload()`` would null
            # ``self._model`` mid-inference and trigger a use-after-free
            # when the inference path dereferenced the freed ORT session.
            while self._active_inference > 0:
                self._inference_cond.wait()
            self._model = None
            self._processor = None
        # gc.collect() OUTSIDE the lock.
        gc.collect()
        # No-op for ORT — kept for API compat (see PLAN_ONNX_INTEGRATION.md §5.2).
        release_gpu_memory()
        log.info("[PARAKEET] Model unloaded")

    # ── Diagnostic properties ───────────────────────────────────────

    @property
    def device_info(self) -> str:
        return f"parakeet/{self.device}"

    @property
    def loaded_via(self) -> str:
        return f"parakeet/{self.device}/{_PARAKERT_ONNX_REPO_ID}"
