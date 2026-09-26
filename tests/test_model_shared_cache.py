"""Shared HF cache migration: shared-first contract pins."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from voice_typer.server import model_availability as ma


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch: pytest.MonkeyPatch):
    ma.invalidate()
    monkeypatch.setattr(ma, "_download_active_impl", lambda: False)
    yield
    ma.invalidate()
    monkeypatch.setattr(ma, "_download_active_impl", lambda: False)


def _point_shared(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    shared = root / "shared-hub"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ma, "shared_hub_dir", lambda: shared)
    return shared


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _any_complete_probe(config_dir: Path):
    def probe(repo_id: str) -> bool:
        return any((d / "complete").exists() for d in ma.snapshot_search_dirs(config_dir, repo_id))

    return probe


class TestSearchOrder:
    def test_shared_first_then_app(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        shared = _point_shared(monkeypatch, tmp_path)
        dirs = ma.snapshot_search_dirs(tmp_path, "org/repo")
        assert dirs[0] == shared / "models--org--repo"
        assert dirs[1] == tmp_path / "huggingface" / "hub" / "models--org--repo"

    def test_leaf_escapes_slash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _point_shared(monkeypatch, tmp_path)
        dirs = ma.snapshot_search_dirs(tmp_path, "a/b/c")
        assert [d.name for d in dirs] == ["models--a--b--c", "models--a--b--c"]


class TestIsAvailable:
    def test_true_via_shared_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        shared = _point_shared(monkeypatch, tmp_path)
        snap = shared / "models--org--repo"
        snap.mkdir(parents=True)
        (snap / "complete").write_bytes(b"x")
        assert ma.is_available("org/repo", tmp_path, probe=_any_complete_probe(tmp_path)) is True

    def test_true_via_app_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _point_shared(monkeypatch, tmp_path)
        snap = tmp_path / "huggingface" / "hub" / "models--org--repo"
        snap.mkdir(parents=True)
        (snap / "complete").write_bytes(b"x")
        assert ma.is_available("org/repo", tmp_path, probe=_any_complete_probe(tmp_path)) is True

    def test_false_when_neither(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _point_shared(monkeypatch, tmp_path)
        assert ma.is_available("org/repo", tmp_path, probe=_any_complete_probe(tmp_path)) is False


class TestSharedEnv:
    # Real-resolution assertion only (reads HF_HUB_CACHE, writes nothing):
    # opts out of the global shared-hub isolation via real_shared_hub.
    @pytest.mark.real_shared_hub
    def test_hf_hub_cache_respected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        pytest.importorskip("huggingface_hub")
        from huggingface_hub import constants as hf_constants

        target = tmp_path / "env-hub"
        monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(target))
        assert ma.shared_hub_dir() == target


class TestEnsureHfEnv:
    def test_leaves_preset_hf_home_alone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from voice_typer.server.asr_setup import ensure_hf_env

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("HF_HOME", str(tmp_path / "preset"))
        ensure_hf_env()
        assert os.environ["HF_HOME"] == str(tmp_path / "preset")

    def test_sets_no_hf_home_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from voice_typer.server.asr_setup import ensure_hf_env

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("HF_HOME", raising=False)
        ensure_hf_env()
        assert "HF_HOME" not in os.environ

    def test_ancillary_flags_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from voice_typer.server.asr_setup import ensure_hf_env

        monkeypatch.setenv("HOME", str(tmp_path))
        for var in (
            "HF_HUB_DISABLE_SYMLINKS_WARNING",
            "HF_HUB_DISABLE_XET",
            "HF_HUB_DISABLE_TELEMETRY",
        ):
            monkeypatch.delenv(var, raising=False)
        ensure_hf_env()
        assert os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] == "1"
        assert os.environ["HF_HUB_DISABLE_XET"] == "true"
        assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


class TestDeleteBothHubs:
    def test_cleanup_removes_both_copies(self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch):
        from voice_typer.server.asr_utils import cleanup_hf_cache_dir

        shared = _point_shared(monkeypatch, tmp_config_dir)
        repo_id = "org/repo"
        leaf = "models--org--repo"
        shared_snap = shared / leaf
        app_snap = tmp_config_dir / "huggingface" / "hub" / leaf
        shared_snap.mkdir(parents=True)
        app_snap.mkdir(parents=True)
        (shared_snap / "w.bin").write_bytes(b"x")
        (app_snap / "w.bin").write_bytes(b"x")
        cleanup_hf_cache_dir(repo_id)
        assert not shared_snap.exists()
        assert not app_snap.exists()

    def test_service_delete_removes_config_copy(self, tmp_config_dir: Path):
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import LausuService

        class FakeApp:
            config = type(
                "FakeConfig",
                (),
                {"asr_backend": "whisper", "model_size": "small.en"},
            )()

        meta = get_model_metadata("parakeet")
        assert meta is not None
        target = tmp_config_dir / "huggingface" / "hub" / f"models--{meta.repo_id.replace('/', '--')}"
        target.mkdir(parents=True)
        (target / "w.bin").write_bytes(b"x")
        result = LausuService(FakeApp()).delete_model("parakeet")
        assert result["success"] is True
        assert not target.exists()


class TestIntegrityFailClosed:
    def test_tampered_shared_copy_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from voice_typer.server import transcription_download as td

        shared = _point_shared(monkeypatch, tmp_path)
        snap = shared / "models--org--repo"
        snap.mkdir(parents=True)
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", lambda _d, _r: False)
        local_dir, failed = td.probe_cache(
            object(),
            lambda **kwargs: str(snap),
            "org/repo",
            "main",
            ["*.bin"],
            "tiny",
        )
        assert (local_dir, failed) == (None, True)

    def test_cached_tamper_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from voice_typer.server.asr_errors import ModelIntegrityError
        from voice_typer.server.transcription_download import require_model_downloaded

        _point_shared(monkeypatch, tmp_path)

        class FakeEngine:
            def _probe_cache(self, *args, **kwargs):
                return None, True

        with pytest.raises(ModelIntegrityError):
            require_model_downloaded(FakeEngine(), "tiny")


class TestTmpAudit:
    def test_hub_paths_confined_to_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.delenv("HF_HOME", raising=False)
        shared = _point_shared(monkeypatch, tmp_path)
        for d in ma.snapshot_search_dirs(tmp_path, "org/repo"):
            assert _inside(d, tmp_path)
        assert _inside(shared, tmp_path)
