"""Focused tests for MO-95..101 service-layer i18n + resume contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestResumeModelDownloadReturnsRealBool:
    """MO-95: resume must forward set_download_paused's return value."""

    def test_resume_returns_false_when_no_active_download(self, monkeypatch):
        from voice_typer.server.asr_setup import clear_download_pause_state
        from voice_typer.server.service import LausuService

        clear_download_pause_state()
        service = LausuService(MagicMock())
        result = service.resume_model_download()
        assert result == {"resumed": False}, f"no-active-download resume must return resumed=False, got: {result}"

    def test_resume_returns_true_when_download_is_live(self, monkeypatch):
        # Simulate a live download pause event.
        import threading

        from voice_typer.server import asr_setup
        from voice_typer.server.service import LausuService

        asr_setup._download_pause_event = threading.Event()
        asr_setup._download_pause_event.set()
        try:
            service = LausuService(MagicMock())
            result = service.resume_model_download()
            assert result == {"resumed": True}, f"live-download resume must return resumed=True, got: {result}"
        finally:
            asr_setup._download_pause_event = None


class TestHotkeyDispatcherI18nKeys:
    """MO-96: every hotkey_dispatcher notify key exists and formats."""

    KEYS = [
        ("notify.hotkey_dispatcher.register_failed", {"hotkey": "<caps_lock>"}),
        ("notify.hotkey_dispatcher.save_failed", {}),
        ("notify.hotkey_dispatcher.wayland_caps_lock", {}),
        ("notify.hotkey_dispatcher.ptt_release_missed", {}),
        ("notify.hotkey_dispatcher.esc_register_failed", {}),
        ("notify.hotkey_dispatcher.repaste_register_failed", {}),
        (
            "notify.hotkey_dispatcher.invalid_hotkey",
            {"hotkey": "<win>+<l>", "validation_error": "reserved"},
        ),
        ("notify.hotkey_dispatcher.restore_failed", {"hotkey": "<caps_lock>"}),
    ]

    @pytest.mark.parametrize(("key", "fmt"), KEYS)
    def test_key_resolves_to_non_key_text(self, key, fmt):
        from voice_typer.server.i18n import t

        text = t(key, **fmt)
        assert text != key, f"missing i18n key: {key}"
        assert "{hotkey}" not in text or fmt.get("hotkey")
        if "hotkey" in fmt:
            assert fmt["hotkey"] in text

    def test_register_failed_mentions_hotkey(self):
        from voice_typer.server.i18n import t

        text = t("notify.hotkey_dispatcher.register_failed", hotkey="<f9>")
        assert "<f9>" in text
        assert "could not be registered" in text


class TestNativeAdapterI18nKeys:
    """MO-97: native_adapter notify keys exist and interpolate {app}."""

    KEYS = [
        "notify.native_adapter.warn_title",
        "notify.native_adapter.fallback_title",
        "notify.native_adapter.fallback_body",
        "notify.native_adapter.recovery_title",
        "notify.native_adapter.recovery_body",
        "notify.native_adapter.failure_title",
        "notify.native_adapter.failure_body",
    ]

    @pytest.mark.parametrize("key", KEYS)
    def test_key_exists(self, key):
        from voice_typer.server.i18n import t

        text = t(key)
        assert text != key, f"missing i18n key: {key}"

    def test_title_interpolates_app(self):
        from voice_typer.server.i18n import t

        text = t("notify.native_adapter.fallback_title", app="DemoApp")
        assert "DemoApp" in text
        assert "{app}" not in text


