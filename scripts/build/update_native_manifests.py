#!/usr/bin/env python3
"""Update the native binary SHA-256 manifest after a compile step.

This script is invoked by ``scripts/build/compile_native.sh`` and
``scripts/build/compile_native.ps1`` as the final build step. It walks
``voice_typer/server/native/``, computes
``hashlib.sha256(path.read_bytes()).hexdigest()`` for each compiled
binary it finds, and writes the sha256 back into
``voice_typer/server/native/binaries.json``.

Manifest key resolution
-----------------------

The manifest is keyed by BOTH the arch-suffixed name
(``linux-key-listener-x86_64``, ``windows-key-listener-aarch64.exe``)
AND the legacy non-suffixed name (``linux-key-listener``,
``windows-key-listener.exe``) — see
``voice_typer/server/native/binaries.json`` for the full alias table.
``voice_typer.server.native_hotkeys.binary_path._equivalent_manifest_names``
mirrors this mapping and is consulted at runtime by
``get_expected_sha256`` to find the right entry regardless of which
form the binary on disk uses.

This script replicates that fallback logic so a freshly-compiled binary
updates BOTH its arch-suffixed entry AND its legacy alias entry (if any)
in a single pass. macOS (``macos-key-listener``) is a single universal
binary with no arch suffix — only the one entry is touched.

Usage
-----

::

    python3 scripts/build/update_native_manifests.py [NATIVE_DIR]

If ``NATIVE_DIR`` is omitted, the script defaults to
``<repo-root>/voice_typer/server/native/``. The compile scripts pass
their ``$OUT`` directory as ``$1``.

Exit codes
----------

0  success — manifest written, all binaries hashed
1  misuse / IO error / manifest unparseable

Design notes
------------

- Idempotent: running twice produces the same manifest.
- Never deletes manifest entries — only updates ``sha256`` fields for
  binaries that exist on disk. Entries for binaries not present on the
  current platform are left untouched (so a Linux compile run does not
  wipe the Windows entry populated by a prior Windows CI leg).
- Preserves the ``version`` and ``min_proto_version`` fields of each
  existing entry; only the ``sha256`` field is rewritten.
- The manifest's ``_comment`` and ``version`` top-level keys are
  preserved verbatim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger("update_native_manifests")

# ─── Manifest layout ──────────────────────────────────────────────────────
# These constants MUST match the alias table in
# voice_typer/server/native_hotkeys/binary_path.py
# (``_LEGACY_TO_ARCH_SUFFIX`` / ``_ARCH_SUFFIX_TO_LEGACY``). If those
# change, this script's alias map must be updated in lock-step.

# Legacy non-suffixed name → arch-suffixed x86_64 name (the legacy
# binary was ALWAYS the x86_64 build pre-arch-split).
_LEGACY_TO_ARCH_SUFFIX: dict[str, str] = {
    "linux-key-listener": "linux-key-listener-x86_64",
    "windows-key-listener.exe": "windows-key-listener-x86_64.exe",
}

# Reverse map: arch-suffixed x86_64 name → legacy non-suffixed name.
_ARCH_SUFFIX_TO_LEGACY: dict[str, str] = {v: k for k, v in _LEGACY_TO_ARCH_SUFFIX.items()}

# Every binary name this script knows how to hash. The manifest may
# contain additional entries (e.g. ``linux-key-listener-aarch64``) that
# we never encounter on the host platform — those entries are left
# untouched.
_KNOWN_BINARY_NAMES: frozenset[str] = frozenset(
    {
        "linux-key-listener",
        "linux-key-listener-x86_64",
        "linux-key-listener-aarch64",
        "windows-key-listener.exe",
        "windows-key-listener-x86_64.exe",
        "windows-key-listener-aarch64.exe",
        "macos-key-listener",
    }
)

# Default manifest path (relative to this script's location).
_DEFAULT_MANIFEST_REL = Path("..", "..", "voice_typer", "server", "native", "binaries.json")


def _equivalent_names(binary_name: str) -> list[str]:
    """Return the manifest keys that should be updated for ``binary_name``.

    Mirrors ``binary_path._equivalent_manifest_names`` so a compiled
    binary's sha256 lands in BOTH its arch-suffixed entry AND its legacy
    alias entry (if any). macOS returns a single-element list.
    """
    candidates: list[str] = [binary_name]
    arch_equiv = _LEGACY_TO_ARCH_SUFFIX.get(binary_name)
    if arch_equiv:
        candidates.append(arch_equiv)
    legacy_equiv = _ARCH_SUFFIX_TO_LEGACY.get(binary_name)
    if legacy_equiv:
        candidates.append(legacy_equiv)
    # De-dup while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _sha256_of(path: Path) -> str:
    """Return the lowercase hex SHA-256 of ``path``'s bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _discover_binaries(native_dir: Path) -> list[Path]:
    """Return the sorted list of compiled native binaries in ``native_dir``.

    Only files whose ``name`` is in :data:`_KNOWN_BINARY_NAMES` are
    returned — the directory also contains the ``.c`` / ``.swift``
    sources and the ``binaries.json`` manifest itself, which we must not
    hash.
    """
    if not native_dir.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(native_dir.iterdir()):
        if entry.is_file() and entry.name in _KNOWN_BINARY_NAMES:
            found.append(entry)
    return found


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
    # Stable formatting: 2-space indent, sort_keys=False (preserve the
    # hand-curated ordering), trailing newline. ascii=False so any
    # non-ASCII in the ``_comment`` field round-trips correctly.
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    manifest_path.write_text(text, encoding="utf-8")


