"""``voice_typer/server/app_undo.py``, the ``UndoRepasteController``"""

from __future__ import annotations

import sys
import types
import unicodedata
from unittest.mock import MagicMock

import pytest
from voice_typer.server.app_undo import UndoRepasteController


def _install_fake_regex(monkeypatch) -> None:
    """Install a minimal ``regex`` module shim in ``sys.modules``."""
    fake = types.ModuleType("regex")

    def findall(pattern: str, text: str, flags: int = 0) -> list[str]:  # noqa: ARG001
        if pattern != r"\X":
            # We only need the \X pattern for these tests. Returning
            return []
        if not text:
            return []
        clusters: list[str] = []
        current = text[0]
        prev_was_zwj = text[0] == "\u200d"
        for ch in text[1:]:
            if ch == "\u200d":
                # Zero-width joiner: extends the current cluster.
                current += ch
                prev_was_zwj = True
            elif prev_was_zwj:
                current += ch
                prev_was_zwj = False
            elif unicodedata.category(ch) in ("Mc", "Mn", "Me"):
                # Combining mark: extends the current cluster.
                current += ch
            elif 0xFE00 <= ord(ch) <= 0xFE0F:
                # Variation selector: extends the current cluster.
                current += ch
            else:
                clusters.append(current)
                current = ch
                prev_was_zwj = False
        clusters.append(current)
        return clusters

    fake.findall = findall  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "regex", fake)


class _StubApp:
    """Minimal duck-typed stub satisfying ``UndoRepasteController``'s"""

    def __init__(self) -> None:
        self._last_transcription: str = ""
        self.history_db = MagicMock()
        self.history_db.get_latest_text = MagicMock(return_value="")
        self.tray = MagicMock()
        self.clipboard = MagicMock()
        self.clipboard._clipboard_seq = 1
        self.clipboard.copy = MagicMock(return_value=None)
        self.clipboard.paste = MagicMock(return_value=True)


@pytest.fixture
def stub_app() -> _StubApp:
    return _StubApp()


@pytest.fixture
def controller(stub_app: _StubApp) -> UndoRepasteController:
    return UndoRepasteController(stub_app)


def _install_pynput_controller_spy(monkeypatch) -> tuple[MagicMock, list[str]]:
    """Install a pynput.keyboard.Controller spy that records every"""
    import pynput.keyboard as pk  # type: ignore

    kb_instance = MagicMock()
    press_calls: list[str] = []
    kb_instance.press = lambda key: press_calls.append(key)
    kb_instance.release = MagicMock()
    pk.Controller = MagicMock(return_value=kb_instance)
    return kb_instance, press_calls


class TestUndoLastGraphemeCount:
    """``undo_last`` MUST send one backspace per USER-PERCEIVED"""

    @pytest.mark.parametrize(
        ("text", "expected_backspaces", "label"),
        [
            ("hello", 5, "ASCII: 5 chars → 5 graphemes"),
            ("👨\u200d👩\u200d👧", 1, "ZWJ family: 5 code points → 1 grapheme"),
            ("你好", 2, "CJK: 2 chars → 2 graphemes"),
        ],
    )
    def test_undo_last_sends_one_backspace_per_grapheme(
        self,
        controller: UndoRepasteController,
        stub_app: _StubApp,
        monkeypatch,
        text: str,
        expected_backspaces: int,
        label: str,
    ) -> None:
        _install_fake_regex(monkeypatch)
        _, press_calls = _install_pynput_controller_spy(monkeypatch)
        monkeypatch.setattr("time.sleep", lambda s: None)

        stub_app._last_transcription = text

        controller.undo_last()

        assert len(press_calls) == expected_backspaces, (
            f"{label}: undo_last must send {expected_backspaces} backspaces "
            f"(one per grapheme), got {len(press_calls)}. "
            f"Text={text!r}, press_calls={press_calls!r}"
        )
        # Every press must be the backspace key (\x08).
        for key in press_calls:
            assert key == "\x08", f"undo_last must press the backspace key (\\x08); got {key!r}"

    def test_undo_last_clears_last_transcription_after_undo(
        self, controller: UndoRepasteController, stub_app: _StubApp, monkeypatch
    ) -> None:
        """Sanity: undo_last clears ``_last_transcription`` after"""
        _install_fake_regex(monkeypatch)
        _install_pynput_controller_spy(monkeypatch)
        monkeypatch.setattr("time.sleep", lambda s: None)

        stub_app._last_transcription = "hello"
        controller.undo_last()

        assert stub_app._last_transcription == "", "undo_last must clear _last_transcription after sending backspaces"


