"""Shared styles, color tokens, layout constants, and theme helpers."""

import flet as ft
import sys


def is_windows_dark_mode() -> bool:
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
CONTENT_MAX_WIDTH = 1024
SETTINGS_MAX_WIDTH = 768


class Tokens:
    """Design tokens – dark/light value pairs."""

    # ── Background layers ────────────────────────────────────────────
    BG_APP_DARK = "#101726"
    BG_APP_LIGHT = "#FFFFFF"

    BG_SIDEBAR_DARK = "#0F1521"
    BG_SIDEBAR_LIGHT = "#F0F4F9"

    BG_CARD_DARK = "#152036"
    BG_CARD_LIGHT = "#F1F5F9"

    BG_WORKSPACE_DARK = "#1A2A44"
    BG_WORKSPACE_LIGHT = "#FFFFFF"

    # ── Borders ──────────────────────────────────────────────────────
    BORDER_SUBTLE_DARK = "#333f54"
    BORDER_SUBTLE_LIGHT = "#d4d4d4"

    # ── Text ─────────────────────────────────────────────────────────
    TEXT_PRIMARY_DARK = "#F9FAFB"
    TEXT_PRIMARY_LIGHT = "#0F172A"

    TEXT_SECONDARY_DARK = "#9CA3AF"
    TEXT_SECONDARY_LIGHT = "#475569"

    # ── Accent ───────────────────────────────────────────────────────
    ACCENT_PRIMARY_DARK = "#2563EB"
    ACCENT_PRIMARY_LIGHT = "#1D4ED8"

    ACCENT_DANGER_DARK = "#EF4444"
    ACCENT_DANGER_LIGHT = "#DC2626"

    # ── Window controls ──────────────────────────────────────────────
    WINDOW_CTRL_ICON_DARK = "#FFFFFF"
    WINDOW_CTRL_ICON_LIGHT = "#0F172A"

    # ── Semantic ─────────────────────────────────────────────────────
    SUCCESS_DARK = "#22C55E"
    SUCCESS_LIGHT = "#16A34A"

    WARNING_DARK = "#F59E0B"
    WARNING_LIGHT = "#D97706"

    # ── Gradient helpers ─────────────────────────────────────────────
    SIDEBAR_HOVER_OPACITY = "0.6"

    @staticmethod
    def bg_app(dark: bool) -> str:
        return Tokens.BG_APP_DARK if dark else Tokens.BG_APP_LIGHT

    @staticmethod
    def bg_sidebar(dark: bool) -> str:
        return Tokens.BG_SIDEBAR_DARK if dark else Tokens.BG_SIDEBAR_LIGHT

    @staticmethod
    def bg_card(dark: bool) -> str:
        return Tokens.BG_CARD_DARK if dark else Tokens.BG_CARD_LIGHT

    @staticmethod
    def border_subtle(dark: bool) -> str:
        return Tokens.BORDER_SUBTLE_DARK if dark else Tokens.BORDER_SUBTLE_LIGHT

    @staticmethod
    def text_primary(dark: bool) -> str:
        return Tokens.TEXT_PRIMARY_DARK if dark else Tokens.TEXT_PRIMARY_LIGHT

    @staticmethod
    def text_secondary(dark: bool) -> str:
        return Tokens.TEXT_SECONDARY_DARK if dark else Tokens.TEXT_SECONDARY_LIGHT

    @staticmethod
    def accent_primary(dark: bool) -> str:
        return Tokens.ACCENT_PRIMARY_DARK if dark else Tokens.ACCENT_PRIMARY_LIGHT

    @staticmethod
    def accent_danger(dark: bool) -> str:
        return Tokens.ACCENT_DANGER_DARK if dark else Tokens.ACCENT_DANGER_LIGHT

    @staticmethod
    def window_ctrl_icon(dark: bool) -> str:
        return Tokens.WINDOW_CTRL_ICON_DARK if dark else Tokens.WINDOW_CTRL_ICON_LIGHT

    @staticmethod
    def success(dark: bool) -> str:
        return Tokens.SUCCESS_DARK if dark else Tokens.SUCCESS_LIGHT

    @staticmethod
    def warning(dark: bool) -> str:
        return Tokens.WARNING_DARK if dark else Tokens.WARNING_LIGHT

    @staticmethod
    def sidebar_hover_bg(dark: bool) -> str:
        c = Tokens.BG_CARD_DARK if dark else Tokens.BG_CARD_LIGHT
        return c + "99"

    @staticmethod
    def sidebar_active_bg(dark: bool) -> str:
        return Tokens.BG_CARD_DARK if dark else Tokens.BG_CARD_LIGHT

    @staticmethod
    def sidebar_active_indicator(dark: bool) -> str:
        return Tokens.ACCENT_PRIMARY_DARK if dark else Tokens.ACCENT_PRIMARY_LIGHT

    @staticmethod
    def bg_workspace(dark: bool) -> str:
        return Tokens.BG_WORKSPACE_DARK if dark else Tokens.BG_WORKSPACE_LIGHT


