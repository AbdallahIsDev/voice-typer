"""AsrBackendRegistry — thin facade composing the ASR split modules.

The former 1072-line ``asr_registry.py`` is split into three
focused modules under :mod:`voice_typer.server.asr`:

- :mod:`voice_typer.server.asr.registry` — :class:`RegistryCore` (the
  base class) with backend CRUD + load/fallback orchestration +
  ``AsrBackend`` / ``ConfigProtocol`` Protocols.
- :mod:`voice_typer.server.asr.circuit_breaker` — :class:`CircuitBreaker`
  with the failure-counter / disabled-set / subscriber state.
- :mod:`voice_typer.server.asr.busy_flag` — :class:`BusyFlag` with the
  per-backend busy flag.

This module defines :class:`AsrBackendRegistry`, the public facade that
subclasses :class:`RegistryCore` and adds:

- Backward-compat wrapper methods (``_record_success``, ``_record_failure``,
  ``_is_disabled``, ``is_busy``, ``set_busy``, ``clear_busy``,
  ``busy_context`` etc.) that delegate to the composed breaker/busy
  helpers. Defining them on the facade (rather than on
  :class:`RegistryCore`) means ``patch.object(registry, "_record_success")``
  patches the instance attribute so the inherited ``load_active`` /
  ``load_with_fallback`` call sites honour the patch.
- State-exposing properties (``_disabled_backends``,
  ``_failure_counts``, ``_last_resort_notified``,
  ``_on_backend_disabled_subscribers``, ``_on_last_resort_subscribers``,
  ``_busy_backends``) that delegate to the breaker/busy helpers so
  existing tests that directly read or mutate
  ``registry._disabled_backends.add(name)`` continue to work.
- Subscriber setters (``on_backend_disabled``, ``on_last_resort``,
  ``add_backend_disabled_subscriber``, etc.).
- Backend construction (``create`` + ``_BACKEND_SPECS``) and
  ``unload`` / ``unregister`` — the remaining CRUD not assigned to
  :class:`RegistryCore` by the split.

All public API names + signatures are preserved, so every existing
``from voice_typer.server.asr_registry import AsrBackendRegistry``
import continues to work unchanged.
"""

from __future__ import annotations

import logging

