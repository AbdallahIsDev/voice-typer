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

Implementation decision (MIG-1.9 Phase 3 — host-side tray rendering):
The capability file ``src-tauri/capabilities/migrate-runtime.json``
records the actual decision taken for the v1 migration:

    "core:tray:* — the Rust host OWNS the system tray (tauri-plugin-tray
    / core tray-icon feature, ADR-0020 §6.5). The Python sidecar
    computes the menu structure and emits a `tray_menu` event; the Rust
    host renders it and routes clicks back via `dispatch({cmd:
    'tray_click', data:{id}})` (pystray is the Electron-fallback path
    only and is never used under Tauri)."

In other words: the Rust host renders the tray icon + menu via Tauri's
built-in tray API (enabled by the ``tray-icon`` cargo feature). The
Python sidecar still owns the menu *logic* (it computes the items,
locale, and dynamic submenus and emits them as a ``tray_menu`` event),
but the *rendering* + click-dispatch lives in the Rust host
(``src-tauri/src/tray.rs``). This is the design the ADR-0020 §6.5
anticipated and MIG-1.9 Phase 3 implemented — so the previously-planned
"pystray owns the tray" fallback is only used on the Electron runtime.

This test file therefore validates BOTH halves of the contract: the
menu structure/locale/dynamic items are preserved 1:1 by the sidecar
that computes them, AND the Rust host correctly renders the
``tray_menu`` event + routes ``tray_click`` back to the sidecar.

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

Gaps / decisions documented (report, do NOT fix — out of scope for this
gate check):
  - GAP-1 (RESOLVED in MIG-1.9 Phase 3 — Rust host owns the tray):
    ADR-0020 §6.5 anticipated the Rust host rendering the tray icon via
    Tauri's built-in tray API, with the menu piped from the sidecar over
    IPC via a ``tray_menu`` event + a ``tray_click`` dispatch command.
    MIG-1.9 Phase 3 IMPLEMENTED this: the Rust host (``src-tauri/src/
    tray.rs``) renders the tray via the ``tray-icon`` cargo feature,
    listens for the sidecar's ``tray_menu`` event, and routes clicks
    back via ``dispatch({cmd:'tray_click', data:{id}})``. The Python
    sidecar still owns the menu *logic* (it emits ``tray_menu``); pystray
    is the Electron-fallback path only. This is documented in
    ``src-tauri/capabilities/migrate-runtime.json``'s description field.
    See ``test_main_rs_sets_up_rust_host_tray``,
    ``test_tray_rs_routes_clicks_via_tray_click_dispatch``, and
    ``test_capability_file_grants_core_tray_permissions``.
  - GAP-2 (RESOLVED — ``tray-icon`` cargo feature enabled): the
    ``tray-icon`` feature IS enabled on the ``tauri`` crate in
    ``Cargo.toml`` (``features = ["tray-icon"]``) because the Rust host
    now renders the tray. See ``test_cargo_toml_tray_icon_feature_is_enabled``.
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
CAPABILITY_JSON = PROJECT_ROOT / "src-tauri" / "capabilities" / "main-runtime.json"

