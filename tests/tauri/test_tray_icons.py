"""Drift guards for the Tauri tray state icons (``src-tauri/icons/tray/``).

The tray icons are REAL committed PNGs rendered from the microphone bar
shape + shared state palette by
``voice_typer/client/scripts/generate-icons.mjs`` (tray-only mode:
``node generate-icons.mjs --tray``, wrapped for repeatability by
``scripts/build/generate_tray_icons.py`` — edit the mjs's ``traySvg`` /
``trayStateColors`` to change them).

They are NOT ``bundle.icon`` app icons — they are ``bundle.resources``
(string entry ``"icons/tray/"`` — Tauri preserves the relative path,
so the files land at ``$RESOURCE/icons/tray/``, mirroring the source
tree), exactly where ``src-tauri/src/tray.rs::load_tray_icon`` reads
them at runtime. That is why ``test_gen_tauri_icons_stub.py``'s
config ↔ git guard excludes ``icons/tray/``.

(Note: Tauri's ``BundleResources`` is an untagged enum — ``List`` of
strings OR a ``Map`` object, never a mix. A map entry can therefore
NOT be added to the string list; the string form is the only option
for a resources array that already uses strings.)

Pairs guarded here (each fails if the two sides drift):

1. ``bundle.resources`` ↔ the tray PNGs — EVERY Tauri config (base +
   each per-arch override) must declare the string ``"icons/tray/"``.
   CI merges a per-arch config over the base, REPLACING the resources
   array — a config missing the entry ships no tray icons and the tray
   silently stops updating (``load_tray_icon`` returns ``None``).
2. committed tray PNGs ↔ the Rust host whitelist
   (``ALLOWED_ICON_NAMES`` in ``src-tauri/src/tray_tests.rs`` /
   ``is_allowed_icon_name``) — a state added to one side but not the
   other breaks the tray icon updates at runtime.
3. the mjs emitter ↔ the Rust whitelist — ``trayStateColors`` keys in
   ``generate-icons.mjs`` must be exactly the 4 whitelisted states.
4. the state palette ↔ the Python host (``tray_icon.py::_make_icon``)
   — both hosts must show identical colors per state.
5. file validity — every committed tray PNG is a 32x32 PNG (the mjs's
   ``trayIconSize``).

``tray-mic-template.png`` (the macOS template source) is intentionally
tracked but NOT whitelisted: it is a documented fallback the host does
not load today (see the mjs comment), so the tracked-set checks admit
it explicitly.
"""

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
# the relative path, so it lands at $RESOURCE/icons/tray/ — the exact
# path tray.rs reads (see the module docstring).
TRAY_RESOURCE_ENTRY = "icons/tray/"

# The macOS template source — tracked + shipped, but NOT a whitelisted
# state icon (the Rust host never loads it; documented fallback).
TEMPLATE_PNG = "tray-mic-template.png"

# The four logical state icons (mirror ALLOWED_ICON_NAMES in tray_tests.rs).
STATE_ICONS = ("idle", "recording", "transcribing", "error")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TRAY_ICON_SIZE = 32


def _all_tauri_configs() -> list[Path]:
    """The base config + every per-arch config (deterministic order)."""
    return [SRC_TAURI / "tauri.conf.json", *sorted(SRC_TAURI.glob("tauri.*.conf.json"))]


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
    """Parse ``ALLOWED_ICON_NAMES`` from ``tray_tests.rs``.

    That constant is the single source of truth the Rust tests pin
    ``is_allowed_icon_name`` against, so parsing it here cross-checks
    the committed PNG set against the ACTUAL runtime whitelist.
    """
    text = TRAY_TESTS_RS.read_text(encoding="utf-8")
    m = re.search(r"ALLOWED_ICON_NAMES:\s*&\[&str\]\s*=\s*&\[(.*?)\]", text, re.S)
    assert m, "ALLOWED_ICON_NAMES constant not found in tray_tests.rs"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _mjs_state_icons() -> set[str]:
    """The ``trayStateColors`` keys in ``generate-icons.mjs`` — the
    filenames the emitter writes."""
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
    """The ``tray_icon.py::_make_icon`` palette as ``{state: (r, g, b)}``.

    Only the ``255``-alpha entries (the full-opacity state colors) are
    parsed — the shape-fallback and indicator colors are out of scope.
    """
    text = TRAY_ICON_PY.read_text(encoding="utf-8")
    return {
        name.lower(): (int(r), int(g), int(b))
        for name, r, g, b in re.findall(r"AppState\.(\w+):\s*\((\d+),\s*(\d+),\s*(\d+),\s*255\)", text)
    }


