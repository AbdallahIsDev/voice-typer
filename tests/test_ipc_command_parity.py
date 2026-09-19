"""Two-way IPC command parity: Python registry ↔ Rust allowlist + SECURITY.md."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
IPC_REGISTRY_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "registry.py"
IPC_SERVER_PY = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
SIDECAR_CMDS_DIR = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds"
ALLOWLIST_RS = SIDECAR_CMDS_DIR / "allowlist.rs"

HOST_DISPATCHED_COMMANDS = frozenset(
    {
        "heartbeat": (
            "Host heartbeat task sends this via dispatch_inner. A "
            "renderer-spoofable heartbeat would keep the watchdog alive "
            "after the WebView is killed."
        ),
        "relaunch_ack": (
            "Host relaunch listener sends this fire-and-forget. A "
            "renderer-spoofable ack would race restart_app's wait event."
        ),
        "shutdown": ("Cooperative shutdown is host-supervised (shutdown_sidecar path), never a renderer dispatch."),
        "tray_click": ("Rust tray menu handler invokes via dispatch_inner. The renderer has no tray-menu UI."),
    }
)


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split)."""
    files = [SIDECAR_CMDS_RS] + sorted(SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


def _python_registry_entries() -> set[str]:
    """Command names in the Python ``_COMMAND_REGISTRY`` literal."""
    sources = [IPC_REGISTRY_PY, IPC_SERVER_PY]
    src = None
    for candidate in sources:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            if "_COMMAND_REGISTRY" in text:
                src = text
                break
    assert src is not None, (
        f"Could not find _COMMAND_REGISTRY in {IPC_REGISTRY_PY} or "
        f"{IPC_SERVER_PY}. Did the registry move? Update this parser."
    )
    m_start = re.search(r"_COMMAND_REGISTRY\s*:\s*dict\[str,\s*str\]\s*=\s*\{", src)
    assert m_start is not None, "_COMMAND_REGISTRY literal not found. Update this parser to match."
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


def _rust_allowed_commands() -> set[str]:
    """Command names inside the Rust ``let cmds: &[&str] = &[...]`` literal."""
    # Strip comments so prose cannot bind the anchor or leak quoted words.
    src = _read_sidecar_cmds_module()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"(?<!:)//[^\n]*", "", src)
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "allowlist.rs no longer declares `let cmds: &[&str] = &[` inside "
        "allowed_commands(). Update this parser to match."
    )
    body = src[m_start.end() :]
    m_end = re.search(r"\];", body)
    assert m_end is not None, "Could not find the closing `];` of the Rust allowlist literal."
    return set(re.findall(r'"([a-z_]+)"', body[: m_end.start()]))


def _documented_rust_count() -> int | None:
    """Extract the documented renderer-allowlist count from SECURITY.md."""
    text = SECURITY_MD.read_text(encoding="utf-8")
    text = text.replace("**", "")
    m = re.search(
        r"(\d+)\s+commands?\s+(listed\s+in\s+)?`?(?:allowed_commands\(\)|ALLOWED_COMMANDS)`?",
        text,
    )
    return int(m.group(1)) if m else None


def test_python_registry_literal_exists() -> None:
    registry = _python_registry_entries()
    assert registry, "Parsed _COMMAND_REGISTRY is empty (parser regression?)."


def test_rust_allowlist_is_nonempty() -> None:
    rust = _rust_allowed_commands()
    assert rust, "Parsed Rust allowed_commands() is empty (parser regression?)."


def test_rust_allowlist_is_subset_of_python_registry() -> None:
    """Every renderer-reachable command must be routable by the backend."""
    py = _python_registry_entries()
    rust = _rust_allowed_commands()
    orphans = sorted(rust - py)
    assert not orphans, (
        f"Rust allowlist has commands missing from _COMMAND_REGISTRY (backend would reply unknown_command): {orphans}"
    )


def test_registry_minus_rust_equals_host_dispatched_delta() -> None:
    """The ONLY registry commands outside the Rust allowlist are host-only."""
    py = _python_registry_entries()
    rust = _rust_allowed_commands()
    extra = sorted(py - rust)
    expected = sorted(HOST_DISPATCHED_COMMANDS)
    assert extra == expected, (
        f"Python registry − Rust allowlist drift.\n"
        f"  Unexpected registry-only: {sorted(set(extra) - set(expected))}\n"
        f"  Missing (stale HOST_DISPATCHED_COMMANDS): "
        f"{sorted(set(expected) - set(extra))}\n"
        f"Every registry command must be EITHER in allowed_commands() "
        f"(renderer-reachable) OR in HOST_DISPATCHED_COMMANDS."
    )


