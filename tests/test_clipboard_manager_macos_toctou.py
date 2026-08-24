"""macOS TOCTOU coverage for ``ClipboardManager.paste()``.

 (Medium): the clipboard package had a coverage gap — the macOS
TOCTOU (Time-Of-Check-To-Time-Of-Use) re-check inside
``ClipboardManager.paste()`` was only tested by mocking
``_get_frontmost_pid_macos`` directly (see
``tests/clipboard/win32/test_win32_copy_paste.py`` (TestPasteWindowsBranches)). That bypasses
the real ``_get_frontmost_pid_macos`` code path which reads
``AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
.processIdentifier()``. These two tests close the gap by mocking
``AppKit.NSWorkspace`` itself, exercising the production
``_get_frontmost_pid_macos`` static method end-to-end.

Scenario under test
-------------------

``voice_typer/server/clipboard/manager.py:1082-1083`` captures
``safe_macos_pid = self._get_frontmost_pid_macos()`` immediately after
the safety check. Then, immediately before sending the Cmd+V keystroke
(either in the terminal branch at ``manager.py:1117-1137`` or the
non-terminal macOS branch at ``manager.py:1150-1163``), the code
re-fetches ``current_pid = self._get_frontmost_pid_macos()`` and
compares. If the PID changed (user Cmd-Tabbed to a credential prompt
in the ~5ms window), the paste is aborted and a TOCTOU warning is
logged. If the PID is unchanged, the paste proceeds normally.

These tests run on Linux CI (the macOS code path is exercised by
patching ``clip_mod.is_macos`` to return ``True``). The
``AppKit`` import inside ``_get_frontmost_pid_macos`` is satisfied by
installing a fake ``AppKit`` module into ``sys.modules`` via
``patch.dict``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402


def _make_cm() -> ClipboardManager:
    """Build a ClipboardManager with mocked pynput controller.

    Mirrors the ``_make_cm`` helper pattern in
    ``tests/clipboard/win32/test_win32_copy_paste.py`` (TestCopyWindowsBranches): bypass the
    real ``__init__`` (which calls ``_cb._ensure_pynput_imported()`` and
    instantiates ``_cb._Controller()``) and install a ``MagicMock``
    keyboard directly. Sets ``_restore_delay_ms`` so ``paste()``'s
    restore-delay lookup (if a snapshot is provided) doesn't blow up.
    """
    cm = ClipboardManager.__new__(ClipboardManager)
    cm.paste_enabled = True
    cm._keyboard = MagicMock()
    cm._last_paste_time = 0.0  # not rate-limited
    cm._clipboard_seq = 0
    cm._last_copied_text = ""
    cm._clipboard_save_restore_enabled = False  # no snapshot → no restore thread
    cm._restore_delay_ms = 150
    return cm


def _install_fake_appkit(*, pid_side_effect) -> MagicMock:
    """Install a fake ``AppKit`` module into ``sys.modules`` for the TOCTOU test.

    Constructs an ``AppKit.NSWorkspace`` mock chain whose
    ``sharedWorkspace().frontmostApplication().processIdentifier()``
    returns the values in ``pid_side_effect`` in order. This exercises
    the real ``ClipboardManager._get_frontmost_pid_macos`` static
    method (which does ``import AppKit`` lazily inside the function
    body), so the test verifies the full AppKit → PID → TOCTOU compare
    → abort/proceed flow end-to-end.

    Returns the ``appkit`` MagicMock so tests can assert on additional
    attributes if needed.
    """
    front_app = MagicMock(name="frontmostApplication")
    front_app.processIdentifier.side_effect = pid_side_effect

    workspace = MagicMock(name="NSWorkspace")
    workspace.sharedWorkspace.return_value = workspace
    workspace.frontmostApplication.return_value = front_app

    appkit = MagicMock(name="AppKit")
    appkit.NSWorkspace = workspace
    return appkit


class TestMacosToctouPidRecheck:
    """macOS TOCTOU re-check via ``AppKit.NSWorkspace`` mocking.

    These tests close the  coverage gap: the existing TOCTOU tests
    in ``tests/clipboard/win32/test_win32_copy_paste.py`` (TestPasteWindowsBranches) patch
    ``ClipboardManager._get_frontmost_pid_macos`` directly with
    ``side_effect=[4242, 9999]``, bypassing the real
    ``_get_frontmost_pid_macos`` implementation. Here we instead
    install a fake ``AppKit`` module into ``sys.modules`` and configure
    ``NSWorkspace.sharedWorkspace().frontmostApplication()
    .processIdentifier()`` with a ``side_effect`` list, so the
    production ``_get_frontmost_pid_macos`` runs for real (including
    the ``import AppKit`` + ``int(pid)`` coercion at
    ``manager.py:579-592``).
    """

    def test_paste_aborts_when_frontmost_pid_changes(self):
        """Frontmost app PID changes between safety check and Cmd+V → abort.

        Simulates: user runs dictation → safety check captures PID 1234
        (e.g. Terminal.app) → user Cmd-Tabs to a credential prompt
        (PID 5678) in the ~5ms window before the Cmd+V keystroke is
        sent. The TOCTOU re-check at ``manager.py:1153-1162`` detects
        the PID change, logs a TOCTOU warning, and returns ``False``
        WITHOUT sending the keystroke — preventing the dictated text
        (potentially a password) from being pasted into the wrong
        window.
        """
        cm = _make_cm()
        # Two calls to _get_frontmost_pid_macos:
        #   1. safety check (manager.py:1083) → 1234
        #   2. TOCTOU re-check (manager.py:1153) → 5678
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
            # _detect_focused_process returns None on non-Windows, so
            # _is_terminal_process(None) is False → non-terminal macOS
            # branch (manager.py:1150-1163).
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
        """Frontmost app PID unchanged between safety check and Cmd+V → proceed.

        Simulates: user runs dictation → safety check captures PID 1234
        → TOCTOU re-check (immediately before Cmd+V) returns the same
        PID 1234 (user did NOT Cmd-Tab away). The paste proceeds: the
        Cmd+V keystroke is sent and ``paste()`` returns ``True``.

        This test fills the coverage gap left by the existing
        ``test_paste_aborts_on_macos_toctou_pid_change`` (which only
        tests the abort path) and
        ``test_paste_proceeds_when_macos_pid_unavailable`` (which tests
        the fail-open ``None`` path) — neither verifies the happy path
        where the PID is available AND unchanged.
        """
        cm = _make_cm()
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
