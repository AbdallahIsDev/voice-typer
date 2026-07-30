"""Cross-platform volume backend tests.

Tests the Linux and macOS backends' parsing logic without requiring
the real platform tools (pactl / wpctl / amixer / osascript) to be
installed.  All ``subprocess.run`` calls are mocked.

Coverage
--------
- LinuxVolumeBackend:
  * Tool detection priority: pactl → wpctl → amixer.
  * pactl output parsing ("Volume: ... 100% ..." + "Mute: yes/no").
  * wpctl output parsing ("Volume: 0.50" + "[MUTED]").
  * amixer output parsing ("Playback 50% [50%] [on]"/"[off]").
  * Linear→percent conversion on set.
  * Graceful failure when no tool is available.
- MacVolumeBackend:
  * osascript get_state / set_linear (CoreAudio path can't be tested
    without macOS, but osascript fallback can be exercised via mocks).
  * Volume clamping.
- WinVolumeBackend:
  * Constructor doesn't crash on non-Windows (resources deferred to
    initialize()).
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from voice_typer.server.volume_backend_base import VolumeState
from voice_typer.server.volume_backends import (
    LinuxVolumeBackend,
    MacVolumeBackend,
    WinVolumeBackend,
)

# ═══════════════════════════════════════════════════════════════════════════
# Linux backend
# ═══════════════════════════════════════════════════════════════════════════


class TestLinuxBackendToolDetection:
    """§5.3: detection priority pactl → wpctl → amixer."""

    def test_pactl_preferred_over_wpctl_and_amixer(self, monkeypatch):
        # All three tools present — pactl wins.
        monkeypatch.setattr("shutil.which", lambda t: f"/usr/bin/{t}")
        b = LinuxVolumeBackend()
        assert b.initialize() is True
        assert b._tool == "pactl"
        assert "pactl" in b.name

    def test_wpctl_used_when_pactl_missing(self, monkeypatch):
        def which(tool):
            return f"/usr/bin/{tool}" if tool in ("wpctl", "amixer") else None

        monkeypatch.setattr("shutil.which", which)
        b = LinuxVolumeBackend()
        assert b.initialize() is True
        assert b._tool == "wpctl"

    def test_amixer_used_when_pactl_and_wpctl_missing(self, monkeypatch):
        def which(tool):
            return f"/usr/bin/{tool}" if tool == "amixer" else None

        monkeypatch.setattr("shutil.which", which)
        b = LinuxVolumeBackend()
        assert b.initialize() is True
        assert b._tool == "amixer"

    def test_no_tool_means_initialize_fails(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda t: None)
        b = LinuxVolumeBackend()
        assert b.initialize() is False

    def test_does_not_support_per_session(self):
        b = LinuxVolumeBackend()
        assert b.supports_per_session is False

    def test_initialize_is_idempotent(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda t: f"/usr/bin/{t}")
        b = LinuxVolumeBackend()
        b.initialize()
        tool = b._tool
        # Second initialize() shouldn't re-detect.
        # We remove the mock to prove it.
        monkeypatch.setattr("shutil.which", lambda t: None)
        b.initialize()
        assert b._tool == tool


class TestLinuxBackendPactl:
    """§5.3: pactl parsing."""

    def test_get_state_parses_volume_and_mute(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"

        # Mock _run to return canned pactl output.
        def fake_run(cmd, timeout=2.0):
            if "get-sink-volume" in cmd:
                return "Volume: front-left: 65536 / 100% / 0.00 dB, front-right: 65536 / 100% / 0.00 dB"
            if "get-sink-mute" in cmd:
                return "Mute: no"
            return None

        b._run = fake_run

        state = b.get_state()
        assert state is not None
        assert state.linear == 1.0  # 100%
        assert state.muted is False

    def test_get_state_detects_muted(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"

        def fake_run(cmd, timeout=2.0):
            if "get-sink-volume" in cmd:
                return "Volume: front-left: 16384 / 25% / -36.00 dB"
            if "get-sink-mute" in cmd:
                return "Mute: yes"
            return None

        b._run = fake_run

        state = b.get_state()
        assert state is not None
        assert state.linear == 0.25
        assert state.muted is True

    def test_set_linear_uses_percent(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        calls = []

        def fake_run(cmd, timeout=2.0):
            calls.append(cmd)
            return "ok"

        b._run = fake_run

        # 0.5 → 50%
        b.set_linear(0.5)
        assert any("50%" in " ".join(c) for c in calls), f"set_linear(0.5) should set 50%; calls={calls}"

    def test_set_linear_clamps_above_max(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        calls = []
        b._run = lambda cmd, timeout=2.0: calls.append(cmd) or "ok"
        b.set_linear(1.5)  # above max
        assert any("100%" in " ".join(c) for c in calls)

    def test_set_linear_clamps_below_min(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        calls = []
        b._run = lambda cmd, timeout=2.0: calls.append(cmd) or "ok"
        b.set_linear(-0.5)
        assert any("0%" in " ".join(c) for c in calls)

    def test_set_linear_sets_mute(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        calls = []
        b._run = lambda cmd, timeout=2.0: calls.append(cmd) or "ok"
        b.set_linear(0.5, muted=True)
        # Should have called set-sink-mute with "1"
        assert any("set-sink-mute" in c and "1" in c for c in calls), (
            f"set_linear(0.5, muted=True) should call set-sink-mute 1; calls={calls}"
        )


class TestLinuxBackendWpctl:
    """§5.3: wpctl parsing (PipeWire native)."""

    def test_get_state_parses_volume(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "wpctl"
        b._run = lambda cmd, timeout=2.0: "Volume: 0.50"
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.5
        assert state.muted is False

    def test_get_state_detects_muted(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "wpctl"
        b._run = lambda cmd, timeout=2.0: "Volume: 0.50 [MUTED]"
        state = b.get_state()
        assert state is not None
        assert state.muted is True

    def test_set_linear_uses_float(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "wpctl"
        calls = []
        b._run = lambda cmd, timeout=2.0: calls.append(cmd) or "ok"
        b.set_linear(0.3)
        # wpctl set-volume takes a float in [0, 1]
        assert any("0.30" in " ".join(c) for c in calls), f"set_linear(0.3) should pass 0.30; calls={calls}"


class TestLinuxBackendAmixer:
    """§5.3: amixer (ALSA fallback) parsing."""

    def test_get_state_parses_percent_and_on(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        b._run = lambda cmd, timeout=2.0: (
            "  Simple mixer control 'Master',0\n    Mono: Playback 50% [50%] [-6.00dB] [on]"
        )
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.5
        assert state.muted is False

    def test_get_state_detects_off(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        b._run = lambda cmd, timeout=2.0: "  Mono: Playback 0% [0%] [-100.00dB] [off]"
        state = b.get_state()
        assert state is not None
        assert state.muted is True

    def test_set_linear_uses_percent(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        calls = []
        b._run = lambda cmd, timeout=2.0: calls.append(cmd) or "ok"
        b.set_linear(0.75)
        assert any("75%" in " ".join(c) for c in calls), f"set_linear(0.75) should pass 75%; calls={calls}"


# ═══════════════════════════════════════════════════════════════════════════
# macOS backend
# ═══════════════════════════════════════════════════════════════════════════


class TestMacBackendOsascript:
    """§5.2: osascript fallback path (CoreAudio can't be tested without
    a real macOS install)."""

    def test_supports_per_session_is_false(self):
        b = MacVolumeBackend()
        assert b.supports_per_session is False

    def test_initialize_returns_true_without_coreaudio(self):
        """Without pyobjc installed (the test environment), initialize()
        falls back to osascript and returns True."""
        b = MacVolumeBackend()
        # Patch the CoreAudio import to fail so osascript is used.
        with patch.dict(sys.modules, {"CoreAudio": None}):
            ok = b.initialize()
        assert ok is True
        assert b._use_coreaudio is False
        assert b.name == "osascript"

    def test_get_state_parses_volume_and_mute(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        # First call returns "65", second returns "false"
        responses = iter(["65", "false"])
        b._osascript_run = lambda script, timeout=2.0: next(responses)
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.65
        assert state.muted is False

    def test_get_state_detects_muted(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        responses = iter(["30", "true"])
        b._osascript_run = lambda script, timeout=2.0: next(responses)
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.3
        assert state.muted is True

    def test_set_linear_uses_percent(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        calls = []

        def fake_run(script, timeout=2.0):
            calls.append(script)
            return "ok"

        b._osascript_run = fake_run
        b.set_linear(0.45)
        # Should have called set volume output volume 45
        assert any("45" in s for s in calls), f"set_linear(0.45) should pass 45%; calls={calls}"

    def test_set_linear_clamps(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        calls = []
        b._osascript_run = lambda script, timeout=2.0: calls.append(script) or "ok"
        b.set_linear(1.5)
        # 1.5 should clamp to 1.0 → "100"
        assert any("100" in s for s in calls)
        calls.clear()
        b.set_linear(-0.5)
        assert any("0" in s for s in calls)

    def test_set_linear_sets_mute(self, monkeypatch):
        b = MacVolumeBackend()
        b._use_coreaudio = False
        calls = []
        b._osascript_run = lambda script, timeout=2.0: calls.append(script) or "ok"
        b.set_linear(0.5, muted=True)
        assert any("output muted true" in s for s in calls), (
            f"set_linear(0.5, muted=True) should set muted=true; calls={calls}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Windows backend (smoke tests only — full pycaw tests need Windows)
# ═══════════════════════════════════════════════════════════════════════════


class TestWinBackendSmoke:
    """§5.1: Windows backend — only tests that don't need pycaw/COM."""

    def test_supports_per_session_is_true(self):
        b = WinVolumeBackend()
        assert b.supports_per_session is True

    def test_name_is_pycaw_wasapi(self):
        b = WinVolumeBackend()
        assert b.name == "pycaw (WASAPI)"

    def test_initialize_returns_false_without_pycaw(self):
        """On non-Windows (or without pycaw installed), initialize() should
        return False gracefully rather than crashing."""
        b = WinVolumeBackend()
        # pycaw isn't installed in the test environment
        ok = b.initialize()
        assert ok is False

    def test_get_state_returns_none_when_uninitialized(self):
        b = WinVolumeBackend()
        assert b.get_state() is None

    def test_set_linear_returns_false_when_uninitialized(self):
        b = WinVolumeBackend()
        assert b.set_linear(0.5) is False


