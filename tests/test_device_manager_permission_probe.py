"""FR-17 — regression tests for the periodic microphone-permission
re-probe in :mod:`voice_typer.server.recording.device_manager`.

Pre-fix symptom: ``permissions.verify_microphone_accessible()`` was
only invoked inside ``Recorder.start()`` — a one-shot gate. If the OS
revoked microphone access mid-recording (macOS System Settings →
Privacy & Security → Microphone, Windows Privacy toggle, Flatpak
portal), PortAudio kept the stream open but delivered zero-filled
buffers. The device_health_checker saw "device still present" so the
disconnect handler never fired. The user saw the REC indicator but no
transcription, until silence-auto-stop triggered with a misleading
"silence detected" notification after 30-60 s.

Post-fix: ``device_manager._device_health_checker_loop`` calls
``permissions.check_microphone_permission()`` every
``_permission_check_interval``-th iteration (~60 s default). On DENIED,
sets ``_device_disconnected=True`` and spawns a handler that calls
``recorder.on_microphone_permission_revoked`` (a NEW callback distinct
from ``on_silence_auto_stop``).

These tests run on any platform — they construct a minimal
``DeviceManager`` via ``__new__`` to avoid the full ``Recorder``
dependency tree, and they mock ``permissions.check_microphone_permission``
directly.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock


def _make_minimal_device_manager():
    """Build a ``DeviceManager`` via ``__new__`` with only the
    attributes the FR-17 path reads.

    The full ``__init__`` constructs a ``MicrophoneDeviceWatcher`` and
    reads ``recorder.config`` — neither is needed for the permission
    re-probe path.
    """
    from voice_typer.server.recording.device_manager import DeviceManager

    dm = DeviceManager.__new__(DeviceManager)
    dm.recorder = MagicMock()
    # ``_device_disconnected`` is the flag the helper sets on DENIED.
    dm._device_disconnected = False
    # Default interval (2 → ~60 s with 30 s wake). Tests override to 1
    # so the very first loop wake triggers the probe.
    dm._permission_check_counter = 0
    dm._permission_check_interval = 1
    # ``_device_health_stop_event`` is used by the loop's ``wait()``
    # call — set it so the loop exits after one iteration in tests.
    dm._device_health_stop_event = threading.Event()
    # ``_device_check_interval_s`` is the wake interval — set to 0.01
    # so the test's loop wake is fast.
    dm._device_check_interval_s = 0.01
    # The recorder's ``_recording_event`` must report "recording" so
    # the helper doesn't bail out early.
    dm.recorder._recording_event.is_set.return_value = True
    dm.recorder._stop_generation = 42
    # ``_spawn_device_thread`` is the helper that schedules the
    # handler on a fresh daemon thread. Use a MagicMock so the test
    # can inspect the call.
    dm.recorder._spawn_device_thread = MagicMock()
    # ``on_microphone_permission_revoked`` is the callback the helper
    # invokes. Defaults to None (not wired) so the fallback path is
    # exercised; individual tests override to a callable. We also
    # explicitly pin ``on_device_lost`` to None so the test exercises
    # the "neither on_microphone_permission_revoked nor on_device_lost
    # is wired → fall back to on_silence_auto_stop" path; without
    # this pin, ``MagicMock`` would auto-vivify ``on_device_lost`` as a
    # truthy callable and the fallback chain would short-circuit at
    # the on_device_lost step.
    dm.recorder.on_microphone_permission_revoked = None
    dm.recorder.on_device_lost = None
    dm.recorder.on_silence_auto_stop = None
    return dm


# _check_microphone_permission_revoked ────────────────────────


class TestPermissionRevokedDetection:
    """FR-17: ``_check_microphone_permission_revoked`` returns True and
    schedules the handler when the OS reports DENIED."""

    def test_returns_false_on_granted(self, monkeypatch):
        """On GRANTED, the helper returns False and does NOT set
        ``_device_disconnected``."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        assert dm._check_microphone_permission_revoked() is False
        assert dm._device_disconnected is False
        dm.recorder._spawn_device_thread.assert_not_called()

    def test_returns_false_on_prompt(self, monkeypatch):
        """On PROMPT (macOS NotDetermined), the helper returns False —
        the OS will re-prompt on next access, not a revocation."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.PROMPT,
        )
        assert dm._check_microphone_permission_revoked() is False

    def test_returns_false_on_unknown(self, monkeypatch):
        """On UNKNOWN (probe failed / unsupported platform), the helper
        returns False — defer to the runtime PortAudio-open
        re-classification path in the recorder."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.UNKNOWN,
        )
        assert dm._check_microphone_permission_revoked() is False

    def test_returns_true_and_sets_disconnect_on_denied(self, monkeypatch):
        """On DENIED, the helper returns True, sets
        ``_device_disconnected=True``, and schedules the handler via
        ``recorder._spawn_device_thread``."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        assert dm._check_microphone_permission_revoked() is True
        assert dm._device_disconnected is True
        dm.recorder._spawn_device_thread.assert_called_once()
        # The handler name must be distinct from "device-disconnect-check"
        # so single-flight doesn't dedupe with the existing disconnect
        # handler.
        call_kwargs = dm.recorder._spawn_device_thread.call_args.kwargs
        assert call_kwargs.get("name") == "mic-permission-revoked", (
            f"FR-17: spawned handler must be named 'mic-permission-revoked' "
            f"(distinct from 'device-disconnect-check'); got: {call_kwargs}"
        )

    def test_does_not_fire_when_recording_stopped(self, monkeypatch):
        """If the user already stopped the recording before the probe
        fired, the helper must NOT schedule the handler — there's
        nothing to revoke."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # ``_recording_event.is_set()`` returns False → recording
        # already stopped.
        dm.recorder._recording_event.is_set.return_value = False
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        # Returns False (nothing to revoke) and does NOT set the
        # disconnect flag.
        assert dm._check_microphone_permission_revoked() is False
        assert dm._device_disconnected is False
        dm.recorder._spawn_device_thread.assert_not_called()

    def test_probe_failure_returns_false(self, monkeypatch):
        """If ``check_microphone_permission`` itself raises, the helper
        must return False (never take down the health-checker thread)."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()

        def boom():
            raise RuntimeError("probe crashed")

        monkeypatch.setattr(permissions, "check_microphone_permission", boom)
        assert dm._check_microphone_permission_revoked() is False
        assert dm._device_disconnected is False
        dm.recorder._spawn_device_thread.assert_not_called()


# handler invokes the distinct callback ──────────────────────


class TestPermissionRevokedHandlerCallback:
    """FR-17: the spawned handler invokes
    ``recorder.on_microphone_permission_revoked`` (a NEW callback
    distinct from ``on_silence_auto_stop``)."""

    def test_handler_calls_on_microphone_permission_revoked(self, monkeypatch):
        """The spawned handler target must invoke the
        ``on_microphone_permission_revoked`` callback when wired."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # Wire the callback.
        called: list[bool] = []
        dm.recorder.on_microphone_permission_revoked = lambda: called.append(True)
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        dm._check_microphone_permission_revoked()

        # The handler was spawned via _spawn_device_thread — extract
        # the target and invoke it directly (the production code
        # spawns it on a daemon thread; we test the target itself).
        call_kwargs = dm.recorder._spawn_device_thread.call_args.kwargs
        target = call_kwargs["target"]
        target()
        assert called == [True], "FR-17: handler must invoke on_microphone_permission_revoked when wired"

    def test_handler_falls_back_to_on_device_lost_when_not_wired(self, monkeypatch):
        """When ``on_microphone_permission_revoked`` is NOT wired
        (older recording_controller / tests bypassing _start_impl),
        the handler falls back to ``on_device_lost``."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # Don't wire on_microphone_permission_revoked (None).
        # Wire on_device_lost as the fallback.
        device_lost_called: list[bool] = []
        dm.recorder.on_device_lost = lambda: device_lost_called.append(True)
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        dm._check_microphone_permission_revoked()
        call_kwargs = dm.recorder._spawn_device_thread.call_args.kwargs
        target = call_kwargs["target"]
        target()
        assert device_lost_called == [True], (
            "FR-17: handler must fall back to on_device_lost when on_microphone_permission_revoked is not wired"
        )

    def test_handler_falls_back_to_silence_auto_stop_when_neither_wired(self, monkeypatch):
        """When neither ``on_microphone_permission_revoked`` NOR
        ``on_device_lost`` is wired, the handler falls back to
        ``on_silence_auto_stop`` (mirrors the recorder's
        ``_handle_device_disconnect`` fallback chain)."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # Don't wire on_microphone_permission_revoked OR on_device_lost.
        # Wire on_silence_auto_stop as the last-resort fallback.
        silence_called: list[bool] = []
        dm.recorder.on_silence_auto_stop = lambda: silence_called.append(True)
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        dm._check_microphone_permission_revoked()
        call_kwargs = dm.recorder._spawn_device_thread.call_args.kwargs
        target = call_kwargs["target"]
        target()
        assert silence_called == [True], (
            "FR-17: handler must fall back to on_silence_auto_stop when "
            "neither on_microphone_permission_revoked nor on_device_lost "
            "is wired"
        )


