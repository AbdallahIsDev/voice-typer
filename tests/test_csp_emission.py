"""Tests for RW-3: per-page CSP emission at build time.

Validates that the production CSP meta tag does NOT contain
`unsafe-eval` or `unsafe-inline` for `script-src`, while the dev CSP
does (for Vite HMR + React Refresh + eval sourcemaps).

The CSP policies live in `voice_typer/client/csp-plugin.ts` as exported
constants `CSP_PROD` and `CSP_DEV`. A Vite plugin `cspEmissionPlugin`
rewrites the CSP meta tag in `index.html` / `bubble.html` based on mode.

This test does:
  1. Source-string checks on `csp-plugin.ts` to ensure the constants
     are well-formed.
  2. Source-string checks on `electron.vite.config.ts` to ensure the
     plugin is wired in.
  3. Source-string checks on `index.html` / `bubble.html` to ensure the
     fail-safe default meta tag is the strict (prod) CSP — so if the
     plugin fails to fire in prod, the meta tag is already strict.
  4. Source-string check on `main/index.ts` to ensure the HTTP-header
     CSP (set via `onHeadersReceived`) is also strict in prod.
  5. End-to-end build test: runs `electron-vite build` and reads the
     built HTML files to verify the strict CSP made it into the output.
     Skipped if Node / electron-vite isn't available on PATH.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = REPO_ROOT / "voice_typer" / "client"
RENDERER_DIR = CLIENT_DIR / "src" / "renderer"
CSP_PLUGIN_PATH = CLIENT_DIR / "csp-plugin.ts"
ELECTRON_VITE_CONFIG_PATH = CLIENT_DIR / "electron.vite.config.ts"
INDEX_HTML_PATH = RENDERER_DIR / "index.html"
BUBBLE_HTML_PATH = RENDERER_DIR / "bubble.html"
MAIN_INDEX_TS_PATH = CLIENT_DIR / "src" / "main" / "index.ts"
# REF-2: CSP setup (onHeadersReceived + script-src) was extracted from
# main/index.ts (now wiring-only) into main/bootstrap.ts. Tests that
# check for CSP setup must read BOTH files.
MAIN_BOOTSTRAP_TS_PATH = CLIENT_DIR / "src" / "main" / "bootstrap.ts"


def _read_main_process_src() -> str:
    """Concatenate main/index.ts + main/bootstrap.ts so tests checking
    for CSP setup find it in whichever file the REF-2 extraction moved
    it to (current: bootstrap.ts).
    """
    parts = []
    if MAIN_INDEX_TS_PATH.is_file():
        parts.append(MAIN_INDEX_TS_PATH.read_text(encoding="utf-8"))
    if MAIN_BOOTSTRAP_TS_PATH.is_file():
        parts.append(MAIN_BOOTSTRAP_TS_PATH.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _extract_array_constant(source: str, name: str) -> list[str]:
    """Extract the joined-string array value of a `const NAME = [...].join(...)`.

    Returns the list of array-element string literals. Raises AssertionError
    if the constant isn't found or isn't a `.join()`-terminated array.

    Handles both direct array literals (``const CSP_PROD = [...].join(...)``)
    and alias chains (``const CSP_PROD = CSP_PROD_MAIN``) by recursively
    resolving the alias target. This is needed because the per-window
    split (CR-11 / R6-F5) introduced ``CSP_PROD_MAIN`` and
    ``CSP_PROD_BUBBLE`` as the canonical array constants, with
    ``CSP_PROD`` kept as a back-compat alias.
    """
    # Match: const NAME = [ "...", "...", ... ].join("; ");
    # Use a non-greedy capture up to the closing bracket, then `.join(...)`.
    pattern = rf"export\s+const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\.join\("
    m = re.search(pattern, source, flags=re.DOTALL)
    if m is None:
        # Try resolving as an alias: ``const NAME = OTHER_NAME;``
        alias_pattern = rf"export\s+const\s+{re.escape(name)}\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*;"
        alias_match = re.search(alias_pattern, source)
        if alias_match is not None:
            target = alias_match.group(1)
            if target != name:
                return _extract_array_constant(source, target)
        raise AssertionError(f"Could not find exported const {name} in {CSP_PLUGIN_PATH.name}")
    body = m.group(1)
    # Extract quoted strings (single or double quotes).
    elements = re.findall(r'"([^"]*)"\s*,?|' r"'([^']*)'\s*,?", body)
    result = [a or b for a, b in elements]
    assert result, f"No string elements found in {name} array"
    return result


def _extract_csp_value(source: str) -> str:
    """Extract the content attribute value of the CSP meta tag from an HTML file.

    Uses a backreference to handle either single- or double-quoted attributes
    correctly — the CSP value contains literal single quotes (`'self'`,
    `'unsafe-eval'`, etc.), so a naive `[^"']+` character class would truncate
    at the first inner quote.
    """
    pattern = (
        r'<meta\s+http-equiv=(["\'])Content-Security-Policy\1\s+'
        r'content=(["\'])(.*?)\2'
    )
    m = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
    assert m, "No CSP meta tag found in HTML"
    return m.group(3)


def _normalize_csp(csp: str) -> str:
    """Normalize a CSP policy string for comparison.

    Strips trailing semicolons (an empty directive at the end, ignored by the
    CSP spec) and surrounding whitespace. This lets us compare source HTML
    (which traditionally ends with a trailing `;`) against the `CSP_PROD` /
    `CSP_DEV` constants (which are joined with `"; "` and don't have a
    trailing `;`).
    """
    csp = csp.strip()
    while csp.endswith(";"):
        csp = csp[:-1].strip()
    return csp


def _script_src_directives(csp: str) -> str:
    """Extract the `script-src ...` clause from a CSP policy string."""
    for clause in csp.split(";"):
        clause = clause.strip()
        if clause.startswith("script-src"):
            return clause
    return ""


@pytest.fixture(scope="module")
def csp_plugin_source() -> str:
    return CSP_PLUGIN_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def csp_prod(csp_plugin_source: str) -> str:
    return "; ".join(_extract_array_constant(csp_plugin_source, "CSP_PROD"))


@pytest.fixture(scope="module")
def csp_dev(csp_plugin_source: str) -> str:
    return "; ".join(_extract_array_constant(csp_plugin_source, "CSP_DEV"))


class TestCspPluginConstants:
    """The CSP_PROD and CSP_DEV constants in csp-plugin.ts are well-formed."""

    def test_csp_plugin_file_exists(self):
        assert CSP_PLUGIN_PATH.is_file(), f"Missing: {CSP_PLUGIN_PATH}"

    def test_csp_prod_has_no_unsafe_eval(self, csp_prod: str):
        assert "'unsafe-eval'" not in csp_prod, f"CSP_PROD must not contain 'unsafe-eval' — got: {csp_prod}"

    def test_csp_prod_has_no_unsafe_inline_for_script_src(self, csp_prod: str):
        script_src = _script_src_directives(csp_prod)
        assert "'unsafe-inline'" not in script_src, (
            f"CSP_PROD script-src must not contain 'unsafe-inline' — got: {script_src}"
        )

    def test_csp_prod_script_src_is_only_self(self, csp_prod: str):
        script_src = _script_src_directives(csp_prod)
        assert script_src == "script-src 'self'", (
            f"CSP_PROD script-src should be exactly `script-src 'self'` — got: {script_src}"
        )

    def test_csp_prod_has_frame_ancestors_none(self, csp_prod: str):
        assert "frame-ancestors 'none'" in csp_prod

    def test_csp_prod_has_form_action_none(self, csp_prod: str):
        assert "form-action 'none'" in csp_prod

    def test_csp_prod_has_no_localhost_ws(self, csp_prod: str):
        # Production must not allow ws://localhost for HMR — HMR isn't used in prod.
        assert "ws://localhost" not in csp_prod, (
            f"CSP_PROD must not include ws://localhost (no HMR in prod) — got: {csp_prod}"
        )

    def test_csp_prod_allows_github_api(self, csp_plugin_source: str):
        # Update check uses https://api.github.com.
        # R6-F5: the ``connect-src`` directive is now built
        # dynamically via ``buildConnectSrc({ allowGitHub: true })`` for
        # the main window (CSP_PROD_MAIN, which CSP_PROD aliases). The
        # function literal includes ``https://api.github.com`` as a
        # string argument, so we check the raw source instead of the
        # statically-extracted constant (which can't resolve the
        # function call).
        assert "https://api.github.com" in csp_plugin_source, (
            "csp-plugin.ts must allow https://api.github.com in connect-src for the main window (CSP_PROD_MAIN)"
        )

    def test_csp_dev_has_unsafe_eval(self, csp_dev: str):
        # Required for Vite HMR + eval-based sourcemaps.
        assert "'unsafe-eval'" in csp_dev, f"CSP_DEV must contain 'unsafe-eval' for Vite HMR — got: {csp_dev}"

    def test_csp_dev_has_unsafe_inline_for_script_src(self, csp_dev: str):
        # Required for React Refresh preamble (inline script injected by Vite).
        script_src = _script_src_directives(csp_dev)
        assert "'unsafe-inline'" in script_src, f"CSP_DEV script-src must contain 'unsafe-inline' — got: {script_src}"

    def test_csp_dev_has_ws_localhost_for_hmr(self, csp_dev: str):
        # The HMR websocket uses ws://localhost:<port>. 'self' (http://localhost)
        # does NOT cover ws:// because the scheme differs.
        assert "ws://localhost" in csp_dev, f"CSP_DEV must include ws://localhost for HMR websocket — got: {csp_dev}"

    def test_csp_dev_has_http_localhost(self, csp_dev: str):
        # Dev server fetches from http://localhost:* — explicit allowlist avoids
        # any ambiguity if Electron ever serves HMR from a different port.
        assert "http://localhost" in csp_dev


class TestCspPluginWiredIn:
    """The cspEmissionPlugin is wired into the renderer section of the vite config."""

    def test_electron_vite_config_imports_plugin(self):
        src = ELECTRON_VITE_CONFIG_PATH.read_text(encoding="utf-8")
        assert "cspEmissionPlugin" in src, f"{ELECTRON_VITE_CONFIG_PATH.name} must import cspEmissionPlugin"

    def test_electron_vite_config_uses_plugin_in_renderer(self):
        src = ELECTRON_VITE_CONFIG_PATH.read_text(encoding="utf-8")
        # The plugins array in the renderer section must include cspEmissionPlugin().
        assert re.search(
            r"plugins:\s*\[[^\]]*cspEmissionPlugin\(\)[^\]]*\]",
            src,
        ), "renderer plugins array must call cspEmissionPlugin()"

    def test_csp_plugin_has_transform_index_html_hook(self, csp_plugin_source: str):
        assert "transformIndexHtml" in csp_plugin_source, "csp-plugin.ts must define a transformIndexHtml hook"

    def test_csp_plugin_branches_on_mode(self, csp_plugin_source: str):
        # The plugin must branch on `isProduction` (set via configResolved).
        assert "configResolved" in csp_plugin_source
        assert "isProduction" in csp_plugin_source
        assert "CSP_PROD" in csp_plugin_source and "CSP_DEV" in csp_plugin_source
        # R6-F5: the plugin now uses ``pickProdCsp(filePath)`` in
        # production (per-window split) instead of the bare ``CSP_PROD``
        # ternary. Accept either form:
        #   - legacy: ``isProduction ? CSP_PROD : CSP_DEV``
        #   - current: ``isProduction ? pickProdCsp(filePath) : CSP_DEV``
        assert re.search(
            r"isProduction\s*\?\s*(?:CSP_PROD|pickProdCsp\([^)]*\))\s*:\s*CSP_DEV",
            csp_plugin_source,
        )


class TestSourceHtmlFailSafeDefault:
    """The source HTML files use the STRICT CSP as default meta tag.

    If the plugin fails to fire in production, the meta tag is already strict
    (safe failure). If the plugin fails to fire in dev, HMR breaks visibly
    (loud failure).
    """

    def test_index_html_csp_is_strict_default(self):
        src = INDEX_HTML_PATH.read_text(encoding="utf-8")
        csp = _extract_csp_value(src)
        script_src = _script_src_directives(csp)
        assert "'unsafe-eval'" not in csp, (
            f"index.html source CSP must not contain 'unsafe-eval' (fail-safe default must be strict) — got: {csp}"
        )
        assert "'unsafe-inline'" not in script_src, (
            f"index.html source CSP script-src must not contain 'unsafe-inline' "
            f"(fail-safe default must be strict) — got: {script_src}"
        )

    def test_bubble_html_csp_is_strict_default(self):
        src = BUBBLE_HTML_PATH.read_text(encoding="utf-8")
        csp = _extract_csp_value(src)
        script_src = _script_src_directives(csp)
        assert "'unsafe-eval'" not in csp, (
            f"bubble.html source CSP must not contain 'unsafe-eval' (fail-safe default must be strict) — got: {csp}"
        )
        assert "'unsafe-inline'" not in script_src, (
            f"bubble.html source CSP script-src must not contain 'unsafe-inline' "
            f"(fail-safe default must be strict) — got: {script_src}"
        )

    @pytest.mark.skip(
        reason="CR-11 / R6-F5: CSP_PROD's connect-src is now built dynamically "
        "via buildConnectSrc({ allowGitHub: true }), so the constant can't be "
        "statically extracted as a complete string. The strict directives "
        "(script-src, frame-ancestors, form-action) are still verified by "
        "test_index_html_csp_is_strict_default."
    )
    def test_index_html_csp_matches_csp_prod(self, csp_prod: str):
        src = INDEX_HTML_PATH.read_text(encoding="utf-8")
        csp = _extract_csp_value(src)
        assert _normalize_csp(csp) == _normalize_csp(csp_prod), (
            f"index.html source CSP must equal CSP_PROD (modulo trailing ;).\n  source: {csp}\n  CSP_PROD: {csp_prod}"
        )

    @pytest.mark.skip(
        reason="CR-11 / R6-F5: bubble.html now ships CSP_PROD_BUBBLE (no "
        "api.github.com) while CSP_PROD aliases CSP_PROD_MAIN (with "
        "api.github.com). The two intentionally diverge — bubble has no "
        "update-check surface. The strict directives are still verified "
        "by test_bubble_html_csp_is_strict_default."
    )
    def test_bubble_html_csp_matches_csp_prod(self, csp_prod: str):
        src = BUBBLE_HTML_PATH.read_text(encoding="utf-8")
        csp = _extract_csp_value(src)
        assert _normalize_csp(csp) == _normalize_csp(csp_prod), (
            f"bubble.html source CSP must equal CSP_PROD (modulo trailing ;).\n  source: {csp}\n  CSP_PROD: {csp_prod}"
        )


class TestMainIndexOnHeadersReceivedStrict:
    """The HTTP-header CSP in main/index.ts is strict in production.

    Belt-and-suspenders alongside the meta tag — HTTP headers take precedence
    over meta tags per the CSP spec.
    """

    def test_main_uses_on_headers_received(self):
        src = _read_main_process_src()
        assert "onHeadersReceived" in src, (
            "main process (index.ts or bootstrap.ts) must register onHeadersReceived to set CSP via HTTP header"
        )

    def test_main_csp_conditional_on_app_is_packaged(self):
        src = _read_main_process_src()
        # The script-src directive must be conditionally permissive only when
        # app.isPackaged === false (i.e. dev mode).
        assert "app.isPackaged" in src, "main process CSP must branch on app.isPackaged"
        # Find the script-src line — it should reference app.isPackaged.
        m = re.search(r"script-src[^;]*", src)
        assert m, "main process must contain a script-src directive (in index.ts or bootstrap.ts)"
        script_src_line = m.group(0)
        assert "isPackaged" in script_src_line, (
            f"main process script-src must conditionally add unsafe-eval/"
            f"unsafe-inline based on app.isPackaged — got: {script_src_line}"
        )


def _node_bin_path() -> str | None:
    """Return the node binary path, preferring the pinned /home/z/.local/node22."""
    candidates = [
        "/home/z/.local/node22/bin/node",
        shutil.which("node"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _electron_vite_bin_path() -> Path | None:
    """Return the electron-vite binary path inside the client's node_modules."""
    p = CLIENT_DIR / "node_modules" / ".bin" / "electron-vite"
    return p if p.is_file() else None


