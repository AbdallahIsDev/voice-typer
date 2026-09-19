"""
Source-grep gate for the kill_process_tree shim removal.
* ``state.rs`` MUST NOT define ``pub(crate) fn kill_process_tree`` any more.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC_TAURI = Path(__file__).resolve().parents[2] / "src-tauri"
_STATE_RS = _SRC_TAURI / "src" / "state.rs"
_HANDLE_RS = _SRC_TAURI / "src" / "sidecar" / "handle.rs"
_SPAWN_RS = _SRC_TAURI / "src" / "sidecar" / "spawn.rs"
_SPAWN_DIR = _SRC_TAURI / "src" / "sidecar" / "spawn"


def _read(path: Path) -> str:
    assert path.exists(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8")


def _read_spawn_module() -> str:
    """Concatenate the spawn module sources (spawn.rs + spawn/*.rs)."""
    files = [_SPAWN_RS] + sorted(_SPAWN_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


def test_state_rs_kill_process_tree_shim_is_removed() -> None:
    """The deprecated ``state::kill_process_tree`` wrapper must be gone."""
    body = _read(_STATE_RS)
    # The actual shim signature was:
    shim_re = re.compile(r"^\s*pub\s*\([^)]*\)\s*fn\s+kill_process_tree\s*\(", re.MULTILINE)
    assert not shim_re.search(body), (
        "state.rs must NOT define `pub(crate) fn kill_process_tree` any more, "
        "the deprecated shim should be removed and all callers should invoke "
        "`crate::platform::process::kill_process_tree` directly."
    )


def test_state_rs_kill_tree_routes_to_platform_module() -> None:
    """``SidecarHandle::kill_tree`` must call the platform module directly."""
    body = _read(_HANDLE_RS)
    # Locate the kill_tree method body.
    kt_match = re.search(
        r"async\s+fn\s+kill_tree\s*\([^)]*\)\s*->\s*[^{]*\{",
        body,
    )
    assert kt_match, "handle.rs must define `async fn kill_tree`"
    start = kt_match.end() - 1
    depth = 0
    end = start
    for i in range(start, len(body)):
        c = body[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    method_body = body[start:end]
    assert "crate::platform::process::kill_process_tree" in method_body, (
        "SidecarHandle::kill_tree must invoke "
        "`crate::platform::process::kill_process_tree` directly (not the "
        "removed same-module shim)."
    )
    # And it must NOT reference the shim via the unqualified name as a
    unqualified_call_re = re.compile(r"(?<!\w)(?<!:)kill_process_tree\s*\(")
    # The qualified path contains `kill_process_tree(` as a substring —
    unqualified_hits = [
        m for m in unqualified_call_re.finditer(method_body) if not _is_inside_qualified_path(method_body, m.start())
    ]
    assert not unqualified_hits, (
        "SidecarHandle::kill_tree must not call the unqualified "
        "`kill_process_tree(...)`, the shim is gone; use the fully-qualified "
        "`crate::platform::process::kill_process_tree(...)` path."
    )


def _is_inside_qualified_path(text: str, offset: int) -> bool:
    """Return True if ``offset`` is preceded by ``::`` (i.e. is the last"""
    # Walk backwards skipping whitespace.
    i = offset - 1
    while i >= 0 and text[i] in " \t\r\n":
        i -= 1
    if i < 1:
        return False
    return text[i - 1 : i + 1] == "::"


def test_spawn_rs_does_not_reference_state_shim() -> None:
    """The spawn module must not call the removed ``crate::state::kill_process_tree``."""
    body = _read_spawn_module()
    assert "crate::state::kill_process_tree" not in body, (
        "the spawn module must not reference `crate::state::kill_process_tree`, "
        "the shim has been removed; callers must use "
        "`crate::platform::process::kill_process_tree` directly."
    )


def test_spawn_rs_uses_platform_module_exactly_four_times() -> None:
    """All spawn-module cleanup callers must route through the platform module."""
    body = _read_spawn_module()
    platform_matches = re.findall(r"crate::platform::process::kill_process_tree\s*\(", body)
    assert len(platform_matches) == 1, (
        f"the spawn module must call `crate::platform::process::kill_process_tree` "
        f"exactly once (inside the shared `kill_process_tree_off_thread` helper in "
        f"spawn/handshake_loop.rs); found {len(platform_matches)}. Route any new "
        f"kill path through the helper, do not duplicate the platform call."
    )
    helper_calls = re.findall(r"(?<!fn )kill_process_tree_off_thread\s*\(", body)
    assert len(helper_calls) == 9, (
        f"the spawn module must invoke `kill_process_tree_off_thread` exactly 9 "
        f"times (one per handshake cleanup path); found {len(helper_calls)}."
    )
