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
import re
from pathlib import Path


# the previous Linux test-env shim that aliased
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
        # KEEP — pins  (Spanish translation registered in i18n.ts).
        # A behavioral test would need to render a component and verify the
        # Spanish label appears, which is heavy (requires a renderer test
        # harness); the file-content check catches removal of the import
        # or registration directly.
        # the 745-LOC ``i18n.ts`` monolith was split into a focused
        # ``i18n/`` package (locale.ts / store.ts / translate.ts / etc.).
        # ``i18n.ts`` is now a thin re-export shim. The ``"es"`` literal
        # lives in ``i18n/locale.ts`` (the SUPPORTED_LOCALES array); the
        # ``ensureLocaleLoaded`` function lives in ``i18n/store.ts`` and
        # is re-exported via ``i18n/index.ts``. Read both leaf modules
        # so the test stays green on the split package.
        i18n_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
        )
        src = "\n".join(
            (i18n_dir / name).read_text(encoding="utf-8") for name in ("i18n.ts", "index.ts", "locale.ts", "store.ts")
        )
        # non-English locales are now dynamically imported via
        # ensureLocaleLoaded() rather than static `import es from ...`.
        # Verify "es" is listed in SUPPORTED_LOCALES and that the dynamic
        # import mechanism (ensureLocaleLoaded) exists.
        assert '"es"' in src, "UX-015: SUPPORTED_LOCALES must include 'es'"
        assert "ensureLocaleLoaded" in src, "UX-015: i18n.ts must define ensureLocaleLoaded for dynamic locale loading"

    def test_i18n_ts_exports_locale_helpers(self):
        # KEEP — pins  (i18n.ts exports SUPPORTED_LOCALES and
        # getLocaleLabel). Same rationale as test_i18n_ts_registers_spanish.
        # ``i18n.ts`` is now a re-export shim; the actual exports live in
        # ``i18n/locale.ts`` and are re-exported via ``i18n/index.ts``.
        i18n_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
        )
        src = "\n".join((i18n_dir / name).read_text(encoding="utf-8") for name in ("i18n.ts", "index.ts", "locale.ts"))
        assert "export { SUPPORTED_LOCALES }" in src, "UX-015: i18n.ts must export SUPPORTED_LOCALES"
        assert "export function getLocaleLabel" in src, "UX-015: i18n.ts must export getLocaleLabel"

    def test_settings_tsx_has_ui_language_selector(self):
        # KEEP — pins  (UI language selector in
        # GeneralSettingsSection.tsx). A behavioral test would need to
        # render the component and interact with the selector, which is
        # heavy; the file-content check catches removal of the selector
        # directly.
        # The UI language selector was refactored out of
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
        # KEEP — pins  (i18n.ts restores locale from localStorage
        # on startup). Same rationale as test_i18n_ts_registers_spanish.
        # ``i18n.ts`` is now a re-export shim; the localStorage read lives
        # in ``i18n/index.ts`` (initI18n auto-restore) and ``i18n/store.ts``
        # (setLocale persistence). Read both leaf modules.
        i18n_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
        )
        src = "\n".join((i18n_dir / name).read_text(encoding="utf-8") for name in ("i18n.ts", "index.ts", "store.ts"))
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

        tray.set_tray_locale("zz")  # not supported
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
        # KEEP — pins  (IPC handler for set_tray_locale exists
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


