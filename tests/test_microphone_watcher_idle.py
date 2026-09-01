"""Pin the contract that ``Recorder.start`` / ``Recorder.stop``
wire the microphone watcher's ``set_idle`` gate.

Pre-fix: ``MicrophoneDeviceWatcher.set_idle`` was DEFINED
(``microphone_watcher.py:177``) and CONSUMED by the macOS/Linux polling
paths (``microphone_watcher.py:552``, ``:722``, ``:728`` — the cadence
selection reads ``self._is_idle``) but had ZERO production callers —
verified via ``rg -n "set_idle\\(" voice_typer/`` (only the definition
itself, no call sites). The default state ``_is_idle = True`` therefore
stayed ``True`` forever and the active 3 s poll cadence NEVER engaged
during recording — the watcher idled at 12 s always. Effect INVERTED:
the cadence selection logic was correct, but no one toggled it.

Post-fix (Wave 1, sub-agent 11): ``start_recording`` calls
``recorder._devices._mic_watcher.set_idle(False)`` at the very end (after
``_start_device_health_checker``), and ``stop_recording`` calls
``set_idle(True)`` before each return path that follows a successful
stop. The ``None`` guard covers hosts where the watcher never came up
(macOS-without-pyobjc fall-back).

These tests pin the contract by exercising the extracted free
functions ``start_recording`` / ``stop_recording`` in
``voice_typer.server.recording._recorder_split`` with a MagicMock
recorder whose ``_mic_watcher`` attribute is a spied mock. They
verify:

  1. Happy-path start calls ``set_idle(False)`` exactly once.
  2. Happy-path stop (non-empty buffer) calls ``set_idle(True)`` once.
  3. Empty-buffer stop (``_buffer`` empty inside the lock) still calls
     ``set_idle(True)`` — the early-return path is gated on the watcher
     toggle.
  4. Failure path: when ``_start_event_worker`` raises,
     ``set_idle(False)`` is NOT called — the toggle sits at the very
     end of ``start_recording`` so any earlier failure prevents it.
  5. ``None`` guard: when ``recorder._devices._mic_watcher is None``, neither
     ``start`` nor ``stop`` touches the attribute (no ``AttributeError``
     on a None deref).

The mock factories here intentionally mirror
``tests/test_recorder_split_start.py::_build_mock_recorder`` and
``tests/test_recorder_split_stop.py::_build_mock_recorder`` so the
stub surface matches what production ``start_recording`` /
``stop_recording`` actually touch (no PortAudio, no real worker
threads, no OS permissions module — pure unit test).
"""

from __future__ import annotations

import collections
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import (
    start_recording,
    stop_recording,
)

# ── Module-binding interception ───────────────────────────────────
# ``start_recording`` / ``stop_recording`` invoke the free functions
# ``refresh_vad_caches`` / ``prepare_audio`` (imported at
# :mod:`._recorder_split` module level) — the historical
# ``Recorder._refresh_vad_caches`` / ``Recorder._prepare_audio``
# delegators were removed. The autouse fixture below patches those
# bindings so mock recorders never run the real bodies.

_mock_bindings_holder: dict = {}


@pytest.fixture(autouse=True)
def _mock_split_bindings(monkeypatch):
    import voice_typer.server.recording._recorder_split as split_mod

    refresh_mock = MagicMock(name="refresh_vad_caches")
    prepare_mock = MagicMock(name="prepare_audio", side_effect=lambda rec, audio, effective_sr_in, **kw: audio)
    monkeypatch.setattr(split_mod, "refresh_vad_caches", refresh_mock)
    monkeypatch.setattr(split_mod, "prepare_audio", prepare_mock)
    _mock_bindings_holder["refresh"] = refresh_mock
    _mock_bindings_holder["prepare"] = prepare_mock
    yield
    _mock_bindings_holder.pop("refresh", None)
    _mock_bindings_holder.pop("prepare", None)


def _refresh() -> MagicMock:
    return _mock_bindings_holder["refresh"]


def _prep() -> MagicMock:
    return _mock_bindings_holder["prepare"]


# ── Mock factories ────────────────────────────────────────────────