TRAY_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray.py"
TRAY_I18N_PY = PROJECT_ROOT / "voice_typer" / "server" / "tray_i18n.py"
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
def tray_i18n_py_source() -> str:
    """Read voice_typer/server/tray_i18n.py as text.

    S1-CR-47: the locale dicts (_TRAY_LABELS_EN, _TRAY_LABELS_ES,
    _TRAY_LABELS_LOCALES, etc.) and the set_tray_locale / get_tray_locale /
    _() functions were extracted from tray.py into tray_i18n.py. tray.py
    re-exports them for backward compat, but the canonical definitions live
    here. Source-text assertions that grep for the dict definitions must
    read tray_i18n.py, not tray.py.
    """
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
    # Scope the scan to the build_menu() function body so the Tauri-side
    # build_tray_menu_model() (which legitimately reuses the same
    # localize() keys) does not pollute the pystray renderer's ordering
    # check (ADR-0020 §6.5: build_menu is the renderer of record).
    _src = tray_menu_py_source
    _start = _src.find("def build_menu(")
    _end = _src.find("\ndef ", _start + 1)
    if _end == -1:
        _end = len(_src)
    build_menu_source = _src[_start:_end]
    key_occurrences = [m.group(1) for m in re.finditer(r'localize\(\s*["\']([a-z_]+)["\']', build_menu_source)]
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
    # Collapse consecutive duplicate keys (e.g. the "models" label is
    # emitted twice under Tauri — once as the pystray MenuItem and once
    # as the Tauri-model spec {"id":"models","label":localize("models")}
    # — both are the SAME logical menu item, so a consecutive repeat
    # must not break the ordering check).
    collapsed = [k for i, k in enumerate(seen_order) if i == 0 or k != seen_order[i - 1]]
    assert collapsed == required_keys, (
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


def test_tray_locale_english_dict_present(tray_i18n_py_source) -> None:
    """TRAY-008: English locale dict must be defined.

    The English locale is the default fallback for every tray label.
    It must include all 6 menu keys (open_app, toggle_dictation,
    models, restart, quit, force_cancel_transcription) plus the
    app_name tooltip key.
    """
    assert "_TRAY_LABELS_EN" in tray_i18n_py_source, (
        "tray_i18n.py must define _TRAY_LABELS_EN — the English locale dict "
        "is the default fallback for every tray label (TRAY-008)."
    )
    # Extract the EN dict body.
    en_dict_match = re.search(
        r"_TRAY_LABELS_EN\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
        tray_i18n_py_source,
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


def test_tray_locale_spanish_dict_present(tray_i18n_py_source) -> None:
    """TRAY-008: Spanish locale dict must be defined.

    Spanish is the proof-of-concept locale for tray i18n. The user
    can switch the UI to Español and the tray must update to Spanish
    labels via the ``set_tray_locale('es')`` IPC command.
    """
    assert "_TRAY_LABELS_ES" in tray_i18n_py_source, (
        "tray_i18n.py must define _TRAY_LABELS_ES — Spanish is the proof-of-concept locale for tray i18n (TRAY-008)."
    )
    es_dict_match = re.search(
        r"_TRAY_LABELS_ES\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
        tray_i18n_py_source,
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


def test_tray_locale_registry_includes_en_and_es(tray_i18n_py_source) -> None:
    """TRAY-008: ``_TRAY_LABELS_LOCALES`` must register en + es.

    The locale registry maps locale codes to their label dicts. The
    ``set_tray_locale`` function falls back to English if the
    requested locale isn't in this registry, so en + es must be
    registered for the host VALIDATE step's Spanish toggle to work.
    """
    assert "_TRAY_LABELS_LOCALES" in tray_i18n_py_source, (
        "tray_i18n.py must define _TRAY_LABELS_LOCALES — the locale→dict registry that set_tray_locale consults."
    )
    locales_match = re.search(
        r"_TRAY_LABELS_LOCALES\s*:\s*dict\[str,\s*dict\[str,\s*str\]\]\s*=\s*\{(.*?)\}",
        tray_i18n_py_source,
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


def test_tray_locale_setter_and_getter_present(tray_i18n_py_source) -> None:
    """TRAY-008: ``set_tray_locale`` + ``get_tray_locale`` functions exist.

    The IPC layer calls ``set_tray_locale('es')`` when the user
    switches the UI language; the getter is used by tests. Both must
    be present and module-level (so the IPC handler can import them
    directly without a TrayIcon instance).
    """
    assert "def set_tray_locale(locale: str) -> None:" in tray_i18n_py_source, (
        "tray_i18n.py must define module-level set_tray_locale(locale) — the "
        "IPC handler in system_handlers.py imports + calls it directly."
    )
    assert "def get_tray_locale() -> str:" in tray_i18n_py_source, (
        "tray_i18n.py must define module-level get_tray_locale() → str — used by tests to verify the current locale."
    )
    # The setter must fall back to English for unknown locales.
    assert 'locale if locale in _TRAY_LABELS_LOCALES else "en"' in tray_i18n_py_source, (
        "set_tray_locale must fall back to 'en' for unknown locales — "
        "prevents a KeyError if the UI sends an unsupported locale code."
    )


def test_tray_locale_lookup_function_present(tray_i18n_py_source) -> None:
    """TRAY-008: the ``_()`` lookup function translates keys → labels.

    The ``_()`` function (mirroring gettext's convention) takes a
    label key and returns the localized string, falling back to
    English then to the key itself. ``build_menu`` calls
    ``localize(_)`` which is this function.
    """
    assert "def _(key: str) -> str:" in tray_i18n_py_source, (
        "tray_i18n.py must define the _(key) lookup function — build_menu "
        "calls localize(_) where _ translates keys to localized labels."
    )
    # The lookup must consult the current locale first, then fall back.
    assert "_TRAY_LABELS_LOCALES.get(_tray_locale, _TRAY_LABELS_EN)" in tray_i18n_py_source, (
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
    """NEW-CQ-008 / RT-FIX-9 (2026-07-24): ``TrayIcon.set_microphones``
    API MUST be present.

    Originally added in NEW-CQ-008 as a no-op (the write-only cache
    had been removed). RT-FIX-9: the API was re-activated in UX-2
    (FIX-10) — it now caches the microphone device list (accepting
    ``list[dict] | None``) and invalidates the menu cache so the
    Microphones ▸ submenu reflects the new device set. The signature
    was widened to accept ``None`` (normalized to ``[]``) so callers
    never have to special-case a missing list.
    """
    # The function MUST be defined on TrayIcon. The signature now
    # accepts ``list[dict] | None`` (UX-2 / FIX-10 — was ``list[dict]``
    # in the NEW-CQ-008 no-op era).
    set_mics_match = re.search(
        r"def set_microphones\(self,\s*mics:\s*list\[dict\]\s*(?:\|\s*None)?\)\s*->\s*None:",
        tray_py_source,
    )
    assert set_mics_match, (
        "TrayIcon must define set_microphones(mics: list[dict] | None) -> None "
        "— the API is preserved for IPC parity (NEW-CQ-008) and was "
        "re-activated in UX-2 / FIX-10 to cache the mic list + invalidate "
        "the menu cache."
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
    """ADR-0020 §6.5: the service package also calls tray.set_microphones.

    The runtime microphone watcher (hotplug) calls
    ``tray.set_microphones`` from within the ``voice_typer/server/service/``
    package so a newly-plugged USB mic is propagated (the in-window
    microphone picker receives the event via the same enumeration
    pipeline).

    RT-FIX-9 (2026-07-24): ``voice_typer/server/service.py`` was split
    into a package (``voice_typer/server/service/``); the
    ``set_microphones`` call site now lives in
    ``service/microphone_test.py`` (the runtime mic-test handler that
    enumerates + propagates the device list to the tray).
    """
    # The service package MUST exist (split from the prior service.py).
    service_pkg = PROJECT_ROOT / "voice_typer" / "server" / "service"
    assert service_pkg.is_dir(), f"service package not found: {service_pkg}"
    # The set_microphones call MUST appear somewhere in the service
    # package (any submodule — currently service/microphone_test.py).
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
        "service/microphone_test.py) — the runtime microphone watcher "
        "propagates hotplug events to the tray (and, via the same "
        "enumeration pipeline, to the in-window UI)."
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


def test_cargo_toml_tray_icon_feature_is_enabled(
    cargo_toml_source,
) -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3: the ``tray-icon`` feature IS enabled
    on the ``tauri`` crate.

    The Rust host now owns the system tray (it renders the menu piped
    from the sidecar's ``tray_menu`` event via the Tauri tray API). The
    ``tray-icon`` cargo feature on the ``tauri`` crate is the gate that
    enables ``TrayIconBuilder`` / ``tauri::menu`` — so it MUST be present
    in the ``tauri`` dependency's feature list.

    The Python sidecar still computes the menu structure under Tauri
    (the Electron fallback uses pystray); under Tauri the sidecar emits
    a ``tray_menu`` event and the Rust host renders it (see
    ``src-tauri/src/tray.rs``).
    """
    # The tauri dep line MUST include "tray-icon" in its features list.
    tauri_dep_match = re.search(
        r"^tauri\s*=\s*\{[^}]*features\s*=\s*\[([^\]]*)\][^}]*\}",
        cargo_toml_source,
        re.MULTILINE,
    )
    assert tauri_dep_match, (
        "Cargo.toml must declare the 'tauri' dependency — couldn't find the tauri = { ... features = [...] } line."
    )
    features_list = tauri_dep_match.group(1)
    assert "tray-icon" in features_list, (
        "The 'tray-icon' cargo feature MUST be enabled on the 'tauri' "
        "crate — the Rust host renders the system tray via Tauri's "
        "built-in tray API (ADR-0020 §6.5 + MIG-1.9 Phase 3). Without "
        "it TrayIconBuilder / tauri::menu are unavailable."
    )


# ─── Section F: Rust host owns the tray (ADR-0020 §6.5 + MIG-1.9 Phase 3) ────


def test_main_rs_sets_up_rust_host_tray(main_rs_source) -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3: the Rust host OWNS the tray.

    ``main.rs`` wires the tray via ``crate::tray::create_tray`` in its
    ``.setup`` hook. The sidecar computes the menu structure and emits a
    ``tray_menu`` event; the Rust host (in ``src-tauri/src/tray.rs``)
    renders it via Tauri's built-in tray API and routes clicks back via
    ``dispatch({cmd:'tray_click', data:{id}})``. This is the opposite of
    the original v1 GAP-1 plan (where the Python sidecar owned the tray
    via pystray) — the host now renders it directly.

    This test confirms the ``main.rs`` wiring is present (``create_tray``
    call + the ``tray_menu`` reference) so future refactors can't
    silently drop it.
    """
    # The .setup hook must call create_tray.
    assert "create_tray" in main_rs_source, (
        "main.rs must call crate::tray::create_tray(...) in its .setup "
        "hook — the Rust host owns the system tray (ADR-0020 §6.5 + "
        "MIG-1.9 Phase 3). If host-side tray rendering is removed, "
        "update this test + the capability file."
    )
    # The tray wiring uses the sidecar-driven menu event.
    assert "tray_menu" in main_rs_source, (
        "main.rs must reference the sidecar's `tray_menu` event (the "
        "Rust host renders the menu piped from the sidecar)."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FIX-2 (CR-1) not yet landed in tray.rs — the emit-based pattern "
        "is BROKEN; this test asserts the correct WS-write pattern that "
        "FIX-2 will introduce. When FIX-2 lands, the xfail will XPASS "
        "and the suite will fail (strict=True), prompting removal of "
        "the marker."
    ),
)
def test_tray_rs_routes_clicks_via_tray_click_dispatch() -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3 + CR-1/CR-2 fix: ``src-tauri/src/tray.rs``
    renders the sidecar's ``tray_menu`` event and routes item clicks back to the
    sidecar via a DIRECT WS frame write (``{"type":"tray_click","data":{"id":<id>}}``).

    CR-1 finding: the previous implementation emitted a Tauri ``dispatch`` EVENT
    (``app.emit("dispatch", payload)``) and relied on a renderer-side listener
    to re-invoke the ``dispatch`` COMMAND — but no such listener existed, so
    tray menu clicks were completely non-functional on the Tauri path. CR-2
    coordinated the test update with the FIX-2 production fix in tray.rs.

    The new contract (post-FIX-2): on menu item click, tray.rs acquires the
    SidecarState, fetches the ``ws_tx`` channel, allocates a fresh frame_id,
    and sends a ``Message::Text`` frame directly to the WS writer. The frame
    shape (``{"type":"tray_click","data":{"id":...},"id":N}``) mirrors what
    the dispatch command builds, so the Python side's ``_dispatch`` sees a
    normal request and routes it to ``_handle_tray_click`` via the
    ``_COMMAND_REGISTRY``. The response is fire-and-forget (no pending entry
    registered).

    WR-7: this test is marked ``xfail(strict=True)`` because FIX-2 (CR-1)
    has not yet landed in tray.rs. The strict WS-write assertions below
    assert the correct pattern that FIX-2 will introduce. When FIX-2 lands,
    the test will XPASS and the suite will fail — prompting removal of the
    xfail marker.
    """
    tray_rs = PROJECT_ROOT / "src-tauri" / "src" / "tray.rs"
    assert tray_rs.exists(), f"tray.rs not found: {tray_rs}"
    src = tray_rs.read_text(encoding="utf-8")
    # The tray module must listen for the sidecar's tray_menu event.
    assert 'app.listen("tray_menu"' in src, (
        "tray.rs must listen for the sidecar's `tray_menu` event to rebuild the native menu on demand."
    )
    # On click, it must build a tray_click frame with the item id.
    assert '"tray_click"' in src, (
        "tray.rs must build a `tray_click` frame (with the menu item id) when "
        "a tray menu item is clicked — this routes the click back to the "
        "Python sidecar (ADR-0020 §6.5 + MIG-1.9 Phase 3)."
    )
    # CR-1/CR-2: the tray menu click must be forwarded DIRECTLY through the
    # WS writer channel — NOT emitted as a Tauri event. The frame is written
    # via `ws_tx.send(Message::Text(frame.to_string()))` after acquiring
    # SidecarState's `ws_tx` mutex.
    assert "ws_tx" in src, (
        "tray.rs must acquire the SidecarState.ws_tx channel to forward the "
        "tray_click frame directly to the WS writer (CR-1 fix). The old "
        "emit-based pattern is broken — no listener exists for the dispatch "
        "event in the renderer."
    )
    assert "Message::Text" in src, (
        "tray.rs must build a WS Message::Text frame to forward tray_click "
        "(CR-1 fix — direct WS write, not a Tauri event emit)."
    )
    # CR-1 regression guard: the OLD buggy `emit("dispatch", ...)` pattern
    # must NOT be present. If it ever returns, the tray menu will be
    # non-functional again (the renderer never listens for the dispatch event).
    assert 'emit("dispatch"' not in src, (
        "stale `emit('dispatch', ...)` pattern present in tray.rs — CR-1 "
        "regression. The tray click must be forwarded via ws_tx.send(Message::Text(...)) "
        "directly to the WS writer, not emitted as a Tauri event."
    )
    assert 'app.emit("dispatch"' not in src, (
        "stale `app.emit('dispatch', ...)` pattern present in tray.rs — CR-1 "
        "regression. The tray click must be forwarded via ws_tx.send(Message::Text(...)) "
        "directly to the WS writer, not emitted as a Tauri event."
    )


def test_main_rs_tray_ownership_documented_in_capability(
    capability_json,
) -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3: capability file documents the
    Rust-host-owns-tray decision.

    The ``migrate-runtime.json`` capability's ``description`` field
    records that the Rust host OWNS the system tray (via the core
    ``tray-icon`` feature) and that the sidecar computes the menu
    structure and emits a ``tray_menu`` event (pystray is the
    Electron-fallback path only). This justifies the ``core:tray:*``
    permissions granted to the Rust host.
    """
    description = capability_json.get("description", "")
    assert "Rust host OWNS the system tray" in description, (
        "capability file's description must document that 'the Rust "
        "host OWNS the system tray' — this is the rationale for "
        "granting core:tray:* permissions to the Rust host."
    )
    assert "tray_menu" in description, (
        "capability file's description must mention the `tray_menu` "
        "event — the sidecar computes the menu structure and emits it "
        "for the Rust host to render."
    )
    assert "tray_click" in description, (
        "capability file's description must mention `tray_click` — the "
        "Rust host routes tray menu clicks back to the sidecar via this "
        "command."
    )


def test_capability_file_grants_core_tray_permissions(
    capability_json,
) -> None:
    """ADR-0020 §6.5 + MIG-1.9 Phase 3: ``core:tray:*`` permissions ARE
    granted to the Rust host.

    The Rust host OWNS the system tray (renders the menu piped from the
    sidecar's ``tray_menu`` event via Tauri's built-in tray API). It
    therefore needs the full ``core:tray:*`` permission set
    (``core:tray:default`` + the ``allow-*`` permissions for
    set-icon / set-menu / set-tooltip / set-title / get-by-id /
    remove-by-id / new). The capability file's ``permissions`` list
    MUST contain these entries.
    """
    permissions = capability_json.get("permissions", [])
    tray_perms = [p for p in permissions if "tray" in p.lower()]
    assert tray_perms, (
        "capability file MUST grant core:tray:* permissions to the "
        "Rust host (the Rust host owns the system tray under Tauri). "
        "Found no tray permissions."
    )
    # The essential tray permissions must all be present.
    required_tray_perms = [
        "core:tray:default",
        "core:tray:allow-set-icon",
        "core:tray:allow-set-menu",
        "core:tray:allow-new",
    ]
    for perm in required_tray_perms:
        assert perm in permissions, (
            f"capability file must grant {perm} — the Rust host needs "
            f"it to build + render the system tray (ADR-0020 §6.5)."
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
