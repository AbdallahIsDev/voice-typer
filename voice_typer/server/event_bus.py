"""In-process event bus for broadcasting events to subscribers.

B-1: extracted from ``voice_typer.server.ipc_server._push_event_now``
to break the tight coupling between 12+ domain modules and the IPC
transport layer.

Architecture
------------
This module is the LEAF of the dependency tree.  It imports nothing
from ``voice_typer.*`` (only stdlib) so that any other module can
import it without risk of a circular import.

Domain modules (recording, service, app, tray, hotkey_dispatcher,
level_monitor, dictation_pipeline, startup_tasks, recording_controller,
handlers/config_handlers, handlers/system_handlers, tray_window) call
``publish(event)`` to broadcast a JSON-lines event.

The IPC server (``voice_typer.server.ipc_server.IPCServer``) calls
``subscribe(self.push)`` on ``start()`` and ``unsubscribe(self.push)``
on ``stop()`` so that every published event is forwarded to the
connected Electron renderer over TCP (or to stdout in stdin/stdout
mode).

Other transports (CLI, gRPC, future WebSocket) can subscribe the same
way without touching the domain modules.

Canonical event catalogue (IPC-2 reconciliation, 2026-07-18)
------------------------------------------------------------
Every event broadcast through ``event_bus.publish`` OR ``IPCServer.push``
flows through the same channel (2) (server-initiated events). The
catalogue below is the source of truth mirrored in ADR-0020 §2's
"Sidecar→UI Event Table". When you ADD an event, append it to BOTH
this list AND the ADR table (the docstring is the code-side anchor;
the ADR is the spec-side anchor).

Events emitted via ``event_bus.publish`` (the modern path):

* ``ready`` — emitted once on first authenticated WS connection
  (sidecar_ws.py) or on TCP server start (ipc_server.py:1899).
  Payload: ``{}``.
* ``bubble_show`` — show waveform bubble. Payload: ``{}``.
* ``bubble_hide`` — hide waveform bubble. Payload: ``{}``.
* ``bubble_level`` — ~60 Hz RMS/peak for the waveform bubble.
  Payload: ``{rms:float, peak:float}``.
* ``bubble_set_state`` — set the bubble's state machine.
  Payload: ``{state:str}``.
* ``transcription_final`` — final transcription text (UI preview).
  Payload: ``{text:str (≤200 chr)}``.
* ``vocabulary_suggestion`` — pending correction suggestions.
  Payload: ``{suggestions:[{original,corrected,confidence,context,timestamp}]}``.
* ``hotkey_capture_cancel`` — cancel hotkey capture mode. Payload: ``{}``.
* ``config_changed`` — config was updated; renderer should refresh.
  Payload: ``{<validated config updates>}``.
* ``history_changed`` — history mutation (add/delete/clear/fav/restore).
  Payload: ``{reason:str}``.
* ``microphone_test_complete`` — a microphone test finished.
  Payload: ``{duration:float}``.
* ``microphones_changed`` — the mic list changed (hot-plug).
  Payload: ``{count:int}``.
* ``audio_clip`` — an audio clipping event was observed.
  Payload: ``{peak:float, count:int}``.
* ``recording_started`` — dictation started. Payload: ``{}``.
* ``recording_stopped`` — dictation stopped. Payload: ``{}``.
* ``download_progress`` — model download progress.
  Payload: ``{model, progress(0-100), status, +optional downloaded_bytes,
  total_bytes, speed_bytes_per_sec, eta_seconds, paused, resumed}``.
* ``notification`` — request a renderer toast. Payload: ``{title, message,
  duration_ms, critical}``. (Canonical name — previously emitted as
  ``electron_notification``; CR-8 renamed the wire event on the Python
  side so both the Electron and Tauri paths consume the same name.)
* ``navigate`` — tray → UI route change. Payload: ``{path:str}``.
* ``show_window`` — show the main window. Payload: ``{}``.
* ``quit_app`` — sidecar requests app quit. Payload: ``{}``.
* ``relaunch_app`` — sidecar requests app relaunch. Payload: ``{}``.
* ``paste_failed`` — clipboard paste failed (NEW-UX-006); renderer
  shows a sonner toast with "Open recovery file" action.
  Payload: ``{message:str, recovery_path:str|null}``.

Events emitted via ``IPCServer.push`` (NOT through ``event_bus.publish``
— they bypass the bus because they are wired into the IPC accept loop
or the tray-state hook, both of which already hold a reference to the
server):

* ``state_changed`` — ERR-017; emitted ONCE per TCP/WS client connect
  so the renderer immediately knows the current app state. Payload:
  ``{status:str, message:str}``.
* ``status_change`` — emitted on EVERY tray state transition via the
  ``_hook_tray_set_state`` wrapper installed in ``IPCServer.start()``.
  Payload: ``{status:str}``. Distinct from ``state_changed``: the
  former is a per-transition signal with just ``status``; the latter
  is the connect-time snapshot with a ``message`` field.

Total: 24 events (23 via ``event_bus.publish`` + 1 ``ready`` (pushed
directly) + 2 ``IPCServer.push``-only events = 24 unique event names;
the ADR-0020 §2 table lists all 24).

Thread safety
-------------
``subscribe`` / ``unsubscribe`` / ``publish`` are all thread-safe.
A re-entrant lock (``threading.RLock``) guards the subscriber set so
that a subscriber which itself calls ``publish`` (re-entrant publish)
does not deadlock.  Re-entrant publish is not encouraged but is
guaranteed not to deadlock.

Subscriber exception isolation
------------------------------
A subscriber that raises is logged at DEBUG level (with ``exc_info``)
and skipped.  Other subscribers still receive the event.  This matches
the previous ``_push_event_now`` semantics and is verified by
``tests/test_event_bus.py::TestSubscriberExceptionIsolation``.
"""

