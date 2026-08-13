"""§10.1 — tests for the GitHub Releases publisher (``publish_pack_release.py``).

Covers the CI-side publisher that uploads the slim-core installer + pack
onefile + ``pack-manifest.json`` as GitHub Release assets.

Two backends are tested:
  * ``gh`` CLI backend — mocks ``subprocess.run`` to avoid spawning ``gh``.
  * GitHub REST API backend — mocks ``urllib.request.urlopen`` to avoid
    real HTTP.

The tests verify:
  * Asset validation (missing / empty / directory assets rejected).
  * ``gh`` command construction (``gh release create`` + ``gh release upload``).
  * Idempotency (re-running with the same tag uploads missing assets +
    clobbers existing ones).
  * Backend auto-selection (``gh`` when available, ``api`` when not).
  * API backend token handling (``GH_TOKEN`` / ``GITHUB_TOKEN`` env vars).
  * Asset-name templates (C-CI-13 — the new artifact-naming convention).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure ``scripts/release`` is importable.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from release import publish_pack_release as pub  # type: ignore[import-not-found]

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_assets(tmp_path: Path) -> dict[str, Path]:
    """Create fake asset files for testing."""
    files: dict[str, Path] = {}
    for name, content in [
        ("VoiceTyper-Setup-1.2.3.exe", b"fake-nsis-installer"),
        ("pack-1.2.3.zip", b"fake-pack-zip"),
        ("pack-manifest.json", json.dumps({
            "version": "1.2.3",
            "sha256": "a" * 64,
            "files": [{"name": "worker.exe", "sha256": "b" * 64, "size": 1024}],
            "min_proto_version": 1,
        }).encode("utf-8")),
    ]:
        p = tmp_path / name
        p.write_bytes(content)
        files[name] = p
    return files


@pytest.fixture
def fake_runner_success():
    """A fake subprocess runner that simulates ``gh`` success."""
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        # Distinguish create vs upload vs view.
        if "view" in cmd:
            # ``gh release view --json url --jq .url`` returns the URL
            # as a plain string (``--jq`` extracts the field). Without
            # ``--jq``, ``--json`` returns the full JSON object.
            if "--jq" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="https://github.com/owner/repo/releases/tag/v1.2.3", stderr=""
                )
            if "--json" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"url": "https://github.com/owner/repo/releases/tag/v1.2.3"}), stderr=""
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="release exists", stderr="")
        if "create" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="created", stderr="")
        if "upload" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="uploaded", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return runner, calls


@pytest.fixture
def fake_runner_release_exists():
    """A fake runner where the release already exists (``gh release view`` succeeds)."""
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        if "view" in cmd and "--jq" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://github.com/owner/repo/releases/tag/v1.2.3", stderr=""
            )
        if "view" in cmd and "--json" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"url": "https://github.com/owner/repo/releases/tag/v1.2.3"}), stderr=""
            )
        if "view" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="release exists", stderr="")
        if "upload" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="uploaded", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unexpected command")

    return runner, calls


# ── Asset validation ───────────────────────────────────────────────────


class TestValidateAssets:
    """``validate_assets`` — rejects missing / empty / directory assets."""

    def test_valid_assets_pass(self, fake_assets: dict[str, Path]):
        errors = pub.validate_assets(list(fake_assets.values()))
        assert errors == []

    def test_missing_asset_rejected(self, tmp_path: Path):
        errors = pub.validate_assets([tmp_path / "nonexistent.exe"])
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_empty_asset_rejected(self, tmp_path: Path):
        empty = tmp_path / "empty.zip"
        empty.write_bytes(b"")
        errors = pub.validate_assets([empty])
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_directory_asset_rejected(self, tmp_path: Path):
        errors = pub.validate_assets([tmp_path])
        assert len(errors) == 1
        assert "directory" in errors[0]

    def test_multiple_errors_returned(self, tmp_path: Path):
        missing = tmp_path / "missing.exe"
        empty = tmp_path / "empty.zip"
        empty.write_bytes(b"")
        errors = pub.validate_assets([missing, empty, tmp_path])
        assert len(errors) == 3


# ── gh CLI command construction ────────────────────────────────────────


class TestGhCommandConstruction:
    """``build_gh_create_command`` + ``build_gh_upload_command``."""

    def test_create_command_includes_tag_and_repo(self):
        cmd = pub.build_gh_create_command(
            "v1.2.3",
            repo="owner/repo",
            notes="release notes",
            notes_file=None,
            draft=False,
            prerelease=False,
            target=None,
            title=None,
        )
        assert cmd[0] == "gh"
        assert cmd[1] == "release"
        assert cmd[2] == "create"
        assert "v1.2.3" in cmd
        assert "--repo" in cmd
        assert "owner/repo" in cmd
        assert "--notes" in cmd
        assert "release notes" in cmd

    def test_create_command_draft_and_prerelease_flags(self):
        cmd = pub.build_gh_create_command(
            "v1.2.3",
            repo="owner/repo",
            notes=None,
            notes_file=None,
            draft=True,
            prerelease=True,
            target=None,
            title=None,
        )
        assert "--draft" in cmd
        assert "--prerelease" in cmd
        # No notes → empty string notes (prevents GitHub auto-generating).
        assert "--notes" in cmd
        assert "" in cmd

    def test_create_command_notes_file_overrides_notes(self):
        notes_file = Path("/tmp/notes.md")
        cmd = pub.build_gh_create_command(
            "v1.2.3",
            repo="owner/repo",
            notes="ignored",
            notes_file=notes_file,
            draft=False,
            prerelease=False,
            target=None,
            title=None,
        )
        assert "--notes-file" in cmd
        assert str(notes_file) in cmd
        # ``--notes`` should NOT be present when ``--notes-file`` is used.
        assert "--notes " not in " ".join(cmd)  # avoid matching ``--notes-file``

    def test_create_command_target_commitish(self):
        cmd = pub.build_gh_create_command(
            "v1.2.3",
            repo="owner/repo",
            notes=None,
            notes_file=None,
            draft=False,
            prerelease=False,
            target="main",
            title=None,
        )
        assert "--target" in cmd
        assert "main" in cmd

    def test_upload_command_includes_assets_and_clobber(self, fake_assets: dict[str, Path]):
        assets = list(fake_assets.values())
        cmd = pub.build_gh_upload_command("v1.2.3", assets, repo="owner/repo")
        assert cmd[0] == "gh"
        assert "upload" in cmd
        assert "v1.2.3" in cmd
        assert "--repo" in cmd
        assert "owner/repo" in cmd
        assert "--clobber" in cmd
        # Every asset path is in the command.
        for asset in assets:
            assert str(asset) in cmd

    def test_upload_command_clobber_can_be_disabled(self, fake_assets: dict[str, Path]):
        assets = list(fake_assets.values())
        cmd = pub.build_gh_upload_command("v1.2.3", assets, repo="owner/repo", clobber=False)
        assert "--clobber" not in cmd


# ── gh release existence check ─────────────────────────────────────────


class TestGhReleaseExists:
    """``gh_release_exists`` + ``gh_release_url``."""

    def test_exists_returns_true_on_exit_zero(self):
        runner = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        assert pub.gh_release_exists("v1.2.3", repo="owner/repo", runner=runner) is True

    def test_exists_returns_false_on_nonzero(self):
        runner = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
        assert pub.gh_release_exists("v1.2.3", repo="owner/repo", runner=runner) is False

    def test_release_url_returns_url_on_success(self):
        """``gh_release_url`` uses ``--jq '.url'`` so ``gh`` returns the URL
        as a plain string (not JSON). The fake runner simulates that."""
        runner = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/owner/repo/releases/tag/v1.2.3", stderr=""
        )
        url = pub.gh_release_url("v1.2.3", repo="owner/repo", runner=runner)
        assert url == "https://github.com/owner/repo/releases/tag/v1.2.3"

    def test_release_url_returns_none_on_failure(self):
        runner = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
        url = pub.gh_release_url("v1.2.3", repo="owner/repo", runner=runner)
        assert url is None

    def test_release_url_returns_none_on_empty_stdout(self):
        runner = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        url = pub.gh_release_url("v1.2.3", repo="owner/repo", runner=runner)
        assert url is None


# ── publish_release (gh backend) ───────────────────────────────────────


class TestPublishReleaseGhBackend:
    """``publish_release`` with the ``gh`` backend."""

    def test_successful_publish_uploads_all_assets(
        self,
        fake_assets: dict[str, Path],
        fake_runner_success,
    ):
        runner, calls = fake_runner_success
        assets = list(fake_assets.values())
        result = pub.publish_release(
            tag="v1.2.3",
            assets=assets,
            repo="owner/repo",
            backend="gh",
            runner=runner,
        )
        assert result.success is True
        assert result.backend == "gh"
        assert set(result.uploaded) == set(fake_assets.keys())
        assert result.release_url == "https://github.com/owner/repo/releases/tag/v1.2.3"

    def test_idempotent_rerun_skips_create(
        self,
        fake_assets: dict[str, Path],
        fake_runner_release_exists,
    ):
        """When the release already exists, ``gh release create`` is NOT called."""
        runner, calls = fake_runner_release_exists
        assets = list(fake_assets.values())
        result = pub.publish_release(
            tag="v1.2.3",
            assets=assets,
            repo="owner/repo",
            backend="gh",
            runner=runner,
        )
        assert result.success is True
        # Verify no ``create`` command was issued.
        create_cmds = [c for c in calls if "create" in c]
        assert create_cmds == [], (
            f"gh release create should NOT be called when release exists; got {create_cmds}"
        )
        # Verify the upload command WAS issued.
        upload_cmds = [c for c in calls if "upload" in c]
        assert len(upload_cmds) == 1

    def test_create_failure_returns_error(
        self,
        fake_assets: dict[str, Path],
    ):
        """When ``gh release create`` fails, the result has ``success=False``."""
        def runner(cmd, **kw):
            if "view" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
            if "create" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="create failed")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unexpected")

        result = pub.publish_release(
            tag="v1.2.3",
            assets=list(fake_assets.values()),
            repo="owner/repo",
            backend="gh",
            runner=runner,
        )
        assert result.success is False
        assert any("create failed" in e for e in result.errors)

    def test_upload_failure_returns_error(
        self,
        fake_assets: dict[str, Path],
    ):
        """When ``gh release upload`` fails, the result has ``success=False``."""
        def runner(cmd, **kw):
            if "view" in cmd and "--jq" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="https://github.com/owner/repo/releases/tag/v1.2.3", stderr=""
                )
            if "view" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "create" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "upload" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="upload failed")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unexpected")

        result = pub.publish_release(
            tag="v1.2.3",
            assets=list(fake_assets.values()),
            repo="owner/repo",
            backend="gh",
            runner=runner,
        )
        assert result.success is False
        assert any("upload failed" in e for e in result.errors)


# ── publish_release (API backend) ──────────────────────────────────────


class TestPublishReleaseApiBackend:
    """``publish_release`` with the GitHub REST API backend."""

    def test_missing_token_returns_error(self, fake_assets: dict[str, Path], monkeypatch):
        """Without a token, the API backend returns a clear error."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = pub.publish_release(
            tag="v1.2.3",
            assets=list(fake_assets.values()),
            repo="owner/repo",
            backend="api",
            token=None,
        )
        assert result.success is False
        assert any("token" in e.lower() for e in result.errors)

    def test_token_from_env_var(self, fake_assets: dict[str, Path], monkeypatch):
        """``GH_TOKEN`` env var is used when ``token=None``."""
        monkeypatch.setenv("GH_TOKEN", "fake-token-123")
        # Mock the API request to return success.
        with patch.object(pub, "_api_request"), \
             patch.object(pub, "api_create_release") as mock_create, \
             patch.object(pub, "api_upload_asset") as mock_upload:
            mock_create.return_value = (
                "https://github.com/owner/repo/releases/tag/v1.2.3",
                "https://uploads.github.com/repos/owner/repo/releases/123/assets",
            )
            mock_upload.return_value = (True, None)
            result = pub.publish_release(
                tag="v1.2.3",
                assets=list(fake_assets.values()),
                repo="owner/repo",
                backend="api",
            )
        assert result.success is True
        assert result.backend == "api"
        assert result.release_url == "https://github.com/owner/repo/releases/tag/v1.2.3"

    def test_upload_failure_recorded(self, fake_assets: dict[str, Path], monkeypatch):
        """A per-asset upload failure is recorded in ``result.errors``."""
        monkeypatch.setenv("GH_TOKEN", "fake-token-123")
        with patch.object(pub, "api_create_release") as mock_create, \
             patch.object(pub, "api_upload_asset") as mock_upload:
            mock_create.return_value = (
                "https://github.com/owner/repo/releases/tag/v1.2.3",
                "https://uploads.github.com/repos/owner/repo/releases/123/assets",
            )
            # First asset succeeds, second fails.
            mock_upload.side_effect = [(True, None), (False, "simulated upload failure")]
            result = pub.publish_release(
                tag="v1.2.3",
                assets=list(fake_assets.values())[:2],
                repo="owner/repo",
                backend="api",
            )
        assert result.success is False
        assert any("simulated upload failure" in e for e in result.errors)
        assert len(result.uploaded) == 1


