"""Tray microphone notification must not hardcode English "System Default"."""

from __future__ import annotations

from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_app(monkeypatch, mic_recording=False):
    from voice_typer.server import settings_controller as sc

    tray = MagicMock()
    tray.notify = MagicMock()
    app = SimpleNamespace(
        config=SimpleNamespace(microphone="old-mic", save=lambda: True),
        _config_mutation_lock=RLock(),
        tray=tray,
        recorder=SimpleNamespace(recording=mic_recording),
        _audio_processor=MagicMock(),
    )
    monkeypatch.setattr(sc, "Recorder", MagicMock(return_value=MagicMock()))
    return app, tray


def test_system_default_label_key_exists_in_english_registry():
    from voice_typer.server import i18n

    assert i18n.t("notify.settings_controller.system_default_device") != (
        "notify.settings_controller.system_default_device"
    ), "English fallback for the System Default device label must exist"


def test_select_none_uses_translated_label_not_hardcoded_english(monkeypatch):
    from voice_typer.server import i18n
    from voice_typer.server.settings_controller import SettingsController

    app, tray = _make_app(monkeypatch)
    SettingsController(app).select_microphone(None)
    expected = i18n.t(
        "notify.settings_controller.mic_changed",
        label=i18n.t("notify.settings_controller.system_default_device"),
    )
    sent = [call.args[1] for call in tray.notify.call_args_list]
    assert expected in sent, "None mic must notify with the translated label"


def test_concrete_device_name_passes_through_unlocalized(monkeypatch):
    from voice_typer.server.settings_controller import SettingsController

    app, tray = _make_app(monkeypatch)
    SettingsController(app).select_microphone("USB Mic")
    sent = [call.args[1] for call in tray.notify.call_args_list]
    assert any("USB Mic" in message for message in sent)


def test_missing_key_falls_back_to_english_until_renderer_pushes(monkeypatch):
    from voice_typer.server import i18n

    i18n.register_locale("xx-fallback-probe", {})
    monkeypatch.setattr(i18n, "_CURRENT_LOCALE", "xx-fallback-probe")
    try:
        assert i18n.t("notify.settings_controller.system_default_device") == "System Default"
        i18n.merge_labels("xx-fallback-probe", {"notify.settings_controller.system_default_device": "Système X"})
        assert i18n.t("notify.settings_controller.system_default_device") == "Système X"
    finally:
        monkeypatch.undo()
        with i18n._LOCK:
            i18n._REGISTRY.pop("xx-fallback-probe", None)
            i18n._CURRENT_LOCALE = i18n.DEFAULT_LOCALE


def test_no_hardcoded_system_default_in_controller_source():
    import pathlib

    source = pathlib.Path("voice_typer/server/settings_controller.py").read_text(encoding="utf-8")
    assert '"System Default"' not in source, "label must come from i18n, not a literal"
