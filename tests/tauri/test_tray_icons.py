"""Drift guards for the Tauri tray state icons (``src-tauri/icons/tray/``)."""

from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = PROJECT_ROOT / "src-tauri"
TRAY_DIR = SRC_TAURI / "icons" / "tray"
MJS = PROJECT_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
TRAY_ICON_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_icon.py"
TRAY_TESTS_RS = SRC_TAURI / "src" / "tray_tests.rs"

# The string resource entry that ships the tray icons. Tauri preserves
TRAY_RESOURCE_ENTRY = "icons/tray/"

# The macOS template source, tracked + shipped, but NOT a whitelisted
TEMPLATE_PNG = "tray-mic-template.png"

# The four logical state icons (mirror ALLOWED_ICON_NAMES in tray_tests.rs).
STATE_ICONS = ("idle", "recording", "transcribing", "error")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TRAY_ICON_SIZE = 32


def _all_tauri_configs() -> list[Path]:
    """
    The base config + every per-arch config (deterministic order).
    ``tauri.dev.conf.json`` (the dev-mode override, C-TDEV-1) is
    """
    per_arch = sorted(p for p in SRC_TAURI.glob("tauri.*.conf.json") if p.name != "tauri.dev.conf.json")
    return [SRC_TAURI / "tauri.conf.json", *per_arch]


