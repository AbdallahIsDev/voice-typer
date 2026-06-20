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
from typing import Optional, Any

log = logging.getLogger("voice_typer")


class AsrBackendRegistry:
    """Registry of ASR backends — single source of truth for "the model".

    ARCH-008: replaces the three-field pattern
    (self.transcriber / self._qwen_engine / self._parakeet_engine)
    with a single registry that callers query via get_active().
    """

    def __init__(self, config: Any):
        self._config = config
        self._backends: dict[str, Any] = {}
        # ARCH-007: the whisper backend is registered under "whisper"
        # and also as the fallback for unknown backends.

    def register(self, name: str, backend: Any) -> None:
        """Register a backend by name (e.g. 'whisper', 'qwen', 'parakeet')."""
        self._backends[name] = backend
        log.debug("[ASR_REGISTRY] registered backend: %s (loaded=%s)",
                  name, getattr(backend, "is_loaded", True))

    def unregister(self, name: str) -> None:
        """Unregister a backend by name.

        ARCH-007: used by app.py when a backend fails to load and
        should be removed from the registry so get_active() no longer
        considers it.
        """
        if name in self._backends:
            del self._backends[name]
            log.debug("[ASR_REGISTRY] unregistered backend: %s", name)

    def get_active(self) -> Optional[Any]:
        """Return the currently active backend based on config.asr_backend.

        Falls back to 'whisper' if the configured backend isn't loaded.
        Returns None if no backend is available.
        """
        name = getattr(self._config, "asr_backend", "whisper")
        backend = self._backends.get(name)
        if backend is not None and self._is_ready(backend):
            return backend

        # Fallback: try whisper (the default/local backend)
        whisper = self._backends.get("whisper")
        if whisper is not None and self._is_ready(whisper):
            if name != "whisper":
                log.info("[ASR_REGISTRY] %s backend not ready, falling back to whisper", name)
            return whisper

        # Last resort: return whatever we have, even if not loaded
        for b in self._backends.values():
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

    def get(self, name: str) -> Optional[Any]:
        """Get a specific backend by name."""
        return self._backends.get(name)

    # ── ARCH-007/008: registry convenience methods ────────────────

    def load_active(self, progress_callback: Any = None) -> Optional[Any]:
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
            log.error("[ASR_REGISTRY] failed to load active backend %s: %s",
                      self.active_name, exc)
            return None

    def load_with_fallback(self, progress_callback: Any = None) -> Optional[Any]:
        """Load the configured backend; on failure, fall back to whisper.

        ARCH-008: replaces the duplicated fallback logic in
        app.py's _load_transcription_engine_background().

        Args:
            progress_callback: optional callable(msg: str) to report
                loading progress (e.g. tray state updates).
        """
        _cb = progress_callback or (lambda msg: None)

        # Try the configured backend first
        name = self.active_name
        backend = self._backends.get(name)
        if backend is not None:
            try:
                backend.load(progress_callback=_cb)
                log.info("[ASR_REGISTRY] loaded backend: %s", name)
                return backend
            except Exception as exc:
                log.warning("[ASR_REGISTRY] failed to load %s: %s, trying fallback", name, exc)
                self.unregister(name)

        # Fallback to whisper
        whisper = self._backends.get("whisper")
        if whisper is not None:
            try:
                whisper.load(progress_callback=_cb)
                log.info("[ASR_REGISTRY] loaded fallback backend: whisper")
                return whisper
            except Exception as exc:
                log.error("[ASR_REGISTRY] whisper fallback also failed: %s", exc)

        return None

    def unload(self, name: Optional[str] = None) -> None:
        """Unload a backend by name, or the active backend if name is None.

        ARCH-007: used by app.py's _change_model() before loading
        the new model.
        """
        target = name or self.active_name
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
        return list(self._backends.keys())
