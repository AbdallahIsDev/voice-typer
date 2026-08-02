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

import numpy as np

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

    def audio_worker_loop(self, recorder: Any) -> None:
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
        """
        while True:
            # Wait for work or stop signal. The 50ms timeout ensures we
            # notice the stop flag even if the wake event is missed
            # (e.g., if the callback sets the event between the worker's
            # wait() return and the clear() call — a rare race that the
            # timeout covers).
            if not recorder._worker_stop_event.is_set():
                recorder._worker_wake_event.wait(timeout=0.05)
            recorder._worker_wake_event.clear()

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
                    if recorder._worker_stop_event.is_set():
                        return  # bail out early when stop signaled
                    time.sleep(0)  # yield GIL to reduce CPU burn

            # Check for shutdown. We drain the ring buffer fully before
            # exiting so stop() doesn't lose in-flight audio. For the
            # discard() path, the ring buffer was already cleared by the
            # caller, so the drain loop above was a no-op.
            if recorder._worker_stop_event.is_set():
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

                Called by ``start()`` AFTER the PortAudio stream is successfully
                opened and the pre-roll buffer has been prepended, but BEFORE
                ``_recording_event.set()`` is... actually, it's called AFTER
                ``_recording_event.set()`` because the callback needs the event
                to be set before it will push to the ring buffer. The worker
                thread is a daemon so it never blocks process exit.

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
            if isinstance(_arr, np.ndarray):
                _arr.fill(0)
        recorder._ring_buffer.clear()
        recorder._worker_thread = threading.Thread(
            target=recorder._audio_worker_loop,
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
                if isinstance(_arr, np.ndarray):
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
        # Clear the stop event so the next start() can reuse the fields.
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
        # iteration boundary) and the test contract
        # (``_event_worker_thread is None`` after stop()) is preserved
        # because we null the reference unconditionally after the join
        # attempt.
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
        recorder._event_stop_event.clear()
        recorder._event_worker_thread = None
