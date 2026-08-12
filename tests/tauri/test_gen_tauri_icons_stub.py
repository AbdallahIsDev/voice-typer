"""Regression tests for ``scripts/gen_tauri_icons_stub.py``.

The script stands up the sidecar/native/prewarm binary stubs that
``cargo tauri build`` needs to even start packaging. The icons under
``src-tauri/icons/`` are REAL committed files (generated once with
``tauri icon`` from ``voice_typer/client/scripts/logo.svg``) — this
module also validates them structurally and guards config↔git lockstep.
These tests verify:

1. Generation creates every expected stub at the right path.
2. The committed icon files (PNGs + ``icon.ico`` + ``icon.icns``) are
   structurally valid (magic bytes + IHDR dimensions + container chunk
   tables; Pillow decode if Pillow happens to be installed).
3. ``--check`` exits 0 when stubs are present and non-zero when missing.
4. ``--clean`` removes every stub file we generated (and never touches
   real binaries or the committed icons).
5. Stub sidecar scripts fail loudly (exit 1 + ``STUB`` marker on stderr)
   when executed — the safety feature that prevents accidentally shipping
   stubs.
6. ``tauri.conf.json`` ``bundle.icon`` stays in lockstep with the
   git-committed icon set (config ↔ git drift guard).
7. The unified fail-fast icon gate (``--check-icons``) validates EVERY
   file in ``tauri.conf.json`` ``bundle.icon`` (4 PNGs + icon.ico +
   icon.icns) in one run — it exits 0 on the committed icons and
   rejects missing/corrupt ones, so every platform workflow runs the
   identical pre-build step.
8. CONTAINER-LEVEL structural pinning: the committed icons must match
   the exact layout real ``tauri icon`` output emits (PNG chunk
   sequence + IHDR fields + valid CRC + IDAT, the ICO entry order and
   blob↔entry dimensions, the full canonical ICNS chunk set with
   8-bit-RGBA non-interlaced PNG payloads). The synthetic stub icons
   the tests build are compared structurally against the committed
   real files so the fixtures can never drift from production.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import filelock
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"
SRC_TAURI = PROJECT_ROOT / "src-tauri"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Hint for xdist schedulers that DO respect ``xdist_group`` (loadgroup /
# loadscope). The hard cross-process serialization is the file lock in
# ``_serialize_and_cleanup`` below — xdist's default ``load`` scheduler
# does NOT strictly honor ``xdist_group`` (verified on xdist 3.8.0), so
# the marker alone is insufficient. Both are kept: the marker is a no-op
# when xdist isn't active, and the file lock covers every scheduler mode.
# (C-TEST-5: test isolation; C-STYLE-1: minimal, documented change.)
pytestmark = pytest.mark.xdist_group("gen_tauri_icons_stub")

# Cross-process lock file — lives in the per-user temp dir so concurrent
# CI runs by different users don't contend. Acquired by every test in
# this module via the autouse ``_serialize_and_cleanup`` fixture so the
# generate→read→clean cycle is atomic across xdist workers.
_LOCK_PATH = Path(tempfile.gettempdir()) / "voice-typer-gen-tauri-icons-stub.test.lock"

# Target triples (mirrors the script's SIDECAR_TRIPLES).
TRIPLES = [
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
]
WINDOWS_TRIPLES = {"x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the stub script with the given args; capture stdout/stderr/exit."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def _script_module():
    """Import ``gen_tauri_icons_stub.py`` so the tests share its canonical
    tables (``EXPECTED_PNG_DIMENSIONS`` / ``EXPECTED_ICO_SIZES`` /
    ``EXPECTED_ICNS_CHUNK_SIZES``) — the single source of truth for the
    icon dimension gates. Importing is safe: the script's top-level code
    only defines constants (the CLI runs under ``__main__``).
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_tauri_icons_stub", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _stub_paths() -> list[Path]:
    """Mirror of the script's _all_stub_paths() — every stub we expect to exist.

    Icons are NOT included: they are committed real files, not stubs.
    """
    paths: list[Path] = []
    for triple in TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        paths.append(SRC_TAURI / "bin" / f"python-sidecar-{triple}{ext}")
    paths.extend(
        [
            SRC_TAURI / "resources/native/windows-key-listener.exe",
            SRC_TAURI / "resources/native/macos-key-listener",
            SRC_TAURI / "resources/native/linux-key-listener",
        ]
    )
    for triple in TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        paths.append(SRC_TAURI / "resources" / f"prewarm-{triple}{ext}")
    return paths


@pytest.fixture(autouse=True)
def _serialize_and_cleanup():
    """Acquire a cross-process file lock for the duration of each test, then
    clean up the generated stub files.

    WHY THE LOCK: every test in this module generates + verifies stub files
    in the shared on-disk ``src-tauri/`` tree, and the post-test cleanup
    runs ``scripts/gen_tauri_icons_stub.py --clean``. Under ``pytest -n
    auto`` (xdist) parallel workers race — one worker's ``--clean`` deletes
    a PNG another worker is mid-read, surfacing as intermittent
    ``FileNotFoundError`` on the icon / sidecar / prewarm paths.

    xdist's default ``load`` scheduler does NOT strictly honor
    ``@pytest.mark.xdist_group`` (verified on xdist 3.8.0 — tests with the
    same group still landed on different workers). The module-level
    ``pytestmark = pytest.mark.xdist_group(...)`` is kept as a hint for
    schedulers that DO respect it (``loadgroup`` / ``loadscope``), but the
    file lock is the actual cross-process guarantee. It serializes every
    test in this module so the generate → read → clean cycle is atomic.

    The lock lives in the per-user temp dir, so concurrent CI runs by
    different users don't contend. ``timeout=60`` bounds the wait so a
    crashed worker can't hang the suite forever. (C-TEST-5: test isolation.)
    """
    lock = filelock.FileLock(str(_LOCK_PATH), timeout=60)
    with lock:
        # Self-heal before each test: a failed/killed earlier run can leave
        # a committed icon corrupt on disk (transient Windows write error in
        # a corrupt-writer test whose finally-restore also failed). Restore
        # every committed icon to its git bytes so each test starts clean.
        _restore_committed_icons()
        yield
        # Ensure stubs are cleaned up after each test (don't pollute the repo).
        _run("--clean")
        # Self-heal after each test too: if THIS test hit the transient
        # write failure and left an icon corrupt, the next test must not
        # read it (was the root cause of a 3-failure cascade in full-suite
        # runs). The corrupt-writer tests' own finally-restores are the
        # primary path; this is the safety net.
        _restore_committed_icons()


def test_generate_creates_all_expected_stubs():
    """The script with no args should create every expected stub file."""
    _run("--clean")  # start from a clean state
    result = _run()
    assert result.returncode == 0, f"generate failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    missing = [p for p in _stub_paths() if not p.exists()]
    assert not missing, f"missing stub files: {missing}"


def test_generate_stdout_lists_summary():
    """The generate output should clearly list what was generated + a warning."""
    _run("--clean")
    result = _run()
    assert result.returncode == 0
    assert "[gen_tauri_icons_stub]" in result.stdout
    assert "Generated stub files:" in result.stdout
    assert "Summary:" in result.stdout
    assert "WARNING" in result.stdout
    # Spot-check that every category is mentioned (binary stubs only —
    # icons are committed real files, not generated).
    assert "Sidecar binaries:" in result.stdout
    assert "Native resources:" in result.stdout
    assert "Prewarm resources:" in result.stdout


