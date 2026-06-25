"""Round 2 regression tests for NEW-PRIV-007 and NEW-PRIV-010.

NEW-PRIV-007: GDPR right-to-export must include templates + config
(previously only history + vocabulary were exportable).

NEW-PRIV-010: Electron's userData directory must be unified with the
Python config directory so both sides read/write the same location.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"


# ── NEW-PRIV-007: GDPR right-to-export ────────────────────────────────


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


# ── NEW-PRIV-010: unified data directory ─────────────────────────────


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


import pytest  # noqa: E402 — imported at bottom so the test class
              # definitions above don't fail on pytest.fail() lookup
              # if the import order changes.
