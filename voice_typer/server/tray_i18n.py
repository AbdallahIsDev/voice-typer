"""TRAY-008: Localization for tray menu labels.

Extracted from ``tray.py`` to separate the i18n concern (locale state +
label dicts + translation function) from the TrayIcon class.

This module is the canonical home for tray i18n. ``tray.py`` re-exports
the public symbols via ``# noqa: F401`` for backward compat with tests
that monkeypatch ``voice_typer.server.tray.set_tray_locale`` /
``voice_typer.server.tray.get_tray_locale``.
"""

from voice_typer.server.branding import APP_NAME

_TRAY_LABELS_EN: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Toggle Dictation",
    "open_app": "Open App",
    "models": "Models",
    "restart": "Restart",
    "quit": "Quit",
    "about": "About",
    "diagnostics": "Diagnostics",
    "recording_active": "Recording active",
    "update_available": "Update Available",
    "version": "version",
    "force_cancel_transcription": "Cancel Transcription",
    "undo_last": "Undo Last",
    "microphones": "Microphones",
    "more_microphones": "More microphones...",
    "force_cancel_stuck_transcription": "Force Cancel Stuck Transcription",
    "settings": "Settings...",
    "history": "History...",
    "help": "Help...",
    "update_available_body": "{app} {version} is available (you have {current})",
}

_TRAY_LABELS_ES: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Alternar Dictado",
    "open_app": "Abrir Aplicación",
    "models": "Modelos",
    "restart": "Reiniciar",
    "quit": "Salir",
    "about": "Acerca de",
    "diagnostics": "Diagnósticos",
    "recording_active": "Grabación activa",
    "update_available": "Actualización Disponible",
    "version": "versión",
    "force_cancel_transcription": "Cancelar Transcripción",
    "undo_last": "Deshacer Último",
    "microphones": "Micrófonos",
    "more_microphones": "Más micrófonos...",
    "force_cancel_stuck_transcription": "Forzar Cancelación de Transcripción Atascada",
    "settings": "Configuración...",
    "history": "Historial...",
    "help": "Ayuda...",
    "update_available_body": "{app} {version} está disponible (tienes {current})",
}

_TRAY_LABELS_LOCALES: dict[str, dict[str, str]] = {
    "en": _TRAY_LABELS_EN,
    "es": _TRAY_LABELS_ES,
}

_tray_locale: str = "en"


def set_tray_locale(locale: str) -> None:
    """Set the tray menu locale.

    Falls back to English if the locale is not supported.
    After calling this, the tray menu must be rebuilt for the new
    labels to take effect.
    """
    global _tray_locale
    _tray_locale = locale if locale in _TRAY_LABELS_LOCALES else "en"


def get_tray_locale() -> str:
    """Return the current tray locale."""
    return _tray_locale


def register_tray_labels(locale: str, labels: dict[str, str]) -> None:
    """Register translated tray-menu labels for a locale, merging with existing."""
    existing = _TRAY_LABELS_LOCALES.get(locale, {})
    merged = {**existing, **labels}
    _TRAY_LABELS_LOCALES[locale] = merged


def _(key: str) -> str:
    """Return the localized tray label for the given key.

    Looks up the key in the current locale's label dict, falling back
    to English, then to the key itself.
    """
    labels = _TRAY_LABELS_LOCALES.get(_tray_locale, _TRAY_LABELS_EN)
    if key in labels:
        return labels[key]
    if key in _TRAY_LABELS_EN:
        return _TRAY_LABELS_EN[key]
    return key
