"""EC-4: Python ``_COMMAND_REGISTRY`` ↔ TS/Rust ``ALLOWED_COMMANDS`` parity.

EC-4 (cross-layer DRY): the IPC command allowlist is hand-mirrored in three
independent declarations:

1. Python ``_COMMAND_REGISTRY`` on ``IPCServer``
   (``voice_typer/server/ipc_server.py``) — the authoritative dispatch table
   that the server uses to route incoming IPC commands.
2. TS ``ALLOWED_COMMANDS = new Set([...])``
   (``voice_typer/client/src/main/allowed-commands.ts``) — the renderer-side
   allowlist gate (any command the renderer invokes MUST be in this set).
3. Rust ``allowed_commands()``
   (``src-tauri/src/commands/sidecar_cmds.rs``) — the Tauri host's
   defense-in-depth backstop (a compromised renderer that calls
   ``invoke('dispatch', {cmd:'<arbitrary>'})`` is rejected at the Rust layer
   if the command is not in this set).

The existing parity tests (``tests/test_security_doc_command_count.py`` and
``tests/test_rust_allowlist_parity.py``) assert EXACT membership parity
between TS and Rust — but they do NOT cross-check against the Python
``_COMMAND_REGISTRY``. EC-4's review note observed that this gap allowed
historical drift (the ERR-IPC-002 incident: ``quit_app`` and
``restart_app`` were missing from the TS allowlist but present in the
Python registry — silently breaking the renderer's Quit / Restart buttons).

This file closes that gap. It asserts EXACT membership parity between
the Python registry and BOTH the TS and Rust allowlists, with an explicit,
documented exception set (``IPCServer._PYTHON_ONLY_COMMANDS``) for the
handful of commands that are intentionally invoked by non-renderer callers
(the Tauri host's Rust bridge and Electron's main process) and therefore
must NOT appear in the renderer allowlist.

Long-term (out of scope for this fix): generate the TS and Rust allowlists
from the Python ``_COMMAND_REGISTRY`` at build time so the three layers
cannot drift by construction. Until then, this parity test is the
regression guard.
"""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server.ipc_server import IPCServer

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_COMMANDS_TS = (
    REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "allowed-commands.ts"
)
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"


def _python_command_registry_keys() -> set[str]:
    """Return the set of command names in ``IPCServer._COMMAND_REGISTRY``.

    EC-4: the Python registry is the authoritative dispatch table; every
    command the server can route MUST be a key here. The TS/Rust allowlists
    MUST mirror this set (minus the explicitly-documented Python-only
    exceptions in ``IPCServer._PYTHON_ONLY_COMMANDS``).
    """
    return set(IPCServer._COMMAND_REGISTRY.keys())


def _python_only_commands() -> set[str]:
    """Return the set of commands intentionally absent from TS/Rust.

    EC-4: ``IPCServer._PYTHON_ONLY_COMMANDS`` is the single source of truth
    for "this command is in the Python registry but is NOT invoked by the
    renderer" (e.g. ``shutdown`` is invoked by the Tauri host's WS
    transport; ``tray_click`` is invoked by the Rust host's tray handler).
    """
    return set(IPCServer._PYTHON_ONLY_COMMANDS)


def _ts_allowed_commands() -> set[str]:
    """Parse the TS ``ALLOWED_COMMANDS = new Set([...])`` literal.

    Mirrors the parser in ``test_security_doc_command_count.py`` and
    ``test_rust_allowlist_parity.py`` — same regex, same anchoring.
    Duplicated here so this test file is self-contained.
    """
    src = ALLOWED_COMMANDS_TS.read_text(encoding="utf-8")
    start = src.index("ALLOWED_COMMANDS = new Set([")
    end = src.index("]);", start)
    block = src[start:end]
    return set(re.findall(r'"([a-z_]+)"', block))


def _rust_allowed_commands() -> set[str]:
    """Parse the Rust ``allowed_commands()`` body for quoted command names.

    Mirrors the parser in ``test_security_doc_command_count.py`` and
    ``test_rust_allowlist_parity.py``. Duplicated here so this test file
    is self-contained.
    """
    src = SIDECAR_CMDS_RS.read_text(encoding="utf-8")
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "src-tauri/src/commands/sidecar_cmds.rs no longer declares the "
        "`let cmds: &[&str] = &[` literal inside `allowed_commands()`. "
        "Update this parser to match."
    )
    body = src[m_start.end():]
    m_end = re.search(r"\];", body)
    assert m_end is not None, (
        "src-tauri/src/commands/sidecar_cmds.rs: could not find the "
        "closing `];` of the `let cmds: &[&str] = &[` literal."
    )
    literal = body[: m_end.start()]
    return set(re.findall(r'"([a-z_]+)"', literal))


