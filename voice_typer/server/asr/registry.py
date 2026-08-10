"""Core ASR backend registry — typed contracts + backend CRUD + load fallback.

Extracted from the former monolithic ``asr_registry.py``. Owns
the ``_backends`` dict, the backend-construction spec table, and the
load/fallback orchestration (``load_active`` / ``load_with_fallback`` /
``transcribe_with_fallback`` / ``unload``).

Composes :class:`voice_typer.server.asr.circuit_breaker.CircuitBreaker`
(failure-counter + disabled-set + subscribers) and
:class:`voice_typer.server.asr.busy_flag.BusyFlag` (per-backend busy
flag) — both constructed with the shared ``self._lock`` so registry +
breaker + busy operations are mutually atomic.

The public :class:`voice_typer.server.asr_registry.AsrBackendRegistry`
subclass adds the backward-compat wrapper methods (``_record_success``
etc.) and state-exposing properties that delegate to the breaker/busy
helpers, so existing tests that ``patch.object(registry,
"_record_success")`` or read ``registry._failure_counts`` continue to
work unchanged.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

from voice_typer.server.asr.busy_flag import BusyFlag
from voice_typer.server.asr.circuit_breaker import CircuitBreaker
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError

log = logging.getLogger(__name__)

# Typed contracts for the registry.
ProgressCallback = Callable[[str], None]


@runtime_checkable
class AsrBackend(Protocol):
    """Structural contract for an ASR backend registered with the registry.

    A ``Protocol`` (not an ABC) so the four real backends do NOT need
    to inherit from a common base class — duck typing is preserved.
    ``@runtime_checkable`` so tests can assert ``isinstance(obj, AsrBackend)``.

    This is a STATIC type-check contract, not a runtime contract: the
    registry's ``register`` / ``get`` accept any object at runtime and
    the pipeline guards optional members (``request_abort`` /
    ``clear_abort``) with ``hasattr`` before calling them (see
    ``dictation_pipeline/orchestrator.py`` + ``transcribe_step.py``).
    The Protocol exists so a future engine author reading the registry
    knows exactly which members the registry and IPC layer rely on —
    and so pyrefly flags a backend that forgets one.
    """

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
        """Transcribe ``audio`` (a float array of PCM samples) and return
        the text (possibly empty). All four concrete engines accept
        ``np.ndarray`` — ``bytes`` was a Protocol bug that would type-
        check but crash at runtime."""
        ...


@runtime_checkable
class ConfigProtocol(Protocol):
    """Structural contract for the Config object passed to the registry.

    ``disabled_backends`` is declared on the Protocol AND on the real
    ``Config`` dataclass. The runtime code path in :meth:`RegistryCore.__init__`
    still falls back to ``getattr(config, "disabled_backends", None)`` so
    legacy configs constructed without the field continue to work.
    """

    asr_backend: str
    model_size: str
    device: str
    language: str
    beam_size: int
    best_of: int
    condition_on_previous_text: bool
    disabled_backends: list[str]


class RegistryCore:
    """Core ASR backend registry — backend CRUD + load/fallback orchestration.

    The ``_backends`` dict is guarded by ``self._lock`` (a reentrant
    lock). ``register`` / ``unregister`` / ``get`` / ``load_with_fallback``
    acquire the lock around their dict operations only — the actual
    ``backend.load(...)`` call is left OUTSIDE the lock so a slow GPU/disk
    load doesn't block other readers.

    Composes :class:`CircuitBreaker` and :class:`BusyFlag` (both
    constructed with ``self._lock``). The
    :class:`voice_typer.server.asr_registry.AsrBackendRegistry` subclass
    provides the backward-compat wrapper methods (``_record_success``,
    ``_record_failure``, ``_is_disabled``, ``is_busy``, ``set_busy``,
    ``clear_busy``, ``busy_context`` etc.) that these methods call via
    ``self.`` — Python's MRO resolves them on the subclass instance at
    runtime, and ``patch.object(registry, "_record_success")`` patches
    the instance attribute so the call sites honour the patch.
    """

    def __init__(self, config: ConfigProtocol) -> None:
        self._config: ConfigProtocol = config
        self._backends: dict[str, AsrBackend] = {}
        self._lock = threading.RLock()
        # Compose the circuit breaker + busy flag with the shared lock
        # so registry + breaker + busy operations are mutually atomic.
        self._breaker = CircuitBreaker(config, self._lock)
        self._busy = BusyFlag(self._lock, lambda: self.active_name)

    # ── backend CRUD ────────────────────────────────────────────────

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
        """Return the currently active backend based on ``config.asr_backend``.

        Falls back to 'whisper' if the configured backend isn't loaded.
        Returns None if no backend is available.

        When this method falls through to the last-resort branch (no
        ready backend) and returns an *unloaded* backend, a one-shot
        tray notification is fired via the ``on_last_resort`` subscriber
        set + an ``asr_last_resort_unloaded`` event is published on the
        global ``event_bus``. The latch ensures the notification fires
        only ONCE per last-resort transition (not on every ``get_active``
        call) and resets when a ready backend becomes available again so
        a recovery → re-fallback sequence re-notifies the user.
        """
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
                            log.warning(
                                "[ASR_REGISTRY] returning unloaded backend %s "
                                "(is_loaded=False) as last-resort active — "
                                "transcription may return empty silently",
                                name,
                            )
                            if self._breaker.should_notify_last_resort():
                                notify_last_resort = True
                        return b
            return None
        finally:
            # Fire subscribers OUTSIDE the lock so a subscriber callback
            # can safely re-enter the registry without deadlock.
            if notify_last_resort:
                self._breaker.fire_last_resort_subscribers(name)

    # ── load orchestration ──────────────────────────────────────────
    #
    # ``load_active`` is intentionally NOT defined here — it lives on the
    # :class:`voice_typer.server.asr_registry.AsrBackendRegistry` facade
    # so its log calls use the facade module's ``log`` (which tests patch
    # via ``patch("voice_typer.server.asr_registry.log")``). The
    # ``load_with_fallback`` and ``transcribe_with_fallback`` methods
    # below DO live here — their log calls are not directly patched by
    # any existing test, and keeping them in the core minimises the
    # facade's surface area.

    def load_with_fallback(self, progress_callback: ProgressCallback | None = None) -> AsrBackend | None:
        """Load the configured backend; on failure, fall back to whisper.

        On primary failure, the failed backend's ``unload()`` is called
        so partially-allocated resources (torch tensors, CUDA contexts)
        are released. The actual ``backend.load()`` call is OUTSIDE the
        lock so a slow GPU/disk load doesn't block other readers.

        Circuit breaker: each PRIMARY-backend load failure increments a
        per-backend counter; on success the counter is reset. After
        ``_MAX_CONSECUTIVE_FAILURES`` (3) consecutive failures, the
        primary backend is added to ``_disabled_backends`` and skipped
        on subsequent calls — we go straight to the whisper fallback.

        F-09: the failed primary stays registered (only ``unload()`` is
        called) so subsequent calls retry it and the failure counter
        can trip the breaker.
        """
        _cb = progress_callback or (lambda msg: None)
        name = self.active_name

        # Skip disabled backends — go straight to whisper fallback.
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
                    # OUTSIDE lock: a model load can take 5-50s.
                    backend.load(progress_callback=_cb)
                    log.info("[ASR_REGISTRY] loaded backend: %s", name)
                    self._record_success(name)
                    return backend
                except (ModelNotDownloadedError, ModelIntegrityError) as exc:
                    # Not a transient failure: the user hasn't downloaded
                    # the model (or the cached files failed integrity
                    # verification) — the app NEVER downloads models
                    # automatically. Do NOT record a circuit-breaker
                    # failure (a retry won't help, and the breaker would
                    # permanently disable a backend the user may download
                    # or repair later) and do NOT fall back to whisper
                    # (silently switching backends would hide the missing
                    # download from the user, who explicitly chose this
                    # backend). Re-raise so the caller (ModelManager)
                    # can surface an actionable "open the Models page and
                    # download" message.
                    log.warning(
                        "[ASR_REGISTRY] %s backend refused to load: %s — "
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
                    # F-09: do NOT unregister — keep it in _backends so
                    # subsequent calls retry (and increment the failure
                    # counter toward the disable threshold).

        # If the primary IS whisper and it failed, no separate fallback.
        if name == "whisper":
            return None

        # Fallback to whisper.
        with self._lock:
            whisper = self._backends.get("whisper")
        if whisper is None:
            # Cold boot with a non-whisper primary — construct whisper
            # with tiny.en so the fallback has something to load.
            log.info("[ASR_REGISTRY] whisper backend not registered — constructing with tiny.en for fallback")
            whisper = self.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size="tiny.en",
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
                whisper.load(progress_callback=_cb)
                log.info("[ASR_REGISTRY] loaded fallback backend: whisper")
                # Do NOT call _record_success("whisper") — whisper is a
                # FALLBACK, not the user's configured backend. But DO
                # clear the last-resort latch so a future fall-through
                # re-notifies.
                self._breaker.clear_last_resort_notified()
                return whisper
            except Exception:
                log.exception("[ASR_REGISTRY] whisper fallback also failed")
                # Do NOT call _record_failure("whisper") — the breaker
                # tracks the user's configured backend, not the fallback.
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
        audio: bytes,
        *args: object,
        name: str | None = None,
        **kwargs: object,
    ) -> str:
        """Wrap the backend's ``transcribe_with_fallback`` with the busy flag.

        Callers SHOULD call ``registry.transcribe_with_fallback(audio, ...)``
        instead of ``active.transcribe_with_fallback(audio, ...)`` so the
        per-backend busy flag is set/cleared atomically and
        :meth:`ModelManager.ensure_active_engine_loaded` can reject new
        dictation requests when the active backend is stuck in a C-level
        ctranslate2 call.

        The ``name`` keyword selects the backend (defaults to the active
        backend). All other args/kwargs are forwarded unchanged. Returns
        the transcript text (possibly empty). If the named backend is
        not registered, logs a warning and returns "" (matching
        :meth:`get_active`'s last-resort silent-empty contract).

        Exceptions from the backend's ``transcribe_with_fallback``
        propagate to the caller after the busy flag is cleared.
        """
        target = name if name is not None else self.active_name
        with self._lock:
            backend = self._backends.get(target) if target else None
        if backend is None:
            log.warning(
                "[ASR_REGISTRY] transcribe_with_fallback: no backend registered for name=%r — returning empty string",
                target,
            )
            return ""
        with self.busy_context(target):
            return backend.transcribe_with_fallback(audio, *args, **kwargs)