from __future__ import annotations

import logging
import threading
import typing
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

log = logging.getLogger("voice_typer.server.event_bus")

# Module-level singleton state.  No class instance needed — this is
# process-global by design so that domain modules can publish without
# holding a reference to any particular IPC server or app instance.
#
# A ``set`` (not a ``list``) so duplicate ``subscribe`` calls for the
# same callable are deduplicated; this matters because ``IPCServer``'s
# ``start()`` is idempotent across stop/start cycles in tests and we
# do not want the same callable registered twice.
_subscribers: set[typing.Callable[[dict], None]] = set()

# RLock (not Lock) so a subscriber that calls publish() re-entrantly
# does not deadlock.  Re-entrant publish is discouraged but supported.
_lock = threading.RLock()

# PERF-2: When ``publish()`` is called from a real-time audio thread
# (sounddevice's PortAudio callback, or the in-process "audio-worker"
# thread that drives the callback), synchronous fan-out to every
# subscriber can glitch capture — a slow subscriber (json.dumps +
# socket.sendall to a stalled Electron renderer) blocks the RT loop.
# Detect the audio thread by name and defer to a single-worker
# ThreadPoolExecutor so the RT thread returns in microseconds.
_RT_THREAD_NAME_PREFIXES: tuple[str, ...] = (
    "audio-worker",  # voice_typer.server.recording._AUDIO_WORKER_THREAD_NAME
    "PortAudio",  # sounddevice's native callback thread prefix
)
_deferred_executor: ThreadPoolExecutor | None = None
_deferred_executor_lock = threading.Lock()


def _get_deferred_executor() -> ThreadPoolExecutor:
    """Lazily create the single-worker deferred-publish executor.

    CR-9: previously the double-checked-locking pattern could leak a
    ``ThreadPoolExecutor`` if two threads both entered the slow path
    and both created a fresh executor before either acquired
    ``_deferred_executor_lock``. (The first thread to acquire the
    lock would install theirs; the second thread's executor was a
    local that went out of scope — but its worker thread kept
    running, leaking a thread + a kernel-level worker pool.)

    The fix creates the executor BEFORE acquiring the lock (in the
    slow path), then races for the global slot. The winner installs
    theirs and returns it; the loser calls ``shutdown(wait=False)``
    on theirs (which signals the worker thread to exit) and returns
    the winner. This is the canonical "create-then-compare-and-swap"
    pattern for lazy singletons guarded by a mutex.
    """
    global _deferred_executor
    # Fast path — no lock acquired. The global is published via the
    # GIL-atomic pointer assignment inside the slow path below; reads
    # here are safe under the GIL.
    if _deferred_executor is not None:
        return _deferred_executor
    # Slow path: optimistically create our own executor BEFORE
    # acquiring the lock, so two racing threads don't serialize on
    # executor construction (which spawns a worker thread — ~1ms).
    local_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="event-bus-publisher",
    )
    with _deferred_executor_lock:
        if _deferred_executor is None:
            # We won the race — install ours.
            _deferred_executor = local_executor
            return local_executor
        # We lost the race — another thread installed theirs while we
        # were constructing ours. Shut ours down so its worker thread
        # doesn't leak (CR-9), then return the winner.
        winner = _deferred_executor
    # Shutdown OUTSIDE the lock to avoid blocking other racing callers.
    # ``wait=False`` returns immediately; the worker thread exits on
    # its next idle poll (it has no queued tasks — we never submitted
    # any to our local executor).
    local_executor.shutdown(wait=False)
    return winner


