"""#2 ModelManager — extracted from VoiceTyperApp.

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
backwards-compat mirrors. (ARCH-REFAC-003: the @property delegates
that used to live on VoiceTyperApp and mirror these fields have
been removed — callers now read ``app.models.transcriber`` /
``app.models._qwen_engine`` / ``app.models._parakeet_engine``
directly, and call ``app.models._sync_registry_from_fields()`` after
mutating them so the registry stays consistent.) All mutations go
through ModelManager methods, which keep the registry and the fields
in sync.

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

from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.branding import APP_NAME
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
      (ARCH-REFAC-003: ``_pending_dictation`` now lives on ModelManager
      directly — accessed via ``app.models._pending_dictation``.)

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

        # Legacy engine fields — mirrored from the registry so callers
        # that read app.models.transcriber / app.models._qwen_engine /
        # app.models._parakeet_engine directly still work. The registry
        # is the source of truth; these are kept in sync by
        # _sync_legacy_fields(). (ARCH-REFAC-003: the @property
        # delegates that used to live on VoiceTyperApp — and auto-sync
        # the registry on assignment — have been removed; callers must
        # now call ``app.models._sync_registry_from_fields()`` after
        # writing to these fields.)
        self.transcriber: Any | None = None
        self._qwen_engine: Any | None = None
        self._parakeet_engine: Any | None = None

        # Background model-load thread (tracked so toggle_dictation can
        # detect "loading in progress" and auto-start once finished).
        self._model_load_thread: threading.Thread | None = None
        self._model_load_attempted: bool = False
        self._pending_dictation: bool = False
        # ERR-003: When the user changes model during an active recording
        # we save config and notify "will change after current recording",
        # but previously never actually applied the change. We capture
        # the requested model here and apply it on the next _start_dictation.
        self._pending_model_change: str | None = None

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

        # HIGH-20 / MODEL-2: reentrant lock guarding the entire body of
        # ``change_model`` so two concurrent calls cannot both unload +
        # re-register + reload the same backend (which previously left
        # the registry in an inconsistent state with two engines
        # constructed for the same name).  Reentrant so ``apply_pending_model_change``
        # can call ``change_model`` while already holding the lock if a
        # caller further up the stack acquired it.
        self._model_change_lock = threading.RLock()

    # ── Registry access ────────────────────────────────────────────────

    @property
    def registry(self) -> AsrBackendRegistry:
        """Direct access to the underlying registry (rarely needed)."""
        return self._registry

    def _sync_legacy_fields(self) -> None:
        """Mirror registry state into the three legacy fields.

        Called after every registry mutation so ``app.models.transcriber``
        etc. stay consistent with the registry without callers needing to
        know about the registry.
        """
        self.transcriber = self._registry.get("whisper")
        self._qwen_engine = self._registry.get("qwen")
        self._parakeet_engine = self._registry.get("parakeet")

    def _sync_registry_from_fields(self) -> None:
        """Re-populate the registry from the legacy fields.

        Used when a code path writes to a legacy field directly (back-compat
        with tests that do ``app.models.transcriber = MagicMock()``).
        (ARCH-REFAC-003: this method used to be auto-invoked by the
        @property delegates on VoiceTyperApp — it must now be called
        explicitly after writing to a legacy field.) After this call,
        the registry and the fields are consistent.

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

    def active_transcriber(self) -> Any | None:
        """Return the active transcriber (Parakeet, Qwen, or Whisper).

        ARCH-007/008: delegates to AsrBackendRegistry which centralizes
        the backend selection logic. Previously every caller re-checked
        self.config.asr_backend and tested three separate fields.

        CR-78: previously this method called ``_sync_registry_from_fields()``
        on every read — which mutates the registry by re-importing state
        from the three legacy engine fields (``self.transcriber`` /
        ``self._qwen_engine`` / ``self._parakeet_engine``) — WITHOUT
        holding ``_model_change_lock``.  A concurrent ``change_model``
        call sets e.g. ``self._parakeet_engine = None`` then later
        unregisters/registers in the registry; if ``active_transcriber``
        interleaved between those two writes, the registry would be
        re-synced from a half-mutated field set (one backend already
        cleared, the others not), and the next ``get_active()`` would
        return a stale or None reference.

        The registry is the source of truth (see file-level docstring):
        all production mutations go through ``ModelManager`` methods
        which keep the registry and the fields in sync via the explicit
        ``_sync_legacy_fields`` / ``_sync_registry_from_fields`` calls
        inside ``change_model`` (now guarded by both
        ``_config_mutation_lock`` and ``_model_change_lock`` per CR-77).
        Reading via ``self._registry.get_active()`` directly is safe:
        ``AsrBackendRegistry`` is internally synchronized (its own
        ``_lock``), and any concurrent ``change_model`` either has not
        yet mutated the registry (so we read the old active backend,
        which is still valid) or has already finished (so we read the
        new one).  Test code that assigns to ``app.models.transcriber``
        must call ``app.models._sync_registry_from_fields()`` explicitly
        — that contract is already documented at the top of this file
        and asserted by ``tests/test_app.py``.
        """
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
                    APP_NAME,
                    f"Could not initialize the {backend_name.title()} backend.{hint}",
                )
            except Exception:
                # CR-90: previously a bare ``except Exception: pass``.
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
        # CR-18: bail out early if shutdown was signalled while this
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
        try:
            backend_name = self._app.config.asr_backend
            self._ensure_engine(backend_name)
            self._sync_registry_from_fields()

            # Set tray state before heavy import so user sees progress
            self._app.tray.set_state(AppState.LOADING, "Loading model -- press F2 to queue...")

            def on_progress(msg: str):
                self._app.tray.set_state(AppState.LOADING, msg)

            success = self._registry.load_with_fallback(progress_callback=on_progress)

            if success:
                # HIGH-19 / MODEL-1: PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict the
                # LRU model if more than _MAX_LOADED_MODELS are now loaded.
                # Guarded so a tracking failure doesn't break the load.
                try:
                    self.touch_model(self._registry.active_name)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF-015] LRU tracking failed (non-fatal)",
                        exc_info=True,
                    )
                active = self._registry.get_active()
                name = self._registry.active_name
                if name == "whisper" and active is not None:
                    self._app.tray.set_state(AppState.IDLE, f"Ready -- {active.device_info}")
                else:
                    self._app.tray.set_state(AppState.IDLE, f"Ready -- {name.title()} ASR")
            else:
                if self._app._shutting_down:
                    return
                # PVT-G5-042 (session-5): list which backends were attempted
                # so the user (and support) can see exactly what failed,
                # plus a remediation hint. ``available_backends`` returns
                # the registered backend names; ``active_name`` is the one
                # that was selected as primary.
                # WR-14: pyrefly not-callable — ``available_backends`` is a
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
                _attempted = ", ".join(_backends) or "(none registered)"
                _primary = getattr(self._app.config, "asr_backend", "unknown")
                log.warning(
                    "[STARTUP] All backends failed to load "
                    "(primary=%s, attempted=[%s]). "
                    "Recovery: press F2 to retry, or change the backend "
                    "in Settings → Models.",
                    _primary,
                    _attempted,
                )
                self._app.tray.set_state(AppState.ERROR, "Model load failed -- press F2 to retry")

        except Exception:
            log.exception("[STARTUP] Background model load crashed")
            self._app.tray.set_state(AppState.ERROR, "Model load failed -- press F2 to retry")
        finally:
            self._model_load_thread = None
            # If the user pressed F2 during load, honour it now.
            if self._pending_dictation and not self._app._shutting_down:
                log.info("[STARTUP] Pending dictation -- auto-starting now")
                self._pending_dictation = False
                # Schedule off this loader thread to avoid nesting
                self._app._schedule_timer(0, self._app._start_dictation)

    def start_background_load(self) -> None:
        """Spawn the background model-load thread (idempotent).

        CR-18: register the thread with ``app._thread_registry`` so
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
        self._model_load_thread = threading.Thread(
            target=self.load_background,
            name="ModelLoad",
            daemon=True,
        )
        self._model_load_thread.start()
        # CR-18: track the loader centrally so shutdown_all() can
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
        """Fallback to Whisper tiny.en after Parakeet/Qwen backend failed.

        ARCH-007/008: Uses the registry to reconfigure and reload.
        Switches config to whisper/tiny.en, ensures the whisper backend
        is registered, and delegates loading to
        AsrBackendRegistry.load_with_fallback().
        """
        self._app.config.model_size = "tiny.en"
        self._app.config.asr_backend = "whisper"
        # HIGH-21 / MODEL-3: persist the fallback so the next boot
        # doesn't re-try the failed backend and repeat the failure
        # loop.  Previously the config mutation was in-memory only —
        # if the app crashed after fallback, the next boot read the
        # original (failed) backend from disk and re-entered the
        # failure loop on every boot.  Mirrors the persist pattern in
        # ``change_model`` (line 557).
        try:
            self._app.config.save()
        except Exception:
            # PVT-G5-076 (session-5): previously missing the ``[MODEL]``
            # topic prefix used by every other log call in this module.
            # Adding it keeps the log topic-consistent so log filters /
            # greps work.
            log.warning("[MODEL] failed to persist fallback config", exc_info=True)
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

        success = self._registry.load_with_fallback(progress_callback=on_progress)
        if success:
            # HIGH-19 / MODEL-1: PERF-015 LRU eviction — touch the
            # freshly-loaded backend so it's tracked, then evict the
            # LRU model if more than _MAX_LOADED_MODELS are now loaded.
            # Guarded so a tracking failure doesn't break the load.
            try:
                self.touch_model(self._registry.active_name)
                self._evict_lru_model()
            except Exception:
                log.warning(
                    "[PERF-015] LRU tracking failed (non-fatal)",
                    exc_info=True,
                )
            active = self._registry.get_active()
            self._app.tray.set_state(
                AppState.IDLE,
                f"Ready -- {active.device_info}" if active else "Ready",
            )
        else:
            self._app.tray.set_state(AppState.ERROR, "Model failed to load -- press F2 to retry")
            if notify_on_failure:
                # NEW-UX-018: critical — bypass toggle (model load failed).
                self._app.tray.notify_safety(
                    APP_NAME,
                    "Could not load the speech model.\nThe app will keep running. Press F2 to retry loading.",
                )

    def try_load(self, notify_on_failure: bool = False) -> None:
        """Attempt to load the transcription model.

        ARCH-007/008: Delegates to AsrBackendRegistry.load_with_fallback()
        instead of calling self.transcriber.load() directly.

        ADR-0009 Issue 4: waits for the prewarm process to finish before
        loading the model. This prevents the app and prewarm from
        fighting over disk I/O when the user logs in quickly (before
        prewarm completes). If prewarm is running, waits up to 60s; if
        prewarm already finished (sentinel exists) or never ran, loads
        immediately. The wait is best-effort — if it times out, the
        model loads anyway (cold, ~50s) rather than blocking forever.

        Task 5: when wait_for_prewarm() times out (prewarm still running
        after 60s), we MANDATORILY spawn a fresh background prewarm
        subprocess with --force. The current boot's prewarm was preempted
        by the app's disk I/O and may not have finished warming; without
        a re-spawn, the NEXT app launch would also hit a cold cache. The
        background prewarm runs detached so it doesn't delay the current
        model load — it's for next time.
        """
        self._model_load_attempted = True
        try:
            # ADR-0009 Issue 4: wait for prewarm to finish before loading
            # the model. This prevents the app and prewarm from fighting
            # over disk I/O when the user logs in quickly (before prewarm
            # completes). Safe no-op if prewarm isn't running.
            try:
                from voice_typer.server.prewarm import (
                    _already_warmed,
                    is_prewarm_running,
                    spawn_background_prewarm,
                    wait_for_prewarm,
                )

                # PREWARM-FIX: detect the case where the OS scheduled task
                # never fired at all (e.g. a misconfigured/interactive-only
                # task). If prewarm has its own process running we'll wait
                # for it; if it never started AND hasn't already warmed
                # this boot, we must spawn our own so the user isn't left
                # on a permanently cold cache.
                prewarm_expected = bool(getattr(self._app.config, "fast_startup", True))
                prewarm_was_running = is_prewarm_running()

                prewarm_finished = wait_for_prewarm(timeout_s=60.0)
                # Task 5: if prewarm timed out (still running after 60s),
                # the app's model load preempted it. Spawn a fresh
                # background prewarm with --force so the cache is warm
                # for the NEXT app launch. This is mandatory, not
                # optional — without it, every subsequent launch in this
                # boot session hits a cold cache.
                if not prewarm_finished:
                    log.info("[MODEL] prewarm timed out — spawning background prewarm for next launch")
                    try:
                        # PW-2: pass trigger="manual" so the prewarm log
                        # records that this background re-spawn was
                        # triggered by the app (prewarm timed out).
                        spawn_background_prewarm(force=True, trigger="manual")
                    except Exception as bg_exc:
                        # Defensive: never let the background spawn
                        # failure block model loading. It's an
                        # optimization for next time, not a correctness
                        # requirement.
                        log.debug(
                            "[MODEL] spawn_background_prewarm raised (non-fatal): %s",
                            bg_exc,
                        )
                # PREWARM-FIX: the scheduled task didn't run this boot and
                # prewarm never warmed the cache. Without this, the app would
                # otherwise load cold forever until a manual "Run Prewarm
                # Now". Spawn a detached prewarm so the NEXT launch is
                # warm. Gated on: prewarm still expected (fast_startup on),
                # it wasn't running when we checked, and the boot sentinel
                # proves it hasn't already succeeded this session.
                elif prewarm_expected and not prewarm_was_running and not _already_warmed():
                    log.info(
                        "[MODEL] prewarm scheduled task did not run this boot "
                        "— spawning background prewarm for next launch"
                    )
                    try:
                        spawn_background_prewarm(force=True, trigger="manual")
                    except Exception as bg_exc:
                        log.debug(
                            "[MODEL] spawn_background_prewarm raised (non-fatal): %s",
                            bg_exc,
                        )
            except Exception as prewarm_exc:
                # Defensive: never let a prewarm-wait failure block model
                # loading. The wait is an optimization, not a correctness
                # requirement — if it fails, we load from disk as before.
                log.debug(
                    "[MODEL] wait_for_prewarm raised (non-fatal): %s",
                    prewarm_exc,
                )

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
                # HIGH-19 / MODEL-1: PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict the
                # LRU model if more than _MAX_LOADED_MODELS are now loaded.
                # Guarded so a tracking failure doesn't break the load.
                try:
                    self.touch_model(self._registry.active_name)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF-015] LRU tracking failed (non-fatal)",
                        exc_info=True,
                    )
                active = self._registry.get_active()
                info = getattr(active, "device_info", "unknown") if active else "unknown"
                self._app.tray.set_state(AppState.IDLE, f"Ready -- {info}")
                log.info("[MODEL] Loaded successfully")
            else:
                raise RuntimeError("All backends failed to load")
        except Exception as e:
            # PVT-G5-042 (session-5): include the model name and backend
            # info so the failure is actionable — the user can see which
            # backend and model size failed and retry / switch via Settings.
            _failed_backend = getattr(self._app.config, "asr_backend", "unknown")
            _failed_model = getattr(self._app.config, "model_size", "unknown")
            log.exception(
                "[MODEL] Load FAILED (backend=%s, model=%s)",
                _failed_backend,
                _failed_model,
            )
            self._app.tray.set_state(AppState.ERROR, "Model failed to load -- press F2 to retry")
            if notify_on_failure:
                self._app.tray.notify(
                    APP_NAME,
                    f"Could not load the speech model.\n{e}\n\nThe app will keep running. Press F2 to retry loading.",
                )

    def change_model(self, model_size: str) -> None:
        """Apply a model change for future dictation sessions.

        Handles Whisper, Parakeet, and Qwen backends.
        Unloads the old engine and loads the new one immediately (unless
        currently recording).

        ARCH-007: Uses the registry to unload/load instead of having
        three separate branches for parakeet/qwen/whisper.

        LOG-001: logs every model change with old/new backend and model size.

        HIGH-20 / MODEL-2: the entire body is guarded by
        ``self._model_change_lock`` so two concurrent calls cannot both
        unload + re-register + reload the same backend.  Previously the
        registry's ``_backends`` dict had no synchronization — a race
        between two ``change_model`` calls (or between ``change_model``
        and ``fallback_to_whisper``) could leave the registry with two
        engines constructed for the same name, or with the old backend
        unregistered before the new one finished loading.

        CR-77: ``change_model`` mutates ``app.config.asr_backend``
        / ``model_size`` and calls ``app.config.save()``.  That mutation
        must be atomic w.r.t. concurrent IPC handlers
        (``service.apply_config`` / ``set_config`` / onboarding) which
        all hold ``app._config_mutation_lock`` for the same
        setattr+save sequence.  Without this lock, a concurrent
        ``apply_config`` could read ``asr_backend`` mid-write (seeing
        the new value) but then ``save()`` a config dict that still
        held the OLD ``model_size`` (because the assignment to
        ``model_size`` happened between the read and the save in the
        IPC handler).  We therefore acquire ``_config_mutation_lock``
        OUTSIDE ``_model_change_lock`` — outer-most first, matching the
        lock-order contract enforced by ``tests/test_lock_order_contract.py``.

        ``_model_change_lock`` lives on ModelManager, NOT on
        VoiceTyperApp, so it is NOT one of the three app-level locks
        (``_lock`` / ``_config_mutation_lock`` / ``_pending_timers_lock``)
        governed by the no-nesting contract.  Nesting it inside
        ``_config_mutation_lock`` is safe and does not create a cycle:
        ``_model_change_lock`` is never held while ``_config_mutation_lock``
        is acquired, so there is no A→B / B→A deadlock hazard.
        """
        # CR-77: outer = _config_mutation_lock (app-level, governs config
        # setattr + save); inner = _model_change_lock (ModelManager-level,
        # guards the unload/reload cycle).  See method docstring.
        #
        # G4-H-16: _config_mutation_lock is acquired ONLY for the brief
        # setattr + save + unload/unregister/clear-field phase. The heavy
        # engine construction (_ensure_engine — may import torch) and
        # load (load_active — 5-30s on cold boot) run under
        # _model_change_lock alone so concurrent IPC set_config calls
        # aren't blocked for the duration of the model load.
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
            # config lock (per G4-H-16).
            self._change_model_load_phase(new_backend)

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
            # ERR-003: capture the request so the next _start_dictation
            # re-runs the unload/load cycle. Without this, the config
            # is saved on disk but the in-memory engine stays as the
            # old backend — the "will change after current recording"
            # notification was a lie.
            self._pending_model_change = model_size
            self._app.tray.notify(
                APP_NAME,
                f"Model will change to {model_size} after current recording",
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
        ``model_size`` kwarg. Skipping the unload (the G4-H-16
        optimization) broke ``test_model_change_uses_config_device``.
        """
        # Unload old backend via registry
        self._sync_registry_from_fields()
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

    def _change_model_load_phase(self, new_backend: str) -> None:
        """Phase 2: construct + load the new engine.

        Caller MUST hold ``_model_change_lock``. Must NOT hold
        ``_config_mutation_lock`` (per G4-H-16).
        """
        # Create new engine object via registry.create()
        self._ensure_engine(new_backend)
        # Sync registry and load
        self._sync_registry_from_fields()

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_active(progress_callback=on_progress)
            if success:
                # HIGH-19 / MODEL-1: PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict
                # the LRU model if more than _MAX_LOADED_MODELS are
                # now loaded. Guarded so a tracking failure doesn't
                # break the load.
                try:
                    self.touch_model(new_backend)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF-015] LRU tracking failed for %s (non-fatal)",
                        new_backend,
                        exc_info=True,
                    )
                active = self._registry.get_active()
                if new_backend == "whisper" and active is not None:
                    self._app.tray.set_state(AppState.IDLE, f"Ready -- {active.device_info}")
                else:
                    self._app.tray.set_state(AppState.IDLE, f"Ready -- {new_backend.title()} ASR")
                self._app.tray.invalidate_menu_cache()
            else:
                log.warning("[MODEL] %s model failed to load", new_backend.title())
                self._app.tray.set_state(AppState.ERROR, f"{new_backend.title()} model failed to load")
        except Exception as exc:
            log.exception("[MODEL] Model load failed: %s", exc)
            self._app.tray.set_state(AppState.ERROR, f"Model failed: {exc}")

    # G4-CR-08: set_active_backend — switch ASR backend WITHOUT changing
    # model_size. Mirrors change_model's unload/reload cycle but only
    # swaps the backend. The model_size field is left untouched so
    # Whisper's model selection (which depends on model_size) is
    # preserved across backend switches. For parakeet/qwen, model_size
    # is informational (their engines ignore it).
    def set_active_backend(self, backend: str) -> None:
        """Switch the active ASR backend WITHOUT changing ``model_size``.

        G4-CR-08: previously ``Service.set_active_backend`` delegated to
        ``self._app.models.set_active_backend(backend)`` but
        :class:`ModelManager` never defined that method — the IPC
        ``set_config`` handler caught the ``AttributeError`` and logged
        a warning, returning ``ack`` to the renderer while the actual
        backend swap never happened. Old backends stayed loaded (GPU
        + RAM) until LRU eviction.

        This implementation mirrors :meth:`change_model`'s
        unload/reload cycle but skips the ``model_size`` mutation:

        1. Acquire ``_config_mutation_lock`` + ``_model_change_lock``.
        2. If ``backend == config.asr_backend``, no-op return.
        3. Unload the OLD backend's engine (if loaded) so its GPU/RAM
           is released immediately, not via LRU eviction later.
        4. Set ``config.asr_backend = backend`` and persist via
           ``config.save()``.
        5. Pre-construct the new backend via ``_ensure_engine`` (no
           load yet — just constructs the engine object).
        6. Release ``_config_mutation_lock`` (per G4-H-16).
        7. Load the new backend via ``_registry.load_active`` under
           ``_model_change_lock`` alone.

        Parameters
        ----------
        backend :
            One of ``"whisper"``, ``"qwen"``, ``"parakeet"``. Any other
            value raises :class:`ValueError`.
        """
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # G4-H-16: outer = _model_change_lock (held throughout the
        # unload+construct+load cycle). Inner = _config_mutation_lock
        # (acquired only for setattr + save + the quick unload phase).
        with self._model_change_lock:
            with self._app._config_mutation_lock:
                old_backend = self._app.config.asr_backend
                if old_backend == backend:
                    # No-op — backend already active.
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
                self._sync_registry_from_fields()
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
                            "[PERF-015] LRU tracking failed (non-fatal)",
                            exc_info=True,
                        )
                    active = self._registry.get_active()
                    name = self._registry.active_name
                    if name == "whisper" and active is not None:
                        self._app.tray.set_state(AppState.IDLE, f"Ready -- {active.device_info}")
                    else:
                        self._app.tray.set_state(AppState.IDLE, f"Ready -- {name.title()} ASR")
                    self._app.tray.invalidate_menu_cache()
                else:
                    log.warning(
                        "[MODEL] %s backend failed to load during set_active_backend",
                        backend.title(),
                    )
                    self._app.tray.set_state(
                        AppState.ERROR,
                        f"{backend.title()} backend failed to load",
                    )
            except Exception as exc:
                log.exception("[MODEL] set_active_backend load failed: %s", exc)
                self._app.tray.set_state(AppState.ERROR, f"Backend failed: {exc}")

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

    def ensure_active_engine_loaded(self) -> Any | None:
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
        # _lazy_init_lock is created in __init__ (LAZY-INIT-LOCK-FIX).
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

            # Find the oldest (least recently used) backend.
            # RW-6 (pyrefly): use a lambda instead of passing
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
                "[PERF-015] Evicting LRU model '%s' (last used %.1fs ago) — %d models loaded, max is %d",
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

    def touch_active_model(self) -> None:
        """PERF-015 / HIGH-19: refresh the LRU timestamp for the active backend.

        Public entry point intended to be called from
        :meth:`voice_typer.server.dictation_pipeline.DictationPipeline._transcribe`
        after every successful ``transcribe()`` so the LRU eviction
        knows the active backend is in use (and therefore should NOT be
        the one evicted on the next ``load_active`` / ``load_with_fallback``).

        Wiring this into the dictation pipeline is tracked under
        FIX-15 / follow-up — this method is exposed now so the LRU
        tracking added in HIGH-19 (``touch_model`` after load) has a
        matching "after transcribe" entry point ready to call.

        Safe to call when no backend is active — ``touch_model`` is a
        no-op for unknown backend names (it just records the timestamp;
        eviction only considers names that were touched).
        """
        try:
            self.touch_model(self._registry.active_name)
        except Exception:
            log.warning(
                "[PERF-015] touch_active_model failed (non-fatal)",
                exc_info=True,
            )
