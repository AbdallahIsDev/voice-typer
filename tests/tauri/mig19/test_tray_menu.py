"""
tray/menu port validation (ADR-0020 §6.5).
Gaps / decisions documented (report, do NOT fix, out of scope for this
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]

MAIN_RS = PROJECT_ROOT / "src-tauri" / "src" / "main.rs"
CARGO_TOML = PROJECT_ROOT / "src-tauri" / "Cargo.toml"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
CAPABILITY_JSON = PROJECT_ROOT / "src-tauri" / "capabilities" / "main-runtime.json"

TRAY_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray.py"
TRAY_I18N_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_i18n.py"
TRAY_MENU_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_menu.py"
TRAY_ICON_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_icon.py"
TRAY_MODELS_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_models.py"
SYSTEM_HANDLERS_PY = PROJECT_ROOT / "voice_typer" / "server" / "handlers" / "system_handlers.py"
STARTUP_TASKS_PY = PROJECT_ROOT / "voice_typer" / "server" / "startup_tasks.py"
SERVICE_PY = PROJECT_ROOT / "voice_typer" / "server" / "service.py"


@pytest.fixture(scope="module")
def main_rs_source() -> str:
    """Read src-tauri/src/main.rs as text (for static assertions)."""
    assert MAIN_RS.exists(), f"main.rs not found: {MAIN_RS}"
    return MAIN_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cargo_toml_source() -> str:
    """Read src-tauri/Cargo.toml as text (for static assertions)."""
    assert CARGO_TOML.exists(), f"Cargo.toml not found: {CARGO_TOML}"
    return CARGO_TOML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def capability_json() -> dict:
    """Load + parse the main-runtime capability JSON."""
    assert CAPABILITY_JSON.exists(), f"capability file not found: {CAPABILITY_JSON}"
    return json.loads(CAPABILITY_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tray_py_source() -> str:
    """Read voice_typer/server/tray.py as text."""
    assert TRAY_PY.exists(), f"tray.py not found: {TRAY_PY}"
    return TRAY_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_i18n_py_source() -> str:
    """Read voice_typer/server/tray_i18n.py as text."""
    assert TRAY_I18N_PY.exists(), f"tray_i18n.py not found: {TRAY_I18N_PY}"
    return TRAY_I18N_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_menu_py_source() -> str:
    """Read voice_typer/server/tray_menu.py as text."""
    assert TRAY_MENU_PY.exists(), f"tray_menu.py not found: {TRAY_MENU_PY}"
    return TRAY_MENU_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_icon_py_source() -> str:
    """Read voice_typer/server/tray_icon.py as text."""
    assert TRAY_ICON_PY.exists(), f"tray_icon.py not found: {TRAY_ICON_PY}"
    return TRAY_ICON_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tray_models_py_source() -> str:
    """Read voice_typer/server/tray_models.py as text."""
    assert TRAY_MODELS_PY.exists(), f"tray_models.py not found: {TRAY_MODELS_PY}"
    return TRAY_MODELS_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def system_handlers_source() -> str:
    """Read voice_typer/server/handlers/system_handlers.py as text."""
    assert SYSTEM_HANDLERS_PY.exists(), f"system_handlers.py not found: {SYSTEM_HANDLERS_PY}"
    return SYSTEM_HANDLERS_PY.read_text(encoding="utf-8")


def test_tray_menu_has_six_required_labels_in_order(tray_menu_py_source) -> None:
    """ADR-0020 §6.5: the core menu labels appear in the mandated order."""
    # Locate each label-key call's argument in source order.
    _src = tray_menu_py_source
    _start = _src.find("def build_menu_for_tray(")
    _end = _src.find("\ndef ", _start + 1)
    if _end == -1:
        _end = len(_src)
    build_menu_source = _src[_start:_end]
    key_occurrences = [m.group(1) for m in re.finditer(r'_\(\s*["\']([a-z_]+)["\']', build_menu_source)]
    required_keys = [
        "open_app",
        "force_cancel_transcription",
        "models",
        "restart",
        "quit",
    ]
    for key in required_keys:
        assert key in key_occurrences, (
            f"tray_menu.py must emit the {key!r} label, missing from "
            f"build_menu_for_tray output (ADR-0020 §6.5: menu structure must be "
            f"preserved 1:1)"
        )
    dictation_present = any(k in ("toggle_dictation", "stop_dictation") for k in key_occurrences) or (
        "dictation_key" in build_menu_source
    )
    assert dictation_present, (
        "tray_menu.py must emit the toggle_dictation/stop_dictation label, "
        "missing from build_menu_for_tray output (ADR-0020 §6.5)."
    )
    seen_order = [k for k in key_occurrences if k in required_keys]
    collapsed = [k for i, k in enumerate(seen_order) if i == 0 or k != seen_order[i - 1]]
    assert collapsed == required_keys, (
        f"tray_menu.py menu keys out of order: expected {required_keys}, "
        f"got {seen_order} (the 6 items must appear in the ADR-mandated "
        f"order: Open App → Start Dictation → Cancel → Models → Restart → Quit)"
    )


def test_tray_menu_open_app_is_default_action(tray_menu_py_source) -> None:
    """ADR-0020 §6.5 + tray_menu.py docstring: \"Open App\" is the default."""
    assert "default=open_app_default" in tray_menu_py_source, (
        "Open App must be the default (bold) menu item, left-click "
        "behavior. build_menu_for_tray must pass "
        "default=open_app_default to pystray.MenuItem for Open App."
    )
    assert 'getattr(tray._config, "tray_left_click_action", "open_app")' in tray_menu_py_source, (
        "build_menu_for_tray must read tray_left_click_action from the "
        "config (defaulting to 'open_app') so the tray opens the app "
        "window on left-click unless the user explicitly reconfigures it."
    )


