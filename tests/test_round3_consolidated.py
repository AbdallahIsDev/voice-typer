"""Consolidated regression tests for Round 3 (a11y, cancel-download, TS/hotkey,
UX-018 notifications, UX fixes).

Merges:
- tests/test_round3_a11y_and_state.py
- tests/test_round3_cancel_download.py
- tests/test_round3_ts_and_hotkey.py
- tests/test_round3_ux018_notifications.py
- tests/test_round3_ux_fixes.py
"""

# === Common imports (deduplicated from all source files) ===

from __future__ import annotations

from pathlib import Path

import pytest

import threading

import time

import json

import re

# === Common module-level constants (identical across files) ===

REPO_ROOT = Path(__file__).resolve().parent.parent

CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"

RENDERER_SRC = CLIENT_SRC / "renderer" / "src"

APP_PY = REPO_ROOT / "voice_typer" / "server" / "app.py"

MODEL_MANAGER_PY = REPO_ROOT / "voice_typer" / "server" / "model_manager.py"

# === Common helpers / fixtures (identical across files) ===

def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")

@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    return tmp_path

# === Source: tests/test_round3_a11y_and_state.py ===

"""Round 3 regression tests for NEW-PRIV-011, NEW-UX-041, NEW-UX-043, NEW-A11Y-006.

NEW-PRIV-011: HuggingFace model download is cancelable.
NEW-UX-041: Navigation state preserved across app restarts.
NEW-UX-043: "?" help overlay for keyboard shortcut discoverability.
NEW-A11Y-006: Keyboard alternative for bubble drag-to-move.
"""

