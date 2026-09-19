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
    """Wraps NVIDIA Parakeet TDT v3 ASR model via ONNX Runtime."""

    # Lazily-populated references to the onnx_asr + onnxruntime modules.
    _imports_loaded: bool = False
    _onnx_asr: Any = None
    _ort: Any = None
    # Guards the check-then-import sequence in ``_ensure_imports`` so
    _imports_lock: threading.Lock = threading.Lock()
    # Class-level fallbacks for instances created via ``__new__`` (some
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
        self.config = config
        # Loaded onnx-asr model adapter instance (or ``None`` when unloaded).
        self._model: Any = None
        # Backward-compat: the pre-migration code populated a separate
        self._processor: Any = None
        # Verified HF-cache snapshot dir of the ONNX model, stashed by
        self._onnx_model_dir: str | None = None
        # One-time tray notification flag for CUDA→CPU transcription
        self._cpu_fallback_notified: bool = False
        # Time / count-based CUDA-retry tracking. The pre-migration code
        self._cpu_fallback_since: float | None = None
        self._cpu_transcribe_count: int = 0
        self._lock = threading.RLock()
        # Counter + Condition so ``transcribe()`` can release the model
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        # Abort token shared by the dictation pipeline's cancel path and
        self._abort_event = threading.Event()
        # Effective ORT providers list used by the most recent
        self._effective_providers: list[str] = []
        # Backward-compat: pre-migration tests pin
        self._INFERENCE_BATCH_SIZE: int = max(1, int(os.environ.get("PARAKEET_BATCH_SIZE", "2")))
