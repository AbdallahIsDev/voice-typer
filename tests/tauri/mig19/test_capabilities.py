r"""MIG-1.9 Phase 3 + §7 — Tauri v2 capabilities least-privilege validation.

This is the **capabilities gate** for MIG-1.9 Phase 3 (the Tauri v2
shell + Python sidecar runtime migration). It validates that
``src-tauri/capabilities/migrate-runtime.json`` grants exactly the
least-privilege permissions ADR-0020 §7 mandates — and **nothing
broader**.

Scope of this check (ADR-0020 §7 "Tauri config + capabilities"):

1. ``src-tauri/capabilities/migrate-runtime.json`` exists + is valid
   JSON + its ``identifier`` matches the filename (Tauri v2 enforces
   this — capabilities are loaded by filename and matched by
   identifier in ``app.security.capabilities``).

2. ``src-tauri/tauri.conf.json``'s ``app.security.capabilities`` list
   references ``migrate-runtime`` — without this reference the
   capability file is dead code (Tauri silently ignores it).

3. The capability grants **``shell:allow-spawn``** AND the
   ``tauri.conf.json`` ``plugins.shell.scope`` is restricted to
   ``bin/python-sidecar`` (sidecar = true). ADR-0020 §7 requires the
   spawn permission be *scoped to the sidecar binary only* — Tauri v2
   enforces BOTH the capability permission AND the config scope at
   runtime, so an unconstrained ``shell:allow-spawn`` is a privilege
   escape (any webview ``invoke('shell:spawn', ...)`` could launch
   ``cmd.exe`` / ``sh``).

4. The capability grants **``shell:allow-kill``** (or the more
   scoped ``shell:allow-kill-children``) — this is the supervisor
   force-kill backstop (ADR-0020 §10 — the Rust supervisor kills the
   sidecar child on crash / shutdown to prevent zombie processes).

5. The capability grants **``notification:allow-notify``** (or
   ``notification:default``) — ADR-0020 §6.1 routes the existing
   ``electron_notification`` event through
   ``tauri-plugin-notification`` (WinRT ToastNotification /
   NSUserNotificationCenter / libnotify). Without this grant, the
   toast path silently no-ops.

6. The capability grants
   **``clipboard-manager:allow-write-text``** (or
   ``clipboard-manager:default``) — ADR-0020 §6.2 routes the long-text
   paste path through ``tauri-plugin-clipboard-manager`` (clipboard +
   Ctrl/Cmd+V via enigo). The short-text path uses enigo.text() only
   and does NOT need this grant, but the long-text path does.

7. The capability grants **``single-instance:default``** — OR, per
   the implementation note in ``migrate-runtime.json``'s description
   field, the ``tauri-plugin-single-instance`` plugin is registered
   in ``src-tauri/src/main.rs`` AND listed in
   ``tauri.conf.json``'s ``plugins`` object. The Tauri v2
   ``single-instance`` plugin is non-scoped (it gates the second
   instance at the OS mutex / NSApplication activation / lockfile
   layer, not via an IPC permission), so it does not strictly require
   a capability grant. ADR-0020 §7 lists ``single-instance:default``
   as the canonical grant; the equivalent plugin registration is the
   accepted fallback (see ``migrate-runtime.json`` description).

8. The capability does **NOT** grant overly-broad permissions —
   specifically:
   - No ``shell:default`` (would grant ALL shell perms, defeating the
     per-triple spawn scope).
   - No ``fs:default`` (would grant unrestricted filesystem access —
     the sidecar owns FS access; the Rust host has no FS commands).
   - No ``http:default`` (would grant unrestricted HTTP fetch —
     cloud engines stay in the Python sidecar, ADR-0020 §6.5).
   - No ``process:default`` / ``process:allow-restart`` (supervisor
     full-app relaunch uses ``AppHandle::restart()`` from the core
     tauri crate, not the plugin — ADR-0020 §15 + the capability
     description).

9. The generic **``dispatch``** command (ADR-0020 §7 — "Exactly ONE
   generic Rust command bridges the webview to the sidecar") is
   registered as a ``#[tauri::command]`` in
   ``src-tauri/src/commands/sidecar_cmds.rs`` AND listed in
   ``src-tauri/src/main.rs``'s ``tauri::generate_handler!`` macro.
   Custom Tauri v2 commands do NOT need a capability entry — only
   plugin commands (``shell:*``, ``notification:*``, etc.) are gated
   by the capability system. ADR-0020 §7: "no per-command ``ipc:``
   capability entry is needed — Rust maps ``dispatch`` to the WS
   connection."

10. The capability's ``identifier`` field matches the filename stem
    (``migrate-runtime.json`` → ``identifier: "migrate-runtime"``).
    Tauri v2's capability loader uses the identifier as the
    stable reference in ``app.security.capabilities``; a mismatch
    would silently disconnect the capability from the app.

VALIDATE ON HOST
================

This file is the Linux-sandbox static check. The actual runtime
capability enforcement MUST be exercised by a human on real hosts
(ADR-0020 §6 / migration runbook) — Tauri v2 silently blocks
ungated ``invoke()`` calls at runtime (no compile error, no console
warning in release builds). One host per platform × arch combo:

**VALIDATE ON HOST — Windows x64 (x86_64-pc-windows-msvc)**::

    # 1. Build + install the NSIS bundle.
    cd src-tauri
    cargo tauri build --target x86_64-pc-windows-msvc
    cd ..
    # Install target\\x86_64-pc-windows-msvc\\release\\bundle\\nsis\\*-setup.exe

    # 2. Launch "Voice Typer" from the Start Menu.

    # 3. Trigger each capability path from the UI:
    #    a) Sidecar spawn — automatic on launch.
    #       Expected: sidecar spawns within 30 s, log shows
    #       [SIDECAR] server_started port=<ephemeral>.
    #       Fail: "permission denied: shell:allow-spawn" → capability
    #       missing OR scope mismatch.
    #    b) Toast — trigger a notification (e.g. dictation timeout).
    #       Expected: WinRT toast appears.
    #       Fail: silent no-op → notification:allow-notify missing.
    #    c) Clipboard paste — dictate >300 chars (long-text path).
    #       Expected: text pasted into focused window via clipboard+Ctrl+V.
    #       Fail: "permission denied: clipboard-manager:allow-write-text"
    #       → capability missing.
    #    d) Single-instance — launch a second "Voice Typer" instance.
    #       Expected: first instance focuses, second exits.
    #       Fail: two sidecars running → plugin not registered OR
    #       Python-side VoiceTyperSingleInstance mutex not disabled
    #       (TAURI_SIDECAR=1).
    #    e) Dispatch — invoke any command from the React UI (e.g.
    #       toggle dictation). Expected: command reaches sidecar.
    #       Fail: "command not found" → dispatch not in generate_handler!.

    # 4. Verify no capability-leak warnings in the dev console:
    #    Open DevTools (Ctrl+Shift+I in dev mode) → Console.
    #    Expected: no "[Tauri] capability denied" warnings.

**VALIDATE ON HOST — macOS (aarch64-apple-darwin)**::

    cd src-tauri
    cargo tauri build --target aarch64-apple-darwin
    cd ..
    open target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
    # Drag Voice Typer.app to /Applications, launch it.
    # Repeat steps 3a–3e above. Toast = NSUserNotificationCenter /
    # UNUserNotificationCenter; single-instance = NSApplication
    # activation. Fail patterns same as Windows.

**VALIDATE ON HOST — Linux x64 (x86_64-unknown-linux-gnu)**::

    cd src-tauri
    cargo tauri build --target x86_64-unknown-linux-gnu
    cd ..
    # Install the AppImage / deb / rpm bundle, then launch.
    # Repeat steps 3a–3e above. Toast = libnotify
    # (notify-send); single-instance = lockfile in
    # <config_dir>/.single-instance.lock.
    # Wayland-specific: AppImage on Wayland may restrict wl-copy
    # access — test the AppImage on a Wayland session (Fedora 40
    # default, Ubuntu 22.04 with GNOME session) before cutover
    # (ADR-0020 §6.6).

Each host run validates three things:
1. The capability grants actually take effect at runtime (Tauri v2
   silently blocks ungated calls — only a real host run catches a
   typo like ``notification:allow-notify`` vs ``notification:notify``).
2. The shell scope restricts spawn to ``bin/python-sidecar`` (a
   malicious / buggy webview ``invoke('shell:spawn', {program: 'cmd'})
   must be rejected).
3. The single-instance plugin actually focuses the existing instance
   (not just exits silently) — ADR-0020 §12.

References:
- ADR-0020 §7 — Tauri config + capabilities + least-privilege contract.
- ADR-0020 §6.1 — toast / notification path (tauri-plugin-notification).
- ADR-0020 §6.2 — paste path (clipboard-manager + enigo).
- ADR-0020 §10 — force-kill backstop (shell:allow-kill).
- ADR-0020 §12 — single-instance behavior + ordering.
- ADR-0020 §15 — auto-update intentionally NOT granted (updater plugin
  out of scope for v1).
- Tauri v2 docs — "Capabilities" (https://tauri.app/security/capabilities/):
  every plugin command must be explicitly whitelisted or Tauri silently
  blocks it at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ─── Repo path resolution ──────────────────────────────────────────────
# Tests run from the repo root, but every path is resolved relative to
# this file's location so the tests pass regardless of cwd.
# parents[0]=mig19, [1]=tauri, [2]=tests, [3]=voice-typer (repo root)
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _SRC_TAURI / "tauri.conf.json"
_CAPABILITIES_DIR = _SRC_TAURI / "capabilities"
# CR-5: migrate-runtime.json was split into main-runtime.json + bubble-runtime.json
_MAIN_RUNTIME_CAPABILITY = _CAPABILITIES_DIR / "main-runtime.json"
_BUBBLE_RUNTIME_CAPABILITY = _CAPABILITIES_DIR / "bubble-runtime.json"
_MAIN_RS = _SRC_TAURI / "src" / "main.rs"
_SIDECAR_CMDS_RS = _SRC_TAURI / "src" / "commands" / "sidecar_cmds.rs"


# ─── Expected wiring constants (single source of truth) ────────────────

#: ADR-0020 §7 + CR-5: capability identifier referenced by tauri.conf.json's
#: ``app.security.capabilities`` list. CR-5 split the original
#: ``migrate-runtime.json`` into ``main-runtime.json`` (full grant set)
#: + ``bubble-runtime.json`` (minimal sandboxed grant).
EXPECTED_MAIN_CAPABILITY_IDENTIFIER = "main-runtime"
EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER = "bubble-runtime"
EXPECTED_CAPABILITY_IDENTIFIER = EXPECTED_MAIN_CAPABILITY_IDENTIFIER

#: ADR-0020 §4.1 + §7: externalBin base name (Tauri appends the Rust
#: target triple at spawn time). The shell scope in tauri.conf.json
#: must restrict spawn to this binary only.
EXPECTED_SIDECAR_BINARY = "bin/python-sidecar"

#: ADR-0020 §7: overly-broad permission identifiers that MUST NOT
#: appear in the capabilities file (privilege-escape vectors).
FORBIDDEN_BROAD_PERMISSIONS = (
    "shell:default",  # grants ALL shell perms → defeats spawn scope
    "fs:default",  # unrestricted filesystem access (sidecar owns FS)
    "http:default",  # unrestricted HTTP fetch (cloud engines in sidecar)
    "http:allow-fetch",  # same — HTTP stays in Python sidecar (ADR-0020 §6.5)
    "process:default",  # unrestricted process control
    "process:allow-restart",  # supervisor uses core AppHandle::restart() (not plugin)
    "global-shortcut:default",  # native hotkey binaries stay in Python (§6.4)
    "updater:default",  # auto-update out of scope for v1 (§15)
    "updater:allow-check",  # same
    "updater:allow-download",  # same
)


# ─── Shared fixtures ───────────────────────────────────────────────────


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
# ``migrate_runtime_capability`` continue to work after the CR-5 split.
@pytest.fixture(scope="module")
def migrate_runtime_capability(main_runtime_capability: dict) -> dict:
    """Back-compat alias — delegates to ``main_runtime_capability`` after CR-5."""
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
    return _SIDECAR_CMDS_RS.read_text(encoding="utf-8")


# ─── Test 1: capability file exists + is valid JSON ────────────────────


def test_capabilities_file_exists_and_is_valid_json(
    main_runtime_capability: dict,
    bubble_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + CR-5: ``main-runtime.json`` + ``bubble-runtime.json`` parse as JSON."""
    for cap, label in (
        (main_runtime_capability, "main-runtime.json"),
        (bubble_runtime_capability, "bubble-runtime.json"),
    ):
        assert isinstance(cap, dict), f"{label} must parse to a JSON object"
        assert "identifier" in cap, f"{label} must have an 'identifier' field (Tauri v2 schema)"
        assert "permissions" in cap, f"{label} must have a 'permissions' field (Tauri v2 schema)"
        assert isinstance(cap["permissions"], list), f"{label} 'permissions' must be a list of permission identifiers"


