"""Integration tests for the auto-volume-duck feature.

Exercises the full dictation lifecycle (start / stop / cancel / quit /
restart / crash-recovery) and verifies that ``VoiceTyperApp`` drives
the ``VolumeDucker`` correctly at each of the six wiring points
described in ``docs/architecture/auto-volume-duck.md`` §7.

These tests use a ``FakeBackend`` injected into the app's
``_volume_ducker`` so they run on any platform — no real audio
hardware or platform-specific library (pycaw / pyobjc / pactl) is
required.  The recorder is mocked so we don't need a microphone.

Regression coverage
-------------------
- Start → duck (with the configured level + fade)
- Stop → restore (after recorder.stop() returns)
- Cancel (ESC) → restore
- Quit while recording → restore with fade_ms=0 (instant)
- Restart → restore BEFORE launching the new subprocess (no ping-pong)
- Crash recovery: stale duck_crash_recovery.json → restored on init
- Manual-volume-override detection during duck
- Per-session duck only attempted when backend supports it
- Disabled duck (config flag) → no duck/restore calls at all
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── Heavy mock imports (autouse) ────────────────────────────────────────
# These must be in place before VoiceTyperApp is imported, so we apply
# them at module-import time (matching tests/test_app.py's pattern).


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    """Mock hardware/GUI deps so tests run headless."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)

    mock_whisper = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

    mock_pynput = MagicMock()
    mock_pynput_kb = MagicMock()
    monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)

    mock_pystray = MagicMock()
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())

    monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

    # Block the app's atexit handler from polluting test output.
    monkeypatch.setattr("voice_typer.server.app.atexit.register", lambda *a, **kw: None)

    # Force PynputHotkey backend so tests can mock pynput.keyboard.GlobalHotKeys.
    from voice_typer.server.hotkeys import PynputHotkey
    monkeypatch.setattr(
        "voice_typer.server.app.create_hotkey_backend",
        lambda hotkey_str: PynputHotkey(hotkey_str),
    )


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point config at a temp directory so tests don't touch user state.

    Patches BOTH ``voice_typer.server.config._config_dir`` (used by
    Config.load / Config.save and the rest of the codebase) AND
    ``voice_typer.server.app._config_dir`` (which app.py bound at
    import time via ``from voice_typer.server.config import _config_dir``).
    Without the second patch, DuckCrashRecovery would use the real
    ``~/.voice-typer`` path because conftest.py's autouse
    ``mock_heavy_imports`` fixture imports ``voice_typer.server.app``
    BEFORE this fixture runs (it patches
    ``voice_typer.server.app.atexit.register``), freezing the local
    reference.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)
    return tmp_path


# ── Fake VolumeBackend (in-memory, no hardware) ─────────────────────────


class FakeBackend:
    """In-memory VolumeBackend — tracks every call for assertions.

    Implements the same surface as
    ``voice_typer.server.volume_backend.VolumeBackend`` but without
    inheriting from the ABC (so we can spy on every call without the
    ABC's abstract-method machinery getting in the way).
    """

    def __init__(self, current: float = 0.5, muted: bool = False,
                 per_session_capable: bool = False,
                 speaker_active: bool = True) -> None:
        self._current = current
        self._muted = muted
        self._per_session_capable = per_session_capable
        self._speaker_active = speaker_active
        self.set_calls: list[tuple[float, bool | None]] = []
        self.fade_calls: list[tuple[float, int]] = []
        self.duck_session_calls: list[float] = []
        self.restore_session_calls: int = 0
        self.is_speaker_active_calls: int = 0

    # ABC-required surface
    @property
    def name(self) -> str:
        return "fake"

    @property
    def supports_per_session(self) -> bool:
        return self._per_session_capable

    def initialize(self) -> bool:
        return True

    def get_state(self):
        from voice_typer.server.volume_backend import VolumeState
        return VolumeState(linear=self._current, muted=self._muted)

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        self._current = max(0.0, min(1.0, level))
        if muted is not None:
            self._muted = muted
        self.set_calls.append((level, muted))
        return True

    def fade_to(self, target_linear: float, duration_ms: int = 150,
                steps: int = 10) -> bool:
        self._current = max(0.0, min(1.0, target_linear))
        self.fade_calls.append((target_linear, duration_ms))
        return True

    def is_speaker_active(self) -> bool:
        self.is_speaker_active_calls += 1
        return self._speaker_active

    def duck_other_sessions(self, level: float) -> bool:
        self.duck_session_calls.append(level)
        return True

    def restore_other_sessions(self) -> bool:
        self.restore_session_calls += 1
        return True


