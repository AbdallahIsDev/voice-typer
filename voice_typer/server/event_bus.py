"""In-process event bus for broadcasting events to subscribers.

B-1: extracted from ``voice_typer.server.ipc_server._push_event_now``
to break the tight coupling between 12+ domain modules and the IPC
transport layer.

Architecture
------------
This module is the LEAF of the dependency tree.  It imports only
stdlib plus :mod:`voice_typer.server.log_rate_limit` (which is itself
stdlib-only, so no circular-import risk); any other module can import
it without risk of a circular import.

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
* ``tray_menu`` — ADR-0020 §6.5 / GT-53; serialized menu model pushed
  to the Tauri sidecar host only (``TAURI_SIDECAR=1``). On Electron/
  pystray the native menu is the single source of truth and this is
  a no-op. Payload: ``{items:[<menu node dict>]}``.
* ``tray_state`` — ADR-0020 §6.5 / GT-53; tray icon name + tooltip
  pushed to the Tauri sidecar host only (``TAURI_SIDECAR=1``). On
  Electron/pystray the ``TrayIcon`` is updated directly so emitting
  a parallel event would double-publish. Payload: ``{icon:str?,
  tooltip:str?}`` (at least one field present).
* ``consent_required`` — UX-005 / GT-53; emitted by ``service/model.py``
  when the renderer must prompt for HuggingFace consent before a model
  download can proceed. Payload: ``{provider:str, model:str,
  message:str}``.
* ``parakeet_cpu_fallback`` — SK-b / GT-53; emitted by
  ``parakeet_engine.py`` when GPU transcription fails and the engine
  falls back to CPU. The tray shows a "(CPU fallback)" status suffix.
  Payload: ``{device:str (="cpu"), reason:str}``.

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

Total: 28 events (26 via ``event_bus.publish`` (``ready`` is also
pushed directly on TCP-server start) + 2 ``IPCServer.push``-only
events = 28 unique event names; the ADR-0020 §2 table lists all 28).

Thread safety
-------------
``subscribe`` / ``unsubscribe`` / ``publish`` are all thread-safe.
A re-entrant lock (``threading.RLock``) guards the subscriber set so
that a subscriber which itself calls ``publish`` (re-entrant publish)
does not deadlock.  Re-entrant publish is not encouraged but is
guaranteed not to deadlock.

Subscriber exception isolation
------------------------------
A subscriber that raises is logged at **WARNING** level (with
``exc_info``) on the FIRST occurrence for that subscriber, then at
DEBUG (without ``exc_info``) on subsequent occurrences — see
:func:`voice_typer.server.log_rate_limit.log_rate_limited`. Production
file handlers run at INFO so the first failure surfaces; rate-limiting
prevents log spam if a subscriber is persistently broken. The
subscriber is then skipped and other subscribers still receive the
event. This matches the previous ``_push_event_now`` semantics (log
and continue) and is verified by
``tests/test_event_bus.py::TestSubscriberExceptionIsolation``.
"""

from __future__ import annotations

import logging
import os
import threading
import typing
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

from voice_typer.server.log_rate_limit import log_rate_limited

# ── DT-42: EVENT_TYPES registry ────────────────────────────────────
# The docstring catalogue above lists every event the system knows about,
# but until DT-42 the list lived ONLY in the docstring — there was no
# Python constant. 30+ ``event_bus.publish({"type": "<name>"})`` call
# sites used bare string literals, and the Rust WS reader
# (``src-tauri/src/sidecar/ws.rs:62-98``) mirrored the list by hand
# (``ALLOWED_EVENT_TYPES: &[&str]``). Drift happened twice (legacy
# aliases documented as "REMOVED"); missed Rust-side updates silently
# dropped events.
#
# This constant is the Python-side source of truth. It mirrors the
# existing ``ERROR_CODES`` pattern in ``ipc/validation.py:98``.
#
# The set is a SUPERSET of the docstring catalogue: it also includes
# events that are emitted but were never added to the docstring
# (``llm_polish_failed``, ``asr_backend_disabled``,
# ``asr_last_resort_unloaded``, ``error``, ``mic_level``,
# ``device_lost``) plus the two ``IPCServer.push``-only events
# (``state_changed``, ``status_change``) so the dev-time assertion in
# ``publish()`` doesn't false-positive on a real call site.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "ready",
        "bubble_show",
        "bubble_hide",
        "bubble_level",
        "bubble_set_state",
        "bubble_config",
        "transcription_final",
        "vocabulary_suggestion",
        "hotkey_capture_cancel",
        "config_changed",
        "history_changed",
        "microphone_test_complete",
        "microphones_changed",
        "audio_clip",
        "recording_started",
        "recording_stopped",
        "download_progress",
        "notification",
        "navigate",
        "show_window",
        "quit_app",
        "relaunch_app",
        "paste_failed",
        "tray_menu",
        "tray_state",
        "consent_required",
        "parakeet_cpu_fallback",
        # IPCServer.push-only (included so assertion doesn't false-positive):
        "state_changed",
        "status_change",
        # Emitted but missing from the docstring catalogue:
        "asr_backend_disabled",
        "asr_last_resort_unloaded",
        "llm_polish_failed",
        "error",
        "mic_level",
        "device_lost",
        "dictation_lost",
    }
)

