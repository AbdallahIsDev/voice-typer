"""Shared styles, colors, and theme constants for the Voice Typer UI."""

import flet as ft

APP_NAME = "Voice Typer"
SIDEBAR_WIDTH = 200
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 500

# Color constants for easy access
class Colors:
    PRIMARY = ft.Colors.BLUE_600
    PRIMARY_LIGHT = ft.Colors.BLUE_100
    ACCENT = ft.Colors.TEAL_600
    SUCCESS = ft.Colors.GREEN_600
    WARNING = ft.Colors.AMBER_600
    ERROR = ft.Colors.RED_600
    INFO = ft.Colors.BLUE_400
    SURFACE = ft.Colors.GREY_50
    SURFACE_DARK = ft.Colors.GREY_900
    SIDEBAR_BG = ft.Colors.GREY_100
    SIDEBAR_BG_DARK = ft.Colors.GREY_800
    TEXT_PRIMARY = ft.Colors.GREY_900
    TEXT_SECONDARY = ft.Colors.GREY_600
    TEXT_PRIMARY_DARK = ft.Colors.WHITE
    TEXT_SECONDARY_DARK = ft.Colors.GREY_400
    DIVIDER = ft.Colors.GREY_300
    DIVIDER_DARK = ft.Colors.GREY_700
    CARD_BG = ft.Colors.WHITE
    CARD_BG_DARK = ft.Colors.GREY_800

# Navigation items configuration
NAV_ITEMS = {
    "home": {
        "title": "Home",
        "icon": ft.Icons.HOME,
        "description": "Start recording and view status",
    },
    "history": {
        "title": "History",
        "icon": ft.Icons.HISTORY,
        "description": "View past transcriptions",
    },
    "templates": {
        "title": "Templates",
        "icon": ft.Icons.TEXT_SNIPPET,
        "description": "Manage voice templates",
    },
    "vocabulary": {
        "title": "Vocabulary",
        "icon": ft.Icons.SCHOOL,
        "description": "Custom words and corrections",
    },
    "models": {
        "title": "Models",
        "icon": ft.Icons.DOWNLOAD,
        "description": "Manage Whisper models",
    },
    "microphone": {
        "title": "Microphone",
        "icon": ft.Icons.MIC,
        "description": "Test and configure audio input",
    },
    "privacy": {
        "title": "Privacy",
        "icon": ft.Icons.SECURITY,
        "description": "Data management and privacy settings",
    },
}

STATUS_COLORS = {
    "idle": ft.Colors.GREY_500,
    "recording": ft.Colors.RED_500,
    "transcribing": ft.Colors.BLUE_500,
    "loading": ft.Colors.AMBER_500,
    "error": ft.Colors.RED_700,
}

STATUS_LABELS = {
    "idle": "Ready",
    "recording": "Recording...",
    "transcribing": "Transcribing...",
    "loading": "Loading model...",
    "error": "Error",
}

RECORD_BUTTON_SIZE = 100
RECORD_BUTTON_COLOR = ft.Colors.RED_500
RECORD_BUTTON_STOP_COLOR = ft.Colors.GREY_600


def get_theme(dark: bool = False) -> ft.Theme:
    """Return a Material 3 theme for the app."""
    return ft.Theme(
        color_scheme_seed=COLORS["primary"],
        visual_density=ft.VisualDensity.COMFORTABLE,
    )
