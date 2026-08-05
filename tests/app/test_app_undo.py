"""Direct unit tests for
``voice_typer/server/app_undo.py`` — the ``UndoRepasteController``
extracted from ``VoiceTyperApp`` (Phase 4.5 spaghetti split).

Previously this module was tested only indirectly via the
``VoiceTyperApp.undo_last`` / ``repaste_last`` delegate methods (see
``tests/app/test_undo_repaste.py`` and
``tests/test_notifications.py::TestRepasteLastSplitsErrors``). Those
tests cover the integration through the delegate; they do NOT pin the
controller's grapheme-counting contract (one backspace per
user-perceived character, not per code point) for the specific inputs
called out in the original review entry — ASCII / ZWJ emoji family / CJK — nor do they
pin the chunking boundary at exactly 10 backspaces.

All heavy dependencies are mocked via the project-wide
``mock_heavy_imports`` autouse fixture (in ``tests/conftest.py``),
which installs ``pynput.keyboard`` as a ``MagicMock`` in
``sys.modules``. The fake ``regex`` module used for grapheme counting
(see ``_install_fake_regex``) is installed per-test via
``monkeypatch.setitem(sys.modules, ...)`` because the ``regex``
package is listed in ``requirements-lock.txt`` (as a transitive dep of
``transformers``) but NOT installed in the test virtualenv — the
production code's ``except ImportError`` fallback would otherwise
kick in and use the code-point count, which is buggy for multi-code-
point graphemes.
"""

from __future__ import annotations

import sys
import types
import unicodedata
from unittest.mock import MagicMock

import pytest
from voice_typer.server.app_undo import UndoRepasteController

# ── Fake ``regex`` module (grapheme cluster counter) ──────────────────


def _install_fake_regex(monkeypatch) -> None:
    """Install a minimal ``regex`` module shim in ``sys.modules``.

    The shim implements ONLY the ``\\X`` grapheme-cluster pattern (the
    one pattern ``UndoRepasteController.undo_last`` uses). It is NOT a
    general regex engine.

    The real ``regex`` package (listed in ``requirements-lock.txt`` as
    a transitive dep of ``transformers``) is not installed in the test
    virtualenv. Production code falls back to ``len(text)`` (code-point
    count) on ``ImportError``, which is buggy for multi-code-point
    graphemes (e.g. ZWJ-joined emoji families). Installing this shim
    exercises the grapheme-counting code path that ships in production
    when ``regex`` IS available.
    """
    fake = types.ModuleType("regex")

    def findall(pattern: str, text: str, flags: int = 0) -> list[str]:  # noqa: ARG001
        if pattern != r"\X":
            # We only need the \X pattern for these tests. Returning
            # an empty list for other patterns is safe — undo_last
            # only calls findall(r"\X", text).
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
                # The character immediately after a ZWJ joins the
                # current cluster (the ZWJ glues the preceding and
                # following base chars into one grapheme).
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


# ── Stub app factory ──────────────────────────────────────────────────


class _StubApp:
    """Minimal duck-typed stub satisfying ``UndoRepasteController``'s
    attribute surface.

    Mirrors the attribute surface enumerated in the
    ``UndoRepasteController`` class docstring:
      - ``_last_transcription`` (memory fallback for repaste).
      - ``history_db.get_latest_text()`` (primary repaste source).
      - ``tray.notify(title, message)`` (localized toasts).
      - ``clipboard.copy(text)`` / ``clipboard.paste(snapshot, ...)``.
      - ``clipboard._clipboard_seq`` (CRIT-3 per-request seq).
    """

    def __init__(self) -> None:
        self._last_transcription: str = ""
        self.history_db = MagicMock()
        self.history_db.get_latest_text = MagicMock(return_value="")
        self.tray = MagicMock()
        self.clipboard = MagicMock()
        self.clipboard._clipboard_seq = 1
        # copy() returns None when save/restore is disabled (matches
        # the real ClipboardManager.copy contract).
        self.clipboard.copy = MagicMock(return_value=None)
        self.clipboard.paste = MagicMock(return_value=True)


@pytest.fixture
def stub_app() -> _StubApp:
    return _StubApp()


@pytest.fixture
def controller(stub_app: _StubApp) -> UndoRepasteController:
    return UndoRepasteController(stub_app)


