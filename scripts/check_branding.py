#!/usr/bin/env python3
"""BRAND-001: Check that source files use centralized branding instead of hardcoded app name.

This script is designed to run in CI and as a pre-commit hook.
It checks all Python, TypeScript, TSX, HTML, JSON, and Rust source files for
hardcoded occurrences of the application name (the value of APP_NAME
defined in branding.py) and reports any that should be using the
branding constant instead.

╔══════════════════════════════════════════════════════════════════════╗
║  ⚠️  AI / AGENT WARNING — DO NOT DISABLE OR BYPASS THIS CHECK         ║
║                                                                       ║
║  This script exists to PREVENT hardcoded app-name strings.            ║
║  The APP_NAME variable (in branding.py / branding.ts) is the         ║
║  SINGLE SOURCE OF TRUTH for the product name. Replacing it           ║
║  with a hardcoded "Voice Typer" string is ALWAYS WRONG,              ║
║  even if the value currently matches.                                 ║
║                                                                       ║
║  If an AI agent suggests inlining the value or removing this          ║
║  check — DO NOT follow that suggestion. The variable exists so        ║
║  the app name can be changed in one place and propagate everywhere.  ║
╚══════════════════════════════════════════════════════════════════════╝

Usage:
    python scripts/check_branding.py          # check all relevant files
    python scripts/check_branding.py --list   # list which files would be checked

Exit code: 0 if OK, 1 if violations found.
"""

import re
import sys
from pathlib import Path

# ── Read APP_NAME from the central branding file ──────────────────────
BRANDING_FILE = Path("voice_typer/server/branding.py")
try:
    branding_text = BRANDING_FILE.read_text(encoding="utf-8")
except FileNotFoundError:
    print(f"ERROR: Branding file not found: {BRANDING_FILE}")
    print("Run this script from the project root directory.")
    sys.exit(1)

APP_NAME = None
for line in branding_text.splitlines():
    m = re.match(r'^APP_NAME\s*=\s*["\']([^"\']+)["\']', line)
    if m:
        APP_NAME = m.group(1)
        break

if not APP_NAME:
    print("ERROR: Could not read APP_NAME from branding.py")
    sys.exit(1)

# ── Branding definition files (exempt entirely) ───────────────────────
# (transitional step): include the Rust mirror `branding.rs` so
# the scanner doesn't false-positive on its `pub const APP_NAME: &str =
# "Voice Typer";` declaration. The Rust file is the canonical source
# for the Tauri host (see the module docstring in branding.rs).
BRANDING_FILES = frozenset(
    {
        "voice_typer/server/branding.py",
        "voice_typer/client/src/renderer/src/branding.ts",
        "voice_typer/client/src/main/branding.ts",
        "src-tauri/src/branding.rs",
    }
)

# ── Rust branding module (read for the cross-language parity check) ───
# the existing branding.rs:11-21 docstring notes that
# `scripts/check_branding.py` does NOT currently read branding.rs, so
# a drift between the Python and Rust constants would go undetected.
# Extend the script to read branding.rs and assert byte-for-byte parity
# with branding.py::APP_NAME. The full codegen-from-protocol/branding.json
# migration is a larger effort; this is the transitional step.
RUST_BRANDING_FILE = Path("src-tauri/src/branding.rs")

# ── Directories to scan ──────────────────────────────────────────────
# include `src-tauri/src` so hardcoded "Voice Typer" literals in
# the Rust host source (tray tooltips, toast titles, etc.) are caught
# the same way Python/TS literals are. The Tauri host's branding.rs is
# exempt (see BRANDING_FILES); other Rust files using the literal would
# be flagged with a hint to `use crate::branding::APP_NAME;`.
#
# The two build-config files (tauri.conf.json, electron-builder.yml)
# are listed individually rather than via their parent directories so
# the scanner does not pick up unrelated noise (Cargo.toml, package.json,
# vite configs, capabilities JSON, icons dir, etc.). These files
# LEGITIMATELY need literal "Voice Typer" strings in their
# productName/title fields — see BUILD_CONFIG_FILES + the
# _is_build_config_literal allowlist below for the documented
# "build-config literal" exception to C-BRAND-1.
SCAN_DIRS = [
    "voice_typer/server",
    "voice_typer/client/src",
    "src-tauri/src",
    "voice_typer/__init__.py",
    "voice_typer/__main__.py",
    "src-tauri/tauri.conf.json",
    "voice_typer/client/electron-builder.yml",
    # C-BRAND-1: scan the GitHub Actions workflows (`.github/workflows/*.yml`)
    # so a future hardcoded app name in CI (tray toast titles, signtool
    # descriptions, artifact names) is caught the same way Python/TS/Rust
    # literals are. Build-artifact paths inside the macOS workflow are
    # allowlisted below (see _is_workflow_build_artifact) because the
    # bundled `.app` / `.dmg` filenames are derived from
    # `productName` at Tauri-build time — the same narrow exception as
    # the tauri.conf.json / electron-builder.yml `productName` field.
    ".github/workflows",
]