def _build_start_recorder(*, open_success: bool = True) -> MagicMock:
    """Build a MagicMock recorder sufficient for ``start_recording``.

    Mirrors ``tests/test_recorder_split_start.py::_build_mock_recorder``
    but adds an explicit, spied ``_mic_watcher`` so we can assert
    ``set_idle`` was/wasn't called. Production ``Recorder._devices._mic_watcher``
    is a property delegating to ``self._devices._mic_watcher``; on a
    MagicMock the property-read is replaced by the explicit attribute
    set below — preserving the same access pattern.
    """
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = 16000
    recorder.config.microphone = None
    recorder.config.save.return_value = True

    recorder._session_state.cache_session_config.return_value = 30
    recorder._devices._resolve_device.return_value = 5
    recorder._devices._same_physical_microphone_candidates.return_value = [5]
    recorder._stream_lifecycle.build_audio_callback.return_value = object()

    if open_success:
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (5, 16000, None)
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")
    else:
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            None,
            16000,
            RuntimeError("no input device could be opened"),
        )
        recorder._stream_lifecycle._stream = None

    recorder._recording_event = threading.Event()
    recorder._audio_processor = None
    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = collections.deque(maxlen=0)

    # Explicit spied watcher (no auto-child) so call counts are
    # deterministic.
    recorder._devices._mic_watcher = MagicMock(name="mic_watcher")
    return recorder


def _build_stop_recorder(
    *,
    recording: bool = True,
    buffer_chunks: list[np.ndarray] | None = None,
) -> MagicMock:
    """Build a MagicMock recorder sufficient for ``stop_recording``.

    Mirrors ``tests/test_recorder_split_stop.py::_build_mock_recorder``
    but adds an explicit spied ``_mic_watcher`` for the same reason as
    the start factory above.
    """
    recorder = MagicMock(name="recorder")

    recorder._recording_event = threading.Event()
    if recording:
        recorder._recording_event.set()

    recorder._stop_generation = 0
    recorder._user_stop_pending = False
    recorder._worker_thread = None
    recorder._event_worker_thread = None
    recorder._audio_pipeline._lock = threading.Lock()

    if buffer_chunks is None:
        buffer_chunks = [np.zeros(100, dtype=np.float32)]
    recorder._audio_pipeline._buffer = collections.deque(buffer_chunks, maxlen=30000)

    recorder._audio_pipeline._chunk_count = len(buffer_chunks)
    recorder._audio_pipeline._buffer_sr = 16000
    recorder._effective_sr = 16000
    recorder._last_rms = 0.0
    recorder._last_audio_stats = (0.0, 0.0, 0.0)

    _prep().side_effect = lambda rec, audio, effective_sr_in, **kw: audio

    # Explicit spied watcher.
    recorder._devices._mic_watcher = MagicMock(name="mic_watcher")
    return recorder


# ── Happy-path start ──────────────────────────────────────────────


