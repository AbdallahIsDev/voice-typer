#!/usr/bin/env python3
"""Synchronize the version string across all project files.

``pyproject.toml`` is the single source of truth for the project
version.  This script reads the version from ``pyproject.toml``
and writes the same value into every other file that hardcodes a
version string:

  - ``voice_typer/client/package.json`` (Electron app version).
  - ``voice_typer/client/electron-builder.yml`` (installer version,
    only when an explicit ``version:`` field already exists).
  - ``src-tauri/tauri.conf.json`` (Tauri host app version).
  - ``src-tauri/Cargo.toml`` (crate ``[package] version``).
  - ``tauri-binaries.json`` (per-binary integrity-manifest versions).

``CHANGELOG.md`` is intentionally NOT touched: it is maintained by
hand and its "Unreleased → versioned" edits are a human review step,
not a string substitution a bump script can perform safely.

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

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]
    except ImportError:  # pragma: no cover — tomli is a fallback dep
        tomllib = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Files that contain a version string we want to keep in sync.
# NOTE: ``voice_typer/__init__.py`` is intentionally NOT synced here.
# ``__init__.py`` resolves ``__version__`` lazily via PEP 562
# ``__getattr__`` (see ``bench/COLDSTART_REPORT.md`` — 57% of the
# post-optimization tray-import cumulative time is metadata I/O).
# A regex like ``r'__version__\s*=\s*"([^"]+)"'`` would NOT match
# the lazy resolver body (which assigns to a local ``v = "<version>"``),
# so a fallback write would APPEND a new ``__version__ = "<version>"``
# module-level line that shadows the lazy ``__getattr__`` and silently
# breaks the coldstart optimization. ``importlib.metadata`` remains
# the source of truth at runtime; ``pyproject.toml`` is the source of
# truth at build time.
PYPROJECT = REPO_ROOT / "pyproject.toml"
PACKAGE_JSON = REPO_ROOT / "voice_typer" / "client" / "package.json"
ELECTRON_BUILDER = REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml"
# WR-20: also sync the Tauri v2 host (src-tauri/) so the Rust shell +
# Tauri config report the same version as the Python package.
TAURI_CONF_JSON = REPO_ROOT / "src-tauri" / "tauri.conf.json"
CARGO_TOML = REPO_ROOT / "src-tauri" / "Cargo.toml"
# TC-34: the Tauri binary integrity manifest (root-level tauri-binaries.json)
# also hardcodes a ``version`` per binary entry. It must be synced on bump
# so the autostart launcher's integrity gate compares against the current
# app version instead of a stale one.
TAURI_BINARIES_JSON = REPO_ROOT / "tauri-binaries.json"


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


def read_tauri_conf_version() -> str | None:
    """Read the ``version`` field from ``src-tauri/tauri.conf.json``.

    WR-20: Tauri v2's config schema stores the app version at the
    top-level ``version`` key. Returns None if the file is absent
    (e.g. in a checkout that doesn't include the Tauri host).
    """
    if not TAURI_CONF_JSON.exists():
        return None
    data = json.loads(TAURI_CONF_JSON.read_text(encoding="utf-8"))
    return data.get("version")


def write_tauri_conf_version(version: str) -> None:
    """Update the top-level ``version`` field in ``tauri.conf.json``.

    Preserves JSON formatting (2-space indent + trailing newline) to
    match the existing file style. Only writes if a ``version`` key is
    already present — never injects a new key.
    """
    text = TAURI_CONF_JSON.read_text(encoding="utf-8")
    data = json.loads(text)
    if "version" not in data:
        return  # don't inject a new key
    data["version"] = version
    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    TAURI_CONF_JSON.write_text(new_text, encoding="utf-8")


def read_cargo_toml_version() -> str | None:
    """Read ``package.version`` from ``src-tauri/Cargo.toml``.

    WR-20: Cargo stores the crate version under ``[package] version =
    "..."``. Uses stdlib ``tomllib`` (Python 3.11+) or the ``tomli``
    backport (3.10). Falls back to a regex if neither is importable
    (rare — packaging depends on tomli).
    """
    if not CARGO_TOML.exists():
        return None
    text = CARGO_TOML.read_text(encoding="utf-8")
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            return data.get("package", {}).get("version")
        except Exception:
            pass  # fall through to regex
    # Regex fallback — handles the common ``[package]\n...\nversion = "..."`` case.
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def write_cargo_toml_version(version: str) -> None:
    """Update ``package.version`` in ``src-tauri/Cargo.toml``.

    Uses a regex replacement so we don't have to round-trip the entire
    TOML through ``tomllib`` (which would lose comments, formatting,
    and the order of tables). The regex matches the first
    ``version = "..."`` line, which is always under ``[package]`` per
    Cargo's manifest schema (the ``[package]`` table is required and
    conventionally placed first).
    """
    text = CARGO_TOML.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'^(version\s*=\s*)"[^"]+"',
        rf'\1"{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        return  # no version field — don't inject
    CARGO_TOML.write_text(new_text, encoding="utf-8")


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Split ``X.Y.Z`` into a numeric tuple for correct ordering.

    Plain ``min()`` on strings is lexicographic — ``"10.0.0" < "9.9.9"``
    — which would mis-report the drift direction. Comparing on the
    numeric parts fixes that.
    """
    return tuple(int(part) for part in version.split("."))


