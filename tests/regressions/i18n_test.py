"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path


# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestSpanishTranslationComplete:
    """UX-015.

    The finding: no i18n framework, all UI hardcoded English. Fix:
    added Spanish translation (es.json), registered it in i18n.ts,
    and added a UI language selector in Settings.tsx.
    """

    def test_es_json_exists(self):
        es_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "translations"
            / "es.json"
        )
        assert es_path.exists(), "UX-015: Spanish translation file (es.json) must exist"

    def test_es_json_has_same_keys_as_en(self):
        """Spanish translation must have the same key structure as English."""
        translations_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "translations"
        )
        en = json.loads((translations_dir / "en.json").read_text(encoding="utf-8"))
        es = json.loads((translations_dir / "es.json").read_text(encoding="utf-8"))

        def collect_keys(obj, prefix=""):
            keys = set()
            for k, v in obj.items():
                full = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    keys |= collect_keys(v, full)
                else:
                    keys.add(full)
            return keys

        en_keys = collect_keys(en)
        es_keys = collect_keys(es)
        missing = en_keys - es_keys
        assert not missing, f"UX-015: es.json is missing keys that en.json has: {sorted(missing)}"

    def test_i18n_ts_registers_spanish(self):
        # RW-8: KEEP — pins UX-015 (Spanish translation registered in i18n.ts).
        # A behavioral test would need to render a component and verify the
        # Spanish label appears, which is heavy (requires a renderer test
        # harness); the file-content check catches removal of the import
        # or registration directly.
        i18n_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "i18n.ts"
        )
        src = i18n_path.read_text(encoding="utf-8")
        assert 'import es from "./translations/es.json"' in src, "UX-015: i18n.ts must import Spanish translations"
        assert '"en"' in src or '"es"' in src or '"en", "es"' in src or '"en","es"' in src, (
            "UX-015: SUPPORTED_LOCALES must include 'es'"
        )
        assert '_translations.set("es"' in src, "UX-015: i18n.ts must register Spanish translations"

    def test_i18n_ts_exports_locale_helpers(self):
        # RW-8: KEEP — pins UX-015 (i18n.ts exports SUPPORTED_LOCALES and
        # getLocaleLabel). Same rationale as test_i18n_ts_registers_spanish.
        i18n_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "i18n.ts"
        )
        src = i18n_path.read_text(encoding="utf-8")
        assert "export { SUPPORTED_LOCALES }" in src, "UX-015: i18n.ts must export SUPPORTED_LOCALES"
        assert "export function getLocaleLabel" in src, "UX-015: i18n.ts must export getLocaleLabel"

    def test_settings_tsx_has_ui_language_selector(self):
        # RW-8: KEEP — pins UX-015 (UI language selector in
        # GeneralSettingsSection.tsx). A behavioral test would need to
        # render the component and interact with the selector, which is
        # heavy; the file-content check catches removal of the selector
        # directly.
        # UX-015: The UI language selector was refactored out of
        # Settings.tsx into the dedicated GeneralSettingsSection
        # component (see components/settings/GeneralSettingsSection.tsx).
        # We assert against the new location.
        # Note: the label is now translated via t("settings.appLanguage")
        # instead of the hardcoded "UI Language" string.
        settings_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "components"
            / "settings"
            / "GeneralSettingsSection.tsx"
        )
        src = settings_path.read_text(encoding="utf-8")
        assert "settings.appLanguage" in src, (
            "UX-015: GeneralSettingsSection.tsx must have an App Language selector (translated key)"
        )
        assert "setLocale" in src, "UX-015: GeneralSettingsSection.tsx must call setLocale when language changes"
        assert "getLocale()" in src, "UX-015: GeneralSettingsSection.tsx must use getLocale() for the current value"
        assert "SUPPORTED_LOCALES" in src, "UX-015: GeneralSettingsSection.tsx must iterate SUPPORTED_LOCALES"
        assert "voice-typer-ui-locale" in src, "UX-015: Settings.tsx must persist locale to localStorage"

    def test_i18n_ts_restores_locale_from_local_storage(self):
        # RW-8: KEEP — pins UX-015 (i18n.ts restores locale from localStorage
        # on startup). Same rationale as test_i18n_ts_registers_spanish.
        i18n_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "i18n.ts"
        )
        src = i18n_path.read_text(encoding="utf-8")
        assert "localStorage" in src, "UX-015: i18n.ts must restore locale from localStorage on startup"
        assert "voice-typer-ui-locale" in src


