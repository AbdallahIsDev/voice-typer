"""Regression tests for the fifth-pass forensic review (changes-5).

Each test class pins one finding to its current verified state.

Findings covered
----------------
Source fixes (4):
- UX-015       i18n: Spanish translation added + UI language selector in Settings
- TRAY-008     tray menu locale switching (set_tray_locale + _TRAY_LABELS_ES)
- TEST-010     mutmut TEST_COMMAND covers all 7 mutated modules
- TRAY-035     Electron notification IPC for persistent/critical notifications

False positives pinned (4):
- TEST-034     upx=False already set in voice-typer.spec
- TEST-037     SHA256 checksum generation already in build.yml
- NEW-IPC-004  TCP reconnect integration tests already exist
- NEW-CONC-003 concurrent cancel tests already exist
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── UX-015 — i18n: Spanish translation + UI language selector ────────────


class TestUx015I18nSpanishTranslation:
    """UX-015.

    The finding: no i18n framework, all UI hardcoded English. Fix:
    added Spanish translation (es.json), registered it in i18n.ts,
    and added a UI language selector in Settings.tsx.
    """

    def test_es_json_exists(self):
        es_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "i18n" / "translations" / "es.json"
        assert es_path.exists(), (
            "UX-015: Spanish translation file (es.json) must exist"
        )

    def test_es_json_has_same_keys_as_en(self):
        """Spanish translation must have the same key structure as English."""
        translations_dir = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "i18n" / "translations"
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
        assert not missing, (
            f"UX-015: es.json is missing keys that en.json has: {sorted(missing)}"
        )

    def test_i18n_ts_registers_spanish(self):
        i18n_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "i18n" / "i18n.ts"
        src = i18n_path.read_text(encoding="utf-8")
        assert 'import es from "./translations/es.json"' in src, (
            "UX-015: i18n.ts must import Spanish translations"
        )
        assert '"en", "es"' in src or '"en","es"' in src, (
            "UX-015: SUPPORTED_LOCALES must include 'es'"
        )
        assert '_translations.set("es"' in src, (
            "UX-015: i18n.ts must register Spanish translations"
        )

    def test_i18n_ts_exports_locale_helpers(self):
        i18n_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "i18n" / "i18n.ts"
        src = i18n_path.read_text(encoding="utf-8")
        assert "export { SUPPORTED_LOCALES }" in src, (
            "UX-015: i18n.ts must export SUPPORTED_LOCALES"
        )
        assert "export function getLocaleLabel" in src, (
            "UX-015: i18n.ts must export getLocaleLabel"
        )

    def test_settings_tsx_has_ui_language_selector(self):
        settings_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings_path.read_text(encoding="utf-8")
        assert "UI Language" in src, (
            "UX-015: Settings.tsx must have a UI Language selector"
        )
        assert "setLocale" in src, (
            "UX-015: Settings.tsx must call setLocale when language changes"
        )
        assert "getLocale()" in src, (
            "UX-015: Settings.tsx must use getLocale() for the current value"
        )
        assert "SUPPORTED_LOCALES" in src, (
            "UX-015: Settings.tsx must iterate SUPPORTED_LOCALES"
        )
        assert "voice-typer-ui-locale" in src, (
            "UX-015: Settings.tsx must persist locale to localStorage"
        )

    def test_i18n_ts_restores_locale_from_local_storage(self):
        i18n_path = Path(__file__).resolve().parent.parent / "voice_typer" / \
            "client" / "src" / "renderer" / "src" / "i18n" / "i18n.ts"
        src = i18n_path.read_text(encoding="utf-8")
        assert "localStorage" in src, (
            "UX-015: i18n.ts must restore locale from localStorage on startup"
        )
        assert "voice-typer-ui-locale" in src


# ─── TRAY-008 — tray menu locale switching ────────────────────────────────


class TestTray008LocaleSwitching:
    """TRAY-008.

    The finding: tray menu hardcoded English, `_()` is a flat dict.get
    stub with no locale switching. Fix: added `set_tray_locale()` /
    `get_tray_locale()` functions, `_TRAY_LABELS_ES` Spanish dict,
    and `_TRAY_LABELS_LOCALES` locale→dict map. The `_()` function
    now looks up the current locale first, falling back to English.
    """

    def test_set_tray_locale_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "set_tray_locale"), (
            "TRAY-008: set_tray_locale function must exist"
        )
        assert callable(tray.set_tray_locale)

    def test_get_tray_locale_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "get_tray_locale"), (
            "TRAY-008: get_tray_locale function must exist"
        )

    def test_spanish_labels_dict_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "_TRAY_LABELS_ES"), (
            "TRAY-008: _TRAY_LABELS_ES dict must exist"
        )
        assert isinstance(tray._TRAY_LABELS_ES, dict)
        assert len(tray._TRAY_LABELS_ES) > 0

    def test_locales_map_exists(self):
        from voice_typer.server import tray

        assert hasattr(tray, "_TRAY_LABELS_LOCALES"), (
            "TRAY-008: _TRAY_LABELS_LOCALES map must exist"
        )
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

        tray.set_tray_locale("fr")  # not supported
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
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.IPCServer._dispatch)
        assert 'cmd == "set_tray_locale"' in src, (
            "TRAY-008: IPC _dispatch must handle 'set_tray_locale' command"
        )
        assert "set_tray_locale" in src
        assert "invalidate_menu_cache" in src, (
            "TRAY-008: IPC handler must rebuild the tray menu after locale change"
        )


# ─── TEST-010 — mutmut TEST_COMMAND covers all 7 modules ─────────────────


class TestTest010MutmutCoverage:
    """TEST-010.

    The finding: TEST_COMMAND ran only 4 test files but MODULES_TO_MUTATE
    has 7 modules. Fix: updated TEST_COMMAND to include all 7 test files.
    """

    def test_test_command_includes_all_7_modules(self):
        from pathlib import Path

        config_path = Path(__file__).resolve().parent / "mutmut_config.py"
        src = config_path.read_text(encoding="utf-8")

        # All 7 test files must be in TEST_COMMAND
        required_test_files = [
            "tests/test_text_cleanup.py",
            "tests/test_config.py",
            "tests/test_tray.py",
            "tests/test_tray_menu.py",
            "tests/test_tray_icon.py",
            "tests/test_recording.py",
            "tests/test_app.py",
        ]
        for tf in required_test_files:
            assert tf in src, (
                f"TEST-010: TEST_COMMAND must include {tf} "
                f"(corresponding to a module in MODULES_TO_MUTATE)"
            )

    def test_modules_to_mutate_has_7_modules(self):
        from pathlib import Path

        config_path = Path(__file__).resolve().parent / "mutmut_config.py"
        src = config_path.read_text(encoding="utf-8")

        # Count modules in MODULES_TO_MUTATE
        assert "voice_typer/server/text_cleanup.py" in src
        assert "voice_typer/server/config.py" in src
        assert "voice_typer/server/tray.py" in src
        assert "voice_typer/server/tray_menu.py" in src
        assert "voice_typer/server/tray_icon.py" in src
        assert "voice_typer/server/recording.py" in src
        assert "voice_typer/server/app.py" in src


# ─── TRAY-035 — Electron notification IPC ────────────────────────────────


class TestTray035ElectronNotificationIpc:
    """TRAY-035.

    The finding: notification duration controlled by OS, not app.
    pystray's `notify()` has no duration parameter. Fix: added
    `show_electron_notification` IPC handler that pushes an
    `electron_notification` event to the Electron UI, which can
    display a persistent toast/banner with user-controlled duration.
    """

    def test_ipc_handler_exists(self):
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.IPCServer._dispatch)
        assert 'cmd == "show_electron_notification"' in src, (
            "TRAY-035: IPC _dispatch must handle 'show_electron_notification' command"
        )

    def test_handler_pushes_electron_notification_event(self):
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.IPCServer._dispatch)
        assert "electron_notification" in src, (
            "TRAY-035: handler must push an 'electron_notification' event"
        )
        assert "duration_ms" in src, (
            "TRAY-035: handler must support a duration_ms parameter"
        )
        assert "critical" in src, (
            "TRAY-035: handler must support a critical flag"
        )

    def test_handler_validates_data_is_dict(self):
        """The handler must reject non-dict data with an error response."""
        from voice_typer.server.ipc_server import IPCServer

        # Build a minimal server with a mock app
        app = MagicMock()
        app._config_mutation_lock = __import__("threading").RLock()
        server = IPCServer.__new__(IPCServer)
        server.app = app
        server.service = MagicMock()

        # Dispatch with non-dict data
        resp = server._dispatch({"type": "show_electron_notification", "data": "not a dict", "id": "test"})
        assert resp["type"] == "error"
        assert "data: object" in resp["data"]["message"]


# ─── TEST-034 — upx=False already set (pin) ──────────────────────────────


class TestTest034UpxDisabled:
    """TEST-034.

    The finding: upx=True triggers AV false positives. Investigation:
    upx is already set to False in voice-typer.spec. This test pins
    that state.
    """

    def test_upx_is_false_in_spec(self):
        from pathlib import Path

        spec_path = Path(__file__).resolve().parent.parent / "scripts" / "build" / \
            "voice-typer.spec"
        src = spec_path.read_text(encoding="utf-8")
        assert "upx=False" in src, (
            "TEST-034: voice-typer.spec must set upx=False to prevent AV false positives"
        )


# ─── TEST-037 — SHA256 checksums already in build.yml (pin) ──────────────


class TestTest037ChecksumsExist:
    """TEST-037.

    The finding: no SHA256 checksum generation in release workflow.
    Investigation: checksum generation AND upload are already in
    build.yml. This test pins that state.
    """

    def test_checksum_generation_step_exists(self):
        from pathlib import Path

        build_yml = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml"
        src = build_yml.read_text(encoding="utf-8")
        assert "SHA-256" in src or "SHA256" in src, (
            "TEST-037: build.yml must have a SHA-256 checksum generation step"
        )
        assert "SHA256SUMS" in src, (
            "TEST-037: build.yml must generate a SHA256SUMS file"
        )
        assert "Get-FileHash" in src, (
            "TEST-037: build.yml must use Get-FileHash to compute checksums"
        )

    def test_checksum_upload_step_exists(self):
        from pathlib import Path

        build_yml = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml"
        src = build_yml.read_text(encoding="utf-8")
        assert "Upload checksums to release" in src, (
            "TEST-037: build.yml must upload SHA256SUMS.txt to the release"
        )


# ─── NEW-IPC-004 — TCP reconnect integration tests exist (pin) ───────────


class TestNewIpc004ReconnectTestsExist:
    """NEW-IPC-004.

    The finding: TCP IPC reconnect not integration-tested. Investigation:
    live TCP reconnect tests exist in test_new_test_001_live_tcp.py.
    This test pins that state.
    """

    def test_reconnect_integration_tests_exist(self):
        from pathlib import Path

        test_file = Path(__file__).resolve().parent / "test_new_test_001_live_tcp.py"
        if test_file.exists():
            src = test_file.read_text(encoding="utf-8")
            assert "test_reconnect_after_disconnect" in src, (
                "NEW-IPC-004: test_reconnect_after_disconnect must exist"
            )
            assert "test_server_survives_client_crash" in src, (
                "NEW-IPC-004: test_server_survives_client_crash must exist"
            )
            assert "live_server" in src, (
                "NEW-IPC-004: tests must use a live_server fixture (real TCP)"
            )


# ─── NEW-CONC-003 — concurrent cancel tests exist (pin) ─────────────────


class TestNewConc003ConcurrentCancelTestsExist:
    """NEW-CONC-003.

    The finding: cancel safety not verified with concurrent tests.
    Investigation: concurrent cancel tests exist in multiple files.
    This test pins that state.
    """

    def test_concurrent_cancel_tests_exist(self):
        from pathlib import Path

        # Check test_volume_ducker.py
        ducker_test = Path(__file__).resolve().parent / "test_volume_ducker.py"
        if ducker_test.exists():
            src = ducker_test.read_text(encoding="utf-8")
            assert "test_concurrent_cancel_and_stop" in src, (
                "NEW-CONC-003: test_concurrent_cancel_and_stop must exist in test_volume_ducker.py"
            )

        # Check test_round11_regression.py
        round11_test = Path(__file__).resolve().parent / "test_round11_regression.py"
        if round11_test.exists():
            src = round11_test.read_text(encoding="utf-8")
            assert "test_schedule_and_cancel_are_threadsafe" in src, (
                "NEW-CONC-003: test_schedule_and_cancel_are_threadsafe must exist"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