def read_tauri_binaries_version() -> str | None:
    """Read the version from ``tauri-binaries.json``.

    TC-34: the manifest stores a ``version`` string on every entry of
    ``data["binaries"]``. Returns None if the file is absent or has no
    binary entries (both are valid — the manifest is only created by
    the Tauri build pipeline).
    """
    if not TAURI_BINARIES_JSON.exists():
        return None
    data = json.loads(TAURI_BINARIES_JSON.read_text(encoding="utf-8"))
    versions = {
        entry.get("version")
        for entry in data.get("binaries", {}).values()
        if isinstance(entry, dict) and entry.get("version")
    }
    if not versions:
        return None
    if len(versions) == 1:
        return next(iter(versions))
    # Entries drifted from each other (shouldn't happen — ``--apply``
    # keeps them identical by construction). Surface the numerically
    # smallest so ``--check`` flags the drift against pyproject.toml
    # regardless of which entry moved.
    return min(versions, key=_version_sort_key)


def write_tauri_binaries_version(version: str) -> None:
    """Update every binary-entry ``version`` field in ``tauri-binaries.json``.

    Uses a targeted regex replace (like ``write_cargo_toml_version``) so
    the file's exact formatting is preserved — no re-serialization churn
    on the compact ``_platforms`` arrays or the large ``_comment``
    prose. Matches only ``"version": "..."`` string fields (the three
    binary entries); the top-level ``"version": 1`` schema int is a
    bare number and is untouched.
    """
    text = TAURI_BINARIES_JSON.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'("version"\s*:\s*)"[^"]+"',
        rf'\1"{version}"',
        text,
    )
    if n == 0:
        return  # no binary version fields — don't inject
    TAURI_BINARIES_JSON.write_text(new_text, encoding="utf-8")


def collect_versions() -> dict[str, str | None]:
    """Return a dict of {location: version_or_None}."""
    return {
        "pyproject.toml": read_pyproject_version(),
        "voice_typer/client/package.json": read_package_json_version(),
        "voice_typer/client/electron-builder.yml": read_electron_builder_version(),
        # WR-20: Tauri v2 host files
        "src-tauri/tauri.conf.json": read_tauri_conf_version(),
        "src-tauri/Cargo.toml": read_cargo_toml_version(),
        # TC-34: Tauri binary integrity manifest
        "tauri-binaries.json": read_tauri_binaries_version(),
    }


def apply_version(version: str) -> list[str]:
    """Write ``version`` to every file.  Returns list of updated paths."""
    updated: list[str] = []
    if PACKAGE_JSON.exists():
        write_package_json_version(version)
        updated.append(str(PACKAGE_JSON))
    if ELECTRON_BUILDER.exists() and read_electron_builder_version() is not None:
        write_electron_builder_version(version)
        updated.append(str(ELECTRON_BUILDER))
    # WR-20: Tauri v2 host files
    if TAURI_CONF_JSON.exists() and read_tauri_conf_version() is not None:
        write_tauri_conf_version(version)
        updated.append(str(TAURI_CONF_JSON))
    if CARGO_TOML.exists() and read_cargo_toml_version() is not None:
        write_cargo_toml_version(version)
        updated.append(str(CARGO_TOML))
    # TC-34: Tauri binary integrity manifest
    if TAURI_BINARIES_JSON.exists() and read_tauri_binaries_version() is not None:
        write_tauri_binaries_version(version)
        updated.append(str(TAURI_BINARIES_JSON))
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
            print(f"ERROR: {len(drifted)} files have a version that", file=sys.stderr)
            print(f"differs from pyproject.toml ({source_version}).", file=sys.stderr)
            print("Run `python scripts/build/sync_versions.py --apply` to fix.", file=sys.stderr)
            return 1
        return 0

    if drifted:
        print()
        print(f"Note: {len(drifted)} files drifted. Run with --apply to sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