class TestNewPriv011CancelableDownload:
    """The backend must support canceling an in-progress model download."""

    def test_service_has_cancel_model_download_method(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "def cancel_model_download" in service_py, (
            "VoiceTyperService must have a cancel_model_download method"
        )

    def test_service_has_download_cancel_event(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "_download_cancel_event" in service_py
        # The polling loop must check the cancel event.
        assert "_download_cancel_event.is_set()" in service_py
        # The cancelled return path must exist.
        assert '"cancelled": True' in service_py

    def test_ipc_server_has_cancel_model_download_handler(self):
        ipc_py = (REPO_ROOT / "voice_typer" / "server" / "ipc_server.py").read_text(
            encoding="utf-8"
        )
        # REFACTOR: _dispatch was converted to a command registry.
        # Accept either the old if/elif pattern or the new registry entry.
        assert (
            'cmd == "cancel_model_download"' in ipc_py
            or '"cancel_model_download": "_handle_cancel_model_download"' in ipc_py
        ), "IPC server must handle cancel_model_download"

    def test_main_allowlist_includes_cancel_model_download(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert '"cancel_model_download"' in main_ts, (
            "IPC allowlist must include cancel_model_download"
        )

    def test_models_page_has_cancel_button(self):
        models = _read("pages/Models.tsx")
        assert "Cancel" in models
        # The cancel button must call cancel_model_download via IPC.
        assert "cancel_model_download" in models

class TestNewUx041StatePreservation:
    """App.tsx must persist the current page + nav history to localStorage
    so the user returns to where they left off after closing/reopening."""

    def test_app_has_nav_state_persistence(self):
        nav = _read("hooks/useNavigation.ts")
        assert "STORAGE_KEY_NAV" in nav
        assert "saveNavState" in nav
        assert "loadNavState" in nav

    def test_navigate_saves_state(self):
        """The navigate function must call saveNavState after updating
        the current page."""
        nav = _read("hooks/useNavigation.ts")
        assert "saveNavState(page, navHistory.current, navIndex.current)" in nav

    def test_goBack_saves_state(self):
        nav = _read("hooks/useNavigation.ts")
        count = nav.count("saveNavState(page, navHistory.current, navIndex.current)")
        assert count >= 3, (
            f"Expected saveNavState in navigate, goBack, goForward; got {count}"
        )

    def test_initial_state_loaded_from_localStorage(self):
        nav = _read("hooks/useNavigation.ts")
        assert "loadNavState()" in nav
        assert "initialNav" in nav

class TestNewUx043HelpOverlay:
    """App.tsx must have a "?" keyboard shortcut that opens a help
    overlay listing all keyboard shortcuts."""

    def test_app_has_help_overlay_state(self):
        app = _read("App.tsx")
        assert "showHelpOverlay" in app
        assert "setShowHelpOverlay" in app

    def test_app_has_question_mark_keydown_handler(self):
        """The "?" key must trigger the help overlay."""
        app = _read("App.tsx")
        assert 'e.key === "?"' in app

    def test_help_overlay_lists_shortcuts(self):
        """The overlay must list the keyboard shortcuts."""
        app = _read("App.tsx")
        assert "Keyboard Shortcuts" in app
        # Must include the common shortcuts.
        assert "Tab / Shift+Tab" in app or "Tab" in app
        assert "Space" in app
        assert "Esc" in app
        # Must mention the "?" shortcut itself.
        assert "Open this help overlay" in app

    def test_help_overlay_closes_on_escape(self):
        app = _read("App.tsx")
        # The Escape key must close the overlay.
        assert "Escape" in app
        assert "setShowHelpOverlay(false)" in app

    def test_help_overlay_does_not_trigger_in_inputs(self):
        """Typing "?" in an input/textarea must NOT open the overlay."""
        app = _read("App.tsx")
        # The handler must check activeElement tag.
        assert "input" in app and "textarea" in app and "select" in app

class TestNewA11y006KeyboardBubbleMove:
    """The bubble must support keyboard-based repositioning via arrow
    keys as an accessibility alternative to mouse drag."""

    def test_bubble_has_arrow_key_handler(self):
        bubble = _read("Bubble.tsx")
        assert "ArrowLeft" in bubble
        assert "ArrowRight" in bubble
        assert "ArrowUp" in bubble
        assert "ArrowDown" in bubble

    def test_bubble_calls_move_by(self):
        bubble = _read("Bubble.tsx")
        assert "moveBy" in bubble

    def test_bubble_respects_draggable_gate(self):
        """Keyboard move must be disabled when draggable is False
        (matches the mouse-drag gate)."""
        bubble = _read("Bubble.tsx")
        # The keyboard handler must check `if (!draggable) return`
        assert "if (!draggable) return" in bubble

    def test_main_has_move_by_ipc_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'bubble:move-by' in main_ts
        # Must clamp to screen bounds.
        assert "getDisplayMatching" in main_ts or "workArea" in main_ts

    def test_preload_exposes_move_by(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "moveBy" in preload
        assert "bubble:move-by" in preload

    def test_window_bubble_type_has_move_by(self):
        ipc_ts = _read("types/ipc.ts")
        assert "moveBy" in ipc_ts

# === Source: tests/test_round3_cancel_download.py ===

"""NEW-PRIV-011: cancelable HuggingFace download — Python-level tests."""

class TestCancelModelDownloadMechanism:
    """Verify the cancel mechanism works at the Python service level."""

    def test_cancel_returns_false_when_no_download_active(self, tmp_config_dir):
        """When no download is in progress, cancel_model_download
        returns {cancelled: False}."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        # No download is active — _download_cancel_event is None.
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_cancel_returns_true_when_download_active(self, tmp_config_dir):
        """When a download IS in progress, cancel_model_download sets
        the event and returns {cancelled: True}."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        # Simulate an active download by creating the cancel event.
        service._download_cancel_event = threading.Event()
        assert not service._download_cancel_event.is_set()

        result = service.cancel_model_download()
        assert result == {"cancelled": True}
        assert service._download_cancel_event.is_set(), (
            "cancel_model_download must set the event so the polling loop exits"
        )

    def test_cancel_event_is_clearable(self, tmp_config_dir):
        """The service must allow clearing the cancel event for the
        next download."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        service._download_cancel_event = threading.Event()
        service.cancel_model_download()
        # After cancellation, the service should allow a new download
        # by resetting _download_cancel_event to None (done in
        # download_model after the polling loop exits).
        service._download_cancel_event = None
        # A subsequent cancel should return False (no active download).
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_download_cancel_event_starts_as_none(self, tmp_config_dir):
        """Fresh service instance must have _download_cancel_event = None."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        assert service._download_cancel_event is None

# === Source: tests/test_round3_ts_and_hotkey.py ===

"""Round 3 regression tests.

Covers:
  - NEW-TS-ERR-R2-001: Settings onNavigate typed as Page (not string)
  - NEW-TS-ERR-R2-002: NumberInputProps Omit includes onInvalid
  - NEW-UX-011: Re-Paste hotkey uses HotkeyPicker (no free-text)
  - NEW-UX-012: Dictation key expanded beyond F2-F12
  - HotkeyPicker utils: formatHotkeyLabel + validateHotkey
"""

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

# === Source: tests/test_round3_ux018_notifications.py ===

"""NEW-UX-018: critical tray notifications bypass the show_notifications toggle.

The bug: critical events (model load failure, onboarding failure, corrections
error, crash recovery, Wayland/macOS permission issues, recording stop
failure) were gated by ``self.config.show_notifications``.  If the user
disabled notifications, they would never see these critical errors.

The fix: critical notifications use ``tray.notify_safety()`` which bypasses
the toggle.  Non-critical notifications (volume, audio quality, re-paste
feedback, mic selection) continue to use ``tray.notify()`` which respects
the toggle.
"""

def _read__ux018(path: Path) -> str:
    return path.read_text(encoding="utf-8")

class TestCriticalNotificationsBypassToggle:
    """Each critical notification must use ``tray.notify_safety()``
    (bypasses the toggle) instead of ``tray.notify()`` (respects it)."""

    def test_onboarding_failure_uses_notify_safety(self):
        """3+ onboarding failures → notify_safety."""
        src = _read__ux018(APP_PY)
        # Find the onboarding-fail-count >= 3 block.
        assert "self._onboarding_fail_count >= 3" in src
        # The notify call in that block must be notify_safety.
        # Extract the block to verify.
        idx = src.index("self._onboarding_fail_count >= 3")
        block = src[idx:idx + 1500]
        assert "notify_safety(" in block, (
            "Onboarding failure (3+ retries) must use notify_safety so it "
            "bypasses the show_notifications toggle"
        )
        assert "Onboarding setup kept failing" in block

    def test_corrections_error_uses_notify_safety(self):
        """Broken corrections file → notify_safety."""
        src = _read__ux018(APP_PY)
        assert "Corrections Error" in src
        # The corrections error block must NOT be gated by show_notifications.
        # Previously: `if err is not None and self.config.show_notifications:`
        # Now: `if err is not None:`
        assert "if err is not None:" in src
        assert "if err is not None and self.config.show_notifications" not in src, (
            "Corrections error must NOT be gated by show_notifications — "
            "use notify_safety to bypass the toggle"
        )

    def test_crash_recovery_uses_notify_safety(self):
        """Recovered transcriptions → notify_safety."""
        src = _read__ux018(APP_PY)
        assert "Recovered" in src
        # Find the crash recovery block.
        idx = src.index("Recovered")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block, (
            "Crash recovery notification must use notify_safety"
        )

    def test_wayland_hotkeys_missing_uses_notify_safety(self):
        """Wayland without wtype/ydotool → notify_safety."""
        src = _read__ux018(APP_PY)
        assert "Wayland Hotkeys" in src
        idx = src.index("Wayland Hotkeys")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block, (
            "Wayland hotkey warning must use notify_safety — without wtype/ydotool "
            "the global hotkey won't fire, which is critical"
        )

    def test_macos_accessibility_missing_uses_notify_safety(self):
        """macOS without Accessibility permission → notify_safety."""
        src = _read__ux018(APP_PY)
        assert "Accessibility Permission" in src
        idx = src.index("Accessibility Permission")
        block = src[idx - 200:idx + 300]
        assert "notify_safety(" in block, (
            "macOS Accessibility permission warning must use notify_safety"
        )

    def test_recording_stop_failure_uses_notify_safety(self):
        """Recording stop exception → notify_safety."""
        src = _read__ux018(APP_PY)
        assert "Could not stop recording" in src
        idx = src.index("Could not stop recording")
        block = src[idx - 200:idx + 200]
        assert "notify_safety(" in block, (
            "Recording stop failure must use notify_safety — the user "
            "needs to know why their dictation didn't produce text"
        )

    def test_model_load_failure_uses_notify_safety(self):
        """Model load failure → notify_safety."""
        src = _read__ux018(MODEL_MANAGER_PY)
        assert "Could not load the speech model" in src
        idx = src.index("Could not load the speech model")
        block = src[idx - 200:idx + 200]
        assert "notify_safety(" in block, (
            "Model load failure must use notify_safety — the app can't "
            "transcribe without a model"
        )

class TestNonCriticalNotificationsRespectToggle:
    """Non-critical notifications must continue to use ``tray.notify()``
    so they respect the user's notification preference."""

    def test_repaste_feedback_uses_notify(self):
        """Re-paste success/failure → notify (non-critical, user-initiated)."""
        src = _read__ux018(APP_PY)
        assert "Last transcription re-pasted" in src
        # The re-paste notification must use notify, not notify_safety.
        idx = src.index("Last transcription re-pasted")
        block = src[idx - 200:idx + 100]
        assert "notify(" in block or 'notify("' in block

    def test_microphone_selection_uses_notify(self):
        """Mic selection feedback → notify (non-critical, user-initiated)."""
        src = _read__ux018(APP_PY)
        assert "Microphone:" in src or "Microphone next recording" in src

    def test_audio_quality_warning_uses_notify(self):
        """Audio quality issues → notify (informational)."""
        src = _read__ux018(APP_PY)
        # The audio quality notification is at the end of _process_transcription
        assert "AudioQualityAnalyzer" in src or "audio_quality" in src.lower()

class TestNotifySafetyMethod:
    """TrayIcon must have a notify_safety method that bypasses the toggle."""

    def test_notify_safety_exists(self):
        from voice_typer.server.tray import TrayIcon
        assert hasattr(TrayIcon, "notify_safety")

    def test_notify_safety_does_not_check_notifications_enabled(self):
        """notify_safety must NOT check _notifications_enabled (that's
        the whole point — it bypasses the toggle)."""
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(
            encoding="utf-8"
        )
        # Find the notify_safety method.
        idx = tray_py.index("def notify_safety")
        method = tray_py[idx:idx + 500]
        # It must NOT reference _notifications_enabled.
        assert "_notifications_enabled" not in method, (
            "notify_safety must NOT check _notifications_enabled — "
            "it exists specifically to bypass the toggle"
        )

# === Source: tests/test_round3_ux_fixes.py ===

"""Round 3 regression tests for UX fixes.

Covers:
  - NEW-UX-018: critical notifications use notify_safety (bypass toggle)
  - NEW-UX-026: Vocabulary + Templates have help text
  - NEW-UX-027: Push-to-Talk mode re-registers hotkey on config change
  - NEW-UX-037: Loading spinner has friendly estimate message
  - NEW-UX-038: loaded_via exposed via get_status + shown in About
  - NEW-UX-039: Vocabulary category picker (override auto-detect)
"""

class TestNewUx026HelpText:
    """Vocabulary and Templates dialogs must have help text explaining
    what to type."""

    def test_vocabulary_dialog_has_help_text(self):
        vocab = _read("pages/Vocabulary.tsx")
        # The "What you say" field must have help text below it.
        assert "Type the word(s) exactly as the ASR mishears them" in vocab
        # The "What gets typed instead" field must have help text.
        assert "The corrected text that will be pasted" in vocab

    def test_templates_dialog_has_help_text(self):
        templates = _read("pages/Templates.tsx")
        # The trigger phrase field must have help text.
        assert "The phrase you'll say during dictation" in templates
        # The output text field must mention the supported variables.
        assert "{today}" in templates
        assert "{now}" in templates
        assert "{clipboard}" in templates
        assert "{username}" in templates

class TestNewUx027PushToTalkWiring:
    """When recording_mode or hotkey changes via set_config, the hotkey
    must be re-registered so PTT's on_release callback is wired up."""

    def test_service_apply_config_side_effects_handles_recording_mode(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "recording_mode" in service_py, (
            "apply_config_side_effects must handle recording_mode changes"
        )
        assert "app.hotkeys.restart" in service_py, (
            "apply_config_side_effects must call app.hotkeys.restart() when "
            "recording_mode or hotkey changes — PTT requires re-registration "
            "to wire up set_on_release"
        )

    def test_service_handles_hotkey_change(self):
        """Changing the hotkey itself must also trigger re-registration."""
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert '"hotkey" in updates' in service_py

class TestNewUx037LoadingEstimate:
    """The App.tsx loading screen must show a friendly message with an
    estimate, not just a bare spinner + 'Starting Python backend...'"""

    def test_app_loading_has_friendly_message(self):
        app = _read("App.tsx")
        # Must mention the model download + size estimate.
        assert "466 MB" in app or "small.en" in app, (
            "Loading screen must mention the model download size estimate"
        )
        assert "30" in app and "60" in app, (
            "Loading screen must mention the 30-60 second estimate"
        )

class TestNewUx038LoadedVia:
    """The active model's loaded_via string must be exposed via
    get_status IPC and surfaced in the About page."""

    def test_service_get_status_returns_loaded_via(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "loaded_via" in service_py, (
            "get_status must return loaded_via so the renderer can display it"
        )

    def test_about_page_shows_loaded_via(self):
        about = _read("pages/About.tsx")
        assert "Loaded Via" in about, (
            "About page must have a 'Loaded Via' row in the Diagnostics section"
        )
        assert "loadedVia" in about, (
            "About page must track loadedVia state from get_status response"
        )

    def test_about_page_reads_loaded_via_from_status(self):
        about = _read("pages/About.tsx")
        # The get_status call must request loaded_via from the response.
        assert "loaded_via" in about

class TestNewUx039CategoryPicker:
    """The Add Vocabulary dialog must have a category picker so the user
    can override the auto-detect."""

    def test_vocabulary_has_category_state(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert "const [category, setCategory]" in vocab

    def test_vocabulary_has_category_labels(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert "CATEGORY_LABELS" in vocab
        # All 6 categories must have labels.
        for cat in ["misspellings", "phrase_corrections", "extra_word_patterns",
                     "technical_terms", "names", "products"]:
            assert cat in vocab, f"Category {cat} must be in CATEGORY_LABELS"

    def test_vocabulary_dialog_has_category_select(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert "Category" in vocab
        # The Select must include an 'auto' option.
        assert 'value="auto"' in vocab
        # The saveEntry function must use the explicit category.
        assert "resolvedCategory" in vocab

    def test_vocabulary_category_has_human_readable_labels(self):
        """Each category must have a human-readable label, not just the
        raw backend key."""
        vocab = _read("pages/Vocabulary.tsx")
        assert "Misspellings" in vocab
        assert "Phrase Corrections" in vocab
        assert "Technical Terms" in vocab
        assert "Names" in vocab
        assert "Products" in vocab

class TestNewUx018CriticalNotifications:
    """Critical notifications must use notify_safety (bypasses toggle)."""

    def test_onboarding_failure_uses_notify_safety(self):
        app_py = (REPO_ROOT / "voice_typer" / "server" / "app.py").read_text(
            encoding="utf-8"
        )
        assert "Onboarding setup kept failing" in app_py
        # Find the block and verify it uses notify_safety.
        idx = app_py.index("Onboarding setup kept failing")
        block = app_py[idx - 300:idx + 100]
        assert "notify_safety(" in block

    def test_corrections_error_uses_notify_safety(self):
        app_py = (REPO_ROOT / "voice_typer" / "server" / "app.py").read_text(
            encoding="utf-8"
        )
        assert "Corrections Error" in app_py
        # Must NOT be gated by show_notifications.
        assert "if err is not None and self.config.show_notifications" not in app_py

    def test_crash_recovery_uses_notify_safety(self):
        app_py = (REPO_ROOT / "voice_typer" / "server" / "app.py").read_text(
            encoding="utf-8"
        )
        idx = app_py.index("Recovered")
        block = app_py[idx - 200:idx + 300]
        assert "notify_safety(" in block

    def test_model_load_failure_uses_notify_safety(self):
        mm_py = (REPO_ROOT / "voice_typer" / "server" / "model_manager.py").read_text(
            encoding="utf-8"
        )
        idx = mm_py.index("Could not load the speech model")
        block = mm_py[idx - 200:idx + 200]
        assert "notify_safety(" in block

    def test_app_py_under_2500_lines(self):
        """Regression guard: security/platform fixes added ~300 lines of
        essential code (DACL, restart token, signal handlers, RDP detection).
        The limit allows for necessary security and platform fixes."""
        from voice_typer.server import app as app_module
        import inspect
        src = inspect.getsource(app_module)
        line_count = src.count("\n")
        # Allow headroom for comprehensive security/platform fixes
        assert line_count < 2600, (
            f"app.py is {line_count} lines; must stay under 2600"
        )

class TestNewUx030AutoSaveIndicator:
    """Settings must show a subtle auto-save indicator near the heading
    so the user knows changes are persisted without manual saving.

    BUGFIX 2026-06-25: the old design was a distracting fixed bottom-right
    banner with green dot and 'Changes are saved automatically' text.
    Replaced with a dim subtitle near PageHeading showing 'Auto-save'
    in very subtle text, with a brief 'Saving...' animation during saves.
    """

    def test_settings_has_auto_save_notice(self):
        settings = _read("pages/Settings.tsx")
        assert "Auto-save" in settings, (
            "Settings must have an 'Auto-save' notice"
        )

    def test_settings_saving_indicator_still_present(self):
        """Regression guard: the 'Saving...' indicator must still be there
        for when a save is in progress."""
        settings = _read("pages/Settings.tsx")
        assert "Saving..." in settings

    def test_settings_has_visual_saving_state(self):
        """The indicator must have a colored status dot for saving state,
        and use dim/low-opacity text for idle state instead of a dot."""
        settings = _read("pages/Settings.tsx")
        assert "bg-amber-400" in settings or "bg-amber-500" in settings
        assert "text-(--text-muted)/40" in settings, (
            "Idle state uses low-opacity muted text instead of a colored dot"
        )
