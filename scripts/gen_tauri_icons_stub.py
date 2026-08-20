#!/usr/bin/env python3
"""Generate placeholder binary stubs (sidecar / native / prewarm) for the Tauri build.

WHY THIS EXISTS
---------------
``src-tauri/tauri.conf.json`` references 1 ``externalBin`` entry (which
Tauri expands into 6 per-arch sidecar binaries at build time) and 9
resource files (3 native hotkey binaries + 6 prewarm binaries). On a
clean checkout NONE of those files exist, so ``cargo tauri build`` fails
immediately with::

    error: resource path 'bin/python-sidecar-x86_64-unknown-linux-gnu' doesn't exist

This script writes minimal, valid placeholder binaries so the packaging
dry-run succeeds. The stubs are intentionally NOT real — every stub
sidecar / native / prewarm binary prints a clear ``STUB: not a real
sidecar`` message to stderr and exits 1, so anyone who accidentally
tries to run the app against the stubs fails loudly rather than
silently shipping a broken build.

WHAT IS GENERATED
-----------------
1. **Sidecar binaries** under ``src-tauri/bin/``:
   - ``python-sidecar-<triple>[.exe]`` for the 6 target triples
     referenced by ADR-0020 (Win x86_64/aarch64, macOS x86_64/aarch64,
     Linux x86_64/aarch64).
   - POSIX stubs are ``#!/bin/sh`` scripts that print
     ``STUB: not a real sidecar`` to stderr and ``exit 1`` (chmod +x'd).
   - Windows stubs are batch-file content written with the ``.exe``
     extension; Windows ``CreateProcess`` will reject them as
     ``ERROR_BAD_EXE_FORMAT`` at runtime (loud failure).
2. **Native hotkey resources** under ``src-tauri/resources/native/``:
   - ``windows-key-listener.exe``, ``macos-key-listener``,
     ``linux-key-listener``. Same stub-content strategy as sidecars.
3. **Prewarm resources** under ``src-tauri/resources/``:
   - ``prewarm-<triple>[.exe]`` for the same 6 triples.

Binary stubs are written ONLY when the path is absent or already a stub
(``_is_stub_file`` heuristic). A real Nuitka / compiled artifact that a
CI step or developer built at one of these paths is preserved — CI runs
the per-platform binary builds BEFORE the stub step, and clobbering them
would ship a broken (exit-1) sidecar in the installer.

ICONS ARE NOT STUBS ANYMORE
---------------------------
The icons under ``src-tauri/icons/`` (``32x32.png``, ``128x128.png``,
``128x128@2x.png``, ``icon.png``, ``icon.ico``, ``icon.icns``) are REAL,
git-committed files generated once with ``tauri icon`` from
``voice_typer/client/scripts/logo.svg`` (the app's logo source of truth
— see ``scripts/build/generate_icon.py``). This script does NOT generate
them and ``--clean`` never touches the icons directory. Its only icon
duty is the fail-fast CI gates that structurally validate the committed
icons before each platform's ``cargo tauri build`` — ``tauri-build``
hard-fails the whole build if the platform's icon is missing or corrupt:

The gates validate at the FULL container level of real ``tauri icon``
output (magic, IHDR fields, IHDR CRC, IDAT presence, ICO blob↔entry
dimensions, the canonical ICNS chunk set) — so a stub / hand-built
icon that only mimics the file magic cannot pass as production; the
structural tests in ``tests/tauri/test_gen_tauri_icons_stub.py`` pin
the same layout against the committed files.

- ``--check-icons`` → validates EVERY file in ``tauri.conf.json``
  ``bundle.icon`` (the 4 PNGs, ``icon.ico``, ``icon.icns``) — the single
  cross-platform gate, run identically by every Tauri workflow
  (tauri-windows-build.yml / tauri-macos-build.yml /
  tauri-linux-build.yml) before ``cargo tauri build``

USAGE
-----
::

    python scripts/gen_tauri_icons_stub.py             # generate all binary stubs
    python scripts/gen_tauri_icons_stub.py --check     # exit 0 iff all stubs present AND structurally valid (CI)
    python scripts/gen_tauri_icons_stub.py --check-icons # exit 0 iff every bundle.icon file is valid (CI)
    python scripts/gen_tauri_icons_stub.py --clean     # remove generated binary stubs

Stubs MUST be replaced with real artifacts (Nuitka-built sidecar,
compiled native binaries, frozen prewarm) before any release build —
see ``docs/migration/tauri-build-runbook.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
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
# The Windows resource icon path — validated by ``--check-icons``. The
# icon files themselves are REAL committed artifacts (generated once with
# ``tauri icon`` from ``voice_typer/client/scripts/logo.svg``); this
# script only validates the .ico, it does not generate icons.
# ``tauri-build`` on Windows resolves the .exe resource icon to the first
# ``.ico`` in ``bundle.icon`` (defaulting to ``icons/icon.ico``) and
# HARD-FAILS if it is missing:
#   ``icons/icon.ico`` not found; required for generating a Windows
#   Resource file during tauri-build
# The MSI bundler likewise errors ("Couldn't find a .ico icon") when the
# icon list contains no ``.ico``.
ICO_ICON: str = "icons/icon.ico"

# The macOS bundle icon — validated by ``--check-icons``. Also a REAL
# committed artifact (see the module docstring); the macOS bundler
# copies it into the ``.app`` bundle.
ICNS_ICON: str = "icons/icon.icns"

# ─── Canonical icon dimensions (committed real files) ─────────────────────
# Expected pixel dimensions of the committed ``bundle.icon`` artifacts
# (generated with ``tauri icon`` from
# ``voice_typer/client/scripts/logo.svg``). These tables are the single
# source of truth enforced by the fail-fast CI gate (``--check-icons``)
# AND by the drift-guard tests in
# ``tests/tauri/test_gen_tauri_icons_stub.py`` (which import this
# module). A bad regeneration — e.g. ``tauri icon`` emitting a 64px
# ``32x32.png`` — must fail CI instead of shipping a wrong-sized
# window / dock / tray icon.

# bundle.icon PNGs: src-tauri-relative path -> (width, height). Every
# ``*.png`` in ``bundle.icon`` MUST have an entry here (``--check-icons``
# fails closed on an unregistered PNG) and the entry must match the
# committed file.
EXPECTED_PNG_DIMENSIONS: dict[str, tuple[int, int]] = {
    "icons/32x32.png": (32, 32),
    "icons/128x128.png": (128, 128),
    "icons/128x128@2x.png": (256, 256),
    "icons/icon.png": (512, 512),
}

# The multi-size ICO entries the window-icon embed / winres rely on.
# The committed ``icon.ico`` (from ``tauri icon``) contains all of these;
# a regenerated ICO that drops one (e.g. the 256px entry Windows uses for
# high-DPI) fails the gate.
EXPECTED_ICO_SIZES: set[tuple[int, int]] = {
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (256, 256),
}

# ICNS PNG-compressed chunk OSType -> expected pixel size. The macOS
# bundler copies the .icns into the ``.app`` bundle; a PNG chunk whose
# decoded IHDR doesn't match its ostype's canonical size is a
# corrupt/bad-regeneration signal.
EXPECTED_ICNS_CHUNK_SIZES: dict[bytes, int] = {
    b"ic07": 128,
    b"ic08": 256,
    b"ic09": 512,
    b"ic10": 1024,
    b"ic11": 32,
    b"ic12": 64,
    b"ic13": 256,
    b"ic14": 512,
}

# The legacy raw-ARGB + mask chunks ``tauri icon`` emits alongside the
# PNG set (the 16x16 is32/s8mk and 32x32 il32/l8mk pairs). Together with
# EXPECTED_ICNS_CHUNK_SIZES these form the EXACT canonical chunk set of
# real ``tauri icon`` output; ``--check-icons`` requires every one of
# them so a stub / hand-built ICNS cannot masquerade as production at
# the container level.
ICNS_LEGACY_OSTYPES: set[bytes] = {
    b"is32",  # 16x16 24-bit raw icon data
    b"s8mk",  # 16x16 8-bit mask
    b"il32",  # 32x32 24-bit raw icon data
    b"l8mk",  # 32x32 8-bit mask
}

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

# externalBin base name for the ML worker exe (Phase 2a — runtime-pack split,
# plan-runtime-pack-split §4.4). Same triple set as the sidecar.
WORKER_BASENAME = "voice-typer-worker"

# bundle.resources — native hotkey binaries
NATIVE_RESOURCES: list[tuple[str, str]] = [
    ("resources/native/windows-key-listener.exe", "windows"),
    ("resources/native/macos-key-listener", "darwin"),
    ("resources/native/linux-key-listener", "linux"),
]

# NOTE: prewarm resources DELETED 2026-08-13 — prewarm became a startup phase
# of the worker exe (plan-runtime-pack-split §6.2 Option P-1). The separate
# prewarm-<triple>[.exe] binary + its OS-level schedulers (Windows
# LogonTrigger / macOS LaunchAgent / Linux systemd) are gone. The worker
# exe is now externalBin (see WORKER_BASENAME above), NOT a bundle.resources
# entry.

# Marker string embedded in every binary stub so --clean can identify
# them safely (won't delete a real Nuitka/compiled binary).
STUB_MARKER = "STUB: not a real sidecar"

# A non-stub file at a stub path is either a REAL artifact (Nuitka
# onefile / compiled native binary — always tens of KB to tens of MB)
# or corruption (truncated write, bad checkout). Our canonical stubs
# are < 200 bytes; a file smaller than this that is not our exact stub
# is neither — ``--check`` rejects it so a truncated or corrupt stub
# cannot sail past the gate into ``cargo tauri build``.
MIN_REAL_BINARY_SIZE = 512


# ─── Icon validation (stdlib only — no Pillow) ────────────────────────────
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_chunk_types(data: bytes) -> list[bytes]:
    """Chunk type sequence of a PNG (best-effort; truncated scans stop)."""
    types: list[bytes] = []
    off = 8
    while off + 8 <= len(data):
        (chunk_len,) = struct.unpack(">I", data[off : off + 4])
        if off + 12 + chunk_len > len(data):
            break
        types.append(data[off + 4 : off + 8])
        off += 12 + chunk_len
    return types


def _png_container_problems(data: bytes, expected: tuple[int, int] | None = None) -> list[str]:
    """Deep PNG container checks (stdlib): magic, IHDR, CRC, IDAT, IEND.

    Used by ``--check-icons`` for the bundle PNGs and the PNG-compressed
    blobs inside the ICO. Enforces the container-level layout ``tauri
    icon`` emits — the committed icon set was generated with it and the
    structural tests pin that layout — so a stub / hand-built icon that
    only mimics the magic bytes cannot pass as production:

    - magic + IHDR with correct dimensions (``expected`` when given)
    - bit depth 8 / color type 6 (RGBA) — tauri-codegen's
      ``generate_context!()`` rejects non-RGBA
    - compression method 0, filter method 0, interlace 0 (tauri icon
      emits plain non-interlaced PNGs)
    - valid IHDR CRC (catches a truncated / corrupted header that a
      dimension-only check would miss)
    - an IDAT chunk between IHDR and IEND (a header-only PNG cannot be
      decoded by the ``image`` crate)
    """
    if not data.startswith(PNG_MAGIC):
        return ["is not a PNG (bad magic)"]
    if len(data) < 33:
        return [f"is too small ({len(data)} bytes) to be a PNG"]
    if data[12:16] != b"IHDR":
        return [f"first chunk is not IHDR: {data[12:16]!r}"]
    (chunk_len,) = struct.unpack(">I", data[8:12])
    if chunk_len != 13:
        return [f"IHDR chunk length {chunk_len} != 13"]
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        return [f"has a zero dimension ({width}x{height})"]
    if expected is not None and (width, height) != expected:
        return [
            f"is {width}x{height}, expected {expected[0]}x{expected[1]} "
            "(bad regeneration? the dimension table in this script is the "
            "source of truth)"
        ]
    bit_depth, color_type, compression, filter_method, interlace = data[24:29]
    if bit_depth != 8 or color_type != 6:
        return [
            f"IHDR is {bit_depth}-bit color-type-{color_type}, expected "
            "8-bit RGBA (color type 6) — what tauri icon emits"
        ]
    if compression != 0 or filter_method != 0:
        return [f"IHDR compression/filter {compression}/{filter_method}, expected 0/0"]
    if interlace != 0:
        return [f"IHDR interlace={interlace}, expected 0 (tauri icon emits non-interlaced)"]
    stored_crc = struct.unpack(">I", data[29:33])[0]
    if stored_crc != (zlib.crc32(data[12:29]) & 0xFFFFFFFF):
        return [f"IHDR CRC mismatch (stored {stored_crc:#010x})"]
    chunk_types = _png_chunk_types(data)
    if b"IDAT" not in chunk_types:
        return ["no IDAT chunk between IHDR and IEND (a header-only PNG cannot decode)"]
    if chunk_types[-1] != b"IEND":
        return ["does not end with an IEND chunk"]
    return []


def _validate_png(path: Path, expected: tuple[int, int] | None = None) -> list[str]:
    """Validate ``path`` as a PNG; return a list of problems (empty = valid).

    Deep structural check (stdlib): magic bytes, IHDR chunk (dimensions /
    bit depth / color type / compression / filter / interlace), a valid
    IHDR CRC, a present IDAT, and a terminating IEND chunk — the
    container layout ``tauri icon`` emits, and the properties
    tauri-codegen's ``image``-crate decode depends on at compile time.
    Validates every ``*.png`` in ``bundle.icon`` for the Linux CI gate
    (the .desktop-entry / package icons).

    ``expected`` — when given, the IHDR dimensions must match exactly
    (the committed files are generated at fixed sizes; a mismatched
    regeneration is caught here instead of shipping a wrong-sized icon).
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    return [f"{path} {p}" for p in _png_container_problems(data, expected)]