from voice_typer.server.asr.circuit_breaker import (
    BackendDisabledCallback,
    LastResortCallback,
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
    """Public facade for the ASR backend registry.

    Subclasses :class:`voice_typer.server.asr.registry.RegistryCore`
    (which composes :class:`CircuitBreaker` + :class:`BusyFlag`) and
    adds the backward-compat wrapper methods, state-exposing properties,
    subscriber setters, and backend-construction CRUD that the
    split assigned to the facade.

    All public API names + signatures match the pre-split
    ``AsrBackendRegistry`` — existing callers and tests are unchanged.
    """

    # Re-exported at class scope for backward compat (tests read
    # ``registry._MAX_CONSECUTIVE_FAILURES``).
    _MAX_CONSECUTIVE_FAILURES = 3

    # Maps backend name -> (module_path, class_name) for lazy import.
    _BACKEND_SPECS: dict[str, tuple[str, str]] = {
        "whisper": ("voice_typer.server.transcription", "TranscriptionEngine"),
        "qwen": ("voice_typer.server.qwen_engine", "QwenEngine"),
        "parakeet": ("voice_typer.server.parakeet_engine", "ParakeetEngine"),
    }

    # ── load orchestration ──────────────────────────────────────────
    #
    # ``load_active`` lives on the facade (not on RegistryCore) so its
    # log calls use this module's ``log`` — which tests patch via
    # ``patch("voice_typer.server.asr_registry.log")``. The inherited
    # ``load_with_fallback`` / ``transcribe_with_fallback`` from
    # RegistryCore are not patched by any existing test, so they stay
    # on the base class.

    def load_active(self, progress_callback: ProgressCallback | None = None) -> AsrBackend | None:
        """Load the active backend and return it.

        OI-15: the circuit-breaker disabled gate is enforced BEFORE
        attempting to load. Pre-fix, ``load_active`` skipped the
        ``_is_disabled`` check: ``get_active``'s last-resort branch
        returns an unloaded backend even when that backend is in
        ``_disabled_backends``, and ``load_active`` would then attempt
        (and usually fail) the load — silently re-attempting the exact
        failure mode the breaker exists to prevent.
        """
        _cb = progress_callback or (lambda msg: None)
        # OI-15: the _is_disabled gate must come BEFORE get_active().
        if self._is_disabled(self.active_name):
            log.warning(
                "[ASR_REGISTRY] active backend %s is disabled — refusing to load (OI-15)",
                self.active_name,
            )
            return None
        backend = self.get_active()
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
            # cache failed integrity verification) — the app NEVER
            # downloads models automatically. Do NOT record a
            # circuit-breaker failure (a retry won't help and the breaker
            # would permanently disable a backend the user may download /
            # repair later). Re-raise so the caller (ModelManager) can
            # surface an actionable "open the Models page and download"
            # message.
            log.warning(
                "[ASR_REGISTRY] active backend %s refused to load: %s — "
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

    # ── backend CRUD (construction + teardown) ──────────────────────

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

        Centralizes the triplicated ``TranscriptionEngine(...)`` /
        ``QwenEngine(...)`` / ``ParakeetEngine(...)`` construction.
        Returns the constructed engine (registered in the registry) or
        None on ImportError / construction failure.
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
            # module name, e.g. "No module named 'whisper'") so a
            # support engineer can diagnose a missing-dependency failure.
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
        """Unload a backend by name, or the active backend if name is None.

        Refuses to unload a backend that is currently marked busy
        (inside :meth:`transcribe_with_fallback` / :meth:`busy_context`).
        Raises :class:`RuntimeError` so the caller can catch and defer /
        log. This closes the TOCTOU window where
        :meth:`transcribe_with_fallback` captures the backend under
        ``self._lock`` and then invokes it *outside* the lock — without
        this guard a concurrent ``unload()`` would tear down the backend
        (free CUDA tensors / ctranslate2 handle) while a transcription
        is mid-flight, crashing the C-level call with a use-after-free.

        Callers SHOULD wrap this call in ``try/except RuntimeError`` and
        log/defer rather than letting the exception propagate.
        """
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
                # log — a backend.unload() failure usually means a CUDA
                # context tear-down or torch-tensor free raised.
                log.exception("[ASR_REGISTRY] failed to unload %s", target)

    # ── circuit-breaker wrapper methods ─────────────────────────────
    #
    # These delegate to ``self._breaker`` (composed in RegistryCore).
    # Defining them on the facade (rather than on RegistryCore) means
    # ``patch.object(registry, "_record_success")`` patches the instance
    # attribute so the inherited ``load_active`` / ``load_with_fallback``
    # call sites honour the patch.

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

    # ── busy-flag wrapper methods ───────────────────────────────────
    #
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

    # ── circuit-breaker state-exposing properties ──────────────────
    #
    # These expose the breaker's internal state as attributes on the
    # facade so existing tests that read ``registry._disabled_backends``
    # or mutate ``registry._failure_counts["parakeet"] = 1`` continue to
    # work. Each property returns the breaker's actual state object
    # (set / dict / bool) so in-place mutations land on the real state.

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

    # ── subscriber management ───────────────────────────────────────
    #
    # Backward-compatible properties so existing
    # ``registry.on_backend_disabled = fn`` assignments continue to work
    # (the callable is added to the subscriber set rather than replacing
    # it).

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
        """Backward-compatible property setter — assigning a callable adds
        it to the subscriber set; assigning None clears the set."""
        self._breaker.on_last_resort = fn

    def add_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """Register a subscriber for last-resort-unloaded-backend events."""
        self._breaker.add_last_resort_subscriber(fn)

    def remove_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """Unregister a last-resort subscriber (no-op if absent)."""
        self._breaker.remove_last_resort_subscriber(fn)


# Re-export the typed contracts at module scope so existing imports
# ``from voice_typer.server.asr_registry import AsrBackend`` /
# ``ConfigProtocol`` / ``ProgressCallback`` continue to work.
__all__ = [
    "AsrBackend",
    "AsrBackendRegistry",
    "BackendDisabledCallback",
    "ConfigProtocol",
    "LastResortCallback",
    "ProgressCallback",
]