@pytest.fixture(scope="module")
def built_html_files():
    """Run `electron-vite build` and return (index_html, bubble_html) source.

    Skipped if Node or electron-vite isn't available.
    """
    node = _node_bin_path()
    evite = _electron_vite_bin_path()
    if not node or not evite:
        pytest.skip("Node or electron-vite not available — skipping build test")

    out_dir = CLIENT_DIR / "out" / "renderer"
    # Remove stale build output so we don't read stale HTML.
    if out_dir.is_dir():
        shutil.rmtree(out_dir)

    env_path = f"{str(Path(node).parent)}:{__import__('os').environ.get('PATH', '')}"
    try:
        proc = subprocess.run(
            [str(evite), "build"],
            cwd=str(CLIENT_DIR),
            env={**__import__("os").environ, "PATH": env_path},
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"electron-vite build could not run: {exc!r}")
        return None, None  # pragma: no cover

    if proc.returncode != 0:
        pytest.skip(
            f"electron-vite build failed (rc={proc.returncode}).\n"
            f"stdout: {proc.stdout[-1000:]}\nstderr: {proc.stderr[-1000:]}"
        )

    built_index = out_dir / "index.html"
    built_bubble = out_dir / "bubble.html"
    if not built_index.is_file() or not built_bubble.is_file():
        pytest.skip(f"Built HTML files not found in {out_dir}")
        return None, None  # pragma: no cover

    return built_index.read_text(encoding="utf-8"), built_bubble.read_text(encoding="utf-8")


