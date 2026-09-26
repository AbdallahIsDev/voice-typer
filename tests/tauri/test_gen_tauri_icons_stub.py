"""Regression tests for ``scripts/gen_tauri_icons_stub.py``."""

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
from types import SimpleNamespace

import filelock
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"
SRC_TAURI = PROJECT_ROOT / "src-tauri"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# (C-TEST-5: test isolation; C-STYLE-1: minimal, documented change.)
pytestmark = pytest.mark.xdist_group("gen_tauri_icons_stub")

# Cross-process lock file, lives in the per-user temp dir so concurrent
_LOCK_PATH = Path(tempfile.gettempdir()) / "lausu-gen-tauri-icons-stub.test.lock"

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

# Local-dev GNU-toolchain mirrors (C-TDEV-1): this machine builds with
# stable-x86_64-pc-windows-gnu (no MSVC), so a local `cargo tauri dev` /
# `cargo check` resolves bundle.externalBin against this triple too. Never
# bundled by release CI (mirrors the script's LOCAL_DEV_TRIPLES).
LOCAL_DEV_TRIPLES = ["x86_64-pc-windows-gnu"]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the stub script with the given args; capture stdout/stderr/exit."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def _script_module():
    """Import ``gen_tauri_icons_stub.py`` so the tests share its canonical"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_tauri_icons_stub", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _stub_paths() -> list[Path]:
    """Mirror of the script's _all_stub_paths(), every stub we expect to exist."""
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
        paths.append(SRC_TAURI / "bin" / f"lausu-worker-{triple}{ext}")
    for triple in LOCAL_DEV_TRIPLES:
        # GNU-toolchain mirrors (C-TDEV-1), always Windows + .exe.
        paths.append(SRC_TAURI / "bin" / f"python-sidecar-{triple}.exe")
        paths.append(SRC_TAURI / "bin" / f"lausu-worker-{triple}.exe")
    return paths


def _ensure_stubs_present(expected: list[Path], attempts: int = 3) -> list[Path]:
    """Regenerate stubs until every ``expected`` path exists again."""
    missing = [p for p in expected if not p.exists()]
    for _ in range(attempts):
        if not missing:
            return []
        _run()
        time.sleep(0.5)
        missing = [p for p in expected if not p.exists()]
    return missing


@pytest.fixture(autouse=True)
def _serialize_and_cleanup():
    """Acquire a cross-process file lock for the duration of each test, then"""
    lock = filelock.FileLock(str(_LOCK_PATH), timeout=60)
    with lock:
        # Self-heal before each test: a failed/killed earlier run can leave
        _restore_committed_icons()
        pre_existing_stubs = [p for p in _stub_paths() if p.exists()]
        yield
        # Ensure stubs are cleaned up after each test (don't pollute the repo).
        _run("--clean")
        # Restore the pre-test stub state: if stubs existed before the test,
        if pre_existing_stubs:
            still_missing = _ensure_stubs_present(pre_existing_stubs)
            if still_missing:
                pytest.fail(
                    "stub restore failed, these pre-existing stubs are "
                    "still missing after --clean + 3x generate: "
                    + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in still_missing)
                )
        _restore_committed_icons()


def test_generate_creates_all_expected_stubs():
    """The script with no args should create every expected stub file."""
    _run("--clean")  # start from a clean state
    result = _run()
    assert result.returncode == 0, f"generate failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    missing = [p for p in _stub_paths() if not p.exists()]
    assert not missing, f"missing stub files: {missing}"


def test_local_dev_triple_mirror_matches_generator() -> None:
    """The GNU-toolchain mirror list here must track the generator's (C-TDEV-1)."""
    stub = _script_module()
    assert set(LOCAL_DEV_TRIPLES) == set(stub.LOCAL_DEV_TRIPLES), (
        "this file's LOCAL_DEV_TRIPLES mirror drifted from "
        "gen_tauri_icons_stub.py::LOCAL_DEV_TRIPLES; keep them in sync so the "
        "clean/restore fixture and _stub_paths() cover every stub the "
        "generator owns."
    )


