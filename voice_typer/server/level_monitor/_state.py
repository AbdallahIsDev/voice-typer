"""Shared state for the level_monitor package ()."""

from __future__ import annotations

import collections
import threading
from typing import Any

# use the canonical Whisper 16 kHz constant as the default
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE


class _State:
    """Singleton holding every piece of level_monitor mutable state."""

    def __init__(self) -> None:
        # ``Optional[object]`` made every downstream
        self._monitor_lock: threading.Lock = threading.Lock()
        self._monitor_stream: Any | None = None  # sounddevice.InputStream
        self._monitor_active: bool = False
        self._monitor_level: float = 0.0  # smoothed RMS (0-1)
        self._monitor_peak: float = 0.0  # smoothed peak (0-1)
        self._monitor_sample_rate: int = WHISPER_SAMPLE_RATE
        self._monitor_mic_id: str | None = None  # device this stream is on

        # Generation counter bumped on every monitor open/stop transition
        # so a slow open can detect a racing switch or stop before committing.
        self._monitor_epoch: int = 0

        # Display gain applied to the smoothed RMS before it reaches any
        self._LEVEL_DISPLAY_GAIN: float = 8.0

        # When set, audio from the callback is run through this processor's
        self._level_processor: Any | None = None  # AudioProcessor instance
        # Stash of the config_dict last passed to ``update_level_processor``.
        self._level_processor_config: dict | None = None
        # Lightweight level-bar mode. When False (default), the cosmetic
        self._level_bar_filtered: bool = False

        # ──  (c-review PERF-03): SPSC ring buffer + worker ──
        self._LEVEL_RING_BUFFER_CAPACITY: int = 64
        self._level_ring_buffer: collections.deque = collections.deque(
            maxlen=self._LEVEL_RING_BUFFER_CAPACITY,
        )
        self._level_worker_thread: threading.Thread | None = None
        self._level_worker_stop_event: threading.Event = threading.Event()
        self._level_worker_wake_event: threading.Event = threading.Event()
        # Counter for chunks dropped because the ring buffer was full
        self._dropped_level_chunks: int = 0
        # timestamp (``time.monotonic()``) of the last throttled
        self._last_drop_log_time: float = 0.0
        # One-shot latch so the RT callback emits a WARNING on
        self._first_drop_warning_emitted: bool = False

        # ``_test_chunks`` / ``_test_raw_chunks`` are bounded
        self._TEST_MAX_CHUNKS_CAP: int = int(30 * 48000 / 512) + 1  # ~2813
        self._test_mode: bool = False
        #  ``_test_chunks`` is retained as a backward-
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
        self._consecutive_zero_chunks: int = 0
        self._device_lost_emitted: bool = False
        self._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD: int = 10

        # mic_level push-event publishing state: coalesces level updates
        self._mic_level_queue: collections.deque = collections.deque(maxlen=16)
        self._mic_level_queue_lock: threading.Lock = threading.Lock()
        self._mic_level_last_push_ts: float = 0.0
        self._mic_level_worker_thread: threading.Thread | None = None
        self._mic_level_worker_wake_event: threading.Event = threading.Event()
        self._mic_level_worker_stop: bool = False
        self._MIC_LEVEL_COALESCE_SEC: float = 1.0 / 30.0

        # When no IPC ``get_level`` poll has been received in
        self._last_get_level_poll_ts: float = 0.0
        # above), so this timeout is purely a defensive backstop for the
        self._LEVEL_IDLE_TIMEOUT_SEC: float = 60.0

        # Raised from 50 ms to 250 ms, the stop path already calls
        self._LEVEL_WORKER_BACKSTOP_TIMEOUT_SEC: float = 0.25

        # Optional central ThreadRegistry for shutdown coordination.
        self._thread_registry: Any | None = None

    def reset_for_tests(self) -> None:
        """Reset all mutable state to its post-``__init__`` defaults."""
        # Re-initialise by creating a fresh instance and copying its
        fresh = _State()
        self.__dict__.clear()
        self.__dict__.update(fresh.__dict__)


# Singleton instance, every submodule imports this and accesses state
_state: _State = _State()
