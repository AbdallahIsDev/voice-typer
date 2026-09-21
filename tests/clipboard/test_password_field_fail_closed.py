"""FV-21: password-field detection fails closed when infra is unavailable."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import voice_typer.server.clipboard as clip_mod
import voice_typer.server.clipboard_target_safety.validation as safety_mod


class TestPasswordFieldFailsClosedWhenDetectionUnavailable:
    def test_macos_pyobjc_missing_fails_closed(self):
        with patch.dict("sys.modules", {"AppKit": None, "ApplicationServices": None}), patch.object(clip_mod, "log"):
            # Force ImportError by clearing modules after import attempt — patch import
            import builtins

            real_import = builtins.__import__

            def _fake_import(name, *args, **kwargs):
                if name in ("AppKit", "ApplicationServices"):
                    raise ImportError("pyobjc missing")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fake_import):
                result = safety_mod._is_password_field_macos()
        assert result is True, "pyobjc missing must fail closed (auto-paste suppressed)"

    def test_macos_workspace_none_fails_closed(self):
        appkit = MagicMock()
        appkit.NSWorkspace.sharedWorkspace.return_value = None
        appservices = MagicMock()
        with (
            patch.dict("sys.modules", {"AppKit": appkit, "ApplicationServices": appservices}),
            patch.object(clip_mod, "log"),
        ):
            result = safety_mod._is_password_field_macos()
        assert result is True

    def test_linux_pyatspi_missing_fails_closed(self):
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "pyatspi":
                raise ImportError("pyatspi missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import), patch.object(clip_mod, "log"):
            result = safety_mod._is_password_field_linux()
        assert result is True, "pyatspi missing must fail closed"

    def test_linux_desktop_none_fails_closed(self):
        fake = MagicMock()
        fake.Registry.getDesktop.return_value = None
        fake.STATE_FOCUSED = 1 << 10
        with patch.dict("sys.modules", {"pyatspi": fake}), patch.object(clip_mod, "log"):
            result = safety_mod._is_password_field_linux()
        assert result is True

    def test_nonwindows_dispatch_exception_fails_closed(self):
        from voice_typer.server.clipboard.safety import _is_safe_paste_target_impl

        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=True),
            patch.object(clip_mod, "_is_password_field_macos", side_effect=RuntimeError("boom")),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = _is_safe_paste_target_impl()
        assert result is False, "dispatch exception must fail closed (FV-21)"
        assert any("failing closed" in str(c) for c in mock_log.warning.call_args_list)
