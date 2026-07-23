"""AsrBackendRegistry: centralized ASR backend selection.

ARCH-007/008: previously VoiceTyperApp had three separate engine
handles (self.transcriber, self._qwen_engine, self._parakeet_engine)
and every method re-checked self.config.asr_backend to pick the
right one. Three near-identical load+fallback branches were
duplicated across app.py.

This registry centralizes the selection logic. The app registers
its engines, and callers use get_active() to get the current
backend without knowing which one it is.
"""

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)


class AsrBackendRegistry:
    """Registry of ASR backends — single source of truth for "the model".

    ARCH-008: replaces the three-field pattern
    (self.transcriber / self._qwen_engine / self._parakeet_engine)
    with a single registry that callers query via get_active().

    HIGH-20 / MODEL-2: the ``_backends`` dict is guarded by
    ``self._lock`` (a reentrant lock).  ``register`` / ``unregister`` /
    ``get`` / ``load_with_fallback`` acquire the lock around their dict
    operations only — the actual ``backend.load(...)`` call is left
    OUTSIDE the lock so a slow GPU/disk load doesn't block other
    readers (e.g. ``get_active`` from the dictation pipeline).
    """

    # G4-M-45: after this many consecutive load failures, a backend is
    # marked "disabled" — subsequent ``load_with_fallback`` calls skip it
    # and fall straight through to the whisper fallback. The disabled
    # state is persisted in ``config.disabled_backends`` (if the Config
    # dataclass exposes that field — see ConfigApplier for the
    # defensive getattr pattern). The user can re-enable a disabled
    # backend from Settings (resets the counter).
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, config: Any):
        self._config = config
        self._backends: dict[str, Any] = {}
        # ARCH-007: the whisper backend is registered under "whisper"
        # and also as the fallback for unknown backends.
        # HIGH-20 / MODEL-2: reentrant lock so methods that already hold
        # the lock (e.g. ``load_with_fallback`` calling ``self.unregister``)
        # don't self-deadlock.  Plain ``Lock`` would deadlock here.
        self._lock = threading.RLock()
        # G4-M-45: per-backend consecutive-failure counter. Incremented
        # on load failure, reset to 0 on load success. When it reaches
        # ``_MAX_CONSECUTIVE_FAILURES``, the backend is added to
        # ``_disabled_backends`` and a one-time tray notification is
        # fired via the ``on_backend_disabled`` callback (set by
        # ModelManager).
        self._failure_counts: dict[str, int] = {}
        self._disabled_backends: set[str] = set()
        # Restore persisted disabled state from config (defensive —
        # config.py may not have the ``disabled_backends`` field yet).
        persisted = getattr(config, "disabled_backends", None) or []
        try:
            self._disabled_backends = set(persisted)
        except TypeError:
            self._disabled_backends = set()
        # Optional callback invoked once when a backend is disabled.
        # Signature: ``on_backend_disabled(backend_name: str, failure_count: int) -> None``.
        # Set by ModelManager to surface a tray notification.
        self.on_backend_disabled: Any | None = None

    def register(self, name: str, backend: Any) -> None:
        """Register a backend by name (e.g. 'whisper', 'qwen', 'parakeet')."""
        with self._lock:
            self._backends[name] = backend
        log.debug("[ASR_REGISTRY] registered backend: %s (loaded=%s)", name, getattr(backend, "is_loaded", True))

    def unregister(self, name: str) -> None:
        """Unregister a backend by name.

        ARCH-007: used by app.py when a backend fails to load and
        should be removed from the registry so get_active() no longer
        considers it.
        """
        with self._lock:
            if name in self._backends:
                del self._backends[name]
                log.debug("[ASR_REGISTRY] unregistered backend: %s", name)

    def get_active(self) -> Any | None:
        """Return the currently active backend based on config.asr_backend.

        Falls back to 'whisper' if the configured backend isn't loaded.
        Returns None if no backend is available.
        """
        name = getattr(self._config, "asr_backend", "whisper")
        with self._lock:
            backend = self._backends.get(name)
            if backend is not None and self._is_ready(backend):
                return backend

            # Fallback: try whisper (the default/local backend)
            whisper = self._backends.get("whisper")
            if whisper is not None and self._is_ready(whisper):
                if name != "whisper":
                    log.info("[ASR_REGISTRY] %s backend not ready, falling back to whisper", name)
                return whisper

            # Last resort: return whatever we have, even if not loaded.
            # Iterate a snapshot (``list``) so callers can mutate the
            # registry while we hold the lock without raising
            # ``RuntimeError: dictionary changed size during iteration``.
            for b in list(self._backends.values()):
                if b is not None:
                    # Diagnostic: callers downstream (the dictation
                    # pipeline) will invoke ``transcribe_with_fallback``
                    # on this backend, which can return an empty string
                    # silently when the model isn't actually loaded —
                    # the user sees "finish dictation → nothing
                    # transcribed" with no error. Surface a warning
                    # here so this failure mode is traceable from the
                    # log file (the engine's own load() failure path
                    # may have logged at DEBUG/INFO, easily missed).
                    if not self._is_ready(b):
                        log.warning(
                            "[ASR_REGISTRY] returning unloaded backend %s "
                            "(is_loaded=False) as last-resort active — "
                            "transcription may return empty silently",
                            name,
                        )
                    return b
        return None

    def _is_ready(self, backend: Any) -> bool:
        """Check if a backend is ready for transcription."""
        is_loaded = getattr(backend, "is_loaded", True)
        return is_loaded

    @property
    def active_name(self) -> str:
        """Return the name of the active backend."""
        return getattr(self._config, "asr_backend", "whisper")

    def get(self, name: str) -> Any | None:
        """Get a specific backend by name."""
        with self._lock:
            return self._backends.get(name)

    # ── ARCH-007/008: registry convenience methods ────────────────

    # ARCH-007: Backend module path / class name lookup for create().
    # Centralized here so all engine construction goes through one chokepoint.
    _BACKEND_SPECS: dict[str, tuple[str, str]] = {
        "whisper": ("voice_typer.server.transcription", "TranscriptionEngine"),
        "qwen": ("voice_typer.server.qwen_engine", "QwenEngine"),
        "parakeet": ("voice_typer.server.parakeet_engine", "ParakeetEngine"),
    }

    def create(
        self,
        name: str,
        *,
        whisper_kwargs: dict | None = None,
        qwen_kwargs: dict | None = None,
        parakeet_kwargs: dict | None = None,
    ) -> Any | None:
        """ARCH-007: Construct (but don't load) a backend engine.

        Centralizes the triplicated TranscriptionEngine(...) /
        QwenEngine(...) / ParakeetEngine(...) construction that was
        previously copy-pasted across app.py:_load_transcription_engine_background,
        _fallback_to_whisper, and _change_model.

        Returns the constructed engine (registered in the registry) or
        None on ImportError / construction failure.

        Parameters
        ----------
        name : str
            Backend name: "whisper", "qwen", or "parakeet".
        whisper_kwargs, qwen_kwargs, parakeet_kwargs : dict, optional
            Constructor kwargs for the corresponding backend. Only the
            kwarg dict matching ``name`` is used; the others are ignored.

        Examples
        --------
        >>> registry.create("whisper", whisper_kwargs=dict(
        ...     model_size="tiny.en", device="cpu", language="en",
        ...     beam_size=1, best_of=1, condition_on_previous_text=False,
        ... ))
        """
        spec = self._BACKEND_SPECS.get(name)
        if spec is None:
            log.error("[ASR_REGISTRY] unknown backend: %s", name)
            return None

        module_path, class_name = spec
        kwargs_map = {
            "whisper": whisper_kwargs or {},
            "qwen": qwen_kwargs or {},
            "parakeet": parakeet_kwargs or {},
        }
        kwargs = kwargs_map[name]

        try:
            import importlib

            mod = importlib.import_module(module_path)
            engine_cls = getattr(mod, class_name)
            engine = engine_cls(**kwargs)
            # Register immediately so callers can fetch via get(name).
            self.register(name, engine)
            log.info(
                "[ASR_REGISTRY] created %s backend (%s), registered",
                name,
                class_name,
            )
            return engine
        except ImportError:
            log.warning(
                "[ASR_REGISTRY] %s backend package not installed, unavailable",
                name,
            )
            return None
        except Exception as exc:
            # CR-91: previously ``log.error("...: %s", name, exc)`` with
            # no ``exc_info=True``. Backend init failures (Parakeet /
            # Qwen) often originate deep in torch / CUDA / transformers
            # stack — the actionable diagnostic is in the *traceback*,
            # not the exception's ``str()``. Include ``exc_info=True`` so
            # the full traceback is logged (matches the ``log.exception``
            # pattern used elsewhere in this codebase).
            log.error(
                "[ASR_REGISTRY] failed to initialise %s backend: %s",
                name,
                exc,
                exc_info=True,
            )
            return None

    def load_active(self, progress_callback: Any = None) -> Any | None:
        """Load the active backend and return it.

        Delegates to the backend's load() method with a progress
        callback.  Returns the backend on success, None on failure.
        """
        _cb = progress_callback or (lambda msg: None)
        backend = self.get_active()
        if backend is None:
            log.warning("[ASR_REGISTRY] no active backend to load")
            return None
        try:
            backend.load(progress_callback=_cb)
            log.info("[ASR_REGISTRY] loaded active backend: %s", self.active_name)
            return backend
        except Exception as exc:
            log.exception("[ASR_REGISTRY] failed to load active backend %s: %s", self.active_name, exc)
            return None

    # ── G4-M-45 circuit-breaker helpers ────────────────────────────────
    # These three helpers track per-backend consecutive load failures
    # and disable a backend after ``_MAX_CONSECUTIVE_FAILURES`` (3)
    # consecutive failures — subsequent ``load_with_fallback`` calls
    # skip the disabled backend and go straight to the whisper fallback.
    # The disabled state is persisted in ``config.disabled_backends``
    # (defensively — Config may not yet have that field).

    def _is_disabled(self, name: str) -> bool:
        """Return True if ``name`` is in the disabled-backends set."""
        with self._lock:
            return name in self._disabled_backends

    def failure_count(self, name: str) -> int:
        """Return the current consecutive-failure count for ``name``.

        G4-M-45: returns 0 for backends that have never failed (or were
        never registered). Used by tests / Settings UI to surface the
        circuit-breaker state to the user.
        """
        with self._lock:
            return self._failure_counts.get(name, 0)

    def reset_failures(self, name: str) -> None:
        """Clear the failure counter and disabled state for ``name``.

        G4-M-45: called by the user-facing "re-enable backend" path
        (e.g. Settings -> Models -> Re-enable) so a backend that was
        auto-disabled by the circuit breaker can be retried. Also
        invoked programmatically when the user manually requests a
        retry (e.g. via the F2 hotkey after a transient failure).

        Clears both ``_failure_counts[name]`` (counter) and the
        ``_disabled_backends`` membership, then persists the disabled
        set to config so the change survives a restart.
        """
        with self._lock:
            self._failure_counts[name] = 0
            if name in self._disabled_backends:
                self._disabled_backends.discard(name)
                log.info("[ASR_REGISTRY] backend %s re-enabled (manual reset)", name)
                self._persist_disabled()

    def _record_success(self, name: str) -> None:
        """Reset the failure counter for ``name`` and re-enable it if disabled."""
        with self._lock:
            self._failure_counts[name] = 0
            if name in self._disabled_backends:
                self._disabled_backends.discard(name)
                log.info("[ASR_REGISTRY] backend %s re-enabled (load succeeded)", name)
                self._persist_disabled()

    def _record_failure(self, name: str) -> None:
        """Increment the failure counter for ``name``; disable if threshold reached."""
        with self._lock:
            count = self._failure_counts.get(name, 0) + 1
            self._failure_counts[name] = count
            if count >= self._MAX_CONSECUTIVE_FAILURES and name not in self._disabled_backends:
                self._disabled_backends.add(name)
                log.warning(
                    "[ASR_REGISTRY] backend %s disabled after %d consecutive failures",
                    name,
                    count,
                )
                self._persist_disabled()
                # Fire one-time callback so ModelManager can show a tray
                # notification. Defensive — callback may not be set yet.
                try:
                    if self.on_backend_disabled is not None:
                        self.on_backend_disabled(name, count)
                except Exception:
                    log.warning(
                        "[ASR_REGISTRY] on_backend_disabled callback raised",
                        exc_info=True,
                    )

    def _persist_disabled(self) -> None:
        """Persist ``_disabled_backends`` to ``config.disabled_backends`` if the field exists."""
        try:
            self._config.disabled_backends = sorted(self._disabled_backends)
        except (AttributeError, TypeError):
            # Config dataclass may not expose ``disabled_backends`` —
            # silently skip; in-memory state still works for this run.
            pass

    # ── End G4-M-45 circuit-breaker helpers ────────────────────────────

    def load_with_fallback(self, progress_callback: Any = None) -> Any | None:
        """Load the configured backend; on failure, fall back to whisper.

        ARCH-008: replaces the duplicated fallback logic in
        app.py's _load_transcription_engine_background().

        MEM-01 (c-review): on failure, the failed backend's ``unload()``
        is called so any partially-allocated resources (torch tensors,
        CUDA contexts, multi-GB model weights) are released.
        ``unload()`` is safe to call on a partially-loaded engine — all
        three backends guard on ``self._model is None``.

        HIGH-20 / MODEL-2: the dict reads (``self._backends.get``) are
        guarded by ``self._lock`` so a concurrent ``register`` /
        ``unregister`` from another thread (e.g. ``change_model``)
        cannot corrupt the iteration.  The actual ``backend.load()``
        call is OUTSIDE the lock so a slow GPU/disk load doesn't block
        other readers (e.g. ``get_active`` from the dictation pipeline).

        G4-H-19: when the primary backend fails AND the primary is not
        whisper, we construct the whisper engine (via :meth:`create`)
        before attempting the whisper load. Previously,
        ``load_with_fallback`` only worked if the whisper engine was
        already registered. On a cold boot with a non-whisper backend
        configured, the whisper branch silently no-op'd and the
        registry returned ``None``.

        G4-M-45: circuit breaker. Each PRIMARY-backend load failure
        increments a per-backend counter; on success the counter is
        reset. After ``_MAX_CONSECUTIVE_FAILURES`` (3) consecutive
        failures, the primary backend is added to ``_disabled_backends``
        and skipped on subsequent ``load_with_fallback`` calls — we go
        straight to the whisper fallback. The whisper FALLBACK's
        failures are NOT tracked (whisper is a safety net, not the
        user's configured backend); tracking them would fire a
        spurious ``on_backend_disabled("whisper")`` callback whenever a
        non-whisper primary is persistently failing.

        XS-17 (F-09): pre-fix, the primary backend was UNREGISTERED on
        failure, which meant subsequent ``load_with_fallback`` calls no
        longer found it in ``_backends`` and skipped the primary path
        entirely — so the failure counter only incremented ONCE and
        the circuit breaker never tripped. The failed primary now
        stays registered (only ``unload()`` is called for resource
        cleanup) so the next call can retry it and increment the
        counter toward the disable threshold.

        Args:
            progress_callback: optional callable(msg: str) to report
                loading progress (e.g. tray state updates).
        """
        _cb = progress_callback or (lambda msg: None)

        # Try the configured (primary) backend first.
        name = self.active_name
        # G4-M-45: skip disabled backends — go straight to whisper fallback.
        if self._is_disabled(name):
            log.info(
                "[ASR_REGISTRY] backend %s is disabled (circuit breaker) — skipping to whisper fallback",
                name,
            )
        else:
            with self._lock:
                backend = self._backends.get(name)
            if backend is not None:
                try:
                    # OUTSIDE lock: a model load can take 5-50s (cold disk
                    # + torch import).  Holding the lock here would block
                    # every other ``get`` / ``register`` / ``get_active``
                    # call for the entire load duration.
                    backend.load(progress_callback=_cb)
                    log.info("[ASR_REGISTRY] loaded backend: %s", name)
                    # G4-M-45: success — reset the failure counter.
                    self._record_success(name)
                    return backend
                except Exception as exc:
                    log.warning("[ASR_REGISTRY] failed to load %s: %s, trying fallback", name, exc)
                    # G4-M-45: increment failure counter; possibly disable.
                    self._record_failure(name)
                    # MEM-01 (c-review): release any partially-allocated
                    # resources (torch tensors, CUDA contexts, model weights).
                    # unload() is safe to call on a partially-loaded engine —
                    # all three backends guard on ``self._model is None``.
                    # Wrap in try/except so an unload failure (e.g. a
                    # corrupted model handle) does not prevent the whisper
                    # fallback.
                    try:
                        backend.unload()
                        log.info("[ASR_REGISTRY] unloaded failed backend: %s", name)
                    except Exception as unload_exc:
                        log.warning(
                            "[ASR_REGISTRY] failed to unload %s after load failure: %s",
                            name,
                            unload_exc,
                        )
                    # XS-17 (F-09): do NOT unregister the failed primary
                    # backend — keep it in ``_backends`` so subsequent
                    # ``load_with_fallback`` calls retry it (and increment
                    # the failure counter toward the disable threshold).
                    # Pre-fix, unregistering meant the counter only
                    # incremented once and the circuit breaker never
                    # tripped. ``get_active()`` still falls through to the
                    # whisper fallback because the failed backend's
                    # ``is_loaded`` remains False.

        # If the primary IS whisper and it failed (or was disabled),
        # there is no separate whisper backend to fall back to — return
        # None so the caller (ModelManager.try_load) can surface the
        # ERROR state to the user.
        if name == "whisper":
            return None

        # Fallback to whisper (primary was a non-whisper backend that
        # failed or was disabled).
        with self._lock:
            whisper = self._backends.get("whisper")
        # G4-H-19: if the whisper backend was never constructed (cold
        # boot with a non-whisper primary), construct it now using a
        # safe default model_size ("tiny.en") so the fallback actually
        # has something to load. The kwargs mirror ModelManager's
        # _ensure_engine("whisper") call.
        if whisper is None:
            log.info("[ASR_REGISTRY] whisper backend not registered — constructing with tiny.en for fallback")
            whisper = self.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size="tiny.en",
                    device=getattr(self._config, "device", "cpu"),
                    language=getattr(self._config, "language", "en"),
                    beam_size=getattr(self._config, "beam_size", 1),
                    best_of=getattr(self._config, "best_of", 1),
                    condition_on_previous_text=getattr(self._config, "condition_on_previous_text", False),
                    config=self._config,
                ),
            )
        if whisper is not None:
            try:
                # OUTSIDE lock — see comment above.
                whisper.load(progress_callback=_cb)
                log.info("[ASR_REGISTRY] loaded fallback backend: whisper")
                # G4-M-45 / XS-17: do NOT call ``_record_success("whisper")``
                # here — whisper is a FALLBACK, not the user's configured
                # backend. Tracking its success would mask failures of the
                # primary backend (the user's actual choice). The primary's
                # counter is only reset by a successful load of the PRIMARY
                # backend, not by the whisper fallback succeeding.
                return whisper
            except Exception:
                log.exception("[ASR_REGISTRY] whisper fallback also failed")
                # G4-M-45 / XS-17: do NOT call ``_record_failure("whisper")``
                # — the circuit breaker tracks the user's configured
                # backend, not the whisper fallback. Otherwise, a
                # persistently failing parakeet would also disable whisper
                # and fire a second ``on_backend_disabled("whisper")``
                # callback (breaking the "exactly one disable callback"
                # contract pinned by ``test_backend_disabled_after_max_consecutive_failures``).
                # MEM-01 (c-review): same fix for the whisper fallback
                # path — unload before giving up so we don't leak the
                # whisper backend's partially-allocated resources.
                try:
                    whisper.unload()
                    log.info("[ASR_REGISTRY] unloaded failed fallback backend: whisper")
                except Exception as unload_exc:
                    log.warning(
                        "[ASR_REGISTRY] failed to unload whisper after load failure: %s",
                        unload_exc,
                    )

        return None

    def unload(self, name: str | None = None) -> None:
        """Unload a backend by name, or the active backend if name is None.

        ARCH-007: used by app.py's _change_model() before loading
        the new model.
        """
        target = name or self.active_name
        with self._lock:
            backend = self._backends.get(target)
        if backend is not None:
            try:
                backend.unload()
                log.info("[ASR_REGISTRY] unloaded backend: %s", target)
            except Exception as exc:
                log.warning("[ASR_REGISTRY] failed to unload %s: %s", target, exc)

    @property
    def available_backends(self) -> list[str]:
        """Return names of all registered backends."""
        with self._lock:
            return list(self._backends.keys())