def _validate_icns(path: Path) -> list[str]:
    """Validate ``path`` as a macOS ``.icns``; return a list of problems.

    Mirrors the structural test in tests/tauri/test_gen_tauri_icons_stub.py:
    8-byte header (``icns`` magic + u32 BE total length), a chunk table
    (4-byte OSType + u32 BE chunk length covering the file exactly), the
    FULL canonical chunk set of real ``tauri icon`` output — all 8
    PNG-compressed chunks (ic07..ic14, each pixel size matching its
    ostype's canonical size, 8-bit RGBA, non-interlaced) plus the 4
    legacy raw-ARGB/mask chunks (is32/s8mk/il32/l8mk) — and no unknown
    chunk types. The macOS bundler copies this .icns into the ``.app``
    bundle, so a corrupt or stub-shaped file must fail the CI gate
    before ``cargo tauri build``. Chunk ORDER is deliberately not
    checked: ``tauri icon``'s icns output is not byte-deterministic
    (the ``image`` crate reorders + recompresses on every run), so only
    the SET is pinned.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    if len(data) < 8:
        return [f"{path} is too small ({len(data)} bytes) to be an ICNS"]
    if data[:4] != b"icns":
        return [f"{path} has bad icns magic: {data[:4]!r}"]
    (total,) = struct.unpack(">I", data[4:8])
    if total != len(data):
        return [f"{path} icns header length {total} != file size {len(data)}"]
    problems: list[str] = []
    offset = 8
    png_ostypes: list[bytes] = []
    ostypes: list[bytes] = []
    while offset < len(data):
        if offset + 8 > len(data):
            problems.append(f"{path} truncated chunk header at offset {offset}")
            break
        ostype = data[offset : offset + 4]
        (chunk_len,) = struct.unpack(">I", data[offset + 4 : offset + 8])
        if chunk_len < 8:
            problems.append(f"{path} chunk {ostype!r} too short: {chunk_len}")
            break
        if offset + chunk_len > len(data):
            problems.append(f"{path} chunk {ostype!r} overruns file")
            break
        payload = data[offset + 8 : offset + chunk_len]
        if payload.startswith(PNG_MAGIC):
            png_ostypes.append(ostype)
            # Decode the PNG payload's IHDR and check the pixels match
            # the chunk ostype's canonical size (EXPECTED_ICNS_CHUNK_SIZES
            # — the sizes macOS actually renders). A PNG chunk carrying
            # the wrong dimensions is a corrupt / bad-regeneration
            # signal; an unregistered PNG ostype is a fail-closed unknown.
            expected_size = EXPECTED_ICNS_CHUNK_SIZES.get(ostype)
            if expected_size is None:
                problems.append(
                    f"{path} PNG chunk {ostype!r} has no registered expected size (add it to EXPECTED_ICNS_CHUNK_SIZES)"
                )
            elif len(payload) >= 24 and payload[12:16] == b"IHDR":
                w, h = struct.unpack(">II", payload[16:24])
                if (w, h) != (expected_size, expected_size):
                    problems.append(f"{path} PNG chunk {ostype!r} is {w}x{h}, expected {expected_size}x{expected_size}")
                # Container-level: PNG payloads must be the same 8-bit
                # RGBA non-interlaced layout tauri icon emits — a stub
                # PNG with different fields must not pass as production.
                bit_depth, color_type, interlace = payload[24], payload[25], payload[28]
                if (bit_depth, color_type, interlace) != (8, 6, 0):
                    problems.append(
                        f"{path} PNG chunk {ostype!r} is {bit_depth}-bit "
                        f"color-type-{color_type} interlace-{interlace}, expected "
                        "8-bit RGBA non-interlaced"
                    )
            else:
                problems.append(f"{path} PNG chunk {ostype!r} has a truncated/undecodable IHDR")
        ostypes.append(ostype)
        offset += chunk_len
    if offset != len(data):
        problems.append(f"{path} chunks do not cover the file exactly (stopped at offset {offset})")
    # The FULL canonical set, not just the large sizes: real tauri icon
    # output carries all 8 PNG chunks + the 4 legacy raw chunks. A stub /
    # hand-built ICNS with only the big PNG chunks must fail the gate.
    missing_png = sorted(set(EXPECTED_ICNS_CHUNK_SIZES) - set(png_ostypes))
    if missing_png:
        problems.append(f"{path} missing PNG chunks {missing_png}; got {ostypes}")
    missing_legacy = sorted(ICNS_LEGACY_OSTYPES - set(ostypes))
    if missing_legacy:
        problems.append(
            f"{path} missing legacy chunks {missing_legacy} (the is32/s8mk/il32/l8mk "
            f"raw set tauri icon emits); got {ostypes}"
        )
    unknown = [o for o in ostypes if o not in EXPECTED_ICNS_CHUNK_SIZES and o not in ICNS_LEGACY_OSTYPES]
    if unknown:
        problems.append(f"{path} has unknown chunk types: {unknown}")
    return problems


def _validate_ico(path: Path) -> list[str]:
    """Validate ``path`` as a Windows ``.ico``; return a list of problems.

    Structural validation only (stdlib): ICONDIR header, entry records
    and PNG signatures at each image offset — the same properties
    tauri-codegen's ``image``-crate decode relies on at compile time.
    An empty list means the file is a valid ICO.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    if len(data) < 6:
        return [f"{path} is too small ({len(data)} bytes) to be an ICO"]
    reserved, icon_type, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or icon_type != 1:
        return [f"{path} has a bad ICONDIR header (reserved={reserved}, type={icon_type}); expected 0/1"]
    if count < 1:
        return [f"{path} declares {count} icon entries (need >= 1)"]
    problems: list[str] = []
    offset = 6
    dims: list[tuple[int, int]] = []
    for i in range(count):
        if offset + 16 > len(data):
            problems.append(f"{path} entry {i} record overruns file")
            break
        w, h, _, _, planes, bpp, size, img_off = struct.unpack("<BBBBHHII", data[offset : offset + 16])
        offset += 16
        # planes: the ICO spec says 1, but the `ico` crate (used by
        # `tauri icon`) writes 0 — Windows + the `image` crate both
        # ignore the field for PNG-compressed entries, so accept 0 or 1.
        if planes not in (0, 1) or bpp != 32:
            problems.append(f"{path} entry {i}: planes={planes} bpp={bpp} (expected 0/1 and 32)")
        if img_off + size > len(data):
            problems.append(f"{path} entry {i} image ({img_off}+{size}) overruns file")
            continue
        blob = data[img_off : img_off + size]
        dw, dh = (256 if w == 0 else w, 256 if h == 0 else h)
        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
            problems.append(f"{path} entry {i} is not PNG-compressed")
        else:
            dims.append((dw, dh))
            # The PNG blob's IHDR must agree with the entry's declared
            # size (tauri icon emits matching blobs); a mismatch is a
            # bad-regeneration / stub signal.
            if len(blob) >= 24 and blob[12:16] == b"IHDR":
                bw, bh = struct.unpack(">II", blob[16:24])
                if (bw, bh) != (dw, dh):
                    problems.append(f"{path} entry {i} PNG IHDR {bw}x{bh} does not match declared {dw}x{dh}")
            # Deep PNG checks on the blob (RGBA b8, CRC, IDAT, IEND) — a
            # stub blob that merely starts with the PNG magic cannot pass.
            for p in _png_container_problems(blob):
                problems.append(f"{path} entry {i} PNG: {p}")
    # The canonical size set the window-icon embed / high-DPI taskbar
    # rely on must all be present (see EXPECTED_ICO_SIZES).
    missing_sizes = sorted(EXPECTED_ICO_SIZES - set(dims))
    if missing_sizes:
        problems.append(
            f"{path} missing expected ICO sizes {missing_sizes}; present: {sorted(set(dims))} (bad regeneration?)"
        )
    return problems


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
    """Heuristic: returns True if ``path`` looks like one of our binary stubs.

    Real binaries (Nuitka onefile, compiled C, etc.) are megabytes and do
    not contain the ``STUB_MARKER`` string in their first 8 KB; our
    binary stubs are < 200 bytes and contain it. Used by ``--clean`` so
    we never delete a real artifact. (The icons are committed real files
    and are NOT in the stub path registry — this heuristic only applies
    to the binary paths below.)
    """
    if not path.exists():
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)  # 8 KB is plenty for any stub
    except OSError:
        return False
    return STUB_MARKER.encode("ascii") in head


