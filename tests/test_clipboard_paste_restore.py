"""Regression test for ``_delayed_restore`` signature mismatch.

Before the fix, ``paste()`` spawned the daemon thread with 4 positional
arguments::

    threading.Thread(
        target=self._delayed_restore,
        args=(snapshot, expected, delay, _pending_entry),
        ...
    ).start()

but ``_delayed_restore(self, snapshot, pasted_text, delay)`` accepted
only 3. Every paste() invocation silently killed the daemon thread
with::

    TypeError: _delayed_restore() takes 4 positional arguments but 5
    were given

The user's original clipboard content was NEVER restored by the daemon
thread, and the entry appended to ``_pending_restores`` was NEVER
removed — accumulating one ``ClipboardSnapshot`` per paste for the
entire session (memory leak + atexit double-restore).

These tests drive the production ``paste()`` path end-to-end (NOT
direct ``_delayed_restore`` calls), mocking only the platform-specific
keystroke + clipboard-read primitives so they run on a headless Linux
box. They assert:

* (a) the pending entry is removed from ``_pending_restores`` after
  the daemon thread completes — proving the signature mismatch is gone
  AND the cleanup runs.
* (b) the original clipboard content is restored via
  ``snapshot.restore()`` when the clipboard still holds the pasted
  text.
* (c) the entry is removed even when the defensive check skips the
  restore (clipboard changed) — proving the ``finally`` block fires on
  the "skip" path, not just the "restore" path.
* (d) the entry is removed even when ``snapshot.restore()`` raises —
  proving the ``finally`` block fires on the exception path.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402

from tests.fixtures.clipboard_helpers import make_clipboard_manager, make_clipboard_snapshot  # noqa: E402

# ---------------------------------------------------------------------------
# Display-env isolation
# ---------------------------------------------------------------------------
# Previously this module mutated the process environment at import time
# (setting DISPLAY=":99" and removing WAYLAND_DISPLAY) to keep clipboard
# code happy on a headless Linux box. Those mutations leaked into the
# entire test session. The autouse fixture below uses ``monkeypatch`` so
# the mutations are auto-restored after each test (no cross-test leak).
# could consolidate this into ``tests/conftest.py`` as a
# session-scoped fixture; for now it is duplicated per-file because
# conftest.py is owned by another sub-agent.


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_pending_restores() -> None:
    """Clear the module-level ``_pending_restores`` list (test isolation)."""
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()


def _patch_paste_plumbing():
    """Context manager bundle that takes ``paste()`` past every guard
    on a headless Linux box so the keystroke branch runs and returns
    True.

    Mocks:
    * ``is_windows`` / ``is_macos`` → False (forces the Linux pynput
      branch).
    * ``_Controller`` / ``_Key`` → MagicMocks so the
      ``_Controller is None`` early-return guard does not fire.
    * ``ClipboardManager._is_safe_paste_target`` → True.
    * ``ClipboardManager._detect_focused_process`` → None (no
      terminal / rich-editor special-casing).
    * ``ClipboardManager._release_stuck_modifiers`` → noop.
    * ``ClipboardManager._safe_key_press`` → noop (the actual
      keystroke primitive).
    """
    return (
        patch.object(clip_mod, "is_windows", return_value=False),
        patch.object(clip_mod, "is_macos", return_value=False),
        patch.object(clip_mod, "_Controller", MagicMock()),
        patch.object(clip_mod, "_Key", MagicMock()),
        patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
        patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        patch.object(ClipboardManager, "_release_stuck_modifiers", lambda self: None),
        patch.object(ClipboardManager, "_safe_key_press", lambda self, *a, **kw: None),
    )


def _enter_all(context_managers):
    """Enter a list of context managers and return a list of exits."""
    exits = []
    for cm in context_managers:
        cm.__enter__()
        exits.append(cm)
    return exits


def _exit_all(exits):
    """Exit a list of context managers in reverse order."""
    for cm in reversed(exits):
        cm.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _isolate_pending_restores():
    """Ensure each test starts and ends with an empty _pending_restores list."""
    _drain_pending_restores()
    yield
    _drain_pending_restores()


# ===========================================================================
# regression tests — drives the production paste() path end-to-end
# ===========================================================================


class TestPasteRestoresAndUnregisters:
    """End-to-end ``paste()`` tests proving  is fixed.

    Each test:
    1. Calls the production ``paste(snapshot=...)`` (NOT
       ``_delayed_restore`` directly).
    2. Waits for the daemon thread to finish.
    3. Asserts the pending entry was removed from ``_pending_restores``
       ( root cause) AND the original snapshot was restored
       ( user-visible symptom).
    """

    def test_paste_restores_clipboard_and_removes_pending_entry(self):
        """paste(snapshot=...) restores the original clipboard content
        and removes its entry from ``_pending_restores`` once the daemon
        thread finishes.

        Before  fix: the daemon thread died with TypeError, the
        snapshot was never restored, and the entry stayed in
        ``_pending_restores`` forever.
        """
        cm = make_clipboard_manager(restore_delay_ms=10)  # 10ms so the test is fast
        snap = make_clipboard_snapshot()
        mock_pyper = MagicMock()
        # Clipboard still has the pasted text → restore path runs.
        mock_pyper.paste.return_value = "the new dictation"
        exits = _enter_all(_patch_paste_plumbing())
        try:
            with (
                patch.object(clip_mod, "pyperclip", mock_pyper),
                patch.object(clip_mod, "_paste_from_clipboard", return_value="the new dictation"),
                patch.object(snap, "restore", return_value=True) as mock_restore,
                patch.object(clip_mod, "log"),
            ):
                result = cm.paste(snapshot=snap, pasted_text="the new dictation")
                assert result is True, "paste() should report keystroke sent"

                # Wait for the daemon thread to finish. The restore delay
                # is 10ms; give a generous 2s ceiling so CI flakes don't
                # occur on a slow box.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    with clip_mod._pending_restores_lock:
                        if not clip_mod._pending_restores:
                            break
                    time.sleep(0.005)
        finally:
            _exit_all(exits)

        # (a) : pending entry removed after daemon thread completes.
        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == [], (
                f"_pending_restores should be empty after daemon thread completed; got {clip_mod._pending_restores!r}"
            )
        # (b) Original clipboard content was restored.
        mock_restore.assert_called_once()

    def test_paste_removes_pending_entry_even_when_restore_skipped(self):
        """If the user copied something else during the restore delay,
        ``_delayed_restore`` defensively skips ``snapshot.restore()`` —
        but it MUST still remove the pending entry.

        Before  fix: the daemon thread died with TypeError BEFORE
        the defensive check, so the entry leaked.
        After  fix: the ``finally`` block fires on the skip path
        too.
        """
        cm = make_clipboard_manager(restore_delay_ms=10)
        snap = make_clipboard_snapshot()
        mock_pyper = MagicMock()
        # Clipboard changed (user copied something else) → skip restore.
        mock_pyper.paste.return_value = "user copied different text"
        exits = _enter_all(_patch_paste_plumbing())
        try:
            with (
                patch.object(clip_mod, "pyperclip", mock_pyper),
                patch.object(
                    clip_mod,
                    "_paste_from_clipboard",
                    return_value="user copied different text",
                ),
                patch.object(snap, "restore", return_value=True) as mock_restore,
                patch.object(clip_mod, "log"),
            ):
                result = cm.paste(snapshot=snap, pasted_text="the dictation")
                assert result is True

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    with clip_mod._pending_restores_lock:
                        if not clip_mod._pending_restores:
                            break
                    time.sleep(0.005)
        finally:
            _exit_all(exits)

        # (a) Entry removed even though restore was skipped.
        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == []
        # (b) Restore was NOT called (defensive skip).
        mock_restore.assert_not_called()

    def test_paste_removes_pending_entry_even_when_restore_raises(self):
        """If ``snapshot.restore()`` raises, the exception is logged but
        the pending entry is STILL removed (``finally`` block).

        Before  fix: the daemon thread died with TypeError BEFORE
        even attempting restore.
        After  fix: the ``finally`` block fires on the exception
        path too.
        """
        cm = make_clipboard_manager(restore_delay_ms=10)
        snap = make_clipboard_snapshot()
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "the dictation"
        exits = _enter_all(_patch_paste_plumbing())
        try:
            with (
                patch.object(clip_mod, "pyperclip", mock_pyper),
                patch.object(clip_mod, "_paste_from_clipboard", return_value="the dictation"),
                patch.object(snap, "restore", side_effect=RuntimeError("restore blew up")),
                patch.object(clip_mod, "log"),
            ):
                result = cm.paste(snapshot=snap, pasted_text="the dictation")
                assert result is True

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    with clip_mod._pending_restores_lock:
                        if not clip_mod._pending_restores:
                            break
                    time.sleep(0.005)
        finally:
            _exit_all(exits)

        # (a) Entry removed even though restore() raised.
        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == []

    def test_paste_does_not_leak_entries_across_many_invocations(self):
        """Stress test simulating a heavy-dictation user (the
        memory-leak symptom called out in 's impact statement).

        Runs ``paste(snapshot=...)`` 25 times back-to-back. After all
        daemon threads finish, ``_pending_restores`` should be empty.

        Before  fix: this would have left 25 entries lingering
        (one per paste) for the lifetime of the process.
        """
        cm = make_clipboard_manager(restore_delay_ms=5)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "dictation"
        exits = _enter_all(_patch_paste_plumbing())
        snapshots = []
        try:
            with (
                patch.object(clip_mod, "pyperclip", mock_pyper),
                patch.object(clip_mod, "_paste_from_clipboard", return_value="dictation"),
                patch.object(clip_mod, "log"),
            ):
                for _ in range(25):
                    snap = make_clipboard_snapshot()
                    snapshots.append(snap)
                    with patch.object(snap, "restore", return_value=True):
                        cm.paste(snapshot=snap, pasted_text="dictation")

                # Wait for ALL daemon threads to finish.
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    with clip_mod._pending_restores_lock:
                        if not clip_mod._pending_restores:
                            break
                    time.sleep(0.01)
        finally:
            _exit_all(exits)

        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == [], (
                f"Heavy-dictation leak: expected empty _pending_restores "
                f"after 25 pastes; got {len(clip_mod._pending_restores)} "
                f"lingering entries"
            )

    def test_paste_with_none_snapshot_does_not_touch_pending_restores(self):
        """Sanity: when ``snapshot is None``, paste() must NOT append
        anything to ``_pending_restores`` (no borrow → no restore
        needed). This documents the pre-existing branch and ensures
        our fix doesn't accidentally register a None entry.
        """
        cm = make_clipboard_manager(restore_delay_ms=10)
        exits = _enter_all(_patch_paste_plumbing())
        try:
            with patch.object(clip_mod, "log"):
                cm.paste(snapshot=None, pasted_text="x")
        finally:
            _exit_all(exits)

        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == []


# ===========================================================================
# Direct unit test proving the signature accepts the 4-arg call
# (defense-in-depth — the end-to-end tests above already cover this)
# ===========================================================================


class TestDelayedRestoreSignature:
    """Directly verify ``_delayed_restore`` accepts the 4 positional args
    that ``paste()`` passes (snapshot, pasted_text, delay, pending_entry).

    This is a focused regression for the literal TypeError that
    reported. The end-to-end tests above exercise the production
    ``paste()`` path, but if a future refactor inlines the call
    differently, this test will still catch a signature drift.
    """

    def test_delayed_restore_accepts_pending_entry_kwarg(self):
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = ("sentinel-entry",)
        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="x"),
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            # Pre-register the entry so we can assert removal.
            with clip_mod._pending_restores_lock:
                clip_mod._pending_restores.append(entry)
            cm._delayed_restore(snap, "x", 0.0, pending_entry=entry)
        mock_restore.assert_called_once()
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores

    def test_delayed_restore_accepts_pending_entry_positional(self):
        """The production call site at paste() line 1018-1020 passes
        ``pending_entry`` positionally. Verify that path works too."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = ("sentinel-entry-positional",)
        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="x"),
            patch.object(snap, "restore", return_value=True),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            with clip_mod._pending_restores_lock:
                clip_mod._pending_restores.append(entry)
            # Positional — exactly what paste() does.
            cm._delayed_restore(snap, "x", 0.0, entry)
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores

    def test_delayed_restore_legacy_3_arg_call_still_works(self):
        """Existing tests at ``test_clipboard_borrow_restore.py:301/318``
        call ``_delayed_restore`` with only 3 positional args (no
        pending_entry). The new signature must remain backward
        compatible (``pending_entry`` defaults to None → no removal).
        """
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="x"),
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            # 3-arg legacy call — must NOT raise.
            cm._delayed_restore(snap, "x", 0.0)
        mock_restore.assert_called_once()