# ─── EC-4: Python ↔ TS exact-membership parity ─────────────────────────


def test_every_ts_command_is_in_python_registry() -> None:
    """EC-4: every renderer-invokable command MUST be registered in Python.

    If the renderer allowlist contains a command that the Python
    ``_COMMAND_REGISTRY`` does not, the renderer will invoke the command,
    the Tauri host will allow it, but the Python server will respond with
    ``unknown_command`` — a broken-UX drift the EC-4 review flagged.
    """
    py = _python_command_registry_keys()
    ts = _ts_allowed_commands()
    only_ts = ts - py
    assert not only_ts, (
        f"EC-4 drift: TS ALLOWED_COMMANDS contains command(s) that are "
        f"NOT in Python _COMMAND_REGISTRY: {sorted(only_ts)}. The "
        f"renderer would invoke these but the server would reject them "
        f"with 'unknown_command'. Either add the handler to "
        f"voice_typer/server/ipc_server.py (and the appropriate mixin "
        f"in voice_typer/server/handlers/) or remove the entry from "
        f"voice_typer/client/src/main/allowed-commands.ts."
    )


def test_every_python_command_is_in_ts_except_documented_exceptions() -> None:
    """EC-4: every Python-registered command MUST be in TS, UNLESS it is
    explicitly listed in ``IPCServer._PYTHON_ONLY_COMMANDS``.

    A Python-only command (e.g. ``shutdown``) is invoked by a non-renderer
    caller (the Tauri host's WS transport). It MUST be added to
    ``_PYTHON_ONLY_COMMANDS`` with a comment documenting the legitimate
    non-renderer caller — otherwise the renderer allowlist would silently
    drift and a future contributor could remove the handler thinking it
    is dead code.
    """
    py = _python_command_registry_keys()
    py_only = _python_only_commands()
    ts = _ts_allowed_commands()

    # Sanity: every "Python-only" entry must actually be in the registry.
    bogus_exceptions = py_only - py
    assert not bogus_exceptions, (
        f"EC-4: IPCServer._PYTHON_ONLY_COMMANDS lists {sorted(bogus_exceptions)} "
        f"but these are NOT in _COMMAND_REGISTRY. Stale exception entry — "
        f"remove from _PYTHON_ONLY_COMMANDS."
    )

    # Every Python command NOT in the exception set MUST appear in TS.
    expected_in_ts = py - py_only
    missing_from_ts = expected_in_ts - ts
    assert not missing_from_ts, (
        f"EC-4 drift: Python _COMMAND_REGISTRY contains command(s) that "
        f"are NOT in TS ALLOWED_COMMANDS and NOT in "
        f"IPCServer._PYTHON_ONLY_COMMANDS: {sorted(missing_from_ts)}. "
        f"Either add the command to "
        f"voice_typer/client/src/main/allowed-commands.ts (if the "
        f"renderer invokes it) OR add it to "
        f"IPCServer._PYTHON_ONLY_COMMANDS with a comment documenting "
        f"the non-renderer caller that legitimately invokes it."
    )


# ─── EC-4: Python ↔ Rust exact-membership parity ───────────────────────


def test_every_rust_command_is_in_python_registry() -> None:
    """EC-4: every Rust-allowed command MUST be registered in Python.

    The Rust allowlist is a defense-in-depth backstop — a compromised
    renderer can call ``invoke('dispatch', {cmd:'<arbitrary>'})``. If
    the Rust allowlist permits a command that the Python server doesn't
    have a handler for, the Rust gate gives a false sense of security
    (it "allows" the command but the server rejects it with
    ``unknown_command`` — which is still safe, but indicates drift).
    """
    py = _python_command_registry_keys()
    rust = _rust_allowed_commands()
    only_rust = rust - py
    assert not only_rust, (
        f"EC-4 drift: Rust ALLOWED_COMMANDS contains command(s) that "
        f"are NOT in Python _COMMAND_REGISTRY: {sorted(only_rust)}. "
        f"Either add the handler to "
        f"voice_typer/server/ipc_server.py (and the appropriate mixin "
        f"in voice_typer/server/handlers/) or remove the entry from "
        f"src-tauri/src/commands/sidecar_cmds.rs."
    )


