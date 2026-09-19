"""ModelManager base state shared by loading/change paths."""

from __future__ import annotations

import logging
import threading
from typing import Any

from voice_typer.server.asr_registry import AsrBackendRegistry

log = logging.getLogger("voice_typer.server.model_manager")


class ModelManagerCore:
    # PERF-015: maximum number of concurrently loaded models
    _MAX_LOADED_MODELS = 2

    # Cooldown between last-resort tray notifications for the SAME
    _LAST_RESORT_NOTIFY_COOLDOWN_SECS: float = 900.0

    def __init__(self, app: Any) -> None:
        self._app = app
        # Registry initialized eagerly (was lazy in app.py).
        self._registry: AsrBackendRegistry = AsrBackendRegistry(app.config)

        # Track backends that were deliberately unloaded by the app
        self._deliberately_unloaded: set[str] = set()
        # Per-backend monotonic timestamps of the last last-resort tray
        self._last_resort_notified_at: dict[str, float] = {}
        # Set while a SYNCHRONOUS model load is running on the calling
        self._sync_load_in_progress: bool = False

        # Wire the production last-resort subscriber: when
        self._registry.add_last_resort_subscriber(self._on_last_resort_unloaded)

        # Gate the event_bus publish (the renderer-toast surface) with
        self._registry.set_last_resort_event_gate(self._should_suppress_last_resort_notification)

        # Gate the ``asr_backend_disabled`` event_bus publish the SAME
        self._registry.set_backend_disabled_event_gate(self._should_suppress_backend_disabled_notification)

        # The three legacy engine attributes (``transcriber`` /

        # Background model-load thread (tracked so toggle_dictation can
        self._model_load_thread: threading.Thread | None = None
        self._model_load_attempted: bool = False
        self._pending_dictation: bool = False
        # Latest background model-change / backend-change thread
        self._model_change_thread: threading.Thread | None = None
        self._backend_change_thread: threading.Thread | None = None
        # When the user changes model during an active recording
        self._pending_model_change: str | None = None
        # sibling to ``_pending_model_change``: captures a
        self._pending_backend_change: str | None = None

        # PERF-015: LRU tracking for loaded models.
        self._model_access_times: dict[str, float] = {}
        self._model_lru_lock = threading.Lock()

        # LAZY-INIT-LOCK-FIX: previously created lazily via
        self._lazy_init_lock = threading.Lock()

        #  spawn lock guarding ``start_background_load``'s
        self._model_load_spawn_lock = threading.Lock()

        #  reentrant lock guarding the entire body of
        self._model_change_lock = threading.RLock()

        # idle-unload. When ``model_idle_unload_minutes > 0``, the
        self._idle_unload_lock = threading.Lock()

    @property
    def registry(self) -> AsrBackendRegistry:
        """Direct access to the underlying registry (rarely needed)."""
        return self._registry

    # The three legacy engine attributes are ``@property`` accessors that
    @property
    def transcriber(self) -> Any | None:
        """The active Whisper transcriber (delegates to registry)."""
        return self._registry.get("whisper")

    @transcriber.setter
    def transcriber(self, value: Any) -> None:
        """Register the whisper backend (delegates to registry)."""
        if value is None:
            self._registry.unregister("whisper")
        else:
            current = self._registry.get("whisper")
            if current is not value:
                if current is not None:
                    self._registry.unregister("whisper")
                self._registry.register("whisper", value)

    @property
    def _qwen_engine(self) -> Any | None:
        """The active Qwen engine (delegates to registry)."""
        return self._registry.get("qwen")

    @_qwen_engine.setter
    def _qwen_engine(self, value: Any) -> None:
        """Register the qwen backend (delegates to registry)."""
        if value is None:
            self._registry.unregister("qwen")
        else:
            current = self._registry.get("qwen")
            if current is not value:
                if current is not None:
                    self._registry.unregister("qwen")
                self._registry.register("qwen", value)

    @property
    def _parakeet_engine(self) -> Any | None:
        """The active Parakeet engine (delegates to registry)."""
        return self._registry.get("parakeet")

    @_parakeet_engine.setter
    def _parakeet_engine(self, value: Any) -> None:
        """Register the parakeet backend (delegates to registry)."""
        if value is None:
            self._registry.unregister("parakeet")
        else:
            current = self._registry.get("parakeet")
            if current is not value:
                if current is not None:
                    self._registry.unregister("parakeet")
                self._registry.register("parakeet", value)

    def active_transcriber(self) -> Any | None:
        """Return the active transcriber (Parakeet, Qwen, or Whisper)."""
        return self._registry.get_active()
