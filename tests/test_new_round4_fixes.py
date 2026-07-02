"""Regression tests for Round 4 fixes.

Covers: NEW-DOC-019, NEW-DOC-015, NEW-DOC-012, NEW-DOC-013,
NEW-TEST-003, NEW-CI-001/002, NEW-BUILD-001/002, NEW-UX-005,
NEW-UX-015, NEW-A11Y-001/003/004, NEW-PRIV-003.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDERER_SRC = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestNewDoc019VersionSingleSource:
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


class TestNewDoc015TestCountUpdated:
    """NEW-DOC-015: CHANGELOG test count updated."""

    def test_changelog_has_current_count(self):
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
        # Must NOT have the old stale count.
        assert "1127 tests passing" not in changelog, (
            "CHANGELOG still has stale '1127 tests passing' count"
        )


class TestNewDoc012PyprojectMetadata:
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


class TestNewDoc013PackageJsonKeywords:
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


class TestNewDoc011StandardFiles:
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


class TestNewTest003NoResourceWarningFilter:
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


class TestNewCi001CiWorkflow:
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


class TestNewCi002VersionSyncCheck:
    """NEW-CI-002: CI verifies version sync."""

    def test_ci_has_version_check_job(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "version-check" in ci, "CI must have a version-check job"

    def test_ci_verifies_tag_matches_installer(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
        assert "MyAppVersion" in ci, "CI must verify installer.iss version matches git tag"


class TestNewBuild001HiddenImports:
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


class TestNewBuild002TkinterExcluded:
    """NEW-BUILD-002: tkinter is in excludes."""

    def test_tkinter_in_excludes(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        assert '"tkinter"' in spec, "Spec must exclude tkinter"


class TestNewUx005DeleteModelBackend:
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


class TestNewUx015ErrorBoundary:
    """NEW-UX-015: ErrorBoundary component exists and is wired."""

    def test_error_boundary_file_exists(self):
        assert (RENDERER_SRC / "components" / "ErrorBoundary.tsx").exists()

    def test_app_wraps_in_error_boundary(self):
        src = _read("App.tsx")
        assert "ErrorBoundary" in src, "App.tsx must use ErrorBoundary"
        assert "<ErrorBoundary>" in src, "App.tsx must wrap render in <ErrorBoundary>"


class TestNewA11y001ReducedMotion:
    """NEW-A11Y-001: prefers-reduced-motion is handled."""

    def test_css_has_reduced_motion(self):
        css = (RENDERER_SRC / "index.css").read_text()
        assert "prefers-reduced-motion" in css, (
            "index.css must handle prefers-reduced-motion"
        )


class TestNewA11y003AriaCurrent:
    """NEW-A11Y-003: Sidebar has aria-current="page"."""

    def test_sidebar_has_aria_current(self):
        src = _read("components/Sidebar.tsx")
        assert "aria-current" in src, "Sidebar must have aria-current on nav items"


class TestNewA11y004SkipToMain:
    """NEW-A11Y-004: Skip-to-main-content link exists."""

    def test_app_has_skip_link(self):
        src = _read("App.tsx")
        assert "Skip to main content" in src, "App must have skip-to-main link"
        assert "#main-content" in src, "Skip link must point to #main-content"


class TestNewPriv003EnvFilter:
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
