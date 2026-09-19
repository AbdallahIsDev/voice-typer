"""
Phase 3 + 4 + 5: Final glue validation for the Tauri migration.
These are *static* source-text + JSON-shape assertions; they do not
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TAURI_DIR = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _TAURI_DIR / "tauri.conf.json"
_MAIN_RS = _TAURI_DIR / "src" / "main.rs"
_CARGO_TOML = _TAURI_DIR / "Cargo.toml"
_CLIENT_PACKAGE_JSON = _REPO_ROOT / "voice_typer" / "client" / "package.json"

# : ADR-0020 §7: the renderer build output (predecessor-vite renderer-only
EXPECTED_FRONTEND_DIST = "../voice_typer/client/out/renderer"

# : ADR-0020 §7: Vite dev server default port (1420, Vite's
EXPECTED_DEV_URL = "http://localhost:1420"

EXPECTED_RENDERER_BUILD_SCRIPT = "build:renderer"

# : The Tauri CLI pins its own process CWD to ``src-tauri/`` immediately
# : from. The plain string hook forms must NOT be used here: their
EXPECTED_HOOK_CWD = "../voice_typer/client"

# ADR-0020 §7 + : capability identifier referenced by tauri.conf.json's
EXPECTED_CAPABILITY_IDENTIFIER = "main-runtime"
EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER = "bubble-runtime"

# : ADR-0020 §7 + the predecessor CSP (client/src/main/bootstrap.ts setupCsp).
EXPECTED_CSP_CORE_DIRECTIVES = [
    "default-src 'self'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
]

# : ADR-0020 §6 + §6.2 + §12: plugins that main.rs MUST register via
EXPECTED_MAIN_RS_PLUGINS = [
    "tauri_plugin_single_instance",
    "tauri_plugin_shell",
    "tauri_plugin_notification",
    "tauri_plugin_dialog",
]

# ADR-0020 §6.2 + §7 + §9 + §10 +  +  + : commands
EXPECTED_MAIN_RS_COMMANDS = [
    # ADR-0020 §6.2 + §10, generic dispatch + shutdown.
    "dispatch",
    "shutdown_sidecar",
    "export_history",
    "export_vocabulary",
    # ADR-0020 §9, bubble window commands
    "bubble_show",
    "bubble_signal_ready",
    "bubble_set_position",
    "bubble_set_draggable",
    "bubble_move_by",
    "bubble_hide_complete",
    "bubble_resize",
    "bubble_toggle_dictation",
    # bubble dismiss (SEC-026 bubble '×' button). Registered in
    "bubble_dismiss",
    "open_logs",
    # `open_host_logs` REMOVED, dead in production:
    "open_model_import_dialog",
    "export_templates",
    "export_config",
    "renderer_log_error",
    "set_host_locale",
    # (SEC-026) and each replaces an predecessor main-process capability
    "open_external_url_command",
    "reveal_path_command",
    "restart_sidecar",
    "renderer_heartbeat",
    # `reveal_path_command` above). Main-window-only (SEC-026 via
    "save_stats_image",
]

# : ADR-0020 §15: the v1 Tauri migration MUST NOT wire up
FORBIDDEN_UPDATER_TOKENS = [
    "tauri-plugin-updater",
    "tauri_plugin_updater",
]

# : ADR-0020 module-layout note (main.rs doc comment): main.rs is
MAIN_RS_WIRING_ONLY_LINE_CEILING = 300


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse src-tauri/tauri.conf.json."""
    assert _TAURI_CONF.exists(), f"tauri.conf.json not found: {_TAURI_CONF}"
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def main_rs_source() -> str:
    """Read src-tauri/src/main.rs as text (for static assertions)."""
    assert _MAIN_RS.exists(), f"main.rs not found: {_MAIN_RS}"
    return _MAIN_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cargo_toml_source() -> str:
    """Read src-tauri/Cargo.toml as text (for static assertions)."""
    assert _CARGO_TOML.exists(), f"Cargo.toml not found: {_CARGO_TOML}"
    return _CARGO_TOML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def client_package_json() -> dict:
    """Load + parse voice_typer/client/package.json (verify build:renderer)."""
    assert _CLIENT_PACKAGE_JSON.exists(), f"package.json not found: {_CLIENT_PACKAGE_JSON}"
    return json.loads(_CLIENT_PACKAGE_JSON.read_text(encoding="utf-8"))


