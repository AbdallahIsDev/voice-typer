"""Tray icon rendering helpers."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_typer.server.platform_utils import is_windows
from voice_typer.server.tray_types import AppState

if TYPE_CHECKING:
    from PIL import Image as PilImage

log = logging.getLogger(__name__)


def _get_pil_image():
    """Lazy import of PIL.Image to avoid importing heavy dependencies at module load."""
    from PIL import Image

    return Image


def _pil_lanczos() -> int:
    """Return the LANCZOS resampling filter across Pillow versions."""
    from PIL import Image

    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return int(getattr(resampling, "LANCZOS", 1))
    return int(getattr(Image, "LANCZOS", 1))


# _icon_cache is intentionally process-global.
_icon_cache: dict[tuple[AppState, int], "PilImage.Image"] = {}

# DPI never changes within a session, cache the result
_dpi_aware_size_cache: "int | None" = None


def _get_dpi_aware_icon_size() -> int:
    """Query DPI scaling and adjust icon size accordingly."""
    global _dpi_aware_size_cache
    if _dpi_aware_size_cache is not None:
        return _dpi_aware_size_cache

    base_size = 64
    detected = base_size
    if is_windows():
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
            log.debug("[TRAY] DPI scaling probe failed", exc_info=True)
    _dpi_aware_size_cache = detected
    return detected


def invalidate_dpi_cache() -> None:
    """Clear the cached DPI-aware icon size."""
    global _dpi_aware_size_cache
    _dpi_aware_size_cache = None


def _get_icon_path(state: AppState, size: int = 0) -> Path | None:
    """Return the path to the appropriate icon file for the state."""
    if size == 0:
        size = _get_dpi_aware_icon_size()
    asset_dir = Path(__file__).resolve().parent / "assets"

    # on Windows, prefer .ico files for sharper tray icons.
    if is_windows():
        ico_path = asset_dir / f"tray-mic-{state.value}.ico"
        if ico_path.exists():
            return ico_path
        # fall back to the base tray-mic.ico (colorized at
        base_ico = asset_dir / "tray-mic.ico"
        if base_ico.exists():
            return base_ico

    # Fallback: use the PNG icon
    available = [16, 24, 32, 48, 64]
    best = min(available, key=lambda x: abs(x - size))
    png_path = asset_dir / f"tray-mic-{best}.png"
    if png_path.exists():
        return png_path
    return None


# Shape definitions for tray icons.
_ICON_SHAPES = {
    AppState.IDLE: "circle",
    AppState.RECORDING: "square",
    AppState.TRANSCRIBING: "diamond",
    AppState.LOADING: "triangle",
    AppState.ERROR: "triangle",
    AppState.CANCELLING: "square",
}


def _draw_shape(shape: str, size: int, color: tuple):
    """Draw a shape-only icon as fallback when no PNG is available."""
    from PIL import ImageDraw

    image = _get_pil_image()
    img = image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = max(2, size // 8)
    inner = size - 2 * margin

    if shape == "circle":
        draw.ellipse([margin, margin, margin + inner, margin + inner], fill=color)
    elif shape == "square":
        draw.rectangle([margin, margin, margin + inner, margin + inner], fill=color)
    elif shape == "diamond":
        cx, cy = size // 2, size // 2
        half = inner // 2
        draw.polygon([(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)], fill=color)
    elif shape == "triangle":
        cx = size // 2
        draw.polygon([(cx, margin), (margin + inner, margin + inner), (margin, margin + inner)], fill=color)
    else:
        # Unknown shape, fallback to circle
        draw.ellipse([margin, margin, margin + inner, margin + inner], fill=color)

    return img


def _draw_shape_indicator(img, shape: str, color: tuple):
    """Overlay a small shape indicator in the bottom-right corner."""
    from PIL import ImageDraw

    img = img.copy()
    draw = ImageDraw.Draw(img)
    img_size = img.size
    if not img_size or len(img_size) != 2:
        log.warning("[TRAY] _draw_shape_indicator: image has invalid size %r, skipping indicator overlay", img_size)
        return img  # Can't draw indicator on image without valid size
    w, h = img_size
    ind_size = max(4, w // 5)  # indicator is ~20% of icon size
    x0 = w - ind_size - 1
    y0 = h - ind_size - 1

    if shape == "circle":
        draw.ellipse([x0, y0, x0 + ind_size, y0 + ind_size], fill=color)
    elif shape == "square":
        draw.rectangle([x0, y0, x0 + ind_size, y0 + ind_size], fill=color)
    elif shape == "diamond":
        cx, cy = x0 + ind_size // 2, y0 + ind_size // 2
        half = ind_size // 2
        draw.polygon([(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)], fill=color)
    elif shape == "triangle":
        draw.polygon([(x0 + ind_size // 2, y0), (x0 + ind_size, y0 + ind_size), (x0, y0 + ind_size)], fill=color)

    return img


def _make_icon(state: AppState, size: int = 0):
    """Generate a colored tray icon based on state."""
    if size == 0:
        size = _get_dpi_aware_icon_size()
    cache_key = (state, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]

    # on Windows, prefer a pre-built STATE-SPECIFIC .ico when
    if is_windows():
        state_ico_path = _get_icon_path(state, size)
        if state_ico_path is not None and state_ico_path.name == f"tray-mic-{state.value}.ico":
            try:
                pil_img = _get_pil_image()
                pre_built = pil_img.open(str(state_ico_path)).convert("RGBA")
                if pre_built.size != (size, size):
                    pre_built = pre_built.resize((size, size), _pil_lanczos())
                _icon_cache[cache_key] = pre_built
                return pre_built
            except Exception:
                log.debug(
                    "[TRAY] Pre-built state ICO load failed, falling back to PNG synthesis",
                    exc_info=True,
                )

    # Color-blind accessible colors.
    colors = {
        AppState.IDLE: (120, 120, 120, 255),
        AppState.RECORDING: (46, 204, 113, 255),  # Bright green
        AppState.TRANSCRIBING: (52, 152, 219, 255),
        AppState.LOADING: (243, 156, 18, 255),
        AppState.ERROR: (231, 76, 60, 255),  # Red
        AppState.CANCELLING: (243, 156, 18, 255),  # Orange
    }
    color = colors.get(state, (120, 120, 120, 255))
    shape = _ICON_SHAPES.get(state, "circle")

    png_loaded = False
    # initialize ``pil_img`` and ``colored`` to ``None`` BEFORE
    pil_img: Any | None = None
    colored: Any | None = None
    try:
        asset_dir = Path(__file__).resolve().parent / "assets"
        available = [16, 24, 32, 48, 64]
        best = min(available, key=lambda x: abs(x - size))
        pil_img = _get_pil_image()
        mic_img = pil_img.open(str(asset_dir / f"tray-mic-{best}.png")).convert("RGBA")
        colored = pil_img.new("RGBA", mic_img.size, color)
        # use getchannel('A') instead of split()[3].
        colored.putalpha(mic_img.getchannel("A"))
        if colored.size != (size, size):
            # PIL 9.1+ moved ``LANCZOS`` to
            colored = colored.resize((size, size), _pil_lanczos())
        png_loaded = True
    except Exception:
        log.debug("[TRAY] PNG icon load failed, using shape fallback", exc_info=True)

    if not png_loaded:
        # No PNG icon available: use shape-only fallback
        colored = _draw_shape(shape, size, color)
    else:
        # PNG loaded, overlay a small shape indicator
        assert colored is not None
        colored = _draw_shape_indicator(colored, shape, color)

    if is_windows() and pil_img is not None and colored is not None:
        # Save as ICO format for Windows tray.
        try:
            import io

            ico_buf = io.BytesIO()
            colored.save(ico_buf, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
            ico_buf.seek(0)
            colored = pil_img.open(ico_buf)
        except Exception:
            log.debug("[TRAY] PIL ICO conversion failed, using PNG", exc_info=True)

    # ``colored`` is guaranteed non-None at this point —
    assert colored is not None
    _icon_cache[cache_key] = colored
    return colored
