"""Consolidated regression tests for Round 2 (UX toasts, PRIV-005, PRIV-007 export).

Merges:
- tests/test_round2_fixes.py
- tests/test_round2_new_priv_005.py
- tests/test_round2_privacy_export.py
"""

# === Common imports (deduplicated from all source files) ===

from __future__ import annotations

from pathlib import Path

import pytest

from typing import Any

# === Common module-level constants (identical across files) ===

REPO_ROOT = Path(__file__).resolve().parent.parent

RENDERER_SRC = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"

CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"

# === Common helpers / fixtures (identical across files) ===

def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")

@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    return tmp_path

# === Source: tests/test_round2_fixes.py ===

"""Round 2 regression tests for the high-priority bug fixes.

Covers:
  - NEW-PRIV-005: TranscriptionEngine self.config crash (separate file
    test_round2_new_priv_005.py — 6 tests, all passing on the real
    construction path).
  - NEW-UX-035: Settings updateConfig now shows a "Saved" toast on success.
  - NEW-TS-ERR-NEW-UX-029: sound_feedback_enabled + consent fields
    exist in the TypeScript VoiceTyperConfig type.
  - NEW-PRIV-006: Models.tsx has per-provider consent toggles +
    setCloudConsent handler.
  - NEW-PRIV-009: About.tsx cites GDPR Article 9; Settings has a
    voice_biometric_consent toggle.
  - tray.py: Wayland early-return path uses _bg_thread (not _bg_work_thread).
"""

class TestNewUx035SuccessToast:
    """The Settings updateConfig callback must call showSnack on success,
    not just on error.  Previously the success path was a comment-only
    intent with no actual call."""

    def test_update_config_calls_show_snack_on_success(self):
        settings = _read("pages/Settings.tsx")
        # The success path must actually call showSnack (not just
        # describe it in a comment).
        # Find the updateConfig callback body.
        assert "await call('set_config', updates)" in settings
        # After the await, there must be a showSnack call with 'success'.
        # We look for the literal call (not the comment).
        # Strip comments to avoid matching the old comment.
        lines = settings.splitlines()
        in_update_config = False
        success_toast_found = False
        for line in lines:
            stripped = line.strip()
            if "const updateConfig = useCallback" in stripped:
                in_update_config = True
            elif in_update_config and stripped.startswith("),") or (
                in_update_config and stripped.startswith("}, [")
            ):
                in_update_config = False
            elif in_update_config and stripped.startswith("//"):
                continue  # skip comment lines
            elif in_update_config and "showSnack(" in stripped and "success" in stripped:
                success_toast_found = True
                break
        assert success_toast_found, (
            "updateConfig must call showSnack(..., 'success') after a "
            "successful set_config call.  Only the error path had a toast "
            "before this fix."
        )

    def test_update_config_still_has_error_toast(self):
        """Regression guard: the error toast must still be there."""
        settings = _read("pages/Settings.tsx")
        assert "showSnack('Failed to save setting', 'error')" in settings

class TestNewTsErrNewUx029TypeCompleteness:
    """The VoiceTyperConfig TypeScript interface must include all
    config fields referenced from the renderer.  Previously
    sound_feedback_enabled + the consent fields were missing, causing
    TS2339 errors in IDE diagnostics (even though tsc --noEmit
    silently passed due to skipLibCheck)."""

    def test_sound_feedback_enabled_in_type(self):
        config_ts = _read("types/config.ts")
        assert "sound_feedback_enabled" in config_ts, (
            "VoiceTyperConfig must declare sound_feedback_enabled"
        )

    def test_huggingface_consent_in_type(self):
        config_ts = _read("types/config.ts")
        assert "huggingface_consent" in config_ts

    def test_cloud_consent_fields_in_type(self):
        config_ts = _read("types/config.ts")
        assert "cloud_openai_consent" in config_ts
        assert "cloud_groq_consent" in config_ts
        assert "cloud_deepgram_consent" in config_ts

    def test_voice_biometric_consent_in_type(self):
        config_ts = _read("types/config.ts")
        assert "voice_biometric_consent" in config_ts

    def test_llm_polish_consent_in_type(self):
        """Pre-existing field that was missing from the TS type —
        added in this round for the centralized Privacy & Consent
        section in Settings."""
        config_ts = _read("types/config.ts")
        assert "llm_polish_consent" in config_ts

