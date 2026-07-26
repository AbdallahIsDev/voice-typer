"""Generate a 64x64 microphone icon (ICO) for the installer.

DEAD-015: There are two icon generators in this repo:
  - ``scripts/build/generate_icon.py`` (this file) — generates the
    Windows .ico used by PyInstaller + Inno Setup.
  - ``voice_typer/client/scripts/generate-icons.mjs`` — generates the
    Electron app's PNG icons (different sizes for tray, taskbar, etc.)
    from an SVG source.

They produce DIFFERENT artifacts for DIFFERENT build pipelines and are
NOT duplicates. The TS version is wired to ``npm run prebuild`` in
package.json; this Python version is wired to the CI workflow's
``Generate app icon`` step.

If you need to change the icon, edit ``voice_typer/client/scripts/logo.svg``
and re-run both generators.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw


def make_mic_icon(size=64):
    color = (120, 120, 120, 255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    mic_w, mic_h = size // 5, size // 3
    draw.rounded_rectangle(
        [cx - mic_w, cy - mic_h, cx + mic_w, cy + mic_h // 3],
        radius=mic_w // 2,
        fill=color,
    )
    stand_radius = size // 3
    draw.arc(
        [cx - stand_radius, cy - stand_radius + mic_h // 4, cx + stand_radius, cy + stand_radius],
        start=0,
        end=180,
        fill=color,
        width=max(2, size // 20),
    )
    base_y = cy + stand_radius
    draw.line(
        [cx - stand_radius // 2, base_y, cx + stand_radius // 2, base_y],
        fill=color,
        width=max(2, size // 20),
    )
    return img


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "voice-typer.ico"
    img = make_mic_icon()
    img.save(out, format="ICO", sizes=[(64, 64)])
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
