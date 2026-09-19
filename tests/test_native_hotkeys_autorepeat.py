"""Tests for the auto-repeat filter  and VERSION handler"""

from __future__ import annotations

import logging
import sys


def _make_linux_backend(monkeypatch, hotkey_str: str = "<caps_lock>"):
    """Construct a LinuxEvdevHotkey with platform stubs in place."""
    from voice_typer.server import native_hotkeys

    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")
    from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

    return LinuxEvdevHotkey(hotkey_str)


def _make_macos_backend(monkeypatch, hotkey_str: str = "<fn>"):
    """Construct a MacNativeHotkey with platform stubs in place."""
    from voice_typer.server import native_hotkeys

    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(sys, "platform", "darwin")
    from voice_typer.server.native_hotkeys import MacNativeHotkey

    return MacNativeHotkey(hotkey_str)


class TestKeyAutoRepeatFilter:
    """a repeated KEY_DOWN (no intervening KEY_UP) must NOT"""

    def test_first_key_down_fires(self, monkeypatch):
        """The first KEY_DOWN after init (or after a KEY_UP) fires once."""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("KEY_DOWN:CapsLock")
        assert fired == ["press"]

    def test_second_key_down_without_keyup_is_suppressed(self, monkeypatch):
        """a second KEY_DOWN without an intervening KEY_UP is"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("KEY_DOWN:CapsLock")
        b._handle_line("KEY_DOWN:CapsLock")  # auto-repeat, suppressed
        b._handle_line("KEY_DOWN:CapsLock")  # auto-repeat, suppressed
        assert fired == ["press"], f"auto-repeat KEY_DOWN should be suppressed; got {fired}"

    def test_key_up_resets_state_allows_new_keydown(self, monkeypatch):
        """After a KEY_UP, the next KEY_DOWN is a fresh press and fires."""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        fired: list[str] = []
        released: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._on_release_callback = lambda: released.append("release")  # noqa: E731
        b._handle_line("KEY_DOWN:CapsLock")
        b._handle_line("KEY_DOWN:CapsLock")  # auto-repeat, suppressed
        b._handle_line("KEY_UP:CapsLock")  # release fires
        b._handle_line("KEY_DOWN:CapsLock")  # fresh press, fires
        assert fired == ["press", "press"], f"got {fired}"
        assert released == ["release"], f"got {released}"

    def test_toggle_on_keyup_only_fires_on_release(self, monkeypatch):
        """In toggle-on-keyup mode, KEY_DOWN never fires; only KEY_UP"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        b.set_toggle_on_keyup(True)
        fired: list[str] = []
        b._callback = lambda: fired.append("toggle")  # noqa: E731
        # KEY_DOWN in toggle-on-keyup mode does nothing (deferred to key-up).
        b._handle_line("KEY_DOWN:CapsLock")
        assert fired == []
        # Auto-repeat KEY_DOWN also does nothing.
        b._handle_line("KEY_DOWN:CapsLock")
        assert fired == []
        # KEY_UP fires the toggle exactly once.
        b._handle_line("KEY_UP:CapsLock")
        assert fired == ["toggle"]

    def test_wrong_key_doesnt_set_main_key_down(self, monkeypatch):
        """A KEY_DOWN for the wrong key does not latch _main_key_down"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        # Wrong key, should not fire and should not latch _main_key_down
        b._handle_line("KEY_DOWN:F2")
        assert fired == []  # wrong key, no fire
        # KEY_DOWN:CapsLock, _main_key_down is now True (latched by
        b._handle_line("KEY_DOWN:CapsLock")
        assert fired == [] or fired == ["press"]


class TestModifierAutoRepeatFilter:
    """a repeated MOD_DOWN (no intervening MOD_UP) must NOT"""

    def test_first_mod_down_fires(self, monkeypatch):
        """For <alt>, the first MOD_DOWN:Alt fires once."""
        b = _make_linux_backend(monkeypatch, "<alt>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("MOD_DOWN:Alt")
        assert fired == ["press"]

    def test_second_mod_down_without_modup_is_suppressed(self, monkeypatch):
        """a second MOD_DOWN:Alt without an intervening MOD_UP"""
        b = _make_linux_backend(monkeypatch, "<alt>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("MOD_DOWN:Alt")
        b._handle_line("MOD_DOWN:Alt")  # auto-repeat, suppressed
        b._handle_line("MOD_DOWN:Alt")  # auto-repeat, suppressed
        assert fired == ["press"], f"auto-repeat MOD_DOWN should be suppressed; got {fired}"

    def test_mod_up_resets_state_allows_new_moddown(self, monkeypatch):
        """After a MOD_UP, the next MOD_DOWN is a fresh press and fires."""
        b = _make_linux_backend(monkeypatch, "<alt>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("MOD_DOWN:Alt")
        b._handle_line("MOD_DOWN:Alt")  # auto-repeat, suppressed
        b._handle_line("MOD_UP:Alt")  # release (currently doesn't fire cb)
        b._handle_line("MOD_DOWN:Alt")  # fresh press, fires
        assert fired == ["press", "press"], f"got {fired}"

    def test_modifier_only_alt_with_extra_doesnt_fire(self, monkeypatch):
        """For <alt>, Alt+Ctrl should NOT fire (extra Ctrl held)."""
        b = _make_linux_backend(monkeypatch, "<alt>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("MOD_DOWN:Ctrl")  # held first
        b._handle_line("MOD_DOWN:Alt")  # now Alt is held, but Ctrl is too
        assert fired == []  # NOT fired, extra modifier

    def test_repeated_ctrl_then_alt_doesnt_double_fire_alt(self, monkeypatch):
        """A repeat of Ctrl (auto-repeat) followed by a fresh Alt press"""
        b = _make_linux_backend(monkeypatch, "<alt>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("MOD_DOWN:Ctrl")  # Ctrl held, extra modifier
        b._handle_line("MOD_DOWN:Ctrl")  # auto-repeat of Ctrl, suppressed
        b._handle_line("MOD_DOWN:Alt")  # fresh Alt, but Ctrl still held
        assert fired == []  # NOT fired, Ctrl is still an extra modifier


class TestFnAutoRepeatFilter:
    """FN_DOWN auto-repeat filter on macOS. The FN event path"""

    def test_fn_down_fires(self, monkeypatch):
        """For <fn> on macOS, FN_DOWN fires once."""
        b = _make_macos_backend(monkeypatch, "<fn>")
        fired: list[str] = []
        b._callback = lambda: fired.append("press")  # noqa: E731
        b._handle_line("FN_DOWN")
        assert fired == ["press"]

    def test_fn_up_fires_release(self, monkeypatch):
        """FN_UP fires the release callback."""
        b = _make_macos_backend(monkeypatch, "<fn>")
        released: list[str] = []
        b._on_release_callback = lambda: released.append("release")  # noqa: E731
        b._handle_line("FN_UP")
        assert released == ["release"]


class TestVersionHandler:
    """factory from the manifest), a mismatch logs a WARNING."""

    def test_version_recorded(self, monkeypatch):
        """A ``VERSION:1.0.0`` line sets ``_binary_version`` to \"1.0.0\"."""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        assert b._binary_version is None
        b._handle_line("VERSION:1.0.0")
        assert b._binary_version == "1.0.0"

    def test_version_with_whitespace_stripped(self, monkeypatch):
        """``VERSION: 1.0.0`` (with a space) is stripped to \"1.0.0\"."""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        b._handle_line("VERSION: 1.2.3 ")
        assert b._binary_version == "1.2.3"

    def test_empty_version_ignored(self, monkeypatch):
        """An empty ``VERSION:`` line does NOT set _binary_version."""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        b._handle_line("VERSION:")
        assert b._binary_version is None

    def test_version_mismatch_logs_warning(self, monkeypatch, caplog):
        """When _expected_version is set and the binary reports a"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        b._expected_version = "2.0.0"  # manifest says 2.0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.native_hotkeys"):
            b._handle_line("VERSION:1.0.0")  # binary says 1.0.0
        # A warning should have been logged mentioning "mismatch".
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("mismatch" in r.getMessage().lower() for r in warnings), (
            f"expected a mismatch warning; got {[r.getMessage() for r in warnings]}"
        )

    def test_version_match_no_warning(self, monkeypatch, caplog):
        """When _expected_version matches the binary's reported version,"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        b._expected_version = "1.0.0"
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.native_hotkeys"):
            b._handle_line("VERSION:1.0.0")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "mismatch" in r.getMessage().lower()]
        assert warnings == [], f"no mismatch warning expected; got {warnings}"

    def test_no_expected_version_skips_comparison(self, monkeypatch, caplog):
        """comparison is skipped, no warning even if the version looks"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        # _expected_version defaults to None in __init__
        assert b._expected_version is None
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.native_hotkeys"):
            b._handle_line("VERSION:99.99.99")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "mismatch" in r.getMessage().lower()]
        assert warnings == [], f"no warning expected; got {warnings}"
        # But _binary_version IS still recorded.
        assert b._binary_version == "99.99.99"


class TestPongHandler:
    """first PONG. Verify the auto-repeat filter and VERSION handler"""

    def test_pong_sets_supported_flag(self, monkeypatch):
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        assert b._pong_supported is False
        b._handle_line("PONG")
        assert b._pong_supported is True

    def test_pong_does_not_update_last_event_timestamp(self, monkeypatch):
        """watchdog can distinguish 'alive and responding' from 'alive"""
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        old_ts = b._last_event_received_at
        # Sleep briefly so time.time() would advance if it were called.
        import time

        time.sleep(0.01)
        b._handle_line("PONG")
        # PONG should NOT have updated _last_event_received_at.
        assert b._last_event_received_at == old_ts


