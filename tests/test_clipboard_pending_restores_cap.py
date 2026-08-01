"""DJ-22 + DJ-26 — ``_pending_restores`` cap, eviction, and orphan cleanup.

DJ-22 (Medium): the DE-63 refactor moved ``_pending_restores.remove(pending_entry)``
from the ``finally`` block of ``_delayed_restore`` to a ``try`` block
BEFORE ``snapshot.restore()``. This narrowed the atexit race but opened
two new leak windows:

  1. ``_cb.time.sleep(delay)`` raises (e.g. signal delivered mid-sleep)
     → the broad ``except Exception`` catches it, but the pending_entry
     is still in the deque.
  2. The lock-acquire for the primary remove fails catastrophically
     (the ``except Exception`` at the inner try block) → the daemon
     proceeds with restore WITHOUT claiming the entry, so the entry
     stays in the deque after restore completes.

Each orphaned entry pins a ClipboardSnapshot (up to 16 MB × N formats).

DJ-22 fix: re-add a defensive ``_pending_restores.remove(pending_entry)``
(under the lock, with ``contextlib.suppress(ValueError)``) to the
``finally`` block, AFTER the existing ``_last_copied_text`` clear. The
``ValueError`` suppress handles the case where the entry was already
claimed (by the primary remove or by atexit) — no double-restore
happens because we're already past the restore call.

DJ-26 (Medium): ``_pending_restores`` is an unbounded plain list. Under
restore-lock contention (a hung Win32 OpenClipboard, etc.), the daemon
threads pile up with their snapshots still in the deque. RSS can grow
by 16 MB × N pending pastes until the lock is released.

DJ-26 fix:

  - Change ``_pending_restores`` to ``collections.deque(maxlen=8)``.
  - Add ``_append_pending_restore(entry)`` helper that, when appending
    would exceed 8 entries, pops the OLDEST entry and synchronously
    restores it via ``ClipboardManager.restore_now(snapshot)`` BEFORE
    appending the new entry.
  - Add a 5-second timeout to ``_restore_lock`` acquisition in
    ``ClipboardSnapshot.restore()`` so a single hung restore doesn't
    block the daemon thread indefinitely.

This test file asserts:

  1. The deque never exceeds 8 entries.
  2. Eviction restores the oldest snapshot via ``restore_now()``.
  3. Orphaned entries (left in the deque by the DJ-22 leak windows)
     are cleaned up by the defensive remove in the ``finally`` block.
  4. ``_restore_lock`` acquisition has a timeout (returns ``False`` on
     timeout rather than blocking).
"""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# Mock pynput / pyperclip at import time so the clipboard module loads
# cleanly on a headless Linux box. (Same pattern as
# test_clipboard_borrow_restore.py.)
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_pending_restores():
    """Each test starts and ends with an empty ``_pending_restores`` deque."""
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()
    yield
    with clip_mod._pending_restores_lock:
        clip_mod._pending_restores.clear()


# ── Helpers (mirrors test_clipboard_restore_fixes.py) ────────────────────────


def _make_cm(
    *,
    paste_enabled: bool = True,
    save_restore: bool = True,
    restore_delay_ms: int = 150,
) -> ClipboardManager:
    """Build a ClipboardManager with mocked keyboard and cached flags set."""
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
# defensive remove in _delayed_restore finally block
# ===========================================================================


class TestDelayedRestoreDefensiveRemove:
    """DJ-22: the ``finally`` block of ``_delayed_restore`` re-adds a
    defensive ``_pending_restores.remove(pending_entry)`` (under the lock,
    with ``contextlib.suppress(ValueError)``) to catch the two leak
    windows opened by the DE-63 refactor."""

    def test_sleep_raising_leaves_entry_cleaned_up_by_finally(self):
        """DJ-22 path 1: ``_cb.time.sleep(delay)`` raises (e.g. signal
        delivered mid-sleep). The broad ``except Exception`` catches it.
        Pre-fix, the entry was orphaned in the deque. Post-fix, the
        ``finally`` block's defensive remove cleans it up."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)
        assert len(clip_mod._pending_restores) == 1

        # Patch time.sleep to raise (simulating signal delivery).
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock(side_effect=RuntimeError("interrupted by signal"))
            # Must not raise (the broad except in _delayed_restore catches it).
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        # the entry must be gone from the deque (cleaned up by
        # the finally block's defensive remove).
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores, (
                "DJ-22: pending_entry must be removed from _pending_restores "
                "even if sleep() raises — the finally block's defensive remove "
                "catches this leak window"
            )
            assert len(clip_mod._pending_restores) == 0

    def test_normal_completion_path_still_cleans_up_entry(self):
        """DJ-22 sanity: the normal-completion path (sleep doesn't raise,
        primary remove succeeds) must still leave the deque empty. The
        finally block's defensive remove raises ValueError (entry already
        claimed) and is suppressed — no double-remove, no leak."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)
        assert len(clip_mod._pending_restores) == 1

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", return_value=True),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        # The entry was removed by the primary remove (before restore).
        # The finally block's defensive remove raised ValueError (entry
        # already gone) and was suppressed.
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores
            assert len(clip_mod._pending_restores) == 0

    def test_short_circuit_path_still_cleans_up_entry(self):
        """DJ-22 sanity: the short-circuit path (atexit already claimed
        the entry → daemon returns early) must still leave the deque
        empty. The finally block runs after the early return — the
        defensive remove raises ValueError (entry was claimed by atexit)
        and is suppressed."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        # Don't register the entry — simulate atexit having already
        # cleared the deque.
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
        # daemon short-circuited (entry already claimed).
        mock_restore.assert_not_called()
        # finally block ran; defensive remove raised ValueError
        # (entry was never in the deque) and was suppressed.
        with clip_mod._pending_restores_lock:
            assert entry not in clip_mod._pending_restores
            assert len(clip_mod._pending_restores) == 0

    def test_finally_remove_does_not_reintroduce_atexit_race(self):
        """DJ-22 contract: the defensive remove in the finally block does
        NOT reintroduce the DE-63 atexit race. The race was: atexit
        claims the entry (clears the deque) WHILE the daemon is inside
        ``snapshot.restore()``, then both threads are inside
        ``snapshot.restore()`` concurrently. The finally-block remove
        runs AFTER restore completes (or short-circuits), so:

          - If the daemon ran restore: atexit already cleared the deque,
            so the finally remove raises ValueError (suppressed). No
            double-restore.
          - If the daemon short-circuited (atexit claimed first): the
            finally remove raises ValueError (suppressed). No restore
            at all from the daemon.

        Either way, the atexit handler is the SOLE restorer. Verified
        by simulating: atexit clears the deque DURING restore; the
        finally remove must NOT call restore again."""
        cm = _make_cm()
        snap = _make_snapshot()
        entry = (cm, snap, "pasted", 0.0)
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append(entry)

        restore_call_count = {"count": 0}

        def _spy_restore(*args, **kwargs):
            restore_call_count["count"] += 1
            # Simulate atexit firing DURING restore — clears the deque.
            with clip_mod._pending_restores_lock:
                clip_mod._pending_restores.clear()

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore", side_effect=_spy_restore),
            patch.object(clip_mod, "log"),
        ):
            mock_time.sleep = MagicMock()
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        # Restore was called exactly once (by the daemon). The finally
        # block's defensive remove did NOT trigger a second restore.
        assert restore_call_count["count"] == 1, (
            f"DJ-22: defensive remove must NOT reintroduce the DE-63 atexit race; "
            f"expected 1 restore call, got {restore_call_count['count']}"
        )
