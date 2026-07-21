"""CR-25: split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

from unittest.mock import MagicMock


class TestAppUndoLastBatching:
    """APP-6: ``undo_last`` must batch backspaces into chunks of ~10
    with a 10ms sleep between chunks, so we don't flood the OS keyboard
    event queue on long transcriptions (>200 chars). Without rate
    limiting, pynput can drop keystrokes silently."""

    def test_undo_last_sleeps_between_chunks(self, app, monkeypatch):
        """For a transcription of 25 chars, undo_last must call
        ``time.sleep(0.01)`` exactly twice (after chunk 1 of 10 and
        after chunk 2 of 10; the last partial chunk of 5 doesn't
        trigger a sleep because it's the final chunk).
        """
        app._last_transcription = "a" * 25
        sleep_calls = []
        monkeypatch.setattr(
            "voice_typer.server.app.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        app.undo_last()

        assert len(sleep_calls) == 2, (
            "APP-6: undo_last must call time.sleep(0.01) once between "
            "each chunk of 10 backspaces (and not after the last chunk). "
            f"For 25 chars (3 chunks) expected 2 sleeps; got {len(sleep_calls)}."
        )
        for s in sleep_calls:
            assert s == 0.01, f"APP-6: undo_last sleep between chunks must be 0.01s (10ms); got {s}"

    def test_undo_last_no_sleep_for_short_text(self, app, monkeypatch):
        """For text shorter than CHUNK_SIZE (10 chars), undo_last must
        NOT sleep at all (single chunk, no inter-chunk pause needed)."""
        app._last_transcription = "hello"  # 5 chars, 1 chunk
        sleep_calls = []
        monkeypatch.setattr(
            "voice_typer.server.app.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        app.undo_last()

        assert sleep_calls == [], (
            "APP-6: undo_last must not sleep when the text fits in a "
            f"single chunk (<=10 chars); got sleeps={sleep_calls}"
        )

    def test_undo_last_clears_last_transcription_after_undo(self, app, monkeypatch):
        """Sanity: undo_last still clears ``_last_transcription`` after
        sending backspaces (so a second undo is a no-op)."""
        app._last_transcription = "hello world"
        monkeypatch.setattr(
            "voice_typer.server.app.time.sleep",
            lambda s: None,
        )

        app.undo_last()

        assert app._last_transcription == "", "undo_last must clear _last_transcription after sending backspaces"


class TestAppUndoLastGraphemeCount:
    """APP-7: ``undo_last`` must count grapheme clusters (NFC-normalized
    code points) rather than raw UTF-16 code units. Combining-character
    sequences like ``é`` written as ``U+0065 U+0301`` are TWO code units
    but ONE user-perceived character — sending two backspaces would
    leave the combining mark behind."""

    def test_undo_counts_nfc_graphemes_not_code_units(self, app, monkeypatch):
        """For a 5-grapheme NFC string that decomposes to 7 code points
        under NFD, undo_last must send exactly 5 backspace pairs
        (press+release), not 7."""
        # 3 a-chars followed by 2 é (NFC) chars.
        text = "aaa" + "é" * 2  # NFC: 5 code points
        import unicodedata

        assert len(unicodedata.normalize("NFC", text)) == 5
        # Each é becomes 2 code points (e + combining acute) under NFD.
        assert len(unicodedata.normalize("NFD", text)) == 7

        app._last_transcription = text
        monkeypatch.setattr(
            "voice_typer.server.app.time.sleep",
            lambda s: None,
        )

        press_calls = []
        # pynput.keyboard is already a MagicMock from the autouse
        # fixture. Replace its Controller attribute with a callable
        # that returns a tracked mock.
        import pynput.keyboard as pk  # type: ignore

        kb_instance = MagicMock()

        def fake_press(key):
            press_calls.append(key)

        kb_instance.press = fake_press
        kb_instance.release = MagicMock()
        pk.Controller = MagicMock(return_value=kb_instance)

        app.undo_last()

        assert len(press_calls) == 5, (
            "APP-7: undo_last must send one backspace per NFC grapheme "
            f"cluster, not per code unit. For 5-grapheme text got "
            f"{len(press_calls)} presses; expected 5."
        )

    def test_undo_handles_empty_string_safely(self, app):
        """When ``_last_transcription`` is empty, undo_last must notify
        the user and return without sending any backspaces."""
        app._last_transcription = ""
        app.tray.notify = MagicMock()

        app.undo_last()

        app.tray.notify.assert_called_once()
        notify_args = app.tray.notify.call_args
        assert "Nothing to undo" in str(notify_args.args), (
            f"undo_last must notify 'Nothing to undo' when there's no transcription to undo; got {notify_args}"
        )
