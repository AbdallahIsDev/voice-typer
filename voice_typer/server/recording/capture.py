"""Audio callback dispatch and worker loop for :class:`Recorder` (extracted from ``recorder.py``).

Phase 4.5 — extracted from :mod:`.recorder` to shrink the
3772-LOC ``recorder.py`` god class (see  in ``review.md``).
Owns the audio callback dispatch body, the audio worker thread main
loop body, and the four worker-lifecycle method bodies
(:meth:`AudioCallbackDispatcher.start_audio_worker_body`,
:meth:`stop_audio_worker_body`, :meth:`start_event_worker_body`,
:meth:`stop_event_worker_body`). The Recorder class keeps 1-line
delegators for these methods so existing call sites, subclass
overrides, and ``inspect.getsource`` checks continue to work.

Collaborator pattern
--------------------
:class:`AudioCallbackDispatcher` is constructed by ``Recorder.__init__``
with a back-reference to the owning ``Recorder`` instance
(``AudioCallbackDispatcher(recorder)``). The collaborator reference is
used to access *shared* state that lives on ``Recorder`` and is NOT
moved here:

- ``self._recorder._ring_buffer`` — SPSC ring buffer (deque)
- ``self._recorder._worker_stop_event`` / ``_worker_wake_event`` — worker sync
- ``self._recorder._worker_thread`` — the worker thread reference
- ``self._recorder._recording_event`` — recording gate
- ``self._recorder._process_audio_chunk`` — heavy per-chunk processing
- ``self._recorder._preroll_buffer`` / ``_preroll_active`` / ``_effective_sr`` — preroll state
- ``self._recorder._dropped_ring_chunks`` — ring-buffer overflow counter
- ``self._recorder._ensure_mono`` — mono downmix staticmethod
- ... and any other state referenced in the extracted bodies

Source-inspection contract
--------------------------
``tests/test_recording_and_audio.py::test_callback_does_not_do_heavy_processing``
pins the source of ``Recorder._audio_callback_dispatch`` and asserts
that it CONTAINS the literals ``_ring_buffer.append`` and
``_worker_wake_event`` (: the callback's RT-safe ops must
stay visible in the Recorder's source) and that it does NOT contain
the heavy-pipeline operations (``compute_vad_prob``, ``_get_resample_poly``,
``process_chunk``, ``_vad_update``).

To preserve this contract after extraction, :meth:`dispatch_callback_body`
does NOT perform the ``_ring_buffer.append`` or ``_worker_wake_event.set``
operations itself. Instead it returns either:

- ``None`` — the early-bailout (pre-roll) path was taken; the caller
  (the thin delegator on ``Recorder``) must NOT append to the ring
  buffer or signal the worker.
- a 5-tuple ``(chunk_copy, frames, time_info, status, perf_ts)`` —
  ready to be passed to ``recorder._ring_buffer.append(payload)``. The
  caller then invokes ``recorder._worker_wake_event.set()``.

The thin delegator on ``Recorder._audio_callback_dispatch`` therefore
retains the literal ``self._ring_buffer.append(...)`` and
``self._worker_wake_event.set()`` calls — those are pinned by the
source-inspection test (Option C from the  plan). The PRIMARY
AGENT (not this module) owns the delegator wiring in :mod:`.recorder`.

Patch-path compatibility
------------------------
The two original methods on this module (``dispatch_callback_body`` and
``audio_worker_loop``) do not directly touch ``sounddevice`` — the
callback is invoked by PortAudio (via the delegator on
``Recorder._audio_callback_dispatch``) and the worker only consumes
from the ring buffer. The four worker-lifecycle bodies added by
also do not touch ``sounddevice``. Patches of the form
``monkeypatch.setattr(recording.sd, "InputStream", fake)`` therefore
target :mod:`.recorder` / the package ``__init__.py`` (where ``sd``
still lives) and need no indirection here.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server.log_rate_limit import log_rate_limited

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")

# how many ring-buffer chunks the audio worker drains between
# stop-event checks. Without this, the drain loop burns up to 3.2s of
# solid CPU (64 chunks × ~50ms RNNoise each) before noticing a stop
# signal, delaying ``stop()`` and orphaning the worker. Checking every
# 4 chunks bounds the stop latency to ~200ms while keeping the
# per-iteration ``is_set()`` + GIL-yield overhead negligible.
_DRAIN_STOP_CHECK_INTERVAL = 4

if TYPE_CHECKING:
    pass


class AudioCallbackDispatcher:
    """Audio callback dispatch + worker loop body for :class:`Recorder`.

    Phase 4.5 — extracted from :mod:`.recorder`. See the module
        docstring for the collaborator-pattern rationale and the
        source-inspection contract that constrains the shape of
        :meth:`dispatch_callback_body`.
    """

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        # import (``recorder`` imports this module at module top to
        # construct this class in ``Recorder.__init__``).
        self._recorder = recorder

    def dispatch_callback_body(
        self,
        recorder: Any,
        indata: Any,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> Any:
        """Body of :meth:`Recorder._audio_callback_dispatch` (excluding
                the ``_ring_buffer.append`` + ``_worker_wake_event.set`` literal
        operations that stay on Recorder for the
                source-inspection contract in
                ``tests/test_recording_and_audio.py::test_callback_does_not_do_heavy_processing``).

                This method is invoked by PortAudio from the real-time audio
                thread (via the 1-line delegator on Recorder). It must complete
                well before the next buffer arrives (~32ms at 512 blocksize /
                16kHz). To meet this deadline, it does ONLY:

                1. Pre-roll capture when not recording (small, fast: ~10µs for
                   copy + mono downmix + deque append).
                2. Copy the indata buffer into a local ``chunk_copy`` (the deque
                   append itself stays on Recorder so the source-inspection
                   check continues to pin the ``_ring_buffer.append`` literal).
                3. Detect ring-buffer overflow (worker can't keep up) and bump
                   the dropped-chunk / skipped-frames counters.
                4. Capture the perf timestamp for silence-timer calculations.

                Returns
                -------
                ``None`` if the early-bailout (pre-roll) path was taken — the
                caller must NOT append to the ring buffer or signal the worker
                in this case.

                Otherwise a 5-tuple ``(chunk_copy, frames, time_info, status,
                perf_ts)`` ready to be passed to
                ``recorder._ring_buffer.append(payload)``. The caller is
                expected to follow up with ``recorder._worker_wake_event.set()``.

                Heavy work (filter chain, Silero VAD, scipy resample, VAD state
                machine, silence timer, callbacks, AUDIO-CLIP IPC event push)
                is done by the audio worker thread (see
                :meth:`audio_worker_loop` / ``_process_audio_chunk``).
        """
        # PortAudio can deliver a callback before start()
        # finishes setting self._recording_start_time and other
        # per-session state. Bail out early so the silence/max-
        # duration callbacks don't compute against a None timestamp.
        #
        # The entire body is wrapped in try/except so a bug in
        # the RT callback (e.g. an unexpected AttributeError from a
        # None _ring_buffer / _preroll_buffer) is surfaced on
        # ``recorder._last_callback_error`` and logged at ERROR by
        # ``_stream_finished_callback`` instead of being silently
        # swallowed by PortAudio (which aborts the stream and surfaces
        # to the user as a phantom "device disconnect"). The exception
        # is re-raised after storage so PortAudio's stream-abort
        # semantics are preserved — the difference is the user now
        # sees the true cause in the log instead of a misdiagnosis.
        try:
            return self._dispatch_callback_body_inner(recorder, indata, frames, time_info, status)
        except Exception as exc:
            # Store on the recorder so _stream_finished_callback can
            # log the true cause. Atomic under CPython's GIL (single
            # attribute assignment). We do NOT use a lock here — this
            # is the RT callback, taking a lock would risk an overrun
            # against the 32ms deadline. The store-then-reraise
            # pattern means PortAudio's behavior is unchanged (the
            # stream still aborts), but the diagnostic is preserved.
            recorder._last_callback_error = exc
            raise

    def _dispatch_callback_body_inner(
        self,
        recorder: Any,
        indata: Any,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> Any:
        """Inner body of :meth:`dispatch_callback_body` (split out
        for the try/except wrapper).

        Extracted verbatim from the pre-split ``dispatch_callback_body``
        body so the try/except wrapper above is the ONLY change — the
        source-inspection contracts in
        ``tests/test_capture_module.py::TestDispatchCallbackBodySourceContract``
        (which check the absence of heavy-pipeline ops, the absence of
        ``_ring_buffer.append``, and the absence of
        ``_worker_wake_event.set``) continue to pass against this
        inner method because the body is unchanged. The wrapper
        delegates here, so the heavy-pipeline / append / set literals
        are still not present in ``dispatch_callback_body``'s source.
        """
        if not recorder._recording_event.is_set():
            # AUDIO-PRE: capture pre-roll even when not officially
            # recording. This is a fast path (~10µs): copy + mono
            # downmix + deque append. Stays in the callback so pre-roll
            # latency is minimal — the worker thread isn't started until
            # after start() finishes, so pre-roll capture MUST happen
            # here.
            if recorder._preroll_active:
                mono_preroll = recorder._ensure_mono(indata.copy())
                recorder._preroll_buffer.append(mono_preroll)
            return None

        # Recording is active — push to the SPSC ring buffer for the
        # worker thread to process. The callback's only job is to copy
        # + enqueue.
        #
        # PERF-: the indata buffer is owned by PortAudio and
        # reused for the next callback, so we MUST copy. ~2KB
        # allocation for 512 float32 samples — negligible compared to
        # the 32ms deadline.
        chunk_copy = indata.copy()

        # Detect ring buffer overflow (worker can't keep up). The
        # deque's maxlen will silently evict the oldest chunk, but we
        # want to log it so the user knows audio is being dropped.
        # This replaces the old PERF-011 frame-skip logic
        # (_previous_chunk_pending) which was a single-slot queue — the
        # ring buffer is a 64-slot queue, so we have much more headroom
        # before dropping.
        ring_maxlen = recorder._ring_buffer.maxlen
        if ring_maxlen is not None and len(recorder._ring_buffer) >= ring_maxlen:
            # increment counter only (atomic under GIL). The
            # log.warning() was removed from this PortAudio RT callback
            # — logging I/O here can take ms and risks an overrun against
            # the 32ms deadline. The counter is surfaced later by the
            # worker thread / diagnostics paths (e.g. _finalize_audio_quality_report
            # and the  backpressure warning in _process_audio_chunk).
            # The former ``_skipped_frames`` counter was removed — it
            # was incremented here but never read anywhere in the
            # codebase (dead code on the 16 Hz hot path).
            recorder._dropped_ring_chunks += 1

        # Push (copy, frames, time_info, status, perf_timestamp) to the
        # ring buffer. The timestamp is captured here (not in the worker)
        # so silence-timer calculations reflect when the audio arrived,
        # not when the worker happened to process it. The deque-append
        # itself is performed by the caller (Recorder._audio_callback_dispatch)
        # so the  source-inspection literal stays pinned on
        # the Recorder's source (see the module docstring §Source-inspection
        # contract).
        perf_ts = time.perf_counter()
        return (chunk_copy, frames, time_info, status, perf_ts)

    def audio_worker_loop(
        self,
        recorder: Any,
        stop_event: Any = None,
        wake_event: Any = None,
    ) -> None:
        """Body of :meth:`Recorder._audio_worker_loop`.

        Consumes chunks from the SPSC ring buffer and runs the heavy
        processing pipeline (filter chain, VAD, resample, state machine,
        callbacks). This thread is the SINGLE consumer — the audio
        callback is the single producer, so no locks are needed for the
        ring buffer access (collections.deque append/popleft are atomic
        under CPython's GIL for SPSC).

        Shutdown: exits when ``_worker_stop_event`` is set. The loop
        drains the ring buffer fully before exiting so ``stop()``
        doesn't lose in-flight audio (unless ``drain=False`` was passed
        to ``_stop_audio_worker``, in which case the ring buffer was
        already cleared by the caller).

        Before entering the main drain loop, this worker drains
        ``recorder._preroll_buffer`` in reverse, runs each chunk
        through ``recorder._audio_processor.process_chunk`` (best-effort
        filter chain), and ``appendleft`` to ``recorder._buffer`` —
        the same work ``_prepend_preroll_to_buffer`` used to do
        synchronously on the start() thread. Moving the prepend here
        unblocks start() (which previously blocked 465ms-4.65s on the
        prepend) while preserving chronological order: pre-roll chunks
        land at the front of ``_buffer`` BEFORE any live chunk (live
        chunks only reach ``_buffer`` via ``_process_audio_chunk`` in
        the main drain loop below). The ring buffer (sized for 2.0s of
        headroom) absorbs the prepend duration — live audio chunks
        queued by the callback during the prepend are drained
        immediately after the prepend finishes.
        """
        # Prefer explicit ``stop_event`` / ``wake_event`` (captured
        # at thread-spawn time by ``start_audio_worker_body``) over the
        # dynamic ``recorder._worker_stop_event`` /
        # ``_worker_wake_event`` attributes. A stale worker whose
        # recorder's events were replaced must retain its OLD (set) events
        # and exit — reading the NEW (cleared) attribute dynamically would
        # resume the loop and violate the SPSC single-consumer invariant.
        # Fall back to the dynamic attributes when ``None`` (direct test /
        # legacy call sites) — preserves backward compat.
        _stop = stop_event if stop_event is not None else recorder._worker_stop_event
        _wake = wake_event if wake_event is not None else recorder._worker_wake_event
        # ── phase 0: pre-roll filter-chain prepend ──
        # Moved off the start() thread to avoid blocking the hotkey
        # critical path. The worker is the single consumer of
        # ``_preroll_buffer`` (the callback only writes to it when
        # ``_recording_event`` is clear, which is now set), so this
        # drain is race-free. The prepend body lives in
        # ``SessionState.prepend_preroll_to_buffer`` (shared with the
        # unit-tested direct call site).
        try:
            recorder._prepend_preroll_to_buffer()
        except Exception:
            log.warning(
                "[RECORDING] Pre-roll prepend failed on audio worker thread",
                exc_info=True,
            )

        while True:
            # Wait for work or stop signal. The 50ms timeout ensures we
            # notice the stop flag even if the wake event is missed
            # (e.g., if the callback sets the event between the worker's
            # wait() return and the clear() call — a rare race that the
            # timeout covers).
            if not _stop.is_set():
                _wake.wait(timeout=0.05)
            _wake.clear()

            # Drain all available chunks. Each chunk is processed by
            # _process_audio_chunk which does the heavy lifting.
            #
            # check the stop event every ``_DRAIN_STOP_CHECK_INTERVAL``
            # chunks so a stop signal during a long catch-up drain (the ring
            # buffer holds up to 64 chunks ≈ 1s of audio, each chunk takes
            # ~50ms in RNNoise → up to 3.2s of solid CPU) is noticed within
            # ~200ms instead of burning the full drain. On stop we bail out
            # immediately (sacrificing in-flight audio — acceptable because
            # ``drain=True`` is best-effort). The ``time.sleep(0)`` yields
            # the GIL to reduce CPU burn on long drains.
            _drain_count = 0
            while True:
                try:
                    chunk_data = recorder._ring_buffer.popleft()
                except IndexError:
                    break
                try:
                    recorder._process_audio_chunk(*chunk_data)
                except Exception:
                    # Log and continue — a single bad chunk must NOT kill
                    # the worker (otherwise all subsequent audio is lost
                    # until the next start()).
                    #
                    # B-5: this worker runs at ~16 Hz (the audio callback
                    # pushes a chunk per PortAudio block).  A persistent
                    # error (e.g. a bad filter config) would flood the
                    # log at ERROR 16 times/sec ≈ 960 lines/min.
                    # ``log_rate_limited`` emits the 1st occurrence and
                    # every 100th thereafter at ERROR with the full
                    # traceback; all other occurrences go to DEBUG (no
                    # traceback) so a persistent error remains visible
                    # in debug mode without spamming the default log.
                    log_rate_limited(
                        log,
                        logging.ERROR,
                        "[RECORDING] Audio worker thread error processing chunk",
                        exc_info=True,
                    )
                _drain_count += 1
                if _drain_count % _DRAIN_STOP_CHECK_INTERVAL == 0:
                    if _stop.is_set():
                        return  # bail out early when stop signaled
                    time.sleep(0)  # yield GIL to reduce CPU burn

            # Check for shutdown. We drain the ring buffer fully before
            # exiting so stop() doesn't lose in-flight audio. For the
            # discard() path, the ring buffer was already cleared by the
            # caller, so the drain loop above was a no-op.
            if _stop.is_set():
                return

    # Phase 4.5: worker-lifecycle bodies (Option C) ──────
    #
    # The four methods below contain the BODIES of
    # ``Recorder._start_audio_worker`` / ``_stop_audio_worker`` /
    # ``_start_event_worker`` / ``_stop_event_worker`` (the part INSIDE
    # the ``_worker_lifecycle_lock`` block). The lock acquisition
    # STAYS on the Recorder methods so the  source-inspection
    # contracts in ``tests/test_recorder_worker_lifecycle.py`` continue
    # to see ``_worker_lifecycle_lock`` acquired via a ``with`` block
    # on the Recorder's source. The bodies themselves must NOT acquire
    # ``_worker_lifecycle_lock`` (the lock stays on the Recorder) and
    # the two ``stop_*_body`` methods must NOT acquire ``self._lock``
    # ( negative contract — see
    # ``test_stop_audio_worker_does_not_hold_self_lock_across_join`` and
    # ``test_stop_event_worker_does_not_hold_self_lock_across_join``).
    #
    # Deferred imports: the constants ``_AUDIO_WORKER_THREAD_NAME`` /
    # ``_AUDIO_WORKER_JOIN_TIMEOUT_S`` / ``_EVENT_WORKER_THREAD_NAME`` /
    # ``_EVENT_WORKER_JOIN_TIMEOUT_S`` / ``_EVENT_WORKER_STOP_SENTINEL``
    # are module-level in :mod:`.recorder`, which imports this module at
    # the top (``from .capture import AudioCallbackDispatcher``) — a
    # module-level ``from .recorder import _AUDIO_WORKER_THREAD_NAME``
    # here would create a circular import (the constant is defined at
    # line ~324 of ``recorder.py``, AFTER the ``from .capture import`` at
    # line ~166). By call time ``.recorder`` is fully loaded, so the
    # deferred function-level import succeeds.

    def start_audio_worker_body(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._start_audio_worker` (inside the
                ``_worker_lifecycle_lock`` block).

        Phase 4.5 — extracted from :mod:`.recorder`. The lock
                acquisition STAYS on ``Recorder._start_audio_worker`` for the
        source-inspection contract (see
                ``tests/test_recorder_worker_lifecycle.py::test_start_audio_worker_holds_lock``);
                this method is the body inside the lock. Idempotent: if the
                worker is already running, returns early.

                Called by ``start()`` AFTER the PortAudio stream is
                successfully opened and ``_recording_event`` is set
                (the callback needs the event set before it will push
                to the ring buffer). The pre-roll filter-chain prepend
                is NO LONGER done synchronously on the start() thread —
                the worker thread performs the prepend as a "phase 0"
                at the top of ``audio_worker_loop`` before entering the
                main drain loop. The worker thread is a daemon so it
                never blocks process exit.

                THREAD-REGISTRY: when a registry was provided to ``__init__``,
                the worker thread is registered so ``shutdown_all()`` can
                signal and join it during ``VoiceTyperApp.quit()``. The
                registry entry is removed by :meth:`stop_audio_worker_body`
                after the join completes (or times out) so a subsequent
                ``start()`` re-registers cleanly without triggering the
                "Re-registering name" warning.

        the entire read-check-create-start sequence is wrapped
                in ``_worker_lifecycle_lock`` (acquired by the caller on
                ``Recorder._start_audio_worker``) so concurrent
                ``start()`` / ``stop()`` / ``discard()`` callers cannot race
                on ``_worker_thread`` (both readers seeing ``None``, the
                starter creating+assigning a fresh worker, the stopper
                returning early and leaving that worker untracked).
        """
        from .recorder import (
            _AUDIO_WORKER_JOIN_TIMEOUT_S,
            _AUDIO_WORKER_THREAD_NAME,
        )

        # the caller holds ``_worker_lifecycle_lock`` across the
        # entire read-check-create-start sequence so a concurrent
        # ``_stop_audio_worker`` cannot observe a stale ``None``
        # mid-create.
        if recorder._worker_thread is not None and recorder._worker_thread.is_alive():
            return
        # Reset stop event (in case a previous stop() left it set)
        recorder._worker_stop_event.clear()
        recorder._worker_wake_event.clear()
        # Clear the ring buffer of any stale chunks from a previous session.
        # SEC-audit-008: zero each chunk's numpy array BEFORE ``.clear()``
        # so the previous session's audio data doesn't linger in process
        # memory after the deque reference is dropped (mirrors the
        # preroll-buffer pattern in stop()/discard() — see
        # ``recorder.py``'s ``_preroll_buffer`` clearing). Ring buffer
        # chunks are small (~2KB each, capacity-bounded by
        # ``_AUDIO_RING_BUFFER_CAPACITY``) so synchronous zeroing is
        # acceptable here. Ring buffer items are 5-tuples
        # ``(chunk_copy, frames, time_info, status, perf_ts)`` — the
        # numpy array is the first element. Defensive against
        # direct-array items too.
        for _payload in recorder._ring_buffer:
            _arr = _payload[0] if isinstance(_payload, tuple) else _payload
            if hasattr(_arr, "fill") and hasattr(_arr, "shape"):
                _arr.fill(0)
        recorder._ring_buffer.clear()
        recorder._worker_thread = threading.Thread(
            target=recorder._audio_worker_loop,
            # Pass the CURRENT stop / wake events as explicit
            # args so the worker binds to THESE events at spawn time.
            # If the recorder's events are later replaced (stale-worker
            # SPSC race), the OLD worker retains its OLD (set) events
            # and exits instead of reading the NEW (cleared)
            # ``_worker_stop_event`` attribute dynamically.
            args=(recorder._worker_stop_event, recorder._worker_wake_event),
            name=_AUDIO_WORKER_THREAD_NAME,
            daemon=True,
        )
        recorder._worker_thread.start()
        # THREAD-REGISTRY: register the freshly-started worker so the
        # central registry can signal/join it on shutdown. The join
        # timeout matches the worst-case stop() path (drain=True).
        if recorder._thread_registry is not None:
            recorder._thread_registry.register(
                name=_AUDIO_WORKER_THREAD_NAME,
                thread=recorder._worker_thread,
                stop_event=recorder._worker_stop_event,
                join_timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
            )

    def stop_audio_worker_body(self, recorder: Any, *, timeout: float, drain: bool = True) -> None:
        """Body of :meth:`Recorder._stop_audio_worker` (inside the
                ``_worker_lifecycle_lock`` block).

        Phase 4.5 — extracted from :mod:`.recorder`. The lock
                acquisition STAYS on ``Recorder._stop_audio_worker`` for the
        source-inspection contracts (see
                ``tests/test_recorder_worker_lifecycle.py::test_stop_audio_worker_holds_lock``
                and
                ``tests/test_recorder_worker_lifecycle.py::test_stop_audio_worker_does_not_hold_self_lock_across_join``);
                this method is the body inside the lock.

                Parameters
                ----------
                timeout : float
                    Maximum seconds to wait for the worker to exit.
                drain : bool
                    If True (default, used by ``stop()``), the worker drains the
                    ring buffer fully before exiting so no in-flight audio is
                    lost. If False (used by ``discard()``), the ring buffer is
                    cleared first so the worker exits immediately after its
                    current chunk.

                Safe to call when the worker is not running (no-op).

                THREAD-REGISTRY: unregisters the worker after the join so a
                subsequent :meth:`start_audio_worker_body` re-registers cleanly.

        the entire read-check-clear-join-unregister sequence is
                wrapped in ``_worker_lifecycle_lock`` (acquired by the caller
                on ``Recorder._stop_audio_worker``) (NOT ``self._lock``) so
                concurrent ``stop()`` / ``discard()`` callers cannot both read
                ``_worker_thread is None`` and both return early leaving a
                fresh worker untracked. ``self._lock`` is intentionally NOT
                held across ``thread.join()`` — the worker thread acquires
                ``self._lock`` inside ``_process_audio_chunk`` for the buffer
                append, so holding it across ``join()`` would deadlock. This
        body does NOT acquire ``self._lock`` (the  negative
                contract would otherwise propagate the lock acquisition to
                the Recorder's source via the delegator).
        """
        from .recorder import _AUDIO_WORKER_THREAD_NAME

        # the caller holds ``_worker_lifecycle_lock`` across the
        # entire read-check-clear-join-unregister sequence. This is a
        # separate lock from ``self._lock`` — see the docstring above.
        if recorder._worker_thread is None:
            # Still reset the stop event so the next start() is clean.
            recorder._worker_stop_event.clear()
            return
        if not drain:
            # discard() path: clear the ring buffer so the worker has
            # nothing left to process. It will finish its current chunk
            # (if any) and then exit on the next iteration.
            # SEC-audit-008: zero each chunk's numpy array BEFORE
            # ``.clear()`` so the cancelled session's audio data
            # doesn't linger in process memory after the deque
            # reference is dropped (mirrors the preroll-buffer pattern
            # in stop()/discard() — see ``recorder.py``'s
            # ``_preroll_buffer`` clearing). Ring buffer chunks are
            # small (~2KB each, capacity-bounded by
            # ``_AUDIO_RING_BUFFER_CAPACITY``) so synchronous zeroing
            # is acceptable here. Ring buffer items are 5-tuples
            # ``(chunk_copy, frames, time_info, status, perf_ts)`` —
            # the numpy array is the first element. Defensive against
            # direct-array items too.
            for _payload in recorder._ring_buffer:
                _arr = _payload[0] if isinstance(_payload, tuple) else _payload
                if hasattr(_arr, "fill") and hasattr(_arr, "shape"):
                    _arr.fill(0)
            recorder._ring_buffer.clear()
        # Signal the worker to stop.
        recorder._worker_stop_event.set()
        # Wake the worker in case it's blocked on the wait event.
        recorder._worker_wake_event.set()
        # Join with timeout. If the worker doesn't exit in time (e.g.,
        # stuck in VAD inference), we proceed anyway — the worker is a
        # daemon, so it won't block process exit. A stale worker is
        # harmless because the stop event is set; it will exit on its
        # next iteration boundary.
        recorder._worker_thread.join(timeout=timeout)
        if recorder._worker_thread.is_alive():
            log.warning(
                "[RECORDING] Audio worker thread did not exit within %.1fs "
                "(it will exit as a daemon on next iteration)",
                timeout,
            )
        else:
            log.debug("[RECORDING] Audio worker thread exited cleanly")
        # THREAD-REGISTRY: remove the entry so a subsequent start()
        # re-registers cleanly. If shutdown_all() already ran and
        # joined the thread, this is a no-op (the entry was already
        # used). Safe to call when no entry exists.
        if recorder._thread_registry is not None:
            recorder._thread_registry.unregister(_AUDIO_WORKER_THREAD_NAME)
        # Only clear the stop/wake events and null the thread reference if
        # the worker actually exited. If still alive (stuck in VAD
        # inference), leave the stop event SET so the zombie exits on its
        # next iteration boundary, and keep the thread reference so the
        # start path's is_alive() guard prevents spawning a duplicate
        # (zombie thread leak mitigation — mirrors the pattern at
        # ``device_manager.py``'s ``_stop_device_health_checker``).
        if not recorder._worker_thread.is_alive():
            recorder._worker_stop_event.clear()
            recorder._worker_wake_event.clear()
            recorder._worker_thread = None

    @staticmethod
    def _drain_event_queue(recorder: Any) -> None:
        """Drain ``recorder._event_queue`` non-blockingly ().

        Previously this loop was duplicated between
        :meth:`start_event_worker_body` (drain stale events before start)
        and :meth:`stop_event_worker_body` (drain on discard path), both
        wrapped in an over-broad ``contextlib.suppress(Exception)`` that
        hid bugs. The only expected exception from ``get_nowait()`` is
        ``queue.Empty``; we catch only that and let any other exception
        propagate.
        """
        while True:
            try:
                recorder._event_queue.get_nowait()
            except queue.Empty:
                break

    def start_event_worker_body(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._start_event_worker` (inside the
                ``_worker_lifecycle_lock`` block).

        Phase 4.5 — extracted from :mod:`.recorder`. The lock
                acquisition STAYS on ``Recorder._start_event_worker`` for the
        source-inspection contract (see
                ``tests/test_recorder_worker_lifecycle.py::test_start_event_worker_holds_lock``);
                this method is the body inside the lock. Idempotent: if the
                event worker is already running, returns early.

        called by ``start()`` AFTER the audio worker is started
                so the audio worker can enqueue IPC events (e.g. ``audio_clip``)
                as soon as it begins processing chunks. The event worker is a
                daemon so it never blocks process exit.

                Any stale events left in the queue from a previous session are
                drained before the worker starts so they are not re-published
                (matches the audio worker's ring-buffer clear in
                :meth:`start_audio_worker_body`).

                THREAD-REGISTRY: when a registry was provided to ``__init__``,
                the event worker thread is registered so ``shutdown_all()`` can
                signal and join it during ``VoiceTyperApp.quit()``.

        the entire read-check-create-start sequence is wrapped
                in ``_worker_lifecycle_lock`` (acquired by the caller on
                ``Recorder._start_event_worker``) (the same lock used by the
                audio worker lifecycle methods) so concurrent
                ``start()`` / ``stop()`` / ``discard()`` callers cannot race on
                ``_event_worker_thread``.
        """
        from .recorder import (
            _EVENT_WORKER_JOIN_TIMEOUT_S,
            _EVENT_WORKER_THREAD_NAME,
        )

        # the caller holds ``_worker_lifecycle_lock`` across the
        # entire read-check-create-start sequence so a concurrent
        # ``_stop_event_worker`` cannot observe a stale ``None``
        # mid-create.
        if recorder._event_worker_thread is not None and recorder._event_worker_thread.is_alive():
            return
        recorder._event_stop_event.clear()
        # Drain any stale events from a previous session (: shared helper).
        self._drain_event_queue(recorder)
        recorder._event_worker_thread = threading.Thread(
            target=recorder._event_worker_loop,
            name=_EVENT_WORKER_THREAD_NAME,
            daemon=True,
        )
        recorder._event_worker_thread.start()
        if recorder._thread_registry is not None:
            recorder._thread_registry.register(
                name=_EVENT_WORKER_THREAD_NAME,
                thread=recorder._event_worker_thread,
                stop_event=recorder._event_stop_event,
                join_timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
            )

    def stop_event_worker_body(self, recorder: Any, *, timeout: float, drain: bool = True) -> None:
        """Body of :meth:`Recorder._stop_event_worker` (inside the
                ``_worker_lifecycle_lock`` block).

        Phase 4.5 — extracted from :mod:`.recorder`. The lock
                acquisition STAYS on ``Recorder._stop_event_worker`` for the
        source-inspection contracts (see
                ``tests/test_recorder_worker_lifecycle.py::test_stop_event_worker_holds_lock``
                and
                ``tests/test_recorder_worker_lifecycle.py::test_stop_event_worker_does_not_hold_self_lock_across_join``);
                this method is the body inside the lock.

                Parameters
                ----------
                timeout : float
                    Maximum seconds to wait for the worker to exit.
                drain : bool
                    If True (default, used by ``stop()``), the worker drains the
                    event queue fully (publishing every queued event) before
                    exiting so no in-flight IPC event is lost. If False (used by
                    ``discard()``), the queue is cleared first so the worker
                    exits immediately after its current publish (if any) —
                    cancelled recordings don't need their queued events
                    published.

                THREAD-REGISTRY: unregisters the worker after the join so a
                subsequent :meth:`start_event_worker_body` re-registers cleanly.

                Safe to call when the worker is not running (no-op).

        the entire read-check-clear-join-unregister sequence is
                wrapped in ``_worker_lifecycle_lock`` (acquired by the caller on
                ``Recorder._stop_event_worker``) so concurrent
                ``stop()`` / ``discard()`` callers cannot both read
                ``_event_worker_thread is None`` and both return early leaving
                a fresh worker untracked. This body does NOT acquire
        ``self._lock`` (the  negative contract would otherwise
                propagate the lock acquisition to the Recorder's source via
                the delegator).
        """
        from .recorder import (
            _EVENT_WORKER_STOP_SENTINEL,
            _EVENT_WORKER_THREAD_NAME,
        )

        # the caller holds ``_worker_lifecycle_lock`` across the
        # entire read-check-clear-join-unregister sequence.
        if recorder._event_worker_thread is None:
            # Still reset the stop event so the next start() is clean.
            recorder._event_stop_event.clear()
            return
        if not drain:
            # discard() path: clear the queue so the worker has nothing
            # left to publish. It will finish its current publish (if
            # any) and then exit on the next iteration.
            self._drain_event_queue(recorder)
        # Signal the worker to stop.
        recorder._event_stop_event.set()
        # push a sentinel onto the queue to wake the worker immediately
        # from its 0.5s ``queue.get`` poll. Without the sentinel, the
        # worker would not notice the stop signal until its next poll
        # iteration (up to 0.5s latency). The sentinel is a unique
        # object the loop checks for BEFORE calling
        # ``event_bus.publish``, so it is never published.
        # ``put_nowait`` is used because the queue is bounded
        # (maxsize=1000) and a Full exception here is benign — the
        # worker will still exit on its next poll iteration within
        # 0.5s. The sentinel is pushed AFTER ``set()`` so the worker's
        # next ``get`` returns the sentinel (FIFO order preserves any
        # real events enqueued before the sentinel).
        with contextlib.suppress(queue.Full):
            recorder._event_queue.put_nowait(_EVENT_WORKER_STOP_SENTINEL)
        # ``stop()`` now passes ``timeout=0.1`` (down from
        # ``_EVENT_WORKER_JOIN_TIMEOUT_S=2.0``) so the worst-case
        # stop() latency drops from ~5.8s to ~2.4s. The 0.1s join is
        # long enough for the daemon to drain its queue (typically
        # <10ms — the queue is MPSC with a 1 Hz source throttle) and
        # exit, but 20x shorter than the original 2.0s timeout. If the
        # daemon is stuck in a slow ``event_bus.publish`` and doesn't
        # exit within ``timeout``, we proceed anyway — the daemon is
        # harmless (the stop event is set; it will exit on its next
        # iteration boundary). The thread reference + stop event are
        # only cleared if the worker actually exited, so a stuck worker
        # doesn't get a duplicate spawned on the next start() (zombie
        # thread leak mitigation — mirrors the pattern at
        # ``device_manager.py``'s ``_stop_device_health_checker``).
        recorder._event_worker_thread.join(timeout=timeout)
        if recorder._event_worker_thread.is_alive():
            log.debug(
                "[RECORDING] Event worker thread did not exit within %.2fs "
                "(it will exit as a daemon on next iteration)",
                timeout,
            )
        else:
            log.debug("[RECORDING] Event worker thread exited cleanly")
        if recorder._thread_registry is not None:
            recorder._thread_registry.unregister(_EVENT_WORKER_THREAD_NAME)
        # Only clear the stop event and null the thread reference if the
        # event worker actually exited. If still alive (stuck in a slow
        # ``event_bus.publish``), leave the stop event SET so the zombie
        # exits on its next iteration boundary, and keep the thread
        # reference so the start path's is_alive() guard prevents
        # spawning a duplicate (zombie thread leak mitigation — mirrors
        # the pattern at ``device_manager.py``'s
        # ``_stop_device_health_checker``).
        if not recorder._event_worker_thread.is_alive():
            recorder._event_stop_event.clear()
            recorder._event_worker_thread = None

    def event_worker_loop(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._event_worker_loop` (god-class split).

        IPC event worker thread main loop. Consumes events from
        ``_event_queue`` and calls ``event_bus.publish`` so the IPC
        transport (TCP / stdout) can forward them to the Electron
        renderer. This thread is the SINGLE consumer — the audio worker
        thread is the single producer, so no locks are needed on the
        queue (``queue.Queue`` is already thread-safe for MPSC).

        Shutdown: exits when ``_event_stop_event`` is set. The loop
        drains the queue fully before exiting so ``stop()`` doesn't
        lose in-flight IPC events. For the ``discard()`` path, the
        queue was already cleared by the caller, so the drain loop is
        a no-op.
        """
        from .recorder import _EVENT_WORKER_STOP_SENTINEL

        while True:
            if not recorder._event_stop_event.is_set():
                # wait for work with a 0.5s timeout (was 50ms).
                # The event queue is MPSC with a tiny source-side
                # throttle (1 Hz), so a 50ms poll was 20x more frequent
                # than necessary -- preventing deep C-states on laptops
                # on battery for no benefit. The 0.5s poll still wakes
                # within 0.5s of an event being enqueued (well within
                # the 1 Hz source throttle) and lets the CPU sleep
                # between publishes. Stop latency is NOT bounded by
                # the 0.5s poll: ``_stop_event_worker`` pushes a
                # sentinel onto the queue to wake the worker
                # immediately (see ``_EVENT_WORKER_STOP_SENTINEL``).
                # ``_audio_worker_loop``'s 50ms wait is unchanged
                # because the audio callback pushes chunks at 16 Hz and
                # a 0.5s wait there would add 0.5s of drain latency on
                # stop().
                try:
                    event = recorder._event_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
            else:
                # Stop signal received — drain remaining events before
                # exiting (for the ``stop()`` path). For ``discard()``
                # the queue was already cleared by the caller, so this
                # loop is a no-op.
                try:
                    event = recorder._event_queue.get_nowait()
                except queue.Empty:
                    return
            # check for the stop sentinel BEFORE publishing. The
            # sentinel is pushed by ``_stop_event_worker`` to wake the
            # worker immediately (instead of waiting up to 0.5s for the
            # next poll iteration). Any real events that were enqueued
            # BEFORE the sentinel have already been drained and
            # published by the iterations above.
            if event is _EVENT_WORKER_STOP_SENTINEL:
                return
            # Type narrowing: ``event`` is now guaranteed to be a dict
            # (the only other variant on the queue). ``isinstance`` here
            # doubles as a defensive guard against a future variant
            # pushed by mistake — it skips the publish instead of
            # crashing ``event_bus.publish`` with a TypeError.
            if not isinstance(event, dict):
                # Pre-fix this branch silently ``continue``d,
                # swallowing any non-dict / non-sentinel event without a
                # log. A future variant pushed by mistake would be
                # invisible in production. Log at WARNING (not ERROR)
                # because the worker continues running; the missing event
                # is recoverable on the next publish. ``%r`` formats the
                # type so the offending variant is identifiable without
                # dumping the (potentially large) event payload.
                log.warning(
                    "[RECORDING] Event worker skipped non-dict event: %r",
                    type(event),
                )
                continue
            try:
                from voice_typer.server import event_bus

                event_bus.publish(event)
            except Exception:
                # A bad event or a buggy subscriber must NOT kill the
                # event worker (otherwise all subsequent IPC events
                # are lost until the next start()). event_bus.publish
                # already isolates subscriber exceptions, so this is a
                # belt-and-suspenders guard for unexpected failures
                # (e.g. a TypeError from a malformed event dict).
                log.debug(
                    "[RECORDING] Event worker thread error publishing event",
                    exc_info=True,
                )

    def surface_ring_overflow_warning(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._surface_ring_overflow_warning` (god-class split).

        Emit a rate-limited WARNING when the ring buffer overflows.

        Called once per chunk from ``process_audio_chunk`` on the audio
        worker thread (non-RT-safe to log). It computes the delta
        between consecutive checks and emits a WARNING (rate-limited
        to one per ``_RING_OVERFLOW_WARN_INTERVAL_S`` seconds) when the
        counter increases. The WARNING is logged at WARNING level (not
        ERROR) because dropping a few chunks is recoverable — the
        transcription will be slightly incomplete but not corrupted.

        The ``_last_seen_dropped_ring_chunks`` counter is ALWAYS updated
        (even when the WARNING is rate-limited) so the delta does not
        accumulate across rate-limit windows — the next WARNING reports
        only the chunks dropped since the previous WARNING, not since
        the last unthrottled check.

        Thread-safety: the audio worker is the SINGLE reader of
        ``_dropped_ring_chunks`` (the audio callback is the single writer,
        atomic under CPython's GIL). No lock is needed here.

        Contract: log-only — this helper must NOT call
        ``event_bus.publish`` directly (IPC events route through
        ``_event_queue``, see the negative source-inspection pin in
        ``tests/test_recorder_ring_overflow_warning.py``).
        """
        from .recorder import _RING_OVERFLOW_WARN_INTERVAL_S

        current = recorder._dropped_ring_chunks
        delta = current - recorder._last_seen_dropped_ring_chunks
        # Always update the last-seen counter so the delta doesn't
        # accumulate across rate-limit windows.
        recorder._last_seen_dropped_ring_chunks = current
        if delta <= 0:
            return
        now = time.perf_counter()
        if now - recorder._ring_overflow_warn_ts < _RING_OVERFLOW_WARN_INTERVAL_S:
            return
        recorder._ring_overflow_warn_ts = now
        log.warning(
            "[RECORDING] Ring buffer overflow: %d chunks dropped since "
            "last check (total this session: %d). Audio worker cannot keep "
            "up; transcription may be incomplete. Consider disabling audio "
            "filters or using a lighter model.",
            delta,
            current,
        )
