"""Tray icon rendering helpers.

ARCH-003: extracted from tray.py to separate the PIL/image-rendering
logic from the menu/callback logic.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, Tuple

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


def _make_icon(state: AppState, size: int = 0) -> Image.Image:
    """Generate a colored microphone icon based on state.

    Uses pre-rendered white microphone PNG (from logo.svg, rendered by
    ``client/scripts/generate-icons.mjs``) and colorizes it per state.
    TRAY-020: If size is 0, auto-detect DPI.
    NEW-DUP-009: the old ``vt_logo.svg`` reference was stale — that file
    was removed; the source SVG now lives at ``client/scripts/logo.svg``.
    """
    if size == 0:
        size = _get_dpi_aware_icon_size()
    cache_key = (state, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]

    colors = {
        AppState.IDLE: (120, 120, 120, 255),
        AppState.RECORDING: (235, 64, 52, 255),
        AppState.TRANSCRIBING: (52, 152, 219, 255),
        AppState.LOADING: (243, 156, 18, 255),
        AppState.ERROR: (231, 76, 60, 255),
        AppState.CANCELLING: (192, 57, 43, 255),
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

    _icon_cache[cache_key] = colored
    return colored