def shutdown_executor() -> None:
    """Shutdown the deferred-publish executor.

    CR-22: Previously the executor was a module-global singleton with no
    explicit lifecycle management. On `restart_app()` the old executor
    was orphaned and a new one created — leaking worker threads. On
    normal exit the worker could block up to the default join timeout
    (~5s) slowing shutdown.

    Call this from `shutdown_controller._do_cleanup()` after the IPC
    server stop to release the worker thread promptly.
    """
    global _deferred_executor
    with _deferred_executor_lock:
        executor = _deferred_executor
        _deferred_executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _is_rt_thread() -> bool:
    """Return True if the current thread is a real-time audio thread."""
    name = threading.current_thread().name
    if name == "audio-worker":
        return True
    return name.startswith("PortAudio")


def subscribe(callback: typing.Callable[[dict], None] | None) -> None:
    """Register *callback* to receive every published event.

    Calling with ``None`` is a no-op (matches the previous
    ``_set_push_event`` semantics where ``None`` was used as a
    sentinel and rejected).

    Duplicate callbacks are stored only once (set semantics).
    Safe to call from any thread.
    """
    if callback is None:
        return
    with _lock:
        _subscribers.add(callback)


def unsubscribe(callback: typing.Callable[[dict], None] | None) -> None:
    """Unregister *callback*.

    Safe to call with a callback that was never registered (no-op).
    Safe to call with ``None`` (no-op).  Safe to call from any thread.
    """
    if callback is None:
        return
    with _lock:
        _subscribers.discard(callback)


def _deliver(event: dict, fns: list[typing.Callable[[dict], None]]) -> bool:
    """Deliver *event* to every callback in *fns* (no lock held)."""
    delivered = False
    for fn in fns:
        try:
            fn(event)
            delivered = True
        except Exception:
            log.debug("[event_bus] subscriber raised", exc_info=True)
    return delivered


def publish(event: dict) -> bool:
    """Broadcast *event* to every subscriber, synchronously.

    Returns
    -------
    bool
        ``True`` if at least one subscriber accepted the event
        (returned without raising).  ``False`` if there are no
        subscribers or every subscriber raised.

    Notes
    -----
    - Synchronous: subscribers are called in the publisher's thread.
      This preserves the previous ``_push_event_now`` semantics
      (existing tests assert that the callable was invoked by the
      time ``publish`` returns).
    - PERF-2: When called from a real-time audio thread (``audio-worker``
      or ``PortAudio``-prefixed), fan-out is deferred to a single-worker
      ``ThreadPoolExecutor`` so the RT thread returns in microseconds.
      Synchronous path is preserved for all other threads.
    - Exception isolation: a subscriber that raises is logged at
      DEBUG level and skipped.  Other subscribers still receive
      the event.  See ``TestSubscriberExceptionIsolation``.
    - The subscriber list is snapshotted under the lock before
      iteration, so ``unsubscribe`` from within a subscriber
      callback does not raise ``RuntimeError: Set changed size
      during iteration`` and the unsubscribed callback will not be
      re-invoked on subsequent publishes.
    """
    with _lock:
        fns = list(_subscribers)
    if not fns:
        return False
    # PERF-2: defer fan-out when called from an RT thread.
    if _is_rt_thread():
        try:
            _get_deferred_executor().submit(_deliver, event, fns)
        except RuntimeError:
            # Executor was shut down (process exit); fall back to sync.
            return _deliver(event, fns)
        # Best-effort: we can't know eventual per-subscriber outcome
        # without blocking, so report True (subscribers are queued).
        return True
    return _deliver(event, fns)


