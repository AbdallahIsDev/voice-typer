"""Shared styles, colors, and theme constants for the Voice Typer UI."""

import flet as ft
import sys


def is_windows_dark_mode() -> bool:
    """Query Windows AppsUseLightTheme registry value."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except Exception:
        return False


APP_NAME = "Voice Typer"
SIDEBAR_WIDTH = 220
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 700
PAGE_MAX_WIDTH = 1200

# ── Global design tokens (dark-mode-first) ──────────────────────────────

class Colors:
    # Accent
    PRIMARY = "#3B82F6"
    PRIMARY_HOVER = "#2563EB"
    GREEN = "#22C55E"
    RED = "#DC2626"
    RED_MUTED = "#F87171"

    # Background layers
    APP_BG = "#0F1117"
    SIDEBAR_BG = "#13151C"
    SURFACE = "#FFFFFF"
    SURFACE_DARK = "#0F1117"
    SIDEBAR_BG_DARK = "#13151C"
    ELEVATED = "#1F2231"

    # Text
    TEXT_PRIMARY = "#F1F1F3"
    TEXT_SECONDARY = "rgba(241,241,243,0.55)"
    TEXT_MUTED = "rgba(241,241,243,0.30)"
    TEXT_DISABLED = "rgba(241,241,243,0.18)"
    TEXT_PRIMARY_DARK = "#F1F1F3"
    TEXT_SECONDARY_DARK = "rgba(241,241,243,0.55)"

    # Borders
    DIVIDER = "#E5E7EB"
    DIVIDER_DARK = "rgba(255,255,255,0.07)"
    BORDER_DEFAULT = "rgba(255,255,255,0.07)"
    BORDER_HOVER = "rgba(255,255,255,0.12)"
    BORDER_FOCUS = "rgba(59,130,246,0.45)"
    BORDER_DANGER = "rgba(220,38,38,0.30)"

    # Legacy aliases for backward compat
    SUCCESS = "#22C55E"
    WARNING = "#F59E0B"
    ERROR = "#DC2626"
    INFO = "#3B82F6"
    CARD_BG = "#FFFFFF"
    CARD_BG_DARK = "#1A1D27"
    PRIMARY_LIGHT = "#60A5FA"
    ACCENT = "#3B82F6"

    @staticmethod
    def sidebar_bg(dark: bool) -> str:
        return Colors.SIDEBAR_BG_DARK if dark else "#F5F5F5"

    @staticmethod
    def surface(dark: bool) -> str:
        return Colors.SURFACE_DARK if dark else Colors.SURFACE

    @staticmethod
    def card_bg(dark: bool) -> str:
        return Colors.CARD_BG_DARK if dark else Colors.CARD_BG

    @staticmethod
    def text_primary(dark: bool) -> str:
        return Colors.TEXT_PRIMARY_DARK if dark else "#111827"

    @staticmethod
    def text_secondary(dark: bool) -> str:
        return Colors.TEXT_SECONDARY_DARK if dark else "#6B7280"

    @staticmethod
    def divider(dark: bool) -> str:
        return Colors.DIVIDER_DARK if dark else Colors.DIVIDER


# ── Border helpers ──────────────────────────────────────────────────────

def border_default() -> ft.Border:
    return ft.Border(
        left=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        top=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        right=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
    )


def border_card() -> ft.Border:
    return ft.Border(
        left=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        top=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        right=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
    )


# ── Shadow helpers ──────────────────────────────────────────────────────

def card_shadow() -> ft.BoxShadow:
    return ft.BoxShadow(blur_radius=20, spread_radius=0, offset=ft.Offset(0, 4), color="rgba(0,0,0,0.20)")


def focus_shadow() -> ft.BoxShadow:
    return ft.BoxShadow(blur_radius=3, spread_radius=0, offset=ft.Offset(0, 0), color="rgba(59,130,246,0.08)")


# ── Navigation items configuration ──────────────────────────────────────

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
    "settings": {
        "title": "Settings",
        "icon": "settings",
        "description": "Application settings",
    },
}

STATUS_COLORS = {
    "idle": "#22C55E",
    "recording": "#DC2626",
    "transcribing": "#3B82F6",
    "loading": "#F59E0B",
    "error": "#DC2626",
    "paused": "#A855F7",
    "warming_up": "#F97316",
    "downloading": "#64748B",
    "processing": "#14B8A6",
    "cancelling": "#F87171",
    "setup": "#3B82F6",
    "not_configured": "rgba(241,241,243,0.30)",
}

STATUS_LABELS = {
    "idle": "READY",
    "recording": "RECORDING",
    "transcribing": "TRANSCRIBING",
    "loading": "LOADING",
    "error": "ERROR",
    "paused": "PAUSED",
    "warming_up": "WARMING UP",
    "downloading": "DOWNLOADING",
    "processing": "PROCESSING",
    "cancelling": "CANCELLING",
    "setup": "SETTING UP",
    "not_configured": "NOT CONFIGURED",
}

RECORD_BUTTON_SIZE = 72
RECORD_BUTTON_COLOR = "#DC2626"
RECORD_BUTTON_STOP_COLOR = "rgba(241,241,243,0.18)"


def get_theme(dark: bool = False) -> ft.Theme:
    return ft.Theme(
        color_scheme_seed="#3B82F6",
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


def get_high_contrast_theme(dark: bool = False) -> ft.Theme:
    seed = ft.Colors.WHITE if dark else ft.Colors.BLACK
    return ft.Theme(
        color_scheme_seed=seed,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


def format_relative_time(timestamp_str: str) -> str:
    if not timestamp_str:
        return ""
    try:
        from datetime import datetime, timezone
        ts = timestamp_str
        if 'T' in ts:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(ts)
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
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return timestamp_str