# ── Backend auto-selection ─────────────────────────────────────────────


class TestBackendAutoSelection:
    """When ``backend=None``, the backend is auto-selected."""

    def test_gh_selected_when_gh_available(self, fake_assets: dict[str, Path], monkeypatch):
        """When ``shutil.which('gh')`` finds the binary, ``gh`` is selected."""
        monkeypatch.setattr(pub.shutil, "which", lambda name: "/usr/bin/gh")
        # Patch the gh backend to avoid actually running ``gh``.
        with patch.object(pub, "_publish_via_gh") as mock_gh:
            mock_gh.return_value = pub.PublishResult(
                success=True, tag="v1.2.3", backend="gh"
            )
            result = pub.publish_release(
                tag="v1.2.3",
                assets=list(fake_assets.values()),
                repo="owner/repo",
                backend=None,
            )
        assert result.backend == "gh"
        mock_gh.assert_called_once()

    def test_api_selected_when_gh_unavailable(self, fake_assets: dict[str, Path], monkeypatch):
        """When ``shutil.which('gh')`` returns None, ``api`` is selected."""
        monkeypatch.setattr(pub.shutil, "which", lambda name: None)
        monkeypatch.setenv("GH_TOKEN", "fake-token-123")
        with patch.object(pub, "_publish_via_api") as mock_api:
            mock_api.return_value = pub.PublishResult(
                success=True, tag="v1.2.3", backend="api"
            )
            result = pub.publish_release(
                tag="v1.2.3",
                assets=list(fake_assets.values()),
                repo="owner/repo",
                backend=None,
            )
        assert result.backend == "api"
        mock_api.assert_called_once()