def _subscriber_count() -> int:
    """Return the current number of subscribers (for tests/diagnostics).

    Not part of the public API; exposed for assertions in
    ``tests/test_event_bus.py`` and for the backward-compat shims
    in ``ipc_server.py``.
    """
    with _lock:
        return len(_subscribers)


def shutdown() -> None:
    """M-22: shut down the deferred-publish ThreadPoolExecutor.

    The single-worker ``ThreadPoolExecutor`` lazily created by
    ``_get_deferred_executor()`` is a process-global resource that was
    previously never explicitly shut down. On ``quit()`` / process
    exit, the executor's worker thread (a non-daemon by default in
    CPython's ``ThreadPoolExecutor``) keeps the interpreter alive
    until ``concurrent.futures`` interpreter-finalization runs — and
    if a deferred ``_deliver`` call is stuck on a slow subscriber
    (e.g. a stalled socket.sendall to the Electron renderer), the
    shutdown can hang for several seconds. Calling this from
    ``ShutdownController._do_cleanup`` releases the worker promptly
    so it doesn't contribute to shutdown latency.

    Idempotent — safe to call multiple times. After this call,
    ``_deferred_executor`` is set to ``None`` so the next RT-thread
    ``publish`` lazily creates a fresh executor (or, if the process
    is exiting, the ``RuntimeError`` branch in ``publish`` falls
    back to synchronous delivery). Already-queued ``_deliver`` tasks
    are NOT cancelled — ``shutdown(wait=False)`` lets them finish on
    the worker thread, so in-flight events are not lost.
    """
    global _deferred_executor
    with _deferred_executor_lock:
        executor = _deferred_executor
        _deferred_executor = None
    if executor is not None:
        try:
            executor.shutdown(wait=False)
        except Exception:
            log.debug(
                "[event_bus] deferred executor shutdown failed",
                exc_info=True,
            )


# ──────────────────────────────────────────────────────────────────────────
# G4-M-18: ConfigChangeListener protocol + typed config-change channel.
#
# ``config_applier.apply_config_side_effects`` is currently a ~200-line
# if-chain that dispatches each changed config field to the relevant
# module's setter (volume_ducker.set_smart_duck_enabled,
# ai_enhancement.set_enabled, recording.set_*, etc.). Adding a new
# config-reactive module means editing the chain AND the test file —
# a fragility that G4-M-18 calls out as Medium severity.
#
# This block provides the SUBSCRIPTION INFRASTRUCTURE only: the
# :class:`ConfigChangeListener` protocol, ``subscribe_config_changes``,
# ``unsubscribe_config_changes``, and the internal
# ``_publish_config_change`` fan-out function. Agent 2-j owns
# ``config_applier.py`` and is coordinated (via the worklog) to wire
# ``_publish_config_change(updates)`` into the post-apply fan-out
# WITHOUT removing the existing if-chain in the same PR — the if-chain
# becomes a transitional fallback while subscribers migrate, then is
# deleted in a follow-up.
#
# Design notes:
# - The protocol is ``runtime_checkable`` so existing modules that
#   already expose ``on_config_changed`` can be registered without
#   inheriting from a base class — duck typing is preserved.
# - The subscriber set is SEPARATE from the generic
#   ``event_bus._subscribers`` set so a misconfigured caller that
#   publishes a normal UI event (e.g. ``bubble_level``) can't
#   accidentally trigger every config-change listener.
# - Fan-out is synchronous and runs on the publisher's thread. Config
#   changes are low-frequency (~1/sec max during a Settings drag)
#   and the listeners are fast (set a flag, flip a toggle). The
#   PERF-2 RT-thread deferral is NOT applied here because no RT
#   thread ever publishes a config change.
# - Listener exceptions are isolated (logged at DEBUG, skipped) so
#   one misbehaving listener doesn't block the others — matches the
#   semantics of the generic ``publish`` path.
# ──────────────────────────────────────────────────────────────────────────