def test_tray_resource_wired_in_every_config() -> None:
    """Every Tauri config (base + per-arch) ships the tray icons.

    CI builds merge a per-arch config over the base (``--config
    tauri.<os>.conf.json`` REPLACES the resources array), so a config
    missing the map entry ships no tray PNGs on that platform —
    ``load_tray_icon`` returns ``None`` and the tray icon stops
    updating. This is the exact failure the user-visible tray would hit.
    """
    for cfg in _all_tauri_configs():
        bundle = json.loads(cfg.read_text(encoding="utf-8"))["bundle"]
        resources = bundle.get("resources", [])
        assert TRAY_RESOURCE_ENTRY in resources, (
            f"{cfg.name} bundle.resources must include {TRAY_RESOURCE_ENTRY!r} — "
            "without it the tray PNGs never ship (tray.rs reads "
            "$RESOURCE/icons/tray/). Fix with "
            "`python scripts/build/generate_tray_icons.py`."
        )


def test_tracked_tray_pngs_match_rust_whitelist() -> None:
    """The committed state PNGs must equal the Rust host's whitelist.

    Every whitelisted state needs a committed PNG (missing on a fresh
    checkout → tray never updates), and every committed state PNG must
    be whitelisted (a renamed/added state the Rust side doesn't know is
    dead weight that ships but is never loaded).
    """
    tracked = _tracked_tray_pngs()
    whitelist = _rust_whitelist()
    assert whitelist == set(STATE_ICONS), (
        f"Rust whitelist changed ({sorted(whitelist)}) — update STATE_ICONS to match, then regenerate the tray PNGs."
    )
    expected = {f"{name}.png" for name in whitelist} | {TEMPLATE_PNG}
    missing = expected - tracked
    assert not missing, (
        "tray PNGs committed under src-tauri/icons/tray/ must match the Rust "
        f"whitelist — missing: {sorted(missing)}. Regenerate with "
        "`python scripts/build/generate_tray_icons.py` and commit them."
    )
    extra = tracked - expected
    assert not extra, (
        "committed tray PNGs that are neither whitelisted states nor the "
        f"template (dead files — is_allowed_icon_name never loads them): "
        f"{sorted(extra)}"
    )


def test_mjs_emits_exactly_the_whitelisted_states() -> None:
    """``generate-icons.mjs`` must emit exactly the 4 whitelisted states.

    The ``trayStateColors`` keys are the filenames the emitter writes; a
    state added there without a whitelist entry (or vice versa) breaks
    the tray icon updates at runtime.
    """
    mjs_states = _mjs_state_icons()
    assert mjs_states == set(STATE_ICONS), (
        f"generate-icons.mjs trayStateColors keys ({sorted(mjs_states)}) must "
        f"match the Rust whitelist ({sorted(STATE_ICONS)})"
    )


def test_palette_matches_python_host() -> None:
    """Both hosts (Tauri mjs + Python pystray) show identical state colors.

    The mjs palette mirrors ``tray_icon.py::_make_icon``; if they drift,
    the tray icon color for a state differs between the Python and Tauri
    hosts. Only the 4 Tauri states are compared (the Python host has
    extra states — LOADING/CANCELLING — the Tauri host doesn't render).
    """
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
    """Every committed tray PNG is a 32x32 PNG (the mjs ``trayIconSize``).

    A future mjs resize or a corrupt/truncated file fails here instead
    of silently shipping a broken tray icon.
    """
    tracked = _tracked_tray_pngs()
    assert tracked, "no tracked tray PNGs — run the generator and commit them"
    for name in sorted(tracked):
        data = (TRAY_DIR / name).read_bytes()
        assert data.startswith(PNG_MAGIC), f"{name} is not a PNG"
        assert len(data) >= 24 and data[12:16] == b"IHDR", f"{name} is truncated"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (TRAY_ICON_SIZE, TRAY_ICON_SIZE), (
            f"{name} is {width}x{height}, expected {TRAY_ICON_SIZE}x{TRAY_ICON_SIZE}"
        )
