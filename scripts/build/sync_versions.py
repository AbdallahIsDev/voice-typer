#!/usr/bin/env python3
"""Synchronize the version string across all project files.

NEW-DOC-019: ``pyproject.toml`` is the single source of truth for the
project version.  This script reads the version from ``pyproject.toml``
and writes the same value into every other file that hardcodes a
version string:

  - ``voice_typer/__init__.py`` (fallback string for source checkouts
    — the runtime reads from ``importlib.metadata`` first, but the
    fallback is what users see when running from a git checkout
    without ``pip install``).
  - ``voice_typer/client/package.json`` (Electron app version).
  - ``voice_typer/client/electron-builder.yml`` (installer version).
  - ``CHANGELOG.md`` (most-recent Unreleased → version bump).

Usage::

    python scripts/build/sync_versions.py            # print current
    python scripts/build/sync_versions.py --apply     # write to all
    python scripts/build/sync_versions.py --check     # CI mode: exit 1 if drift

Exit codes:
  0 — versions are in sync (or successfully synced with --apply)
  1 — versions drifted and --check was passed (CI mode)
  2 — I/O error reading or writing a file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Files that contain a version string we want to keep in sync.
PYPROJECT = REPO_ROOT / "pyproject.toml"
INIT_PY = REPO_ROOT / "voice_typer" / "__init__.py"
PACKAGE_JSON = REPO_ROOT / "voice_typer" / "client" / "package.json"
ELECTRON_BUILDER = REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml"


def read_pyproject_version() -> str:
    """Read the version from pyproject.toml [project] table."""
    if not PYPROJECT.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {PYPROJECT}")
    text = PYPROJECT.read_text(encoding="utf-8")
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r'^version\s*=\s*"([^"]+)"', stripped)
            if m:
                return m.group(1)
    raise ValueError("Could not find [project] version in pyproject.toml")


def read_init_py_fallback() -> str | None:
    """Read the hardcoded fallback version from voice_typer/__init__.py."""
    if not INIT_PY.exists():
        return None
    text = INIT_PY.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def write_init_py_fallback(version: str) -> None:
    """Update the fallback version string in voice_typer/__init__.py."""
    text = INIT_PY.read_text(encoding="utf-8")
    new_text = re.sub(
        r'(__version__\s*=\s*)"[^"]+"',
        rf'\1"{version}"',
        text,
    )
    if new_text == text:
        new_text = text + f'\n__version__ = "{version}"\n'
    INIT_PY.write_text(new_text, encoding="utf-8")


def read_package_json_version() -> str | None:
    if not PACKAGE_JSON.exists():
        return None
    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    return data.get("version")


def write_package_json_version(version: str) -> None:
    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    data["version"] = version
    PACKAGE_JSON.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )


def read_electron_builder_version() -> str | None:
    """Read the explicit version from electron-builder.yml.

    Returns None if the file doesn't exist OR if it has no ``version:``
    field (which is valid — electron-builder inherits from package.json
    when no explicit version is set).
    """
    if not ELECTRON_BUILDER.exists():
        return None
    text = ELECTRON_BUILDER.read_text(encoding="utf-8")
    m = re.search(r"^version:\s*([^\s]+)", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else None


def write_electron_builder_version(version: str) -> None:
    """Only write if a version field already exists.

    If the file has no explicit ``version:`` field, electron-builder
    inherits from package.json — which is already synced separately.
    Don't inject a redundant field.
    """
    text = ELECTRON_BUILDER.read_text(encoding="utf-8")
    if not re.search(r"^version:\s*[^\s]+", text, re.MULTILINE):
        return
    new_text = re.sub(
        r"^(version:\s*)[^\s]+",
        rf"\g<1>{version}",
        text,
        flags=re.MULTILINE,
    )
    ELECTRON_BUILDER.write_text(new_text, encoding="utf-8")


def collect_versions() -> dict[str, str | None]:
    """Return a dict of {location: version_or_None}."""
    return {
        "pyproject.toml": read_pyproject_version(),
        "voice_typer/__init__.py (fallback)": read_init_py_fallback(),
        "voice_typer/client/package.json": read_package_json_version(),
        "voice_typer/client/electron-builder.yml": read_electron_builder_version(),
    }


def apply_version(version: str) -> list[str]:
    """Write ``version`` to every file.  Returns list of updated paths."""
    updated: list[str] = []
    write_init_py_fallback(version)
    updated.append(str(INIT_PY))
    if PACKAGE_JSON.exists():
        write_package_json_version(version)
        updated.append(str(PACKAGE_JSON))
    if ELECTRON_BUILDER.exists() and read_electron_builder_version() is not None:
        write_electron_builder_version(version)
        updated.append(str(ELECTRON_BUILDER))
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize version strings across project files.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the pyproject.toml version to all other files.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode: exit 1 if any file's version differs from pyproject.toml.",
    )
    args = parser.parse_args(argv)

    try:
        versions = collect_versions()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    source_version = versions["pyproject.toml"]
    if source_version is None:
        print("ERROR: could not read version from pyproject.toml", file=sys.stderr)
        return 2

    print(f"Source of truth: pyproject.toml = {source_version}")
    print()
    print("Current versions across files:")
    for location, ver in versions.items():
        if ver is None:
            marker = "-"
            display = "(inherits / not set)"
        elif ver == source_version:
            marker = " "
            display = ver
        else:
            marker = "!"
            display = ver
        print(f"  {marker} {location:50s} {display}")

    drifted = [(loc, ver) for loc, ver in versions.items() if ver is not None and ver != source_version]

    if args.apply:
        updated = apply_version(source_version)
        print()
        print(f"Updated {len(updated)} files to version {source_version}:")
        for path in updated:
            print(f"  - {path}")
        return 0

    if args.check:
        if drifted:
            print()
            print(f"ERROR: {len(drifted)} file(s) have a version that", file=sys.stderr)
            print(f"differs from pyproject.toml ({source_version}).", file=sys.stderr)
            print("Run `python scripts/build/sync_versions.py --apply` to fix.", file=sys.stderr)
            return 1
        return 0

    if drifted:
        print()
        print(f"Note: {len(drifted)} file(s) drifted. Run with --apply to sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