# ── File extensions to check ─────────────────────────────────────────
# include `.rs` so Rust source files are scanned, and `.json` so
# main-process locale files (i18n/locales/*.json) cannot smuggle in a
# hardcoded app name (they must use the `{appName}` placeholder, which
# `_withAppName` in main/i18n.ts substitutes with APP_NAME).
# `.yml` / `.yaml` are included so electron-builder.yml is scanned.
EXTENSIONS = frozenset({".py", ".ts", ".tsx", ".html", ".rs", ".json", ".yml", ".yaml"})

# ── Skip binary/exempt dirs ──────────────────────────────────────────
SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        "stubs",
        ".hypothesis",
        "out",
    }
)

# ── Renderer translations (intentionally localized brand) ────────────
# The renderer's `i18n/translations/*.json` localize the product name
# per-locale (e.g. hi.json carries a translated brand) — a deliberate
# i18n design exercised by the setLocale-propagation tests. These files
# are therefore exempt from the hardcoded-name scan, which would
# otherwise flag the `"name": "Voice Typer"` fallback spellings. The
# main-process locale files (`main/i18n/locales/*.json`) are NOT exempt:
# they must use the `{appName}` placeholder.
RENDERER_TRANSLATIONS_PREFIX = "voice_typer/client/src/renderer/src/i18n/translations"

# ── Build-config files (documented "build-config literal" exception) ──
# tauri.conf.json and electron-builder.yml are read by Tauri /
# electron-builder BEFORE the app boots — at that point no JS / Python /
# Rust code runs, so the branding constant (APP_NAME) is NOT yet
# available. These two files therefore LEGITIMATELY require literal
# "Voice Typer" strings in their `productName` and `title` fields
# (Tauri uses productName for the bundle name + window titles;
# electron-builder uses productName for the artifact / installer name).
#
# This is a narrow, documented exception to C-BRAND-1 — it applies ONLY
# to the `productName` and `title` keys in these two files. Every other
# literal "Voice Typer" reference in these files (descriptions, paths,
# identifier fields, comments) is still flagged. Adding a new build-
# config file to this allowlist requires updating the audit trail in
# worklog.md citing the field that legitimately needs the literal.
BUILD_CONFIG_FILES = frozenset(
    {
        "src-tauri/tauri.conf.json",
        "voice_typer/client/electron-builder.yml",
    }
)

# The set of JSON / YAML keys whose value is allowlisted as a build-
# config literal. Both productName (Tauri + electron-builder) and
# title (Tauri window titles) are required by the bundler / window
# manager at config-parse time, before any branding constant can run.
_BUILD_CONFIG_LITERAL_KEYS = ("productName", "title")


def _skip_dir(segments: list[str]) -> bool:
    """Return True if any segment is in SKIP_DIRS."""
    return any(s in SKIP_DIRS for s in segments)


def _is_comment_line(line: str, ext: str) -> bool:
    """Check if a line is a pure comment (no code)."""
    stripped = line.strip()
    if not stripped:
        return True
    if ext == ".py":
        return stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''")
    if ext in (".ts", ".tsx"):
        return stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*")
    if ext == ".html":
        return stripped.startswith("<!--")
    if ext == ".rs":
        # Rust line comments: `//` or `//!` (inner doc) or `///` (outer doc).
        # Block comments start with `/*` (rare in this codebase).
        return stripped.startswith("//") or stripped.startswith("/*")
    if ext in (".yml", ".yaml"):
        # YAML comments start with `#`.
        return stripped.startswith("#")
    return False


def _to_rel_str(filepath: Path) -> str:
    """Convert a file path to a forward-slash relative string for branding-file lookup."""
    try:
        rel = filepath.relative_to(Path.cwd())
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(filepath).replace("\\", "/")


