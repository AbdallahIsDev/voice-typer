"""god-class decomposition: WaveformBubbleWiring — extracted from VoiceTyperApp.

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

    Phase 6: extracted from ``VoiceTyperApp``. The app passes itself
        (``app``) as a back-reference so ``WaveformBubbleWiring`` can:

        - Read ``app._waveform_bubble`` (the ``WaveformBubble`` coordinator
          owned by the app)
        - Read ``app._thread_registry`` (the central ``ThreadRegistry`` so
          the bubble-level-pusher daemon thread is tracked for shutdown)

    Threading contract (PERF- / BUBBLE-):

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
        # PERF-: dedicated queue + worker thread for bubble level
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
        # BUBBLE-: throttle timestamp for the 16 ms / ~60 Hz
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
            # PERF- / PERF-: this callback fires from the
            # PortAudio thread at the device's native chunk rate
            # (~31 Hz @ 16 kHz / blocksize 512, ~94 Hz @ 48 kHz).
            # Calling _push_event_now directly was holding the IPC
            # server's _lock for json.dumps + socket.sendall, which on
            # a slow Electron receive window stalled the audio thread
            # and triggered xruns.  We push the actual IPC send to a
            # background queue drained by a low-priority daemon thread.
            #
            # BUBBLE-: the previous throttle (33 ms / ~30 Hz) sat
            # exactly at the 32 ms chunk interval for 16 kHz devices, so
            # PortAudio timing jitter caused irregular accept/drop
            # patterns and the visualizer froze.  Lowered to 16 ms
            # (~60 Hz) so every chunk is delivered; the bounded queue
            # (maxsize=64) and worker thread handle backpressure.  Each
            # message is ~40 bytes JSON, so 60 msg/s is trivial for TCP.
            #
            # 16 ms still dropped ~36% of 48 kHz chunks (94 Hz
            # source vs 60 Hz gate). Lowered to 8 ms (~125 Hz) so every
            # chunk at 48 kHz passes the gate; the bounded queue +
            # PERF-3 drain handle backpressure on the consumer side, so
            # the gate no longer needs to be a backpressure mechanism.
            now = time.monotonic()
            if now - self._last_bubble_level_push_ts < 0.008:  # 8 ms = ~125 Hz
                return
            self._last_bubble_level_push_ts = now
            q = self._bubble_level_queue
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

        # PERF-: dedicated queue + worker thread for bubble
        # level pushes.  Bounded so a stuck Electron client can't
        # cause unbounded memory growth on the Python side.  Created
        # idempotently — if _wire_waveform_bubble is called twice
        # (e.g. in tests after a stop/start cycle), the existing
        # queue and worker are reused.
        #
        # __init__ pre-declares these attributes (as None), so
        # the ``hasattr`` guards below are dead branches. Use direct
        # ``is None`` checks instead — clearer intent, fewer ops.
        if self._bubble_level_queue is None:
            self._bubble_level_queue: queue.Queue[dict | None] = queue.Queue(maxsize=64)
        if self._bubble_level_worker_stop is None:
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

        # __init__ pre-declares _bubble_level_worker (as None),
        # so the ``hasattr`` guard is a dead branch. Direct ``is None``
        # check is sufficient — and re-creating the worker when the
        # previous one has exited (e.g. after stop()) is still handled
        # by the ``not is_alive()`` clause.
        if self._bubble_level_worker is None or not self._bubble_level_worker.is_alive():
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
            """push the bubble-relevant subset of config to the
                        Electron bubble renderer so it can decide whether to show the
                        mic button (the bubble is sandboxed and receives NO get_config
                        otherwise). Emits a ``bubble_config`` event carrying the
                        bubble-behavior keys plus the theme triplet (``theme_mode``,
                        ``theme_preset``, ``custom_theme``) so the bubble renderer's
            ``useThemeSync`` hook () can paint with the same preset
                        as the main app instead of always falling through to the OS
                        ``prefers-color-scheme`` default. Fires once at startup and
                        again on every ``set_config`` that touches any of these keys
                        (see ``config_handlers`` push path — the trigger list there
                        must include the theme keys for the bubble to actually receive
            theme updates; see  handoff note in the worklog).

            Fix: ``getattr(cfg, name, default)`` returned the attribute
            value even when it was explicitly ``None`` (e.g. a Config
            loaded from a partial / corrupt file where these fields
            were missing or nulled) — so the bubble renderer would
            receive ``None`` instead of the documented default and
            fall through to OS prefs. Use
            ``getattr(cfg, name, None) or default`` so a missing /
            null value falls back to the default. ``custom_theme`` is
            exempt because ``None`` is its valid default (no custom
            theme configured).
            """
            event_bus.publish(
                {
                    "type": "bubble_config",
                    "data": {
                        "bubble_behavior": getattr(cfg, "bubble_behavior", None) or "show_on_record",
                        "bubble_click_to_toggle": getattr(cfg, "bubble_click_to_toggle", None) or True,
                        "bubble_mic_button": getattr(cfg, "bubble_mic_button", None) or True,
                        # theme sync. The renderer's useThemeSync hook
                        # (bubble-components.tsx) already consumes these three
                        # fields; previously the backend never sent them, so
                        # the bubble always fell through to OS prefs.
                        "theme_mode": getattr(cfg, "theme_mode", None) or "system",
                        "theme_preset": getattr(cfg, "theme_preset", None) or "default",
                        # None is a valid value for custom_theme (means "no
                        # custom theme configured") — keep getattr without
                        # the ``or default`` fallback so we don't paper over
                        # a legitimately-cleared custom_theme.
                        "custom_theme": getattr(cfg, "custom_theme", None),
                        # Persisted drag position: ``bubble_x`` / ``bubble_y``
                        # are forwarded VERBATIM (plain ``getattr`` without an
                        # ``or`` fallback) so hosts can restore the user's
                        # last dragged position after a restart. Both fields
                        # are optional-int config keys (``None`` = "never
                        # dragged — use default centering"); a coordinate of
                        # ``0`` is legitimate, so the truthiness-based
                        # fallback used for the enum/bool keys above would
                        # silently discard it. Hosts validate the pair
                        # against the current displays at restore time (both
                        # must be non-null and on-screen).
                        #
                        # These two keys also make this push event the
                        # durable-position transport to BOTH runtimes
                        # (Electron main caches it from the forwarded frame;
                        # the Tauri host's WS reader caches it from the same
                        # frame), so a Settings top/bottom toggle that clears
                        # them server-side propagates as a fresh push
                        # carrying ``None``.
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
        # PERF-: stop the bubble level worker so it doesn't
        # try to push to a torn-down IPC server during shutdown.
        #
        # __init__ pre-declares all three attributes (as None),
        # so the ``hasattr`` guards are dead branches. Direct ``is
        # not None`` checks are clearer and faster.
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
        # ``app -> app._waveform_bubble (WaveformBubble) -> .on_* (closure)
        # -> closure.__closure__[0] (self=WaveformBubbleWiring)
        # -> self._app (back to app)`` by nulling the 5 callbacks after
        # the worker thread is stopped. Without this, any future codepath
        # that recreates WaveformBubble or WaveformBubbleWiring (e.g. a
        # 'restart bubble' debug feature, or tests doing stop/start
        # cycles) would leak the old WaveformBubbleWiring instance via
        # the closure -> self cycle. Cheap, idempotent, breaks the cycle
        # deterministically rather than relying on the cyclic GC.
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
