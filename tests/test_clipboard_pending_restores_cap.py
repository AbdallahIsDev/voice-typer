"""— ``_pending_restores`` hard cap with force-restore on overflow."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (
    manager as manager_mod,  # noqa: E402
)

from tests.fixtures.clipboard_helpers import make_clipboard_manager, make_clipboard_snapshot  # noqa: E402


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


class TestMaxPendingRestoresConstant:
    """Pin the constant so a future change is intentional."""

    def test_constant_exists_and_is_8(self) -> None:
        """``_MAX_PENDING_RESTORES = 8``, far above the 1-2 entries normal"""
        assert manager_mod._MAX_PENDING_RESTORES == 8

    def test_constant_is_int(self) -> None:
        """The constant is an int (not a float / string) so the ``len()``"""
        assert isinstance(manager_mod._MAX_PENDING_RESTORES, int)


class TestPendingRestoresCapForceRestore:
    """When ``_pending_restores`` is at the cap, appending a new entry"""

    def test_cap_not_hit_does_not_force_restore(self) -> None:
        """When the list is below the cap, no force-restore happens, the"""
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

            # Below the cap, populate the list with a few entries,
            with clip_mod._pending_restores_lock:
                for i in range(5):
                    other_snap = make_clipboard_snapshot()
                    clip_mod._pending_restores.append((cm, other_snap, f"old-{i}", 0.0))
                clip_mod._pending_restores.append(entry)

            # Now call _delayed_restore to drain one (simulating normal
            cm._delayed_restore(snap, "pasted", 0.0, entry)

        assert mock_restore.call_count == 1

    def test_cap_hit_force_restores_oldest_snapshot(self) -> None:
        """When the list is AT the cap (64 entries), appending the 65th"""
        cm = make_clipboard_manager()
        new_snap = make_clipboard_snapshot()

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
            # Isolate the cap-overflow logic from keystroke dispatch:
            patch.object(cm, "_dispatch_keystroke", return_value=True),
        ):
            mock_time.monotonic = MagicMock(return_value=100.0)
            mock_time.sleep = MagicMock()
            # Stub out the Thread so no real daemon thread spawns.
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            # Call paste() with a snapshot, this triggers the cap logic.
            cm.paste(snapshot=new_snap, pasted_text="new")

        oldest_snap_restore.assert_called_once_with()
        # After paste(), the list should still be at the cap (we removed
        with clip_mod._pending_restores_lock:
            entries = list(clip_mod._pending_restores)
        entry_snapshots = [e[1] for e in entries]
        assert not any(s is oldest_snap for s in entry_snapshots)
        assert any(s is new_snap for s in entry_snapshots)
        assert len(entries) == manager_mod._MAX_PENDING_RESTORES

    def test_cap_hit_force_restore_failure_does_not_break_append(self) -> None:
        """append STILL happens, we don't lose the new entry just because"""
        cm = make_clipboard_manager()
        new_snap = make_clipboard_snapshot()

        # Populate the list to the cap with an oldest snapshot whose
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
            # Isolate the cap-overflow logic from keystroke dispatch:
            patch.object(cm, "_dispatch_keystroke", return_value=True),
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
        entry_snapshots = [e[1] for e in entries]
        assert any(s is new_snap for s in entry_snapshots)
        # The oldest entry was popped (force-restore attempt was made).
        assert not any(s is oldest_snap for s in entry_snapshots)

    def test_cap_hit_restores_exactly_one_oldest_not_all(self) -> None:
        """When the cap is hit, ONLY the single oldest entry is force-restored"""
        cm = make_clipboard_manager()
        new_snap = make_clipboard_snapshot()

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
            # Isolate the cap-overflow logic from keystroke dispatch:
            patch.object(cm, "_dispatch_keystroke", return_value=True),
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