class TestStartRecordingWiresSetIdleFalse:
    """``start_recording`` must call ``set_idle(False)`` on success."""

    def test_start_calls_set_idle_false_once(self):
        """A successful ``start_recording`` must invoke
        ``recorder._devices._mic_watcher.set_idle(False)`` exactly once —
        toggling the watcher from the default idle 12 s cadence to
        the active 3 s cadence during recording."""
        recorder = _build_start_recorder()
        start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_called_once_with(False)

    def test_start_does_not_call_set_idle_true(self):
        """``start_recording`` must NOT call ``set_idle(True)`` —
        that's ``stop_recording``'s job. A regression that swaps the
        polarity would leave the watcher in the idle 12 s cadence
        during recording (the exact bug this fixes)."""
        recorder = _build_start_recorder()
        start_recording(recorder)

        # ``set_idle`` was called exactly once with ``False`` —
        # no spurious ``True`` call.
        assert recorder._devices._mic_watcher.set_idle.call_count == 1
        assert recorder._devices._mic_watcher.set_idle.call_args == ((False,), {})

    def test_set_idle_false_runs_after_start_device_health_checker(self):
        """The ``set_idle(False)`` call must be the LAST step in
        ``start_recording`` so any earlier failure prevents the toggle
        (failure-path contract). The source-order assertion pins this
        via ``call_log``."""
        recorder = _build_start_recorder()
        call_log: list[str] = []

        def log_call(name, ret=None):
            def _hook(*a, **k):
                call_log.append(name)
                return ret

            return _hook

        recorder._secure_clear_session_caches.side_effect = log_call("secure_clear")
        recorder._session_state.reset_session_state.side_effect = log_call("reset_session")
        recorder._session_state.cache_session_config.side_effect = log_call("cache_config", ret=30)
        recorder._devices._resolve_device.side_effect = log_call("resolve_device", ret=5)
        recorder._devices._same_physical_microphone_candidates.side_effect = log_call("candidates", ret=[5])
        recorder._stream_lifecycle.build_audio_callback.side_effect = log_call("build_callback", ret=object())
        recorder._stream_lifecycle.open_stream_for_candidates.side_effect = log_call(
            "open_stream", ret=(5, 16000, None)
        )
        recorder._session_state.resize_buffers_for_sample_rate.side_effect = log_call("resize_buffers")
        recorder._recording_event = MagicMock(wraps=threading.Event())
        recorder._recording_event.set.side_effect = log_call("event.set")
        _refresh().side_effect = log_call("refresh_vad")
        recorder._start_audio_worker.side_effect = log_call("start_audio_worker")
        recorder._capture.start_event_worker_body.side_effect = log_call("start_event_worker")
        recorder._devices._start_device_health_checker.side_effect = log_call("start_device_health_checker")

        def _set_idle_hook(is_idle):
            call_log.append(f"set_idle({is_idle})")

        recorder._devices._mic_watcher.set_idle.side_effect = _set_idle_hook

        start_recording(recorder)

        # ``set_idle(False)`` must be the FINAL entry — after
        # ``start_device_health_checker``.
        assert call_log[-1] == "set_idle(False)", (
            f"set_idle(False) must be the last call in start_recording; got call_log={call_log}"
        )
        assert call_log.index("start_device_health_checker") < call_log.index("set_idle(False)"), (
            f"set_idle(False) must come AFTER start_device_health_checker; got call_log={call_log}"
        )


# ── Failure path start ────────────────────────────────────────────


class TestStartRecordingFailurePath:
    """If ``start_recording`` raises, ``set_idle(False)`` must NOT be
    called — otherwise the watcher would be left in the active 3 s
    cadence even though no recording is in flight (the inverse of the
    cadence-toggle bug, but still wrong)."""

    def test_set_idle_false_not_called_when_event_worker_raises(self):
        """When ``_start_event_worker`` raises (the second-to-last
        step before the ``set_idle(False)`` toggle),
        ``set_idle(False)`` must NOT be called."""
        recorder = _build_start_recorder()
        recorder._capture.start_event_worker_body.side_effect = RuntimeError("event worker spawn failed")

        with pytest.raises(RuntimeError, match="event worker spawn failed"):
            start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_not_called()

    def test_set_idle_false_not_called_when_audio_worker_raises(self):
        """When ``_start_audio_worker`` raises, the rollback path
        tears down the stream and re-raises — ``set_idle(False)``
        must NOT be called."""
        recorder = _build_start_recorder()
        recorder._start_audio_worker.side_effect = RuntimeError("audio worker spawn failed")

        with pytest.raises(RuntimeError, match="audio worker spawn failed"):
            start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_not_called()

    def test_set_idle_false_not_called_when_stream_open_fails(self):
        """When the stream-open path fails (no input device could be
        opened), ``start_recording`` re-raises ``last_error`` before
        reaching the ``set_idle(False)`` toggle."""
        recorder = _build_start_recorder(open_success=False)
        recorder._stream_lifecycle.open_stream_fallback.return_value = (
            None,
            16000,
            False,
            RuntimeError("fallback failed"),
        )

        with pytest.raises(RuntimeError, match="fallback failed"):
            start_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_not_called()


# ── None-guard ────────────────────────────────────────────────────


class TestStartRecordingNoneWatcherGuard:
    """When ``recorder._devices._mic_watcher is None`` (macOS-without-pyobjc
    fall-back, or the watcher failed to start — see
    ``DeviceManager.__init__``), ``start_recording`` must NOT touch
    the attribute (no ``None.set_idle(...)`` deref → ``AttributeError``)."""

    def test_start_does_not_raise_when_mic_watcher_is_none(self):
        """A ``None`` ``_mic_watcher`` must be tolerated — the watcher
        never came up, so the cadence toggle is a no-op anyway."""
        recorder = _build_start_recorder()
        recorder._devices._mic_watcher = None

        # Must not raise AttributeError.
        start_recording(recorder)