def test_stub_path_mirror_covers_the_generator_registry() -> None:
    """``_stub_paths()`` must mirror the generator's registry exactly."""
    stub = _script_module()
    mirror = {p.resolve().relative_to(SRC_TAURI.resolve()).as_posix() for p in _stub_paths()}
    registry = {p.resolve().relative_to(SRC_TAURI.resolve()).as_posix() for p in stub._all_stub_paths()}
    assert mirror == registry, (
        "_stub_paths() drifted from gen_tauri_icons_stub.py::_all_stub_paths(); "
        "the clean/restore fixture of this module only covers the paths listed "
        "here, so a generator-owned stub would be deleted and never restored:\n"
        f"  only in the mirror: {sorted(mirror - registry)}\n"
        f"  only in the generator: {sorted(registry - mirror)}"
    )


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
    assert "Sidecar binaries:" in result.stdout
    assert "Native resources:" in result.stdout
    assert "Worker binaries:" in result.stdout


_DIM_TABLE = _script_module().EXPECTED_PNG_DIMENSIONS
_ICNS_SIZES = _script_module().EXPECTED_ICNS_CHUNK_SIZES


# ─── Committed-icon self-healing (C-TEST-5: test isolation) ──────────────
_COMMITTED_ICON_RELS: tuple[str, ...] = tuple(sorted(_DIM_TABLE)) + ("icons/icon.ico", "icons/icon.icns")


def _git_show_bytes(rel: str) -> bytes:
    """The git-object-store bytes for a src-tauri-relative icon path."""
    for spec in (f":src-tauri/{rel}", f"HEAD:src-tauri/{rel}"):
        res = subprocess.run(
            ["git", "show", spec],
            capture_output=True,
            cwd=str(PROJECT_ROOT),
        )
        if res.returncode == 0:
            return res.stdout
    raise RuntimeError(f"cannot read committed icon {rel} via git show: {res.stderr.decode(errors='replace').strip()}")


_COMMITTED_ICON_BYTES: dict[str, bytes] = {rel: _git_show_bytes(rel) for rel in _COMMITTED_ICON_RELS}