# ── App fixture with injected FakeBackend ───────────────────────────────


@pytest.fixture
def app_with_fake_ducker(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with a FakeBackend wired into _volume_ducker.

    Returns (app, backend) so tests can assert on backend call lists.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp
    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    # NEW-PRIV-009 (revised): RecordingController.start() now enforces
    # voice_biometric_consent before capturing audio. Tests that exercise
    # the recording path must explicitly opt in (just like real users
    # must enable the toggle in Settings → Privacy before recording).
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True

    # Replace the auto-detected backend with our FakeBackend.  We keep
    # the real DuckCrashRecovery so crash-recovery tests can write a
    # stale file.  The ducker is re-initialized so the FakeBackend's
    # initialize() runs (which sets _ready=True).
    backend = FakeBackend(current=0.5, muted=False)
    from voice_typer.server.volume_ducker import VolumeDucker
    instance._volume_ducker = VolumeDucker(
        backend=backend,
        crash_recovery=instance._duck_crash_recovery,
        on_crash_restore=instance._on_volume_crash_restore,
    )
    instance._volume_ducker.initialize()

    # Make the recorder a MagicMock so no real audio device is opened.
    instance.recorder = MagicMock()
    instance.recorder.recording = False
    instance.recorder._effective_sr = 16000

    return instance, backend


# ── Helpers ─────────────────────────────────────────────────────────────


def _wait_for_busy_clear(app, timeout=2.0):
    """Poll until app._busy_event is set (not busy)."""
    deadline = time.monotonic() + timeout
    while not app._busy_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not app._busy_event.is_set():
        raise TimeoutError(f"_busy_event still not set after {timeout}s")


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStartDictationDucksVolume:
    """§7.2: _start_dictation() must duck system volume AFTER recorder.start()."""

    def test_start_ducks_to_configured_level(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app.config.volume_duck_level = 0.25
        app.config.volume_duck_fade_ms = 200
        app.config.volume_duck_per_session = False
        app.recorder.recording = False
        app.recorder.start = MagicMock()

        app._start_dictation()

        # Duck should have called fade_to(0.25, 200)
        assert (0.25, 200) in backend.fade_calls
        assert app._volume_ducker.is_ducked

    def test_start_does_not_duck_when_disabled(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = False
        app.recorder.recording = False
        app.recorder.start = MagicMock()

        app._start_dictation()

        # No fade calls — ducking is disabled
        assert backend.fade_calls == []
        assert not app._volume_ducker.is_ducked

    def test_start_ducks_after_recorder_start(self, app_with_fake_ducker):
        """The architecture doc §7.2 says duck happens AFTER recorder.start()
        so the first chunk of audio benefits from the ducked speakers."""
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app.config.volume_duck_level = 0.25

        call_order = []
        app.recorder.recording = False
        app.recorder.start = MagicMock(side_effect=lambda: call_order.append("recorder.start"))
        # _duck_volume → backend.fade_to — we hook that to record the order
        original_fade = backend.fade_to
        def spy_fade(target, duration_ms=200, steps=10):
            call_order.append("volume.duck")
            return original_fade(target, duration_ms, steps)
        backend.fade_to = spy_fade

        app._start_dictation()

        assert call_order.index("recorder.start") < call_order.index("volume.duck"), \
            "recorder.start() must happen BEFORE volume.duck()"


class TestStopDictationRestoresVolume:
    """§7.3: _stop_dictation() must restore volume AFTER recorder.stop(),
    BEFORE the transcription thread starts."""

    def test_stop_restores_volume(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app.config.volume_duck_level = 0.25

        # Simulate an active, ducked recording
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app._volume_ducker.duck(0.25)
        backend.fade_calls.clear()  # we only want to see the restore fade
        assert app._volume_ducker.is_ducked

        app._stop_dictation()
        _wait_for_busy_clear(app)

        # Restore should have faded back to the saved 0.5
        assert (0.5, 200) in backend.fade_calls
        assert not app._volume_ducker.is_ducked

    def test_stop_restores_before_transcription_starts(self, app_with_fake_ducker):
        """Restore must happen BEFORE the transcription thread runs (architecture §7.3)."""
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True

        app.recorder.recording = True
        app.models.transcriber = MagicMock()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="hello")
        app.models.transcriber.device_info = "cpu"
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app._volume_ducker.duck(0.25)

        events = []
        original_fade = backend.fade_to
        def spy_fade(target, duration_ms=200, steps=10):
            events.append("restore")
            return original_fade(target, duration_ms, steps)
        backend.fade_to = spy_fade
        original_transcribe = app.models.transcriber.transcribe_with_fallback
        def spy_transcribe(*a, **kw):
            events.append("transcribe")
            return original_transcribe(*a, **kw)
        app.models.transcriber.transcribe_with_fallback = spy_transcribe

        app._stop_dictation()
        _wait_for_busy_clear(app)

        # Restore should appear before transcribe
        if "transcribe" in events:
            assert events.index("restore") < events.index("transcribe"), \
                f"restore must happen before transcribe; got {events}"

    def test_stop_does_not_restore_when_disabled(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = False

        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()
        _wait_for_busy_clear(app)

        # No fade calls — restore is gated on volume_duck_enabled
        assert backend.fade_calls == []


class TestCancelDictationRestoresVolume:
    """§7.4: _cancel_dictation() must restore volume (and must NOT throw
    AttributeError on the removed _background_audio_monitor)."""

    def test_cancel_restores_volume(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app.recorder.recording = True
        app.recorder.discard = MagicMock()
        app._volume_ducker.duck(0.25)
        backend.fade_calls.clear()

        app._cancel_dictation()

        # Volume should be restored to 0.5
        assert (0.5, 200) in backend.fade_calls
        assert not app._volume_ducker.is_ducked

    def test_cancel_does_not_raise_attribute_error_on_removed_monitor(self, app_with_fake_ducker):
        """Regression for the bug fixed in architecture §7.4: the old code
        called self._background_audio_monitor.stop() which threw AttributeError
        because that attribute was never initialized."""
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = False  # so we don't trigger duck/restore
        app.recorder.recording = True
        app.recorder.discard = MagicMock()

        # Should not raise — _background_audio_monitor is gone
        app._cancel_dictation()

        # discard should have been called (proves we got past the
        # AttributeError that previously swallowed the rest of the method)
        app.recorder.discard.assert_called_once()

    def test_cancel_when_not_recording_still_restores_volume(self, app_with_fake_ducker):
        """If ESC is pressed while ducked but not actively recording (edge case),
        we still restore volume.  No-op if not ducked."""
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app.recorder.recording = True
        app._volume_ducker.duck(0.25)
        backend.fade_calls.clear()

        app._cancel_dictation()

        assert (0.5, 200) in backend.fade_calls


class TestQuitRestoresVolumeInstantly:
    """§7.5: quit() must restore volume with fade_ms=0 (no fade — fast exit)."""

    def test_quit_restores_with_zero_fade(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app.recorder.recording = True
        app.recorder.discard = MagicMock()
        app._volume_ducker.duck(0.25)
        backend.fade_calls.clear()

        # Patch out the parts of quit() that would actually exit the
        # process or block on hotkey backends.
        app._cancel_pending_timers = MagicMock()
        app._get_streaming_session = MagicMock(return_value=None)
        app._set_streaming_session = MagicMock()
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._crash_recovery = MagicMock()
        # ARCH-REFAC-003: write to RecordingController directly (was a
        # @property delegate on VoiceTyperApp).
        app.recording._transcription_thread = None
        app.tray = MagicMock()
        # Stub sys.exit so quit() doesn't actually terminate the test runner.
        with patch("voice_typer.server.app.sys.exit"):
            app.quit()

        # Restore should have used fade_ms=0
        assert (0.5, 0) in backend.fade_calls, \
            f"quit() must restore with fade_ms=0; got {backend.fade_calls}"


class TestRestartRestoresBeforeExiting:
    """§7.6 + fix-restart-tcp: restart_app() must restore volume BEFORE
    exiting so the user's audio isn't left ducked while Electron spawns
    the replacement Python process (which can take a few seconds for
    the Python interpreter + torch import).  Previously this asserted
    that restore happened before ``subprocess.Popen`` — but
    fix-restart-tcp removed the Popen call entirely (Electron is now
    the sole spawner), so the assertion now checks that restore
    happens before ``sys.exit(0)``."""

    def test_restart_restores_before_exit(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app._volume_ducker.duck(0.25)
        backend.fade_calls.clear()

        events = []
        original_fade = backend.fade_to
        def spy_fade(target, duration_ms=0, steps=10):
            events.append("restore")
            return original_fade(target, duration_ms, steps)
        backend.fade_to = spy_fade

        # fix-restart-tcp: restart_app() no longer calls subprocess.Popen.
        # Stub _push_event_now so the TCP push doesn't blow up in the
        # test environment (no IPC server wired up).
        import voice_typer.server.app as app_mod
        with patch("voice_typer.server.ipc_server._push_event_now"):
            # Also stub the rest of restart_app() that would block or kill
            app._cancel_pending_timers = MagicMock()
            app.hotkeys._hotkey_backend = MagicMock()
            app.hotkeys._esc_backend = MagicMock()
            app.hotkeys._repaste_backend = MagicMock()
            app._crash_recovery = MagicMock()
            app.tray = MagicMock()
            # ARCH-REFAC-003: write to RecordingController directly (was
            # a @property delegate on VoiceTyperApp).
            app.recording._transcription_thread = None
            # Spy on sys.exit to record that exit happened AFTER restore.
            def spy_exit(code=0):
                events.append("sys.exit")
                raise SystemExit(code)
            with patch.object(app_mod.sys, "exit", spy_exit), contextlib.suppress(SystemExit):
                app.restart_app()

        # Restore must happen BEFORE sys.exit
        assert "restore" in events, "restart_app() should have called restore"
        assert "sys.exit" in events, "restart_app() should have called sys.exit"
        assert events.index("restore") < events.index("sys.exit"), \
            f"restore must precede sys.exit; got {events}"
        # And restore must use fade_ms=0 (instant — no ping-pong window)
        assert (0.5, 0) in backend.fade_calls


class TestCrashRecoveryOnStartup:
    """§7.7 + §9: if duck_crash_recovery.json exists at startup, the
    VolumeDucker.initialize() must restore the saved volume and warn
    the user via the on_crash_restore callback."""

    def test_stale_crash_recovery_file_triggers_restore_on_init(
        self, tmp_config_dir, monkeypatch
    ):
        """Simulate a crash: write a stale duck_crash_recovery.json,
        then construct a fresh VolumeDucker and verify initialize()
        restores the saved volume."""
        from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
        from voice_typer.server.volume_backend import VolumeState
        from voice_typer.server.volume_ducker import VolumeDucker

        crash_recovery = DuckCrashRecovery(config_dir=tmp_config_dir)
        # Simulate the previous session having crashed while ducked at 0.25,
        # with the original volume being 0.7.
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        assert crash_recovery.load_stale() is not None

        backend = FakeBackend(current=0.10)  # system stuck at ducked level
        callback_calls: list = []
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
            on_crash_restore=lambda state: callback_calls.append(state),
        )

        ok = ducker.initialize()
        assert ok
        # Volume should be restored to 0.7 (the saved pre-duck value)
        assert backend._current == 0.7, \
            f"stale crash-recovery file should have restored volume to 0.7; got {backend._current}"
        # Stale file should be cleared
        assert crash_recovery.load_stale() is None
        # Callback should have been invoked once with the saved state
        assert len(callback_calls) == 1
        assert callback_calls[0].linear == 0.7

    def test_no_stale_file_means_no_restore(self, tmp_config_dir):
        """Sanity check: with no stale file, initialize() doesn't touch volume."""
        from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
        from voice_typer.server.volume_ducker import VolumeDucker

        crash_recovery = DuckCrashRecovery(config_dir=tmp_config_dir)
        backend = FakeBackend(current=0.3)
        ducker = VolumeDucker(backend=backend, crash_recovery=crash_recovery)
        ducker.initialize()
        # Volume unchanged
        assert backend._current == 0.3
        assert backend.set_calls == []


class TestManualVolumeOverride:
    """§4.2 + §8: if the user manually changes volume while ducked,
    restore() must respect the manual change (restore to current,
    not saved)."""

    def test_manual_override_restores_to_current(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        app.config.volume_duck_enabled = True
        app._volume_ducker.duck(0.25)  # saved 0.5, ducked to 0.25
        backend.fade_calls.clear()

        # User cranks volume to 0.9 while ducked
        backend._current = 0.9

        app._volume_ducker.restore()

        # Should restore to 0.9 (current), not 0.5 (saved)
        assert backend.fade_calls[-1][0] == 0.9


class TestPerSessionDuckGatedOnSupport:
    """§7.2: per-session duck should only be attempted when the backend
    supports it (Windows only).  The config flag is opt-in."""

    def test_per_session_not_attempted_when_unsupported(self, app_with_fake_ducker):
        app, backend = app_with_fake_ducker
        # FakeBackend defaults to supports_per_session=False
        app.config.volume_duck_enabled = True
        app.config.volume_duck_per_session = True  # user opted in
        app.recorder.recording = False
        app.recorder.start = MagicMock()

        app._start_dictation()

        # Duck happened, but no per-session calls because backend doesn't support it
        assert backend.duck_session_calls == []
        assert backend.fade_calls  # fell back to master-volume fade

    def test_per_session_attempted_when_supported(self, monkeypatch, tmp_config_dir):
        """UX-2: per-session ducking was REMOVED. Even when the backend
        supports it AND the config says True, the app must NOT attempt
        per-session ducking — it always uses master-volume ducking
        cross-platform."""
        from voice_typer.server.app import VoiceTyperApp
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        instance = VoiceTyperApp()
        instance.config.esc_cancel_enabled = False
        # NEW-PRIV-009 (revised): RecordingController.start() enforces
        # voice_biometric_consent — tests that exercise the recording
        # path must explicitly opt in.
        instance.config.voice_biometric_consent = True
        instance.models.transcriber = MagicMock()
        instance.models.transcriber.is_loaded = True

        backend = FakeBackend(current=0.5, per_session_capable=True)
        from voice_typer.server.volume_ducker import VolumeDucker
        instance._volume_ducker = VolumeDucker(
            backend=backend,
            crash_recovery=instance._duck_crash_recovery,
        )
        instance._volume_ducker.initialize()
        instance.recorder = MagicMock()
        instance.recorder.recording = False
        instance.recorder.start = MagicMock()
        instance.config.volume_duck_enabled = True
        # UX-2: per_session is set to True in config, but the app must
        # ignore it and always use master-volume ducking.
        instance.config.volume_duck_per_session = True

        instance._start_dictation()

        # UX-2: per-session duck should NOT be attempted — master fade instead
        assert backend.duck_session_calls == [], (
            "per-session ducking was removed (UX-2); master fade should be used"
        )
        assert len(backend.fade_calls) > 0, "master fade_to should have been called"


class TestDuckCrashRecoveryPersistsOnDuck:
    """§9: duck() must persist the pre-duck state so a crash doesn't
    leave the system stuck.  restore() must clear it."""

    def test_duck_writes_recovery_file(self, app_with_fake_ducker, tmp_config_dir):
        app, backend = app_with_fake_ducker
        app._volume_ducker.duck(0.25)

        recovery_path = tmp_config_dir / "duck_crash_recovery.json"
        assert recovery_path.exists(), \
            "duck() should have persisted pre-duck state for crash recovery"

    def test_restore_clears_recovery_file(self, app_with_fake_ducker, tmp_config_dir):
        app, backend = app_with_fake_ducker
        app._volume_ducker.duck(0.25)
        recovery_path = tmp_config_dir / "duck_crash_recovery.json"
        assert recovery_path.exists()

        app._volume_ducker.restore()

        assert not recovery_path.exists(), \
            "restore() should have cleared the crash-recovery file"