def test_tray_menu_toggle_dictation_includes_hotkey_label(
    tray_menu_py_source,
) -> None:
    """ADR-0020 §6.5: Start Dictation label includes the hotkey hint."""
    assert re.search(
        r"_\(dictation_key\)\s*\}\s*\(\{hotkey_label\}\)",
        tray_menu_py_source,
    ), (
        "Start Dictation label must include the hotkey hint in parens, "
        "expected an f-string like "
        'f"{_(dictation_key)} ({hotkey_label})" so the '
        "user sees e.g. 'Start Dictation (F2)' in the tray menu."
    )


def test_tray_menu_cancel_item_conditional(tray_menu_py_source) -> None:
    """PR-2 Finding #3: Cancel Transcription item is conditional."""
    assert "force_cancel_transcription" in tray_menu_py_source, (
        "build_menu_for_tray must render a Force Cancel item, the menu "
        "entry is a manual escape hatch for stuck transcriptions "
        "(PR-2 Finding #3)."
    )
    assert "if tray._state == AppState.TRANSCRIBING:" in tray_menu_py_source, (
        "The Force Cancel menu item must be added conditionally, only "
        "while a transcription is running, adding it unconditionally "
        "would clutter the menu when nothing is transcribing."
    )


def test_tray_menu_models_submenu_delegated(tray_menu_py_source) -> None:
    """ADR-0020 §6.5: Models submenu is built by a delegated callable."""
    assert "models_sub = tray._build_models_submenu()" in tray_menu_py_source, (
        "build_menu_for_tray must delegate the Models submenu items to "
        "tray._build_models_submenu(), the items are dynamic (reflect "
        "downloaded models) and are built by the TrayIcon."
    )
    assert "pystray.Menu(*models_sub)" in tray_menu_py_source, (
        "build_menu_for_tray must wrap the models submenu items in "
        "pystray.Menu(*models_sub) so they render as a nested submenu "
        "off the 'Models ▸' parent item."
    )


def test_tray_menu_separators_present(tray_menu_py_source) -> None:
    """ADR-0020 §6.5: two separators structure the menu into 3 groups."""
    sep_count = tray_menu_py_source.count("pystray.Menu.SEPARATOR")
    assert sep_count >= 2, (
        f"build_menu_for_tray must insert at least 2 "
        f"pystray.Menu.SEPARATORs to structure the menu into 3 groups "
        f"(Open/Toggle/Cancel | Models/Microphones | Restart/Quit). "
        f"Found only {sep_count}."
    )