# ─── Test 2: tauri.conf.json references main-runtime + bubble-runtime ───


def test_tauri_conf_references_migrate_runtime_capability(
    tauri_conf: dict,
) -> None:
    """ADR-0020 §7 + CR-5: ``app.security.capabilities`` lists both
    ``main-runtime`` and ``bubble-runtime`` (the original migrate-runtime
    was deleted)."""
    security = tauri_conf.get("app", {}).get("security", {})
    assert "capabilities" in security, (
        "app.security.capabilities must exist (ADR-0020 §7) — without it the capability files are never loaded"
    )
    capabilities = security["capabilities"]
    assert isinstance(capabilities, list), "app.security.capabilities must be a list of identifier strings"
    assert EXPECTED_MAIN_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference {EXPECTED_MAIN_CAPABILITY_IDENTIFIER!r} — got {capabilities!r}"
    )
    assert EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference {EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER!r} "
        f"(CR-5 split — the bubble window MUST have its own minimal capability) — got {capabilities!r}"
    )
    assert "migrate-runtime" not in capabilities, (
        f"app.security.capabilities must NOT reference 'migrate-runtime' (CR-5 deleted it) — got {capabilities!r}"
    )


# ─── Test 3: identifier matches filename (CR-5: both files) ────────────


def test_capability_identifier_matches_filename(
    main_runtime_capability: dict,
    bubble_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + CR-5: ``identifier`` field matches the filename stem."""
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


# ─── Test 4: shell:allow-spawn granted + scoped to python-sidecar ──────


def test_grants_shell_allow_spawn_scoped_to_python_sidecar(
    migrate_runtime_capability: dict,
    tauri_conf: dict,
) -> None:
    """ADR-0020 §7: ``shell:allow-spawn`` granted + scoped to sidecar.

    Tauri v2 enforces BOTH the capability permission AND the config
    scope at runtime — the capability grants the *right* to call
    ``shell:spawn``, and ``plugins.shell.scope`` in tauri.conf.json
    restricts *which* binaries may be spawned. An unconstrained
    ``shell:allow-spawn`` (scope = []) would let any webview
    ``invoke('shell:spawn', {program: 'cmd'})`` launch arbitrary
    programs — a privilege escape.

    ADR-0020 §7 lists per-triple scope entries (one per target
    triple); the implementation uses the base name
    ``bin/python-sidecar`` with Tauri's sidecar mechanism (Tauri
    appends the Rust target triple at spawn time — see §4.1). Both
    forms are acceptable; what matters is that the scope names the
    sidecar binary ONLY (no ``cmd``, ``sh``, ``bash``, etc.).
    """
    permissions = migrate_runtime_capability["permissions"]
    assert "shell:allow-spawn" in permissions, (
        "capability must grant 'shell:allow-spawn' (ADR-0020 §7) — "
        "without it the Rust host cannot spawn the Python sidecar"
    )

    # The shell scope in tauri.conf.json must restrict spawn to the
    # sidecar binary ONLY.
    shell_plugin = tauri_conf.get("plugins", {}).get("shell", {})
    assert "scope" in shell_plugin, (
        "plugins.shell.scope must exist in tauri.conf.json — without it "
        "shell:allow-spawn is unconstrained (privilege escape)"
    )
    scope = shell_plugin["scope"]
    assert isinstance(scope, list) and scope, "plugins.shell.scope must be a non-empty list of allowed binaries"

    # Every scope entry must name the sidecar binary — no foreign
    # binaries (cmd, sh, bash, powershell, etc.) may be in scope.
    sidecar_in_scope = False
    for entry in scope:
        assert isinstance(entry, dict), f"shell.scope entry must be an object — got {type(entry).__name__}"
        name = entry.get("name", "")
        cmd = entry.get("cmd", "")
        # The scope entry must point at the python-sidecar binary.
        assert EXPECTED_SIDECAR_BINARY in (name, cmd), (
            f"shell.scope entry names a non-sidecar binary: name={name!r} "
            f"cmd={cmd!r} — only {EXPECTED_SIDECAR_BINARY!r} is permitted "
            f"(ADR-0020 §7 least-privilege)"
        )
        # The sidecar flag must be true (Tauri v2 requires this for
        # externalBin binaries — gates the per-triple suffix append).
        assert entry.get("sidecar") is True, (
            f"shell.scope entry for {name!r} must set sidecar=true (Tauri v2 externalBin contract)"
        )
        sidecar_in_scope = True

    assert sidecar_in_scope, f"shell.scope must include an entry for {EXPECTED_SIDECAR_BINARY!r} — got {scope!r}"


# ─── Test 5: shell:allow-kill granted for force-kill ──────────────


def test_grants_shell_allow_kill_for_force_kill(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + §10: ``shell:allow-kill`` (or kill-children) granted.

    The supervisor (``src-tauri/src/sidecar/supervisor.rs``) force-kills
    the sidecar child on crash / shutdown to prevent zombie processes
    (ADR-0020 §10). Without ``shell:allow-kill`` or the more scoped
    ``shell:allow-kill-children``, the kill call silently no-ops and
    the sidecar leaks as a zombie holding the microphone.

    ADR-0020 §7 names ``shell:allow-kill-children``; the implementation
    uses ``shell:allow-kill`` (slightly broader — kills any spawned
    child, not just direct children). Both are accepted by this gate;
    what matters is that the kill permission exists.
    """
    permissions = migrate_runtime_capability["permissions"]
    acceptable_kill_perms = {
        "shell:allow-kill",
        "shell:allow-kill-children",
    }
    granted_kill_perms = acceptable_kill_perms & set(permissions)
    assert granted_kill_perms, (
        f"capability must grant at least one of {acceptable_kill_perms} "
        f"(ADR-0020 §7 + §10 — force-kill backstop) — permissions: "
        f"{permissions!r}"
    )


