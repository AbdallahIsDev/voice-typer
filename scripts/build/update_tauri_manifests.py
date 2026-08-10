#!/usr/bin/env python3
"""Update the Tauri host binary SHA-256 manifest after a cargo build.

The Tauri cutover ships a native host binary (``voice-typer-tauri``,
built from ``src-tauri/Cargo.toml``) that the autostart launcher spawns
at login. ``tauri-binaries.json`` is the integrity manifest the launcher
verifies against (fail-closed), so production builds MUST record the
real SHA-256 of the cargo-built binary per (platform, arch).

This script is invoked by CI after each platform's ``cargo tauri build``:

::

    # record the hash of this leg's binary (idempotent):
    python3 scripts/build/update_tauri_manifests.py --triple x86_64-unknown-linux-gnu

    # enforce this leg's hash is present + valid (fail-closed for release):
    python3 scripts/build/update_tauri_manifests.py --check --triple x86_64-unknown-linux-gnu

    # enforce the WHOLE manifest (all platforms; run post-merge in the
    # tauri-build.yml aggregate job, NEVER on a dev checkout):
    python3 scripts/build/update_tauri_manifests.py --check

Manifest key resolution
-----------------------

The manifest's per-arch ``sha256`` keys are derived from the Rust target
triple via :data:`TRIPLE_TO_MANIFEST_KEY` — the SAME mapping
``tests/tauri/test_config_script_drift.py`` pins against the manifest
(and the stub generator's ``SIDECAR_TRIPLES``), so a new triple forces
this map and the drift test to move together. macOS collapses both
darwin triples into the single ``macos`` key (universal Mach-O binary);
``--triple universal-apple-darwin`` maps to it too.

Binary discovery
----------------

- Linux:    ``<target-dir>/<triple>/release/voice-typer-tauri``
- Windows:  ``<target-dir>/<triple>/release/voice-typer-tauri.exe``
- macOS:    the inner executable of the unique
  ``<target-dir>/<triple>/release/bundle/macos/*.app/Contents/MacOS/``
  bundle (productName = APP_NAME). Requires exactly one matching
  ``.app`` in the bundle dir.

``--binary PATH`` bypasses discovery (used when the workflow knows the
exact path, e.g. the macOS universal job).

Exit codes
----------

0  success
1  misuse / unknown triple / binary not found / --check found violations

Design notes
------------

- Idempotent: running twice produces the same manifest.
- Never deletes manifest entries — only rewrites the ``sha256`` sub-key
  that belongs to the given triple. Other platforms' keys (populated by
  other CI legs) are left untouched.
- Preserves the ``version`` / ``min_proto_version`` / ``_install_paths``
  fields of each entry and every top-level key verbatim.
- ``--check`` validates 64-char lowercase hex values and rejects empty
  sub-keys (release builds must never ship an unhashed binary). Without
  ``--triple`` it validates the ENTIRE manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("update_tauri_manifests")

# The Cargo binary name — MUST stay in lockstep with the ``package.name``
# in src-tauri/Cargo.toml (drift-pinned by
# tests/tauri/test_config_script_drift.py::TestTauriBinariesManifestBinaryNames).
_BINARY_NAME = "voice-typer-tauri"

# Rust target triple → tauri-binaries.json per-arch sha256 key. The SAME
# mapping as tests/tauri/test_config_script_drift.py::TRIPLE_TO_MANIFEST_KEY
# (that test imports this constant from the drift-file copy; the updater
# contract in the manifest's ``_updater_contract`` pins them together).
# macOS ships a universal Mach-O, so both darwin triples (and the
# universal-apple-darwin build triple) collapse into the single ``macos``
# key.
TRIPLE_TO_MANIFEST_KEY: dict[str, str] = {
    "x86_64-pc-windows-msvc": "windows-x86_64",
    "aarch64-pc-windows-msvc": "windows-aarch64",
    "x86_64-apple-darwin": "macos",
    "aarch64-apple-darwin": "macos",
    "universal-apple-darwin": "macos",
    "x86_64-unknown-linux-gnu": "linux-x86_64",
    "aarch64-unknown-linux-gnu": "linux-aarch64",
}

# Triple → platform family (drives the discovery path shape).
_TRIPLE_PLATFORM: dict[str, str] = {
    "x86_64-pc-windows-msvc": "windows",
    "aarch64-pc-windows-msvc": "windows",
    "x86_64-apple-darwin": "macos",
    "aarch64-apple-darwin": "macos",
    "universal-apple-darwin": "macos",
    "x86_64-unknown-linux-gnu": "linux",
    "aarch64-unknown-linux-gnu": "linux",
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256_of(path: Path) -> str:
    """Return the lowercase hex SHA-256 of ``path``'s bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_binary(target_dir: Path, triple: str) -> Path | None:
    """Locate the cargo-built ``voice-typer-tauri`` for ``triple``.

    Returns ``None`` when the binary does not exist on disk (dev
    checkout, or this leg hasn't built it), and raises ``ValueError`` on
    ambiguous matches (e.g. multiple ``.app`` bundles on macOS).
    """
    platform = _TRIPLE_PLATFORM[triple]
    triple_dir = target_dir / triple / "release"
    if platform == "windows":
        cand = triple_dir / f"{_BINARY_NAME}.exe"
        return cand if cand.is_file() else None
    if platform == "linux":
        cand = triple_dir / _BINARY_NAME
        return cand if cand.is_file() else None
    # macOS: unique .app bundle whose inner executable is the Cargo bin.
    macos_dir = triple_dir / "bundle" / "macos"
    if not macos_dir.is_dir():
        return None
    matches: list[Path] = []
    for app_dir in sorted(macos_dir.glob("*.app")):
        exe = app_dir / "Contents" / "MacOS" / _BINARY_NAME
        if exe.is_file():
            matches.append(exe)
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous macOS discovery for {triple}: multiple .app bundles "
            f"containing {_BINARY_NAME}: {matches}. Use --binary to disambiguate."
        )
    return matches[0] if matches else None


