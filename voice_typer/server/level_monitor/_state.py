"""Shared state for the level_monitor package (AC-129).

This module owns the singleton ``_state`` instance that holds every
piece of mutable module-level state previously scattered across
``level_monitor.py``'s top-level namespace (27 module-level globals
in the pre-refactor god-module).

Why a singleton class instead of plain module-level globals?
------------------------------------------------------------
Python's ``global X`` statement only refers to the *current module's*
namespace. If ``monitoring.py`` did ``global _monitor_active;
_monitor_active = True``, the write would land in ``monitoring.py``'s
``__dict__`` — NOT in ``worker.py``'s. The worker thread (which reads
``_monitor_active`` inside ``_level_worker_loop``) would never see
the update.

The recording package solves this by owning each mutable global in
exactly ONE submodule and having other submodules read it via
``from . import resampling as _r; _r._resample_poly``. That works but
requires every read site to qualify the access (``_r.X`` instead of
``X``) and every write site to do ``_r.X = Y`` instead of
``global X; X = Y``.

A singleton state class is mechanically equivalent: every access
becomes ``_state.X`` (read) or ``_state.X = Y`` (write). The benefit
is that ALL state lives in one obvious place, and the package's
``__init__.py`` can route test writes via a single ``__setattr__``
hook (instead of one routing rule per owning submodule, as the
recording package does).

Test-patch compatibility
------------------------
Tests access state via ``lm._test_mode`` (read) / ``lm._test_mode = False``
(write) — i.e. via the package namespace, NOT via ``_state`` directly.
``__init__.py`` installs a custom module class (``_LevelMonitorModule``)
whose ``__getattr__`` / ``__setattr__`` route ``_``-prefixed attribute
access through to ``_state``. So:

    lm._test_mode              →  _state._test_mode
    lm._test_mode = False      →  _state._test_mode = False
    lm._test_chunks.clear()    →  _state._test_chunks.clear()
    lm._test_chunks.append(x)  →  _state._test_chunks.append(x)
    lm._monitor_lock.acquire() →  _state._monitor_lock.acquire()

This preserves every test access pattern documented in AC-129.
"""

from __future__ import annotations

import collections
import threading
from typing import Any

# UE-12-F15: use the canonical Whisper 16 kHz constant as the default
# monitor sample rate instead of a hardcoded ``16000`` literal. The
# pre-refactor ``level_monitor.py`` god-module used ``WHISPER_SAMPLE_RATE``
# here; the AC-129 package split lost that link and inlined the literal.
# Re-establishing the import keeps a single source of truth so an
# intent-level change (e.g. moving to 24 kHz models) propagates here
# automatically.
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE


