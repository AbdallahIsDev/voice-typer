"""Monitoring public API for the level_monitor package ().

Contains the continuous level-monitoring public API (``start_monitoring``,
``stop_monitoring``, ``is_monitoring``, ``get_level``,
``get_level_diagnostics``, ``update_level_processor``) plus the
``device_lost`` / ``mic_level`` push-event helpers that the worker
thread (in :mod:`.worker`) and the PortAudio ``finished_callback``
both call into.

 (idle-timeout): ``get_level`` records the timestamp of every
IPC poll on ``_state._last_get_level_poll_ts``. The worker thread
(:mod:`.worker`) checks this timestamp on every iteration and
auto-stops the stream when no poll has been received in
``_state._LEVEL_IDLE_TIMEOUT_SEC`` seconds (default 60.0 — a
defensive backstop, NOT a 5 s reaction time; see the attribute's
docstring in :mod:`._state` for why it was raised). This
prevents the RNNoise filter chain from pegging a core when the tray
bubble is hidden but the frontend forgot to call ``level_monitor_stop``.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import types
from typing import TYPE_CHECKING

import numpy as np

from voice_typer.server._audio_constants import (
    WHISPER_SAMPLE_RATE,
    scaled_audio_blocksize,
)

from ._state import _state

if TYPE_CHECKING:
    pass

log = logging.getLogger("voice_typer.server.level_monitor")


def _emit_device_lost(source: str) -> None:
    """Publish a ``device_lost`` IPC event (idempotent via ``_device_lost_emitted``).

    Safe to call from inside ``_monitor_lock`` — uses a lock-free
    check-and-set on ``_device_lost_emitted`` (GIL-safe for bools in
    CPython) to avoid re-entrant lock acquisition.
    """
    if _state._device_lost_emitted:
        return
    _state._device_lost_emitted = True
    try:
        from voice_typer.server import event_bus

        event_bus.publish({"type": "device_lost", "data": {"source": source}})
        log.info("[LEVEL-MON] device_lost event emitted (source=%s)", source)
    except Exception:
        log.debug("[LEVEL-MON] Failed to publish device_lost event", exc_info=True)


def _make_stream_finished_guard(stream_cell: dict[str, object]):
    """Build a PortAudio ``finished_callback`` bound to the stream in *stream_cell*.

    CRITICAL INVARIANT: PortAudio fires ``PaStreamFinishedCallback`` not
    only when a device vanishes mid-stream but ALSO on every INTENTIONAL
    inactive transition — ``stream.stop()`` / ``stream.close()``, which is
    exactly what a device SWITCH or a page-unmount ``stop_monitoring``
    does to the OLD stream. Treating those as losses emitted a bogus
    ``device_lost`` for perfectly healthy streams: selecting any concrete
    non-default microphone restarted the monitor and instantly showed
    "Selected microphone disconnected".

    The guard captures the stream identity at creation time and only
    reports a loss when the FINISHING stream is still the CURRENT monitor
    stream (an unexpected finish of the live stream = real device loss).
    An old stream finishing after it was already replaced/cleared finds a
    different-or-None ``_monitor_stream`` and stays silent.
    """

    def _on_stream_finished() -> None:
        with _state._monitor_lock:
            current = _state._monitor_stream
            active = _state._monitor_active
        if stream_cell.get("stream") is not current or not active:
            # Intentional stop / replaced by a newer stream — not a loss.
            log.debug("[LEVEL-MON] finished callback ignored (stream replaced or intentionally stopped)")
            return
        with _state._monitor_lock:
            _state._monitor_active = False
        log.warning("[LEVEL-MON] InputStream finished - device disconnected")
        _emit_device_lost("stream_finished")

    return _on_stream_finished


def _level_stream_finished() -> None:
    """Legacy unguarded finished callback — device disconnected mid-stream.

    Kept for tests/back-compat; production streams use the identity-aware
    :func:`_make_stream_finished_guard` callback instead.
    """
    with _state._monitor_lock:
        _state._monitor_active = False
    log.warning("[LEVEL-MON] InputStream finished - device disconnected")
    _emit_device_lost("stream_finished")


def _push_mic_level(rms: float, peak: float, active: bool) -> None:
    """Coalesce + enqueue a mic_level push-event payload.

    Called from the level worker thread (under ``_monitor_lock``).
    Coalesces to ~30 Hz (``_MIC_LEVEL_COALESCE_SEC``) so a fast chunk
    rate doesn't flood the event_bus. The actual publish happens on a
    dedicated worker thread (``_mic_level_worker_loop``) so the RT
    callback / level worker is never blocked on event_bus publish
    latency.
    """
    now = time.monotonic()
    if now - _state._mic_level_last_push_ts < _state._MIC_LEVEL_COALESCE_SEC:
        return
    _state._mic_level_last_push_ts = now
    payload = {"level": float(rms), "peak": float(peak), "active": bool(active)}
    with _state._mic_level_queue_lock:
        # deque(maxlen=16) auto-evicts oldest on overflow, so we don't
        # need explicit get-then-put. The lock serializes against the
        # worker thread's drain loop below.
        _state._mic_level_queue.append(payload)
    _state._mic_level_worker_wake_event.set()


def _mic_level_worker_loop() -> None:
    """Background worker thread that publishes ``mic_level`` push-events.

    Drains the coalesce queue (keeping the latest payload only — PERF-3
    latest-only drop pattern) and publishes via ``event_bus.publish``.
    Runs on a dedicated thread so the level worker / PortAudio callback
    is never blocked on event_bus publish latency.
    """
    while True:
        _state._mic_level_worker_wake_event.wait(timeout=1.0)
        _state._mic_level_worker_wake_event.clear()
        if _state._mic_level_worker_stop:
            return
        # PERF-3 latest-only: drain all pending payloads, keep the last.
        latest = None
        with _state._mic_level_queue_lock:
            while _state._mic_level_queue:
                latest = _state._mic_level_queue.popleft()
        if latest is not None:
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "mic_level",
                        "data": {
                            "level": latest["level"],
                            "peak": latest["peak"],
                            "active": latest["active"],
                        },
                    },
                )
            except Exception:
                log.debug("[LEVEL-MON] Failed to publish mic_level event", exc_info=True)


def _ensure_mic_level_worker_running() -> None:
    """Start the mic_level push-event worker thread if not already running.

    Idempotent: if a worker from a previous ``start_monitoring`` call is
    still alive, reuse it. Called from ``start_monitoring``.
    """
    if _state._mic_level_worker_thread is not None and _state._mic_level_worker_thread.is_alive():
        return
    _state._mic_level_worker_stop = False
    _state._mic_level_worker_thread = threading.Thread(
        target=_mic_level_worker_loop,
        name="level-monitor-mic-level-worker",
        daemon=True,
    )
    _state._mic_level_worker_thread.start()


def _stop_mic_level_worker() -> None:
    """Signal the mic_level worker thread to stop and join it (best-effort)."""
    _state._mic_level_worker_stop = True
    _state._mic_level_worker_wake_event.set()
    t = _state._mic_level_worker_thread
    if t is not None and t is not threading.current_thread():
        with contextlib.suppress(Exception):
            t.join(timeout=1.0)
    _state._mic_level_worker_thread = None


# ── Public API: monitoring ──────────────────────────────────────────


def is_monitoring() -> bool:
    """Return True if the continuous level monitor is active.

    Returns:
        True if the level monitor stream is currently running.
    """
    with _state._monitor_lock:
        return _state._monitor_active


def get_level() -> dict:
    """Return the current audio level from the monitor.

    records the timestamp of this IPC poll on
    ``_state._last_get_level_poll_ts`` so the level worker thread can
    auto-stop the stream when no poll has been received in
    ``_state._LEVEL_IDLE_TIMEOUT_SEC`` seconds (default 5.0). This
    prevents the RNNoise filter chain from pegging a core when the
    tray bubble is hidden but the frontend forgot to call
    ``level_monitor_stop``.

    Returns:
        dict with keys:
            - "level": float (0-1) — current RMS level, scaled.
            - "peak": float (0-1) — peak level since last call.
            - "active": bool — whether the monitor stream is running.
    """
    # record the poll timestamp so the worker thread can detect
    # the idle condition (no polls in N seconds → auto-stop).
    _state._last_get_level_poll_ts = time.monotonic()
    with _state._monitor_lock:
        return {
            # Display gain: shared constant (see ``_state._LEVEL_DISPLAY_GAIN``)
            # so this poll response and the ``mic_level`` push payload scale
            # identically. The bubble's visualizer applies the same
            # multiplier client-side in rmsToNorm().
            "level": min(1.0, _state._monitor_level * _state._LEVEL_DISPLAY_GAIN),
            "peak": _state._monitor_peak,
            "active": _state._monitor_active,
        }


def get_level_diagnostics() -> dict:
    """Return runtime diagnostics for the level monitor ().

    Exposes ``_dropped_level_chunks`` (chunks dropped because the worker
    thread couldn't keep up with the PortAudio callback rate) plus ring
    buffer fill state, for telemetry / debugging. The per-burst
    ``dropped_level_chunks`` counter is reset every 5s by
    ``_level_worker_loop`` after logging, so this snapshot is
    point-in-time (drops since the last 5s log emission).

    ALSO exposes ``total_dropped_level_chunks`` — the CUMULATIVE
    counter that NEVER resets in production. The per-burst counter
    flakes under sustained overload (a test that snapshots it
    before/after a single overflow can see 0 if the worker drained it
    between the snapshot and the check); the cumulative counter is the
    stable field for "drops since ``start_monitoring`` first ran"
    telemetry. Lives on ``worker.py`` as a module-level global (NOT on
    ``_state`` — see ``worker.py``'s cumulative-counter comment block
    for the rationale); read here via deferred import so ``monitoring.py``
    does not take a top-level dependency on ``worker.py`` (which would
    short-circuit the package's lazy-import benefits).

    No existing IPC handler wires this through to the frontend (the
    ``level_monitor_*`` IPC family is start/stop/status only); the
    function is exposed for future diagnostics-IPC use and for in-process
    callers (e.g. ``service.py`` could surface it in
    ``level_monitor_status``).

    Returns:
        dict with keys:
            - ``dropped_level_chunks`` (int): chunks dropped since the
              last 5s throttled log (per-burst delta; resets on each
              log emission).
            - ``total_dropped_level_chunks`` (int): cumulative drops
              since ``start_monitoring`` first ran (NEVER reset in
              production).
            - ``ring_buffer_capacity`` (int): configured max capacity.
            - ``ring_buffer_len`` (int): current fill level.
            - ``monitor_active`` (bool): whether the monitor stream is
              running.
    """
    # ``_dropped_level_chunks`` is incremented in the PortAudio callback
    # (RT thread) and reset in the worker thread (here, via the 5s log);
    # ``int`` read is atomic under CPython's GIL, so no lock is needed
    # for a point-in-time snapshot.
    #
    # Deferred import of ``worker`` to read the cumulative
    # counter. ``int`` read is GIL-atomic; the worker is the ONLY
    # writer in production. Deferred (not top-level) because
    # ``monitoring.py`` already imports from ``_state`` at module top,
    # and adding a top-level ``from . import worker`` would create a
    # circular load dependency at package import time (``__init__.py``
    # imports both submodules in sequence; ``worker`` reads
    # ``_state._level_worker_*`` etc., so it must load AFTER
    # ``_state``). Deferred import inside the function body side-steps
    # the ordering constraint and only pays the (cached) import lookup
    # cost when diagnostics are actually requested.
    from . import worker as _worker_mod

    return {
        "dropped_level_chunks": _state._dropped_level_chunks,
        "total_dropped_level_chunks": _worker_mod._total_dropped_level_chunks,
        "ring_buffer_capacity": _state._LEVEL_RING_BUFFER_CAPACITY,
        "ring_buffer_len": len(_state._level_ring_buffer),
        "monitor_active": _state._monitor_active,
    }


def update_level_processor(config_dict: dict) -> None:
    """Create or update the audio processor for the live level bar.

    When enabled, the level monitor's callback will run audio through
    this processor before computing RMS/peak so the level bar reflects
    the active noise filters in real-time (high-pass, noise gate,
    RNNoise — but not post-capture which is offline-only).

    All mutations of ``_state._level_processor`` and the snapshot read
    of ``_state._monitor_sample_rate`` happen under ``_state._monitor_lock``
    so the level worker thread (which reads ``_level_processor`` from
    ``_process_level_chunk``) sees a consistent pair: the processor is
    always built against the sample rate of the stream that will
    consume its output, and a concurrent ``start_monitoring`` /
    ``stop_monitoring`` can't tear the assignment. The AudioProcessor
    construction itself runs OUTSIDE the lock so a slow ``__init__``
    (RNNoise model load) doesn't block ``get_level()`` / worker drain.

    The ``config_dict`` is stashed on ``_state._level_processor_config``
    so ``start_monitoring``'s "different device — restart" branch can
    rebuild the chain at the new native sample rate after a hot-swap.
    Pre-fix, the restart path only updated ``_monitor_sample_rate`` and
    left the old processor (built against the previous device's rate)
    in place — IIR ``zi`` arrays + RNNoise ``_carry`` were tuned to the
    wrong rate.

    ``config_dict["level_bar_filtered"]`` (default False) opts IN to
    running the filter chain for the cosmetic level bar. When False
    (default), the filter chain is SKIPPED for the cosmetic bar (RMS
    is computed on raw audio) — the filter chain still runs when a
    test recording is active (the "after" WAV needs the filtered
    audio). This avoids pegging a core at 31-94 Hz with RNNoise for a
    non-functional visualization.

    Args:
        config_dict: dict with noise_filter_enabled, noise_filter_highpass,
            noise_filter_gate, noise_filter_rnnoise keys, etc. May also
            contain ``level_bar_filtered`` (bool, default False).
    """
    # Stash the config_dict BEFORE the early-return disable path so the
    # restart branch can distinguish "user explicitly disabled filters"
    # (stash = config_dict with enabled=False → rebuild yields None
    # processor) from "no update_level_processor call ever happened"
    # (stash = None → skip rebuild). A defensive copy so a later
    # caller-side mutation of the dict doesn't race the restart path's
    # read.
    try:
        _state._level_processor_config = dict(config_dict)
    except Exception:
        _state._level_processor_config = None

    # Stash the ``level_bar_filtered`` flag on _state so the worker can
    # read it without re-parsing the config dict. Default False — the
    # cosmetic bar uses raw audio only.
    level_bar_filtered = bool(config_dict.get("level_bar_filtered", False))
    with _state._monitor_lock:
        _state._level_bar_filtered = level_bar_filtered

    if not config_dict.get("noise_filter_enabled", True):
        with _state._monitor_lock:
            _state._level_processor = None
        log.debug("[LEVEL-MON] Level processor disabled")
        return

    try:
        # ADR 0007: AudioProcessor takes a config-like object (anything
        # with ``noise_filter_*`` attributes). The dict already has the
        # right keys; ``types.SimpleNamespace`` exposes them as attrs.
        # ``build_chain()`` reads each field via ``getattr(..., default)``
        # so missing keys fall back to ADR 0007 defaults.
        from voice_typer.server.audio_processor import AudioProcessor

        # Snapshot the current monitor sample rate under the lock so
        # the AudioProcessor is built against the rate of the stream
        # that will actually feed it. ``_monitor_sample_rate`` is
        # mutated by ``start_monitoring`` (also under this lock), so
        # reading it bare here could race a restart on a different
        # device and yield a processor tuned to the wrong rate.
        with _state._monitor_lock:
            sample_rate = _state._monitor_sample_rate

        # Construct the processor OUTSIDE the lock: ``AudioProcessor.__init__``
        # may load the RNNoise model (5–50 ms) and we must not block
        # ``get_level()`` / the level worker thread while it loads.
        ap_config = types.SimpleNamespace(**config_dict)
        new_processor = AudioProcessor(ap_config, sample_rate=sample_rate, quiet=True)

        # Assign under the lock so the worker's read of
        # ``_level_processor`` is pair-consistent with the snapshot
        # above (no torn update where the processor is for the previous
        # rate).
        with _state._monitor_lock:
            _state._level_processor = new_processor

        log.info(
            "[LEVEL-MON] Level processor updated: highpass=%s, gate=%s, method=%s",
            config_dict.get("noise_filter_highpass", True),
            config_dict.get("noise_filter_gate", True),
            config_dict.get("noise_suppression_method", "rnnoise"),
        )
    except Exception as exc:
        log.warning("[LEVEL-MON] Failed to create level processor: %s", exc)
        with _state._monitor_lock:
            _state._level_processor = None


def start_monitoring(mic_id: str | None = None) -> dict:
    """Start continuous real-time audio level monitoring.

    If monitoring is already active, this is a no-op unless `mic_id`
    differs from the current device — in that case the old stream is
    stopped and a new one is opened on the requested device.

    Args:
        mic_id: Device index string (e.g. "3") or None for system default.

    Returns:
        dict with {"success": bool, "message": str, "sample_rate": int}.
    """
    import sounddevice as sd

    with _state._monitor_lock:
        # Already running on the same device — no-op
        if _state._monitor_active and _state._monitor_mic_id == mic_id:
            return {
                "success": True,
                "message": "Already monitoring",
                "sample_rate": _state._monitor_sample_rate,
            }

        # Already running on a DIFFERENT device — restart
        if _state._monitor_active:
            old_stream = _state._monitor_stream
            _state._monitor_stream = None
            _state._monitor_active = False
            _state._monitor_level = 0.0
            _state._monitor_peak = 0.0
            _state._monitor_mic_id = None
            # Reset the OLD processor's filter state before closing the
            # stream so IIR ``zi`` arrays + RNNoise ``_carry`` don't
            # bleed residuals into the next ``process_chunk`` call after
            # the new stream opens at a different native rate. The
            # ``stop_monitoring`` path already does this; the restart
            # branch was missing it. Guarded: a failing ``reset()`` must
            # NOT block the restart — the new ``update_level_processor``
            # call below replaces the processor entirely, so a
            # partially-reset old processor is discarded anyway.
            old_processor = _state._level_processor
            if old_processor is not None:
                with contextlib.suppress(Exception):
                    old_processor.reset()
            # Close old stream outside the lock to avoid blocking
        else:
            old_stream = None

    # Close old stream (if any) without holding the lock.
    # the ``if old_stream is not None`` guard was previously
    # fused into the trailing comment, so it was never executed and
    # pyrefly reported ``Object of class `NoneType` has no attribute
    # `stop`/`close``` at the unconditional call sites below.  Split
    # the guard onto its own line so it actually runs.
    if old_stream is not None:
        try:
            old_stream.stop()
            old_stream.close()
        except Exception as exc:
            log.debug("[LEVEL-MON] Close old stream: %s", exc)

    # Open new stream
    # Pre-declare the post-lock locals so a defensive ``return`` from
    # inside the ``with`` block (or a future refactor that adds an
    # early-exit branch) never trips UnboundLocalError at the
    # ``if config_snapshot is not None`` check below.
    config_snapshot: dict | None = None
    result: dict | None = None
    with _state._monitor_lock:
        # Resolve the requested mic id to a live PortAudio index. Handles
        # every id shape (stable "<host api>|<name>[#N]" ids from
        # list_microphones, legacy bare-index strings, None = system
        # default); an unresolvable id falls back to the system default
        # instead of crashing the monitor.
        device = None
        if mic_id is not None:
            try:
                from voice_typer.server.server_platform import (
                    resolve_mic_id_to_device_index,
                )

                device = resolve_mic_id_to_device_index(mic_id)
            except Exception:
                log.debug(
                    "[LEVEL-MON] mic id %r resolution failed; using system default",
                    mic_id,
                    exc_info=True,
                )
                device = None

        try:
            dev_info_raw = sd.query_devices(kind="input") if device is None else sd.query_devices(device)
            # ``query_devices`` is overloaded to return either a
            # ``dict`` (single device) or a ``DeviceList`` (tuple).  The
            # ``default_samplerate`` key only exists on the dict form, so
            # narrow before indexing.
            native_rate = (
                int(dev_info_raw["default_samplerate"]) if isinstance(dev_info_raw, dict) else WHISPER_SAMPLE_RATE
            )
        except Exception:
            native_rate = WHISPER_SAMPLE_RATE

        _state._monitor_sample_rate = native_rate
        _state._monitor_level = 0.0
        _state._monitor_peak = 0.0

        def callback(indata, frames, time_info, status):
            #  (c-review PERF-03): the PortAudio callback runs
            # on the real-time audio thread and must complete in well
            # under the ~32 ms PortAudio deadline. The blocksize below
            # is scaled to ``max(512, int(native_rate * 0.032))`` so a
            # chunk always represents ~32 ms of audio regardless of the
            # device native rate — at 16 kHz the block is 512 samples
            # (32 ms), at 44.1 kHz it is 1411 samples (~32 ms), and at
            # 48 kHz it is 1536 samples (32 ms). Without that scaling,
            # a fixed 512-sample block on a 48 kHz device produced a
            # ~10.7 ms chunk (≈94 Hz callback rate) that flooded the
            # ring buffer and worker. To meet this deadline the
            # callback does ONLY:
            #
            #   1. ``indata.copy()`` — allocates a ~2 KB float32 buffer
            #      for 512 samples (negligible).
            #   2. ``deque.append`` — atomic under CPython's GIL for
            #      SPSC, ~1 µs.
            #   3. ``Event.set()`` — wakes the worker thread, ~1 µs.
            #
            # All heavy work (filter chain via ``_level_processor``,
            # ``np.abs`` / ``np.sqrt(np.mean(...))`` for RMS/peak, test
            # chunk accumulation + quality metrics) runs on the
            # dedicated worker thread ``_level_worker_loop``. Without
            # this refactor, RNNoise could spend 5–50 ms per chunk in
            # the callback and miss the deadline whenever the level
            # monitor was active.
            #
            # The ``status`` flag (PortAudio xrun / underflow) is
            # forwarded to the worker so log throttling / xrun tracking
            # can happen off the RT thread.
            try:
                _state._level_ring_buffer.append((indata.copy(), status))
            except Exception:
                # deque.append only raises on capacity-overflow if a
                # maxlen isn't set — but we set one, so this is purely
                # defensive. Don't let a callback error kill the stream.
                log.debug("[LEVEL-MON] ring buffer append failed", exc_info=True)
                return
            if len(_state._level_ring_buffer) >= _state._LEVEL_RING_BUFFER_CAPACITY:
                # Ring buffer full — worker can't keep up. Drop the
                # oldest chunk to make room (deque with maxlen already
                # does this, but we want to count drops for telemetry).
                # NOTE: deque(maxlen=N) silently drops the OLDEST entry
                # on overflow, so the append above already succeeded
                # and the buffer is at capacity. We just count.
                _state._dropped_level_chunks += 1
                # Emit a one-shot WARNING on the first drop of a
                # burst so operators see the overflow immediately (the
                # worker thread's 5s throttled log only fires later).
                # The latch is reset by the worker once it drains the
                # counter; this bounds the RT-thread log to ≤1 per
                # burst while still surfacing the first drop.
                if not _state._first_drop_warning_emitted:
                    _state._first_drop_warning_emitted = True
                    log.warning(
                        "[LEVEL-MON] ring buffer full — dropped audio chunk "
                        "(worker thread can't keep up with the PortAudio "
                        "callback rate; consider disabling RNNoise or "
                        "reducing the filter chain cost)",
                    )
            _state._level_worker_wake_event.set()

        # Scale the block size with the device native sample rate so
        # every chunk represents ~32 ms of audio (chunk rate ≈ 31 Hz,
        # regardless of whether the device runs at 16 / 44.1 / 48 kHz).
        # Previously ``blocksize=512`` was hardcoded, which on a 48 kHz
        # device produced ~10.7 ms chunks (≈94 Hz callback rate) and
        # made the 64-entry ring buffer hold only ~0.68 s of audio —
        # far short of the ~2 s window it is sized for. The shared
        # helper applies the same 512-sample floor on low-rate devices
        # so PortAudio doesn't get pathologically small blocks.
        blocksize = scaled_audio_blocksize(native_rate)

        # Cell for the not-yet-created InputStream: the finished-callback
        # guard needs the stream's IDENTITY, but Python has no forward
        # reference at construction time. The callback can only fire
        # after ``start()``/``close()``, by which point the cell is filled.
        stream_cell: dict[str, object] = {}

        try:
            stream = sd.InputStream(
                samplerate=native_rate,
                channels=1,
                dtype=np.float32,
                device=device,
                callback=callback,
                finished_callback=_make_stream_finished_guard(stream_cell),
                blocksize=blocksize,
            )
            stream.start()
            stream_cell["stream"] = stream
            _state._monitor_stream = stream
            _state._monitor_active = True
            _state._monitor_mic_id = mic_id
            _state._device_lost_emitted = False
            _state._consecutive_zero_chunks = 0
            # seed the idle-timeout poll timestamp so the worker
            # doesn't immediately auto-stop the stream right after
            # start (the first ``get_level`` IPC poll will arrive
            # shortly from the frontend).
            _state._last_get_level_poll_ts = time.monotonic()
            _ensure_mic_level_worker_running()

            #  (c-review PERF-03): start the dedicated worker
            # thread that drains ``_level_ring_buffer`` and runs the
            # heavy filter chain + RMS/peak + test-chunk accumulation.
            # Idempotent: if a worker from a previous ``start_monitoring``
            # call is still alive (e.g. test fixtures that reset module
            # state without calling ``stop_monitoring``), reuse it —
            # the worker checks ``_monitor_active`` inside its loop.
            # Local import to avoid a top-level circular dependency
            # (worker.py imports monitoring.py for _emit_device_lost).
            from .worker import _ensure_level_worker_running

            _ensure_level_worker_running()

            log.info(
                "[LEVEL-MON] Monitoring started: mic=%s, sr=%d",
                mic_id or "default",
                native_rate,
            )
            result = {
                "success": True,
                "message": "Monitoring active",
                "sample_rate": native_rate,
            }
            # Snapshot the stashed config INSIDE the lock so the
            # post-lock ``update_level_processor`` call rebuilds the
            # chain against the NEW ``_monitor_sample_rate`` we just
            # wrote. Pre-fix, the restart path left the old processor
            # (built against the previous device's rate) in place after
            # a hot-swap, so the IIR ``zi`` arrays + RNNoise ``_carry``
            # were tuned to the wrong rate. ``None`` ⇒ no
            # ``update_level_processor`` call ever happened (the user
            # hasn't toggled any noise filter); skip the rebuild in
            # that case so we don't fabricate a default config the
            # user never asked for.
            config_snapshot = _state._level_processor_config
        except Exception as exc:
            log.warning("[LEVEL-MON] Failed to start monitoring: %s", exc)
            _state._monitor_stream = None
            _state._monitor_active = False
            _state._monitor_mic_id = None
            return {"success": False, "message": str(exc), "sample_rate": native_rate}

    # Rebuild the level processor at the new native rate. Must happen
    # OUTSIDE ``_monitor_lock`` because ``update_level_processor``
    # acquires the same lock (and constructs the AudioProcessor — which
    # may load the RNNoise model — outside it). Best-effort: a rebuild
    # failure must NOT turn a successful stream-open into a failed
    # ``start_monitoring`` return value, so the result dict is
    # preserved regardless.
    if config_snapshot is not None:
        try:
            update_level_processor(config_snapshot)
        except Exception:
            log.debug(
                "[LEVEL-MON] post-restart update_level_processor failed",
                exc_info=True,
            )

    return result


def stop_monitoring() -> dict:
    """Stop the continuous level monitor stream.

    Also cancels any in-progress test recording.

    Returns:
        dict with {"success": bool, "message": str}.
    """
    # Cancel any active test first.
    # Local import to avoid a top-level circular dependency
    # (test_recording.py imports monitoring.py for _stop_mic_level_worker
    # indirectly via stop_monitoring; lazy import breaks the cycle).
    from .test_recording import _cancel_test_locked
    from .worker import _stop_level_worker

    _cancel_test_locked()
    _stop_mic_level_worker()

    already_stopped = False
    stream = None
    with _state._monitor_lock:
        if not _state._monitor_active:
            #  (c-review PERF-03): monitoring was already
            # stopped. Remember this so we can still stop a possibly
            # leaked worker thread — but that must happen OUTSIDE the lock
            # (see below), because the worker acquires ``_monitor_lock``
            # while draining queued chunks; joining it while we hold the
            # lock could stall up to the join timeout.
            already_stopped = True
        else:
            _state._monitor_active = False
            stream = _state._monitor_stream
            _state._monitor_stream = None
            _state._monitor_level = 0.0
            _state._monitor_peak = 0.0
            _state._monitor_mic_id = None

    if already_stopped:
        #  (c-review PERF-03): stop the (possibly leaked) worker
        # thread OUTSIDE the lock so the join can't stall against the
        # worker's own ``_monitor_lock`` acquisition. Safe to call
        # repeatedly.
        _stop_level_worker()
        return {"success": True, "message": "Not monitoring"}

    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.debug("[LEVEL-MON] Stream close: %s", exc)

    # Reset the per-session audio processor filter state so the IIR
    # ``zi`` arrays + RNNoise ``_carry`` don't retain audio-derived
    # residuals from this monitoring session and bleed into the next
    # one. Mirrors the pattern in ``recording/session_state.py`` for
    # the dictation ``AudioProcessor``: the model itself stays loaded;
    # only the per-session filter state is zeroed. Best-effort: a
    # failing ``reset()`` must NOT block the worker shutdown path below.
    if _state._level_processor is not None:
        with contextlib.suppress(Exception):
            _state._level_processor.reset()

    #  (c-review PERF-03): now that the stream is closed (no
    # more callbacks will fire), stop the worker thread so it doesn't
    # spin waiting for chunks that will never arrive.
    _stop_level_worker()

    log.info("[LEVEL-MON] Monitoring stopped")
    return {"success": True, "message": "Monitoring stopped"}


def _idle_timeout_auto_stop() -> bool:
    """auto-stop the monitor stream if the IPC idle-timeout has fired.

    Called from the level worker loop on every iteration. Returns True
    if the stream was auto-stopped (so the caller can ``return`` from
    the worker loop — see ``_level_worker_loop``).

    The stream is closed (``stream.stop()`` / ``stream.close()``) AND
    the worker thread exits its loop on the next iteration (the caller
    ``return``s after a True return). The next ``start_monitoring``
    call spawns a fresh worker thread via
    ``_ensure_level_worker_running`` (thread creation is ~1 ms —
    negligible vs. the 60 s idle window). This eliminates the 4 Hz
    idle wakeups (250 ms backstop ``wait()`` timeout × forever) that
    would otherwise drain the battery on an idle laptop.

    IMPORTANT: this function must NOT call ``stop_monitoring()`` —
    ``stop_monitoring`` calls ``_stop_level_worker`` which joins the
    worker thread, and we ARE the worker thread. Deadlock would result.
    Instead, we close the stream directly and flip ``_monitor_active``
    to False. The caller (``_level_worker_loop``) is responsible for
    clearing ``_level_worker_thread`` and ``return``ing — it does NOT
    call ``_stop_level_worker`` (which would deadlock joining itself).
    """
    if not _state._monitor_active:
        return False
    # Consider BOTH activity timestamps. After the push-event migration
    # the Microphone page and the always-visible bubble consume
    # ``mic_level`` push events and may only call ``get_level`` once on
    # mount. Checking only ``_last_get_level_poll_ts`` would falsely
    # trip the idle timeout while the frontend is actively listening
    # via push events. The MORE RECENT of the two timestamps governs
    # the idle check.
    last_activity_ts = max(
        _state._last_get_level_poll_ts,
        _state._mic_level_last_push_ts,
    )
    if last_activity_ts <= 0.0:
        # No poll or push has ever been recorded — don't auto-stop yet
        # (the stream was just started; the first ``get_level`` poll or
        # ``mic_level`` push will arrive shortly).
        return False
    now = time.monotonic()
    if (now - last_activity_ts) < _state._LEVEL_IDLE_TIMEOUT_SEC:
        return False

    # Idle timeout has fired — close the stream.
    log.info(
        "[LEVEL-MON] idle-timeout: auto-stopping monitor stream (no get_level poll in %.1fs)",
        _state._LEVEL_IDLE_TIMEOUT_SEC,
    )
    stream = None
    with _state._monitor_lock:
        if not _state._monitor_active:
            return False
        _state._monitor_active = False
        stream = _state._monitor_stream
        _state._monitor_stream = None
        _state._monitor_level = 0.0
        _state._monitor_peak = 0.0
        _state._monitor_mic_id = None
        # Reset BOTH idle-timestamp clocks so the next
        # ``start_monitoring`` call seeds them freshly. The push
        # timestamp is reset here (alongside the poll timestamp) so a
        # stale push from this session can't keep a freshly-restarted
        # stream alive past its real idle window.
        _state._last_get_level_poll_ts = 0.0
        _state._mic_level_last_push_ts = 0.0

    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.debug("[LEVEL-MON] idle-timeout stream close: %s", exc)

    # Stop the mic_level push-event worker too (no more levels to push).
    _stop_mic_level_worker()
    return True
