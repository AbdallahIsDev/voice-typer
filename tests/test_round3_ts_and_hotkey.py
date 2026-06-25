"""Round 3 regression tests.

Covers:
  - NEW-TS-ERR-R2-001: Settings onNavigate typed as Page (not string)
  - NEW-TS-ERR-R2-002: NumberInputProps Omit includes onInvalid
  - NEW-UX-011: Re-Paste hotkey uses HotkeyPicker (no free-text)
  - NEW-UX-012: Dictation key expanded beyond F2-F12
  - HotkeyPicker utils: formatHotkeyLabel + validateHotkey
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


# ── NEW-TS-ERR-R2-001: Settings onNavigate typed as Page ─────────────


class TestNewTsErrR2001OnNavigateType:
    """The onNavigate prop must be typed as Page (the string-literal
    union), not string, so TypeScript can catch invalid page names."""

    def test_settings_imports_page_type(self):
        settings = _read("pages/Settings.tsx")
        assert "import type { Page } from '@/types/ipc'" in settings, (
            "Settings.tsx must import the Page type so onNavigate can be "
            "typed as (page: Page) => void instead of (page: string) => void"
        )

    def test_settings_onnavigate_typed_as_page(self):
        settings = _read("pages/Settings.tsx")
        # The onNavigate prop must be typed as (page: Page) => void
        assert "onNavigate?: (page: Page) => void" in settings, (
            "SettingsPageProps.onNavigate must be typed as (page: Page) => void "
            "not (page: string) => void — see NEW-TS-ERR-R2-001"
        )

    def test_app_passes_navigate_without_type_error(self):
        """App.tsx passes its `navigate: (page: Page) => void` to
        SettingsPage.  With the fix, this should type-check cleanly."""
        app = _read("App.tsx")
        assert "onNavigate={navigate}" in app
        # App must also import Page (it already does for its own
        # navigate function — just verify it's there).
        assert "Page" in app


# ── NEW-TS-ERR-R2-002: NumberInputProps Omit onInvalid ───────────────


class TestNewTsErrR2002NumberInputOnInvalid:
    """NumberInputProps must Omit 'onInvalid' from the inherited HTML
    attributes so the custom semantic callback doesn't conflict."""

    def test_omit_includes_oninvalid(self):
        number_input = _read("components/ui/number-input.tsx")
        # The Omit must include "onInvalid" alongside "type" and "onChange".
        assert re.search(
            r'Omit<React\.ComponentProps<"input">,\s*"type"\s*\|\s*"onChange"\s*\|\s*"onInvalid"',
            number_input,
        ), (
            'NumberInputProps must Omit "onInvalid" from ComponentProps<"input"> '
            "to avoid the TS2430 conflict with the inherited HTML onInvalid handler"
        )

    def test_custom_oninvalid_still_declared(self):
        """The custom onInvalid callback must still be declared on the
        interface (just not conflicting with the inherited one)."""
        number_input = _read("components/ui/number-input.tsx")
        assert 'onInvalid?: (reason: "parse" | "range" | null) => void' in number_input


# ── NEW-UX-011: Re-Paste hotkey uses HotkeyPicker ────────────────────


class TestNewUx011RepasteHotkeyPicker:
    """The Re-Paste Key setting must use the HotkeyPicker component
    (which lets the user press a combo) instead of a free-text Input
    that required pynput syntax knowledge."""

    def test_settings_imports_hotkey_picker(self):
        settings = _read("pages/Settings.tsx")
        assert "import { HotkeyPicker }" in settings
        assert "@/components/HotkeyPicker" in settings

    def test_repaste_key_uses_hotkey_picker_combo_mode(self):
        settings = _read("pages/Settings.tsx")
        # The Re-Paste Key row must use HotkeyPicker with mode="combo".
        assert "<HotkeyPicker" in settings
        assert 'mode="combo"' in settings
        assert 'repaste_hotkey' in settings

    def test_no_free_text_input_for_repaste(self):
        """The old free-text Input for repaste_hotkey must be gone.
        Previously it was:
            <Input value={config.repaste_hotkey} onChange={...} />
        which silently failed on invalid pynput syntax."""
        settings = _read("pages/Settings.tsx")
        # The repaste_hotkey must NOT appear in an Input element's value.
        # Look for the pattern `value={config.repaste_hotkey` inside an
        # Input tag — that would indicate the old free-text field.
        assert not re.search(
            r'<Input[^>]*value=\{config\.repaste_hotkey',
            settings,
            re.DOTALL,
        ), (
            "Re-Paste Key must not use a free-text Input — use HotkeyPicker instead"
        )