def test_host_dispatched_commands_absent_from_rust_allowlist() -> None:
    rust = _rust_allowed_commands()
    leaked = sorted(set(HOST_DISPATCHED_COMMANDS) & rust)
    assert not leaked, (
        f"Host-dispatched commands leaked into the Rust allowlist: {leaked}. "
        f"Remove them from allowlist.rs (renderer must not reach these)."
    )


def test_count_invariant_registry_equals_rust_plus_host_delta() -> None:
    py = _python_registry_entries()
    rust = _rust_allowed_commands()
    assert len(py) == len(rust) + len(HOST_DISPATCHED_COMMANDS), (
        f"Count invariant broken: registry={len(py)} "
        f"rust={len(rust)} host-delta={len(HOST_DISPATCHED_COMMANDS)} "
        f"(expected {len(rust) + len(HOST_DISPATCHED_COMMANDS)})."
    )


def test_security_md_documents_rust_allowlist_count() -> None:
    """SECURITY.md must advertise the ATTACK SURFACE (Rust count)."""
    documented = _documented_rust_count()
    rust_count = len(_rust_allowed_commands())
    registry_count = len(_python_registry_entries())
    assert documented is not None, (
        "SECURITY.md no longer documents the allowlist count in a "
        "parseable form (expected 'only the N commands listed in "
        "allowed_commands()')."
    )
    assert documented == rust_count, (
        f"SECURITY.md documents {documented} renderer-reachable commands "
        f"but allowed_commands() defines {rust_count} "
        f"(registry has {registry_count}). Update SECURITY.md."
    )


def test_python_only_commands_match_registry_frozenset() -> None:
    """``registry._PYTHON_ONLY_COMMANDS`` stays a subset of the delta."""
    from voice_typer.server.ipc.registry import _PYTHON_ONLY_COMMANDS

    assert set(HOST_DISPATCHED_COMMANDS) >= _PYTHON_ONLY_COMMANDS, (
        f"_PYTHON_ONLY_COMMANDS {sorted(_PYTHON_ONLY_COMMANDS)} has entries not documented in HOST_DISPATCHED_COMMANDS."
    )


def test_check_accessibility_is_registered_in_python_command_registry() -> None:
    """Settings → Troubleshooting re-invoked check_accessibility (finding #919b)."""
    registry = _python_registry_entries()
    assert "check_accessibility" in registry, (
        "'check_accessibility' MUST stay in _COMMAND_REGISTRY (Troubleshooting section calls it on macOS)."
    )


def test_check_accessibility_is_in_rust_allowlist() -> None:
    """Renderer-reachable: the Troubleshooting UI dispatches it under Tauri."""
    rust = _rust_allowed_commands()
    assert "check_accessibility" in rust, (
        "'check_accessibility' MUST stay in allowed_commands() (renderer call site exists)."
    )


def test_repaste_last_is_in_rust_allowlist() -> None:
    """Home/tray re-paste is wired; the Rust gate must forward it."""
    rust = _rust_allowed_commands()
    assert "repaste_last" in rust, (
        "'repaste_last' is renderer-reachable (Home + tray) and MUST be in allowed_commands()."
    )


def test_rust_allowlist_rejects_known_dangerous_commands() -> None:
    rust = _rust_allowed_commands()
    dangerous = {
        "eval": "would let a compromised renderer run arbitrary Python",
        "exec": "would let a compromised renderer run arbitrary shell",
        "shutdown": ("cooperative shutdown is host-supervised, NOT via generic dispatch"),
        "delete_everything": "sentinel placeholder that must never exist",
        "system": "would let a compromised renderer run system calls",
        "os": "would let a compromised renderer run OS calls",
    }
    leaked = sorted(cmd for cmd in dangerous if cmd in rust)
    assert not leaked, f"Rust allowlist contains dangerous command(s): {leaked}. Reasons:\n" + "\n".join(
        f"  - {cmd}: {dangerous[cmd]}" for cmd in leaked
    )


def test_rust_allowlist_contains_key_commands() -> None:
    """Load-bearing UI commands must not vanish from the Rust gate."""
    rust = _rust_allowed_commands()
    required = {
        "get_status",
        "set_config",
        "quit_app",
        "toggle_dictation",
        "download_model",
    }
    missing = sorted(required - rust)
    assert not missing, f"Rust allowlist is missing key UI command(s): {missing}."