# ── Border factories ────────────────────────────────────────────────

def border_card(dark: bool) -> ft.Border:
    c = Tokens.border_subtle(dark)
    return ft.Border(
        left=ft.BorderSide(0.5, c),
        top=ft.BorderSide(0.5, c),
        right=ft.BorderSide(0.5, c),
        bottom=ft.BorderSide(0.5, c),
    )


def border_row(dark: bool) -> ft.Border:
    c = Tokens.border_subtle(dark)
    return ft.Border(bottom=ft.BorderSide(0.5, c))


# ── Shared component style helpers ────────────────────────────────────

def switch_control(value: bool, on_change) -> ft.Switch:
    return ft.Switch(
        value=value,
        on_change=on_change,
        active_color=Tokens.ACCENT_PRIMARY_DARK,
        active_track_color=Tokens.ACCENT_PRIMARY_DARK,
        track_color={ft.ControlState.DEFAULT: "rgba(255,255,255,0.14)"},
        width=48, height=32,
        thumb_color="#FFFFFF",
    )


def input_field(**kwargs) -> ft.TextField:
    return ft.TextField(
        bgcolor=Tokens.BG_CARD_DARK,
        border_radius=8,
        border_color=Tokens.BORDER_SUBTLE_DARK,
        text_size=13,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=7),
        **kwargs,
    )


def dropdown_control(**kwargs) -> ft.Dropdown:
    return ft.Dropdown(
        bgcolor=Tokens.BG_CARD_DARK,
        border_radius=8,
        border_color=Tokens.BORDER_SUBTLE_DARK,
        text_size=13,
        **kwargs,
    )


# ── Status helpers ───────────────────────────────────────────────────

STATUS_COLORS = {
    "idle": "#22C55E",
    "recording": "#FF3333",
    "transcribing": "#2563EB",
    "loading": "#F59E0B",
    "error": "#FF3333",
    "paused": "#A855F7",
    "warming_up": "#F97316",
    "downloading": "#64748B",
    "processing": "#14B8A6",
    "cancelling": "#F87171",
    "setup": "#2563EB",
    "not_configured": "#9CA3AF",
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

RECORD_BUTTON_SIZE = 96
RECORD_BUTTON_COLOR = "#FF3333"
RECORD_BUTTON_STOP_COLOR = "rgba(255,255,255,0.18)"


# ── Navigation items ─────────────────────────────────────────────────

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
        "icon": "settings-03",
        "description": "Application settings",
    },
}


# ── Theme factory ────────────────────────────────────────────────────

def get_theme(dark: bool = False) -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=Tokens.ACCENT_PRIMARY_DARK if dark else Tokens.ACCENT_PRIMARY_LIGHT,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


def get_high_contrast_theme(dark: bool = False) -> ft.Theme:
    seed = ft.Colors.WHITE if dark else ft.Colors.BLACK
    return ft.Theme(
        color_scheme_seed=seed,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


# ── Time helpers ─────────────────────────────────────────────────────

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
