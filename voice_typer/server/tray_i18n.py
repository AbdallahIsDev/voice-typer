"""TRAY-008: Localization for tray menu labels.

Extracted from ``tray.py`` to separate the i18n concern (locale state +
label dicts + translation function) from the TrayIcon class.

This module is the canonical home for tray i18n. ``tray.py`` re-exports
the public symbols via ``# noqa: F401`` for backward compat with tests
that monkeypatch ``voice_typer.server.tray.set_tray_locale`` /
``voice_typer.server.tray.get_tray_locale``.
"""

from voice_typer.server.branding import APP_NAME
from voice_typer.server.i18n import DEFAULT_LOCALE

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
    "force_cancel_transcription": "Force cancel transcription",
    "undo_last": "Undo Last",
    "microphones": "Microphones",
    "more_microphones": "More microphones...",
    "settings": "Settings",
    "history": "History",
    "help": "Help",
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
    "force_cancel_transcription": "Forzar cancelación de transcripción",
    "undo_last": "Deshacer Último",
    "microphones": "Micrófonos",
    "more_microphones": "Más micrófonos...",
    "settings": "Configuración...",
    "history": "Historial...",
    "help": "Ayuda...",
    "update_available_body": "{app} {version} está disponible (tienes {current})",
}

# S1-CR-47: server-side tray i18n only supported 2 of 8 locales (en, es).
# Switching to any of ar/de/fr/hi/ru/zh fell back to English. These dicts
# provide the fallback so the tray menu, notifications, and tooltip state
# messages are localized even before the renderer pushes its full label
# dict via the set_tray_locale IPC. The renderer's push (which includes
# the 50+ notify.* and state.* keys from i18n.py) still takes precedence
# via register_tray_labels() merging — these dicts are the floor, not
# the ceiling.
_TRAY_LABELS_AR: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "تبديل الإملاء",
    "open_app": "فتح التطبيق",
    "models": "النماذج",
    "restart": "إعادة التشغيل",
    "quit": "خروج",
    "about": "حول",
    "diagnostics": "التشخيصات",
    "recording_active": "التسجيل نشط",
    "update_available": "تحديث متاح",
    "version": "إصدار",
    "force_cancel_transcription": "إلغاء النسخ قسراً",
    "undo_last": "تراجع عن الأخير",
    "microphones": "الميكروفونات",
    "more_microphones": "ميكروفونات أخرى...",
    "settings": "الإعدادات...",
    "history": "السجل...",
    "help": "مساعدة...",
    "update_available_body": "{app} {version} متاح (لديك {current})",
}

_TRAY_LABELS_DE: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Diktat umschalten",
    "open_app": "App öffnen",
    "models": "Modelle",
    "restart": "Neu starten",
    "quit": "Beenden",
    "about": "Über",
    "diagnostics": "Diagnose",
    "recording_active": "Aufnahme aktiv",
    "update_available": "Update verfügbar",
    "version": "Version",
    "force_cancel_transcription": "Transkription abbrechen erzwingen",
    "undo_last": "Letztes rückgängig",
    "microphones": "Mikrofone",
    "more_microphones": "Weitere Mikrofone...",
    "settings": "Einstellungen...",
    "history": "Verlauf...",
    "help": "Hilfe...",
    "update_available_body": "{app} {version} ist verfügbar (Sie haben {current})",
}

_TRAY_LABELS_FR: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Basculer la dictée",
    "open_app": "Ouvrir l'application",
    "models": "Modèles",
    "restart": "Redémarrer",
    "quit": "Quitter",
    "about": "À propos",
    "diagnostics": "Diagnostics",
    "recording_active": "Enregistrement actif",
    "update_available": "Mise à jour disponible",
    "version": "version",
    "force_cancel_transcription": "Forcer l'annulation de la transcription",
    "undo_last": "Annuler le dernier",
    "microphones": "Microphones",
    "more_microphones": "Plus de microphones...",
    "settings": "Paramètres...",
    "history": "Historique...",
    "help": "Aide...",
    "update_available_body": "{app} {version} est disponible (vous avez {current})",
}

_TRAY_LABELS_HI: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "डिक्टेशन टॉगल करें",
    "open_app": "ऐप खोलें",
    "models": "मॉडल",
    "restart": "पुनः प्रारंभ करें",
    "quit": "बाहर निकलें",
    "about": "बारे में",
    "diagnostics": "निदान",
    "recording_active": "रिकॉर्डिंग सक्रिय",
    "update_available": "अपडेट उपलब्ध",
    "version": "संस्करण",
    "force_cancel_transcription": "ट्रांसक्रिप्शन रद्द करने पर बल दें",
    "undo_last": "अंतिम पूर्ववत करें",
    "microphones": "माइक्रोफ़ोन",
    "more_microphones": "अधिक माइक्रोफ़ोन...",
    "settings": "सेटिंग्स...",
    "history": "इतिहास...",
    "help": "सहायता...",
    "update_available_body": "{app} {version} उपलब्ध है (आपके पास {current} है)",
}

_TRAY_LABELS_RU: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Переключить диктовку",
    "open_app": "Открыть приложение",
    "models": "Модели",
    "restart": "Перезапустить",
    "quit": "Выход",
    "about": "О программе",
    "diagnostics": "Диагностика",
    "recording_active": "Запись активна",
    "update_available": "Доступно обновление",
    "version": "версия",
    "force_cancel_transcription": "Принудительно отменить транскрипцию",
    "undo_last": "Отменить последнее",
    "microphones": "Микрофоны",
    "more_microphones": "Больше микрофонов...",
    "settings": "Настройки...",
    "history": "История...",
    "help": "Справка...",
    "update_available_body": "{app} {version} доступна (у вас {current})",
}

_TRAY_LABELS_ZH: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "切换听写",
    "open_app": "打开应用",
    "models": "模型",
    "restart": "重启",
    "quit": "退出",
    "about": "关于",
    "diagnostics": "诊断",
    "recording_active": "录音中",
    "update_available": "有可用更新",
    "version": "版本",
    "force_cancel_transcription": "强制取消转录",
    "undo_last": "撤销上一次",
    "microphones": "麦克风",
    "more_microphones": "更多麦克风...",
    "settings": "设置...",
    "history": "历史记录...",
    "help": "帮助...",
    "update_available_body": "{app} {version} 可用（您当前是 {current}）",
}

_TRAY_LABELS_LOCALES: dict[str, dict[str, str]] = {
    DEFAULT_LOCALE: _TRAY_LABELS_EN,
    "es": _TRAY_LABELS_ES,
    "ar": _TRAY_LABELS_AR,
    "de": _TRAY_LABELS_DE,
    "fr": _TRAY_LABELS_FR,
    "hi": _TRAY_LABELS_HI,
    "ru": _TRAY_LABELS_RU,
    "zh": _TRAY_LABELS_ZH,
}

_tray_locale: str = DEFAULT_LOCALE


def set_tray_locale(locale: str) -> None:
    """Set the tray menu locale.

    Falls back to English if the locale is not supported.
    After calling this, the tray menu must be rebuilt for the new
    labels to take effect.
    """
    global _tray_locale
    _tray_locale = locale if locale in _TRAY_LABELS_LOCALES else DEFAULT_LOCALE


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
