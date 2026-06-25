"""NEW-UX-018: critical tray notifications bypass the show_notifications toggle.

The bug: critical events (model load failure, onboarding failure, corrections
error, crash recovery, Wayland/macOS permission issues, recording stop
failure) were gated by ``self.config.show_notifications``.  If the user
disabled notifications, they would never see these critical errors.

The fix: critical notifications use ``tray.notify_safety()`` which bypasses
the toggle.  Non-critical notifications (volume, audio quality, re-paste
feedback, mic selection) continue to use ``tray.notify()`` which respects
the toggle.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PY = REPO_ROOT / "voice_typer" / "server" / "app.py"
MODEL_MANAGER_PY = REPO_ROOT / "voice_typer" / "server" / "model_manager.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Critical notifications use notify_safety ──────────────────────────


class TestCriticalNotificationsBypassToggle:
    """Each critical notification must use ``tray.notify_safety()``
    (bypasses the toggle) instead of ``tray.notify()`` (respects it)."""

    def test_onboarding_failure_uses_notify_safety(self):
        """3+ onboarding failures → notify_safety."""
        src = _read(APP_PY)
        # Find the onboarding-fail-count >= 3 block.
        assert "self._onboarding_fail_count >= 3" in src
        # The notify call in that block must be notify_safety.
        # Extract the block to verify.
        idx = src.index("self._onboarding_fail_count >= 3")
        block = src[idx:idx + 1500]
        assert "notify_safety(" in block, (
            "Onboarding failure (3+ retries) must use notify_safety so it "
            "bypasses the show_notifications toggle"
        )
        assert "Onboarding setup kept failing" in block

    def test_corrections_error_uses_notify_safety(self):
        """Broken corrections file → notify_safety."""
        src = _read(APP_PY)
        assert "Corrections Error" in src
        # The corrections error block must NOT be gated by show_notifications.
        # Previously: `if err is not None and self.config.show_notifications:`
        # Now: `if err is not None:`
        assert "if err is not None:" in src
        assert "if err is not None and self.config.show_notifications" not in src, (
            "Corrections error must NOT be gated by show_notifications — "
            "use notify_safety to bypass the toggle"
        )

    def test_crash_recovery_uses_notify_safety(self):
        """Recovered transcriptions → notify_safety."""
        src = _read(APP_PY)
        assert "Recovered" in src
        # Find the crash recovery block.
        idx = src.index("Recovered")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block, (
            "Crash recovery notification must use notify_safety"
        )

    def test_wayland_hotkeys_missing_uses_notify_safety(self):
        """Wayland without wtype/ydotool → notify_safety."""
        src = _read(APP_PY)
        assert "Wayland Hotkeys" in src
        idx = src.index("Wayland Hotkeys")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block, (
            "Wayland hotkey warning must use notify_safety — without wtype/ydotool "
            "the global hotkey won't fire, which is critical"
        )

    def test_macos_accessibility_missing_uses_notify_safety(self):
        """macOS without Accessibility permission → notify_safety."""
        src = _read(APP_PY)
        assert "Accessibility Permission" in src
        idx = src.index("Accessibility Permission")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block, (
            "macOS Accessibility permission warning must use notify_safety"
        )

    def test_recording_stop_failure_uses_notify_safety(self):
        """Recording stop exception → notify_safety."""
        src = _read(APP_PY)
        assert "Could not stop recording" in src
        idx = src.index("Could not stop recording")
        block = src[idx - 200:idx + 200]
        assert "notify_safety(" in block, (
            "Recording stop failure must use notify_safety — the user "
            "needs to know why their dictation didn't produce text"
        )

    def test_model_load_failure_uses_notify_safety(self):
        """Model load failure → notify_safety."""
        src = _read(MODEL_MANAGER_PY)
        assert "Could not load the speech model" in src
        idx = src.index("Could not load the speech model")
        block = src[idx - 200:idx + 200]
        assert "notify_safety(" in block, (
            "Model load failure must use notify_safety — the app can't "
            "transcribe without a model"
        )


# ── Non-critical notifications still use notify (respect toggle) ─────


class TestNonCriticalNotificationsRespectToggle:
    """Non-critical notifications must continue to use ``tray.notify()``
    so they respect the user's notification preference."""

    def test_repaste_feedback_uses_notify(self):
        """Re-paste success/failure → notify (non-critical, user-initiated)."""
        src = _read(APP_PY)
        assert "Last transcription re-pasted" in src
        # The re-paste notification must use notify, not notify_safety.
        idx = src.index("Last transcription re-pasted")
        block = src[idx - 200:idx + 100]
        assert "notify(" in block or 'notify("' in block

    def test_microphone_selection_uses_notify(self):
        """Mic selection feedback → notify (non-critical, user-initiated)."""
        src = _read(APP_PY)
        assert "Microphone:" in src or "Microphone next recording" in src

    def test_audio_quality_warning_uses_notify(self):
        """Audio quality issues → notify (informational)."""
        src = _read(APP_PY)
        # The audio quality notification is at the end of _process_transcription
        assert "AudioQualityAnalyzer" in src or "audio_quality" in src.lower()


# ── notify_safety method exists on TrayIcon ──────────────────────────


class TestNotifySafetyMethod:
    """TrayIcon must have a notify_safety method that bypasses the toggle."""

    def test_notify_safety_exists(self):
        from voice_typer.server.tray import TrayIcon
        assert hasattr(TrayIcon, "notify_safety")

    def test_notify_safety_does_not_check_notifications_enabled(self):
        """notify_safety must NOT check _notifications_enabled (that's
        the whole point — it bypasses the toggle)."""
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(
            encoding="utf-8"
        )
        # Find the notify_safety method.
        idx = tray_py.index("def notify_safety")
        method = tray_py[idx:idx + 500]
        # It must NOT reference _notifications_enabled.
        assert "_notifications_enabled" not in method, (
            "notify_safety must NOT check _notifications_enabled — "
            "it exists specifically to bypass the toggle"
        )
