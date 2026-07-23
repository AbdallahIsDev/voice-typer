#!/usr/bin/env python3
"""BRAND-001: Check that source files use centralized branding instead of hardcoded app name.

This script is designed to run in CI and as a pre-commit hook.
It checks all Python, TypeScript, TSX, and HTML source files for
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
BRANDING_FILES = frozenset(
    {
        "voice_typer/server/branding.py",
        "voice_typer/client/src/renderer/src/branding.ts",
        "voice_typer/client/src/main/branding.ts",
    }
)

# ── Directories to scan ──────────────────────────────────────────────
SCAN_DIRS = [
    "voice_typer/server",
    "voice_typer/client/src",
    "voice_typer/__init__.py",
    "voice_typer/__main__.py",
]

# ── File extensions to check ─────────────────────────────────────────
EXTENSIONS = frozenset({".py", ".ts", ".tsx", ".html"})

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
    return False


def _to_rel_str(filepath: Path) -> str:
    """Convert a file path to a forward-slash relative string for branding-file lookup."""
    try:
        rel = filepath.relative_to(Path.cwd())
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(filepath).replace("\\", "/")


def check_file(filepath: Path) -> list[tuple[int, str]]:
    """Check a single file for hardcoded app name. Returns list of (line_no, line_text)."""
    rel_str = _to_rel_str(filepath)

    if rel_str in BRANDING_FILES:
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

        # Check if the app name appears inside a string literal
        # Simple heuristic: it's inside quotes or backticks
        if re.search(rf'["\'`]{re.escape(APP_NAME)}["\'`]', line):
            hits.append((i, line.strip()))

    return hits


def main() -> int:
    show_list = "--list" in sys.argv
    all_hits: list[tuple[str, int, str]] = []  # (file, line, text)

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
        return 1

    print(f"OK: No hardcoded '{APP_NAME}' references found in source files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
