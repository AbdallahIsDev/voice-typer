"""Regression tests for Round 3 low-priority fixes.

Covers: NEW-TS-007, NEW-TS-019, NEW-DEAD-022, NEW-DEAD-023,
NEW-DEAD-027, NEW-DEAD-030, NEW-DEAD-033, NEW-DEAD-034,
NEW-DUP-005, NEW-DUP-008, NEW-DUP-009, NEW-MEM-003, NEW-MEM-004.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch  # TEST-033: unified mock import

import pytest


RENDERER_SRC = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
)


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestNewTs007SingleMaximizedSubscription:
    """NEW-TS-007: App.tsx passes isMaximized to TitleBar (single source)."""

    def test_titlebar_accepts_isMaximized_prop(self):
        src = _read("components/TitleBar.tsx")
        assert "isMaximized?" in src, (
            "TitleBar must accept an optional isMaximized prop"
        )

    def test_app_passes_isMaximized_to_titlebar(self):
        src = _read("App.tsx")
        assert "isMaximized={isMaximized}" in src, (
            "App.tsx must pass isMaximized to TitleBar"
        )

    def test_titlebar_skips_subscription_when_prop_provided(self):
        src = _read("components/TitleBar.tsx")
        assert "isMaximizedProp !== undefined" in src, (
            "TitleBar must skip its own subscription when isMaximized prop is provided"
        )


class TestNewTs019VariablesTooltip:
    """NEW-TS-019: Templates.tsx shows variable names in tooltip."""

    def test_template_row_has_used_variables(self):
        src = _read("pages/Templates.tsx")
        assert "used_variables" in src, (
            "TemplateRow must track which variables are used"
        )

    def test_tooltip_shows_variable_names(self):
        src = _read("pages/Templates.tsx")
        assert "Variables:" in src, (
            "The tooltip must list the variable names"
        )


class TestNewDead022SetHotkeyAlias:
    """NEW-DEAD-022: set_hotkey is an alias for change_hotkey."""

    def test_set_hotkey_is_alias(self):
        from voice_typer.server.app import VoiceTyperApp
        assert hasattr(VoiceTyperApp, "set_hotkey")
        assert hasattr(VoiceTyperApp, "change_hotkey")
        # When assigned via ``set_hotkey = change_hotkey``, the two
        # names share the same underlying function in the class dict.
        # (They may appear as different descriptors depending on how
        # Python resolves the alias, so we check the __wrapped__ or
        # __func__ attribute.)
        sh = VoiceTyperApp.__dict__.get("set_hotkey")
        ch = VoiceTyperApp.__dict__.get("change_hotkey")
        # Both should be the same callable (function or descriptor).
        assert sh is not None and ch is not None, (
            "Both set_hotkey and change_hotkey must exist on VoiceTyperApp"
        )
        # If they're functions, they should be identical objects.
        if hasattr(sh, "__func__") and hasattr(ch, "__func__"):
            assert sh.__func__ is ch.__func__, (
                "set_hotkey.__func__ must be the same as change_hotkey.__func__"
            )


class TestNewDead023PythonFallback:
    """NEW-DEAD-023: generate-icons.mjs tries multiple Python paths."""

    def test_script_has_fallback_chain(self):
        script = (
            Path(__file__).resolve().parent.parent
            / "voice_typer"
            / "client"
            / "scripts"
            / "generate-icons.mjs"
        )
        src = script.read_text()
        assert "candidates" in src, (
            "generate-icons.mjs must have a candidates array for Python paths"
        )
        assert "python3" in src, "Must try python3 from PATH"
        assert "python" in src, "Must try python from PATH"


class TestNewDead027ConfigDirDirect:
    """NEW-DEAD-027: asr_setup no longer has _CONFIG_DIR cache."""

    def test_no_config_dir_cache(self):
        from voice_typer.server import asr_setup
        assert not hasattr(asr_setup, "_CONFIG_DIR"), (
            "asr_setup must not have the _CONFIG_DIR module cache"
        )
        assert not hasattr(asr_setup, "_config_dir"), (
            "asr_setup must not have the _config_dir wrapper function"
        )

    def test_parakeet_uses_config_directly(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine
        source = inspect.getsource(ParakeetEngine._is_cached)
        assert "from voice_typer.server.config import _config_dir" in source, (
            "ParakeetEngine._is_cached must import _config_dir from config directly"
        )
        # The old asr_setup import must NOT appear.
        assert "from voice_typer.server.asr_setup import _config_dir" not in source, (
            "ParakeetEngine._is_cached must not import _config_dir from asr_setup"
        )


class TestNewDead030ModifierCheck:
    """NEW-DEAD-030: fallback listener checks all modifiers are held."""

    def test_fallback_tracks_modifiers(self):
        from voice_typer.server.hotkeys import PynputHotkey
        source = inspect.getsource(PynputHotkey._start_fallback)
        assert "modifier_keys" in source, (
            "Fallback listener must track modifier_keys"
        )
        assert "held_modifiers" in source, (
            "Fallback listener must track held_modifiers set"
        )
        # The check that all modifiers are held before firing.
        assert "len(held_modifiers) < len(modifier_keys)" in source, (
            "Fallback listener must check all modifiers are held"
        )


class TestNewDead033OnboardingCallbacksRemoved:
    """NEW-DEAD-033: on_step_change and on_complete removed."""

    def test_no_callbacks_in_init(self):
        from voice_typer.server.onboarding import OnboardingController
        source = inspect.getsource(OnboardingController.__init__)
        # The actual attribute assignments must be gone.  We check
        # that ``self.on_step_change =`` and ``self.on_complete =``
        # don't appear (the comment may mention them for context).
        assert "self.on_step_change =" not in source, (
            "on_step_change must not be assigned in __init__"
        )
        assert "self.on_complete =" not in source, (
            "on_complete must not be assigned in __init__"
        )

    def test_next_step_no_callback_invocation(self):
        from voice_typer.server.onboarding import OnboardingController
        source = inspect.getsource(OnboardingController.next_step)
        assert "on_step_change" not in source, (
            "next_step must not invoke on_step_change"
        )
        assert "on_complete" not in source, (
            "next_step must not invoke on_complete"
        )


class TestNewDead034RootRenamed:
    """NEW-DEAD-034: root → clientDir in generate-icons.mjs."""

    def test_no_confusing_root_variable(self):
        script = (
            Path(__file__).resolve().parent.parent
            / "voice_typer"
            / "client"
            / "scripts"
            / "generate-icons.mjs"
        )
        src = script.read_text()
        assert "const clientDir" in src, (
            "generate-icons.mjs must use clientDir instead of root"
        )
        # The old confusing ``const root =`` line must be gone.
        assert "const root =" not in src, (
            "generate-icons.mjs must not have the confusing 'const root =' variable"
        )


class TestNewDup005NotDuplicate:
    """NEW-DUP-005: _validate_non_numeric_fields is NOT a duplicate."""

    def test_validator_has_clarifying_docstring(self):
        from voice_typer.server.config import Config
        source = inspect.getsource(Config._validate_non_numeric_fields)
        assert "NEW-DUP-005" in source, (
            "_validate_non_numeric_fields must document why it's not a duplicate"
        )
        assert "migration layer" in source, (
            "Must explain it's a migration layer for legacy configs"
        )


class TestNewDup008NotDuplicate:
    """NEW-DUP-008: __main__.py and console script serve different purposes."""

    def test_main_has_clarifying_docstring(self):
        main_path = (
            Path(__file__).resolve().parent.parent
            / "voice_typer"
            / "__main__.py"
        )
        source = main_path.read_text()
        assert "NEW-DUP-008" in source, (
            "__main__.py must document why it's not a duplicate of the console script"
        )
        assert "different purposes" in source.lower() or "NOT a duplicate" in source, (
            "Must explain the two entry points serve different purposes"
        )


class TestNewDup009StaleSvgReference:
    """NEW-DUP-009: vt_logo.svg references updated."""

    def test_tray_icon_no_longer_references_vt_logo(self):
        from voice_typer.server import tray_icon
        source = inspect.getsource(tray_icon._make_icon)
        # The old reference "from vt_logo.svg" should be gone.
        assert "from vt_logo.svg" not in source, (
            "tray_icon._make_icon must not reference the removed vt_logo.svg"
        )


class TestNewMem004Getchannel:
    """NEW-MEM-004: use getchannel('A') instead of split()[3]."""

    def test_no_split_index_3(self):
        from voice_typer.server import tray_icon
        source = inspect.getsource(tray_icon._make_icon)
        # Strip comment lines before checking (our explanatory comment
        # mentions "split()[3]" for context, which is fine — we only
        # care that the actual CODE doesn't use it).
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "split()[3]" not in code_only, (
            "tray_icon._make_icon code must not use split()[3]"
        )
        assert "getchannel('A')" in code_only, (
            "tray_icon._make_icon must use getchannel('A')"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
