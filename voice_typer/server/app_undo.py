"""DT-25 (Phase 4.5 spaghetti split): UndoRepasteController — extracted
from VoiceTyperApp.

Owns the undo / repaste side effects of ``VoiceTyperApp``:

    - ``undo_last`` — UX-003: send N backspaces (grapheme-cluster
      counted, CR-016) to undo the last transcription. CR-017:
      keyboard-ownership check mirrors ``_cancel_dictation`` so the
      backspaces don't land in the frontend HotkeyPicker capture field.
      APP-6: backspaces are batched in chunks of 10 with a 10ms
      ``time.sleep(0.01)`` between chunks so we don't flood the OS
      keyboard event queue on long transcriptions.
    - ``repaste_last`` — ADR-0010 §7.1 / DP6 / DP4: re-paste the last
      transcription from ``history_db.get_latest_text()`` (primary —
      survives app restart) with a ``self._last_transcription`` memory
      fallback. ERR-018: clipboard-copy failures and paste-keystroke
      failures are split into separate toasts so the user knows which
      step failed.

Previously both lived on ``VoiceTyperApp`` as ~166 LOC across 2
methods (``undo_last`` 90 LOC, ``repaste_last`` 76 LOC). The
behaviour is preserved verbatim — only the class boundary moved.
``VoiceTyperApp`` keeps thin delegate methods so tray menu callbacks,
hotkey handlers, and tests calling ``app.undo_last()`` /
``app.repaste_last()`` directly keep working unchanged.

A note on logging: this module uses
``logging.getLogger("voice_typer.server.app")`` rather than the
conventional ``__name__`` so caplog captures in tests (if any are
added later) route to the same logger as the original VoiceTyperApp
methods.

A note on monkeypatching (mirrors the convention in
``shutdown_controller.py`` and ``settings_controller.py``): tests
patch ``voice_typer.server.app.time.sleep`` and the autouse
``mock_heavy_imports`` fixture in ``tests/conftest.py`` installs
``pynput.keyboard`` as a MagicMock in ``sys.modules``. Because
``voice_typer.server.app.time`` IS the global ``time`` module, the
``time.sleep`` patch propagates to this module's calls too. The
``pynput.keyboard`` mock propagates because the ``import pynput.keyboard
as _pk_keyboard`` statement (preserved verbatim from the original
``VoiceTyperApp.undo_last`` body) resolves via ``sys.modules`` at call
time — the test's late override ``pk.Controller = MagicMock(...)``
lands on the same mocked module this module picks up.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    # ``app_undo`` via the ``UndoRepasteController(self)`` call inside
    # ``VoiceTyperApp.__init__``). At runtime, ``app`` is whatever
    # object was passed to ``__init__`` (always a ``VoiceTyperApp`` in
    # production, but tests pass mocks that satisfy the same duck-typed
    # surface).
    pass

# Use the same logger name as ``voice_typer.server.app`` so any future
# caplog captures (and existing log-analysis tooling) keep working
# without per-module filter changes.
log = logging.getLogger("voice_typer.server.app")


class UndoRepasteController:
    """DT-25: owns undo_last / repaste_last side effects.

    RW-9 Phase 4.5: extracted from ``VoiceTyperApp``. The app passes
    itself (``app``) so ``UndoRepasteController`` can:

    - Read ``app._last_transcription`` (memory fallback for repaste;
      cleared after undo).
    - Call ``app.history_db.get_latest_text()`` (primary repaste
      source — survives app restart).
    - Call ``app.tray.notify(APP_NAME, i18n.t(...))`` (localized
      toasts for success / failure / nothing-to-undo states).
    - Call ``app.clipboard.copy(text)`` /
      ``app.clipboard.paste(snapshot, ...)`` (snapshot/restore
      mechanism shared with auto-paste).
    - Read ``app.clipboard._clipboard_seq`` (CRIT-3 per-request
      sequence number threaded into ``paste()`` so a concurrent
      ``copy()`` can't clobber the seq validated in ``paste()``).
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    # ─── Repaste ──────────────────────────────────────────────────────

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey).

        ADR-0010 §7.1 / DP6 / DP4.

        Reads from ``history_db.get_latest_text()`` (primary — survives
        app restart), falling back to ``self._app._last_transcription``
        if the DB read fails. Uses the same snapshot/restore mechanism
        as auto-paste so the user's clipboard is preserved.

        ``paste(force=True)`` bypasses the ``paste_enabled`` gate
        (§2.12) so a manual repaste works regardless of the auto-paste
        (``paste_on_stop``) setting.

        ERR-018: previously a single try/except collapsed clipboard-copy
        failures and paste-keystroke failures into one generic toast.
        We now split them so the user knows which step failed.

        Fallback chain:
          1. ``history_db.get_latest_text()``  (primary — survives restart)
          2. ``self._app._last_transcription``  (fallback if DB read fails)
          3. "No previous transcription" toast  (both empty)

        DT-25: body lives here now; ``VoiceTyperApp.repaste_last`` is a
        one-line delegate.
        """
        app = self._app
        # ① READ FROM DB (primary — survives restart)
        text = ""
        try:
            text = app.history_db.get_latest_text()
        except Exception as e:
            log.warning("[REPASTE] DB read failed, falling back to memory: %s", e)
            text = app._last_transcription

        if not text:
            # i18n key for localized notification.
            app.tray.notify(APP_NAME, i18n.t("notify.app.repaste_no_previous"))
            return

        # ② COPY (snapshot + empty + pyperclip.copy + verify).
        # copy() returns None when save/restore is disabled; it raises
        # ClipboardCopyError only on a genuine copy failure.
        snapshot = None
        try:
            snapshot = app.clipboard.copy(text)
            pasted_seq = app.clipboard._clipboard_seq
        except ClipboardCopyError as e:
            log.warning("[REPASTE] Clipboard copy failed: %s", e)
            # i18n key for localized notification.
            app.tray.notify(
                APP_NAME,
                i18n.t("notify.app.repaste_copy_failed"),
            )
            return

        # ③ PASTE (keystroke + delayed restore scheduled inside paste()).
        # paste() schedules the restore of the user's ORIGINAL clipboard
        # at its top, before any early return (DP1). It returns False
        # (does not raise) when the keystroke is skipped/blocked/rate-
        # limited — and the restore is still scheduled. We therefore do
        # NOT call restore_now() here: that would be redundant and would
        # remove the transcription from the clipboard. The transcription
        # is safely stored in the DB. ``force=True`` bypasses the
        # ``paste_enabled`` gate (§2.12) so a manual repaste works
        # regardless of the auto-paste (``paste_on_stop``) setting.
        # pasted_seq is threaded per-request (CRIT-3) so a concurrent
        # copy() can't clobber the seq validated in paste().
        pasted = app.clipboard.paste(snapshot, pasted_text=text, force=True, pasted_seq=pasted_seq)
        if pasted:
            log.info("[REPASTE] Repasted transcription (%d chars)", len(text))
            # Use the i18n key so the tray notification renders in the
            # user's selected UI locale.
            app.tray.notify(APP_NAME, i18n.t("notify.app.repaste_done"))
        else:
            log.warning("[REPASTE] Paste keystroke was skipped/blocked")
            app.tray.notify(
                APP_NAME,
                i18n.t("notify.app.repaste_blocked"),
            )

    # ─── Undo ─────────────────────────────────────────────────────────

    def undo_last(self) -> None:
        """UX-003: Undo last transcription by sending backspace keystrokes.

        Sends one backspace per character in the last transcription.
        Works by simulating keyboard input via the hotkey backend's
        keyboard controller (pynput on all platforms).

        CR-016 (IMPROVE-mode run, 2026-07-21): count grapheme clusters
        (not Unicode code points) so emoji and combining-character
        sequences are deleted with a single backspace (matching the
        OS's grapheme-level delete behavior). Pre-fix, ``len(text)``
        returned 14 for
        ``"Hello \\U0001f468\\u200d\\U0001f469\\u200d\\U0001f467 world"``
        (1 grapheme = 5 code points ZWJ-joined) but the OS only needs
        11 backspaces — the extra 3 deleted the user's PREVIOUS text.

        CR-017 (IMPROVE-mode run, 2026-07-21): check keyboard_ownership
        before sending backspaces (mirror ``_cancel_dictation``).
        Pre-fix, if the frontend HotkeyPicker was in capture mode, the
        backspaces landed in the capture field instead of undoing the
        transcription.

        APP-6: batch backspaces into chunks of ``_undo_chunk_size``
        (10) with a 10ms ``time.sleep(0.01)`` between chunks so we
        don't flood the OS keyboard event queue on long transcriptions
        (>200 chars). Without rate limiting, pynput can drop keystrokes
        silently. The sleep is omitted after the final (possibly
        partial) chunk — there's no subsequent chunk to space it from.

        DT-25: body lives here now; ``VoiceTyperApp.undo_last`` is a
        one-line delegate.
        """
        app = self._app
        # CR-017: keyboard-ownership check — mirror _cancel_dictation.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[UNDO] skipping undo — frontend hotkey capture active")
                return
        except Exception:
            log.debug("[UNDO] keyboard ownership check failed", exc_info=True)
        if not app._last_transcription:
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_nothing"))
            return
        text = app._last_transcription
        # CR-016: count grapheme clusters using the regex library
        # (already in deps for text_cleanup.py). ``\X`` matches a
        # single user-perceived grapheme (handles ZWJ emoji, combining
        # marks, etc.).
        try:
            import regex as _regex

            char_count = len(_regex.findall(r"\X", text))
        except ImportError:
            # Fallback: code-point count (pre-CR-016 behavior — buggy
            # for multi-code-point graphemes but at least doesn't
            # crash).
            char_count = len(text)
        log.info("[UNDO] Undoing last transcription (%d chars)", char_count)
        try:
            # CR-016 (cont.): use ``import pynput.keyboard as _pk_keyboard``
            # + ``_pk_keyboard.Controller()`` (module-attribute access at
            # call time) rather than ``from pynput.keyboard import
            # Controller`` (which binds the name at import time). Both
            # are functionally identical in production, but the
            # module-attribute form makes the existing test mock setup
            # work: tests do
            # ``pk.Controller = MagicMock(return_value=kb_instance)``
            # AFTER the autouse fixture installs the ``pynput.keyboard``
            # MagicMock, and the module-attribute access picks up that
            # late assignment while the ``from`` import may bind to the
            # auto-generated MagicMock child before the test's override
            # lands.
            import pynput.keyboard as _pk_keyboard

            kb = _pk_keyboard.Controller()
            # Select all text in the current field first (Ctrl+A), then
            # Delete — this is more reliable than sending N backspaces
            # because it handles multi-line text and doesn't leave
            # partial characters.
            # However, Ctrl+A selects ALL text in the field, which may
            # be more than just our transcription.  So we send N
            # backspaces instead — this is the standard "undo paste"
            # behavior.
            #
            # APP-6: batch backspaces into chunks of
            # ``_undo_chunk_size`` (10) with a 10ms
            # ``time.sleep(0.01)`` between chunks so we don't flood the
            # OS keyboard event queue on long transcriptions (>200
            # chars). Without rate limiting, pynput can drop keystrokes
            # silently. The sleep is omitted after the final (possibly
            # partial) chunk — there's no subsequent chunk to space it
            # from.
            _undo_chunk_size = 10
            for _i in range(char_count):
                kb.press("\x08")  # Backspace
                kb.release("\x08")
                if (_i + 1) % _undo_chunk_size == 0 and (_i + 1) < char_count:
                    time.sleep(0.01)
            app._last_transcription = ""
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_done", char_count=char_count))
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_no_pynput"))
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            # XZ-EH-017: do NOT interpolate the raw exception into the
            # user-facing tray notification — pynput failures include
            # OS-level error strings, X11 paths, and AT-SPI addresses
            # (PII / sensitive environment details). The raw ``str(e)``
            # is already captured in ``log.warning`` above for
            # operator-side diagnostics. The user-facing notification
            # uses only the exception class name (e.g. ``OSError``),
            # which is enough to triage without leaking PII.
            app.tray.notify(APP_NAME, i18n.t("notify.app.undo_failed", error=type(e).__name__))