def update_manifest(
    native_dir: Path,
    manifest_path: Path | None = None,
    binaries: Iterable[Path] | None = None,
) -> dict[str, str]:
    """Update ``manifest_path`` with sha256 hashes for ``binaries``.

    Returns a dict mapping ``binary_name → sha256`` for every binary
    that was hashed and written. Callers (notably the test suite) can
    inspect this to verify the script did the right thing.

    Parameters
    ----------
    native_dir
        Directory containing the compiled native binaries.
    manifest_path
        Path to ``binaries.json``. Defaults to
        ``<native_dir>/binaries.json``.
    binaries
        Iterable of binary paths to hash. Defaults to the result of
        :func:`_discover_binaries` on ``native_dir``. Exposed for the
        test suite so tests can pass mock binaries without touching the
        real filesystem layout.
    """
    if manifest_path is None:
        manifest_path = native_dir / "binaries.json"
    if binaries is None:
        binaries = _discover_binaries(native_dir)

    manifest = _load_manifest(manifest_path)
    binaries_section = manifest["binaries"]

    updated: dict[str, str] = {}
    for binary in binaries:
        name = binary.name
        if name not in _KNOWN_BINARY_NAMES:
            log.debug("skipping unknown file in native dir: %s", binary)
            continue
        sha = _sha256_of(binary)
        for key in _equivalent_names(name):
            entry = binaries_section.get(key)
            if not isinstance(entry, dict):
                # The manifest doesn't have an entry for this name —
                # skip rather than create one (the manifest is the
                # source of truth for which names are expected to exist).
                continue
            entry["sha256"] = sha
            updated[key] = sha
            log.info("updated %s → %s", key, sha)

    _write_manifest(manifest_path, manifest)
    return updated


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[update_native_manifests] %(levelname)s: %(message)s",
    )
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent

    if len(argv) > 2:
        log.error("usage: %s [NATIVE_DIR]", argv[0])
        return 1

    if len(argv) == 2:
        native_dir = Path(argv[1]).resolve()
    else:
        native_dir = (project_root / "voice_typer" / "server" / "native").resolve()

    if not native_dir.is_dir():
        log.error("NATIVE_DIR not found: %s", native_dir)
        return 1

    manifest_path = native_dir / "binaries.json"
    if not manifest_path.is_file():
        log.error("manifest not found: %s", manifest_path)
        return 1

    try:
        updated = update_manifest(native_dir, manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.error("failed to update manifest: %s", exc)
        return 1

    if not updated:
        log.warning("no known binaries found in %s — manifest left untouched", native_dir)
    else:
        log.info("manifest updated: %d entr(y|ies) at %s", len(updated), manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