def test_tauri_conf_frontend_dist_points_to_renderer_build_output(
    tauri_conf,
) -> None:
    """ADR-0020 §7: ``build.frontendDist`` must point at the React build output."""
    build = tauri_conf.get("build", {})
    assert "frontendDist" in build, (
        "build.frontendDist must exist (ADR-0020 §7), Tauri v2 embeds the "
        "webview from this directory in production builds"
    )
    assert build["frontendDist"] == EXPECTED_FRONTEND_DIST, (
        f"build.frontendDist must be {EXPECTED_FRONTEND_DIST!r} (the React "
        f"renderer build output, relative to src-tauri/); got "
        f"{build['frontendDist']!r}"
    )


def test_tauri_conf_dev_url_is_vite_default_port(tauri_conf) -> None:
    """ADR-0020 §7: ``build.devUrl`` must be the Vite dev server URL."""
    build = tauri_conf.get("build", {})
    assert "devUrl" in build, (
        "build.devUrl must exist (ADR-0020 §7), Tauri loads this URL in dev mode (cargo tauri dev)"
    )
    assert build["devUrl"] == EXPECTED_DEV_URL, (
        f"build.devUrl must be {EXPECTED_DEV_URL!r} (Vite dev server, port 1420); got {build['devUrl']!r}"
    )


# ─── Test 3: beforeDevCommand + beforeBuildCommand run renderer build ─


def test_package_json_defines_build_renderer_script(
    client_package_json,
) -> None:
    """ADR-0020 §7: ``voice_typer/client/package.json`` must define"""
    scripts = client_package_json.get("scripts", {})
    assert EXPECTED_RENDERER_BUILD_SCRIPT in scripts, (
        f"package.json:scripts must define {EXPECTED_RENDERER_BUILD_SCRIPT!r} "
        f"(invoked by tauri.conf.json beforeDevCommand + beforeBuildCommand)"
    )
    script_value = scripts[EXPECTED_RENDERER_BUILD_SCRIPT]
    assert isinstance(script_value, str) and script_value, (
        f"package.json:scripts.{EXPECTED_RENDERER_BUILD_SCRIPT} must be a non-empty string; got {script_value!r}"
    )
    # The script must invoke a build tool (predecessor-vite or vite), not
    assert re.search(r"\b(predecessor-vite|vite)\b.*\bbuild\b", script_value), (
        f"package.json:scripts.{EXPECTED_RENDERER_BUILD_SCRIPT} must invoke a "
        f"Vite-family build (predecessor-vite or vite build); got {script_value!r}"
    )


def test_tauri_conf_before_dev_command_runs_renderer_build(tauri_conf) -> None:
    """ADR-0020 §7: ``beforeDevCommand`` must run ``npm run build:renderer``."""
    build = tauri_conf.get("build", {})
    assert "beforeDevCommand" in build, (
        "build.beforeDevCommand must exist (ADR-0020 §7), Tauri runs this before starting the dev server"
    )
    cmd = build["beforeDevCommand"]
    assert isinstance(cmd, dict), (
        "build.beforeDevCommand must be an object ({'script', 'cwd', 'wait'}) "
        f"— string-form hooks have version-dependent CWD semantics; got {cmd!r}"
    )
    expected_script = f"npm run {EXPECTED_RENDERER_BUILD_SCRIPT}"
    assert cmd.get("script") == expected_script, (
        f"build.beforeDevCommand.script must be exactly {expected_script!r}; got {cmd.get('script')!r}"
    )
    assert cmd.get("cwd") == EXPECTED_HOOK_CWD, (
        f"build.beforeDevCommand.cwd must be {EXPECTED_HOOK_CWD!r} (relative to src-tauri/, "
        f"which is where the CLI pins its CWD before spawning); got {cmd.get('cwd')!r}"
    )
    assert cmd.get("wait") is True, (
        "build.beforeDevCommand.wait must be true, the dev recipe serves static "
        "output on the devUrl port while this hook rebuilds it; without wait the "
        "CLI polls against stale files mid-rebuild"
    )


