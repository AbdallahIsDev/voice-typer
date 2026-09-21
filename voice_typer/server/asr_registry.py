"""ASR engine registry."""

from __future__ import annotations

import logging

from voice_typer.server.asr.circuit_breaker import (
    BackendDisabledCallback,
    BackendDisabledEventGate,
    LastResortCallback,
    LastResortEventGate,
)
from voice_typer.server.asr.registry import (
    AsrBackend,
    ConfigProtocol,
    ProgressCallback,
    RegistryCore,
)
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError

log = logging.getLogger(__name__)


class AsrBackendRegistry(RegistryCore):
    """Public facade for the ASR backend registry."""

    # Re-exported at class scope for backward compat (tests read
    _MAX_CONSECUTIVE_FAILURES = 3

    # Maps backend name -> (module_path, class_name) for lazy import.
    _BACKEND_SPECS: dict[str, tuple[str, str]] = {
        "whisper": ("voice_typer.server.transcription", "TranscriptionEngine"),
        "qwen": ("voice_typer.server.qwen_engine", "QwenEngine"),
        "parakeet": ("voice_typer.server.parakeet_engine", "ParakeetEngine"),
    }

    # ``load_active`` lives on the facade (not on RegistryCore) so its

    def load_active(self, progress_callback: ProgressCallback | None = None) -> AsrBackend | None:
        """Load the active backend and return it."""
        _cb = progress_callback or (lambda msg: None)
        # The _is_disabled gate must come BEFORE backend resolution.
        if self._is_disabled(self.active_name):
            log.warning(
                "[ASR_REGISTRY] active backend %s is disabled, refusing to load",
                self.active_name,
            )
            return None
        backend = self.get(self.active_name)
        if backend is None:
            log.warning("[ASR_REGISTRY] no active backend to load")
            return None
        try:
            backend.load(progress_callback=_cb)
            log.info("[ASR_REGISTRY] loaded active backend: %s", self.active_name)
            self._record_success(self.active_name)
            return backend
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # Not a transient failure: the model isn't downloaded (or the
            log.warning(
                "[ASR_REGISTRY] active backend %s refused to load: %s, "
                "model not downloaded / integrity check failed. "
                "No circuit-breaker record.",
                self.active_name,
                exc,
            )
            raise
        except Exception as exc:
            log.exception("[ASR_REGISTRY] failed to load active backend %s: %s", self.active_name, exc)
            self._record_failure(self.active_name)
            try:
                backend.unload()
            except Exception as unload_exc:
                log.warning(
                    "[ASR_REGISTRY] failed to unload %s after load failure: %s",
                    self.active_name,
                    unload_exc,
                    exc_info=True,
                )
            return None

    def unregister(self, name: str) -> None:
        """Unregister a backend by name."""
        with self._lock:
            if name in self._backends:
                del self._backends[name]
                log.debug("[ASR_REGISTRY] unregistered backend: %s", name)

    def create(
        self,
        name: str,
        *,
        whisper_kwargs: dict | None = None,
        qwen_kwargs: dict | None = None,
        parakeet_kwargs: dict | None = None,
    ) -> AsrBackend | None:
        """Construct (but don't load) a backend engine.

        Returns the constructed engine (registered in the registry) or
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
            self.register(name, engine)
            log.info("[ASR_REGISTRY] created %s backend (%s), registered", name, class_name)
            return engine
        except ImportError as exc:
            # Forward the ImportError's str() (carries the missing
            log.warning(
                "[ASR_REGISTRY] %s backend package not installed (%s), unavailable",
                name,
                exc,
                exc_info=True,
            )
            return None
        except Exception as exc:
            log.exception("[ASR_REGISTRY] failed to initialise %s backend: %s", name, exc)
            return None

    def unload(self, name: str | None = None) -> None:
        """Unload a backend by name, or the active backend if name is None."""
        target = name or self.active_name
        with self._lock:
            if self._busy.is_target_busy(target):
                raise RuntimeError(f"cannot unload busy backend: {target}")
            backend = self._backends.get(target)
        if backend is not None:
            try:
                backend.unload()
                log.info("[ASR_REGISTRY] unloaded backend: %s", target)
            except Exception:
                # ``log.exception`` so the full traceback lands in the
                log.exception("[ASR_REGISTRY] failed to unload %s", target)

    # These delegate to ``self._breaker`` (composed in RegistryCore).

    def _is_disabled(self, name: str) -> bool:
        """Return True if ``name`` is in the disabled-backends set."""
        return self._breaker._is_disabled(name)

    def failure_count(self, name: str) -> int:
        """Return the current consecutive-failure count for ``name``."""
        return self._breaker.failure_count(name)

    def reset_failures(self, name: str) -> None:
        """Clear the failure counter and disabled state for ``name``."""
        self._breaker.reset_failures(name)

    def _record_success(self, name: str) -> None:
        """Reset the failure counter for ``name`` and clear the last-resort latch."""
        self._breaker._record_success(name)

    def _record_failure(self, name: str) -> None:
        """Increment the failure counter for ``name``; disable if threshold reached."""
        self._breaker._record_failure(name)

    def _persist_disabled(self) -> None:
        """Persist ``_disabled_backends`` to ``config.disabled_backends``."""
        self._breaker._persist_disabled()

    def _fire_last_resort_subscribers(self, name: str) -> None:
        """Fire last-resort subscribers + publish the event_bus event."""
        self._breaker.fire_last_resort_subscribers(name)

    # These delegate to ``self._busy`` (composed in RegistryCore).

    def is_busy(self, name: str | None = None) -> bool:
        """Return True if the named/active backend is inside ``transcribe_with_fallback``."""
        return self._busy.is_busy(name)

    def set_busy(self, name: str | None = None) -> None:
        """Mark the named/active backend as busy."""
        self._busy.set_busy(name)

    def clear_busy(self, name: str | None = None) -> None:
        """Mark the named/active backend as not busy (idempotent)."""
        self._busy.clear_busy(name)

    def busy_context(self, name: str | None = None):
        """Context manager that sets/clears the busy flag around a block."""
        return self._busy.busy_context(name)

    def force_clear_busy(self, name: str | None = None) -> None:
        """Alias for :meth:`clear_busy` for the watchdog's force-recover path."""
        self._busy.force_clear_busy(name)

    # These expose the breaker's internal state as attributes on the

    @property
    def _disabled_backends(self) -> set[str]:
        return self._breaker._disabled_backends

    @property
    def _failure_counts(self) -> dict[str, int]:
        return self._breaker._failure_counts

    @property
    def _last_resort_notified(self) -> bool:
        return self._breaker._last_resort_notified

    @property
    def _on_backend_disabled_subscribers(self) -> set[BackendDisabledCallback]:
        return self._breaker._on_backend_disabled_subscribers

    @property
    def _on_last_resort_subscribers(self) -> set[LastResortCallback]:
        return self._breaker._on_last_resort_subscribers

    @property
    def _busy_backends(self) -> set[str]:
        return self._busy._busy_backends

    # Backward-compatible properties so existing

    @property
    def on_backend_disabled(self) -> set[BackendDisabledCallback]:
        return self._breaker.on_backend_disabled

    @on_backend_disabled.setter
    def on_backend_disabled(self, fn: BackendDisabledCallback | None) -> None:
        self._breaker.on_backend_disabled = fn

    def add_backend_disabled_subscriber(self, fn: BackendDisabledCallback) -> None:
        """Register a subscriber for backend-disabled events."""
        self._breaker.add_backend_disabled_subscriber(fn)

    def remove_backend_disabled_subscriber(self, fn: BackendDisabledCallback) -> None:
        """Unregister a backend-disabled subscriber (no-op if absent)."""
        self._breaker.remove_backend_disabled_subscriber(fn)

    @property
    def on_last_resort(self) -> set[LastResortCallback]:
        """Set of subscribers fired when get_active() falls through to an
        unloaded last-resort backend."""
        return self._breaker.on_last_resort

    @on_last_resort.setter
    def on_last_resort(self, fn: LastResortCallback | None) -> None:
        """Backward-compatible property setter, assigning a callable adds
        it to the subscriber set; assigning None clears the set."""
        self._breaker.on_last_resort = fn

    def add_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """Register a subscriber for last-resort-unloaded-backend events."""
        self._breaker.add_last_resort_subscriber(fn)

    def remove_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """Unregister a last-resort subscriber (no-op if absent)."""
        self._breaker.remove_last_resort_subscriber(fn)

    def set_last_resort_event_gate(self, gate: LastResortEventGate | None) -> None:
        """Install/clear the last-resort suppression gate (delegates to"""
        self._breaker.set_last_resort_event_gate(gate)

    def set_backend_disabled_event_gate(self, gate: BackendDisabledEventGate | None) -> None:
        """Install/clear the backend-disabled suppression gate (delegates"""
        self._breaker.set_backend_disabled_event_gate(gate)


# Re-export the typed contracts at module scope so existing imports
__all__ = [
    "AsrBackend",
    "AsrBackendRegistry",
    "BackendDisabledCallback",
    "ConfigProtocol",
    "LastResortCallback",
    "ProgressCallback",
]