class TestNewPriv006CloudConsentUi:
    """Models.tsx must expose a consent toggle for each cloud provider
    so users can actually grant the consent the backend requires."""

    def test_models_imports_switch(self):
        models = _read("pages/Models.tsx")
        assert "import { Switch }" in models, (
            "Models.tsx must import Switch for the consent toggles"
        )

    def test_models_has_set_cloud_consent_handler(self):
        models = _read("pages/Models.tsx")
        assert "setCloudConsent" in models
        assert "cloud_openai_consent" in models
        assert "cloud_groq_consent" in models
        assert "cloud_deepgram_consent" in models

    def test_models_has_consent_key_helper(self):
        """The consentKeyFor helper exists to avoid ternary soup in JSX."""
        models = _read("pages/Models.tsx")
        assert "consentKeyFor" in models

    def test_models_has_consent_disclosure_text(self):
        """Each provider's consent section must explain what the user
        is agreeing to (audio sent to provider, provider's privacy
        policy applies)."""
        models = _read("pages/Models.tsx")
        assert "Audio transmission consent" in models
        # The "audio recordings will be sent" text spans a <strong> tag
        # in the JSX, so we check for the two halves separately.
        assert "audio recordings will be" in models
        assert "sent</strong>" in models
        assert "privacy policy applies" in models

    def test_models_has_hugging_face_consent_banner(self):
        """NEW-PRIV-005: a banner at the top of Models page explains
        HuggingFace consent and provides a grant button."""
        models = _read("pages/Models.tsx")
        assert "HuggingFace download consent required" in models
        assert "Grant consent" in models
        assert "setHuggingFaceConsent" in models

    def test_models_consent_section_only_shown_when_key_present(self):
        """The per-provider consent toggle should only render when the
        user has either entered an API key OR already granted consent
        (no point showing it otherwise)."""
        models = _read("pages/Models.tsx")
        # The conditional render uses apiKeys[provider.key] OR the consent flag
        assert "apiKeys[provider.key]" in models
        assert "consentKeyFor(provider.key)" in models

class TestNewPriv009VoiceBiometricUi:
    """About.tsx must cite GDPR Article 9 explicitly (not just 'other
    regulations').  Settings must have a centralized Privacy & Consent
    section with a voice_biometric_consent toggle."""

    def test_about_cites_gdpr_article_9(self):
        about = _read("pages/About.tsx")
        # Must cite GDPR Article 9 explicitly.
        assert "GDPR Article 9" in about, (
            "About.tsx must cite 'GDPR Article 9' (not just 'other regulations')"
        )
        # BIPA must still be there (regression guard).
        assert "BIPA" in about

    def test_settings_has_privacy_consent_section(self):
        settings = _read("pages/Settings.tsx")
        assert "Privacy & Consent" in settings
        assert "Review and revoke consent" in settings

    def test_settings_has_voice_biometric_consent_toggle(self):
        settings = _read("pages/Settings.tsx")
        assert "voice_biometric_consent" in settings
        assert "Voice biometric processing" in settings
        # The toggle's info text must cite BIPA + GDPR Art. 9.
        assert "BIPA" in settings
        assert "GDPR Article 9" in settings

    def test_settings_has_all_consent_toggles_consolidated(self):
        """The Privacy & Consent section must include all consent flags
        so the user has one place to review/revoke."""
        settings = _read("pages/Settings.tsx")
        assert "huggingface_consent" in settings
        assert "voice_biometric_consent" in settings
        assert "cloud_openai_consent" in settings
        assert "cloud_groq_consent" in settings
        assert "cloud_deepgram_consent" in settings
        assert "llm_polish_consent" in settings

class TestTrayThreadAttributeNaming:
    """MINOR FIX: Wayland early-return path must use self._bg_thread
    (canonical name), not self._bg_work_thread (which would orphan
    the thread if stop() ever joins it)."""

    def test_no_bg_work_thread_attribute(self):
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(
            encoding="utf-8"
        )
        # The buggy attribute name must NOT appear anywhere.
        assert "_bg_work_thread" not in tray_py, (
            "tray.py must not use _bg_work_thread — use _bg_thread (canonical name)"
        )

    def test_bg_thread_used_in_all_three_paths(self):
        """All three start() paths (Wayland, OSError, normal) must
        assign self._bg_thread so the attribute is consistent."""
        tray_py = (REPO_ROOT / "voice_typer" / "server" / "tray.py").read_text(
            encoding="utf-8"
        )
        # Count occurrences of "self._bg_thread = threading.Thread"
        count = tray_py.count("self._bg_thread = threading.Thread")
        assert count == 3, (
            f"Expected 3 assignments to self._bg_thread (Wayland path, "
            f"OSError path, normal path), got {count}"
        )

# === Source: tests/test_round2_new_priv_005.py ===

"""NEW-PRIV-005 regression: TranscriptionEngine consent check must NOT
crash on the real construction path.

The bug: ``TranscriptionEngine.__init__`` did not assign ``self.config``,
but ``_pre_download_model`` called ``getattr(self.config, ...)``.  The
``getattr`` default of ``False`` only protects against a missing
*huggingface_consent* attribute; it does NOT protect against ``self.config``
itself being missing.  Result: ``AttributeError`` on every uncached
Whisper download attempt — the most common production path.

Round 1 tests bypassed the bug by using ``TranscriptionEngine.__new__()``
(which skips ``__init__``) and manually setting ``engine.config =
FakeConfig()``.  These tests use the REAL ``__init__`` path so the bug
would be caught if it regressed.
"""

