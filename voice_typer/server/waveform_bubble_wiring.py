"""Waveform bubble UI wiring into the app/tray surface."""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported only under TYPE_CHECKING to avoid a circular import at
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class WaveformBubbleWiring:
    """Owns wiring of the waveform bubble coordinator → IPC push events.

    Phase 6: extracted from ``VoiceTyperApp``. The app passes itself
        (``app``) as a back-reference so ``WaveformBubbleWiring`` can:

        - Read ``app._waveform_bubble`` (the ``WaveformBubble`` coordinator
          owned by the app)
        - Read ``app._thread_registry`` (the central ``ThreadRegistry`` so
          the bubble-level-pusher daemon thread is tracked for shutdown)

    Threading contract (PERF- / BUBBLE-):

        The ``on_level`` callback fires from the PortAudio thread at the
        recorder's chunk rate, ~31 Hz at every native sample rate now
        that the stream blocksize is rate-scaled (≈32 ms of audio per
        chunk, 512-sample floor; a fixed 512 block used to make 48 kHz
        devices fire at ~94 Hz). Calling ``_push_event_now`` directly held the IPC
        server's ``_lock`` for ``json.dumps`` + ``socket.sendall``, which on
        a slow predecessor receive window stalled the audio thread and
        triggered xruns. The actual IPC send is therefore pushed to a
        background queue drained by a low-priority daemon thread.

        The queue is bounded (``maxsize=64``) so a stuck predecessor client
        can't cause unbounded memory growth on the Python side; when full,
        the audio thread drops the sample (the next one will pick up the
        latest smoothed level from ``update_level``'s low-pass filter).
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app
        # PERF-: dedicated queue + worker thread for bubble level
        self._bubble_level_queue: queue.Queue[dict | None] | None = None
        self._bubble_level_worker_stop: threading.Event | None = None
        self._bubble_level_worker: threading.Thread | None = None
        # BUBBLE-: throttle timestamp for the 16 ms / ~60 Hz
        self._last_bubble_level_push_ts: float = 0.0

    def _wire_waveform_bubble(self) -> None:
        """Forward waveform bubble events to the IPC server."""
        from voice_typer.server import event_bus

        app = self._app

        def _push_bubble_show() -> None:
            sent = event_bus.publish({"type": "bubble_show"})
            log.info("[WAVEFORM] bubble.show() fired; push=%s", "OK" if sent else "NO IPC")

        def _push_bubble_hide() -> None:
            event_bus.publish({"type": "bubble_hide"})

        def _push_bubble_level(rms: float, peak: float) -> None:
            # PERF-3 drain handle backpressure on the consumer side, so
            now = time.monotonic()
            if now - self._last_bubble_level_push_ts < 0.008:  # 8 ms = ~125 Hz
                return
            self._last_bubble_level_push_ts = now
            q = self._bubble_level_queue
            if q is None:
                return  # wiring not complete yet
            with contextlib.suppress(queue.Full):
                # Queue is full, the worker thread fell behind.  Drop
                q.put_nowait(
                    {
                        "type": "bubble_level",
                        "data": {"rms": float(rms), "peak": float(peak)},
                    }
                )

        # PERF-: dedicated queue + worker thread for bubble
        if self._bubble_level_queue is None:
            self._bubble_level_queue: queue.Queue[dict | None] = queue.Queue(maxsize=64)
        if self._bubble_level_worker_stop is None:
            self._bubble_level_worker_stop = threading.Event()

        def _bubble_level_worker() -> None:
            """Drain the bubble_level queue and push events to the IPC server.

            PERF-3: coalesce stale levels, after dequeuing an item, drain
            any newer items that piled up (non-blocking) and keep only the
            latest. Older frames are dropped silently (they've been
            superseded by the newer level). This converts a slow-renderer
            ~128s freeze into a single publish of the most recent level.
            """
            q = self._bubble_level_queue
            stop = self._bubble_level_worker_stop
            # Narrowing (not suppression): the attributes are Optional only
            assert q is not None and stop is not None
            while not stop.is_set():
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                # PERF-3: drain any newer items that piled up while we
                while True:
                    try:
                        newer = q.get_nowait()
                    except queue.Empty:
                        break
                    if newer is None:
                        # Shutdown sentinel arrived during drain —
                        event_bus.publish(item)
                        q.task_done()
                        return
                    # Drop the older item; promote the newer one.
                    q.task_done()
                    item = newer
                event_bus.publish(item)
                q.task_done()

        # __init__ pre-declares _bubble_level_worker (as None),
        if self._bubble_level_worker is None or not self._bubble_level_worker.is_alive():
            self._bubble_level_worker = threading.Thread(
                target=_bubble_level_worker,
                name="bubble-level-pusher",
                daemon=True,
                # RACE-016: daemon=True is acceptable because the bubble
            )
            self._bubble_level_worker.start()
            # THREAD-REGISTRY: register the bubble-level-pusher so
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
            """push the bubble-relevant subset of config to the"""
            event_bus.publish(
                {
                    "type": "bubble_config",
                    "data": {
                        "bubble_behavior": getattr(cfg, "bubble_behavior", None) or "show_on_record",
                        "bubble_click_to_toggle": getattr(cfg, "bubble_click_to_toggle", None) or True,
                        "bubble_mic_button": getattr(cfg, "bubble_mic_button", None) or True,
                        # theme sync. The renderer's useThemeSync hook
                        "theme_mode": getattr(cfg, "theme_mode", None) or "system",
                        "theme_preset": getattr(cfg, "theme_preset", None) or "default",
                        # None is a valid value for custom_theme (means "no
                        "custom_theme": getattr(cfg, "custom_theme", None),
                        # UI text size: the bubble's ``useThemeSync`` hook
                        "text_size": getattr(cfg, "text_size", None) or 14,
                        # Persisted drag position: ``bubble_x`` / ``bubble_y``
                        "bubble_x": getattr(cfg, "bubble_x", None),
                        "bubble_y": getattr(cfg, "bubble_y", None),
                    },
                }
            )

        app._waveform_bubble.on_show = _push_bubble_show
        app._waveform_bubble.on_hide = _push_bubble_hide
        app._waveform_bubble.on_level = _push_bubble_level
        app._waveform_bubble.on_set_state = _push_bubble_set_state
        app._waveform_bubble.on_config = _push_bubble_config
        log.info("[WAVEFORM] listeners wired on bubble coordinator")

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
        # PERF-: stop the bubble level worker so it doesn't
        try:
            if self._bubble_level_worker_stop is not None:
                self._bubble_level_worker_stop.set()
                if self._bubble_level_queue is not None:
                    with contextlib.suppress(queue.Full):
                        self._bubble_level_queue.put_nowait(None)  # sentinel
                if self._bubble_level_worker is not None:
                    self._bubble_level_worker.join(timeout=1.0)
        except Exception as e:
            log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)
        # break the closure reference cycle
        try:
            bubble = getattr(self._app, "_waveform_bubble", None)
            if bubble is not None:
                bubble.on_show = None
                bubble.on_hide = None
                bubble.on_level = None
                bubble.on_set_state = None
                bubble.on_config = None
        except Exception as e:
            log.debug("[SHUTDOWN] waveform bubble callback clear failed: %s", e)