_DIM_TABLE = _script_module().EXPECTED_PNG_DIMENSIONS
_ICNS_SIZES = _script_module().EXPECTED_ICNS_CHUNK_SIZES


# ─── Committed-icon self-healing (C-TEST-5: test isolation) ──────────────
# The corrupt-writer tests deliberately overwrite the committed icons with
# broken bytes and restore them in a ``finally``. On Windows, those rapid
# write→restore cycles can hit a transient ``OSError: [Errno 22] Invalid
# argument`` (antivirus / file-lock contention) — and if the RESTORE write
# then also fails, the icon is left corrupt on disk, which cascades into
# every later test that reads it (seen as a 3-failure cluster in full-suite
# runs: corrupt-writer IndexError + Pillow decode failure).
#
# Two guards make the module robust:
#   1. ``_write_with_retry`` rides out the transient write failure window.
#   2. The autouse fixture restores every committed icon to its git bytes
#      BEFORE and AFTER each test — so a failed/killed earlier run can
#      never leave corruption behind, and one test's transient failure can
#      never poison the next.
# ``git show`` reads the object store, NOT the working tree, so the
# snapshot is authoritative even when the tree is dirty.
_COMMITTED_ICON_RELS: tuple[str, ...] = tuple(sorted(_DIM_TABLE)) + ("icons/icon.ico", "icons/icon.icns")


def _git_show_bytes(rel: str) -> bytes:
    """The git-committed bytes for a src-tauri-relative icon path."""
    res = subprocess.run(
        ["git", "show", f"HEAD:src-tauri/{rel}"],
        capture_output=True,
        cwd=str(PROJECT_ROOT),
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"cannot read committed icon {rel} via git show: "
            f"{res.stderr.decode(errors='replace').strip()}"
        )
    return res.stdout


_COMMITTED_ICON_BYTES: dict[str, bytes] = {rel: _git_show_bytes(rel) for rel in _COMMITTED_ICON_RELS}