# ─── Test 6: notification permission granted ───────────────────────────


def test_grants_notification_permission(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §6.1 + §7: ``notification:allow-notify`` granted.

    The ``electron_notification`` event routes through
    ``tauri-plugin-notification`` (WinRT ToastNotification /
    NSUserNotificationCenter / libnotify). Without this grant, the
    toast path silently no-ops — the user sees no notification and
    there is no error in the dev console (Tauri v2 silently blocks
    ungated plugin commands in release builds).

    ``notification:default`` is also accepted (it's a superset that
    includes ``allow-notify``).
    """
    permissions = migrate_runtime_capability["permissions"]
    acceptable_notification_perms = {
        "notification:allow-notify",
        "notification:default",
    }
    granted = acceptable_notification_perms & set(permissions)
    assert granted, (
        f"capability must grant at least one of "
        f"{acceptable_notification_perms} (ADR-0020 §6.1 + §7 — toast path) "
        f"— permissions: {permissions!r}"
    )


# ─── Test 7: clipboard-manager write-text granted ──────────────────────


def test_grants_clipboard_manager_write_text(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §6.2 + §7: ``clipboard-manager:allow-write-text`` granted.

    The long-text paste path (>~300 chars) copies the text via
    ``tauri-plugin-clipboard-manager`` then sends Ctrl+V / Cmd+V via
    enigo. Without this grant, the long-text paste silently no-ops
    (the short-text path uses enigo.text() only and is unaffected,
    but anything over ~300 chars would be lost).

    ``clipboard-manager:default`` is also accepted (superset).
    """
    permissions = migrate_runtime_capability["permissions"]
    acceptable_clipboard_perms = {
        "clipboard-manager:allow-write-text",
        "clipboard-manager:default",
    }
    granted = acceptable_clipboard_perms & set(permissions)
    assert granted, (
        f"capability must grant at least one of "
        f"{acceptable_clipboard_perms} (ADR-0020 §6.2 + §7 — long-text "
        f"paste path) — permissions: {permissions!r}"
    )


# ─── Test 8: single-instance granted (capability OR plugin registration)


def test_grants_single_instance(
    migrate_runtime_capability: dict,
    tauri_conf: dict,
    main_rs_source: str,
) -> None:
    """ADR-0020 §7 + §12: single-instance gate is in place.

    ADR-0020 §7 lists ``single-instance:default`` as the canonical
    capability grant. However, the ``tauri-plugin-single-instance``
    plugin is non-scoped (it gates the second instance at the OS
    mutex / NSApplication activation / lockfile layer, not via an IPC
    permission), so it does not strictly require a capability grant
    — see the ``migrate-runtime.json`` description field which
    documents this exemption.

    This test accepts EITHER:
    - ``single-instance:default`` (or ``single-instance:allow-*``) in
      the capability's permissions list, OR
    - The plugin is registered in ``main.rs`` via
      ``tauri_plugin_single_instance::init(...)`` AND listed in
      ``tauri.conf.json``'s ``plugins`` object.

    The plugin registration is what actually enforces single-instance
    behavior at runtime (ADR-0020 §12 — second launch focuses the
    existing main window and exits).
    """
    permissions = migrate_runtime_capability["permissions"]
    capability_grants_single_instance = any(perm.startswith("single-instance:") for perm in permissions)

    # Plugin registration path (the implementation's chosen approach).
    plugin_in_main_rs = "tauri_plugin_single_instance::init" in main_rs_source
    plugin_in_tauri_conf = "single-instance" in tauri_conf.get("plugins", {})

    if not capability_grants_single_instance:
        # Must fall back to the plugin-registration path.
        assert plugin_in_main_rs, (
            "single-instance:default not granted in capability AND "
            "tauri_plugin_single_instance::init not called in main.rs — "
            "ADR-0020 §12 single-instance gate is missing"
        )
        assert plugin_in_tauri_conf, (
            "single-instance:default not granted in capability AND "
            "'single-instance' not listed in tauri.conf.json plugins — "
            "ADR-0020 §12 single-instance gate is missing"
        )

    # If the capability grants it directly, the plugin must STILL be
    # registered (the capability alone is insufficient — capabilities
    # gate IPC, not plugin registration).
    assert plugin_in_main_rs, (
        "tauri_plugin_single_instance::init must be called in main.rs "
        "regardless of capability grant — capabilities gate IPC, not "
        "plugin registration (ADR-0020 §12)"
    )


# ─── Test 9: no overly-broad permissions granted ───────────────────────


def test_does_not_grant_overly_broad_permissions(
    migrate_runtime_capability: dict,
) -> None:
    """ADR-0020 §7 + §15: capability must NOT grant overly-broad perms.

    The migrate-runtime capability is the **least-privilege** grant
    for the Tauri + Python sidecar shell. Overly-broad permissions
    defeat the per-triple spawn scope (``shell:default``), grant
    filesystem access the Rust host doesn't need (``fs:default``),
    or enable HTTP fetch that must stay in the Python sidecar
    (``http:default`` — ADR-0020 §6.5 keeps cloud engines in Python).

    Auto-update (``updater:*``) is intentionally NOT granted — v1
    ships without auto-update (ADR-0020 §15). Process control
    (``process:*``) is unnecessary — supervisor uses the core
    ``AppHandle::restart()`` API, not the process plugin.
    """
    permissions = migrate_runtime_capability["permissions"]
    granted_forbidden = [perm for perm in permissions if perm in FORBIDDEN_BROAD_PERMISSIONS]
    assert not granted_forbidden, (
        f"capability grants overly-broad / out-of-scope permissions: "
        f"{granted_forbidden!r} — ADR-0020 §7 mandates least privilege; "
        f"see FORBIDDEN_BROAD_PERMISSIONS for the rationale per identifier"
    )


# ─── Test 10: dispatch command is registered as a Tauri command ────────


def test_dispatch_command_is_registered_as_tauri_command(
    main_rs_source: str,
    sidecar_cmds_rs_source: str,
) -> None:
    """ADR-0020 §7: the generic ``dispatch`` command is registered.

    ADR-0020 §7 mandates "Exactly ONE generic Rust command" bridging
    the webview to the sidecar: ``invoke('dispatch', {cmd, data})``.
    The webview calls ``dispatch``; Rust forwards the envelope over
    the WebSocket and awaits the per-id response.

    Custom Tauri v2 commands (``#[tauri::command]`` fns registered
    via ``tauri::generate_handler!``) do NOT need a capability entry
    — only plugin commands (``shell:*``, ``notification:*``, etc.)
    are gated by the capability system. ADR-0020 §7: "no per-command
    ``ipc:`` capability entry is needed — Rust maps ``dispatch`` to
    the WS connection."

    This test verifies the dispatch command is:
    1. Defined as a ``#[tauri::command]`` in ``sidecar_cmds.rs``.
    2. Listed in ``main.rs``'s ``tauri::generate_handler!`` macro.

    A failure here means the webview's ``invoke('dispatch', ...)``
    call would reject with "command not found" at runtime.
    """
    # 1. sidecar_cmds.rs defines dispatch as a #[tauri::command].
    assert "#[tauri::command]" in sidecar_cmds_rs_source, (
        "sidecar_cmds.rs must define at least one #[tauri::command] — no tauri::command attribute found"
    )
    # The public generic dispatch command is `pub async fn dispatch(`.
    # (The CR-5/CR-14 decomposition also adds `dispatch_inner` and
    # `dispatch_frame` internal helpers — match the public command
    # exactly so those helpers don't shadow this check.)
    assert "fn dispatch(" in sidecar_cmds_rs_source, (
        "sidecar_cmds.rs must define a `dispatch` function — ADR-0020 §7 mandates exactly one generic dispatch command"
    )
    # The #[tauri::command] attribute must appear BEFORE `fn dispatch(`
    # (within a few lines — Rust attribute placement is strict). The
    # attribute immediately preceding the public dispatch command is the
    # last `#[tauri::command]` before `fn dispatch(`.
    dispatch_idx = sidecar_cmds_rs_source.find("fn dispatch(")
    tauri_cmd_idx = sidecar_cmds_rs_source.rfind("#[tauri::command]", 0, dispatch_idx)
    assert tauri_cmd_idx != -1 and dispatch_idx != -1, (
        "both #[tauri::command] and fn dispatch( must be present in sidecar_cmds.rs"
    )
    assert tauri_cmd_idx < dispatch_idx, (
        "#[tauri::command] attribute must precede `fn dispatch(` in "
        "sidecar_cmds.rs — currently the attribute appears AFTER the fn"
    )

    # 2. main.rs registers dispatch in generate_handler!.
    assert "generate_handler!" in main_rs_source, "main.rs must call tauri::generate_handler! to register commands"
    assert "dispatch" in main_rs_source, (
        "main.rs must reference `dispatch` (in the generate_handler! list "
        "or a `use` statement) — ADR-0020 §7 generic dispatch command"
    )
    # The dispatch identifier must appear inside the generate_handler!
    # macro body (between `generate_handler!` and the closing `]`).
    gen_handler_start = main_rs_source.find("generate_handler!")
    assert gen_handler_start != -1, "generate_handler! not found in main.rs"
    # The macro body is terminated by `]` followed by `)` (when invoked
    # inline as `.invoke_handler(tauri::generate_handler![...])`) or by
    # `]` followed by `;` (when assigned to a variable). Find the first
    # `]` after `generate_handler!` — that closes the macro body.
    macro_body_full = main_rs_source[gen_handler_start:]
    closing_idx = macro_body_full.find("]")
    assert closing_idx != -1, "generate_handler! macro body not terminated by ']'"
    macro_body = macro_body_full[:closing_idx]
    assert "dispatch" in macro_body, (
        "`dispatch` must be listed inside the generate_handler![...] macro "
        "body in main.rs — without it, invoke('dispatch', ...) rejects at "
        "runtime with 'command not found'"
    )