def _is_build_config_literal(rel_str: str, line: str) -> bool:
    """Return True if a line is an allowlisted build-config literal.

    Build-config files (``tauri.conf.json``, ``electron-builder.yml``)
    require literal ``APP_NAME`` strings in their ``productName`` and
    ``title`` fields because these values are read by the bundler /
    window-manager BEFORE the app boots — the branding constant is not
    yet available at config-parse time. This is the documented
    "build-config literal" exception to C-BRAND-1.

    Matches both JSON (``"productName": "Voice Typer"``) and YAML
    (``productName: Voice Typer`` or ``productName: "Voice Typer"``)
    forms. The optional surrounding quotes on the value handle both
    styles; the optional surrounding quotes on the key handle the JSON
    form (YAML keys are unquoted but the regex still matches because
    the quote is optional).

    Only the keys in ``_BUILD_CONFIG_LITERAL_KEYS`` (``productName``,
    ``title``) are allowlisted — every other literal reference in these
    files (descriptions, identifiers, paths, comments) is still flagged.
    """
    if rel_str not in BUILD_CONFIG_FILES:
        return False
    for key in _BUILD_CONFIG_LITERAL_KEYS:
        # Allow optional quotes around the key (JSON form) and around
        # the value (JSON or quoted-YAML form). The unquoted YAML form
        # (``productName: Voice Typer``) is also matched because the
        # closing quote / end-of-line both satisfy the optional quote.
        pattern = (
            rf'["\']?{re.escape(key)}["\']?\s*:\s*["\']?{re.escape(APP_NAME)}["\']?'
        )
        if re.search(pattern, line):
            return True
    return False


def _is_workflow_build_artifact(rel_str: str, line: str) -> bool:
    """Return True for a build-artifact literal inside a workflow YAML.

    The Tauri bundler names its output artifacts after ``productName``
    (the ``.app`` bundle directory and the ``.dmg`` disk image in
    ``tauri-macos-build.yml``). Those filenames are generated by
    ``cargo tauri build`` — a script cannot read ``branding.py`` and
    substitute ``APP_NAME`` into them. This is the same narrow
    "build-config literal" exception as ``productName`` in
    ``tauri.conf.json`` (see ``BUILD_CONFIG_FILES``).

    The pattern is anchored to the current ``APP_NAME`` value so a
    future rename keeps working (the artifact filenames are kept in
    lockstep with ``productName``). Every OTHER ``Voice Typer``
    reference in a workflow (toast titles, signtool descriptions,
    env values) is still flagged.
    """
    if not rel_str.startswith(".github/workflows/"):
        return False
    if "bundle/" not in line:
        return False
    return bool(
        re.search(rf"bundle/(?:[a-z]+/)?{re.escape(APP_NAME)}(?:_[^\"/]*)?\.(?:app|dmg)", line)
    )


def check_file(filepath: Path) -> list[tuple[int, str]]:
    """Check a single file for hardcoded app name. Returns list of (line_no, line_text)."""
    rel_str = _to_rel_str(filepath)

    if rel_str in BRANDING_FILES:
        return []

    if rel_str.startswith(RENDERER_TRANSLATIONS_PREFIX):
        return []

    ext = filepath.suffix.lower()
    hits: list[tuple[int, str]] = []

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    for i, line in enumerate(text.splitlines(), 1):
        # Skip comment-only lines
        if _is_comment_line(line, ext):
            continue

        # If the line doesn't contain the app name at all, skip
        if APP_NAME not in line:
            continue

        # If the line already references APP_NAME (the constant), it's OK
        if re.search(r"APP_NAME", line):
            continue

        # If the line imports from branding, it's OK
        if re.search(r"(from\s+.*branding|import.*branding)", line):
            continue
        # Rust modules use `use crate::branding::APP_NAME;` (or
        # `use super::branding::APP_NAME;`) rather than a Python/TS
        # `from ... import` or `import ... branding`. Treat any Rust
        # `use` line that mentions `branding` as the equivalent
        # exemption.
        if ext == ".rs" and re.search(r"\buse\s+.*branding\b", line):
            continue

        # Build-config literal exception: productName / title in
        # tauri.conf.json + electron-builder.yml legitimately need the
        # literal brand (see BUILD_CONFIG_FILES docstring).
        if _is_build_config_literal(rel_str, line):
            continue

        # Workflow build-artifact exception: .app / .dmg filenames in
        # the CI workflows are generated by the Tauri bundler from
        # productName and cannot read the branding constant at build
        # time (see _is_workflow_build_artifact).
        if _is_workflow_build_artifact(rel_str, line):
            continue

        # Check if the app name appears inside a string literal
        # Simple heuristic: it's inside quotes or backticks
        if re.search(rf'["\'`]{re.escape(APP_NAME)}["\'`]', line):
            hits.append((i, line.strip()))

    return hits


