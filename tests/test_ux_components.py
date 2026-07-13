"""Tests for UI/UX components: About page, Settings, Navigation, Accessibility,
ErrorBoundary, bubble, loading screen, vocabulary/templates dialogs."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestSettingsShowsSuccessToastOnUpdateConfig:
    """Settings calls showSnack on successful set_config."""

    def test_update_config_calls_show_snack_on_success(self):
        settings = _read("pages/Settings.tsx")
        assert (
            "await call('set_config', updates)" in settings
            or 'await call("set_config", diff)' in settings
            or 'await call("set_config", updates)' in settings
        )
        lines = settings.splitlines()
        in_callback = False
        success_toast_found = False
        for line in lines:
            stripped = line.strip()
            if (
                "const updateConfig = useCallback" in stripped
                or "const updateConfigDebounced = useCallback" in stripped
                or "const flushPendingUpdates = useCallback" in stripped
            ):
                in_callback = True
            elif in_callback and (stripped.startswith("),") or stripped.startswith("}, [")):
                in_callback = False
            elif in_callback and stripped.startswith("//"):
                continue
            elif in_callback and "showSnack(" in stripped and "success" in stripped:
                success_toast_found = True
                break
        assert success_toast_found

    def test_update_config_still_has_error_toast(self):
        settings = _read("pages/Settings.tsx")
        # The error toast is a hardcoded string, not a t() key
        assert "Failed to save setting" in settings or 'showSnack("Failed' in settings


class TestOnNavigateTypedAsPageLiteralUnion:
    """onNavigate prop typed as Page (not string)."""

    def test_settings_imports_page_type(self):
        settings = _read("pages/Settings.tsx")
        assert (
            "import type { Page } from '@/types/ipc'" in settings
            or 'import type { Page } from "@/types/ipc"' in settings
        )

    def test_settings_onnavigate_typed_as_page(self):
        settings = _read("pages/Settings.tsx")
        assert "onNavigate?: (page: Page) => void" in settings

    def test_app_passes_navigate_without_type_error(self):
        app = _read("App.tsx")
        assert "onNavigate={navigate}" in app
        assert "Page" in app


class TestNumberInputOmitsOnInvalidFromProps:
    """NumberInputProps omits onInvalid from inherited HTML attributes."""

    def test_omit_includes_oninvalid(self):
        number_input = _read("components/ui/number-input.tsx")
        assert re.search(
            r'Omit<\s*React\.ComponentProps<"input">\s*,\s*"type"\s*\|\s*"onChange"\s*\|\s*"onInvalid"',
            number_input,
            re.DOTALL,
        )

    def test_custom_oninvalid_still_declared(self):
        number_input = _read("components/ui/number-input.tsx")
        assert 'onInvalid?: (reason: "parse" | "range" | null) => void' in number_input


class TestAppPreservesNavStateToLocalStorage:
    """App persists nav state to localStorage."""

    def test_app_has_nav_state_persistence(self):
        nav = _read("hooks/useNavigation.ts")
        assert "STORAGE_KEY_NAV" in nav
        assert "saveNavState" in nav
        assert "loadNavState" in nav

    def test_navigate_saves_state(self):
        nav = _read("hooks/useNavigation.ts")
        assert "saveNavState(page, navHistory.current, navIndex.current)" in nav

    def test_goBack_saves_state(self):  # noqa: N802
        nav = _read("hooks/useNavigation.ts")
        count = nav.count("saveNavState(page, navHistory.current, navIndex.current)")
        assert count >= 3

    def test_initial_state_loaded_from_localStorage(self):  # noqa: N802
        nav = _read("hooks/useNavigation.ts")
        assert "loadNavState()" in nav
        assert "initialNav" in nav


class TestAppHasHelpOverlayForShortcuts:
    """App has a ? keyboard shortcut help overlay."""

    def test_app_has_help_overlay_state(self):
        app = _read("App.tsx")
        assert "showHelpOverlay" in app
        assert "setShowHelpOverlay" in app

    def test_app_has_question_mark_keydown_handler(self):
        app = _read("App.tsx")
        assert 'e.key === "?"' in app

    def test_help_overlay_lists_shortcuts(self):
        app = _read("App.tsx")
        assert 't("help.title")' in app
        assert "Tab / Shift+Tab" in app or "Tab" in app
        assert "Space" in app
        assert "Esc" in app
        assert 't("help.openHelp")' in app
        en = _read("i18n/translations/en.json")
        assert "Keyboard Shortcuts" in en
        assert "Open this help overlay" in en

    def test_help_overlay_closes_on_escape(self):
        app = _read("App.tsx")
        assert "Escape" in app
        assert "setShowHelpOverlay(false)" in app

    def test_help_overlay_does_not_trigger_in_inputs(self):
        app = _read("App.tsx")
        assert "input" in app and "textarea" in app and "select" in app


class TestBubbleSupportsKeyboardArrowMove:
    """Bubble supports keyboard-based repositioning via arrow keys."""

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
        bubble = _read("Bubble.tsx")
        assert "if (!draggable) return" in bubble

    def test_main_has_move_by_ipc_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert "bubble:move-by" in main_ts
        assert "getDisplayMatching" in main_ts or "workArea" in main_ts

    def test_preload_exposes_move_by(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "moveBy" in preload
        assert "bubble:move-by" in preload

    def test_window_bubble_type_has_move_by(self):
        ipc_ts = _read("types/ipc.ts")
        assert "moveBy" in ipc_ts


class TestLoadingScreenShowsSizeEstimate:
    """Loading screen shows a friendly message with model download estimate."""

    def test_app_loading_has_friendly_message(self):
        app = _read("App.tsx")
        assert "466 MB" in app or "small.en" in app
        assert "30" in app and "60" in app


class TestGetStatusExposesLoadedVia:
    """get_status IPC returns loaded_via for the active model."""

    def test_service_get_status_returns_loaded_via(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "loaded_via" in service_py

    def test_about_page_shows_loaded_via(self):
        about = _read("pages/About.tsx")
        assert 't("about.loadedVia")' in about
        assert "loadedVia" in about

    def test_about_page_reads_loaded_via_from_status(self):
        about = _read("pages/About.tsx")
        assert "loaded_via" in about


class TestVocabularyAndTemplatesHaveHelpText:
    """Vocabulary and Templates dialogs have help text."""

    def test_vocabulary_dialog_has_help_text(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert 't("vocabulary.triggerHelp")' in vocab
        assert 't("vocabulary.replacementHelp")' in vocab

    def test_templates_dialog_has_help_text(self):
        templates = _read("pages/Templates.tsx")
        assert 't("templates.triggerHelp")' in templates
        assert 't("templates.outputHelp")' in templates
        assert "{today}" in templates
        assert "{now}" in templates
        assert "{clipboard}" in templates
        assert "{username}" in templates


class TestVocabularyDialogHasCategoryPicker:
    """The Add Vocabulary dialog has a category picker."""

    def test_vocabulary_has_category_state(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert "const [category, setCategory]" in vocab

    def test_vocabulary_has_category_labels(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert "CATEGORY_LABELS" in vocab
        for cat in ["misspellings", "phrase_corrections", "extra_word_patterns",
                     "technical_terms", "names", "products"]:
            assert cat in vocab

    def test_vocabulary_dialog_has_category_select(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert "Category" in vocab
        assert 'value="auto"' in vocab
        assert "resolvedCategory" in vocab

    def test_vocabulary_category_has_human_readable_labels(self):
        vocab = _read("pages/Vocabulary.tsx")
        assert 't("vocabulary.category.misspellings")' in vocab
        assert 't("vocabulary.category.phraseCorrections")' in vocab
        assert 't("vocabulary.category.technicalTerms")' in vocab
        assert 't("vocabulary.category.names")' in vocab
        assert 't("vocabulary.category.products")' in vocab


class TestSettingsShowsSubtleAutoSaveIndicator:
    """Settings shows a subtle auto-save indicator."""

    def test_settings_has_auto_save_notice(self):
        settings = _read("pages/Settings.tsx")
        assert "Auto-save" in settings

    def test_settings_saving_indicator_still_present(self):
        settings = _read("pages/Settings.tsx")
        assert "Saving..." in settings or 'setSaving(' in settings

    def test_settings_has_visual_saving_state(self):
        settings = _read("pages/Settings.tsx")
        assert "bg-amber-400" in settings or "bg-amber-500" in settings
        assert "text-(--text-muted)/40" in settings


class TestTitleBarReceivesIsMaximizedProp:
    """TitleBar accepts isMaximized prop from App."""

    def test_titlebar_accepts_isMaximized_prop(self):  # noqa: N802
        src = _read("components/TitleBar.tsx")
        assert "isMaximized?" in src

    def test_app_passes_isMaximized_to_titlebar(self):  # noqa: N802
        src = _read("App.tsx")
        assert "isMaximized={isMaximized}" in src

    def test_titlebar_skips_subscription_when_prop_provided(self):
        src = _read("components/TitleBar.tsx")
        assert "isMaximizedProp !== undefined" in src


class TestTemplatesShowVariableNamesInTooltip:
    """Templates shows variable names in tooltip."""

    def test_template_row_has_used_variables(self):
        src = _read("pages/Templates.tsx")
        assert "used_variables" in src

    def test_tooltip_shows_variable_names(self):
        src = _read("pages/Templates.tsx")
        assert '"templates.variablesTooltip"' in src


class TestAboutDiagnosticsPageExists:
    """About/Diagnostics page exists and is routed."""

    def test_about_page_exists(self):
        assert (RENDERER_SRC / "pages" / "About.tsx").exists()

    def test_about_page_exported(self):
        src = _read("pages/About.tsx")
        assert "export default" in src

    def test_sidebar_has_about_nav(self):
        src = _read("components/Sidebar.tsx")
        assert "'about'" in src or '"about"' in src

    def test_app_routes_to_about(self):
        src = _read("App.tsx")
        assert "case 'about'" in src or 'case "about"' in src
        assert "AboutPage" in src


class TestDeleteModelRouteRemovesFiles:
    """delete_model IPC route exists."""

    def test_service_has_delete_model(self):
        from voice_typer.server.service import VoiceTyperService
        assert hasattr(VoiceTyperService, "delete_model")

    def test_ipc_has_delete_model_route(self):
        from voice_typer.server.ipc_server import IPCServer
        assert "delete_model" in IPCServer._COMMAND_REGISTRY
        assert hasattr(IPCServer, "_handle_delete_model")

    def test_renderer_allowlist_has_delete_model(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert '"delete_model"' in main_ts


class TestErrorBoundaryComponentExists:
    """ErrorBoundary component exists and is wired."""

    def test_error_boundary_file_exists(self):
        assert (RENDERER_SRC / "components" / "ErrorBoundary.tsx").exists()

    def test_app_wraps_in_error_boundary(self):
        src = _read("App.tsx")
        assert "ErrorBoundary" in src
        assert "<ErrorBoundary>" in src


class TestCssHandlesPrefersReducedMotion:
    """CSS handles prefers-reduced-motion."""

    def test_css_has_reduced_motion(self):
        css = _read("index.css")
        assert "prefers-reduced-motion" in css


class TestSidebarHasAriaCurrentPage:
    """Sidebar has aria-current=page."""

    def test_sidebar_has_aria_current(self):
        src = _read("components/Sidebar.tsx")
        assert "aria-current" in src


class TestAppHasSkipToMainContentLink:
    """Skip-to-main-content link exists."""

    def test_app_has_skip_link(self):
        src = _read("App.tsx")
        assert "Skip to main content" in src
        assert "#main-content" in src


class TestCssSupportsWindowsHighContrastMode:
    """CSS supports forced-colors (high-contrast mode)."""

    def test_css_has_forced_colors(self):
        css = _read("index.css")
        assert "forced-colors" in css


class TestIndexHtmlHasLangAttribute:
    """HTML files have lang attribute."""

    def test_index_html_has_lang(self):
        html = (CLIENT_SRC / "renderer" / "index.html").read_text()
        assert 'lang="en"' in html

    def test_bubble_html_has_lang(self):
        html = (CLIENT_SRC / "renderer" / "bubble.html").read_text()
        assert 'lang="en"' in html


class TestAppAnnouncesRecordingStartStopWithAriaLive:
    """Recording start/stop announced via aria-live."""

    def test_app_has_aria_live(self):
        src = _read("App.tsx")
        assert "aria-live" in src
        assert "Recording started" in src


class TestHistorySearchHasClearButton:
    """Search field has a clear button."""

    def test_history_has_clear_button(self):
        src = _read("pages/History.tsx")
        assert "SearchField" in src
        sf = _read("components/SearchField.tsx")
        assert "Clear search" in sf or 'aria-label="Clear search"' in sf


class TestModelDownloadSupportsCancel:
    """Backend supports canceling an in-progress model download."""

    def test_service_has_cancel_model_download_method(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "def cancel_model_download" in service_py

    def test_service_has_download_cancel_event(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "_download_cancel_event" in service_py
        assert "_download_cancel_event.is_set()" in service_py
        assert '"cancelled": True' in service_py

    def test_ipc_server_has_cancel_model_download_handler(self):
        ipc_py = (REPO_ROOT / "voice_typer" / "server" / "ipc_server.py").read_text(encoding="utf-8")
        assert (
            'cmd == "cancel_model_download"' in ipc_py
            or '"cancel_model_download": "_handle_cancel_model_download"' in ipc_py
        )

    def test_main_allowlist_includes_cancel_model_download(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert '"cancel_model_download"' in main_ts

    def test_models_page_has_cancel_button(self):
        models = _read("pages/Models.tsx")
        assert "Cancel" in models
        assert "cancel_model_download" in models
