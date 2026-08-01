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
# per-backend consecutive-error counter (observability)
# ═══════════════════════════════════════════════════════════════════════════
#
# Backends swallow errors and return safe defaults (True for
# is_speaker_active, None for get_state) so duck-state is never
# corrupted by a transient backend hiccup.  But a stuck/revoked COM
# pointer (Windows), a missing CLI tool (Linux), or revoked AppleScript
# permission (macOS 13+) would degrade ducking to a silent no-op with
# no log breadcrumb.   adds a per-backend consecutive-error
# counter that surfaces a WARNING after N consecutive failures.
#
# The safe-default return values are preserved -- the counter is purely
# additive observability.


class TestWinBackendErrorCounter:
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


class TestLinuxBackendErrorCounter:
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


class TestMacBackendErrorCounter:
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


class TestCrossPlatformImportSafety:
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


# ═══════════════════════════════════════════════════════════════════════════
# XS-80: WinVolumeBackend ducking logic — behavioral coverage with mocked pycaw.
#
# The existing TestWinBackendSmoke only verifies the 'pycaw not installed →
# graceful failure' path. These tests mock pycaw.pycaw.AudioUtilities,
# IAudioEndpointVolume, and IAudioMeterInformation so initialize() succeeds
# and the ducking logic (is_speaker_active peak threshold, get_other_sessions
# PROC-FILTER-FIX regex, duck/restore round-trip) is exercised on Linux.
# ═══════════════════════════════════════════════════════════════════════════