class TestNewPriv005RealConstructionPath:
    """Verify the consent check works when the engine is constructed
    the way production constructs it (via __init__, with config=...)."""

    def test_engine_accepts_config_kwarg(self, tmp_config_dir):
        """TranscriptionEngine.__init__ must accept a ``config`` kwarg
        and store it as ``self.config``."""
        from voice_typer.server.config import Config
        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)
        # The engine MUST have a non-None config attribute after __init__.
        assert engine.config is cfg, (
            "TranscriptionEngine.__init__ must assign self.config when "
            "the config kwarg is passed"
        )
        assert engine.config.huggingface_consent is True

    def test_engine_defaults_config_to_none(self, tmp_config_dir):
        """When no config is passed, self.config must be None (not missing).
        The consent check must handle None gracefully — no AttributeError."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en")
        # Must not raise AttributeError.  None is the safe default.
        assert engine.config is None

    def test_pre_download_does_not_crash_without_config(self, tmp_path, monkeypatch):
        """The bug: ``_pre_download_model`` crashed with AttributeError
        when self.config was missing.  Verify the fix handles None
        gracefully (treats as 'no consent', returns without downloading)."""
        # Stub huggingface_hub.snapshot_download so cache-check fails
        # (forces the consent-check path).
        import sys
        fake_module = type(sys)("huggingface_hub")

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            # If we reach here, consent was given AND we'd hit network.
            # The test asserts we NEVER reach here when consent is missing.
            raise AssertionError(
                "snapshot_download was called with local_files_only=False "
                "even though consent was not given — the consent check is broken."
            )

        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        # Construct via the REAL __init__ path (no __new__ bypass).
        # No config kwarg → self.config is None → consent defaults to
        # False → _pre_download_model returns early without crashing.
        engine = TranscriptionEngine(model_size="small.en")
        progress_messages: list[str] = []
        # Must NOT raise AttributeError.
        engine._pre_download_model(
            "small.en",
            progress_callback=progress_messages.append,
        )
        # The consent-required message should have been pushed.
        assert any("consent" in m.lower() for m in progress_messages), (
            f"Expected a consent-required progress message, got: {progress_messages}"
        )

    def test_pre_download_downloads_when_consent_given(self, tmp_path, monkeypatch):
        """When consent IS given (config passed with huggingface_consent=True),
        _pre_download_model must actually call snapshot_download with
        local_files_only=False (i.e. proceed with the download)."""
        import sys
        from voice_typer.server.config import Config

        download_calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            download_calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            # Simulate a successful download.
            return str(tmp_path / "fake_model")

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        # Verify a real (non-local-only) download was attempted.
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) >= 1, (
            f"Expected at least one non-local download call, got: {download_calls}"
        )

    def test_pre_download_refuses_download_without_consent(self, tmp_path, monkeypatch):
        """When consent is NOT given (config passed but
        huggingface_consent=False), _pre_download_model must NOT call
        snapshot_download with local_files_only=False."""
        import sys
        from voice_typer.server.config import Config

        download_calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            download_calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            raise AssertionError(
                "snapshot_download called with local_files_only=False "
                "even though huggingface_consent is False"
            )

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = False
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        # Only the local_files_only call (cache check) should have happened.
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) == 0, (
            f"Expected zero non-local download calls when consent is False, "
            f"got: {non_local_calls}"
        )

class TestNewPriv005ModelManagerWiring:
    """Verify the production code path (model_manager._ensure_engine)
    passes the live Config to TranscriptionEngine."""

    def test_ensure_engine_passes_config_to_whisper(self, tmp_config_dir, monkeypatch):
        """model_manager._ensure_engine('whisper') must pass
        config=self._app.config to the TranscriptionEngine constructor."""
        from voice_typer.server.config import Config
        from voice_typer.server.model_manager import ModelManager
        from voice_typer.server.asr_registry import AsrBackendRegistry
        from voice_typer.server.tray import AppState

        # Minimal app stub with the fields ModelManager reads.
        class FakeTray:
            state = AppState.IDLE
            def set_state(self, *args, **kwargs): pass
            def notify(self, *args, **kwargs): pass

        class FakeApp:
            def __init__(self):
                self.config = Config()
                self.config.huggingface_consent = True
                self.tray = FakeTray()
                self._ipc_server = None
                self.models = None  # set by ModelManager.__init__
                # Required by some legacy code paths
                self._cloud_engine = None
                self._llm_polisher = None
                self._template_manager = None

        app = FakeApp()
        app.models = ModelManager(app)
        # ModelManager creates its own internal AsrBackendRegistry
        # (self._registry) — use it to verify the engine was registered.
        registry = app.models._registry
        # _ensure_engine('whisper') constructs a TranscriptionEngine via
        # registry.create("whisper", whisper_kwargs=dict(..., config=...)).
        app.models._ensure_engine("whisper")
        engine = registry.get("whisper")
        assert engine is not None, "whisper engine was not registered"
        # The engine MUST have the live config reference.
        assert engine.config is app.config, (
            "model_manager._ensure_engine('whisper') did not pass "
            "config=self._app.config to TranscriptionEngine"
        )

# === Source: tests/test_round2_privacy_export.py ===

"""Round 2 regression tests for NEW-PRIV-007 and NEW-PRIV-010.