def _load_manifest(manifest_path: Path) -> dict:
    """Load and validate the manifest. Raises on parse failure."""
    text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(text)
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest at {manifest_path} is not a JSON object")
    binaries = manifest.get("binaries")
    if not isinstance(binaries, dict):
        raise ValueError(f"manifest at {manifest_path} has no 'binaries' object")
    return manifest


def _write_manifest(manifest_path: Path, manifest: dict) -> None:
    """Write ``manifest`` back to ``manifest_path`` with stable formatting."""
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    manifest_path.write_text(text, encoding="utf-8")


def _find_entry(manifest: dict, key: str) -> dict | None:
    """Return the binary entry whose sha256 dict contains ``key``, or None."""
    for entry in manifest["binaries"].values():
        sha = entry.get("sha256")
        if isinstance(sha, dict) and key in sha:
            return entry
    return None


def record_sha256(
    manifest_path: Path,
    target_dir: Path,
    triple: str,
    binary: Path | None = None,
) -> str:
    """Hash the built binary for ``triple`` and write it into the manifest.

    Returns the sha256 that was written. Raises ``FileNotFoundError``
    when the binary cannot be found, ``KeyError`` for an unknown triple,
    and ``ValueError`` for manifest/entry problems.
    """
    key = TRIPLE_TO_MANIFEST_KEY[triple]  # KeyError → unknown triple
    if binary is None:
        binary = discover_binary(target_dir, triple)
        if binary is None:
            raise FileNotFoundError(
                f"no binary found for triple {triple} under {target_dir} "
                f"(looked for {_BINARY_NAME} in {target_dir / triple / 'release'})"
            )
    if not binary.is_file():
        raise FileNotFoundError(f"binary not found: {binary}")
    log.info("hashing %s → %s", binary, key)
    sha = _sha256_of(binary)
    manifest = _load_manifest(manifest_path)
    entry = _find_entry(manifest, key)
    if entry is None:
        raise ValueError(
            f"manifest has no sha256 sub-key {key!r} — the manifest must "
            "declare every per-arch key (drift test Pair 2)."
        )
    entry["sha256"][key] = sha
    _write_manifest(manifest_path, manifest)
    log.info("recorded %s → %s", key, sha)
    return sha