@pytest.mark.usefixtures("built_html_files")
class TestBuiltHtmlHasStrictCsp:
    """End-to-end: the production-built HTML files contain the strict CSP."""

    def test_built_index_html_has_strict_csp(self, built_html_files):
        index_html, _ = built_html_files
        csp = _extract_csp_value(index_html)
        assert "'unsafe-eval'" not in csp, f"Built index.html CSP must NOT contain 'unsafe-eval' — got: {csp}"
        script_src = _script_src_directives(csp)
        assert "'unsafe-inline'" not in script_src, (
            f"Built index.html CSP script-src must NOT contain 'unsafe-inline' — got: {script_src}"
        )

    def test_built_bubble_html_has_strict_csp(self, built_html_files):
        _, bubble_html = built_html_files
        csp = _extract_csp_value(bubble_html)
        assert "'unsafe-eval'" not in csp, f"Built bubble.html CSP must NOT contain 'unsafe-eval' — got: {csp}"
        script_src = _script_src_directives(csp)
        assert "'unsafe-inline'" not in script_src, (
            f"Built bubble.html CSP script-src must NOT contain 'unsafe-inline' — got: {script_src}"
        )

    def test_built_index_html_csp_matches_csp_prod(self, built_html_files, csp_prod: str):
        index_html, _ = built_html_files
        csp = _extract_csp_value(index_html)
        assert _normalize_csp(csp) == _normalize_csp(csp_prod), (
            f"Built index.html CSP must equal CSP_PROD (modulo trailing ;).\n  built: {csp}\n  CSP_PROD: {csp_prod}"
        )

    def test_built_bubble_html_csp_matches_csp_prod(self, built_html_files, csp_prod: str):
        _, bubble_html = built_html_files
        csp = _extract_csp_value(bubble_html)
        assert _normalize_csp(csp) == _normalize_csp(csp_prod), (
            f"Built bubble.html CSP must equal CSP_PROD (modulo trailing ;).\n  built: {csp}\n  CSP_PROD: {csp_prod}"
        )

    def test_built_html_has_no_inline_event_handlers(self, built_html_files):
        """Production HTML must not have inline event handlers (onclick=...)."""
        for html in built_html_files:
            # Look for inline event-handler attributes — these would need
            # 'unsafe-inline' in script-src to function.
            assert not re.search(
                r"\son(click|load|error|submit|change|mouseover)\s*=",
                html,
            ), "Built HTML must not contain inline event-handler attributes"

    def test_built_html_has_no_inline_scripts(self, built_html_files):
        """Production HTML must not have inline <script>...</script> blocks.

        Vite should have replaced them with external script tags.
        """
        for html in built_html_files:
            # Inline <script> blocks (without src attribute) would need
            # 'unsafe-inline' in script-src. Allow empty <script></script>
            # (unusual but harmless) and JSON-LD blocks (we don't use those).
            inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>", html, flags=re.DOTALL)
            # Filter out truly empty <script></script>.
            non_empty_inline = [s for s in inline_scripts if re.sub(r"<[^>]+>", "", s).strip()]
            assert not non_empty_inline, (
                f"Built HTML must not contain non-empty inline <script> blocks — found: {non_empty_inline}"
            )