def test_tray_locale_english_dict_present(tray_i18n_py_source) -> None:
    """TRAY-008: English locale dict must be defined."""
    assert "_TRAY_LABELS_EN" in tray_i18n_py_source, (
        "tray_i18n.py must define _TRAY_LABELS_EN, the English locale dict "
        "is the default fallback for every tray label (TRAY-008)."
    )
    # Extract the EN dict body.
    en_dict_match = re.search(
        r"_TRAY_LABELS_EN\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
        tray_i18n_py_source,
        re.DOTALL,
    )
    assert en_dict_match, (
        "Could not extract the _TRAY_LABELS_EN dict body, check the type annotation matches dict[str, str]."
    )
    en_dict_body = en_dict_match.group(1)
    required_keys = [
        '"open_app"',
        '"toggle_dictation"',
        '"models"',
        '"restart"',
        '"quit"',
        '"force_cancel_transcription"',
        '"app_name"',
    ]
    for key in required_keys:
        assert key in en_dict_body, (
            f"_TRAY_LABELS_EN must define {key}, the English tray "
            f"locale is the fallback for every other locale's missing "
            f"keys (TRAY-008)."
        )
    # Sanity-check the actual English strings are what users see.
    assert '"open_app": "Open App"' in en_dict_body, (
        "EN open_app label must be 'Open App', this is the user-facing string the host VALIDATE step looks for."
    )
    assert '"quit": "Quit"' in en_dict_body, (
        "EN quit label must be 'Quit', this is the user-facing string the host VALIDATE step looks for."
    )


def test_tray_locale_spanish_dict_present(tray_i18n_py_source) -> None:
    """TRAY-008: Spanish locale dict must be defined."""
    assert "_TRAY_LABELS_ES" in tray_i18n_py_source, (
        "tray_i18n.py must define _TRAY_LABELS_ES. Spanish is the proof-of-concept locale for tray i18n (TRAY-008)."
    )
    es_dict_match = re.search(
        r"_TRAY_LABELS_ES\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
        tray_i18n_py_source,
        re.DOTALL,
    )
    assert es_dict_match, (
        "Could not extract the _TRAY_LABELS_ES dict body, check the type annotation matches dict[str, str]."
    )
    es_dict_body = es_dict_match.group(1)
    required_keys = [
        '"open_app"',
        '"toggle_dictation"',
        '"models"',
        '"restart"',
        '"quit"',
        '"force_cancel_transcription"',
    ]
    for key in required_keys:
        assert key in es_dict_body, (
            f"_TRAY_LABELS_ES must define {key}, every locale must cover all 6 menu keys + the app_name tooltip."
        )
    # Sanity-check the actual Spanish strings match the host VALIDATE step.
    assert '"open_app": "Abrir Aplicación"' in es_dict_body, (
        "ES open_app label must be 'Abrir Aplicación', this is the user-facing string the host VALIDATE step looks for."
    )
    assert '"quit": "Salir"' in es_dict_body, (
        "ES quit label must be 'Salir', this is the user-facing string the host VALIDATE step looks for."
    )
    assert '"toggle_dictation": "Iniciar Dictado"' in es_dict_body, (
        "ES toggle_dictation label must be 'Iniciar Dictado', this is "
        "the user-facing string the host VALIDATE step looks for."
    )


def test_tray_locale_registry_includes_en_and_es(tray_i18n_py_source) -> None:
    """TRAY-008: ``_TRAY_LABELS_LOCALES`` must register en + es."""
    assert "_TRAY_LABELS_LOCALES" in tray_i18n_py_source, (
        "tray_i18n.py must define _TRAY_LABELS_LOCALES, the locale→dict registry that set_tray_locale consults."
    )
    locales_match = re.search(
        r"_TRAY_LABELS_LOCALES\s*:\s*dict\[str,\s*dict\[str,\s*str\]\]\s*=\s*\{(.*?)\}",
        tray_i18n_py_source,
        re.DOTALL,
    )
    assert locales_match, "Could not extract _TRAY_LABELS_LOCALES dict body."
    body = locales_match.group(1)
    assert '"en": _TRAY_LABELS_EN' in body, (
        "_TRAY_LABELS_LOCALES must map 'en' → _TRAY_LABELS_EN. English is the default locale."
    )
    assert '"es": _TRAY_LABELS_ES' in body, (
        "_TRAY_LABELS_LOCALES must map 'es' → _TRAY_LABELS_ES. Spanish is the proof-of-concept non-English locale."
    )


