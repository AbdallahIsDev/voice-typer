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
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Helpers ─────────────────────────────────────────────────────────────


def _hf_cache_dir_name(repo_id: str) -> str:
    """Convert a HuggingFace repo ID to the cache directory name.

    Example: ``Systran/faster-whisper-tiny.en`` → ``models--Systran--faster-whisper-tiny.en``
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
    # The service doesn't call app methods during import_model; it only
    # needs the app reference for construction and the tray cache
    # invalidation path.
    return VoiceTyperService(mock_app)


# ── Tests ───────────────────────────────────────────────────────────────


class TestImportModelHappyPath:
    """Core scenario: scanning a directory with HF cache subfolders."""

    def test_imports_recognized_models(self, service, tmp_path, monkeypatch):
        """Create a source dir with tiny.en + small.en HF cache subdirs;
        call import_model; verify both are copied to the app's HF cache."""
        # Point the app's HF cache to tmp_path / app_hf
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        # Create source directory with two model cache subdirs
        src_dir = tmp_path / "source"
        src_dir.mkdir()

        tiny_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")
        small_dir = _make_model_cache_dir(src_dir, "Systran/faster-whisper-small.en")

        # Mock tray cache invalidation so it doesn't fail
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"], f"Expected tiny.en to be imported, got {result['imported']}"
        assert "small.en" in result["imported"], f"Expected small.en to be imported, got {result['imported']}"
        assert len(result["errors"]) == 0, f"Expected no errors, got {result['errors']}"

        # Verify the files were actually copied
        assert (app_hf / tiny_dir.name).exists(), f"tiny.en cache dir not found at {app_hf / tiny_dir.name}"
        assert (app_hf / small_dir.name).exists(), f"small.en cache dir not found at {app_hf / small_dir.name}"
        # Verify content was copied (the .no_exist placeholder)
        assert (app_hf / tiny_dir.name / ".no_exist").read_text() == "placeholder"

    def test_imports_multiple_models_from_registry(self, service, tmp_path, monkeypatch):
        """Import multiple models of different types (whisper + distil)."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "source"
        src_dir.mkdir()

        # Create medium.en (whisper) + distil-large-v3 (distil-whisper)
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-medium.en")
        _make_model_cache_dir(src_dir, "Systran/faster-distil-whisper-large-v3")

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert "medium.en" in result["imported"]
        assert "distil-large-v3" in result["imported"]
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

        # Create non-model subdirectories
        (src_dir / "my_models").mkdir()
        (src_dir / "random_data").mkdir()
        (src_dir / "models--Unknown--Model").mkdir()  # unknown model

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert result["imported"] == []
        assert result["found"] == []
        assert result["errors"] == []

    def test_selected_dir_is_itself_a_model_cache_dir(self, service, tmp_path, monkeypatch):
        """User selects a ``models--Systran--faster-whisper-tiny.en`` directory
        directly (not its parent)."""
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        # Create a model cache dir outside the app's cache
        src_parent = tmp_path / "portable_models"
        src_parent.mkdir()
        model_dir = _make_model_cache_dir(src_parent, "Systran/faster-whisper-tiny.en")

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        # Point import_model at the model cache dir itself
        result = service.import_model(str(model_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
        # Verify it was copied to the app's HF cache
        assert (app_hf / model_dir.name).exists()

    def test_overwrite_existing_model(self, service, tmp_path, monkeypatch):
        """Import a model that already exists in the app's HF cache;
        verify it is replaced (old rmtree + new copytree)."""
        app_hf = tmp_path / "app_hf" / "huggingface" / "hub"
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        # Create an old version of tiny.en in the app cache with
        # a distinguishing marker file.
        old_cache = app_hf / _hf_cache_dir_name("Systran/faster-whisper-tiny.en")
        old_cache.mkdir(parents=True)
        (old_cache / "old_version_marker.txt").write_text("this is the old version")

        # Create a source with a fresh copy
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")
        # Overwrite the placeholder with a new marker
        (src_dir / _hf_cache_dir_name("Systran/faster-whisper-tiny.en") / "new_version.txt").write_text(
            "this is the new version"
        )

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
        # The old marker should be gone
        assert not (app_hf / _hf_cache_dir_name("Systran/faster-whisper-tiny.en") / "old_version_marker.txt").exists()
        # The new content should be present
        assert (
            app_hf / _hf_cache_dir_name("Systran/faster-whisper-tiny.en") / "new_version.txt"
        ).read_text() == "this is the new version"

    def test_permission_denied_on_scan(self, service, tmp_path, monkeypatch):
        """Simulate a PermissionError when reading the source directory."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path / "app_hf",
        )

        src_dir = tmp_path / "restricted"
        src_dir.mkdir()

        # Monkeypatch os.listdir to raise PermissionError
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

        # Create tiny.en (will succeed)
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")
        # Create small.en (will fail — simulate by making the dest unwritable
        # or monkeypatching copytree)
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-small.en")

        # Monkeypatch shutil.copytree to fail for small.en
        original_copytree = shutil.copytree

        # Use *args because shutil.copytree recursively calls itself
        # with 7 positional arguments internally.
        def _failing_copytree(*args, **kwargs):
            dst = args[1]  # second positional arg is the destination
            if "small" in str(dst):
                raise OSError(f"Disk full writing to {dst}")
            return original_copytree(*args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", _failing_copytree)
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
        assert "small.en" not in result["imported"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "small.en"
        assert "Disk full" in result["errors"][0]["error"]

    def test_tray_cache_invalidated_on_success(self, service, tmp_path, monkeypatch):
        """When at least one model is imported, the tray models cache
        must be invalidated."""
        # Track whether invalidate was called
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
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
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

        # Ensure the cache dir does NOT exist
        assert not app_hf.exists()

        src_dir = tmp_path / "source"
        src_dir.mkdir()
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        assert result["success"] is True
        assert "tiny.en" in result["imported"]
        # The app cache dir should have been created
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
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-tiny.en")
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-small.en")
        _make_model_cache_dir(src_dir, "Systran/faster-whisper-medium.en")

        # Make small.en fail
        original_copytree = shutil.copytree

        # Use *args because shutil.copytree recursively calls itself
        # with 7 positional arguments internally.
        def _failing_copytree(*args, **kwargs):
            dst = args[1]  # second positional arg is the destination
            if "small" in str(dst):
                raise OSError(f"Disk full writing to {dst}")
            return original_copytree(*args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", _failing_copytree)
        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        result = service.import_model(str(src_dir))

        # All three should be in 'found'
        assert set(result["found"]) == {"tiny.en", "small.en", "medium.en"}, (
            f"Expected all 3 models in found, got {result['found']}"
        )
        # Only 2 should be imported (small.en failed)
        assert set(result["imported"]) == {"tiny.en", "medium.en"}
        assert len(result["errors"]) == 1
        assert result["errors"][0]["model"] == "small.en"


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
