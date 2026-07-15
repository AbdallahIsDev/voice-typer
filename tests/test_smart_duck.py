"""Tests for the smart-duck feature (skip volume ducking when no audio is playing).

Covers:
- Smart-duck skip path: ``is_speaker_active() -> False`` → no fade, no
  crash-recovery file written, ``_actually_ducked=False``.
- Smart-duck normal path: ``is_speaker_active() -> True`` → duck
  proceeds normally, ``_actually_ducked=True``.
- ``restore()`` after smart-duck skip is a no-op (no fade).
- ``restore()`` after normal duck fades back to saved volume.
- Crash-recovery file is NOT written when smart-duck skips.
- ``volume_duck_smart`` config field gates smart-duck behaviour.
- ``set_smart_duck_enabled(False)`` restores the pre-smart-duck
  always-duck behaviour.
- The v1.1 BUGFIX: second ``duck()`` call after a smart-duck skip
  must NOT call ``fade_to()`` (would fade the user's volume with no
  saved state to restore from).
- ``actually_ducked`` property distinguishes "logically ducked" from
  "volume was actually changed".
- Cross-platform ``is_speaker_active()`` for LinuxVolumeBackend
  (pactl/wpctl/amixer parsing) and MacVolumeBackend (osascript).
"""

from __future__ import annotations

import threading

from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_backends import (
    LinuxVolumeBackend,
    MacVolumeBackend,
)
from voice_typer.server.volume_ducker import VolumeDucker

# ── Shared FakeBackend (matches the one in test_volume_ducker.py) ───────


