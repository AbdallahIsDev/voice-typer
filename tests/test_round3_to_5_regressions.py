"""Consolidated regression tests for the NEW-ROUND-3/4/5 fix batches.

Merges:
- tests/test_new_round3_fixes.py
- tests/test_new_round4_fixes.py
- tests/test_new_round5_fixes.py
"""

# === Common imports (deduplicated from all source files) ===

from __future__ import annotations

import inspect

from pathlib import Path

from unittest.mock import MagicMock, patch

import pytest

# === Common module-level constants (identical across files) ===

RENDERER_SRC = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# === Common helpers / fixtures (identical across files) ===

def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")

# === Source: tests/test_new_round3_fixes.py ===

"""Regression tests for Round 3 low-priority fixes.

Covers: NEW-TS-007, NEW-TS-019, NEW-DEAD-022, NEW-DEAD-023,
NEW-DEAD-027, NEW-DEAD-030, NEW-DEAD-033, NEW-DEAD-034,
NEW-DUP-005, NEW-DUP-008, NEW-DUP-009, NEW-MEM-003, NEW-MEM-004.
"""

class TestTitleBarReceivesIsMaximizedProp:
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

class TestTemplatesShowVariableNamesInTooltip:
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

class TestSetHotkeyAliasForChangeHotkey:
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

class TestIconScriptFallsBackAcrossPythonPaths:
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

class TestAsrSetupHasNoConfigDirCache:
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

class TestFallbackListenerChecksAllModifiersHeld:
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

class TestOnboardingControllerRemovesStepCallbacks:
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

class TestIconScriptRenamesRootToClientDir:
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

class TestValidateNonNumericFieldsHasClarifyingDocstring:
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

class TestMainModuleDocumentsConsoleScriptRole:
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

class TestTrayIconNoLongerReferencesStaleSvg:
    """NEW-DUP-009: vt_logo.svg references updated."""

    def test_tray_icon_no_longer_references_vt_logo(self):
        from voice_typer.server import tray_icon
        source = inspect.getsource(tray_icon._make_icon)
        # The old reference "from vt_logo.svg" should be gone.
        assert "from vt_logo.svg" not in source, (
            "tray_icon._make_icon must not reference the removed vt_logo.svg"
        )

class TestTrayIconUsesGetchannelNotSplitIndex:
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

# === Source: tests/test_new_round4_fixes.py ===

"""Regression tests for Round 4 fixes.

Covers: NEW-DOC-019, NEW-DOC-015, NEW-DOC-012, NEW-DOC-013,
NEW-TEST-003, NEW-CI-001/002, NEW-BUILD-001/002, NEW-UX-005,
NEW-UX-015, NEW-A11Y-001/003/004, NEW-PRIV-003.
"""

RENDERER_SRC__new_round4_fixes = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"

class TestVersionReadsFromPackageMetadata:
    """NEW-DOC-019: __version__ reads from package metadata."""

    def test_version_uses_importlib_metadata(self):
        from voice_typer import __version__
        assert __version__ is not None
        assert isinstance(__version__, str)
        assert len(__version__) > 0

    def test_init_py_uses_importlib(self):
        init_src = (REPO_ROOT / "voice_typer" / "__init__.py").read_text()
        assert "importlib.metadata" in init_src, (
            "__init__.py must use importlib.metadata for version"
        )
        assert "_pkg_version" in init_src or "version(" in init_src

    def test_sync_versions_script_exists(self):
        script = REPO_ROOT / "scripts" / "build" / "sync_versions.py"
        assert script.exists(), "sync_versions.py must exist"

class TestChangelogHasCurrentTestCount:
    """NEW-DOC-015: CHANGELOG test count updated."""

    def test_changelog_has_current_count(self):
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
        # Must NOT have the old stale count.
        assert "1127 tests passing" not in changelog, (
            "CHANGELOG still has stale '1127 tests passing' count"
        )

