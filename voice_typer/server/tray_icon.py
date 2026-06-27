"""Tray icon rendering helpers.

ARCH-003: extracted from tray.py to separate the PIL/image-rendering
logic from the menu/callback logic.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image

from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)

# NEW-MEM-003 / NEW-PERF-005: _icon_cache is intentionally process-global.
# It caches rendered PIL Images keyed by (AppState, size).  With 6
# states × 1-2 DPI-aware sizes = ~12 entries, each ~64×64 RGBA = ~16 KB,
# the total footprint is ~200 KB (not 1 MB as the issue estimated).
# The icons are needed for the lifetime of the tray (the whole process),
# so clearing them would just cause re-rendering on every state change
# — the exact overhead NEW-PERF-005 was designed to eliminate.
_icon_cache: Dict[Tuple[AppState, int], Image.Image] = {}

# NEW-PERF-005: DPI never changes within a session — cache the result
# of _get_dpi_aware_icon_size() after the first call so we don't run
# Win32 GetDC(0) + GetDeviceCaps + ReleaseDC on every tray state
# change (10–30 ms per state change → 0 ms after the first call).
_dpi_aware_size_cache: "int | None" = None


def _get_dpi_aware_icon_size() -> int:
    """TRAY-020: Query DPI scaling and adjust icon size accordingly.

    NEW-PERF-005: cached after the first call.  DPI never changes
    within a session (the user would have to log out + log back in
    after changing display scaling), so re-querying Win32 on every
    tray state change is pure waste.  The cache is at module level
    because the tray icon renderer is a singleton within the process.
    """
    global _dpi_aware_size_cache
    if _dpi_aware_size_cache is not None:
        return _dpi_aware_size_cache

    base_size = 64
    detected = base_size
    if sys.platform == "win32":
        try:
            import ctypes
            hdc = ctypes.windll.user32.GetDC(0)
            if hdc:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                if dpi > 96:
                    scale = dpi / 96.0
                    detected = int(base_size * scale)
        except Exception:
            pass
    _dpi_aware_size_cache = detected
    return detected


def invalidate_dpi_cache() -> None:
    """Clear the cached DPI-aware icon size.

    Useful in tests that mock ctypes.windll with different DPI values.
    In production, DPI never changes mid-session so this is never
    called.
    """
    global _dpi_aware_size_cache
    _dpi_aware_size_cache = None


def _get_icon_path(state: AppState, size: int = 0) -> Optional[Path]:
    """PLAT-024: Return the path to the appropriate icon file for the state.

    On Windows, prefers ICO format (multiple sizes in one file, sharper
    on Windows 11). On other platforms, returns the PNG path.

    Returns None if no icon file is found.
    """
    if size == 0:
        size = _get_dpi_aware_icon_size()
    asset_dir = Path(__file__).resolve().parent / "assets"

    # PLAT-024: on Windows, prefer .ico files for sharper tray icons
    if sys.platform == "win32":
        ico_path = asset_dir / f"tray-mic-{state.value}.ico"
        if ico_path.exists():
            return ico_path

    # Fallback: use the PNG icon
    available = [16, 24, 32, 48, 64]
    best = min(available, key=lambda x: abs(x - size))
    png_path = asset_dir / f"tray-mic-{best}.png"
    if png_path.exists():
        return png_path
    return None


# PLAT-021: Shape definitions for tray icons.
# Current implementation uses color-only differentiation (grey=idle,
# orange=recording, etc.). This is insufficient for color-blind users.
# The shapes below are documented for a future release that will
# render distinct shapes (circle, square, triangle) in addition to
# colors. The current PNG-based approach can't easily add shapes
# without re-rendering the SVGs, so we document the intent here.
_ICON_SHAPES = {
    AppState.IDLE: "circle",
    AppState.RECORDING: "square",
    AppState.TRANSCRIBING: "diamond",
    AppState.LOADING: "triangle",
    AppState.ERROR: "triangle",
    AppState.CANCELLING: "square",
}


def _make_icon(state: AppState, size: int = 0) -> Image.Image:
    """Generate a colored microphone icon based on state.

    Uses pre-rendered white microphone PNG (from logo.svg, rendered by
    ``client/scripts/generate-icons.mjs``) and colorizes it per state.
    TRAY-020: If size is 0, auto-detect DPI.
    NEW-DUP-009: the old ``vt_logo.svg`` reference was stale — that file
    was removed; the source SVG now lives at ``client/scripts/logo.svg``.

    PLAT-021: Icons use both color AND shape to differentiate states.
    Color-only differentiation is insufficient for color-blind users.
    Shape definitions are in _ICON_SHAPES above. Currently shapes are
    documented but not rendered (requires SVG re-rendering). A future
    release should render shape-differentiated icons.
    """
    if size == 0:
        size = _get_dpi_aware_icon_size()
    cache_key = (state, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]

    # PLAT-021/TRAY-006: Color-blind accessible colors.
    # RECORDING: bright green (was red/orange) — clearly distinct from
    #   ERROR red and CANCELLING orange for color-blind users.
    # ERROR: red — keeps the universal "error" association.
    # CANCELLING: orange — distinct from both green and red.
    colors = {
        AppState.IDLE: (120, 120, 120, 255),
        AppState.RECORDING: (46, 204, 113, 255),   # Bright green
        AppState.TRANSCRIBING: (52, 152, 219, 255),
        AppState.LOADING: (243, 156, 18, 255),
        AppState.ERROR: (231, 76, 60, 255),         # Red
        AppState.CANCELLING: (243, 156, 18, 255),   # Orange
    }
    color = colors.get(state, (120, 120, 120, 255))

    try:
        asset_dir = Path(__file__).resolve().parent / "assets"
        available = [16, 24, 32, 48, 64]
        best = min(available, key=lambda x: abs(x - size))
        mic_img = Image.open(str(asset_dir / f"tray-mic-{best}.png")).convert("RGBA")
        colored = Image.new("RGBA", mic_img.size, color)
        # NEW-MEM-004: use getchannel('A') instead of split()[3].
        # split() creates 4 separate channel images (R, G, B, A) even
        # though we only need the alpha channel.  getchannel('A')
        # extracts just the alpha band.  This only runs on cache miss
        # (~12 times per session), so the savings are small, but the
        # code is also clearer about intent.
        colored.putalpha(mic_img.getchannel('A'))
        if colored.size != (size, size):
            colored = colored.resize((size, size), Image.LANCZOS)
    except Exception:
        colored = Image.new("RGBA", (size, size), color)

    if sys.platform == "win32":
        # PLAT-024: Save as ICO format for Windows tray.
        # ICO supports multiple sizes (16, 32, 48, 256) and is the
        # native format for Windows tray icons — sharper than PNG on
        # Windows 11 with per-monitor DPI scaling.
        try:
            import io
            ico_buf = io.BytesIO()
            colored.save(ico_buf, format='ICO', sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
            ico_buf.seek(0)
            colored = Image.open(ico_buf)
        except Exception:
            pass

    _icon_cache[cache_key] = colored
    return colored
