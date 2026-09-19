"""Window-lifecycle parity pins for the Tauri host (MO-123 / MO-124)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ACL_MANIFESTS = REPO_ROOT / "src-tauri" / "gen" / "schemas" / "acl-manifests.json"
CAPABILITIES_DIR = REPO_ROOT / "src-tauri" / "capabilities"
WINDOW_CLOSE_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds" / "window_close.rs"
HOST_EVENTS_RS = REPO_ROOT / "src-tauri" / "src" / "host_events.rs"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing source file: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _expand_permissions(manifest: dict, granted: set[str]) -> set[str]:
    """Expand a capability's permission ids transitively."""
    resolved: set[str] = set()
    pending = list(granted)
    while pending:
        identifier = pending.pop()
        if identifier in resolved:
            continue
        resolved.add(identifier)
        namespace, _, local = identifier.rpartition(":")
        entry = manifest.get(namespace)
        if not isinstance(entry, dict) or not local:
            continue
        # Children are listed either fully qualified (`core:window:default`)
        for children in (
            (entry.get("default_permission") or {})
            if (entry.get("default_permission") or {}).get("identifier") == local
            else {},
            ((entry.get("permission_sets") or {}).get(local) or {}),
        ):
            for child in children.get("permissions", []):
                pending.append(child if ":" in child else f"{namespace}:{child}")
    return resolved


def _capability_permissions(identifier: str) -> set[str]:
    manifest = json.loads(ACL_MANIFESTS.read_text(encoding="utf-8"))
    for path in sorted(CAPABILITIES_DIR.glob("*.json")):
        capability = json.loads(path.read_text(encoding="utf-8"))
        if capability.get("identifier") == identifier:
            return _expand_permissions(manifest, set(capability.get("permissions", [])))
    pytest.fail(f"capability {identifier!r} not found under {CAPABILITIES_DIR}")


def test_main_runtime_grants_the_window_queries_the_bridge_calls() -> None:
    """MO-123: every `window_` window query/subscribe must be granted."""
    resolved = _capability_permissions("main-runtime")

    # Setters the bridge calls directly (explicit grants in the file).
    for required in (
        "core:window:allow-show",
        "core:window:allow-hide",
        "core:window:allow-set-focus",
        "core:window:allow-close",
        "core:window:allow-minimize",
        "core:window:allow-toggle-maximize",
        "core:window:allow-start-dragging",
        "core:window:allow-set-position",
    ):
        assert required in resolved, (
            f"{required} is not granted by the main-runtime capability; "
            "the corresponding window_ bridge call would be denied"
        )

    # Queries reached through the default sets (the MO-123 audit claim).
    assert "core:window:allow-is-maximized" in resolved, (
        "core:window:allow-is-maximized must be granted (via core:default → "
        "core:window:default): window_.isMaximized() runs on mount and on "
        "every resize, and a denied invoke leaves the maximize glyph and the "
        "rounded-corner state stale"
    )


def test_main_runtime_grants_on_resized_via_event_listen() -> None:
    """MO-123 second half: `window_.onResized()` must be granted."""
    resolved = _capability_permissions("main-runtime")

    assert "core:event:allow-listen" in resolved, (
        "core:event:allow-listen must be granted for window_.onResized(): "
        "onResized is an event-plugin subscription (not a window permission), "
        "and a denied listen leaves the maximize glyph / rounded-corner "
        "class stale on OS-snap / taskbar-drag / external resizes"
    )
    assert "core:window:allow-on-resized" not in resolved, (
        "core:window:allow-on-resized is not a valid Tauri v2 permission id; "
        "onResized is gated by core:event:allow-listen. A phantom grant here "
        "means someone invented an ACL id instead of reading the manifest."
    )
    manifest = json.loads(ACL_MANIFESTS.read_text(encoding="utf-8"))
    core_window_default = ((manifest.get("core:window") or {}).get("default_permission") or {}).get("permissions") or []
    assert "allow-on-resized" not in core_window_default, (
        "the core:window default set must not grow a fake allow-on-resized "
        "entry; if upstream ever adds one, update BOTH the capability grant "
        "and this pin together"
    )


def test_window_close_pairs_skip_taskbar_with_hide() -> None:
    """MO-124: the close-to-tray hide must also skip the taskbar entry."""
    source = _read(WINDOW_CLOSE_RS)
    hide_block = source[source.index("api.prevent_close();") :]
    assert "set_skip_taskbar(true)" in hide_block, (
        "close-to-tray must call set_skip_taskbar(true) next to hide() "
        "(a hidden window keeping a taskbar button reads as "
        "a ghost entry)"
    )
    assert re.search(r"if let Err\(e\) = window\.set_skip_taskbar\(true\)", hide_block), (
        "the skip-taskbar call must stay best-effort (warn, never panic)"
    )


def test_show_path_restores_the_taskbar_entry_once() -> None:
    """MO-124 (show half): the restore lives in the ONE shared raise"""
    source = _read(HOST_EVENTS_RS)
    start = source.index("fn raise_main_window(")
    body = source[start : source.index("\n}\n", start)]
    call = "window.set_skip_taskbar(false)"
    assert body.count(call) == 1, (
        "raise_main_window must clear the hidden-launch taskbar skip exactly once; per-path copies drift"
    )
    # ...and the whole file keeps only that one call site, so a second show
    assert source.count(call) == 1