# ── Asset-name templates (C-CI-13) ─────────────────────────────────────


class TestAssetNameTemplates:
    """The asset-name templates follow the C-CI-13 convention.

    The publisher does NOT enforce these names — it uploads whatever
    paths the caller passes. The templates are documented constants so
    CI workflows + the docs can reference them consistently.
    """

    def test_pack_onefile_template(self):
        assert pub.ASSET_NAME_TEMPLATES["pack_onefile"] == "pack-{version}.zip"

    def test_pack_manifest_template_is_not_versioned(self):
        """The manifest is NOT versioned — ``releases/latest/download/pack-manifest.json``
        serves the latest release's manifest."""
        assert pub.ASSET_NAME_TEMPLATES["pack_manifest"] == "pack-manifest.json"

    def test_slim_core_windows_template(self):
        assert pub.ASSET_NAME_TEMPLATES["slim_core_windows"] == "VoiceTyper-Setup-{version}.exe"

    def test_slim_core_macos_template_includes_arch(self):
        template = pub.ASSET_NAME_TEMPLATES["slim_core_macos"]
        assert "{arch}" in template
        assert "{version}" in template

    def test_slim_core_linux_template_includes_arch(self):
        template = pub.ASSET_NAME_TEMPLATES["slim_core_linux"]
        assert "{arch}" in template
        assert "{version}" in template