def _tracked_tray_pngs() -> set[str]:
    """Every git-tracked filename under ``src-tauri/icons/tray/``."""
    result = subprocess.run(
        ["git", "ls-files", "src-tauri/icons/tray/"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
    return {Path(p).name for p in result.stdout.splitlines()}


def _rust_whitelist() -> set[str]:
    """
    Parse ``ALLOWED_ICON_NAMES`` from ``tray_tests.rs``.
    That constant is the single source of truth the Rust tests pin
    """
    text = TRAY_TESTS_RS.read_text(encoding="utf-8")
    m = re.search(r"ALLOWED_ICON_NAMES:\s*&\[&str\]\s*=\s*&\[(.*?)\]", text, re.S)
    assert m, "ALLOWED_ICON_NAMES constant not found in tray_tests.rs"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _mjs_state_icons() -> set[str]:
    """The ``trayStateColors`` keys in ``generate-icons.mjs``, the"""
    text = MJS.read_text(encoding="utf-8")
    block = re.search(r"const trayStateColors = \{(.*?)\};", text, re.S)
    assert block, "trayStateColors not found in generate-icons.mjs"
    return set(re.findall(r"^\s*(\w+):\s*\{", block.group(1), re.M))


def _mjs_palette() -> dict[str, tuple[int, int, int]]:
    """The mjs state palette as ``{state: (r, g, b)}``."""
    text = MJS.read_text(encoding="utf-8")
    block = re.search(r"const trayStateColors = \{(.*?)\};", text, re.S)
    assert block, "trayStateColors not found in generate-icons.mjs"
    return {
        name: (int(r), int(g), int(b))
        for name, r, g, b in re.findall(r"(\w+):\s*\{\s*r:\s*(\d+),\s*g:\s*(\d+),\s*b:\s*(\d+)", block.group(1))
    }


def _python_palette() -> dict[str, tuple[int, int, int]]:
    """The ``tray_icon.py::_make_icon`` palette as ``{state: (r, g, b)}``."""
    text = TRAY_ICON_PY.read_text(encoding="utf-8")
    return {
        name.lower(): (int(r), int(g), int(b))
        for name, r, g, b in re.findall(r"AppState\.(\w+):\s*\((\d+),\s*(\d+),\s*(\d+),\s*255\)", text)
    }


def test_tray_resource_wired_in_every_config() -> None:
    """Every Tauri config (base + per-arch) ships the tray icons."""
    for cfg in _all_tauri_configs():
        bundle = json.loads(cfg.read_text(encoding="utf-8"))["bundle"]
        resources = bundle.get("resources", [])
        assert TRAY_RESOURCE_ENTRY in resources, (
            f"{cfg.name} bundle.resources must include {TRAY_RESOURCE_ENTRY!r}, "
            "without it the tray PNGs never ship (tray.rs reads "
            "$RESOURCE/icons/tray/). Fix with "
            "`python scripts/build/generate_tray_icons.py`."
        )


def test_tracked_tray_pngs_match_rust_whitelist() -> None:
    """The committed state PNGs must equal the Rust host's whitelist."""
    tracked = _tracked_tray_pngs()
    whitelist = _rust_whitelist()
    assert whitelist == set(STATE_ICONS), (
        f"Rust whitelist changed ({sorted(whitelist)}), update STATE_ICONS to match, then regenerate the tray PNGs."
    )
    expected = {f"{name}.png" for name in whitelist} | {TEMPLATE_PNG}
    missing = expected - tracked
    assert not missing, (
        "tray PNGs committed under src-tauri/icons/tray/ must match the Rust "
        f"whitelist, missing: {sorted(missing)}. Regenerate with "
        "`python scripts/build/generate_tray_icons.py` and commit them."
    )
    extra = tracked - expected
    assert not extra, (
        "committed tray PNGs that are neither whitelisted states nor the "
        f"template (dead files, is_allowed_icon_name never loads them): "
        f"{sorted(extra)}"
    )


def test_mjs_emits_exactly_the_whitelisted_states() -> None:
    """``generate-icons.mjs`` must emit exactly the 4 whitelisted states."""
    mjs_states = _mjs_state_icons()
    assert mjs_states == set(STATE_ICONS), (
        f"generate-icons.mjs trayStateColors keys ({sorted(mjs_states)}) must "
        f"match the Rust whitelist ({sorted(STATE_ICONS)})"
    )


def test_python_icon_map_matches_rust_whitelist() -> None:
    """The 8th cross-language parity pair: Python's AppState→icon map ⊆ whitelist."""
    import sys
    from pathlib import Path as _PathLib

    tests_root = str(_PathLib(__file__).resolve().parents[2])
    if tests_root not in sys.path:
        sys.path.insert(0, tests_root)
    from voice_typer.server.tray_publish import _APP_STATE_TO_ICON_NAME

    whitelist = _rust_whitelist()
    unknown = set(_APP_STATE_TO_ICON_NAME.values()) - whitelist
    assert not unknown, (
        f"tray_publish._APP_STATE_TO_ICON_NAME sends icon name(s) {sorted(unknown)} "
        f"that the Rust host whitelist ({sorted(whitelist)}) does not accept, "
        "the tray icon would freeze at its last state. Add the name to "
        "ALLOWED_ICON_NAMES (tray_tests.rs / icon_cache.rs) + commit a tray PNG."
    )


def test_palette_matches_python_host() -> None:
    """Both hosts (Tauri mjs + Python pystray) show identical state colors."""
    mjs_palette = _mjs_palette()
    py_palette = _python_palette()
    for state in STATE_ICONS:
        assert state in mjs_palette, f"mjs palette missing state {state!r}"
        assert state in py_palette, f"tray_icon.py palette missing state {state!r}"
        assert mjs_palette[state] == py_palette[state], (
            f"state {state!r} color drifted between generate-icons.mjs "
            f"{mjs_palette[state]} and tray_icon.py {py_palette[state]}"
        )


def test_committed_tray_pngs_are_valid_32x32() -> None:
    """Every committed tray PNG is a 32x32 PNG (the mjs ``trayIconSize``)."""
    tracked = _tracked_tray_pngs()
    assert tracked, "no tracked tray PNGs, run the generator and commit them"
    for name in sorted(tracked):
        data = (TRAY_DIR / name).read_bytes()
        assert data.startswith(PNG_MAGIC), f"{name} is not a PNG"
        assert len(data) >= 24 and data[12:16] == b"IHDR", f"{name} is truncated"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (TRAY_ICON_SIZE, TRAY_ICON_SIZE), (
            f"{name} is {width}x{height}, expected {TRAY_ICON_SIZE}x{TRAY_ICON_SIZE}"
        )


def test_committed_tray_pngs_have_pinned_ihdr_layout() -> None:
    """The IHDR layout of every committed tray PNG is pinned exactly."""
    tracked = _tracked_tray_pngs()
    assert tracked, "no tracked tray PNGs, run the generator and commit them"
    for name in sorted(tracked):
        data = (TRAY_DIR / name).read_bytes()
        assert data.startswith(PNG_MAGIC) and data[12:16] == b"IHDR", f"{name} is not a valid PNG"
        ihdr = data[24:29]
        bit_depth, color_type, compression, filter_method, interlace = ihdr
        assert (bit_depth, color_type) == (8, 6), (
            f"{name} is {bit_depth}-bit color-type {color_type}; expected 8-bit "
            "RGBA (color type 6), the generator must render RGBA PNGs."
        )
        assert (compression, filter_method, interlace) == (0, 0, 0), (
            f"{name} IHDR compression/filter/interlace "
            f"({compression}/{filter_method}/{interlace}) drifted "
            "from 0/0/0 (default zlib, no filtering, no interlacing)."
        )


# "palette" contract is the DOMINANT non-transparent pixel color per
def _decode_tray_png_rgba(path: Path) -> list[tuple[int, int, int, int]]:
    """Decode a 32x32 RGBA tray PNG to a flat RGBA pixel list (stdlib)."""
    import zlib

    data = path.read_bytes()
    assert data.startswith(PNG_MAGIC), f"{path.name} is not a PNG"
    pos = 8
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        chunk_type = data[pos + 4 : pos + 8]
        if chunk_type == b"IDAT":
            idat += data[pos + 8 : pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = TRAY_ICON_SIZE * 4
    prev = bytearray(stride)
    pixels: list[tuple[int, int, int, int]] = []
    offset = 0
    for _row in range(TRAY_ICON_SIZE):
        (filter_type,) = raw[offset : offset + 1]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride
        if filter_type == 1:
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                up_left = prev[i - 4] if i >= 4 else 0
                pred = left + prev[i] - up_left
                pa, pb, pc = abs(pred - left), abs(pred - prev[i]), abs(pred - up_left)
                pr = left if (pa <= pb and pa <= pc) else (prev[i] if pb <= pc else up_left)
                line[i] = (line[i] + pr) & 0xFF
        pixels.extend((line[i], line[i + 1], line[i + 2], line[i + 3]) for i in range(0, stride, 4))
        prev = line
    return pixels


def _dominant_tray_color(path: Path) -> tuple[int, int, int]:
    """The most frequent non-transparent pixel color of a tray PNG."""
    from collections import Counter

    counts: Counter[tuple[int, int, int]] = Counter()
    for r, g, b, a in _decode_tray_png_rgba(path):
        if a > 0:
            counts[(r, g, b)] += 1
    assert counts, f"{path.name} is fully transparent"
    return counts.most_common(1)[0][0]


def test_committed_tray_png_colors_match_mjs_palette() -> None:
    """Each state icon's dominant color equals its ``trayStateColors`` value."""
    mjs_palette = _mjs_palette()
    for state in STATE_ICONS:
        dominant = _dominant_tray_color(TRAY_DIR / f"{state}.png")
        assert dominant == mjs_palette[state], (
            f"{state}.png dominant color {dominant} drifted from the mjs "
            f"trayStateColors value {mjs_palette[state]}, regenerate with "
            "`python scripts/build/generate_tray_icons.py` and commit."
        )
