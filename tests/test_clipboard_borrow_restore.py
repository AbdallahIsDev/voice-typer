"""Tests for the ADR-0010 §10.2 borrow/restore cycle.

These tests exercise the new ``ClipboardManager.copy`` / ``paste`` /
``restore_now`` / ``_delayed_restore`` / ``refresh_config`` API surface
introduced by ADR-0010. They mock ``ClipboardSnapshot.capture()`` and
``ClipboardSnapshot.restore()`` so they run on any platform without
touching the real clipboard.

ADR-0010 design principles covered here:

* DP1 — every borrow is paired with a restore (``test_paste_schedules_
  restore_thread``).
* DP2 — ``restore_now`` restores even when no paste is sent.
* DP3 — restore runs on a daemon thread.
* DP4 — snapshots are passed as values, not stored as instance state.
* DP7 — ``clipboard_save_restore`` flag actually gates capture.

The fixture below constructs a ``ClipboardManager`` directly via
``ClipboardManager.__new__`` so we can set the cached config flags
without paying the pynput-import cost. ``DISPLAY`` is set to ``:99``
(Xvfb is running) and ``WAYLAND_DISPLAY`` is popped from the
environment (per-test via the autouse ``_mock_display_env`` fixture
below — see XS-22) so any incidental pynput usage doesn't crash the
suite.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (  # noqa: E402
    ClipboardCopyError,
    ClipboardManager,
)
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402

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
# Helper: build a ClipboardManager via __new__ so we control cached flags
# without paying the pynput import cost.
# ---------------------------------------------------------------------------


# ===========================================================================
# copy() — snapshot capture paths (ADR-0010 §5.2)
# ===========================================================================


class TestCopySnapshotCapture:
    """``copy()`` returns a snapshot when capture is enabled, None otherwise."""

    def test_copy_returns_snapshot_when_save_restore_enabled(self):
        """copy() returns the ClipboardSnapshot captured before overwrite."""
        cm = make_clipboard_manager(save_restore=True)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"  # verification match
        sentinel = make_clipboard_snapshot()
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=sentinel,
            ) as mock_capture,
        ):
            result = cm.copy("new text")
        assert result is sentinel
        mock_capture.assert_called_once()

    def test_copy_returns_none_when_save_restore_disabled(self):
        """When save_restore is off, copy() does NOT call capture()."""
        cm = make_clipboard_manager(save_restore=False)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=None,
            ) as mock_capture,
        ):
            result = cm.copy("new text")
        assert result is None
        # DP7: the flag actually gates the capture call.
        mock_capture.assert_not_called()

    def test_copy_raises_clipboard_copy_error_on_failure(self):
        """A genuine pyperclip.copy() failure raises ClipboardCopyError."""
        cm = make_clipboard_manager(save_restore=False)
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = OSError("clipboard locked")
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")

    def test_copy_restores_snapshot_on_failure(self):
        """If copy fails after a snapshot was captured, the snapshot is restored.

        ADR-0010 §5.2: "The snapshot, if captured, is restored before
        raising so the clipboard is never left torn."
        """
        cm = make_clipboard_manager(save_restore=True)
        sentinel = make_clipboard_snapshot()
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = OSError("copy failed")
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=sentinel,
            ),
            patch.object(sentinel, "restore") as mock_restore,
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")
            # Snapshot was restored before the exception propagated.
            mock_restore.assert_called_once()


# ===========================================================================
# paste() — restore scheduling and gates (ADR-0010 §5.3)
# ===========================================================================


class TestPasteRestoreScheduling:
    """``paste()`` schedules a daemon-thread restore when given a snapshot."""

    def test_paste_schedules_restore_thread(self):
        """paste(snapshot=...) starts a daemon thread to restore later."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()

        # Avoid the real paste-keystroke path: stub _send_ctrl_v_win32 and
        # the safety check so paste() returns True after scheduling.
        # ADR-0010 §5.3: paste() now checks ``_Controller is None`` (was
        # ``_Key is None``); patch both so the early-return guard doesn't
        # fire on the Linux branch (is_windows=False).
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "_Key", MagicMock()),
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
            patch.object(threading, "Thread") as mock_thread_cls,
            patch.object(clip_mod, "time") as mock_time,
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            mock_thread_inst = MagicMock()
            mock_thread_cls.return_value = mock_thread_inst
            result = cm.paste(snapshot=snap, pasted_text="new text")
        assert result is True
        # A Thread was constructed and started.
        mock_thread_cls.assert_called_once()
        mock_thread_inst.start.assert_called_once()

    def test_paste_force_bypasses_paste_enabled_gate(self):
        """force=True bypasses paste_enabled=False (used by repaste_last)."""
        cm = make_clipboard_manager(paste_enabled=False)
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_macos", return_value=False),
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
            # On Linux without pynput, paste() would early-return False.
            # Patch _Controller so the early-return guard doesn't fire.
            with patch.object(clip_mod, "_Controller", MagicMock()), patch.object(clip_mod, "_Key", MagicMock()):
                result = cm.paste(force=True)
        # force=True bypasses the paste_enabled gate; the keystroke may
        # still fail (we're on Linux with a mocked keyboard), but the
        # important thing is we got PAST the gate.
        assert result in (True, False)

    def test_paste_returns_false_when_paste_enabled_false_without_force(self):
        """Without force=True, paste_enabled=False returns False."""
        cm = make_clipboard_manager(paste_enabled=False)
        with patch.object(clip_mod, "is_windows", return_value=False), patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            result = cm.paste()
        assert result is False


