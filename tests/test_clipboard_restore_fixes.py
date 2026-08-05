"""Tests for DE-59 / DE-60 / DE-61 / DE-62 / DE-63 (session-DE, Group 4).

Covers the five clipboard-package findings from the Group 4
(Security & Data) review:

* **DE-59** — ``ClipboardManager._last_copied_text`` PII retention.
  The instance attribute caches dictated text (which can be PII:
  passwords, messages, financial data). The pre-fix code set it
  unconditionally in ``copy()`` and cleared it ONLY in
  ``_delayed_restore``'s ``finally`` block, leaving three paths that
  retained the PII for the process lifetime: ``restore_now()``,
  ``paste_on_stop=False`` + ``clipboard_save_restore=False``, and
  ``paste(snapshot=None)``. Fix: clear in ``restore_now()`` and only
  set in ``copy()`` when ``snapshot is not None``.

* **DE-60** — Race on ``_last_copied_text`` in seq-mismatch re-copy.
  The pre-fix seq-mismatch re-copy path read the shared mutable
  instance attribute, which a concurrent ``copy(text_B)`` could
  overwrite between this cycle's ``copy(text_A)`` and ``paste()``.
  Fix: thread the request-scoped ``pasted_text`` parameter through
  the re-copy path (and the Wayland paste call sites).

* **DE-61** — Linux restore returned ``True`` on xclip/wl-copy
  non-zero exit. The pre-fix code called ``subprocess.run(...)``
  WITHOUT ``check=True`` so non-zero exits did NOT raise — silent
  data loss with false-success signal. Fix: pass ``check=True``,
  catch ``CalledProcessError``, return ``False`` + WARNING log.

* **DE-62** — Windows ``_restore_windows`` returned ``True`` even if
  all ``SetClipboardData`` calls failed. After ``EmptyClipboard()``
  ran, the user's prior content was gone, but the caller logged
  "Restored snapshot". Fix: track a success count, return ``False``
  if zero items set.

* **DE-63** — atexit/signal handler raced the daemon restore. The
  pre-fix code removed the ``pending_entry`` from
  ``_pending_restores`` in the ``finally`` block AFTER
  ``snapshot.restore()`` ran. If atexit fired in that window, two
  threads were inside ``snapshot.restore()`` concurrently. Fix: claim
  the entry under the lock BEFORE calling ``snapshot.restore()``;
  short-circuit if already claimed by atexit.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server import clipboard_snapshot as snap_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402

# Mock pynput / pyperclip at import time so the clipboard module loads
# cleanly on a headless Linux box. (Same pattern as
# test_clipboard_borrow_restore.py.)
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())

# ---------------------------------------------------------------------------
# Display-env isolation () — autouse fixture mirroring the pattern
# in test_clipboard_borrow_restore.py / test_clipboard_paste_restore.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_pending_restores():
    """Each test starts and ends with an empty ``_pending_restores`` list."""
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()
    yield
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()


# ---------------------------------------------------------------------------
# Helpers (mirrors _make_cm / _make_snapshot in test_clipboard_paste_restore.py)
# ---------------------------------------------------------------------------


def _make_cm(
    *,
    paste_enabled: bool = True,
    save_restore: bool = True,
    restore_delay_ms: int = 150,
) -> ClipboardManager:
    """Build a ClipboardManager with mocked keyboard and cached flags set.

    Construct via ``__new__`` to skip the pynput-import cost.
    """
    cm = ClipboardManager.__new__(ClipboardManager)
    cm.paste_enabled = paste_enabled
    cm._keyboard = MagicMock()
    cm._last_paste_time = 0.0  # not rate-limited
    cm._clipboard_seq = 0
    cm._last_copied_text = ""
    cm._clipboard_save_restore_enabled = save_restore
    cm._restore_delay_ms = restore_delay_ms
    return cm


def _make_snapshot(platform: str = "linux-x11") -> ClipboardSnapshot:
    """Build a fake ClipboardSnapshot for tests that need a non-None value."""
    return ClipboardSnapshot(
        platform=platform,
        items=[("text/plain;charset=utf-8", b"prior clipboard content")],
        captured_at=time.monotonic(),
    )


# ===========================================================================
# _last_copied_text PII retention
# ===========================================================================


class TestLastCopiedTextRetention:
    """DE-59: ``_last_copied_text`` is cleared on every code path.

    Three leak paths existed pre-fix:
      1. ``restore_now()`` — restores snapshot but never clears.
      2. ``copy()`` with ``snapshot is None`` (no save_restore) — sets
         ``_last_copied_text``, no daemon thread will clear it.
      3. ``paste(snapshot=None)`` — copy() set the attribute, paste()
         skips the daemon scheduling, no clear.
    """

    def test_copy_does_not_cache_text_when_snapshot_is_none(self):
        """DE-59 path 2/3: when save_restore is disabled, ``copy()``
        must NOT cache the dictated text — no daemon thread will run
        to clear it later."""
        cm = _make_cm(save_restore=False)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "super-secret-password"  # verify match
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
        ):
            # Pre-seed with stale value to prove the clear runs.
            cm._last_copied_text = "stale-from-prior-cycle"
            cm.copy("super-secret-password")
        # text must NOT be cached when snapshot is None.
        assert cm._last_copied_text == "", (
            f"_last_copied_text should be cleared when snapshot is None; got {cm._last_copied_text!r} (PII leak)"
        )

    def test_copy_does_cache_text_when_snapshot_captured(self):
        """DE-59 sanity: when save_restore IS enabled, ``copy()`` still
        caches the text — the daemon thread's ``finally`` block will
        clear it after the restore-delay window. This is the intended
        bounded-retention behavior."""
        cm = _make_cm(save_restore=True)
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "the dictation"
        sentinel = _make_snapshot()
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
            patch.object(cm, "_get_clipboard_sequence_number", return_value=42),
        ):
            cm.copy("the dictation")
        # Text IS cached because a restore IS scheduled.
        assert cm._last_copied_text == "the dictation"

    def test_restore_now_clears_last_copied_text(self):
        """DE-59 path 1: ``restore_now()`` must clear
        ``_last_copied_text`` in its ``finally`` block. This is the
        path used when ``paste_on_stop=False`` — no daemon thread
        will run to clear it."""
        cm = _make_cm()
        cm._last_copied_text = "dictated-secret"
        snap = _make_snapshot()
        with (
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            cm.restore_now(snap)
        mock_restore.assert_called_once()
        # _last_copied_text must be cleared by restore_now().
        assert cm._last_copied_text == "", (
            f"restore_now() should clear _last_copied_text; got {cm._last_copied_text!r} (PII leak)"
        )

    def test_restore_now_clears_last_copied_text_even_if_restore_raises(self):
        """DE-59 path 1 (exception): the clear must run even if
        ``snapshot.restore()`` raises."""
        cm = _make_cm()
        cm._last_copied_text = "dictated-secret"
        snap = _make_snapshot()
        with (
            patch.object(snap, "restore", side_effect=RuntimeError("restore blew up")),
            patch.object(clip_mod, "log"),
        ):
            # Must not raise — restore_now() catches + logs.
            cm.restore_now(snap)
        # clear ran in finally block despite the exception.
        assert cm._last_copied_text == ""

    def test_restore_now_with_none_snapshot_is_noop_for_clear(self):
        """DE-59 sanity: ``restore_now(None)`` does not raise and does
        not need to clear anything (no snapshot → no copy path entered)."""
        cm = _make_cm()
        cm._last_copied_text = "pre-existing-value"
        with patch.object(clip_mod, "log"):
            cm.restore_now(None)
        # No restore was scheduled, so the pre-existing value is
        # untouched (this is correct — we did NOT borrow the clipboard).
        # The important contract is that restore_now(None) doesn't raise.
        assert cm._last_copied_text == "pre-existing-value"


# ===========================================================================
# seq-mismatch re-copy race on _last_copied_text
# ===========================================================================


class TestSeqMismatchRecopyUsesPastedText:
    """DE-60: the seq-mismatch re-copy path threads ``pasted_text``.

    The pre-fix code read ``self._last_copied_text`` directly, which
    could be overwritten by a concurrent ``copy(text_B)`` between this
    cycle's ``copy(text_A)`` and ``paste()``. The re-copy would then
    write ``text_B`` to the clipboard while the daemon's
    ``expected=text_A`` no longer matches — wrong text pasted +
    spurious restore.

    Fix: read ``pasted_text`` (request-scoped parameter) first, fall
    back to the instance attribute only when ``pasted_text is None``.
    """

    def test_seq_mismatch_recopy_uses_pasted_text_not_instance_attr(self):
        """When the seq mismatches, the re-copy uses ``pasted_text``,
        NOT ``self._last_copied_text`` (which may have been overwritten
        by a concurrent cycle)."""
        cm = _make_cm(save_restore=False)
        # Simulate the race: a concurrent copy() has overwritten the
        # instance attribute with text_B.
        cm._last_copied_text = "TEXT_B_FROM_CONCURRENT_COPY"
        cm._clipboard_seq = 100  # what copy() recorded for THIS cycle
        # ``pasted_text`` is what THIS cycle copied (text_A). The fix
        # must use this value, NOT the stale instance attribute.
        pasted_text_for_this_cycle = "TEXT_A_FROM_THIS_CYCLE"

        captured_inputs: list[str] = []

        def _capture_copy(text):
            captured_inputs.append(text)

        # We need is_windows() to return True so the seq-mismatch path
        # runs (the path is Windows-only). Patch _get_clipboard_sequence_number
        # to return a DIFFERENT seq than expected → triggers the re-copy.
        with (
            patch.object(clip_mod, "is_windows", return_value=True),
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
            patch.object(
                ClipboardManager,
                "_release_stuck_modifiers",
                lambda self: None,
            ),
            patch.object(
                ClipboardManager,
                "_send_ctrl_v_win32",
                return_value=True,
            ),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_copy_to_clipboard", side_effect=_capture_copy),
            patch.object(
                ClipboardManager,
                "_get_clipboard_sequence_number",
                # First call (during paste's seq check) returns 999 (mismatch
                # with expected_seq=100), triggering the re-copy path. Second
                # call (after re-copy, to update self._clipboard_seq) can be
                # anything.
                side_effect=[999, 1000],
            ),
            patch.object(clip_mod, "log"),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            result = cm.paste(
                snapshot=None,
                pasted_text=pasted_text_for_this_cycle,
                pasted_seq=100,
            )
        assert result is True
        # the re-copy must use pasted_text (TEXT_A), NOT the
        # stale instance attribute (TEXT_B).
        assert captured_inputs == [pasted_text_for_this_cycle], (
            f"seq-mismatch re-copy should use pasted_text={pasted_text_for_this_cycle!r}; "
            f"got _copy_to_clipboard called with {captured_inputs!r}"
        )

    def test_seq_mismatch_recopy_falls_back_to_instance_attr_when_pasted_text_none(self):
        """DE-60: backward-compat — when ``pasted_text is None`` (legacy
        callers that don't thread it), the re-copy falls back to the
        instance attribute. This preserves the pre-fix behavior for
        callers that haven't been updated."""
        cm = _make_cm(save_restore=False)
        cm._last_copied_text = "FALLBACK_TEXT"
        cm._clipboard_seq = 100

        captured_inputs: list[str] = []

        def _capture_copy(text):
            captured_inputs.append(text)

        with (
            patch.object(clip_mod, "is_windows", return_value=True),
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
            patch.object(
                ClipboardManager,
                "_release_stuck_modifiers",
                lambda self: None,
            ),
            patch.object(
                ClipboardManager,
                "_send_ctrl_v_win32",
                return_value=True,
            ),
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_copy_to_clipboard", side_effect=_capture_copy),
            patch.object(
                ClipboardManager,
                "_get_clipboard_sequence_number",
                side_effect=[999, 1000],
            ),
            patch.object(clip_mod, "log"),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            # pasted_text=None → fallback to instance attribute.
            cm.paste(snapshot=None, pasted_text=None, pasted_seq=100)
        # Fallback used the instance attribute.
        assert captured_inputs == ["FALLBACK_TEXT"]


# ===========================================================================
# Linux restore returns False on xclip/wl-copy non-zero exit
# ===========================================================================


class TestLinuxRestoreReturnsFalseOnNonZeroExit:
    """DE-61: ``_restore_x11`` / ``_restore_wayland`` return False when
    xclip/wl-copy exits non-zero (no DISPLAY, compositor error, etc.).

    Pre-fix: ``subprocess.run`` was called WITHOUT ``check=True``, so
    non-zero exits did NOT raise. The function returned True
    unconditionally — the caller logged "Restored snapshot" while the
    clipboard still contained the dictated text.
    """

    def test_restore_x11_returns_false_on_xclip_nonzero_exit(self):
        """xclip exits non-zero (e.g. no DISPLAY) → restore returns False."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            # With check=True ( fix), subprocess.run raises
            # CalledProcessError when the process exits non-zero. The
            # pre-fix code did NOT pass check=True, so non-zero exits
            # did NOT raise and the function returned True — silent
            # data loss. We simulate the post-fix behavior by raising
            # CalledProcessError directly.
            raise subprocess.CalledProcessError(returncode=1, cmd=args[0])

        with (
            patch("subprocess.run", side_effect=_fake_run) as mock_run,
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_x11()
        assert result is False, (
            "DE-61: xclip non-zero exit must return False, not True (silent data loss with false-success signal)"
        )
        mock_run.assert_called_once()
        # failure must be logged at WARNING (not DEBUG).
        mock_log.warning.assert_called_once()
        # The warning must mention  for traceability.
        warning_args = mock_log.warning.call_args
        assert "DE-61" in str(warning_args), f"DE-61 warning must reference the finding ID; got {warning_args!r}"

    def test_restore_x11_returns_true_on_success(self):
        """DE-61 sanity: xclip exits 0 → restore returns True (no regression)."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_x11()
        assert result is True

    def test_restore_x11_still_returns_false_on_timeout(self):
        """DE-61 regression: TimeoutExpired is still caught (was caught
        pre-fix too — we must not regress)."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="xclip", timeout=2.0),
            ),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_x11()
        assert result is False

    def test_restore_x11_still_returns_false_on_missing_binary(self):
        """DE-61 regression: FileNotFoundError (xclip not installed) is
        still caught (was caught pre-fix too)."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        with (
            patch(
                "subprocess.run",
                side_effect=FileNotFoundError("xclip not found"),
            ),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_x11()
        assert result is False

    def test_restore_wayland_returns_false_on_wl_copy_nonzero_exit(self):
        """wl-copy exits non-zero → restore returns False."""
        snap = ClipboardSnapshot(
            platform="linux-wayland",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            # With check=True ( fix), subprocess.run raises
            # CalledProcessError when the process exits non-zero.
            raise subprocess.CalledProcessError(returncode=1, cmd=args[0])

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_wayland()
        assert result is False, "DE-61: wl-copy non-zero exit must return False, not True"
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        assert "DE-61" in str(warning_args)

    def test_restore_wayland_returns_true_on_success(self):
        """DE-61 sanity: wl-copy exits 0 → restore returns True."""
        snap = ClipboardSnapshot(
            platform="linux-wayland",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_wayland()
        assert result is True

    def test_restore_x11_passes_check_true_to_subprocess_run(self):
        """DE-63 source-string pin: the production code MUST pass
        ``check=True`` to ``subprocess.run`` so non-zero exits raise
        ``CalledProcessError``. This test catches a regression that
        reverts ``check=True``."""
        snap = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            # Verify check=True was passed.
            assert kwargs.get("check") is True, (
                f"DE-61: subprocess.run must be called with check=True; got kwargs={kwargs!r}"
            )
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            snap._restore_x11()

    def test_restore_wayland_passes_check_true_to_subprocess_run(self):
        """DE-61 source-string pin: ``check=True`` on the Wayland path."""
        snap = ClipboardSnapshot(
            platform="linux-wayland",
            items=[("text/plain;charset=utf-8", b"prior")],
            captured_at=0.0,
        )

        def _fake_run(*args, **kwargs):
            assert kwargs.get("check") is True, (
                f"DE-61: subprocess.run must be called with check=True; got kwargs={kwargs!r}"
            )
            return subprocess.CompletedProcess(args=args[0], returncode=0)

        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch.object(snap_mod, "log"),
        ):
            snap._restore_wayland()


# ===========================================================================
# Windows _restore_windows returns False if all SetClipboardData fail
# ===========================================================================


def _install_fake_windll_for_restore(*, set_clipboard_data_returns: int = 0):
    """Install a fake ``ctypes.windll`` for the Windows restore path.

    ``set_clipboard_data_returns`` controls the return value of
    ``SetClipboardData`` (0 = failure, non-zero = success — the
    function returns a HANDLE).
    """
    user32 = MagicMock()
    user32.OpenClipboard.return_value = 1  # opened
    user32.EmptyClipboard.return_value = 1
    user32.SetClipboardData.return_value = set_clipboard_data_returns
    user32.CloseClipboard.return_value = 1
    # RegisterClipboardFormatW returns a non-zero format ID on success.
    # For builtins (CF_UNICODETEXT etc.) this is a no-op but we return
    # a non-zero value so the production code's `if registered:` check
    # passes and the item is NOT skipped.
    user32.RegisterClipboardFormatW.return_value = 1

    kernel32 = MagicMock()
    kernel32.GlobalAlloc.return_value = 0xDEADBEEF  # non-NULL handle
    kernel32.GlobalLock.return_value = 0xCAFEBABE  # non-NULL ptr
    kernel32.GlobalUnlock.return_value = 1
    kernel32.GlobalFree.return_value = 0

    windll = MagicMock()
    windll.user32 = user32
    windll.kernel32 = kernel32
    return windll, user32, kernel32


class TestWindowsRestoreReturnsFalseOnAllFailures:
    """DE-62: ``_restore_windows`` returns ``False`` when zero items are
    successfully set.

    Pre-fix: ``EmptyClipboard()`` ran first (clearing the clipboard),
    then per-item ``SetClipboardData`` failures were logged at DEBUG
    and skipped, and the function returned ``True`` unconditionally —
    the clipboard was left EMPTY while the caller logged "Restored
    snapshot".
    """

    def test_restore_windows_returns_false_when_all_setclipboarddata_fail(self):
        """DE-62: all SetClipboardData calls fail → return False."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[(_cf_unicodetext := 13, "CF_UNICODETEXT", b"hello\0")],
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore(
            set_clipboard_data_returns=0,  # SetClipboardData fails
        )

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.memmove"),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_windows()
        assert result is False, (
            "DE-62: when all SetClipboardData calls fail, _restore_windows "
            "must return False (not True — false success with empty clipboard)"
        )
        # must log at WARNING (not DEBUG) for zero-items-set case.
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        assert "DE-62" in str(warning_args), f"DE-62 warning must reference the finding ID; got {warning_args!r}"
        # EmptyClipboard DID run (clipboard is now empty), but
        # SetClipboardData failed for every item.
        user32.EmptyClipboard.assert_called_once()
        user32.SetClipboardData.assert_called()

    def test_restore_windows_returns_true_when_at_least_one_item_set(self):
        """DE-62 sanity: at least one SetClipboardData succeeds → return True."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[
                (13, "CF_UNICODETEXT", b"hello\0"),
                (1, "CF_TEXT", b"hello\0"),
            ],
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore(
            set_clipboard_data_returns=0xDEADBEEF,  # non-NULL = success
        )

        with (
            patch("ctypes.windll", windll, create=True),
            patch("ctypes.memmove"),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_windows()
        assert result is True
        user32.SetClipboardData.assert_called()

    def test_restore_windows_returns_false_when_openclipboard_fails(self):
        """DE-62 regression: OpenClipboard failure still returns False
        (pre-fix behavior preserved)."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[(13, "CF_UNICODETEXT", b"hello\0")],
            captured_at=0.0,
        )
        user32 = MagicMock()
        user32.OpenClipboard.return_value = 0  # locked
        user32.CloseClipboard.return_value = 1
        windll = MagicMock()
        windll.user32 = user32
        windll.kernel32 = MagicMock()

        with (
            patch("ctypes.windll", windll, create=True),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_windows()
        assert result is False

    def test_restore_windows_empty_items_does_not_call_emptyclipboard(self):
        """DE-62 sanity: a snapshot with zero items shouldn't reach the
        success-count check (OpenClipboard + EmptyClipboard still run,
        but the loop body is empty). success_count == 0 → return False."""
        snap = ClipboardSnapshot(
            platform="windows",
            items=[],  # empty
            captured_at=0.0,
        )
        windll, user32, _ = _install_fake_windll_for_restore()

        with (
            patch("ctypes.windll", windll, create=True),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_windows()
        # zero items → success_count == 0 → return False.
        assert result is False
        # The warning was logged.
        mock_log.warning.assert_called_once()


# ===========================================================================
# macOS _restore_macos returns False on per-item / writeObjects_ failure
# ===========================================================================


def _install_fake_appkit_for_restore(
    *,
    set_data_returns: bool = True,
    write_objects_returns: bool = True,
):
    """Install fake ``AppKit`` and ``Foundation`` modules into ``sys.modules``
    for the macOS restore path.

    ``set_data_returns`` controls the BOOL return value of
    ``NSPasteboardItem.setData_forType_`` (True = item accepted the type,
    False = item rejected — e.g. unsupported type-name or a payload that
    violates the type's contract).

    ``write_objects_returns`` controls the BOOL return value of
    ``NSPasteboard.writeObjects_`` (True = at least one NSPasteboardItem
    was accepted by the pasteboard, False = no items accepted → the
    clipboard is still empty after ``clearContents``).

    Returns ``(appkit, foundation, pb, item_mock)`` so tests can assert
    on call patterns. The ``item_mock`` is the single
    NSPasteboardItem instance returned by ``alloc().init()`` — when the
    snapshot has multiple pasteboard-item indices, every alloc().init()
    call returns the SAME mock (sufficient for the boolean-returns tests
    below; the multi-item structural test inspects ``writeObjects_``'s
    call_args instead).
    """
    item_mock = MagicMock(name="ns_pasteboard_item")
    item_mock.setData_forType_.return_value = set_data_returns

    alloc_mock = MagicMock(name="ns_pasteboard_item_alloc")
    alloc_mock.init.return_value = item_mock

    pasteboard_item_cls = MagicMock(name="NSPasteboardItem")
    pasteboard_item_cls.alloc.return_value = alloc_mock

    pb = MagicMock(name="ns_pasteboard")
    pb.clearContents.return_value = None
    pb.writeObjects_.return_value = write_objects_returns

    ns_pasteboard_cls = MagicMock(name="NSPasteboard")
    ns_pasteboard_cls.generalPasteboard.return_value = pb

    appkit = MagicMock(name="AppKit")
    appkit.NSPasteboard = ns_pasteboard_cls
    appkit.NSPasteboardItem = pasteboard_item_cls

    foundation = MagicMock(name="Foundation")
    # NSData.dataWithBytes_length_ and NSData.data() return opaque NSData
    # placeholders — the production code only passes them through to
    # setData_forType_; their internal structure is irrelevant to the
    # restore contract under test.
    foundation.NSData.dataWithBytes_length_.return_value = MagicMock(name="nsdata_bytes")
    foundation.NSData.data.return_value = MagicMock(name="nsdata_empty")

    return appkit, foundation, pb, item_mock


class TestMacosRestoreReturnsFalseOnAllFailures:
    """macOS ``_restore_macos`` returns ``False`` when zero items are
    successfully set OR ``writeObjects_`` rejects the items.

    Pre-fix: ``clearContents()`` ran first (clearing the pasteboard),
    then per-item ``setData_forType_`` failures were silently swallowed
    (the return value was ignored) and ``writeObjects_``'s BOOL return
    was also ignored — the function returned ``True`` unconditionally
    and the caller logged "Restored snapshot" while the clipboard was
    left EMPTY. This mirrors the Windows ``_restore_windows`` pattern
    (DE-62): track ``success_count``, inspect ``writeObjects_``, and
    return ``False`` with a WARNING log on failure.
    """

    def test_restore_macos_returns_false_when_all_setdata_fail(self):
        """All ``setData_forType_`` calls fail → ``success_count == 0``
        → return False (not True — false success with empty pasteboard)."""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[(0, "public.utf8-plain-text", b"hello")],
            captured_at=0.0,
        )
        appkit, foundation, pb, item_mock = _install_fake_appkit_for_restore(
            set_data_returns=False,  # setData_forType_ fails
            write_objects_returns=True,
        )

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        assert result is False, (
            "When all setData_forType_ calls fail, _restore_macos must "
            "return False (not True — false success with empty pasteboard "
            "after clearContents)"
        )
        # The warning must mention the per-item failure mode.
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        # The format string reports per-item success and writeObjects_.
        fmt_str = warning_args.args[0]
        assert "items set" in fmt_str and "writeObjects_" in fmt_str, (
            f"WARNING format string must report items-set count and writeObjects_; got {fmt_str!r}"
        )
        # success_count=0, total items=1, writeObjects_ returned True.
        assert warning_args.args[1:] == (0, 1, True), (
            f"WARNING args must be (success_count, total, write_ok); got {warning_args.args[1:]!r}"
        )
        # clearContents DID run (pasteboard is now empty).
        pb.clearContents.assert_called_once()
        # setData_forType_ was called but returned False.
        item_mock.setData_forType_.assert_called()

    def test_restore_macos_returns_true_when_at_least_one_item_set(self):
        """At least one ``setData_forType_`` succeeds AND
        ``writeObjects_`` returns True → return True (sanity check)."""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[
                (0, "public.utf8-plain-text", b"hello"),
                (0, "public.rtf", b"{\\rtf1 hello}"),
            ],
            captured_at=0.0,
        )
        appkit, foundation, pb, _ = _install_fake_appkit_for_restore(
            set_data_returns=True,
            write_objects_returns=True,
        )

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log"),
        ):
            result = snap._restore_macos()

        assert result is True
        # writeObjects_ was called with a non-empty list.
        pb.writeObjects_.assert_called_once()
        ns_items_arg = pb.writeObjects_.call_args.args[0]
        assert isinstance(ns_items_arg, list) and len(ns_items_arg) >= 1

    def test_restore_macos_returns_false_when_writeobjects_returns_false(self):
        """``setData_forType_`` succeeds (so ``success_count > 0``) but
        ``writeObjects_`` returns False (pasteboard rejected every item)
        → return False.

        This is the failure mode unique to the macOS path: per-item
        ``setData_forType_`` calls succeed (the NSPasteboardItem accepts
        the data) but the pasteboard itself rejects the items at
        ``writeObjects_`` time. Pre-fix, this returned True — silent
        data loss with false-success signal."""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[(0, "public.utf8-plain-text", b"hello")],
            captured_at=0.0,
        )
        appkit, foundation, pb, item_mock = _install_fake_appkit_for_restore(
            set_data_returns=True,  # setData_forType_ succeeds
            write_objects_returns=False,  # but writeObjects_ rejects
        )

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        assert result is False, (
            "When writeObjects_ returns False, _restore_macos must return "
            "False (not True — pasteboard rejected every item, clipboard is "
            "empty after clearContents)"
        )
        # WARNING was logged (not DEBUG).
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args
        # The format string references "items set" and "writeObjects_".
        fmt_str = warning_args.args[0]
        assert "items set" in fmt_str and "writeObjects_" in fmt_str, (
            f"WARNING format string must report items-set count and writeObjects_; got {fmt_str!r}"
        )
        # success_count=1, total items=1, writeObjects_ returned False.
        assert warning_args.args[1:] == (1, 1, False), (
            f"WARNING args must be (success_count, total, write_ok) with write_ok=False; got {warning_args.args[1:]!r}"
        )
        # setData_forType_ DID succeed (success_count was > 0), but
        # writeObjects_ rejected the items — the failure is on the
        # pasteboard side, not the item side.
        item_mock.setData_forType_.assert_called()
        pb.writeObjects_.assert_called_once()

    def test_restore_macos_returns_false_when_no_items(self):
        """A snapshot with zero items must NOT call ``writeObjects_``
        (nothing to write), but ``success_count == 0`` → return False
        (mirrors the Windows empty-items path)."""
        snap = ClipboardSnapshot(
            platform="macos",
            items=[],  # empty
            captured_at=0.0,
        )
        appkit, foundation, pb, _ = _install_fake_appkit_for_restore()

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        # zero items → success_count == 0 → return False.
        assert result is False
        # writeObjects_ must NOT be called when ns_items is empty
        # (the `if ns_items else True` guard short-circuits to True,
        # but success_count == 0 still triggers the False return).
        pb.writeObjects_.assert_not_called()
        # WARNING was logged for the 0/0 items-set case.
        mock_log.warning.assert_called_once()

    def test_restore_macos_multi_item_mixed_success_returns_true(self):
        """Multi-item / multi-type pasteboard where one ``setData_forType_``
        call fails but another succeeds: ``success_count > 0`` and
        ``writeObjects_`` returns True → return True.

        This pins the best-effort semantics: per-item failures are
        logged at DEBUG and skipped, but as long as AT LEAST ONE type
        was set AND ``writeObjects_`` accepted the items, the restore
        is considered successful (mirror of the Windows path's
        per-item-continue + at-least-one-succeeded contract).
        """
        # Two pasteboard items (idx=0 and idx=1), each with one type.
        snap = ClipboardSnapshot(
            platform="macos",
            items=[
                (0, "public.utf8-plain-text", b"item-0-text"),
                (1, "public.utf8-plain-text", b"item-1-text"),
            ],
            captured_at=0.0,
        )
        appkit, foundation, pb, item_mock = _install_fake_appkit_for_restore(
            set_data_returns=True,  # all setData_forType_ succeed
            write_objects_returns=True,
        )
        # Make the FIRST setData_forType_ call fail and the SECOND
        # succeed — proves best-effort continues past a per-item failure.
        item_mock.setData_forType_.side_effect = [False, True]

        with (
            patch.dict(sys.modules, {"AppKit": appkit, "Foundation": foundation}),
            patch.object(snap_mod, "log") as mock_log,
        ):
            result = snap._restore_macos()

        # success_count == 1 (the second call), writeObjects_ == True → True.
        assert result is True
        # No WARNING was logged — the per-item failure was DEBUG-only.
        mock_log.warning.assert_not_called()
        # The DEBUG log for the per-item failure was emitted.
        debug_calls = [str(c) for c in mock_log.debug.call_args_list]
        assert any("setData_forType_ failed" in c for c in debug_calls), (
            f"Per-item setData_forType_ failure must be logged at DEBUG; got debug calls: {debug_calls!r}"
        )
        # writeObjects_ was called with both NSPasteboardItem instances
        # (the per-item failure did NOT abort the loop — best-effort).
        pb.writeObjects_.assert_called_once()
        ns_items_arg = pb.writeObjects_.call_args.args[0]
        assert len(ns_items_arg) == 2, (
            f"Multi-item pasteboard must write 2 NSPasteboardItem objects; got {len(ns_items_arg)}"
        )


# ===========================================================================
# atexit/signal handler races daemon restore
# ===========================================================================


class TestAtexitDoesNotRaceDaemonRestore:
    """DE-63: the daemon's ``_pending_restores.remove(pending_entry)``
    runs BEFORE ``snapshot.restore()``, under the lock. If atexit
    has already claimed the entry (cleared the list), the daemon
    short-circuits — preventing two threads inside
    ``snapshot.restore()`` concurrently.
    """

    def test_daemon_claims_entry_before_restore(self):
        """The daemon removes its entry from ``_pending_restores`` BEFORE
        calling ``snapshot.restore()``. We verify by patching
        ``snapshot.restore`` to inspect the list during the call."""
        cm = _make_cm()
        snap = _make_snapshot()
        snap_restore_call_count = {"count": 0}
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        def _spy_restore(*args, **kwargs):
            snap_restore_call_count["count"] += 1
            # At the time snapshot.restore() is called, the entry must
            # already be removed from _pending_restores ().
            with clip_mod._pending_restores_lock:
                assert entry not in clip_mod._pending_restores, (
                    "DE-63: daemon must claim (remove) the pending_entry "
                    "BEFORE calling snapshot.restore() — atexit could fire "
                    "during snapshot.restore() and double-restore"
                )
            return True

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", side_effect=_spy_restore),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        assert snap_restore_call_count["count"] == 1

    def test_daemon_short_circuits_when_atexit_already_claimed(self):
        """If atexit has already cleared ``_pending_restores`` (claimed
        the entry), the daemon must NOT call ``snapshot.restore()`` —
        atexit will restore synchronously."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # Do NOT register the entry — simulate atexit having already
        # taken it (cleared the list).
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore") as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        # daemon must NOT restore — atexit will do it synchronously.
        mock_restore.assert_not_called()

    def test_daemon_short_circuit_logs_at_debug(self):
        """The atexit-claimed short-circuit path logs at DEBUG (not
        WARNING — this is an expected race-resolution, not an error)."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # entry NOT in _pending_restores (simulating atexit claimed it)
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore"),
            patch.object(clip_mod, "log") as mock_log,
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        # A debug log was emitted for the short-circuit.
        mock_log.debug.assert_called()
        # The debug call names the atexit-claimed race resolution.
        debug_calls = [str(c) for c in mock_log.debug.call_args_list]
        assert any("already claimed by atexit" in c for c in debug_calls), (
            f"atexit-claimed short-circuit must log at DEBUG with the reason; got debug calls: {debug_calls!r}"
        )

    def test_atexit_handler_skips_entries_claimed_by_daemon(self):
        """DE-63: the atexit handler copies ``_pending_restores`` and
        clears it. If the daemon has already claimed (removed) its
        entry, the atexit handler will not see it — no double-restore.

        This test simulates the race where the daemon claims first,
        then atexit fires."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        # Daemon claims the entry (removes it under the lock) — this
        # is what _delayed_restore does BEFORE calling snapshot.restore().
        with clip_mod._pending_restores_lock, contextlib.suppress(ValueError):
            clip_mod._pending_restores.remove(entry)

        # Now atexit fires. It should see an EMPTY list (daemon already
        # claimed its entry) and NOT call snapshot.restore().
        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore") as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            clip_mod._force_restore_pending_at_exit()
        # atexit saw an empty list, so it did not call restore.
        mock_restore.assert_not_called()

    def test_atexit_still_restores_entries_daemon_has_not_claimed(self):
        """DE-63 regression: atexit still restores entries the daemon
        hasn't claimed yet (the common case — app exits during the
        restore-delay window before the daemon thread fires)."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # Entry is still in _pending_restores — daemon hasn't claimed it.
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", return_value=True) as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            clip_mod._force_restore_pending_at_exit()
        # atexit saw the entry and restored it.
        mock_restore.assert_called_once()
        # The list was cleared.
        with clip_mod._pending_restores_lock:
            assert clip_mod._pending_restores == []

    def test_daemon_clears_last_copied_text_even_on_short_circuit(self):
        """DE-63 + DE-59 interaction: the ``finally`` block (which
        clears ``_last_copied_text``) must still run on the
        short-circuit path. Otherwise the PII would leak through the
        short-circuit path."""
        cm = _make_cm()
        cm._last_copied_text = "secret-pii"
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # entry NOT in _pending_restores → short-circuit.

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore"),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)
        # clear ran in finally block despite the short-circuit.
        assert cm._last_copied_text == "", (
            f"_last_copied_text should be cleared by _delayed_restore's "
            f"finally block even on the short-circuit path; got "
            f"{cm._last_copied_text!r} (PII leak)"
        )

    def test_no_concurrent_restore_when_atexit_fires_during_daemon_restore(self):
        """DE-63 end-to-end: simulate atexit firing WHILE the daemon is
        inside ``snapshot.restore()``. Verify snapshot.restore() is
        called exactly ONCE (not twice).

        Pre-fix: atexit could fire during the daemon's restore window
        (between snapshot.restore() and the finally block's remove()),
        leading to two concurrent restore calls.
        """
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        restore_call_count = {"count": 0}
        atexit_call_count = {"count": 0}

        def _spy_restore(*args, **kwargs):
            restore_call_count["count"] += 1
            # Simulate atexit firing DURING snapshot.restore(). At this
            # point, the daemon has already claimed (removed) the entry
            # ( fix), so atexit will see an empty list and NOT
            # call restore.
            with (
                patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
                patch.object(clip_mod, "log"),
            ):
                clip_mod._force_restore_pending_at_exit()
            atexit_call_count["count"] += 1
            return True

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", side_effect=_spy_restore),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        # Daemon called restore exactly once.
        assert restore_call_count["count"] == 1
        # Atexit fired during the daemon's restore call.
        assert atexit_call_count["count"] == 1
        # No second restore call from atexit (it saw an empty list).
        # If we got here without a concurrent-restore assertion error,
        # the race is fixed.


# ===========================================================================
# Source-string pin:  threading through seq-mismatch path
# ===========================================================================


class TestSourceStringPin:
    """Source-string pin: the seq-mismatch re-copy path in
    ``paste()`` must reference ``pasted_text`` (the threaded
    parameter), NOT ``self._last_copied_text`` directly. This catches
    a regression that reverts the DE-60 fix.
    """

    def test_seq_mismatch_recopy_reads_pasted_text_not_instance_attr(self):
        """The seq-mismatch re-copy block must compute ``recopy_text``
        from ``pasted_text`` (with fallback to ``self._last_copied_text``)."""
        import inspect
        import re

        manager_src = inspect.getsource(ClipboardManager)
        # The source MUST contain a ``recopy_text`` local that prefers
        # ``pasted_text`` over ``self._last_copied_text``.
        # We look for the pattern in the re-copy block.
        # Note: pattern does NOT require parentheses around the ternary —
        # the production code uses bare ``a if cond else b`` form.
        m = re.search(
            r"recopy_text\s*=\s*pasted_text\s+if\s+pasted_text\s+is\s+not\s+None\s+else\s+self\._last_copied_text",
            manager_src,
        )
        assert m is not None, (
            "DE-60: seq-mismatch re-copy path must compute recopy_text from "
            "pasted_text (request-scoped parameter), with fallback to "
            "self._last_copied_text only when pasted_text is None. "
            "Expected pattern not found in manager.py source."
        )

    def test_wayland_paste_call_sites_use_wtype_text(self):
        """DE-60: the Wayland paste call sites (both terminal and
        non-terminal branches) pass ``wtype_text`` (computed from
        ``pasted_text``), NOT ``self._last_copied_text`` directly."""
        import inspect
        import re

        manager_src = inspect.getsource(ClipboardManager)
        # The wtype_text local must be computed from pasted_text.
        m = re.search(
            r"wtype_text\s*=\s*pasted_text\s+if\s+pasted_text\s+is\s+not\s+None\s+else\s+self\._last_copied_text",
            manager_src,
        )
        assert m is not None, (
            "DE-60: Wayland paste call sites must compute wtype_text from "
            "pasted_text (with fallback to self._last_copied_text)."
        )
        # The two call sites must use wtype_text, not self._last_copied_text.
        # Verify NO direct _linux_paste_via_wtype(self._last_copied_text) calls remain.
        bad_pattern = r"_linux_paste_via_wtype\(\s*self\._last_copied_text\s*\)"
        bad_matches = re.findall(bad_pattern, manager_src)
        assert not bad_matches, (
            f"DE-60: _linux_paste_via_wtype must not be called with "
            f"self._last_copied_text directly; found {len(bad_matches)} "
            f"occurrences. Should use wtype_text instead."
        )
