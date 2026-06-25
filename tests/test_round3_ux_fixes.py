"""Round 3 regression tests for UX fixes.

Covers:
  - NEW-UX-018: critical notifications use notify_safety (bypass toggle)
  - NEW-UX-026: Vocabulary + Templates have help text
  - NEW-UX-027: Push-to-Talk mode re-registers hotkey on config change
  - NEW-UX-037: Loading spinner has friendly estimate message
  - NEW-UX-038: loaded_via exposed via get_status + shown in About
  - NEW-UX-039: Vocabulary category picker (override auto-detect)
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


# ── NEW-UX-026: help text on Vocabulary + Templates ──────────────────


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


# ── NEW-UX-027: Push-to-Talk re-registers hotkey on config change ────


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


# ── NEW-UX-037: loading spinner has estimate ─────────────────────────


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


# ── NEW-UX-038: loaded_via exposed + surfaced ────────────────────────


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


# ── NEW-UX-039: vocabulary category picker ───────────────────────────


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


# ── NEW-UX-018: critical notifications bypass toggle ─────────────────


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

    def test_app_py_under_2000_lines(self):
        """Regression guard: the notify_safety changes must not push
        app.py over the 2000-line limit (test_round9_e2e enforces this)."""
        from voice_typer.server import app as app_module
        import inspect
        src = inspect.getsource(app_module)
        line_count = src.count("\n")
        assert line_count < 2000, (
            f"app.py is {line_count} lines; must stay under 2000"
        )


# ── NEW-UX-030: Settings auto-save indicator ─────────────────────────


class TestNewUx030AutoSaveIndicator:
    """Settings must show a persistent auto-save indicator so the user
    knows there's no manual save step."""

    def test_settings_has_auto_save_notice(self):
        settings = _read("pages/Settings.tsx")
        assert "Changes are saved automatically" in settings, (
            "Settings must have a persistent 'Changes are saved automatically' notice"
        )

    def test_settings_saving_indicator_still_present(self):
        """Regression guard: the 'Saving…' indicator must still be there
        for when a save is in progress."""
        settings = _read("pages/Settings.tsx")
        assert "Saving…" in settings or "Saving..." in settings

    def test_settings_has_visual_status_dot(self):
        """The indicator must have a colored status dot (amber for saving,
        emerald for idle) so the user can see the state at a glance."""
        settings = _read("pages/Settings.tsx")
        assert "bg-amber-400" in settings or "bg-amber-500" in settings
        assert "bg-emerald-500" in settings or "bg-emerald-400" in settings