def _stub_problems(path: Path, platform: str, kind: str) -> list[str]:
    """Structural problems with the file at a stub path (empty list = OK).

    A stub path may hold either our exact canonical stub bytes
    (``_stub_content`` — what ``generate`` writes) or a REAL artifact a
    CI step / developer built there. ``--check`` accepts both, but must
    fail on anything else:

    - a truncated / modified stub (STUB_MARKER present but the bytes do
      not match the canonical content),
    - an EMPTY file (a partial write — neither our stub nor a binary),
    - a tiny non-stub file (too small to be a real compiled binary).

    Real binaries are never read here: the size gate short-circuits
    before any read, so a multi-hundred-MB Nuitka sidecar costs one
    ``stat``.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"cannot stat {path.relative_to(PROJECT_ROOT)}: {exc}"]
    canonical = _stub_content(platform, kind)
    if size == len(canonical):
        # Only our exact stub bytes fit this size class — read + compare.
        try:
            data = path.read_bytes()
        except OSError as exc:
            return [f"cannot read {path.relative_to(PROJECT_ROOT)}: {exc}"]
        if data == canonical:
            return []
        if _is_stub_file(path):
            return [
                f"{path.relative_to(PROJECT_ROOT)} is a stub but does not match "
                f"the canonical {platform}/{kind} stub ({len(canonical)} bytes) — "
                "truncated or corrupt; re-run the generator to repair"
            ]
        return [
            f"{path.relative_to(PROJECT_ROOT)} is {size} bytes and not our stub — "
            "too small to be a real binary; truncated or corrupt"
        ]
    if size == 0:
        return [f"{path.relative_to(PROJECT_ROOT)} is EMPTY — truncated/corrupt (neither our stub nor a real binary)"]
    if size < MIN_REAL_BINARY_SIZE:
        if _is_stub_file(path):
            return [
                f"{path.relative_to(PROJECT_ROOT)} is a stub but {size} bytes "
                f"(expected {len(canonical)}) — truncated or corrupt"
            ]
        return [
            f"{path.relative_to(PROJECT_ROOT)} is {size} bytes and not our stub — "
            "too small to be a real compiled binary; truncated or corrupt"
        ]
    return []  # large non-stub -> real artifact (Nuitka/compiled binary)


def _write_stub_file_if_needed(path: Path, platform: str, kind: str) -> None:
    """Write a stub at ``path`` unless a REAL artifact already lives there.

    In CI the per-platform workflows build the REAL sidecar / prewarm /
    native listener binaries first and only then run
    ``gen_tauri_icons_stub.py --check || gen_tauri_icons_stub.py`` to
    fill in the OTHER platforms' placeholder stubs. An unconditional
    write would clobber those real artifacts with stub bytes and the
    produced installer would ship a broken (exit-1) sidecar.

    ``_stub_problems`` is the gate: a REAL artifact (large, marker-free)
    returns no problems and is preserved byte-for-byte; a missing file,
    a truncated/corrupt stub, an empty file or a tiny garbage file all
    return problems and are REPLACED with a fresh canonical stub — so
    the ``--check || generate`` CI idiom both detects AND heals corrupt
    stubs before ``cargo tauri build``.
    """
    if path.exists() and not _stub_problems(path, platform, kind):
        return
    _write_stub_file(path, platform, kind)


# ─── Path registry ────────────────────────────────────────────────────────
def _all_stub_specs() -> list[tuple[Path, str, str]]:
    """``(path, platform, kind)`` for every stub this script owns.

    ``platform`` is ``"windows"`` or ``"unix"`` and ``kind`` is
    ``"sidecar"`` / ``"native"`` / ``"prewarm"`` — the same values
    ``generate`` passes, so ``--check`` can byte-compare each file
    against the canonical stub it SHOULD contain. Icons are
    intentionally NOT included — they are committed real files, not
    stubs (see the module docstring).
    """
    specs: list[tuple[Path, str, str]] = []
    for triple in SIDECAR_TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        platform = "windows" if triple in WINDOWS_TRIPLES else "unix"
        specs.append((SRC_TAURI / "bin" / f"{SIDECAR_BASENAME}-{triple}{ext}", platform, "sidecar"))
        # Worker exe stubs (externalBin, parallel to the sidecar).
        specs.append((SRC_TAURI / "bin" / f"{WORKER_BASENAME}-{triple}{ext}", platform, "worker"))
    for rel, platform in NATIVE_RESOURCES:
        specs.append((SRC_TAURI / rel, platform, "native"))
    return specs


def _all_stub_paths() -> list[Path]:
    """Return every stub path this script owns (absolute, under src-tauri).

    Icons are intentionally NOT included — they are committed real files,
    not stubs (see the module docstring).
    """
    return [p for p, _, _ in _all_stub_specs()]


# ─── Commands ─────────────────────────────────────────────────────────────
def generate() -> list[Path]:
    """Generate all binary stubs. Returns the list of created file paths.

    Icons are NOT generated here — they are committed real files.
    """
    created: list[Path] = []

    # 1. Sidecar binaries (externalBin — Tauri resolves per-arch at build time).
    bin_dir = SRC_TAURI / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for triple in SIDECAR_TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        platform = "windows" if triple in WINDOWS_TRIPLES else "unix"
        # Preserve a REAL sidecar a CI step / developer already built at
        # this path — never clobber it with a placeholder stub.
        _write_stub_file_if_needed(bin_dir / f"{SIDECAR_BASENAME}-{triple}{ext}", platform, "sidecar")
        created.append(bin_dir / f"{SIDECAR_BASENAME}-{triple}{ext}")
        # Worker exe stubs (externalBin, parallel to the sidecar).
        _write_stub_file_if_needed(bin_dir / f"{WORKER_BASENAME}-{triple}{ext}", platform, "worker")
        created.append(bin_dir / f"{WORKER_BASENAME}-{triple}{ext}")

    # 2. Native hotkey resources.
    native_dir = SRC_TAURI / "resources" / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    for rel, platform in NATIVE_RESOURCES:
        path = SRC_TAURI / rel
        _write_stub_file_if_needed(path, platform, "native")
        created.append(path)

    return created


def _bundle_icon_paths() -> list[str]:
    """Return the ``bundle.icon`` entries from ``tauri.conf.json``.

    ``tauri.conf.json`` is the single source of truth for the icon set
    (the drift-guard test enforces config ↔ git lockstep), so any icon
    added to ``bundle.icon`` is automatically covered by ``--check-icons``
    on every platform. Raises ``SystemExit(1)`` with an actionable
    message when the config cannot be read or lists no icons.
    """
    conf_path = SRC_TAURI / "tauri.conf.json"
    try:
        conf = json.loads(conf_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[gen_tauri_icons_stub] cannot read {conf_path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    icons = conf.get("bundle", {}).get("icon", [])
    if not icons:
        print(
            "[gen_tauri_icons_stub] tauri.conf.json bundle.icon is empty",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    return [str(p) for p in icons]


def check_icons() -> int:
    """Exit 0 iff EVERY file in ``bundle.icon`` is present + structurally valid.

    The single cross-platform fail-fast icon gate — it replaces the old
    per-platform ``--check-ico`` / ``--check-icns`` / ``--check-png``
    flags, so every platform workflow runs the identical step. Reads the
    icon list from ``tauri.conf.json`` and dispatches each entry to the
    right structural validator by extension: ``.png`` → deep PNG checks
    + expected dimensions, ``.ico`` → ICONDIR + PNG blobs, ``.icns`` →
    the canonical chunk set. An unknown extension fails closed.

    ``tauri-build`` hard-fails the whole build when the platform's icon
    is missing or corrupt (the window-icon embed / bundler decodes it
    via the ``image`` crate), and a corrupt file would otherwise only
    surface minutes into the compile — this gate fails in milliseconds.
    """
    try:
        icons = _bundle_icon_paths()
    except SystemExit:
        return 1
    rc = 0
    for rel in icons:
        path = SRC_TAURI / rel
        rel_display = path.relative_to(PROJECT_ROOT)
        if not path.exists():
            print(f"[gen_tauri_icons_stub] MISSING {rel_display}", file=sys.stderr)
            rc = 1
            continue
        if rel.endswith(".png"):
            # Fail closed on a bundle.icon PNG with no registered expected
            # dimensions: the gate cannot tell a correct size from a bad
            # one, and the config↔table lockstep test catches the drift.
            expected = EXPECTED_PNG_DIMENSIONS.get(rel)
            if expected is None:
                print(
                    f"[gen_tauri_icons_stub] INVALID {rel_display}: no expected "
                    f"dimensions registered for {rel} — add it to "
                    "EXPECTED_PNG_DIMENSIONS",
                    file=sys.stderr,
                )
                rc = 1
                continue
            problems = _validate_png(path, expected)
            label = "PNG"
        elif rel.endswith(".ico"):
            problems = _validate_ico(path)
            label = "ICO"
        elif rel.endswith(".icns"):
            problems = _validate_icns(path)
            label = "ICNS"
        else:
            print(
                f"[gen_tauri_icons_stub] INVALID {rel_display}: unsupported "
                "bundle.icon entry (expected .png / .ico / .icns)",
                file=sys.stderr,
            )
            rc = 1
            continue
        if problems:
            print(f"[gen_tauri_icons_stub] INVALID {rel_display}:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            rc = 1
            continue
        print(f"[gen_tauri_icons_stub] OK: {rel_display} is a valid {label}.")
    if rc:
        print(
            "[gen_tauri_icons_stub] Run: python scripts/gen_tauri_icons_stub.py",
            file=sys.stderr,
        )
    return rc


def check() -> int:
    """Exit 0 iff every expected stub exists AND is structurally valid.

    Designed for CI: a non-zero exit blocks the build pipeline. Presence
    alone is not enough — a truncated or corrupt stub (partial write,
    bad checkout) would otherwise pass a presence-only ``--check`` and
    fail only inside ``cargo tauri build`` (or ship a broken bundle).
    Each path must be either the exact canonical stub bytes or a
    plausible real binary (large, marker-free); anything else — a
    truncated/mangled stub, an empty file, a tiny garbage file — fails
    here in milliseconds.
    """
    specs = _all_stub_specs()
    problems: list[str] = []
    for path, platform, kind in specs:
        if not path.exists():
            problems.append(f"{path.relative_to(PROJECT_ROOT)} MISSING")
            continue
        problems.extend(_stub_problems(path, platform, kind))
    if problems:
        print(
            f"[gen_tauri_icons_stub] {len(problems)}/{len(specs)} stub files missing or structurally invalid:",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "[gen_tauri_icons_stub] Run: python scripts/gen_tauri_icons_stub.py",
            file=sys.stderr,
        )
        return 1
    print(f"[gen_tauri_icons_stub] OK: all {len(specs)} stub files present and structurally valid.")
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

    # Remove now-empty stub directories (don't remove src-tauri itself or
    # the committed icons/ dir).
    for d in (
        SRC_TAURI / "bin",
        SRC_TAURI / "resources" / "native",
        SRC_TAURI / "resources",
    ):
        if d.exists() and d.is_dir():
            with contextlib.suppress(OSError):
                # not empty (real artifacts left) — leave it alone
                d.rmdir()

    print(f"[gen_tauri_icons_stub] Removed {removed} stub file(s); skipped {skipped} real artifacts.")
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
    print(f"  Sidecar binaries:  {len(SIDECAR_TRIPLES)}")
    print(f"  Worker binaries:   {len(SIDECAR_TRIPLES)}")
    print(f"  Native resources:  {len(NATIVE_RESOURCES)}")
    print(f"  Total:             {len(created)}")
    print()
    print("[gen_tauri_icons_stub] WARNING: stubs are NOT real binaries — they print")
    print(f"  '{STUB_MARKER}' to stderr and exit 1 if executed. Replace with real")
    print("  artifacts (Nuitka sidecar, compiled native binaries, frozen prewarm)")
    print("  before any release build. See docs/migration/tauri-build-runbook.md.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate placeholder binary stubs (sidecar / native / prewarm) "
            "for the Tauri build. Stubs are NOT real — replace with real "
            "artifacts before release. Icons are committed real files "
            "(validated by --check-icons, not generated)."
        ),
        # The icon gates were COLLAPSED into the single --check-icons flag;
        # allow_abbrev=False stops argparse from accepting the old
        # per-platform spellings (--check-ico / --check-icns / --check-png)
        # as unambiguous abbreviations of --check-icons.
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help=(
            "exit 0 iff all stubs present AND structurally valid (canonical "
            "stub bytes or a real binary), 1 if missing/corrupt (CI gates)"
        ),
    )
    mode.add_argument(
        "--check-icons",
        action="store_true",
        help=(
            "exit 0 iff every bundle.icon file (PNGs + icon.ico + icon.icns) "
            "exists and is structurally valid — the single cross-platform "
            "fail-fast gate, run by every Tauri workflow before cargo tauri build"
        ),
    )
    mode.add_argument(
        "--clean",
        action="store_true",
        help="remove generated stub files (preserves real binaries)",
    )
    args = parser.parse_args()

    if args.check:
        return check()
    if args.check_icons:
        return check_icons()
    if args.clean:
        return clean()

    created = generate()
    _print_summary(created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
