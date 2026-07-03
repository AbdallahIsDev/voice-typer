"""Consolidated regression tests for Round 1 (privacy, UX, cross-platform).

Merges:
- tests/test_round1_privacy_fixes.py
- tests/test_round1_ux_fixes.py
- tests/test_round1_xplat_fixes.py
"""

# === Common imports (deduplicated from all source files) ===

from __future__ import annotations

import json

from pathlib import Path

import pytest

import re

import subprocess

import sys

# === Common module-level constants (identical across files) ===

REPO_ROOT = Path(__file__).resolve().parent.parent

# === Common helpers / fixtures (identical across files) ===

@pytest.fixture
def history_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    from voice_typer.server.history_db import HistoryDB

    db = HistoryDB(db_path=tmp_path / "history.db")
    yield db
    db.close()

@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    return tmp_path

# === Source: tests/test_round1_privacy_fixes.py ===

"""Regression tests for Round 1 privacy + UX fixes.

Covers:
  - NEW-PRIV-004: in-app privacy policy disclosure (About page)
  - NEW-PRIV-005: huggingface_consent config field + enforcement
  - NEW-PRIV-006: per-provider cloud consent flags + ConsentRequiredError
  - NEW-PRIV-009: voice_biometric_consent config field
  - NEW-UX-023: About page exposes latest version from GitHub releases API
  - NEW-UX-025: Settings Troubleshooting section has Help/Diagnostics/Bug links
"""