def test_tray_locale_setter_and_getter_present(tray_i18n_py_source) -> None:
    """TRAY-008: ``set_tray_locale`` + ``get_tray_locale`` functions exist."""
    assert "def set_tray_locale(locale: str) -> None:" in tray_i18n_py_source, (
        "tray_i18n.py must define module-level set_tray_locale(locale), the "
        "IPC handler in system_handlers.py imports + calls it directly."
    )
    assert "def get_tray_locale() -> str:" in tray_i18n_py_source, (
        "tray_i18n.py must define module-level get_tray_locale() → str, used by tests to verify the current locale."
    )
    # The setter must fall back to English for unknown locales.
    assert 'locale if locale in _TRAY_LABELS_LOCALES else "en"' in tray_i18n_py_source, (
        "set_tray_locale must fall back to 'en' for unknown locales, "
        "prevents a KeyError if the UI sends an unsupported locale code."
    )


def test_tray_locale_lookup_function_present(tray_i18n_py_source) -> None:
    """TRAY-008: the ``_()`` lookup function translates keys → labels."""
    assert "def _(key: str) -> str:" in tray_i18n_py_source, (
        "tray_i18n.py must define the _(key) lookup function, "
        "build_menu_for_tray calls it to translate keys to localized labels."
    )
    # The lookup must consult the current locale first, then fall back.
    assert "_TRAY_LABELS_LOCALES.get(_tray_locale, _TRAY_LABELS_EN)" in tray_i18n_py_source, (
        "_(key) must look up the key in the current locale's dict, "
        "falling back to _TRAY_LABELS_EN, the 3-tier fallback "
        "(locale → en → key) is the contract."
    )


def test_tray_locale_command_wired_in_system_handlers(
    system_handlers_source,
) -> None:
    """ADR-0020 §6.5: ``set_tray_locale`` IPC command is wired."""
    assert "_handle_set_tray_locale" in system_handlers_source, (
        "system_handlers.py must define _handle_set_tray_locale, the "
        "IPC handler for the set_tray_locale command (ADR-0020 §6.5)."
    )
    assert "from voice_typer.server.tray import" in system_handlers_source, (
        "system_handlers.py must import from voice_typer.server.tray to call set_tray_locale + get_tray_locale."
    )
    assert "set_tray_locale" in system_handlers_source, (
        "system_handlers.py must call set_tray_locale(), the IPC "
        "handler is the bridge from the UI's language picker to the "
        "tray's locale state."
    )


def test_dynamic_microphone_list_api_present_but_noop(
    tray_py_source,
) -> None:
    """NEW-CQ-008 / RT-FIX-9 (2026-07-24): ``TrayIcon.set_microphones``"""
    # The function MUST be defined on TrayIcon. The signature now
    set_mics_match = re.search(
        r"def set_microphones\(self,\s*mics:\s*list\[dict\]\s*(?:\|\s*None)?\)\s*->\s*None:",
        tray_py_source,
    )
    assert set_mics_match, (
        "TrayIcon must define set_microphones(mics: list[dict] | None) -> None "
        "— the API is preserved for IPC parity (NEW-CQ-008) and was "
        "re-activated to cache the mic list + invalidate "
        "the menu cache."
    )


def test_dynamic_microphone_list_wired_in_startup_tasks() -> None:
    """ADR-0020 §6.5: startup_tasks.py enumerates mics → tray.set_microphones."""
    assert STARTUP_TASKS_PY.exists(), f"startup_tasks.py not found: {STARTUP_TASKS_PY}"
    src = STARTUP_TASKS_PY.read_text(encoding="utf-8")
    assert "set_microphones" in src, (
        "startup_tasks.py must call tray.set_microphones(mics), the "
        "microphone enumeration pipeline is preserved even though the "
        "tray's set_microphones is a no-op (NEW-CQ-008)."
    )