# ═══════════════════════════════════════════════════════════════════════════
# VolumeBackend base class default fade_to behaviour
# ═══════════════════════════════════════════════════════════════════════════


class TestVolumeBackendFadeTo:
    """§4.1: VolumeBackend.fade_to() default implementation uses N
    discrete set_linear calls with equal-sized sleeps."""

    def test_fade_to_uses_set_linear(self):
        # Use LinuxVolumeBackend to exercise the default fade_to
        # (it doesn't override).
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        set_calls = []
        b._run = lambda cmd, timeout=2.0: "ok"
        original_set = b.set_linear

        def spy_set(level, muted=None):
            set_calls.append(level)
            return original_set(level, muted)

        b.set_linear = spy_set

        # Fade from current (we need get_state to return a starting point).
        # The default fade_to calls get_state first.
        b.get_state = lambda: VolumeState(linear=0.2, muted=False)
        # Patch time.sleep so the test doesn't take 150ms.
        with patch("voice_typer.server.volume_backend_base.time.sleep"):
            ok = b.fade_to(0.8, duration_ms=150, steps=10)

        assert ok is True
        # Should have at least one set_linear call per step
        assert len(set_calls) >= 5
        # Final value should be 0.8
        assert set_calls[-1] == 0.8

    def test_fade_to_zero_duration_is_single_set(self):
        b = LinuxVolumeBackend()
        b._tool = "pactl"
        b._run = lambda cmd, timeout=2.0: "ok"
        b.get_state = lambda: VolumeState(linear=0.2, muted=False)
        set_calls = []
        original_set = b.set_linear

        def spy_set(level, muted=None):
            set_calls.append(level)
            return original_set(level, muted)

        b.set_linear = spy_set

        ok = b.fade_to(0.5, duration_ms=0)
        assert ok is True
        # With duration_ms <= 0, the default impl does a single set_linear
        assert len(set_calls) == 1
        assert set_calls[0] == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# UE-25: per-backend consecutive-error counter (observability)