# ── Defaults ───────────────────────────────────────────────────────────


class TestDefaults:
    """Default values are pinned (changing them breaks the URL contract)."""

    def test_default_repo(self):
        assert pub.DEFAULT_REPO == "AbdallahIsDev/voice-typer"

    def test_default_gh_cli(self):
        assert pub.DEFAULT_GH_CLI == "gh"


# ── CLI ────────────────────────────────────────────────────────────────


class TestCli:
    """The CLI entry point (``main``)."""

    def test_no_assets_returns_usage_error(self, capsys, tmp_path: Path):
        """No assets → exit code 2 (usage error)."""
        exit_code = pub.main(["--tag", "v1.2.3"])
        assert exit_code == 2

    def test_notes_and_notes_file_mutually_exclusive(
        self,
        fake_assets: dict[str, Path],
        tmp_path: Path,
        capsys,
    ):
        """``--notes`` and ``--notes-file`` together → exit code 2."""
        notes_file = tmp_path / "notes.md"
        notes_file.write_text("release notes")
        exit_code = pub.main([
            "--tag", "v1.2.3",
            "--pack-onefile", str(fake_assets["pack-1.2.3.zip"]),
            "--notes", "some notes",
            "--notes-file", str(notes_file),
            "--backend", "gh",
        ])
        assert exit_code == 2

    def test_json_output_format(
        self,
        fake_assets: dict[str, Path],
        capsys,
        fake_runner_success,
    ):
        """``--json`` prints the result as JSON for CI parsing."""
        runner, _ = fake_runner_success
        # Patch ``run_gh`` to use the fake runner.
        with patch.object(pub, "run_gh", side_effect=runner):
            exit_code = pub.main([
                "--tag", "v1.2.3",
                "--pack-onefile", str(fake_assets["pack-1.2.3.zip"]),
                "--pack-manifest", str(fake_assets["pack-manifest.json"]),
                "--repo", "owner/repo",
                "--backend", "gh",
                "--json",
            ])
        assert exit_code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["success"] is True
        assert result["tag"] == "v1.2.3"
        assert result["backend"] == "gh"
        assert "uploaded" in result

    def test_missing_asset_fails(
        self,
        tmp_path: Path,
        capsys,
    ):
        """A missing asset path → exit code 1 (failure)."""
        exit_code = pub.main([
            "--tag", "v1.2.3",
            "--pack-onefile", str(tmp_path / "nonexistent.zip"),
            "--backend", "gh",
        ])
        assert exit_code == 1