class _State:
    """Singleton holding every piece of level_monitor mutable state.

    All 27+ module-level globals from the pre-refactor ``level_monitor.py``
    live here as instance attributes. Submodules (``monitoring.py``,
    ``test_recording.py``, ``worker.py``) import the singleton ``_state``
    instance and access state via ``_state.X`` (read) / ``_state.X = Y``
    (write).

    The class is intentionally NOT a ``dataclass`` — many attributes are
    mutable containers (``deque``, ``dict``, ``threading.Event``) whose
    identity must remain stable across the lifetime of the process
    (tests do ``lm._test_chunks.clear()`` and expect the SAME deque
    object to be cleared, not a freshly-constructed one).
    """

    def __init__(self) -> None:
        # ── Monitor session state ────────────────────────────────────
        # TASK-14: ``Optional[object]`` made every downstream
        # ``stream.stop()`` / ``stream.close()`` call raise
        # ``Object of class `object` has no attribute ...``.  ``Any``
        # matches the actual runtime type (``sounddevice.InputStream``)
        # which is too heavy to import here and has no inline stubs.
        self._monitor_lock: threading.Lock = threading.Lock()
        self._monitor_stream: Any | None = None  # sounddevice.InputStream
        self._monitor_active: bool = False
        self._monitor_level: float = 0.0  # smoothed RMS (0-1)
        self._monitor_peak: float = 0.0  # smoothed peak (0-1)
        self._monitor_sample_rate: int = WHISPER_SAMPLE_RATE
        self._monitor_mic_id: str | None = None  # device this stream is on

        # ── Audio processor for filtering the live level bar ─────────
        # When set, audio from the callback is run through this processor's
        # process_chunk() before computing RMS/peak so the level bar
        # reflects the effect of noise filters in real-time.
        # TASK-14: same as ``_monitor_stream`` — ``Optional[object]``
        # rejects ``.process_chunk()`` / ``.cancel()`` calls below.  Use
        # ``Any`` to match the runtime ``AudioProcessor`` type.
        self._level_processor: Any | None = None  # AudioProcessor instance

        # ── RT-SAFE-001 (c-review PERF-03): SPSC ring buffer + worker ──
        # The PortAudio callback (single producer) pushes
        # (indata_copy, status) tuples to this deque; the level worker
        # thread (single consumer) pops them and runs the heavy
        # processing pipeline (filter chain, RMS/peak smoothing,
        # test-chunk accumulation + quality metrics).
        self._LEVEL_RING_BUFFER_CAPACITY: int = 64  # ~4s @ 16 Hz block rate
        self._level_ring_buffer: collections.deque = collections.deque(
            maxlen=self._LEVEL_RING_BUFFER_CAPACITY,
        )
        self._level_worker_thread: threading.Thread | None = None
        self._level_worker_stop_event: threading.Event = threading.Event()
        self._level_worker_wake_event: threading.Event = threading.Event()
        # Counter for chunks dropped because the ring buffer was full
        # (worker couldn't keep up). Logged with throttling.
        self._dropped_level_chunks: int = 0
        # XV-58: timestamp (``time.monotonic()``) of the last throttled
        # log emission for ``_dropped_level_chunks``.
        self._last_drop_log_time: float = 0.0
        # R3-F6: one-shot latch so the RT callback emits a WARNING on
        # the FIRST drop of a burst (before the worker thread's 5s
        # throttle window would).
        self._first_drop_warning_emitted: bool = False

        # ── Test recording state (uses the SAME stream) ──────────────
        # MEM-02: ``_test_chunks`` / ``_test_raw_chunks`` are bounded
        # ``collections.deque`` (NOT plain ``list``).  The maxlen is
        # derived from the CURRENT sample rate and the currently-
        # requested test duration, so a forgotten
        # ``stop_test_recording()`` cannot accumulate unbounded audio.
        self._TEST_MAX_CHUNKS_CAP: int = int(30 * 48000 / 512) + 1  # ~2813
        self._test_mode: bool = False
        # XV-54 / PVT-013: ``_test_chunks`` is retained as a backward-
        # compat shim ONLY because external test files reference it
        # directly via ``lm._test_chunks.clear() / .append() / .maxlen /
        # len(...)``. Removing the symbol here would break those tests.
        # The shim is bounded + cleared alongside ``_test_raw_chunks``
        # (MEM-02) so it cannot leak.
        self._test_chunks: collections.deque = collections.deque(
            maxlen=self._TEST_MAX_CHUNKS_CAP,
        )
        self._test_raw_chunks: collections.deque = collections.deque(
            maxlen=self._TEST_MAX_CHUNKS_CAP,
        )
        self._test_filtered_chunks: collections.deque = collections.deque(
            maxlen=self._TEST_MAX_CHUNKS_CAP,
        )
        self._test_start_time: float = 0.0
        self._test_duration: float = 10.0
        self._test_filters: dict = {}
        self._test_auto_stop_timer: threading.Timer | None = None

        # Quality metrics accumulated during test
        self._test_peak_history: collections.deque = collections.deque(
            maxlen=self._TEST_MAX_CHUNKS_CAP,
        )
        self._test_rms_history: collections.deque = collections.deque(
            maxlen=self._TEST_MAX_CHUNKS_CAP,
        )
        self._test_clip_count: int = 0
        self._test_silence_blocks: int = 0

        # Disconnect-detection state: when the mic produces N consecutive
        # zero-RMS + zero-peak chunks (or the InputStream finishes),
        # emit a ``device_lost`` IPC event so the frontend can surface
        # the disconnect instead of freezing the level bar with no
        # signal.
        self._consecutive_zero_chunks: int = 0
        self._device_lost_emitted: bool = False
        self._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD: int = 10

        # mic_level push-event publishing state: coalesces level updates
        # to ~30 Hz and pushes them on a dedicated worker thread so the
        # RT callback never blocks on event_bus publish latency.
        self._mic_level_queue: collections.deque = collections.deque(maxlen=16)
        self._mic_level_queue_lock: threading.Lock = threading.Lock()
        self._mic_level_last_push_ts: float = 0.0
        self._mic_level_worker_thread: threading.Thread | None = None
        self._mic_level_worker_wake_event: threading.Event = threading.Event()
        self._mic_level_worker_stop: bool = False
        self._MIC_LEVEL_COALESCE_SEC: float = 1.0 / 30.0

        # ── ER-14: idle-timeout auto-stop ────────────────────────────
        # When no IPC ``get_level`` poll has been received in
        # ``_LEVEL_IDLE_TIMEOUT_SEC`` seconds, the next ``get_level``
        # call auto-stops the stream (and re-starts it on the next
        # ``start_monitoring`` / ``get_level`` poll). This prevents the
        # RNNoise filter chain from pegging a core when the tray bubble
        # is hidden but the frontend forgot to call ``level_monitor_stop``.
        self._last_get_level_poll_ts: float = 0.0
        self._LEVEL_IDLE_TIMEOUT_SEC: float = 5.0

        # ── ER-75: worker backstop poll interval ─────────────────────
        # Raised from 50 ms to 250 ms — the stop path already calls
        # ``_level_worker_wake_event.set()`` so stop latency is
        # unaffected; the timeout only governs the "missed wakeup"
        # recovery interval (a rare edge case). 250 ms cuts idle
        # wakeups 5× with no functional change.
        self._LEVEL_WORKER_BACKSTOP_TIMEOUT_SEC: float = 0.25

    def reset_for_tests(self) -> None:
        """Reset all mutable state to its post-``__init__`` defaults.

        Used by the package-level ``_reset_state_for_tests()`` helper
        which the test fixtures call via ``lm._reset_state_for_tests()``
        to start from a clean slate. Mirrors the inline reset that
        ``tests/test_level_monitor.py`` and
        ``tests/test_level_monitor_disconnect.py`` previously did by
        hand (assigning to ~25 ``lm._X`` attributes one at a time).
        """
        # Re-initialise by creating a fresh instance and copying its
        # attributes — simpler than re-listing every field here (and
        # stays in sync if new fields are added to ``__init__``).
        fresh = _State()
        self.__dict__.clear()
        self.__dict__.update(fresh.__dict__)


# Singleton instance — every submodule imports this and accesses state
# via ``_state._X`` (read) / ``_state._X = Y`` (write). The package's
# ``__init__.py`` installs a custom module class that routes
# ``level_monitor._X`` reads/writes through to this singleton, so tests
# can keep using the ``lm._X`` access pattern unchanged.
_state: _State = _State()