# ═══════════════════════════════════════════════════════════════════════════
#
# Backends swallow errors and return safe defaults (True for
# is_speaker_active, None for get_state) so duck-state is never
# corrupted by a transient backend hiccup.  But a stuck/revoked COM
# pointer (Windows), a missing CLI tool (Linux), or revoked AppleScript
# permission (macOS 13+) would degrade ducking to a silent no-op with
# no log breadcrumb.  UE-25 adds a per-backend consecutive-error
# counter that surfaces a WARNING after N consecutive failures.
#
# The safe-default return values are preserved -- the counter is purely
# additive observability.


class TestUE25WinBackendErrorCounter:
    """UE-25: WinVolumeBackend.get_state / is_speaker_active error counter."""

    def test_get_state_warns_after_threshold(self, caplog):
        """3 consecutive get_state failures fire a WARNING (safe-default None preserved)."""
        import logging
        from unittest.mock import MagicMock

        from voice_typer.server.volume_backends import WinVolumeBackend

        b = WinVolumeBackend()
        # Simulate an initialised backend with a broken _vol COM pointer.
        b._vol = MagicMock()
        b._vol.GetMasterVolumeLevelScalar.side_effect = RuntimeError("COM pointer revoked")
        b._vol.GetMute = MagicMock(return_value=0)
        b._com_initialized = True
        b._consecutive_errors = 0

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert b.get_state() is None  # safe default preserved

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "get_state" in r.message and "failed 3 times" in r.message
        ]
        assert len(warnings) >= 1, "Expected a WARNING after 3 consecutive get_state failures"

    def test_get_state_counter_resets_on_success(self, caplog):
        """A single success resets the consecutive-error counter."""
        import logging
        from unittest.mock import MagicMock

        from voice_typer.server.volume_backends import WinVolumeBackend

        b = WinVolumeBackend()
        b._vol = MagicMock()
        b._vol.GetMute = MagicMock(return_value=0)
        b._com_initialized = True
        b._consecutive_errors = 0

        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("transient")
            return 0.5  # success on 3rd call

        b._vol.GetMasterVolumeLevelScalar.side_effect = flaky

        with caplog.at_level(logging.WARNING):
            assert b.get_state() is None  # fail 1
            assert b.get_state() is None  # fail 2
            state = b.get_state()  # success
            assert state is not None

        assert b._consecutive_errors == 0, "Counter must reset on success"
        # No WARNING should have fired (only 2 consecutive failures).
        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "get_state" in r.message and "failed" in r.message
        ]
        assert len(warnings) == 0, f"No WARNING should fire below threshold; got {warnings}"

    def test_is_speaker_active_warns_after_threshold(self, caplog):
        """3 consecutive is_speaker_active failures fire a WARNING (safe-default True preserved)."""
        import logging
        from unittest.mock import MagicMock

        from voice_typer.server.volume_backends import WinVolumeBackend

        b = WinVolumeBackend()
        b._meter = MagicMock()
        b._meter.GetPeakValue.side_effect = RuntimeError("meter revoked")
        b._com_initialized = True
        b._consecutive_errors = 0

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert b.is_speaker_active() is True  # safe default preserved

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "is_speaker_active" in r.message and "failed 3 times" in r.message
        ]
        assert len(warnings) >= 1, "Expected a WARNING after 3 consecutive is_speaker_active failures"

    def test_is_speaker_active_success_resets_counter(self, caplog):
        """A successful is_speaker_active call resets the counter."""
        from unittest.mock import MagicMock

        from voice_typer.server.volume_backends import WinVolumeBackend

        b = WinVolumeBackend()
        b._meter = MagicMock()
        b._com_initialized = True
        b._consecutive_errors = 0

        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("transient")
            return 0.05  # success on 3rd call (>= 0.01 threshold)

        b._meter.GetPeakValue.side_effect = flaky

        for _ in range(2):
            assert b.is_speaker_active() is True  # safe default
        assert b.is_speaker_active() is True  # success (peak >= 0.01)
        assert b._consecutive_errors == 0, "Counter must reset on success"


