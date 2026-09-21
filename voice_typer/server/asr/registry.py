"""ASR engine registry helpers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

from voice_typer.server._timeout_utils import TIMEOUT, _run_with_timeout
from voice_typer.server.asr.busy_flag import BusyFlag
from voice_typer.server.asr.circuit_breaker import CircuitBreaker
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError

log = logging.getLogger(__name__)

# Typed contracts for the registry.
ProgressCallback = Callable[[str], None]

# Hard ceiling for a single model-load call. The documented window is
MODEL_LOAD_TIMEOUT_SECONDS = 120


@runtime_checkable
class AsrBackend(Protocol):
    """layer rely on, and makes pyrefly flag a backend that forgets one."""

    is_loaded: bool

    @property
    def device_info(self) -> str:
        """Human-readable device description for tray status lines."""
        ...

    @property
    def loaded_via(self) -> str:
        """How the model was loaded (device/compute-type combo)."""
        ...

    def load(self, *, progress_callback: ProgressCallback | None = ...) -> None:
        """Load the model into memory. Idempotent if already loaded."""
        ...

    def unload(self) -> None:
        """Release the model and any partially-allocated resources."""
        ...

    def request_abort(self) -> None:
        """Signal an in-flight transcription to abort as soon as possible."""
        ...

    def clear_abort(self) -> None:
        """Clear a stale abort token at the start of a fresh transcription cycle."""
        ...

    def transcribe_with_fallback(self, audio: np.ndarray, *args: object, **kwargs: object) -> str:
        """Transcribe ``audio`` (float PCM samples) to text (possibly empty)."""
        ...


@runtime_checkable
class ConfigProtocol(Protocol):
    """Structural contract for the Config object passed to the registry."""

    asr_backend: str
    model_size: str
    device: str
    language: str
    beam_size: int
    best_of: int
    condition_on_previous_text: bool
    disabled_backends: list[str]


class RegistryCore:
    """Core ASR backend registry, backend CRUD + load/fallback orchestration."""

    def __init__(self, config: ConfigProtocol) -> None:
        self._config: ConfigProtocol = config
        self._backends: dict[str, AsrBackend] = {}
        self._lock = threading.RLock()
        # Compose the circuit breaker + busy flag with the shared lock
        self._breaker = CircuitBreaker(config, self._lock)
        self._busy = BusyFlag(self._lock, lambda: self.active_name)

    def register(self, name: str, backend: AsrBackend) -> None:
        """Register a backend by name (e.g. 'whisper', 'qwen', 'parakeet')."""
        with self._lock:
            self._backends[name] = backend
        log.debug("[ASR_REGISTRY] registered backend: %s (loaded=%s)", name, getattr(backend, "is_loaded", True))

    def get(self, name: str | None) -> AsrBackend | None:
        """Get a specific backend by name."""
        with self._lock:
            return self._backends.get(name)

    @property
    def active_name(self) -> str:
        """Return the name of the active backend."""
        return getattr(self._config, "asr_backend", "whisper")

    @property
    def available_backends(self) -> list[str]:
        """Return names of all registered backends."""
        with self._lock:
            return list(self._backends.keys())

    def _is_ready(self, backend: AsrBackend) -> bool:
        """Check if a backend is ready for transcription."""
        return getattr(backend, "is_loaded", True)

    def get_active(self) -> AsrBackend | None:
        """Return the ready configured backend, else whisper, else None"""
        name = getattr(self._config, "asr_backend", "whisper")
        notify_last_resort = False
        try:
            with self._lock:
                backend = self._backends.get(name)
                if backend is not None and self._is_ready(backend):
                    self._breaker.clear_last_resort_notified()
                    return backend

                whisper = self._backends.get("whisper")
                if whisper is not None and self._is_ready(whisper):
                    if name != "whisper":
                        log.info("[ASR_REGISTRY] %s backend not ready, falling back to whisper", name)
                    self._breaker.clear_last_resort_notified()
                    return whisper

                for b in list(self._backends.values()):
                    if b is not None:
                        if not self._is_ready(b):
                            # Fail-loud: None so callers take not-ready
                            first = self._breaker.should_notify_last_resort()
                            if first:
                                notify_last_resort = True
                                log.warning(
                                    "[ASR_REGISTRY] no loaded backend available "
                                    "(last-resort %s is_loaded=False), "
                                    "returning None, transcription not attempted",
                                    name,
                                )
                            else:
                                log.debug(
                                    "[ASR_REGISTRY] unloaded backend %s last-resort (repeat)",
                                    name,
                                )
                            return None
                        return b
            return None
        finally:
            # Fire subscribers OUTSIDE the lock so a subscriber callback
            if notify_last_resort:
                self._breaker.fire_last_resort_subscribers(name)

    # ``load_active`` lives on the facade (patched via

    def _load_with_timeout(
        self,
        backend: AsrBackend,
        label: str,
        progress_callback: ProgressCallback,
    ) -> AsrBackend | None:
        """Run ``backend.load`` under the ``MODEL_LOAD_TIMEOUT_SECONDS``"""
        result = _run_with_timeout(
            f"{label}.load",
            lambda: backend.load(progress_callback=progress_callback),
            timeout=MODEL_LOAD_TIMEOUT_SECONDS,
        )
        if result is not TIMEOUT:
            return backend
        log.warning(
            "[ASR_REGISTRY] %s load timed out after %ds, trying fallback",
            label,
            MODEL_LOAD_TIMEOUT_SECONDS,
        )
        try:
            backend.unload()
            log.info("[ASR_REGISTRY] unloaded timed-out backend: %s", label)
        except Exception:
            log.exception("[ASR_REGISTRY] failed to unload %s after load timeout", label)
        return None

    def load_with_fallback(self, progress_callback: ProgressCallback | None = None) -> AsrBackend | None:
        """Load the configured backend; on failure, fall back to whisper."""
        _cb = progress_callback or (lambda msg: None)
        name = self.active_name

        # Skip disabled backends, go straight to whisper fallback.
        if self._is_disabled(name):
            log.info(
                "[ASR_REGISTRY] backend %s is disabled (circuit breaker), skipping to whisper fallback",
                name,
            )
        else:
            with self._lock:
                backend = self._backends.get(name)
            if backend is not None:
                try:
                    if self._load_with_timeout(backend, name, _cb) is not None:
                        log.info("[ASR_REGISTRY] loaded backend: %s", name)
                        self._record_success(name)
                        return backend
                    # TIMEOUT, helper already unloaded; fall through.
                except (ModelNotDownloadedError, ModelIntegrityError) as exc:
                    # Not a transient failure, the user hasn't downloaded
                    log.warning(
                        "[ASR_REGISTRY] %s backend refused to load: %s, "
                        "model not downloaded / integrity check failed. "
                        "No circuit-breaker record, no whisper fallback.",
                        name,
                        exc,
                    )
                    raise
                except Exception as exc:
                    # ``exc_info=True`` so the full traceback is captured.
                    log.warning(
                        "[ASR_REGISTRY] failed to load %s: %s, trying fallback",
                        name,
                        exc,
                        exc_info=True,
                    )
                    self._record_failure(name)
                    try:
                        backend.unload()
                        log.info("[ASR_REGISTRY] unloaded failed backend: %s", name)
                    except Exception:
                        log.exception(
                            "[ASR_REGISTRY] failed to unload %s after load failure",
                            name,
                        )
                    # Do NOT unregister. Keep it in _backends so

        # If the primary IS whisper and it failed, no separate fallback.
        if name == "whisper":
            return None

        # Fallback to whisper.
        with self._lock:
            whisper = self._backends.get("whisper")
        if whisper is None:
            # Cold boot with a non-whisper primary, construct whisper
            fallback_size = getattr(self._config, "model_size", "")
            log.info(
                "[ASR_REGISTRY] whisper backend not registered, constructing with "
                "configured model_size=%r for fallback",
                fallback_size,
            )
            whisper = self.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size=fallback_size,
                    device=self._config.device,
                    language=self._config.language,
                    beam_size=self._config.beam_size,
                    best_of=self._config.best_of,
                    condition_on_previous_text=self._config.condition_on_previous_text,
                    config=self._config,
                ),
            )
        if whisper is not None:
            try:
                if self._load_with_timeout(whisper, "whisper fallback", _cb) is not None:
                    log.info("[ASR_REGISTRY] loaded fallback backend: whisper")
                    # Do NOT call _record_success("whisper"), whisper is
                    self._breaker.clear_last_resort_notified()
                    return whisper
                # TIMEOUT, helper already unloaded; fall through.
            except Exception:
                log.exception("[ASR_REGISTRY] whisper fallback also failed")
                # Do NOT call _record_failure("whisper"), the breaker
                try:
                    whisper.unload()
                    log.info("[ASR_REGISTRY] unloaded failed fallback backend: whisper")
                except Exception:
                    log.exception(
                        "[ASR_REGISTRY] failed to unload whisper after load failure",
                    )

        return None

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        *args: object,
        name: str | None = None,
        **kwargs: object,
    ) -> str:
        """Wrap the backend's ``transcribe_with_fallback`` with the busy flag."""
        target = name if name is not None else self.active_name
        with self._lock:
            backend = self._backends.get(target) if target else None
        if backend is None:
            log.warning(
                "[ASR_REGISTRY] transcribe_with_fallback: no backend registered for name=%r, returning empty string",
                target,
            )
            return ""
        with self.busy_context(target):
            return backend.transcribe_with_fallback(audio, *args, **kwargs)