def test_dynamic_microphone_list_wired_in_service() -> None:
    """ADR-0020 §6.5: the service package also calls tray.set_microphones."""
    service_pkg = PROJECT_ROOT / "voice_typer" / "server" / "service"
    assert service_pkg.is_dir(), f"service package not found: {service_pkg}"
    # The set_microphones call MUST appear somewhere in the service
    found = False
    for py_file in service_pkg.rglob("*.py"):
        try:
            src = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "set_microphones" in src:
            found = True
            break
    assert found, (
        "the voice_typer/server/service/ package must call "
        "tray.set_microphones(mics) somewhere (currently in "
        "service/microphone_test.py), the runtime microphone watcher "
        "propagates hotplug events to the tray (and, via the same "
        "enumeration pipeline, to the in-window UI)."
    )


def test_dynamic_model_submenu_builder_present(
    tray_menu_py_source,
) -> None:
    """ADR-0020 §6.5: the Models ▸ submenu is built dynamically."""
    assert "models_sub = tray._build_models_submenu()" in tray_menu_py_source, (
        "build_menu_for_tray must invoke tray._build_models_submenu() to "
        "materialize the dynamic Models submenu items, this is the "
        "canonical dynamic item of the tray menu (ADR-0020 §6.5)."
    )


def test_dynamic_model_submenu_data_builder_present(
    tray_models_py_source,
) -> None:
    """ADR-0020 §6.5: ``build_models_submenu_data`` enumerates models."""
    assert "def build_models_submenu_data(" in tray_models_py_source, (
        "tray_models.py must define build_models_submenu_data, the data-gathering function for the Models submenu."
    )
    # All 5 candidate models must be enumerated.
    for model_name in ["tiny", "large-v3", "large-v3-turbo", "parakeet", "qwen"]:
        assert f'"{model_name}"' in tray_models_py_source, (
            f"tray_models.py must enumerate the {model_name!r} model as "
            f"a candidate, the Models submenu shows each downloaded "
            f"model with a '•' prefix on the active one."
        )


def test_dynamic_model_submenu_items_builder_present(
    tray_models_py_source,
) -> None:
    """ADR-0020 §6.5: ``build_models_menu_items`` produces pystray items."""
    assert "def build_models_menu_items(" in tray_models_py_source, (
        "tray_models.py must define build_models_menu_items, the "
        "pystray-UI glue that wraps the data tuples as MenuItem instances."
    )
    # The "More models..." item must be present (deep-link to the UI).
    assert '"More models..."' in tray_models_py_source, (
        "tray_models.py must append a 'More models...' menu item that "
        "opens the app's Models page, this is the deep-link contract."
    )
    # Active models are marked with the native checkmark via pystray's
    assert "checked=" in tray_models_py_source, (
        "tray_models.py must mark the active model via pystray's "
        "checked= callable (native checkmark) so the user can see at "
        "a glance which model is currently selected."
    )


def test_tray_py_build_models_submenu_method_present(
    tray_py_source,
) -> None:
    """ADR-0020 §6.5: TrayIcon._build_models_submenu delegates to tray_models."""
    assert "def _build_models_submenu(self) -> list:" in tray_py_source, (
        "TrayIcon must define _build_models_submenu, the method that "
        "delegates to tray_models.build_models_menu_items (the #13 "
        "extraction keeps tray.py as the cache owner)."
    )
    assert "build_models_menu_items" in tray_py_source, (
        "tray.py must import + call build_models_menu_items from "
        "tray_models, the actual menu-item construction is delegated."
    )


