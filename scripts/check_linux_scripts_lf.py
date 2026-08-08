#!/usr/bin/env python3
"""Verify Linux installer scripts stay LF and bundled copies match canonical.

Why this check exists
---------------------
The maintainer scripts shipped in the .deb / .rpm bundles
(``scripts/linux/postinst`` and friends) are executed by dpkg/rpm on the
INSTALLING machine. If any of them carries a CR byte (CRLF line
endings), bash chokes on the stray ``\\r`` and the install fails with an
obscure ``$'\\r': command not found`` error.

The repo enforces LF on these files via ``.gitattributes``::

    scripts/linux/* text eol=lf
    src-tauri/resources/linux-scripts/* text eol=lf

But that rule can silently regress:

  * a contributor deletes/changes the rule, or checks the repo out with
    ``core.autocrlf`` misconfigured, and
  * a Windows editor saves the file with CRLF and it is committed as-is.

The existing pytest byte-compare (``test_linux_installer_paths.py``
asserts bundled == canonical) cannot detect a UNIFORM CRLF drift on
BOTH sides — the two files would still be byte-identical. This script
closes that gap by asserting two independent invariants:

  1. **LF-only**: no file under ``scripts/linux/`` or
     ``src-tauri/resources/linux-scripts/`` may contain a single CR
     byte (``\\r``).
  2. **Parity**: every bundled copy in
     ``src-tauri/resources/linux-scripts/`` must be byte-identical to
     its canonical source in ``scripts/linux/``.

Runs in CI (build.yml ``test`` job) and locally. Exit code: 0 if OK,
1 if violations found.

Usage:
    python scripts/check_linux_scripts_lf.py          # check
    python scripts/check_linux_scripts_lf.py --list   # list checked files
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL_DIR = _REPO_ROOT / "scripts" / "linux"
_BUNDLED_DIR = _REPO_ROOT / "src-tauri" / "resources" / "linux-scripts"


def _iter_script_files(directory: Path) -> list[Path]:
    """Return all regular files under *directory*, skipping ``__pycache__``."""
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the files that would be checked, then exit 0",
    )
    args = parser.parse_args(argv)

    violations: list[str] = []

    for label, directory in (("canonical", _CANONICAL_DIR), ("bundled", _BUNDLED_DIR)):
        if not directory.is_dir():
            violations.append(f"{label} directory missing: {directory}")
            continue
        for path in _iter_script_files(directory):
            if args.list:
                print(path)
                continue
            if b"\r" in path.read_bytes():
                violations.append(f"CRLF line endings: {path.relative_to(_REPO_ROOT)}")

    if args.list:
        return 0

    # Parity: every bundled copy must byte-match its canonical source.
    # ``relative_to`` (not ``.name``) keeps the mapping correct if the
    # bundled layout ever gains nested subdirectories.
    if _BUNDLED_DIR.is_dir():
        for bundled in _iter_script_files(_BUNDLED_DIR):
            canonical = _CANONICAL_DIR / bundled.relative_to(_BUNDLED_DIR)
            if not canonical.is_file():
                violations.append(
                    f"bundled {bundled.relative_to(_REPO_ROOT)} has no canonical "
                    f"source {canonical.relative_to(_REPO_ROOT)}"
                )
                continue
            if bundled.read_bytes() != canonical.read_bytes():
                violations.append(
                    f"bundled {bundled.relative_to(_REPO_ROOT)} differs from "
                    f"canonical {canonical.relative_to(_REPO_ROOT)} — re-sync via "
                    "`cp scripts/linux/<file> src-tauri/resources/linux-scripts/`"
                )

    if violations:
        print(f"linux-scripts LF check: {len(violations)} violation(s) found:")
        for violation in violations:
            print(f"  - {violation}")
        print(
            "Fix: normalize line endings (`dos2unix` or a CRLF-stripping editor) "
            "and re-sync bundled copies from canonical."
        )
        return 1

    print("linux-scripts LF check: OK — all Linux scripts are LF and bundled copies match canonical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