class TestUE25LinuxBackendErrorCounter:
    """UE-25: LinuxVolumeBackend._alsa_is_playing error counter."""

    def test_alsa_is_playing_warns_after_threshold(self, caplog, monkeypatch):
        """3 consecutive _alsa_is_playing failures fire a WARNING (safe-default True preserved)."""
        import logging

        from voice_typer.server.volume_backends import LinuxVolumeBackend

        b = LinuxVolumeBackend()
        b._tool = "amixer"  # forces the _alsa_is_playing path
        b._consecutive_errors = 0

        # Patch volume_backends.Path to raise on construction -- simulates
        # a broken /proc/asound (e.g. permission denied on all reads).
        import voice_typer.server.volume_backends as vb_mod

        class BrokenPath:
            def __init__(self, p):
                raise OSError("permission denied")

        monkeypatch.setattr(vb_mod, "Path", BrokenPath)

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert b._alsa_is_playing() is True  # safe default preserved

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "_alsa_is_playing" in r.message and "failed 3 times" in r.message
        ]
        assert len(warnings) >= 1, "Expected a WARNING after 3 consecutive _alsa_is_playing failures"

    def test_alsa_is_playing_success_resets_counter(self, tmp_path, monkeypatch):
        """A successful _alsa_is_playing call resets the counter."""
        from voice_typer.server.volume_backends import LinuxVolumeBackend

        b = LinuxVolumeBackend()
        b._tool = "amixer"
        b._consecutive_errors = 5  # pretend we had prior failures

        # Build a fake /proc/asound with no running substreams (returns False = success).
        import voice_typer.server.volume_backends as vb_mod

        original_path = vb_mod.Path

        def fake_path(p):
            if str(p) == "/proc/asound":
                return original_path(str(tmp_path))
            return original_path(str(p))

        monkeypatch.setattr(vb_mod, "Path", fake_path)

        result = b._alsa_is_playing()
        assert result is False  # no running substreams
        assert b._consecutive_errors == 0, "Counter must reset on success"