def test_tray_icon_path_uses_assets_dir_with_fallback(
    tray_icon_py_source,
) -> None:
    """PLAT-024: tray icon path resolves to ``server/assets/``."""
    assert 'asset_dir = Path(__file__).resolve().parent / "assets"' in tray_icon_py_source, (
        "tray_icon.py must resolve the assets dir relative to its own "
        "file path (Path(__file__).resolve().parent / 'assets') so the "
        "tray icon PNGs are found regardless of the CWD."
    )
    # The 5 standard PNG sizes must be enumerated.
    assert "available = [16, 24, 32, 48, 64]" in tray_icon_py_source, (
        "tray_icon.py must enumerate the 5 standard PNG sizes "
        "[16, 24, 32, 48, 64], the DPI-aware size selector picks the "
        "closest available size."
    )
    assert "tray-mic-{best}.png" in tray_icon_py_source, (
        "tray_icon.py must build the PNG path as "
        "asset_dir / f'tray-mic-{best}.png', the {best} placeholder "
        "is the closest available size to the DPI-aware target."
    )


def test_tray_icon_path_windows_ico_preference(tray_icon_py_source) -> None:
    """PLAT-024: on Windows, .ico files are preferred for sharper rendering."""
    assert "is_windows()" in tray_icon_py_source, (
        "tray_icon.py must check is_windows() before preferring .ico files, the ICO preference is Windows-only."
    )
    assert "tray-mic-{state.value}.ico" in tray_icon_py_source, (
        "tray_icon.py must try the state-specific ICO path "
        "(tray-mic-{state.value}.ico) first on Windows, this gives "
        "sharper rendering than recoloring a PNG at runtime."
    )
    assert 'base_ico = asset_dir / "tray-mic.ico"' in tray_icon_py_source, (
        "tray_icon.py must fall back to the base tray-mic.ico if no "
        "state-specific ICO exists, the base ICO is colorized at "
        "runtime per AppState."
    )


def test_tray_icon_shape_fallback_for_colorblind_accessibility(
    tray_icon_py_source,
) -> None:
    """PLAT-021 / TRAY-032: shape-only fallback for colorblind users."""
    assert "_ICON_SHAPES" in tray_icon_py_source, (
        "tray_icon.py must define the _ICON_SHAPES dict, the AppState→shape map for colorblind-accessibility."
    )
    # Each AppState must have a shape (no fallback to "unknown").
    for state in ["IDLE", "RECORDING", "TRANSCRIBING", "LOADING", "ERROR", "CANCELLING"]:
        assert f"AppState.{state}:" in tray_icon_py_source, (
            f"_ICON_SHAPES must define a shape for AppState.{state}, "
            f"every state needs a distinct shape for colorblind users."
        )
    assert "def _draw_shape(" in tray_icon_py_source, (
        "tray_icon.py must define _draw_shape, the shape-only fallback "
        "renderer for environments where no PNG icon is available."
    )


def test_cargo_toml_no_separate_tray_plugin_crate(cargo_toml_source) -> None:
    """ADR-0020 §6.5 (Tauri v2 note): no ``tauri-plugin-tray`` crate dep."""
    # Extract the [dependencies] table body (up to the next table header).
    deps_match = re.search(
        r"^\[dependencies\]\s*\n(.*?)(?=^\[|\Z)",
        cargo_toml_source,
        re.MULTILINE | re.DOTALL,
    )
    assert deps_match, "Cargo.toml must declare a [dependencies] table, couldn't find the [dependencies] header."
    deps_body = deps_match.group(1)
    # Each dep entry is `name = { ... }` or `name = "version"`. Strip
    deps_no_comments = "\n".join(line for line in deps_body.splitlines() if not line.strip().startswith("#"))
    # Dependency keys are at the start of a line (no leading whitespace).
    dep_keys = re.findall(r"^([a-zA-Z0-9_-]+)\s*=", deps_no_comments, re.MULTILINE)
    assert "tauri-plugin-tray" not in dep_keys, (
        "Cargo.toml's [dependencies] table must NOT declare a "
        "tauri-plugin-tray dependency, in Tauri v2 the tray API is "
        "built into the core 'tauri' crate. The v1-era "
        "tauri-plugin-tray crate does not exist for v2. (Found dep "
        f"keys: {dep_keys})"
    )


