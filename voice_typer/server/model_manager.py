"""#2 ModelManager — extracted from VoiceTyperApp.

Owns the ASR backend lifecycle: construction (via AsrBackendRegistry.create),
loading (with whisper fallback), model changes, and the three legacy engine
fields (``transcriber``, ``_qwen_engine``, ``_parakeet_engine``) that were
previously scattered across VoiceTyperApp.

Architecture:
    VoiceTyperApp
        └── ModelManager
                ├── AsrBackendRegistry (single source of truth)
                ├── transcriber (whisper)       ← @property, delegates to registry
                ├── _qwen_engine                ← @property, delegates to registry
                └── _parakeet_engine            ← @property, delegates to registry

The registry is the single source of truth; the three legacy
attributes are read-only ``@property`` accessors that delegate
directly to ``self._registry.get(...)`` — there is no mirrored
state and no sync step. Writes (``app.models.transcriber = ...``)
flow through ``@property.setter`` methods that delegate to
``self._registry.register(...)`` / ``self._registry.unregister(...)``
so test code that assigns to these attributes continues to work
transparently. All mutations go through ModelManager methods.

Previously this concern lived in VoiceTyperApp as ~500 LOC across 8 methods:
    _load_transcription_engine_background, _fallback_to_whisper,
    _try_load_model, _change_model, _init_qwen_engine,
    _init_parakeet_engine, _init_asr_engine, _sync_asr_registry,
    _get_active_transcriber

All of those now delegate to ModelManager.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any

from voice_typer.server import i18n
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_registry import NO_MODEL_SIZE
from voice_typer.server.tray_hotkey import notification_hotkey_label
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class ModelManager:
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
        self._registry.set_last_resort_event_gate(
            self._should_suppress_last_resort_notification
        )

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
        self._registry.set_backend_disabled_event_gate(
            self._should_suppress_backend_disabled_notification
        )

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
        # Latest background model-change / backend-change thread (AB-10
        # non-blocking path). ``change_model`` / ``set_active_backend``
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

    def _ensure_engine(self, backend_name: str) -> None:
        """Ensure the engine object for ``backend_name`` exists (no load).

        delegates to AsrBackendRegistry.create() so all backend
        construction goes through one code path.

        previously failures here were swallowed by the registry
        and only logged. The user picked Qwen/Parakeet, saw "Ready", and
        got nothing on failure. We now surface init failures via tray
        notification so the user knows the backend didn't initialize.
        """
        if self._registry.get(backend_name) is not None:
            return
        try:
            if backend_name == "parakeet":
                self._registry.create(
                    "parakeet",
                    parakeet_kwargs=dict(
                        device=self._app.config.device,
                        language=self._app.config.language,
                    ),
                )
            elif backend_name == "qwen":
                self._registry.create(
                    "qwen",
                    qwen_kwargs=dict(
                        model_path=self._app.config.qwen_model_path,
                        device=self._app.config.device,
                        language=self._app.config.language,
                    ),
                )
            else:
                self._registry.create(
                    "whisper",
                    whisper_kwargs=dict(
                        model_size=self._app.config.model_size,
                        device=self._app.config.device,
                        language=self._app.config.language,
                        beam_size=self._app.config.beam_size,
                        best_of=self._app.config.best_of,
                        condition_on_previous_text=self._app.config.condition_on_previous_text,
                        # pass the live Config reference so the engine
                        # can read huggingface_consent / model settings
                        # without crashing on AttributeError. Previously
                        # this kwarg was missing, so self.config was None
                        # in the engine and consent/cache reads crashed
                        # on every uncached model load.
                        config=self._app.config,
                    ),
                )
        except Exception as exc:
            log.exception("[MODEL] Failed to initialize %s engine: %s", backend_name, exc)
            # surface to user via tray notification so they
            # don't sit waiting for "Ready" forever. Include the
            # backend name and a short hint.
            try:
                hint = ""
                if backend_name == "qwen":
                    hint = " Check that the Qwen model path is set correctly in Settings."
                elif backend_name == "parakeet":
                    hint = " Check that Parakeet weights are downloaded."
                self._app.tray.notify(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.backend_init_failed",
                        backend=backend_name.title(),
                        hint=hint,
                    ),
                )
            except Exception:
                # previously a bare ``except Exception: pass``.
                # If ``tray.notify`` ALSO fails (e.g. pystray broken on
                # a headless Linux container), the user was left with
                # NO visual signal that backend init failed. Log the
                # secondary failure so the error trail is at least
                # visible in the log file.
                log.error(
                    "[MODEL] tray.notify ALSO failed for backend init error",
                    exc_info=True,
                )
            # Re-raise so callers (load_background, ensure_active_engine_loaded)
            # can react; previously the bare-except in registry.create
            # swallowed the error.
            raise

    # ── Loading ────────────────────────────────────────────────────────

    def _notify_model_load_refused(self, exc: Exception, backend: str | None = None) -> str:
        """Surface a model-load refusal (not downloaded / integrity failed).

        The app NEVER downloads models automatically, so a
        ``ModelNotDownloadedError`` (or ``ModelIntegrityError``) at load
        time is a UX signal: tell the user the model isn't on disk and
        point them at the Models page — instead of the generic "model
        load failed" message that implies a transient, retryable error.

        Returns a short human-readable ``failure_reason`` string for
        callers that publish an ``asr_backend_load_failed`` event.
        """
        backend_name = (backend or getattr(self._app.config, "asr_backend", "unknown")).title()
        # Distinguish the two load-refusal flavors so the tray surfaces
        # the message that matches the actual state (and the renderer's
        # Home status pill):
        #  1. ``NO_MODEL_SIZE`` (``model_size == ""``) — genuine "no
        #     model selected": nothing is configured, so tell the user
        #     to pick a model. Uses ``state.model_manager
        #     .no_model_selected``, whose text matches the renderer's
        #     ``home.noModelSelectedHint`` (pushed via ``set_tray_locale``)
        #     so the tray tooltip and the Home hint agree verbatim.
        #  2. A concrete model that is missing from disk — "model not
        #     downloaded": tell the user to download it.
        no_model_selected = (
            isinstance(exc, ModelNotDownloadedError)
            and exc.model_size == NO_MODEL_SIZE
        )
        if isinstance(exc, ModelIntegrityError):
            reason = i18n.t(
                "state.model_manager.model_integrity_failed",
                backend=backend_name,
            )
        elif no_model_selected:
            reason = i18n.t("state.model_manager.no_model_selected")
        else:
            reason = i18n.t(
                "state.model_manager.model_not_downloaded",
                backend=backend_name,
            )
        log.warning("[MODEL] %s model load refused: %s", backend_name, exc)
        try:
            self._app.tray.set_state(AppState.ERROR, reason)
            self._app.tray.notify(
                APP_NAME,
                i18n.t(
                    "notify.model_manager.no_model_selected"
                    if no_model_selected
                    else "notify.model_manager.model_not_downloaded",
                    backend=backend_name,
                ),
            )
        except Exception:
            # A tray failure must never break the load-refusal path.
            log.debug(
                "[MODEL] tray notification for load refusal failed (non-fatal)",
                exc_info=True,
            )
        return reason

    def _model_downloaded_precheck(self) -> bool:
        """Fast filesystem probe: is the active backend's model on disk?

        Returns True (proceed with load) when the config is not a real
        ``Config`` (test doubles — the probe must never read the real
        user's HF cache from a unit test), the backend is cloud/unknown
        (no local model to gate), or the model size is unknown. Only
        definitively-absent LOCAL models are refused. The probe itself
        is TTL-cached (5s) and costs one stat.
        """
        from voice_typer.server.tray_models import is_active_model_downloaded

        return is_active_model_downloaded(self._app.config)

    def _mark_deliberately_unloaded(self, backend_name: str | None) -> None:
        """Record a deliberate unload (idle-unload / force-unload /
        LRU eviction / model change) for ``backend_name``.

        Lazily creates ``_deliberately_unloaded`` so ``__new__``-
        constructed test fixtures (which bypass ``__init__``) that call
        ``_evict_lru_model`` / ``_do_idle_unload`` etc. don't raise
        ``AttributeError`` — mirrors the defensive ``getattr`` pattern
        in ``cancel_idle_unload_timer``.
        """
        if not backend_name:
            return
        s = getattr(self, "_deliberately_unloaded", None)
        if s is None:
            s = self._deliberately_unloaded = set()
        s.add(backend_name)

    def _clear_deliberately_unloaded(self, backend_name: str | None) -> None:
        """Clear the deliberate-unload flag after a successful load."""
        s = getattr(self, "_deliberately_unloaded", None)
        if s is not None and backend_name:
            s.discard(backend_name)

    def _was_deliberately_unloaded(self, backend_name: str) -> bool:
        """Return True if ``backend_name`` was deliberately unloaded
        this session (idle-unload / force-unload / LRU eviction /
        model change)."""
        s = getattr(self, "_deliberately_unloaded", None)
        return bool(s) and backend_name in s

    def _model_load_in_progress(self) -> bool:
        """Return True while a background load / model-change /
        backend-change thread is alive.

        During these windows the backend is REGISTERED but not yet
        loaded (``is_loaded=False``), so a ``get_active`` last-resort
        fall-through is a false positive — the model is about to be
        ready, not broken. The last-resort tray notification must be
        suppressed until the load settles.
        """
        for thread in (
            self._model_load_thread,
            self._model_change_thread,
            self._backend_change_thread,
        ):
            if thread is not None and thread.is_alive():
                return True
        return bool(self._sync_load_in_progress)

    def _in_deliberate_unload_window(self, backend_name: str) -> bool:
        """Return True while the app is in a deliberate-unload window for
        ``backend_name``: the app is shutting down, a load / model-change /
        backend-change thread is alive (or a synchronous load is
        running), or the backend was deliberately unloaded this session.

        Shared by ``_should_suppress_last_resort_notification`` and
        ``_should_suppress_backend_disabled_notification`` so BOTH
        event surfaces (the ``asr_last_resort_unloaded`` tray/toast and
        the ``asr_backend_disabled`` event) suppress during the same
        windows — a backend that is mid-switch or deliberately released
        is not "broken", so neither surface should alert.
        """
        if getattr(self._app, "_shutting_down", False):
            return True
        if self._model_load_in_progress():
            return True
        return self._was_deliberately_unloaded(backend_name)

    def _should_suppress_backend_disabled_notification(self, backend_name: str) -> bool:
        """Return True when the ``asr_backend_disabled`` alert must be
        suppressed for ``backend_name``.

        Wired as the breaker's backend-disabled event gate in
        ``__init__`` so the ``asr_backend_disabled`` event_bus publish
        (consumed by the renderer) matches the last-resort suppression
        windows — during a deliberate unload (idle-unload / force-unload /
        LRU eviction / model change) or while a load is in progress, a
        transient load failure can trip the breaker and would otherwise
        publish a spurious "backend disabled" event telling the user the
        backend is permanently broken when the app is merely switching.

        Unlike ``_should_suppress_last_resort_notification`` this has NO
        cooldown rate limit: the disabled event fires at most once per
        breaker trip (``_record_failure`` skips already-disabled
        backends), so there is nothing to spam. The state mutation
        (disabling the backend in the breaker) is intentionally NOT
        gated — only the notification surface.
        """
        if self._in_deliberate_unload_window(backend_name):
            log.debug(
                "[MODEL] backend-disabled %s suppressed (deliberate-unload window)",
                backend_name,
            )
            return True
        return False

    def _should_suppress_last_resort_notification(self, backend_name: str) -> bool:
        """Return True when the last-resort alert must be suppressed for
        ``backend_name``.

        Shared by the tray notification path
        (``_on_last_resort_unloaded``) and the event_bus suppression
        gate (wired in ``__init__``) so the renderer toast that consumes
        the ``asr_last_resort_unloaded`` event matches the tray
        notification's suppressions exactly — the toast cannot see these
        ModelManager-side checks otherwise.

        Suppressed when:
        * the app is shutting down (tray may be torn down);
        * a load / model-change / backend-change thread is alive (or a
          synchronous load is running) — the backend is registered but
          about to load, not actually broken;
        * the backend was deliberately unloaded this session
          (idle-unload / force-unload / LRU eviction / model change) —
          the model IS on disk, a download nudge would be wrong;
        * the same backend was notified within
          ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS`` (the registry latch
          can be reset by the 15s get_status probe, so this rate
          limit stops both surfaces from being spammed).

        The first three checks are delegated to
        :meth:`_in_deliberate_unload_window` (shared with the
        backend-disabled gate so both surfaces suppress during the same
        windows); the cooldown is last-resort-only.

        NOTE: the cooldown read here is read-only — the timestamp is
        recorded by the tray subscriber after a non-suppressed
        transition (``_on_last_resort_unloaded``), so the two callers
        can't double-record.
        """
        if self._in_deliberate_unload_window(backend_name):
            if self._was_deliberately_unloaded(backend_name):
                log.debug(
                    "[MODEL] last-resort unloaded %s suppressed (deliberate unload — model on disk)",
                    backend_name,
                )
            return True
        import time

        now = time.monotonic()
        # ``None`` sentinel (not ``0.0``): monotonic() on Windows is
        # seconds since boot, so ``now - 0.0`` could already exceed the
        # cooldown and let a REPEAT through (or, on a fresh boot,
        # suppress the FIRST notification). Only a real timestamp
        # (i.e. "this backend was notified before") engages the limit.
        # Defensive ``getattr`` (mirrors ``_mark_deliberately_unloaded``)
        # so a ``__new__``-constructed fixture (which bypasses
        # ``__init__``) that somehow reaches this helper doesn't raise
        # AttributeError inside the subscriber/gate path.
        last = getattr(self, "_last_resort_notified_at", {}).get(backend_name)
        return (
            last is not None
            and now - last < self._LAST_RESORT_NOTIFY_COOLDOWN_SECS
        )

    def _on_last_resort_unloaded(self, backend_name: str) -> None:
        """Show a tray notification when ``get_active()`` falls through
        to an unloaded last-resort backend.

        Wired as the production ``on_last_resort`` subscriber in
        ``__init__`` (the subscriber set existed but was never wired —
        the documented tray notification was dead code). Without this,
        an unloaded backend (e.g. the model failed to load / is not
        downloaded) silently returns empty transcriptions with no
        visible feedback — exactly the ``transcription may return empty
        silently`` WARN the user sees every 15s.

        Always points the user at the Models page with the download
        instruction (the app never auto-downloads models). When a host
        (Electron/Tauri) is connected, the notification is published as a
        ``notification`` event carrying ``click_path: "/models"`` so the
        host renders a CLICKABLE native toast that opens the Models page
        on click (pystray Win32 balloons cannot carry click handlers);
        without a live transport the pystray balloon fallback is used.

        Suppression is delegated to
        :meth:`_should_suppress_last_resort_notification` (shared with
        the event_bus gate so the renderer toast matches the tray).
        """
        if self._should_suppress_last_resort_notification(backend_name):
            return
        import time

        # Record the cooldown timestamp here (the alert is about to be
        # shown); the shared helper's cooldown read stays read-only so
        # the two callers can't double-record.
        self._last_resort_notified_at[backend_name] = time.monotonic()
        # Respect the user's notifications toggle (mirrors
        # ``tray_notifications.notify`` — the event-bus path below must
        # not bypass it).
        if not getattr(self._app.tray, "_notifications_enabled", True):
            return
        message = i18n.t(
            "notify.model_manager.last_resort_unloaded",
            backend=backend_name,
        )
        # Prefer a CLICKABLE host notification: when an Electron (or
        # Tauri) host is connected, publish a ``notification`` event with
        # a ``click_path`` so the host renders a native toast whose click
        # opens the Models page directly (the Electron main-process
        # ``notification`` push handler wires ``Notification.on("click")``
        # → show window + broadcast ``navigate /models``). pystray Win32
        # balloons (the ``tray.notify`` fallback) cannot carry a click
        # handler, so when no live transport exists (standalone backend)
        # fall back to the pystray balloon.
        from voice_typer.server import event_bus

        live = False
        try:
            live = event_bus.has_live_transport()
        except Exception:
            live = False
        if live:
            try:
                # NOTE: ``event_bus.publish`` returning True only means an
                # in-process subscriber accepted the event — it does NOT
                # prove the host received it (the TCP transport buffers
                # to ``_pending_tcp`` and marks the client dead on write
                # failure instead of raising). If the host dies between
                # the ``has_live_transport`` probe and the publish, the
                # toast is silently dropped with no balloon fallback.
                # Acceptable (single-host window is microseconds wide);
                # the debug log below keeps the drop diagnosable.
                ok = event_bus.publish(
                    {
                        "type": "notification",
                        "data": {
                            "title": APP_NAME,
                            "message": message,
                            "duration_ms": 0,
                            "critical": False,
                            "click_path": "/models",
                        },
                    }
                )
                if not ok:
                    log.debug(
                        "[MODEL] last-resort notification event publish returned False (no subscriber accepted)",
                    )
                return
            except Exception:
                log.debug(
                    "[MODEL] last-resort notification event publish failed — falling back to tray balloon",
                    exc_info=True,
                )
        try:
            self._app.tray.notify(APP_NAME, message)
        except Exception:
            # A tray failure must never break the last-resort path.
            log.debug(
                "[MODEL] last-resort tray notification failed (non-fatal)",
                exc_info=True,
            )

    def load_background(self) -> None:
        """Background worker: create + load the transcription engine.

        Runs in a daemon thread so the heavy torch/transformers import and
        weight download/read (off-disk on cold boot) do not block the app
        reaching an interactive state.  All tray state transitions happen
        here; if a dictation is pending (user pressed F2 during load), it
        is auto-started once loading succeeds.
        """
        # bail out early if shutdown was signalled while this
        # loader was queued. Without this guard the loader would proceed
        # to construct + load an ASR backend after ``_do_cleanup`` has
        # already torn down the tray, recorder, hotkeys, etc., touching
        # freed state. The thread_registry join (3s timeout) gives the
        # in-flight loader a chance to exit, but this early check avoids
        # the race where the loader hasn't started its first instruction
        # yet.
        if self._app._shutting_down:
            log.debug("[MODEL] load_background skipped — shutdown already in progress")
            return
        # Capture the backend/model BEFORE the try so the except handler
        # can log them without re-reading ``self._app.config`` (which
        # could itself be the source of the original exception — a
        # degraded/None config would make the handler raise a SECOND
        # exception, skipping the pending-dictation clear and the tray
        # ERROR transition).
        backend_name = getattr(self._app.config, "asr_backend", "unknown")
        model_size = getattr(self._app.config, "model_size", "unknown")
        try:
            # Fast existence pre-check: if the configured model is
            # definitively NOT on disk, refuse immediately — BEFORE the
            # heavy engine import, BEFORE the misleading "Loading model"
            # LOADING state, and with a GENERIC message (no model name).
            # The load path would raise ModelNotDownloadedError anyway
            # (the registry re-raises for a missing primary — no whisper
            # fallback), so this only skips wasted work. Cloud backends /
            # unknown model sizes return True from the probe (nothing to
            # gate); the probe is TTL-cached and costs one stat.
            if not self._model_downloaded_precheck():
                if model_size == NO_MODEL_SIZE:
                    # Genuine "no model selected" state — nothing to
                    # load, and no phantom model to claim is missing.
                    log.warning(
                        "[MODEL] no model selected — refusing load before heavy "
                        "import; open the Models page to pick a model",
                    )
                    self._notify_model_load_refused(
                        ModelNotDownloadedError(
                            "No model selected. Open the Models page to pick a model.",
                            model_size=NO_MODEL_SIZE,
                            backend=backend_name,
                        ),
                        backend=backend_name,
                    )
                else:
                    log.warning(
                        "[MODEL] %s model not downloaded (model=%s) — refusing load "
                        "before heavy import; open the Models page to download it "
                        "or pick another model",
                        backend_name,
                        model_size,
                    )
                    self._notify_model_load_refused(
                        ModelNotDownloadedError(
                            f"The configured {backend_name} model is not downloaded. "
                            "Open the Models page to download a model.",
                            model_size=model_size,
                            backend=backend_name,
                        ),
                        backend=backend_name,
                    )
                self._pending_dictation = False
                return
            self._ensure_engine(backend_name)

            # Set tray state before heavy import so user sees progress
            self._app.tray.set_state(
                AppState.LOADING, i18n.t("state.model_manager.loading")
            )

            def on_progress(msg: str):
                self._app.tray.set_state(AppState.LOADING, msg)

            success = self._registry.load_with_fallback(progress_callback=on_progress)

            if success:
                #  PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict the
                # LRU model if more than _MAX_LOADED_MODELS are now loaded.
                # Guarded so a tracking failure doesn't break the load.
                try:
                    self.touch_model(self._registry.active_name)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF] LRU tracking failed (non-fatal)",
                        exc_info=True,
                    )
                # Successful load → the backend is healthy again; clear
                # any deliberate-unload flag so a FUTURE genuine failure
                # re-notifies the user.
                self._clear_deliberately_unloaded(self._registry.active_name)
                active = self._registry.get_active()
                name = self._registry.active_name
                if name == "whisper" and active is not None:
                    self._app.tray.set_state(
                        AppState.IDLE,
                        i18n.t(
                            "state.model_manager.ready_whisper",
                            device_info=active.device_info,
                        ),
                    )
                else:
                    self._app.tray.set_state(
                        AppState.IDLE,
                        i18n.t(
                            "state.model_manager.ready_other", name=name.title()
                        ),
                    )
            else:
                if self._app._shutting_down:
                    return
                # List which backends were attempted
                # so the user (and support) can see exactly what failed,
                # plus a remediation hint. ``available_backends`` returns
                # the registered backend names; ``active_name`` is the one
                # that was selected as primary.
                # pyrefly not-callable — ``available_backends`` is a
                # @property on ASRRegistry (asr_registry.py:534-538) returning
                # ``list[str]``, NOT a method. Calling it (``()``) raises
                # ``TypeError: 'list' object is not callable`` at runtime.
                # Use the property's value directly (no parentheses). Guard
                # with ``callable()`` so test doubles that override the
                # attribute with a callable (e.g. MagicMock auto-spec) still
                # work.
                _backends = self._registry.available_backends
                if callable(_backends):
                    _backends = _backends()
                # Narrow ``_backends`` to a list before joining.
                # ``available_backends`` is a ``@property`` returning
                # ``list[str]``, but the ``callable(_backends)`` fallback
                # (for MagicMock test doubles that override the attribute
                # with a callable) widens the inferred type to
                # ``list[str] | object`` — ``str.join`` rejects ``object``
                # (not iterable). At runtime the value is always a list.
                if isinstance(_backends, list):
                    _attempted = ", ".join(str(_b) for _b in _backends) or "(none registered)"
                else:
                    _attempted = "(none registered)"
                _primary = getattr(self._app.config, "asr_backend", "unknown")
                log.warning(
                    "[STARTUP] All backends failed to load "
                    "(primary=%s, attempted=[%s]). "
                    "Recovery: press your hotkey to retry, or change the backend "
                    "in Settings -> Models.",
                    _primary,
                    _attempted,
                )
                self._app.tray.set_state(
                    AppState.ERROR, i18n.t("state.model_manager.load_failed_retry")
                )
                # Clear the pending-dictation flag so the ``finally``
                # block does NOT auto-start a dictation that would
                # immediately fail (no model is loaded). Pre-fix, the
                # flag was NOT cleared and the finally block
                # unconditionally scheduled ``_start_dictation``, which
                # fell through to ``fallback_to_whisper`` (same root
                # cause), entered a tight retry loop, and spammed the
                # tray with ERROR state.
                self._pending_dictation = False

        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # The selected model isn't on disk (or failed integrity
            # verification) — the app never auto-downloads. Surface an
            # actionable message and do NOT auto-start any pending
            # dictation (it would fail the same way).
            self._notify_model_load_refused(exc, backend=backend_name)
            self._pending_dictation = False
        except Exception:
            log.exception(
                "[STARTUP] Background model load crashed (backend=%s, model=%s)",
                backend_name,
                model_size,
            )
            self._app.tray.set_state(
                AppState.ERROR, i18n.t("state.model_manager.load_failed_retry")
            )
            # Same failure-path guard as above: a crash must NOT trigger
            # the finally's auto-start of a pending dictation (it would
            # crash again on the same root cause).
            self._pending_dictation = False
        finally:
            self._model_load_thread = None
            # If the user pressed F2 during load, honour it now — but
            # ONLY on success. On failure/crash the ``_pending_dictation``
            # flag was already cleared by the error paths above, so this
            # check skips the auto-start (which would otherwise loop on
            # ``fallback_to_whisper`` and fail the same way, spamming the
            # tray with ERROR state).
            if self._pending_dictation and not self._app._shutting_down:
                log.info("[STARTUP] Pending dictation -- auto-starting now")
                self._pending_dictation = False
                # Schedule off this loader thread to avoid nesting
                self._app._schedule_timer(0, self._app._start_dictation)

    def start_background_load(self) -> None:
        """Spawn the background model-load thread (idempotent).

        register the thread with ``app._thread_registry`` so
        ``shutdown_all()`` can join it during ``quit()``. Previously
        the ModelLoad thread was a daemon but untracked — it was
        indirectly signalled via ``_shutting_down`` checks inside
        ``load_background``, which meant a stuck model load (e.g. a
        slow Whisper download on a cold boot) could outlive
        ``_do_cleanup`` and access torn-down state (tray, recorder,
        hotkeys). With registration, ``shutdown_all()`` joins it with
        a 3s timeout, matching the existing transcription-thread join
        in ``_do_cleanup``. ``stop_event=None`` because the loader
        has no single cancellation point — it checks
        ``_app._shutting_down`` itself at the top of
        ``load_background``.
        """
        if self._model_load_thread is not None and self._model_load_thread.is_alive():
            return
        with self._model_load_spawn_lock:
            # Re-check under the lock — a concurrent caller may have
            # spawned the thread between our check and the lock
            # acquisition.
            if self._model_load_thread is not None and self._model_load_thread.is_alive():
                return
            self._model_load_thread = threading.Thread(
                target=self.load_background,
                name="ModelLoad",
                daemon=True,
            )
            self._model_load_thread.start()
        # track the loader centrally so shutdown_all() can
        # signal-and-join it. Best-effort — if the registry is missing
        # (e.g. in a stripped-down test fixture) we log and continue;
        # the loader is a daemon and will die on process exit anyway.
        try:
            self._app._thread_registry.register(
                name="ModelLoad",
                thread=self._model_load_thread,
                stop_event=None,
                join_timeout=3.0,
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to register ModelLoad thread with thread_registry",
                exc_info=True,
            )

    def fallback_to_whisper(self, notify_on_failure: bool = False) -> None:
        """Fallback to Whisper tiny after Parakeet/Qwen backend failed.

        008: Uses the registry to reconfigure and reload.
        Switches config to whisper/tiny (the smallest Whisper model —
        deliberately a LITERAL, not ``DEFAULT_MODEL_SIZE``: the
        fallback must stay small/fast even if the config default
        changes), ensures the whisper backend is registered, and
        delegates loading to AsrBackendRegistry.load_with_fallback().
        """
        self._app.config.model_size = "tiny"
        self._app.config.asr_backend = "whisper"
        #  persist the fallback so the next boot
        # doesn't re-try the failed backend and repeat the failure
        # loop.  Previously the config mutation was in-memory only —
        # if the app crashed after fallback, the next boot read the
        # original (failed) backend from disk and re-entered the
        # failure loop on every boot.  Mirrors the persist pattern in
        # ``change_model`` (line 557).
        try:
            self._app.config.save()
        except Exception:
            # Previously missing the ``[MODEL]``
            # topic prefix used by every other log call in this module.
            # Adding it keeps the log topic-consistent so log filters /
            # greps work.
            log.warning("[MODEL] failed to persist fallback config", exc_info=True)
        existing = self._registry.get("whisper")
        if existing is None:
            self._registry.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size="tiny",
                    device=self._app.config.device,
                    language=self._app.config.language,
                    beam_size=self._app.config.beam_size,
                    best_of=self._app.config.best_of,
                    condition_on_previous_text=self._app.config.condition_on_previous_text,
                    # pass live Config so consent check works.
                    config=self._app.config,
                ),
            )
        else:
            existing.model_size = "tiny"
            existing._configured_model_size = "tiny"
            # also backfill the config reference on the
            # existing engine in case it was constructed without one
            # (e.g. by an older code path or a test).  No-op if the
            # engine already has a non-None config.
            if getattr(existing, "config", None) is None:
                existing.config = self._app.config

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_with_fallback(progress_callback=on_progress)
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # Whisper tiny isn't downloaded either — surface the
            # actionable message instead of crashing the hotkey thread.
            self._notify_model_load_refused(exc, backend="whisper")
            return
        if success:
            #  PERF-015 LRU eviction — touch the
            # freshly-loaded backend so it's tracked, then evict the
            # LRU model if more than _MAX_LOADED_MODELS are now loaded.
            # Guarded so a tracking failure doesn't break the load.
            try:
                self.touch_model(self._registry.active_name)
                self._evict_lru_model()
            except Exception:
                log.warning(
                    "[PERF] LRU tracking failed (non-fatal)",
                    exc_info=True,
                )
            # Successful load → backend healthy again; clear any
            # deliberate-unload flag so a future genuine failure
            # re-notifies.
            self._clear_deliberately_unloaded(self._registry.active_name)
            active = self._registry.get_active()
            self._app.tray.set_state(
                AppState.IDLE,
                f"Ready -- {active.device_info}" if active else "Ready",
            )
        else:
            self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
            if notify_on_failure:
                # critical — bypass toggle (model load failed).
                # Use the i18n key so the tray tooltip + OS notification
                # render in the user's selected UI locale, and name the
                # user's ACTUAL configured hotkey in the retry hint.
                self._app.tray.notify_safety(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.load_failed_critical",
                        hotkey=notification_hotkey_label(self._app.config.hotkey),
                    ),
                )

    def try_load(self, notify_on_failure: bool = False) -> None:
        """Attempt to load the transcription model.

        008: Delegates to AsrBackendRegistry.load_with_fallback()
        instead of calling self.transcriber.load() directly.

        Prewarm became a worker startup phase (master plan §6.2 P-1):
        the previous ADR-0009 Issue 4 wait-for-prewarm handshake (wait
        for a separate prewarm process to finish, spawn a fresh
        background prewarm on timeout) was removed along with the
        deleted prewarm machinery. Each worker spawn warms the OS
        file cache itself before accepting the first transcription
        request, so there is no separate process to wait for or
        re-spawn here.
        """
        self._model_load_attempted = True
        try:
            log.info(
                "[MODEL] Loading model (backend=%s, size=%s, device=%s)...",
                self._app.config.asr_backend,
                self._app.config.model_size,
                self._app.config.device,
            )

            def on_progress(message: str):
                self._app.tray.set_state(AppState.LOADING, message)

            success = self._registry.load_with_fallback(progress_callback=on_progress)
            if success:
                #  PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict the
                # LRU model if more than _MAX_LOADED_MODELS are now loaded.
                # Guarded so a tracking failure doesn't break the load.
                try:
                    self.touch_model(self._registry.active_name)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF] LRU tracking failed (non-fatal)",
                        exc_info=True,
                    )
                # Successful load → backend healthy again; clear any
                # deliberate-unload flag so a future genuine failure
                # re-notifies.
                self._deliberately_unloaded.discard(self._registry.active_name)
                active = self._registry.get_active()
                info = getattr(active, "device_info", "unknown") if active else "unknown"
                self._app.tray.set_state(
                    AppState.IDLE,
                    i18n.t(
                        "state.model_manager.ready_whisper", device_info=info
                    ),
                )
                log.info("[MODEL] Loaded successfully")
            else:
                raise RuntimeError("All backends failed to load")
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            _failed_backend = getattr(self._app.config, "asr_backend", "unknown")
            self._notify_model_load_refused(exc, backend=_failed_backend)
        except Exception as e:
            # Include the model name and backend
            # info so the failure is actionable — the user can see which
            # backend and model size failed and retry / switch via Settings.
            _failed_backend = getattr(self._app.config, "asr_backend", "unknown")
            _failed_model = getattr(self._app.config, "model_size", "unknown")
            log.exception(
                "[MODEL] Load FAILED (backend=%s, model=%s)",
                _failed_backend,
                _failed_model,
            )
            self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
            if notify_on_failure:
                self._app.tray.notify(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.load_failed",
                        error=str(e),
                        hotkey=notification_hotkey_label(self._app.config.hotkey),
                    ),
                )

    def change_model(self, model_size: str) -> dict:
        """Apply a model change for future dictation sessions (non-blocking).

        Previously this method blocked the calling IPC worker for
        5-30s while ``_change_model_load_phase`` ran ``load_active`` (disk
        + torch import + weight load). Now it spawns a background daemon
        thread to do the full setattr+unload+load cycle and returns
        immediately with a "loading" ack. The background thread holds
        ``_model_change_lock`` for the duration so concurrent
        ``change_model`` / ``set_active_backend`` calls serialize. On
        completion the thread publishes an ``asr_backend_ready`` event on
        the event_bus (mirroring the ``asr_backend_disabled`` pattern).

        Handles Whisper, Parakeet, and Qwen backends. Unloads the old
        engine and loads the new one immediately (unless currently
        recording — in which case the change is deferred via
        ``_pending_model_change``).

        Uses the registry to unload/load instead of having
        three separate branches for parakeet/qwen/whisper.

        logs every model change with old/new backend and model size.

         the background thread's body is guarded by
        ``self._model_change_lock`` so two concurrent calls cannot both
        unload + re-register + reload the same backend.

        the background thread acquires ``_config_mutation_lock``
        for the setattr+save+unload phase. See ``_change_model_blocking``
        for the lock-order contract.

        Returns
        -------
        dict
            Ack dict shaped ``{"status": "loading", "previous": {...},
            "pending": {...}}``. Callers that need to wait for the load
            to complete (e.g. ``apply_pending_model_change``, tests)
            should call ``_change_model_blocking`` instead.
        """
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle — otherwise the timer could fire
        # mid-switch and unload the NEW model.
        self.cancel_idle_unload_timer()
        old_backend = self._app.config.asr_backend
        old_model_size = self._app.config.model_size
        # Determine new backend (mirrors _change_model_setattr_phase
        # logic) so the ack dict carries the pending backend.
        if model_size == "parakeet":
            new_backend = "parakeet"
        elif model_size == "qwen":
            new_backend = "qwen"
        else:
            new_backend = "whisper"
        # spawn background daemon thread for the full cycle.
        self._change_model_background(model_size)
        return {
            "status": "loading",
            "previous": {"backend": old_backend, "model_size": old_model_size},
            "pending": {"backend": new_backend, "model_size": model_size},
        }

    def _change_model_background(self, model_size: str) -> None:
        """Spawn a daemon thread to run ``_change_model_blocking``.

        The thread holds ``_model_change_lock`` for the duration of the
        setattr+unload+load cycle so concurrent ``change_model`` /
        ``set_active_backend`` calls serialize. On completion the thread
        publishes an ``asr_backend_ready`` event on the event_bus.

        Mirrors ``start_background_load``'s thread-registration pattern
        () so ``shutdown_all()`` can join the thread during
        ``quit()``. Best-effort registration — if the registry is missing
        (e.g. in a stripped-down test fixture) we log and continue; the
        thread is a daemon and will die on process exit anyway.
        """
        thread = threading.Thread(
            target=self._change_model_blocking,
            args=(model_size,),
            name="ModelChange",
            daemon=True,
        )
        # Track the thread BEFORE start so callers joining
        # ``_model_change_thread`` never miss it (a fast load could
        # otherwise finish between ``start()`` and the assignment).
        self._model_change_thread = thread
        thread.start()
        # track the thread centrally so shutdown_all() can
        # signal-and-join it. Best-effort — re-registering "ModelChange"
        # overwrites the previous entry (with a warning log); the
        # previous thread is still a daemon and will be killed on
        # process exit.
        try:
            self._app._thread_registry.register(
                name="ModelChange",
                thread=thread,
                stop_event=None,
                join_timeout=3.0,
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to register ModelChange thread with thread_registry",
                exc_info=True,
            )

    def _change_model_blocking(self, model_size: str) -> None:
        """Synchronous model change (for test compat and direct callers).

        This is the original ``change_model`` body, preserved as a
        separate method so callers that need to wait for the load to
        complete (e.g. ``apply_pending_model_change``, tests) can do so.
        The IPC handler uses the non-blocking ``change_model`` instead.

        On completion (success or failure), publishes an
        ``asr_backend_ready`` event on the event_bus so subscribers
        (renderer, diagnostics aggregator) know the load finished.
        """
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle.
        self.cancel_idle_unload_timer()
        # outer = _config_mutation_lock (app-level, governs config
        # setattr + save); inner = _model_change_lock (ModelManager-level,
        # guards the unload/reload cycle).  See change_model docstring.
        with self._model_change_lock:
            # Phase 1: setattr + save + unload-old under config lock.
            with self._app._config_mutation_lock:
                new_backend, old_backend, deferred = self._change_model_setattr_phase(model_size)
                if deferred:
                    # Recording in progress — config saved, deferred
                    # flag set, notification shown. Skip the load.
                    return
                # Unload + unregister + clear legacy fields.
                self._change_model_unload_phase(new_backend, old_backend)
            # _config_mutation_lock released here. _model_change_lock
            # still held — concurrent IPC set_config calls can proceed.
            # Phase 2: construct + load the new engine OUTSIDE the
            # config lock (see above). ``NO_MODEL_SIZE`` ("") means the
            # user has no active model — the unload above is the whole
            # change; do NOT construct/load an engine for the empty
            # size (there is no repo for it). The renderer sees
            # ``asr_backend_ready`` with an empty model and shows
            # "No model selected".
            if model_size == NO_MODEL_SIZE:
                failure_reason = None
            else:
                failure_reason = self._change_model_load_phase(new_backend, model_size)
        # Publish asr_backend_ready ONLY on success. On failure, publish
        # asr_backend_load_failed so the renderer can show an error
        # instead of silently dismissing the loading spinner. Published
        # AFTER the lock is released so subscribers don't block on the
        # lock. ``failure_reason is None`` covers the deferred
        # early-return path (no event published — the load didn't
        # happen).
        if failure_reason is None:
            self._publish_backend_ready_event(new_backend, model_size)
        else:
            self._publish_backend_load_failed_event(
                new_backend,
                model_size,
                failure_reason=failure_reason,
            )

    def _change_model_setattr_phase(self, model_size: str) -> tuple[str, str, bool]:
        """Phase 1a: determine backend, setattr + save config.

        Returns ``(new_backend, old_backend, deferred)``. ``deferred`` is True when
        the model change was queued via ``_pending_model_change``
        because a recording is in progress — the caller should skip
        the load phase. ``old_backend`` is captured BEFORE the setattr
        overwrites ``config.asr_backend`` so the unload phase can use
        the correct value.

        Caller MUST hold both ``_config_mutation_lock`` and
        ``_model_change_lock``.
        """
        if model_size == "parakeet":
            new_backend = "parakeet"
        elif model_size == "qwen":
            new_backend = "qwen"
        else:
            new_backend = "whisper"

        old_backend = self._app.config.asr_backend
        log.info(
            "[MODEL] Changing model: %s (%s) -> %s (%s)",
            self._app.config.model_size,
            old_backend,
            model_size,
            new_backend,
        )

        self._app.config.asr_backend = new_backend
        self._app.config.model_size = model_size
        if not self._app.config.save():
            log.warning("[MODEL] config.save() returned False — model change may not persist")

        if self._app.recorder.recording or not self._app._busy_event.is_set():
            log.info(
                "[CONFIG] Model changed to %s (%s); applying after active work",
                model_size,
                new_backend,
            )
            # capture the request so the next _start_dictation
            # re-runs the unload/load cycle. Without this, the config
            # is saved on disk but the in-memory engine stays as the
            # old backend — the "will change after current recording"
            # notification was a lie.
            self._pending_model_change = model_size
            self._app.tray.notify(
                APP_NAME,
                i18n.t("notify.model_manager.change_deferred", model=model_size),
            )
            return new_backend, old_backend, True
        return new_backend, old_backend, False

    def _change_model_unload_phase(self, new_backend: str, old_backend: str) -> None:
        """Phase 1b: unload + unregister + clear legacy fields for the OLD backend.

        Caller MUST hold both ``_config_mutation_lock`` and
        ``_model_change_lock``. ``new_backend`` is the target backend.
        ``old_backend`` is the backend that was active BEFORE the setattr
        phase overwrote ``config.asr_backend`` — captured by the caller
        in ``_change_model_setattr_phase`` and passed in here so we don't
        accidentally read the post-setattr value (which would always
        equal ``new_backend``).

        Note: we ALWAYS unload + unregister, even when
        ``old_backend == new_backend``. This is required for whisper,
        where ``model_size`` may have changed and a fresh
        ``TranscriptionEngine`` must be constructed with the new
        ``model_size`` kwarg. Skipping the unload (the
        optimization) broke ``test_model_change_uses_config_device``.
        """
        # Deliberate unload — the old backend is being swapped out for a
        # new one; the last-resort tray notification must NOT tell the
        # user to download a backend they explicitly switched away from.
        self._mark_deliberately_unloaded(old_backend)
        # Unload old backend via registry
        self._registry.unload(old_backend)
        # #2 UNREGISTER the old backend so _ensure_engine
        # actually constructs a fresh one. Previously unload() only
        # called backend.unload() but left the backend in the registry,
        # so _ensure_engine's "if registry.get(name) is not None: return"
        # short-circuited and no new engine was constructed.
        self._registry.unregister(old_backend)
        self._model_load_attempted = False

        # Clear old engine fields
        if old_backend == "parakeet":
            self._parakeet_engine = None
        elif old_backend == "qwen":
            self._qwen_engine = None
        elif self.transcriber is not None:
            with contextlib.suppress(Exception):
                self.transcriber.unload()
            self.transcriber = None

    def _change_model_load_phase(self, new_backend: str, model_size: str) -> str | None:
        """Phase 2: construct + load the new engine.

        Caller MUST hold ``_model_change_lock``. Must NOT hold
        ``_config_mutation_lock`` (see above).

        ``model_size`` is the user-requested model size (operation
        input) — included in the failure log so a model-load failure
        report shows the input that produced the failure, not just the
        backend name. The underlying exception is already logged one
        level down by ``AsrBackendRegistry.load_active`` via
        ``log.exception`` (see ``asr_registry.py``).

        Returns
        -------
        str | None
            ``None`` on success; a short human-readable
            ``failure_reason`` string on failure (falsy ``load_active``
            return or raised exception). The caller
            (``_change_model_blocking``) uses the return value to decide
            whether to publish ``asr_backend_ready`` vs
            ``asr_backend_load_failed``.
        """
        # Create new engine object via registry.create()
        self._ensure_engine(new_backend)

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_active(progress_callback=on_progress)
            if success:
                #  PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict
                # the LRU model if more than _MAX_LOADED_MODELS are
                # now loaded. Guarded so a tracking failure doesn't
                # break the load.
                try:
                    self.touch_model(new_backend)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF] LRU tracking failed for %s (non-fatal)",
                        new_backend,
                        exc_info=True,
                    )
                # Successful load → backend healthy; clear any
                # deliberate-unload flag for it.
                self._clear_deliberately_unloaded(new_backend)
                active = self._registry.get_active()
                if new_backend == "whisper" and active is not None:
                    self._app.tray.set_state(
                        AppState.IDLE,
                        i18n.t(
                            "state.model_manager.ready_whisper",
                            device_info=active.device_info,
                        ),
                    )
                else:
                    self._app.tray.set_state(
                        AppState.IDLE,
                        i18n.t(
                            "state.model_manager.ready_other",
                            name=new_backend.title(),
                        ),
                    )
                self._app.tray.invalidate_menu_cache()
                return None
            log.warning(
                "[MODEL] %s model failed to load (model_size=%s)",
                new_backend.title(),
                model_size,
            )
            self._app.tray.set_state(
                AppState.ERROR,
                i18n.t(
                    "state.model_manager.backend_failed",
                    backend=new_backend.title(),
                ),
            )
            return f"{new_backend.title()} model failed to load"
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            return self._notify_model_load_refused(exc, backend=new_backend)
        except Exception as exc:
            log.exception("[MODEL] Model load failed: %s", exc)
            self._app.tray.set_state(
                AppState.ERROR,
                i18n.t("state.model_manager.model_failed", error=str(exc)),
            )
            return f"load_active raised: {exc}"

    # set_active_backend — switch ASR backend WITHOUT changing
    # model_size. Mirrors change_model's unload/reload cycle but only
    # swaps the backend. The model_size field is left untouched so
    # Whisper's model selection (which depends on model_size) is
    # preserved across backend switches. For parakeet/qwen, model_size
    # is informational (their engines ignore it).
    def set_active_backend(self, backend: str) -> dict:
        """Switch the active ASR backend WITHOUT changing ``model_size`` (non-blocking).

        Previously this method blocked the calling IPC worker for
        5-30s while ``load_active`` ran. Now it spawns a background
        daemon thread (mirroring ``change_model``'s pattern) and returns
        immediately with a "loading" ack. On completion the thread
        publishes an ``asr_backend_ready`` event on the event_bus.

        Previously ``Service.set_active_backend`` delegated to
        ``self._app.models.set_active_backend(backend)`` but
        :class:`ModelManager` never defined that method — the IPC
        ``set_config`` handler caught the ``AttributeError`` and logged
        a warning, returning ``ack`` to the renderer while the actual
        backend swap never happened. Old backends stayed loaded (GPU
        + RAM) until LRU eviction.

        if the user is recording or the transcribe thread holds
        the busy event (mid-transcription), DEFER — persist config +
        capture the requested backend in ``_pending_backend_change`` +
        notify "will change after current recording" + return a
        "deferred" ack. The actual unload/load cycle runs on the next
        :meth:`apply_pending_model_change` (called from
        ``recording_controller.start`` before the new recording begins).
        Without this guard, the background thread would run the unload
        phase mid-transcription, unloading the ctranslate2 model from
        underneath the in-flight transcribe thread (crash / heap
        corruption / stuck thread — see  in review.md).

        Parameters
        ----------
        backend :
            One of ``"whisper"``, ``"qwen"``, ``"parakeet"``. Any other
            value raises :class:`ValueError`.

        Returns
        -------
        dict
            Ack dict shaped ``{"status": "loading"|"ready"|"deferred",
            "previous": {...}, "pending": {...}}``. ``status == "ready"``
            when the backend is already active (fast-path no-op).
            ``status == "deferred"`` when the change was queued via
            ``_pending_backend_change`` ().

        Raises
        ------
        ValueError
            If ``backend`` is not one of ``"whisper"``, ``"qwen"``,
            ``"parakeet"``. The validation happens synchronously BEFORE
            any thread spawn so the IPC handler's ``failed_keys``
            mechanism still catches it.
        """
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle.
        self.cancel_idle_unload_timer()
        old_backend = self._app.config.asr_backend
        old_model_size = self._app.config.model_size
        # Fast-path no-op: if the backend is already active, don't
        # spawn a background thread. (The background thread re-checks
        # this under the lock for race-safety, but the fast path avoids
        # needless thread churn in the common case.)
        if old_backend == backend:
            return {
                "status": "ready",
                "previous": {"backend": old_backend, "model_size": old_model_size},
                "pending": {"backend": backend, "model_size": old_model_size},
            }
        # busy/recording guard, mirroring ``change_model``'s
        # ``_change_model_setattr_phase`` deferral pattern. If the user
        # is recording OR the transcribe thread holds the busy event
        # (mid-transcription), we MUST NOT run the unload phase —
        # unloading the ctranslate2 model mid-inference crashes /
        # corrupts / hangs the transcribe thread. Persist config +
        # capture the request + notify, then return a "deferred" ack.
        # The next ``apply_pending_model_change`` (called from
        # ``recording_controller.start`` before the new recording
        # begins) re-invokes ``set_active_backend`` when the app is no
        # longer busy. The check is best-effort outside the lock —
        # ``_set_active_backend_blocking`` re-checks ``recorder.recording``
        # and ``_busy_event`` INSIDE ``_model_change_lock`` +
        # ``_config_mutation_lock`` for race-safety (and re-defers if a
        # recording started between this check and the lock acquisition).
        try:
            is_recording = bool(self._app.recorder.recording)
            is_busy = not self._app._busy_event.is_set()
        except Exception:
            log.debug(
                "[MODEL] busy/recording check in set_active_backend failed (non-fatal)",
                exc_info=True,
            )
            is_recording = False
            is_busy = False
        if is_recording or is_busy:
            log.info(
                "[CONFIG] Backend change to %s; applying after active work",
                backend,
            )
            # Persist the new backend so a crash mid-recording doesn't
            # lose the user's intent (matches ``change_model``'s
            # setattr-before-deferral pattern).
            self._app.config.asr_backend = backend
            if not self._app.config.save():
                log.warning("[MODEL] config.save() returned False during set_active_backend (deferred)")
            # Capture the request — ``apply_pending_model_change`` will
            # re-invoke ``set_active_backend`` when the app is no longer
            # busy. Because we are not currently recording at that point
            # and ``_busy_event`` is set (not busy), this deferral
            # branch is skipped and the full unload/load cycle runs.
            self._pending_backend_change = backend
            self._app.tray.notify(
                APP_NAME,
                i18n.t("notify.model_manager.backend_change_deferred", backend=backend),
            )
            return {
                "status": "deferred",
                "previous": {"backend": old_backend, "model_size": old_model_size},
                "pending": {"backend": backend, "model_size": old_model_size},
            }
        # spawn background daemon thread for the full cycle.
        self._set_active_backend_background(backend)
        return {
            "status": "loading",
            "previous": {"backend": old_backend, "model_size": old_model_size},
            "pending": {"backend": backend, "model_size": old_model_size},
        }

    def _set_active_backend_background(self, backend: str) -> None:
        """Spawn a daemon thread to run ``_set_active_backend_blocking``.

        Mirrors ``_change_model_background``'s thread-registration pattern.
        The thread holds ``_model_change_lock`` for the duration so
        concurrent ``change_model`` / ``set_active_backend`` calls
        serialize.
        """
        thread = threading.Thread(
            target=self._set_active_backend_blocking,
            args=(backend,),
            name="BackendChange",
            daemon=True,
        )
        # Track the thread BEFORE start (see ``_change_model_background``).
        self._backend_change_thread = thread
        thread.start()
        try:
            self._app._thread_registry.register(
                name="BackendChange",
                thread=thread,
                stop_event=None,
                join_timeout=3.0,
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to register BackendChange thread with thread_registry",
                exc_info=True,
            )

    def _set_active_backend_blocking(self, backend: str) -> None:
        """Synchronous backend switch (for test compat and direct callers).

        This is the original ``set_active_backend`` body,
        preserved as a separate method so callers that need to wait for
        the load to complete (tests) can do so. The IPC handler uses
        the non-blocking ``set_active_backend`` instead.

        On completion, publishes ``asr_backend_ready`` ONLY on success.
        On failure (load_active returned falsy OR raised), publishes
        ``asr_backend_load_failed`` with ``{"backend": ..., "failure_reason": ...}``
        so the renderer can show an error instead of silently
        dismissing the loading spinner. The deferred case (recording
        in progress) and the no-op case (backend already active)
        publish no event.
        """
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle — otherwise the timer could fire
        # mid-switch and unload the NEW model. The post-load touch_model
        # call below re-arms a fresh timer on the new backend.
        self.cancel_idle_unload_timer()
        # Outer = _model_change_lock (held throughout the
        # unload+construct+load cycle). Inner = _config_mutation_lock
        # (acquired only for setattr + save + the quick unload phase).
        # ``load_outcome`` captures whether the load succeeded so we
        # can publish the correct event AFTER the lock is released.
        # Initialised to ``None`` so the no-op / deferred early returns
        # skip event publication (their existing behaviour).
        load_outcome: bool | None = None
        with self._model_change_lock:
            with self._app._config_mutation_lock:
                old_backend = self._app.config.asr_backend
                if old_backend == backend:
                    # No-op — backend already active.
                    return
                # Re-check ``recorder.recording`` and ``_busy_event``
                # INSIDE both locks for race-safety. The non-blocking
                # ``set_active_backend`` wrapper checks these OUTSIDE the
                # lock (best-effort) before spawning this background
                # thread; a recording could have started between that
                # check and the lock acquisition. Without this re-check,
                # we would unload the ctranslate2 model mid-inference
                # (crashing / corrupting / hanging the transcribe
                # thread). Mirrors the deferral pattern in
                # ``_change_model_setattr_phase`` (line ~891).
                try:
                    rec_now = bool(self._app.recorder.recording)
                    busy_now = not self._app._busy_event.is_set()
                except Exception:
                    log.debug(
                        "[MODEL] busy/recording re-check in _set_active_backend_blocking failed (non-fatal)",
                        exc_info=True,
                    )
                    rec_now = False
                    busy_now = False
                if rec_now or busy_now:
                    log.info(
                        "[CONFIG] Backend change to %s deferred (recording=%s, "
                        "busy=%s at lock-acquire time); applying after active work",
                        backend,
                        rec_now,
                        busy_now,
                    )
                    # Persist the new backend so a crash mid-recording
                    # doesn't lose the user's intent (matches
                    # ``change_model``'s setattr-before-deferral pattern).
                    self._app.config.asr_backend = backend
                    if not self._app.config.save():
                        log.warning(
                            "[MODEL] config.save() returned False during _set_active_backend_blocking (deferred)"
                        )
                    # Capture the request — ``apply_pending_model_change``
                    # will re-invoke ``_set_active_backend_blocking`` when
                    # the app is no longer busy.
                    self._pending_backend_change = backend
                    self._app.tray.notify(
                        APP_NAME,
                        i18n.t("notify.model_manager.backend_change_deferred", backend=backend),
                    )
                    return
                log.info(
                    "[MODEL] Switching active backend: %s -> %s (model_size=%s unchanged)",
                    old_backend,
                    backend,
                    self._app.config.model_size,
                )
                # Unload old backend via the shared helper. Pass
                # ``backend`` as ``new_backend`` and the captured
                # ``old_backend`` so the helper sees "different
                # backends" and performs the unload.
                self._change_model_unload_phase(backend, old_backend)
                # Set config + persist
                self._app.config.asr_backend = backend
                if not self._app.config.save():
                    log.warning("[MODEL] config.save() returned False during set_active_backend")
                # Pre-construct new backend (no load yet).
                self._ensure_engine(backend)
            # _config_mutation_lock released. _model_change_lock still held.
            # Load the new backend.

            def on_progress(msg: str):
                self._app.tray.set_state(AppState.LOADING, msg)

            try:
                success = self._registry.load_active(progress_callback=on_progress)
                if success:
                    try:
                        self.touch_model(self._registry.active_name)
                        self._evict_lru_model()
                    except Exception:
                        log.warning(
                            "[PERF] LRU tracking failed (non-fatal)",
                            exc_info=True,
                        )
                    # Successful load → backend healthy; clear any
                    # deliberate-unload flag for it.
                    self._clear_deliberately_unloaded(backend)
                    active = self._registry.get_active()
                    name = self._registry.active_name
                    if name == "whisper" and active is not None:
                        self._app.tray.set_state(AppState.IDLE, f"Ready -- {active.device_info}")
                    else:
                        self._app.tray.set_state(AppState.IDLE, f"Ready -- {name.title()} ASR")
                    self._app.tray.invalidate_menu_cache()
                    load_outcome = True
                else:
                    log.warning(
                        "[MODEL] %s backend failed to load during set_active_backend",
                        backend.title(),
                    )
                    self._app.tray.set_state(
                        AppState.ERROR,
                        f"{backend.title()} backend failed to load",
                    )
                    load_outcome = False
            except (ModelNotDownloadedError, ModelIntegrityError) as exc:
                self._notify_model_load_refused(exc, backend=backend)
                load_outcome = False
            except Exception as exc:
                log.exception("[MODEL] set_active_backend load failed: %s", exc)
                self._app.tray.set_state(AppState.ERROR, f"Backend failed: {exc}")
                load_outcome = False
        # Publish asr_backend_ready ONLY on success. On failure, publish
        # asr_backend_load_failed so the renderer can show an error
        # instead of silently dismissing the loading spinner. Published
        # AFTER the lock is released so subscribers don't block on the
        # lock. ``load_outcome is None`` covers the no-op and deferred
        # early-return paths (no event published).
        if load_outcome is True:
            self._publish_backend_ready_event(backend, self._app.config.model_size)
        elif load_outcome is False:
            self._publish_backend_load_failed_event(
                backend,
                self._app.config.model_size,
                failure_reason="load_active returned falsy or raised",
            )

    def _publish_backend_ready_event(self, backend: str, model_size: str) -> None:
        """Publish an ``asr_backend_ready`` event on the event_bus.

        Mirrors the ``asr_backend_disabled`` event pattern in
        ``asr_registry.py``. The event signals to the renderer (and any
        in-process subscribers) that a backend load has finished — either
        the user changed models via Settings, or the active backend was
        switched. The renderer uses this to dismiss the "loading"
        spinner shown after the IPC ``set_config`` ack.

        The event is published AFTER ``_model_change_lock`` is released
        so subscribers don't block on the lock. Best-effort — a publish
        failure is logged at DEBUG and swallowed (the load itself
        already succeeded; the event is purely informational).
        """
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "asr_backend_ready",
                    "data": {
                        "backend": backend,
                        "model_size": model_size,
                    },
                }
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to publish asr_backend_ready event",
                exc_info=True,
            )

    def _publish_backend_load_failed_event(
        self,
        backend: str,
        model_size: str,
        *,
        failure_reason: str,
    ) -> None:
        """Publish an ``asr_backend_load_failed`` event on the event_bus.

        Previously ``_change_model_blocking`` and
        ``_set_active_backend_blocking`` published ``asr_backend_ready``
        UNCONDITIONALLY on completion — even when ``load_active`` had
        returned falsy or raised. The renderer's loading spinner was
        dismissed on ``asr_backend_ready``, so a failed load left the
        user with no visual indication that the spinner had cleared
        because the load FAILED (not because it succeeded). The
        renderer's tray icon transitioned to ``AppState.ERROR`` (set by
        the load-phase error path), but the renderer-side spinner
        dismissal was incorrect.

        This companion event is published ONLY on failure. The renderer
        should subscribe to it (alongside ``asr_backend_ready``) and
        show an error message in addition to dismissing the spinner.

        ``failure_reason`` is a short, human-readable string explaining
        why the load failed (e.g. ``"load_active returned falsy"`` or
        ``"load_active raised: <ExcType>"``). It's surfaced in the
        event payload so the renderer can show it verbatim or map it to
        a user-facing message.

        NOTE: the renderer change (subscribing to
        ``asr_backend_load_failed`` and showing an error) is OUT OF
        SCOPE for this fix — it lives in the Electron/renderer codebase.
        This method only emits the event; the renderer's matching
        listener must be added separately.

        Best-effort — a publish failure is logged at DEBUG and swallowed
        (the load already failed; the event is purely informational).
        """
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "asr_backend_load_failed",
                    "data": {
                        "backend": backend,
                        "model_size": model_size,
                        "failure_reason": failure_reason,
                    },
                }
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to publish asr_backend_load_failed event",
                exc_info=True,
            )

    # apply a deferred model change captured during an active
    # recording. Called from _start_dictation before the new recording
    # begins so the in-memory engine matches the saved config.
    def apply_pending_model_change(self) -> bool:
        """If a model change was deferred during a previous recording,
        re-run change_model now (without the early return) so the new
        backend is actually loaded. Returns True if a change was applied.

        also applies a deferred backend-only change
        (``_pending_backend_change``) captured by
        :meth:`set_active_backend` while the user was recording/busy.
        The two pending fields are independent — a single recording
        could have triggered BOTH a ``change_model`` request (which
        sets ``_pending_model_change``) AND a ``set_active_backend``
        request (which sets ``_pending_backend_change``). We apply the
        model change FIRST (because ``change_model`` re-evaluates the
        backend from the new ``model_size`` — e.g. model_size="parakeet"
        implies backend="parakeet") and then the backend change
        SECOND (so an explicit ``set_active_backend("whisper")``
        overrides the model-change-implied backend).

        Both pending fields are cleared BEFORE either apply runs so a
        crash mid-apply doesn't leave a stale request that re-fires on
        the next recording. The apply methods (``_change_model_blocking``
        / ``_set_active_backend_blocking``) are idempotent under the
        not-currently-busy precondition (which holds because
        ``recording_controller.start`` calls us before recording
        starts), so a re-invocation from a subsequent recording would
        be a no-op (the config already reflects the requested state).
        """
        # ``getattr`` defensive: some test fixtures (and the legacy
        # ``test_recording_and_audio.py::TestPendingModelChange``)
        # construct ModelManager via ``__new__`` and only set
        # ``_pending_model_change`` — they don't know about the new
        # ``_pending_backend_change`` field added in  Reading
        # via ``getattr(..., None)`` preserves their behaviour (no
        # AttributeError) instead of forcing every test fixture to
        # set the new field.
        pending = self._pending_model_change
        pending_backend = getattr(self, "_pending_backend_change", None)
        if pending is None and pending_backend is None:
            return False
        # Clear both BEFORE applying to avoid re-entry on a crash.
        # See method docstring for the safety argument.
        self._pending_model_change = None
        self._pending_backend_change = None
        applied = False
        if pending is not None:
            log.info("[MODEL] Applying deferred model change to %s", pending)
            # use the BLOCKING variant (not the non-blocking
            # ``change_model``) because the caller —
            # ``recording_controller._start_dictation`` — needs the model
            # fully loaded BEFORE the recorder starts capturing audio. The
            # non-blocking variant would return immediately and the
            # recorder would start with the OLD (unloaded) engine.
            self._change_model_blocking(pending)
            applied = True
        if pending_backend is not None:
            log.info("[MODEL] Applying deferred backend change to %s", pending_backend)
            #  + : use the BLOCKING variant for the same
            # reason as the model-change branch above. The
            # preconditions (not recording + not busy) hold because
            # ``recording_controller.start`` calls us before recording
            # starts, so the  deferral branch in
            # ``set_active_backend`` is skipped.
            self._set_active_backend_blocking(pending_backend)
            applied = True
        return applied

    # ── Lazy init for _start_dictation ─────────────────────────────────

    def ensure_active_engine_loaded(self) -> Any | None:
        """Ensure the active backend's engine exists; lazy-init if missing.

        Called from VoiceTyperApp._start_dictation to handle the case
        where the user changed the backend via Electron UI after startup
        (so the engine wasn't created during __init__).

        previously two threads could both pass the
        ``registry.get(backend) is None`` check and each call
        ``_ensure_engine``, creating two engine instances (memory leak
        + double GPU allocation). We guard with a dedicated lock so
        the second caller sees the engine created by the first.

        this is also the reload-after-idle-unload path. When the
        idle-unload timer has fired (``engine.is_loaded == False``),
        this method reloads the SAME backend via
        ``_registry.load_active(progress_callback=...)`` (not Whisper
        fallback). The tray transitions through LOADING "Loading
        model..." → IDLE "Ready -- ..." so the user sees the reload
        latency. After reload, ``touch_model`` re-arms the idle-unload
        timer so the cycle can repeat on the next idle period.

        if the active backend is currently busy (inside
        ``transcribe_with_fallback`` on another thread — e.g. a stuck
        ctranslate2 call that the watchdog hasn't force-recovered yet),
        REJECT this request and mark a pending dictation so the user's
        F2 press is honoured after the watchdog recovers. Returning
        None here causes ``recording_controller.start`` to fall through
        to the ``fallback_to_whisper`` path, which is the safest
        fallback — it loads a separate Whisper backend rather than
        piling up on the stuck backend's ctranslate2 internal lock.

        Returns the active transcriber on success, or None if no engine
        could be created. The caller is responsible for checking
        ``is_loaded`` and calling fallback_to_whisper() if needed.
        """
        # busy-flag rejection. The transcribe thread sets the
        # flag via ``registry.busy_context`` / ``transcribe_with_fallback``
        # (or, when the pipeline adopts the wrapper, automatically); the
        # IPC / hotkey thread reads it here. The check is best-effort —
        # the flag may have been cleared between the read and the
        # subsequent ``_lazy_init_lock`` acquisition — but it
        # short-circuits the common case where the previous
        # transcription is stuck and the user has pressed F2 again. The
        # ``_pending_dictation`` flag ensures the user's F2 press isn't
        # lost: the next ``recording_controller.start`` (after the
        # watchdog recovers) will re-enter this method and the busy
        # check will pass.
        #
        # Strict ``is True`` check (not just truthy): test fixtures that
        # replace the registry with a ``MagicMock`` get a truthy
        # MagicMock from ``is_busy()`` by default — a truthy check
        # would incorrectly reject every dictation in those tests.
        # The real ``AsrBackendRegistry.is_busy`` returns a real
        # ``bool``, so the strict check is safe in production.
        try:
            active_name = self._app.config.asr_backend
            if self._registry.is_busy(active_name) is True:
                log.warning(
                    "[MODEL] Active backend %s is busy (stuck transcription?) — "
                    "rejecting ensure_active_engine_loaded and queuing the "
                    "dictation. The watchdog will force-recover and clear "
                    "the busy flag via force_unload_active().",
                    active_name,
                )
                # Queue the dictation so the user's F2 press is honoured
                # after the watchdog recovers (mirrors the
                # ``_pending_dictation`` semantics in ``load_background``'s
                # finally block).
                self._pending_dictation = True
                return None
        except Exception:
            log.debug(
                "[MODEL] busy-check in ensure_active_engine_loaded failed (non-fatal)",
                exc_info=True,
            )
        # cancel any pending idle-unload timer — the user is
        # actively dictating so the model must NOT be unloaded mid-
        # dictation. This is the canonical "cancel on activity" path.
        self.cancel_idle_unload_timer()
        # race-safe lazy init. The ``backend = config.asr_backend`` read
        # MUST happen INSIDE ``_lazy_init_lock`` (not before it) so a
        # concurrent ``_change_model_blocking`` — which does NOT take
        # ``_lazy_init_lock`` — cannot rewrite ``config.asr_backend``
        # between our read and the lock acquisition (producing a phantom
        # VRAM engine for the stale backend name). The check inside
        # ``_ensure_engine`` is also guarded, but we need to guard the
        # whole check-then-init sequence so two threads don't both create
        # the engine. ``_lazy_init_lock`` is created in __init__
        # (LAZY-INIT-LOCK-FIX).
        with self._lazy_init_lock:
            backend = self._app.config.asr_backend
            engine = self._registry.get(backend)
            if engine is None:
                self._ensure_engine(backend)
                engine = self._registry.get(backend)
                # re-validate ``config.asr_backend`` after
                # ``_ensure_engine``: a concurrent ``_change_model_blocking``
                # may have rewritten it while we constructed the (now
                # phantom) engine for the stale backend name. Re-route to
                # the CURRENT backend so the caller never transcribes
                # against an abandoned backend.
                current_backend = self._app.config.asr_backend
                if current_backend != backend:
                    log.info(
                        "[MODEL] config.asr_backend changed during engine "
                        "init (%s -> %s); re-routing to current backend",
                        backend,
                        current_backend,
                    )
                    backend = current_backend
                    engine = self._registry.get(backend)
                    if engine is None:
                        self._ensure_engine(backend)
                        engine = self._registry.get(backend)
            # reload-after-idle-unload. If the engine exists but
            # has been unloaded by the idle-unload timer (is_loaded=False),
            # reload it via load_active so the SAME backend is restored
            # (not silently switched to Whisper fallback). The tray
            # transitions through LOADING "Loading model..." then IDLE
            # "Ready -- ..." so the user sees the reload latency.
            if engine is not None and hasattr(engine, "is_loaded") and not engine.is_loaded:
                self._app.tray.set_state(AppState.LOADING, "Loading model...")

                def on_progress(msg: str) -> None:
                    self._app.tray.set_state(AppState.LOADING, msg)

                # Set the synchronous-load flag so the last-resort
                # subscriber (fired by a concurrent 15s get_status probe)
                # does NOT tell the user to download a model that is
                # literally loading on this thread.
                self._sync_load_in_progress = True
                try:
                    self._registry.load_active(progress_callback=on_progress)
                    # Successful reload → backend healthy; clear any
                    # deliberate-unload flag (set by _do_idle_unload) so
                    # a FUTURE genuine failure re-notifies.
                    self._clear_deliberately_unloaded(backend)
                except Exception:
                    log.warning(
                        "[MODEL] reload after idle-unload failed (non-fatal)",
                        exc_info=True,
                    )
                finally:
                    self._sync_load_in_progress = False
                # Re-arm the idle-unload timer for the next idle period
                # (touch_model only arms when backend == active_name).
                try:
                    self.touch_model(self._registry.active_name)
                except Exception:
                    log.debug("[MODEL] touch_model after reload failed", exc_info=True)
                # Surface the active backend's device info on the tray
                # so the user sees "Ready -- <device>" (matches the
                # set_active_backend success-path tray message).
                try:
                    active = self._registry.get_active()
                    name = self._registry.active_name
                    if active is not None:
                        if name == "whisper":
                            self._app.tray.set_state(AppState.IDLE, f"Ready -- {active.device_info}")
                        else:
                            self._app.tray.set_state(AppState.IDLE, f"Ready -- {name.title()} ASR")
                except Exception:
                    log.debug("[MODEL] tray set_state after reload failed", exc_info=True)
        return self.active_transcriber()

    def _evict_lru_model(self) -> None:
        """PERF-015: Evict the least recently used model if too many are loaded.

        When more than ``_MAX_LOADED_MODELS`` models are loaded concurrently,
        unloads the least recently used one to prevent GPU OOM. Called after
        loading a new model.

        The LRU is based on ``_model_access_times`` which is updated each
        time a model is used for transcription.
        """
        with self._model_lru_lock:
            if len(self._model_access_times) <= self._MAX_LOADED_MODELS:
                return

            # Find the oldest (least recently used) backend.
            #  (pyrefly): use a lambda instead of passing
            # ``self._model_access_times.get`` directly. ``dict.get``
            # is typed as returning ``float | None`` (because callers
            # may pass a key that isn't present), but ``min(key=...)``
            # requires a function returning ``SupportsRichComparison``
            # — pyrefly rejects ``None`` as not orderable. The lambda
            # resolves this by giving an explicit ``0.0`` default that
            # can never actually be returned here (every key in
            # ``_model_access_times`` has a real ``float`` value), but
            # satisfies the type system without changing behaviour.
            oldest_backend = min(
                self._model_access_times,
                key=lambda k: self._model_access_times.get(k, 0.0),
            )
            oldest_time = self._model_access_times[oldest_backend]
            log.info(
                "[PERF] Evicting LRU model '%s' (last used %.1fs ago) — %d models loaded, max is %d",
                oldest_backend,
                __import__("time").monotonic() - oldest_time,
                len(self._model_access_times),
                self._MAX_LOADED_MODELS,
            )

            # Unload the engine via the registry so the busy-check
            # (``AsrBackendRegistry.unload`` refuses to unload a backend
            # currently inside ``transcribe_with_fallback``) is honoured.
            # Mirrors ``_do_idle_unload`` (lines ~1972-1979): if the
            # backend is busy, log + skip eviction rather than tearing
            # down a backend mid-transcription (which would crash the
            # C-level ctranslate2 / torch call with a use-after-free).
            try:
                self._registry.unload(oldest_backend)
            except RuntimeError as busy_exc:
                log.warning(
                    "[PERF] Skipping LRU eviction of busy backend '%s': %s",
                    oldest_backend,
                    busy_exc,
                )
                return
            except Exception as exc:
                log.warning(
                    "[PERF] registry.unload('%s') failed (non-fatal): %s",
                    oldest_backend,
                    exc,
                    exc_info=True,
                )
            # Deliberate unload — the evicted model was loaded and is
            # on disk; the last-resort tray notification must NOT tell
            # the user to download it. Marked AFTER the busy-check so a
            # skipped eviction (busy backend) does not leave a stale
            # flag for a backend that was never actually unloaded.
            self._mark_deliberately_unloaded(oldest_backend)
            # Unregister the backend so a subsequent ``_ensure_engine``
            # actually constructs a fresh one. Previously this path
            # called ``engine.unload()`` directly and left the backend
            # in the registry, so ``_ensure_engine``'s
            # "if registry.get(name) is not None: return" short-circuited
            # and a stale (unloaded) handle was returned. Mirrors
            # ``_change_model_unload_phase`` (lines ~990-996).
            try:
                self._registry.unregister(oldest_backend)
            except Exception as exc:
                log.warning(
                    "[PERF] registry.unregister('%s') failed (non-fatal): %s",
                    oldest_backend,
                    exc,
                    exc_info=True,
                )
            # Release GPU memory (CUDA caching allocator blocks).
            # Defense in depth — mirrors ``_do_idle_unload``
            # (lines ~1996-1998) and ``force_unload_active``
            # (lines ~2115-2117): without this, the freed CUDA tensors
            # stay in PyTorch's caching allocator and the VRAM is not
            # actually returned to the OS, defeating the eviction's
            # goal of preventing GPU OOM.
            try:
                from voice_typer.server.asr_utils import release_gpu_memory

                release_gpu_memory()
            except Exception:
                log.debug(
                    "[PERF] release_gpu_memory() failed (non-fatal)",
                    exc_info=True,
                )

            # Remove from tracking
            del self._model_access_times[oldest_backend]

    def touch_model(self, backend_name: str) -> None:
        """PERF-015: Update the last-access timestamp for a model backend.

        Called when a model is used for transcription so the LRU eviction
        knows which models are actively being used.

        if the touched backend is the ACTIVE backend AND
        ``model_idle_unload_minutes > 0``, (re)arm the idle-unload
        timer. Touching an inactive backend (e.g. via ``touch_model``
        on a non-active name during a load path) does NOT arm the
        timer — the timer is only for the active backend.
        """
        import time

        with self._model_lru_lock:
            self._model_access_times[backend_name] = time.monotonic()
        # arm the idle-unload timer only when the touched
        # backend is the active one (the timer exists to release the
        # ACTIVE backend's VRAM after inactivity). Touching a non-active
        # backend (e.g. during a registry-level pre-warm) is harmless
        # but should not arm the timer.
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        if backend_name == active_name:
            self._schedule_idle_unload_timer()

    def touch_active_model(self) -> None:
        """PERF-015: refresh the LRU timestamp for the active backend.

        Public entry point intended to be called from
        :meth:`voice_typer.server.dictation_pipeline.DictationPipeline._transcribe`
        after every successful ``transcribe()`` so the LRU eviction
        knows the active backend is in use (and therefore should NOT be
        the one evicted on the next ``load_active`` / ``load_with_fallback``).

        Wired into ``DictationPipeline._transcribe``
        (``voice_typer/server/dictation_pipeline.py:636``) — called after
        every successful ``transcribe()`` so the LRU tracking added in
         (``touch_model`` after load) has a matching "after
        transcribe" entry point.

        Safe to call when no backend is active — ``touch_model`` is a
        no-op for unknown backend names (it just records the timestamp;
        eviction only considers names that were touched).

        also (re)arms the idle-unload timer via the
        ``touch_model`` → ``_schedule_idle_unload_timer`` path when the
        active backend is touched (i.e. after every successful
        transcribe the timer is pushed out by N minutes).
        """
        try:
            self.touch_model(self._registry.active_name)
        except Exception:
            log.warning(
                "[PERF] touch_active_model failed (non-fatal)",
                exc_info=True,
            )

    # ── : idle-unload timer ───────────────────────────────────────

    def cancel_idle_unload_timer(self) -> None:
        """cancel any pending idle-unload timer (idempotent).

        Safe to call when no timer is armed (no-op). Called from:
        - ``ensure_active_engine_loaded`` (user pressed toggle_dictation)
        - ``change_model`` / ``set_active_backend`` (model swap)
        - ``_schedule_idle_unload_timer`` (reschedule-on-touch)
        - ``app.shutdown`` paths (best-effort via the registry's
          stop_event — the daemon Timer is killed implicitly by
          process exit too).

        Defensive against test fixtures that construct
        ``ModelManager.__new__(ModelManager)`` and bypass ``__init__``
        (so ``_idle_unload_lock`` / ``_idle_unload_timer`` may not be
        set). In that case the method is a no-op (there is no timer to
        cancel — the fixture never armed one).
        """
        lock = getattr(self, "_idle_unload_lock", None)
        if lock is None:
            return
        with lock:
            timer = self._idle_unload_timer
            self._idle_unload_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                log.debug(
                    "[MODEL] timer.cancel() failed (non-fatal)",
                    exc_info=True,
                )

    def _schedule_idle_unload_timer(self) -> None:
        """arm (or re-arm) the idle-unload timer.

        Reads ``app.config.model_idle_unload_minutes``:
        - ``0`` (default) → feature disabled; cancel any existing timer
          and return (current behaviour preserved exactly).
        - ``N > 0`` → cancel any existing timer, then arm a new
          ``threading.Timer(N * 60.0, _on_idle_unload_fire)``.

        Each call cancels the previous timer so the deadline is pushed
        out to N minutes after the most recent touch (the "use it or
        lose it" pattern). The timer is a daemon thread so it never
        blocks process exit.

        Defensive against test fixtures that bypass ``__init__`` — if
        ``_idle_unload_lock`` is missing, the method is a no-op (the
        fixture never set ``model_idle_unload_minutes`` either, so the
        feature would be disabled anyway).
        """
        lock = getattr(self, "_idle_unload_lock", None)
        if lock is None:
            return
        try:
            minutes = getattr(self._app.config, "model_idle_unload_minutes", 0)
        except Exception:
            minutes = 0
        if not isinstance(minutes, int | float) or minutes <= 0:
            # Feature disabled — cancel any existing timer and return.
            self.cancel_idle_unload_timer()
            return
        # Cancel any existing timer first so the deadline is pushed out
        # to N minutes after THIS touch (not the previous one).
        self.cancel_idle_unload_timer()
        delay = float(minutes) * 60.0
        # Create the Timer with a placeholder callback, then override
        # ``timer.function`` with a closure that captures ``timer`` so
        # ``_on_idle_unload_fire`` can do the identity check (only the
        # CURRENT timer's callback actually unloads — a cancelled /
        # rescheduled timer's callback aborts).
        timer = threading.Timer(delay, lambda: None)
        timer.daemon = True
        timer.name = "TY-11-idle-unload"

        def _fire() -> None:
            self._on_idle_unload_fire(timer)

        # Override the Timer's stored function so the run() method
        # invokes our closure (which captures ``timer`` for the
        # identity check). The default ``timer.function`` is the
        # ``lambda: None`` placeholder above; replacing it post-init is
        # safe because ``Timer.run`` reads ``self.function`` at fire
        # time, not at construction.
        timer.function = _fire
        with lock:
            self._idle_unload_timer = timer
        timer.start()

    def _on_idle_unload_fire(self, timer: threading.Timer) -> None:
        """timer callback — identity-check then delegate to ``_do_idle_unload``.

        The identity check ensures only the CURRENT timer's callback
        actually unloads. If the timer was cancelled or rescheduled
        (replaced by a new Timer) before this callback ran, the check
        fails and the callback returns without unloading. This prevents
        the race where an old timer fires after a new one was scheduled
        (which would unload the model right after the user started a
        new dictation).
        """
        with self._idle_unload_lock:
            current = self._idle_unload_timer
        if current is not timer:
            log.debug("[MODEL] idle-unload timer callback aborted (timer no longer current)")
            return
        self._do_idle_unload()
        # Clear the timer reference (the callback has run; the timer is
        # dead). The next ``touch_active_model`` will arm a fresh timer.
        with self._idle_unload_lock:
            if self._idle_unload_timer is timer:
                self._idle_unload_timer = None

    def _do_idle_unload(self) -> None:
        """unload the active backend + release GPU memory.

        Called from ``_on_idle_unload_fire`` (the timer's callback) and
        directly from tests. Skips the unload if:
        - ``app._shutting_down`` is True (avoids racing with teardown).
        - the active engine is already unloaded (no double-unload).

        After unloading, calls ``release_gpu_memory()`` to release
        PyTorch's CUDA caching allocator blocks back to the OS, then
        sets the tray state to ``AppState.IDLE`` with the
        "Idle — model unloaded" message so the user sees the tray
        transition. The model is reloaded on the next
        ``toggle_dictation`` via ``ensure_active_engine_loaded``.
        """
        # Skip if shutting down — don't race with teardown.
        if getattr(self._app, "_shutting_down", False):
            log.debug("[MODEL] idle-unload skipped (app shutting down)")
            return
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        engine = self._registry.get_active()
        # Skip if no active engine (nothing to unload).
        if engine is None:
            log.debug("[MODEL] idle-unload skipped (no active engine)")
            return
        # Skip if already unloaded (no double-unload).
        if hasattr(engine, "is_loaded") and not engine.is_loaded:
            log.debug("[MODEL] idle-unload skipped (engine already unloaded)")
            return
        log.info(
            "[MODEL] idle-unload: unloading active backend '%s' after idle period",
            active_name,
        )
        # Deliberate unload — the model IS on disk, so the last-resort
        # tray notification ("open the Models page and download") must
        # NOT fire for it. Record BEFORE the unload so a get_active
        # last-resort fall-through during the unload is also suppressed.
        self._mark_deliberately_unloaded(active_name)
        # Unload via the registry (which calls engine.unload() on the
        # registered backend).
        try:
            self._registry.unload(active_name)
        except Exception:
            log.warning(
                "[MODEL] registry.unload(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
        # Defense in depth: also call engine.unload() directly in case
        # registry.unload only clears the registration without calling
        # the engine's unload (the contract varies by registry impl).
        try:
            if hasattr(engine, "unload"):
                engine.unload()
        except Exception:
            log.debug(
                "[MODEL] engine.unload() failed (non-fatal)",
                exc_info=True,
            )
        # Release GPU memory (CUDA caching allocator blocks). Defense
        # in depth — parakeet_engine.unload() also calls this, but the
        # ModelManager calls it explicitly too so a registry impl that
        # doesn't propagate unload still releases VRAM.
        try:
            from voice_typer.server.asr_utils import release_gpu_memory

            release_gpu_memory()
            try:
                from voice_typer.server import vad

                vad.unload()
            except Exception:
                log.debug("[MODEL] vad.unload() failed (non-fatal)", exc_info=True)
        except Exception:
            log.debug(
                "[MODEL] release_gpu_memory() failed (non-fatal)",
                exc_info=True,
            )
        # Tray state transition: "Idle — model unloaded" (reuses
        # AppState.IDLE per the  constraint of not touching
        # tray.py / tray_types.py — no new enum value).
        try:
            self._app.tray.set_state(AppState.IDLE, "Idle — model unloaded")
        except Exception:
            log.debug(
                "[MODEL] tray.set_state failed (non-fatal)",
                exc_info=True,
            )

    # ── : force-unload the active backend (watchdog escalation) ──

    def force_unload_active(self) -> None:
        """force-unload the active backend + clear its busy flag.

        Called by the watchdog
        (:meth:`voice_typer.server.recording_controller.RecordingController._force_recover_from_stuck_transcription`)
        after the 2nd force-recovery to actually tear down the stuck
        ctranslate2 model's GPU resources. The watchdog itself can't
        interrupt the C-level ctranslate2 call, but unloading the
        backend object releases the Python-side reference and (for
        backends whose ``unload()`` frees CUDA tensors) the VRAM.

        Contract:

        * **Idempotent.** Safe to call multiple times — each call is a
          no-op if the backend is already unloaded.

        * **Best-effort.** Catches every exception, logs a warning, and
          NEVER raises. The watchdog calls this from its own
          force-recover path; a raise here would mask the recovery
          state reset (``_busy_event.set()``, tray to IDLE) the
          watchdog has already performed.

        * **Three-layer unload.**

          1. ``registry.unload(active_name)`` — clears the registry's
             ``_backends[name]`` slot (defence-in-depth so a
             subsequent ``get_active`` doesn't return a half-torn-down
             backend).
          2. ``backend.unload()`` directly on the backend object —
             bypasses the registry's name lookup in case a concurrent
             ``change_model`` / ``set_active_backend`` swapped the
             registered backend out from under us between step 1 and
             step 2.
          3. ``release_gpu_memory()`` — releases PyTorch's CUDA caching
             allocator blocks back to the OS (matches the
             idle-unload path's three-layer pattern).

        * **Clears the busy flag.** Calls
          :meth:`AsrBackendRegistry.force_clear_busy` so the next
          :meth:`ensure_active_engine_loaded` call isn't rejected by
          the  busy-check. Without this, the busy flag would
          remain set forever (the stuck transcription never returned
          to clear it) and every subsequent dictation would be
          rejected + queued.

        * **Does NOT touch ``config.asr_backend``.** The next
          :meth:`ensure_active_engine_loaded` call (from
          ``recording_controller.start``) will re-create + re-load
          the same backend via ``_ensure_engine`` + ``load_active``.
          The watchdog's contract is "tear down the stuck model so the
          next dictation can load a fresh one", not "switch to a
          different backend".

        * **Does NOT call ``tray.set_state``.** The watchdog has
          already set the tray to ``AppState.IDLE`` with the
          "recovered" message — we don't want to overwrite that with
          the  "Idle — model unloaded" message (which would
          confuse the user, since the recovery message is more
          specific).
        """
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        log.warning(
            "[MODEL] force_unload_active: tearing down active backend %r "
            "(watchdog escalation after stuck transcription)",
            active_name,
        )
        # Deliberate unload — the model IS on disk (it was loaded and
        # got stuck); the last-resort tray notification must NOT tell
        # the user to download it.
        self._mark_deliberately_unloaded(active_name)
        # Layer 1: registry.unload (clears _backends[name] slot).
        try:
            self._registry.unload(active_name)
        except Exception:
            log.warning(
                "[MODEL] registry.unload(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
        # Layer 2: backend.unload() directly (bypasses the registry in
        # case a concurrent change_model / set_active_backend swapped
        # the registered backend out from under us).
        try:
            backend = self._registry.get(active_name)
            if backend is not None and hasattr(backend, "unload"):
                backend.unload()
        except Exception:
            log.warning(
                "[MODEL] backend.unload() failed (non-fatal)",
                exc_info=True,
            )
        # Layer 3: release GPU memory (CUDA caching allocator blocks).
        try:
            from voice_typer.server.asr_utils import release_gpu_memory

            release_gpu_memory()
        except Exception:
            log.debug(
                "[MODEL] release_gpu_memory() failed (non-fatal)",
                exc_info=True,
            )
        # Clear the busy flag so the next ensure_active_engine_loaded
        # isn't rejected by the  busy-check. Without this, the
        # busy flag would remain set forever (the stuck transcription
        # never returned to clear it) and every subsequent dictation
        # would be rejected + queued indefinitely.
        try:
            self._registry.force_clear_busy(active_name)
        except Exception:
            log.debug(
                "[MODEL] force_clear_busy(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
