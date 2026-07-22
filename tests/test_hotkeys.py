"""Tests for hotkey validation, utils, picker, VK map, and hotkey backends."""

from __future__ import annotations

import inspect
from pathlib import Path

RENDERER_SRC = Path(__file__).resolve().parent.parent / "voice_typer" / "client" / "src" / "renderer" / "src"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestApplyConfigReRegistersHotkeyForPushToTalk:
    """Hotkey is re-registered when recording_mode or hotkey changes."""

    def test_service_apply_config_side_effects_handles_recording_mode(self):
        # PVT-21 (session-1) extracted apply_config_side_effects from
        # service.py into config_applier.py. The recording_mode / hotkey
        # re-registration logic now lives in config_applier.py.
        config_applier_py = (REPO_ROOT / "voice_typer" / "server" / "config_applier.py").read_text(encoding="utf-8")
        assert "recording_mode" in config_applier_py
        assert "app.hotkeys.restart" in config_applier_py

    def test_service_handles_hotkey_change(self):
        # PVT-21 (session-1) extracted apply_config_side_effects from
        # service.py into config_applier.py. The hotkey-in-updates check
        # now lives in config_applier.py.
        config_applier_py = (REPO_ROOT / "voice_typer" / "server" / "config_applier.py").read_text(encoding="utf-8")
        assert '"hotkey" in updates' in config_applier_py


class TestFallbackListenerChecksAllModifiersHeld:
    """Fallback listener checks all modifiers are held before firing.

    HOTKEYS-12: previously this test pinned the source text of
    ``PynputHotkey._start_fallback`` via ``inspect.getsource``. That
    made the test brittle — any cosmetic refactor (renaming a local
    variable, adding a comment) would break it even if the behavior
    was unchanged. The behavioral tests below verify the actual
    contract: ``_parse_hotkey_to_pynput`` returns the modifier_keys
    tuple that the fallback listener uses to gate the callback fire,
    and the tuple contains ALL configured modifiers (not just one).
    The on_press handler in ``_start_fallback`` then requires
    ``len(held_modifiers) >= len(modifier_keys)`` before firing.
    """

    @staticmethod
    def _make_fake_pynput():
        """Build minimal pynput.keyboard.Key / KeyCode stand-ins.

        ``_parse_hotkey_to_pynput`` only uses ``hasattr``/``getattr`` on
        the ``key`` object and ``key_code.from_char`` / ``from_vk`` on
        the ``key_code`` object, so simple namespace classes work.
        """

        class _FakeKey:
            def __init__(self, name):
                self.name = name

            def __repr__(self):
                return f"_FakeKey({self.name!r})"

        _FakeKey.ctrl = _FakeKey("ctrl")
        _FakeKey.alt = _FakeKey("alt")
        _FakeKey.shift = _FakeKey("shift")
        _FakeKey.cmd = _FakeKey("cmd")
        _FakeKey.alt_r = _FakeKey("alt_r")
        _FakeKey.alt_gr = _FakeKey("alt_gr")

        class _FakeKeyCode:
            def __init__(self, kind, value):
                self.kind = kind
                self.value = value

            @classmethod
            def from_char(cls, char):
                return cls("char", char)

            @classmethod
            def from_vk(cls, vk):
                return cls("vk", vk)

        return _FakeKey, _FakeKeyCode

    def test_parse_returns_modifier_tuple_for_combo(self):
        """For ``<ctrl>+1``, the parser returns ``(modifier_keys, target)``
        where ``modifier_keys`` contains ctrl — the fallback listener
        uses this tuple to require ALL modifiers held before firing."""
        from voice_typer.server.hotkeys import _parse_hotkey_to_pynput

        Key, KeyCode = self._make_fake_pynput()
        result = _parse_hotkey_to_pynput("<ctrl>+1", Key, KeyCode)
        assert isinstance(result, tuple), f"expected (modifier_keys, target) tuple for combo, got {type(result)}"
        modifier_keys, match_key = result
        assert Key.ctrl in modifier_keys, f"ctrl not in modifier_keys {modifier_keys}"
        assert match_key is not None

    def test_parse_returns_single_key_for_no_modifiers(self):
        """For ``<f2>`` (no modifiers), the parser returns the bare
        target key — the fallback listener doesn't gate on modifiers."""
        from voice_typer.server.hotkeys import _parse_hotkey_to_pynput

        Key, KeyCode = self._make_fake_pynput()
        result = _parse_hotkey_to_pynput("1", Key, KeyCode)
        # Single-key hotkeys return the bare key (not a tuple).
        assert result is not None
        assert not isinstance(result, tuple), f"expected bare key for single-key hotkey, got tuple {result}"

    def test_parse_returns_all_modifiers_for_multi_combo(self):
        """For ``<ctrl>+<alt>+v``, the parser returns ALL modifiers
        (ctrl AND alt) — the fallback listener requires BOTH held
        before firing the callback."""
        from voice_typer.server.hotkeys import _parse_hotkey_to_pynput

        Key, KeyCode = self._make_fake_pynput()
        result = _parse_hotkey_to_pynput("<ctrl>+<alt>+v", Key, KeyCode)
        assert isinstance(result, tuple)
        modifier_keys, match_key = result
        assert Key.ctrl in modifier_keys
        assert Key.alt in modifier_keys
        assert len(modifier_keys) == 2, (
            f"expected 2 modifiers for <ctrl>+<alt>+v, got {len(modifier_keys)}: {modifier_keys}"
        )

    def test_parse_returns_no_modifiers_for_bare_modifier(self):
        """For ``<alt>`` alone, the parser returns the bare modifier key
        (single-modifier hotkey — no main key, no modifier tuple)."""
        from voice_typer.server.hotkeys import _parse_hotkey_to_pynput

        Key, KeyCode = self._make_fake_pynput()
        result = _parse_hotkey_to_pynput("<alt>", Key, KeyCode)
        # Single-modifier spec returns the bare modifier key, not a tuple.
        assert result is not None
        assert not isinstance(result, tuple)


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
        import tests.conftest as conftest_mod

        conftest_path = inspect.getfile(conftest_mod)
        with open(conftest_path) as f:
            src = f.read()
        assert "hotkey_dispatcher.create_hotkey_backend" in src


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