class TestRendererLocalesUseAppNamePlaceholder:
    """HU-43 / C-BRAND-1: renderer translation files must not contain
    literal brand strings — the ``{appName}`` placeholder is substituted
    with ``APP_NAME`` at load time via ``_withAppName`` (store.ts), so a
    product rename touches one constant instead of hundreds of strings.

    ``scripts/check_branding.py`` deliberately EXEMPTS renderer
    translations (per C-BRAND-1 rationale), so this guard test is the CI
    enforcement for the rule.
    """

    TRANSLATIONS_DIR = (
        Path(__file__).resolve().parent.parent.parent
        / "voice_typer"
        / "client"
        / "src"
        / "renderer"
        / "src"
        / "i18n"
        / "translations"
    )
    LOCALES = ("ar", "de", "en", "es", "fr", "hi", "ru", "zh")

    def test_no_literal_brand_string_in_any_locale(self):
        """None of the 8 renderer locale files may contain a literal
        brand string (the placeholder must be used instead)."""
        for name in self.LOCALES:
            path = self.TRANSLATIONS_DIR / f"{name}.json"
            assert path.exists(), f"{name}.json must exist"
            text = path.read_text(encoding="utf-8")
            assert "Voice Typer" not in text, (
                f"{name}.json: literal brand string found — must use the "
                "{appName} placeholder (C-BRAND-1 / HU-43)"
            )

    def test_en_uses_appname_placeholder(self):
        """The placeholder pattern must actually be in use (guards
        against a vacuous migration that deleted the brand entirely)."""
        en = json.loads(
            (self.TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8")
        )
        assert "{appName}" in json.dumps(en), (
            "en.json must use the {appName} placeholder somewhere (HU-43)"
        )


class TestNotifyPushCoversServerKeys:
    """Drift guard for the ``set_tray_locale`` ``notify.*`` push.

    ``trayLabelsForLocale`` (``i18n/push.ts``) maps every live server
    tray-notification key (a ``notify.*`` literal that appears at a call
    site in ``voice_typer/server`` AND is defined in the server's
    ``_INITIAL_LABELS`` fallback) 1:1 to a renderer ``notify.*``
    translation, so OS notifications follow the renderer locale like the
    tray tooltip state messages. Two invariants keep that contract from
    drifting:

    1. Every live server key has a matching renderer ``en.json`` key.
       A new server notification key without a renderer translation
       would silently stay English for non-English locales.
    2. The renderer English value is byte-identical to the server
       fallback, so the English path is unchanged by the push (the
       server registry's existing English entries win via
       ``merge_labels`` setdefault semantics).

    Dead keys (defined in ``_INITIAL_LABELS`` but never referenced, e.g.
    ``notify.recording_controller.mic_unplugged``) are intentionally not
    required to have a renderer translation.
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    TRANSLATIONS_DIR = (
        REPO_ROOT
        / "voice_typer"
        / "client"
        / "src"
        / "renderer"
        / "src"
        / "i18n"
        / "translations"
    )
    SERVER_DIR = REPO_ROOT / "voice_typer" / "server"

    @classmethod
    def _live_server_notify_keys(cls) -> set[str]:
        """Keys referenced as ``"notify.<group>.<key>"`` literals in
        server code (call sites) that also exist in ``_INITIAL_LABELS``.
        Comments are stripped so docstring mentions don't count."""
        from voice_typer.server.i18n import _INITIAL_LABELS

        literals: set[str] = set()
        for py in cls.SERVER_DIR.rglob("*.py"):
            if py.name == "i18n.py":
                continue  # the registry itself — not a call site
            for line in py.read_text(encoding="utf-8").splitlines():
                code = line.split("#", 1)[0]
                for m in re.finditer(r'"notify\.[a-z_]+\.(?:[a-z_]+\.)*[a-z_]+"', code):
                    literals.add(m.group(0)[1:-1])
        return {k for k in literals if k in _INITIAL_LABELS}

    @classmethod
    def _renderer_notify_keys(cls) -> dict[str, str]:
        en = json.loads(
            (cls.TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8")
        )
        result: dict[str, str] = {}
        for group, keys in en["notify"].items():
            for key, value in keys.items():
                result[f"notify.{group}.{key}"] = value
        return result

    def test_live_server_notify_keys_have_renderer_translations(self):
        live = self._live_server_notify_keys()
        assert live, "no live notify keys found — extraction may be broken"
        renderer = set(self._renderer_notify_keys())
        missing = live - renderer
        assert not missing, (
            "server notify keys without a renderer en.json translation "
            "(they would stay English for non-English locales): "
            f"{sorted(missing)}"
        )

    def test_renderer_notify_english_matches_server_fallback(self):
        """Byte-identical English keeps the en-path notifications
        unchanged after the push (server's own English wins via
        ``merge_labels`` setdefault). Brand placeholders are normalized
        before comparing: the renderer must use ``{appName}``
        (C-BRAND-1, substituted with APP_NAME at registration) where
        the server text carries the literal brand or the ``{app}``
        token (formatted with the same APP_NAME at call time)."""
        from voice_typer.server.branding import APP_NAME
        from voice_typer.server.i18n import _INITIAL_LABELS

        def normalize(value: str) -> str:
            return value.replace("{appName}", APP_NAME).replace(
                "{app}", APP_NAME
            )

        renderer = self._renderer_notify_keys()
        differing = [
            (key, renderer[key], _INITIAL_LABELS[key])
            for key in self._live_server_notify_keys()
            if key in renderer
            and normalize(renderer[key]) != normalize(_INITIAL_LABELS[key])
        ]
        assert not differing, (
            "renderer notify English drifted from the server fallback: "
            + "; ".join(f"{k}: {r!r} != {s!r}" for k, r, s in differing)
        )


class TestTrayTooltipHotkeyWordingRoundTrip:
    """Round-trip guard for the tray tooltip's hotkey wording.

    The tray tooltip state messages that reference the hotkey
    (``state.recording_controller.model_failed_retry``,
    ``state.model_manager.loading``,
    ``state.model_manager.load_failed_retry``) exist on BOTH sides of
    the IPC boundary: the server's ``_INITIAL_LABELS`` registry (the
    pre-push English fallback) and the renderer's ``trayState.*``
    translations, pushed 1:1 via ``trayLabelsForLocale`` in
    ``i18n/push.ts`` so the tooltip follows the renderer locale. The
    wording must stay generic ("press your hotkey") because the user
    can remap the hotkey — a hardcoded "F2" in any locale would show a
    stale key to a user who changed it.

    Invariants under test:

    1. Every server ``state.*`` message that mentions the hotkey is
       pushed (has a ``trayState.*`` mapping) and the renderer English
       says the same thing.
    2. The English wording agrees VERBATIM between the two sides (same
       contract as the ``notify.*`` push — the server's English wins
       via ``merge_labels`` setdefault, so a drift would silently
       change the English tooltip for everyone).
    3. No locale's ``trayState`` tooltip hardcodes a concrete key
       (``F2``, ``Ctrl+B``, …) — "no F2 drift across languages".
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    TRANSLATIONS_DIR = (
        REPO_ROOT
        / "voice_typer"
        / "client"
        / "src"
        / "renderer"
        / "src"
        / "i18n"
        / "translations"
    )
    PUSH_TS = (
        REPO_ROOT
        / "voice_typer"
        / "client"
        / "src"
        / "renderer"
        / "src"
        / "i18n"
        / "push.ts"
    )
    LOCALES = ["en", "ar", "de", "es", "fr", "hi", "ru", "zh"]

    # Concrete key names / glyphs that must never appear in a tooltip
    # string — the wording must reference "your hotkey", never a
    # specific key (the user may have remapped it).
    _CONCRETE_KEY = re.compile(
        r"(?i)\b(F\d{1,2}|Ctrl|Control|Alt|Shift|Cmd|Command|"
        r"Super|Win|Meta|Esc|Tab|Space|Enter)\b"
        r"|<(caps|ctrl|alt|shift|cmd|f\d{1,2})>"
        r"|[\u2303\u2325\u21e7\u2318\u232b]"
    )

    @classmethod
    def _server_state_values(cls) -> dict[str, str]:
        from voice_typer.server.i18n import _INITIAL_LABELS

        return {
            key: value
            for key, value in _INITIAL_LABELS.items()
            if key.startswith("state.") and isinstance(value, str)
        }

    @classmethod
    def _push_state_pairs(cls) -> dict[str, str]:
        """Server ``state.*`` key → renderer ``trayState.*`` key, parsed
        out of ``trayLabelsForLocale`` in ``i18n/push.ts``."""
        src = cls.PUSH_TS.read_text(encoding="utf-8")
        pat = re.compile(
            r'\[\s*"(state\.[a-z_]+(?:\.[a-z_]+)*)",\s*'
            r'"(trayState\.[a-zA-Z.]+)"\s*,?\s*\]',
            re.S,
        )
        return dict(pat.findall(src))

    @classmethod
    def _en_value(cls, key: str) -> str:
        """Look up a dotted ``trayState.*`` key in en.json."""
        en = json.loads(
            (cls.TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8")
        )
        node: object = en
        for part in key.split("."):
            assert isinstance(node, dict), f"{key} resolves to a non-object"
            node = node[part]
        assert isinstance(node, str), f"{key} is not a string"
        return node

    @classmethod
    def _locale_tray_state_values(cls, locale: str) -> list[str]:
        """Every ``trayState`` string value in one locale (flat +
        nested groups)."""
        data = json.loads(
            (cls.TRANSLATIONS_DIR / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        values: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, str):
                values.append(node)
            elif isinstance(node, dict):
                for child in node.values():
                    walk(child)

        walk(data.get("trayState", {}))
        return values

    def test_every_hotkey_state_key_is_pushed_and_wording_matches(self):
        pairs = self._push_state_pairs()
        assert pairs, "no state→trayState push pairs extracted — parser broken"
        server = self._server_state_values()
        hotkey_keys = [
            key
            for key, value in server.items()
            if "hotkey" in value.lower()
        ]
        assert hotkey_keys, "no hotkey-bearing state.* keys found — extraction broken"
        for key in hotkey_keys:
            renderer_key = pairs.get(key)
            assert renderer_key, (
                f"server tray state {key!r} mentions the hotkey but has no "
                "trayState push mapping in i18n/push.ts — non-English "
                "tooltips would stay on the server's English fallback"
            )
            renderer_value = self._en_value(renderer_key)
            assert "hotkey" in renderer_value.lower(), (
                f"renderer {renderer_key} no longer mentions the hotkey "
                f"({renderer_value!r}) while the server {key!r} does"
            )
            assert renderer_value == server[key], (
                f"hotkey wording drifted between the server and the "
                f"renderer for {key}: server {server[key]!r} != "
                f"renderer {renderer_value!r}"
            )

    def test_no_hardcoded_hotkey_in_any_locale_tray_state(self):
        """No locale's tray tooltip hardcodes a concrete key (F2,
        Ctrl+B, …) — the wording must stay generic so a remapped
        hotkey never shows a stale key."""
        offenders: list[tuple[str, str]] = []
        for locale in self.LOCALES:
            for value in self._locale_tray_state_values(locale):
                if self._CONCRETE_KEY.search(value):
                    offenders.append((locale, value))
        assert not offenders, (
            "tray tooltip strings hardcode a concrete hotkey in some "
            "locale (F2/Ctrl/… would go stale when the user remaps): "
            + "; ".join(f"{locale}: {value!r}" for locale, value in offenders)
        )