class TestNativeLogPath:
    """``_compute_native_log_path`` resolves a stable per-backend"""

    def test_log_path_resolved(self, monkeypatch, tmp_path, request):
        """The log path is ``<config_dir>/logs/native-<backend>.log`` (no PID)."""
        # Point the canonical config dir at tmp so the test never
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        _paths_mod._reset_config_dir_cache()
        request.addfinalizer(_paths_mod._reset_config_dir_cache)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        path = b._compute_native_log_path()
        assert path is not None
        assert path.parent == tmp_path / "logs"
        assert path.name == "native-linux.log"
        # The log dir should have been created.
        assert path.parent.is_dir()

    def test_log_path_memoised(self, monkeypatch, tmp_path, request):
        """Repeated calls return the same path (memoised)."""
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        _paths_mod._reset_config_dir_cache()
        request.addfinalizer(_paths_mod._reset_config_dir_cache)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        p1 = b._compute_native_log_path()
        p2 = b._compute_native_log_path()
        assert p1 == p2

    def test_log_path_none_when_home_unavailable(self, monkeypatch):
        """If no logs dir resolves, the log path is None (no crash)."""
        from voice_typer.server.native_hotkeys import _spawn as _spawn_mod

        monkeypatch.setattr(_spawn_mod, "_resolve_canonical_logs_dir", lambda: None)
        monkeypatch.setattr(_spawn_mod, "_legacy_home_logs_dir", lambda: None)
        b = _make_linux_backend(monkeypatch, "<caps_lock>")
        path = b._compute_native_log_path()
        assert path is None
