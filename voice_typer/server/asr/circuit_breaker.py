"""Circuit-breaker for the ASR backend registry.

Extracted from the former monolithic ``asr_registry.py``. Owns
the per-backend consecutive-failure counter, the disabled-backend set,
and the subscriber notification paths that fire when the breaker trips
(``on_backend_disabled``) or when ``get_active`` falls through to an
unloaded last-resort backend (``on_last_resort``).

The breaker is constructed with a shared ``threading.RLock`` so the
``AsrBackendRegistry`` facade's atomicity guarantees are preserved: a
``load_with_fallback`` call that holds the lock to read
``_disabled_backends`` cannot race with a concurrent
``_record_failure`` that adds to it.

The breaker also owns the one-shot ``_last_resort_notified`` latch —
set by ``get_active``'s last-resort branch, cleared by
``_record_success`` (primary-backend load succeeded) and by
``load_with_fallback``'s whisper-fallback success path. The latch
ensures the tray notification fires only ONCE per last-resort
transition (not on every ``get_active`` call) and resets when a ready
backend becomes available again so a recovery → re-fallback sequence
re-notifies the user.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# Subscriber callback for backend-disabled events.
# Pre-fix, ``on_backend_disabled`` was a single callback attribute.
# Only ONE subscriber could be notified when the circuit breaker
# tripped. The set-based subscriber list lets ModelManager (tray), the
# IPC layer (renderer event), and a telemetry sink subscribe
# independently without overwriting each other.
BackendDisabledCallback = Callable[[str, int], None]

# Subscriber callback for the last-resort unloaded-backend fallback
# path in get_active(). Pre-fix, that path logged a WARNING ("returning
# unloaded backend %s (is_loaded=False) as last-resort active —
# transcription may return empty silently") but fired no tray
# notification — the user silently got empty transcriptions with no
# visible feedback that voice recognition wasn't working. The callback
# receives the configured backend name (the same value passed to the
# WARNING log) so the tray can render a useful message (e.g. "Voice
# Typer: Active backend '<name>' is not loaded — transcription may be
# unavailable. Check your model settings.").
LastResortCallback = Callable[[str], None]


class CircuitBreaker:
    """Per-backend consecutive-failure counter + disabled-set state.

    The breaker is parameterised on the shared ``config`` object (for
    persisting the disabled set into ``config.disabled_backends``) and
    the shared ``lock`` (so registry + breaker operations are mutually
    atomic). All public state mutations happen under ``lock``; the
    subscriber-notification fan-out happens OUTSIDE the lock (so a
    subscriber callback can safely re-enter the registry without
    deadlock).
    """

    # After this many consecutive load failures, a backend is marked
    # "disabled" — subsequent ``load_with_fallback`` calls skip it and
    # fall straight through to the whisper fallback.
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, config: object, lock: threading.RLock) -> None:
        self._config = config
        self._lock = lock
        self._failure_counts: dict[str, int] = {}
        # ``Config`` declares ``disabled_backends`` as a real dataclass
        # field (default empty list). The ``getattr`` fallback is
        # retained so test stubs / legacy Config objects constructed via
        # ``__new__`` (which skip ``__init__``) keep working — the field
        # is absent on those bare instances.
        persisted = getattr(config, "disabled_backends", None) or []
        try:
            self._disabled_backends: set[str] = set(persisted)
        except TypeError:
            # A misconfigured ``disabled_backends`` (e.g. a string
            # instead of a list — ``set("whisper")`` produces
            # ``{"w", "h", "i", "s", "p", "e", "r"}`` rather than
            # raising) would silently clear the persisted disabled set,
            # re-enabling a backend the user explicitly disabled. Log at
            # WARNING so the misconfiguration is visible in diagnostics
            # exports without crashing the app.
            log.warning(
                "[ASR_REGISTRY] config.disabled_backends is not iterable (%r); ignoring persisted disabled set",
                persisted,
            )
            self._disabled_backends = set()
        # Backend-disabled subscribers. Pre-fix this was a single
        # ``on_backend_disabled: Any | None = None`` attribute. The
        # ``on_backend_disabled`` property below preserves the legacy
        # ``registry.on_backend_disabled = fn`` assignment pattern by
        # adding ``fn`` to the subscriber set.
        self._on_backend_disabled_subscribers: set[BackendDisabledCallback] = set()
        # Subscribers for the last-resort unloaded-backend event in
        # get_active(). Same set-based pattern as
        # _on_backend_disabled_subscribers so ModelManager (tray), the
        # IPC layer (renderer event), and a telemetry sink can subscribe
        # independently without overwriting each other.
        self._on_last_resort_subscribers: set[LastResortCallback] = set()
        # One-shot latch so we don't fire the tray notification on every
        # get_active() call while the registry is stuck in the
        # last-resort state. Reset to False whenever get_active() finds
        # a ready backend (so a recovery → re-fallback sequence
        # re-notifies) and in _record_success (primary-backend load
        # success).
        self._last_resort_notified: bool = False

    # ── subscriber management ───────────────────────────────────────

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

    # Last-resort subscriber management. Mirrors the backend-disabled
    # subscriber API so the app can wire a tray notification via the
    # same path used for load_with_fallback failures.
    @property
    def on_last_resort(self) -> set[LastResortCallback]:
        """Set of subscribers fired when get_active() falls through to
        an unloaded last-resort backend."""
        return self._on_last_resort_subscribers

    @on_last_resort.setter
    def on_last_resort(self, fn: LastResortCallback | None) -> None:
        """Backward-compatible property setter mirroring
        ``on_backend_disabled`` — assigning a callable adds it to the
        subscriber set; assigning None clears the set."""
        if fn is None:
            self._on_last_resort_subscribers.clear()
        elif callable(fn):
            self._on_last_resort_subscribers.add(fn)

    def add_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """Register a subscriber for last-resort-unloaded-backend events."""
        if callable(fn):
            self._on_last_resort_subscribers.add(fn)

    def remove_last_resort_subscriber(self, fn: LastResortCallback) -> None:
        """Unregister a last-resort subscriber (no-op if absent)."""
        self._on_last_resort_subscribers.discard(fn)

    # ── circuit-breaker helpers ─────────────────────────────────────

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
        """Reset the failure counter for ``name`` and re-enable it if disabled.

        Also clears the last-resort notification latch — a successful
        primary-backend load means we've recovered from the last-resort
        state, so the next fall-through should re-notify the user
        (instead of being suppressed by the one-shot latch).
        """
        with self._lock:
            self._failure_counts[name] = 0
            if name in self._disabled_backends:
                self._disabled_backends.discard(name)
                log.info("[ASR_REGISTRY] backend %s re-enabled (load succeeded)", name)
                self._persist_disabled()
            self._last_resort_notified = False

    def _record_failure(self, name: str) -> None:
        """Increment the failure counter for ``name``; disable if threshold reached.

        When the circuit breaker trips, two notification paths fire:

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
        # Declare ``subscribers`` BEFORE the lock so pyrefly's
        # null-safety analysis sees a defined binding on the post-lock
        # read at the ``for fn in subscribers:`` loop (the previous
        # code only assigned it inside ``if tripped:``, so the post-lock
        # branch could read an undefined local if the lock block raised
        # before the assignment — even though ``tripped`` is also False
        # in that case, pyrefly can't prove the correlation).
        subscribers: list[BackendDisabledCallback] = []
        with self._lock:
            count = self._failure_counts.get(name, 0) + 1
            self._failure_counts[name] = count
            tripped = (
                count >= self._MAX_CONSECUTIVE_FAILURES
                and name not in self._disabled_backends
            )
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
            # Fire per-registry subscribers. Defensive — a subscriber
            # that raises is logged and skipped so one buggy subscriber
            # doesn't block the others.
            for fn in subscribers:
                try:
                    fn(name, count)
                except Exception:
                    log.warning(
                        "[ASR_REGISTRY] on_backend_disabled subscriber raised",
                        exc_info=True,
                    )
            # Publish process-wide event on event_bus so the IPC push
            # channel and diagnostics aggregator are notified
            # independently of the per-registry subscribers. Payload
            # fields wrapped under the canonical ``data`` key (matching
            # every other event_bus.publish caller) so the Rust WS
            # reader + usePythonEvent forwarding actually surface them.
            # Previously the fields were emitted at the message ROOT,
            # which the Rust reader discarded — the TS
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

        ``Config`` declares ``disabled_backends`` as a real dataclass
        field, so this write lands on a real attribute and is serialized
        by ``asdict(self)`` in ``Config.save()``. The
        ``contextlib.suppress`` is retained defensively for legacy
        Config stubs that skip ``__init__`` (and thus lack the field).
        """
        with contextlib.suppress(AttributeError, TypeError):
            self._config.disabled_backends = sorted(self._disabled_backends)

    # ── last-resort latch ───────────────────────────────────────────

    def mark_last_resort_notified(self) -> None:
        """Set the one-shot latch (called by ``get_active``'s
        last-resort branch under the lock)."""
        with self._lock:
            self._last_resort_notified = True

    def should_notify_last_resort(self) -> bool:
        """Return True if the latch is unset AND set it atomically.

        Called by ``get_active``'s last-resort branch — returns True the
        first time per last-resort transition, False on subsequent
        ``get_active`` calls until the latch is reset (by
        ``_record_success`` or by ``load_with_fallback``'s
        whisper-fallback success path).
        """
        with self._lock:
            if self._last_resort_notified:
                return False
            self._last_resort_notified = True
            return True

    def clear_last_resort_notified(self) -> None:
        """Clear the one-shot latch (called by ``load_with_fallback``'s
        whisper-fallback success path under the lock)."""
        with self._lock:
            self._last_resort_notified = False

    def fire_last_resort_subscribers(self, name: str) -> None:
        """Fire per-registry subscribers + publish an event_bus event
        for the last-resort unloaded-backend fallback.

        Called from ``get_active``'s ``finally`` block, AFTER the
        ``_last_resort_notified`` latch has been set under the lock.
        Snapshotting the subscribers under the lock + firing them
        outside mirrors the ``_record_failure`` pattern — a subscriber
        that raises is logged and skipped so one buggy subscriber
        doesn't block the others.
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

        # Publish process-wide event on event_bus so the IPC push
        # channel and any diagnostics aggregator are notified
        # independently of the per-registry subscribers (mirrors the
        # asr_backend_disabled event published from _record_failure).
        # Payload fields wrapped under the canonical ``data`` key
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
