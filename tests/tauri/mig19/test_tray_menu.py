r"""MIG-1.9 Phase 3 — tray/menu port validation (ADR-0020 §6.5).

This is the **Phase 3 tray-menu check** for the MIG-1.9 Tauri runtime
migration. It validates that the **tray menu structure is preserved
1:1** across the runtime migration — the user must see the same six
menu items (Open App, Toggle Dictation, Cancel, Models, Restart,
Quit), the same locale toggles (English + Spanish at minimum), and
the same dynamic items (microphone list, model list) as before the
migration.

ADR-0020 §6.5 mandates:

    "the tray icon moves to ``tauri-plugin-tray`` (Win32 / AppKit /
    GTK via ``gtk-3.0``), but the **menu structure and locale logic
    stay in the Python sidecar** — the sidecar computes the menu items
    and emits them as a ``tray_menu`` event; the Rust host renders
    them via the Tauri tray API. This preserves the existing
    ``tray.py`` / ``tray_menu.py`` logic unchanged."

Implementation note (Tauri v2): in Tauri v2 the tray API is built
into the **core** ``tauri`` crate (enabled by the ``tray-icon``
feature) — it is NOT a separate ``tauri-plugin-tray`` crate. The
project's ``src-tauri/Cargo.toml`` documents this choice.

Implementation decision (deliberate deviation from ADR §6.5):
The capability file ``src-tauri/capabilities/migrate-runtime.json``
records the actual decision taken for the v1 migration:

    "Tray: the Python sidecar owns the tray via pystray (no
    Tauri-mode branch needed; the sidecar inherits the desktop
    session from externalBin spawn), so no core:tray:* permissions
    are granted to the Rust host."

In other words: rather than porting the *icon* to the Rust host and
piping the menu over IPC (the ADR's original plan), the v1
implementation keeps the **entire tray** (icon + menu + locale
logic + dynamic items) in the Python sidecar via ``pystray``. The
Rust host has zero tray code. This is sound because the sidecar is
spawned by Tauri via ``externalBin`` and inherits the user's
graphical session, so ``pystray`` can attach to the same Win32 /
AppKit / GTK status-notifier area the host would have used.

This test file therefore validates the **contract that matters to
the user**: the menu structure, locale, and dynamic items are
preserved 1:1 by the sidecar that renders them. The Rust-host tray
hooks (which the ADR anticipated) are documented as a gap and
deferred to a future host-side rendering iteration.

Scope (ADR-0020 §6.5 + MIG-1.9 task brief):

1. **Menu structure preserved 1:1** — the six menu items mandated by
   ``tray_menu.py::build_menu`` (Open App, Toggle Dictation, Cancel
   Transcription [conditional], Models ▸, Restart, Quit) appear in
   the same order with the same separators. Source-inspected on the
   Python sidecar (the renderer of record).

2. **Locale support preserved** — the ``set_tray_locale`` IPC command
   + the ``_TRAY_LABELS_LOCALES`` dict in ``tray.py`` ship English
   (``en``) + Spanish (``es``) translations for every menu key. The
   ``set_tray_locale`` handler is wired in ``system_handlers.py``.

3. **Dynamic items wired** — the microphone list (via
   ``TrayIcon.set_microphones``) and the model list (via
   ``build_models_submenu`` → ``tray_models.build_models_menu_items``)
   are reachable from the running sidecar.

4. **Tray icon path** — ``tray_icon.py::_get_icon_path`` resolves to
   ``voice_typer/server/assets/tray-mic-{16,24,32,48,64}.png`` (with
   ``.ico`` preferred on Windows for sharper rendering).

5. **Tauri tray API choice (built-in, not a separate plugin)** —
   ``Cargo.toml`` documents that Tauri v2 ships tray support in the
   core crate; there is NO ``tauri-plugin-tray`` dependency. (The
   ``tray-icon`` cargo feature is not yet enabled on the host because
   the host has no tray code — see gap below.)

6. **Rust host tray status** — ``main.rs`` contains no tray setup
   code; the capability file documents the deliberate decision that
   the Python sidecar owns the tray. No ``core:tray:*`` permissions
   are granted to the Rust host.

VALIDATE ON HOST (Linux — after building the Tauri app):
    1. Build the sidecar + Tauri bundle:
         bash scripts/build/build_sidecar_linux.sh x86_64
         cd src-tauri && cargo tauri build
    2. Launch the built AppImage / .deb install:
         target/release/bundle/appimage/voice-typer_*.AppImage
       (or the .deb install + ``voice-typer`` from the desktop launcher)
    3. Find the tray icon in the system status area (top-bar on GNOME
       with the AppIndicator extension, waybar tray module on Sway,
       etc.). RIGHT-click it.
    4. Verify the menu shows these 6 items in this exact order:
           Open App
           Toggle Dictation (F2)         ← hotkey label in parens
           ─── separator ───
           Models ▸                       ← hover for the submenu
           ─── separator ───
           Restart
           Quit
       The "Cancel Transcription" item appears ONLY while a
       transcription is in flight — start a dictation, then re-open
       the menu to verify it surfaces.
    5. Verify the Models ▸ submenu lists each downloaded model with a
       "•" prefix on the active one and a "More models..." entry at
       the bottom that opens the app on the Models page.
    6. Locale — open the app → Settings → Language → switch to
       Español. Re-open the tray menu and verify the labels changed
       to:
           Abrir Aplicación
           Alternar Dictado (F2)
           Modelos
           Reiniciar
           Salir
    7. Dynamic items — plug / unplug a USB microphone; verify the
       tray menu doesn't error and the app's microphone picker
       updates (the tray's microphone list is intentionally a no-op
       cache since NEW-CQ-008, but the IPC wiring must not crash).
    8. Quit via the tray's "Salir" / "Quit" item — verify the sidecar
       process exits (``pgrep -f python-sidecar`` returns empty) and
       the Tauri main window closes.

VALIDATE ON HOST (Windows — after building the Tauri app):
    1. Build:
         bash scripts/build/build_sidecar_windows.sh
         cd src-tauri && cargo tauri build
    2. Install the produced .msi / .exe and launch from the Start
       Menu.
    3. RIGHT-click the Voice Typer tray icon in the Windows
       notification area (chevron ↑ in the taskbar).
    4. Same 6-item menu check as Linux step 4 (labels in English by
       default). The "Cancel Transcription" item appears only while
       transcribing.
    5. Locale — same as Linux step 6 (Settings → Language → Español).
    6. Tray icon path check — verify ``%LOCALAPPDATA%\voice-typer``
       contains the extracted ``voice_typer/server/assets/`` dir with
       the ``tray-mic-{16,24,32,48,64}.png`` icons. On Windows the
       tray prefers ``tray-mic-{state}.ico`` for sharper rendering
       (PLAT-024).
    7. Quit via "Quit" — verify ``tasklist | findstr python-sidecar``
       returns nothing.

VALIDATE ON HOST (macOS — after building the Tauri app):
    1. Build:
         bash scripts/build/build_sidecar_macos.sh aarch64
         cd src-tauri && cargo tauri build
    2. Drag the produced .app to /Applications and launch.
    3. Click the Voice Typer microphone icon in the macOS menu-bar
       tray (top-right).
    4. Same 6-item menu check as Linux step 4.
    5. Locale — same as Linux step 6 (Settings → Language → Español).
    6. Quit via "Quit" — verify ``pgrep -f python-sidecar`` returns
       empty and the menu-bar icon disappears.

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1 (Rust host tray hooks not implemented): ADR-0020 §6.5
    anticipated the Rust host rendering the tray icon via Tauri's
    built-in tray API, with the menu piped from the sidecar over IPC
    via a ``tray_menu`` event + a ``tray_click`` dispatch command.
    The v1 implementation skips this: the Python sidecar owns the
    entire tray via ``pystray``, and the Rust host has zero tray
    code (``main.rs`` registers no tray builder, no
    ``tray::TrayIconBuilder``, no ``tray_menu`` event handler).
    Rationale: the sidecar inherits the desktop session from
    ``externalBin`` spawn, so ``pystray`` can attach to the same
    Win32 / AppKit / GTK status-notifier area the host would have
    used — and the existing ``tray.py`` / ``tray_menu.py`` logic
    stays unchanged (no IPC round-trip needed for menu rendering).
    This is documented in
    ``src-tauri/capabilities/migrate-runtime.json``'s description
    field. See ``test_main_rs_no_direct_tray_setup`` and
    ``test_capability_file_documents_tray_ownership_strategy``.
  - GAP-2 (``tray-icon`` cargo feature not enabled): the
    ``tauri`` crate's ``tray-icon`` feature is NOT enabled in
    ``Cargo.toml`` (only ``devtools`` is). The Cargo.toml comment
    *describes* the feature but the project doesn't turn it on
    because there is no host-side tray code to use it. Enabling it
    would compile the ``tray-icon`` crate for nothing. See
    ``test_cargo_toml_tray_icon_feature_not_yet_enabled``.
  - GAP-3 (microphone list cache removed): the tray's
    ``set_microphones`` API is a no-op (NEW-CQ-008 removed the
    write-only cache). The IPC wiring is preserved for parity, but
    the tray menu doesn't actually render a microphone list. The
    app's in-window microphone picker is the canonical UI. See
    ``test_dynamic_microphone_list_api_present_but_noop``.
  - GAP-4 (no tray icon assets shipped): the directory
    ``voice_typer/server/assets/`` is referenced by
    ``tray_icon.py::_get_icon_path`` but doesn't exist on disk in
    this repo (icons are generated by
    ``client/scripts/generate-icons.mjs`` at build time). At runtime
    the shape-only fallback (``_draw_shape``) renders a colored
    circle / square / diamond / triangle. See
    ``test_tray_icon_path_uses_assets_dir_with_fallback``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig19/test_tray_menu.py.
# Path from file → root:
#   parents[0] = mig19/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]

MAIN_RS = PROJECT_ROOT / "src-tauri" / "src" / "main.rs"
CARGO_TOML = PROJECT_ROOT / "src-tauri" / "Cargo.toml"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
CAPABILITY_JSON = PROJECT_ROOT / "src-tauri" / "capabilities" / "migrate-runtime.json"

TRAY_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray.py"
TRAY_MENU_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_menu.py"
TRAY_ICON_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_icon.py"
TRAY_MODELS_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_models.py"
SYSTEM_HANDLERS_PY = PROJECT_ROOT / "voice_typer" / "server" / "handlers" / "system_handlers.py"
STARTUP_TASKS_PY = PROJECT_ROOT / "voice_typer" / "server" / "startup_tasks.py"
SERVICE_PY = PROJECT_ROOT / "voice_typer" / "server" / "service.py"


# ─── Fixtures ────────────────────────────────────────────────────────────────
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
    """Load + parse the migrate-runtime capability JSON."""
    assert CAPABILITY_JSON.exists(), f"capability file not found: {CAPABILITY_JSON}"
    return json.loads(CAPABILITY_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tray_py_source() -> str:
    """Read voice_typer/server/tray.py as text."""
    assert TRAY_PY.exists(), f"tray.py not found: {TRAY_PY}"
    return TRAY_PY.read_text(encoding="utf-8")


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


# ─── Section A: tray menu structure preserved 1:1 ───────────────────────────


def test_tray_menu_has_six_required_labels_in_order(tray_menu_py_source) -> None:
    """ADR-0020 §6.5: the 6 menu labels appear in the mandated order.

    The Python sidecar's ``tray_menu.build_menu`` is the renderer of
    record for the tray menu (the Rust host has no tray code — see
    GAP-1). The menu structure MUST be preserved 1:1 across the
    migration, so the ``localize`` keys for the 6 items must appear
    in the file in this exact order:

        1. open_app
        2. toggle_dictation
        3. force_cancel_transcription   (conditional — only when the
                                         caller passes a callback)
        4. models
        5. restart
        6. quit
    """
    # Locate each localize() call's key argument in source order.
    key_occurrences = [m.group(1) for m in re.finditer(r'localize\(\s*["\']([a-z_]+)["\']', tray_menu_py_source)]
    # The 6 mandated keys must ALL be present.
    required_keys = [
        "open_app",
        "toggle_dictation",
        "force_cancel_transcription",
        "models",
        "restart",
        "quit",
    ]
    for key in required_keys:
        assert key in key_occurrences, (
            f"tray_menu.py must call localize({key!r}) — missing from "
            f"build_menu output (ADR-0020 §6.5: menu structure must be "
            f"preserved 1:1)"
        )
    # The keys must appear in the mandated order. We compare the
    # subsequence of key_occurrences filtered to required_keys.
    seen_order = [k for k in key_occurrences if k in required_keys]
    assert seen_order == required_keys, (
        f"tray_menu.py menu keys out of order: expected {required_keys}, "
        f"got {seen_order} (the 6 items must appear in the ADR-mandated "
        f"order: Open App → Toggle Dictation → Cancel → Models → Restart → Quit)"
    )


def test_tray_menu_open_app_is_default_action(tray_menu_py_source) -> None:
    """ADR-0020 §6.5 + tray_menu.py docstring: "Open App" is the default.

    Left-click on the tray icon triggers the ``default=True`` menu
    item, which is "Open App" unless the user reconfigures
    ``tray_left_click_action``. This default MUST be preserved.
    """
    assert "default=open_app_default" in tray_menu_py_source, (
        "Open App must be the default (bold) menu item — left-click "
        "behavior. The build_menu function must pass "
        "default=open_app_default to pystray.MenuItem for Open App."
    )
    assert 'left_click_action: str = "open_app"' in tray_menu_py_source, (
        "build_menu's left_click_action parameter must default to "
        "'open_app' so the tray opens the app window on left-click "
        "unless the user explicitly reconfigures it."
    )


def test_tray_menu_toggle_dictation_includes_hotkey_label(
    tray_menu_py_source,
) -> None:
    """ADR-0020 §6.5: Toggle Dictation label includes the hotkey hint.

    The tray's "Toggle Dictation" item shows the current hotkey in
    parentheses (e.g. "Toggle Dictation (F2)") so the user knows
    which key to press without opening Settings. The label is built
    by formatting ``f"{localize('toggle_dictation')} ({hotkey_label})"``.
    """
    assert re.search(
        r"localize\(['\"]toggle_dictation['\"]\)\s*\}\s*\(\{hotkey_label\}\)",
        tray_menu_py_source,
    ), (
        "Toggle Dictation label must include the hotkey hint in parens — "
        "expected an f-string like "
        "f\"{localize('toggle_dictation')} ({hotkey_label})\" so the "
        "user sees e.g. 'Toggle Dictation (F2)' in the tray menu."
    )


def test_tray_menu_cancel_item_conditional(tray_menu_py_source) -> None:
    """PR-2 Finding #3: Cancel Transcription item is conditional.

    The "Cancel Transcription" menu item is only added when
    ``force_cancel_transcription`` callback is provided (a manual
    escape hatch for stuck transcriptions). It must NOT be
    unconditional — that would clutter the menu when nothing is
    transcribing.
    """
    assert "force_cancel_transcription" in tray_menu_py_source, (
        "build_menu must accept a force_cancel_transcription callback "
        "parameter — the Cancel Transcription menu item is a manual "
        "escape hatch for stuck transcriptions (PR-2 Finding #3)."
    )
    assert "if force_cancel_transcription is not None:" in tray_menu_py_source, (
        "The Cancel Transcription menu item must be added conditionally "
        "only when force_cancel_transcription is provided — adding it "
        "unconditionally would clutter the menu when nothing is "
        "transcribing."
    )


def test_tray_menu_models_submenu_delegated(tray_menu_py_source) -> None:
    """ADR-0020 §6.5: Models submenu is built by a delegated callable.

    The Models ▸ submenu is built by ``build_models_submenu()``
    (passed as a callable so the tray module stays testable without
    a TrayIcon instance). The submenu's items are dynamic — they
    reflect the currently-downloaded models.
    """
    assert "build_models_submenu: Callable[[], list]" in tray_menu_py_source, (
        "build_menu must accept a build_models_submenu callable "
        "parameter that returns the list of pystray.MenuItem for the "
        "Models submenu — the items are dynamic (reflect downloaded "
        "models) and are built by the caller."
    )
    assert "pystray.Menu(*models_sub)" in tray_menu_py_source, (
        "build_menu must wrap the models submenu items in "
        "pystray.Menu(*models_sub) so they render as a nested submenu "
        "off the 'Models ▸' parent item."
    )


def test_tray_menu_separators_present(tray_menu_py_source) -> None:
    """ADR-0020 §6.5: two separators structure the menu into 3 groups.

    The tray menu has 3 visual groups separated by 2 horizontal
    rules: (1) Open App + Toggle Dictation + Cancel, (2) Models ▸,
    (3) Restart + Quit. The separators MUST be present.
    """
    sep_count = tray_menu_py_source.count("pystray.Menu.SEPARATOR")
    assert sep_count >= 2, (
        f"build_menu must insert at least 2 pystray.Menu.SEPARATORs to "
        f"structure the menu into 3 groups (Open/Toggle/Cancel | Models "
        f"| Restart/Quit). Found only {sep_count}."
    )


# ─── Section B: locale support preserved (en + es at minimum) ───────────────


def test_tray_locale_english_dict_present(tray_py_source) -> None:
    """TRAY-008: English locale dict must be defined.

    The English locale is the default fallback for every tray label.
    It must include all 6 menu keys (open_app, toggle_dictation,
    models, restart, quit, force_cancel_transcription) plus the
    app_name tooltip key.
    """
    assert "_TRAY_LABELS_EN" in tray_py_source, (
        "tray.py must define _TRAY_LABELS_EN — the English locale dict "
        "is the default fallback for every tray label (TRAY-008)."
    )
    # Extract the EN dict body.
    en_dict_match = re.search(
        r"_TRAY_LABELS_EN\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
        tray_py_source,
        re.DOTALL,
    )
    assert en_dict_match, (
        "Could not extract the _TRAY_LABELS_EN dict body — check the type annotation matches dict[str, str]."
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
            f"_TRAY_LABELS_EN must define {key} — the English tray "
            f"locale is the fallback for every other locale's missing "
            f"keys (TRAY-008)."
        )
    # Sanity-check the actual English strings are what users see.
    assert '"open_app": "Open App"' in en_dict_body, (
        "EN open_app label must be 'Open App' — this is the user-facing string the host VALIDATE step looks for."
    )
    assert '"quit": "Quit"' in en_dict_body, (
        "EN quit label must be 'Quit' — this is the user-facing string the host VALIDATE step looks for."
    )


def test_tray_locale_spanish_dict_present(tray_py_source) -> None:
    """TRAY-008: Spanish locale dict must be defined.

    Spanish is the proof-of-concept locale for tray i18n. The user
    can switch the UI to Español and the tray must update to Spanish
    labels via the ``set_tray_locale('es')`` IPC command.
    """
    assert "_TRAY_LABELS_ES" in tray_py_source, (
        "tray.py must define _TRAY_LABELS_ES — Spanish is the proof-of-concept locale for tray i18n (TRAY-008)."
    )
    es_dict_match = re.search(
        r"_TRAY_LABELS_ES\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
        tray_py_source,
        re.DOTALL,
    )
    assert es_dict_match, (
        "Could not extract the _TRAY_LABELS_ES dict body — check the type annotation matches dict[str, str]."
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
            f"_TRAY_LABELS_ES must define {key} — every locale must cover all 6 menu keys + the app_name tooltip."
        )
    # Sanity-check the actual Spanish strings match the host VALIDATE step.
    assert '"open_app": "Abrir Aplicación"' in es_dict_body, (
        "ES open_app label must be 'Abrir Aplicación' — this is the "
        "user-facing string the host VALIDATE step looks for."
    )
    assert '"quit": "Salir"' in es_dict_body, (
        "ES quit label must be 'Salir' — this is the user-facing string the host VALIDATE step looks for."
    )
    assert '"toggle_dictation": "Alternar Dictado"' in es_dict_body, (
        "ES toggle_dictation label must be 'Alternar Dictado' — this is "
        "the user-facing string the host VALIDATE step looks for."
    )


def test_tray_locale_registry_includes_en_and_es(tray_py_source) -> None:
    """TRAY-008: ``_TRAY_LABELS_LOCALES`` must register en + es.

    The locale registry maps locale codes to their label dicts. The
    ``set_tray_locale`` function falls back to English if the
    requested locale isn't in this registry, so en + es must be
    registered for the host VALIDATE step's Spanish toggle to work.
    """
    assert "_TRAY_LABELS_LOCALES" in tray_py_source, (
        "tray.py must define _TRAY_LABELS_LOCALES — the locale→dict registry that set_tray_locale consults."
    )
    locales_match = re.search(
        r"_TRAY_LABELS_LOCALES\s*:\s*dict\[str,\s*dict\[str,\s*str\]\]\s*=\s*\{(.*?)\}",
        tray_py_source,
        re.DOTALL,
    )
    assert locales_match, "Could not extract _TRAY_LABELS_LOCALES dict body."
    body = locales_match.group(1)
    assert '"en": _TRAY_LABELS_EN' in body, (
        "_TRAY_LABELS_LOCALES must map 'en' → _TRAY_LABELS_EN — English is the default locale."
    )
    assert '"es": _TRAY_LABELS_ES' in body, (
        "_TRAY_LABELS_LOCALES must map 'es' → _TRAY_LABELS_ES — Spanish is the proof-of-concept non-English locale."
    )


def test_tray_locale_setter_and_getter_present(tray_py_source) -> None:
    """TRAY-008: ``set_tray_locale`` + ``get_tray_locale`` functions exist.

    The IPC layer calls ``set_tray_locale('es')`` when the user
    switches the UI language; the getter is used by tests. Both must
    be present and module-level (so the IPC handler can import them
    directly without a TrayIcon instance).
    """
    assert "def set_tray_locale(locale: str) -> None:" in tray_py_source, (
        "tray.py must define module-level set_tray_locale(locale) — the "
        "IPC handler in system_handlers.py imports + calls it directly."
    )
    assert "def get_tray_locale() -> str:" in tray_py_source, (
        "tray.py must define module-level get_tray_locale() → str — used by tests to verify the current locale."
    )
    # The setter must fall back to English for unknown locales.
    assert 'locale if locale in _TRAY_LABELS_LOCALES else "en"' in tray_py_source, (
        "set_tray_locale must fall back to 'en' for unknown locales — "
        "prevents a KeyError if the UI sends an unsupported locale code."
    )


def test_tray_locale_lookup_function_present(tray_py_source) -> None:
    """TRAY-008: the ``_()`` lookup function translates keys → labels.

    The ``_()`` function (mirroring gettext's convention) takes a
    label key and returns the localized string, falling back to
    English then to the key itself. ``build_menu`` calls
    ``localize(_)`` which is this function.
    """
    assert "def _(key: str) -> str:" in tray_py_source, (
        "tray.py must define the _(key) lookup function — build_menu "
        "calls localize(_) where _ translates keys to localized labels."
    )
    # The lookup must consult the current locale first, then fall back.
    assert "_TRAY_LABELS_LOCALES.get(_tray_locale, _TRAY_LABELS_EN)" in tray_py_source, (
        "_(key) must look up the key in the current locale's dict, "
        "falling back to _TRAY_LABELS_EN — the 3-tier fallback "
        "(locale → en → key) is the contract."
    )


def test_tray_locale_command_wired_in_system_handlers(
    system_handlers_source,
) -> None:
    """ADR-0020 §6.5: ``set_tray_locale`` IPC command is wired.

    The IPC handler ``_handle_set_tray_locale`` in
    ``system_handlers.py`` is the entry point for the UI's locale
    change. It validates the locale string and calls
    ``set_tray_locale()`` from ``tray.py``.
    """
    assert "_handle_set_tray_locale" in system_handlers_source, (
        "system_handlers.py must define _handle_set_tray_locale — the "
        "IPC handler for the set_tray_locale command (ADR-0020 §6.5)."
    )
    assert "from voice_typer.server.tray import" in system_handlers_source, (
        "system_handlers.py must import from voice_typer.server.tray to call set_tray_locale + get_tray_locale."
    )
    assert "set_tray_locale" in system_handlers_source, (
        "system_handlers.py must call set_tray_locale() — the IPC "
        "handler is the bridge from the UI's language picker to the "
        "tray's locale state."
    )


# ─── Section C: dynamic items wired (microphones + models) ──────────────────


def test_dynamic_microphone_list_api_present_but_noop(
    tray_py_source,
) -> None:
    """NEW-CQ-008: ``TrayIcon.set_microphones`` API is preserved (no-op).

    The microphone list is no longer rendered in the tray menu (the
    write-only cache was removed in NEW-CQ-008). The API is kept for
    IPC parity — the sidecar's startup_tasks.py + service.py still
    call it, so removing it would break the call sites. The tray
    must NOT crash on the call.
    """
    assert "def set_microphones(self, mics: list[dict]) -> None:" in tray_py_source, (
        "TrayIcon must define set_microphones(mics) — the API is "
        "preserved for IPC parity even though it's a no-op (NEW-CQ-008)."
    )
    # The body must be a documented no-op (just `pass` after the docstring).
    set_mics_match = re.search(
        r"def set_microphones\(self, mics: list\[dict\]\) -> None:\s*"
        r'"""[^"]*?NEW-CQ-008[^"]*?"""\s*pass',
        tray_py_source,
        re.DOTALL,
    )
    assert set_mics_match, (
        "set_microphones must be a documented no-op (NEW-CQ-008) — the "
        "docstring must mention NEW-CQ-008 and the body must be `pass`."
    )


def test_dynamic_microphone_list_wired_in_startup_tasks() -> None:
    """ADR-0020 §6.5: startup_tasks.py enumerates mics → tray.set_microphones.

    Even though set_microphones is a no-op, the startup task still
    enumerates microphones and calls it. This keeps the wiring intact
    so a future tray menu redesign can re-add the microphone list
    without touching the startup pipeline.
    """
    assert STARTUP_TASKS_PY.exists(), f"startup_tasks.py not found: {STARTUP_TASKS_PY}"
    src = STARTUP_TASKS_PY.read_text(encoding="utf-8")
    assert "set_microphones" in src, (
        "startup_tasks.py must call tray.set_microphones(mics) — the "
        "microphone enumeration pipeline is preserved even though the "
        "tray's set_microphones is a no-op (NEW-CQ-008)."
    )


def test_dynamic_microphone_list_wired_in_service() -> None:
    """ADR-0020 §6.5: service.py also calls tray.set_microphones.

    The runtime microphone watcher (hotplug) calls
    ``tray.set_microphones`` from ``service.py`` so a newly-plugged
    USB mic is propagated (even though the tray no-op's the call,
    the in-window microphone picker still receives the event via
    the same enumeration pipeline).
    """
    assert SERVICE_PY.exists(), f"service.py not found: {SERVICE_PY}"
    src = SERVICE_PY.read_text(encoding="utf-8")
    assert "set_microphones" in src, (
        "service.py must call tray.set_microphones(mics) — the runtime "
        "microphone watcher propagates hotplug events to the tray (and, "
        "via the same enumeration pipeline, to the in-window UI)."
    )


def test_dynamic_model_submenu_builder_present(
    tray_menu_py_source,
) -> None:
    """ADR-0020 §6.5: the Models ▸ submenu is built dynamically.

    The Models submenu is the canonical "dynamic item" of the tray
    menu — it reflects which models the user has downloaded. The
    build_models_submenu callable passed to build_menu returns the
    list of pystray.MenuItem for the submenu.
    """
    assert "models_sub = build_models_submenu()" in tray_menu_py_source, (
        "build_menu must invoke build_models_submenu() to materialize "
        "the dynamic Models submenu items — this is the canonical "
        "dynamic item of the tray menu (ADR-0020 §6.5)."
    )


def test_dynamic_model_submenu_data_builder_present(
    tray_models_py_source,
) -> None:
    """ADR-0020 §6.5: ``build_models_submenu_data`` enumerates models.

    The data builder returns tuples of (name, downloaded, is_active,
    change_fn) for the candidate models. The candidate list MUST
    include the 5 models the app supports: tiny.en, small.en,
    medium.en, parakeet, qwen.
    """
    assert "def build_models_submenu_data(" in tray_models_py_source, (
        "tray_models.py must define build_models_submenu_data — the data-gathering function for the Models submenu."
    )
    # All 5 candidate models must be enumerated.
    for model_name in ["tiny.en", "small.en", "medium.en", "parakeet", "qwen"]:
        assert f'"{model_name}"' in tray_models_py_source, (
            f"tray_models.py must enumerate the {model_name!r} model as "
            f"a candidate — the Models submenu shows each downloaded "
            f"model with a '•' prefix on the active one."
        )


def test_dynamic_model_submenu_items_builder_present(
    tray_models_py_source,
) -> None:
    """ADR-0020 §6.5: ``build_models_menu_items`` produces pystray items.

    The item builder wraps the data tuples in pystray.MenuItem
    instances, marks the active one with "• ", and appends a
    "More models..." item that opens the app's Models page.
    """
    assert "def build_models_menu_items(" in tray_models_py_source, (
        "tray_models.py must define build_models_menu_items — the "
        "pystray-UI glue that wraps the data tuples as MenuItem instances."
    )
    # The "More models..." item must be present (deep-link to the UI).
    assert '"More models..."' in tray_models_py_source, (
        "tray_models.py must append a 'More models...' menu item that "
        "opens the app's Models page — this is the deep-link contract."
    )
    # Active models are prefixed with "• ".
    assert "'• '" in tray_models_py_source, (
        "tray_models.py must prefix the active model with '• ' so the "
        "user can see at a glance which model is currently selected."
    )


def test_tray_py_build_models_submenu_method_present(
    tray_py_source,
) -> None:
    """ADR-0020 §6.5: TrayIcon._build_models_submenu delegates to tray_models.

    The TrayIcon class's _build_models_submenu method is the bridge
    from the tray icon's menu cache to the standalone
    build_models_menu_items function in tray_models.py.
    """
    assert "def _build_models_submenu(self) -> list:" in tray_py_source, (
        "TrayIcon must define _build_models_submenu — the method that "
        "delegates to tray_models.build_models_menu_items (the #13 "
        "extraction keeps tray.py as the cache owner)."
    )
    assert "build_models_menu_items" in tray_py_source, (
        "tray.py must import + call build_models_menu_items from "
        "tray_models — the actual menu-item construction is delegated."
    )


# ─── Section D: tray icon path ──────────────────────────────────────────────


def test_tray_icon_path_uses_assets_dir_with_fallback(
    tray_icon_py_source,
) -> None:
    """PLAT-024: tray icon path resolves to ``server/assets/``.

    The tray icon PNGs live at
    ``voice_typer/server/assets/tray-mic-{16,24,32,48,64}.png``.
    The renderer falls back to a shape-only icon (circle / square /
    diamond / triangle) if no PNG is found — this is the GAP-4
    fallback for environments where the icons aren't shipped.
    """
    assert 'asset_dir = Path(__file__).resolve().parent / "assets"' in tray_icon_py_source, (
        "tray_icon.py must resolve the assets dir relative to its own "
        "file path (Path(__file__).resolve().parent / 'assets') so the "
        "tray icon PNGs are found regardless of the CWD."
    )
    # The 5 standard PNG sizes must be enumerated.
    assert "available = [16, 24, 32, 48, 64]" in tray_icon_py_source, (
        "tray_icon.py must enumerate the 5 standard PNG sizes "
        "[16, 24, 32, 48, 64] — the DPI-aware size selector picks the "
        "closest available size."
    )
    assert "tray-mic-{best}.png" in tray_icon_py_source, (
        "tray_icon.py must build the PNG path as "
        "asset_dir / f'tray-mic-{best}.png' — the {best} placeholder "
        "is the closest available size to the DPI-aware target."
    )


def test_tray_icon_path_windows_ico_preference(tray_icon_py_source) -> None:
    """PLAT-024: on Windows, .ico files are preferred for sharper rendering.

    ICO supports multiple sizes (16, 32, 48, 256) in one file and is
    the native format for Windows tray icons — sharper than PNG on
    Windows 11 with per-monitor DPI scaling.
    """
    assert "is_windows()" in tray_icon_py_source, (
        "tray_icon.py must check is_windows() before preferring .ico files — the ICO preference is Windows-only."
    )
    assert "tray-mic-{state.value}.ico" in tray_icon_py_source, (
        "tray_icon.py must try the state-specific ICO path "
        "(tray-mic-{state.value}.ico) first on Windows — this gives "
        "sharper rendering than recoloring a PNG at runtime."
    )
    assert 'base_ico = asset_dir / "tray-mic.ico"' in tray_icon_py_source, (
        "tray_icon.py must fall back to the base tray-mic.ico if no "
        "state-specific ICO exists — the base ICO is colorized at "
        "runtime per AppState."
    )


def test_tray_icon_shape_fallback_for_colorblind_accessibility(
    tray_icon_py_source,
) -> None:
    """PLAT-021 / TRAY-032: shape-only fallback for colorblind users.

    When no PNG is available, the renderer draws a shape-only icon
    (circle / square / diamond / triangle) so colorblind users can
    identify the state by shape alone. Each AppState maps to a
    distinct shape.
    """
    assert "_ICON_SHAPES" in tray_icon_py_source, (
        "tray_icon.py must define the _ICON_SHAPES dict — the AppState→shape map for colorblind-accessibility."
    )
    # Each AppState must have a shape (no fallback to "unknown").
    for state in ["IDLE", "RECORDING", "TRANSCRIBING", "LOADING", "ERROR", "CANCELLING"]:
        assert f"AppState.{state}:" in tray_icon_py_source, (
            f"_ICON_SHAPES must define a shape for AppState.{state} — "
            f"every state needs a distinct shape for colorblind users."
        )
    assert "def _draw_shape(" in tray_icon_py_source, (
        "tray_icon.py must define _draw_shape — the shape-only fallback "
        "renderer for environments where no PNG icon is available."
    )


# ─── Section E: Tauri tray API choice (built-in, not separate plugin) ───────


def test_cargo_toml_no_separate_tray_plugin_crate(cargo_toml_source) -> None:
    """ADR-0020 §6.5 (Tauri v2 note): no ``tauri-plugin-tray`` crate dep.

    In Tauri v2 the tray API is built into the core ``tauri`` crate
    (enabled by the ``tray-icon`` cargo feature). The project must
    NOT declare a ``tauri-plugin-tray`` crate as a dependency (the
    v1-era crate doesn't exist for v2).

    This test inspects the ``[dependencies]`` table's KEYS only — the
    string "tauri-plugin-tray" DOES appear in a Cargo.toml comment
    that documents WHY the separate crate isn't used, so a naive
    substring search would false-positive on the comment.
    """
    # Extract the [dependencies] table body (up to the next table header).
    deps_match = re.search(
        r"^\[dependencies\]\s*\n(.*?)(?=^\[|\Z)",
        cargo_toml_source,
        re.MULTILINE | re.DOTALL,
    )
    assert deps_match, "Cargo.toml must declare a [dependencies] table — couldn't find the [dependencies] header."
    deps_body = deps_match.group(1)
    # Each dep entry is `name = { ... }` or `name = "version"`. Strip
    # comment lines so a `#` mention of tauri-plugin-tray in a comment
    # inside the [dependencies] table doesn't false-positive.
    deps_no_comments = "\n".join(line for line in deps_body.splitlines() if not line.strip().startswith("#"))
    # Dependency keys are at the start of a line (no leading whitespace).
    dep_keys = re.findall(r"^([a-zA-Z0-9_-]+)\s*=", deps_no_comments, re.MULTILINE)
    assert "tauri-plugin-tray" not in dep_keys, (
        "Cargo.toml's [dependencies] table must NOT declare a "
        "tauri-plugin-tray dependency — in Tauri v2 the tray API is "
        "built into the core 'tauri' crate. The v1-era "
        "tauri-plugin-tray crate does not exist for v2. (Found dep "
        f"keys: {dep_keys})"
    )


def test_cargo_toml_documents_builtin_tray_api(cargo_toml_source) -> None:
    """ADR-0020 §6.5: Cargo.toml documents the built-in tray API choice.

    A Cargo.toml comment must explain that tray support is built
    into the core ``tauri`` crate (not a separate plugin) so future
    contributors don't try to add ``tauri-plugin-tray`` as a dep.
    """
    assert "tray support is built into the core crate" in cargo_toml_source, (
        "Cargo.toml must document (in a comment) that tray support is "
        "built into the core 'tauri' crate in v2 — prevents future "
        "contributors from adding a non-existent tauri-plugin-tray dep."
    )
    assert "NOT a separate tauri-plugin-tray crate" in cargo_toml_source, (
        "Cargo.toml must explicitly note that tray support is NOT a "
        "separate crate — the v1→v2 migration changed this and the "
        "comment is the contract."
    )


def test_cargo_toml_tray_icon_feature_documentation(
    cargo_toml_source,
) -> None:
    """ADR-0020 §6.5: Cargo.toml documents the ``tray-icon`` feature gate.

    The ``tray-icon`` cargo feature on the ``tauri`` crate enables
    the tray API. The Cargo.toml comment must mention this feature
    so future contributors know how to turn it on when the host
    starts rendering the tray directly.
    """
    assert '"tray-icon" feature' in cargo_toml_source, (
        "Cargo.toml must mention the 'tray-icon' cargo feature in a "
        "comment — this is the gate that enables the tray API on the "
        "core 'tauri' crate."
    )


def test_cargo_toml_tray_icon_feature_not_yet_enabled(
    cargo_toml_source,
) -> None:
    """GAP-2: ``tray-icon`` feature is NOT yet enabled on the ``tauri`` crate.

    The Cargo.toml declares ``tauri = { version = "2", features = ["devtools"] }``
    — the ``tray-icon`` feature is documented in a comment but not
    turned on, because the Rust host has no tray code (the Python
    sidecar owns the tray via pystray — see GAP-1). Enabling the
    feature would compile the ``tray-icon`` crate for nothing.

    This test documents the gap so a future host-side tray rendering
    iteration knows to flip the feature on.
    """
    # The tauri dep line must NOT include "tray-icon" in its features list.
    tauri_dep_match = re.search(
        r"^tauri\s*=\s*\{[^}]*features\s*=\s*\[([^\]]*)\][^}]*\}",
        cargo_toml_source,
        re.MULTILINE,
    )
    assert tauri_dep_match, (
        "Cargo.toml must declare the 'tauri' dependency — couldn't find the tauri = { ... features = [...] } line."
    )
    features_list = tauri_dep_match.group(1)
    assert "tray-icon" not in features_list, (
        "GAP-2: the 'tray-icon' cargo feature is NOT yet enabled on "
        "the 'tauri' crate (the Rust host has no tray code — the "
        "Python sidecar owns the tray). When the host starts "
        "rendering the tray directly, flip this feature on by adding "
        "'tray-icon' to the features list."
    )


# ─── Section F: Rust host tray status — Python sidecar owns it ──────────────


def test_main_rs_no_direct_tray_setup(main_rs_source) -> None:
    """GAP-1: ``main.rs`` does NOT directly configure the tray.

    Per ADR-0020 §6.5, the Rust host was supposed to render the tray
    icon via Tauri's built-in tray API (with the menu piped from the
    sidecar). The v1 implementation skips this: the Python sidecar
    owns the entire tray via pystray, and ``main.rs`` has zero tray
    code. This is documented in the capability file's description
    (see ``test_capability_file_documents_tray_ownership_strategy``).

    This test confirms the gap is real (``main.rs`` has no
    ``TrayIconBuilder``, no ``tray_menu`` event handler, no
    ``tray_click`` dispatch) so future host-side rendering work has
    a clear baseline.
    """
    # No tray builder, no tray event handler, no tray-click dispatch.
    forbidden_tokens = [
        "TrayIconBuilder",
        "tray_menu",
        "tray_click",
        "tray::TrayIconBuilder",
        "tauri::tray::",
        ".tray(",
    ]
    for token in forbidden_tokens:
        assert token not in main_rs_source, (
            f"GAP-1 (informational): main.rs contains {token!r} — the "
            f"v1 implementation expected NO tray code in main.rs (the "
            f"Python sidecar owns the tray via pystray; see capability "
            f"file's description). If you're adding host-side tray "
            f"rendering, update this test + the capability file."
        )


def test_main_rs_tray_ownership_documented_in_capability(
    capability_json,
) -> None:
    """GAP-1: capability file documents the Python-sidecar-owns-tray decision.

    The ``migrate-runtime.json`` capability's ``description`` field
    records the deliberate decision that the Python sidecar owns
    the tray (no ``core:tray:*`` permissions granted to the Rust
    host). This is the contract that justifies the gap from ADR-0020
    §6.5.
    """
    description = capability_json.get("description", "")
    assert "Python sidecar owns the tray" in description, (
        "capability file's description must document that 'the Python "
        "sidecar owns the tray' — this is the rationale for not "
        "granting core:tray:* permissions to the Rust host."
    )
    assert "pystray" in description, (
        "capability file's description must mention pystray — the "
        "Python library the sidecar uses to render the tray icon + menu."
    )
    assert "no core:tray:* permissions are granted" in description, (
        "capability file's description must explicitly state that no "
        "core:tray:* permissions are granted to the Rust host — this "
        "is the least-privilege contract."
    )


def test_capability_file_no_core_tray_permissions_granted(
    capability_json,
) -> None:
    """GAP-1: no ``core:tray:*`` permissions are granted to the Rust host.

    Since the Python sidecar owns the tray (via pystray), the Rust
    host doesn't need any tray permissions. The capability file's
    ``permissions`` list must NOT contain any ``core:tray:*`` entry.
    """
    permissions = capability_json.get("permissions", [])
    tray_perms = [p for p in permissions if "tray" in p.lower()]
    assert tray_perms == [], (
        f"capability file must NOT grant any core:tray:* permissions "
        f"to the Rust host (the Python sidecar owns the tray via "
        f"pystray). Found tray permissions: {tray_perms}"
    )


def test_main_rs_plugins_do_not_include_tray(main_rs_source) -> None:
    """GAP-1: ``main.rs`` does not register a tray plugin.

    Even if the tray API is built into the core crate (Tauri v2),
    the Rust host would still need to register / build the tray
    icon in ``main.rs``. Since the Python sidecar owns the tray,
    no tray plugin / builder registration appears in ``main.rs``.
    """
    # No tauri_plugin_tray or tray-related plugin registration.
    forbidden_patterns = [
        r"tauri_plugin_tray",
        r"\.plugin\(tauri::tray::",
        r"tauri::tray::TrayIconBuilder",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, main_rs_source), (
            f"GAP-1 (informational): main.rs matches {pattern!r} — "
            f"the v1 implementation expected NO tray plugin "
            f"registration in main.rs."
        )


# ─── Section G: tauri.conf.json tray plugin section ─────────────────────────


def test_tauri_conf_no_separate_tray_plugin_section() -> None:
    """ADR-0020 §6.5: ``tauri.conf.json`` has no separate ``tray`` plugin entry.

    ADR-0020 §7 anticipated a ``"tray": {}`` entry under
    ``plugins``. In Tauri v2 the tray API is built into the core
    crate (no plugin registration), and the v1 implementation
    doesn't use it anyway (Python sidecar owns the tray). The
    ``plugins`` section must NOT include ``"tray"``.
    """
    assert TAURI_CONF.exists(), f"tauri.conf.json not found: {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    plugins = conf.get("plugins", {})
    assert "tray" not in plugins, (
        "tauri.conf.json's plugins section must NOT include 'tray' — "
        "in Tauri v2 the tray API is built into the core crate (no "
        "plugin registration), and the v1 implementation doesn't use "
        "it (Python sidecar owns the tray). The 'tray': {} entry from "
        "ADR-0020 §7 was a v1-era anticipation that's no longer needed."
    )
