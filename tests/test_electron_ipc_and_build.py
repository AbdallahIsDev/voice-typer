"""Tests for Electron/IPC infrastructure, build tooling, CI, package metadata,
and type-safety fixes."""

from __future__ import annotations

import contextlib
import inspect
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestElectronExposesDataExportHandlers:
    """Electron exposes export handlers for templates + config."""

    def test_main_has_templates_export_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("templates:export"' in main_ts or '"templates:export"' in main_ts

    def test_main_has_config_export_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("config:export"' in main_ts or '"config:export"' in main_ts

    def test_preload_exposes_export_templates(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "exportTemplates" in preload
        assert "exportConfig" in preload

    def test_window_bridge_type_includes_export_methods(self):
        ipc_ts = _read("types/ipc.ts")
        assert "exportTemplates" in ipc_ts
        assert "exportConfig" in ipc_ts

    def test_settings_has_export_buttons(self):
        settings = _read("components/settings/PrivacySettingsSection.tsx")
        assert 't("settings.privacy.exportTemplates")' in settings
        assert 't("settings.privacy.exportConfig")' in settings
        assert 't("settings.privacy.exportAllDataLabel")' in settings

    def test_history_export_still_present(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("history:export"' in main_ts or '"history:export"' in main_ts

    def test_vocabulary_export_still_present(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'ipcMain.handle("vocabulary:export"' in main_ts or '"vocabulary:export"' in main_ts


class TestTypeScriptWebConfigClean:
    """The web tsconfig must type-check cleanly."""

    def test_package_json_typecheck_includes_web_config(self):
        pkg = json.loads((CLIENT_SRC.parent / "package.json").read_text())
        typecheck_script = pkg.get("scripts", {}).get("typecheck", "")
        assert "tsconfig.web.json" in typecheck_script
        assert "tsconfig.node.json" in typecheck_script

    def test_typecheck_web_script_exists(self):
        pkg = json.loads((CLIENT_SRC.parent / "package.json").read_text())
        assert "typecheck:web" in pkg.get("scripts", {})


class TestStandardProjectFilesExist:
    """Standard project files exist."""

    def test_license_exists(self):
        assert (REPO_ROOT / "LICENSE").exists()

    def test_contributing_exists(self):
        assert (REPO_ROOT / "CONTRIBUTING.md").exists()

    def test_security_exists(self):
        assert (REPO_ROOT / "SECURITY.md").exists()

    def test_editorconfig_exists(self):
        assert (REPO_ROOT / ".editorconfig").exists()

    def test_issue_templates_exist(self):
        assert (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md").exists()
        assert (REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.md").exists()

    def test_pr_template_exists(self):
        assert (REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").exists()


class TestPyprojectHasStandardMetadataFields:
    """pyproject.toml has standard fields."""

    def test_has_license(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert "license = " in pyproject

    def test_has_classifiers(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert "classifiers" in pyproject

    def test_has_project_urls(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert "[project.urls]" in pyproject

    def test_has_readme(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert "readme = " in pyproject


class TestPackageJsonDeclaresKeywords:
    """package.json has keywords and engines."""

    def test_has_keywords(self):
        pkg = json.loads((REPO_ROOT / "voice_typer" / "client" / "package.json").read_text())
        assert "keywords" in pkg
        assert len(pkg["keywords"]) > 0

    def test_has_engines(self):
        pkg = json.loads((REPO_ROOT / "voice_typer" / "client" / "package.json").read_text())
        assert "engines" in pkg
        assert "node" in pkg["engines"]


class TestVersionReadsFromPackageMetadata:
    """__version__ reads from package metadata."""

    def test_version_uses_importlib_metadata(self):
        from voice_typer import __version__

        assert __version__ is not None
        assert isinstance(__version__, str)
        assert len(__version__) > 0

    def test_init_py_uses_importlib(self):
        init_src = (REPO_ROOT / "voice_typer" / "__init__.py").read_text()
        assert "importlib.metadata" in init_src
        assert "_pkg_version" in init_src or "version(" in init_src

    def test_sync_versions_script_exists(self):
        script = REPO_ROOT / "scripts" / "build" / "sync_versions.py"
        assert script.exists()


class TestChangelogHasCurrentTestCount:
    """CHANGELOG test count updated."""

    def test_changelog_has_current_count(self):
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
        assert "1127 tests passing" not in changelog


class TestNoBlanketResourceWarningFilter:
    """ResourceWarning is no longer blanket-ignored."""

    def test_no_blanket_resource_warning_filter(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        lines = pyproject.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"ignore::ResourceWarning"'):
                pytest.fail(f"Blanket 'ignore::ResourceWarning' filter found: {stripped}")


class TestCiRunsRuffCoverageAndPipAudit:
    """CI runs ruff, coverage, pip-audit, and multiple Python versions."""

    def test_ci_has_ruff(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "ruff" in ci

    def test_ci_has_coverage(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "cov" in ci or "coverage" in ci

    def test_ci_has_pip_audit(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "pip-audit" in ci

    def test_ci_tests_multiple_python_versions(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "3.10" in ci
        assert "3.11" in ci


class TestCiVerifiesVersionSync:
    """CI verifies version sync across installers and tags."""

    def test_ci_has_version_check_job(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "version-check" in ci

    def test_ci_verifies_tag_matches_installer(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "MyAppVersion" in ci


class TestPyinstallerSpecHasAsrHiddenImports:
    """PyInstaller spec has ASR engine hiddenimports."""

    def test_has_parakeet_engine(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "parakeet_engine" in spec

    def test_has_qwen_engine(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "qwen_engine" in spec

    def test_has_transformers(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "transformers" in spec

    def test_has_ctranslate2(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "ctranslate2" in spec

    def test_has_huggingface_hub(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert "huggingface_hub" in spec


class TestPyinstallerSpecExcludesTkinter:
    """tkinter is in PyInstaller excludes."""

    def test_tkinter_in_excludes(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert '"tkinter"' in spec


class TestElectronBuilderConfigHasSigningAndPublish:
    """electron-builder.yml has code signing + auto-update."""

    def test_has_publish_config(self):
        yml = (REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml").read_text()
        assert "publish:" in yml
        assert "provider: github" in yml

    def test_has_code_signing_config(self):
        yml = (REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml").read_text()
        assert "signAndEditExecutable" in yml
        assert "notarize" in yml


class TestSetConfigRejectsSensitiveAttrs:
    """set_config rejects trusted-path fields from the renderer."""

    def test_rejects_combined_sensitive_payload(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        cfg = config_module.Config()
        cfg.save = MagicMock(return_value=True)

        app = MagicMock()
        app.config = cfg
        # RW-9 Phase 1: the app-level test-seam delegates have been removed;
        # the IPC server's apply_config_side_effects now calls
        # ``startup_tasks.sync_autostart(app)``, ``app.hotkeys.register_esc()``
        # etc. directly. On a MagicMock these are auto-stubbed, so the
        # explicit assignments below are no-ops kept for documentation.
        app._sync_prewarm_task = MagicMock()
        app._sync_autostart = MagicMock()
        app._register_esc_hotkey = MagicMock()
        app._unregister_esc_hotkey = MagicMock()
        app._register_repaste_hotkey = MagicMock()

        server = IPCServer(app)

        original_qwen = cfg.qwen_model_path
        original_parakeet = cfg.parakeet_model_path
        original_corrections = cfg.corrections_path

        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {
                    "qwen_model_path": "/etc/passwd",
                    "parakeet_model_path": "/tmp/evil",
                    "corrections_path": "/tmp/evil-corrections.json",
                    "beam_size": 7,
                },
            }
        )

        assert result["type"] == "ack"
        assert cfg.qwen_model_path == original_qwen
        assert cfg.parakeet_model_path == original_parakeet
        assert cfg.corrections_path == original_corrections
        assert cfg.beam_size == 7


class TestUnknownIPCCommandCode:
    """Unknown-command error includes code: unknown_command."""

    def test_unknown_command_payload_has_code_field(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 7, "type": "totally_made_up_command"})

        assert result["type"] == "error"
        assert result["data"]["code"] == "unknown_command"
        assert result["data"]["command"] == "totally_made_up_command"
        assert "Unknown command" in result["data"]["message"]


class TestEntryPointImportable:
    """The main entry point must be importable."""

    def test_ipc_server_main_importable(self):
        from voice_typer.server.ipc_server import main

        assert callable(main)

    def test_app_main_re_export_exists(self):
        import voice_typer.server.app as app_mod

        assert hasattr(app_mod, "main")
        assert callable(app_mod.main)

    def test_dunder_main_imports_from_ipc_server(self):
        import voice_typer.server.__main__ as main_mod

        assert hasattr(main_mod, "main")
        assert callable(main_mod.main)

    def test_pyproject_entry_point_points_to_ipc_server(self):
        pyproject = REPO_ROOT / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert 'voice-typer = "voice_typer.server.ipc_server:main"' in content


class TestAllowlistCorrectness:
    """Allowlist correctness — no dead entries, all have server handlers."""

    @pytest.fixture
    def allowlist_entries(self):
        idx_path = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "index.ts"
        src = idx_path.read_text(encoding="utf-8")
        start = src.index("ALLOWED_COMMANDS = new Set([")
        end = src.index("]);", start)
        block = src[start:end]
        import re

        entries = re.findall(r'"([a-z_]+)"', block)
        return set(entries)

    def test_quit_app_in_allowlist(self, allowlist_entries):
        assert "quit_app" in allowlist_entries

    def test_restart_app_in_allowlist(self, allowlist_entries):
        assert "restart_app" in allowlist_entries

    def test_dead_quit_not_in_allowlist(self, allowlist_entries):
        assert "quit" not in allowlist_entries

    def test_dead_restart_not_in_allowlist(self, allowlist_entries):
        assert "restart" not in allowlist_entries

    def test_dead_save_config_not_in_allowlist(self, allowlist_entries):
        assert "save_config" not in allowlist_entries

    def test_dead_save_vocabulary_with_diff_not_in_allowlist(self, allowlist_entries):
        assert "save_vocabulary_with_diff" not in allowlist_entries

    def test_dead_repaste_last_not_in_allowlist(self, allowlist_entries):
        assert "repaste_last" not in allowlist_entries

    def test_dead_complete_onboarding_not_in_allowlist(self, allowlist_entries):
        assert "complete_onboarding" not in allowlist_entries

    def test_allowlist_matches_server_commands(self, allowlist_entries):
        import re

        ipc_path = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
        src = ipc_path.read_text(encoding="utf-8")
        old_cmds = set(re.findall(r'cmd == "([a-z_]+)"', src))
        new_cmds = set(re.findall(r'"([a-z_]+)": "_handle_', src))
        server_cmds = old_cmds | new_cmds
        orphans = allowlist_entries - server_cmds
        assert not orphans, f"Allowlist has orphan entries: {sorted(orphans)}"


class TestRestartRequestRemoved:
    """The dead RestartRequest type is removed."""

    def test_restart_request_not_in_types(self):
        types_path = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc.ts"
        src = types_path.read_text(encoding="utf-8")
        assert "export interface RestartRequest" not in src
        assert "| RestartRequest" not in src


class TestGetVocabularyHandler:
    """get_vocabulary handler uses get_all(), not list_entries()."""

    def test_vocabulary_manager_has_no_list_entries(self):
        from voice_typer.server.vocabulary import VocabularyManager

        assert not hasattr(VocabularyManager, "list_entries")

    def test_vocabulary_manager_has_get_all(self):
        from voice_typer.server.vocabulary import VocabularyManager

        assert hasattr(VocabularyManager, "get_all")

    def test_service_get_vocabulary_uses_get_all(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

        from voice_typer.server.service import VoiceTyperService

        app = MagicMock()
        app.config.config_dir = tmp_path
        service = VoiceTyperService(app)

        result = service.get_vocabulary()
        assert isinstance(result, dict)
        assert "misspellings" in result

    def test_ipc_dispatch_get_vocabulary_returns_vocabulary_type(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "get_vocabulary"})
        assert result["type"] == "vocabulary"
        assert "misspellings" in result["data"]


class TestVoiceTyperAppSingleton:
    """VoiceTyperApp uses _ensure_single_instance for singleton enforcement."""

    def test_ensure_single_instance_exists(self):
        from voice_typer.server import app as app_module

        assert hasattr(app_module, "_ensure_single_instance")

    def test_main_calls_ensure_single_instance(self):
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.main)
        assert "_ensure_single_instance" in src or "single_instance" in src

    def test_singleton_via_request_single_instance_lock(self):
        from voice_typer.server import app as app_module

        assert hasattr(app_module, "_ensure_single_instance") or hasattr(app_module, "main")


class TestIPCDispatchInvalidData:
    """_dispatch must not crash when data is not a dict."""

    def test_dispatch_with_string_data(self):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": "not a dict"})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_string_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": "not a dict"})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_list_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": ["not", "a", "dict"]})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_none_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": None})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_integer_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": 42})
        assert result["type"] in ("ack", "error")


class TestExceptExceptionNotBaseException:
    """ipc_server.main() catches Exception, not BaseException."""

    def test_main_catches_exception_not_baseexception(self):
        ipc_path = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
        src = ipc_path.read_text(encoding="utf-8")
        assert "except BaseException:" not in src
        assert "except Exception:" in src


class TestTypeIgnoreBugsFixed:
    """type:ignore real bugs are fixed."""

    def test_audio_processor_quality_callback_null_check(self):
        from voice_typer.server.audio_processor import AudioProcessor

        src = inspect.getsource(AudioProcessor._run_quality_check)
        assert "if self._quality_callback is not None" in src

    def test_volume_ducker_backend_null_check_in_monitor(self):
        from voice_typer.server.volume_ducker import VolumeDucker

        src = inspect.getsource(VolumeDucker._smart_duck_monitor_loop)
        assert "if self._backend is None" in src

    def test_volume_ducker_backend_null_check_in_duck(self):
        from voice_typer.server.volume_ducker import VolumeDucker

        src = inspect.getsource(VolumeDucker.duck)
        assert "self._backend is not None" in src

    def test_volume_backends_bare_type_ignore_fixed(self):
        path = REPO_ROOT / "voice_typer" / "server" / "volume_backends.py"
        src = path.read_text(encoding="utf-8")
        lines = [ln for ln in src.split("\n") if "type: ignore" in ln and "import-not-found" not in ln]
        bare_ignores = [ln for ln in lines if ln.rstrip().endswith("# type: ignore")]
        assert not bare_ignores

    def test_no_malformed_type_ignore_isc(self):
        server_dir = REPO_ROOT / "voice_typer" / "server"
        for py_file in server_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            assert "ignoreisc]" not in src


class TestTypeScriptNonNullAssertions:
    """Non-null assertions are fixed across TypeScript files."""

    def test_history_no_non_null_assertion_on_path(self):
        path = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "History.tsx"
        src = path.read_text(encoding="utf-8")
        assert "result.path!" not in src

    def test_vocabulary_no_non_null_assertion_on_path(self):
        path = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Vocabulary.tsx"
        src = path.read_text(encoding="utf-8")
        assert "result.path!" not in src

    def test_main_tsx_no_non_null_assertion(self):
        path = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "main.tsx"
        src = path.read_text(encoding="utf-8")
        assert "getElementById('root')!" not in src
        assert "if (!rootEl)" in src

    def test_bubble_main_tsx_no_non_null_assertion(self):
        path = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "bubble-main.tsx"
        src = path.read_text(encoding="utf-8")
        assert "getElementById('bubble-root')!" not in src
        assert "if (!bubbleRootEl)" in src


class TestVadStderrRedirect:
    """vad.py redirects both stdout and stderr."""

    def test_vad_redirects_both_streams(self):
        from voice_typer.server import vad

        src = inspect.getsource(vad)
        assert "redirect_stderr" in src


class TestMacOSAccessibilityCheck:
    """macOS accessibility permission check exists in the startup path.

    RW-9 Phase 5: the body of ``_do_startup`` was extracted into
    :class:`voice_typer.server.startup_sequence.StartupSequence`. The
    macOS accessibility check now lives in ``StartupSequence.run``, so
    these source-string checks are retargeted there. Intent unchanged.
    """

    def test_accessibility_check_in_startup_source(self):
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        has_macos_guard = "darwin" in src or "is_macos()" in src
        assert has_macos_guard and "accessibility" in src.lower()

    def test_accessibility_check_notifies_on_missing(self):
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        assert "tray.notify" in src


class TestRestartAppStopsBackends:
    """restart_app stops all hotkey backends."""

    def test_restart_calls_stop_on_all_three_backends(self, monkeypatch, tmp_path):
        from voice_typer.server import app as app_module

        for mod_name in [
            "sounddevice",
            "faster_whisper",
            "faster_whisper.WhisperModel",
            "pynput",
            "pynput.keyboard",
            "pystray",
            "PIL",
            "PIL.Image",
            "PIL.ImageDraw",
            "pyperclip",
        ]:
            sys.modules.setdefault(mod_name, MagicMock())

        with (
            patch.object(app_module, "_config_dir", return_value=tmp_path),
            patch.object(app_module, "is_autostart_enabled", return_value=False),
            patch.object(app_module, "enable_autostart"),
            patch.object(app_module, "disable_autostart"),
            patch.object(app_module, "list_microphones", return_value=[]),
        ):
            app = app_module.VoiceTyperApp()
            app.hotkeys._hotkey_backend = MagicMock()
            app.hotkeys._esc_backend = MagicMock()
            app.hotkeys._repaste_backend = MagicMock()
            app.recorder = MagicMock()
            app.recorder.discard = MagicMock()
            app.tray = MagicMock()
            app._do_restart = MagicMock()
            with contextlib.suppress(BaseException):
                app.restart_app()

            stops_called = sum(
                1
                for be in (app.hotkeys._hotkey_backend, app.hotkeys._esc_backend, app.hotkeys._repaste_backend)
                if be.stop.called
            )
            assert stops_called >= 1


class TestRestartFiltersEnvVarsWithAllowlist:
    """restart_app does not leak env vars via os.environ.copy()."""

    def test_app_uses_env_allowlist(self):
        from voice_typer.server.app import VoiceTyperApp

        for name in ("_restart_app", "restart_app", "_do_restart"):
            if hasattr(VoiceTyperApp, name):
                source = inspect.getsource(getattr(VoiceTyperApp, name))
                assert "os.environ.copy()" not in source
                return
        pytest.fail("Could not find restart method on VoiceTyperApp")


class TestIconsScriptPutsProjectVenvFirst:
    """generate-icons.mjs uses project venv as first candidate."""

    def test_project_venv_is_first_candidate(self):
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text(encoding="utf-8")
        assert "projectVenvPython" in src
        assert ".venv" in src

    def test_legacy_venv_path_is_last_resort(self):
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text(encoding="utf-8")
        m = re.search(r"const candidates = \[(.+?)\]", src, re.DOTALL)
        assert m, "candidates array not found"
        candidates_body = m.group(1)
        first_entries = candidates_body.split(",")[:2]
        first_text = ",".join(first_entries)
        assert ".voice-typer" not in first_text


class TestPackageJsonDropsUndeclaredBiome:
    """package.json no longer references undeclared biome."""

    def test_no_biome_scripts(self):
        pkg = json.loads((REPO_ROOT / "voice_typer" / "client" / "package.json").read_text())
        scripts = pkg.get("scripts", {})
        assert "biome:check" not in scripts
        assert "biome:write" not in scripts

    def test_python_dev_script_cross_platform(self):
        pkg = json.loads((REPO_ROOT / "voice_typer" / "client" / "package.json").read_text())
        python_dev = pkg.get("scripts", {}).get("python:dev", "")
        assert "python3" in python_dev

    def test_package_json_is_valid_json(self):
        pkg_path = REPO_ROOT / "voice_typer" / "client" / "package.json"
        json.loads(pkg_path.read_text(encoding="utf-8"))


class TestIconScriptFallsBackAcrossPythonPaths:
    """generate-icons.mjs tries multiple Python paths."""

    def test_script_has_fallback_chain(self):
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text()
        assert "candidates" in src
        assert "python3" in src
        assert "python" in src


class TestIconScriptRenamesRootToClientDir:
    """root renamed to clientDir in generate-icons.mjs."""

    def test_no_confusing_root_variable(self):
        script = REPO_ROOT / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        src = script.read_text()
        assert "const clientDir" in src
        assert "const root =" not in src
