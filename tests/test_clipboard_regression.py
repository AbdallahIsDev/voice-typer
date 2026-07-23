"""Regression tests for ADR-0010 §10.3.

These tests pin behavior that previously regressed (or could regress)
across the ADR-0010 migration:

* ``copy()`` still writes the text to the clipboard (the new return
  type is ``ClipboardSnapshot | None``, but the side effect of putting
  the text on the clipboard is unchanged).
* ``paste()`` still sends a paste keystroke when not rate-limited or
  gated.
* Config validation: ``clipboard_save_restore`` and
  ``clipboard_restore_delay_ms`` are accepted by
  ``validate_config_update`` (ADR-0010 §2.11).
* ``HistoryDB.get_latest_text()`` orders by ``id DESC`` and returns
  ``""`` for an empty DB or on read failure.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock pynput / pyperclip at import time so the clipboard module loads
# cleanly on a headless Linux box.
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402
from voice_typer.server.config_validators import (  # noqa: E402
    IPC_CONFIG_ALLOWLIST,
    validate_config_update,
)
from voice_typer.server.history_db import HistoryDB  # noqa: E402

# ---------------------------------------------------------------------------
# Display-env isolation (XS-22)
# ---------------------------------------------------------------------------
# Previously this module mutated the process environment at import time
# (setting DISPLAY=":99" and removing WAYLAND_DISPLAY) to keep clipboard
# code happy on a headless Linux box. Those mutations leaked into the
# entire test session. The autouse fixture below uses ``monkeypatch`` so
# the mutations are auto-restored after each test (no cross-test leak).
# XS-FIX-2 could consolidate this into ``tests/conftest.py`` as a
# session-scoped fixture; for now it is duplicated per-file because
# conftest.py is owned by another sub-agent.


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield

# ---------------------------------------------------------------------------
# ClipboardManager.copy — regression: still writes text to clipboard
# ---------------------------------------------------------------------------


class TestCopyWritesText:
    """``copy()`` still writes the text to the clipboard.

    ADR-0010 §5.2 changed the return type to ``ClipboardSnapshot | None``
    but the *side effect* (text on the clipboard) is unchanged. This
    guards against a future refactor that drops the pyperclip.copy()
    call entirely.
    """

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
        # pyperclip.copy MUST have been called with the text.
        mock_pyper.copy.assert_any_call("hello world")


# ---------------------------------------------------------------------------
# ClipboardManager.paste — regression: still sends a paste keystroke
# ---------------------------------------------------------------------------


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
        # routes through _safe_key_press(_Key.ctrl, "v"). Patch _Key and
        # _Controller so the early-return guard doesn't fire.
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


# ---------------------------------------------------------------------------
# Config validation — ADR-0010 §2.11 (IPC_CONFIG_ALLOWLIST additions)
# ---------------------------------------------------------------------------


class TestClipboardConfigValidation:
    """Config validation accepts the new clipboard keys.

    ADR-0010 §2.11: ``clipboard_save_restore`` and
    ``clipboard_restore_delay_ms`` MUST be in ``IPC_CONFIG_ALLOWLIST``
    so the renderer can toggle them via IPC. These tests are marked
    ``xfail`` until the primary agent adds the entries — the tests
    document the expected behavior and will start passing once the
    production allowlist is updated.
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
            "clipboard_restore_delay_ms entry — pending primary agent "
            "implementation"
        ),
        strict=True,
    )
    def test_clipboard_restore_delay_ms_rejects_out_of_range(self):
        """An out-of-range restore delay is rejected by the validator."""
        # The validator should reject 999999 (well above the documented
        # upper bound). Once the primary agent adds the entry with a
        # bounded int validator, this test will pass.
        validated, errors = validate_config_update(
            {
                "clipboard_restore_delay_ms": 999999,
            }
        )
        assert errors  # must be non-empty (rejection)
        assert "clipboard_restore_delay_ms" not in validated


# ---------------------------------------------------------------------------
# HistoryDB.get_latest_text — ADR-0010 §8.1 / DP6
# ---------------------------------------------------------------------------


class TestGetLatestText:
    """``HistoryDB.get_latest_text`` returns the most-recent transcription.

    ADR-0010 §8.1: ``repaste_last()`` now reads from
    ``history_db.get_latest_text()`` (primary) with an in-memory
    fallback. This pins the contract:
      * orders by ``id DESC`` (the autoincrement PK), not ``timestamp``
        (which can tie within the same second).
      * returns ``""`` for an empty DB.
      * returns ``""`` on read failure (best-effort, never raises).
    """

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
        """If _get_read_conn raises, get_latest_text returns "".

        ADR-0010 §8.1: the method is best-effort and must NEVER raise
        — a read failure degrades gracefully to "".
        """
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
