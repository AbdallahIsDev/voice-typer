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

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


# Typed contracts for the registry.
ProgressCallback = Callable[[str], None]


@runtime_checkable
class AsrBackend(Protocol):
    """Structural contract for an ASR backend registered with
    :class:`AsrBackendRegistry`.

    A ``Protocol`` (not an ABC) so the three real backends do NOT need
    to inherit from a common base class — duck typing is preserved.
    Type-checkers can now verify that ``backend.load(...)``,
    ``backend.unload()``, ``backend.is_loaded`` exist before they are
    called at runtime.

    A backend that renames ``is_loaded`` -> ``loaded`` (or drops
    ``unload()``) now fails type-check at static-analysis time instead
    of failing at runtime mid-dictation.

    ``@runtime_checkable`` so tests can assert ``isinstance(obj, AsrBackend)``.
    """

    is_loaded: bool

    def load(self, *, progress_callback: ProgressCallback | None = ...) -> None:
        """Load the model into memory. Idempotent if already loaded."""
        ...

    def unload(self) -> None:
        """Release the model and any partially-allocated resources."""
        ...

    def transcribe_with_fallback(self, audio: bytes, *args: object, **kwargs: object) -> str:
        """Transcribe ``audio`` and return the text (possibly empty)."""
        ...


@runtime_checkable
class ConfigProtocol(Protocol):
    """Structural contract for the Config object passed to
    :class:`AsrBackendRegistry`.

    The real ``Config`` dataclass declares ``asr_backend``, ``device``,
    ``language``, ``beam_size``, ``best_of``, ``condition_on_previous_text``
    as required fields — all of which the registry reads when
    constructing the whisper fallback.

    ``disabled_backends`` is declared on the Protocol AND on the real
    ``Config`` dataclass (XZ-CFG-01). The runtime code path in
    :meth:`AsrBackendRegistry.__init__` still falls back to
    ``getattr(config, "disabled_backends", None)`` so legacy configs
    constructed without the field (e.g. test stubs that build a Config
    via ``__new__``) continue to work.
    """

    asr_backend: str
    model_size: str
    device: str
    language: str
    beam_size: int
    best_of: int
    condition_on_previous_text: bool
    # ``disabled_backends`` is optional at runtime (legacy configs).
    disabled_backends: list[str]


# Subscriber set for backend-disabled events.
# Pre-fix, ``on_backend_disabled`` was a single callback attribute.
# Only ONE subscriber could be notified when the circuit breaker
# tripped. The set-based subscriber list lets ModelManager (tray), the
# IPC layer (renderer event), and a telemetry sink subscribe
# independently without overwriting each other.
BackendDisabledCallback = Callable[[str, int], None]