def _write_with_retry(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, retrying transient OSErrors."""
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


# These builders fabricate icons for the red-tests with the SAME


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


def _committed_icns_legacy_sizes() -> dict[bytes, int]:
    """Legacy-chunk payload sizes from the committed ICNS."""
    fingerprint = dict(_icns_fingerprint(_COMMITTED_ICON_BYTES["icons/icon.icns"]))
    return {ostype: v[1] for ostype, v in fingerprint.items() if v[0] == "raw"}


def _synthetic_icns() -> bytes:
    """An ICNS with the full canonical chunk set (8 PNG + 4 legacy raw)."""
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
    committed_sizes = _committed_icns_legacy_sizes()
    for ostype, n in _ICNS_LEGACY_PAYLOAD_SIZES:
        payload_len = committed_sizes.get(ostype, n)
        chunks.append(ostype + struct.pack(">I", 8 + payload_len) + b"\x00" * payload_len)
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


# These extract the structural properties the gates + tests pin. Used


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
    """(reserved, type, entries), each entry is"""
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
    """Each committed icon PNG must have the magic bytes + correct IHDR dimensions."""
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
    assert data[24] == 8, f"{rel}: expected bit_depth=8, got {data[24]}"
    assert data[25] == 6, f"{rel}: expected color_type=6 (RGBA), got {data[25]}"
    # Must end with an IEND chunk.
    assert b"IEND" in data, f"{rel}: no IEND chunk found"


def test_dimension_table_consistent_with_filename_convention():
    """Table entries must agree with the ``tauri icon`` filename convention."""
    table = _DIM_TABLE
    assert table["icons/32x32.png"] == (32, 32)
    assert table["icons/128x128.png"] == (128, 128)
    assert table["icons/128x128@2x.png"] == (256, 256)
    assert table["icons/icon.png"] == (512, 512)


def test_bundle_icon_pngs_match_dimension_table():
    """Every ``bundle.icon`` PNG must have a registered expected dimension."""
    conf = json.loads((SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    config_pngs = {p for p in conf["bundle"]["icon"] if p.endswith(".png")}
    table = set(_DIM_TABLE)
    assert config_pngs == table, (
        f"bundle.icon PNGs ({sorted(config_pngs)}) must match the dimension "
        f"table ({sorted(table)}), add/remove entries in "
        "EXPECTED_PNG_DIMENSIONS in scripts/gen_tauri_icons_stub.py"
    )


def test_committed_pngs_have_real_tauri_icon_container_layout():
    """The committed PNGs must be byte-for-byte the tauri icon container."""
    for rel, expected in sorted(_DIM_TABLE.items()):
        types, ihdr, crc_ok = _png_fingerprint((SRC_TAURI / rel).read_bytes())
        assert types == (b"IHDR", b"IDAT", b"IEND"), f"{rel}: chunk layout {types}"
        assert ihdr == (expected[0], expected[1], 8, 6, 0, 0, 0), f"{rel}: IHDR {ihdr}"
        assert crc_ok, f"{rel}: IHDR CRC invalid"


def test_committed_ico_matches_real_tauri_icon_container():
    """Pin the committed ICO to the exact tauri icon entry layout."""
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
    """Pin the committed ICNS to the canonical tauri icon chunk set."""
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
    """The test-suite stub icons must be structurally identical to production."""
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
    """``icons/icon.ico`` must be a structurally valid ICO with PNG entries."""
    data = (SRC_TAURI / "icons/icon.ico").read_bytes()
    # ICONDIR: reserved=0 (u16 LE), type=1=icon (u16 LE), count (u16 LE).
    reserved, icon_type, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0
    assert icon_type == 1
    assert count >= 2, f"expected >= 2 ICO entries, got {count}"
    # ICONDIRENTRY records: width, height, colorCount, reserved, planes,
    offset = 6
    dims: list[tuple[int, int]] = []
    blobs: list[tuple[int, int]] = []
    for i in range(count):
        rec = data[offset : offset + 16]
        offset += 16
        w, h, _, _, planes, bpp, size, img_off = struct.unpack("<BBBBHHII", rec)
        dims.append((256 if w == 0 else w, 256 if h == 0 else h))  # 0 means 256px
        assert planes in (0, 1) and bpp == 32, f"entry {i}: planes={planes} bpp={bpp}"
        blobs.append((img_off, size))
    # The canonical sizes tauri-build's winres + the window-icon embed
    assert (32, 32) in dims and (256, 256) in dims, f"missing 32x32 or 256x256 entry: {dims}"
    # Every image blob must be a PNG (tauri icon writes PNG-in-ICO).
    for img_off, size in blobs:
        blob = data[img_off : img_off + size]
        assert blob.startswith(PNG_MAGIC), f"ICO entry at offset {img_off} is not PNG-compressed"
    # All blobs must fit inside the file.
    assert max(off + size for off, size in blobs) <= len(data)


def test_generated_icns_is_valid_macos_icon_container():
    """``icons/icon.icns`` must be a structurally valid ICNS."""
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
    for expected in (b"ic07", b"ic08", b"ic09", b"ic10"):
        assert expected in png_ostypes, f"icns missing PNG chunk {expected!r}; got {ostypes}"
    # Any non-PNG chunk must be a known legacy type, not arbitrary bytes.
    legacy = {b"il32", b"is32", b"l8mk", b"s8mk", b"it32", b"t8mk"}
    unknown = [o for o in ostypes if o not in png_ostypes and o not in legacy]
    assert not unknown, f"icns has unknown chunk types: {unknown}"


def test_check_icons_validates_every_bundle_icon():
    """``--check-icons`` must validate ALL SIX bundle.icon files at once."""
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
    """``--check-icons`` must pass on the committed icon.ico."""
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed ICO:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "valid ICO" in result.stdout


def test_check_ico_exits_nonzero_when_missing():
    """``--check-icons`` must fail when icon.ico is missing (CI gate)."""
    ico = SRC_TAURI / "icons" / "icon.ico"
    assert ico.exists(), "committed icon.ico missing, icon set not committed?"
    tmp = ico.with_name("icon.ico.check-missing-tmp")
    try:
        ico.rename(tmp)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail when icon.ico is missing"
        assert "MISSING" in result.stderr or "MISSING" in result.stdout
    finally:
        tmp.rename(ico)


def test_check_ico_rejects_corrupt_ico():
    """``--check-icons`` must reject structurally invalid ICO files."""
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
        _write_with_retry(
            ico, struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(blob), 22) + blob
        )
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "not PNG-compressed" in result.stderr or "not PNG-compressed" in result.stdout
    finally:
        _write_with_retry(ico, original)


def test_check_icns_exits_zero_on_committed_icns():
    """``--check-icons`` must pass on the committed icon.icns."""
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed ICNS:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "valid ICNS" in result.stdout


def test_check_icns_exits_nonzero_when_missing():
    """``--check-icons`` must fail when icon.icns is missing (CI gate)."""
    icns = SRC_TAURI / "icons" / "icon.icns"
    assert icns.exists(), "committed icon.icns missing, icon set not committed?"
    tmp = icns.with_name("icon.icns.check-missing-tmp")
    try:
        icns.rename(tmp)
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail when icon.icns is missing"
        assert "MISSING" in result.stderr or "MISSING" in result.stdout
    finally:
        tmp.rename(icns)


def test_check_icns_rejects_corrupt_icns():
    """``--check-icons`` must reject structurally invalid ICNS files."""
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
        _write_with_retry(icns, b"icns" + struct.pack(">I", 16) + b"ic07" + struct.pack(">I", 9999))
        result = _run("--check-icons")
        assert result.returncode != 0
        assert "overruns file" in result.stderr or "overruns file" in result.stdout
    finally:
        _write_with_retry(icns, original)


def test_check_png_exits_zero_on_committed_pngs():
    """``--check-icons`` must pass on every committed bundle.icon PNG."""
    result = _run("--check-icons")
    assert result.returncode == 0, (
        f"--check-icons should pass on the committed PNGs:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    for name in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png"):
        assert f"{name} is a valid PNG" in result.stdout, f"missing OK line for {name}:\n{result.stdout}"


def test_check_png_exits_nonzero_when_missing():
    """``--check-icons`` must fail when a bundle.icon PNG is missing (CI gate)."""
    png = SRC_TAURI / "icons" / "32x32.png"
    assert png.exists(), "committed 32x32.png missing, icon set not committed?"
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
    """``--check-icons`` must reject structurally invalid PNG files."""
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
        _write_with_retry(
            png,
            PNG_MAGIC
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", 0, 32)
            + bytes([8, 6, 0, 0, 0])
            + b"\x00" * 4,  # CRC placeholder
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
    """``--check-icons`` must fail when a bundle.icon PNG has wrong dimensions."""
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        _write_with_retry(png, (SRC_TAURI / "icons" / "128x128.png").read_bytes())
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a wrong-sized PNG"
        assert "expected 32x32" in result.stderr or "expected 32x32" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_ico_rejects_missing_expected_size():
    """``--check-icons`` must fail when the committed size set is incomplete."""
    ico = SRC_TAURI / "icons" / "icon.ico"
    original = ico.read_bytes()
    try:
        # Header (0/1/1) + a single 32x32 entry pointing at the committed
        blob = (SRC_TAURI / "icons" / "32x32.png").read_bytes()
        _write_with_retry(
            ico, struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(blob), 22) + blob
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on an incomplete size set"
        assert "missing expected ICO sizes" in result.stderr or "missing expected ICO sizes" in result.stdout
    finally:
        _write_with_retry(ico, original)


def test_check_icns_rejects_wrong_png_chunk_size():
    """``--check-icons`` must fail when a PNG chunk's pixels don't match its OSType."""
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
    """``--check-icons`` must fail when a canonical chunk is missing."""
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
    """``--check-icons`` must fail on a header-only PNG (magic + IHDR + IEND)."""
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        _write_with_retry(
            png,
            PNG_MAGIC + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 6, 0, 0, 0)) + _png_chunk(b"IEND", b""),
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a header-only PNG"
        assert "IDAT" in result.stderr or "IDAT" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_png_rejects_interlaced():
    """``--check-icons`` must fail on an interlaced PNG."""
    png = SRC_TAURI / "icons" / "32x32.png"
    original = png.read_bytes()
    try:
        idat = zlib.compress((b"\x00" + b"\x00\x00\x00\x00" * 32) * 32)
        _write_with_retry(
            png,
            PNG_MAGIC
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 6, 0, 0, 1))
            + _png_chunk(b"IDAT", idat)
            + _png_chunk(b"IEND", b""),
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on an interlaced PNG"
        assert "interlace" in result.stderr or "interlace" in result.stdout
    finally:
        _write_with_retry(png, original)


def test_check_png_rejects_corrupt_ihdr_crc():
    """``--check-icons`` must fail when the stored IHDR CRC is wrong."""
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
    """``--check-icons`` must fail when an entry's PNG blob doesn't match its size."""
    ico = SRC_TAURI / "icons" / "icon.ico"
    original = ico.read_bytes()
    try:
        blob = _synthetic_png(16, 16)  # valid PNG, wrong size for the entry
        _write_with_retry(
            ico, struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(blob), 22) + blob
        )
        result = _run("--check-icons")
        assert result.returncode != 0, "--check-icons should fail on a blob/entry dimension mismatch"
        assert "does not match declared" in result.stderr or "does not match declared" in result.stdout
    finally:
        _write_with_retry(ico, original)


@pytest.mark.real_pil
def test_generated_pngs_decode_with_pillow_if_available():
    """If Pillow is installed, the PNGs must decode to the expected size."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed, skipping decode check")
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
    """``--clean`` must remove every stub file the script generated."""
    _run()  # generate
    result = _run("--clean")
    assert result.returncode == 0, f"--clean failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    leftover = [p for p in _stub_paths() if p.exists()]
    assert not leftover, f"--clean left stub files behind: {[str(p) for p in leftover]}"