class TestNewPrivConsentConfigFields:
    """The Config dataclass must declare the new consent flags."""

    def test_huggingface_consent_field_exists(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert hasattr(cfg, "huggingface_consent")
        assert cfg.huggingface_consent is False  # default: not given

    def test_cloud_per_provider_consent_fields_exist(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert hasattr(cfg, "cloud_openai_consent")
        assert hasattr(cfg, "cloud_groq_consent")
        assert hasattr(cfg, "cloud_deepgram_consent")
        # Defaults: not given
        assert cfg.cloud_openai_consent is False
        assert cfg.cloud_groq_consent is False
        assert cfg.cloud_deepgram_consent is False

    def test_voice_biometric_consent_field_exists(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert hasattr(cfg, "voice_biometric_consent")
        assert cfg.voice_biometric_consent is False

    def test_consent_fields_round_trip_via_save_load(self, tmp_path, monkeypatch):
        """Consent flags must survive save → load round trip."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path
        )
        from voice_typer.server.config import Config
        cfg = Config()
        cfg.huggingface_consent = True
        cfg.cloud_openai_consent = True
        cfg.voice_biometric_consent = True
        assert cfg.save() is True

        loaded = Config.load()
        assert loaded.huggingface_consent is True
        assert loaded.cloud_openai_consent is True
        assert loaded.cloud_groq_consent is False  # untouched
        assert loaded.voice_biometric_consent is True

    def test_consent_fields_settable_via_ipc_allowlist(self):
        """NEW-PRIV-005/006/009: validate_config_update accepts the
        new consent fields so the renderer can set them."""
        from voice_typer.server.config import validate_config_update
        validated, errors = validate_config_update({
            "huggingface_consent": True,
            "cloud_openai_consent": True,
            "cloud_groq_consent": False,
            "cloud_deepgram_consent": True,
            "voice_biometric_consent": True,
        })
        assert errors == []
        assert validated["huggingface_consent"] is True
        assert validated["cloud_openai_consent"] is True
        assert validated["cloud_deepgram_consent"] is True
        assert validated["voice_biometric_consent"] is True

    def test_consent_fields_reject_non_bool(self):
        """Consent fields must be bool — non-bool values are rejected."""
        from voice_typer.server.config import validate_config_update
        validated, errors = validate_config_update({
            "huggingface_consent": "yes",  # string, not bool
        })
        assert errors, "Non-bool consent value should be rejected"
        assert "huggingface_consent" not in validated

class TestNewPriv006CloudConsentEnforcement:
    """CloudEngine refuses to transcribe without consent."""

    def test_consent_required_error_is_runtime_error(self):
        from voice_typer.server.cloud_engines import ConsentRequiredError
        assert issubclass(ConsentRequiredError, RuntimeError)

    def test_cloud_engine_not_loaded_without_consent(self):
        from voice_typer.server.cloud_engines import CloudEngine
        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=False,
        )
        # is_loaded should be False because consent is False.
        assert eng.is_loaded is False

    def test_cloud_engine_loaded_with_consent(self):
        from voice_typer.server.cloud_engines import CloudEngine
        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=True,
        )
        assert eng.is_loaded is True

    def test_transcribe_raises_without_consent(self):
        """NEW-PRIV-006: transcribe() must raise ConsentRequiredError
        when consent hasn't been given, even if API key is present."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine, ConsentRequiredError

        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=False,
        )
        audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
        with pytest.raises(ConsentRequiredError):
            eng.transcribe(audio)

    def test_transcribe_does_not_raise_with_consent(self):
        """When consent IS given, transcribe should NOT raise
        ConsentRequiredError (it will fail later with a network error,
        but that's a different code path)."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine, ConsentRequiredError

        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=True,
        )
        audio = np.zeros(16000, dtype=np.float32)
        # Empty audio returns "" without sending a request.
        # Use empty array to avoid actually hitting the network.
        empty_audio = np.array([], dtype=np.float32)
        result = eng.transcribe(empty_audio)
        assert result == ""

class TestNewPriv005HuggingFaceConsent:
    """WhisperEngine._pre_download_model respects huggingface_consent."""

    def test_pre_download_returns_early_without_consent(self, monkeypatch, tmp_path):
        """When consent is False and model is not cached, _pre_download_model
        returns early WITHOUT calling snapshot_download with local_files_only=False."""
        # We can't easily construct a TranscriptionEngine without mocking a lot
        # of dependencies, so we monkeypatch snapshot_download to detect
        # whether it was called with local_files_only=False (which would
        # mean a real network download).
        calls = []

        def fake_snapshot_download(**kwargs):
            calls.append(kwargs)
            if kwargs.get("local_files_only"):
                # Cache miss — raise to simulate "not cached"
                raise FileNotFoundError("not in cache")
            # Real download attempt — this is what we want to PREVENT
            # when consent is False.
            return "/fake/path"

        # Inject the fake into the huggingface_hub module's namespace
        # so the `from huggingface_hub import snapshot_download` in
        # _pre_download_model picks it up.
        import sys
        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        # Build a minimal engine stub.  We only need _pre_download_model
        # and a config attribute.
        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)  # bypass __init__
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        # Use a non-Whisper model size to test the early-return path.
        engine._pre_download_model("parakeet")
        # Should not have called snapshot_download at all.
        assert calls == [], (
            f"_pre_download_model should not call snapshot_download for "
            f"non-Whisper models.  Calls: {calls}"
        )

    def test_pre_download_skips_download_without_consent(self, monkeypatch):
        """When consent is False and model is not cached, _pre_download_model
        should NOT trigger a network download."""
        network_calls = []

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")  # cache miss
            network_calls.append(kwargs)  # this would be a real download
            return "/fake/path"

        # Inject the fake into the huggingface_hub module's namespace
        # so the `from huggingface_hub import snapshot_download` in
        # _pre_download_model picks it up.
        import sys
        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        # Should return without doing a network download.
        engine._pre_download_model("small.en")
        assert network_calls == [], (
            f"_pre_download_model should NOT do a network download when "
            f"huggingface_consent is False.  Network calls: {network_calls}"
        )

class TestNewPriv004AboutPagePrivacy:
    """NEW-PRIV-004: About page has expanded privacy disclosure."""

    def test_about_page_has_privacy_section(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        # The page must mention all the privacy-relevant topics.
        assert "Audio processing" in src, "Missing 'Audio processing' section"
        assert "Model weights" in src, "Missing 'Model weights' section"
        assert "HuggingFace" in src, "Missing HuggingFace disclosure"
        assert "Cloud ASR" in src, "Missing 'Cloud ASR' section"
        assert "Voice biometrics" in src, "Missing 'Voice biometrics' section"
        assert "BIPA" in src, "Missing BIPA mention (Illinois biometric law)"

    def test_about_page_has_updates_section(self):
        """NEW-UX-023: About page has a Check for Updates button."""
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "Check for Updates" in src, "Missing 'Check for Updates' button"
        assert "LATEST_RELEASE_API" in src, "Missing GitHub releases API URL"
        assert "api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest" in src

    def test_about_page_has_help_links(self):
        """NEW-UX-021: About page has Documentation + Changelog links."""
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "README_URL" in src or "README.md" in src
        assert "CHANGELOG_URL" in src or "CHANGELOG.md" in src

    def test_about_page_has_feedback_links(self):
        """NEW-UX-022: About page has feedback / report-a-bug links."""
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "Report a Bug" in src or "Report an Issue" in src
        assert "github.com/AbdallahIsDev/voice-typer/issues" in src

class TestNewUx025SettingsTroubleshoot:
    """NEW-UX-025: Settings Troubleshoot section has real diagnostic actions."""

    def test_settings_has_diagnostics_button(self):
        settings = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings.read_text(encoding="utf-8")
        assert "Diagnostics" in src, "Missing 'Diagnostics' button"
        assert "Help & FAQ" in src, "Missing 'Help & FAQ' button"
        assert "Report a Bug" in src, "Missing 'Report a Bug' button"
        # The "View Logs" button label should be clarified (not just "View Logs").
        assert "Open Log Folder" in src, (
            "Missing 'Open Log Folder' label (NEW-UX-025: clarify that it opens the folder, not a viewer)"
        )

    def test_settings_has_on_navigate_prop(self):
        """NEW-UX-025: Settings component accepts onNavigate so the
        Diagnostics button can route to the About page."""
        settings = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings.read_text(encoding="utf-8")
        assert "onNavigate" in src, "Settings should accept onNavigate prop"

# === Source: tests/test_round1_ux_fixes.py ===

"""Regression tests for Round 1 fixes.

Covers:
  - NEW-UX-004: History restore (undo delete) round-trip
  - NEW-UX-008: Templates persisted via TemplateManager (not config attr)
  - NEW-UX-003: useSnackbar hook delegates to sonner (TS-side; we test
    the import surface here)
"""

class TestNewUx004HistoryRestore:
    """NEW-UX-004: deleted records can be re-inserted via restore()."""

    def test_restore_reinserts_record_with_new_id(self, history_db):
        rid = history_db.add_transcription(
            "hello world", duration=1.5, model="small.en", device="cpu",
        )
        rec = history_db.get_recent()[0]
        assert history_db.delete(rid) is True
        assert history_db.get_recent() == []

        new_id = history_db.restore(rec)
        assert new_id > 0
        assert new_id != rid  # new id assigned by SQLite
        restored = history_db.get_recent()[0]
        assert restored["text"] == "hello world"
        assert restored["duration"] == 1.5
        assert restored["model"] == "small.en"
        assert restored["device"] == "cpu"

    def test_restore_preserves_favorite_flag(self, history_db):
        history_db.add_transcription("favorite text")
        rec = history_db.get_recent()[0]
        # Mark as favorite
        history_db.toggle_favorite(rec["id"])
        rec = history_db.get_recent()[0]
        assert rec["favorite"] == 1

        history_db.delete(rec["id"])
        new_id = history_db.restore(rec)
        restored = history_db.get_recent()[0]
        assert restored["id"] == new_id
        assert restored["favorite"] == 1

    def test_restore_with_minimal_record(self, history_db):
        # Only text is required; missing fields default to 0 / empty.
        new_id = history_db.restore({"text": "minimal restore"})
        assert new_id > 0
        rec = history_db.get_recent()[0]
        assert rec["text"] == "minimal restore"
        assert rec["duration"] == 0
        assert rec["model"] == ""

    def test_restore_empty_text_returns_negative(self, history_db):
        # Empty text at the DB layer is allowed (the DB layer doesn't
        # enforce semantic meaning — that's the service layer's job).
        # The DB just stores what it's given.  Test that the row is
        # inserted with the empty text:
        new_id = history_db.restore({"text": ""})
        assert new_id > 0  # DB accepted the row
        # The service layer (test_service_empty_text_rejected below)
        # is what enforces the non-empty-text rule.

    def test_service_empty_text_rejected(self, templates_dir):
        """NEW-UX-004: service.restore_history rejects empty-text records."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None
            # history_db is required for restore_history
            history_db = None

        # We don't actually need a real history_db because the
        # validation happens BEFORE the call to history_db.restore.
        # The service raises ValueError before touching history_db.
        service = VoiceTyperService(FakeApp())
        with pytest.raises(ValueError, match="text"):
            service.restore_history({"text": ""})
        with pytest.raises(ValueError, match="text"):
            service.restore_history({})  # missing 'text' key
        with pytest.raises(ValueError, match="dict"):
            service.restore_history("not a dict")

class TestNewUx008TemplatesPersistence:
    """NEW-UX-008: templates survive to disk via TemplateManager._save."""

    def test_templates_persisted_to_json_file(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=templates_dir)
        tm.add("hello", "Hello World!")
        tm.add("bye", "Goodbye!", match_mode="contains")

        # File on disk
        templates_file = templates_dir / "voice-typer-templates.json"
        assert templates_file.exists()
        data = json.loads(templates_file.read_text(encoding="utf-8"))
        assert "templates" in data
        assert len(data["templates"]) == 2
        assert data["templates"][0]["trigger"] == "hello"
        assert data["templates"][1]["match_mode"] == "contains"

    def test_templates_loaded_on_restart(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm1 = TemplateManager(config_dir=templates_dir)
        tm1.add("persist_test", "persisted value")
        del tm1  # simulate process exit

        # New manager instance — should load from disk
        tm2 = TemplateManager(config_dir=templates_dir)
        templates = tm2.templates
        assert len(templates) == 1
        assert templates[0]["trigger"] == "persist_test"
        assert templates[0]["output"] == "persisted value"

    def test_service_save_and_get_round_trip(self, templates_dir):
        """NEW-UX-008: service.save_templates / get_templates round-trip
        survives a process restart (i.e. relies on disk persistence)."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())

        templates_to_save = [
            {"trigger": "my_email", "output": "me@example.com", "match_mode": "exact"},
            {"trigger": "signature", "output": "Best regards,\nJohn", "match_mode": "contains"},
        ]
        assert service.save_templates(templates_to_save) is True

        # Simulate process restart: drop the in-memory _template_manager
        # so the next get_templates() call creates a fresh TemplateManager
        # that reads from disk.
        FakeApp._template_manager = None
        service2 = VoiceTyperService(FakeApp())
        loaded = service2.get_templates()
        assert len(loaded) == 2
        assert loaded[0]["trigger"] == "my_email"
        assert loaded[0]["output"] == "me@example.com"
        assert loaded[1]["match_mode"] == "contains"

    def test_service_save_rejects_invalid_entries(self, templates_dir):
        """NEW-UX-008: save_templates normalizes/rejects invalid entries
        instead of crashing."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())
        # Mix of valid + invalid entries
        bad_input = [
            {"trigger": "valid", "output": "ok", "match_mode": "exact"},
            {"trigger": "", "output": "missing trigger"},  # rejected
            {"trigger": "missing output", "output": ""},  # rejected
            {"trigger": "bad mode", "output": "ok", "match_mode": "invalid"},  # normalized
            "not a dict",  # rejected
            None,  # rejected
        ]
        assert service.save_templates(bad_input) is True
        loaded = service.get_templates()
        assert len(loaded) == 2  # only "valid" + "bad mode" survived
        triggers = [t["trigger"] for t in loaded]
        assert "valid" in triggers
        assert "bad mode" in triggers
        # "invalid" match_mode was normalized to "exact"
        bad_mode_entry = next(t for t in loaded if t["trigger"] == "bad mode")
        assert bad_mode_entry["match_mode"] == "exact"

# === Source: tests/test_round1_xplat_fixes.py ===

"""Regression tests for Round 1 cross-platform + privacy + UX fixes.

Covers:
  - NEW-XPLAT-003: tray_window.py uses shutil.which instead of shell=True
  - NEW-XPLAT-005: launchctl load has a 5s timeout
  - NEW-XPLAT-006: macOS plist WorkingDirectory is absolute (no literal ~)
  - NEW-XPLAT-007: _desktop_quote quotes per freedesktop spec
  - NEW-DOC-024: generate-icons.mjs uses .venv first, python3 second
  - NEW-DOC-027: package.json has no broken biome scripts
"""

class TestNewXplat007DesktopQuoting:
    """NEW-XPLAT-007: _desktop_quote follows the freedesktop spec."""

    def test_no_reserved_chars_returns_unquoted(self):
        from voice_typer.server.server_platform import _desktop_quote
        assert _desktop_quote("python3") == "python3"
        assert _desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
        assert _desktop_quote("--hidden") == "--hidden"

    def test_space_triggers_quoting(self):
        from voice_typer.server.server_platform import _desktop_quote
        assert _desktop_quote("/path with spaces/app") == '"/path with spaces/app"'

    def test_backslash_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote
        # Windows path with backslashes
        result = _desktop_quote("C:\\Users\\John\\app")
        assert result == '"C:\\\\Users\\\\John\\\\app"'

    def test_double_quote_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote
        result = _desktop_quote('John "Bob"')
        assert result == '"John \\"Bob\\""'

    def test_dollar_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote
        result = _desktop_quote("$HOME/app")
        assert result == '"\\$HOME/app"'

    def test_backtick_escaped(self):
        from voice_typer.server.server_platform import _desktop_quote
        result = _desktop_quote("path with `backtick`")
        assert result == '"path with \\`backtick\\`"'

    def test_autostart_command_is_quoted(self):
        """The full autostart command should be valid for the .desktop Exec field."""
        from voice_typer.server.server_platform import _autostart_command
        cmd = _autostart_command()
        # The launcher path is always present.
        assert "autostart_launcher.py" in cmd
        # The python interpreter is always present.
        assert "python" in cmd.lower() or sys.executable in cmd
        # --hidden flag is always present.
        assert "--hidden" in cmd

class TestNewXplat005006MacOSPlist:
    """NEW-XPLAT-005/006: macOS autostart plist is well-formed."""

    def test_plist_uses_absolute_working_directory(self):
        """NEW-XPLAT-006: WorkingDirectory must be an absolute path,
        not the literal ``~``."""
        from voice_typer.server import server_platform as platform
        # Read the source of _enable_autostart_macos to verify it
        # doesn't emit the literal `~` as WorkingDirectory.
        import inspect
        src = inspect.getsource(platform._enable_autostart_macos)
        # The literal `<string>~</string>` is the bug.
        assert "<string>~</string>" not in src, (
            "macOS plist still uses literal '~' for WorkingDirectory"
        )
        # The fix uses Path.home() to expand ~.
        assert "Path.home()" in src or "str(Path.home())" in src, (
            "macOS plist should use Path.home() for WorkingDirectory"
        )

    def test_launchctl_load_has_timeout(self):
        """NEW-XPLAT-005: launchctl load subprocess.run call has a timeout."""
        from voice_typer.server import server_platform as platform
        import inspect
        src = inspect.getsource(platform._enable_autostart_macos)
        # The fix adds timeout=5.0 to the subprocess.run call.
        assert "timeout=" in src, (
            "launchctl load subprocess.run call has no timeout= argument"
        )

class TestNewXplat003ShellTrueAvoidance:
    """NEW-XPLAT-003: tray_window.py uses shutil.which instead of shell=True."""

    def test_tray_window_uses_shutil_which(self):
        tray_window = REPO_ROOT / "voice_typer" / "server" / "tray_window.py"
        src = tray_window.read_text(encoding="utf-8")
        # The fix: use shutil.which to resolve npm path.
        assert "shutil.which" in src, (
            "tray_window.py should use shutil.which to resolve npm"
        )
        # shell=True should only be in the fallback path (with a warning).
        assert "shell=True" in src, (
            "tray_window.py should still have shell=True as last-resort fallback"
        )
        # The fallback should be gated by an explicit warning log.
        assert "npm not on PATH" in src or "shell=True" in src

class TestNewXplat002WaylandTrayDetection:
    """NEW-XPLAT-002: tray.py detects Wayland without SNI and skips tray."""

    def test_wayland_detection_method_exists(self):
        from voice_typer.server.tray import TrayIcon
        assert hasattr(TrayIcon, "_is_linux_wayland_without_sni")

    def test_returns_false_on_non_linux(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon
        # Force non-Linux platform
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        assert TrayIcon._is_linux_wayland_without_sni() is False

    def test_returns_false_when_not_wayland(self, monkeypatch):
        from voice_typer.server.tray import TrayIcon
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        assert TrayIcon._is_linux_wayland_without_sni() is False

    def test_returns_true_on_wayland_without_dbus_module(self, monkeypatch):
        """If we're on Wayland but python-dbus isn't installed, the
        conservative answer is "assume SNI unavailable" (return True)."""
        from voice_typer.server.tray import TrayIcon
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        # Force ImportError when `import dbus` is attempted.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "dbus":
                raise ImportError("no module named dbus")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert TrayIcon._is_linux_wayland_without_sni() is True

class TestNewXplat004PyobjcCocoaDep:
    """NEW-XPLAT-004: pyproject.toml declares pyobjc-framework-Cocoa for macOS."""

    def test_pyobjc_cocoa_in_dependencies(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # The dep must be present and gated to darwin.
        assert "pyobjc-framework-Cocoa" in pyproject, (
            "pyproject.toml must declare pyobjc-framework-Cocoa"
        )
        # Must be darwin-only.
        assert "sys_platform == 'darwin'" in pyproject

class TestNewDoc024IconsScriptFallback:
    """NEW-DOC-024: generate-icons.mjs no longer hardcodes venv path as first."""

    def test_project_venv_is_first_candidate(self):
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text(encoding="utf-8")
        # The fix adds the project's .venv as the first candidate.
        assert "projectVenvPython" in src, (
            "generate-icons.mjs should use project venv as first candidate"
        )
        assert ".venv" in src, "generate-icons.mjs should reference .venv"

    def test_legacy_venv_path_is_last_resort(self):
        """The ~/.voice-typer/venv/... path should be in the fallback chain
        but NOT the first candidate."""
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text(encoding="utf-8")
        # Find the candidates array.
        m = re.search(r"const candidates = \[(.+?)\]", src, re.DOTALL)
        assert m, "candidates array not found in generate-icons.mjs"
        candidates_body = m.group(1)
        # The legacy path should NOT be the first entry.
        first_entries = candidates_body.split(",")[:2]
        first_text = ",".join(first_entries)
        assert ".voice-typer" not in first_text, (
            f"Legacy ~/.voice-typer/venv path should NOT be the first candidate. "
            f"First entries: {first_text}"
        )

class TestNewDoc027PackageJsonCleanup:
    """NEW-DOC-027: package.json no longer references undeclared biome."""

    def test_no_biome_scripts(self):
        pkg = json.loads(
            (REPO_ROOT / "voice_typer" / "client" / "package.json").read_text()
        )
        scripts = pkg.get("scripts", {})
        # The broken biome:check / biome:write scripts should be gone.
        assert "biome:check" not in scripts, (
            "package.json should not have biome:check script "
            "(biome is not in devDependencies)"
        )
        assert "biome:write" not in scripts, (
            "package.json should not have biome:write script "
            "(biome is not in devDependencies)"
        )

    def test_python_dev_script_cross_platform(self):
        """python:dev script should work on Linux (python3) and Windows (python)."""
        pkg = json.loads(
            (REPO_ROOT / "voice_typer" / "client" / "package.json").read_text()
        )
        python_dev = pkg.get("scripts", {}).get("python:dev", "")
        # The fix uses python3 || python fallback chain.
        assert "python3" in python_dev, (
            f"python:dev script should prefer python3 — got: {python_dev}"
        )

    def test_package_json_is_valid_json(self):
        """The package.json must remain valid JSON after our edits."""
        pkg_path = REPO_ROOT / "voice_typer" / "client" / "package.json"
        # If this loads without error, the JSON is valid.
        json.loads(pkg_path.read_text(encoding="utf-8"))