def test_cargo_toml_documents_builtin_tray_api(cargo_toml_source) -> None:
    """ADR-0020 §6.5: Cargo.toml documents the built-in tray API choice."""
    assert "tray support is built into the core crate" in cargo_toml_source, (
        "Cargo.toml must document (in a comment) that tray support is "
        "built into the core 'tauri' crate in v2, prevents future "
        "contributors from adding a non-existent tauri-plugin-tray dep."
    )
    assert "NOT a separate tauri-plugin-tray crate" in cargo_toml_source, (
        "Cargo.toml must explicitly note that tray support is NOT a "
        "separate crate, the v1→v2 migration changed this and the "
        "comment is the contract."
    )


def test_cargo_toml_tray_icon_feature_documentation(
    cargo_toml_source,
) -> None:
    """ADR-0020 §6.5: Cargo.toml documents the ``tray-icon`` feature gate."""
    assert '"tray-icon" feature' in cargo_toml_source, (
        "Cargo.toml must mention the 'tray-icon' cargo feature in a "
        "comment, this is the gate that enables the tray API on the "
        "core 'tauri' crate."
    )


def test_cargo_toml_tray_icon_feature_is_enabled(
    cargo_toml_source,
) -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3: the ``tray-icon`` feature IS enabled"""
    # The tauri dep line MUST include "tray-icon" in its features list.
    tauri_dep_match = re.search(
        r"^tauri\s*=\s*\{[^}]*features\s*=\s*\[([^\]]*)\][^}]*\}",
        cargo_toml_source,
        re.MULTILINE,
    )
    assert tauri_dep_match, (
        "Cargo.toml must declare the 'tauri' dependency, couldn't find the tauri = { ... features = [...] } line."
    )
    features_list = tauri_dep_match.group(1)
    assert "tray-icon" in features_list, (
        "The 'tray-icon' cargo feature MUST be enabled on the 'tauri' "
        "crate, the Rust host renders the system tray via Tauri's "
        "built-in tray API (ADR-0020 §6.5 + MIG-1.9 Phase 3). Without "
        "it TrayIconBuilder / tauri::menu are unavailable."
    )


# Section F: Rust host owns the tray (ADR-0020 §6.5 +  Phase 3) ────


def test_main_rs_sets_up_rust_host_tray(main_rs_source) -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3: the Rust host OWNS the tray."""
    # The .setup hook must call create_tray.
    assert "create_tray" in main_rs_source, (
        "main.rs must call crate::tray::create_tray(...) in its .setup "
        "hook, the Rust host owns the system tray (ADR-0020 §6.5 + "
        "MIG-1.9 Phase 3). If host-side tray rendering is removed, "
        "update this test + the capability file."
    )
    # The tray wiring uses the sidecar-driven menu event (tray.rs).
    tray_rs = PROJECT_ROOT / "src-tauri" / "src" / "tray.rs"
    assert tray_rs.exists(), f"tray.rs not found: {tray_rs}"
    tray_rs_source = tray_rs.read_text(encoding="utf-8")
    assert "tray_menu" in tray_rs_source, (
        "tray.rs must reference the sidecar's `tray_menu` event (the "
        "Rust host renders the menu piped from the sidecar)."
    )


