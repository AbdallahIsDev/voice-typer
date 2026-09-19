"""ASR circuit-breaker state."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# Backend-disabled subscriber callback (set-based, lets ModelManager
BackendDisabledCallback = Callable[[str, int], None]

# Last-resort unloaded-backend callback (pre-fix: only a WARNING log).
LastResortCallback = Callable[[str], None]

# Optional gates at the top of the last-resort / backend-disabled trip
LastResortEventGate = Callable[[str], bool]
BackendDisabledEventGate = Callable[[str], bool]


class CircuitBreaker:
    """Per-backend consecutive-failure counter + disabled-set state."""

    # After this many consecutive load failures, a backend is marked
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, config: object, lock: threading.RLock) -> None:
        self._config = config
        self._lock = lock
        self._failure_counts: dict[str, int] = {}
        # ``Config`` declares ``disabled_backends`` as a real dataclass
        persisted = getattr(config, "disabled_backends", None) or []
        try:
            self._disabled_backends: set[str] = set(persisted)
        except TypeError:
            # A misconfigured ``disabled_backends`` (e.g. a string
            log.warning(
                "[ASR_REGISTRY] config.disabled_backends is not iterable (%r); ignoring persisted disabled set",
                persisted,
            )
            self._disabled_backends = set()
        # Backend-disabled subscribers. Pre-fix this was a single
        self._on_backend_disabled_subscribers: set[BackendDisabledCallback] = set()
        # Subscribers for the last-resort unloaded-backend event in
        self._on_last_resort_subscribers: set[LastResortCallback] = set()
        # Optional suppression gate (see ``LastResortEventGate``); None
        self._last_resort_event_gate: LastResortEventGate | None = None
        # Optional gate for the backend-disabled trip fan-out (see
        self._backend_disabled_event_gate: BackendDisabledEventGate | None = None
        # One-shot latch so we don't fire the tray notification on every
        self._last_resort_notified: bool = False

    # Backward-compatible property so existing
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
    @property
    def on_last_resort(self) -> set[LastResortCallback]:
        """Set of subscribers fired when get_active() falls through to
        an unloaded last-resort backend."""
        return self._on_last_resort_subscribers

    @on_last_resort.setter
    def on_last_resort(self, fn: LastResortCallback | None) -> None:
        """Backward-compatible setter: callable adds to the set, None clears."""
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

    def set_last_resort_event_gate(self, gate: LastResortEventGate | None) -> None:
        """Install/clear the last-resort suppression gate."""
        self._last_resort_event_gate = gate

    def set_backend_disabled_event_gate(self, gate: BackendDisabledEventGate | None) -> None:
        """Install/clear the backend-disabled suppression gate."""
        self._backend_disabled_event_gate = gate

    def _fan_out_suppressed(
        self,
        gate: Callable[[str], bool] | None,
        name: str,
        label: str,
    ) -> bool:
        """True when ``gate`` suppresses the fan-out for ``name`` (both
        fan-outs; a raising gate fails OPEN).
        """
        if gate is None:
            return False
        try:
            if gate(name):
                log.debug(
                    "[ASR_REGISTRY] %s fan-out suppressed by event gate (backend=%s)",
                    label,
                    name,
                )
                return True
        except Exception:
            # Fail open: a broken gate must not swallow a genuine alert.
            log.warning(
                "[ASR_REGISTRY] %s event gate raised, proceeding with fan-out",
                label,
                exc_info=True,
            )
        return False

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
        """Reset the failure counter; re-enable if disabled; clear the
        last-resort latch so recovery re-notifies."""
        with self._lock:
            self._failure_counts[name] = 0
            if name in self._disabled_backends:
                self._disabled_backends.discard(name)
                log.info("[ASR_REGISTRY] backend %s re-enabled (load succeeded)", name)
                self._persist_disabled()
            self._last_resort_notified = False

    def _record_failure(self, name: str) -> None:
        """Increment the failure counter; disable if threshold reached."""
        # Declare ``subscribers`` BEFORE the lock so pyrefly's
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
                subscribers = list(self._on_backend_disabled_subscribers)

        if tripped:
            # Fail-open suppression gate: ModelManager mirrors the
            if self._fan_out_suppressed(self._backend_disabled_event_gate, name, "backend-disabled"):
                return

            # Fire per-registry subscribers. Defensive, a subscriber
            for fn in subscribers:
                try:
                    fn(name, count)
                except Exception:
                    log.warning(
                        "[ASR_REGISTRY] on_backend_disabled subscriber raised",
                        exc_info=True,
                    )
            # Publish process-wide event (IPC push + diagnostics),
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
        """Persist ``_disabled_backends`` to ``config.disabled_backends``."""
        with contextlib.suppress(AttributeError, TypeError):
            self._config.disabled_backends = sorted(self._disabled_backends)

    def mark_last_resort_notified(self) -> None:
        """Set the one-shot latch (``get_active``'s last-resort branch)."""
        with self._lock:
            self._last_resort_notified = True

    def should_notify_last_resort(self) -> bool:
        """Return True if the latch is unset AND set it atomically."""
        with self._lock:
            if self._last_resort_notified:
                return False
            self._last_resort_notified = True
            return True

    def clear_last_resort_notified(self) -> None:
        """Clear the one-shot latch (whisper-fallback success path)."""
        with self._lock:
            self._last_resort_notified = False

    def fire_last_resort_subscribers(self, name: str) -> None:
        """Fire per-registry subscribers + publish the event_bus event"""
        # Fail-open suppression gate: ModelManager mirrors the tray
        if self._fan_out_suppressed(self._last_resort_event_gate, name, "last-resort"):
            return

        # Snapshot subscribers under the lock so a subscriber that calls
        with self._lock:
            subscribers = list(self._on_last_resort_subscribers)

        # Fire per-registry subscribers (tray notification, IPC push,
        for fn in subscribers:
            try:
                fn(name)
            except Exception:
                log.warning(
                    "[ASR_REGISTRY] on_last_resort subscriber raised",
                    exc_info=True,
                )

        # Publish process-wide event on event_bus so the IPC push
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
