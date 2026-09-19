"""SECURITY.md allowlist-count guard + Python↔Rust command parity."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
IPC_REGISTRY_PY = REPO_ROOT / "voice_typer" / "server" / "ipc" / "registry.py"
IPC_SERVER_PY = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
SIDECAR_CMDS_DIR = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds"

_HOST_ONLY_OR_HOST_DISPATCHED = frozenset(
    {
        "shutdown": ("Tauri cooperative-shutdown command. Host-supervised; the renderer never dispatches it."),
        "tray_click": ("Tauri tray-menu click dispatch. Rust tray handler invokes via dispatch_inner."),
        "heartbeat": (
            "Host heartbeat task sends this via dispatch_inner. Kept "
            "out of the renderer gate so a compromised WebView cannot "
            "spoof watchdog ticks."
        ),
        "relaunch_ack": (
            "Host relaunch listener sends this fire-and-forget. Kept out "
            "of the renderer gate so a compromised WebView cannot race "
            "the restart-wait event."
        ),
    }
)


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split)."""
    files = [SIDECAR_CMDS_RS] + sorted(SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


def _command_registry_entries() -> set[str]:
    """Command names in the Python ``_COMMAND_REGISTRY`` literal."""
    sources = [IPC_REGISTRY_PY, IPC_SERVER_PY]
    src = None
    for candidate in sources:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            if "_COMMAND_REGISTRY" in text:
                src = text
                break
    assert src is not None, f"Could not find _COMMAND_REGISTRY in either {IPC_REGISTRY_PY} or {IPC_SERVER_PY}."
    m_start = re.search(r"_COMMAND_REGISTRY\s*:\s*dict\[str,\s*str\]\s*=\s*\{", src)
    assert m_start is not None, "_COMMAND_REGISTRY literal not found."
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


def _allowed_commands_rust() -> set[str]:
    """Command names in the Rust ``allowed_commands()`` literal."""
    src = _read_sidecar_cmds_module()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"(?<!:)//[^\n]*", "", src)
    m_start = re.search(r"let\s+cmds:\s*&\[&str\]\s*=\s*&\[", src)
    assert m_start is not None, (
        "allowlist.rs no longer declares the `let cmds: &[&str] = &[` literal. Update this parser."
    )
    body = src[m_start.end() :]
    m_end = re.search(r"\];", body)
    assert m_end is not None, "Could not find the closing `];` of the Rust allowlist literal."
    return set(re.findall(r'"([a-z_]+)"', body[: m_end.start()]))


def _documented_count() -> int | None:
    """Extract the documented renderer-allowlist count from SECURITY.md."""
    text = SECURITY_MD.read_text(encoding="utf-8")
    text = text.replace("**", "")
    m = re.search(
        r"(\d+)\s+commands?\s+(listed\s+in\s+)?`?(?:allowed_commands\(\)|ALLOWED_COMMANDS)`?",
        text,
    )
    return int(m.group(1)) if m else None


def test_security_md_allowlist_count_matches_source() -> None:
    """SECURITY.md's documented count must equal the Rust allowlist size."""
    actual = len(_allowed_commands_rust())
    documented = _documented_count()
    assert documented is not None, (
        "SECURITY.md no longer documents the allowlist count in a "
        "parseable form (expected: 'only the N commands listed in "
        "allowed_commands()')."
    )
    assert documented == actual, (
        f"SECURITY.md documents {documented} renderer-reachable commands "
        f"but allowed_commands() defines {actual}. Update the "
        f"'Command Allowlist (SEC-019)' section in SECURITY.md."
    )


def test_allowed_commands_nonempty() -> None:
    assert len(_allowed_commands_rust()) > 0
    assert len(_command_registry_entries()) > 0


def test_rust_allowlist_is_subset_of_python_registry() -> None:
    py = _command_registry_entries()
    rust = _allowed_commands_rust()
    orphans = sorted(rust - py)
    assert not orphans, f"Rust allowlist has commands missing from _COMMAND_REGISTRY: {orphans}"


def test_command_registry_count_matches_rust_allowlist_with_host_delta() -> None:
    """``len(registry) == len(rust) + len(host-only/host-dispatched)``."""
    rust = _allowed_commands_rust()
    registry = _command_registry_entries()
    host_delta = set(_HOST_ONLY_OR_HOST_DISPATCHED)

    host_not_in_registry = sorted(host_delta - registry)
    assert not host_not_in_registry, (
        f"_HOST_ONLY_OR_HOST_DISPATCHED lists {host_not_in_registry} but these are NOT in _COMMAND_REGISTRY."
    )

    leaked_to_rust = sorted(host_delta & rust)
    assert not leaked_to_rust, f"Host-only/host-dispatched commands leaked into the Rust allowlist: {leaked_to_rust}."

    unaccounted = sorted(registry - rust - host_delta)
    assert not unaccounted, (
        f"_COMMAND_REGISTRY has commands that are neither in "
        f"allowed_commands() nor in the documented host delta: {unaccounted}."
    )

    assert len(registry) == len(rust) + len(host_delta), (
        f"Count invariant broken: registry={len(registry)} "
        f"rust={len(rust)} host-delta={len(host_delta)} "
        f"(expected {len(rust) + len(host_delta)})."
    )


def test_security_md_documents_rust_count_not_registry_count() -> None:
    """SECURITY.md advertises the renderer-reachable surface, not the full table."""
    documented = _documented_count()
    rust_count = len(_allowed_commands_rust())
    registry_count = len(_command_registry_entries())
    assert documented is not None, "SECURITY.md count missing/unparseable."
    assert documented == rust_count, (
        f"SECURITY.md documents {documented} but allowed_commands() has "
        f"{rust_count}. Registry has {registry_count} (includes the "
        f"{len(_HOST_ONLY_OR_HOST_DISPATCHED)} host-delta commands the "
        f"renderer cannot invoke). SECURITY.md must document the Rust count."
    )


def test_check_accessibility_is_registered_in_python_command_registry() -> None:
    registry = _command_registry_entries()
    assert "check_accessibility" in registry, (
        "'check_accessibility' MUST be in the Python _COMMAND_REGISTRY "
        "(Settings → Troubleshooting re-invoked it on macOS)."
    )


def test_check_accessibility_is_in_rust_allowlist() -> None:
    rust = _allowed_commands_rust()
    assert "check_accessibility" in rust, (
        "'check_accessibility' MUST be in allowed_commands() (renderer-reachable under Tauri)."
    )


def test_rust_allowlist_contains_key_commands() -> None:
    rust = _allowed_commands_rust()
    required = {
        "get_status",
        "set_config",
        "quit_app",
        "toggle_dictation",
        "download_model",
    }
    missing = sorted(required - rust)
    assert not missing, f"Rust allowlist is missing key UI commands: {missing}"