def test_tray_rs_routes_clicks_via_tray_click_dispatch() -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3 + CR-1/CR-2 fix: ``src-tauri/src/tray.rs``"""
    tray_rs = PROJECT_ROOT / "src-tauri" / "src" / "tray.rs"
    assert tray_rs.exists(), f"tray.rs not found: {tray_rs}"
    src = tray_rs.read_text(encoding="utf-8")
    # The tray module must listen for the sidecar's tray_menu event.
    assert 'app.listen("tray_menu"' in src, (
        "tray.rs must listen for the sidecar's `tray_menu` event to rebuild the native menu on demand."
    )
    # On click, it must build a tray_click command with the item id.
    assert '"tray_click"' in src, (
        "tray.rs must build a `tray_click` command (with the menu item id) when "
        "a tray menu item is clicked, this routes the click back to the "
        "Python sidecar (ADR-0020 §6.5 + MIG-1.9 Phase 3)."
    )
    assert "dispatch_inner" in src, (
        "tray.rs must route tray_click through the shared `dispatch_inner` "
        "path (CR-1 fix). An inline ws_tx.send would fork a second WS-send "
        "implementation bypassing the payload cap + shutdown + timeout guards."
    )
    # must NOT be present. If it ever returns, the tray menu will be
    assert 'emit("dispatch"' not in src, (
        "stale `emit('dispatch', ...)` pattern present in tray.rs, CR-1 "
        "regression. The tray click must go through `dispatch_inner`, "
        "not emitted as a Tauri event."
    )
    assert 'app.emit("dispatch"' not in src, (
        "stale `app.emit('dispatch', ...)` pattern present in tray.rs, CR-1 "
        "regression. The tray click must go through `dispatch_inner`, "
        "not emitted as a Tauri event."
    )


def test_main_rs_tray_ownership_documented_in_capability(
    capability_json,
) -> None:
    """Rust-host-owns-tray decision."""
    description = capability_json.get("description", "")
    assert "Rust host OWNS the system tray" in description, (
        "capability file's description must document that 'the Rust "
        "host OWNS the system tray', this is the rationale for "
        "granting core:tray:* permissions to the Rust host."
    )
    assert "tray_menu" in description, (
        "capability file's description must mention the `tray_menu` "
        "event, the sidecar computes the menu structure and emits it "
        "for the Rust host to render."
    )
    assert "tray_click" in description, (
        "capability file's description must mention `tray_click`, the "
        "Rust host routes tray menu clicks back to the sidecar via this "
        "command."
    )


def test_capability_file_grants_core_tray_permissions(
    capability_json,
) -> None:
    """
    ADR-0020 §6.5 + MIG-1.9 Phase 3 + XZ-R4-015: the MAIN window
    MUST NOT contain the manipulation grants.
    """
    permissions = capability_json.get("permissions", [])
    tray_perms = [p for p in permissions if "tray" in p.lower()]
    assert tray_perms, (
        "capability file MUST grant core:tray:* permissions to the "
        "main window (so the renderer can observe tray-related events "
        "broadcast by the Rust host). Found no tray permissions."
    )
    # The default tray permission must be present.
    assert "core:tray:default" in permissions, (
        "capability file must grant core:tray:default, the renderer "
        "needs the default tray-observation permission set (ADR-0020 "
        "§6.5 + XZ-R4-015)."
    )
    forbidden_tray_perms = [
        "core:tray:allow-set-icon",
        "core:tray:allow-set-menu",
        "core:tray:allow-set-tooltip",
        "core:tray:allow-set-title",
        "core:tray:allow-get-by-id",
        "core:tray:allow-remove-by-id",
        "core:tray:allow-new",
    ]
    for perm in forbidden_tray_perms:
        assert perm not in permissions, (
            f"capability file must NOT grant {perm} to the main window, "
            f"the Rust host owns the tray and the renderer does not "
            f"manipulate it (XZ-R4-015 / ADR-0020 §6.5)."
        )


def test_main_rs_plugins_do_not_include_tray(main_rs_source) -> None:
    """GAP-1: ``main.rs`` does not register a tray plugin."""
    # No tauri_plugin_tray or tray-related plugin registration.
    forbidden_patterns = [
        r"tauri_plugin_tray",
        r"\.plugin\(tauri::tray::",
        r"tauri::tray::TrayIconBuilder",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, main_rs_source), (
            f"GAP-1 (informational): main.rs matches {pattern!r}, "
            f"the v1 implementation expected NO tray plugin "
            f"registration in main.rs."
        )


def test_tauri_conf_no_separate_tray_plugin_section() -> None:
    """
    ADR-0020 §6.5: ``tauri.conf.json`` has no separate ``tray`` plugin entry.
    ``plugins`` section must NOT include ``"tray"``.
    """
    assert TAURI_CONF.exists(), f"tauri.conf.json not found: {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    plugins = conf.get("plugins", {})
    assert "tray" not in plugins, (
        "tauri.conf.json's plugins section must NOT include 'tray', "
        "in Tauri v2 the tray API is built into the core crate (no "
        "plugin registration), and the v1 implementation doesn't use "
        "it (Python sidecar owns the tray). The 'tray': {} entry from "
        "ADR-0020 §7 was a v1-era anticipation that's no longer needed."
    )
