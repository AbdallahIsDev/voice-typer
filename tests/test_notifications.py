"""Tests for notification system: critical notifications bypass the
show_notifications toggle, non-critical notifications respect it."""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PY = REPO_ROOT / "voice_typer" / "server" / "app.py"
MODEL_MANAGER_PY = REPO_ROOT / "voice_typer" / "server" / "model_manager.py"


def _read_ux018(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestCriticalNotificationsBypassToggle:
    """Each critical notification uses tray.notify_safety() to bypass the toggle."""

    def test_onboarding_failure_uses_notify_safety(self):
        src = _read_ux018(APP_PY)
        assert "self._onboarding_fail_count >= 3" in src
        idx = src.index("self._onboarding_fail_count >= 3")
        block = src[idx:idx + 1500]
        assert "notify_safety(" in block
        assert "Onboarding setup kept failing" in block

    def test_corrections_error_uses_notify_safety(self):
        src = _read_ux018(APP_PY)
        assert "Corrections Error" in src
        assert "if err is not None:" in src
        assert "if err is not None and self.config.show_notifications" not in src

    def test_crash_recovery_uses_notify_safety(self):
        src = _read_ux018(APP_PY)
        assert "Recovered" in src
        idx = src.index("Recovered")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block

    def test_wayland_hotkeys_missing_uses_notify_safety(self):
        src = _read_ux018(APP_PY)
        assert "Wayland Hotkeys" in src
        idx = src.index("Wayland Hotkeys")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block

    def test_macos_accessibility_missing_uses_notify_safety(self):
        src = _read_ux018(APP_PY)
        assert "Accessibility Permission" in src
        idx = src.index("Accessibility Permission")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block

    def test_recording_stop_failure_uses_notify_safety(self):
        rc_py = (REPO_ROOT / "voice_typer" / "server" / "recording_controller.py")
        src = rc_py.read_text(encoding="utf-8")
        assert "Could not stop recording" in src
        idx = src.index("Could not stop recording")
        block = src[idx - 200:idx + 200]
        assert "notify_safety(" in block

    def test_model_load_failure_uses_notify_safety(self):
        src = _read_ux018(MODEL_MANAGER_PY)
        assert "Could not load the speech model" in src
        idx = src.index("Could not load the speech model")
        block = src[idx - 200:idx + 200]
        assert "notify_safety(" in block


class TestNonCriticalNotificationsRespectToggle:
    """Non-critical notifications use tray.notify() (respects the toggle)."""

    def test_repaste_feedback_uses_notify(self):
        src = _read_ux018(APP_PY)
        assert "Last transcription re-pasted" in src
        idx = src.index("Last transcription re-pasted")
        block = src[idx - 200:idx + 100]
        assert "notify(" in block or 'notify("' in block

    def test_microphone_selection_uses_notify(self):
        src = _read_ux018(APP_PY)
        assert "Microphone:" in src or "Microphone next recording" in src

    def test_audio_quality_warning_uses_notify(self):
        src = _read_ux018(APP_PY)
        assert "AudioQualityAnalyzer" in src or "audio_quality" in src.lower()


class TestNotifySafetyMethod:
    """TrayIcon has a notify_safety method that bypasses the toggle."""

    def test_notify_safety_exists(self):
        from voice_typer.server.tray import TrayIcon
        assert hasattr(TrayIcon, "notify_safety")

    def test_notify_safety_does_not_check_notifications_enabled(self):
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(encoding="utf-8")
        idx = tray_py.index("def notify_safety")
        method = tray_py[idx:idx + 500]
        assert "_notifications_enabled" not in method


class TestStoreResultFailurePromotion:
    """Failure to write history fires a tray notification."""

    def test_store_result_calls_tray_notify_on_history_failure(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline
        from unittest.mock import MagicMock

        pipeline = DictationPipeline.__new__(DictationPipeline)
        pipeline._duration = 1.0
        pipeline._cycle_id = "test-cycle"
        app = MagicMock()
        app.config.model_size = "tiny.en"
        app.config.device = "cpu"
        app.config.crash_recovery_enabled = False
        app.config.log_transcriptions = False
        app.history_db.add_transcription.side_effect = RuntimeError("DB locked")
        app.tray.notify = MagicMock()
        pipeline._app = app

        pipeline._store_result("hello world")

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("history" in str(args).lower() for args in notify_calls)


class TestApplyVocabularyTemplateNotify:
    """Vocabulary/template apply failures fire a tray notification."""

    def test_apply_vocabulary_notifies_on_failure(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline
        from unittest.mock import MagicMock

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        app._vocabulary_manager = MagicMock()
        app._vocabulary_manager.apply_to_text.side_effect = RuntimeError("vocab boom")
        app.tray.notify = MagicMock()
        pipeline._app = app
        pipeline._vocab_fail_notified = False

        pipeline._apply_vocabulary("hello world")

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("Vocabulary" in str(args) for args in notify_calls)

    def test_apply_templates_notifies_on_failure(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline
        from unittest.mock import MagicMock

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        app.config.templates_enabled = True
        app._template_manager = MagicMock()
        app._template_manager.match.side_effect = RuntimeError("template boom")
        app.tray.notify = MagicMock()
        pipeline._app = app
        pipeline._template_fail_notified = False

        pipeline._apply_templates("hello world")

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("Template" in str(args) for args in notify_calls)


class TestRepasteLastSplitsErrors:
    """Clipboard-copy failure and paste-keystroke failure produce distinct notifications."""

    def test_copy_failure_message_mentions_clipboard(self):
        from voice_typer.server import app as app_module
        from unittest.mock import MagicMock

        app = app_module.VoiceTyperApp.__new__(app_module.VoiceTyperApp)
        app._last_transcription = "hello"
        app.clipboard = MagicMock()
        app.clipboard.copy.side_effect = RuntimeError("clipboard locked")
        app.tray = MagicMock()

        app.repaste_last()

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("clipboard" in str(args).lower() for args in notify_calls)
        app.clipboard.paste.assert_not_called()

    def test_paste_failure_message_mentions_keystroke(self):
        from voice_typer.server import app as app_module
        from unittest.mock import MagicMock

        app = app_module.VoiceTyperApp.__new__(app_module.VoiceTyperApp)
        app._last_transcription = "hello"
        app.clipboard = MagicMock()
        app.clipboard.copy.return_value = True
        app.clipboard.paste.side_effect = RuntimeError("SendInput failed")
        app.tray = MagicMock()

        app.repaste_last()

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("paste" in str(args).lower() or "ctrl+v" in str(args).lower()
                    for args in notify_calls)