# DT-42: dev-time assertion gate. Default OFF so production is not
# slowed and existing event_bus unit tests (which publish synthetic
# types like ``"test"``) don't false-positive. Set
# ``VOICE_TYPER_DEBUG_EVENTS=1`` at dev time to opt in.
_DEBUG_EVENTS: bool = os.environ.get("VOICE_TYPER_DEBUG_EVENTS", "") == "1"


def _subscriber_key(fn: typing.Callable[..., typing.Any]) -> str:
    """Return a stable string key identifying *fn* for rate-limit counters.

    Used as the ``key=`` argument to :func:`log_rate_limited` so that each
    distinct subscriber gets its own counter: the FIRST exception from a
    given subscriber logs at WARNING (with full traceback); subsequent
    exceptions from the SAME subscriber log at DEBUG (no traceback) so a
    persistently-broken subscriber doesn't spam the production log.

    Strategy:
    - Bound methods (``self.method``): include ``id(self.__self__)`` so
      two methods bound to different instances get separate counters.
    - Named functions / unbound methods: use ``module.qualname`` —
      stable across calls and unique within a process.
    - Lambdas and C-level callables (``__qualname__`` is ``<lambda>``
      or absent): fall back to ``id(fn)`` — unique for the callable's
      lifetime, which is the only window the counter matters for.
    """
    qualname = getattr(fn, "__qualname__", None) or ""
    module = getattr(fn, "__module__", "") or ""
    # Bound methods: include id() of the bound instance so two methods
    # bound to different instances get separate counters.
    self_obj = getattr(fn, "__self__", None)
    if self_obj is not None:
        return f"{module}.{qualname}@0x{id(self_obj):x}"
    if qualname and qualname != "<lambda>":
        return f"{module}.{qualname}" if module else qualname
    # Lambdas and C-level callables: use id() of the callable itself.
    return f"callable@0x{id(fn):x}"


log = logging.getLogger("voice_typer.server.event_bus")


