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

_icon_cache: Dict[Tuple[AppState, int], Image.Image] = {}


def _get_dpi_aware_icon_size() -> int:
    """TRAY-020: Query DPI scaling and adjust icon size accordingly."""
    base_size = 64
    if sys.platform == "win32":
        try:
            import ctypes
            hdc = ctypes.windll.user32.GetDC(0)
            if hdc:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                if dpi > 96:
                    scale = dpi / 96.0
                    return int(base_size * scale)
        except Exception:
            pass
    return base_size


def _make_icon(state: AppState, size: int = 0) -> Image.Image:
    """Generate a colored microphone icon based on state.

    Uses pre-rendered white microphone PNG (from vt_logo.svg) and
    colorizes it per state.  TRAY-020: If size is 0, auto-detect DPI.
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
        AppState.PAUSED: (155, 89, 182, 255),
        AppState.WARMING_UP: (230, 126, 34, 255),
        AppState.DOWNLOADING: (52, 73, 94, 255),
        AppState.PROCESSING: (22, 160, 133, 255),
        AppState.CANCELLING: (192, 57, 43, 255),
        AppState.SETUP: (41, 128, 185, 255),
        AppState.NOT_CONFIGURED: (149, 165, 166, 255),
    }
    color = colors.get(state, (120, 120, 120, 255))

    try:
        asset_dir = Path(__file__).resolve().parent / "assets"
        available = [16, 24, 32, 48, 64]
        best = min(available, key=lambda x: abs(x - size))
        mic_img = Image.open(str(asset_dir / f"tray-mic-{best}.png")).convert("RGBA")
        colored = Image.new("RGBA", mic_img.size, color)
        colored.putalpha(mic_img.split()[3])
        if colored.size != (size, size):
            colored = colored.resize((size, size), Image.LANCZOS)
    except Exception:
        colored = Image.new("RGBA", (size, size), color)

    _icon_cache[cache_key] = colored
    return colored