# ── Idempotency ────────────────────────────────────────────────────────


class TestIdempotency:
    """Re-running the publisher with the same tag is safe (idempotent)."""

    def test_clobber_flag_default_true(self, fake_assets: dict[str, Path]):
        """The default upload command includes ``--clobber`` so re-runs replace
        existing assets with the same name."""
        assets = list(fake_assets.values())
        cmd = pub.build_gh_upload_command("v1.2.3", assets, repo="owner/repo")
        assert "--clobber" in cmd, (
            "default upload command must include --clobber for idempotent re-runs"
        )

    def test_existing_release_does_not_fail_publish(
        self,
        fake_assets: dict[str, Path],
        fake_runner_release_exists,
    ):
        """When the release already exists, the publish succeeds (skips create)."""
        runner, _ = fake_runner_release_exists
        result = pub.publish_release(
            tag="v1.2.3",
            assets=list(fake_assets.values()),
            repo="owner/repo",
            backend="gh",
            runner=runner,
        )
        assert result.success is True


# ── PublishResult dataclass ────────────────────────────────────────────


class TestPublishResultDataclass:
    """``PublishResult`` is a serializable dataclass (for ``--json`` output)."""

    def test_to_dict_via_asdict(self):
        """``dataclasses.asdict`` produces a JSON-serializable dict."""
        from dataclasses import asdict

        result = pub.PublishResult(
            success=True,
            tag="v1.2.3",
            release_url="https://github.com/owner/repo/releases/tag/v1.2.3",
            uploaded=["pack-1.2.3.zip"],
            backend="gh",
        )
        d = asdict(result)
        assert d["success"] is True
        assert d["tag"] == "v1.2.3"
        # Must be JSON-serializable (for the CLI ``--json`` flag).
        json.dumps(d)

    def test_default_empty_lists(self):
        """``uploaded`` / ``skipped`` / ``errors`` default to empty lists."""
        result = pub.PublishResult(success=True, tag="v1.2.3")
        assert result.uploaded == []
        assert result.skipped == []
        assert result.errors == []