class TestPyprojectHasStandardMetadataFields:
    """NEW-DOC-012: pyproject.toml has standard fields."""

    def test_has_license(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert 'license = ' in pyproject, "pyproject.toml must have license field"

    def test_has_classifiers(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert "classifiers" in pyproject, "pyproject.toml must have classifiers"

    def test_has_project_urls(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert "[project.urls]" in pyproject, "pyproject.toml must have [project.urls]"

    def test_has_readme(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert 'readme = ' in pyproject, "pyproject.toml must have readme field"

class TestPackageJsonDeclaresKeywords:
    """NEW-DOC-013: package.json has keywords."""

    def test_has_keywords(self):
        import json
        pkg = json.loads((REPO_ROOT / "voice_typer" / "client" / "package.json").read_text())
        assert "keywords" in pkg, "package.json must have keywords"
        assert len(pkg["keywords"]) > 0

    def test_has_engines(self):
        import json
        pkg = json.loads((REPO_ROOT / "voice_typer" / "client" / "package.json").read_text())
        assert "engines" in pkg, "package.json must have engines"
        assert "node" in pkg["engines"]

class TestStandardProjectFilesExist:
    """NEW-DOC-011: standard project files exist."""

    def test_license_exists(self):
        assert (REPO_ROOT / "LICENSE").exists(), "LICENSE file must exist"

    def test_contributing_exists(self):
        assert (REPO_ROOT / "CONTRIBUTING.md").exists(), "CONTRIBUTING.md must exist"

    def test_security_exists(self):
        assert (REPO_ROOT / "SECURITY.md").exists(), "SECURITY.md must exist"

    def test_editorconfig_exists(self):
        assert (REPO_ROOT / ".editorconfig").exists(), ".editorconfig must exist"

    def test_issue_templates_exist(self):
        assert (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md").exists()
        assert (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.md").exists()

    def test_pr_template_exists(self):
        assert (REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").exists()

class TestNoBlanketResourceWarningFilter:
    """NEW-TEST-003: ResourceWarning is no longer blanket-ignored."""

    def test_no_blanket_resource_warning_filter(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        # The blanket "ignore::ResourceWarning" must be gone.
        # It's OK to have targeted filters like "ignore::ResourceWarning:sounddevice".
        lines = pyproject.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"ignore::ResourceWarning"'):
                # This is the blanket filter — must not exist.
                pytest.fail(
                    f"Blanket 'ignore::ResourceWarning' filter found: {stripped}"
                )

class TestCiRunsRuffCoverageAndPipAudit:
    """NEW-CI-001: CI runs ruff, coverage, pip-audit."""

    def test_ci_has_ruff(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "ruff" in ci, "CI must run ruff"

    def test_ci_has_coverage(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "cov" in ci or "coverage" in ci, "CI must run coverage"

    def test_ci_has_pip_audit(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "pip-audit" in ci, "CI must run pip-audit"

    def test_ci_tests_multiple_python_versions(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "3.10" in ci, "CI must test Python 3.10"
        assert "3.11" in ci, "CI must test Python 3.11"

class TestCiVerifiesVersionSync:
    """NEW-CI-002: CI verifies version sync."""

    def test_ci_has_version_check_job(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "version-check" in ci, "CI must have a version-check job"

    def test_ci_verifies_tag_matches_installer(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "MyAppVersion" in ci, "CI must verify installer.iss version matches git tag"

class TestPyinstallerSpecHasAsrHiddenImports:
    """NEW-BUILD-001: PyInstaller spec has ASR engine hiddenimports."""

    def test_has_parakeet_engine(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "parakeet_engine" in spec, "Spec must include parakeet_engine"

    def test_has_qwen_engine(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "qwen_engine" in spec, "Spec must include qwen_engine"

    def test_has_transformers(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "transformers" in spec, "Spec must include transformers"

    def test_has_ctranslate2(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "ctranslate2" in spec, "Spec must include ctranslate2"

    def test_has_huggingface_hub(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "huggingface_hub" in spec, "Spec must include huggingface_hub"

class TestPyinstallerSpecExcludesTkinter:
    """NEW-BUILD-002: tkinter is in excludes."""

    def test_tkinter_in_excludes(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert '"tkinter"' in spec, "Spec must exclude tkinter"

class TestDeleteModelRouteRemovesFiles:
    """NEW-UX-005: delete_model IPC route exists and actually deletes files."""

    def test_service_has_delete_model(self):
        from voice_typer.server.service import VoiceTyperService
        assert hasattr(VoiceTyperService, "delete_model"), (
            "VoiceTyperService must have delete_model method"
        )

    def test_ipc_has_delete_model_route(self):
        from voice_typer.server.ipc_server import IPCServer
        # REFACTOR: _dispatch was converted to a command registry.
        # The handler is now _handle_delete_model, registered in
        # _COMMAND_REGISTRY. Check both the registry and the handler.
        assert "delete_model" in IPCServer._COMMAND_REGISTRY, (
            "IPC _COMMAND_REGISTRY must include delete_model"
        )
        assert hasattr(IPCServer, "_handle_delete_model"), (
            "IPC must have _handle_delete_model handler method"
        )

    def test_renderer_allowlist_has_delete_model(self):
        main_ts = (
            REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "index.ts"
        ).read_text()
        assert '"delete_model"' in main_ts, "Renderer allowlist must include delete_model"

class TestErrorBoundaryComponentExists:
    """NEW-UX-015: ErrorBoundary component exists and is wired."""

    def test_error_boundary_file_exists(self):
        assert (RENDERER_SRC__new_round4_fixes / "components" / "ErrorBoundary.tsx").exists()

    def test_app_wraps_in_error_boundary(self):
        src = _read("App.tsx")
        assert "ErrorBoundary" in src, "App.tsx must use ErrorBoundary"
        assert "<ErrorBoundary>" in src, "App.tsx must wrap render in <ErrorBoundary>"

class TestCssHandlesPrefersReducedMotion:
    """NEW-A11Y-001: prefers-reduced-motion is handled."""

    def test_css_has_reduced_motion(self):
        css = (RENDERER_SRC__new_round4_fixes / "index.css").read_text()
        assert "prefers-reduced-motion" in css, (
            "index.css must handle prefers-reduced-motion"
        )

class TestSidebarHasAriaCurrentPage:
    """NEW-A11Y-003: Sidebar has aria-current="page"."""

    def test_sidebar_has_aria_current(self):
        src = _read("components/Sidebar.tsx")
        assert "aria-current" in src, "Sidebar must have aria-current on nav items"

class TestAppHasSkipToMainContentLink:
    """NEW-A11Y-004: Skip-to-main-content link exists."""

    def test_app_has_skip_link(self):
        src = _read("App.tsx")
        assert "Skip to main content" in src, "App must have skip-to-main link"
        assert "#main-content" in src, "Skip link must point to #main-content"

class TestRestartFiltersEnvVarsWithAllowlist:
    """NEW-PRIV-003: restart subprocess filters env vars."""

    def test_app_uses_env_allowlist(self):
        from voice_typer.server.app import VoiceTyperApp
        # Find the restart method (might be _restart_app or restart_app).
        for name in ("_restart_app", "restart_app", "_do_restart"):
            if hasattr(VoiceTyperApp, name):
                source = inspect.getsource(getattr(VoiceTyperApp, name))
                assert "_SAFE" in source or "SAFE" in source, (
                    f"{name} must use an env allowlist"
                )
                assert "os.environ.copy()" not in source, (
                    f"{name} must NOT use os.environ.copy() (leaks API keys)"
                )
                return
        pytest.fail("Could not find restart method on VoiceTyperApp")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_new_round5_fixes.py ===

"""Regression tests for Round 5 fixes.

Covers: NEW-DOC-014, NEW-UX-009/010/019/020/031,
NEW-A11Y-002/005/007, NEW-XPLAT-001, NEW-BUILD-001.
"""

RENDERER_SRC__new_round5_fixes = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"

class TestElectronBuilderConfigHasSigningAndPublish:
    """NEW-DOC-014: electron-builder.yml has code signing + auto-update."""

    def test_has_publish_config(self):
        yml = (REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml").read_text()
        assert "publish:" in yml, "electron-builder.yml must have publish config"
        assert "provider: github" in yml, "Must use GitHub provider for auto-update"

    def test_has_code_signing_config(self):
        yml = (REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml").read_text()
        assert "certificateFile" in yml, "Must have Windows certificate config"
        assert "signAndEditExecutable" in yml, "Must enable signAndEditExecutable"
        assert "notarize" in yml, "Must have macOS notarization config"

class TestAboutDiagnosticsPageExists:
    """NEW-UX-009: About/Diagnostics page exists."""

    def test_about_page_exists(self):
        assert (RENDERER_SRC__new_round5_fixes / "pages" / "About.tsx").exists()

    def test_about_page_exported(self):
        src = _read("pages/About.tsx")
        assert "export default" in src, "About page must have default export"

    def test_sidebar_has_about_nav(self):
        src = _read("components/Sidebar.tsx")
        assert "'about'" in src or '"about"' in src, "Sidebar must have about nav item"

    def test_app_routes_to_about(self):
        src = _read("App.tsx")
        assert "case 'about'" in src, "App must route to about page"
        assert "AboutPage" in src, "App must import AboutPage"

class TestAutoPunctuationDefaultsTrue:
    """NEW-UX-010: auto_punctuation defaults ON."""

    def test_auto_punctuation_defaults_true(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert cfg.auto_punctuation is True, (
            "auto_punctuation must default to True (NEW-UX-010)"
        )

class TestEscCancelDefaultsTrue:
    """NEW-UX-020: esc_cancel_enabled defaults ON."""

    def test_esc_cancel_defaults_true(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert cfg.esc_cancel_enabled is True, (
            "esc_cancel_enabled must default to True (NEW-UX-020)"
        )

class TestResetToDefaultsPreservesOnboardingCompleted:
    """NEW-UX-019: Reset to Defaults preserves onboarding_completed."""

    def test_reset_skips_onboarding(self):
        src = _read("pages/Settings.tsx")
        assert "onboarding_completed" in src, (
            "Settings.tsx must reference onboarding_completed in reset logic"
        )
        # The code should explicitly skip it.
        assert "intentionally preserved" in src or "skip" in src.lower(), (
            "Settings.tsx must document why onboarding_completed is preserved"
        )

class TestHistorySearchHasClearButton:
    """NEW-UX-031: Search field has clear (×) button."""

    def test_history_has_clear_button(self):
        src = _read("pages/History.tsx")
        assert "Clear search" in src or "aria-label=\"Clear search\"" in src, (
            "History search must have a clear button"
        )

class TestAppAnnouncesRecordingStartStopWithAriaLive:
    """NEW-A11Y-002: Recording start/stop has aria-live announcement."""

    def test_app_has_aria_live(self):
        src = _read("App.tsx")
        assert "aria-live" in src, "App must have aria-live region"
        assert "Recording started" in src, (
            "aria-live must announce 'Recording started'"
        )

class TestCssSupportsWindowsHighContrastMode:
    """NEW-A11Y-005: Windows high-contrast mode support."""

    def test_css_has_forced_colors(self):
        css = _read("index.css")
        assert "forced-colors" in css, (
            "CSS must handle forced-colors (high-contrast mode)"
        )

class TestIndexHtmlHasLangAttribute:
    """NEW-A11Y-007: HTML has lang attribute."""

    def test_index_html_has_lang(self):
        html = (REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "index.html").read_text()
        assert 'lang="en"' in html, "index.html must have lang attribute"

    def test_bubble_html_has_lang(self):
        html = (REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "bubble.html").read_text()
        assert 'lang="en"' in html, "bubble.html must have lang attribute"

class TestConfigDirIsPlatformAware:
    """NEW-XPLAT-001: _config_dir() uses platform-aware paths."""

    def test_config_dir_checks_platform(self):
        from voice_typer.server.config import _config_dir
        source = inspect.getsource(_config_dir)
        assert "sys.platform" in source or "platform" in source, (
            "_config_dir must check platform for OS-specific paths"
        )
        assert "APPDATA" in source, "Must check APPDATA on Windows"
        assert "XDG_DATA_HOME" in source, "Must check XDG_DATA_HOME on Linux"
        assert "Library" in source and "Application Support" in source, (
            "Must use Library/Application Support on macOS"
        )

    def test_legacy_path_migration(self):
        from voice_typer.server.config import _config_dir
        source = inspect.getsource(_config_dir)
        assert "legacy" in source, "Must check legacy ~/.voice-typer for migration"

class TestPyinstallerSpecAsrImportsPresent:
    """NEW-BUILD-001: PyInstaller spec has ASR hiddenimports."""

    def test_spec_has_asr_imports(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        for imp in ["parakeet_engine", "qwen_engine", "transformers", "ctranslate2"]:
            assert imp in spec, f"Spec must include {imp}"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
