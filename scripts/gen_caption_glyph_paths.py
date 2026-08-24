"""Extract the native Windows caption-button glyphs from Segoe Fluent Icons
as SVG path data for the renderer's window-control icons (TitleBar.tsx).

The Windows 11 caption buttons (minimize / maximize / restore / close) draw
their glyphs from the `Segoe Fluent Icons` font that ships with the OS —
ChromeClose U+E8BB, ChromeMinimize U+E921, ChromeMaximize U+E922,
ChromeRestore U+E923. Rendering these exact outlines (scaled to the same 10px
em DWM uses inside a 46x32 caption button) makes custom title-bar buttons
pixel-identical to the native chrome: solid filled contours, no strokes, no
sub-pixel antialiasing wash.

Usage:
    python scripts/gen_caption_glyph_paths.py

Prints one `d="..."` attribute per glyph, centered in a 10x10 viewBox, ready
to paste into `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx`.
"""

import re

from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT = r"C:\Windows\Fonts\SegoeIcons.ttf"  # Segoe Fluent Icons (Windows 11)
GLYPHS = {
    "ChromeClose": 0xE8BB,
    "ChromeMinimize": 0xE921,
    "ChromeMaximize": 0xE922,
    "ChromeRestore": 0xE923,
}

font = TTFont(FONT)
upm = font["head"].unitsPerEm
cmap = font.getBestCmap()
glyph_set = font.getGlyphSet()

print("upm =", upm)
for name, cp in GLYPHS.items():
    glyph = glyph_set[cmap[cp]]
    bp = BoundsPen(glyph_set)
    glyph.draw(bp)
    xmin, ymin, xmax, ymax = bp.bounds
    print(f"\n== {name} U+{cp:04X} adv={glyph.width} bounds={bp.bounds}")
    s = 10 / upm
    w = (xmax - xmin) * s
    h = (ymax - ymin) * s
    # Font units are y-up; SVG is y-down. Scale by 10/upm, flip y, and center
    # the ink bbox in the 10x10 viewBox.
    t = Transform(s, 0, 0, -s, -xmin * s + (10 - w) / 2, (10 - h) / 2 + ymax * s)
    sp = SVGPathPen(glyph_set)
    glyph.draw(TransformPen(sp, t))
    d = re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group()):.2f}", sp.getCommands())
    print("centered-in-10x10 d =", d)
