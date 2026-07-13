"""Regenerate all icon PNGs / ICOs from the new transparent logo design.

Requires PIL (Pillow).  Run from project root:
    python scripts/generate_icons_new.py
"""
import os

from PIL import Image, ImageDraw

# ── Paths (relative to project root) ─────────────────────────────────
ASSETS_DIR = "voice_typer/server/assets"
RESOURCES_DIR = "voice_typer/client/resources"
PUBLIC_DIR = "voice_typer/client/src/renderer/public"

# ── Bar definitions from logo.svg (148×148 viewBox) ──────────────────
# Each: (x, y, width, height, rx)
BARS = [
    (18.5, 55.5, 18.5, 37, 9.25),
    (49.3333, 37, 18.5, 74, 9.25),
    (80.1667, 18.5, 18.5, 111, 9.25),
    (111, 45.0938, 18.5, 57.8125, 9.25),
]


def _draw_bars(draw: ImageDraw.Draw, size: int, color: tuple[int, int, int, int]):
    """Draw the 4 rounded bars scaled to *size*."""
    def sx(x): return int(x * size / 148)
    def sy(y): return int(y * size / 148)
    def sr(r): return max(1, int(r * size / 148))
    for x, y, w, h, r in BARS:
        draw.rounded_rectangle(
            [sx(x), sy(y), sx(x + w), sy(y + h)],
            radius=sr(r),
            fill=color,
        )


def make_icon(size: int, color: tuple[int, int, int, int]) -> Image.Image:
    """Create RGBA image with transparent background and colored bars."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_bars(draw, size, color)
    return img


def main():
    # Ensure output dirs exist
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(RESOURCES_DIR, exist_ok=True)
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    # ── TRAY icons (white bars on transparent — colorized by Python) ──
    white = (255, 255, 255, 255)
    for s in [16, 24, 32, 48, 64]:
        img = make_icon(s, white)
        img.save(os.path.join(ASSETS_DIR, f"tray-mic-{s}.png"))
        print(f"  tray-mic-{s}.png  ({img.size})")
    img = make_icon(64, white)
    img.save(os.path.join(ASSETS_DIR, "tray-mic.png"))
    print(f"  tray-mic.png  ({img.size})")

    # ── LOGO PNGs for Python server (black bars on transparent) ───────
    black = (0, 0, 0, 255)
    for s in [64, 256]:
        img = make_icon(s, black)
        img.save(os.path.join(ASSETS_DIR, f"logo-{s}.png"))
        print(f"  logo-{s}.png  ({img.size})")

    # ── ELECTRON resources (light + dark) ────────────────────────────
    for s in [512, 256]:
        img = make_icon(s, black)
        name = "icon.png" if s == 512 else "icon-256.png"
        img.save(os.path.join(RESOURCES_DIR, name))
        print(f"  {name}  ({img.size})")

    for s in [512, 256]:
        img = make_icon(s, white)
        name = "icon-dark.png" if s == 512 else "icon-dark-256.png"
        img.save(os.path.join(RESOURCES_DIR, name))
        print(f"  {name}  ({img.size})")

    # ── FAVICONS (public dir) ────────────────────────────────────────
    for s in [16, 32, 48]:
        img = make_icon(s, black)
        img.save(os.path.join(PUBLIC_DIR, f"favicon-{s}.png"))
        print(f"  favicon-{s}.png  ({img.size})")
        img_d = make_icon(s, white)
        img_d.save(os.path.join(PUBLIC_DIR, f"favicon-dark-{s}.png"))
        print(f"  favicon-dark-{s}.png  ({img_d.size})")

    img = make_icon(180, black)
    img.save(os.path.join(PUBLIC_DIR, "apple-touch-icon.png"))
    print(f"  apple-touch-icon.png  ({img.size})")

    # ── .ICO files ────────────────────────────────────────────────────
    img = make_icon(256, black)
    img.save(
        os.path.join(RESOURCES_DIR, "icon.ico"),
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("  icon.ico  saved")

    img_d = make_icon(256, white)
    img_d.save(
        os.path.join(RESOURCES_DIR, "icon-dark.ico"),
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("  icon-dark.ico  saved")

    print("\n✅  All icons regenerated successfully.")


if __name__ == "__main__":
    main()
