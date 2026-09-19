"""FR-17: regression tests for the periodic microphone-permission"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock


def _make_minimal_device_manager():
    """attributes the FR-17 path reads."""
    from voice_typer.server.recording.device_manager import DeviceManager

    dm = DeviceManager.__new__(DeviceManager)
    dm.recorder = MagicMock()
    # ``_device_disconnected`` is the flag the helper sets on DENIED.
    dm._device_disconnected = False
    # Default interval (2 → ~60 s with 30 s wake). Tests override to 1
    dm._permission_check_counter = 0
    dm._permission_check_interval = 1
    # ``_device_health_stop_event`` is used by the loop's ``wait()``
    dm._device_health_stop_event = threading.Event()
    # ``_device_check_interval_s`` is the wake interval, set to 0.01
    dm._device_check_interval_s = 0.01
    dm.recorder._recording_event.is_set.return_value = True
    dm.recorder._stop_generation = 42
    dm.recorder._spawn_device_thread = MagicMock()
    dm.recorder.on_microphone_permission_revoked = None
    dm.recorder.on_device_lost = None
    dm.recorder.on_silence_auto_stop = None
    return dm


# _check_microphone_permission_revoked ────────────────────────


class TestPermissionRevokedDetection:
    """FR-17: ``_check_microphone_permission_revoked`` returns True and"""

    def test_returns_false_on_granted(self, monkeypatch):
        """On GRANTED, the helper returns False and does NOT set"""
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
        """On PROMPT (macOS NotDetermined), the helper returns False —"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.PROMPT,
        )
        assert dm._check_microphone_permission_revoked() is False

    def test_returns_false_on_unknown(self, monkeypatch):
        """On UNKNOWN (probe failed / unsupported platform), the helper"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.UNKNOWN,
        )
        assert dm._check_microphone_permission_revoked() is False

    def test_returns_true_and_sets_disconnect_on_denied(self, monkeypatch):
        """On DENIED, the helper returns True, sets"""
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
        call_kwargs = dm.recorder._spawn_device_thread.call_args.kwargs
        assert call_kwargs.get("name") == "mic-permission-revoked", (
            f"FR-17: spawned handler must be named 'mic-permission-revoked' "
            f"(distinct from 'device-disconnect-check'); got: {call_kwargs}"
        )

    def test_does_not_fire_when_recording_stopped(self, monkeypatch):
        """If the user already stopped the recording before the probe"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # ``_recording_event.is_set()`` returns False → recording
        dm.recorder._recording_event.is_set.return_value = False
        monkeypatch.setattr(
            permissions,
            "check_microphone_permission",
            lambda: permissions.MicrophonePermissionState.DENIED,
        )
        assert dm._check_microphone_permission_revoked() is False
        assert dm._device_disconnected is False
        dm.recorder._spawn_device_thread.assert_not_called()

    def test_probe_failure_returns_false(self, monkeypatch):
        """If ``check_microphone_permission`` itself raises, the helper"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()

        def boom():
            raise RuntimeError("probe crashed")

        monkeypatch.setattr(permissions, "check_microphone_permission", boom)
        assert dm._check_microphone_permission_revoked() is False
        assert dm._device_disconnected is False
        dm.recorder._spawn_device_thread.assert_not_called()


class TestPermissionRevokedHandlerCallback:
    """FR-17: the spawned handler invokes"""

    def test_handler_calls_on_microphone_permission_revoked(self, monkeypatch):
        """The spawned handler target must invoke the"""
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

        # The handler was spawned via _spawn_device_thread, extract
        call_kwargs = dm.recorder._spawn_device_thread.call_args.kwargs
        target = call_kwargs["target"]
        target()
        assert called == [True], "FR-17: handler must invoke on_microphone_permission_revoked when wired"

    def test_handler_falls_back_to_on_device_lost_when_not_wired(self, monkeypatch):
        """When ``on_microphone_permission_revoked`` is NOT wired"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # Don't wire on_microphone_permission_revoked (None).
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
        """When neither ``on_microphone_permission_revoked`` NOR"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        # Don't wire on_microphone_permission_revoked OR on_device_lost.
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
    """FR-17: the health-checker loop calls"""

    def test_loop_calls_permission_probe_on_first_iteration(self, monkeypatch):
        """With ``_permission_check_interval=1``, the loop calls the"""
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
        dm._device_health_stop_event.set()
        dm._device_health_stop_event = threading.Event()

        def _set_stop_after_delay():
            # Wait long enough for one wake, then set the stop event.
            import time

            time.sleep(0.05)
            dm._device_health_stop_event.set()

        stop_thread = threading.Thread(target=_set_stop_after_delay, daemon=True)
        stop_thread.start()
        dm._device_health_checker_loop()
        stop_thread.join(timeout=1.0)
        assert len(probe_calls) >= 1, (
            "FR-17: health-checker loop must call the permission probe on "
            "the first wake when _permission_check_interval=1"
        )

    def test_loop_skips_permission_probe_when_counter_below_interval(self, monkeypatch):
        """With ``_permission_check_interval=2`` and counter starting"""
        from voice_typer.server import permissions

        dm = _make_minimal_device_manager()
        dm._permission_check_interval = 2
        dm._permission_check_counter = 0
        # Long wake interval so the loop is sleeping when the stop
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

        stop_thread = threading.Thread(target=_set_stop_after_delay, daemon=True)
        stop_thread.start()
        dm._device_health_checker_loop()
        stop_thread.join(timeout=1.0)
        # After 1 wake with interval=2: counter went 0→1, did NOT
        assert len(probe_calls) == 0, (
            f"FR-17: probe should NOT fire on the first wake when "
            f"_permission_check_interval=2 (counter 0→1, below threshold); "
            f"got {len(probe_calls)} probe calls"
        )