def _install_pynput_controller_spy(monkeypatch) -> tuple[MagicMock, list[str]]:
    """Install a pynput.keyboard.Controller spy that records every
    ``press("\x08")`` call.

    ``mock_heavy_imports`` already installs ``pynput.keyboard`` as a
    ``MagicMock`` in ``sys.modules``. ``undo_last`` does
    ``import pynput.keyboard as _pk_keyboard`` at call time, then
    ``_pk_keyboard.Controller()`` — so we set the ``Controller``
    attribute on the already-mocked module to a callable that returns
    our spy instance.

    Returns ``(kb_instance, press_calls)`` where ``press_calls`` is
    the list that will accumulate the backspace key strings.
    """
    import pynput.keyboard as pk  # type: ignore

    kb_instance = MagicMock()
    press_calls: list[str] = []
    kb_instance.press = lambda key: press_calls.append(key)
    kb_instance.release = MagicMock()
    pk.Controller = MagicMock(return_value=kb_instance)
    return kb_instance, press_calls


# ── (a, b, c) grapheme-counting contract ──────────────────────────────


class TestUndoLastGraphemeCount:
    """``undo_last`` MUST send one backspace per USER-PERCEIVED
    character (grapheme cluster), NOT per Unicode code point.

    Pre-fix: ``len(text)`` returned the code-point count, so a ZWJ-
    joined emoji family (5 code points, 1 grapheme) was undone with 5
    backspaces — the extra 4 deleted the user's PREVIOUS text.

    Cases:
      (a) ``"hello"``        → 5 backspaces  (5 ASCII graphemes)
      (b) ``"👨‍👩‍👧"``        → 1 backspace   (ZWJ family, 1 grapheme)
      (c) ``"你好"``          → 2 backspaces  (2 CJK graphemes)
    """

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
            f"{label}: undo_last must send {expected_backspaces} backspace(s) "
            f"(one per grapheme), got {len(press_calls)}. "
            f"Text={text!r}, press_calls={press_calls!r}"
        )
        # Every press must be the backspace key (\x08).
        for key in press_calls:
            assert key == "\x08", f"undo_last must press the backspace key (\\x08); got {key!r}"

    def test_undo_last_clears_last_transcription_after_undo(
        self, controller: UndoRepasteController, stub_app: _StubApp, monkeypatch
    ) -> None:
        """Sanity: undo_last clears ``_last_transcription`` after
        sending backspaces so a second undo is a no-op (surfaces
        'Nothing to undo' toast)."""
        _install_fake_regex(monkeypatch)
        _install_pynput_controller_spy(monkeypatch)
        monkeypatch.setattr("time.sleep", lambda s: None)

        stub_app._last_transcription = "hello"
        controller.undo_last()

        assert stub_app._last_transcription == "", "undo_last must clear _last_transcription after sending backspaces"


# ── (d) chunking: 11 backspaces → 2 chunks (10 + 1) ───────────────────


class TestUndoLastChunking:
    """``undo_last`` MUST batch backspaces into chunks of 10 with a
    10ms ``time.sleep(0.01)`` between chunks (so we don't flood the
    OS keyboard event queue on long transcriptions). The sleep is
    omitted after the FINAL chunk (no subsequent chunk to space it
    from).

    Case (d): 11 backspaces → 2 chunks (10 + 1).
      • chunk 1: 10 backspaces → sleep
      • chunk 2: 1 backspace  → NO sleep (final chunk)
    Expected: exactly 1 sleep call.
    """

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
        # 2 chunks (10 + 1) → exactly 1 sleep BETWEEN them.
        # The final chunk must NOT trigger a sleep.
        assert len(sleep_calls) == 1, (
            f"11 backspaces (2 chunks: 10+1) must trigger exactly 1 sleep "
            f"between chunks; got {len(sleep_calls)}. sleep_calls={sleep_calls}"
        )
        assert sleep_calls[0] == 0.01, f"Inter-chunk sleep must be 0.01s (10ms); got {sleep_calls[0]}"


# ── (e) repaste_last falls back to _last_transcription ────────────────