# XZ-14-06: subscriber callback for the last-resort unloaded-backend
# fallback path in get_active(). Pre-fix, that path logged a WARNING
# ("returning unloaded backend %s (is_loaded=False) as last-resort
# active — transcription may return empty silently") but fired no
# tray notification — the user silently got empty transcriptions with
# no visible feedback that voice recognition wasn't working. The
# callback receives the configured backend name (the same value
# passed to the WARNING log) so the tray can render a useful message
# (e.g. "Voice Typer: Active backend '<name>' is not loaded —
# transcription may be unavailable. Check your model settings.").
LastResortCallback = Callable[[str], None]


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

    # After this many consecutive load failures, a backend is
    # marked "disabled" — subsequent ``load_with_fallback`` calls skip it
    # and fall straight through to the whisper fallback.
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, config: ConfigProtocol):
        self._config: ConfigProtocol = config
        self._backends: dict[str, AsrBackend] = {}
        self._lock = threading.RLock()
        self._failure_counts: dict[str, int] = {}
        self._disabled_backends: set[str] = set()
        # XZ-CFG-01: ``Config`` now declares ``disabled_backends`` as a
        # real dataclass field (default empty list). The ``getattr``
        # fallback is retained so test stubs / legacy Config objects
        # constructed via ``__new__`` (which skip ``__init__``) keep
        # working — the field is absent on those bare instances.
        persisted = getattr(config, "disabled_backends", None) or []
        try:
            self._disabled_backends = set(persisted)
        except TypeError:
            # XE-14-F: previously the TypeError was silently swallowed
            # (the ``except`` block just reset to an empty set with no
            # log). A misconfigured ``disabled_backends`` (e.g. a string
            # instead of a list — ``set("whisper")`` produces
            # ``{"w", "h", "i", "s", "p", "e", "r"}`` rather than
            # raising) would silently clear the persisted disabled set,
            # re-enabling a backend the user explicitly disabled. Log
            # at WARNING so the misconfiguration is visible in
            # diagnostics exports without crashing the app.
            log.warning(
                "[ASR_REGISTRY] config.disabled_backends is not iterable (%r); ignoring persisted disabled set",
                persisted,
            )
            self._disabled_backends = set()
        # Backend-disabled subscribers. Pre-fix this was a
        # single ``on_backend_disabled: Any | None = None`` attribute.
        # The ``on_backend_disabled`` property below preserves the
        # legacy ``registry.on_backend_disabled = fn`` assignment pattern
        # by adding ``fn`` to the subscriber set.
        self._on_backend_disabled_subscribers: set[BackendDisabledCallback] = set()
        # XZ-14-06: subscribers for the last-resort unloaded-backend
        # event in get_active(). Same set-based pattern as
        # _on_backend_disabled_subscribers so ModelManager (tray), the
        # IPC layer (renderer event), and a telemetry sink can subscribe
        # independently without overwriting each other.
        self._on_last_resort_subscribers: set[LastResortCallback] = set()
        # XZ-14-06: one-shot latch so we don't fire the tray notification
        # on every get_active() call while the registry is stuck in the
        # last-resort state. Reset to False whenever get_active() finds a
        # ready backend (so a recovery → re-fallback sequence re-notifies)
        # and in _record_success (primary-backend load success).
        self._last_resort_notified: bool = False

        # UE-48: per-backend "busy" flag. Set when a backend enters
        # ``transcribe_with_fallback`` (via the registry's wrapper or the
        # ``busy_context`` context manager), cleared on exit (including
        # the exception path). Used by
        # :meth:`ModelManager.ensure_active_engine_loaded` to reject new
        # dictation requests when the active backend is stuck in a
        # C-level ctranslate2 call (which can hold GPU + GIL for 5-30
        # min). The flag is guarded by ``self._lock`` — the transcribe
        # thread sets it (via the wrapper) and the IPC thread reads it
        # (via :meth:`is_busy`), so the read/write pair must be
        # atomic. The set is keyed by backend NAME (not the backend
        # object) so a backend that was unregistered + re-registered
        # under the same name (e.g. via ``change_model``) doesn't carry
        # over a stale busy state.
        self._busy_backends: set[str] = set()

    # Backward-compatible property so existing
    # ``registry.on_backend_disabled = fn`` assignments continue to
    # work (the lambda is added to the subscriber set rather than
    # replacing it).
    @property
    def on_backend_disabled(self) -> set[BackendDisabledCallback]:
        return self._on_backend_disabled_subscribers

    @on_backend_disabled.setter
    def on_backend_disabled(self, fn: BackendDisabledCallback | None) -> None:
        if fn is None:
            self._on_backend_disabled_subscribers.clear()
        elif callable(fn):
            self._on_backend_disabled_subscribers.add(fn)

    def add_backend_disabled_subscriber(self, fn: BackendDisabledCallback) -> None:
        """Register a subscriber for backend-disabled events."""
        if callable(fn):
            self._on_backend_disabled_subscribers.add(fn)

    def remove_backend_disabled_subscriber(self, fn: BackendDisabledCallback) -> None:
        """Unregister a backend-disabled subscriber (no-op if absent)."""
        self._on_backend_disabled_subscribers.discard(fn)

    # XZ-14-06: last-resort subscriber management. Mirrors the
    # backend-disabled subscriber API so the app can wire a tray
    # notification via the same path used for load_with_fallback
    # failures (see _record_failure's subscriber loop + event_bus.publish).
    @property
    def on_last_resort(self) -> set[LastResortCallback]:
        """XZ-14-06: set of subscribers fired when get_active() falls
        through to an unloaded last-resort backend."""
        return self._on_last_resort_subscribers

    @on_last_resort.setter
    def on_last_resort(self, fn: LastResortCallback | None) -> None:
        """XZ-14-06: backward-compatible property setter mirroring
        ``on_backend_disabled`` — assigning a callable adds it to the
        subscriber set; assigning None clears the set."""
        if fn is None:
            self._on_last_resort_subscribers.clear()
        elif callable(fn):
            self._on_last_resort_subscribers.add(fn)

    def add_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """XZ-14-06: register a subscriber for last-resort-unloaded-backend events."""
        if callable(fn):
            self._on_last_resort_subscribers.add(fn)

    def remove_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """XZ-14-06: unregister a last-resort subscriber (no-op if absent)."""
        self._on_last_resort_subscribers.discard(fn)

    def register(self, name: str, backend: AsrBackend) -> None:
        """Register a backend by name (e.g. 'whisper', 'qwen', 'parakeet')."""
        with self._lock:
            self._backends[name] = backend
        log.debug("[ASR_REGISTRY] registered backend: %s (loaded=%s)", name, getattr(backend, "is_loaded", True))

    def unregister(self, name: str) -> None:
        """Unregister a backend by name."""
        with self._lock:
            if name in self._backends:
                del self._backends[name]
                log.debug("[ASR_REGISTRY] unregistered backend: %s", name)

    def get_active(self) -> AsrBackend | None:
        """Return the currently active backend based on config.asr_backend.

        Falls back to 'whisper' if the configured backend isn't loaded.
        Returns None if no backend is available.

        XZ-14-06: when this method falls through to the last-resort
        branch (no ready backend) and returns an *unloaded* backend, a
        one-shot tray notification is fired via the
        ``_on_last_resort_subscribers`` set + an ``asr_last_resort_unloaded``
        event is published on the global ``event_bus``. The latch
        (``_last_resort_notified``) ensures the notification fires only
        ONCE per last-resort transition (not on every get_active() call)
        and resets when a ready backend becomes available again so a
        recovery → re-fallback sequence re-notifies the user.
        """
        name = getattr(self._config, "asr_backend", "whisper")
        notify_last_resort = False
        try:
            with self._lock:
                backend = self._backends.get(name)
                if backend is not None and self._is_ready(backend):
                    # XZ-14-06: a ready configured backend is available —
                    # clear the last-resort latch so a future fall-through
                    # re-notifies.
                    self._last_resort_notified = False
                    return backend

                whisper = self._backends.get("whisper")
                if whisper is not None and self._is_ready(whisper):
                    if name != "whisper":
                        log.info("[ASR_REGISTRY] %s backend not ready, falling back to whisper", name)
                    # XZ-14-06: whisper is ready — clear the last-resort latch.
                    self._last_resort_notified = False
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
                            # XZ-14-06: fire one-shot tray notification
                            # so the user knows voice transcription is
                            # silently broken. The latch ensures we only
                            # fire once per last-resort transition.
                            if not self._last_resort_notified:
                                self._last_resort_notified = True
                                notify_last_resort = True
                        return b
            return None
        finally:
            # XZ-14-06: fire subscribers OUTSIDE the lock (the `with`
            # block's __exit__ has already released it by the time
            # `finally` runs) so a subscriber callback can safely
            # re-enter the registry (e.g. to query active_name) without
            # deadlock. Mirrors the `_record_failure` subscriber pattern.
            if notify_last_resort:
                self._fire_last_resort_subscribers(name)

    def _fire_last_resort_subscribers(self, name: str) -> None:
        """XZ-14-06: fire per-registry subscribers + publish an event_bus
        event for the last-resort unloaded-backend fallback.

        Called from :meth:`get_active`'s ``finally`` block, AFTER the
        ``_last_resort_notified`` latch has been set under the lock.
        Snapshotting the subscribers under the lock + firing them outside
        mirrors the :meth:`_record_failure` pattern — a subscriber that
        raises is logged and skipped so one buggy subscriber doesn't
        block the others.
        """
        # Snapshot subscribers under the lock so a subscriber that calls
        # remove_last_resort_subscriber from within its own callback
        # doesn't mutate the set we're iterating.
        with self._lock:
            subscribers = list(self._on_last_resort_subscribers)

        # Fire per-registry subscribers (tray notification, IPC push,
        # telemetry sink, …). Defensive — a subscriber that raises is
        # logged and skipped so one buggy subscriber doesn't block the
        # others (same contract as _record_failure's subscriber loop).
        for fn in subscribers:
            try:
                fn(name)
            except Exception:
                log.warning(
                    "[ASR_REGISTRY] on_last_resort subscriber raised",
                    exc_info=True,
                )

        # XZ-14-06: publish process-wide event on event_bus so the IPC
        # push channel and any diagnostics aggregator are notified
        # independently of the per-registry subscribers (mirrors the
        # asr_backend_disabled event published from _record_failure).
        # DT-16: payload fields wrapped under the canonical ``data`` key
        # (matching every other event_bus.publish caller) so the Rust WS
        # reader + usePythonEvent forwarding actually surface them.
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "asr_last_resort_unloaded",
                    "data": {
                        "backend": name,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
        except Exception:
            log.warning(
                "[ASR_REGISTRY] failed to publish asr_last_resort_unloaded event",
                exc_info=True,
            )

    def _is_ready(self, backend: AsrBackend) -> bool:
        """Check if a backend is ready for transcription."""
        is_loaded = getattr(backend, "is_loaded", True)
        return is_loaded

    @property
    def active_name(self) -> str:
        """Return the name of the active backend."""
        return getattr(self._config, "asr_backend", "whisper")

    def get(self, name: str) -> AsrBackend | None:
        """Get a specific backend by name."""
        with self._lock:
            return self._backends.get(name)

    # ── ARCH-007/008: registry convenience methods ────────────────

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
    ) -> AsrBackend | None:
        """ARCH-007: Construct (but don't load) a backend engine.

        Centralizes the triplicated TranscriptionEngine(...) /
        QwenEngine(...) / ParakeetEngine(...) construction.

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
            log.info(
                "[ASR_REGISTRY] created %s backend (%s), registered",
                name,
                class_name,
            )
            return engine
        except ImportError as exc:
            # XE-14-E: previously the ImportError message was dropped
            # (the log line said "package not installed, unavailable"
            # with no detail). The ImportError's str() carries the
            # missing module name (e.g. "No module named 'whisper'")
            # which is the single piece of information a support
            # engineer needs to diagnose a missing-dependency ASR
            # failure. Forward it to the log.
            log.warning(
                "[ASR_REGISTRY] %s backend package not installed (%s), unavailable",
                name,
                exc,
                exc_info=True,
            )
            return None
        except Exception as exc:
            # CR-91 / S5-CR-41: use ``log.exception`` so the full
            # traceback is captured (mechanical pass replacing
            # ``log.error("...: %s", exc, exc_info=True)`` with the
            # idiomatic ``log.exception("...")`` form — same behaviour,
            # clearer intent, harder to drop the ``exc_info`` arg in a
            # future refactor).
            log.exception(
                "[ASR_REGISTRY] failed to initialise %s backend: %s",
                name,
                exc,
            )
            return None

    def load_active(self, progress_callback: ProgressCallback | None = None) -> AsrBackend | None:
        """Load the active backend and return it."""
        _cb = progress_callback or (lambda msg: None)
        backend = self.get_active()
        if backend is None:
            log.warning("[ASR_REGISTRY] no active backend to load")
            return None
        try:
            backend.load(progress_callback=_cb)
            log.info("[ASR_REGISTRY] loaded active backend: %s", self.active_name)
            self._record_success(self.active_name)
            return backend
        except Exception as exc:
            log.exception("[ASR_REGISTRY] failed to load active backend %s: %s", self.active_name, exc)
            self._record_failure(self.active_name)
            # XE-14-D: previously the unload error was silently
            # suppressed via ``contextlib.suppress(Exception)``. A
            # partially-loaded backend that fails to unload leaks GPU
            # memory / CUDA contexts / file handles — and the silent
            # suppress meant the leak was invisible in diagnostics
            # exports. Mirror the load_with_fallback pattern: try /
            # except / log so the unload failure is visible at WARNING
            # level without preventing the load_active caller from
            # receiving the None return.
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

    # ── Circuit-breaker helpers ───────────────────────────────────

    def _is_disabled(self, name: str) -> bool:
        """Return True if ``name`` is in the disabled-backends set."""
        with self._lock:
            return name in self._disabled_backends

    def failure_count(self, name: str) -> int:
        """Return the current consecutive-failure count for ``name``."""
        with self._lock:
            return self._failure_counts.get(name, 0)

    def reset_failures(self, name: str) -> None:
        """Clear the failure counter and disabled state for ``name``."""
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
            # XZ-14-06: clear the last-resort notification latch — a
            # successful primary-backend load means we've recovered
            # from the last-resort state, so the next fall-through
            # should re-notify the user (instead of being suppressed
            # by the one-shot latch).
            self._last_resort_notified = False

    def _record_failure(self, name: str) -> None:
        """Increment the failure counter for ``name``; disable if threshold reached.

        When the circuit breaker trips, two notification
        paths fire:

        1. Every registered ``on_backend_disabled`` subscriber is
           called with ``(backend_name, failure_count)``. Pre-fix this
           was a single callback attribute — only one consumer could
           subscribe. The set-based subscriber list lets ModelManager
           (tray), the IPC layer (renderer event), and a future
           telemetry sink subscribe independently without overwriting
           each other.
        2. An ``{"type": "asr_backend_disabled", ...}`` event is
           published on the global ``event_bus`` so any process-wide
           subscriber (the IPC push channel, diagnostics aggregator)
           is notified. This is in addition to (not instead of) the
           per-registry subscriber set.
        """
        # Declare ``subscribers`` BEFORE the lock so
        # pyrefly's null-safety analysis sees a defined binding on the
        # post-lock read at the ``for fn in subscribers:`` loop (the
        # previous code only assigned it inside ``if tripped:``, so the
        # post-lock branch could read an undefined local if the lock
        # block raised before the assignment — even though ``tripped``
        # is also False in that case, pyrefly can't prove the
        # correlation).
        subscribers: list[BackendDisabledCallback] = []
        with self._lock:
            count = self._failure_counts.get(name, 0) + 1
            self._failure_counts[name] = count
            tripped = count >= self._MAX_CONSECUTIVE_FAILURES and name not in self._disabled_backends
            if tripped:
                self._disabled_backends.add(name)
                log.warning(
                    "[ASR_REGISTRY] backend %s disabled after %d consecutive failures",
                    name,
                    count,
                )
                self._persist_disabled()
                # Snapshot subscribers under the lock so a subscriber
                # that calls remove_backend_disabled_subscriber from
                # within its own callback doesn't mutate the set we're
                # iterating.
                subscribers = list(self._on_backend_disabled_subscribers)

        if tripped:
            # Fire per-registry subscribers. Defensive —
            # a subscriber that raises is logged and skipped so one
            # buggy subscriber doesn't block the others.
            for fn in subscribers:
                try:
                    fn(name, count)
                except Exception:
                    log.warning(
                        "[ASR_REGISTRY] on_backend_disabled subscriber raised",
                        exc_info=True,
                    )
            # Publish process-wide event on event_bus so
            # the IPC push channel and diagnostics aggregator are
            # notified independently of the per-registry subscribers.
            # DT-16: payload fields wrapped under the canonical ``data``
            # key (matching every other event_bus.publish caller) so the
            # Rust WS reader + usePythonEvent forwarding actually surface
            # them. Previously the fields were emitted at the message
            # ROOT, which the Rust reader discarded — the TS
            # ``ASRBackendDisabledEvent`` interface declared them as
            # required root fields but they were unreachable at runtime.
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "asr_backend_disabled",
                        "data": {
                            "backend": name,
                            "failure_count": count,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                )
            except Exception:
                log.warning(
                    "[ASR_REGISTRY] failed to publish asr_backend_disabled event",
                    exc_info=True,
                )

    def _persist_disabled(self) -> None:
        """Persist ``_disabled_backends`` to ``config.disabled_backends``.

        XZ-CFG-01: ``Config`` now declares ``disabled_backends`` as a
        real dataclass field, so this write lands on a real attribute
        and is serialized by ``asdict(self)`` in ``Config.save()``.
        The ``contextlib.suppress`` is retained defensively for legacy
        Config stubs that skip ``__init__`` (and thus lack the field).
        """
        with contextlib.suppress(AttributeError, TypeError):
            self._config.disabled_backends = sorted(self._disabled_backends)

    # ── End circuit-breaker helpers ─────────────────────────────────

    def load_with_fallback(self, progress_callback: ProgressCallback | None = None) -> AsrBackend | None:
        """Load the configured backend; on failure, fall back to whisper.

        ARCH-008: replaces the duplicated fallback logic in
        app.py's _load_transcription_engine_background().

        MEM-01 (c-review): on failure, the failed backend's ``unload()``
        is called so any partially-allocated resources (torch tensors,
        CUDA contexts, multi-GB model weights) are released.

        HIGH-20 / MODEL-2: the dict reads (``self._backends.get``) are
        guarded by ``self._lock`` so a concurrent ``register`` /
        ``unregister`` from another thread (e.g. ``change_model``)
        cannot corrupt the iteration.  The actual ``backend.load()``
        call is OUTSIDE the lock so a slow GPU/disk load doesn't block
        other readers.

        When the primary backend fails AND the primary is not
        whisper, we construct the whisper engine (via :meth:`create`)
        before attempting the whisper load.

        Circuit breaker. Each PRIMARY-backend load failure
        increments a per-backend counter; on success the counter is
        reset. After ``_MAX_CONSECUTIVE_FAILURES`` (3) consecutive
        failures, the primary backend is added to ``_disabled_backends``
        and skipped on subsequent ``load_with_fallback`` calls — we go
        straight to the whisper fallback.

        XS-17 (F-09): pre-fix, the primary backend was UNREGISTERED on
        failure, which meant subsequent ``load_with_fallback`` calls no
        longer found it in ``_backends`` and skipped the primary path
        entirely — so the failure counter only incremented ONCE and
        the circuit breaker never tripped. The failed primary now
        stays registered (only ``unload()`` is called for resource
        cleanup) so the next call can retry it.

        Args:
            progress_callback: optional callable(msg: str) to report
                loading progress (e.g. tray state updates).
        """
        _cb = progress_callback or (lambda msg: None)

        # Try the configured (primary) backend first.
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
                    # OUTSIDE lock: a model load can take 5-50s (cold disk
                    # + torch import).
                    backend.load(progress_callback=_cb)
                    log.info("[ASR_REGISTRY] loaded backend: %s", name)
                    self._record_success(name)
                    return backend
                except Exception as exc:
                    # Use ``exc_info=True`` (matching the sibling
                    # failure paths on lines 260 and 496 which use
                    # ``log.exception``). Pre-fix this was the ONLY
                    # primary-load failure path that discarded the
                    # traceback — the most common ASR failure mode
                    # (Parakeet/Qwen CUDA init) was the only one a
                    # support engineer couldn't diagnose from the log.
                    log.warning(
                        "[ASR_REGISTRY] failed to load %s: %s, trying fallback",
                        name,
                        exc,
                        exc_info=True,
                    )
                    # Increment failure counter; possibly disable.
                    self._record_failure(name)
                    # MEM-01 (c-review): release any partially-allocated
                    # resources. Wrap in try/except so an unload failure
                    # does not prevent the whisper fallback.
                    try:
                        backend.unload()
                        log.info("[ASR_REGISTRY] unloaded failed backend: %s", name)
                    except Exception:
                        # XE-14-J: ``log.exception`` (not ``log.warning``
                        # without ``exc_info``) so the full traceback
                        # lands in the log — a backend.unload() failure
                        # usually means a CUDA context tear-down or
                        # torch-tensor free raised, and the frames are
                        # essential for diagnosing the leak. Mirrors
                        # the sibling unload-failure paths in
                        # ``load_with_fallback`` (whisper fallback) and
                        # ``unload`` (the public API).
                        log.exception(
                            "[ASR_REGISTRY] failed to unload %s after load failure",
                            name,
                        )
                    # XS-17 (F-09): do NOT unregister the failed primary
                    # backend — keep it in ``_backends`` so subsequent
                    # ``load_with_fallback`` calls retry it (and increment
                    # the failure counter toward the disable threshold).

        # If the primary IS whisper and it failed (or was disabled),
        # there is no separate whisper backend to fall back to.
        if name == "whisper":
            return None

        # Fallback to whisper (primary was a non-whisper backend that
        # failed or was disabled).
        with self._lock:
            whisper = self._backends.get("whisper")
        # If the whisper backend was never constructed (cold
        # boot with a non-whisper primary), construct it now using a
        # safe default model_size ("tiny.en") so the fallback actually
        # has something to load.
        if whisper is None:
            log.info("[ASR_REGISTRY] whisper backend not registered — constructing with tiny.en for fallback")
            # ``Config`` declares ``device``, ``language``,
            # ``beam_size``, ``best_of``, ``condition_on_previous_text``
            # as required fields (see ``ConfigProtocol`` above), so the
            # getattr-with-default pattern is no longer needed —
            # type-checkers can verify the field access. The
            # ``getattr(..., "disabled_backends", ...)`` fallback in
            # ``__init__`` is kept because ``Config`` does NOT yet
            # declare ``disabled_backends``.
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
                # OUTSIDE lock — see comment above.
                whisper.load(progress_callback=_cb)
                log.info("[ASR_REGISTRY] loaded fallback backend: whisper")
                # XS-17: do NOT call ``_record_success("whisper")``
                # here — whisper is a FALLBACK, not the user's configured
                # backend.
                # XZ-14-06: but DO clear the last-resort notification
                # latch — the whisper fallback successfully loaded, so
                # we've recovered from the last-resort state and the
                # next fall-through should re-notify the user.
                with self._lock:
                    self._last_resort_notified = False
                return whisper
            except Exception:
                log.exception("[ASR_REGISTRY] whisper fallback also failed")
                # XS-17: do NOT call ``_record_failure("whisper")``
                # — the circuit breaker tracks the user's configured
                # backend, not the whisper fallback.
                # MEM-01 (c-review): unload before giving up so we don't
                # leak the whisper backend's partially-allocated resources.
                try:
                    whisper.unload()
                    log.info("[ASR_REGISTRY] unloaded failed fallback backend: whisper")
                except Exception:
                    # XE-14-J: ``log.exception`` (not ``log.warning``
                    # without ``exc_info``) so the full traceback lands
                    # in the log. Mirrors the sibling unload-failure
                    # paths in ``load_with_fallback`` (primary backend)
                    # and ``unload`` (the public API).
                    log.exception(
                        "[ASR_REGISTRY] failed to unload whisper after load failure",
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
            except Exception:
                # XE-14-J: ``log.exception`` (not ``log.warning`` without
                # ``exc_info``) so the full traceback lands in the log —
                # a backend.unload() failure usually means a CUDA
                # context tear-down or torch-tensor free raised, and
                # the frames are essential for diagnosing the leak.
                # Mirrors the sibling unload-failure paths in
                # ``load_with_fallback`` (primary backend + whisper
                # fallback).
                log.exception("[ASR_REGISTRY] failed to unload %s", target)

    @property
    def available_backends(self) -> list[str]:
        """Return names of all registered backends."""
        with self._lock:
            return list(self._backends.keys())

    # ── UE-48: per-backend busy flag ──────────────────────────────────

    def is_busy(self, name: str | None = None) -> bool:
        """UE-48: return True if the named backend (or the active
        backend when ``name`` is None) is currently inside
        ``transcribe_with_fallback``.

        Thread-safety: the transcribe thread sets/clears the flag via
        :meth:`busy_context` or :meth:`transcribe_with_fallback`, and
        the IPC thread reads it via this method. Both paths acquire
        ``self._lock`` so the read/write pair is atomic. A ``False``
        return value is a snapshot — the backend may become busy
        immediately after the call returns — but callers
        (e.g. :meth:`ModelManager.ensure_active_engine_loaded`) use it
        as a defence-in-depth rejection gate, not as a strict
        mutual-exclusion primitive.

        Args:
            name: backend name to query. If None, queries the active
                backend (``self.active_name``). Returns False if the
                name is unknown or no active backend is configured.
        """
        target = name if name is not None else self.active_name
        if not target:
            return False
        with self._lock:
            return target in self._busy_backends

    def set_busy(self, name: str | None = None) -> None:
        """UE-48: mark the named backend (or the active backend when
        ``name`` is None) as busy.

        Callers SHOULD prefer :meth:`busy_context` (or the
        :meth:`transcribe_with_fallback` wrapper) so the flag is
        cleared automatically on exit — including the exception path.
        Manual ``set_busy`` / :meth:`clear_busy` pairs are error-prone
        (a missed ``clear_busy`` leaves the backend permanently busy,
        blocking all subsequent dictations).

        Thread-safety: see :meth:`is_busy`.
        """
        target = name if name is not None else self.active_name
        if not target:
            return
        with self._lock:
            self._busy_backends.add(target)

    def clear_busy(self, name: str | None = None) -> None:
        """UE-48: mark the named backend (or the active backend when
        ``name`` is None) as not busy.

        Idempotent — calling on a backend that wasn't busy is a
        no-op. Safe to call from a finally block / context-manager
        exit even if ``set_busy`` was never called (e.g. the
        ``busy_context``'s ``__exit__`` always calls this).

        Thread-safety: see :meth:`is_busy`.
        """
        target = name if name is not None else self.active_name
        if not target:
            return
        with self._lock:
            self._busy_backends.discard(target)

    @contextlib.contextmanager
    def busy_context(self, name: str | None = None):
        """UE-48: context manager that sets the busy flag on enter and
        clears it on exit (including the exception path).

        Yields the resolved backend name so callers can pass it to
        subsequent registry methods without re-resolving.

        Usage::

            with registry.busy_context("parakeet") as backend_name:
                backend = registry.get(backend_name)
                text = backend.transcribe_with_fallback(audio, ...)

        Or, equivalently and preferred, use
        :meth:`transcribe_with_fallback` which wraps the call
        automatically.

        Thread-safety: ``set_busy`` + ``clear_busy`` are both
        ``self._lock``-guarded, so the context manager is safe to
        enter/exit from any thread. The flag is keyed by backend NAME
        so a backend that was unregistered mid-transcription (e.g. by
        a concurrent ``change_model``) is still correctly marked
        not-busy on exit — the name doesn't disappear from
        ``_busy_backends`` just because the backend object was
        replaced.
        """
        target = name if name is not None else self.active_name
        if not target:
            # Nothing to mark busy — yield the empty name and return.
            yield target
            return
        self.set_busy(target)
        try:
            yield target
        finally:
            self.clear_busy(target)

    def transcribe_with_fallback(
        self,
        audio: bytes,
        *args: object,
        name: str | None = None,
        **kwargs: object,
    ) -> str:
        """UE-48: wrap the backend's ``transcribe_with_fallback`` call
        with the busy flag set/clear cycle.

        Callers (e.g. ``dictation_pipeline._transcribe``) SHOULD call
        ``registry.transcribe_with_fallback(audio, ...)`` instead of
        ``active.transcribe_with_fallback(audio, ...)`` so the
        registry's per-backend busy flag is set/cleared atomically and
        :meth:`ModelManager.ensure_active_engine_loaded` can reject
        new dictation requests when the active backend is stuck in a
        C-level ctranslate2 call.

        The ``name`` keyword argument selects the backend to invoke
        (defaults to the active backend). All other positional and
        keyword arguments are forwarded to the backend's
        ``transcribe_with_fallback`` unchanged — this is a transparent
        wrapper w.r.t. the backend's signature, so callers that
        already pass ``audio_stats=`` / ``local_engine=`` need no
        changes.

        Returns the transcript text (possibly empty) on success. If
        the named backend is not registered, logs a warning and
        returns an empty string (matching the silent-empty contract
        of :meth:`get_active`'s last-resort branch).

        Exceptions raised by the backend's
        ``transcribe_with_fallback`` propagate to the caller after
        the busy flag is cleared (the ``finally`` block in
        :meth:`busy_context` ensures the flag never gets stuck set).
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

    def force_clear_busy(self, name: str | None = None) -> None:
        """UE-48: alias for :meth:`clear_busy` exposed under a more
        discoverable name for the watchdog's force-recover path.

        The watchdog (:meth:`RecordingController._force_recover_from_stuck_transcription`)
        calls :meth:`ModelManager.force_unload_active` after the 2nd
        force-recovery to tear down the stuck model's GPU
        resources. ``force_unload_active`` calls this method to clear
        the busy flag so the next dictation isn't rejected by
        :meth:`ModelManager.ensure_active_engine_loaded`'s busy-check.

        Kept as a separate public method (rather than just calling
        ``clear_busy`` directly) so the watchdog call site is
        self-documenting: ``registry.force_clear_busy(name)`` reads
        as "force-clear the busy flag because the watchdog decided
        the backend is unrecoverable", whereas ``clear_busy`` could
        be misread as a routine cleanup.
        """
        self.clear_busy(name)
