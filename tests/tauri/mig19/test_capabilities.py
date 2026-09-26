"""
Phase 3 + §7: Tauri v2 capabilities least-privilege validation.
Custom Tauri v2 commands do NOT need a capability entry, only
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _SRC_TAURI / "tauri.conf.json"
_CAPABILITIES_DIR = _SRC_TAURI / "capabilities"
_MAIN_RUNTIME_CAPABILITY = _CAPABILITIES_DIR / "main-runtime.json"
_BUBBLE_RUNTIME_CAPABILITY = _CAPABILITIES_DIR / "bubble-runtime.json"
_MAIN_RS = _SRC_TAURI / "src" / "main.rs"
_SIDECAR_CMDS_RS = _SRC_TAURI / "src" / "commands" / "sidecar_cmds.rs"
_SIDECAR_CMDS_DIR = _SRC_TAURI / "src" / "commands" / "sidecar_cmds"


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split)."""
    files = [_SIDECAR_CMDS_RS] + sorted(_SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


# ADR-0020 §7 + : capability identifier referenced by tauri.conf.json's
EXPECTED_MAIN_CAPABILITY_IDENTIFIER = "main-runtime"
EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER = "bubble-runtime"
EXPECTED_CAPABILITY_IDENTIFIER = EXPECTED_MAIN_CAPABILITY_IDENTIFIER

# : ADR-0020 §4.1 + §7: externalBin base name (Tauri appends the Rust
EXPECTED_SIDECAR_BINARY = "bin/python-sidecar"

# : plan-runtime-pack-split.md §4.4/§7: the worker exe is the second
EXPECTED_WORKER_BINARY = "bin/lausu-worker"

# : ADR-0020 §7: overly-broad permission identifiers that MUST NOT
FORBIDDEN_BROAD_PERMISSIONS = (
    "shell:default",  # grants ALL shell perms → defeats spawn scope
    "fs:default",  # unrestricted filesystem access (sidecar owns FS)
    "http:default",  # unrestricted HTTP fetch (cloud engines in sidecar)
    "http:allow-fetch",  # same, HTTP stays in Python sidecar (ADR-0020 §6.5)
    "process:default",  # unrestricted process control
    "process:allow-restart",  # supervisor uses core AppHandle::restart() (not plugin)
    "global-shortcut:default",  # native hotkey binaries stay in Python (§6.4)
    "updater:default",  # auto-update out of scope for v1 (§15)
    "updater:allow-check",  # same
    "updater:allow-download",  # same
)


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse src-tauri/tauri.conf.json."""
    assert _TAURI_CONF.exists(), f"tauri.conf.json not found: {_TAURI_CONF}"
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def main_runtime_capability() -> dict:
    """Load + parse the main-runtime capability JSON (CR-5 split)."""
    assert _MAIN_RUNTIME_CAPABILITY.exists(), f"capability file not found: {_MAIN_RUNTIME_CAPABILITY}"
    return json.loads(_MAIN_RUNTIME_CAPABILITY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bubble_runtime_capability() -> dict:
    """Load + parse the bubble-runtime capability JSON (CR-5 split)."""
    assert _BUBBLE_RUNTIME_CAPABILITY.exists(), f"capability file not found: {_BUBBLE_RUNTIME_CAPABILITY}"
    return json.loads(_BUBBLE_RUNTIME_CAPABILITY.read_text(encoding="utf-8"))


# Back-compat alias fixture so existing test signatures that take
@pytest.fixture(scope="module")
def migrate_runtime_capability(main_runtime_capability: dict) -> dict:
    """Back-compat alias, delegates to ``main_runtime_capability`` after CR-5."""
    return main_runtime_capability


@pytest.fixture(scope="module")
def main_rs_source() -> str:
    """Read src-tauri/src/main.rs as text (for static assertions)."""
    assert _MAIN_RS.exists(), f"main.rs not found: {_MAIN_RS}"
    return _MAIN_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sidecar_cmds_rs_source() -> str:
    """Read src-tauri/src/commands/sidecar_cmds.rs as text."""
    assert _SIDECAR_CMDS_RS.exists(), f"sidecar_cmds.rs not found: {_SIDECAR_CMDS_RS}"
    return _read_sidecar_cmds_module()


def test_capabilities_file_exists_and_is_valid_json(
    main_runtime_capability: dict,
    bubble_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + ``main-runtime.json`` + ``bubble-runtime.json`` parse as JSON."""
    for cap, label in (
        (main_runtime_capability, "main-runtime.json"),
        (bubble_runtime_capability, "bubble-runtime.json"),
    ):
        assert isinstance(cap, dict), f"{label} must parse to a JSON object"
        assert "identifier" in cap, f"{label} must have an 'identifier' field (Tauri v2 schema)"
        assert "permissions" in cap, f"{label} must have a 'permissions' field (Tauri v2 schema)"
        assert isinstance(cap["permissions"], list), f"{label} 'permissions' must be a list of permission identifiers"


def test_tauri_conf_references_migrate_runtime_capability(
    tauri_conf: dict,
) -> None:
    """ADR-0020 §7 + ``app.security.capabilities`` lists both"""
    security = tauri_conf.get("app", {}).get("security", {})
    assert "capabilities" in security, (
        "app.security.capabilities must exist (ADR-0020 §7), without it the capability files are never loaded"
    )
    capabilities = security["capabilities"]
    assert isinstance(capabilities, list), "app.security.capabilities must be a list of identifier strings"
    assert EXPECTED_MAIN_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference {EXPECTED_MAIN_CAPABILITY_IDENTIFIER!r}, got {capabilities!r}"
    )
    assert EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference {EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER!r} "
        f"(CR-5 split, the bubble window MUST have its own minimal capability), got {capabilities!r}"
    )
    assert "migrate-runtime" not in capabilities, (
        f"app.security.capabilities must NOT reference 'migrate-runtime' (CR-5 deleted it), got {capabilities!r}"
    )