def _write_with_retry(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, retrying transient OSErrors.

    Windows AV / file-lock contention can briefly block a write to a
    just-written committed icon (``OSError: [Errno 22]``). Retrying a few
    times with a short pause rides out the window; the last error is
    re-raised so a genuine failure is still reported.
    """
    for attempt in range(5):
        try:
            path.write_bytes(data)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.25)


def _restore_committed_icons() -> None:
    """Restore every committed icon to its git-committed bytes."""
    for rel, data in _COMMITTED_ICON_BYTES.items():
        _write_with_retry(SRC_TAURI / rel, data)


# ─── Synthetic icon containers (structurally faithful to tauri icon) ─────
# These builders fabricate icons for the red-tests with the SAME
# container layout the committed real files have (the layout `tauri
# icon` emits): 8-bit RGBA non-interlaced PNGs with a valid IHDR CRC
# and an IDAT, a PNG-in-ICO with the exact entry order
# [32,16,24,48,64,256] and blobs matching their declared sizes, and an
# ICNS with the full canonical chunk set (8 PNG + is32/s8mk/il32/l8mk).
# ``test_synthetic_icons_match_committed_container_structure`` compares
# them structurally against the committed real files so the fixtures
# can never drift from production at the container level.


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length(4 BE) + type + payload + CRC(4 BE)."""
    return (
        struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


def _synthetic_png(width: int, height: int) -> bytes:
    """A real, decodable 8-bit RGBA PNG matching tauri icon's layout."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00\x00" * width
    idat = zlib.compress(row * height)
    return PNG_MAGIC + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _synthetic_ico() -> bytes:
    """A 6-entry PNG-in-ICO in the exact order tauri icon emits."""
    order = [32, 16, 24, 48, 64, 256]
    header = struct.pack("<HHH", 0, 1, len(order))
    entries: list[bytes] = []
    body = b""
    offset = 6 + 16 * len(order)
    for size in order:
        blob = _synthetic_png(size, size)
        w = 0 if size == 256 else size  # ICO: 0 means 256px
        entries.append(struct.pack("<BBBBHHII", w, w, 0, 0, 0, 32, len(blob), offset))
        body += blob
        offset += len(blob)
    return header + b"".join(entries) + body


_ICNS_LEGACY_PAYLOAD_SIZES = ((b"is32", 12), (b"s8mk", 256), (b"il32", 48), (b"l8mk", 1024))


def _synthetic_icns() -> bytes:
    """An ICNS with the full canonical chunk set (8 PNG + 4 legacy raw).

    The legacy payload lengths mirror the committed real file (the raw
    is32/s8mk/il32/l8mk chunks are fixed-size ARGB + mask data).
    """
    png_chunks = [
        (b"ic07", 128),
        (b"ic13", 256),
        (b"ic08", 256),
        (b"ic12", 64),
        (b"ic10", 1024),
        (b"ic11", 32),
        (b"ic14", 512),
        (b"ic09", 512),
    ]
    chunks: list[bytes] = []
    for ostype, size in png_chunks:
        payload = _synthetic_png(size, size)
        chunks.append(ostype + struct.pack(">I", 8 + len(payload)) + payload)
    for ostype, n in _ICNS_LEGACY_PAYLOAD_SIZES:
        chunks.append(ostype + struct.pack(">I", 8 + n) + b"\x00" * n)
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


# ─── Container fingerprints (extracted from the committed real files) ─────
# These extract the structural properties the gates + tests pin. Used
# both to assert the committed files match the real tauri icon layout
# and to prove the synthetic fixtures are structurally identical.


def _png_fingerprint(data: bytes) -> tuple:
    """(chunk types, IHDR fields, IHDR-CRC-valid) of a PNG."""
    types: list[bytes] = []
    off = 8
    while off + 8 <= len(data):
        (ln,) = struct.unpack(">I", data[off : off + 4])
        if off + 12 + ln > len(data):
            break
        types.append(data[off + 4 : off + 8])
        off += 12 + ln
    ihdr = struct.unpack(">IIBBBBB", data[16:29])
    crc_ok = struct.unpack(">I", data[29:33])[0] == (zlib.crc32(data[12:29]) & 0xFFFFFFFF)
    return (tuple(types), ihdr, crc_ok)


def _ico_fingerprint(data: bytes) -> tuple:
    """(reserved, type, entries) — each entry is
    (declared size, planes, bpp, blob IHDR w, h, bit, color)."""
    reserved, itype, count = struct.unpack("<HHH", data[:6])
    off = 6
    entries: list[tuple] = []
    for _ in range(count):
        w, h, _, _, planes, bpp, size, img_off = struct.unpack("<BBBBHHII", data[off : off + 16])
        off += 16
        blob = data[img_off : img_off + size]
        dw = 256 if w == 0 else w
        if blob.startswith(PNG_MAGIC) and len(blob) >= 26:
            bw, bh, bit, color = struct.unpack(">IIBB", blob[16:26])
            entries.append((dw, planes, bpp, bw, bh, bit, color))
        else:
            entries.append((dw, planes, bpp, 0, 0, 0, 0))
    return (reserved, itype, tuple(entries))


def _icns_fingerprint(data: bytes) -> tuple:
    """Sorted (ostype, payload-fingerprint) pairs of an ICNS."""
    off = 8
    chunks: dict[bytes, tuple] = {}
    while off + 8 <= len(data):
        ostype = data[off : off + 4]
        (clen,) = struct.unpack(">I", data[off + 4 : off + 8])
        payload = data[off + 8 : off + clen]
        if payload.startswith(PNG_MAGIC) and len(payload) >= 27:
            w, h, bit, color, interlace = struct.unpack(">IIBBB", payload[16:27])
            chunks[ostype] = (w, h, bit, color, interlace)
        else:
            chunks[ostype] = ("raw", clen - 8)
        off += clen
    return tuple(sorted(chunks.items()))


@pytest.mark.parametrize(
    "rel, expected",
    sorted(_DIM_TABLE.items()),
    ids=[rel.split("/")[-1] for rel, _ in sorted(_DIM_TABLE.items())],
)
def test_generated_pngs_have_valid_signature_and_ihdr(rel, expected):
    """Each committed icon PNG must have the magic bytes + correct IHDR dimensions.

    The icons are committed real files generated with ``tauri icon`` —
    no generation step runs here. Expected dimensions come from the
    script's ``EXPECTED_PNG_DIMENSIONS`` table (the same table the
    ``--check-icons`` CI gate enforces), so test and gate cannot drift.
    """
    path = SRC_TAURI / rel
    assert path.is_file(), f"committed icon missing: {rel}"
    data = path.read_bytes()
    # Magic bytes.
    assert data.startswith(PNG_MAGIC), f"bad PNG magic in {rel}"
    # First chunk: length=13 (IHDR), type=IHDR.
    assert data[8:12] == b"\x00\x00\x00\x0d", f"bad IHDR length in {rel}"
    assert data[12:16] == b"IHDR", f"bad IHDR type in {rel}"
    # IHDR data: width (4 BE) + height (4 BE) + bit_depth (1) + color_type (1) + ...
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == expected, f"{rel}: IHDR says {(width, height)}, expected {expected}"
    # bit_depth=8, color_type=6 (RGBA) — Tauri v2 generate_context!()
    # requires RGBA; RGB (color_type=2) is rejected with
    # "icon ... is not RGBA".
    assert data[24] == 8, f"{rel}: expected bit_depth=8, got {data[24]}"
    assert data[25] == 6, f"{rel}: expected color_type=6 (RGBA), got {data[25]}"
    # Must end with an IEND chunk.
    assert b"IEND" in data, f"{rel}: no IEND chunk found"


def test_dimension_table_consistent_with_filename_convention():
    """Table entries must agree with the ``tauri icon`` filename convention.

    ``32x32.png`` is 32px, ``128x128.png`` is 128, ``128x128@2x.png`` is
    256 (the @2x Retina double), ``icon.png`` is the 512px source. This
    pins ``EXPECTED_PNG_DIMENSIONS`` to the naming convention so a
    typo'd table entry fails here rather than silently blessing a
    wrong-sized icon everywhere else.
    """
    table = _DIM_TABLE
    assert table["icons/32x32.png"] == (32, 32)
    assert table["icons/128x128.png"] == (128, 128)
    assert table["icons/128x128@2x.png"] == (256, 256)
    assert table["icons/icon.png"] == (512, 512)


def test_bundle_icon_pngs_match_dimension_table():
    """Every ``bundle.icon`` PNG must have a registered expected dimension.

    The ``--check-icons`` gate fails closed on an unregistered PNG (it
    cannot tell a correct size from a bad one), so adding a PNG to
    ``tauri.conf.json`` without a table entry breaks CI; a table entry
    for a PNG no longer in ``bundle.icon`` is dead weight. This keeps
    config, gate, and tests on one source of truth.
    """
    conf = json.loads((SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    config_pngs = {p for p in conf["bundle"]["icon"] if p.endswith(".png")}
    table = set(_DIM_TABLE)
    assert config_pngs == table, (
        f"bundle.icon PNGs ({sorted(config_pngs)}) must match the dimension "
        f"table ({sorted(table)}) — add/remove entries in "
        "EXPECTED_PNG_DIMENSIONS in scripts/gen_tauri_icons_stub.py"
    )


# ─── Container-level structural pinning (vs real tauri icon output) ──────


def test_committed_pngs_have_real_tauri_icon_container_layout():
    """The committed PNGs must be byte-for-byte the tauri icon container.

    Fingerprint: exactly ``[IHDR, IDAT, IEND]``, IHDR = (w, h, 8, 6, 0,
    0, 0) — 8-bit RGBA, compression/filter/interlace 0 — and a VALID
    IHDR CRC. A regeneration that changes any of these (e.g. an
    interlaced or palette PNG from a different tool) fails here.
    """
    for rel, expected in sorted(_DIM_TABLE.items()):
        types, ihdr, crc_ok = _png_fingerprint((SRC_TAURI / rel).read_bytes())
        assert types == (b"IHDR", b"IDAT", b"IEND"), f"{rel}: chunk layout {types}"
        assert ihdr == (expected[0], expected[1], 8, 6, 0, 0, 0), f"{rel}: IHDR {ihdr}"
        assert crc_ok, f"{rel}: IHDR CRC invalid"


def test_committed_ico_matches_real_tauri_icon_container():
    """Pin the committed ICO to the exact tauri icon entry layout.

    Six PNG-compressed entries in the order tauri icon emits
    (32, 16, 24, 48, 64, 256), each planes=0 bpp=32 with a blob whose
    IHDR matches the declared size and is 8-bit RGBA.
    """
    reserved, itype, entries = _ico_fingerprint((SRC_TAURI / "icons/icon.ico").read_bytes())
    assert (reserved, itype) == (0, 1)
    assert entries == (
        (32, 0, 32, 32, 32, 8, 6),
        (16, 0, 32, 16, 16, 8, 6),
        (24, 0, 32, 24, 24, 8, 6),
        (48, 0, 32, 48, 48, 8, 6),
        (64, 0, 32, 64, 64, 8, 6),
        (256, 0, 32, 256, 256, 8, 6),
    )


def test_committed_icns_matches_real_tauri_icon_container():
    """Pin the committed ICNS to the canonical tauri icon chunk set.

    All 8 PNG chunks (ic07..ic14), each with pixel size matching its
    ostype and 8-bit RGBA non-interlaced payload, plus the 4 legacy
    raw chunks (is32/s8mk/il32/l8mk). Order is NOT pinned — tauri
    icon's icns output is non-deterministic in chunk order.
    """
    chunks = dict(_icns_fingerprint((SRC_TAURI / "icons/icon.icns").read_bytes()))
    png = {o: v for o, v in chunks.items() if v[0] != "raw"}
    legacy = {o: v for o, v in chunks.items() if v[0] == "raw"}
    assert set(png) == set(_ICNS_SIZES), f"PNG chunk set {sorted(png)} != canonical"
    for ostype, (w, h, bit, color, interlace) in png.items():
        size = _ICNS_SIZES[ostype]
        assert (w, h) == (size, size), f"{ostype!r}: {w}x{h}, expected {size}x{size}"
        assert (bit, color, interlace) == (8, 6, 0), f"{ostype!r}: {bit}-bit ct{color} i{interlace}"
    assert set(legacy) == {b"is32", b"s8mk", b"il32", b"l8mk"}, f"legacy set {sorted(legacy)}"


def test_synthetic_icons_match_committed_container_structure():
    """The test-suite stub icons must be structurally identical to production.

    This is the literal "compare the generated stub icons against real
    output" guard: the synthetic fixtures (_synthetic_png/_ico/_icns)
    must carry the same chunk layout, IHDR fields, ICO entry order and
    ICNS chunk set as the committed files a real ``tauri icon`` run
    produced. If the fixtures ever drift from production — e.g. a
    future tauri icon emits a different layout and the builders aren't
    updated — this fails and the builder must be brought back in line.
    """
    committed = SRC_TAURI / "icons"
    for name, size in (
        ("32x32.png", 32),
        ("128x128.png", 128),
        ("128x128@2x.png", 256),
        ("icon.png", 512),
    ):
        assert _png_fingerprint(_synthetic_png(size, size)) == _png_fingerprint((committed / name).read_bytes()), name
    assert _ico_fingerprint(_synthetic_ico()) == _ico_fingerprint((committed / "icon.ico").read_bytes())
    assert _icns_fingerprint(_synthetic_icns()) == _icns_fingerprint((committed / "icon.icns").read_bytes())


def test_generated_ico_is_valid_windows_icon_container():
    """``icons/icon.ico`` must be a structurally valid ICO with PNG entries.

    tauri-build on Windows hard-fails without ``icons/icon.ico``
    (``required for generating a Windows Resource file during
    tauri-build``) and the MSI bundler errors with "Couldn't find a .ico
    icon" when ``bundle.icon`` has no ``.ico`` — so the committed ICO
    (produced by ``tauri icon``) must be a real ICO container decodable
    by the ``image`` crate used by tauri-codegen.
    """
    data = (SRC_TAURI / "icons/icon.ico").read_bytes()
    # ICONDIR: reserved=0 (u16 LE), type=1=icon (u16 LE), count (u16 LE).
    reserved, icon_type, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0
    assert icon_type == 1
    assert count >= 2, f"expected >= 2 ICO entries, got {count}"
    # ICONDIRENTRY records: width, height, colorCount, reserved, planes,
    # bitCount, bytesInRes, imageOffset.
    offset = 6
    dims: list[tuple[int, int]] = []
    blobs: list[tuple[int, int]] = []
    for i in range(count):
        rec = data[offset : offset + 16]
        offset += 16
        w, h, _, _, planes, bpp, size, img_off = struct.unpack("<BBBBHHII", rec)
        dims.append((256 if w == 0 else w, 256 if h == 0 else h))  # 0 means 256px
        # planes: the ICO spec says 1 but `tauri icon` (ico crate) writes 0 —
        # both are accepted by Windows + the image crate for PNG entries.
        assert planes in (0, 1) and bpp == 32, f"entry {i}: planes={planes} bpp={bpp}"
        blobs.append((img_off, size))
    # The canonical sizes tauri-build's winres + the window-icon embed
    # rely on must be present.
    assert (32, 32) in dims and (256, 256) in dims, f"missing 32x32 or 256x256 entry: {dims}"
    # Every image blob must be a PNG (tauri icon writes PNG-in-ICO).
    for img_off, size in blobs:
        blob = data[img_off : img_off + size]
        assert blob.startswith(PNG_MAGIC), f"ICO entry at offset {img_off} is not PNG-compressed"
    # All blobs must fit inside the file.
    assert max(off + size for off, size in blobs) <= len(data)


def test_generated_icns_is_valid_macos_icon_container():
    """``icons/icon.icns`` must be a structurally valid ICNS.

    The macOS bundler prefers an existing ``.icns`` in ``bundle.icon``
    (it copies it into the ``.app`` bundle), so the committed ICNS must
    be a real container — an 8-byte header (``icns`` magic + u32 BE total
    length) followed by chunks. Modern PNG-compressed chunks carry the
    icon pixels; legacy ``il32``/``is32``/``l8mk``/``s8mk`` chunks (raw
    ARGB + mask, emitted by ``tauri icon``) are also valid.
    """
    data = (SRC_TAURI / "icons/icon.icns").read_bytes()
    # Header: "icns" magic + total file length (u32 BE, includes header).
    assert data[:4] == b"icns", f"bad icns magic: {data[:4]!r}"
    (total,) = struct.unpack(">I", data[4:8])
    assert total == len(data), f"icns header length {total} != file size {len(data)}"
    # Walk the chunks: 4-byte OSType + u32 BE chunk length (8 + payload).
    offset = 8
    ostypes: list[bytes] = []
    png_ostypes: list[bytes] = []
    while offset < len(data):
        ostype = data[offset : offset + 4]
        (chunk_len,) = struct.unpack(">I", data[offset + 4 : offset + 8])
        assert chunk_len >= 8, f"icns chunk {ostype!r} too short: {chunk_len}"
        assert offset + chunk_len <= len(data), f"icns chunk {ostype!r} overruns file"
        payload = data[offset + 8 : offset + chunk_len]
        if payload.startswith(PNG_MAGIC):
            png_ostypes.append(ostype)
        ostypes.append(ostype)
        offset += chunk_len
    assert offset == len(data), "icns chunks do not cover the file exactly"
    # The canonical large-size PNG chunks must be present (ic07=128,
    # ic08=256, ic09=512, ic10=1024 — the sizes macOS actually uses).
    for expected in (b"ic07", b"ic08", b"ic09", b"ic10"):
        assert expected in png_ostypes, f"icns missing PNG chunk {expected!r}; got {ostypes}"
    # Any non-PNG chunk must be a known legacy type, not arbitrary bytes.
    legacy = {b"il32", b"is32", b"l8mk", b"s8mk", b"it32", b"t8mk"}
    unknown = [o for o in ostypes if o not in png_ostypes and o not in legacy]
    assert not unknown, f"icns has unknown chunk types: {unknown}"


def test_check_icons_validates_every_bundle_icon():
    """``--check-icons`` must validate ALL SIX bundle.icon files at once.

    The collapsed cross-platform gate reads ``bundle.icon`` from
    tauri.conf.json and validates every entry — the 4 PNGs (with their
    expected dimensions), icon.ico and icon.icns — in one run, so a
    single identical step in every workflow covers the whole set.
    """
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed icon set:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    for ok_line in (
        "32x32.png is a valid PNG",
        "128x128.png is a valid PNG",
        "128x128@2x.png is a valid PNG",
        "icon.png is a valid PNG",
        "icon.icns is a valid ICNS",
        "icon.ico is a valid ICO",
    ):
        assert ok_line in result.stdout, f"missing OK line {ok_line!r}:\n{result.stdout}"


def test_check_ico_exits_zero_on_committed_ico():
    """``--check-icons`` must pass on the committed icon.ico.

    This is the fail-fast CI gate in tauri-windows-build.yml that runs
    right before ``cargo tauri build`` — tauri-build hard-fails the whole
    build when icons/icon.ico is missing or corrupt, so the gate must
    agree with the committed artifact.
    """
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed ICO:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "valid ICO" in result.stdout


def test_check_ico_exits_nonzero_when_missing():
    """``--check-icons`` must fail when icon.ico is missing (CI gate).

    The icon is a committed file, so temporarily rename it away (and
    restore it afterwards) instead of generating — icons are never
    regenerated.
    """
    ico = SRC_TAURI / "icons" / "icon.ico"
    assert ico.exists(), "committed icon.ico missing — icon set not committed?"
    tmp = ico.with_name("icon.ico.check-missing-tmp")
    try:
        ico.rename(tmp)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail when icon.ico is missing"
        assert "MISSING" in result.stderr or "MISSING" in result.stdout
    finally:
        tmp.rename(ico)


def test_check_ico_rejects_corrupt_ico():
    """``--check-icons`` must reject structurally invalid ICO files.

    A corrupt icon.ico would pass the presence-based ``--check`` but fail
    minutes later inside tauri-build's winres / image-crate decode — the
    dedicated gate must catch it in milliseconds. The committed file is
    restored byte-for-byte afterwards.
    """
    ico = SRC_TAURI / "icons" / "icon.ico"
    original = ico.read_bytes()
    try:
        # Case 1: valid ICONDIR header but truncated entry records.
        _write_with_retry(ico, b"\x00\x00\x01\x00\x02\x00" + b"\x00" * 8)
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "INVALID" in result.stderr or "INVALID" in result.stdout

        # Case 2: well-formed header + entry record pointing at a non-PNG blob.
        blob = b"\x00" * 64  # not a PNG
        _write_with_retry(ico,
            struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(blob), 22) + blob
        )
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "not PNG-compressed" in result.stderr or "not PNG-compressed" in result.stdout
    finally:
        _write_with_retry(ico, original)


# ─── --check-icons: ICNS red-tests ───────────────────────────────────────────


def test_check_icns_exits_zero_on_committed_icns():
    """``--check-icons`` must pass on the committed icon.icns.

    This is the fail-fast CI gate in tauri-macos-build.yml that runs
    right before ``cargo tauri build`` — the macOS bundler copies the
    .icns into the .app bundle, so the gate must agree with the
    committed artifact.
    """
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed ICNS:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "valid ICNS" in result.stdout


def test_check_icns_exits_nonzero_when_missing():
    """``--check-icons`` must fail when icon.icns is missing (CI gate)."""
    icns = SRC_TAURI / "icons" / "icon.icns"
    assert icns.exists(), "committed icon.icns missing — icon set not committed?"
    tmp = icns.with_name("icon.icns.check-missing-tmp")
    try:
        icns.rename(tmp)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail when icon.icns is missing"
        assert "MISSING" in result.stderr or "MISSING" in result.stdout
    finally:
        tmp.rename(icns)


def test_check_icns_rejects_corrupt_icns():
    """``--check-icons`` must reject structurally invalid ICNS files.

    The committed file is restored byte-for-byte afterwards.
    """
    icns = SRC_TAURI / "icons" / "icon.icns"
    original = icns.read_bytes()
    try:
        # Case 1: bad magic.
        _write_with_retry(icns, b"xxxx" + struct.pack(">I", 8))
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "INVALID" in result.stderr or "INVALID" in result.stdout
        assert "bad icns magic" in result.stderr or "bad icns magic" in result.stdout

        # Case 2: valid magic but header length != file size.
        _write_with_retry(icns, b"icns" + struct.pack(">I", 9999))
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "header length" in result.stderr or "header length" in result.stdout

        # Case 3: valid magic + length but zero chunks -> missing PNG chunks.
        _write_with_retry(icns, b"icns" + struct.pack(">I", 8))
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "missing PNG chunk" in result.stderr or "missing PNG chunk" in result.stdout

        # Case 4: chunk length overruns the file (header length == file size
        # so the chunk-walk itself must catch it).
        _write_with_retry(icns, b"icns" + struct.pack(">I", 16) + b"ic07" + struct.pack(">I", 9999))
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "overruns file" in result.stderr or "overruns file" in result.stdout
    finally:
        _write_with_retry(icns, original)


# ─── --check-icons: PNG red-tests ────────────────────────────────────────────


def test_check_png_exits_zero_on_committed_pngs():
    """``--check-icons`` must pass on every committed bundle.icon PNG.

    This is the fail-fast CI gate in tauri-linux-build.yml. The PNG set
    is read from ``tauri.conf.json`` ``bundle.icon`` (single source of
    truth), so all four committed PNGs must validate.
    """
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed PNGs:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    for name in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png"):
        assert f"{name} is a valid PNG" in result.stdout, f"missing OK line for {name}:\n{result.stdout}"


def test_check_png_exits_nonzero_when_missing():
    """``--check-icons`` must fail when a bundle.icon PNG is missing (CI gate)."""
    png = SRC_TAURI / "icons" / "32x32.png"
    assert png.exists(), "committed 32x32.png missing — icon set not committed?"
    tmp = png.with_name("32x32.png.check-missing-tmp")
    try:
        png.rename(tmp)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail when a bundle.icon PNG is missing"
        assert "MISSING" in result.stderr or "MISSING" in result.stdout
        assert "32x32.png" in result.stderr or "32x32.png" in result.stdout
    finally:
        tmp.rename(png)


def test_check_png_rejects_corrupt_png():
    """``--check-icons`` must reject structurally invalid PNG files.

    The committed file is restored byte-for-byte afterwards.
    """
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        # Case 1: bad magic.
        _write_with_retry(png, b"not a png at all")
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "INVALID" in result.stderr or "INVALID" in result.stdout
        assert "bad magic" in result.stderr or "bad magic" in result.stdout

        # Case 2: valid magic + IHDR with a zero dimension.
        _write_with_retry(png,
            PNG_MAGIC
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", 0, 32)
            + bytes([8, 6, 0, 0, 0])
            + b"\x00" * 4  # CRC placeholder
        )
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "zero dimension" in result.stderr or "zero dimension" in result.stdout

        # Case 3: valid magic but first chunk is not IHDR.
        _write_with_retry(png, PNG_MAGIC + b"\x00\x00\x00\x13XXXX" + b"\x00" * 21)
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "not IHDR" in result.stderr or "not IHDR" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_png_rejects_wrong_dimensions():
    """``--check-icons`` must fail when a bundle.icon PNG has wrong dimensions.

    A bad regeneration — e.g. ``tauri icon`` emitting a 64px ``32x32.png``
    — is structurally a perfect PNG and used to sail through the gate
    (dimensions were read but never compared). The dimension table now
    makes it fail CI here instead of shipping a wrong-sized window icon.
    """
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        # A valid PNG of the WRONG size: 128x128.png is 128x128, but the
        # table (and the filename) say 32x32.png must be 32x32.
        _write_with_retry(png, (SRC_TAURI / "icons" / "128x128.png").read_bytes())
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a wrong-sized PNG"
        assert "expected 32x32" in result.stderr or "expected 32x32" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_ico_rejects_missing_expected_size():
    """``--check-icons`` must fail when the committed size set is incomplete.

    A regenerated ICO that drops one of the canonical sizes (e.g. the
    256px entry Windows uses for high-DPI taskbars) is structurally
    valid — a header + a 32x32 entry is a legal ICO — but ships a
    degraded icon. The size-set check makes it fail CI here.
    """
    ico = SRC_TAURI / "icons" / "icon.ico"
    original = ico.read_bytes()
    try:
        # Header (0/1/1) + a single 32x32 entry pointing at the committed
        # 32x32.png bytes (a real PNG, so the structural checks pass).
        blob = (SRC_TAURI / "icons" / "32x32.png").read_bytes()
        _write_with_retry(ico,
            struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(blob), 22) + blob
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on an incomplete size set"
        assert "missing expected ICO sizes" in result.stderr or "missing expected ICO sizes" in result.stdout
    finally:
        _write_with_retry(ico, original)


def test_check_icns_rejects_wrong_png_chunk_size():
    """``--check-icons`` must fail when a PNG chunk's pixels don't match its OSType.

    Build an ICNS with the FULL canonical chunk set where every chunk
    carries a VALID PNG of the right size EXCEPT ic07 (which must be
    128x128) — it gets a 32x32 PNG instead. Structurally the container
    is perfect (all 12 canonical chunks present, RGBA, valid CRCs);
    only the decoded IHDR dimensions betray it.
    """
    icns = SRC_TAURI / "icons" / "icon.icns"
    original = icns.read_bytes()
    try:
        chunks: list[bytes] = []
        for ostype, size in (
            (b"ic07", 128),
            (b"ic13", 256),
            (b"ic08", 256),
            (b"ic12", 64),
            (b"ic10", 1024),
            (b"ic11", 32),
            (b"ic14", 512),
            (b"ic09", 512),
        ):
            payload = _synthetic_png(32, 32) if ostype == b"ic07" else _synthetic_png(size, size)
            chunks.append(ostype + struct.pack(">I", 8 + len(payload)) + payload)
        for ostype, n in _ICNS_LEGACY_PAYLOAD_SIZES:
            chunks.append(ostype + struct.pack(">I", 8 + n) + b"\x00" * n)
        body = b"".join(chunks)
        _write_with_retry(icns, b"icns" + struct.pack(">I", 8 + len(body)) + body)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a wrong-sized PNG chunk"
        assert "expected 128x128" in result.stderr or "expected 128x128" in result.stdout
    finally:
        _write_with_retry(icns, original)


def test_check_icns_rejects_missing_canonical_chunk():
    """``--check-icons`` must fail when a canonical chunk is missing.

    The full chunk SET is part of the container: real tauri icon output
    carries all 8 PNG chunks + the 4 legacy raw chunks, and a stub /
    hand-built ICNS with only the large PNG chunks must not pass as
    production. Drop ic11 (the 32x32 PNG chunk) and the gate must fail.
    """
    icns = SRC_TAURI / "icons" / "icon.icns"
    original = icns.read_bytes()
    try:
        chunks: list[bytes] = []
        for ostype, size in (
            (b"ic07", 128),
            (b"ic13", 256),
            (b"ic08", 256),
            (b"ic12", 64),
            (b"ic10", 1024),
            (b"ic14", 512),
            (b"ic09", 512),
        ):
            payload = _synthetic_png(size, size)
            chunks.append(ostype + struct.pack(">I", 8 + len(payload)) + payload)
        for ostype, n in _ICNS_LEGACY_PAYLOAD_SIZES:
            chunks.append(ostype + struct.pack(">I", 8 + n) + b"\x00" * n)
        body = b"".join(chunks)
        _write_with_retry(icns, b"icns" + struct.pack(">I", 8 + len(body)) + body)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a missing canonical chunk"
        assert "ic11" in result.stderr or "ic11" in result.stdout
    finally:
        _write_with_retry(icns, original)


def test_check_png_rejects_missing_idat():
    """``--check-icons`` must fail on a header-only PNG (magic + IHDR + IEND).

    A stub PNG that only mimics the magic and header is structurally
    "a PNG" at the magic level but cannot be decoded by the image
    crate — the IDAT-presence check makes it fail the gate.
    """
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        _write_with_retry(png,
            PNG_MAGIC + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 6, 0, 0, 0)) + _png_chunk(b"IEND", b"")
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a header-only PNG"
        assert "IDAT" in result.stderr or "IDAT" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_png_rejects_interlaced():
    """``--check-icons`` must fail on an interlaced PNG.

    Interlace=1 is a legal PNG (Adam7) but tauri icon never emits it —
    a structurally perfect interlaced icon must fail the gate so a
    foreign tool's output can't silently replace the production layout.
    """
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        idat = zlib.compress((b"\x00" + b"\x00\x00\x00\x00" * 32) * 32)
        _write_with_retry(png,
            PNG_MAGIC
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 6, 0, 0, 1))
            + _png_chunk(b"IDAT", idat)
            + _png_chunk(b"IEND", b"")
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on an interlaced PNG"
        assert "interlace" in result.stderr or "interlace" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_png_rejects_corrupt_ihdr_crc():
    """``--check-icons`` must fail when the stored IHDR CRC is wrong.

    A corrupted header passes every dimension/field check — only the
    CRC check catches it, so a truncated/bit-rotted icon fails the gate
    instead of shipping.
    """
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        corrupted = bytearray(original)
        corrupted[29] ^= 0xFF  # flip a bit in the stored IHDR CRC
        _write_with_retry(png, bytes(corrupted))
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a corrupt IHDR CRC"
        assert "CRC" in result.stderr or "CRC" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_ico_rejects_blob_dimension_mismatch():
    """``--check-icons`` must fail when an entry's PNG blob doesn't match its size.

    An entry declaring 32x32 but carrying a valid 16x16 PNG is a legal
    ICO container — the blob↔entry dimension check is what betrays it.
    """
    ico = SRC_TAURI / "icons" / "icon.ico"
    original = ico.read_bytes()
    try:
        blob = _synthetic_png(16, 16)  # valid PNG, wrong size for the entry
        _write_with_retry(ico,
            struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(blob), 22) + blob
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a blob/entry dimension mismatch"
        assert "does not match declared" in result.stderr or "does not match declared" in result.stdout
    finally:
        _write_with_retry(ico, original)


@pytest.mark.real_pil
def test_generated_pngs_decode_with_pillow_if_available():
    """If Pillow is installed, the PNGs must decode to the expected size.

    Marked ``real_pil`` because the project-wide ``tests/conftest.py``
    auto-mocks PIL by default; this test needs the real ``PIL.Image`` to
    actually decode the PNG bytes.
    """
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed — skipping decode check")
    _run()
    for rel, expected in sorted(_DIM_TABLE.items()):
        path = SRC_TAURI / rel
        with Image.open(path) as img:
            assert img.size == expected, f"{rel}: Pillow read {img.size}"


def test_check_exits_zero_when_stubs_present():
    """``--check`` must exit 0 when all stubs are present."""
    _run()  # generate
    result = _run("--check")
    assert result.returncode == 0, f"--check should pass:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout


def test_check_exits_nonzero_when_stubs_missing():
    """``--check`` must exit non-zero when stubs are missing (CI gate)."""
    _run("--clean")
    result = _run("--check")
    assert result.returncode != 0, "--check should fail when stubs are missing"
    assert "MISSING" in result.stderr or "MISSING" in result.stdout


def test_clean_removes_generated_stubs():
    """``--clean`` must remove every stub file the script generated.

    In the dev/CI environment there are no real binaries at any stub
    path, so every file we just generated must be gone after ``--clean``.
    (The ``test_clean_does_not_remove_real_binary`` test separately
    verifies that a planted real binary IS preserved.)
    """
    _run()  # generate
    result = _run("--clean")
    assert result.returncode == 0, f"--clean failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    leftover = [p for p in _stub_paths() if p.exists()]
    assert not leftover, f"--clean left stub files behind: {[str(p) for p in leftover]}"


def test_clean_does_not_remove_real_binary():
    """``--clean`` must preserve a real (large, marker-free) binary at a stub path."""
    _run()  # generate stubs first
    # Plant a fake "real" binary at one of the sidecar paths: > 8 KB and
    # contains neither the PNG signature nor the STUB_MARKER string.
    real_binary_path = SRC_TAURI / "bin" / "python-sidecar-x86_64-unknown-linux-gnu"
    real_binary_path.write_bytes(b"\x7fELF" + b"\x00" * 32768)  # 32 KB, no marker
    os.chmod(real_binary_path, 0o755)

    result = _run("--clean")
    assert result.returncode == 0
    # The real binary must still exist.
    assert real_binary_path.exists(), "--clean deleted a real binary — heuristic failed"
    # But the other stubs must be gone.
    other_stub = SRC_TAURI / "bin" / "python-sidecar-x86_64-apple-darwin"
    assert not other_stub.exists(), "--clean did not remove a sibling stub"
    # Clean up the planted real binary ourselves — ``--clean`` is
    # deliberately a no-op on real artifacts, and later tests must not
    # see it at a stub path (generate() preserves real binaries, so it
    # would otherwise leak into the marker-content tests).
    real_binary_path.unlink()


def test_generate_preserves_existing_real_binary():
    """``generate`` must NOT clobber a real artifact with a placeholder stub.

    CI builds the REAL sidecar / prewarm / native binaries first and only
    then runs ``gen_tauri_icons_stub.py --check || gen_tauri_icons_stub.py``
    to fill in the other platforms' stubs. If ``generate`` overwrote the
    real per-platform binaries, the produced installer would bundle a
    stub (exit-1) sidecar. A planted real binary (no PNG signature / no
    STUB_MARKER in the first 8 KB) must survive ``generate`` byte-for-byte
    while the sibling stubs are still created.
    """
    _run("--clean")  # start clean
    # Plant a fake "real" binary at the host-arch sidecar path.
    real_path = SRC_TAURI / "bin" / "python-sidecar-x86_64-pc-windows-msvc.exe"
    real_content = b"\x4d\x5a" + b"\x00" * 32768  # MZ header, 32 KB, no marker
    real_path.write_bytes(real_content)

    result = _run()  # generate
    assert result.returncode == 0
    # The real binary must be untouched.
    assert real_path.read_bytes() == real_content, "generate() overwrote a real binary — must preserve it"
    # The sibling stubs must still be created.
    assert (SRC_TAURI / "bin" / "python-sidecar-x86_64-apple-darwin").exists()
    assert (SRC_TAURI / "resources" / "native" / "linux-key-listener").exists()
    # Clean up the planted real binary ourselves (same rationale as
    # test_clean_does_not_remove_real_binary — --clean preserves it).
    real_path.unlink()


def test_check_and_clean_are_mutually_exclusive():
    """``--check --clean`` together must fail (mutually exclusive group).."""
    result = _run("--check", "--clean")
    assert result.returncode != 0, "argparse should reject --check + --clean together"
    # The consolidated icon gate lives in the same exclusive group.
    result = _run("--check-icons", "--check")
    assert result.returncode != 0, "argparse should reject --check-icons + --check together"
    result = _run("--check-icons", "--clean")
    assert result.returncode != 0, "argparse should reject --check-icons + --clean together"


def test_old_per_platform_icon_flags_are_rejected():
    """The collapsed gates must NOT survive as argparse abbreviations.

    ``--check-ico`` / ``--check-icns`` / ``--check-png`` were merged into
    the single ``--check-icons`` flag; ``allow_abbrev=False`` stops
    argparse from silently accepting the old spellings as prefixes of
    ``--check-icons`` (which would resurrect a per-platform gate in the
    workflows / docs).
    """
    for old_flag in ("--check-ico", "--check-icns", "--check-png"):
        result = _run(old_flag)
        assert result.returncode != 0, f"{old_flag} should be rejected (collapsed into --check-icons)"
        assert "unrecognized arguments" in result.stderr, f"{old_flag}: {result.stderr}"


def test_check_icons_rejects_unsupported_bundle_icon_extension():
    """``--check-icons`` must fail closed on an unsupported bundle.icon entry.

    A non-.png/.ico/.icns entry cannot be validated by any structural
    validator — the gate must reject it instead of silently skipping it.
    The config is restored byte-for-byte afterwards.
    """
    conf = SRC_TAURI / "tauri.conf.json"
    fake = SRC_TAURI / "icons" / "logo.svg"
    original = conf.read_bytes()
    try:
        # The file must EXIST: the gate reports MISSING for absent entries
        # before it ever looks at the extension (both fail closed, but
        # this test exercises the unsupported-extension branch).
        fake.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        data = json.loads(original)
        data["bundle"]["icon"].append("icons/logo.svg")
        conf.write_bytes(json.dumps(data, indent=2).encode())
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail closed on an unsupported bundle.icon entry"
        assert "unsupported bundle.icon entry" in result.stderr + result.stdout
    finally:
        fake.unlink()
        conf.write_bytes(original)


# ─── --check structural validation of stub CONTENT ────────────────────────


def _windows_sidecar_path() -> Path:
    """The Windows sidecar stub path used by the content-gate tests."""
    return SRC_TAURI / "bin" / "python-sidecar-x86_64-pc-windows-msvc.exe"


def test_check_rejects_truncated_stub():
    """``--check`` must fail on a truncated stub (marker present, bytes wrong).

    Presence alone used to pass — a partial write (killed generator, bad
    checkout) left a short file that ``cargo tauri build`` would happily
    bundle. The structural gate catches it in milliseconds and names the
    offending path.
    """
    _run()  # generate canonical stubs
    p = _windows_sidecar_path()
    canonical = p.read_bytes()
    try:
        p.write_bytes(canonical[:45])  # cut mid-content; marker still present
        result = _run("--check")
        assert result.returncode != 0, "--check should fail on a truncated stub"
        out = result.stderr + result.stdout
        assert "truncated or corrupt" in out
        assert p.name in out, "--check should name the offending stub path"
    finally:
        p.write_bytes(canonical)  # restore so the autouse --clean removes it


def test_check_rejects_empty_file():
    """``--check`` must fail on an empty file at a stub path (partial write)."""
    _run()
    p = _windows_sidecar_path()
    try:
        p.write_bytes(b"")
        result = _run("--check")
        assert result.returncode != 0, "--check should fail on an empty stub file"
        assert "EMPTY" in result.stderr + result.stdout
    finally:
        p.unlink()  # --clean preserves marker-less files — remove it ourselves


def test_check_rejects_tiny_garbage_file():
    """``--check`` must fail on a tiny non-stub file (neither stub nor binary)."""
    _run()
    p = SRC_TAURI / "resources" / "native" / "windows-key-listener.exe"
    try:
        p.write_bytes(b"\x00" * 100)
        result = _run("--check")
        assert result.returncode != 0, "--check should fail on a tiny garbage file"
        assert "too small to be a real" in result.stderr + result.stdout
    finally:
        p.unlink()


def test_check_accepts_real_binary_at_stub_path():
    """``--check`` must PASS when a real (large, marker-free) binary is present.

    CI builds the host-platform artifacts (Nuitka sidecar / compiled
    native listener) BEFORE this gate — a real binary at a stub path is
    the expected state there, not an error.
    """
    _run()
    p = _windows_sidecar_path()
    real = b"\x4d\x5a" + b"\x00" * 32768  # MZ header, 32 KB, no marker
    try:
        p.write_bytes(real)
        result = _run("--check")
        assert result.returncode == 0, (
            f"--check should accept a real binary at a stub path:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "structurally valid" in result.stdout
    finally:
        p.unlink()  # --clean would preserve it (real) — remove ourselves


def test_generate_heals_truncated_and_empty_stubs():
    """``generate`` must repair a truncated stub and an empty file.

    The ``--check || generate`` CI idiom must not just DETECT corruption
    — ``generate`` rewrites any file the structural gate rejects, so the
    repair happens in the same step that fills in missing stubs.
    """
    _run("--clean")
    p = _windows_sidecar_path()
    p.write_bytes(b"")  # empty -> corrupt
    q = SRC_TAURI / "resources" / "prewarm-x86_64-pc-windows-msvc.exe"
    _run()  # generate — must heal p and create q
    content = p.read_bytes()
    assert content != b"", "generate() should replace an empty file with a stub"
    assert b"STUB: not a real sidecar" in content, "healed file must be a canonical stub"
    assert q.exists(), "generate() should still create the sibling stub"
    # The healed tree must now pass --check.
    result = _run("--check")
    assert result.returncode == 0, (
        f"--check should pass after generate healed the stubs:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ─── Config ↔ committed-icon drift guard ───────────────────────────────────


def _config_icon_paths(config_path: Path) -> set[str]:
    """The ``bundle.icon`` list from a Tauri config, relative to ``src-tauri/``."""
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return set(data["bundle"]["icon"])


def _tracked_icon_paths() -> set[str]:
    """Every git-tracked file under ``src-tauri/icons/``, src-tauri-relative.

    The icons are REAL committed artifacts now (generated once with
    ``tauri icon`` from ``voice_typer/client/scripts/logo.svg``), so the
    lockstep guard is config ↔ git rather than config ↔ stub generator.
    Paths are normalized to the ``icons/...`` form used by
    ``bundle.icon`` (git ls-files emits ``src-tauri/icons/...`` repo-root
    paths).

    The ``icons/tray/`` subdir is EXCLUDED: those are the Tauri tray
    state icons — ``bundle.resources`` (shipped to ``$RESOURCE/tray/``),
    not ``bundle.icon`` app icons. They are guarded by
    ``tests/tauri/test_tray_icons.py`` (committed set ↔ Rust whitelist ↔
    mjs emitter ↔ config wiring).
    """
    result = subprocess.run(
        ["git", "ls-files", "src-tauri/icons/"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
    prefix = "src-tauri/"
    return {
        p[len(prefix) :]
        for p in result.stdout.splitlines()
        if p.startswith(prefix + "icons/") and not p.startswith(prefix + "icons/tray/")
    }


def test_tauri_conf_icon_list_matches_tracked_icons() -> None:
    """``tauri.conf.json`` ``bundle.icon`` must match the committed icon set.

    Every path in ``bundle.icon`` must be a git-tracked icon file (a path
    that is not committed is missing on a fresh CI checkout and breaks
    ``cargo tauri build``), and every tracked icon must be listed in
    ``bundle.icon`` (an unlisted one is dead weight — or a new icon
    added to the config but never committed). Adding a new icon to one
    side but not the other must fail this test.
    """
    config_icons = _config_icon_paths(SRC_TAURI / "tauri.conf.json")
    tracked_icons = _tracked_icon_paths()

    missing_from_git = config_icons - tracked_icons
    assert not missing_from_git, (
        "icons in tauri.conf.json bundle.icon but NOT committed to git "
        "(missing on a fresh CI checkout): "
        f"{sorted(missing_from_git)}. Regenerate them with "
        "`python scripts/build/generate_tauri_icons.py` (runs `tauri icon` "
        "from voice_typer/client/scripts/logo.svg, re-prunes to the "
        "bundle.icon set, re-runs this guard) and commit them."
    )
    tracked_but_unlisted = tracked_icons - config_icons
    assert not tracked_but_unlisted, (
        "icons committed under src-tauri/icons/ but NOT listed in "
        "tauri.conf.json bundle.icon (dead files): "
        f"{sorted(tracked_but_unlisted)}. Either add them to bundle.icon "
        "or remove them from the repo."
    )


def test_per_arch_configs_do_not_override_bundle_icon() -> None:
    """Per-arch Tauri configs must NOT set ``bundle.icon``.

    CI merges a per-arch config over the base (``--config
    tauri.<os>.conf.json``); if one introduced its own icon list, the
    base config would no longer be the single source of truth and the
    drift guard above would be bypassed on that platform.
    """
    for cfg in sorted(SRC_TAURI.glob("tauri.*.conf.json")):
        bundle = json.loads(cfg.read_text(encoding="utf-8")).get("bundle", {})
        assert "icon" not in bundle, (
            f"{cfg.name} overrides bundle.icon — the base tauri.conf.json "
            "must remain the single source of truth for bundle.icon "
            "(per-arch configs may only narrow bundle.targets / bundle.resources)."
        )


def _posix_sidecar_stubs() -> list[Path]:
    """All POSIX (non-Windows) stub sidecar paths generated by the script."""
    return [
        SRC_TAURI / "bin/python-sidecar-x86_64-apple-darwin",
        SRC_TAURI / "bin/python-sidecar-aarch64-apple-darwin",
        SRC_TAURI / "bin/python-sidecar-x86_64-unknown-linux-gnu",
        SRC_TAURI / "bin/python-sidecar-aarch64-unknown-linux-gnu",
    ]


def _linux_sidecar_stubs() -> list[Path]:
    """Only the Linux-variant POSIX stub sidecars."""
    return [
        SRC_TAURI / "bin/python-sidecar-x86_64-unknown-linux-gnu",
        SRC_TAURI / "bin/python-sidecar-aarch64-unknown-linux-gnu",
    ]


def _macos_sidecar_stubs() -> list[Path]:
    """Only the macOS-variant POSIX stub sidecars."""
    return [
        SRC_TAURI / "bin/python-sidecar-x86_64-apple-darwin",
        SRC_TAURI / "bin/python-sidecar-aarch64-apple-darwin",
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="linux-only POSIX sidecar exec")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_linux():
    """Executable Linux stub sidecars must exit 1 + print STUB to stderr.

    This is the safety feature: a stub that accidentally got into a
    release build fails loudly at runtime instead of silently doing nothing.

    Replaces the previous EC-26 silent ``if sys.platform == "win32":``
    guard with an explicit per-platform test so non-Linux runs report
    SKIP (not silent PASS) — the orchestrator's acceptance criteria
    require visibility into which platform branches actually executed.
    """
    _run()
    for p in _linux_sidecar_stubs():
        if not p.exists():
            continue
        result = subprocess.run([str(p)], capture_output=True, text=True)
        assert result.returncode != 0, f"{p.name} should exit non-zero (got {result.returncode})"
        assert "STUB" in result.stderr, f"{p.name} should print STUB marker to stderr; got: {result.stderr!r}"


@pytest.mark.skipif(sys.platform != "darwin", reason="macos-only POSIX sidecar exec")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_macos():
    """Executable macOS stub sidecars must exit 1 + print STUB to stderr.

    Mirrors the Linux variant but exercises the two ``*-apple-darwin``
    stubs so a macOS CI run reports the actual exec behavior on that
    platform.
    """
    _run()
    for p in _macos_sidecar_stubs():
        if not p.exists():
            continue
        result = subprocess.run([str(p)], capture_output=True, text=True)
        assert result.returncode != 0, f"{p.name} should exit non-zero (got {result.returncode})"
        assert "STUB" in result.stderr, f"{p.name} should print STUB marker to stderr; got: {result.stderr!r}"


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only POSIX sidecar content check")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_windows():
    """On Windows the POSIX stub files cannot be exec'd (``WinError 193``:
    not a valid Win32 application — there is no ``/bin/sh``), so we
    verify the marker is embedded in the stub content instead.

    The Windows .exe stubs fail at ``CreateProcess`` time, which is
    equally "loud" but not directly exec'-able from a Python test —
    this content check ensures a mis-packaged Windows build that picks
    the wrong binary would fail loudly when shipped.
    """
    _run()
    for p in _posix_sidecar_stubs():
        if not p.exists():
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        assert "STUB" in content, (
            f"{p.name} should embed the STUB marker so it fails "
            f"loudly if executed (Windows cannot run the POSIX "
            f"shell script directly)."
        )