class _SubscriberSet:
    """A set-like container for event-bus subscribers (PVT-031).

    ``IPCServer`` subscribes with ``subscribe(self.push)`` — a *bound
    method*. A plain ``set`` holds a strong ref to the bound method,
    which holds a strong ref to the IPCServer via ``__self__``. If the
    IPCServer is destroyed without calling ``unsubscribe(self.push)``
    (exception during ``stop()``, crash, ``restart_app``), the bound
    method keeps the IPCServer alive forever — a leak.

    This container fixes the leak by storing bound methods via
    ``weakref.WeakMethod`` (Python-level) or ``weakref.ref(__self__)``
    (C-level like ``list.append``): when the owning instance is GC'd,
    the weak ref's callback fires and the entry is evicted automatically.
    Plain functions / lambdas are stored as strong refs (``weakref.ref``
    of an ephemeral lambda would die immediately).
    """

    def __init__(self) -> None:
        self._strong: set[typing.Callable[[dict], None]] = set()
        # Python bound methods (have __func__), keyed by
        # (id(__self__), id(__func__)). Value is a WeakMethod.
        self._weak_py: dict[tuple[int, int], weakref.WeakMethod] = {}
        # C-level bound methods (e.g. list.append — have __self__ +
        # __name__ but no __func__). Keyed by (id(__self__), __name__).
        # Value is (weakref to __self__, method_name).
        self._weak_c: dict[tuple[int, str], tuple[weakref.ref, str]] = {}
        # Fallback for C-level bound methods whose __self__ is not
        # weakly referenceable (e.g. list, tuple). Same keying as
        # _weak_c so discard finds entries regardless of bucket.
        self._strong_c: dict[tuple[int, str], typing.Callable[[dict], None]] = {}

    @staticmethod
    def _classify(callback: typing.Any) -> str:
        self_obj = getattr(callback, "__self__", None)
        if self_obj is None:
            return "plain"
        if hasattr(callback, "__func__"):
            return "py_bound"
        if hasattr(callback, "__name__"):
            return "c_bound"
        return "plain"

    def add(self, callback: typing.Callable[[dict], None]) -> None:
        kind = self._classify(callback)
        if kind == "py_bound":
            key = (id(callback.__self__), id(callback.__func__))
            if key not in self._weak_py or self._weak_py[key]() is None:
                self._weak_py[key] = weakref.WeakMethod(callback, lambda _ref, k=key: self._weak_py.pop(k, None))
        elif kind == "c_bound":
            key = (id(callback.__self__), callback.__name__)
            existing_weak = self._weak_c.get(key)
            if existing_weak is not None and existing_weak[0]() is not None:
                return
            if key in self._strong_c:
                return
            try:
                ref = weakref.ref(
                    callback.__self__,
                    lambda _r, k=key: self._weak_c.pop(k, None),
                )
            except TypeError:
                self._strong_c[key] = callback
            else:
                self._weak_c[key] = (ref, callback.__name__)
        else:
            self._strong.add(callback)

    def discard(self, callback: typing.Callable[[dict], None]) -> None:
        kind = self._classify(callback)
        if kind == "py_bound":
            self._weak_py.pop((id(callback.__self__), id(callback.__func__)), None)
        elif kind == "c_bound":
            key = (id(callback.__self__), callback.__name__)
            self._weak_c.pop(key, None)
            self._strong_c.pop(key, None)
        else:
            self._strong.discard(callback)

    def clear(self) -> None:
        self._strong.clear()
        self._weak_py.clear()
        self._weak_c.clear()
        self._strong_c.clear()

    def update(self, items: typing.Iterable[typing.Callable[[dict], None]]) -> None:
        for item in items:
            self.add(item)

    def __iter__(self) -> typing.Iterator[typing.Callable[[dict], None]]:
        live: list[typing.Callable[[dict], None]] = list(self._strong)
        for key, ref in list(self._weak_py.items()):
            cb = ref()
            if cb is not None:
                live.append(cb)
            else:
                self._weak_py.pop(key, None)
        for key, (ref, name) in list(self._weak_c.items()):
            obj = ref()
            if obj is not None:
                cb = getattr(obj, name, None)
                if cb is not None:
                    live.append(cb)
                else:
                    self._weak_c.pop(key, None)
            else:
                self._weak_c.pop(key, None)
        live.extend(self._strong_c.values())
        return iter(live)

    def __len__(self) -> int:
        for key in [k for k, r in self._weak_py.items() if r() is None]:
            self._weak_py.pop(key, None)
        for key in [k for k, (r, _n) in self._weak_c.items() if r() is None]:
            self._weak_c.pop(key, None)
        return len(self._strong) + len(self._weak_py) + len(self._weak_c) + len(self._strong_c)

    def __contains__(self, callback: typing.Callable[[dict], None]) -> bool:
        kind = self._classify(callback)
        if kind == "py_bound":
            ref = self._weak_py.get((id(callback.__self__), id(callback.__func__)))
            return ref is not None and ref() is not None
        elif kind == "c_bound":
            key = (id(callback.__self__), callback.__name__)
            if key in self._strong_c:
                return True
            entry = self._weak_c.get(key)
            return entry is not None and entry[0]() is not None
        return callback in self._strong


# PVT-031: weak-ref-aware subscriber set. Bound methods are stored via
# WeakMethod so destroyed subscribers (e.g. an IPCServer that crashed
# during stop() without calling unsubscribe) are GC'd instead of
# leaking forever. Plain functions / lambdas stay strong-ref'd.
_subscribers: _SubscriberSet = _SubscriberSet()

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

