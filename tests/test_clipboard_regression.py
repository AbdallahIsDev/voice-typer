"""Regression tests for ADR-0010 §10.3."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402
from voice_typer.server.config_validators import (  # noqa: E402
    IPC_CONFIG_ALLOWLIST,
    validate_config_update,
)
from voice_typer.server.history_db import HistoryDB  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


class TestCopyWritesText:
    """``copy()`` still writes the text to the clipboard."""

    def test_copy_still_writes_text_to_clipboard(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0
        cm._clipboard_seq = 0
        cm._last_copied_text = ""
        cm._clipboard_save_restore_enabled = False
        cm._restore_delay_ms = 150

        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "hello world"  # verification match
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
        ):
            cm.copy("hello world")
        mock_pyper.copy.assert_any_call("hello world")


class TestPasteSendsKeystroke:
    """``paste()`` still sends a Ctrl+V (or platform equivalent) keystroke."""

    def test_paste_still_sends_keystroke(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0  # not rate-limited
        cm._clipboard_seq = 0
        cm._clipboard_save_restore_enabled = False
        cm._last_copied_text = "test"
        cm._restore_delay_ms = 150

        # On Linux (is_windows=False) with a mocked _Controller, paste()
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "_Key") as mock_key,
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
            patch.object(clip_mod, "time") as mock_time,
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            mock_key.ctrl = "ctrl_key"
            result = cm.paste()
        assert result is True
        # _safe_key_press pressed ctrl + "v" via the keyboard mock.
        cm._keyboard.press.assert_any_call("ctrl_key")
        cm._keyboard.press.assert_any_call("v")


class TestClipboardConfigValidation:
    """
    Config validation accepts the new clipboard keys.
    ``xfail`` until the primary agent adds the entries, the tests
    """

    @pytest.mark.xfail(
        "clipboard_save_restore" not in IPC_CONFIG_ALLOWLIST,
        reason=(
            "ADR-0010 §2.11: IPC_CONFIG_ALLOWLIST missing "
            "clipboard_save_restore / clipboard_restore_delay_ms entries "
            "— pending primary agent implementation"
        ),
        strict=True,
    )
    def test_clipboard_config_keys_pass_validation(self):
        """Both clipboard keys pass type validation and appear in validated."""
        validated, errors = validate_config_update(
            {
                "clipboard_save_restore": False,
                "clipboard_restore_delay_ms": 250,
            }
        )
        assert errors == []
        assert "clipboard_save_restore" in validated
        assert validated["clipboard_save_restore"] is False
        assert "clipboard_restore_delay_ms" in validated
        assert validated["clipboard_restore_delay_ms"] == 250

    @pytest.mark.xfail(
        "clipboard_restore_delay_ms" not in IPC_CONFIG_ALLOWLIST,
        reason=(
            "ADR-0010 §2.11: IPC_CONFIG_ALLOWLIST missing "
            "clipboard_restore_delay_ms entry, pending primary agent "
            "implementation"
        ),
        strict=True,
    )
    def test_clipboard_restore_delay_ms_rejects_out_of_range(self):
        """An out-of-range restore delay is rejected by the validator."""
        # The validator should reject 999999 (well above the documented
        validated, errors = validate_config_update(
            {
                "clipboard_restore_delay_ms": 999999,
            }
        )
        assert errors  # must be non-empty (rejection)
        assert "clipboard_restore_delay_ms" not in validated


class TestGetLatestText:
    """``HistoryDB.get_latest_text`` returns the most-recent transcription."""

    def test_get_latest_text_orders_by_id(self, tmp_path):
        db = HistoryDB(db_path=tmp_path / "history.db")
        try:
            db.add_transcription("first")
            db.add_transcription("second")
            db.flush()
            # Order by id DESC: "second" was inserted last → highest id.
            assert db.get_latest_text() == "second"
        finally:
            db.close()

    def test_get_latest_text_empty_db_returns_empty_string(self, tmp_path):
        db = HistoryDB(db_path=tmp_path / "history.db")
        try:
            assert db.get_latest_text() == ""
        finally:
            db.close()

    def test_get_latest_text_fallback_on_exception(self, tmp_path):
        """If _get_read_conn raises, get_latest_text returns \"\"."""
        db = HistoryDB(db_path=tmp_path / "history.db")
        try:
            db.add_transcription("first")
            db.flush()
            # Force _get_read_conn to raise on the next call.
            with patch.object(
                db,
                "_get_read_conn",
                side_effect=RuntimeError("conn failed"),
            ):
                result = db.get_latest_text()
            assert result == ""
        finally:
            db.close()
