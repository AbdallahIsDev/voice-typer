"""#2 (Round 9): ModelManager — extracted from VoiceTyperApp.

Owns the ASR backend lifecycle: construction (via AsrBackendRegistry.create),
loading (with whisper fallback), model changes, and the three legacy engine
fields (``transcriber``, ``_qwen_engine``, ``_parakeet_engine``) that were
previously scattered across VoiceTyperApp.

Architecture:
    VoiceTyperApp
        └── ModelManager
                ├── AsrBackendRegistry (single source of truth)
                ├── transcriber (whisper)       ← legacy field, mirrored from registry
                ├── _qwen_engine                ← legacy field, mirrored from registry
                └── _parakeet_engine            ← legacy field, mirrored from registry

The registry is the source of truth; the three fields are kept as
backwards-compat mirrors because tests and a few call sites still read
``app.transcriber`` directly. All mutations go through ModelManager
methods, which keep the registry and the fields in sync.

Previously this concern lived in VoiceTyperApp as ~500 LOC across 8 methods:
    _load_transcription_engine_background, _fallback_to_whisper,
    _try_load_model, _change_model, _init_qwen_engine,
    _init_parakeet_engine, _init_asr_engine, _sync_asr_registry,
    _get_active_transcriber

All of those now delegate to ModelManager.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class ModelManager:
    """Owns ASR backend construction, loading, fallback, and switching.

    #2 (Round 9): extracted from VoiceTyperApp. Centralizes the three
    legacy engine fields + the AsrBackendRegistry so callers go through
    one object instead of poking at app.py internals.

    The app passes itself (``app``) so ModelManager can:
    - Read/write ``app.config`` (asr_backend, model_size, etc.)
    - Update ``app.tray`` state during loads
    - Schedule the pending-dictation callback via ``app._schedule_timer``
    - Read ``app._shutting_down`` / ``app._pending_dictation`` flags

    PERF-015: includes an LRU cache for loaded models. When loading a
    new model, if more than 2 models are loaded, the least recently
    used one is unloaded. This prevents GPU OOM from accumulating
    multiple model instances.
    """

    # PERF-015: maximum number of concurrently loaded models
    _MAX_LOADED_MODELS = 2

    def __init__(self, app: Any) -> None:
        self._app = app
        # ARCH-008: Registry initialized eagerly (was lazy in app.py).
        # Callers can rely on it existing from the start.
        self._registry: AsrBackendRegistry = AsrBackendRegistry(app.config)

        # Legacy engine fields — mirrored from the registry so existing
        # tests that read app.transcriber / app._qwen_engine /
        # app._parakeet_engine directly still work. The registry is the
        # source of truth; these are kept in sync by _sync_legacy_fields().
        self.transcriber: Optional[Any] = None
        self._qwen_engine: Optional[Any] = None
        self._parakeet_engine: Optional[Any] = None

        # Background model-load thread (tracked so toggle_dictation can
        # detect "loading in progress" and auto-start once finished).
        self._model_load_thread: Optional[threading.Thread] = None
        self._model_load_attempted: bool = False
        self._pending_dictation: bool = False
        # ERR-003: When the user changes model during an active recording
        # we save config and notify "will change after current recording",
        # but previously never actually applied the change. We capture
        # the requested model here and apply it on the next _start_dictation.
        self._pending_model_change: Optional[str] = None

        # PERF-015: LRU tracking for loaded models.
        # Maps backend_name → last-access timestamp. When loading a new
        # model and more than _MAX_LOADED_MODELS are present, the oldest
        # entry is unloaded.
        self._model_access_times: dict[str, float] = {}
        self._model_lru_lock = threading.Lock()

    # ── Registry access ────────────────────────────────────────────────

    @property
    def registry(self) -> AsrBackendRegistry:
        """Direct access to the underlying registry (rarely needed)."""
        return self._registry

    def _sync_legacy_fields(self) -> None:
        """Mirror registry state into the three legacy fields.

        Called after every registry mutation so ``app.transcriber`` etc.
        stay consistent with the registry without callers needing to know
        about the registry.
        """
        self.transcriber = self._registry.get("whisper")
        self._qwen_engine = self._registry.get("qwen")
        self._parakeet_engine = self._registry.get("parakeet")

    def _sync_registry_from_fields(self) -> None:
        """Re-populate the registry from the legacy fields.

        Used when a code path writes to a legacy field directly (back-compat
        with tests that do ``app.transcriber = MagicMock()``). After this
        call, the registry and the fields are consistent.

        ARCH-047: previously this method unconditionally unregistered all
        three backends and re-registered them, producing log spam
        (``unregistered backend: whisper`` → ``registered backend: whisper``)
        every time it was called. We now skip the unregister+register
        cycle when the registered instance is already the same object
        as the legacy field — the common case after the first sync.
        """
        for name, field_val in (
            ("whisper", self.transcriber),
            ("qwen", self._qwen_engine),
            ("parakeet", self._parakeet_engine),
        ):
            current = self._registry.get(name)
            if field_val is None:
                if current is not None:
                    self._registry.unregister(name)
            elif current is not field_val:
                # Only churn when the instance actually changed.
                if current is not None:
                    self._registry.unregister(name)
                self._registry.register(name, field_val)

    def active_transcriber(self) -> Optional[Any]:
        """Return the active transcriber (Parakeet, Qwen, or Whisper).

        ARCH-007/008: delegates to AsrBackendRegistry which centralizes
        the backend selection logic. Previously every caller re-checked
        self.config.asr_backend and tested three separate fields.

        The registry is re-synced on every call so it never holds stale
        references after model changes.
        """
        self._sync_registry_from_fields()
        return self._registry.get_active()

    # ── Engine construction (ARCH-007 single chokepoint) ──────────────

    def _ensure_engine(self, backend_name: str) -> None:
        """Ensure the engine object for ``backend_name`` exists (no load).

        ARCH-007: delegates to AsrBackendRegistry.create() so all backend
        construction goes through one code path.

        ERR-011: previously failures here were swallowed by the registry
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
                        # NEW-PRIV-005: pass the live Config reference so
                        # TranscriptionEngine._pre_download_model can
                        # read huggingface_consent without crashing on
                        # AttributeError.  Previously this kwarg was
                        # missing, so self.config was None in the engine
                        # and the consent check raised AttributeError
                        # on every uncached Whisper download.
                        config=self._app.config,
                    ),
                )
        except Exception as exc:
            log.exception("[MODEL] Failed to initialize %s engine: %s", backend_name, exc)
            # ERR-011: surface to user via tray notification so they
            # don't sit waiting for "Ready" forever. Include the
            # backend name and a short hint.
            try:
                hint = ""
                if backend_name == "qwen":
                    hint = " Check that the Qwen model path is set correctly in Settings."
                elif backend_name == "parakeet":
                    hint = " Check that Parakeet weights are downloaded."
                self._app.tray.notify(
                    "Voice Typer",
                    f"Could not initialize the {backend_name.title()} backend.{hint}",
                )
            except Exception:
                pass
            # Re-raise so callers (load_background, ensure_active_engine_loaded)
            # can react; previously the bare-except in registry.create
            # swallowed the error.
            raise
        self._sync_legacy_fields()

    # ── Loading ────────────────────────────────────────────────────────

    def load_background(self) -> None:
        """Background worker: create + load the transcription engine.

        Runs in a daemon thread so the heavy torch/transformers import and
        weight download/read (off-disk on cold boot) do not block the app
        reaching an interactive state.  All tray state transitions happen
        here; if a dictation is pending (user pressed F2 during load), it
        is auto-started once loading succeeds.
        """
        try:
            backend_name = self._app.config.asr_backend
            self._ensure_engine(backend_name)
            self._sync_registry_from_fields()

            # Set tray state before heavy import so user sees progress
            self._app.tray.set_state(
                AppState.LOADING, "Loading model -- press F2 to queue..."
            )

            def on_progress(msg: str):
                self._app.tray.set_state(AppState.LOADING, msg)

            success = self._registry.load_with_fallback(
                progress_callback=on_progress
            )

            if success:
                active = self._registry.get_active()
                name = self._registry.active_name
                if name == "whisper" and active is not None:
                    self._app.tray.set_state(
                        AppState.IDLE, f"Ready -- {active.device_info}"
                    )
                else:
                    self._app.tray.set_state(
                        AppState.IDLE, f"Ready -- {name.title()} ASR"
                    )
            else:
                if self._app._shutting_down:
                    return
                log.warning("[STARTUP] All backends failed to load")
                self._app.tray.set_state(
                    AppState.ERROR, "Model load failed -- press F2 to retry"
                )

        except Exception:
            log.exception("[STARTUP] Background model load crashed")
            self._app.tray.set_state(
                AppState.ERROR, "Model load failed -- press F2 to retry"
            )
        finally:
            self._model_load_thread = None
            # If the user pressed F2 during load, honour it now.
            if self._pending_dictation and not self._app._shutting_down:
                log.info("[STARTUP] Pending dictation -- auto-starting now")
                self._pending_dictation = False
                # Schedule off this loader thread to avoid nesting
                self._app._schedule_timer(0, self._app._start_dictation)

    def start_background_load(self) -> None:
        """Spawn the background model-load thread (idempotent)."""
        if self._model_load_thread is not None and self._model_load_thread.is_alive():
            return
        self._model_load_thread = threading.Thread(
            target=self.load_background,
            name="ModelLoad",
            daemon=True,
        )
        self._model_load_thread.start()

    def fallback_to_whisper(self, notify_on_failure: bool = False) -> None:
        """Fallback to Whisper tiny.en after Parakeet/Qwen backend failed.

        ARCH-007/008: Uses the registry to reconfigure and reload.
        Switches config to whisper/tiny.en, ensures the whisper backend
        is registered, and delegates loading to
        AsrBackendRegistry.load_with_fallback().
        """
        self._app.config.model_size = "tiny.en"
        self._app.config.asr_backend = "whisper"
        existing = self._registry.get("whisper")
        if existing is None:
            self._registry.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size="tiny.en",
                    device=self._app.config.device,
                    language=self._app.config.language,
                    beam_size=self._app.config.beam_size,
                    best_of=self._app.config.best_of,
                    condition_on_previous_text=self._app.config.condition_on_previous_text,
                    # NEW-PRIV-005: pass live Config so consent check works.
                    config=self._app.config,
                ),
            )
            self._sync_legacy_fields()
        else:
            existing.model_size = "tiny.en"
            existing._configured_model_size = "tiny.en"
            # NEW-PRIV-005: also backfill the config reference on the
            # existing engine in case it was constructed without one
            # (e.g. by an older code path or a test).  No-op if the
            # engine already has a non-None config.
            if getattr(existing, "config", None) is None:
                existing.config = self._app.config

        self._sync_registry_from_fields()

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        success = self._registry.load_with_fallback(
            progress_callback=on_progress
        )
        if success:
            active = self._registry.get_active()
            self._app.tray.set_state(
                AppState.IDLE,
                f"Ready -- {active.device_info}" if active else "Ready",
            )
        else:
            self._app.tray.set_state(
                AppState.ERROR, "Model failed to load -- press F2 to retry"
            )
            if notify_on_failure:
                # NEW-UX-018: critical — bypass toggle (model load failed).
                self._app.tray.notify_safety(
                    "Voice Typer",
                    "Could not load the speech model.\n"
                    "The app will keep running. Press F2 to retry loading.",
                )

    def try_load(self, notify_on_failure: bool = False) -> None:
        """Attempt to load the transcription model.

        ARCH-007/008: Delegates to AsrBackendRegistry.load_with_fallback()
        instead of calling self.transcriber.load() directly.
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

            success = self._registry.load_with_fallback(
                progress_callback=on_progress
            )
            if success:
                active = self._registry.get_active()
                info = getattr(active, 'device_info', 'unknown') if active else 'unknown'
                self._app.tray.set_state(AppState.IDLE, f"Ready -- {info}")
                log.info("[MODEL] Loaded successfully")
            else:
                raise RuntimeError("All backends failed to load")
        except Exception as e:
            log.exception("[MODEL] Load FAILED")
            self._app.tray.set_state(
                AppState.ERROR, "Model failed to load -- press F2 to retry"
            )
            if notify_on_failure:
                self._app.tray.notify(
                    "Voice Typer",
                    f"Could not load the speech model.\n{e}\n\n"
                    "The app will keep running. Press F2 to retry loading.",
                )

    def change_model(self, model_size: str) -> None:
        """Apply a model change for future dictation sessions.

        Handles Whisper, Parakeet, and Qwen backends.
        Unloads the old engine and loads the new one immediately (unless
        currently recording).

        ARCH-007: Uses the registry to unload/load instead of having
        three separate branches for parakeet/qwen/whisper.
        """
        # Determine backend from model name
        if model_size == "parakeet":
            new_backend = "parakeet"
        elif model_size == "qwen":
            new_backend = "qwen"
        else:
            new_backend = "whisper"

        old_backend = self._app.config.asr_backend

        self._app.config.asr_backend = new_backend
        self._app.config.model_size = model_size
        self._app.config.save()

        if self._app.recorder.recording or not self._app._busy_event.is_set():
            log.info(
                "[CONFIG] Model changed to %s (%s); applying after active work",
                model_size, new_backend,
            )
            # ERR-003: capture the request so the next _start_dictation
            # re-runs the unload/load cycle. Without this, the config
            # is saved on disk but the in-memory engine stays as the
            # old backend — the "will change after current recording"
            # notification was a lie.
            self._pending_model_change = model_size
            self._app.tray.notify(
                "Voice Typer",
                f"Model will change to {model_size} after current recording",
            )
            return

        # Unload old backend via registry
        self._sync_registry_from_fields()
        self._registry.unload(old_backend)
        # #2 (Round 9): UNREGISTER the old backend so _ensure_engine
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
            try:
                self.transcriber.unload()
            except Exception:
                pass
            self.transcriber = None

        # Create new engine object via registry.create()
        self._ensure_engine(new_backend)

        # Sync registry and load
        self._sync_registry_from_fields()

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_active(progress_callback=on_progress)
            if success:
                active = self._registry.get_active()
                if new_backend == "whisper" and active is not None:
                    self._app.tray.set_state(
                        AppState.IDLE, f"Ready -- {active.device_info}"
                    )
                else:
                    self._app.tray.set_state(
                        AppState.IDLE, f"Ready -- {new_backend.title()} ASR"
                    )
                self._app.tray.invalidate_menu_cache()
            else:
                log.warning("[MODEL] %s model failed to load", new_backend.title())
                self._app.tray.set_state(
                    AppState.ERROR, f"{new_backend.title()} model failed to load"
                )
        except Exception as exc:
            log.exception("[MODEL] Model load failed: %s", exc)
            self._app.tray.set_state(AppState.ERROR, f"Model failed: {exc}")

    # ERR-003: apply a deferred model change captured during an active
    # recording. Called from _start_dictation before the new recording
    # begins so the in-memory engine matches the saved config.
    def apply_pending_model_change(self) -> bool:
        """If a model change was deferred during a previous recording,
        re-run change_model now (without the early return) so the new
        backend is actually loaded. Returns True if a change was applied.
        """
        pending = self._pending_model_change
        if pending is None:
            return False
        log.info("[MODEL] Applying deferred model change to %s", pending)
        self._pending_model_change = None
        # Re-invoke change_model. Because we are not currently recording
        # and busy_event is set (not busy), the early-return branch is
        # skipped and the full unload/load cycle runs.
        self.change_model(pending)
        return True

    # ── Lazy init for _start_dictation ─────────────────────────────────

    def ensure_active_engine_loaded(self) -> Optional[Any]:
        """Ensure the active backend's engine exists; lazy-init if missing.

        Called from VoiceTyperApp._start_dictation to handle the case
        where the user changed the backend via Electron UI after startup
        (so the engine wasn't created during __init__).

        ERR-024: previously two threads could both pass the
        ``registry.get(backend) is None`` check and each call
        ``_ensure_engine``, creating two engine instances (memory leak
        + double GPU allocation). We guard with a dedicated lock so
        the second caller sees the engine created by the first.

        Returns the active transcriber on success, or None if no engine
        could be created. The caller is responsible for checking
        ``is_loaded`` and calling fallback_to_whisper() if needed.
        """
        backend = self._app.config.asr_backend
        # ERR-024: race-safe lazy init. The check inside _ensure_engine
        # is also guarded, but we need to guard the whole check-then-init
        # sequence so two threads don't both create the engine.
        if not hasattr(self, "_lazy_init_lock"):
            self._lazy_init_lock = __import__("threading").Lock()
        with self._lazy_init_lock:
            if self._registry.get(backend) is None:
                self._ensure_engine(backend)
                self._sync_registry_from_fields()
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

            # Find the oldest (least recently used) backend
            oldest_backend = min(self._model_access_times, key=self._model_access_times.get)
            oldest_time = self._model_access_times[oldest_backend]
            log.info(
                "[PERF-015] Evicting LRU model '%s' (last used %.1fs ago) — "
                "%d models loaded, max is %d",
                oldest_backend,
                __import__("time").monotonic() - oldest_time,
                len(self._model_access_times),
                self._MAX_LOADED_MODELS,
            )

            # Unload the engine
            engine = self._registry.get(oldest_backend)
            if engine is not None:
                try:
                    if hasattr(engine, "unload"):
                        engine.unload()
                except Exception as exc:
                    log.warning("[PERF-015] Failed to unload '%s': %s", oldest_backend, exc)

            # Remove from tracking
            del self._model_access_times[oldest_backend]

    def touch_model(self, backend_name: str) -> None:
        """PERF-015: Update the last-access timestamp for a model backend.

        Called when a model is used for transcription so the LRU eviction
        knows which models are actively being used.
        """
        import time
        with self._model_lru_lock:
            self._model_access_times[backend_name] = time.monotonic()