# ── Happy-path stop ───────────────────────────────────────────────


class TestStopRecordingWiresSetIdleTrue:
    """``stop_recording`` must call ``set_idle(True)`` on each
    successful return path."""

    def test_stop_calls_set_idle_true_once_non_empty_buffer(self):
        """A successful ``stop_recording`` with a non-empty buffer
        must invoke ``recorder._devices._mic_watcher.set_idle(True)`` exactly
        once — returning the watcher to the idle 12 s cadence
        between recordings."""
        recorder = _build_stop_recorder(recording=True)
        stop_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_called_once_with(True)

    def test_stop_calls_set_idle_true_on_empty_buffer_path(self):
        """The empty-buffer early-return path (the
        ``if not recorder._audio_pipeline._buffer:`` branch inside the lock) must
        ALSO call ``set_idle(True)`` — a stop with no audio captured
        is still a stop, and the watcher must return to idle."""
        recorder = _build_stop_recorder(recording=True, buffer_chunks=[])
        stop_recording(recorder)

        recorder._devices._mic_watcher.set_idle.assert_called_once_with(True)

    def test_stop_does_not_call_set_idle_false(self):
        """``stop_recording`` must NOT call ``set_idle(False)`` —
        that's ``start_recording``'s job. A regression that swaps the
        polarity would tighten the cadence to 3 s BETWEEN recordings
        (the inverse of the cadence-toggle bug)."""
        recorder = _build_stop_recorder(recording=True)
        stop_recording(recorder)

        assert recorder._devices._mic_watcher.set_idle.call_count == 1
        assert recorder._devices._mic_watcher.set_idle.call_args == ((True,), {})


# ── None-guard for stop ───────────────────────────────────────────


class TestStopRecordingNoneWatcherGuard:
    """When ``recorder._devices._mic_watcher is None``, ``stop_recording``
    must not raise (mirrors the start-side guard)."""

    def test_stop_does_not_raise_when_mic_watcher_is_none(self):
        recorder = _build_stop_recorder(recording=True)
        recorder._devices._mic_watcher = None

        # Must not raise AttributeError.
        result = stop_recording(recorder)
        # The non-empty-buffer happy path still returns the audio array.
        assert isinstance(result, np.ndarray)


# ── Start → Stop round-trip ───────────────────────────────────────


class TestStartStopRoundTrip:
    """Verify a start() → stop() round-trip toggles the watcher
    active→idle in the correct order: ``set_idle(False)`` on start,
    ``set_idle(True)`` on stop. A regression that swaps the order
    (or skips one) breaks the cadence contract."""

    def test_round_trip_calls_set_idle_false_then_true(self):
        recorder = _build_start_recorder()
        # Reuse the same watcher mock for stop — start populated
        # ``_mic_watcher`` on the MagicMock, so stop reads the same
        # spied instance.
        start_recording(recorder)
        # Reset the recording_event so stop's normal path runs (the
        # start_recording mock left it set, so stop's fast-path
        # ``if not recorder._recording_event.is_set() and ...``
        # would fire otherwise). Production stop() is called after
        # start(), so the event IS set — that's the contract.
        # ``_buffer`` must be a real deque so stop's snapshot works.
        recorder._audio_pipeline._buffer = collections.deque([np.zeros(100, dtype=np.float32)], maxlen=30000)
        recorder._audio_pipeline._buffer_sr = 16000
        recorder._effective_sr = 16000
        recorder._audio_pipeline._lock = threading.Lock()
        _prep().side_effect = lambda rec, audio, effective_sr_in, **kw: audio

        stop_recording(recorder)

        # Two calls: first ``False`` (start), then ``True`` (stop).
        assert recorder._devices._mic_watcher.set_idle.call_count == 2
        assert recorder._devices._mic_watcher.set_idle.call_args_list[0] == ((False,), {})
        assert recorder._devices._mic_watcher.set_idle.call_args_list[1] == ((True,), {})