@runtime_checkable
class ConfigChangeListener(Protocol):
    """Protocol for modules that react to ``Config`` mutations.

    A listener exposes a single method:

    .. py:method:: on_config_changed(updates)

        Called by :func:`_publish_config_change` after
        ``config_applier.apply_config`` has committed the validated
        updates to the :class:`Config` instance.

        :param updates: a flat ``{field_name: new_value}`` dict
            containing every field that was changed in this apply
            cycle. The values are the post-validation, post-``setattr``
            values — listeners can read them directly off the dict
            without re-fetching from the Config object. Listeners MUST
            NOT mutate the dict (it is shared across all listeners).
        :type updates: dict

    Implementations should be idempotent: the same ``updates`` dict
    may be published more than once during a single drag interaction
    (the renderer debounces ``set_config`` IPC calls but doesn't
    deduplicate identical values). A listener that toggles a heavy
    resource on/off should diff against its own cached "last applied"
    state and no-op when nothing actually changed.

    Future config-reactive modules should implement this protocol and
    register via :func:`subscribe_config_changes` rather than adding
    a new branch to the ``apply_config_side_effects`` if-chain.
    """

    def on_config_changed(self, updates: dict) -> None: ...


# Separate subscriber set + lock so config-change fan-out is isolated
# from the generic event-bus publish path. Using the same
# ``_subscribers`` set would risk a config listener being invoked by
# an unrelated ``bubble_level`` event if a caller fat-fingered the
# event dict's ``type`` field.
_config_change_listeners: set[ConfigChangeListener] = set()
_config_change_lock = threading.RLock()


def subscribe_config_changes(listener: ConfigChangeListener) -> None:
    """Register *listener* to receive every config-change fan-out.

    Calling with ``None`` is a no-op (mirrors :func:`subscribe`'s
    None-handling). Duplicate listeners are stored only once (set
    semantics, mirroring :func:`subscribe`). Safe to call from any
    thread.

    The listener is registered by identity (``__hash__`` /
    ``__eq__``) — for a stateful listener that needs to be
    unregistered later, hold a reference to the instance and pass
    the SAME instance to :func:`unsubscribe_config_changes`.
    """
    if listener is None:
        return
    with _config_change_lock:
        _config_change_listeners.add(listener)


def unsubscribe_config_changes(listener: ConfigChangeListener) -> None:
    """Unregister *listener* from config-change fan-out.

    Safe to call with a listener that was never registered (no-op).
    Safe to call with ``None`` (no-op). Safe to call from any thread.
    """
    if listener is None:
        return
    with _config_change_lock:
        _config_change_listeners.discard(listener)


def _publish_config_change(updates: dict) -> bool:
    """Fan out *updates* to every registered :class:`ConfigChangeListener`.

    Called by ``config_applier.apply_config`` (agent 2-j owns that
    file — coordination note in the worklog) AFTER the validated
    updates have been ``setattr``'d onto the :class:`Config`
    instance. This is the future replacement for the
    ``apply_config_side_effects`` if-chain; the if-chain remains as a
    transitional fallback until every config-reactive module has
    migrated to a listener.

    Parameters
    ----------
    updates
        A flat ``{field_name: new_value}`` dict of every field that
        was changed in this apply cycle. The dict is shared across
        all listeners — they MUST NOT mutate it.

    Returns
    -------
    bool
        ``True`` if at least one listener accepted the event
        (returned without raising).  ``False`` if there are no
        listeners or every listener raised. Mirrors the semantics of
        :func:`publish`.

    Notes
    -----
    - Synchronous: listeners are called in the publisher's thread.
      Config changes are low-frequency so this is fine; do NOT add
      PERF-2-style deferral here (RT threads never publish config
      changes).
    - Listener exception isolation: a listener that raises is logged
      at DEBUG level (with ``exc_info``) and skipped. Other
      listeners still receive the event. Matches the generic
      ``publish`` semantics.
    - The listener list is snapshotted under the lock before
      iteration so a listener that (un)subscribes itself or another
      listener during fan-out does not raise ``RuntimeError: Set
      changed size during iteration``.
    """
    with _config_change_lock:
        listeners = list(_config_change_listeners)
    if not listeners:
        return False
    delivered = False
    for listener in listeners:
        try:
            listener.on_config_changed(updates)
            delivered = True
        except Exception:
            log.debug(
                "[event_bus] config-change listener raised",
                exc_info=True,
            )
    return delivered


def _config_change_listener_count() -> int:
    """Return the current number of config-change listeners (tests only).

    Not part of the public API; exposed for assertions in
    ``tests/test_event_bus.py`` and for diagnostic logging.
    """
    with _config_change_lock:
        return len(_config_change_listeners)
