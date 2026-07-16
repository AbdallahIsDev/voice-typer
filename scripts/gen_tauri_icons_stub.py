#!/usr/bin/env python3
"""Generate placeholder PNG icons + binary stubs for the Tauri build.

WHY THIS EXISTS
---------------
``src-tauri/tauri.conf.json`` references 4 PNG icons, 1 ``externalBin``
entry (which Tauri expands into 6 per-arch sidecar binaries at build
time) and 9 resource files (3 native hotkey binaries + 6 prewarm
binaries). On a clean checkout NONE of those files exist, so
``cargo tauri build`` fails immediately with::

    error: failed to open icon 'icons/32x32.png'
    error: resource path 'bin/python-sidecar-x86_64-unknown-linux-gnu' doesn't exist

This script writes minimal, valid placeholder files so the packaging
dry-run succeeds. The stubs are intentionally NOT real — every stub
sidecar / native / prewarm binary prints a clear ``STUB: not a real
sidecar`` message to stderr and exits 1, so anyone who accidentally
tries to run the app against the stubs fails loudly rather than
silently shipping a broken build.

WHAT IS GENERATED
-----------------
1. **PNG icons** under ``src-tauri/icons/``:
   - ``32x32.png``, ``128x128.png``, ``128x128@2x.png`` (256x256),
     ``icon.png`` (512x512).
   - Solid dark-blue color (#1a1a2e). Minimal valid PNG: signature +
     IHDR + IDAT (zlib-compressed) + IEND. Uses only stdlib ``struct``
     + ``zlib`` (no Pillow required).
2. **Sidecar binaries** under ``src-tauri/bin/``:
   - ``python-sidecar-<triple>[.exe]`` for the 6 target triples
     referenced by ADR-0020 (Win x86_64/aarch64, macOS x86_64/aarch64,
     Linux x86_64/aarch64).
   - POSIX stubs are ``#!/bin/sh`` scripts that print
     ``STUB: not a real sidecar`` to stderr and ``exit 1`` (chmod +x'd).
   - Windows stubs are batch-file content written with the ``.exe``
     extension; Windows ``CreateProcess`` will reject them as
     ``ERROR_BAD_EXE_FORMAT`` at runtime (loud failure).
3. **Native hotkey resources** under ``src-tauri/resources/native/``:
   - ``windows-key-listener.exe``, ``macos-key-listener``,
     ``linux-key-listener``. Same stub-content strategy as sidecars.
4. **Prewarm resources** under ``src-tauri/resources/``:
   - ``prewarm-<triple>[.exe]`` for the same 6 triples.

USAGE
-----
::

    python scripts/gen_tauri_icons_stub.py           # generate all stubs
    python scripts/gen_tauri_icons_stub.py --check   # exit 0 if all present, 1 if missing (CI)
    python scripts/gen_tauri_icons_stub.py --clean   # remove generated stubs

Stubs MUST be replaced with real artifacts (Nuitka-built sidecar,
compiled native binaries, frozen prewarm) before any release build —
see ``docs/migration/tauri-build-runbook.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import struct
import sys
import zlib
from pathlib import Path

# ─── Path resolution ──────────────────────────────────────────────────────
# This script lives at <repo-root>/scripts/gen_tauri_icons_stub.py
SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
SRC_TAURI = PROJECT_ROOT / "src-tauri"

# ─── Configuration (must match src-tauri/tauri.conf.json) ─────────────────
# bundle.icon list — (relative-to-src-tauri path, width, height)
ICONS: list[tuple[str, int, int]] = [
    ("icons/32x32.png", 32, 32),
    ("icons/128x128.png", 128, 128),
    ("icons/128x128@2x.png", 256, 256),
    ("icons/icon.png", 512, 512),
]

# Target triples (6 per-arch builds — ADR-0020 §4.1 + §5)
SIDECAR_TRIPLES: list[str] = [
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
]
WINDOWS_TRIPLES: set[str] = {
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
}

# externalBin base name — Tauri appends -<triple>[.exe]
SIDECAR_BASENAME = "python-sidecar"

# bundle.resources — native hotkey binaries
NATIVE_RESOURCES: list[tuple[str, str]] = [
    ("resources/native/windows-key-listener.exe", "windows"),
    ("resources/native/macos-key-listener", "darwin"),
    ("resources/native/linux-key-listener", "linux"),
]

# bundle.resources — prewarm binaries (one per triple)
PREWARM_RESOURCES: list[tuple[str, str]] = [
    (
        f"resources/prewarm-{triple}.exe" if triple in WINDOWS_TRIPLES else f"resources/prewarm-{triple}",
        "windows" if triple in WINDOWS_TRIPLES else "unix",
    )
    for triple in SIDECAR_TRIPLES
]

# Placeholder brand color (dark blue) for the PNG icons.
STUB_COLOR_RGB: tuple[int, int, int] = (0x1A, 0x1A, 0x2E)

# Marker string embedded in every binary stub so --clean can identify
# them safely (won't delete a real Nuitka/compiled binary).
STUB_MARKER = "STUB: not a real sidecar"


# ─── PNG generation (stdlib only — no Pillow) ─────────────────────────────
def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a PNG chunk: 4-byte length + 4-byte type + data + 4-byte CRC32."""
    return (
        struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Generate a minimal valid PNG file (solid color, 8-bit RGBA, no interlace).

    Layout: signature + IHDR + IDAT (zlib-compressed raw scanlines with
    filter byte 0 per row) + IEND. Uses only stdlib ``struct`` + ``zlib``.

    Tauri v2 ``generate_context!()`` requires RGBA (color type 6) for the
    bundle icons — RGB (color type 2) is rejected with "icon ... is not RGBA".
    """
    signature = b"\x89PNG\r\n\x1a\n"

    # IHDR: width, height, bit_depth=8, color_type=6 (RGBA),
    # compression=0 (deflate), filter=0 (adaptive), interlace=0 (none).
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = _png_chunk(b"IHDR", ihdr_data)

    # IDAT: each scanline is prefixed with a single filter byte (0 = None),
    # followed by width * 4 bytes of RGBA data (alpha = 0xFF = opaque).
    r, g, b = rgb
    row = b"\x00" + bytes([r, g, b, 0xFF]) * width
    raw = row * height
    idat = _png_chunk(b"IDAT", zlib.compress(raw, 9))

    # IEND: empty data.
    iend = _png_chunk(b"IEND", b"")

    return signature + ihdr + idat + iend


# ─── Stub binary content ──────────────────────────────────────────────────
def _stub_content(platform: str, kind: str) -> bytes:
    """Build stub script content for the given platform + kind.

    ``kind`` is ``"sidecar"``, ``"native"``, or ``"prewarm"`` — included
    in the stderr message so the failure is self-describing.
    """
    msg = f"{STUB_MARKER} ({kind})"
    if platform == "windows":
        # Batch-file content written with .exe extension. Windows
        # CreateProcess rejects non-PE files with ERROR_BAD_EXE_FORMAT
        # -> loud failure at runtime. For the packaging dry-run, Tauri
        # just bundles the bytes — extension is what matters there.
        return f"@echo off\recho {msg} >&2\rexit /b 1\r".encode("ascii")
    # POSIX shell script. chmod +x'd so a real spawn runs the script
    # and exits 1 with the marker on stderr.
    return f"#!/bin/sh\necho '{msg}' >&2\nexit 1\n".encode("ascii")


def _write_stub_file(path: Path, platform: str, kind: str) -> None:
    """Write a stub binary file at ``path`` with the right content + perms."""
    path.write_bytes(_stub_content(platform, kind))
    if platform != "windows":
        # POSIX stubs need the executable bit so a real spawn runs them.
        os.chmod(path, 0o755)


def _is_stub_file(path: Path) -> bool:
    """Heuristic: returns True if ``path`` looks like one of our stubs.

    Real binaries (Nuitka onefile, compiled C, etc.) are megabytes and
    do not contain the PNG signature or the ``STUB_MARKER`` string in
    their first 8 KB. Our PNG stubs are < 1.5 KB and start with the PNG
    signature; our binary stubs are < 200 bytes and contain the marker.
    Used by ``--clean`` so we never delete a real artifact.
    """
    if not path.exists():
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)  # 8 KB is plenty for any stub
    except OSError:
        return False
    # PNG stubs: signature.
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    # Binary stubs: contain the marker string.
    return STUB_MARKER.encode("ascii") in head


# ─── Path registry ────────────────────────────────────────────────────────
def _all_stub_paths() -> list[Path]:
    """Return every stub path this script owns (absolute, under src-tauri)."""
    paths: list[Path] = []
    for rel, _, _ in ICONS:
        paths.append(SRC_TAURI / rel)
    for triple in SIDECAR_TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        paths.append(SRC_TAURI / "bin" / f"{SIDECAR_BASENAME}-{triple}{ext}")
    for rel, _ in NATIVE_RESOURCES:
        paths.append(SRC_TAURI / rel)
    for rel, _ in PREWARM_RESOURCES:
        paths.append(SRC_TAURI / rel)
    return paths


# ─── Commands ─────────────────────────────────────────────────────────────
def generate() -> list[Path]:
    """Generate all stubs. Returns the list of created file paths."""
    created: list[Path] = []

    # 1. PNG icons.
    icons_dir = SRC_TAURI / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    for rel, w, h in ICONS:
        path = SRC_TAURI / rel
        path.write_bytes(make_png(w, h, STUB_COLOR_RGB))
        created.append(path)

    # 2. Sidecar binaries (externalBin — Tauri resolves per-arch at build time).
    bin_dir = SRC_TAURI / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for triple in SIDECAR_TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        path = bin_dir / f"{SIDECAR_BASENAME}-{triple}{ext}"
        platform = "windows" if triple in WINDOWS_TRIPLES else "unix"
        _write_stub_file(path, platform, "sidecar")
        created.append(path)

    # 3. Native hotkey resources.
    native_dir = SRC_TAURI / "resources" / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    for rel, platform in NATIVE_RESOURCES:
        path = SRC_TAURI / rel
        _write_stub_file(path, platform, "native")
        created.append(path)

    # 4. Prewarm resources.
    res_dir = SRC_TAURI / "resources"
    res_dir.mkdir(parents=True, exist_ok=True)
    for rel, platform in PREWARM_RESOURCES:
        path = SRC_TAURI / rel
        _write_stub_file(path, platform, "prewarm")
        created.append(path)

    return created


def check() -> int:
    """Exit 0 if every expected stub file exists, 1 if any are missing.

    Designed for CI: a non-zero exit blocks the build pipeline.
    """
    expected = _all_stub_paths()
    missing = [p for p in expected if not p.exists()]
    if missing:
        print(
            f"[gen_tauri_icons_stub] MISSING {len(missing)}/{len(expected)} stub file(s):",
            file=sys.stderr,
        )
        for p in missing:
            print(
                f"  - {p.relative_to(PROJECT_ROOT)}",
                file=sys.stderr,
            )
        print(
            "[gen_tauri_icons_stub] Run: python scripts/gen_tauri_icons_stub.py",
            file=sys.stderr,
        )
        return 1
    print(f"[gen_tauri_icons_stub] OK: all {len(expected)} stub file(s) present.")
    return 0


def clean() -> int:
    """Remove every stub file we generated. Preserves real binaries.

    Uses ``_is_stub_file`` heuristic so a developer who already built a
    real Nuitka sidecar / compiled native binary at one of these paths
    won't lose it.
    """
    removed = 0
    skipped = 0
    for p in _all_stub_paths():
        if not p.exists():
            continue
        if not _is_stub_file(p):
            # Real artifact — preserve it.
            skipped += 1
            print(f"[gen_tauri_icons_stub] SKIP (not a stub): {p.relative_to(PROJECT_ROOT)}")
            continue
        try:
            p.unlink()
            removed += 1
        except OSError as exc:
            print(
                f"[gen_tauri_icons_stub] WARN: could not remove {p.relative_to(PROJECT_ROOT)}: {exc}",
                file=sys.stderr,
            )

    # Remove now-empty stub directories (don't remove src-tauri itself).
    for d in (
        SRC_TAURI / "icons",
        SRC_TAURI / "bin",
        SRC_TAURI / "resources" / "native",
        SRC_TAURI / "resources",
    ):
        if d.exists() and d.is_dir():
            with contextlib.suppress(OSError):
                # not empty (real artifacts left) — leave it alone
                d.rmdir()

    print(f"[gen_tauri_icons_stub] Removed {removed} stub file(s); skipped {skipped} real artifact(s).")
    return 0


def _print_summary(created: list[Path]) -> None:
    """Print a clear summary of what was generated."""
    print("[gen_tauri_icons_stub] Generated stub files:")
    for p in created:
        rel = p.relative_to(PROJECT_ROOT)
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        print(f"  - {rel}  ({size} bytes)")

    print()
    print("[gen_tauri_icons_stub] Summary:")
    print(f"  PNG icons:         {len(ICONS)}")
    print(f"  Sidecar binaries:  {len(SIDECAR_TRIPLES)}")
    print(f"  Native resources:  {len(NATIVE_RESOURCES)}")
    print(f"  Prewarm resources: {len(PREWARM_RESOURCES)}")
    print(f"  Total:             {len(created)}")
    print()
    print("[gen_tauri_icons_stub] WARNING: stubs are NOT real binaries — they print")
    print(f"  '{STUB_MARKER}' to stderr and exit 1 if executed. Replace with real")
    print("  artifacts (Nuitka sidecar, compiled native binaries, frozen prewarm)")
    print("  before any release build. See docs/migration/tauri-build-runbook.md.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate placeholder PNG icons + binary stubs for the Tauri "
            "build. Stubs are NOT real — replace with real artifacts "
            "before release."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 0 if all stubs present, 1 if any missing (for CI gates)",
    )
    mode.add_argument(
        "--clean",
        action="store_true",
        help="remove generated stub files (preserves real binaries)",
    )
    args = parser.parse_args()

    if args.check:
        return check()
    if args.clean:
        return clean()

    created = generate()
    _print_summary(created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
