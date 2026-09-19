"""macOS TOCTOU coverage for ``ClipboardManager.paste()``."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402

from tests.fixtures.clipboard_helpers import make_clipboard_manager  # noqa: E402


def _install_fake_appkit(*, pid_side_effect) -> MagicMock:
    """Install a fake ``AppKit`` module into ``sys.modules`` for the TOCTOU test."""
    front_app = MagicMock(name="frontmostApplication")
    front_app.processIdentifier.side_effect = pid_side_effect

    workspace = MagicMock(name="NSWorkspace")
    workspace.sharedWorkspace.return_value = workspace
    workspace.frontmostApplication.return_value = front_app

    appkit = MagicMock(name="AppKit")
    appkit.NSWorkspace = workspace
    return appkit


class TestMacosToctouPidRecheck:
    """macOS TOCTOU re-check via ``AppKit.NSWorkspace`` mocking."""

    def test_paste_aborts_when_frontmost_pid_changes(self):
        """Frontmost app PID changes between safety check and Cmd+V → abort."""
        cm = make_clipboard_manager(save_restore=False)
        # Two calls to _get_frontmost_pid_macos:
        appkit = _install_fake_appkit(pid_side_effect=[1234, 5678])

        with (
            patch.dict(sys.modules, {"AppKit": appkit}),
            patch.object(clip_mod, "_Key") as mock_key,
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=True),
            patch.object(
                ClipboardManager,
                "_is_safe_paste_target",
                return_value=True,
            ),
            patch.object(
                ClipboardManager,
                "_detect_focused_process",
                return_value=None,
            ),
            patch.object(clip_mod, "log") as mock_log,
        ):
            mock_key.cmd = "cmd_key"
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()

            result = cm.paste()

        # TOCTOU defense: paste must abort, NOT send the keystroke.
        assert result is False, (
            "paste() must return False when the frontmost macOS app PID "
            "changes between the safety check and the Cmd+V keystroke "
            "(TOCTOU: user Cmd-Tabbed to a credential prompt)."
        )
        # Verify the keystroke was NOT sent.
        cm._keyboard.press.assert_not_called()
        # Verify the TOCTOU warning was logged (manager.py:1155-1161).
        toctou_warnings = [
            c for c in mock_log.warning.call_args_list if "TOCTOU" in str(c) and "1234" in str(c) and "5678" in str(c)
        ]
        assert len(toctou_warnings) == 1, (
            f"Expected exactly one TOCTOU warning log call referencing "
            f"PID 1234 -> 5678; got {len(toctou_warnings)}: "
            f"{mock_log.warning.call_args_list}"
        )

    def test_paste_proceeds_when_frontmost_pid_unchanged(self):
        """Frontmost app PID unchanged between safety check and Cmd+V → proceed."""
        cm = make_clipboard_manager(save_restore=False)
        # Two calls to _get_frontmost_pid_macos, both return the same PID.
        appkit = _install_fake_appkit(pid_side_effect=[1234, 1234])

        with (
            patch.dict(sys.modules, {"AppKit": appkit}),
            patch.object(clip_mod, "_Key") as mock_key,
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=True),
            patch.object(
                ClipboardManager,
                "_is_safe_paste_target",
                return_value=True,
            ),
            patch.object(
                ClipboardManager,
                "_detect_focused_process",
                return_value=None,
            ),
            patch.object(clip_mod, "log") as mock_log,
        ):
            mock_key.cmd = "cmd_key"
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()

            result = cm.paste()

        # Happy path: paste succeeds and the Cmd+V keystroke is sent.
        assert result is True, (
            "paste() must return True when the frontmost macOS app PID "
            "is unchanged between the safety check and the Cmd+V keystroke."
        )
        # Verify the Cmd+V keystroke was sent (modifier press + char press).
        cm._keyboard.press.assert_any_call("cmd_key")
        cm._keyboard.press.assert_any_call("v")
        # Verify NO TOCTOU warning was logged on the happy path.
        toctou_warnings = [c for c in mock_log.warning.call_args_list if "TOCTOU" in str(c)]
        assert len(toctou_warnings) == 0, (
            f"Expected NO TOCTOU warning on the happy path; got {len(toctou_warnings)}: {toctou_warnings}"
        )