# Test 3: identifier matches filename (: both files) ────────────


def test_capability_identifier_matches_filename(
    main_runtime_capability: dict,
    bubble_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + ``identifier`` field matches the filename stem."""
    for cap, expected_id, path in (
        (main_runtime_capability, EXPECTED_MAIN_CAPABILITY_IDENTIFIER, _MAIN_RUNTIME_CAPABILITY),
        (bubble_runtime_capability, EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER, _BUBBLE_RUNTIME_CAPABILITY),
    ):
        identifier = cap.get("identifier")
        assert identifier == expected_id, (
            f"capability identifier {identifier!r} must match filename stem {expected_id!r} (file: {path.name})"
        )
        filename_stem = path.stem
        assert filename_stem == identifier, (
            f"filename stem {filename_stem!r} must match identifier {identifier!r} (file: {path.name})"
        )


def test_grants_shell_allow_spawn_scoped_to_python_sidecar(
    migrate_runtime_capability: dict,
    tauri_conf: dict,
) -> None:
    """ADR-0020 §7: ``shell:allow-spawn`` granted + spawn scoping intact."""
    permissions = migrate_runtime_capability["permissions"]
    assert "shell:allow-spawn" in permissions, (
        "capability must grant 'shell:allow-spawn' (ADR-0020 §7), "
        "without it the Rust host cannot spawn the Python sidecar"
    )

    shell_plugin = tauri_conf.get("plugins", {}).get("shell")
    assert shell_plugin == {"open": False}, (
        "plugins.shell must be exactly {'open': false}, tauri-plugin-shell "
        "v2 rejects 'sidecar'/'scope' keys at startup "
        f"('unknown field `scope`, expected `open`'); got {shell_plugin!r}"
    )


def test_grants_shell_allow_kill_for_force_kill(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + §10: ``shell:allow-kill`` (or kill-children) granted."""
    permissions = migrate_runtime_capability["permissions"]
    acceptable_kill_perms = {
        "shell:allow-kill",
        "shell:allow-kill-children",
    }
    granted_kill_perms = acceptable_kill_perms & set(permissions)
    assert granted_kill_perms, (
        f"capability must grant at least one of {acceptable_kill_perms} "
        f"(ADR-0020 §7 + §10, force-kill backstop), permissions: "
        f"{permissions!r}"
    )


def test_grants_notification_permission(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §6.1 + §7: ``notification:allow-notify`` granted."""
    permissions = migrate_runtime_capability["permissions"]
    acceptable_notification_perms = {
        "notification:allow-notify",
        "notification:default",
    }
    granted = acceptable_notification_perms & set(permissions)
    assert granted, (
        f"capability must grant at least one of "
        f"{acceptable_notification_perms} (ADR-0020 §6.1 + §7, toast path) "
        f"— permissions: {permissions!r}"
    )


# Test 7: clipboard-manager plugin REMOVED () ────────────────


def test_clipboard_manager_plugin_removed(
    migrate_runtime_capability: dict,
) -> None:
    """XE-4-4: ``tauri-plugin-clipboard-manager`` capability grants removed."""
    permissions = migrate_runtime_capability["permissions"]
    forbidden_clipboard_perms = {
        "clipboard-manager:allow-read-text",
        "clipboard-manager:allow-write-text",
        "clipboard-manager:allow-clear",
        "clipboard-manager:default",
    }
    granted = forbidden_clipboard_perms & set(permissions)
    assert not granted, (
        f"capability must NOT grant any of "
        f"{forbidden_clipboard_perms} (XE-4-4, clipboard-manager "
        f"plugin removed as vestigial exfiltration vector), "
        f"permissions: {permissions!r}"
    )


# ─── Test 8: single-instance granted (capability OR plugin registration)


def test_grants_single_instance(
    migrate_runtime_capability: dict,
    tauri_conf: dict,
    main_rs_source: str,
) -> None:
    """ADR-0020 §7 + §12: single-instance gate is in place."""
    permissions = migrate_runtime_capability["permissions"]
    capability_grants_single_instance = any(perm.startswith("single-instance:") for perm in permissions)

    # Plugin registration path (the implementation's chosen approach).
    plugin_in_main_rs = "tauri_plugin_single_instance::init" in main_rs_source
    plugin_in_tauri_conf = "single-instance" in tauri_conf.get("plugins", {})

    if not capability_grants_single_instance:
        # Must fall back to the plugin-registration path.
        assert plugin_in_main_rs, (
            "single-instance:default not granted in capability AND "
            "tauri_plugin_single_instance::init not called in main.rs, "
            "ADR-0020 §12 single-instance gate is missing"
        )
        assert plugin_in_tauri_conf, (
            "single-instance:default not granted in capability AND "
            "'single-instance' not listed in tauri.conf.json plugins, "
            "ADR-0020 §12 single-instance gate is missing"
        )

    # If the capability grants it directly, the plugin must STILL be
    assert plugin_in_main_rs, (
        "tauri_plugin_single_instance::init must be called in main.rs "
        "regardless of capability grant, capabilities gate IPC, not "
        "plugin registration (ADR-0020 §12)"
    )


def test_does_not_grant_overly_broad_permissions(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + §15: capability must NOT grant overly-broad perms."""
    permissions = migrate_runtime_capability["permissions"]
    granted_forbidden = [perm for perm in permissions if perm in FORBIDDEN_BROAD_PERMISSIONS]
    assert not granted_forbidden, (
        f"capability grants overly-broad / out-of-scope permissions: "
        f"{granted_forbidden!r}, ADR-0020 §7 mandates least privilege; "
        f"see FORBIDDEN_BROAD_PERMISSIONS for the rationale per identifier"
    )


def test_dispatch_command_is_registered_as_tauri_command(
    main_rs_source: str,
    sidecar_cmds_rs_source: str,
) -> None:
    """ADR-0020 §7: the generic ``dispatch`` command is registered."""
    # 1. sidecar_cmds.rs defines dispatch as a #[tauri::command].
    assert "#[tauri::command]" in sidecar_cmds_rs_source, (
        "sidecar_cmds.rs must define at least one #[tauri::command], no tauri::command attribute found"
    )
    # The public generic dispatch command is `pub async fn dispatch(`.
    assert "fn dispatch(" in sidecar_cmds_rs_source, (
        "sidecar_cmds.rs must define a `dispatch` function, ADR-0020 §7 mandates exactly one generic dispatch command"
    )
    # The #[tauri::command] attribute must appear BEFORE `fn dispatch(`
    dispatch_idx = sidecar_cmds_rs_source.find("fn dispatch(")
    tauri_cmd_idx = sidecar_cmds_rs_source.rfind("#[tauri::command]", 0, dispatch_idx)
    assert tauri_cmd_idx != -1 and dispatch_idx != -1, (
        "both #[tauri::command] and fn dispatch( must be present in sidecar_cmds.rs"
    )
    assert tauri_cmd_idx < dispatch_idx, (
        "#[tauri::command] attribute must precede `fn dispatch(` in "
        "sidecar_cmds.rs, currently the attribute appears AFTER the fn"
    )

    # 2. main.rs registers dispatch in generate_handler!.
    assert "generate_handler!" in main_rs_source, "main.rs must call tauri::generate_handler! to register commands"
    assert "dispatch" in main_rs_source, (
        "main.rs must reference `dispatch` (in the generate_handler! list "
        "or a `use` statement), ADR-0020 §7 generic dispatch command"
    )
    # The dispatch identifier must appear inside the generate_handler!
    gen_handler_start = main_rs_source.find("generate_handler!")
    assert gen_handler_start != -1, "generate_handler! not found in main.rs"
    # The macro body is terminated by `]` followed by `)` (when invoked
    macro_body_full = main_rs_source[gen_handler_start:]
    closing_idx = macro_body_full.find("]")
    assert closing_idx != -1, "generate_handler! macro body not terminated by ']'"
    macro_body = macro_body_full[:closing_idx]
    assert "dispatch" in macro_body, (
        "`dispatch` must be listed inside the generate_handler![...] macro "
        "body in main.rs, without it, invoke('dispatch', ...) rejects at "
        "runtime with 'command not found'"
    )
