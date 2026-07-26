"""d-review Finding 5 regression guard + CR-4 Rust/TS allowlist parity.

SECURITY.md documents the number of IPC commands in the Electron main
process's ``ALLOWED_COMMANDS`` allowlist. Finding 5 noted the doc had
stale "~35 commands" while the real allowlist had grown. This test
parses the documented count out of SECURITY.md and asserts it matches
the actual ``ALLOWED_COMMANDS`` ``Set`` entries in
``voice_typer/client/src/main/allowed-commands.ts`` (canonical
declaration since R6-F10 — was previously inline in ``index.ts``),
so the doc can't silently drift again when commands are added or
removed.

The same count is also asserted in the allowlist parity test
(``tests/test_electron_ipc_and_build.py``), which cross-checks the
renderer allowlist against the server command registry — together they
keep the security docs, the renderer allowlist, and the server registry
in lockstep.

CR-4 (Fix-C): a second parity check was added — the Rust host's
``ALLOWED_COMMANDS`` set in ``src-tauri/src/commands/sidecar_cmds.rs``
must mirror the TS renderer allowlist EXACTLY (same count + same
entries). The Rust gate is the defense-in-depth backstop for a
compromised-renderer attack (XSS in the WebView →
``invoke('dispatch', {cmd:'<arbitrary>'})``); if the Rust set drifts
from the TS set, either (a) a command the renderer can invoke gets
silently rejected by Rust (broken UX), or (b) a command the Rust gate
allows but the TS gate doesn't creates an attack surface that only a
compromised renderer can reach (security hole).

PVT-G5-009: the ``INDEX_TS`` path constant was previously pointing at
``index.ts`` (where the literal USED to live inline). R6-F10 moved the
canonical declaration to ``allowed-commands.ts``; ``index.ts`` now only
re-exports it. The path was updated to point at the canonical file.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
# PVT-G5-009: previously pointed at `index.ts`, but R6-F10 moved the
# canonical `ALLOWED_COMMANDS = new Set([...])` literal out of `index.ts`
# into its own dependency-free leaf module `allowed-commands.ts`
# (`index.ts:56` now just re-exports it). The parity parsers below look
# for the literal substring `"ALLOWED_COMMANDS = new Set"`, which no
# longer exists in `index.ts` — the test was silently erroring out with
# `StopIteration` (in `_count_allowed_commands`'s `next(...)` without a
# default) or `ValueError: substring not found` (in the
# `test_electron_ipc_and_build.py` fixture). Pointing at the canonical
# file restores the cross-layer safety net.
INDEX_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "allowed-commands.ts"
# CR-4 (Fix-C): Rust host's allowlist literal — kept in lockstep with
# the TS allowlist by this parity test. Both files MUST be updated in
# the same PR when a command is added or removed.
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"

# S4-CR-18 (this session): Python backend's ``_COMMAND_REGISTRY`` literal —
# asserted to match the renderer allowlist PLUS the documented set of
# host-only commands (routed by the Rust host directly, never via the
# renderer's ``dispatch`` path). If this count drifts from
# ``allowed-commands.ts`` + ``_HOST_ONLY_COMMANDS`` (in either direction),
# the test below fails with the drifted file's name.
IPC_SERVER_PY = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"

# S4-CR-18: commands present in ``_COMMAND_REGISTRY`` but intentionally
# absent from the renderer ``ALLOWED_COMMANDS`` because the Rust host
# routes them directly (they don't go through the renderer's
# ``dispatch`` path). When a host-only command is added or removed,
# this set MUST be updated in the same PR.
_HOST_ONLY_COMMANDS = frozenset({
    "shutdown": (
        "Tauri cooperative-shutdown command (EC-FIX-2 / EC-9). The "
        "Rust host invokes it via the WS transport's special-case "
        "intercept; the renderer never dispatches it."
    ),
    "tray_click": (
        "Tauri tray-menu click dispatch (ADR-0020 §6.5 / §16). The "
        "Rust host forwards a clicked menu item id; the renderer has "
        "no tray-menu UI."
    ),
})


def _count_allowed_commands() -> int:
    """Count quoted command strings inside the ALLOWED_COMMANDS Set block."""
    lines = INDEX_TS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "ALLOWED_COMMANDS = new Set" in line)
    count = 0
    for line in lines[start:]:
        if line.strip().startswith("]);"):
            break
        if re.match(r'\s*"([a-z_]+)"', line):
            count += 1
    return count


def _allowed_commands_ts() -> set[str]:
    """Return the set of command names in the TS ALLOWED_COMMANDS block.

    Used by the CR-4 Rust/TS parity test to assert exact entry-level
    parity (not just count-level) — a count match with a different
    entry set (e.g. ``quit_app`` in TS but ``quit`` in Rust) would
    pass the count check but break at runtime.
    """
    lines = INDEX_TS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "ALLOWED_COMMANDS = new Set" in line)
    out: set[str] = set()
    for line in lines[start:]:
        if line.strip().startswith("]);"):
            break
        m = re.match(r'\s*"([a-z_]+)"', line)
        if m:
            out.add(m.group(1))
    return out


def _allowed_commands_rust() -> set[str]:
    """Return the set of command names in the Rust ALLOWED_COMMANDS literal.

    The Rust source stores them as a ``&[&str]`` slice literal inside
    the ``allowed_commands()`` function's ``get_or_init`` closure. We
    slice from the ``let cmds: &[&str] = &[`` line to the closing
    ``];`` and extract each ``"<name>"`` token.

    This mirrors the TS-side parser so a contributor adding a command
    in only one of the two files gets a clear, actionable test failure
    pointing at the file that's missing the entry.
    """
    src = SIDECAR_CMDS_RS.read_text(encoding="utf-8")
    # Anchor on the ``let cmds: &[&str] = &[`` line — this is the
    # start of the literal that backs the Rust ALLOWED_COMMANDS set.
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "src-tauri/src/commands/sidecar_cmds.rs no longer declares the "
        "`let cmds: &[&str] = &[` literal inside `allowed_commands()`. "
        "Did the constructor shape change? Update this parser to match."
    )
    body = src[m_start.end() :]
    # The literal ends with ``];`` — find the first one after the start.
    m_end = re.search(r"\];", body)
    assert m_end is not None, (
        "src-tauri/src/commands/sidecar_cmds.rs: could not find the "
        "closing `];` of the `let cmds: &[&str] = &[` literal. "
        "Update this parser if the literal shape changed."
    )
    literal = body[: m_end.start()]
    # Each entry is a quoted string ``"command_name"`` on its own line
    # (the literal is one-per-line for readability + diff-friendliness).
    return set(re.findall(r'"([a-z_]+)"', literal))


def _documented_count() -> int | None:
    """Extract the documented allowlist count from SECURITY.md.

    Matches prose like "only the 68 commands listed in ALLOWED_COMMANDS"
    or "only the 35 commands".
    """
    text = SECURITY_MD.read_text(encoding="utf-8")
    # Strip Markdown emphasis markers so "**68**" matches like "68".
    text = text.replace("**", "")
    m = re.search(r"(\d+)\s+commands?\s+(listed\s+in\s+)?`?ALLOWED_COMMANDS`?", text)
    return int(m.group(1)) if m else None


def test_security_md_allowlist_count_matches_source() -> None:
    """SECURITY.md must document the IPC allowlist count accurately.

    YJ-10 + YJ-FIX-A2 reconciliation note (session YJ, Group 5):

    This test was temporarily downgraded to a soft-warn in YJ-FIX-A because
    no agent in that wave owned ``SECURITY.md`` (a docs file outside the
    fix-subagents' file scope), so the doc was stale: it still documented
    76 commands while the TS renderer source had 59 (GT-32 had removed 17
    stale entries, and YJ-10 reconciled the Rust allowlist to match). The
    soft-warn surfaced the drift in CI logs without blocking the build.

    YJ-FIX-A2 (this session) updated ``SECURITY.md`` to reflect the
    current 59-entry renderer allowlist AND the actual 78-handler
    ``_COMMAND_REGISTRY`` count (19 of those 78 handlers are intentionally
    absent from the renderer allowlist — ``tray_click``, ``shutdown``, and
    the 17 GT-32-removed commands). With ``SECURITY.md`` now accurate, the
    strict ``assert documented == actual`` is restored below as the
    regression guard, so any future drift between ``SECURITY.md`` and the
    renderer allowlist is caught hard at CI time.

    The YJ-10 Rust ↔ TS count parity invariant is also asserted here
    (duplicated from ``test_rust_allowlist_matches_ts_allowlist_count``)
    so this test stays useful as a parity guard.
    """
    actual = _count_allowed_commands()
    # YJ-10 invariant: Rust ↔ TS allowlist count MUST match. This is
    # the regression-guard portion of the test (the YJ-10 fix's primary
    # contract). Already enforced separately by
    # `test_rust_allowlist_matches_ts_allowlist_count`, but duplicated
    # here so this test stays useful as a parity guard.
    rust_count = len(_allowed_commands_rust())
    assert rust_count == actual, (
        f"Rust ALLOWED_COMMANDS has {rust_count} entries but the TS "
        f"renderer source defines {actual}. YJ-10 parity broken — "
        f"update both files in the same PR."
    )

    documented = _documented_count()
    assert documented is not None, (
        "SECURITY.md no longer documents the ALLOWED_COMMANDS count in a "
        "parseable form (expected: 'only the N commands listed in "
        "ALLOWED_COMMANDS')."
    )
    # YJ-FIX-A2: strict regression guard restored. SECURITY.md was updated
    # in this same fix wave to document 59 entries (matching the
    # `allowed-commands.ts` count after GT-32's 17-command prune +
    # YJ-10's Rust reconciliation). If this assertion fires, either
    # SECURITY.md drifted (a contributor added/removed a command in the
    # renderer allowlist without updating the doc) or the parser in
    # `_documented_count` needs updating because the prose shape changed.
    assert documented == actual, (
        f"SECURITY.md documents {documented} ALLOWED_COMMANDS but the "
        f"renderer source defines {actual}. SECURITY.md is stale — "
        f"update the count in the 'Command Allowlist (SEC-019)' section "
        f"(and the surrounding prose about the Python `_COMMAND_REGISTRY` "
        f"handler count, which is now {len(_allowed_commands_rust())} "
        f"Rust entries ↔ {actual} TS entries ↔ 78 Python handlers)."
    )


def test_allowed_commands_nonempty() -> None:
    # Sanity: the allowlist must never be empty.
    assert _count_allowed_commands() > 0


# ─── CR-4 (Fix-C): Rust ↔ TS allowlist parity ──────────────────────────


def test_rust_allowlist_matches_ts_allowlist_count() -> None:
    """The Rust allowlist must contain the SAME NUMBER of commands as TS.

    A count mismatch means a command was added in one file but not the
    other — the next test (``test_rust_allowlist_matches_ts_allowlist_entries``)
    pinpoints which one.
    """
    ts_count = len(_allowed_commands_ts())
    rust_count = len(_allowed_commands_rust())
    assert rust_count == ts_count, (
        f"Rust ALLOWED_COMMANDS has {rust_count} entries but TS has "
        f"{ts_count}. Both files MUST be updated in the same PR when a "
        f"command is added or removed. Files:\n"
        f"  - Rust: src-tauri/src/commands/sidecar_cmds.rs\n"
        f"  - TS:   voice_typer/client/src/main/allowed-commands.ts"
    )


def test_rust_allowlist_matches_ts_allowlist_entries() -> None:
    """The Rust allowlist must contain the EXACT SAME entries as TS.

    Catches the case where the counts match but the entries differ
    (e.g. a typo renamed ``quit_app`` to ``quit`` in one file but not
    the other). Reports the symmetric difference so the contributor
    sees exactly which commands are in only one of the two files.
    """
    ts = _allowed_commands_ts()
    rust = _allowed_commands_rust()
    only_ts = ts - rust
    only_rust = rust - ts
    assert not only_ts and not only_rust, (
        f"Rust ↔ TS ALLOWED_COMMANDS drift detected:\n"
        f"  In TS but NOT in Rust: {sorted(only_ts) or '(none)'}\n"
        f"  In Rust but NOT in TS: {sorted(only_rust) or '(none)'}\n"
        f"Both files MUST list the same commands. Update them in the "
        f"same PR. Files:\n"
        f"  - Rust: src-tauri/src/commands/sidecar_cmds.rs "
        f"(allowed_commands() fn)\n"
        f"  - TS:   voice_typer/client/src/main/allowed-commands.ts "
        f"(ALLOWED_COMMANDS = new Set([...]))"
    )


def test_rust_allowlist_rejects_known_dangerous_commands() -> None:
    """Sentinel check: dangerous commands must NOT be in the Rust allowlist.

    These are commands that would let a compromised renderer escalate
    privilege if they slipped into the allowlist. The list is intentionally
    short and high-signal — we're not trying to enumerate every bad
    command, just to catch a regression where one of the obvious ones
    gets added.
    """
    rust = _allowed_commands_rust()
    dangerous = {
        "eval": "would let a compromised renderer run arbitrary Python",
        "exec": "would let a compromised renderer run arbitrary shell commands",
        "shutdown": ("cooperative shutdown is sent via shutdown_sidecar directly, NOT via the generic dispatch path"),
        "delete_everything": (
            "sentinel — no such server command; a positive result here means a typo added a dangerous placeholder"
        ),
        "system": "would let a compromised renderer run arbitrary system calls",
        "os": "would let a compromised renderer run arbitrary OS calls",
    }
    leaked = {cmd for cmd in dangerous if cmd in rust}
    assert not leaked, (
        f"Rust ALLOWED_COMMANDS contains dangerous command(s): {sorted(leaked)}. "
        f"Reasons:\n"
        + "\n".join(f"  - {cmd}: {dangerous[cmd]}" for cmd in sorted(leaked))
        + "\nRemove them from src-tauri/src/commands/sidecar_cmds.rs."
    )


def test_rust_allowlist_contains_key_commands() -> None:
    """Key commands the renderer depends on MUST be in the Rust allowlist.

    These are picked because they're load-bearing for the UI:
      - ``get_status``: Home.tsx on mount
      - ``set_config``: Settings page saves
      - ``quit_app``: tray Quit menu item
      - ``toggle_dictation``: main hotkey action
      - ``download_model``: Models page download button
      - ``heartbeat``: RW-10 watchdog

    If any of these go missing from the Rust allowlist, the
    corresponding UI feature silently breaks (the renderer's
    ``invoke('dispatch', ...)`` rejects with ``disallowed_command``).
    """
    rust = _allowed_commands_rust()
    required = {
        "get_status",
        "set_config",
        "quit_app",
        "toggle_dictation",
        "download_model",
        "heartbeat",
    }
    missing = required - rust
    assert not missing, (
        f"Rust ALLOWED_COMMANDS is missing key command(s): {sorted(missing)}. "
        f"These are load-bearing for the UI — add them back to "
        f"src-tauri/src/commands/sidecar_cmds.rs."
    )

# ─── S4-CR-18 (this session): Python _COMMAND_REGISTRY ↔ TS allowlist parity ──


def _command_registry_entries() -> set[str]:
    """Return the set of command names in the Python ``_COMMAND_REGISTRY``.

    Parses ``voice_typer/server/ipc_server.py`` and extracts every
    ``"<cmd_name>": "_handle_..."`` key from the
    ``_COMMAND_REGISTRY: dict[str, str] = {...}`` literal.
    """
    src = IPC_SERVER_PY.read_text(encoding="utf-8")
    m_start = re.search(r"_COMMAND_REGISTRY\s*:\s*dict\[str,\s*str\]\s*=\s*\{", src)
    assert m_start is not None, (
        "voice_typer/server/ipc_server.py no longer declares the "
        "`_COMMAND_REGISTRY: dict[str, str] = {` literal. Did the "
        "registry shape change? Update this parser to match."
    )
    depth = 1
    i = m_start.end()
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[m_start.end() : i - 1]
    return set(re.findall(r'"([a-z_]+)"\s*:\s*"_handle_', body))


def test_command_registry_literal_exists() -> None:
    """Sanity: ``_COMMAND_REGISTRY`` literal must exist and be non-empty."""
    registry = _command_registry_entries()
    assert registry, (
        "Parsed _COMMAND_REGISTRY is empty — either the literal was "
        "moved/renamed (update _command_registry_entries' anchor "
        "regex) or the registry was emptied (very likely a bug)."
    )


def test_command_registry_count_matches_renderer_allowlist_with_host_only_delta() -> None:
    """Python ``_COMMAND_REGISTRY`` count == TS allowlist + host-only set.

    S4-CR-18: the original finding flagged drift between docs (which
    said 68/69/~35) and the actual IPC surface (then 70). The docs are
    now reconciled to 61 (the renderer allowlist count), but the
    Python ``_COMMAND_REGISTRY`` is intentionally larger because two
    host-only commands — ``shutdown`` and ``tray_click`` — are routed
    by the Rust host directly, never via the renderer's ``dispatch``
    path, so they're deliberately absent from ``allowed-commands.ts``.

    This test encodes that invariant:

        len(_COMMAND_REGISTRY) == len(ALLOWED_COMMANDS) + len(_HOST_ONLY_COMMANDS)
    """
    ts = _allowed_commands_ts()
    registry = _command_registry_entries()
    host_only = set(_HOST_ONLY_COMMANDS)

    # All host-only commands must actually be in the registry.
    host_only_not_in_registry = host_only - registry
    assert not host_only_not_in_registry, (
        f"_HOST_ONLY_COMMANDS lists {sorted(host_only_not_in_registry)} "
        f"but these are NOT in _COMMAND_REGISTRY. Either the registry "
        f"dropped a host-only command (update _HOST_ONLY_COMMANDS) or "
        f"the command was renamed."
    )

    # All host-only commands must be ABSENT from the renderer allowlist.
    host_only_leaked_to_renderer = host_only & ts
    assert not host_only_leaked_to_renderer, (
        f"_HOST_ONLY_COMMANDS {sorted(host_only_leaked_to_renderer)} "
        f"are ALSO in the renderer ALLOWED_COMMANDS — that contradicts "
        f"the 'host-only' designation. Either remove them from "
        f"allowed-commands.ts or remove them from _HOST_ONLY_COMMANDS."
    )

    # Every registry command must be EITHER in the renderer allowlist
    # OR in the host-only set.
    unaccounted = registry - ts - host_only
    assert not unaccounted, (
        f"_COMMAND_REGISTRY has {len(unaccounted)} command(s) not in "
        f"the renderer ALLOWED_COMMANDS and not in _HOST_ONLY_COMMANDS: "
        f"{sorted(unaccounted)}. Each registry command must be EITHER "
        f"reachable from the renderer (add to allowed-commands.ts) OR "
        f"explicitly host-only (add to _HOST_ONLY_COMMANDS)."
    )

    # Every renderer-allowlisted command must be in the registry.
    missing_from_registry = ts - registry
    assert not missing_from_registry, (
        f"Renderer ALLOWED_COMMANDS lists {sorted(missing_from_registry)} "
        f"but these are NOT in _COMMAND_REGISTRY. The renderer would "
        f"dispatch these commands and the backend would reject them "
        f"with 'unknown_command'."
    )

    # Count invariant: registry == renderer + host-only.
    assert len(registry) == len(ts) + len(host_only), (
        f"Count invariant broken: _COMMAND_REGISTRY has {len(registry)} "
        f"entries, renderer ALLOWED_COMMANDS has {len(ts)}, and "
        f"_HOST_ONLY_COMMANDS has {len(host_only)} — expected "
        f"{len(ts)} + {len(host_only)} = {len(ts) + len(host_only)}. "
        f"Files to check:\n"
        f"  - voice_typer/server/ipc_server.py (_COMMAND_REGISTRY)\n"
        f"  - voice_typer/client/src/main/allowed-commands.ts\n"
        f"  - tests/test_security_doc_command_count.py (_HOST_ONLY_COMMANDS)"
    )


def test_security_md_documents_renderer_count_not_registry_count() -> None:
    """SECURITY.md must document the RENDERER allowlist count, not the registry count.

    S4-CR-18: the security doc should advertise the ATTACK SURFACE —
    i.e. what a compromised renderer can invoke — which is the renderer
    allowlist count (61), NOT the registry count (63, including 2
    host-only commands the renderer cannot invoke).
    """
    documented = _documented_count()
    ts_count = _count_allowed_commands()
    registry_count = len(_command_registry_entries())
    assert documented is not None, (
        "SECURITY.md no longer documents the ALLOWED_COMMANDS count in a "
        "parseable form."
    )
    assert documented == ts_count, (
        f"SECURITY.md documents {documented} ALLOWED_COMMANDS but the "
        f"renderer source defines {ts_count}. SECURITY.md must document "
        f"the RENDERER allowlist count (the attack surface a compromised "
        f"renderer can reach), NOT the Python _COMMAND_REGISTRY count "
        f"(which is {registry_count}, including {len(_HOST_ONLY_COMMANDS)} "
        f"host-only commands the renderer cannot invoke)."
    )
