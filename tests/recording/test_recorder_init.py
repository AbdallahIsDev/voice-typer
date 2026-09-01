"""Tests for ``voice_typer.server.recording.recorder_init``.

``recorder_init.py`` was extracted from the ``Recorder.__init__``
god-method (Phase 4.5 / DT-21 split). It contains
:class:`RecorderInitMixin` with a single method
:meth:`_setup_device_state_and_collaborators` that:

  1. Initializes the disconnect-handler bouncer state
     (``_stop_generation``, ``_user_stop_pending``) and constructs the
     collaborators (each owning its own state: the single-flight guard
     now lives on ``DisconnectHandler`` — see
     ``tests/test_recorder_init.py::TestDisconnectHandlerStateInit``).
  2. Constructs the six collaborators (``DeviceManager``,
     ``DisconnectHandler``, ``AudioPipeline``,
     ``AudioCallbackDispatcher``, ``StreamLifecycle``,
     ``SessionState``) with a back-reference to ``self``.
  3. Calls ``self._prewarm_device_cache()`` to spawn a best-effort
     daemon thread that populates the device-list cache.

The acceptance criteria (TC-INVEST-05) requires exercising this mixin
in ISOLATION with a mock host class — the existing
``tests/test_recording.py`` drives it via the composed ``Recorder``
class, so a regression in the mixin alone would be masked by
``Recorder``'s own behavior. This file builds a small
``_MockRecorderHost`` that wires ``RecorderInitMixin`` and provides
the ``_prewarm_device_cache`` stub, then patches the six collaborator
classes to verify they're constructed with ``self`` and in the right
order.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.recording.recorder_init import RecorderInitMixin

# ---------------------------------------------------------------------------
# Mock host — wires ``RecorderInitMixin`` and provides the
# ``_prewarm_device_cache`` method the mixin calls at the tail.
# ---------------------------------------------------------------------------


class _MockRecorderHost(RecorderInitMixin):
    """Minimal host class for ``RecorderInitMixin``.

    The mixin assumes the host class has already initialized the basic
    Recorder state (``config``, ``_recording_event``, ``_stream``,
    ``_lock``, ``_thread_registry``, ``_effective_sr``, ``_buffer_sr``,
    VAD caches, preroll buffer, ring buffer, worker thread state,
    ``_actual_channels``, ``_mono_scratch_local``) — the contract is
    documented on :meth:`_setup_device_state_and_collaborators`. The
    collaborators' ``__init__`` methods store a back-reference to
    ``self`` and do NOT touch recorder state at construction time, so
    a minimal host with just ``_prewarm_device_cache`` stubbed is
    sufficient for the mixin's construction path.

    Tests that need to verify a specific collaborator (e.g.
    ``DeviceManager``) is constructed with ``self`` patch the
    collaborator class at its source module and assert the call args.
    """

    def __init__(self):
        # The mixin calls ``self._prewarm_device_cache()`` at the tail.
        # Stub it so the test doesn't actually spawn a daemon thread.
        self._prewarm_device_cache = MagicMock()
        # Provide the basic-state attributes the collaborators might
        # reference via the back-reference (the docstring says they
        # don't touch recorder state at construction, but the mock
        # collaborators created by ``patch`` are MagicMocks and don't
        # care; the real collaborators may read ``recorder.config`` /
        # ``recorder._recording_event`` lazily later — not at __init__).
        # STATE-OWNERSHIP: ``AudioPipeline.__init__`` reads
        # ``recorder.config.sample_rate`` (the pipeline owns the
        # recording buffer, whose nominal sample rate comes from the
        # config) — a MagicMock config auto-provides it.
        self.config = MagicMock(sample_rate=16000)
        # The pipeline's recording buffer registers the host's
        # extra-eviction hook (a real Recorder method) at construction.
        self._note_buffer_capacity_eviction = MagicMock()
        self._recording_event = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self._thread_registry = None


# ---------------------------------------------------------------------------
# State-attribute initialization
# ---------------------------------------------------------------------------


class TestDisconnectHandlerStateInit:
    """The bouncer + single-flight state attributes must be initialized
    to their documented defaults so the disconnect-handler thread
    (spawned later by ``_handle_device_disconnect``) sees a clean
    slate on every ``Recorder.__init__``."""

    @pytest.fixture()
    def host_with_patched_collaborators(self):
        """Build a host with all six collaborator classes patched so
        ``_setup_device_state_and_collaborators`` doesn't construct
        the real (heavy) collaborator instances."""
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
        """``DisconnectHandler._single_flight_lock`` must be a real
        ``threading.Lock`` (not a MagicMock) so the disconnect-handler
        thread can actually acquire/release it.

        STATE-OWNERSHIP: the lock + running flag were moved from
        ``Recorder._disconnect_handler_lock`` / ``_disconnect_handler_running``
        onto the owning collaborator (``DisconnectHandler``); the pinned
        BEHAVIOR is unchanged — the guard is a real, acquirable
        ``threading.Lock`` initialized at construction time (C-ARCH-2:
        the test now reads the OWNING submodule's attribute).
        """
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        handler = DisconnectHandler(recorder=MagicMock(name="recorder"))
        # ``threading.Lock`` returns a ``_thread.lock`` object, not a
        # ``Lock`` class instance — check via the context-manager
        # protocol (``__enter__`` / ``__exit__``).
        assert hasattr(handler._single_flight_lock, "__enter__")
        assert hasattr(handler._single_flight_lock, "__exit__")
        # Sanity-check: the lock can actually be acquired + released.
        with handler._single_flight_lock:
            pass

    def test_disconnect_handler_running_initialized_to_false(self):
        """``DisconnectHandler._single_flight_running`` must initialize
        to ``False`` so the first disconnect-handler spawn is not
        single-flight-suppressed (STATE-OWNERSHIP: moved from
        ``Recorder._disconnect_handler_running``; pinned behavior
        unchanged)."""
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        handler = DisconnectHandler(recorder=MagicMock(name="recorder"))
        assert handler._single_flight_running is False


# ---------------------------------------------------------------------------
# Collaborator construction — device enumeration, stream config, etc.
# ---------------------------------------------------------------------------


class TestCollaboratorConstruction:
    """Each of the six collaborators must be constructed exactly once
    with ``self`` as the only positional argument. The construction
    order is documented in the mixin docstring (devices →
    disconnect_handler → audio_pipeline → capture → stream_lifecycle →
    session_state) and the back-reference pattern lets each
    collaborator read/write shared state on the host."""

    @pytest.fixture()
    def patched_collaborators(self):
        """Yield ``(host, mock_dm, mock_dh, mock_ap, mock_cd, mock_sl,
        mock_ss)`` as a flat 7-tuple so individual tests can unpack
        only the mocks they need via ``host, _mock_dm, mock_dh, *_rest``."""
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
        """DeviceManager owns device enumeration + hot-swap + the
        mic-watcher + the health-checker thread. Must be constructed
        with ``self`` as the back-reference."""
        host, mock_dm, *_rest = patched_collaborators
        mock_dm.assert_called_once_with(host)
        assert host._devices is mock_dm.return_value

    def test_disconnect_handler_constructed_with_self(self, patched_collaborators):
        """DisconnectHandler owns the ~175-LOC stream-restart block.
        Must be constructed with ``self`` as the back-reference."""
        host, _mock_dm, mock_dh, *_rest = patched_collaborators
        mock_dh.assert_called_once_with(host)
        assert host._disconnect_handler is mock_dh.return_value

    def test_audio_pipeline_constructed_with_self(self, patched_collaborators):
        """AudioPipeline owns the six named helpers split out of
        ``_process_audio_chunk``. Must be constructed with ``self``
        as the back-reference."""
        host, _mock_dm, _mock_dh, mock_ap, *_rest = patched_collaborators
        mock_ap.assert_called_once_with(host)
        assert host._audio_pipeline is mock_ap.return_value

    def test_capture_constructed_with_self(self, patched_collaborators):
        """AudioCallbackDispatcher owns the audio worker main loop +
        the RT callback dispatch body. Must be constructed with
        ``self`` as the back-reference."""
        host, _mock_dm, _mock_dh, _mock_ap, mock_cd, *_rest = patched_collaborators
        mock_cd.assert_called_once_with(host)
        assert host._capture is mock_cd.return_value

    def test_stream_lifecycle_constructed_with_self(self, patched_collaborators):
        """StreamLifecycle owns the PortAudio stream-open candidate
        loop + teardown body (stream config). Must be constructed with
        ``self`` as the back-reference."""
        host, _mock_dm, _mock_dh, _mock_ap, _mock_cd, mock_sl, *_rest = patched_collaborators
        mock_sl.assert_called_once_with(host)
        assert host._stream_lifecycle is mock_sl.return_value

    def test_session_state_constructed_with_self(self, patched_collaborators):
        """SessionState owns per-session state reset, config-derived
        scalar caching, secure-clear, buffer resizing, preroll prepend.
        Must be constructed with ``self`` as the back-reference."""
        host, _mock_dm, _mock_dh, _mock_ap, _mock_cd, _mock_sl, mock_ss = patched_collaborators
        mock_ss.assert_called_once_with(host)
        assert host._session_state is mock_ss.return_value

    def test_prewarm_device_cache_called_once(self, patched_collaborators):
        """``_prewarm_device_cache`` must be called exactly once at
        the tail of the mixin method (it spawns a best-effort daemon
        thread that populates ``DeviceManager._device_list_cache``
        ahead of the first ``start()`` call)."""
        host, *_rest = patched_collaborators
        host._prewarm_device_cache.assert_called_once_with()


# ---------------------------------------------------------------------------
# Error path — collaborator construction failure propagates
# ---------------------------------------------------------------------------


class TestCollaboratorConstructionErrorPath:
    """If a collaborator's ``__init__`` raises (e.g. ``DeviceManager``
    fails because PortAudio is unavailable / no audio device on a
    headless CI host), the error must propagate to the caller
    (``Recorder.__init__``) so it can be caught and surfaced — the
    mixin must NOT swallow the exception and leave the host in a
    half-constructed state."""

    def test_device_manager_failure_propagates(self):
        """Simulate DeviceManager raising (e.g. missing device) and
        verify the error propagates out of
        ``_setup_device_state_and_collaborators``."""
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
            # construction, so it must be initialized even on the
            # error path.
            assert host._stop_generation == 0
            assert host._user_stop_pending is False
            # _prewarm_device_cache must NOT have been called (the
            # error raised before reaching the tail).
            host._prewarm_device_cache.assert_not_called()

    def test_stream_lifecycle_failure_propagates(self):
        """Simulate StreamLifecycle raising (e.g. stream config error)
        and verify the error propagates. DeviceManager /
        DisconnectHandler / AudioPipeline / Capture are constructed
        before StreamLifecycle, so they're left assigned on the host
        (the host is in a half-constructed state — the caller must
        handle this)."""
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


# ---------------------------------------------------------------------------
# Construction order — devices first, prewarm last
# ---------------------------------------------------------------------------


class TestConstructionOrder:
    """The mixin constructs collaborators in a specific order
    (devices → disconnect_handler → audio_pipeline → capture →
    stream_lifecycle → session_state) so each collaborator can find
    its dependencies already assigned on ``self`` (the docstring notes
    the order mirrors the dependency direction: callback → lifecycle →
    session). This test pins the order so a future refactor can't
    silently reorder construction and break a collaborator's
    assumption about what's already on ``self``."""

    def test_construction_order_is_devices_then_handlers_then_pipeline(self):
        """Use ``MagicMock.mock_calls`` (which records call order) to
        verify the six collaborators are constructed in the documented
        order."""
        # A single MagicMock that records every constructor call.
        # Patch all six classes to return the SAME parent_mock so
        # ``parent_mock.mock_calls`` lists them in call order.
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
        # Each constructor call appears as ``<child_name>(host)``.
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


# ---------------------------------------------------------------------------
# Real (un-patched) construction — verifies the mixin actually wires
# the real collaborator classes without raising. This is a smoke test
# that the local imports inside the mixin still resolve.
# ---------------------------------------------------------------------------


class TestRealCollaboratorConstruction:
    """Smoke test: with NO patches, ``_setup_device_state_and_collaborators``
    must construct the real collaborator instances without raising.
    This verifies the local imports inside the mixin (``from
    .audio_pipeline import AudioPipeline`` etc.) still resolve and
    the real collaborator ``__init__`` methods don't touch any host
    state that the mock host doesn't provide."""

    def test_real_construction_does_not_raise(self):
        host = _MockRecorderHost()
        # The real DeviceManager / DisconnectHandler / etc. should
        # construct fine — their __init__ only stores the back-reference
        # and initializes their own state (see device_manager.py:104).
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