def test_tauri_conf_before_build_command_runs_renderer_build(tauri_conf) -> None:
    """ADR-0020 §7: ``beforeBuildCommand`` must run ``npm run build:renderer``."""
    build = tauri_conf.get("build", {})
    assert "beforeBuildCommand" in build, (
        "build.beforeBuildCommand must exist (ADR-0020 §7), Tauri runs this before bundling the production app"
    )
    cmd = build["beforeBuildCommand"]
    assert isinstance(cmd, dict), (
        "build.beforeBuildCommand must be an object ({'script', 'cwd'}) "
        f"— string-form hooks have version-dependent CWD semantics; got {cmd!r}"
    )
    expected_script = f"npm run {EXPECTED_RENDERER_BUILD_SCRIPT}"
    assert cmd.get("script") == expected_script, (
        f"build.beforeBuildCommand.script must be exactly {expected_script!r}; got {cmd.get('script')!r}"
    )
    assert cmd.get("cwd") == EXPECTED_HOOK_CWD, (
        f"build.beforeBuildCommand.cwd must be {EXPECTED_HOOK_CWD!r} (relative to src-tauri/, "
        f"which is where the CLI pins its CWD before spawning); got {cmd.get('cwd')!r}"
    )


def test_tauri_conf_security_csp_is_set(tauri_conf) -> None:
    """ADR-0020 §7: ``app.security.csp`` must be a non-empty string."""
    security = tauri_conf.get("app", {}).get("security", {})
    assert "csp" in security, (
        "app.security.csp must exist (ADR-0020 §7), Tauri v2 enforces CSP "
        "at the WebView level; the field must be set explicitly"
    )
    csp = security["csp"]
    assert isinstance(csp, str) and csp, f"app.security.csp must be a non-empty string; got {csp!r}"


def test_tauri_conf_security_csp_has_core_directives(tauri_conf) -> None:
    """ADR-0020 §7: the Tauri CSP must carry the core directives."""
    csp = tauri_conf.get("app", {}).get("security", {}).get("csp", "")
    for directive in EXPECTED_CSP_CORE_DIRECTIVES:
        assert directive in csp, (
            f"app.security.csp must contain {directive!r} (one of the four core CSP directives); full CSP was: {csp!r}"
        )

    # CR-SEC: script-src must NOT allow 'unsafe-eval' or 'unsafe-inline'
    assert "'unsafe-eval'" not in csp, (
        f"app.security.csp must NOT contain 'unsafe-eval' (script injection footgun); full CSP was: {csp!r}"
    )
    # style-src allows 'unsafe-inline' (Tailwind needs it); script-src must NOT.
    script_src_match = re.search(r"script-src\s+([^;]+)", csp)
    assert script_src_match, f"app.security.csp must contain a script-src directive; full CSP was: {csp!r}"
    assert "'unsafe-inline'" not in script_src_match.group(1), (
        f"app.security.csp script-src must NOT contain 'unsafe-inline' "
        f"(script injection footgun); script-src was: {script_src_match.group(1)!r}"
    )


def test_tauri_conf_security_capabilities_references_migrate_runtime(
    tauri_conf,
) -> None:
    """ADR-0020 §7 + ``app.security.capabilities`` must reference"""
    security = tauri_conf.get("app", {}).get("security", {})
    assert "capabilities" in security, (
        "app.security.capabilities must exist (ADR-0020 §7), Tauri v2 "
        "ships zero permissions by default; every plugin/invoke must be "
        "explicitly whitelisted via a capability file"
    )
    capabilities = security["capabilities"]
    assert isinstance(capabilities, list) and capabilities, (
        f"app.security.capabilities must be a non-empty list; got {capabilities!r}"
    )
    assert EXPECTED_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference "
        f"{EXPECTED_CAPABILITY_IDENTIFIER!r} (the main-window capability "
        f"file, CR-5 split); got {capabilities!r}"
    )
    assert EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference "
        f"{EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER!r} (the bubble-window "
        f"sandboxed capability file, CR-5 split / SEC-026); got "
        f"{capabilities!r}"
    )
    assert "migrate-runtime" not in capabilities, (
        f"app.security.capabilities must NOT reference 'migrate-runtime' "
        f"(CR-5 deleted it; use 'main-runtime' + 'bubble-runtime' instead) "
        f"— got {capabilities!r}"
    )


# ─── Test 6: NO tauri-plugin-updater (ADR-0020 §15, no auto-update v1) ─