class TestUE25MacBackendErrorCounter:
    """UE-25: MacVolumeBackend._osascript_run / _osascript_get_state error counter."""

    def test_osascript_run_warns_after_threshold(self, caplog, monkeypatch):
        """3 consecutive _osascript_run failures fire a WARNING (safe-default None preserved)."""
        import logging

        from voice_typer.server.volume_backends import MacVolumeBackend

        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._consecutive_errors = 0

        # Patch subprocess.run to raise -- simulates osascript missing.
        import voice_typer.server.volume_backends.macos as mac_mod

        def broken_run(*args, **kwargs):
            raise FileNotFoundError("osascript not found")

        monkeypatch.setattr(mac_mod.subprocess, "run", broken_run)

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert b._osascript_run("get volume settings") is None  # safe default

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "_osascript_run" in r.message and "failed 3 times" in r.message
        ]
        assert len(warnings) >= 1, "Expected a WARNING after 3 consecutive _osascript_run failures"

    def test_osascript_run_nonzero_exit_increments_counter(self, caplog, monkeypatch):
        """A non-zero osascript exit (e.g. revoked permission) increments the counter."""
        import logging

        from voice_typer.server.volume_backends import MacVolumeBackend

        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._consecutive_errors = 0

        import voice_typer.server.volume_backends.macos as mac_mod

        class FakeResult:
            returncode = 1
            stderr = "execution error: Not authorised to send Apple events. (-1743)"
            stdout = ""

        monkeypatch.setattr(mac_mod.subprocess, "run", lambda *a, **k: FakeResult())

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert b._osascript_run("get volume settings") is None

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "_osascript_run" in r.message and "failed 3 times" in r.message
        ]
        assert len(warnings) >= 1, "Expected a WARNING after 3 non-zero osascript exits"

    def test_osascript_run_success_resets_counter(self, monkeypatch):
        """A successful _osascript_run call resets the counter."""
        from voice_typer.server.volume_backends import MacVolumeBackend

        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._consecutive_errors = 5  # pretend we had prior failures

        import voice_typer.server.volume_backends.macos as mac_mod

        class FakeResult:
            returncode = 0
            stderr = ""
            stdout = "65"

        monkeypatch.setattr(mac_mod.subprocess, "run", lambda *a, **k: FakeResult())

        result = b._osascript_run("get volume settings")
        assert result == "65"
        assert b._consecutive_errors == 0, "Counter must reset on success"

    def test_osascript_get_state_parse_error_increments_counter(self, caplog):
        """An unparseable volume string from _osascript_get_state increments the counter."""
        import logging

        from voice_typer.server.volume_backends import MacVolumeBackend

        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._consecutive_errors = 0

        # _osascript_run returns a non-numeric string -> ValueError in _osascript_get_state.
        b._osascript_run = lambda script, timeout=2.0: "not a number"

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert b._osascript_get_state() is None  # safe default

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "_osascript_get_state" in r.message and "failed 3 times" in r.message
        ]
        assert len(warnings) >= 1, "Expected a WARNING after 3 consecutive _osascript_get_state parse failures"

    def test_osascript_get_state_no_double_count(self, monkeypatch):
        """When _osascript_run fails, _osascript_get_state must NOT double-count.

        _osascript_run already increments the counter on subprocess failure;
        _osascript_get_state returns None without incrementing again.
        """
        from voice_typer.server.volume_backends import MacVolumeBackend

        b = MacVolumeBackend()
        b._use_coreaudio = False
        b._consecutive_errors = 0

        # _osascript_run fails (returns None) -- the REAL _osascript_run
        # records the error via _record_error before returning None.
        # Simulate that real behaviour so the counter reflects one failure.
        def fake_run(script, timeout=2.0):
            b._record_error("_osascript_run", RuntimeError("subprocess failed"))
            return None

        b._osascript_run = fake_run

        result = b._osascript_get_state()
        assert result is None
        assert b._consecutive_errors == 1, (
            f"_osascript_get_state must NOT double-count when _osascript_run fails; got counter={b._consecutive_errors}"
        )


class TestUE25CrossPlatformImportSafety:
    """UE-25: all backends import cleanly on any OS (no pycaw/pyobjc required)."""

    def test_windows_backend_imports_without_pycaw(self):
        from voice_typer.server.volume_backends import WinVolumeBackend

        b = WinVolumeBackend()
        assert b._consecutive_errors == 0
        assert b.name == "pycaw (WASAPI)"

    def test_linux_backend_imports_without_pactl(self):
        from voice_typer.server.volume_backends import LinuxVolumeBackend

        b = LinuxVolumeBackend()
        assert b._consecutive_errors == 0

    def test_macos_backend_imports_without_pyobjc(self):
        from voice_typer.server.volume_backends import MacVolumeBackend

        b = MacVolumeBackend()
        assert b._consecutive_errors == 0
