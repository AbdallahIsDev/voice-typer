"""Source-grep gate for the kill_process_tree shim removal.

The deprecated ``state::kill_process_tree`` shim in ``src-tauri/src/state.rs``
forwarded to ``crate::platform::process::kill_process_tree``. The shim was
kept only because four ``spawn.rs`` callers and the ``SidecarHandle::kill_tree``
method referenced it. With the migration complete:

* ``state.rs`` MUST NOT define ``pub(crate) fn kill_process_tree`` any more.
* ``SidecarHandle::kill_tree`` MUST call ``crate::platform::process::kill_process_tree``
  directly (not the unqualified ``kill_process_tree`` that used to resolve
  to the shim in the same module).
* ``spawn.rs`` MUST NOT reference ``crate::state::kill_process_tree`` any more.
* ``spawn.rs`` MUST reference ``crate::platform::process::kill_process_tree``
  exactly six times (the four original spawn-timeout / Terminated / Error
  cleanup call sites that previously went through the shim, PLUS two
  later additions: a dev-mode server-started-deadline fallback and a
  shared-process cleanup path that were added after the shim removal).

These checks are static (read-the-source) so they run in the Linux sandbox
without needing a real sidecar process.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC_TAURI = Path(__file__).resolve().parents[2] / "src-tauri"
_STATE_RS = _SRC_TAURI / "src" / "state.rs"
_SPAWN_RS = _SRC_TAURI / "src" / "sidecar" / "spawn.rs"


def _read(path: Path) -> str:
    assert path.exists(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8")


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
    body = _read(_STATE_RS)
    # Locate the kill_tree method body.
    kt_match = re.search(
        r"async\s+fn\s+kill_tree\s*\([^)]*\)\s*->\s*[^{]*\{",
        body,
    )
    assert kt_match, "state.rs must define `async fn kill_tree`"
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
    """``spawn.rs`` must not call the removed ``crate::state::kill_process_tree``."""
    body = _read(_SPAWN_RS)
    assert "crate::state::kill_process_tree" not in body, (
        "spawn.rs must not reference `crate::state::kill_process_tree` — "
        "the shim has been removed; callers must use "
        "`crate::platform::process::kill_process_tree` directly."
    )


def test_spawn_rs_uses_platform_module_exactly_four_times() -> None:
    """All four spawn.rs cleanup callers must route through the platform module.

    The four call sites are:
      1. release-path ``Terminated`` arm — spawn-failure cleanup.
      2. release-path ``Error`` arm — spawn-failure cleanup.
      3. release-path server-started-deadline fallback — kill after timeout.
      4. dev-mode server-started-deadline fallback — kill after timeout.
    """
    body = _read(_SPAWN_RS)
    matches = re.findall(r"crate::platform::process::kill_process_tree\s*\(", body)
    # Six call sites: the four original spawn-timeout / Terminated / Error
    # cleanup paths (which migrated off the state.rs shim) + two later
    # additions (a dev-mode server-started-deadline fallback wrapped in
    # spawn_blocking, and a shared-process cleanup helper). Bump this pin
    # deliberately when adding ANOTHER kill path — the point is that
    # spawn.rs must route through the platform module, never resurrect
    # the state.rs shim.
    assert len(matches) == 6, (
        f"spawn.rs must call `crate::platform::process::kill_process_tree` "
        f"exactly 6 times (4 spawn-timeout/cleanup paths + 2 later "
        f"additions); found {len(matches)}."
    )