@pytest.mark.parametrize("forbidden_token", FORBIDDEN_UPDATER_TOKENS)
def test_cargo_toml_has_no_updater_plugin(cargo_toml_source, forbidden_token) -> None:
    """ADR-0020 §15: ``Cargo.toml`` MUST NOT declare ``tauri-plugin-updater``."""
    # Strip comments to avoid false positives from doc references.
    stripped = re.sub(r"^#.*$", "", cargo_toml_source, flags=re.MULTILINE)
    assert forbidden_token not in stripped, (
        f"Cargo.toml MUST NOT declare {forbidden_token!r} (ADR-0020 §15: "
        f"auto-update is out of scope for the v1 Tauri migration). Found "
        f"in Cargo.toml source."
    )


def test_tauri_conf_has_no_updater_plugin_entry(tauri_conf) -> None:
    """ADR-0020 §15: ``tauri.conf.json`` ``plugins`` MUST NOT include ``updater``."""
    plugins = tauri_conf.get("plugins", {})
    assert isinstance(plugins, dict), f"tauri.conf.json:plugins must be a dict; got {type(plugins).__name__}"
    assert "updater" not in plugins, (
        "tauri.conf.json:plugins MUST NOT include 'updater' (ADR-0020 §15: "
        "auto-update is out of scope for the v1 Tauri migration). Found "
        f"'updater' key in plugins block: {plugins!r}"
    )


def test_tauri_conf_unit_config_plugins_are_null(tauri_conf) -> None:
    """Unit-config plugins MUST deserialize as serde units (``null``)."""
    plugins = tauri_conf.get("plugins", {})
    assert isinstance(plugins, dict), f"tauri.conf.json:plugins must be a dict; got {type(plugins).__name__}"
    for plugin_name in ("single-instance", "notification", "dialog"):
        assert plugin_name in plugins, (
            f"tauri.conf.json:plugins must still declare '{plugin_name}' (the gate tests assert key presence)"
        )
        assert plugins[plugin_name] is None, (
            f"tauri.conf.json:plugins.'{plugin_name}' must be null (serde unit), "
            f"got {plugins[plugin_name]!r}; any non-null value fails app startup "
            "with 'invalid type: map, expected unit'"
        )


@pytest.mark.parametrize("plugin_crate", EXPECTED_MAIN_RS_PLUGINS)
def test_main_rs_registers_required_plugin(main_rs_source, plugin_crate) -> None:
    """ADR-0020 §6 + §6.2 + §12 + MIG-1.1: main.rs must register every required plugin."""
    pattern = re.compile(
        r"\.plugin\s*\(\s*" + re.escape(plugin_crate) + r"::init",
        re.MULTILINE,
    )
    assert pattern.search(main_rs_source), (
        f"main.rs must register {plugin_crate} via "
        f"`.plugin({plugin_crate}::init(...))` (ADR-0020 §6 + §12 + MIG-1.1). "
        f"Not found in main.rs source."
    )


def test_main_rs_single_instance_is_first_plugin(main_rs_source) -> None:
    """ADR-0020 §12: ``tauri_plugin_single_instance`` MUST be the FIRST ``.plugin()`` call."""
    # Find all `.plugin(tauri_plugin_*::init` calls in order.
    plugin_calls = re.findall(
        r"\.plugin\s*\(\s*(tauri_plugin_\w+)::init",
        main_rs_source,
    )
    assert plugin_calls, "main.rs must register at least one .plugin(...) call (ADR-0020 §6 + §12)"
    assert plugin_calls[0] == "tauri_plugin_single_instance", (
        f"main.rs: tauri_plugin_single_instance MUST be the first .plugin() "
        f"call (ADR-0020 §12, runs before any sidecar spawn); got "
        f"{plugin_calls[0]!r} as first. Full order: {plugin_calls}"
    )


@pytest.mark.parametrize("command", EXPECTED_MAIN_RS_COMMANDS)
def test_main_rs_registers_required_command(main_rs_source, command) -> None:
    """ADR-0020 §6.2 + §7 + §9 + §10 + MIG-1.1 + MIG-1.2: main.rs must register every required command."""
    # The command name must appear inside the generate_handler![...]
    handler_match = re.search(
        r"generate_handler!\s*\[(?P<body>[^\]]*)\]",
        main_rs_source,
        re.DOTALL,
    )
    assert handler_match, (
        "main.rs must call tauri::generate_handler![...] to register IPC commands (ADR-0020 §6.2 + §7)"
    )
    handler_body = handler_match.group("body")
    # Match the command as a standalone identifier (bounded by
    ident_pattern = re.compile(
        r"(?:^|[\s,])" + re.escape(command) + r"(?:$|[\s,])",
        re.MULTILINE,
    )
    assert ident_pattern.search(handler_body), (
        f"main.rs: tauri::generate_handler![...] must register {command!r} "
        f"(ADR-0020 §6.2 + §7 + §9 + §10 + MIG-1.1 + MIG-1.2). The macro "
        f"body was:\n{handler_body}"
    )


