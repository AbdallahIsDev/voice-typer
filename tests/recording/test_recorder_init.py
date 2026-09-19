"""
Tests for ``voice_typer.server.recording.recorder_init``.
The acceptance criteria (TC-INVEST-05) requires exercising this mixin
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.recording.recorder_init import RecorderInitMixin


class _MockRecorderHost(RecorderInitMixin):
    """
    Minimal host class for ``RecorderInitMixin``.
    ``self`` and do NOT touch recorder state at construction time, so
    """

    def __init__(self):
        # The mixin calls ``self._prewarm_device_cache()`` at the tail.
        self._prewarm_device_cache = MagicMock()
        # Provide the basic-state attributes the collaborators might
        self.config = MagicMock(sample_rate=16000)
        # The pipeline's recording buffer registers the host's
        self._note_buffer_capacity_eviction = MagicMock()
        self._recording_event = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self._thread_registry = None


class TestDisconnectHandlerStateInit:
    """The bouncer + single-flight state attributes must be initialized"""

    @pytest.fixture()
    def host_with_patched_collaborators(self):
        """Build a host with all six collaborator classes patched so"""
        with (
            patch("voice_typer.server.recording.device_manager.DeviceManager") as mock_dm,
            patch("voice_typer.server.recording.disconnect_handler.DisconnectHandler") as mock_dh,
            patch("voice_typer.server.recording.audio_pipeline.AudioPipeline") as mock_ap,
            patch("voice_typer.server.recording.capture.AudioCallbackDispatcher") as mock_cd,
            patch("voice_typer.server.recording.stream_lifecycle.StreamLifecycle") as mock_sl,
            patch("voice_typer.server.recording.session_state.SessionState") as mock_ss,
        ):
            host = _MockRecorderHost()
            host._setup_device_state_and_collaborators()
            yield host, (mock_dm, mock_dh, mock_ap, mock_cd, mock_sl, mock_ss)

    def test_stop_generation_initialized_to_zero(self, host_with_patched_collaborators):
        host, _ = host_with_patched_collaborators
        assert host._stop_generation == 0

    def test_user_stop_pending_initialized_to_false(self, host_with_patched_collaborators):
        host, _ = host_with_patched_collaborators
        assert host._user_stop_pending is False

    def test_disconnect_handler_lock_is_threading_lock(self):
        """
        ``DisconnectHandler._single_flight_lock`` must be a real
        ``threading.Lock`` initialized at construction time (C-ARCH-2:
        """
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        handler = DisconnectHandler(recorder=MagicMock(name="recorder"))
        assert hasattr(handler._single_flight_lock, "__enter__")
        assert hasattr(handler._single_flight_lock, "__exit__")
        # Sanity-check: the lock can actually be acquired + released.
        with handler._single_flight_lock:
            pass

    def test_disconnect_handler_running_initialized_to_false(self):
        """
        ``DisconnectHandler._single_flight_running`` must initialize
        ``Recorder._disconnect_handler_running``; pinned behavior
        """
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        handler = DisconnectHandler(recorder=MagicMock(name="recorder"))
        assert handler._single_flight_running is False


class TestCollaboratorConstruction:
    """Each of the six collaborators must be constructed exactly once"""

    @pytest.fixture()
    def patched_collaborators(self):
        """Yield ``(host, mock_dm, mock_dh, mock_ap, mock_cd, mock_sl,"""
        with (
            patch("voice_typer.server.recording.device_manager.DeviceManager") as mock_dm,
            patch("voice_typer.server.recording.disconnect_handler.DisconnectHandler") as mock_dh,
            patch("voice_typer.server.recording.audio_pipeline.AudioPipeline") as mock_ap,
            patch("voice_typer.server.recording.capture.AudioCallbackDispatcher") as mock_cd,
            patch("voice_typer.server.recording.stream_lifecycle.StreamLifecycle") as mock_sl,
            patch("voice_typer.server.recording.session_state.SessionState") as mock_ss,
        ):
            host = _MockRecorderHost()
            host._setup_device_state_and_collaborators()
            yield host, mock_dm, mock_dh, mock_ap, mock_cd, mock_sl, mock_ss

    def test_device_manager_constructed_with_self(self, patched_collaborators):
        """mic-watcher + the health-checker thread. Must be constructed"""
        host, mock_dm, *_rest = patched_collaborators
        mock_dm.assert_called_once_with(host)
        assert host._devices is mock_dm.return_value

    def test_disconnect_handler_constructed_with_self(self, patched_collaborators):
        """DisconnectHandler owns the ~175-LOC stream-restart block."""
        host, _mock_dm, mock_dh, *_rest = patched_collaborators
        mock_dh.assert_called_once_with(host)
        assert host._disconnect_handler is mock_dh.return_value

    def test_audio_pipeline_constructed_with_self(self, patched_collaborators):
        """``_process_audio_chunk``. Must be constructed with ``self``"""
        host, _mock_dm, _mock_dh, mock_ap, *_rest = patched_collaborators
        mock_ap.assert_called_once_with(host)
        assert host._audio_pipeline is mock_ap.return_value

    def test_capture_constructed_with_self(self, patched_collaborators):
        """AudioCallbackDispatcher owns the audio worker main loop +"""
        host, _mock_dm, _mock_dh, _mock_ap, mock_cd, *_rest = patched_collaborators
        mock_cd.assert_called_once_with(host)
        assert host._capture is mock_cd.return_value

    def test_stream_lifecycle_constructed_with_self(self, patched_collaborators):
        """StreamLifecycle owns the PortAudio stream-open candidate"""
        host, _mock_dm, _mock_dh, _mock_ap, _mock_cd, mock_sl, *_rest = patched_collaborators
        mock_sl.assert_called_once_with(host)
        assert host._stream_lifecycle is mock_sl.return_value

    def test_session_state_constructed_with_self(self, patched_collaborators):
        """SessionState owns per-session state reset, config-derived"""
        host, _mock_dm, _mock_dh, _mock_ap, _mock_cd, _mock_sl, mock_ss = patched_collaborators
        mock_ss.assert_called_once_with(host)
        assert host._session_state is mock_ss.return_value

    def test_prewarm_device_cache_called_once(self, patched_collaborators):
        """``_prewarm_device_cache`` must be called exactly once at"""
        host, *_rest = patched_collaborators
        host._prewarm_device_cache.assert_called_once_with()


class TestCollaboratorConstructionErrorPath:
    """If a collaborator's ``__init__`` raises (e.g. ``DeviceManager``"""

    def test_device_manager_failure_propagates(self):
        """``_setup_device_state_and_collaborators``."""
        with (
            patch("voice_typer.server.recording.device_manager.DeviceManager") as mock_dm,
            patch("voice_typer.server.recording.disconnect_handler.DisconnectHandler"),
            patch("voice_typer.server.recording.audio_pipeline.AudioPipeline"),
            patch("voice_typer.server.recording.capture.AudioCallbackDispatcher"),
            patch("voice_typer.server.recording.stream_lifecycle.StreamLifecycle"),
            patch("voice_typer.server.recording.session_state.SessionState"),
        ):
            mock_dm.side_effect = OSError("no audio device available")
            host = _MockRecorderHost()
            # The error must propagate (not be swallowed).
            with pytest.raises(OSError, match="no audio device"):
                host._setup_device_state_and_collaborators()
            # The bouncer state is set BEFORE the DeviceManager
            assert host._stop_generation == 0
            assert host._user_stop_pending is False
            # _prewarm_device_cache must NOT have been called (the
            host._prewarm_device_cache.assert_not_called()

    def test_stream_lifecycle_failure_propagates(self):
        """Simulate StreamLifecycle raising (e.g. stream config error)"""
        with (
            patch("voice_typer.server.recording.device_manager.DeviceManager"),
            patch("voice_typer.server.recording.disconnect_handler.DisconnectHandler"),
            patch("voice_typer.server.recording.audio_pipeline.AudioPipeline"),
            patch("voice_typer.server.recording.capture.AudioCallbackDispatcher"),
            patch("voice_typer.server.recording.stream_lifecycle.StreamLifecycle") as mock_sl,
            patch("voice_typer.server.recording.session_state.SessionState"),
        ):
            mock_sl.side_effect = RuntimeError("stream config invalid")
            host = _MockRecorderHost()
            with pytest.raises(RuntimeError, match="stream config invalid"):
                host._setup_device_state_and_collaborators()
            # _prewarm_device_cache must NOT have been called.
            host._prewarm_device_cache.assert_not_called()


class TestConstructionOrder:
    """The mixin constructs collaborators in a specific order"""

    def test_construction_order_is_devices_then_handlers_then_pipeline(self):
        """verify the six collaborators are constructed in the documented"""
        # A single MagicMock that records every constructor call.
        parent_mock = MagicMock()
        with (
            patch(
                "voice_typer.server.recording.device_manager.DeviceManager",
                new=parent_mock.DeviceManager,
            ),
            patch(
                "voice_typer.server.recording.disconnect_handler.DisconnectHandler",
                new=parent_mock.DisconnectHandler,
            ),
            patch(
                "voice_typer.server.recording.audio_pipeline.AudioPipeline",
                new=parent_mock.AudioPipeline,
            ),
            patch(
                "voice_typer.server.recording.capture.AudioCallbackDispatcher",
                new=parent_mock.AudioCallbackDispatcher,
            ),
            patch(
                "voice_typer.server.recording.stream_lifecycle.StreamLifecycle",
                new=parent_mock.StreamLifecycle,
            ),
            patch(
                "voice_typer.server.recording.session_state.SessionState",
                new=parent_mock.SessionState,
            ),
        ):
            host = _MockRecorderHost()
            host._setup_device_state_and_collaborators()

        # Extract the construction call order from parent_mock.mock_calls.
        constructor_names_in_order = [call[0] for call in parent_mock.mock_calls if "." not in str(call[0])]
        # Filter to just the six collaborator constructor names.
        collaborator_names = [
            n
            for n in constructor_names_in_order
            if n
            in {
                "DeviceManager",
                "DisconnectHandler",
                "AudioPipeline",
                "AudioCallbackDispatcher",
                "StreamLifecycle",
                "SessionState",
            }
        ]
        assert collaborator_names == [
            "DeviceManager",
            "DisconnectHandler",
            "AudioPipeline",
            "AudioCallbackDispatcher",
            "StreamLifecycle",
            "SessionState",
        ], f"Collaborator construction order wrong: {collaborator_names}"


class TestRealCollaboratorConstruction:
    """Smoke test: with NO patches, ``_setup_device_state_and_collaborators``"""

    def test_real_construction_does_not_raise(self):
        host = _MockRecorderHost()
        # The real DeviceManager / DisconnectHandler / etc. should
        host._setup_device_state_and_collaborators()
        # Verify the real instances are typed correctly.
        from voice_typer.server.recording.audio_pipeline import AudioPipeline
        from voice_typer.server.recording.capture import AudioCallbackDispatcher
        from voice_typer.server.recording.device_manager import DeviceManager
        from voice_typer.server.recording.disconnect_handler import (
            DisconnectHandler,
        )
        from voice_typer.server.recording.session_state import SessionState
        from voice_typer.server.recording.stream_lifecycle import StreamLifecycle

        assert isinstance(host._devices, DeviceManager)
        assert isinstance(host._disconnect_handler, DisconnectHandler)
        assert isinstance(host._audio_pipeline, AudioPipeline)
        assert isinstance(host._capture, AudioCallbackDispatcher)
        assert isinstance(host._stream_lifecycle, StreamLifecycle)
        assert isinstance(host._session_state, SessionState)
        # The back-reference must be set on each collaborator.
        assert host._devices.recorder is host
        # _prewarm_device_cache must have been called (the stub).
        host._prewarm_device_cache.assert_called_once_with()
