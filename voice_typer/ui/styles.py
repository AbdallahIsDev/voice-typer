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
        "icon": "home",
        "description": "Start recording and view status",
    },
    "history": {
        "title": "History",
        "icon": "history",
        "description": "View past transcriptions",
    },
    "templates": {
        "title": "Templates",
        "icon": "templates",
        "description": "Manage voice templates",
    },
    "vocabulary": {
        "title": "Vocabulary",
        "icon": "vocabulary",
        "description": "Custom words and corrections",
    },
    "models": {
        "title": "Models",
        "icon": "models",
        "description": "Manage Whisper models",
    },
    "microphone": {
        "title": "Microphone",
        "icon": "microphone",
        "description": "Test and configure audio input",
    },
    "privacy": {
        "title": "Privacy",
        "icon": "privacy",
        "description": "Data management and privacy settings",
    },
}

STATUS_COLORS = {
    "idle": ft.Colors.GREY_500,
    "recording": ft.Colors.RED_500,
    "transcribing": ft.Colors.BLUE_500,
    "loading": ft.Colors.AMBER_500,
    "error": ft.Colors.RED_700,
    "paused": ft.Colors.PURPLE_500,
    "warming_up": ft.Colors.ORANGE_500,
    "downloading": ft.Colors.BLUE_GREY_500,
    "processing": ft.Colors.TEAL_500,
    "cancelling": ft.Colors.RED_300,
    "setup": ft.Colors.BLUE_700,
    "not_configured": ft.Colors.GREY_400,
}

# UX-040: Expanded STATUS_LABELS from 5 to 12 states
STATUS_LABELS = {
    "idle": "Ready",
    "recording": "Recording...",
    "transcribing": "Transcribing...",
    "loading": "Loading model...",
    "error": "Error",
    "paused": "Paused",
    "warming_up": "Warming up...",
    "downloading": "Downloading...",
    "processing": "Processing...",
    "cancelling": "Cancelling...",
    "setup": "Setting up...",
    "not_configured": "Not configured",
}

RECORD_BUTTON_SIZE = 100
RECORD_BUTTON_COLOR = ft.Colors.RED_500
RECORD_BUTTON_STOP_COLOR = ft.Colors.GREY_600


def get_theme(dark: bool = False) -> ft.Theme:
    """UX-002: Return a Material 3 theme for the app.

    Now wired into the Flet page via ui/app.py.
    """
    if dark:
        return ft.Theme(
            color_scheme_seed=Colors.PRIMARY,
            visual_density=ft.VisualDensity.COMFORTABLE,
            brightness=ft.Brightness.DARK,
        )
    return ft.Theme(
        color_scheme_seed=Colors.PRIMARY,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


# UX-036: High-contrast theme
def get_high_contrast_theme(dark: bool = False) -> ft.Theme:
    """Return a high-contrast accessibility theme."""
    if dark:
        return ft.Theme(
            color_scheme_seed=ft.Colors.WHITE,
            visual_density=ft.VisualDensity.COMFORTABLE,
            brightness=ft.Brightness.DARK,
        )
    return ft.Theme(
        color_scheme_seed=ft.Colors.BLACK,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


# UX-027: Relative time formatter
def format_relative_time(timestamp_str: str) -> str:
    """Convert ISO timestamp to relative time string.

    Returns strings like 'just now', '3 min ago', '2 hours ago',
    'yesterday', or the original date for older entries.
    """
    if not timestamp_str:
        return ""
    try:
        from datetime import datetime, timedelta, timezone
        # Parse ISO timestamp
        ts = timestamp_str
        if 'T' in ts:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(ts)
        # If naive, assume local time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins} min ago"
        if seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        if seconds < 172800:
            return "yesterday"
        if seconds < 604800:
            days = int(seconds / 86400)
            return f"{days} days ago"
        # Older than a week — show date
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return timestamp_str
