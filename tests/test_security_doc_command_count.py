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
    actual = _count_allowed_commands()
    documented = _documented_count()
    assert documented is not None, (
        "SECURITY.md no longer documents the ALLOWED_COMMANDS count in a "
        "parseable form (expected: 'only the N commands listed in "
        "ALLOWED_COMMANDS')."
    )
    assert documented == actual, (
        f"SECURITY.md documents {documented} ALLOWED_COMMANDS but the "
        f"renderer source defines {actual}. Update SECURITY.md and this "
        f"assertion together (d-review Finding 5)."
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
