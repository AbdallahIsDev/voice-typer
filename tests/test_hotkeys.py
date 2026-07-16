"""Tests for hotkey validation, utils, picker, VK map, and hotkey backends."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

RENDERER_SRC = Path(__file__).resolve().parent.parent / "voice_typer" / "client" / "src" / "renderer" / "src"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestHotkeyUtilsFormatLabel:
    """formatHotkeyLabel converts pynput syntax to human-readable."""

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "hotkey-utils-behavior.test.ts — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_formats_single_key(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert "function formatHotkeyLabel" in utils
        assert '"Ctrl"' in utils or "'Ctrl'" in utils
        assert '"Caps Lock"' in utils or "'Caps Lock'" in utils
        assert '"Space"' in utils or "'Space'" in utils

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "hotkey-utils-behavior.test.ts — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_formats_combo(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert '.split("+")' in utils or ".split('+')" in utils
        assert '.join("+")' in utils or ".join('+')" in utils


class TestHotkeyUtilsValidate:
    """validateHotkey returns null for valid, error string for invalid."""

    def test_validate_function_exists(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert "function validateHotkey" in utils

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "hotkey-utils-behavior.test.ts — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_validate_rejects_empty(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert "Hotkey is empty" in utils

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "hotkey-utils-behavior.test.ts — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_validate_rejects_modifiers_only_in_combo(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert "must end with a non-modifier key" in utils

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "hotkey-utils-behavior.test.ts — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_validate_rejects_multi_key_in_single_mode(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert "must be a single key" in utils


class TestRepasteKeySettingUsesHotkeyPicker:
    """The Re-Paste Key setting uses HotkeyPicker instead of free-text Input."""

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "RecordingSettings-hotkey-picker.test.tsx — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_settings_imports_hotkey_picker(self):
        recording = _read("components/settings/RecordingSettingsSection.tsx")
        assert "import { HotkeyPicker }" in recording
        assert "@/components/hotkey/HotkeyPicker" in recording

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "RecordingSettings-hotkey-picker.test.tsx — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_repaste_key_uses_hotkey_picker_combo_mode(self):
        recording = _read("components/settings/RecordingSettingsSection.tsx")
        assert "<HotkeyPicker" in recording
        assert 'mode="combo"' in recording
        assert "repaste_hotkey" in recording

    def test_no_free_text_input_for_repaste(self):
        recording = _read("components/settings/RecordingSettingsSection.tsx")
        assert not re.search(
            r"<Input[^>]*value=\{config\.repaste_hotkey",
            recording,
            re.DOTALL,
        )


class TestDictationKeySupportsExpandedPresets:
    """The Dictation Key selector supports more than just F2-F12."""

    def test_dictation_key_uses_hotkey_picker_combo_mode(self):
        recording = _read("components/settings/RecordingSettingsSection.tsx")
        assert 'mode="combo"' in recording
        assert "DICTATION_KEY_PRESETS" in recording

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "hotkey-utils-behavior.test.ts — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_single_key_presets_include_beyond_f12(self):
        utils = _read("components/hotkey/hotkey-utils.ts")
        assert "caps_lock" in utils
        assert "print_screen" in utils
        assert "scroll_lock" in utils
        assert "pause" in utils
        assert "insert" in utils
        assert "home" in utils
        assert "page_up" in utils
        assert "page_down" in utils

    def test_old_f2_f12_dropdown_removed(self):
        settings = _read("pages/Settings.tsx")
        assert "'f2', 'f3', 'f4', 'f5', 'f6'" not in settings


class TestApplyConfigReRegistersHotkeyForPushToTalk:
    """Hotkey is re-registered when recording_mode or hotkey changes."""

    def test_service_apply_config_side_effects_handles_recording_mode(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "recording_mode" in service_py
        assert "app.hotkeys.restart" in service_py

    def test_service_handles_hotkey_change(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert '"hotkey" in updates' in service_py


class TestFallbackListenerChecksAllModifiersHeld:
    """Fallback listener checks all modifiers are held before firing."""

    def test_fallback_tracks_modifiers(self):
        from voice_typer.server.hotkeys import PynputHotkey

        source = inspect.getsource(PynputHotkey._start_fallback)
        assert "modifier_keys" in source
        assert "held_modifiers" in source
        assert "len(held_modifiers) < len(modifier_keys)" in source


class TestAutouseFixturePatchesBothHotkeyNamespaces:
    """The autouse fixture must patch both app and hotkey_dispatcher namespaces."""

    def test_fixture_patches_hotkey_dispatcher_namespace(self, monkeypatch):
        import voice_typer.server.hotkey_dispatcher as hd_mod
        from voice_typer.server.hotkeys import PynputHotkey

        original = hd_mod.create_hotkey_backend
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda hotkey_str: PynputHotkey(hotkey_str),
        )
        assert hd_mod.create_hotkey_backend is not original

    def test_app_py_fixture_patches_both_namespaces(self):
        import inspect

        import tests.test_app as test_app_mod

        fixture_src = None
        for _name, obj in vars(test_app_mod).items():
            if callable(obj) and hasattr(obj, "__wrapped__"):
                src = inspect.getsource(obj)
                if "create_hotkey_backend" in src and "PynputHotkey" in src:
                    fixture_src = src
                    break
        if fixture_src is None:
            test_app_path = inspect.getfile(test_app_mod)
            with open(test_app_path) as f:
                fixture_src = f.read()
        assert "hotkey_dispatcher.create_hotkey_backend" in fixture_src


class TestVKMapInitLockGuarded:
    """_init_vk_map is safe to call from multiple threads."""

    def test_concurrent_init_does_not_corrupt_map(self):
        from voice_typer.server import hotkeys

        with hotkeys._VK_MAP_LOCK:
            hotkeys._VK_MAP.clear()

        import threading

        errors: list[Exception] = []

        def init_many():
            try:
                for _ in range(20):
                    hotkeys._init_vk_map()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=init_many) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert "f1" in hotkeys._VK_MAP
        assert "f24" in hotkeys._VK_MAP
        assert "a" in hotkeys._VK_MAP
        assert "esc" in hotkeys._VK_MAP


class TestExtendedVKMap:
    """_init_vk_map includes numpad, media, browser, special keys."""

    def test_media_keys_present(self):
        from voice_typer.server.hotkeys import _VK_MAP, _VK_MAP_LOCK, _init_vk_map

        with _VK_MAP_LOCK:
            _VK_MAP.clear()
        _init_vk_map()
        assert "media_next" in _VK_MAP
        assert "media_play_pause" in _VK_MAP
        assert "browser_home" in _VK_MAP
        assert "capslock" in _VK_MAP
        assert "printscreen" in _VK_MAP

    def test_numpad_keys_present(self):
        from voice_typer.server.hotkeys import _VK_MAP, _init_vk_map

        _init_vk_map()
        assert "num_0" in _VK_MAP
        assert "numpad_5" in _VK_MAP
        assert "num_add" in _VK_MAP
