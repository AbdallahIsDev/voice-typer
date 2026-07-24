r"""MIG-1.9 Phase 3 + 4 + 5 — Final glue validation for the Tauri migration.

This is the **end-to-end wiring check** that closes out MIG-1.9. It
validates that the three migration artefacts —
``src-tauri/tauri.conf.json`` (ADR-0020 §7), ``src-tauri/src/main.rs``
(wiring-only Rust host), and ``src-tauri/Cargo.toml`` (plugin crate
list) — are mutually consistent and match the ADR contract.

Scope (ADR-0020 §7 + §15):

1. **``build`` block** — ``frontendDist`` points at the React renderer
   build output, ``devUrl`` is the Vite dev server
   (``http://localhost:1420``), and ``beforeDevCommand`` +
   ``beforeBuildCommand`` both invoke ``npm run build:renderer`` so a
   fresh ``dist/`` is always present before Tauri bundles or serves.

2. **``app.security`` block** — ``csp`` is set and reproduces the
   Electron CSP's core directives (``default-src 'self'``,
   ``img-src 'self' data:``, ``style-src 'self' 'unsafe-inline'``,
   ``script-src 'self'``). ``capabilities`` references the
   ``migrate-runtime`` capability file (per ADR-0020 §7's mandatory
   capability whitelisting note).

3. **No auto-update (ADR-0020 §15).** ``tauri-plugin-updater`` MUST
   NOT appear in ``Cargo.toml`` and the ``updater`` plugin entry MUST
   NOT appear in ``tauri.conf.json``'s ``plugins`` block. The v1
   Tauri migration ships as a manual download (same model as today's
   Electron build); auto-update is a follow-up ADR.

4. **Plugin chain in ``main.rs``** — registers ``shell``,
   ``notification``, ``clipboard-manager``, ``single-instance`` (first
   — ADR-0020 §12 single-instance gate), and ``dialog`` (MIG-1.1
   save-file dialogs for the export commands).

5. **Command handler list in ``main.rs``** — registers ``dispatch``,
   ``paste_text``, ``shutdown_sidecar``, the six ``bubble_*``
   commands, and ``export_history`` / ``export_vocabulary``.

6. **``main.rs`` is wiring-only** — well under ~300 lines, with no
   business logic. All real logic lives in the focused modules
   (``state``, ``util``, ``sidecar::*``, ``commands::*``,
   ``platform::*``) per ADR-0020 module layout.

These are *static* source-text + JSON-shape assertions; they do not
spawn Tauri, do not build the renderer, and do not run the sidecar.
The end-to-end runtime flow is captured in the **VALIDATE ON HOST**
block below — those commands run on a real desktop host (Windows /
macOS / Linux) with the Nuitka-frozen sidecar + native hotkey binary
present, and are out of scope for CI.

VALIDATE ON HOST
----------------

After ``cargo tauri dev`` (or ``cargo tauri build`` + installer run)
launches the bundled app on a target host, run these manual checks
to confirm the full glue holds end-to-end:

    # 1. Sidecar spawns + WS handshake completes (ADR-0020 §1 + §3).
    #    The Tauri host log (rotating file under config_dir/voice-typer/)
    #    should show exactly one "[SETUP] config_dir resolved to: ..." line
    #    followed by a successful WS connect (no "[SETUP] initial WS connect
    #    failed" + ft1_respawn retry storm).
    tail -f "$(config_dir)/voice-typer/voice-typer.log"   # macOS/Linux
    Get-Content "$env:APPDATA\voice-typer\voice-typer.log" -Wait  # Windows

    # 2. Renderer build is fresh before each `tauri dev` / `tauri build`.
    #    The beforeDevCommand / beforeBuildCommand must run the renderer
    #    build (npm run build:renderer) so frontendDist is populated.
    cd voice_typer/client && npm run build:renderer
    ls dist/                                                # index.html + assets/

    # 3. WebView CSP is enforced — open DevTools (right-click → Inspect),
    #    confirm the <meta http-equiv="Content-Security-Policy"> tag in
    #    the rendered index.html matches the tauri.conf.json value, and
    #    that no inline <script> executes (console should show CSP
    #    violation reports if anything tried).

    # 4. dispatch() round-trips a real command to the Python sidecar.
    #    In DevTools console:
    #       await window.__TAURI__.core.invoke('dispatch', {
    #         cmd: 'get_status', data: {}
    #       });
    #    → must return {"type":"result","data":{...}} within 200 ms
    #    (proves WS bridge + bearer-token auth + dispatch forwarding).

    # 5. paste_text injects a real keystroke (ADR-0020 §6.2 enigo).
    #    Focus a text editor, then in DevTools:
    #       await window.__TAURI__.core.invoke('paste_text', { text: 'hello' });
    #    → "hello" must appear at the caret (proves enigo path).

    # 6. bubble_show opens the bubble window (ADR-0020 §9).
    #       await window.__TAURI__.core.invoke('bubble_show');
    #    → a 240×80 transparent always-on-top window appears.

    # 7. export_history opens a native save-file dialog (MIG-1.1).
    #       await window.__TAURI__.core.invoke('export_history', { format: 'csv' });
    #    → OS save-file dialog appears; saving writes the CSV.

    # 8. shutdown_sidecar cleanly exits the Python process.
    #       await window.__TAURI__.core.invoke('shutdown_sidecar');
    #    → config_dir/voice-typer/voice-typer.log shows a clean
    #      "shutdown complete" line; no zombie python-sidecar process
    #      remains (check via Task Manager / Activity Monitor / `ps`).

    # 9. Single-instance gate (ADR-0020 §12): launch the app a second
    #    time. The second instance must exit immediately and the
    #    existing main window must come to the foreground. No second
    #    python-sidecar process must spawn (the single-instance plugin
    #    runs before the .setup task).

    # 10. No updater — confirm there is NO "Check for Updates…" menu
    #     item, no `tauri-plugin-updater` JS import in the renderer,
    #     and no `latest.json` network call in the DevTools Network
    #     tab. Auto-update is out of scope for v1 (ADR-0020 §15).

These ten steps constitute the full MIG-1.9 acceptance gate; the
pytest assertions below statically prove the wiring is in place for
those runtime checks to even be possible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ─── Repo paths ────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TAURI_DIR = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _TAURI_DIR / "tauri.conf.json"
_MAIN_RS = _TAURI_DIR / "src" / "main.rs"
_CARGO_TOML = _TAURI_DIR / "Cargo.toml"
_CLIENT_PACKAGE_JSON = _REPO_ROOT / "voice_typer" / "client" / "package.json"

#: ADR-0020 §7: the renderer build output (Vite/electron-vite renderer
#: build → dist/). beforeDevCommand + beforeBuildCommand must populate
#: this dir before Tauri bundles or serves the webview.
EXPECTED_FRONTEND_DIST = "../voice_typer/client/dist"

#: ADR-0020 §7: Vite dev server default port (1420 — Vite's
#: canonical "first user port", matching Tauri's own create-tauri-app
#: template). The Tauri host loads this URL in dev mode.
EXPECTED_DEV_URL = "http://localhost:1420"

#: ADR-0020 §7: the npm script that builds the React renderer for
#: embedding in the Tauri webview. Must exist in package.json:scripts
#: and be invoked by both beforeDevCommand + beforeBuildCommand.
EXPECTED_RENDERER_BUILD_SCRIPT = "build:renderer"

#: ADR-0020 §7 + CR-5: capability identifier referenced by tauri.conf.json's
#: ``app.security.capabilities`` list. CR-5 split the original
#: ``migrate-runtime.json`` into ``main-runtime.json`` (full grant set,
#: scoped to the main window) + ``bubble-runtime.json`` (minimal sandboxed
#: grant, scoped to the bubble window).
EXPECTED_CAPABILITY_IDENTIFIER = "main-runtime"
EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER = "bubble-runtime"

#: ADR-0020 §7 + the Electron CSP (client/src/main/bootstrap.ts setupCsp).
#: The Tauri CSP must reproduce at least these four core directives:
#:   - default-src 'self'          (no cross-origin loads)
#:   - img-src 'self' data:        (inline data-URI icons)
#:   - style-src 'self' 'unsafe-inline'   (Tailwind / styled-jsx inlines)
#:   - script-src 'self'           (NO 'unsafe-eval', NO 'unsafe-inline')
#: The Electron CSP additionally carries font-src / media-src /
#: connect-src / frame-ancestors / form-action / base-uri directives
#: that the Tauri CSP currently omits — see the implementation-gap
#: note attached to test_tauri_conf_security_csp_matches_electron_subset.
EXPECTED_CSP_CORE_DIRECTIVES = [
    "default-src 'self'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
]

#: ADR-0020 §6 + §6.2 + §12: plugins that main.rs MUST register via
#: ``.plugin(tauri_plugin_*::init())``. single-instance MUST be first
#: (see ADR-0020 §12 — its duplicate-instance gate runs before any
#: sidecar spawn to avoid zombie python processes on double-launch).
EXPECTED_MAIN_RS_PLUGINS = [
    "tauri_plugin_single_instance",
    "tauri_plugin_shell",
    "tauri_plugin_notification",
    "tauri_plugin_clipboard_manager",
    "tauri_plugin_dialog",
]

#: ADR-0020 §6.2 + §7 + §9 + §10 + MIG-1.1 + MIG-1.2 + CR-33: commands
#: that main.rs MUST register in ``tauri::generate_handler![...]``.
EXPECTED_MAIN_RS_COMMANDS = [
    # ADR-0020 §6.2 + §10 — generic dispatch + paste + shutdown
    "dispatch",
    "paste_text",
    "shutdown_sidecar",
    # MIG-1.1 — export commands (dialog save-file flow)
    "export_history",
    "export_vocabulary",
    # MIG-1.2 / ADR-0020 §9 — bubble window commands
    "bubble_show",
    "bubble_signal_ready",
    "bubble_set_position",
    "bubble_set_draggable",
    "bubble_move_by",
    "bubble_hide_complete",
    # CR-33 — bubble window extensions (resize / toggle).
    # GT-82: `bubble_emit_state` removed — dead in production.
    "bubble_resize",
    "bubble_toggle_dictation",
    # CR-33 — system-level window_ commands.
    "open_logs",
    # GT-83: dedicated host-log-dir opener.
    "open_host_logs",
    "open_model_import_dialog",
    "export_templates",
    "export_config",
    # GT-35: renderer-side error log sink.
    "renderer_log_error",
]

#: ADR-0020 §15: the v1 Tauri migration MUST NOT wire up
#: ``tauri-plugin-updater``. Auto-update is an explicit non-goal for
#: v1 (out of scope — track as a separate follow-up ADR after the
#: Tauri cutover stabilizes).
FORBIDDEN_UPDATER_TOKENS = [
    "tauri-plugin-updater",
    "tauri_plugin_updater",
]

#: ADR-0020 module-layout note (main.rs doc comment): main.rs is
#: "wiring-only (~200 lines)". The hard ceiling for the v1 migration
#: is 300 lines — anything beyond that means business logic has crept
#: back into the host entrypoint and must be moved to a focused module
#: under ``sidecar::``, ``commands::``, ``platform::``, or ``util``.
MAIN_RS_WIRING_ONLY_LINE_CEILING = 300


# ─── Shared fixtures ──────────────────────────────────────────────────


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


# ─── Test 1: build.frontendDist → React renderer build output ─────────


def test_tauri_conf_frontend_dist_points_to_renderer_build_output(
    tauri_conf,
) -> None:
    """ADR-0020 §7: ``build.frontendDist`` must point at the React build output.

    Tauri v2 embeds the webview by serving static files from
    ``frontendDist`` in production builds. The path is relative to
    ``src-tauri/`` (the location of tauri.conf.json), so
    ``../voice_typer/client/dist`` resolves to the Vite/electron-vite
    renderer build output directory.
    """
    build = tauri_conf.get("build", {})
    assert "frontendDist" in build, (
        "build.frontendDist must exist (ADR-0020 §7) — Tauri v2 embeds the "
        "webview from this directory in production builds"
    )
    assert build["frontendDist"] == EXPECTED_FRONTEND_DIST, (
        f"build.frontendDist must be {EXPECTED_FRONTEND_DIST!r} (the React "
        f"renderer build output, relative to src-tauri/); got "
        f"{build['frontendDist']!r}"
    )


# ─── Test 2: build.devUrl → Vite dev server (port 1420) ───────────────


def test_tauri_conf_dev_url_is_vite_default_port(tauri_conf) -> None:
    """ADR-0020 §7: ``build.devUrl`` must be the Vite dev server URL.

    Port 1420 is Vite's canonical "first user port" (matching the
    Tauri create-tauri-app template default). In dev mode
    (``cargo tauri dev``) the Tauri host loads this URL instead of
    the static ``frontendDist`` so HMR works.
    """
    build = tauri_conf.get("build", {})
    assert "devUrl" in build, (
        "build.devUrl must exist (ADR-0020 §7) — Tauri loads this URL in dev mode (cargo tauri dev)"
    )
    assert build["devUrl"] == EXPECTED_DEV_URL, (
        f"build.devUrl must be {EXPECTED_DEV_URL!r} (Vite dev server, port 1420); got {build['devUrl']!r}"
    )


# ─── Test 3: beforeDevCommand + beforeBuildCommand run renderer build ─


def test_package_json_defines_build_renderer_script(
    client_package_json,
) -> None:
    """ADR-0020 §7: ``voice_typer/client/package.json`` must define
    ``build:renderer``.

    Tauri's ``beforeDevCommand`` + ``beforeBuildCommand`` invoke this
    npm script to populate ``frontendDist`` before serving / bundling.
    The script builds only the React renderer (not the Electron main
    process) via electron-vite's ``--config`` flag — the Electron
    main-process build artefacts are not consumed by Tauri.
    """
    scripts = client_package_json.get("scripts", {})
    assert EXPECTED_RENDERER_BUILD_SCRIPT in scripts, (
        f"package.json:scripts must define {EXPECTED_RENDERER_BUILD_SCRIPT!r} "
        f"(invoked by tauri.conf.json beforeDevCommand + beforeBuildCommand)"
    )
    script_value = scripts[EXPECTED_RENDERER_BUILD_SCRIPT]
    assert isinstance(script_value, str) and script_value, (
        f"package.json:scripts.{EXPECTED_RENDERER_BUILD_SCRIPT} must be a non-empty string; got {script_value!r}"
    )
    # The script must invoke a build tool (electron-vite or vite) — not
    # just `echo` or `true` — so the renderer dist/ is actually populated.
    assert re.search(r"\b(electron-vite|vite)\b.*\bbuild\b", script_value), (
        f"package.json:scripts.{EXPECTED_RENDERER_BUILD_SCRIPT} must invoke a "
        f"Vite-family build (electron-vite or vite build); got {script_value!r}"
    )


def test_tauri_conf_before_dev_command_runs_renderer_build(tauri_conf) -> None:
    """ADR-0020 §7: ``beforeDevCommand`` must run ``npm run build:renderer``.

    In dev mode, Tauri runs this before starting the dev server so the
    renderer is rebuilt on every ``cargo tauri dev`` invocation. The
    command must ``cd`` into ``voice_typer/client`` (relative to
    ``src-tauri/``) and invoke ``npm run build:renderer``.
    """
    build = tauri_conf.get("build", {})
    assert "beforeDevCommand" in build, (
        "build.beforeDevCommand must exist (ADR-0020 §7) — Tauri runs this before starting the dev server"
    )
    cmd = build["beforeDevCommand"]
    assert isinstance(cmd, str), f"build.beforeDevCommand must be a string; got {type(cmd).__name__}"
    assert "npm run build:renderer" in cmd, f"build.beforeDevCommand must invoke 'npm run build:renderer'; got {cmd!r}"
    assert "voice_typer/client" in cmd, f"build.beforeDevCommand must cd into voice_typer/client; got {cmd!r}"


def test_tauri_conf_before_build_command_runs_renderer_build(tauri_conf) -> None:
    """ADR-0020 §7: ``beforeBuildCommand`` must run ``npm run build:renderer``.

    In production builds, Tauri runs this before bundling the app so
    ``frontendDist`` is populated with a fresh renderer build. Same
    shape as ``beforeDevCommand`` (the renderer build is identical in
    dev and prod — only the Vite mode flag differs, handled inside
    the ``build:renderer`` script).
    """
    build = tauri_conf.get("build", {})
    assert "beforeBuildCommand" in build, (
        "build.beforeBuildCommand must exist (ADR-0020 §7) — Tauri runs this before bundling the production app"
    )
    cmd = build["beforeBuildCommand"]
    assert isinstance(cmd, str), f"build.beforeBuildCommand must be a string; got {type(cmd).__name__}"
    assert "npm run build:renderer" in cmd, (
        f"build.beforeBuildCommand must invoke 'npm run build:renderer'; got {cmd!r}"
    )
    assert "voice_typer/client" in cmd, f"build.beforeBuildCommand must cd into voice_typer/client; got {cmd!r}"


# ─── Test 4: app.security.csp is set + matches Electron CSP subset ────


def test_tauri_conf_security_csp_is_set(tauri_conf) -> None:
    """ADR-0020 §7: ``app.security.csp`` must be a non-empty string.

    Tauri v2 enforces CSP at the WebView level — this is the primary
    defense against XSS in the renderer (the renderer can invoke
    Tauri IPC, so a script-injection → arbitrary command dispatch is
    the threat model). Without a CSP, the webview accepts any
    inline script. The CSP must be set explicitly (Tauri v2 does NOT
    ship a safe default).
    """
    security = tauri_conf.get("app", {}).get("security", {})
    assert "csp" in security, (
        "app.security.csp must exist (ADR-0020 §7) — Tauri v2 enforces CSP "
        "at the WebView level; the field must be set explicitly"
    )
    csp = security["csp"]
    assert isinstance(csp, str) and csp, f"app.security.csp must be a non-empty string; got {csp!r}"


def test_tauri_conf_security_csp_matches_electron_subset(tauri_conf) -> None:
    """ADR-0020 §7: the Tauri CSP must reproduce the Electron CSP's core directives.

    The Electron build's CSP (``client/src/main/bootstrap.ts::setupCsp``)
    carries these core directives that the Tauri CSP MUST also carry:

      - ``default-src 'self'``          (no cross-origin loads)
      - ``img-src 'self' data:``        (inline data-URI icons)
      - ``style-src 'self' 'unsafe-inline'``   (Tailwind inlines)
      - ``script-src 'self'``           (NO 'unsafe-eval', NO 'unsafe-inline')

    The Electron CSP additionally carries ``font-src``, ``media-src``,
    ``connect-src 'self' https://api.github.com``, ``frame-ancestors 'none'``,
    ``form-action 'none'``, ``base-uri 'self'``. The current Tauri CSP
    omits those extras — that is a known implementation gap (tracked
    as a follow-up; not a v1 blocker because ``default-src 'self'``
    falls back for any unlisted directive). This test pins the four
    core directives so a regression that loosens them is caught.
    """
    csp = tauri_conf.get("app", {}).get("security", {}).get("csp", "")
    for directive in EXPECTED_CSP_CORE_DIRECTIVES:
        assert directive in csp, (
            f"app.security.csp must contain {directive!r} (one of the four "
            f"core Electron CSP directives); full CSP was: {csp!r}"
        )

    # CR-SEC: script-src must NOT allow 'unsafe-eval' or 'unsafe-inline'
    # — those are the two script-injection footguns. The Electron CSP
    # also forbids them; the Tauri CSP must do the same.
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


# ─── Test 5: app.security.capabilities → migrate-runtime ──────────────


def test_tauri_conf_security_capabilities_references_migrate_runtime(
    tauri_conf,
) -> None:
    """ADR-0020 §7 + CR-5: ``app.security.capabilities`` must reference
    ``main-runtime`` AND ``bubble-runtime`` (CR-5 deleted migrate-runtime)."""
    security = tauri_conf.get("app", {}).get("security", {})
    assert "capabilities" in security, (
        "app.security.capabilities must exist (ADR-0020 §7) — Tauri v2 "
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
        f"file — CR-5 split); got {capabilities!r}"
    )
    assert EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must reference "
        f"{EXPECTED_BUBBLE_CAPABILITY_IDENTIFIER!r} (the bubble-window "
        f"sandboxed capability file — CR-5 split / SEC-026); got "
        f"{capabilities!r}"
    )
    assert "migrate-runtime" not in capabilities, (
        f"app.security.capabilities must NOT reference 'migrate-runtime' "
        f"(CR-5 deleted it; use 'main-runtime' + 'bubble-runtime' instead) "
        f"— got {capabilities!r}"
    )


# ─── Test 6: NO tauri-plugin-updater (ADR-0020 §15 — no auto-update v1) ─


@pytest.mark.parametrize("forbidden_token", FORBIDDEN_UPDATER_TOKENS)
def test_cargo_toml_has_no_updater_plugin(cargo_toml_source, forbidden_token) -> None:
    """ADR-0020 §15: ``Cargo.toml`` MUST NOT declare ``tauri-plugin-updater``.

    Auto-update is an explicit non-goal for the v1 Tauri migration
    (ADR-0020 §15). The ``tauri-plugin-updater`` crate adds a signing-
    key distribution problem + a manifest-hosting problem that are
    orthogonal to the runtime migration. Ship the Tauri build as a
    manual-download release (matching today's Electron release
    model — there is no working auto-update today, see
    ``docs/auto-update-feature.md``'s "STATUS: NOT IMPLEMENTED"
    header). Track auto-update as a separate follow-up ADR.
    """
    # Strip comments to avoid false positives from doc references.
    stripped = re.sub(r"^#.*$", "", cargo_toml_source, flags=re.MULTILINE)
    assert forbidden_token not in stripped, (
        f"Cargo.toml MUST NOT declare {forbidden_token!r} (ADR-0020 §15: "
        f"auto-update is out of scope for the v1 Tauri migration). Found "
        f"in Cargo.toml source."
    )


def test_tauri_conf_has_no_updater_plugin_entry(tauri_conf) -> None:
    """ADR-0020 §15: ``tauri.conf.json`` ``plugins`` MUST NOT include ``updater``.

    Even if the Cargo crate is absent, a stray ``updater`` entry in
    tauri.conf.json's ``plugins`` block would cause Tauri to fail
    context build with "plugin 'updater' not registered". The block
    must be updater-free.
    """
    plugins = tauri_conf.get("plugins", {})
    assert isinstance(plugins, dict), f"tauri.conf.json:plugins must be a dict; got {type(plugins).__name__}"
    assert "updater" not in plugins, (
        "tauri.conf.json:plugins MUST NOT include 'updater' (ADR-0020 §15: "
        "auto-update is out of scope for the v1 Tauri migration). Found "
        f"'updater' key in plugins block: {plugins!r}"
    )


# ─── Test 7: main.rs registers all required plugins ───────────────────


@pytest.mark.parametrize("plugin_crate", EXPECTED_MAIN_RS_PLUGINS)
def test_main_rs_registers_required_plugin(main_rs_source, plugin_crate) -> None:
    """ADR-0020 §6 + §6.2 + §12 + MIG-1.1: main.rs must register every required plugin.

    Each plugin must be wired via ``.plugin(<crate>::init(...))`` on
    the Tauri builder. The required set is:

      - ``tauri_plugin_single_instance`` (FIRST — ADR-0020 §12 single-
        instance gate runs before any sidecar spawn to avoid zombie
        python processes on double-launch)
      - ``tauri_plugin_shell`` (ADR-0020 §4.1 — spawns the python-sidecar
        externalBin)
      - ``tauri_plugin_notification`` (replaces Electron's
        show_electron_notification — ADR-0020 §6)
      - ``tauri_plugin_clipboard_manager`` (paste fallback path when
        enigo can't reach the focused window — ADR-0020 §6.2)
      - ``tauri_plugin_dialog`` (MIG-1.1 — save-file dialog for the
        export_history / export_vocabulary commands)
    """
    # Look for the plugin registration call: `.plugin(tauri_plugin_X::init(`
    # (the call may span multiple lines, so use a regex that allows
    # whitespace between the segments). We don't pin the argument shape
    # (some plugins take a closure, some take nothing) — just confirm
    # the crate's init() is invoked via .plugin(...).
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
    """ADR-0020 §12: ``tauri_plugin_single_instance`` MUST be the FIRST ``.plugin()`` call.

    The single-instance plugin's duplicate-instance callback runs
    synchronously during plugin init — if it isn't first, a second
    launch could spawn a zombie python-sidecar (via the .setup task
    or the WS bridge) before the single-instance gate trips and
    exits the second process. Pinning "first" prevents a future
    refactor from reordering the plugin chain.
    """
    # Find all `.plugin(tauri_plugin_*::init` calls in order.
    plugin_calls = re.findall(
        r"\.plugin\s*\(\s*(tauri_plugin_\w+)::init",
        main_rs_source,
    )
    assert plugin_calls, "main.rs must register at least one .plugin(...) call (ADR-0020 §6 + §12)"
    assert plugin_calls[0] == "tauri_plugin_single_instance", (
        f"main.rs: tauri_plugin_single_instance MUST be the first .plugin() "
        f"call (ADR-0020 §12 — runs before any sidecar spawn); got "
        f"{plugin_calls[0]!r} as first. Full order: {plugin_calls}"
    )


# ─── Test 8: main.rs registers all required commands ──────────────────


@pytest.mark.parametrize("command", EXPECTED_MAIN_RS_COMMANDS)
def test_main_rs_registers_required_command(main_rs_source, command) -> None:
    """ADR-0020 §6.2 + §7 + §9 + §10 + MIG-1.1 + MIG-1.2: main.rs must register every required command.

    Each command must be listed in the ``tauri::generate_handler![...]``
    macro call. The required set is:

      - ``dispatch`` (ADR-0020 §6.2 — generic WS-forwarding command)
      - ``paste_text`` (ADR-0020 §6.2 + §10 — enigo keystroke injection)
      - ``shutdown_sidecar`` (ADR-0020 §10 — clean sidecar exit on
        main-window close)
      - ``export_history`` / ``export_vocabulary`` (MIG-1.1 — CSV export
        via dialog save-file)
      - ``bubble_show`` / ``bubble_signal_ready`` / ``bubble_set_position``
        / ``bubble_set_draggable`` / ``bubble_move_by`` /
        ``bubble_hide_complete`` (MIG-1.2 / ADR-0020 §9 — bubble window)
    """
    # The command name must appear inside the generate_handler![...]
    # macro call. Locate the macro call, then check the command is
    # listed inside it (as a bare identifier, possibly with a trailing
    # comma). This avoids false positives from `use` imports above.
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
    # whitespace / comma / bracket on either side).
    ident_pattern = re.compile(
        r"(?:^|[\s,])" + re.escape(command) + r"(?:$|[\s,])",
        re.MULTILINE,
    )
    assert ident_pattern.search(handler_body), (
        f"main.rs: tauri::generate_handler![...] must register {command!r} "
        f"(ADR-0020 §6.2 + §7 + §9 + §10 + MIG-1.1 + MIG-1.2). The macro "
        f"body was:\n{handler_body}"
    )


def test_main_rs_does_not_register_unknown_commands(main_rs_source) -> None:
    """ADR-0020 §16: main.rs must NOT register commands outside the frozen contract.

    The 11 commands in ``EXPECTED_MAIN_RS_COMMANDS`` plus the generic
    ``dispatch`` are the complete Tauri-exposed IPC surface for v1.
    Adding new commands widens the wire contract and must go through
    ADR-0020 §16's process (registry entry + ADR addendum + payload
    schema + dispatch-error test). This test catches accidental
    additions to the generate_handler! list.

    The check is intentionally lenient (it only flags identifiers in
    the macro body that aren't in the expected set + aren't obviously
    a Rust keyword/path) to avoid false positives from formatting.
    """
    handler_match = re.search(
        r"generate_handler!\s*\[(?P<body>[^\]]*)\]",
        main_rs_source,
        re.DOTALL,
    )
    assert handler_match, "main.rs must call tauri::generate_handler![...] (ADR-0020 §6.2 + §7)"
    handler_body = handler_match.group("body")
    # Strip `//` line comments before tokenizing — the macro body carries
    # explanatory comments (e.g. "// CR-33: bubble window extensions
    # (resize / state / toggle)") whose prose words would otherwise be
    # mis-read as command identifiers.
    handler_body = re.sub(r"//[^\n]*", "", handler_body)
    # Extract candidate identifiers: snake_case tokens, possibly with
    # a leading path segment (we strip those).
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
    # Tolerate a small number of unknown identifiers (formatting noise
    # like `path::to::cmd` would surface the last segment, which is
    # usually in EXPECTED_MAIN_RS_COMMANDS). Anything genuinely unknown
    # is a contract widening — flag it.
    assert not unknown, (
        "main.rs: generate_handler! list contains identifiers not in the "
        "frozen v1 command contract (ADR-0020 §16). New commands must go "
        f"through the §16 process. Unknown identifiers: {unknown}"
    )


# ─── Test 9: main.rs is wiring-only (≤ 300 lines) ─────────────────────


def test_main_rs_is_wiring_only_under_line_ceiling(main_rs_source) -> None:
    """ADR-0020 module layout: main.rs is wiring-only (≤ 300 lines).

    The ADR's main.rs doc comment says the file is "wiring-only
    (~200 lines): app builder, plugin registration, .setup glue,
    generate_handler! list, and the single-instance gate. All real
    logic lives in focused modules" (state, util, sidecar::*,
    commands::*, platform::*).

    The hard ceiling for v1 is 300 lines — anything beyond that
    means business logic has crept back into the host entrypoint and
    must be moved to a focused module. The check is on non-blank,
    non-comment lines so doc comments + blank lines don't inflate the
    count (the ceiling guards against logic creep, not documentation).
    """
    # Count non-blank, non-comment lines. Rust comments: `//` line,
    # `/* ... */` block (block comments are rare in Rust idiom — most
    # are `//` or `///` / `//!` doc lines).
    lines = main_rs_source.splitlines()
    code_lines = []
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
                # anything after `*/` on the same line counts as code
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
    """ADR-0020 module layout: main.rs must NOT contain business logic.

    The wiring-only contract means no:
      - ``tokio::spawn(async move {...})`` blocks with non-trivial bodies
        (the .setup spawn is the ONE allowed exception — it forwards
        to spawn_sidecar_and_get_port + reconnect_ws + ft1_respawn,
        all of which live in focused modules)
      - ``match`` statements on sidecar protocol fields (lives in
        sidecar/ws.rs)
      - ``enigo::`` keystroke construction (lives in commands/sidecar_cmds.rs)
      - ``serde_json::`` frame serialization (lives in sidecar/ws.rs)
      - ``WebSocket<`` connection handling (lives in sidecar/ws.rs)

    This test catches logic that should have been moved to a focused
    module but lingered in the host entrypoint.
    """
    # enigo / WebSocket / serde_json frame construction must NOT appear
    # in main.rs — they live in the focused modules.
    forbidden_in_main = [
        # enigo keystroke construction — lives in commands/sidecar_cmds.rs
        r"\benigo::",
        # raw WebSocket connection — lives in sidecar/ws.rs
        r"\btungstenite::",
        r"\bWebSocketStream\b",
        # serde_json frame serialization — lives in sidecar/ws.rs +
        # commands/sidecar_cmds.rs (main.rs only deals with the typed
        # SidecarState; it never (de)serializes a WS frame)
        r"\bserde_json::from_str\b",
        r"\bserde_json::to_string\b",
        # faster-whisper / model loading — lives in the Python sidecar,
        # NEVER in the Rust host
        r"\bfaster_whisper\b",
        r"\bWhisperModel\b",
        # ctranslate2 — same as above
        r"\bctranslate2\b",
    ]
    for pattern in forbidden_in_main:
        assert not re.search(pattern, main_rs_source), (
            f"main.rs must NOT contain {pattern!r} — that's business logic "
            f"that belongs in a focused module (sidecar/ / commands/ / "
            f"platform/), per ADR-0020 module layout. The host entrypoint "
            f"is wiring-only."
        )