# PVT-031: bound the deferred-publish queue. ``ThreadPoolExecutor`` uses
# an unbounded ``SimpleQueue`` internally; a slow subscriber (stalled
# socket.sendall to the Electron renderer) at 60 Hz ``bubble_level``
# fan-out would queue 36,000 tasks over 10 minutes — unbounded memory
# growth under backpressure. The counter tracks in-flight deferred
# tasks; when it exceeds ``_DEFERRED_QUEUE_MAX`` new submissions are
# dropped (with a rate-limited WARNING) so memory is bounded. Dropped
# events are idempotent high-frequency UI updates (bubble_level,
# recording_level) — losing some under backpressure is preferable to
# OOM-killing the audio process.
_DEFERRED_QUEUE_MAX = 256
_deferred_in_flight: int = 0
_deferred_in_flight_lock = threading.Lock()
_deferred_drop_count: int = 0  # cumulative, for diagnostics


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
    """Deliver *event* to every callback in *fns* (no lock held).

    GT-3: subscriber exceptions are logged at WARNING (with
    ``exc_info=True``) on the FIRST occurrence per subscriber, then at
    DEBUG (no ``exc_info``) on subsequent occurrences via
    :func:`log_rate_limited`.  Production file handlers run at INFO so
    the first failure surfaces; rate-limiting prevents a persistently
    broken subscriber from flooding the log.  Other subscribers still
    receive the event.
    """
    delivered = False
    for fn in fns:
        try:
            fn(event)
            delivered = True
        except Exception:
            log_rate_limited(
                log,
                logging.WARNING,
                "[event_bus] subscriber raised",
                exc_info=True,
                key=f"subscriber:{_subscriber_key(fn)}",
            )
    return delivered


def _deliver_deferred(event: dict, fns: list[typing.Callable[[dict], None]]) -> None:
    """Deliver *event* on the deferred-executor thread, then decrement
    the in-flight counter (PVT-031).

    Pairs with the bounded-submit logic in ``publish()`` so the
    in-flight counter is decremented exactly once per submitted task —
    whether the delivery succeeded, a subscriber raised, or the
    executor was shut down mid-flight. Failing to decrement would
    re-introduce the unbounded-queue memory growth (the counter would
    hit ``_DEFERRED_QUEUE_MAX`` and never recover).
    """
    global _deferred_in_flight
    try:
        _deliver(event, fns)
    finally:
        with _deferred_in_flight_lock:
            _deferred_in_flight = max(0, _deferred_in_flight - 1)