def _read_rust_app_name() -> str | None:
    """Read ``APP_NAME`` from ``src-tauri/src/branding.rs``.

    the Rust host mirrors the Python ``branding.py::APP_NAME``
        constant as ``pub const APP_NAME: &str = "Voice Typer";``. Return
        the string literal value (or None if the file/constant is missing)
        so the cross-language parity check can compare it against the
        Python canonical value.
    """
    if not RUST_BRANDING_FILE.is_file():
        return None
    try:
        rust_text = RUST_BRANDING_FILE.read_text(encoding="utf-8")
    except Exception:
        return None
    # Match `pub const APP_NAME: &str = "Voice Typer";`
    # (allow optional `pub(crate)` visibility + any whitespace).
    m = re.search(
        r'pub(?:\(crate\))?\s+const\s+APP_NAME\s*:\s*&str\s*=\s*"([^"]+)"',
        rust_text,
    )
    return m.group(1) if m else None


def main() -> int:
    show_list = "--list" in sys.argv
    all_hits: list[tuple[str, int, str]] = []  # (file, line, text)

    # cross-language parity check. branding.rs is the Rust mirror
    # of branding.py::APP_NAME — they MUST be byte-for-byte identical.
    # Per branding.rs:11-21, the script historically did NOT read
    # branding.rs, so a drift between the two would go undetected. This
    # check fails fast (before the hardcoded-literal scan) if the Rust
    # constant drifted from the Python canonical value.
    rust_app_name = _read_rust_app_name()
    if rust_app_name is not None and rust_app_name != APP_NAME:
        print(
            f"ERROR: branding.rs APP_NAME ({rust_app_name!r}) does NOT "
            f"match branding.py APP_NAME ({APP_NAME!r}). The two "
            "constants MUST be byte-for-byte identical — update both "
            "files in lockstep when renaming the product."
        )
        print(f"  - voice_typer/server/branding.py: APP_NAME = {APP_NAME!r}")
        print(f"  - src-tauri/src/branding.rs: APP_NAME = {rust_app_name!r}")
        return 1

    # Collect files from directories
    for entry in SCAN_DIRS:
        path = Path(entry)
        if not path.exists():
            continue
        if path.is_file():
            hits = check_file(path.resolve())
            for lineno, text in hits:
                all_hits.append((str(path), lineno, text))
        else:
            for f in path.rglob("*"):
                if not f.is_file():
                    continue
                abs_f = f.resolve()
                # Compute relative parts to check against SKIP_DIRS
                try:
                    rel_parts = abs_f.relative_to(Path.cwd()).parts
                except ValueError:
                    rel_parts = abs_f.parts
                if _skip_dir(rel_parts):
                    continue
                if f.suffix.lower() not in EXTENSIONS:
                    continue
                hits = check_file(abs_f)
                for lineno, text in hits:
                    all_hits.append((str(abs_f), lineno, text))

    if show_list:
        if all_hits:
            print(f"Files with hardcoded '{APP_NAME}' references:\n")
            for filepath, lineno, text in sorted(all_hits):
                print(f"  {filepath}:{lineno}:  {text}")
        else:
            print(f"No hardcoded '{APP_NAME}' references found in source files.")
        return 0

    if all_hits:
        print(
            f"ERROR: Found {len(all_hits)} hardcoded reference(s) to "
            f"'{APP_NAME}' that should use the branding constant."
        )
        print()
        for filepath, lineno, text in sorted(all_hits):
            print(f"  {filepath}:{lineno}:  {text}")
        print()
        print("Fix: Replace the hardcoded string with the APP_NAME constant from:")
        print("  - Python: from voice_typer.server.branding import APP_NAME")
        print("  - TypeScript (main): import { APP_NAME } from './branding'  (src/main/branding.ts)")
        print("  - TypeScript (renderer): import { APP_NAME } from '../branding'  (src/renderer/src/branding.ts)")
        print("  - Rust: use crate::branding::APP_NAME;  (src-tauri/src/branding.rs)")
        return 1

    print(f"OK: No hardcoded '{APP_NAME}' references found in source files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