class TestTrayLocaleSwitchingRebuildsMenu:
    """TRAY-008.

    The finding: tray menu hardcoded English, `_()` is a flat dict.get
    stub with no locale switching. Fix: added `set_tray_locale()` /
    `get_tray_locale()` functions, `_TRAY_LABELS_ES` Spanish dict,
    and `_TRAY_LABELS_LOCALES` locale→dict map. The `_()` function
    now looks up the current locale first, falling back to English.
    """

    def test_set_tray_locale_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "set_tray_locale"), "TRAY-008: set_tray_locale function must exist"
        assert callable(tray.set_tray_locale)

    def test_get_tray_locale_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "get_tray_locale"), "TRAY-008: get_tray_locale function must exist"

    def test_spanish_labels_dict_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "_TRAY_LABELS_ES"), "TRAY-008: _TRAY_LABELS_ES dict must exist"
        assert isinstance(tray._TRAY_LABELS_ES, dict)
        assert len(tray._TRAY_LABELS_ES) > 0

    def test_locales_map_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "_TRAY_LABELS_LOCALES"), "TRAY-008: _TRAY_LABELS_LOCALES map must exist"
        assert "en" in tray._TRAY_LABELS_LOCALES
        assert "es" in tray._TRAY_LABELS_LOCALES

    def test_locale_switching_to_spanish(self):
        """Switching to Spanish must return Spanish labels."""
        from voice_typer.server import tray

        # Reset to English first
        tray.set_tray_locale("en")
        assert tray._("toggle_dictation") == "Toggle Dictation"

        # Switch to Spanish
        tray.set_tray_locale("es")
        assert tray._("toggle_dictation") == "Alternar Dictado"
        assert tray._("quit") == "Salir"
        assert tray._("models") == "Modelos"

        # Reset to English for other tests
        tray.set_tray_locale("en")

    def test_unknown_locale_falls_back_to_english(self):
        """An unsupported locale must fall back to English."""
        from voice_typer.server import tray

        tray.set_tray_locale("xx")  # not supported
        assert tray.get_tray_locale() == "en"  # falls back
        assert tray._("toggle_dictation") == "Toggle Dictation"

    def test_unknown_key_falls_back_to_english_then_key(self):
        """An unknown key must fall back to English, then to the key itself."""
        from voice_typer.server import tray

        tray.set_tray_locale("es")
        # Key that exists in neither Spanish nor English
        assert tray._("nonexistent_key") == "nonexistent_key"
        # Reset
        tray.set_tray_locale("en")

    def test_ipc_set_tray_locale_handler_exists(self):
        # RW-8: KEEP — pins TRAY-008 (IPC handler for set_tray_locale exists
        # and rebuilds the tray menu). The sibling test_locale_switching_to_spanish
        # tests the locale switching behavior, but doesn't verify the IPC
        # handler exists; the source-string check catches removal of the
        # handler or the invalidate_menu_cache call directly.
        from voice_typer.server import ipc_server

        # REFACTOR: _dispatch was converted to a command registry.
        assert "set_tray_locale" in ipc_server.IPCServer._COMMAND_REGISTRY, (
            "TRAY-008: IPC _COMMAND_REGISTRY must include 'set_tray_locale'"
        )
        handler_src = inspect.getsource(ipc_server.IPCServer._handle_set_tray_locale)
        assert "set_tray_locale" in handler_src
        assert "invalidate_menu_cache" in handler_src, (
            "TRAY-008: IPC handler must rebuild the tray menu after locale change"
        )
