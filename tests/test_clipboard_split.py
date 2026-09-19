"""clipboard/manager.py split: structural + delegation tests."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (  # noqa: E402
    manager as mgr_mod,  # noqa: E402
    restore,
    safety,
)


class TestSubmodulesExist:
    """The two new submodules created by  exist and are importable."""

    def test_restore_module_exists_with_expected_symbols(self):
        """``clipboard/restore.py`` exists and exposes the registry + impls."""
        pkg_dir = Path(clip_mod.__file__).parent
        assert (pkg_dir / "restore.py").is_file(), "restore.py must exist in the clipboard package"
        for name in (
            "_pending_restores",
            "_pending_restores_lock",
            "_MAX_PENDING_RESTORES",
            "_force_restore_pending_at_exit",
            "_delayed_restore_impl",
            "_restore_now_impl",
        ):
            assert hasattr(restore, name), f"restore.py must expose {name!r}"

    def test_safety_module_exists_with_expected_symbols(self):
        """``clipboard/safety.py`` exists and exposes the 4 safety impls."""
        pkg_dir = Path(clip_mod.__file__).parent
        assert (pkg_dir / "safety.py").is_file(), "safety.py must exist in the clipboard package"
        for name in (
            "_is_safe_paste_target_impl",
            "_is_terminal_process_impl",
            "_detect_focused_process_impl",
            "_get_frontmost_pid_macos_impl",
        ):
            assert hasattr(safety, name), f"safety.py must expose {name!r}"


class TestRegistryIdentityInvariants:
    """three namespaces (``clipboard``, ``clipboard.manager``,"""

    def test_pending_restores_is_same_object_across_namespaces(self):
        assert clip_mod._pending_restores is mgr_mod._pending_restores
        assert mgr_mod._pending_restores is restore._pending_restores

    def test_pending_restores_lock_is_same_object_across_namespaces(self):
        assert clip_mod._pending_restores_lock is mgr_mod._pending_restores_lock
        assert mgr_mod._pending_restores_lock is restore._pending_restores_lock

    def test_max_pending_restores_value(self):
        assert restore._MAX_PENDING_RESTORES == 8
        assert mgr_mod._MAX_PENDING_RESTORES == 8

    def test_force_restore_pending_at_exit_is_same_object(self):
        assert clip_mod._force_restore_pending_at_exit is mgr_mod._force_restore_pending_at_exit
        assert mgr_mod._force_restore_pending_at_exit is restore._force_restore_pending_at_exit

    def test_mutations_through_manager_visible_through_restore(self):
        """Appending through ``manager._pending_restores`` must be visible"""
        with restore._pending_restores_lock:
            restore._pending_restores.clear()
            restore._pending_restores.append(("sentinel-manager-mutation",))
        try:
            assert restore._pending_restores == [("sentinel-manager-mutation",)]
            # Now mutate through the manager namespace and check restore sees it.
            with mgr_mod._pending_restores_lock:
                mgr_mod._pending_restores.append(("via-mgr-namespace",))
            assert restore._pending_restores[-1] == ("via-mgr-namespace",)
        finally:
            with restore._pending_restores_lock:
                restore._pending_restores.clear()


class TestDelegatorContract:
    """The extracted methods remain on ``ClipboardManager`` as thin"""

    def test_delayed_restore_signature_preserved(self):
        """``_delayed_restore`` must keep the 4-arg + ``pending_entry=None``"""
        sig = inspect.signature(clip_mod.ClipboardManager._delayed_restore)
        params = list(sig.parameters)
        assert params == ["self", "snapshot", "pasted_text", "delay", "pending_entry"], (
            f"_delayed_restore signature must be (self, snapshot, pasted_text, delay, pending_entry=None); got {params}"
        )
        assert sig.parameters["pending_entry"].default is None, (
            "pending_entry must default to None (legacy 3-arg call compatibility)"
        )

    def test_restore_now_signature_preserved(self):
        sig = inspect.signature(clip_mod.ClipboardManager.restore_now)
        assert list(sig.parameters) == ["self", "snapshot"]

    def test_is_safe_paste_target_delegates_to_impl(self):
        """return value of ``ClipboardManager._is_safe_paste_target()`` —"""
        original = mgr_mod._is_safe_paste_target_impl
        sentinel = object()
        try:
            mgr_mod._is_safe_paste_target_impl = lambda: sentinel  # type: ignore[assignment]
            assert clip_mod.ClipboardManager._is_safe_paste_target() is sentinel
        finally:
            mgr_mod._is_safe_paste_target_impl = original  # type: ignore[assignment]
        # Restored: returns a real bool.
        assert clip_mod.ClipboardManager._is_safe_paste_target() in (True, False)

    def test_is_terminal_process_delegates_to_impl(self):
        original = mgr_mod._is_terminal_process_impl
        try:
            mgr_mod._is_terminal_process_impl = lambda name: True  # type: ignore[assignment]
            assert clip_mod.ClipboardManager._is_terminal_process("whatever") is True
        finally:
            mgr_mod._is_terminal_process_impl = original  # type: ignore[assignment]

    def test_detect_focused_process_delegates_to_impl(self):
        original = mgr_mod._detect_focused_process_impl
        sentinel = "fake-proc"
        try:
            mgr_mod._detect_focused_process_impl = lambda: sentinel  # type: ignore[assignment]
            assert clip_mod.ClipboardManager._detect_focused_process() == sentinel
        finally:
            mgr_mod._detect_focused_process_impl = original  # type: ignore[assignment]

    def test_get_frontmost_pid_macos_delegates_to_impl(self):
        original = mgr_mod._get_frontmost_pid_macos_impl
        try:
            mgr_mod._get_frontmost_pid_macos_impl = lambda: 99999  # type: ignore[assignment]
            assert clip_mod.ClipboardManager._get_frontmost_pid_macos() == 99999
        finally:
            mgr_mod._get_frontmost_pid_macos_impl = original  # type: ignore[assignment]

    def test_delayed_restore_delegates_to_impl(self):
        """``ClipboardManager._delayed_restore`` must forward all 4 args to"""
        original = mgr_mod._delayed_restore_impl
        calls: list[tuple] = []
        try:

            def spy(mgr, snapshot, pasted_text, delay, pending_entry=None):
                calls.append((mgr, snapshot, pasted_text, delay, pending_entry))

            mgr_mod._delayed_restore_impl = spy  # type: ignore[assignment]
            cm = clip_mod.ClipboardManager.__new__(clip_mod.ClipboardManager)
            cm._delayed_restore("snap", "text", 0.0, "entry")
            assert calls == [(cm, "snap", "text", 0.0, "entry")]
        finally:
            mgr_mod._delayed_restore_impl = original  # type: ignore[assignment]

    def test_restore_now_delegates_to_impl(self):
        original = mgr_mod._restore_now_impl
        calls: list[tuple] = []
        try:

            def spy(mgr, snapshot):
                calls.append((mgr, snapshot))

            mgr_mod._restore_now_impl = spy  # type: ignore[assignment]
            cm = clip_mod.ClipboardManager.__new__(clip_mod.ClipboardManager)
            cm.restore_now("snap")
            assert calls == [(cm, "snap")]
        finally:
            mgr_mod._restore_now_impl = original  # type: ignore[assignment]


class TestManagerSlimmed:
    """``ClipboardManager``'s class body), but it MUST be well under 1417."""

    def test_manager_py_is_smaller_than_pre_split(self):
        mgr_pkg_dir = Path(mgr_mod.__file__).parent
        loc = sum(1 for leaf in sorted(mgr_pkg_dir.glob("*.py")) for _ in leaf.open(encoding="utf-8"))
        assert loc < 1417, (
            f"manager package must be smaller than the pre-split 1417 LOC; got {loc}. "
            " split did not actually remove code."
        )
        # Sanity floor: the slim manager must still contain __init__,
        assert loc >= 400, (
            f"manager package is suspiciously small ({loc} LOC), did the split "
            "accidentally delete preserved methods (copy/paste/__init__)?"
        )

    def test_restore_and_safety_modules_substantive(self):
        """The extracted modules must carry real implementation, not be"""
        with Path(restore.__file__).open(encoding="utf-8") as _rf:
            restore_loc = sum(1 for _ in _rf)
        with Path(safety.__file__).open(encoding="utf-8") as _sf:
            safety_loc = sum(1 for _ in _sf)
        assert restore_loc >= 200, f"restore.py too small ({restore_loc} LOC), extraction incomplete?"
        assert safety_loc >= 200, f"safety.py too small ({safety_loc} LOC), extraction incomplete?"


class TestPublicAPIPreserved:
    """The symbols that existed on the package before the split must still"""

    @pytest.mark.parametrize(
        "name",
        [
            "ClipboardCopyError",
            "ClipboardManager",
            "_force_restore_pending_at_exit",
            "_pending_restores",
            "_pending_restores_lock",
        ],
    )
    def test_symbol_importable_from_package(self, name):
        assert hasattr(clip_mod, name), f"package must expose {name!r}"

    @pytest.mark.parametrize(
        "name",
        [
            "ClipboardCopyError",
            "ClipboardManager",
            "_MAX_PENDING_RESTORES",
            "_force_restore_pending_at_exit",
            "_pending_restores",
            "_pending_restores_lock",
        ],
    )
    def test_symbol_importable_from_manager_submodule(self, name):
        assert hasattr(mgr_mod, name), f"clipboard.manager must expose {name!r}"

    def test_manager_all_lists_max_pending_restores(self):
        """``_MAX_PENDING_RESTORES`` must be in ``manager.__all__`` so"""
        assert "_MAX_PENDING_RESTORES" in mgr_mod.__all__
