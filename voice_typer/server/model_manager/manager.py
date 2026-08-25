"""Assembled ModelManager - public facade class of the package."""

from __future__ import annotations

from ._base import ModelManagerCore
from ._change import ChangeMixin
from ._construction import ConstructionMixin
from ._lifecycle import LifecycleMixin
from ._loading import LoadingMixin
from ._notify import LastResortNotifyMixin


class ModelManager(
    LoadingMixin,
    ChangeMixin,
    LastResortNotifyMixin,
    ConstructionMixin,
    LifecycleMixin,
    ModelManagerCore,
):
    """Owns ASR backend construction, loading, fallback, and switching.

    #2 extracted from VoiceTyperApp. Centralizes the three
    legacy engine fields + the AsrBackendRegistry so callers go through
    one object instead of poking at app.py internals.

    The app passes itself (``app``) so ModelManager can:
    - Read/write ``app.config`` (asr_backend, model_size, etc.)
    - Update ``app.tray`` state during loads
    - Schedule the pending-dictation callback via ``app._schedule_timer``
    - Read ``app._shutting_down`` flag and ``self._pending_dictation``
      (: ``_pending_dictation`` now lives on ModelManager
      directly — accessed via ``app.models._pending_dictation``.)

    PERF-015: includes an LRU cache for loaded models. When loading a
    new model, if more than 2 models are loaded, the least recently
    used one is unloaded. This prevents GPU OOM from accumulating
    multiple model instances.
    """
