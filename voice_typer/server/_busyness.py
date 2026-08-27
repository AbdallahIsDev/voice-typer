"""BusynessCoordinator — owns the pipeline's "busy" flag + lock.

Pre-refactor: ``VoiceTyperApp.__init__`` declared three
private attributes consumed by 6 external modules via direct
``app._busy_event`` / ``app._lock`` access:

    self._microphones: list[dict] = []
    self._busy_event = threading.Event()
    self._busy_event.set()  # SET = not busy
    self._lock = threading.Lock()

The ``_busy_event`` semantics were INVERTED (``is_set() == True``
means NOT busy — the event doubles as a "ready" signal whose
``wait()`` blocks while busy). This inversion was documented only
at the declaration site, which made every consumer call site a
silent landmine (a careless ``if app._busy_event.is_set():``
reads as "is the app busy?" but actually means "is the app
IDLE?").

This module fixes both issues by introducing an explicit
:class:`BusynessCoordinator` that owns the event + lock and
exposes intent-revealing methods: :meth:`is_busy`,
:meth:`set_busy`, :meth:`set_idle`, :meth:`wait_idle`,
and a :attr:`lock` property for back-compat with code that still
wants the raw ``threading.Lock``.

Back-compat: ``VoiceTyperApp._busy_event`` and ``VoiceTyperApp._lock``
remain accessible (as read-only properties delegating to this
coordinator) so non-owned consumer files that haven't been migrated
yet (e.g. ``recording_lifecycle.py``, ``transcription_watchdog.py``,
``dictation_pipeline/paste_step.py``, ``dictation_pipeline/transcribe_step.py``,
``dictation_pipeline/orchestrator.py``) keep working unchanged — only the
owned consumer files (``model_manager.py``, ``startup_tasks.py``,
``service/microphone_test.py``, ``recording_controller.py``) are migrated
to the new API in this wave.
"""

from __future__ import annotations

import threading


class BusynessCoordinator:
    """Owns the pipeline-wide "busy" flag and its companion lock.

    The "busy" flag signals whether a transcription cycle is in
    flight on the dictation thread. ``set_busy()`` is called when
    the cycle starts (so a concurrent toggle / hotkey press sees
    "busy" and refuses to start a second cycle); ``set_idle()`` is
    called from the orchestrator's finally block when the cycle
    ends (success, cancel, or handled exception).

    The underlying primitive is a ``threading.Event`` so callers
    that want to BLOCK until the pipeline returns to idle can call
    :meth:`wait_idle` (this is the original "ready signal" use
    case — ``wait_idle`` blocks while busy, returns immediately
    when idle).

    Semantics (NON-inverted):
      * :meth:`is_busy` → ``True`` while a transcription cycle is in flight.
      * :meth:`set_busy` → mark busy (clears the underlying event).
      * :meth:`set_idle` → mark idle (sets the underlying event).

    The legacy ``_busy_event`` was INVERTED (``is_set() == True``
    meant NOT busy) — the inversion is internal to this class and
    the public methods present the natural reading instead.
    """

    def __init__(self) -> None:
        # ``threading.Event`` whose SET state means "NOT busy" (the
        # event doubles as a "ready" signal: ``wait()`` blocks while
        # busy and returns immediately when idle). This preserves the
        # exact primitive the legacy code used so the back-compat
        # ``_busy_event`` property (delegating to this object) keeps
        # the same wait/notify semantics for non-migrated consumers.
        self._busy_event = threading.Event()
        self._busy_event.set()  # start IDLE
        # Companion lock — used by the legacy code paths as a
        # coarse-grained mutex around ``_transcription_thread`` writes
        # etc. Kept here (not deleted) because non-owned consumer
        # files (``recording_lifecycle.py``, ``transcription_watchdog.py``)
        # still acquire it via the back-compat ``_lock`` property on
        # VoiceTyperApp.
        self._lock = threading.Lock()

    # ── Intent-revealing public API ────────────────────────────────

    def adopt_event(self, event: threading.Event) -> None:
        """Rebind the underlying event to ``event``.

        Used by the ``VoiceTyperApp._busy_event`` back-compat setter so
        test/monkeypatch code that assigns a fresh ``threading.Event``
        to ``app._busy_event`` keeps the coordinator's state machine
        (``is_busy`` / ``set_busy`` / ``set_idle`` / ``wait_idle``)
        operating on the SAME primitive the legacy consumers see.
        The adopted event's flag IS the new busy state (a fresh
        ``Event()`` starts unset == busy — identical to the pre-extraction
        rebinding semantics).
        """
        self._busy_event = event

    def adopt_lock(self, lock: threading.Lock) -> None:
        """Rebind the companion lock to ``lock`` (see :meth:`adopt_event`)."""
        self._lock = lock

    def is_busy(self) -> bool:
        """Return ``True`` while a transcription cycle is in flight.

        Non-inverted semantic vs the legacy ``_busy_event.is_set()``
        (which returns ``True`` when NOT busy). Callers can read this
        as the natural "is the pipeline busy?" question.
        """
        return not self._busy_event.is_set()

    def set_busy(self) -> None:
        """Mark the pipeline as busy (a cycle is in flight).

        Equivalent to the legacy ``_busy_event.clear()`` (which set
        busy = True). Idempotent.
        """
        self._busy_event.clear()

    def set_idle(self) -> None:
        """Mark the pipeline as idle (no cycle in flight).

        Equivalent to the legacy ``_busy_event.set()`` (which set
        busy = False). Idempotent.
        """
        self._busy_event.set()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until the pipeline is idle, or until ``timeout`` elapses.

        Returns ``True`` if the pipeline became idle within ``timeout``
        (or ``timeout`` is ``None``), ``False`` otherwise. Mirrors
        ``threading.Event.wait`` semantics — busy == block, idle ==
        return. This is the original "ready signal" use case from the
        legacy ``_busy_event.wait()`` callers.
        """
        return self._busy_event.wait(timeout)

    @property
    def lock(self) -> threading.Lock:
        """The companion ``threading.Lock`` for coarse-grained mutual exclusion.

        Exposed for back-compat with consumers that previously called
        ``app._lock.acquire()`` / ``app._lock.release()`` or used
        ``with app._lock:``. New code should prefer explicit
        coordination methods when possible, but this lock is kept
        because some legacy call sites genuinely need a shared mutex.
        """
        return self._lock

    @property
    def event(self) -> threading.Event:
        """The underlying ``threading.Event`` (back-compat for legacy callers).

        Exposed so non-migrated consumer files that read
        ``app._busy_event`` directly (e.g. via the back-compat
        ``VoiceTyperApp._busy_event`` property) get the same primitive
        the new coordinator owns — no copy, no proxy. New code should
        prefer :meth:`is_busy` / :meth:`set_busy` / :meth:`set_idle`.
        """
        return self._busy_event
