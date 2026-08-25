"""Assembled ParakeetEngine - public facade class of the package."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from voice_typer.server.i18n import DEFAULT_LOCALE

from ._load import LoadMixin
from ._transcribe import TranscribeMixin

log = logging.getLogger(__name__)


class ParakeetEngine(LoadMixin, TranscribeMixin):
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