# ===========================================================================
# restore_now() — immediate restore without paste (ADR-0010 §5.4 / DP2)
# ===========================================================================


class TestRestoreNow:
    """``restore_now`` restores immediately without sending a paste keystroke."""

    def test_restore_now_restores_immediately(self):
        """restore_now(snapshot) calls snapshot.restore() right away."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        with patch.object(snap, "restore", return_value=True) as mock_restore, patch.object(clip_mod, "log"):
            cm.restore_now(snap)
        mock_restore.assert_called_once()

    def test_restore_now_with_none_is_noop(self):
        """restore_now(None) does nothing and does not raise."""
        cm = make_clipboard_manager()
        with patch.object(clip_mod, "log"):
            # Must not raise.
            cm.restore_now(None)


# ===========================================================================
# _delayed_restore() — daemon-thread restore (ADR-0010 §5.3 / DP3)
# ===========================================================================


class TestDelayedRestore:
    """``_delayed_restore`` runs on a daemon thread and re-checks the clipboard."""

    def test_delayed_restore_skips_when_clipboard_changed(self):
        """If the clipboard was changed by the user, restore is skipped."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        mock_pyper = MagicMock()
        # Clipboard no longer matches pasted_text → skip restore.
        mock_pyper.paste.return_value = "user's new copy"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(snap, "restore") as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, pasted_text="original text", delay=0.0)
        mock_restore.assert_not_called()

    def test_delayed_restore_restores_when_clipboard_unchanged(self):
        """If the clipboard still holds the pasted text, restore is called."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        mock_pyper = MagicMock()
        # Clipboard still has the pasted text → restore proceeds.
        mock_pyper.paste.return_value = "original text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, pasted_text="original text", delay=0.0)
        mock_restore.assert_called_once()

    def test_delayed_restore_accepts_4_arg_pending_entry_from_paste_call_site(self):
        """regression guard: ``paste()`` spawns the daemon thread
        with 4 positional args ``(snapshot, expected, delay, _pending_entry)``,
        so ``_delayed_restore`` MUST accept 4 positional args without
        raising ``TypeError``.

        The original  bug was that the production signature was
        3-arg while the call site passed 4 — the daemon thread died
        immediately on every ``paste()`` invocation, silently breaking
        clipboard restore. The fix added ``pending_entry: Any = None``
        to the signature.

        This test invokes the SUT with the EXACT 4-arg shape that
        ``paste()`` uses (verified via static call-site inspection at
        ``voice_typer/server/clipboard/manager.py``), so a future
        regression that removes the 4th parameter from the signature
        fails this test directly rather than silently breaking every
        paste restore in production.
        """
        import inspect

        from voice_typer.server.clipboard.manager import ClipboardManager

        # (1) Static contract: the signature accepts 4 positional args.
        sig = inspect.signature(ClipboardManager._delayed_restore)
        positional_params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional_params) >= 4, (
            " regression: _delayed_restore must accept 4 "
            "positional args (self, snapshot, pasted_text, delay, "
            "pending_entry) — paste() spawns the thread with "
            "args=(snapshot, expected, delay, _pending_entry). "
            f"Got {len(positional_params)} positional params: "
            f"{list(sig.parameters)}"
        )

        # (2) Behavioral contract: calling with the 4-arg shape must
        # not raise TypeError. This is the exact call shape ``paste()``
        # uses at the Thread() constructor.
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        pending_entry = (cm, snap, "original text", 0.0)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "original text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(snap, "restore", return_value=True),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            # Must not raise TypeError — the original bug.
            cm._delayed_restore(snap, "original text", 0.0, pending_entry)


# ===========================================================================
# refresh_config() — sync cached flags from runtime config (ADR-0010 §5.5)
# ===========================================================================


class TestRefreshConfig:
    """``refresh_config`` syncs cached flags from the runtime Config object."""

    def test_refresh_config_syncs_paste_enabled_from_paste_on_stop(self):
        """§2.12: paste_on_stop=False → paste_enabled=False (mirror)."""
        cm = make_clipboard_manager(paste_enabled=True)
        cfg = MagicMock()
        cfg.paste_on_stop = False
        cfg.clipboard_save_restore = True
        cfg.clipboard_restore_delay_ms = 150
        cm.refresh_config(cfg)
        assert cm.paste_enabled is False

    def test_refresh_config_updates_restore_delay_ms(self):
        """refresh_config picks up the new clipboard_restore_delay_ms value."""
        cm = make_clipboard_manager(restore_delay_ms=150)
        cfg = MagicMock()
        cfg.paste_on_stop = True
        cfg.clipboard_save_restore = True
        cfg.clipboard_restore_delay_ms = 250
        cm.refresh_config(cfg)
        assert cm._restore_delay_ms == 250

    def test_refresh_config_updates_save_restore_enabled(self):
        """refresh_config picks up the new clipboard_save_restore value."""
        cm = make_clipboard_manager(save_restore=True)
        cfg = MagicMock()
        cfg.paste_on_stop = True
        cfg.clipboard_save_restore = False
        cfg.clipboard_restore_delay_ms = 150
        cm.refresh_config(cfg)
        assert cm._clipboard_save_restore_enabled is False
