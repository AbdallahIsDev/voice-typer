"""Structural pin: split-package mixins must declare their host-provided
members.

The pyrefly floor is a COUNT gate, a fresh error in a split-package
mixin can hide under the floor (exactly the stale-headroom failure
mode this session fixed: 84 native_hotkeys + 19 microphone_watcher
errors rode silently under the stale floor). This test pins the
source-level fix itself: every
mixin that reads composed-class state must carry the annotation-only
host-provided declarations (and, for cross-mixin methods, the
TYPE_CHECKING-only stubs) that make those reads type-check. Deleting
the declarations re-introduces the missing-attribute errors under the
floor's headroom, this test catches that immediately, without
running pyrefly.

Runs on any platform (pure AST inspection, no pyrefly dependency).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# (file, class, annotation-only member declarations the class must carry)
MIXIN_HOST_MEMBERS: dict[str, dict[str, set[str]]] = {
    "voice_typer/server/native_hotkeys/_spawn.py": {
        "_SpawnMixin": {
            "platform_name",
            "hotkey_str",
            "_binary_path",
            "_native_log_path",
            "_process",
            "_reader_thread",
            "_watchdog_thread",
            "_watchdog_stop_event",
            "_failed",
            "_error_message",
            "_last_event_received_at",
            "_last_pong_received_at",
        },
    },
    "voice_typer/server/native_hotkeys/_reader.py": {
        "_ReaderMixin": {
            "platform_name",
            "_process",
            "_stop_event",
            "_ready_event",
            "_restart_lock",
            "_restart_attempts",
            "_failed",
            "_error_message",
            "_binary_version",
            "_on_permanent_failure_callback",
            "_on_error_callback",
            "_on_warn_callback",
            "_WIRE_HANDLERS",
        },
    },
    "voice_typer/server/native_hotkeys/_watchdog.py": {
        "_WatchdogMixin": {
            "platform_name",
            "_process",
            "_stop_event",
            "_watchdog_stop_event",
            "_last_event_received_at",
            "_last_pong_received_at",
            "_pong_supported",
            "_shutdown_requested",
            "_callback",
            "_on_watchdog_restart_callback",
        },
    },
    "voice_typer/server/native_hotkeys/_matching.py": {
        "_MatchingMixin": {
            "platform_name",
            "_match_lock",
            "_held_modifiers",
            "_fn_down",
            "_main_key_down",
            "_parsed",
            "_extra_matchers",
            "_on_release_callback",
        },
    },
    "voice_typer/server/microphone_watcher/_linux.py": {
        "_LinuxMixin": {
            "_stop_event",
            "_poll_interval",
            "_idle_poll_interval_s",
            "_active_poll_interval_s",
            "_is_idle",
            "_on_default_device_changed",
        },
    },
    "voice_typer/server/microphone_watcher/_macos.py": {
        "_MacOSMixin": {
            "_stop_event",
            "_poll_interval",
            "_idle_poll_interval_s",
            "_active_poll_interval_s",
            "_is_idle",
        },
    },
}

# (file, class, TYPE_CHECKING-only method stubs the class must carry)
MIXIN_STUB_METHODS: dict[str, dict[str, set[str]]] = {
    "voice_typer/server/native_hotkeys/_spawn.py": {
        "_SpawnMixin": {"_reader_loop", "_watchdog_loop"},
    },
    "voice_typer/server/native_hotkeys/_reader.py": {
        "_ReaderMixin": {"_spawn_process"},
    },
    "voice_typer/server/native_hotkeys/_watchdog.py": {
        "_WatchdogMixin": {"start", "stop"},
    },
    "voice_typer/server/microphone_watcher/_linux.py": {
        "_LinuxMixin": {"_invoke_callback"},
    },
    "voice_typer/server/microphone_watcher/_macos.py": {
        "_MacOSMixin": {"_invoke_callback", "_device_signature"},
    },
    "voice_typer/server/microphone_watcher/_windows.py": {
        "_WindowsMixin": {"_invoke_callback"},
    },
}


def _class_node(tree: ast.Module, class_name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"class {class_name} not found")


def _annotated_members(cls: ast.ClassDef) -> set[str]:
    """Names declared as annotation-only statements in the class body."""
    names: set[str] = set()
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
            targets = stmt.target
            if isinstance(targets, ast.Name):
                names.add(targets.id)
    return names


def _type_checking_stub_methods(cls: ast.ClassDef) -> set[str]:
    """Method names defined under an ``if TYPE_CHECKING:`` block."""
    names: set[str] = set()
    for stmt in cls.body:
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if not is_tc:
            continue
        for inner in stmt.body:
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(inner.name)
            # @staticmethod-decorated stubs inside the block count too.
            elif isinstance(inner, ast.ClassDef):
                pass
    return names


def test_mixin_host_member_declarations_present() -> None:
    """Every split-package mixin still declares its host-provided state."""
    for rel_path, classes in MIXIN_HOST_MEMBERS.items():
        tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"), filename=rel_path)
        for class_name, expected in classes.items():
            declared = _annotated_members(_class_node(tree, class_name))
            missing = expected - declared
            assert not missing, (
                f"{rel_path}: {class_name} lost host-provided member "
                f"declaration(s) {sorted(missing)}, re-add the "
                f"annotation-only declaration so pyrefly keeps resolving "
                f"the composed-class attribute (missing declarations were "
                f"the original error source)."
            )


def test_mixin_type_checking_stubs_present() -> None:
    """Every cross-mixin method reference still has its TYPE_CHECKING stub."""
    for rel_path, classes in MIXIN_STUB_METHODS.items():
        tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"), filename=rel_path)
        for class_name, expected in classes.items():
            stubs = _type_checking_stub_methods(_class_node(tree, class_name))
            missing = expected - stubs
            assert not missing, (
                f"{rel_path}: {class_name} lost the TYPE_CHECKING stub(s) "
                f"{sorted(missing)} for sibling-mixin methods, without the "
                f"stub the cross-mixin method reference is an untyped "
                f"missing-attribute error again."
            )


def test_linux_default_device_callback_none_guarded() -> None:
    """_on_default_device_changed must be None-guarded before the call.

    Pins the real bug fix from the wave-3 reconcile: the Linux mixin
    called the optional callback unguarded, so an unregistered callback
    raised TypeError (caught and logged at EXCEPTION level by the
    surrounding handler) instead of being a clean no-op.
    """
    src = (REPO_ROOT / "voice_typer/server/microphone_watcher/_linux.py").read_text(encoding="utf-8")
    # The guarded shape: local binding + None check before the call.
    assert "callback = self._on_default_device_changed" in src
    assert "if callback is None:" in src
    assert "self._on_default_device_changed()" not in src
