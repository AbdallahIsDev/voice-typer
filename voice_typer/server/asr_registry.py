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

    def __init__(self, config: Any):
        self._config = config
        self._backends: dict[str, Any] = {}
        # ARCH-007: the whisper backend is registered under "whisper"
        # and also as the fallback for unknown backends.
        # HIGH-20 / MODEL-2: reentrant lock so methods that already hold
        # the lock (e.g. ``load_with_fallback`` calling ``self.unregister``)
        # don't self-deadlock.  Plain ``Lock`` would deadlock here.
        self._lock = threading.RLock()

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

    def load_with_fallback(self, progress_callback: Any = None) -> Any | None:
        """Load the configured backend; on failure, fall back to whisper.

        ARCH-008: replaces the duplicated fallback logic in
        app.py's _load_transcription_engine_background().

        MEM-01 (c-review): on failure, the failed backend's ``unload()``
        is now called BEFORE ``unregister(name)`` so any partially-
        allocated resources (torch tensors, CUDA contexts, multi-GB
        model weights) are released. Previously, the local ``backend``
        variable went out of scope after the except block without
        calling ``unload()``, leaking whatever the failed ``load()``
        allocated before raising (e.g. a half-downloaded model on disk
        + its in-memory weight tensor). ``unload()`` is safe to call
        on a partially-loaded engine — all three backends guard on
        ``self._model is None``.

        HIGH-20 / MODEL-2: the dict reads (``self._backends.get``) are
        guarded by ``self._lock`` so a concurrent ``register`` /
        ``unregister`` from another thread (e.g. ``change_model``)
        cannot corrupt the iteration.  The actual ``backend.load()``
        call is OUTSIDE the lock so a slow GPU/disk load doesn't block
        other readers (e.g. ``get_active`` from the dictation pipeline).

        Args:
            progress_callback: optional callable(msg: str) to report
                loading progress (e.g. tray state updates).
        """
        _cb = progress_callback or (lambda msg: None)

        # Try the configured backend first
        name = self.active_name
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
                return backend
            except Exception as exc:
                log.warning("[ASR_REGISTRY] failed to load %s: %s, trying fallback", name, exc)
                # MEM-01 (c-review): release any partially-allocated
                # resources (torch tensors, CUDA contexts, model weights)
                # BEFORE unregistering the backend. unload() is safe to
                # call on a partially-loaded engine — all three backends
                # guard on ``self._model is None``. Wrap in try/except so
                # an unload failure (e.g. a corrupted model handle) does
                # not prevent the unregister + whisper fallback.
                try:
                    backend.unload()
                    log.info("[ASR_REGISTRY] unloaded failed backend: %s", name)
                except Exception as unload_exc:
                    log.warning(
                        "[ASR_REGISTRY] failed to unload %s after load failure: %s",
                        name,
                        unload_exc,
                    )
                # unregister() acquires the lock itself (RLock, so
                # re-entry is safe even if we held it here).
                self.unregister(name)

        # Fallback to whisper
        with self._lock:
            whisper = self._backends.get("whisper")
        if whisper is not None:
            try:
                # OUTSIDE lock — see comment above.
                whisper.load(progress_callback=_cb)
                log.info("[ASR_REGISTRY] loaded fallback backend: whisper")
                return whisper
            except Exception:
                log.exception("[ASR_REGISTRY] whisper fallback also failed")
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