def test_every_python_command_is_in_rust_except_documented_exceptions() -> None:
    """EC-4: every Python-registered command MUST be in Rust, UNLESS it is
    explicitly listed in ``IPCServer._PYTHON_ONLY_COMMANDS``.

    Symmetric to the TS-side check. The Rust gate is the
    defense-in-depth backstop for the renderer; a Python-only command
    invoked by the Tauri host's Rust bridge (e.g. ``tray_click``) must
    NOT appear in the renderer-protecting Rust allowlist (otherwise a
    compromised renderer could invoke it).
    """
    py = _python_command_registry_keys()
    py_only = _python_only_commands()
    rust = _rust_allowed_commands()

    expected_in_rust = py - py_only
    missing_from_rust = expected_in_rust - rust
    assert not missing_from_rust, (
        f"EC-4 drift: Python _COMMAND_REGISTRY contains command(s) that "
        f"are NOT in Rust ALLOWED_COMMANDS and NOT in "
        f"IPCServer._PYTHON_ONLY_COMMANDS: {sorted(missing_from_rust)}. "
        f"Either add the command to "
        f"src-tauri/src/commands/sidecar_cmds.rs (if the renderer "
        f"invokes it) OR add it to IPCServer._PYTHON_ONLY_COMMANDS "
        f"with a comment documenting the non-renderer caller."
    )


# ─── EC-4: documentation invariants on the exception set ───────────────


def test_python_only_commands_is_frozen() -> None:
    """EC-4: ``_PYTHON_ONLY_COMMANDS`` MUST be a ``frozenset``.

    A mutable set could be accidentally modified at runtime (e.g. a
    handler does ``IPCServer._PYTHON_ONLY_COMMANDS.add('foo')``), which
    would silently weaken the parity guard. ``frozenset`` makes that a
    runtime ``AttributeError``.
    """
    assert isinstance(IPCServer._PYTHON_ONLY_COMMANDS, frozenset), (
        "EC-4: IPCServer._PYTHON_ONLY_COMMANDS must be a frozenset so it "
        "cannot be accidentally mutated at runtime."
    )


def test_python_only_commands_subset_of_registry() -> None:
    """EC-4: every entry in ``_PYTHON_ONLY_COMMANDS`` MUST be a real key
    in ``_COMMAND_REGISTRY``.

    A stale exception entry (e.g. after a command is removed from the
    registry) would silently hide future drift — the parity check would
    skip a command that no longer exists, and a future contributor adding
    a NEW command with the same name would inherit the "Python-only"
    designation without review.
    """
    py = _python_command_registry_keys()
    py_only = _python_only_commands()
    stale = py_only - py
    assert not stale, (
        f"EC-4: IPCServer._PYTHON_ONLY_COMMANDS contains stale entry/entries "
        f"not present in _COMMAND_REGISTRY: {sorted(stale)}. Remove the "
        f"stale entry/entries from _PYTHON_ONLY_COMMANDS."
    )


def test_python_only_commands_is_nonempty() -> None:
    """EC-4 sanity: ``_PYTHON_ONLY_COMMANDS`` is non-empty.

    At minimum ``shutdown`` (Tauri host's WS cooperative shutdown) and
    ``tray_click`` (Tauri host's tray dispatch) are Python-only by
    design. If this set becomes empty, EITHER (a) those commands were
    added to the renderer allowlist (a security regression — a
    compromised renderer could then invoke cooperative shutdown or spoof
    tray clicks) OR (b) the constant was accidentally deleted. Either
    way, this assertion forces a review.
    """
    py_only = _python_only_commands()
    assert py_only, (
        "EC-4: IPCServer._PYTHON_ONLY_COMMANDS is empty. The Python "
        "registry should always have at least 'shutdown' and "
        "'tray_click' as Python-only commands (invoked by the Tauri "
        "host, not the renderer). If those were removed from the "
        "registry, this is expected — otherwise investigate."
    )