NEW-PRIV-007: GDPR right-to-export must include templates + config
(previously only history + vocabulary were exportable).

NEW-PRIV-010: Electron's userData directory must be unified with the
Python config directory so both sides read/write the same location.
"""

class TestNewPriv007RightToExport:
    """Electron must expose export handlers for templates + config."""

    def test_main_has_templates_export_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("templates:export"' in main_ts, (
            "main/index.ts must register a templates:export IPC handler"
        )

    def test_main_has_config_export_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("config:export"' in main_ts, (
            "main/index.ts must register a config:export IPC handler"
        )

    def test_preload_exposes_export_templates(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "exportTemplates" in preload, (
            "preload bridge must expose exportTemplates"
        )
        assert "exportConfig" in preload, (
            "preload bridge must expose exportConfig"
        )

    def test_window_bridge_type_includes_export_methods(self):
        ipc_ts = (CLIENT_SRC / "renderer" / "src" / "types" / "ipc.ts").read_text(
            encoding="utf-8"
        )
        assert "exportTemplates" in ipc_ts
        assert "exportConfig" in ipc_ts

    def test_settings_has_export_buttons(self):
        settings = (CLIENT_SRC / "renderer" / "src" / "pages" / "Settings.tsx").read_text(
            encoding="utf-8"
        )
        # The Privacy & Consent section must have buttons that invoke
        # the new export handlers.
        assert "Export Templates" in settings
        assert "Export Config" in settings
        assert "GDPR Art. 15/20" in settings

    def test_history_export_still_present(self):
        """Regression guard: the pre-existing history:export handler
        must still be there (we didn't accidentally remove it)."""
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("history:export"' in main_ts

    def test_vocabulary_export_still_present(self):
        """Regression guard: vocabulary:export must still be there."""
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("vocabulary:export"' in main_ts

class TestNewPriv010UnifiedDataDir:
    """Electron's userData must be set to the same path Python's
    _config_dir() returns, so both sides read/write the same location."""

    def test_main_sets_user_data_path(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'app.setPath("userData"' in main_ts, (
            "main/index.ts must call app.setPath('userData', ...) to "
            "align Electron's userData with Python's config dir"
        )

    def test_main_mirrors_python_config_dir_logic(self):
        """The path computation in main/index.ts must mirror
        voice_typer/server/config.py:_config_dir() — same env var
        override, same legacy fallback, same platform-specific paths."""
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        # Must honor the env var override.
        assert "VOICE_TYPER_CONFIG_DIR" in main_ts
        # Must check the legacy ~/.voice-typer path.
        assert ".voice-typer" in main_ts
        # Must use platform-appropriate paths.
        assert "APPDATA" in main_ts  # Windows
        assert "Application Support" in main_ts  # macOS
        assert "XDG_DATA_HOME" in main_ts  # Linux

    def test_gitignore_does_not_ignore_scripts_build(self):
        """The .gitignore must not ignore scripts/build/ (the
        ``build/`` pattern was previously unanchored and matched
        scripts/build/, hiding sync_versions.py from git)."""
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        # The anchored form /build/ must be present.
        assert "/build/" in gitignore, (
            ".gitignore must use /build/ (anchored to repo root) so it "
            "doesn't accidentally ignore scripts/build/"
        )
        # The unanchored form must NOT be present (it would match
        # scripts/build/ too).
        lines = [l.strip() for l in gitignore.splitlines()]
        for line in lines:
            if line == "build/":
                pytest.fail(
                    ".gitignore still has unanchored 'build/' pattern — "
                    "this would ignore scripts/build/"
                )

    def test_sync_versions_script_exists(self):
        """The sync_versions.py script must exist in scripts/build/
        (was previously hidden by the over-broad build/ gitignore)."""
        script = REPO_ROOT / "scripts" / "build" / "sync_versions.py"
        assert script.exists(), (
            "scripts/build/sync_versions.py must exist (NEW-DOC-019)"
        )
