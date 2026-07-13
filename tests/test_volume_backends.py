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

from voice_typer.server.volume_backend import VolumeState
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
        assert any("50%" in " ".join(c) for c in calls), \
            f"set_linear(0.5) should set 50%; calls={calls}"

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
        assert any("set-sink-mute" in c and "1" in c for c in calls), \
            f"set_linear(0.5, muted=True) should call set-sink-mute 1; calls={calls}"


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
        assert any("0.30" in " ".join(c) for c in calls), \
            f"set_linear(0.3) should pass 0.30; calls={calls}"


class TestLinuxBackendAmixer:
    """§5.3: amixer (ALSA fallback) parsing."""

    def test_get_state_parses_percent_and_on(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        b._run = lambda cmd, timeout=2.0: \
            "  Simple mixer control 'Master',0\n    Mono: Playback 50% [50%] [-6.00dB] [on]"
        state = b.get_state()
        assert state is not None
        assert state.linear == 0.5
        assert state.muted is False

    def test_get_state_detects_off(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        b._run = lambda cmd, timeout=2.0: \
            "  Mono: Playback 0% [0%] [-100.00dB] [off]"
        state = b.get_state()
        assert state is not None
        assert state.muted is True

    def test_set_linear_uses_percent(self, monkeypatch):
        b = LinuxVolumeBackend()
        b._tool = "amixer"
        calls = []
        b._run = lambda cmd, timeout=2.0: calls.append(cmd) or "ok"
        b.set_linear(0.75)
        assert any("75%" in " ".join(c) for c in calls), \
            f"set_linear(0.75) should pass 75%; calls={calls}"


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
        assert any("45" in s for s in calls), \
            f"set_linear(0.45) should pass 45%; calls={calls}"

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
        assert any("output muted true" in s for s in calls), \
            f"set_linear(0.5, muted=True) should set muted=true; calls={calls}"


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
        with patch("voice_typer.server.volume_backend.time.sleep"):
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