def publish(event: dict, *, async_dispatch: bool = False) -> bool:
    """Broadcast *event* to every subscriber.

    Parameters
    ----------
    event:
        The event dict. Must contain a ``"type"`` key (validated at
        dev-time when ``VOICE_TYPER_DEBUG_EVENTS=1``).
    async_dispatch:
        ZR-20: when ``True``, fan-out is deferred to the single-worker
        :class:`ThreadPoolExecutor` so the caller returns immediately
        (subscribers run on the executor thread, not the publisher's
        thread). Useful for non-RT publisher threads that must not
        block on slow IPC writes — e.g. the transcription thread
        calling ``publish({"type": "transcription_final", ...})``
        would otherwise block on ``IPCServer.push`` →
        ``socket.sendall`` to a stalled Electron renderer (seconds of
        latency if the renderer is paused in the debugger).

        When ``False`` (default), subscribers are called synchronously
        in the publisher's thread (existing ``_push_event_now``
        semantics — most tests assert the callable was invoked by the
        time ``publish`` returns).

        The RT-thread auto-defer (PERF-2) takes precedence over this
        flag: audio-worker / PortAudio threads always defer, regardless
        of the ``async_dispatch`` value, so the RT loop never glitches.

    Returns
    -------
    bool
        ``True`` if at least one subscriber accepted the event
        (returned without raising), OR if the event was queued for
        deferred delivery (``async_dispatch=True`` or RT thread).
        ``False`` if there are no subscribers or every subscriber
        raised on the synchronous path.

    Notes
    -----
    - Synchronous (default): subscribers are called in the publisher's
      thread. This preserves the previous ``_push_event_now`` semantics
      (existing tests assert that the callable was invoked by the time
      ``publish`` returns).
    - PERF-2: When called from a real-time audio thread (``audio-worker``
      or ``PortAudio``-prefixed), fan-out is deferred to a single-worker
      ``ThreadPoolExecutor`` so the RT thread returns in microseconds.
      Synchronous path is preserved for all other threads.
    - ZR-20: ``async_dispatch=True`` opts non-RT threads into the same
      deferred path. The bounded queue (PVT-031, ``_DEFERRED_QUEUE_MAX``)
      protects against unbounded memory growth under backpressure.
    - Exception isolation: a subscriber that raises is logged at
      WARNING (with ``exc_info``) on the first occurrence per
      subscriber, then at DEBUG on subsequent occurrences
      (:func:`log_rate_limited`), and skipped. Other subscribers
      still receive the event. See ``TestSubscriberExceptionIsolation``.
    - The subscriber list is snapshotted under the lock before
      iteration, so ``unsubscribe`` from within a subscriber
      callback does not raise ``RuntimeError: Set changed size
      during iteration`` and the unsubscribed callback will not be
      re-invoked on subsequent publishes.
    """
    # DT-42: dev-time membership check. Gated by ``_DEBUG_EVENTS`` (env
    # var ``VOICE_TYPER_DEBUG_EVENTS=1``) so production is not slowed
    # and the existing event_bus unit tests don't false-positive.
    if _DEBUG_EVENTS:
        _event_type = event.get("type")
        assert _event_type in EVENT_TYPES, (
            f"Unknown event type: {_event_type!r}. "
            "Add it to EVENT_TYPES in event_bus.py AND to the Rust "
            "ALLOWED_EVENT_TYPES allowlist in src-tauri/src/sidecar/ws.rs."
        )
    with _lock:
        fns = list(_subscribers)
    if not fns:
        return False
    # PERF-2: defer fan-out when called from an RT thread.
    # ZR-20: also defer when the caller explicitly opts in via
    # ``async_dispatch=True`` (e.g. transcription thread that must not
    # block on slow IPC writes). The RT-thread check takes precedence
    # so audio hot-path latency stays bounded regardless of the flag.
    if _is_rt_thread() or async_dispatch:
        global _deferred_in_flight, _deferred_drop_count
        # PVT-031: bound the deferred queue. If the single worker is
        # backed up (slow subscriber), drop new submissions rather than
        # queuing them indefinitely. The drop is rate-limited so a
        # persistently-slow subscriber produces one WARNING per minute,
        # not 60/sec. Dropped events are idempotent high-frequency UI
        # updates (bubble_level, recording_level); losing some under
        # backpressure is preferable to unbounded memory growth.
        with _deferred_in_flight_lock:
            if _deferred_in_flight >= _DEFERRED_QUEUE_MAX:
                _deferred_drop_count += 1
                would_drop = True
            else:
                _deferred_in_flight += 1
                would_drop = False
        if would_drop:
            log_rate_limited(
                log,
                logging.WARNING,
                "[event_bus] deferred queue at capacity (%d); dropping event (cumulative drops: %d)",
                _DEFERRED_QUEUE_MAX,
                _deferred_drop_count,
                key="event_bus:deferred_drop",
            )
            return True
        try:
            _get_deferred_executor().submit(_deliver_deferred, event, fns)
        except RuntimeError:
            # Executor was shut down (process exit); fall back to sync.
            # Undo the in-flight increment so the counter doesn't leak.
            with _deferred_in_flight_lock:
                _deferred_in_flight = max(0, _deferred_in_flight - 1)
            return _deliver(event, fns)
        return True
    return _deliver(event, fns)


def publish_sync(event: dict) -> bool:
    """Broadcast *event* to every subscriber, synchronously (ZR-20).

    Explicit-synchronous alias for :func:`publish` with
    ``async_dispatch=False``. Use this when the caller needs ordering
    guarantees (subscribers invoked before the caller proceeds) — e.g.
    a sequence of related events where the second depends on the first
    having been processed.

    The default :func:`publish` is already synchronous, so this function
    is primarily a self-documenting call site marker: it makes the
    ordering intent explicit at the call site, and protects against a
    future default-flip of ``publish``'s ``async_dispatch`` parameter
    silently breaking ordering-sensitive callers.

    Notes
    -----
    - The RT-thread auto-defer (PERF-2) still applies: an audio-worker
      thread calling ``publish_sync`` will defer to the executor
      regardless, because the RT loop must not block on subscriber
      fan-out. The ``async_dispatch=False`` flag only controls the
      non-RT path.
    - Returns the same bool as :func:`publish` (True if at least one
      subscriber accepted; False if no subscribers or all raised).
    """
    return publish(event, async_dispatch=False)


