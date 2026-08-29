"""— ``_pending_restores`` hard cap with force-restore on overflow.

Background
----------
``_pending_restores`` is a module-level list of in-flight delayed-restore
entries. Each paste() appends one entry; the daemon thread removes its
own entry on completion. Under normal use (``_restore_delay_ms=150``),
entries live ~150 ms so steady-state size is 1-2 entries. BUT:

  (a) ``clipboard_restore_delay_ms`` is user-configurable with no upper
      bound — a user setting it to 5000 ms creates a 5 s window per entry.
  (b) If the daemon thread fails to start, a hang in ``_delayed_restore``
      leaves the entry forever.
  (c) Each entry holds a ``ClipboardSnapshot`` whose ``items`` list can
      be 16 MB × N formats. At paste rate 5/s × 5 s delay the cap is the
      only thing bounding peak RSS.

 fix: add ``_MAX_PENDING_RESTORES`` as a module-level constant
(originally 64; tightened to 8 to bound the worst case at ~128 MB
instead of ~1 GB) in the clipboard package. When ``paste()`` would
append a new entry
to a list already at the cap, force-restore the OLDEST entry's snapshot
synchronously (under ``_pending_restores_lock``) BEFORE appending the
new entry. This bounds peak RSS at ``_MAX_PENDING_RESTORES × ~16 MB ×
N_formats`` instead of unbounded growth.

These tests pin the behaviour in isolation — they call the production
``ClipboardManager.paste()`` path with mocked dependencies so the cap
logic is exercised end-to-end without actually touching the clipboard.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (
    manager as manager_mod,  # noqa: E402
)

from tests.fixtures.clipboard_helpers import make_clipboard_manager, make_clipboard_snapshot  # noqa: E402

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).

# ── Fixtures ────────────────────────────────────────────────────────────


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


# ── Helpers (mirrors test_clipboard_pending_restores_cap.py) ────────────────


# ===========================================================================
# _MAX_PENDING_RESTORES constant
# ===========================================================================


class TestMaxPendingRestoresConstant:
    """Pin the constant so a future change is intentional."""

    def test_constant_exists_and_is_8(self) -> None:
        """``_MAX_PENDING_RESTORES = 8`` — far above the 1-2 entries normal
        use ever holds, but small enough that a runaway condition (leaked
        daemon thread, user-set multi-second restore delay) cannot pin
        gigabytes of snapshots (worst case ~128 MB)."""
        assert manager_mod._MAX_PENDING_RESTORES == 8

    def test_constant_is_int(self) -> None:
        """The constant is an int (not a float / string) so the ``len()``
        comparison is type-stable."""
        assert isinstance(manager_mod._MAX_PENDING_RESTORES, int)


# ===========================================================================
# Force-restore on overflow
# ===========================================================================


class TestPendingRestoresCapForceRestore:
    """When ``_pending_restores`` is at the cap, appending a new entry
    force-restores the OLDEST entry's snapshot synchronously (under the
    lock) BEFORE appending the new entry."""

    def test_cap_not_hit_does_not_force_restore(self) -> None:
        """When the list is below the cap, no force-restore happens — the
        new entry is simply appended."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = (cm, snap, "pasted", 0.0)

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_paste_from_clipboard", return_value="pasted"),
            patch.object(snap, "restore") as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            mock_time.monotonic = MagicMock(return_value=0.0)
            mock_time.sleep = MagicMock()

            # Below the cap — populate the list with a few entries,
            # INCLUDING the entry we're about to restore (so the remove
            # inside _delayed_restore succeeds and snapshot.restore() runs).
            with clip_mod._pending_restores_lock:
                for i in range(5):
                    other_snap = make_clipboard_snapshot()
                    clip_mod._pending_restores.append((cm, other_snap, f"old-{i}", 0.0))
                clip_mod._pending_restores.append(entry)

            # Now call _delayed_restore to drain one (simulating normal
            # completion). Then verify force-restore did NOT fire.
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        # snap.restore() called once (by _delayed_restore). Force-restore
        # would have called it an EXTRA time, so total == 1 means cap
        # wasn't hit.
        assert mock_restore.call_count == 1

    def test_cap_hit_force_restores_oldest_snapshot(self) -> None:
        """When the list is AT the cap (64 entries), appending the 65th
        force-restores the OLDEST entry's snapshot BEFORE appending the new."""
        cm = make_clipboard_manager()
        new_snap = make_clipboard_snapshot()

        # Populate the list to exactly the cap. Use DISTINCT snapshots so
        # we can verify the OLDEST is the one restored.
        oldest_snap = make_clipboard_snapshot()
        oldest_snap_restore = MagicMock(return_value=True)
        oldest_snap.restore = oldest_snap_restore  # type: ignore[method-assign]
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append((cm, oldest_snap, "oldest", 0.0))
            for i in range(manager_mod._MAX_PENDING_RESTORES - 1):
                other_snap = make_clipboard_snapshot()
                other_snap_restore = MagicMock(return_value=True)
                other_snap.restore = other_snap_restore  # type: ignore[method-assign]
                clip_mod._pending_restores.append((cm, other_snap, f"other-{i}", 0.0))
        assert len(clip_mod._pending_restores) == manager_mod._MAX_PENDING_RESTORES

        # Trigger the cap by calling the paste() path's append logic.
        # We exercise JUST the append-with-cap branch by invoking the
        # code path through paste() with all the paste-time side effects
        # mocked out.
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=True),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_is_wayland_paste_session", return_value=False),
            patch.object(clip_mod, "_have_wtype", return_value=False),
            patch.object(clip_mod, "_is_password_field", return_value=False),
            patch.object(clip_mod, "_is_content_editable", return_value=False),
            patch.object(clip_mod, "_is_elevated_target", return_value=False),
            patch.object(clip_mod, "_paste_from_clipboard", return_value="new"),
            patch.object(sys.modules["threading"], "Thread") as mock_thread_cls,
        ):
            mock_time.monotonic = MagicMock(return_value=100.0)
            mock_time.sleep = MagicMock()
            # Stub out the Thread so no real daemon thread spawns.
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            # Call paste() with a snapshot — this triggers the cap logic.
            cm.paste(snapshot=new_snap, pasted_text="new")

        # The OLDEST snapshot's restore was called exactly once (the
        # force-restore on cap hit). The NEW snapshot was NOT restored
        # (it's still in _pending_restores waiting for its daemon thread).
        oldest_snap_restore.assert_called_once_with()
        # After paste(), the list should still be at the cap (we removed
        # one and added one). The oldest entry should no longer be in
        # the list; the new entry should be.
        with clip_mod._pending_restores_lock:
            entries = list(clip_mod._pending_restores)
        # Check by snapshot IDENTITY — the delay field differs (0.0 for
        # pre-populated entries, cm._restore_delay_ms/1000 for paste()'d
        # entry) so full-tuple equality would fail. `in`/`not in` use
        # __eq__, and ClipboardSnapshot __eq__ includes captured_at, which
        # on coarse-resolution monotonic clocks (e.g. Windows ~1 ms) is
        # identical across snapshots made in a tight loop — so every
        # snapshot compares equal and `oldest_snap not in ...` would
        # ALWAYS fail. Compare object identity instead.
        entry_snapshots = [e[1] for e in entries]
        assert not any(s is oldest_snap for s in entry_snapshots)
        assert any(s is new_snap for s in entry_snapshots)
        assert len(entries) == manager_mod._MAX_PENDING_RESTORES

    def test_cap_hit_force_restore_failure_does_not_break_append(self) -> None:
        """If the force-restore raises (e.g. Win32 OpenClipboard hang), the
        append STILL happens — we don't lose the new entry just because
        the oldest couldn't be restored."""
        cm = make_clipboard_manager()
        new_snap = make_clipboard_snapshot()

        # Populate the list to the cap with an oldest snapshot whose
        # restore() raises.
        oldest_snap = make_clipboard_snapshot()
        oldest_snap.restore = MagicMock(side_effect=RuntimeError("OpenClipboard hung"))  # type: ignore[method-assign]
        with clip_mod._pending_restores_lock:
            clip_mod._pending_restores.append((cm, oldest_snap, "oldest", 0.0))
            for i in range(manager_mod._MAX_PENDING_RESTORES - 1):
                other_snap = make_clipboard_snapshot()
                other_snap.restore = MagicMock(return_value=True)  # type: ignore[method-assign]
                clip_mod._pending_restores.append((cm, other_snap, f"other-{i}", 0.0))

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=True),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_is_wayland_paste_session", return_value=False),
            patch.object(clip_mod, "_have_wtype", return_value=False),
            patch.object(clip_mod, "_is_password_field", return_value=False),
            patch.object(clip_mod, "_is_content_editable", return_value=False),
            patch.object(clip_mod, "_is_elevated_target", return_value=False),
            patch.object(clip_mod, "_paste_from_clipboard", return_value="new"),
            patch.object(sys.modules["threading"], "Thread") as mock_thread_cls,
        ):
            mock_time.monotonic = MagicMock(return_value=100.0)
            mock_time.sleep = MagicMock()
            mock_thread_cls.return_value = MagicMock()

            # Must NOT raise even though oldest_snap.restore() raised.
            cm.paste(snapshot=new_snap, pasted_text="new")

        # The new entry was still appended.
        with clip_mod._pending_restores_lock:
            entries = list(clip_mod._pending_restores)
        # Check by snapshot identity (delay field differs between
        # pre-populated entries and the paste()'d entry). `in` uses
        # __eq__, and all snapshots made in a tight loop compare equal
        # on coarse monotonic clocks (captured_at is included in
        # __eq__) — compare object identity instead.
        entry_snapshots = [e[1] for e in entries]
        assert any(s is new_snap for s in entry_snapshots)
        # The oldest entry was popped (force-restore attempt was made).
        assert not any(s is oldest_snap for s in entry_snapshots)

    def test_cap_hit_restores_exactly_one_oldest_not_all(self) -> None:
        """When the cap is hit, ONLY the single oldest entry is force-restored
        (not all of them). The list size stays at the cap after the append."""
        cm = make_clipboard_manager()
        new_snap = make_clipboard_snapshot()

        # Populate the list with the cap of entries, each with a
        # distinct snapshot whose restore() we can track.
        snaps_with_restores = []
        with clip_mod._pending_restores_lock:
            for i in range(manager_mod._MAX_PENDING_RESTORES):
                snap = make_clipboard_snapshot()
                m = MagicMock(return_value=True)
                snap.restore = m  # type: ignore[method-assign]
                snaps_with_restores.append((snap, m))
                clip_mod._pending_restores.append((cm, snap, f"entry-{i}", 0.0))

        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "_Controller", MagicMock()),
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(clip_mod, "is_linux", return_value=True),
            patch.object(clip_mod, "is_macos", return_value=False),
            patch.object(clip_mod, "_is_wayland_paste_session", return_value=False),
            patch.object(clip_mod, "_have_wtype", return_value=False),
            patch.object(clip_mod, "_is_password_field", return_value=False),
            patch.object(clip_mod, "_is_content_editable", return_value=False),
            patch.object(clip_mod, "_is_elevated_target", return_value=False),
            patch.object(clip_mod, "_paste_from_clipboard", return_value="new"),
            patch.object(sys.modules["threading"], "Thread") as mock_thread_cls,
        ):
            mock_time.monotonic = MagicMock(return_value=100.0)
            mock_time.sleep = MagicMock()
            mock_thread_cls.return_value = MagicMock()

            cm.paste(snapshot=new_snap, pasted_text="new")

        # ONLY the oldest (index 0) was force-restored.
        snaps_with_restores[0][1].assert_called_once_with()
        for _snap, m in snaps_with_restores[1:]:
            m.assert_not_called()

        # List size is still at the cap.
        with clip_mod._pending_restores_lock:
            assert len(clip_mod._pending_restores) == manager_mod._MAX_PENDING_RESTORES
