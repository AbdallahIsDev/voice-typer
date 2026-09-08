"""Source-grep gate for the kill_process_tree shim removal.

The deprecated ``state::kill_process_tree`` shim in ``src-tauri/src/state.rs``
forwarded to ``crate::platform::process::kill_process_tree``. The shim was
kept only because four ``spawn.rs`` callers and the ``SidecarHandle::kill_tree``
method referenced it. With the migration complete:

* ``state.rs`` MUST NOT define ``pub(crate) fn kill_process_tree`` any more.
* ``SidecarHandle::kill_tree`` MUST call ``crate::platform::process::kill_process_tree``
  directly (not the unqualified ``kill_process_tree`` that used to resolve
  to the shim in the same module).
* the spawn module (``spawn.rs`` + the ``spawn/*.rs`` submodules from the
  EO-33 split) MUST NOT reference ``crate::state::kill_process_tree`` any more.
* the spawn module MUST route every process-tree kill through the shared
  ``kill_process_tree_off_thread`` helper in ``spawn/handshake_loop.rs``
  (the handshake-loop consolidation) — the helper holds the single
  ``crate::platform::process::kill_process_tree`` call site and is
  invoked once per handshake cleanup path.

These checks are static (read-the-source) so they run in the Linux sandbox
without needing a real sidecar process.
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
    """Concatenate the spawn module sources (spawn.rs + spawn/*.rs).

    EO-33 split the former single-file ``sidecar/spawn.rs`` into an
    orchestrator (``spawn.rs``) + six concern submodules
    (``spawn/dev_mode.rs``, ``spawn/release_mode.rs``,
    ``spawn/handshake.rs``, ``spawn/env_allowlist.rs``,
    ``spawn/prewarm.rs``, ``spawn/target_triple.rs``). The
    kill-process-tree callers live in the release-mode + dev-mode
    submodules, so this gate must scan the whole spawn module.
    """
    files = [_SPAWN_RS] + sorted(_SPAWN_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


def test_state_rs_kill_process_tree_shim_is_removed() -> None:
    """The deprecated ``state::kill_process_tree`` wrapper must be gone.

    The shim was a one-line forwarder to ``crate::platform::process::kill_process_tree``.
    Keeping it after the callers were migrated would be dead code that obscures
    the real call path. This test fails if anyone re-introduces the wrapper
    (e.g. as a "convenience" during a future refactor).
    """
    body = _read(_STATE_RS)
    # The actual shim signature was:
    #   pub(crate) fn kill_process_tree(pid: u32) {
    #       crate::platform::process::kill_process_tree(pid)
    #   }
    # We forbid any module-level `fn kill_process_tree` definition in state.rs.
    # (An `async fn kill_tree` is allowed — that's the SidecarHandle method
    # that delegates to the platform module.)
    shim_re = re.compile(r"^\s*pub\s*\([^)]*\)\s*fn\s+kill_process_tree\s*\(", re.MULTILINE)
    assert not shim_re.search(body), (
        "state.rs must NOT define `pub(crate) fn kill_process_tree` any more — "
        "the deprecated shim should be removed and all callers should invoke "
        "`crate::platform::process::kill_process_tree` directly."
    )


def test_state_rs_kill_tree_routes_to_platform_module() -> None:
    """``SidecarHandle::kill_tree`` must call the platform module directly.

    Previously the method called the unqualified ``kill_process_tree(pid)``
    which resolved to the same-module shim. Now that the shim is gone, the
    method must use the fully-qualified ``crate::platform::process::kill_process_tree``
    path so the build doesn't break.
    """
    body = _read(_HANDLE_RS)
    # Locate the kill_tree method body.
    kt_match = re.search(
        r"async\s+fn\s+kill_tree\s*\([^)]*\)\s*->\s*[^{]*\{",
        body,
    )
    assert kt_match, "handle.rs must define `async fn kill_tree`"
    # Take the slice from the method opening brace to the closing brace of
    # the method body (the first balanced `{ ... }` after kt_match.end()-1).
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
    # standalone call (the shim is gone, so this would be a compile error).
    unqualified_call_re = re.compile(r"(?<!\w)(?<!:)kill_process_tree\s*\(")
    # The qualified path contains `kill_process_tree(` as a substring —
    # exclude matches inside `crate::platform::process::kill_process_tree(`.
    unqualified_hits = [
        m for m in unqualified_call_re.finditer(method_body) if not _is_inside_qualified_path(method_body, m.start())
    ]
    assert not unqualified_hits, (
        "SidecarHandle::kill_tree must not call the unqualified "
        "`kill_process_tree(...)` — the shim is gone; use the fully-qualified "
        "`crate::platform::process::kill_process_tree(...)` path."
    )


def _is_inside_qualified_path(text: str, offset: int) -> bool:
    """Return True if ``offset`` is preceded by ``::`` (i.e. is the last
    segment of an absolute path like ``crate::platform::process::kill_process_tree``).
    """
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
        "the spawn module must not reference `crate::state::kill_process_tree` — "
        "the shim has been removed; callers must use "
        "`crate::platform::process::kill_process_tree` directly."
    )


def test_spawn_rs_uses_platform_module_exactly_four_times() -> None:
    """All spawn-module cleanup callers must route through the platform module.

    The four stdout-handshake loops (sidecar release/dev + worker
    release/dev) were copy-paste twins; their kill/drain/deadline
    semantics were consolidated into ``spawn/handshake_loop.rs``. All
    process-tree kills now route through ONE shared off-thread helper
    — ``kill_process_tree_off_thread`` — which contains the single
    ``crate::platform::process::kill_process_tree`` call site, and is
    invoked once per handshake cleanup path (release-path
    ``Terminated`` / ``Error`` arms, the server-started-deadline
    fallbacks, and the worker-path siblings). The platform module
    remains the ONLY tree-kill implementation in the spawn module —
    the state.rs shim must stay dead (guarded by the tests above).
    """
    body = _read_spawn_module()
    # The platform-module call now appears exactly ONCE — inside the
    # shared off-thread helper. Any NEW direct call site (bypassing
    # the helper) would show up here as a count > 1 and should be
    # routed through the helper instead.
    platform_matches = re.findall(r"crate::platform::process::kill_process_tree\s*\(", body)
    assert len(platform_matches) == 1, (
        f"the spawn module must call `crate::platform::process::kill_process_tree` "
        f"exactly once (inside the shared `kill_process_tree_off_thread` helper in "
        f"spawn/handshake_loop.rs); found {len(platform_matches)}. Route any new "
        f"kill path through the helper — do not duplicate the platform call."
    )
    # The helper is defined once and invoked from exactly six cleanup
    # paths (the handshake-loop arms enumerated in the docstring above).
    # Bump this pin deliberately when adding ANOTHER kill path — the
    # point is that every spawn kill routes through the shared helper,
    # never resurrects the state.rs shim, and never re-duplicates the
    # platform call.
    helper_calls = re.findall(r"(?<!fn )kill_process_tree_off_thread\s*\(", body)
    assert len(helper_calls) == 6, (
        f"the spawn module must invoke `kill_process_tree_off_thread` exactly 6 "
        f"times (one per handshake cleanup path); found {len(helper_calls)}."
    )