class TestWinBackendPycaw:
    """XS-80: WinVolumeBackend ducking logic with mocked pycaw."""

    @staticmethod
    def _install_fake_pycaw(
        monkeypatch,
        *,
        peak_value=0.0,
        scalar=0.5,
        mute=0,
        sessions=None,
    ):
        """Inject a fake ``pycaw.pycaw`` + ``comtypes`` module into sys.modules.

        Returns ``(vol_ptr, meter_ptr, audio_utilities)`` for assertions.
        """
        from unittest.mock import MagicMock

        # Fake IAudioEndpointVolume / IAudioMeterInformation — the source
        # uses them only as type markers (accesses ``_iid_`` on the legacy
        # Activate path; we exercise the modern EndpointVolume path).
        class FakeIAudioEndpointVolume:
            _iid_ = "fake-iid"

        class FakeIAudioMeterInformation:
            pass

        # Fake vol_ptr (the IAudioEndpointVolume COM pointer).
        vol_ptr = MagicMock()
        vol_ptr.GetMasterVolumeLevelScalar.return_value = scalar
        vol_ptr.GetMute.return_value = mute
        vol_ptr.SetMasterVolumeLevelScalar.return_value = None
        vol_ptr.SetMute.return_value = None

        # Fake meter_ptr (IAudioMeterInformation COM pointer).
        meter_ptr = MagicMock()
        meter_ptr.GetPeakValue.return_value = peak_value
        vol_ptr.QueryInterface.return_value = meter_ptr

        # Fake speakers device — pycaw >= 20251023 path: EndpointVolume
        # property returns the vol_ptr directly.
        speakers = MagicMock()
        speakers.EndpointVolume = vol_ptr

        # Fake AudioUtilities.
        audio_utilities = MagicMock()
        audio_utilities.GetSpeakers.return_value = speakers
        audio_utilities.GetAllSessions.return_value = sessions if sessions is not None else []

        # Build the fake pycaw.pycaw module.
        fake_pycaw_mod = MagicMock()
        fake_pycaw_mod.AudioUtilities = audio_utilities
        fake_pycaw_mod.IAudioEndpointVolume = FakeIAudioEndpointVolume
        fake_pycaw_mod.IAudioMeterInformation = FakeIAudioMeterInformation

        # Build the fake comtypes module (only CLSCTX_ALL is needed).
        fake_comtypes = MagicMock()
        fake_comtypes.CLSCTX_ALL = 23

        # Inject into sys.modules so the in-function imports resolve.
        import sys

        monkeypatch.setitem(sys.modules, "pycaw", MagicMock())
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_mod)
        monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)

        return vol_ptr, meter_ptr, audio_utilities

    @staticmethod
    def _make_session(pid, name):
        """Build a fake pycaw AudioSession whose Process has pid + name()."""
        from unittest.mock import MagicMock

        proc = MagicMock()
        proc.pid = pid
        proc.name.return_value = name
        session = MagicMock()
        session.Process = proc
        return session

    def test_initialize_succeeds_with_pycaw(self, monkeypatch):
        """initialize() happy path: pycaw imports cleanly, _vol + _meter bound."""
        vol_ptr, meter_ptr, _ = self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        assert b.initialize() is True
        assert b._vol is vol_ptr
        assert b._meter is meter_ptr
        assert b._com_initialized is True

    def test_initialize_is_idempotent(self, monkeypatch):
        """Second initialize() returns True without redoing setup."""
        self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        assert b.initialize() is True
        sentinel = object()
        b._vol = sentinel
        assert b.initialize() is True
        assert b._vol is sentinel

    def test_initialize_returns_false_when_no_speakers(self, monkeypatch):
        """If GetSpeakers() returns None, initialize() returns False."""
        import sys
        from unittest.mock import MagicMock

        fake_pycaw_mod = MagicMock()
        fake_pycaw_mod.AudioUtilities.GetSpeakers.return_value = None
        fake_pycaw_mod.IAudioEndpointVolume = type("X", (), {"_iid_": "x"})
        fake_pycaw_mod.IAudioMeterInformation = type("Y", (), {})
        monkeypatch.setitem(sys.modules, "pycaw", MagicMock())
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_mod)
        monkeypatch.setitem(sys.modules, "comtypes", MagicMock())
        b = WinVolumeBackend()
        assert b.initialize() is False

    def test_get_state_reads_scalar_and_mute(self, monkeypatch):
        """get_state returns VolumeState(linear, muted) from pycaw calls."""
        self._install_fake_pycaw(monkeypatch, scalar=0.6, mute=1)
        b = WinVolumeBackend()
        b.initialize()
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.6
        assert state.muted is True

    def test_get_state_clamps_above_1(self, monkeypatch):
        """Driver-bug defense: scalar > 1.0 clamps to 1.0."""
        self._install_fake_pycaw(monkeypatch, scalar=1.5)
        b = WinVolumeBackend()
        b.initialize()
        state = b.get_state()
        assert state is not None
        assert state.linear == 1.0

    def test_set_linear_calls_setmastervolumelevelscalar(self, monkeypatch):
        """set_linear invokes SetMasterVolumeLevelScalar with clamped level."""
        vol_ptr, _, _ = self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        b.initialize()
        assert b.set_linear(0.4) is True
        vol_ptr.SetMasterVolumeLevelScalar.assert_called_once_with(0.4, None)

    def test_set_linear_clamps(self, monkeypatch):
        """set_linear clamps to [0.0, 1.0] before calling pycaw."""
        vol_ptr, _, _ = self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        b.initialize()
        b.set_linear(1.5)
        assert vol_ptr.SetMasterVolumeLevelScalar.call_args[0][0] == 1.0
        b.set_linear(-0.3)
        assert vol_ptr.SetMasterVolumeLevelScalar.call_args[0][0] == 0.0

    def test_set_linear_sets_mute(self, monkeypatch):
        """When muted kwarg is passed, SetMute is invoked."""
        vol_ptr, _, _ = self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        b.initialize()
        b.set_linear(0.5, muted=True)
        vol_ptr.SetMute.assert_called_once_with(1, None)

    def test_is_speaker_active_below_threshold_returns_false(self, monkeypatch):
        """Peak < 0.01 (~ -40 dBFS) → no audible audio → False (skip ducking)."""
        self._install_fake_pycaw(monkeypatch, peak_value=0.005)
        b = WinVolumeBackend()
        b.initialize()
        assert b.is_speaker_active() is False

    def test_is_speaker_active_at_or_above_threshold_returns_true(self, monkeypatch):
        """Peak >= 0.01 → audio is playing → True (duck)."""
        self._install_fake_pycaw(monkeypatch, peak_value=0.01)
        b = WinVolumeBackend()
        b.initialize()
        assert b.is_speaker_active() is True

    def test_is_speaker_active_no_meter_returns_true(self, monkeypatch):
        """If _meter is None (meter init failed), default True (duck anyway)."""
        self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        b.initialize()
        b._meter = None
        assert b.is_speaker_active() is True

    def test_get_other_sessions_excludes_own_pid(self, monkeypatch):
        """PROC-FILTER-FIX: own process excluded by PID backstop (name-independent)."""
        import os

        own_pid = os.getpid()
        own = self._make_session(own_pid, "totally-unrelated-name.exe")
        foreign = self._make_session(own_pid + 1, "spotify.exe")
        self._install_fake_pycaw(monkeypatch, sessions=[own, foreign])
        b = WinVolumeBackend()
        b.initialize()
        result = b.get_other_sessions()
        assert own not in result
        assert foreign in result

    def test_get_other_sessions_excludes_voice_typer_names(self, monkeypatch):
        """PROC-FILTER-FIX: substring match excludes voice_typer / voice-typer / voicetyper."""
        import os

        own_pid = os.getpid()
        voice_typer = self._make_session(own_pid + 100, "voice_typer.exe")
        voice_typer_hyphen = self._make_session(own_pid + 101, "voice-typer.exe")
        voicetyper_camel = self._make_session(own_pid + 102, "VoiceTyper.exe")
        foreign = self._make_session(own_pid + 103, "chrome.exe")
        self._install_fake_pycaw(
            monkeypatch,
            sessions=[voice_typer, voice_typer_hyphen, voicetyper_camel, foreign],
        )
        b = WinVolumeBackend()
        b.initialize()
        result = b.get_other_sessions()
        assert voice_typer not in result
        assert voice_typer_hyphen not in result
        assert voicetyper_camel not in result
        assert foreign in result

    def test_get_other_sessions_excludes_python_interpreter_names(self, monkeypatch):
        """PROC-FILTER-FIX: exact + regex match excludes python / python3 / pythonw + versioned."""
        import os

        own_pid = os.getpid()
        python = self._make_session(own_pid + 200, "python")
        python3 = self._make_session(own_pid + 201, "python3")
        pythonw = self._make_session(own_pid + 202, "pythonw.exe")
        python312 = self._make_session(own_pid + 203, "python3.12")
        foreign = self._make_session(own_pid + 204, "firefox.exe")
        self._install_fake_pycaw(
            monkeypatch,
            sessions=[python, python3, pythonw, python312, foreign],
        )
        b = WinVolumeBackend()
        b.initialize()
        result = b.get_other_sessions()
        assert python not in result
        assert python3 not in result
        assert pythonw not in result
        assert python312 not in result
        assert foreign in result

    def test_get_other_sessions_skips_none_process(self, monkeypatch):
        """Sessions with Process=None are silently skipped."""
        import os
        from unittest.mock import MagicMock

        own_pid = os.getpid()
        none_proc_session = MagicMock()
        none_proc_session.Process = None
        foreign = self._make_session(own_pid + 300, "zoom.exe")
        self._install_fake_pycaw(monkeypatch, sessions=[none_proc_session, foreign])
        b = WinVolumeBackend()
        b.initialize()
        result = b.get_other_sessions()
        assert none_proc_session not in result
        assert foreign in result

    def test_duck_and_restore_round_trip(self, monkeypatch):
        """duck_other_sessions saves original volume; restore_other_sessions restores it."""
        import os
        from unittest.mock import MagicMock

        own_pid = os.getpid()
        sa_vol = MagicMock()
        sa_vol.GetMasterVolume.return_value = 0.8
        sa_vol.SetMasterVolume.return_value = None
        foreign = self._make_session(own_pid + 400, "chrome.exe")
        foreign.SimpleAudioVolume = sa_vol
        self._install_fake_pycaw(monkeypatch, sessions=[foreign])
        b = WinVolumeBackend()
        b.initialize()

        # Duck to 0.2.
        assert b.duck_other_sessions(0.2) is True
        sa_vol.GetMasterVolume.assert_called_once()
        assert sa_vol.SetMasterVolume.call_args[0][0] == 0.2
        assert len(b._sessions) == 1
        saved_vol, saved_orig = b._sessions[0]
        assert saved_vol is sa_vol
        assert saved_orig == 0.8

        # Restore.
        assert b.restore_other_sessions() is True
        assert sa_vol.SetMasterVolume.call_args[0][0] == 0.8
        assert b._sessions == []

    def test_duck_clamps_level(self, monkeypatch):
        """duck_other_sessions clamps level to [0.0, 1.0]."""
        import os
        from unittest.mock import MagicMock

        own_pid = os.getpid()
        sa_vol = MagicMock()
        sa_vol.GetMasterVolume.return_value = 0.5
        sa_vol.SetMasterVolume.return_value = None
        foreign = self._make_session(own_pid + 500, "x.exe")
        foreign.SimpleAudioVolume = sa_vol
        self._install_fake_pycaw(monkeypatch, sessions=[foreign])
        b = WinVolumeBackend()
        b.initialize()
        b.duck_other_sessions(1.5)
        assert sa_vol.SetMasterVolume.call_args[0][0] == 1.0
        b.duck_other_sessions(-0.2)
        assert sa_vol.SetMasterVolume.call_args[0][0] == 0.0

    def test_duck_returns_false_when_no_foreign_sessions(self, monkeypatch):
        """No foreign sessions → nothing to duck → returns False."""
        self._install_fake_pycaw(monkeypatch, sessions=[])
        b = WinVolumeBackend()
        b.initialize()
        assert b.duck_other_sessions(0.2) is False
        assert b._sessions == []

    def test_restore_returns_false_when_nothing_ducked(self, monkeypatch):
        """_sessions empty → nothing to restore → returns False."""
        self._install_fake_pycaw(monkeypatch)
        b = WinVolumeBackend()
        b.initialize()
        assert b.restore_other_sessions() is False


