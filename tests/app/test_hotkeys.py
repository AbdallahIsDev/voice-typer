"""CR-25: split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

import sys
from unittest.mock import MagicMock


class TestHotkeyMapping:
    """Verify the hotkey registration uses the new backend abstraction."""

    def test_register_hotkey_creates_backend(self, app, monkeypatch):
        """_register_hotkey should create a hotkey backend and call start()."""

        # Ensure GlobalHotKeys works (mock returns a MagicMock with is_alive=True)
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        # pyrefly: ignore [missing-attribute]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        assert app.hotkeys._hotkey_backend is not None
        # Main hotkey + repaste hotkey both call GlobalHotKeys.start
        assert mock_ghk_cls.call_count >= 1
        assert mock_listener.start.call_count >= 1

    def test_register_hotkey_failure_does_not_crash(self, app):
        """If both GlobalHotKeys AND fallback Listener raise, app should not crash."""
        mock_kb = sys.modules["pynput.keyboard"]
        # pyrefly: ignore [missing-attribute]
        mock_kb.GlobalHotKeys = MagicMock(side_effect=Exception("no display"))
        # pyrefly: ignore [missing-attribute]
        mock_kb.Listener = MagicMock(side_effect=Exception("no input"))

        # Should not raise
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()
        # Backend was created but start() failed -> not alive or None
        if app.hotkeys._hotkey_backend is not None:
            assert app.hotkeys._hotkey_backend.is_alive() is False

    def test_register_esc_hotkey_creates_and_starts_backend(self, app, monkeypatch):
        """_register_esc_hotkey should create a backend and call start().

        ARCH-ESC-001: the ESC callback is now a closure
        (_esc_callback in hotkey_dispatcher.register_esc) that wraps
        app._cancel_dictation with a keyboard_ownership guard — so the
        test must accept any callable, not the raw bound method.
        """

        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        assert app.hotkeys._esc_backend is None
        # RW-9 Phase 1: was ``app._register_esc_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register_esc()

        assert app.hotkeys._esc_backend is not None
        # The callback is now a closure (ARCH-ESC-001), so accept any
        # callable rather than asserting it's the raw bound method.
        mock_ghk_cls.assert_called_once()
        call_args = mock_ghk_cls.call_args
        assert len(call_args.args) == 1, "GlobalHotKeys must be called with the hotkey dict"
        hotkey_dict = call_args.args[0]
        assert "<esc>" in hotkey_dict, "hotkey dict must contain <esc>"
        assert callable(hotkey_dict["<esc>"]), "ESC callback must be callable"
        mock_listener.start.assert_called_once()

    def test_unregister_esc_hotkey_stops_and_clears_backend(self, app, monkeypatch):
        """_unregister_esc_hotkey should stop the backend and set it to None."""
        mock_backend = MagicMock()
        # #2 create_hotkey_backend is now imported in
        # hotkey_dispatcher, so monkeypatch it there.
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            MagicMock(return_value=mock_backend),
        )

        # RW-9 Phase 1: was ``app._register_esc_hotkey()`` /
        # ``app._unregister_esc_hotkey()`` (test-seam delegates removed);
        # call the dispatcher methods directly.
        app.hotkeys.register_esc()
        assert app.hotkeys._esc_backend is mock_backend

        app.hotkeys.unregister_esc()

        assert app.hotkeys._esc_backend is None
        mock_backend.stop.assert_called_once()

    def test_unregister_esc_hotkey_noop_when_none(self, app):
        """_unregister_esc_hotkey should not crash when _esc_backend is None."""
        app.hotkeys._esc_backend = None
        # RW-9 Phase 1: was ``app._unregister_esc_hotkey()`` (test-seam
        # delegate removed); call the dispatcher method directly.
        app.hotkeys.unregister_esc()  # Must not raise

    def test_register_esc_hotkey_failure_does_not_crash(self, app, monkeypatch):
        """If ESC hotkey registration fails, app should not crash."""

        def failing_create(*args):
            raise RuntimeError("no display")

        # #2 create_hotkey_backend is now imported in
        # hotkey_dispatcher, so monkeypatch it there.
        monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", failing_create)
        # Should not raise even though create_hotkey_backend raises
        # RW-9 Phase 1: was ``app._register_esc_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register_esc()
        assert app.hotkeys._esc_backend is None

    def test_register_hotkey_includes_esc_when_enabled(self, app, monkeypatch):
        """When esc_cancel_enabled is True, _register_hotkey should also call _register_esc_hotkey."""

        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        app.config.esc_cancel_enabled = True
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        assert app.hotkeys._esc_backend is not None

    def test_dictation_callback_respects_hotkey_capture(self, app, monkeypatch):
        """HOTKEY-FIX-001: the dictation hotkey callback must
        be a no-op when the frontend is in hotkey capture mode (keyboard
        ownership == "hotkey_capture"). This prevents sub-tasks 2.4 and
        2.5 — pressing a key during hotkey capture immediately triggering
        recording.
        """
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        # Build the callback via the dispatcher's helper
        callback = app.hotkeys._make_dictation_callback()
        assert callable(callback)

        # Track toggle_dictation calls
        toggle_called = {"n": 0}
        monkeypatch.setattr(app, "toggle_dictation", lambda: toggle_called.__setitem__("n", toggle_called["n"] + 1))

        # Case 1: ownership = normal → callback should fire toggle_dictation
        keyboard_ownership().set_owner("normal", reason="test")
        callback()
        assert toggle_called["n"] == 1, "callback should fire when capture is NOT active"

        # Case 2: ownership = hotkey_capture → callback should be a no-op
        keyboard_ownership().set_owner("hotkey_capture", reason="test capture")
        callback()
        assert toggle_called["n"] == 1, "callback must NOT fire when capture IS active"

        # Case 3: ownership back to normal → callback fires again
        keyboard_ownership().set_owner("normal", reason="test done")
        callback()
        assert toggle_called["n"] == 2, "callback should fire again after capture ends"

    def test_repaste_callback_respects_hotkey_capture(self, app, monkeypatch):
        """HOTKEY-FIX-001: the repaste hotkey callback must
        also be a no-op during hotkey capture (defense-in-depth)."""
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        callback = app.hotkeys._make_repaste_callback()
        assert callable(callback)

        repaste_called = {"n": 0}
        monkeypatch.setattr(app, "repaste_last", lambda: repaste_called.__setitem__("n", repaste_called["n"] + 1))

        keyboard_ownership().set_owner("normal", reason="test")
        callback()
        assert repaste_called["n"] == 1

        keyboard_ownership().set_owner("hotkey_capture", reason="test capture")
        callback()
        assert repaste_called["n"] == 1, "repaste must NOT fire during capture"

        keyboard_ownership().set_owner("normal", reason="test done")
        callback()
        assert repaste_called["n"] == 2

    def test_register_hotkey_skips_esc_when_disabled(self, app, monkeypatch):
        """When esc_cancel_enabled is False, _register_hotkey should skip ESC registration."""

        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        app.config.esc_cancel_enabled = False
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        assert app.hotkeys._esc_backend is None

    def test_cancel_dictation_uses_canonical_ownership_not_legacy_flag(self, app, monkeypatch):
        """Regression: ESC cancel must work from the canonical KeyboardOwnership
        state, not the legacy ``_esc_cancel_paused`` alias. The alias can drift
        out of sync (e.g. the ESC-release path resets the owner but relied on a
        frontend round-trip to clear the alias); if a stale ``_esc_cancel_paused
        = True`` was the gate, ESC became a permanent no-op during recording
        even though the canonical owner was normal/recording.
        """
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        # Simulate the divergence: canonical owner is NORMAL but the legacy
        # alias is stuck True.
        keyboard_ownership().set_owner("normal", reason="test")
        app._esc_cancel_paused = True

        cancel_called = {"n": 0}
        monkeypatch.setattr(app.recording, "cancel", lambda: cancel_called.__setitem__("n", cancel_called["n"] + 1))

        # ESC cancel must still fire despite the stale alias.
        app._cancel_dictation()
        assert cancel_called["n"] == 1, "ESC cancel must fire when canonical ownership is not hotkey_capture"

        # And must remain a no-op while a real capture is active.
        keyboard_ownership().set_owner("hotkey_capture", reason="capture")
        app._cancel_dictation()
        assert cancel_called["n"] == 1, "ESC cancel must NOT fire during real hotkey capture"


class TestFallbackHotkeyParser:
    """Verify parse_hotkey_to_vk correctly converts hotkey strings."""

    def test_parse_f2(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        result = parse_hotkey_to_vk("<f2>")
        assert result == 0x71

    def test_parse_f1(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        result = parse_hotkey_to_vk("<f1>")
        assert result == 0x70

    def test_parse_f12(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        result = parse_hotkey_to_vk("<f12>")
        assert result == 0x7B


class TestHotkeyCallbackChain:
    """End-to-end: hotkey callback -> toggle_dictation -> _start_dictation."""

    def test_full_callback_chain(self, app):
        """Simulate the exact callback path: GlobalHotKeys fires toggle_dictation,
        which should call recorder.start() and set state to RECORDING."""
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app._busy_event.set()  # not busy
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = True

        # Simulate what GlobalHotKeys does: call the registered callback directly
        from voice_typer.server.tray import AppState

        # The callback stored in the hotkey mapping IS app.toggle_dictation
        app.toggle_dictation()

        app.recorder.start.assert_called_once()
        app.tray.set_state.assert_called_once()
        args = app.tray.set_state.call_args
        assert args[0][0] == AppState.RECORDING

    def test_callback_chain_register_then_fire(self, app, monkeypatch):
        """Register hotkey, extract the callback, call it, verify recording starts."""
        captured_mapping = {}

        class FakeGlobalHotKeys:
            def __init__(self, mapping):
                captured_mapping.update(mapping)

            def start(self):
                pass

        mock_kb = sys.modules["pynput.keyboard"]
        # pyrefly: ignore [missing-attribute]
        mock_kb.GlobalHotKeys = FakeGlobalHotKeys

        # NATIVE-001: force the legacy pynput backend (skip native binary)
        # so FakeGlobalHotKeys is actually used. Use monkeypatch.setattr
        # so the patch is reverted after the test (no state leakage).
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)

        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app._busy_event.set()  # not busy

        # Register hotkey - this captures the mapping
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        # The default hotkey is platform-aware (Fn on macOS, Caps Lock on
        # Windows/Linux, F2 on unknown). Use the actual config value.
        expected_hotkey = app.config.hotkey
        assert expected_hotkey in captured_mapping
        callback = captured_mapping[expected_hotkey]
        # HOTKEY-FIX-001: the callback is now an ownership-checking
        # wrapper (_dictation_callback) that calls app.toggle_dictation
        # internally, not the raw bound method. Accept any callable and
        # verify it fires toggle_dictation when ownership is "normal".
        assert callable(callback)

        # Simulate the hotkey being pressed (ownership = normal → should fire)
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        keyboard_ownership().set_owner("normal", reason="test")
        callback()

        from voice_typer.server.tray import AppState

        app.recorder.start.assert_called_once()
        args = app.tray.set_state.call_args
        assert args[0][0] == AppState.RECORDING