class TestPasteStepClipboardI18n:
    """MO-98: tray uses i18n body; paste_failed omits message."""

    def test_clipboard_body_keys_exist(self):
        from voice_typer.server.i18n import t

        body = t("notify.app.clipboard_unavailable_body")
        assert body != "notify.app.clipboard_unavailable_body"
        assert "clipboard" in body.lower()
        path_line = t(
            "notify.app.clipboard_unavailable_recovery_path",
            path="/tmp/recovery.json",
        )
        assert "/tmp/recovery.json" in path_line

    def test_paste_failed_omits_message(self, monkeypatch):
        from voice_typer.server.clipboard import ClipboardCopyError
        from voice_typer.server.dictation_pipeline import DictationPipeline

        app = MagicMock()
        app.config.paste_on_stop = True
        app.config.clipboard_save_restore = True
        app.config.crash_recovery_enabled = True
        app.clipboard.copy.side_effect = ClipboardCopyError("locked")
        app._crash_recovery._path = "/fake/recovery.json"
        app._device_info = "test"

        published: list[dict] = []

        def _capture(event: dict) -> bool:
            published.append(event)
            return True

        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            _capture,
        )
        pipeline = DictationPipeline.__new__(DictationPipeline)
        pipeline._app = app
        pipeline._cycle_id = "test-cycle"
        pipeline._device_info = "test-device"
        pipeline._hide_or_idle_bubble = MagicMock()
        pipeline._copy_and_paste("hello")

        failed = [e for e in published if e.get("type") == "paste_failed"]
        assert failed, "paste_failed must be published on clipboard failure"
        assert failed[0]["data"].get("message") in (None, "")
        assert failed[0]["data"]["recovery_path"] == "/fake/recovery.json"
        assert app.tray.notify.called
        assert "clipboard" in app.tray.notify.call_args.args[1].lower()


class TestPasteDeferredReasonOnly:
    """MO-101: paste_deferred publishes reason only, no English message."""

    def test_ime_composition_source_has_no_english_message(self):
        from pathlib import Path

        from voice_typer.server.clipboard.manager import _paste as clip_mod

        src = Path(clip_mod.__file__).read_text(encoding="utf-8")
        assert '"reason": "ime_composition"' in src
        assert '"message": "Paste deferred' not in src

    def test_secure_input_source_has_no_english_message(self):
        from pathlib import Path

        from voice_typer.server.clipboard_target_safety import (
            validation as validation_mod,
        )

        src = Path(validation_mod.__file__).read_text(encoding="utf-8")
        assert '"reason": "secure_input"' in src
        assert '"message": "Paste target has secure input' not in src


class TestDeleteModelSuccessOmitsMessage:
    """MO-99: success paths omit message; failures carry i18n message."""

    def test_not_downloaded_failure_uses_i18n(self, tmp_path, monkeypatch):
        from voice_typer.server.service import LausuService

        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )
        service = LausuService(app)
        app.config.model_size = "large-v3-turbo"
        result = service.delete_model("tiny")
        assert result["success"] is False
        assert result["reason"] == "not_downloaded"
        assert "not downloaded" in result["message"]

    def test_unknown_model_failure_uses_i18n(self, tmp_path, monkeypatch):
        from voice_typer.server.service import LausuService

        app = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )
        service = LausuService(app)
        result = service.delete_model("nope-not-real")
        assert result["success"] is False
        assert result["reason"] == "unknown_model"
        assert "Unknown model" in result["message"]


class TestHotkeyDispatcherSourceUsesI18n:
    """Source-level pin: no leftover hardcoded English tray bodies."""

    def test_hotkey_dispatcher_no_hardcoded_notice_bodies(self):
        from pathlib import Path

        src = Path("voice_typer/server/hotkey_dispatcher.py").read_text(encoding="utf-8")
        # The two previously-hardcoded long bodies must be gone.
        assert "could not be registered. It may be in use" not in src
        assert "Failed to save hotkey to disk. Check disk space" not in src
        assert "i18n_t(" in src
        assert "notify.hotkey_dispatcher.register_failed" in src

    def test_native_adapter_no_hardcoded_title_prefix(self):
        from pathlib import Path

        src = Path("voice_typer/server/hotkeys/native_adapter.py").read_text(encoding="utf-8")
        assert 'f"{APP_NAME}: Compatibility mode"' not in src
        assert 'f"{APP_NAME}: Hotkey error"' not in src
        assert "notify.native_adapter.fallback_title" in src
        assert "notify.native_adapter.failure_title" in src