class TestUndoLastChunking:
    """OS keyboard event queue on long transcriptions). The sleep is"""

    def test_11_backspaces_split_into_2_chunks(
        self,
        controller: UndoRepasteController,
        stub_app: _StubApp,
        monkeypatch,
    ) -> None:
        _install_fake_regex(monkeypatch)
        _, press_calls = _install_pynput_controller_spy(monkeypatch)
        sleep_calls: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

        stub_app._last_transcription = "a" * 11

        controller.undo_last()

        # 11 graphemes → 11 backspaces.
        assert len(press_calls) == 11, f"Expected 11 backspaces for 11-char text; got {len(press_calls)}"
        # The final chunk must NOT trigger a sleep.
        assert len(sleep_calls) == 1, (
            f"11 backspaces (2 chunks: 10+1) must trigger exactly 1 sleep "
            f"between chunks; got {len(sleep_calls)}. sleep_calls={sleep_calls}"
        )
        assert sleep_calls[0] == 0.01, f"Inter-chunk sleep must be 0.01s (10ms); got {sleep_calls[0]}"


class TestRepasteLastFallback:
    """``history_db.get_latest_text()`` (primary, survives app restart),"""

    def test_repaste_last_falls_back_to_memory_when_db_raises(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        stub_app._last_transcription = "from memory fallback"
        # DB read raises, the except branch sets text = _last_transcription.
        stub_app.history_db.get_latest_text = MagicMock(side_effect=RuntimeError("sqlite locked"))
        stub_app.clipboard.paste = MagicMock(return_value=True)

        controller.repaste_last()

        # The paste must have been called with the MEMORY fallback text
        paste_kwargs = stub_app.clipboard.paste.call_args.kwargs
        assert paste_kwargs.get("pasted_text") == "from memory fallback", (
            "repaste_last must fall back to _last_transcription when "
            "history_db.get_latest_text() raises; expected "
            f"pasted_text='from memory fallback', got {paste_kwargs!r}"
        )

    def test_repaste_last_shows_no_previous_toast_when_both_empty(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        """When both DB and memory are empty, repaste_last surfaces the"""
        stub_app._last_transcription = ""
        stub_app.history_db.get_latest_text = MagicMock(return_value="")

        controller.repaste_last()

        # The 'no previous' toast must have fired.
        notify_calls = [c.args for c in stub_app.tray.notify.call_args_list]
        assert any("No previous transcription" in str(args) for args in notify_calls), (
            f"repaste_last must notify 'No previous transcription' when both "
            f"DB and memory are empty; got notify_calls={notify_calls!r}"
        )
        # paste() must NOT have been called.
        stub_app.clipboard.paste.assert_not_called()


class TestRepasteLastClipboardCopyFailure:
    """ADR-0010 §7.1: when ``clipboard.copy()`` raises"""

    def test_clipboard_copy_failure_surfaces_clipboard_toast(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        from voice_typer.server.clipboard import ClipboardCopyError

        stub_app._last_transcription = "hello"
        stub_app.history_db.get_latest_text = MagicMock(return_value="hello")
        stub_app.clipboard.copy = MagicMock(side_effect=ClipboardCopyError("clipboard locked by another app"))

        controller.repaste_last()

        notify_calls = [c.args for c in stub_app.tray.notify.call_args_list]
        # The 'clipboard copy failed' toast must have fired.
        assert any("clipboard" in str(args).lower() for args in notify_calls), (
            f"repaste_last must surface a 'clipboard copy failed' toast when "
            f"clipboard.copy() raises ClipboardCopyError; got notify_calls="
            f"{notify_calls!r}"
        )
        # paste() must NOT have been called (the copy failed, so
        stub_app.clipboard.paste.assert_not_called()


class TestRepasteLastPasteKeystrokeFailure:
    """When ``clipboard.paste()`` returns ``False`` (the keystroke was"""

    def test_paste_returns_false_surfaces_blocked_toast(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        stub_app._last_transcription = "hello"
        stub_app.history_db.get_latest_text = MagicMock(return_value="hello")
        stub_app.clipboard.copy = MagicMock(return_value=None)
        stub_app.clipboard.paste = MagicMock(return_value=False)

        controller.repaste_last()

        notify_calls = [c.args for c in stub_app.tray.notify.call_args_list]
        # The 'repaste blocked' toast must have fired (the keystroke
        assert any("blocked" in str(args).lower() or "re-paste" in str(args).lower() for args in notify_calls), (
            f"repaste_last must surface a 'Re-paste was blocked' toast when "
            f"paste() returns False (paste keystroke failed); got "
            f"notify_calls={notify_calls!r}"
        )
        # The 'repaste done' SUCCESS toast must NOT have fired.
        assert not any("re-pasted" in str(args).lower() for args in notify_calls), (
            f"repaste_last must NOT surface the success toast when paste() "
            f"returns False; got notify_calls={notify_calls!r}"
        )