# ═══════════════════════════════════════════════════════════════════════════
# XS-80: MacVolumeBackend CoreAudio path — mocked CoreAudio module.
#
# The existing TestMacBackendOsascript patches CoreAudio to None to force the
# osascript fallback. These tests patch sys.platform='darwin' and install a
# fake CoreAudio module so initialize() switches to the in-process CoreAudio
# path, then exercise the get_state / set_linear / is_speaker_active CoreAudio
# methods (which the osascript tests never reach).
# ═══════════════════════════════════════════════════════════════════════════


class TestMacBackendCoreAudio:
    """XS-80: MacVolumeBackend CoreAudio (pyobjc) path with mocked CoreAudio."""

    @staticmethod
    def _install_fake_coreaudio(monkeypatch):
        """Patch sys.platform='darwin' + install fake CoreAudio module.

        Returns the fake module so tests can assert on
        ``AudioObjectGetPropertyData`` / ``AudioObjectSetPropertyData`` calls.
        """
        import sys
        from unittest.mock import MagicMock

        monkeypatch.setattr(sys, "platform", "darwin")
        fake_ca = MagicMock()
        # Pin the constant values used as selectors / object IDs.
        fake_ca.kAudioDevicePropertyDeviceIsRunning = "kDeviceIsRunning"
        fake_ca.kAudioHardwarePropertyDefaultOutputDevice = "kDefaultOutput"
        fake_ca.kAudioHardwareServiceDeviceProperty_VirtualMasterMute = "kMasterMute"
        fake_ca.kAudioHardwareServiceDeviceProperty_VirtualMasterVolume = "kMasterVolume"
        fake_ca.kAudioHardwareServiceSystemObject = "kHwsSystem"
        fake_ca.kAudioObjectPropertyElementMaster = "kElementMaster"
        fake_ca.kAudioObjectPropertyScopeGlobal = "kScopeGlobal"
        fake_ca.kAudioObjectPropertyScopeOutput = "kScopeOutput"
        fake_ca.kAudioObjectSystemObject = "kSystemObject"
        monkeypatch.setitem(sys.modules, "CoreAudio", fake_ca)
        return fake_ca

    @staticmethod
    def _make_get_property_side_effect(fake_ca, *, default_device=42, is_running=1, volume=0.6, mute=0, status=0):
        """Build a side_effect for AudioObjectGetPropertyData that writes through
        ``ctypes.byref(...)`` data pointers and returns a status code.
        """

        def fake_get_property(obj_id, address, qual_size, qual_data, size_ptr, data_ptr):
            selector = address[0]
            # ctypes.byref(obj) exposes the underlying object via _obj.
            data_obj = getattr(data_ptr, "_obj", None)
            if selector == fake_ca.kAudioHardwarePropertyDefaultOutputDevice:
                if data_obj is not None:
                    data_obj.value = default_device
            elif selector == fake_ca.kAudioDevicePropertyDeviceIsRunning:
                if data_obj is not None:
                    data_obj.value = is_running
            elif selector == fake_ca.kAudioHardwareServiceDeviceProperty_VirtualMasterVolume:
                if data_obj is not None:
                    data_obj.value = volume
            elif selector == fake_ca.kAudioHardwareServiceDeviceProperty_VirtualMasterMute and data_obj is not None:
                data_obj.value = mute
            return status

        return fake_get_property

    def test_initialize_with_coreaudio_succeeds(self, monkeypatch):
        """initialize() loads CoreAudio → _use_coreaudio = True."""
        self._install_fake_coreaudio(monkeypatch)
        b = MacVolumeBackend()
        assert b.initialize() is True
        assert b._use_coreaudio is True
        assert b._ca is not None
        assert b.name == "CoreAudio (pyobjc)"

    def test_initialize_resets_error_counter(self, monkeypatch):
        """initialize() resets _consecutive_errors to 0 on a fresh init."""
        self._install_fake_coreaudio(monkeypatch)
        b = MacVolumeBackend()
        b._consecutive_errors = 5
        b.initialize()
        assert b._consecutive_errors == 0

    def test_recommended_poll_interval_100ms_with_coreaudio(self, monkeypatch):
        """CoreAudio path uses 100ms poll; osascript uses 500ms."""
        self._install_fake_coreaudio(monkeypatch)
        b = MacVolumeBackend()
        b.initialize()
        assert b.recommended_poll_interval_ms == 100
        assert b._set_linear_is_subprocess is False

    def test_supports_per_session_is_false_with_coreaudio(self, monkeypatch):
        """macOS has no per-app volume API even with CoreAudio."""
        self._install_fake_coreaudio(monkeypatch)
        b = MacVolumeBackend()
        b.initialize()
        assert b.supports_per_session is False

    def test_get_default_output_device_returns_id(self, monkeypatch):
        """_get_default_output_device resolves the AudioDeviceID via CoreAudio."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(
            fake_ca, default_device=99, status=0
        )
        b = MacVolumeBackend()
        b.initialize()
        dev = b._get_default_output_device()
        assert dev == 99
        assert b._default_device_id == 99

    def test_get_default_output_device_returns_none_on_status_error(self, monkeypatch):
        """Non-zero status from AudioObjectGetPropertyData → None."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, status=1)
        b = MacVolumeBackend()
        b.initialize()
        assert b._get_default_output_device() is None

    def test_ca_is_device_running_true(self, monkeypatch):
        """kAudioDevicePropertyDeviceIsRunning == 1 → True."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, is_running=1)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_is_device_running(42) is True

    def test_ca_is_device_running_false(self, monkeypatch):
        """kAudioDevicePropertyDeviceIsRunning == 0 → False."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, is_running=0)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_is_device_running(42) is False

    def test_ca_is_device_running_returns_none_on_status_error(self, monkeypatch):
        """Non-zero status → None (caller falls back)."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, status=1)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_is_device_running(42) is None

    def test_ca_get_volume(self, monkeypatch):
        """_ca_get_volume reads Float32 in [0.0, 1.0] via CoreAudio."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        # 0.75 is exactly representable in float32 (avoids c_float quantization).
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, volume=0.75)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_get_volume() == 0.75

    def test_ca_get_volume_clamps_above_1(self, monkeypatch):
        """Driver-bug defense: volume > 1.0 clamps to 1.0."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, volume=1.5)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_get_volume() == 1.0

    def test_ca_get_mute_true(self, monkeypatch):
        """_ca_get_mute reads UInt32 (0/1) via CoreAudio."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, mute=1)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_get_mute() is True

    def test_ca_get_mute_false(self, monkeypatch):
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, mute=0)
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_get_mute() is False

    def test_ca_set_volume_success(self, monkeypatch):
        """_ca_set_volume returns True on status==0."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 0
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_set_volume(0.45) is True
        fake_ca.AudioObjectSetPropertyData.assert_called_once()

    def test_ca_set_volume_failure(self, monkeypatch):
        """_ca_set_volume returns False on non-zero status."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 1
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_set_volume(0.45) is False

    def test_ca_set_mute_success(self, monkeypatch):
        """_ca_set_mute returns True on status==0."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 0
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_set_mute(True) is True

    def test_ca_set_mute_failure(self, monkeypatch):
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 1
        b = MacVolumeBackend()
        b.initialize()
        assert b._ca_set_mute(True) is False

    def test_get_state_via_coreaudio(self, monkeypatch):
        """get_state reads volume + mute via CoreAudio (no osascript subprocess)."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        # 0.75 is exactly representable in float32 (avoids c_float quantization).
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(
            fake_ca, volume=0.75, mute=0
        )
        b = MacVolumeBackend()
        b.initialize()
        # Spy: osascript must NOT be called when CoreAudio succeeds.
        b._osascript_get_state = lambda: (_ for _ in ()).throw(
            AssertionError("osascript should not be called when CoreAudio succeeds")
        )
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.75
        assert state.muted is False

    def test_get_state_detects_muted_via_coreaudio(self, monkeypatch):
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(
            fake_ca, volume=0.5, mute=1
        )
        b = MacVolumeBackend()
        b.initialize()
        state = b.get_state()
        assert state is not None
        assert state.muted is True

    def test_get_state_falls_back_to_osascript_on_coreaudio_failure(self, monkeypatch):
        """If CoreAudio get_state returns None, falls back to osascript."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        # Non-zero status on the volume query → _ca_get_volume returns None
        # → _coreaudio_get_state returns None → fall through to osascript.
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, status=1)
        b = MacVolumeBackend()
        b.initialize()
        b._osascript_get_state = lambda: VolumeState(linear=0.3, muted=True)
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.3
        assert state.muted is True

    def test_set_linear_via_coreaudio(self, monkeypatch):
        """set_linear calls AudioObjectSetPropertyData with the new volume."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 0
        b = MacVolumeBackend()
        b.initialize()
        b._osascript_set = lambda level, muted: (_ for _ in ()).throw(
            AssertionError("osascript should not be called when CoreAudio succeeds")
        )
        assert b.set_linear(0.45) is True
        fake_ca.AudioObjectSetPropertyData.assert_called()

    def test_set_linear_clamps_via_coreaudio(self, monkeypatch):
        """set_linear clamps to [0.0, 1.0] before calling CoreAudio."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 0
        b = MacVolumeBackend()
        b.initialize()
        assert b.set_linear(1.5) is True
        assert b.set_linear(-0.2) is True

    def test_set_linear_falls_back_to_osascript_on_coreaudio_failure(self, monkeypatch):
        """If CoreAudio set returns False, falls back to osascript."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectSetPropertyData.return_value = 1  # failure
        b = MacVolumeBackend()
        b.initialize()
        called = {"yes": False}

        def fake_osascript_set(level, muted):
            called["yes"] = True
            return True

        b._osascript_set = fake_osascript_set
        assert b.set_linear(0.5) is True
        assert called["yes"] is True

    def test_is_speaker_active_returns_true_when_running(self, monkeypatch):
        """kAudioDevicePropertyDeviceIsRunning == 1 → True (audio playing)."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(
            fake_ca, default_device=42, is_running=1
        )
        b = MacVolumeBackend()
        b.initialize()
        assert b.is_speaker_active() is True

    def test_is_speaker_active_returns_false_when_idle(self, monkeypatch):
        """kAudioDevicePropertyDeviceIsRunning == 0 → False (skip ducking)."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(
            fake_ca, default_device=42, is_running=0
        )
        b = MacVolumeBackend()
        b.initialize()
        assert b.is_speaker_active() is False

    def test_is_speaker_active_safe_default_on_query_failure(self, monkeypatch):
        """If CoreAudio query fails, returns True (safe default — duck anyway)."""
        fake_ca = self._install_fake_coreaudio(monkeypatch)
        # Non-zero status on the device-is-running query → None → raise →
        # fall through to safe-default True.
        fake_ca.AudioObjectGetPropertyData.side_effect = self._make_get_property_side_effect(fake_ca, status=1)
        b = MacVolumeBackend()
        b.initialize()
        assert b.is_speaker_active() is True