def _subscriber_count() -> int:
    """Return the current number of subscribers (for tests/diagnostics).

    Not part of the public API; exposed for assertions in
    ``tests/test_event_bus.py`` and for the backward-compat shims
    in ``ipc_server.py``.
    """
    with _lock:
        return len(_subscribers)


def shutdown() -> None:
    """M-22 / GT-C1-7 / TY-15: shut down the deferred-publish ThreadPoolExecutor.

    This is the SINGLE canonical lifecycle hook for the lazily-created
    ``ThreadPoolExecutor``.  Previously a duplicate ``shutdown_executor()``
    function existed alongside this one — it was deleted in GT-C1-7
    (DRY, Rule 24) because nothing in the codebase called it (only
    ``shutdown()`` is invoked from
    ``shutdown_controller._teardown_event_bus``).

    TY-15: the call now uses ``executor.shutdown(wait=True,
    cancel_futures=True)`` instead of ``wait=False``. ``wait=False``
    returned immediately and did NOT block on already-running or queued
    tasks — but the worker thread is a NON-DAEMON (CPython
    ``ThreadPoolExecutor`` default), so it kept the interpreter alive
    past the ``shutdown()`` call until all queued/in-flight tasks
    finished. The 5s ``_run_with_timeout`` wrapper in
    ``_teardown_event_bus`` was therefore bounding NOTHING (the
    non-blocking call returned in microseconds). With
    ``wait=True, cancel_futures=True``:
      (a) queued-but-not-started tasks are cancelled immediately (they
          are stale by definition on shutdown);
      (b) the call blocks until the in-flight task completes.
    The 5s ``_run_with_timeout`` wrapper then ACTUALLY bounds the wait.
    If the in-flight task exceeds 5s, the wrapper returns ``TIMEOUT``
    and the worker thread is leaked as a daemon (the
    ``_run_with_timeout`` worker is daemon-marked).

    The single-worker ``ThreadPoolExecutor`` lazily created by
    ``_get_deferred_executor()`` is a process-global resource. On
    ``quit()`` / process exit, calling this from
    ``ShutdownController._teardown_event_bus`` releases the worker
    promptly so it doesn't contribute to shutdown latency.

    Idempotent — safe to call multiple times. After this call,
    ``_deferred_executor`` is set to ``None`` so the next RT-thread
    ``publish`` lazily creates a fresh executor (or, if the process
    is exiting, the ``RuntimeError`` branch in ``publish`` falls
    back to synchronous delivery).
    """
    global _deferred_executor
    with _deferred_executor_lock:
        executor = _deferred_executor
        _deferred_executor = None
    if executor is not None:
        try:
            executor.shutdown(wait=True, cancel_futures=True)
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
# - Listener exceptions are isolated (logged at WARNING on the first
#   occurrence per listener, then at DEBUG via ``log_rate_limited``,
#   and skipped) so one misbehaving listener doesn't block the others
#   — matches the semantics of the generic ``publish`` path (GT-3).
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
      at WARNING (with ``exc_info``) on the FIRST occurrence per
      listener, then at DEBUG on subsequent occurrences
      (:func:`log_rate_limited`), and skipped. Other listeners still
      receive the event. Matches the generic ``publish`` semantics
      (GT-3).
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
            # GT-3: same WARNING-on-first / DEBUG-on-repeat policy as
            # the generic ``_deliver`` path. ``listener`` is a
            # ``ConfigChangeListener`` Protocol; ``_subscriber_key``
            # falls back to ``id()`` for protocol-implementing objects
            # without a useful ``__qualname__``.
            log_rate_limited(
                log,
                logging.WARNING,
                "[event_bus] config-change listener raised",
                exc_info=True,
                key=f"config_listener:{_subscriber_key(listener)}",
            )
    return delivered


def _config_change_listener_count() -> int:
    """Return the current number of config-change listeners (tests only).

    Not part of the public API; exposed for assertions in
    ``tests/test_event_bus.py`` and for diagnostic logging.
    """
    with _config_change_lock:
        return len(_config_change_listeners)