def test_clean_does_not_remove_real_binary():
    """``--clean`` must preserve a real (large, marker-free) binary at a stub path."""
    _run()  # generate stubs first
    real_binary_path = SRC_TAURI / "bin" / "python-sidecar-x86_64-unknown-linux-gnu"
    real_binary_path.write_bytes(b"\x7fELF" + b"\x00" * 32768)  # 32 KB, no marker
    os.chmod(real_binary_path, 0o755)

    result = _run("--clean")
    assert result.returncode == 0
    # The real binary must still exist.
    assert real_binary_path.exists(), "--clean deleted a real binary, heuristic failed"
    # But the other stubs must be gone.
    other_stub = SRC_TAURI / "bin" / "python-sidecar-x86_64-apple-darwin"
    assert not other_stub.exists(), "--clean did not remove a sibling stub"
    real_binary_path.unlink()


def test_generate_preserves_existing_real_binary():
    """``generate`` must NOT clobber a real artifact with a placeholder stub."""
    _run("--clean")  # start clean
    # Plant a fake "real" binary at the host-arch sidecar path.
    real_path = SRC_TAURI / "bin" / "python-sidecar-x86_64-pc-windows-msvc.exe"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_content = b"\x4d\x5a" + b"\x00" * 32768  # MZ header, 32 KB, no marker
    real_path.write_bytes(real_content)

    result = _run()  # generate
    assert result.returncode == 0
    # The real binary must be untouched.
    assert real_path.read_bytes() == real_content, "generate() overwrote a real binary, must preserve it"
    # The sibling stubs must still be created.
    assert (SRC_TAURI / "bin" / "python-sidecar-x86_64-apple-darwin").exists()
    assert (SRC_TAURI / "resources" / "native" / "linux-key-listener").exists()
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
    """The collapsed gates must NOT survive as argparse abbreviations."""
    for old_flag in ("--check-ico", "--check-icns", "--check-png"):
        result = _run(old_flag)
        assert result.returncode != 0, f"{old_flag} should be rejected (collapsed into --check-icons)"
        assert "unrecognized arguments" in result.stderr, f"{old_flag}: {result.stderr}"


