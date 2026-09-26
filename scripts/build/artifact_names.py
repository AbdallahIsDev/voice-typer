"""Canonical release-artifact naming for the runtime-pack split (C-CI-13).

New names (slim-core / runtime-pack / full-offline / pack-manifest) are
ADDITIVE and must not collide with EXISTING_PROTECTED_NAMES.
SUPPORTED_TRIPLES stays in lockstep with gen_tauri_icons_stub.SIDECAR_TRIPLES.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence

# MUST match gen_tauri_icons_stub.SIDECAR_TRIPLES (installer-naming drift test).
SUPPORTED_TRIPLES: tuple[str, ...] = (
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
)

WINDOWS_TRIPLES: frozenset[str] = frozenset({"x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"})

# C-CI-13: never rename these; new §11.9 names are additive only.
EXISTING_PROTECTED_NAMES: tuple[str, ...] = (
    "tauri-windows-installer",
    "Lausu-Tauri-MSI",
    "Lausu-Tauri-Sidecar-Binaries",
    "Lausu-Tauri-SHA256SUMS",
    "tauri-binaries-manifest-windows",
    "python-sidecar-x86_64-pc-windows-msvc.exe",
    "python-sidecar-aarch64-pc-windows-msvc.exe",
    "lausu-worker-x86_64-pc-windows-msvc.exe",
    "lausu-worker-aarch64-pc-windows-msvc.exe",
    "windows-key-listener.exe",
)

#: App versions X.Y.Z; pack versions plain integers (pack-<n>).
_APP_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PACK_VERSION_RE = re.compile(r"^\d+$")


def _check_triple(triple: str) -> None:
    """Raise ``ValueError`` for any triple outside the canonical 6."""
    if triple not in SUPPORTED_TRIPLES:
        raise ValueError(f"unsupported target triple: {triple!r} (supported: {', '.join(SUPPORTED_TRIPLES)})")


def _check_app_version(version: str) -> None:
    """Raise ``ValueError`` for a malformed ``X.Y.Z`` app version."""
    if not _APP_VERSION_RE.match(version):
        raise ValueError(f"malformed app-version: {version!r} (expected X.Y.Z, e.g. 1.0.0)")


def _check_pack_version(version: str) -> None:
    """Raise ``ValueError`` for a malformed integer pack version."""
    if not _PACK_VERSION_RE.match(version):
        raise ValueError(f"malformed pack-version: {version!r} (expected an integer, e.g. 3)")


def _exe_suffix(triple: str) -> str:
    """``.exe`` on Windows triples, empty elsewhere (sidecar convention)."""
    return ".exe" if triple in WINDOWS_TRIPLES else ""


def slim_core_installer_name(app_version: str, triple: str) -> str:
    """§11.9 slim-core installer name: ``lausu-slim-core-<v>-<triple>[.exe]``."""
    _check_app_version(app_version)
    _check_triple(triple)
    return f"lausu-slim-core-{app_version}-{triple}{_exe_suffix(triple)}"


def runtime_pack_name(pack_version: str, triple: str) -> str:
    """§11.9 runtime-pack zip name: ``lausu-runtime-pack-<n>-<triple>.zip``.

    The zip is the platform-agnostic container, every platform ships a
    ``.zip`` (the pack's internal layout differs, not the container).
    """
    _check_pack_version(pack_version)
    _check_triple(triple)
    return f"lausu-runtime-pack-{pack_version}-{triple}.zip"


def pack_manifest_name() -> str:
    """§11.9 integrity-manifest release-asset name (platform-agnostic)."""
    return "pack-manifest.json"


def full_offline_installer_name(app_version: str, triple: str) -> str:
    """§11.9 addendum full-offline installer name.

    The full-offline installer bundles the slim core + a runtime pack,
    so its name mirrors the slim-core scheme with the
    ``full-offline`` product token.
    """
    _check_app_version(app_version)
    _check_triple(triple)
    return f"lausu-full-offline-{app_version}-{triple}{_exe_suffix(triple)}"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: print the canonical artifact name for the requested product.

    Examples::

        python scripts/build/artifact_names.py --slim-core --app-version 1.0.0 --triple x86_64-pc-windows-msvc
        python scripts/build/artifact_names.py --runtime-pack --pack-version 3 --triple aarch64-unknown-linux-gnu
        python scripts/build/artifact_names.py --pack-manifest
    """
    parser = argparse.ArgumentParser(
        description="Print the canonical §11.9 release-artifact name.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slim-core", action="store_true", help="slim-core installer name")
    group.add_argument("--runtime-pack", action="store_true", help="runtime-pack zip name")
    group.add_argument("--full-offline", action="store_true", help="full-offline installer name")
    group.add_argument("--pack-manifest", action="store_true", help="pack-manifest.json name")
    parser.add_argument("--app-version", help="app version (X.Y.Z) for installer names")
    parser.add_argument("--pack-version", help="pack version (integer) for the runtime-pack name")
    parser.add_argument("--triple", help="target triple for platform-suffixed names")
    args = parser.parse_args(argv)

    try:
        if args.slim_core:
            _require(args.app_version, "--app-version")
            _require(args.triple, "--triple")
            print(slim_core_installer_name(args.app_version, args.triple))
        elif args.runtime_pack:
            _require(args.pack_version, "--pack-version")
            _require(args.triple, "--triple")
            print(runtime_pack_name(args.pack_version, args.triple))
        elif args.full_offline:
            _require(args.app_version, "--app-version")
            _require(args.triple, "--triple")
            print(full_offline_installer_name(args.app_version, args.triple))
        else:
            print(pack_manifest_name())
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _require(value: str | None, flag: str) -> None:
    if not value:
        raise ValueError(f"{flag} is required for this artifact type")


if __name__ == "__main__":
    raise SystemExit(main())
