"""Circuit-breaker for the ASR backend registry.

Extracted from the former monolithic ``asr_registry.py``. Owns the
per-backend consecutive-failure counter, the disabled-backend set, and
the subscriber notification paths that fire when the breaker trips
(``on_backend_disabled``) or when ``get_active`` falls through to an
unloaded last-resort backend (``on_last_resort``).

Shares a ``threading.RLock`` with the registry facade (atomicity) and
owns the one-shot ``_last_resort_notified`` latch so the last-resort
tray notification fires once per transition and re-fires on recovery.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# Backend-disabled subscriber callback (set-based — lets ModelManager
# tray / IPC / telemetry subscribe independently).
BackendDisabledCallback = Callable[[str, int], None]

# Last-resort unloaded-backend callback (pre-fix: only a WARNING log).
LastResortCallback = Callable[[str], None]

# Optional gates at the top of the last-resort / backend-disabled trip
# fan-outs: True suppresses the whole fan-out (subscribers + event_bus
# publish) so the renderer events match the tray's suppressions.
# ModelManager installs both.
LastResortEventGate = Callable[[str], bool]
BackendDisabledEventGate = Callable[[str], bool]


class CircuitBreaker:
    """Per-backend consecutive-failure counter + disabled-set state.

    Parameterised on the shared ``config`` (persists the disabled set)
    and ``lock`` (mutually atomic with the registry). State mutations
    happen under ``lock``; the notification fan-out happens outside it
    so subscribers can safely re-enter the registry.
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
        # Optional suppression gate (see ``LastResortEventGate``); None
        # = no gate (publish always proceeds). Installed by ModelManager.
        self._last_resort_event_gate: LastResortEventGate | None = None
        # Optional gate for the backend-disabled trip fan-out (see
        # ``BackendDisabledEventGate``); None = no gate.
        self._backend_disabled_event_gate: BackendDisabledEventGate | None = None
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
        """Install/clear the last-resort suppression gate.

        True skips the ENTIRE fan-out (subscribers + the
        ``asr_last_resort_unloaded`` publish); a gate that RAISES fails
        OPEN (alert still delivered).
        """
        self._last_resort_event_gate = gate

    def set_backend_disabled_event_gate(self, gate: BackendDisabledEventGate | None) -> None:
        """Install/clear the backend-disabled suppression gate.

        True skips the trip fan-out (subscribers + the publish); a gate
        that RAISES fails OPEN. The breaker STATE mutation is NOT gated.
        """
        self._backend_disabled_event_gate = gate

    # ── circuit-breaker helpers ─────────────────────────────────────

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
                "[ASR_REGISTRY] %s event gate raised — proceeding with fan-out",
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
        """Increment the failure counter; disable if threshold reached.

        On trip, the ``on_backend_disabled`` subscribers fire AND an
        ``asr_backend_disabled`` event_bus publish is made (both gated
        by ``set_backend_disabled_event_gate``).
        """
        # Declare ``subscribers`` BEFORE the lock so pyrefly's
        # null-safety analysis sees it bound on the post-lock read.
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
            # Fail-open suppression gate: ModelManager mirrors the
            # deliberate-unload windows onto the ``asr_backend_disabled``
            # surface (a backend mid-switch isn't genuinely broken). The
            # state mutation above (disable + persist) is NOT undone.
            if self._fan_out_suppressed(self._backend_disabled_event_gate, name, "backend-disabled"):
                return

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
            # Publish process-wide event (IPC push + diagnostics),
            # payload under the canonical ``data`` key (matching every
            # other event_bus.publish caller) so the Rust WS reader +
            # usePythonEvent forwarding surface them.
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

        The ``contextlib.suppress`` covers legacy Config stubs that
        skip ``__init__`` (and thus lack the field).
        """
        with contextlib.suppress(AttributeError, TypeError):
            self._config.disabled_backends = sorted(self._disabled_backends)

    # ── last-resort latch ───────────────────────────────────────────

    def mark_last_resort_notified(self) -> None:
        """Set the one-shot latch (``get_active``'s last-resort branch)."""
        with self._lock:
            self._last_resort_notified = True

    def should_notify_last_resort(self) -> bool:
        """Return True if the latch is unset AND set it atomically.

        Called by ``get_active``'s last-resort branch: True the first
        time per transition, False until recovery resets the latch.
        """
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
        """Fire per-registry subscribers + publish the event_bus event
        for the last-resort unloaded-backend fallback.

        Called from ``get_active``'s ``finally`` block after the latch
        is set. Subscribers are snapshotted under the lock + fired
        outside; a raising subscriber is logged and skipped.
        """
        # Fail-open suppression gate: ModelManager mirrors the tray
        # notification's suppressions to the renderer-toast surface.
        if self._fan_out_suppressed(self._last_resort_event_gate, name, "last-resort"):
            return

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
