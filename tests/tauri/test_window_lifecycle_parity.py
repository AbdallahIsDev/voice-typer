"""Window-lifecycle parity pins for the Tauri host (MO-123 / MO-124).

Two Electron behaviors the audit flagged as missing under Tauri:

- **MO-124**: close-to-tray must pair ``hide()`` with
  ``setSkipTaskbar(true)`` (and restore it on show), otherwise a
  "hidden" app keeps a ghost taskbar button.
- **MO-123**: the ``window_`` bridge calls ``isMaximized()`` and
  ``onResized()`` on mount and on every resize. Both must be granted:

  1. ``isMaximized()`` → ``core:window:allow-is-maximized``, reached via
     ``core:default`` → ``core:window:default`` (explicit pin below).
  2. ``onResized()`` → NOT a window permission. The JS API implements it
     as ``listen(TAURI_RESIZE_EVENT)`` + an ``isMaximized()`` re-query,
     so it is gated by ``core:event:allow-listen`` (also reached via
     ``core:default``). There is no ``core:window:allow-on-resized`` id
     in the Tauri v2 ACL; adding one fails the build script with
     ``Permission core:window:allow-on-resized not found``.

MO-123 is therefore COVERED by the two set-expansion grants, pinned
below so removing ``core:default`` / ``core:event:default`` (or adding
a bridge subscribe without a grant) fails loudly instead of silently
desyncing the UI.
"""

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
    """Expand a capability's permission ids transitively.

    ``core:default`` is a permission *set* whose ``permissions`` list names
    other sets (``core:window:default``, …), so the raw capability JSON
    never mentions ``allow-is-maximized`` at all. Resolving the sets is what
    makes a coverage assertion meaningful.
    """
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
        # or as bare local names (`allow-is-maximized`); bare names belong to
        # the namespace that declares them, so qualify them here. Without
        # this the resolved set holds unqualified ids and a coverage check
        # against a full identifier can never match.
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
    """MO-123 second half: `window_.onResized()` must be granted.

    `onResized` is a webview event subscription, gated by the event
    plugin — there is NO `core:window:allow-on-resized` permission in
    the Tauri v2 ACL (verified against the generated
    `acl-manifests.json` and against the build script, which fails with
    ``Permission core:window:allow-on-resized not found`` when a
    phantom grant is added). The JS API is
    ``listen(TAURI_RESIZE_EVENT)`` + an ``isMaximized()`` re-query, so
    the grant that matters is ``core:event:allow-listen``.

    Both halves of MO-123 fail loudly if either grant regresses.
    """
    resolved = _capability_permissions("main-runtime")

    assert "core:event:allow-listen" in resolved, (
        "core:event:allow-listen must be granted for window_.onResized(): "
        "onResized is an event-plugin subscription (not a window permission), "
        "and a denied listen leaves the maximize glyph / rounded-corner "
        "class stale on OS-snap / taskbar-drag / external resizes"
    )
    # Negative pin: the ACL does not define a window permission for
    # onResized. Adding `core:window:allow-on-resized` to any capability
    # JSON fails the Tauri build script ("Permission ... not found").
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
        "(Electron parity: a hidden window keeping a taskbar button reads as "
        "a ghost entry)"
    )
    assert re.search(r"if let Err\(e\) = window\.set_skip_taskbar\(true\)", hide_block), (
        "the skip-taskbar call must stay best-effort (warn, never panic)"
    )


def test_show_path_restores_the_taskbar_entry_once() -> None:
    """MO-124 (show half): the restore lives in the ONE shared raise
    routine every show path routes through (E7)."""
    source = _read(HOST_EVENTS_RS)
    start = source.index("fn raise_main_window(")
    body = source[start : source.index("\n}\n", start)]
    call = "window.set_skip_taskbar(false)"
    assert body.count(call) == 1, (
        "raise_main_window must clear the hidden-launch taskbar skip exactly once; per-path copies drift"
    )
    # ...and the whole file keeps only that one call site, so a second show
    # path cannot grow its own copy.
    assert source.count(call) == 1
