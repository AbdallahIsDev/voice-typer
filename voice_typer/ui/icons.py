"""Hugeicons stroke-rounded icon mapping and helper for Flet UI.

Supports both:
- SVG icons from ``assets/icons/{name}.svg`` (uses ``ft.SvgImage``)
- Hugeicons font icons from the ``ICONS`` codepoint mapping (uses ``ft.Text``)
"""

import os
import flet as ft

# ── SVG icon paths ─────────────────────────────────────────────────────
# Icons with SVG files in assets/icons/ are rendered as ft.SvgImage.
# Add the SVG filename (without path) here to use the SVG renderer.
SVG_ICONS = {
    "home": "home-03.svg",
    "history": "history-anticlockwise-line.svg",
    "templates": "license.svg",
}

_ICONS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")

# ── Font-based icon codepoints ─────────────────────────────────────────
# Map of semantic icon names to their Hugeicons unicode codepoints
ICONS = {
    # Nav items
    # "templates" is now an SVG icon (license.svg)
    "vocabulary": "\u4682",    # hgi-school
    "models": "\u44c0",        # hgi-package
    "microphone": "\u4395",    # hgi-mic-01
    "privacy": "\u46e0",       # hgi-shield-01
    "settings": "\u46c1",      # hgi-settings-01

    # Common controls
    "stop": "\u47f2",          # hgi-stop
    "add": "\u3aa9",           # hgi-add-01
    "add-circle": "\u3aac",    # hgi-add-circle
    "remove": "\u45f5",        # hgi-remove-01
    "delete": "\u3ef9",        # hgi-delete-01
    "delete-sweep": "\u3efa",  # hgi-delete-02
    "edit": "\u3fb1",          # hgi-edit-01
    "search": "\u4695",        # hgi-search-01
    "filter": "\u4031",        # hgi-filter
    "tick": "\u4907",          # hgi-tick-01
    "close": "\u3d42",         # hgi-cancel-01
    "folder": "\u4061",        # hgi-folder-01
    "file": "\u3ff9",          # hgi-file-01
    "sun": "\u4825",           # hgi-sun-01
    "moon": "\u43e3",          # hgi-moon-01
    "mic-off": "\u4397",       # hgi-mic-off-01
    "speech-to-text": "\u4793", # hgi-speech-to-text
    "ai-brain": "\u3abf",      # hgi-ai-brain-01
    "volume-up": "\u4a16",     # hgi-volume-up
    "volume-off": "\u4a15",    # hgi-volume-off
    "sparkles": "\u478e",      # hgi-sparkles
    "sidebar-left": "\u4721",  # hgi-sidebar-left
    "sidebar-right": "\u4723", # hgi-sidebar-right
    
    # Extra mappings to fully replace all used material icons
    "arrow-forward": "\u495d", # hgi-translate (semantic separator for trans)
    "arrow-up-down": "\u3b8c", # hgi-arrow-up-down
    "arrow-left-right": "\u3b70", # hgi-arrow-left-right
    "refresh": "\u45ec",       # hgi-refresh
    "computer": "\u3abf",      # hgi-ai-brain-01
    "cloud-off": "\u3e1f",     # hgi-cloud
    "send": "\u4ab2",          # hgi-zap
    "download-done": "\u4907", # hgi-tick-01
    "play-arrow": "\u453b",    # hgi-play
    "check": "\u4907",         # hgi-tick-01
    "speed": "\u4ab2",         # hgi-zap
    "mic-outlined": "\u4396",  # hgi-mic-02
    "description": "\u3ff9",   # hgi-file-01
    "copy-01": "\u3e74",       # hgi-copy-01
    "import-export": "\u3b70", # hgi-arrow-left-right
    "download": "\u3f70",      # hgi-download-01
    "cloud-download": "\u3e11", # hgi-cloud-download
}


def icon(name: str, color: str = None, size: float = None, tooltip: str = None) -> ft.Control:
    """Helper function to create an icon.

    If ``name`` has a matching SVG file in ``assets/icons/``, renders it
    as an ``ft.SvgImage`` (which supports ``currentColor`` from the SVG
    via the ``color`` parameter).  Otherwise falls back to the Hugeicons
    font icon font using the codepoint from the ``ICONS`` dict.
    """
    svg_filename = SVG_ICONS.get(name)
    if svg_filename:
        svg_path = os.path.join(_ICONS_DIR, svg_filename)
        return ft.Image(
            src=svg_path,
            width=size or 24,
            height=size or 24,
            color=color,
            tooltip=tooltip,
        )

    codepoint = ICONS.get(name, name)
    return ft.Text(
        codepoint,
        font_family="hgi",
        color=color,
        size=size,
        tooltip=tooltip,
    )
