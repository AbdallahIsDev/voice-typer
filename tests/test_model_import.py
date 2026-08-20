"""Tests for ``VoiceTyperService.import_model()`` — scanning directories for
HuggingFace model cache folders and importing recognized models into the
app's HF cache.

Covers:
- Happy path: scanning a dir with HF cache subdirs matching MODEL_REGISTRY
- Empty directory
- No recognized models
- Permission denied on read
- Selected directory IS a model cache dir
- Overwrite when model already exists in cache
- Mixed success/failure

The catalog was pruned 2026-08-15 to `tiny` + `large-v3-turbo` (Whisper)
plus `parakeet` / `qwen`, so the tests use those repos. Import only
recognizes models in MODEL_REGISTRY.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Kept-catalog repos ────────────────────────────────────────────────
_REPO_TINY = "Systran/faster-whisper-tiny"
_REPO_TURBO = "Systran/faster-whisper-large-v3-turbo"
_REPO_PARKEET = "grikdotnet/parakeet-tdt-0.6b-fp16"


# ── Helpers ─────────────────────────────────────────────────────────────


def _hf_cache_dir_name(repo_id: str) -> str:
    """Convert a HuggingFace repo ID to the cache directory name.

    Example: ``Systran/faster-whisper-tiny`` → ``models--Systran--faster-whisper-tiny``
    """
    return f"models--{repo_id.replace('/', '--')}"


def _make_model_cache_dir(parent: Path, repo_id: str) -> Path:
    """Create a minimal HF cache subdirectory structure under ``parent``.

    HF cache dirs contain:
      - blobs/ (empty)
      - refs/ (empty)
      - snapshots/ (empty)
      - .no_exist (placeholder file)
    """
    dir_name = _hf_cache_dir_name(repo_id)
    model_dir = parent / dir_name
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "blobs").mkdir(exist_ok=True)
    (model_dir / "refs").mkdir(exist_ok=True)
    (model_dir / "snapshots").mkdir(exist_ok=True)
    (model_dir / ".no_exist").write_text("placeholder")
    return model_dir


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def service():
    """Build a VoiceTyperService with a mock app.

    Returns a service whose ``import_model()`` is ready to call with a
    monkeypatched ``_config_dir`` (set by each test via ``tmp_path``).
    """
    from voice_typer.server.service import VoiceTyperService

    mock_app = MagicMock()
    return VoiceTyperService(mock_app)


# ── Tests ───────────────────────────────────────────────────────────────


class TestImportModelHappyPath:
    """Core scenario: scanning a directory with HF cache subfolders."""

    def test_imports_recognized_models(self, service, tmp_path, monkeypatch):
        """Create a source dir with tiny + large-v3-turbo HF cache subdirs;
        call import_model; verify both are copied to the app's HF cache."""
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()

        tiny_dir = _make_model_cache_dir(src_dir, _REPO_TINY)
        turbo_dir = _make_model_cache_dir(src_dir, _REPO_TURBO)

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny" in result["imported"], f"Expected tiny to be imported, got {result['imported']}"
        assert "large-v3-turbo" in result["imported"], (
            f"Expected large-v3-turbo to be imported, got {result['imported']}"
        )
        assert len(result["errors"]) == 0, f"Expected no errors, got {result['errors']}"

        assert (app_hf / tiny_dir.name).exists(), f"tiny cache dir not found at {app_hf / tiny_dir.name}"
        assert (app_hf / turbo_dir.name).exists(), f"large-v3-turbo cache dir not found at {app_hf / turbo_dir.name}"
        assert (app_hf / tiny_dir.name / ".no_exist").read_text() == "placeholder"

    def test_imports_multiple_models_from_registry(self, service, tmp_path, monkeypatch):
        """Import multiple models of different backends (whisper + parakeet)."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()

        _make_model_cache_dir(src_dir, _REPO_TURBO)
        _make_model_cache_dir(src_dir, _REPO_PARKEET)

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert "large-v3-turbo" in result["imported"]
        assert "parakeet" in result["imported"]
        assert len(result["errors"]) == 0


class TestImportModelEdgeCases:
    """Edge cases for the import_model method."""

    def test_empty_directory(self, service, tmp_path, monkeypatch):
        """Empty source dir — no models found, none imported."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "empty_source"
        src_dir.mkdir()

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert result["imported"] == []
        assert result["found"] == []
        assert result["errors"] == []

    def test_no_recognized_models(self, service, tmp_path, monkeypatch):
        """Source dir has subdirs that don't match any known model patterns."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()

        (src_dir / "my_models").mkdir()
        (src_dir / "random_data").mkdir()
        (src_dir / "models--Unknown--Model").mkdir()  # unknown model
        # Removed-from-catalog repos are NOT recognized either.
        (src_dir / "models--Systran--faster-whisper-small.en").mkdir()

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert result["imported"] == []
        assert result["found"] == []
        assert result["errors"] == []

    def test_selected_dir_is_itself_a_model_cache_dir(self, service, tmp_path, monkeypatch):
        """User selects a ``models--Systran--faster-whisper-tiny`` directory
        directly (not its parent)."""
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_parent = tmp_path / "portable_models"
        src_parent.mkdir()
        model_dir = _make_model_cache_dir(src_parent, _REPO_TINY)

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(model_dir))

        assert result["success"] is True
        assert "tiny" in result["imported"]
        assert (app_hf / model_dir.name).exists()

    def test_overwrite_existing_model(self, service, tmp_path, monkeypatch):
        """Import a model that already exists in the app's HF cache;
        verify it is replaced (old rmtree + new copytree)."""
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        old_cache = app_hf / _hf_cache_dir_name(_REPO_TINY)
        old_cache.mkdir(parents=True)
        (old_cache / "old_version_marker.txt").write_text("this is the old version")

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        _make_model_cache_dir(src_dir, _REPO_TINY)
        (src_dir / _hf_cache_dir_name(_REPO_TINY) / "new_version.txt").write_text("this is the new version")

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny" in result["imported"]
        assert not (app_hf / _hf_cache_dir_name(_REPO_TINY) / "old_version_marker.txt").exists()
        assert (app_hf / _hf_cache_dir_name(_REPO_TINY) / "new_version.txt").read_text() == "this is the new version"

    def test_permission_denied_on_scan(self, service, tmp_path, monkeypatch):
        """Simulate a PermissionError when reading the source directory."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "restricted"
        src_dir.mkdir()

        original_listdir = os.listdir

        def _restricted_listdir(path):
            if str(path) == str(src_dir):
                raise PermissionError(f"Permission denied: {path}")
            return original_listdir(path)

        monkeypatch.setattr(os, "listdir", _restricted_listdir)

        result = service.import_model(str(src_dir))

        assert result["success"] is False
        assert result["imported"] == []
        assert result["found"] == []
        assert len(result["errors"]) == 1
        assert "Permission denied" in result["errors"][0]["error"]

    def test_partial_import_failure(self, service, tmp_path, monkeypatch):
        """One model succeeds, another fails (simulated via shutil.copytree
        raising on a specific directory)."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()

        # tiny will succeed
        _make_model_cache_dir(src_dir, _REPO_TINY)
        # large-v3-turbo will fail (monkeypatched copytree)
        _make_model_cache_dir(src_dir, _REPO_TURBO)

        original_copytree = shutil.copytree

        def _failing_copytree(*args, **kwargs):
            dst = args[1]  # second positional arg is the destination
            if "large-v3-turbo" in str(dst):
                raise OSError(f"Disk full writing to {dst}")
            return original_copytree(*args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", _failing_copytree)
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny" in result["imported"]
        assert "large-v3-turbo" not in result["imported"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "large-v3-turbo"
        assert "Disk full" in result["errors"][0]["error"]

    def test_tray_cache_invalidated_on_success(self, service, tmp_path, monkeypatch):
        """When at least one model is imported, the tray models cache
        must be invalidated."""
        invalidate_called = [False]

        def _mock_invalidate():
            invalidate_called[0] = True

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            _mock_invalidate,
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        _make_model_cache_dir(src_dir, _REPO_TINY)

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny" in result["imported"]
        assert invalidate_called[0], "tray cache invalidation should have been called"

    def test_tray_cache_not_invalidated_on_no_imports(self, service, tmp_path, monkeypatch):
        """No models imported → tray cache must NOT be invalidated."""
        invalidate_called = [False]

        def _mock_invalidate():
            invalidate_called[0] = True

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            _mock_invalidate,
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "empty_dir"
        src_dir.mkdir()

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert result["imported"] == []
        assert not invalidate_called[0], (
            "tray cache invalidation should NOT have been called when no models were imported"
        )


class TestImportModelIntegration:
    """Tests that exercise the full import_model path end-to-end."""

    def test_import_creates_app_cache_dir_if_missing(self, service, tmp_path, monkeypatch):
        """The app's HF cache dir doesn't exist before the call;
        import_model must create it."""
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        assert not app_hf.exists()

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        _make_model_cache_dir(src_dir, _REPO_TINY)

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny" in result["imported"]
        assert app_hf.exists(), "import_model should create the app's HF cache dir if missing"

    def test_found_includes_all_matched_models(self, service, tmp_path, monkeypatch):
        """``found`` list includes all models matched in the registry,
        even if some fail to import."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        _make_model_cache_dir(src_dir, _REPO_TINY)
        _make_model_cache_dir(src_dir, _REPO_TURBO)
        _make_model_cache_dir(src_dir, _REPO_PARKEET)

        # Make large-v3-turbo fail
        original_copytree = shutil.copytree

        def _failing_copytree(*args, **kwargs):
            dst = args[1]  # second positional arg is the destination
            if "large-v3-turbo" in str(dst):
                raise OSError(f"Disk full writing to {dst}")
            return original_copytree(*args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", _failing_copytree)
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        # All three should be in 'found'
        assert set(result["found"]) == {"tiny", "large-v3-turbo", "parakeet"}, (
            f"Expected all 3 models in found, got {result['found']}"
        )
        # Only 2 should be imported (large-v3-turbo failed)
        assert set(result["imported"]) == {"tiny", "parakeet"}
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "large-v3-turbo"


class TestImportModelProtocolDrift:
    """Guard: import_model must be declared on ServiceProtocol."""

    def test_service_protocol_declares_import_model(self):
        """``ServiceProtocol`` must declare ``import_model`` so the
        protocol-drift detection test passes."""
        from voice_typer.server.providers import ServiceProtocol

        assert hasattr(ServiceProtocol, "import_model"), "ServiceProtocol must declare import_model method"

    def test_service_has_import_model_method(self):
        """``VoiceTyperService`` has the ``import_model`` method."""
        from voice_typer.server.service import VoiceTyperService

        assert hasattr(VoiceTyperService, "import_model"), "VoiceTyperService must have import_model method"

    def test_ipc_registers_import_model_command(self):
        """``IPCServer._COMMAND_REGISTRY`` includes ``import_model``."""
        from voice_typer.server.ipc_server import IPCServer

        assert "import_model" in IPCServer._COMMAND_REGISTRY, "IPC _COMMAND_REGISTRY must include import_model"

    def test_ipc_has_import_model_handler(self):
        """``IPCServer`` has ``_handle_import_model`` handler."""
        from voice_typer.server.ipc_server import IPCServer

        assert hasattr(IPCServer, "_handle_import_model"), "IPCServer must have _handle_import_model method"