# _device_health_checker_loop periodic probe ─────────────────


class TestHealthCheckerLoopPeriodicProbe:
    """FR-17: the health-checker loop calls
    ``_check_microphone_permission_revoked`` every
    ``_permission_check_interval``-th iteration."""

    def test_loop_calls_permission_probe_on_first_iteration(self, monkeypatch):
        """With ``_permission_check_interval=1``, the loop calls the
        permission probe on every wake."""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        dm._permission_check_interval = 1
        probe_calls: list[bool] = []
        original_probe = dm._check_microphone_permission_revoked

        def counting_probe():
            probe_calls.append(True)
            return original_probe()

        dm._check_microphone_permission_revoked = counting_probe
        # GRANTED so the loop continues past the probe.
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        # Run one loop iteration: stop the event AFTER the first wake
        # so the loop body executes once, then exits.
        dm._device_health_stop_event.set()
        # ``wait(timeout=0.01)`` returns True immediately because the
        # event is set — the loop body does NOT run. To exercise the
        # loop body, we need the event NOT set, then set it after a
        # short delay.
        dm._device_health_stop_event = threading.Event()

        def _set_stop_after_delay():
            # Wait long enough for one wake, then set the stop event.
            import time

            time.sleep(0.05)
            dm._device_health_stop_event.set()

        threading.Thread(target=_set_stop_after_delay, daemon=True).start()
        dm._device_health_checker_loop()
        assert len(probe_calls) >= 1, (
            "FR-17: health-checker loop must call the permission probe on "
            "the first wake when _permission_check_interval=1"
        )

    def test_loop_skips_permission_probe_when_counter_below_interval(self, monkeypatch):
        """With ``_permission_check_interval=2`` and counter starting
        at 0, the loop does NOT call the probe on the first wake
        (counter goes 0→1, below the threshold of 2).

        To guarantee only ONE wake fires before the stop event, we
        use a long wake interval (0.5s) — the loop is sleeping in
        ``Event.wait(0.5)`` when the stop event arrives (~0.05s
        later), so it wakes-on-stop and exits without a second wake.
        With the original 0.01s wake interval + 0.05s stop delay,
        the loop would race through ~5 wakes before the stop
        arrived and trigger the probe at the 2nd wake.
        """
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        dm._permission_check_interval = 2
        dm._permission_check_counter = 0
        # Long wake interval so the loop is sleeping when the stop
        # event fires (rather than racing through multiple 0.01s
        # wakes).
        dm._device_check_interval_s = 0.5
        probe_calls: list[bool] = []
        original_probe = dm._check_microphone_permission_revoked

        def counting_probe():
            probe_calls.append(True)
            return original_probe()

        dm._check_microphone_permission_revoked = counting_probe
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.GRANTED,
        )
        # Run ONE wake only: stop the event after one wake.
        dm._device_health_stop_event = threading.Event()

        def _set_stop_after_delay():
            import time

            time.sleep(0.05)
            dm._device_health_stop_event.set()

        threading.Thread(target=_set_stop_after_delay, daemon=True).start()
        dm._device_health_checker_loop()
        # After 1 wake with interval=2: counter went 0→1, did NOT
        # reach the threshold of 2 → probe NOT called.
        assert len(probe_calls) == 0, (
            f"FR-17: probe should NOT fire on the first wake when "
            f"_permission_check_interval=2 (counter 0→1, below threshold); "
            f"got {len(probe_calls)} probe calls"
        )