def check_manifest(manifest_path: Path, triple: str | None = None) -> list[str]:
    """Return a list of violations, empty iff the manifest is valid.

    With ``triple``, only that triple's own key is validated (used by the
    per-leg enforce step right after the record step — other platforms'
    keys may legitimately still be empty mid-build). Without it, every
    ``sha256`` sub-key must be non-empty 64-char lowercase hex (the
    full-manifest release gate, run post-merge in the aggregate job).
    """
    violations: list[str] = []
    manifest = _load_manifest(manifest_path)
    if triple is not None:
        key = TRIPLE_TO_MANIFEST_KEY[triple]
        for name, entry in manifest["binaries"].items():
            sha = entry.get("sha256")
            if isinstance(sha, dict) and key in sha:
                value = sha[key]
                if not _HEX64.match(value):
                    violations.append(
                        f"{name}.sha256[{key}] invalid: {value!r} (expected non-empty 64-char lowercase hex)"
                    )
                return violations
        violations.append(f"no manifest entry declares sha256 sub-key {key!r}")
        return violations
    for name, entry in manifest["binaries"].items():
        sha = entry.get("sha256")
        if not isinstance(sha, dict):
            violations.append(f"{name}.sha256 is not a per-arch dict")
            continue
        for key, value in sha.items():
            if not _HEX64.match(value):
                violations.append(f"{name}.sha256[{key}] invalid: {value!r} (expected non-empty 64-char lowercase hex)")
    return violations


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[update_tauri_manifests] %(levelname)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="update_tauri_manifests.py",
        description=(
            "Record the SHA-256 of the built voice-typer-tauri binary into "
            "tauri-binaries.json, and/or enforce the manifest's integrity."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="path to tauri-binaries.json (default: <repo-root>/tauri-binaries.json)",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        help="cargo target directory (default: <repo-root>/src-tauri/target)",
    )
    parser.add_argument(
        "--triple",
        help="Rust target triple (required for record mode; scopes --check)",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        help="explicit path to the built binary (bypasses discovery)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify mode: never write; exit 1 on any violation. Without "
        "--triple, validates the ENTIRE manifest (all platforms).",
    )
    args = parser.parse_args(argv[1:])

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent
    manifest_path = args.manifest.resolve() if args.manifest else (project_root / "tauri-binaries.json").resolve()
    target_dir = args.target_dir.resolve() if args.target_dir else (project_root / "src-tauri" / "target").resolve()

    if not manifest_path.is_file():
        log.error("manifest not found: %s", manifest_path)
        return 1

    try:
        if args.check:
            if args.triple:
                # Per-leg enforce: scope-check this leg's key.
                violations = check_manifest(manifest_path, triple=args.triple)
            else:
                # Full-manifest release gate (only valid post-merge).
                violations = check_manifest(manifest_path)
        else:
            if args.triple is None:
                log.error("record mode requires --triple TRIPLE")
                return 1
            record_sha256(manifest_path, target_dir, args.triple, binary=args.binary)
            violations = check_manifest(manifest_path, triple=args.triple)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        log.error("failed: %s", exc)
        return 1
    except FileNotFoundError as exc:
        log.error("failed: %s", exc)
        return 1

    if violations:
        for violation in violations:
            log.error("violation: %s", violation)
        log.error(
            "manifest integrity check FAILED (%d violation(s)) — refusing to mark the build as release-ready.",
            len(violations),
        )
        return 1
    if args.check:
        scope = f" for triple {args.triple}" if args.triple else ""
        log.info("manifest integrity check PASSED%s.", scope)
    else:
        log.info("recorded sha256 for %s and check PASSED.", args.triple)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