class FakeBackend(VolumeBackend):
    """In-memory VolumeBackend with controllable speaker activity."""

    def __init__(
        self,
        current: float = 0.5,
        muted: bool = False,
        speaker_active: bool = True,
    ) -> None:
        self._current = current
        self._muted = muted
        self._speaker_active = speaker_active
        self.set_calls: list[tuple[float, bool | None]] = []
        self.fade_calls: list[tuple[float, int]] = []
        self.is_speaker_active_calls: int = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def supports_per_session(self) -> bool:
        return False

    def initialize(self) -> bool:
        return True

    def get_state(self) -> VolumeState | None:
        return VolumeState(linear=self._current, muted=self._muted)

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        self._current = max(0.0, min(1.0, level))
        if muted is not None:
            self._muted = muted
        self.set_calls.append((level, muted))
        return True

    def fade_to(self, target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
        self._current = max(0.0, min(1.0, target_linear))
        self.fade_calls.append((target_linear, duration_ms))
        return True

    def is_speaker_active(self) -> bool:
        self.is_speaker_active_calls += 1
        return self._speaker_active


# ═══════════════════════════════════════════════════════════════════════════
# Smart-duck core behaviour
# ═══════════════════════════════════════════════════════════════════════════


class TestSmartDuckSkip:
    """When is_speaker_active() returns False, duck() should skip the fade."""

    def test_skip_when_no_audio_playing(self):
        """No audio → no fade_to call, no crash-recovery file, _actually_ducked=False."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        ok = ducker.duck(0.25)
        assert ok is True  # success (we "skipped" but that's a success)
        assert backend.fade_calls == [], "Smart-duck skip should NOT call fade_to"
        assert backend.is_speaker_active_calls == 1, "duck() should query is_speaker_active() exactly once"
        assert ducker.is_ducked is True, "is_ducked should be True (logical state) even after skip"
        assert ducker.actually_ducked is False, "actually_ducked should be False — we skipped the fade"

    def test_skip_does_not_write_crash_recovery(self, tmp_path):
        """Smart-duck skip must NOT write a crash-recovery file.

        Rationale: the crash-recovery file is the "I crashed while
        ducked" signal.  If we skipped the duck, we didn't change the
        volume, so there's nothing to recover from.  Writing a file
        would cause the next launch to "restore" a volume that was
        never changed — confusing and wrong.
        """
        from voice_typer.server.duck_crash_recovery import DuckCrashRecovery

        cr = DuckCrashRecovery(config_dir=tmp_path)
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend, crash_recovery=cr)
        ducker.initialize()

        ducker.duck(0.25)
        assert not cr.path.exists(), "Smart-duck skip should NOT persist a crash-recovery file"

    def test_skip_then_restore_is_noop(self):
        """After a smart-duck skip, restore() should NOT call fade_to."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ducker.duck(0.25)
        backend.fade_calls.clear()

        ok = ducker.restore()
        assert ok is True
        assert backend.fade_calls == [], "restore() after smart-duck skip should NOT fade"
        assert ducker.is_ducked is False

    def test_skip_does_not_change_volume(self):
        """The actual system volume must NOT change when smart-duck skips."""
        backend = FakeBackend(current=0.7, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        ducker.duck(0.25)
        assert backend._current == 0.7, f"Volume should be unchanged after smart-duck skip; got {backend._current}"

        ducker.restore()
        assert backend._current == 0.7, f"Volume should still be unchanged after restore; got {backend._current}"


class TestSmartDuckNormal:
    """When is_speaker_active() returns True, duck() proceeds normally."""

    def test_duck_proceeds_when_audio_playing(self):
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        ducker.duck(0.25)
        assert (0.25, 150) in backend.fade_calls
        assert ducker.actually_ducked is True
        assert ducker.is_ducked is True

    def test_restore_fades_back_after_normal_duck(self):
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ducker.duck(0.25)
        backend.fade_calls.clear()

        ducker.restore()
        assert (0.5, 150) in backend.fade_calls
        assert ducker.actually_ducked is False  # after restore, no longer ducked


# ═══════════════════════════════════════════════════════════════════════════
# The v1.1 BUGFIX: second duck() after smart-duck skip
# ═══════════════════════════════════════════════════════════════════════════


class TestSmartDuckSecondDuckAfterSkip:
    """Regression: v1 had a bug where calling duck() a second time after a
    smart-duck skip would call fade_to() — fading the user's volume down to
    the new duck level with no saved state to restore from.

    Scenario:
      1. duck(0.25) called, no audio playing → smart-duck skips, no fade.
         _saved_state is set (so is_ducked reports True), _actually_ducked=False.
      2. duck(0.15) called (e.g. config changed mid-dictation, or a second
         dictation starts before stop).

    v1 behaviour (BUG): the `else` branch of duck() runs because
    _saved_state is non-None.  It calls fade_to(0.15), which fades the
    user's volume from 0.5 to 0.15 with no saved state to restore from.

    v1.1 behaviour (FIXED): the `else` branch checks _actually_ducked
    first.  If False (smart-duck skipped), it just updates the logical
    ducked_level and returns True without calling fade_to.
    """

    def test_second_duck_after_skip_does_not_fade(self):
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        # First duck — smart-duck skips
        ducker.duck(0.25)
        assert backend.fade_calls == []
        assert ducker.actually_ducked is False

        # Second duck — must NOT fade (v1.1 bugfix)
        ok = ducker.duck(0.15)
        assert ok is True
        assert backend.fade_calls == [], f"Second duck() after smart-duck skip must NOT fade; got {backend.fade_calls}"
        # ducked_level should be updated for restore() consistency
        assert ducker._ducked_level == 0.15

    def test_second_duck_after_normal_duck_does_fade(self):
        """Sanity: if the first duck DID fade, the second duck should still fade."""
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        ducker.duck(0.25)
        backend.fade_calls.clear()

        ducker.duck(0.15)
        assert (0.15, 150) in backend.fade_calls, "Second duck() after a normal duck should fade to the new level"


# ═══════════════════════════════════════════════════════════════════════════
# Smart-duck config toggle
# ═══════════════════════════════════════════════════════════════════════════


class TestSmartDuckToggle:
    """The volume_duck_smart config field gates smart-duck behaviour."""

    def test_smart_duck_enabled_by_default(self):
        ducker = VolumeDucker(backend=FakeBackend())
        assert ducker.smart_duck_enabled is True

    def test_set_smart_duck_disabled(self):
        """When smart-duck is disabled, duck() should NOT query is_speaker_active."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ducker.set_smart_duck_enabled(False)

        ducker.duck(0.25)
        assert backend.is_speaker_active_calls == 0, "Smart-duck disabled — is_speaker_active() should not be called"
        assert backend.fade_calls, "Smart-duck disabled — duck() should fade normally"
        assert ducker.actually_ducked is True

    def test_set_smart_duck_enabled_at_runtime(self):
        """Toggling smart-duck at runtime takes effect on the next duck()."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        # First duck — smart-duck enabled (default), skips
        ducker.duck(0.25)
        assert backend.fade_calls == []
        ducker.restore()

        # Disable smart-duck
        ducker.set_smart_duck_enabled(False)

        # Second duck — smart-duck disabled, fades normally
        ducker.duck(0.25)
        assert (0.25, 150) in backend.fade_calls


# ═══════════════════════════════════════════════════════════════════════════
# Cross-platform is_speaker_active() — Linux
# ═══════════════════════════════════════════════════════════════════════════


class TestLinuxIsSpeakerActive:
    """LinuxVolumeBackend.is_speaker_active() — pactl / wpctl / amixer."""

    def test_pactl_running_sink_input_returns_true(self, monkeypatch):
        """pactl list sink-inputs with 'State: running' → audio is playing."""
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        b._run = lambda cmd, timeout=2.0: (
            "Sink Input #42\n\tState: running\n\tSink: alsa_output.pci-0000_00_1b.0.analog-stereo\n"
            if "list" in cmd
            else "ok"
        )
        assert b.is_speaker_active() is True

    def test_pactl_no_sink_inputs_returns_false(self, monkeypatch):
        """pactl list sink-inputs with empty output → no audio playing."""
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        b._run = lambda cmd, timeout=2.0: "" if "list" in cmd else "ok"
        assert b.is_speaker_active() is False

    def test_pactl_corked_sink_input_returns_false(self, monkeypatch):
        """A corked (paused) sink-input is not actively playing."""
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        b._run = lambda cmd, timeout=2.0: "Sink Input #42\n\tState: corked\n" if "list" in cmd else "ok"
        assert b.is_speaker_active() is False

    def test_pactl_idle_sink_input_returns_false(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        b._run = lambda cmd, timeout=2.0: "Sink Input #42\n\tState: idle\n" if "list" in cmd else "ok"
        assert b.is_speaker_active() is False

    def test_wpctl_falls_through_to_alsa_procfs(self, monkeypatch):
        """wpctl-only system (no pactl) → ALSA /proc/asound fallback."""
        b = LinuxVolumeBackend()
        b._tool = "wpctl"
        # pactl list sink-inputs fails (None) — wpctl-only system.
        # Then _alsa_is_playing is called.
        b._run = lambda cmd, timeout=2.0: None if cmd[0] == "pactl" else "ok"
        # Mock _alsa_is_playing to return True
        b._alsa_is_playing = lambda: True
        assert b.is_speaker_active() is True

    def test_wpctl_falls_through_to_alsa_procfs_false(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "wpctl"
        b._run = lambda cmd, timeout=2.0: None if cmd[0] == "pactl" else "ok"
        b._alsa_is_playing = lambda: False
        assert b.is_speaker_active() is False

    def test_amixer_uses_alsa_procfs(self, monkeypatch):
        """amixer-only system → ALSA /proc/asound directly."""
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        b._alsa_is_playing = lambda: True
        assert b.is_speaker_active() is True


class TestLinuxAlsaProcfs:
    """LinuxVolumeBackend._alsa_is_playing() — /proc/asound parsing."""

    def test_running_substream_returns_true(self, tmp_path, monkeypatch):
        """Simulate /proc/asound/card0/pcm0p/sub0/status with 'state: RUNNING'."""
        b = LinuxVolumeBackend()
        # Build a fake /proc/asound structure under tmp_path
        card = tmp_path / "card0"
        pcm = card / "pcm0p"
        sub = pcm / "sub0"
        sub.mkdir(parents=True)
        (sub / "status").write_text("state: RUNNING\n")
        # Patch Path("/proc/asound") to point at tmp_path
        import voice_typer.server.volume_backends as vb_mod

        original_path = vb_mod.Path

        def fake_path(p):
            if str(p) == "/proc/asound":
                return original_path(str(tmp_path))
            return original_path(str(p))

        monkeypatch.setattr(vb_mod, "Path", fake_path)
        assert b._alsa_is_playing() is True

    def test_no_running_substream_returns_false(self, tmp_path, monkeypatch):
        """All substreams idle → no audio playing."""
        b = LinuxVolumeBackend()
        card = tmp_path / "card0"
        pcm = card / "pcm0p"
        sub = pcm / "sub0"
        sub.mkdir(parents=True)
        (sub / "status").write_text("state: IDLE\n")
        import voice_typer.server.volume_backends as vb_mod

        original_path = vb_mod.Path  # noqa: F841 — used in fake_path closure

        def fake_path(p):
            if str(p) == "/proc/asound":
                return original_path(str(tmp_path))
            return original_path(str(p))

        monkeypatch.setattr(vb_mod, "Path", fake_path)
        assert b._alsa_is_playing() is False

    def test_no_proc_asound_returns_true(self, monkeypatch):
        """If /proc/asound doesn't exist (non-Linux), return True (duck anyway)."""
        b = LinuxVolumeBackend()
        import voice_typer.server.volume_backends as vb_mod

        original_path = vb_mod.Path  # noqa: F841 — used in fake_path closure

        class FakePath:
            def __init__(self, p):
                self._p = str(p)

            def exists(self):
                return False  # /proc/asound doesn't exist

        def fake_path(p):
            return FakePath(p)

        monkeypatch.setattr(vb_mod, "Path", fake_path)
        assert b._alsa_is_playing() is True


# ═══════════════════════════════════════════════════════════════════════════
# Cross-platform is_speaker_active() — macOS
# ═══════════════════════════════════════════════════════════════════════════


class TestMacIsSpeakerActive:
    """MacVolumeBackend.is_speaker_active() — osascript fallback.

    The CoreAudio pyobjc path can't be tested without macOS hardware
    (it defers to osascript per the existing _get_default_output_device
    comment).  We test the osascript fallback path.
    """

    def test_osascript_with_spotify_running_returns_true(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._osascript_run = lambda script, timeout=2.0: "Spotify, Safari, Finder"
        assert b.is_speaker_active() is True

    def test_osascript_with_only_text_editor_returns_false(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._osascript_run = lambda script, timeout=2.0: "TextEdit, Finder, Mail"
        assert b.is_speaker_active() is False

    def test_osascript_returns_none_ducks_anyway(self, monkeypatch):
        """If osascript fails (returns None), default to True (duck anyway)."""
        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._osascript_run = lambda script, timeout=2.0: None
        assert b.is_speaker_active() is True

    def test_osascript_with_chrome_returns_true(self, monkeypatch):
        """Chrome is in the audio-app list (YouTube, etc.)."""
        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._osascript_run = lambda script, timeout=2.0: "Google Chrome, Slack"
        assert b.is_speaker_active() is True

    def test_osascript_with_zoom_returns_true(self, monkeypatch):
        """Zoom is in the audio-app list (meetings produce audio)."""
        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._osascript_run = lambda script, timeout=2.0: "zoom.us, Finder"
        assert b.is_speaker_active() is True

    def test_coreaudio_path_falls_through_to_osascript(self, monkeypatch):
        """When CoreAudio is enabled but the pyobjc path raises, fall through to osascript."""
        b = MacVolumeBackend()
        b._use_coreaudio = True
        # _get_default_output_device raises (pyobjc not fully working)
        b._get_default_output_device = lambda: None
        b._osascript_run = lambda script, timeout=2.0: "Spotify"
        assert b.is_speaker_active() is True


# ═══════════════════════════════════════════════════════════════════════════
# Introspection
# ═══════════════════════════════════════════════════════════════════════════


class TestSmartDuckIntrospection:
    def test_actually_ducked_property(self):
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        assert ducker.actually_ducked is False  # not ducked yet

        ducker.duck(0.25)
        assert ducker.actually_ducked is False  # smart-duck skipped

        ducker.restore()
        assert ducker.actually_ducked is False  # restored

    def test_actually_ducked_true_after_normal_duck(self):
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ducker.duck(0.25)
        assert ducker.actually_ducked is True

    def test_smart_duck_enabled_property(self):
        ducker = VolumeDucker(backend=FakeBackend())
        assert ducker.smart_duck_enabled is True
        ducker.set_smart_duck_enabled(False)
        assert ducker.smart_duck_enabled is False
        ducker.set_smart_duck_enabled(True)
        assert ducker.smart_duck_enabled is True


# ═══════════════════════════════════════════════════════════════════════════
# Concurrency — smart-duck + restore race
# ═══════════════════════════════════════════════════════════════════════════


class TestSmartDuckConcurrency:
    def test_concurrent_duck_and_restore_with_smart_skip(self):
        """If duck() (smart-skip) and restore() fire concurrently, no errors."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        errors: list[Exception] = []

        def duck_call():
            try:
                ducker.duck(0.25)
            except Exception as e:
                errors.append(e)

        def restore_call():
            try:
                ducker.restore()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=duck_call)
        t2 = threading.Thread(target=restore_call)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert not errors
