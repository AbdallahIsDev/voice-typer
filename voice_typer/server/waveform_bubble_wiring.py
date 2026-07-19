"""RW-9 god-class decomposition: WaveformBubbleWiring — extracted from VoiceTyperApp.

Owns the wiring between the ``WaveformBubble`` coordinator
(``voice_typer.server.waveform``) and the IPC server's push-event
bus. The bubble itself is a frameless, always-on-top ``BrowserWindow``
owned by the Electron main process; this module emits push events
through ``voice_typer.server.event_bus`` so listeners don't need to hold
a reference to the app or IPC server (avoids closure-capture bugs that
broke the bubble on first run).

Methods (preserved verbatim from ``VoiceTyperApp._wire_waveform_bubble``):

    - ``_wire_waveform_bubble`` — forwards the bubble's 4 callbacks
      (``on_show``, ``on_hide``, ``on_level``, ``on_set_state``) to the
      IPC server. Includes the bubble-level-pusher background worker.

State migrated from ``VoiceTyperApp`` (these were created lazily inside
``_wire_waveform_bubble`` on first call; they now live on
``WaveformBubbleWiring.__init__`` for explicit ownership):

    - ``self._bubble_level_queue: queue.Queue[dict | None] | None``
    - ``self._bubble_level_worker_stop: threading.Event | None``
    - ``self._bubble_level_worker: threading.Thread | None``
    - ``self._last_bubble_level_push_ts: float``

The actual wiring code remains idempotent (the
``if not hasattr(self, ...) or self._... is None:`` guards are preserved
verbatim) so calling ``_wire_waveform_bubble`` twice (e.g. in tests after
a stop/start cycle) reuses the existing queue and worker thread.

A new ``stop()`` helper mirrors the bubble-worker-shutdown block that
lived in ``VoiceTyperApp._do_cleanup`` (app.py:1469-1480). The primary
agent should replace that block with a call to
``self.waveform_wiring.stop()`` when wiring the delegate.

Risk (per docs/rw9-god-class-decomposition.md §5.5): MEDIUM — the bubble
level worker has threading concerns (bounded queue + daemon thread +
sentinel shutdown). Extraction is conceptually clean but the worker's
lifecycle was intertwined with ``_do_cleanup`` (which stopped the
worker on shutdown). The ``stop()`` method restores that integration
point as a single, idempotent helper.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported only under TYPE_CHECKING to avoid a circular import at
    # runtime — ``voice_typer.server.app`` imports
    # ``voice_typer.server.waveform_bubble_wiring`` (this module) when
    # the primary agent wires the delegate.
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class WaveformBubbleWiring:
    """Owns wiring of the waveform bubble coordinator → IPC push events.

    RW-9 Phase 6: extracted from ``VoiceTyperApp``. The app passes itself
    (``app``) as a back-reference so ``WaveformBubbleWiring`` can:

    - Read ``app._waveform_bubble`` (the ``WaveformBubble`` coordinator
      owned by the app)
    - Read ``app._thread_registry`` (the central ``ThreadRegistry`` so
      the bubble-level-pusher daemon thread is tracked for shutdown)

    Threading contract (PERF-NEW-001 / BUBBLE-FIX-4.1):

    The ``on_level`` callback fires from the PortAudio thread at the
    device's native chunk rate (~31 Hz @ 16 kHz / blocksize 512,
    ~94 Hz @ 48 kHz). Calling ``_push_event_now`` directly held the IPC
    server's ``_lock`` for ``json.dumps`` + ``socket.sendall``, which on
    a slow Electron receive window stalled the audio thread and
    triggered xruns. The actual IPC send is therefore pushed to a
    background queue drained by a low-priority daemon thread.

    The queue is bounded (``maxsize=64``) so a stuck Electron client
    can't cause unbounded memory growth on the Python side; when full,
    the audio thread drops the sample (the next one will pick up the
    latest smoothed level from ``update_level``'s low-pass filter).
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app
        # PERF-NEW-001: dedicated queue + worker thread for bubble level
        # pushes. Bounded so a stuck Electron client can't cause
        # unbounded memory growth on the Python side. Created lazily in
        # ``_wire_waveform_bubble`` (the original code created them
        # idempotently on first call via ``hasattr`` guards — we
        # pre-declare them here as ``None`` so the wiring code's
        # ``is None`` check still triggers creation on first call, and
        # ``stop()`` can detect "not yet wired" without ``hasattr``).
        self._bubble_level_queue: queue.Queue[dict | None] | None = None
        self._bubble_level_worker_stop: threading.Event | None = None
        self._bubble_level_worker: threading.Thread | None = None
        # BUBBLE-FIX-4.1: throttle timestamp for the 16 ms / ~60 Hz
        # ``on_level`` push gate. Lives on the wiring instance so the
        # ``on_level`` closure (which captures ``self``) reads/writes
        # this attribute directly.
        self._last_bubble_level_push_ts: float = 0.0

    # ─── Waveform Bubble (IPC push) ───────────────────────────────────

    def _wire_waveform_bubble(self) -> None:
        """Forward waveform bubble events to the IPC server.

        The bubble itself is a frameless, always-on-top ``BrowserWindow``
        owned by the Electron main process.  We just emit push events;
        the IPC server is reached via the module-level hook in
        ``voice_typer.server.ipc_server`` so listeners don't need to
        hold a reference to the app or server (avoids closure-capture
        bugs that broke the bubble on first run).
        """
        from voice_typer.server import event_bus

        app = self._app

        def _push_bubble_show() -> None:
            sent = event_bus.publish({"type": "bubble_show"})
            log.info("[WAVEFORM] bubble.show() fired; push=%s", "OK" if sent else "NO IPC")

        def _push_bubble_hide() -> None:
            event_bus.publish({"type": "bubble_hide"})

        def _push_bubble_level(rms: float, peak: float) -> None:
            # PERF-NEW-001 / PERF-NEW-015: this callback fires from the
            # PortAudio thread at the device's native chunk rate
            # (~31 Hz @ 16 kHz / blocksize 512, ~94 Hz @ 48 kHz).
            # Calling _push_event_now directly was holding the IPC
            # server's _lock for json.dumps + socket.sendall, which on
            # a slow Electron receive window stalled the audio thread
            # and triggered xruns.  We push the actual IPC send to a
            # background queue drained by a low-priority daemon thread.
            #
            # BUBBLE-FIX-4.1: the previous throttle (33 ms / ~30 Hz) sat
            # exactly at the 32 ms chunk interval for 16 kHz devices, so
            # PortAudio timing jitter caused irregular accept/drop
            # patterns and the visualizer froze.  Lowered to 16 ms
            # (~60 Hz) so every chunk is delivered; the bounded queue
            # (maxsize=64) and worker thread handle backpressure.  Each
            # message is ~40 bytes JSON, so 60 msg/s is trivial for TCP.
            now = time.monotonic()
            last = getattr(self, "_last_bubble_level_push_ts", 0.0)
            if now - last < 0.016:  # 16 ms = ~60 Hz
                return
            self._last_bubble_level_push_ts = now
            q = getattr(self, "_bubble_level_queue", None)
            if q is None:
                return  # wiring not complete yet
            with contextlib.suppress(queue.Full):
                # Queue is full — the worker thread fell behind.  Drop
                # this sample; the next one will pick up the latest
                # smoothed level from update_level's low-pass filter.
                q.put_nowait(
                    {
                        "type": "bubble_level",
                        "data": {"rms": float(rms), "peak": float(peak)},
                    }
                )

        # PERF-NEW-001: dedicated queue + worker thread for bubble
        # level pushes.  Bounded so a stuck Electron client can't
        # cause unbounded memory growth on the Python side.  Created
        # idempotently — if _wire_waveform_bubble is called twice
        # (e.g. in tests after a stop/start cycle), the existing
        # queue and worker are reused.
        if not hasattr(self, "_bubble_level_queue") or self._bubble_level_queue is None:
            self._bubble_level_queue: queue.Queue[dict | None] = queue.Queue(maxsize=64)
        if not hasattr(self, "_bubble_level_worker_stop") or self._bubble_level_worker_stop is None:
            self._bubble_level_worker_stop = threading.Event()

        def _bubble_level_worker() -> None:
            """Drain the bubble_level queue and push events to the IPC server.

            PERF-3: coalesce stale levels — after dequeuing an item, drain
            any newer items that piled up (non-blocking) and keep only the
            latest. Older frames are dropped silently (they've been
            superseded by the newer level). This converts a slow-renderer
            ~128s freeze into a single publish of the most recent level.
            """
            q = self._bubble_level_queue
            stop = self._bubble_level_worker_stop
            while not stop.is_set():
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                # PERF-3: drain any newer items that piled up while we
                # were processing this one. Keep only the latest level.
                # The None shutdown sentinel is honoured inline (we break
                # out of the outer loop if we see it during drain).
                while True:
                    try:
                        newer = q.get_nowait()
                    except queue.Empty:
                        break
                    if newer is None:
                        # Shutdown sentinel arrived during drain —
                        # publish the current item, then exit.
                        event_bus.publish(item)
                        q.task_done()
                        return
                    # Drop the older item; promote the newer one.
                    q.task_done()
                    item = newer
                event_bus.publish(item)
                q.task_done()

        if (
            not hasattr(self, "_bubble_level_worker")
            or self._bubble_level_worker is None
            or not self._bubble_level_worker.is_alive()
        ):
            self._bubble_level_worker = threading.Thread(
                target=_bubble_level_worker,
                name="bubble-level-pusher",
                daemon=True,
                # RACE-016: daemon=True is acceptable because the bubble
                # level worker is a UI-only push; on shutdown the IPC
                # server is torn down first and the worker's queue will
                # be drained by the atexit handler.
            )
            self._bubble_level_worker.start()
            # THREAD-REGISTRY: register the bubble-level-pusher so
            # ``shutdown_all()`` can signal and join it during
            # ``quit()``. This closes the "leaked daemon" gap noted at
            # app.py:1377 — the worker is now tracked centrally and
            # joined on shutdown (with a 1.0s timeout matching the
            # existing _do_cleanup() join). The existing
            # _do_cleanup() path still sets the stop event + enqueues
            # the None sentinel as a safety net; both paths are
            # idempotent.
            app._thread_registry.register(
                name="bubble-level-pusher",
                thread=self._bubble_level_worker,
                stop_event=self._bubble_level_worker_stop,
                join_timeout=1.0,
            )

        def _push_bubble_set_state(state: str) -> None:
            event_bus.publish(
                {
                    "type": "bubble_set_state",
                    "data": {"state": state},
                }
            )

        def _push_bubble_config(cfg: Any) -> None:
            """UX-10: push the bubble-relevant subset of config to the
            Electron bubble renderer so it can decide whether to show the
            mic button (the bubble is sandboxed and receives NO get_config
            otherwise). Emits a ``bubble_config`` event carrying just the
            two keys the bubble needs. Fires once at startup and again on
            every ``set_config`` that touches either key (see
            ``config_handlers`` / service ``apply_config`` push path).
            """
            event_bus.publish(
                {
                    "type": "bubble_config",
                    "data": {
                        "bubble_behavior": getattr(cfg, "bubble_behavior", "show_on_record"),
                        "bubble_click_to_toggle": getattr(cfg, "bubble_click_to_toggle", True),
                        "bubble_mic_button": getattr(cfg, "bubble_mic_button", True),
                    },
                }
            )

        app._waveform_bubble.on_show = _push_bubble_show
        app._waveform_bubble.on_hide = _push_bubble_hide
        app._waveform_bubble.on_level = _push_bubble_level
        app._waveform_bubble.on_set_state = _push_bubble_set_state
        app._waveform_bubble.on_config = _push_bubble_config
        log.info("[WAVEFORM] listeners wired on bubble coordinator")

    # ─── Shutdown integration ─────────────────────────────────────────

    def stop(self) -> None:
        """Stop the bubble-level-pusher worker thread.

        Mirrors the shutdown block that lived in
        ``VoiceTyperApp._do_cleanup`` (app.py:1469-1480). Idempotent —
        safe to call before ``_wire_waveform_bubble`` has run (in which
        case the worker / queue / stop event are still ``None`` and this
        is a no-op) and safe to call multiple times.

        The primary agent should replace the inline
        ``self._bubble_level_worker_stop.set() / put_nowait(None) /
        join(timeout=1.0)`` block in ``_do_cleanup`` with a single call
        to ``self.waveform_wiring.stop()`` when wiring the delegate.
        """
        # PERF-NEW-001: stop the bubble level worker so it doesn't
        # try to push to a torn-down IPC server during shutdown.
        try:
            if hasattr(self, "_bubble_level_worker_stop") and self._bubble_level_worker_stop is not None:
                self._bubble_level_worker_stop.set()
                if hasattr(self, "_bubble_level_queue") and self._bubble_level_queue is not None:
                    with contextlib.suppress(queue.Full):
                        self._bubble_level_queue.put_nowait(None)  # sentinel
                if hasattr(self, "_bubble_level_worker") and self._bubble_level_worker is not None:
                    self._bubble_level_worker.join(timeout=1.0)
        except Exception as e:
            log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)