# ── NEW-UX-012: Dictation key expanded beyond F2-F12 ─────────────────


class TestNewUx012DictationKeyExpanded:
    """The Dictation Key selector must support more than just F2-F12.
    The HotkeyPicker's SINGLE_KEY_PRESETS includes Caps Lock, Print
    Screen, Scroll Lock, Pause, Insert, Home, Page Up/Down."""

    def test_dictation_key_uses_hotkey_picker_single_mode(self):
        settings = _read("pages/Settings.tsx")
        assert 'mode="single"' in settings

    def test_single_key_presets_include_beyond_f12(self):
        utils = _read("components/hotkey-utils.ts")
        # Must include Caps Lock, Print Screen, etc.
        assert "caps_lock" in utils
        assert "print_screen" in utils
        assert "scroll_lock" in utils
        assert "pause" in utils
        assert "insert" in utils
        assert "home" in utils
        assert "page_up" in utils
        assert "page_down" in utils

    def test_old_f2_f12_dropdown_removed(self):
        """The old hardcoded `['f2', 'f3', ... 'f12'].map(...)` dropdown
        must be gone from Settings.tsx."""
        settings = _read("pages/Settings.tsx")
        # The old pattern was a list literal inside Settings.tsx.
        # After the fix, the presets live in hotkey-utils.ts.
        assert "'f2', 'f3', 'f4', 'f5', 'f6'" not in settings, (
            "The old F2-F12 dropdown list must be removed from Settings.tsx "
            "— it's been replaced by HotkeyPicker"
        )


# ── HotkeyPicker utils: formatHotkeyLabel + validateHotkey ───────────


class TestHotkeyUtilsFormatLabel:
    """formatHotkeyLabel converts pynput syntax to human-readable."""

    def test_formats_single_key(self):
        utils = _read("components/hotkey-utils.ts")
        # The function must exist and handle the cases below via the
        # displayMap.  We verify the source contains the mapping logic.
        assert "function formatHotkeyLabel" in utils
        assert "'Ctrl'" in utils  # ctrl modifier
        assert "'Caps Lock'" in utils  # caps_lock
        assert "'Space'" in utils  # space

    def test_formats_combo(self):
        """The function splits on '+' and formats each part."""
        utils = _read("components/hotkey-utils.ts")
        assert ".split('+')" in utils
        assert ".join('+')" in utils


class TestHotkeyUtilsValidate:
    """validateHotkey returns null for valid, error string for invalid."""

    def test_validate_function_exists(self):
        utils = _read("components/hotkey-utils.ts")
        assert "function validateHotkey" in utils

    def test_validate_rejects_empty(self):
        utils = _read("components/hotkey-utils.ts")
        assert "Hotkey is empty" in utils

    def test_validate_rejects_modifiers_only_in_combo(self):
        """In combo mode, the last key must be a non-modifier."""
        utils = _read("components/hotkey-utils.ts")
        assert "must end with a non-modifier key" in utils

    def test_validate_rejects_multi_key_in_single_mode(self):
        """In single mode, only one key is allowed (no modifiers)."""
        utils = _read("components/hotkey-utils.ts")
        assert "must be a single key" in utils


# ── TypeScript: tsc -p tsconfig.web.json must pass ────────────────────


class TestTypeScriptWebConfigClean:
    """The web tsconfig must type-check cleanly.  Previously
    `npm run typecheck` ran `tsc --noEmit` against the root tsconfig
    which has `files: []` and missed errors in the renderer."""

    def test_package_json_typecheck_includes_web_config(self):
        """npm run typecheck must check all three sub-configs."""
        pkg = json.loads((CLIENT_SRC.parent / "package.json").read_text())
        typecheck_script = pkg.get("scripts", {}).get("typecheck", "")
        assert "tsconfig.web.json" in typecheck_script, (
            "package.json typecheck script must include tsc -p tsconfig.web.json --noEmit"
        )
        assert "tsconfig.node.json" in typecheck_script, (
            "package.json typecheck script must include tsc -p tsconfig.node.json --noEmit"
        )

    def test_typecheck_web_script_exists(self):
        """A standalone typecheck:web script must exist for targeted runs."""
        pkg = json.loads((CLIENT_SRC.parent / "package.json").read_text())
        assert "typecheck:web" in pkg.get("scripts", {}), (
            "package.json must have a typecheck:web script"
        )