class TestRepasteLastFallback:
    """ADR-0010 §7.1 / DP6: ``repaste_last`` reads from
    ``history_db.get_latest_text()`` (primary — survives app restart),
    falling back to ``self._app._last_transcription`` (memory) when
    the DB read FAILS (raises).

    Note on acceptance-criterion interpretation: the literal wording
    "returns None → falls back" does not match the production code —
    the fallback fires in the ``except`` branch (when
    ``get_latest_text()`` RAISES), not when it returns a falsy value.
    A ``None`` return is treated as "DB empty" and surfaces the
    'no previous transcription' toast (NOT the memory fallback).
    This test pins the actual code path: the memory fallback fires
    when the DB read raises.
    """

    def test_repaste_last_falls_back_to_memory_when_db_raises(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        stub_app._last_transcription = "from memory fallback"
        # DB read raises — the except branch sets text = _last_transcription.
        stub_app.history_db.get_latest_text = MagicMock(side_effect=RuntimeError("sqlite locked"))
        # paste() must return True so the "repaste_done" toast fires.
        stub_app.clipboard.paste = MagicMock(return_value=True)

        controller.repaste_last()

        # The paste must have been called with the MEMORY fallback text
        # (not an empty string from the DB).
        paste_kwargs = stub_app.clipboard.paste.call_args.kwargs
        assert paste_kwargs.get("pasted_text") == "from memory fallback", (
            "repaste_last must fall back to _last_transcription when "
            "history_db.get_latest_text() raises; expected "
            f"pasted_text='from memory fallback', got {paste_kwargs!r}"
        )

    def test_repaste_last_shows_no_previous_toast_when_both_empty(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        """When both DB and memory are empty, repaste_last surfaces the
        'no previous transcription' toast and does NOT call paste()."""
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


# ── (f) clipboard-copy raises → "clipboard copy failed" toast ─────────


class TestRepasteLastClipboardCopyFailure:
    """ADR-0010 §7.1: when ``clipboard.copy()`` raises
    ``ClipboardCopyError``, repaste_last MUST surface the
    'clipboard copy failed' toast and NOT call ``paste()``.

    Pre-fix: a single try/except collapsed clipboard-copy failures
    and paste-keystroke failures into one generic toast. The fix
    splits them so the user knows which step failed.
    """

    def test_clipboard_copy_failure_surfaces_clipboard_toast(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        from voice_typer.server.clipboard import ClipboardCopyError

        stub_app._last_transcription = "hello"
        stub_app.history_db.get_latest_text = MagicMock(return_value="hello")
        # copy() raises ClipboardCopyError on genuine copy failure.
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
        # there's nothing on the clipboard to paste).
        stub_app.clipboard.paste.assert_not_called()


# ── (g) paste-keystroke failure → "paste keystroke failed" toast ──────
#
# Note on acceptance-criterion interpretation: the literal wording
# "paste-keystroke raises → 'paste keystroke failed' toast" does not
# match the production code — ``app.clipboard.paste()`` does NOT raise
# on failure; it returns ``False`` (the paste was skipped / blocked /
# rate-limited). The user-facing toast that fires in that case is
# ``notify.app.repaste_blocked`` ("Re-paste was blocked (unsafe
# target or rate-limited). ...") — that IS the 'paste keystroke
# failed' toast (the keystroke did not land). This test pins that
# contract.


class TestRepasteLastPasteKeystrokeFailure:
    """When ``clipboard.paste()`` returns ``False`` (the keystroke was
    skipped / blocked / rate-limited — paste does NOT raise),
    repaste_last MUST surface the 'repaste blocked' toast (the
    'paste keystroke failed' toast) instead of the 'repaste done'
    success toast.
    """

    def test_paste_returns_false_surfaces_blocked_toast(
        self, controller: UndoRepasteController, stub_app: _StubApp
    ) -> None:
        stub_app._last_transcription = "hello"
        stub_app.history_db.get_latest_text = MagicMock(return_value="hello")
        # copy() returns a snapshot (None when save/restore disabled —
        # matches the real ClipboardManager.copy contract).
        stub_app.clipboard.copy = MagicMock(return_value=None)
        # paste() returns False (keystroke was skipped / blocked /
        # rate-limited — paste does NOT raise).
        stub_app.clipboard.paste = MagicMock(return_value=False)

        controller.repaste_last()

        notify_calls = [c.args for c in stub_app.tray.notify.call_args_list]
        # The 'repaste blocked' toast must have fired (the keystroke
        # did not land). The message contains "blocked" or "Re-paste".
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