def test_check_icons_rejects_unsupported_bundle_icon_extension():
    """``--check-icons`` must fail closed on an unsupported bundle.icon entry."""
    conf = SRC_TAURI / "tauri.conf.json"
    fake = SRC_TAURI / "icons" / "logo.svg"
    original = conf.read_bytes()
    try:
        # The file must EXIST: the gate reports MISSING for absent entries
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


def _windows_sidecar_path() -> Path:
    """The Windows sidecar stub path used by the content-gate tests."""
    return SRC_TAURI / "bin" / "python-sidecar-x86_64-pc-windows-msvc.exe"


def test_check_rejects_truncated_stub():
    """``--check`` must fail on a truncated stub (marker present, bytes wrong)."""
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
        p.unlink()  # --clean preserves marker-less files, remove it ourselves


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
    """``--check`` must PASS when a real (large, marker-free) binary is present."""
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
        p.unlink()  # --clean would preserve it (real), remove ourselves


def test_generate_heals_truncated_and_empty_stubs():
    """
    ``generate`` must repair a truncated stub and an empty file.
    The ``--check || generate`` CI idiom must not just DETECT corruption
    """
    _run("--clean")
    p = _windows_sidecar_path()
    # Recreate the parent dir: ``--clean`` rmdirs the now-empty
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")  # empty -> corrupt
    q = SRC_TAURI / "bin" / "lausu-worker-x86_64-pc-windows-msvc.exe"
    _run()  # generate, must heal p and create q
    content = p.read_bytes()
    assert content != b"", "generate() should replace an empty file with a stub"
    assert b"STUB: not a real sidecar" in content, "healed file must be a canonical stub"
    assert q.exists(), "generate() should still create the sibling stub"
    # The healed tree must now pass --check.
    result = _run("--check")
    assert result.returncode == 0, (
        f"--check should pass after generate healed the stubs:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def _config_icon_paths(config_path: Path) -> set[str]:
    """The ``bundle.icon`` list from a Tauri config, relative to ``src-tauri/``."""
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return set(data["bundle"]["icon"])


def _tracked_icon_paths() -> set[str]:
    """Every git-tracked file under ``src-tauri/icons/``, src-tauri-relative."""
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
    """``tauri.conf.json`` ``bundle.icon`` must match the committed icon set."""
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
    """Per-arch Tauri configs must NOT set ``bundle.icon``."""
    for cfg in sorted(SRC_TAURI.glob("tauri.*.conf.json")):
        bundle = json.loads(cfg.read_text(encoding="utf-8")).get("bundle", {})
        assert "icon" not in bundle, (
            f"{cfg.name} overrides bundle.icon, the base tauri.conf.json "
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
    """Executable Linux stub sidecars must exit 1 + print STUB to stderr."""
    _run()
    for p in _linux_sidecar_stubs():
        if not p.exists():
            continue
        result = subprocess.run([str(p)], capture_output=True, text=True)
        assert result.returncode != 0, f"{p.name} should exit non-zero (got {result.returncode})"
        assert "STUB" in result.stderr, f"{p.name} should print STUB marker to stderr; got: {result.stderr!r}"


@pytest.mark.skipif(sys.platform != "darwin", reason="macos-only POSIX sidecar exec")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_macos():
    """Executable macOS stub sidecars must exit 1 + print STUB to stderr."""
    _run()
    for p in _macos_sidecar_stubs():
        if not p.exists():
            continue
        result = subprocess.run([str(p)], capture_output=True, text=True)
        assert result.returncode != 0, f"{p.name} should exit non-zero (got {result.returncode})"
        assert "STUB" in result.stderr, f"{p.name} should print STUB marker to stderr; got: {result.stderr!r}"


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only POSIX sidecar content check")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_windows():
    """On Windows the POSIX stub files cannot be exec'd (``WinError 193``:"""
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


# Pins the two guards that keep ``cargo check`` working after test runs:


def test_ensure_stubs_present_noop_when_all_exist(tmp_path, monkeypatch):
    """All paths present → returns [] WITHOUT invoking generate."""
    import sys as _sys

    present = tmp_path / "a.exe"
    present.write_bytes(b"x")
    calls: list = []
    monkeypatch.setattr(
        _sys.modules[__name__],
        "_run",
        lambda *a: calls.append(a) or subprocess.CompletedProcess(args=a, returncode=0),
    )
    assert _ensure_stubs_present([present]) == []
    assert calls == []


def test_ensure_stubs_present_retries_then_reports_missing(tmp_path, monkeypatch):
    """Persistently missing path → ``attempts`` generate calls, path returned."""
    import sys as _sys

    calls: list = []
    monkeypatch.setattr(
        _sys.modules[__name__],
        "_run",
        lambda *a: calls.append(a) or subprocess.CompletedProcess(args=a, returncode=0),
    )
    missing = tmp_path / "ghost.exe"
    assert _ensure_stubs_present([missing], attempts=2) == [missing]
    assert len(calls) == 2


def _load_tauri_conftest():
    """Load ``tests/tauri/conftest.py`` as a module (importlib precedent:"""
    import importlib.util

    path = PROJECT_ROOT / "tests" / "tauri" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_vt_tauri_conftest_guard", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sessionfinish_restores_only_on_controller(tmp_path, monkeypatch, capsys):
    """Controller sessionfinish re-runs generate for missing pre-session"""
    import subprocess as _subprocess

    mod = _load_tauri_conftest()
    ghost = tmp_path / "ghost.exe"
    monkeypatch.setattr(mod, "_session_stub_paths", [str(ghost)])
    calls: list = []
    monkeypatch.setattr(
        _subprocess,
        "run",
        lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(args=a, returncode=0),
    )
    controller = SimpleNamespace(config=SimpleNamespace())
    mod.pytest_sessionfinish(controller, 0)  # must not raise
    assert len(calls) == 3  # 3 attempts, ghost never appears
    assert not ghost.exists()
    assert "stub-guard" in capsys.readouterr().out

    worker = SimpleNamespace(config=SimpleNamespace(workerinput={"workerid": "gw0"}))
    mod.pytest_sessionfinish(worker, 0)  # must not raise, must not run generate
    assert len(calls) == 3


def test_tauri_conftest_stub_guard_covers_the_generator_registry() -> None:
    """The session restore guard must cover every stub ``--clean`` deletes."""
    mod = _load_tauri_conftest()
    stub = _script_module()
    guard = {
        Path(p).resolve().relative_to(SRC_TAURI.resolve()).as_posix() for p in mod._canonical_stub_paths()
    }
    registry = {p.resolve().relative_to(SRC_TAURI.resolve()).as_posix() for p in stub._all_stub_paths()}
    assert guard == registry, (
        "tests/tauri/conftest.py::_canonical_stub_paths() drifted from "
        "gen_tauri_icons_stub.py::_all_stub_paths(); a killed test run then "
        "leaves the missing stubs unrestored and the next `cargo check` / "
        "`tauri dev` fails on externalBin resolution (C-TDEV-1):\n"
        f"  only in the guard: {sorted(guard - registry)}\n"
        f"  only in the generator: {sorted(registry - guard)}"
    )
