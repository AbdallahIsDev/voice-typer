"""ModelManager core state: locks, registries, engine accessors.

Split leaf of the model_manager package; ``ModelManager`` composes
this core with the concern mixins. Bodies are verbatim from the
original monolith."""

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
    # backend. The registry's one-shot latch normally limits the
    # ``get_active`` last-resort notification to once per last-resort
    # transition, but the 15s ``get_status`` probe (or a failed
    # ``load_with_fallback`` retry) can RESET the latch, so without an
    # additional ModelManager-side rate limit a permanently-unloaded
    # backend would re-notify every 15s (spamming the tray while the
    # backend stays broken). 15 minutes is the balance: the user is
    # told promptly, and a broken backend re-notifies at most ~4x/hour.
    _LAST_RESORT_NOTIFY_COOLDOWN_SECS: float = 900.0

    def __init__(self, app: Any) -> None:
        self._app = app
        # Registry initialized eagerly (was lazy in app.py).
        # Callers can rely on it existing from the start.
        self._registry: AsrBackendRegistry = AsrBackendRegistry(app.config)

        # Track backends that were deliberately unloaded by the app
        # (idle-unload, force-unload, LRU eviction, model-change unload).
        # These are NOT "missing download" situations — the model is on
        # disk, the app just released it (VRAM / switch). The last-resort
        # tray notification ("open the Models page and download") must be
        # SUPPRESSED for these so the user isn't told to download a model
        # that is already installed.
        self._deliberately_unloaded: set[str] = set()
        # Per-backend monotonic timestamps of the last last-resort tray
        # notification (rate limiter — see
        # ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS``). Guarded by the same
        # GIL-atomicity reasoning as ``_pending_model_change`` — the
        # IPC worker thread and the subscriber path never mutate the
        # same key concurrently.
        self._last_resort_notified_at: dict[str, float] = {}
        # Set while a SYNCHRONOUS model load is running on the calling
        # thread (``ensure_active_engine_loaded``'s reload-after-
        # idle-unload / retry branch). Unlike ``_model_load_thread``
        # (background load) / ``_model_change_thread`` /
        # ``_backend_change_thread`` (background changes), a synchronous
        # load has NO tracked thread — the last-resort subscriber would
        # otherwise fire during the load window and tell the user to
        # download a model that is literally loading.
        self._sync_load_in_progress: bool = False

        # Wire the production last-resort subscriber: when
        # ``registry.get_active()`` falls through to an unloaded backend
        # (transcription would silently return empty), show a tray
        # notification pointing the user at the Models page. Pre-fix the
        # ``on_last_resort`` subscriber set existed but NO production
        # subscriber was ever wired — the documented tray notification
        # was dead code and the user got zero feedback.
        self._registry.add_last_resort_subscriber(self._on_last_resort_unloaded)

        # Gate the event_bus publish (the renderer-toast surface) with
        # the SAME suppressions the tray notification applies inside
        # ``_on_last_resort_unloaded``. The renderer toast consumes the
        # ``asr_last_resort_unloaded`` event and cannot see the
        # ModelManager-side checks, so without this gate it would tell
        # the user to download a model that was deliberately unloaded
        # (idle-unload / force-unload / LRU eviction / model change) or
        # is literally loading. The gate is checked by the breaker
        # BEFORE the subscribers fire, so a suppressed window skips the
        # tray path too (which would self-suppress anyway — no behavior
        # change there).
        self._registry.set_last_resort_event_gate(self._should_suppress_last_resort_notification)

        # Gate the ``asr_backend_disabled`` event_bus publish the SAME
        # way: during deliberate-unload windows (idle-unload /
        # force-unload / LRU eviction / model change, or a load in
        # progress) the breaker can trip on a transient failure and
        # publish a spurious "backend disabled" event — the renderer
        # would tell the user the backend is permanently broken when
        # the app is just switching away / loading. The gate shares the
        # window checks with the last-resort gate but NOT the cooldown
        # (the disabled event fires at most once per trip — the breaker
        # skips already-disabled backends — so no rate limit is needed).
        self._registry.set_backend_disabled_event_gate(self._should_suppress_backend_disabled_notification)

        # The three legacy engine attributes (``transcriber`` /
        # ``_qwen_engine`` / ``_parakeet_engine``) are now ``@property``
        # accessors defined further down — they delegate directly to
        # ``self._registry.get(...)`` with no mirrored state. Writes go
        # through their ``@property.setter`` methods which delegate to
        # ``self._registry.register(...)`` / ``unregister(...)``.

        # Background model-load thread (tracked so toggle_dictation can
        # detect "loading in progress" and auto-start once finished).
        self._model_load_thread: threading.Thread | None = None
        self._model_load_attempted: bool = False
        self._pending_dictation: bool = False
        # Latest background model-change / backend-change thread
        # (non-blocking path). ``change_model`` / ``set_active_backend``
        # spawn a ``ModelChange`` / ``BackendChange`` daemon thread and
        # return immediately; these attrs track the most recently
        # spawned thread so callers (tests, shutdown) can join it and
        # know the full cycle — including the ``asr_backend_ready`` /
        # ``asr_backend_load_failed`` publish — has completed. Concurrent
        # change calls serialize on ``_model_change_lock``, so joining
        # the LATEST thread also covers any earlier thread still waiting
        # on the lock. Mirrors ``_model_load_thread``'s tracking role.
        self._model_change_thread: threading.Thread | None = None
        self._backend_change_thread: threading.Thread | None = None
        # When the user changes model during an active recording
        # we save config and notify "will change after current recording",
        # but previously never actually applied the change. We capture
        # the requested model here and apply it on the next _start_dictation.
        self._pending_model_change: str | None = None
        # sibling to ``_pending_model_change`` — captures a
        # backend-only change (``set_active_backend``) that was requested
        # while the user was recording or busy. Mirrors the
        # ``_pending_model_change`` deferral pattern: the request is
        # saved (and config is persisted) at IPC time, but the actual
        # unload/load cycle is deferred to the next
        # :meth:`apply_pending_model_change` call (invoked from
        # ``recording_controller.start`` before the new recording
        # begins). Without this guard, ``set_active_backend``
        # unconditionally ran the unload phase mid-transcription,
        # unloading the ctranslate2 model from underneath the in-flight
        # transcribe thread (crash / heap corruption / stuck thread —
        # see  in review.md).
        self._pending_backend_change: str | None = None

        # PERF-015: LRU tracking for loaded models.
        # Maps backend_name → last-access timestamp. When loading a new
        # model and more than _MAX_LOADED_MODELS are present, the oldest
        # entry is unloaded.
        self._model_access_times: dict[str, float] = {}
        self._model_lru_lock = threading.Lock()

        # LAZY-INIT-LOCK-FIX: previously created lazily via
        # ``if not hasattr(self, "_lazy_init_lock"): self._lazy_init_lock =
        # __import__("threading").Lock()`` in ensure_active_engine_loaded.
        # The ``hasattr`` check is itself a race — two threads could both
        # see ``not hasattr`` and both create a Lock, then one wins the
        # assignment and the other holds a stale Lock that protects
        # nothing. Moving to ``__init__`` guarantees the lock exists
        # before any thread can call ``active_transcriber``.
        self._lazy_init_lock = threading.Lock()

        #  spawn lock guarding ``start_background_load``'s
        # check-then-spawn critical section. The liveness check
        # (``_model_load_thread is None / not alive``) + Thread
        # construction + assignment to ``_model_load_thread`` MUST be
        # atomic so two concurrent callers can't both spawn a ModelLoad
        # thread (the second assignment would overwrite the first,
        # leaking the first thread — still running, untracked, no
        # shutdown join). Plain Lock (no re-entrancy needed); created in
        # ``__init__`` so it exists before any thread can call
        # ``start_background_load``.
        self._model_load_spawn_lock = threading.Lock()

        #  reentrant lock guarding the entire body of
        # ``change_model`` so two concurrent calls cannot both unload +
        # re-register + reload the same backend (which previously left
        # the registry in an inconsistent state with two engines
        # constructed for the same name).  Reentrant so ``apply_pending_model_change``
        # can call ``change_model`` while already holding the lock if a
        # caller further up the stack acquired it.
        self._model_change_lock = threading.RLock()

        # idle-unload timer. When ``model_idle_unload_minutes > 0``,
        # each ``touch_active_model()`` (called after every successful
        # transcribe) arms a ``threading.Timer`` that fires after N
        # minutes of inactivity. When the timer fires, the active
        # backend is unloaded + ``release_gpu_memory()`` is called so
        # the ~2.4 GB of VRAM (Parakeet fp16) + CUDA caching allocator
        # blocks are returned to the OS. The model is reloaded on the
        # next ``toggle_dictation`` via ``ensure_active_engine_loaded``'s
        # reload-after-idle-unload path. ``model_idle_unload_minutes = 0``
        # (the default) disables the feature — current behaviour is
        # preserved exactly.
        #
        # The lock guards the ``_idle_unload_timer`` reference so the
        # identity check in ``_on_idle_unload_fire`` (which prevents a
        # cancelled / rescheduled timer's callback from unloading) is
        # race-free against concurrent ``cancel_idle_unload_timer`` /
        # ``_schedule_idle_unload_timer`` calls.
        self._idle_unload_timer: threading.Timer | None = None
        self._idle_unload_lock = threading.Lock()

    # ── Registry access ────────────────────────────────────────────────

    @property
    def registry(self) -> AsrBackendRegistry:
        """Direct access to the underlying registry (rarely needed)."""
        return self._registry

    # The three legacy engine attributes are ``@property`` accessors that
    # delegate directly to ``self._registry.get(...)`` — no mirrored
    # state, no sync needed. Their setters delegate to
    # ``self._registry.register(...)`` / ``unregister(...)`` so test
    # code that does ``app.models.transcriber = MagicMock()`` continues
    # to work transparently. Writing ``None`` unregisters the backend;
    # writing a non-None value unregisters the old (if different) and
    # registers the new (no churn when the same instance is reassigned).
    @property
    def transcriber(self) -> Any | None:
        """The active Whisper transcriber (delegates to registry)."""
        return self._registry.get("whisper")

    @transcriber.setter
    def transcriber(self, value: Any) -> None:
        """Register the whisper backend (delegates to registry).

        Writing ``None`` unregisters the backend; writing a non-None
        value unregisters the old instance (if different) and registers
        the new one. No churn when the same instance is reassigned.
        """
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
        """Return the active transcriber (Parakeet, Qwen, or Whisper).

        008: delegates to AsrBackendRegistry which centralizes
        the backend selection logic. Previously every caller re-checked
        self.config.asr_backend and tested three separate fields.

        previously this method called ``_sync_registry_from_fields()``
        on every read — which re-imported state from the three legacy
        engine fields into the registry WITHOUT holding
        ``_model_change_lock``.  A concurrent ``change_model`` could
        leave the fields half-mutated, so the registry would be
        re-synced from a stale snapshot and the next ``get_active()``
        could return a stale or None reference.

        That race is now structurally impossible: the three legacy
        attributes are ``@property`` accessors that delegate directly
        to ``self._registry.get(...)`` with no mirrored state, so there
        is no second source of truth to drift and no sync step to call.
        Reading via ``self._registry.get_active()`` directly is safe:
        ``AsrBackendRegistry`` is internally synchronized (its own
        ``_lock``), and any concurrent ``change_model`` either has not
        yet mutated the registry (so we read the old active backend,
        which is still valid) or has already finished (so we read the
        new one).  Test code that assigns to ``app.models.transcriber``
        now goes through the ``@property.setter`` which delegates to
        ``self._registry.register(...)`` directly — no manual sync
        call needed.
        """
        return self._registry.get_active()

    # ── Engine construction ( single chokepoint) ──────────────