def test_main_rs_does_not_register_paste_text(main_rs_source) -> None:
    """FZ-19 / PVT-051: main.rs must NOT register the dead ``paste_text`` command."""
    assert "paste_text" not in main_rs_source, (
        "main.rs must NOT reference `paste_text`, the dead Tauri command "
        "was deleted in FZ-19 / PVT-051 (Python sidecar owns the paste path). "
        "Remove any registration, import, or comment that mentions `paste_text`."
    )


def test_main_rs_does_not_register_unknown_commands(main_rs_source) -> None:
    """ADR-0020 §16: main.rs must NOT register commands outside the frozen contract."""
    handler_match = re.search(
        r"generate_handler!\s*\[(?P<body>[^\]]*)\]",
        main_rs_source,
        re.DOTALL,
    )
    assert handler_match, "main.rs must call tauri::generate_handler![...] (ADR-0020 §6.2 + §7)"
    handler_body = handler_match.group("body")
    # Strip `//` line comments before tokenizing, the macro body carries
    handler_body = re.sub(r"//[^\n]*", "", handler_body)
    candidates = re.findall(r"\b([a-z][a-z0-9_]*)\b", handler_body)
    # Filter out Rust keywords + the expected commands.
    rust_keywords = {
        "use",
        "crate",
        "self",
        "super",
        "as",
        "fn",
        "let",
        "mut",
        "ref",
        "move",
        "async",
        "await",
        "pub",
        "private",
    }
    expected = set(EXPECTED_MAIN_RS_COMMANDS) | rust_keywords
    unknown = sorted({c for c in candidates if c not in expected})
    assert not unknown, (
        "main.rs: generate_handler! list contains identifiers not in the "
        "frozen v1 command contract (ADR-0020 §16). New commands must go "
        f"through the §16 process. Unknown identifiers: {unknown}"
    )


def test_main_rs_is_wiring_only_under_line_ceiling(main_rs_source) -> None:
    """ADR-0020 module layout: main.rs is wiring-only (≤ 300 lines)."""
    # Count non-blank, non-comment lines. Rust comments: `//` line,
    lines = main_rs_source.splitlines()
    code_lines = []
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
                after = stripped.split("*/", 1)[1].strip()
                if after and not after.startswith("//"):
                    code_lines.append(after)
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if stripped.startswith("//"):
            continue
        if not stripped:
            continue
        code_lines.append(line)

    assert len(code_lines) <= MAIN_RS_WIRING_ONLY_LINE_CEILING, (
        f"main.rs must be wiring-only (≤ {MAIN_RS_WIRING_ONLY_LINE_CEILING} "
        f"non-blank, non-comment lines, per ADR-0020 module layout). "
        f"Current: {len(code_lines)} lines. Move business logic to a "
        f"focused module under sidecar/ / commands/ / platform/ / util."
    )


def test_main_rs_has_no_business_logic_patterns(main_rs_source) -> None:
    """ADR-0020 module layout: main.rs must NOT contain business logic."""
    # enigo / WebSocket / serde_json frame construction must NOT appear
    forbidden_in_main = [
        r"\benigo::",
        r"\btungstenite::",
        r"\bWebSocketStream\b",
        r"\bserde_json::from_str\b",
        r"\bserde_json::to_string\b",
        r"\bfaster_whisper\b",
        r"\bWhisperModel\b",
        r"\bctranslate2\b",
    ]
    for pattern in forbidden_in_main:
        assert not re.search(pattern, main_rs_source), (
            f"main.rs must NOT contain {pattern!r}, that's business logic "
            f"that belongs in a focused module (sidecar/ / commands/ / "
            f"platform/), per ADR-0020 module layout. The host entrypoint "
            f"is wiring-only."
        )
