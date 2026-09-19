"""XV-1 / XV-2 / XV-5 service.py fix regression tests."""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from unittest.mock import MagicMock


def _make_service(tmp_config_dir):
    """Build a ``VoiceTyperService`` against a minimal fake app."""
    from voice_typer.server import service as svc_mod

    class FakeApp:
        config = type("FakeConfig", (), {})()
        _microphones: list = []
        tray = MagicMock()

    return svc_mod.VoiceTyperService(FakeApp())


# XV-1 deps-probe tests removed 2026-08-15: ``_check_qwen_deps`` /


class TestDownloadPollScopedToModelDir:
    """XV-2: ``download_model``'s progress-polling loop walks ONLY the"""

    def test_poll_uses_per_repo_subdir_construction(self):
        """Source guard: the polling loop must construct"""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.download_model)
        assert 'model_dir = cache_dir / f"models--{repo_id.replace' in src, (
            "XV-2: download_model must construct the per-repo subdir "
            "via cache_dir / f\"models--{repo_id.replace('/', '--')}\" "
            "before walking it."
        )
        assert 'model_dir.rglob("*")' in src, (
            "XV-2: progress polling must walk model_dir (the per-repo subdir), not the whole cache_dir."
        )
        # The old wide-scan form must NOT appear in the polling block.
        assert 'cache_dir.rglob("*")' not in src, (
            "XV-2 regression: download_model still walks the entire cache_dir tree via cache_dir.rglob('*')."
        )

    def test_poll_does_not_stat_unrelated_repos(self, tmp_config_dir, monkeypatch):
        """End-to-end: when the HF cache contains an unrelated repo's"""

        # Build a fake HF hub cache with TWO repos:
        cache_root = tmp_config_dir / "huggingface" / "hub"
        other_repo = cache_root / "models--unrelated--other" / "snapshots" / "rev1"
        other_repo.mkdir(parents=True)
        other_file = other_repo / "model.safetensors"
        other_file.write_bytes(b"\x00" * 4096)  # 4 KB sentinel file

        target_repo = cache_root / "models--org--target" / "snapshots" / "rev1"
        target_repo.mkdir(parents=True)
        target_file = target_repo / "model.safetensors"
        target_file.write_bytes(b"\x01" * 8192)  # 8 KB

        # Track every explicit Path.stat call during the poll.
        stat_paths: list[str] = []
        real_stat = Path.stat

        def spy_stat(self, *args, **kwargs):
            stat_paths.append(str(self))
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", spy_stat)

        fake_meta = MagicMock()
        fake_meta.repo_id = "org/target"
        fake_meta.backend = "whisper"
        fake_meta.download_size_mb = 1
        monkeypatch.setattr(
            "voice_typer.server.model_registry.get_model_metadata",
            lambda name: fake_meta,
        )

        # Stub huggingface_hub.snapshot_download: the local-files-only
        def fake_snapshot_download(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not cached, drive into polling branch")
            return None

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)

        # Stub the retry wrapper so the download thread stays alive just
        import threading as _threading

        _dl_barrier = _threading.Event()

        def _fake_download_with_retry(fn, **kwargs):
            # Block briefly so the main thread's first ``t.is_alive()``
            _dl_barrier.wait(timeout=0.5)

        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            _fake_download_with_retry,
        )

        # Stub the asr_setup pause helpers (imported locally inside
        monkeypatch.setattr("voice_typer.server.asr_setup.clear_download_pause_state", lambda: None)
        monkeypatch.setattr("voice_typer.server.asr_setup.is_download_paused", lambda: False)
        monkeypatch.setattr("voice_typer.server.asr_setup.reset_download_pause_state", lambda: None)
        monkeypatch.setattr("voice_typer.server.asr_setup.wait_while_paused", lambda timeout_s=1.0: None)

        monkeypatch.setattr(
            "voice_typer.server.segmented_download.plan_segmented_files",
            lambda **kwargs: [],
        )

        monkeypatch.setattr(
            "voice_typer.server.tray_models.invalidate_model_availability_cache",
            lambda: None,
        )

        # Stub event_bus.publish so _push_progress doesn't touch real
        monkeypatch.setattr("voice_typer.server.event_bus.publish", lambda event: None)

        svc = _make_service(tmp_config_dir)
        # Stub the per-download cancellation plumbing.
        monkeypatch.setattr(svc, "_register_download", lambda name: "dlid")
        monkeypatch.setattr(svc, "_unregister_download", lambda dlid: None)
        monkeypatch.setattr(svc, "_is_download_cancelled", lambda dlid: False)
        # Stub the HF consent gate (return None = consent granted).
        monkeypatch.setattr(svc, "_require_huggingface_consent", lambda name: None)
        # Stub model-status cache invalidation (success path).
        monkeypatch.setattr(svc, "_invalidate_model_status_cache", lambda: None)

        # Drive download_model: the polling loop runs at least once
        try:
            svc.download_model("target-model")
        except Exception:
            # Any residual stubbing gap is fine, the stat spy has
            pass
        finally:
            # Release the barrier so no thread lingers past the test.
            _dl_barrier.set()

        # The unrelated repo's file must never appear in stat_paths.
        assert not any("models--unrelated--other" in p for p in stat_paths), (
            "XV-2: download_model's progress loop stat'd a file in an "
            "UNRELATED repo (models--unrelated--other). The polling loop "
            "must only walk the per-repo subdir. Stats seen: "
            f"{stat_paths[:5]}..."
        )
        # And at least one file in the target repo WAS stat'd (proving
        assert any("models--org--target" in p for p in stat_paths), (
            "XV-2: expected the target repo's file to be stat'd during "
            "the progress poll, but it wasn't. Stats seen: "
            f"{stat_paths[:5]}..."
        )


class TestMicrophonesCacheEmptyList:
    """XV-5: ``_microphones_cache`` starts as ``None`` (not ``[]``) and"""

    def test_cache_initialised_to_none(self, tmp_config_dir):
        svc = _make_service(tmp_config_dir)
        assert svc._microphones_cache is None, (
            "XV-5: _microphones_cache must be initialised to None (not "
            "[]) so the truthiness check doesn't bypass the cache when "
            "PortAudio returns an empty list."
        )

    def test_refresh_uses_is_not_none_source(self):
        """Source guard: the truthiness check must be ``is not None``,"""
        from voice_typer.server.service import VoiceTyperService

        src = inspect.getsource(VoiceTyperService.refresh_microphones)
        assert "self._microphones_cache is not None" in src, (
            "XV-5: refresh_microphones must use 'is not None' (not bare "
            "truthiness) so an empty cached list is still served from cache."
        )
        code_lines = [line for line in src.splitlines() if line.lstrip() and not line.lstrip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "if self._microphones_cache and " not in code_only, (
            "XV-5 regression: refresh_microphones still uses bare-truthiness "
            "cache check, this skips the cache when PortAudio returns 0 mics."
        )

    def test_empty_list_served_from_cache(self, tmp_config_dir, monkeypatch):
        """When the cache holds an empty list (legitimate \"0 mics\"),"""
        svc = _make_service(tmp_config_dir)
        svc._microphones_cache = []
        svc._microphones_cache_ts = time.monotonic()

        call_count = {"n": 0}

        def fake_list_microphones():
            call_count["n"] += 1
            return [{"name": "should-not-be-returned", "index": 0}]

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == [], (
            "XV-5: refresh_microphones must serve the cached empty list "
            "instead of re-querying PortAudio when the cache is empty."
        )
        assert call_count["n"] == 0, (
            "XV-5: list_microphones must NOT be called when the cache (even if empty) is fresh."
        )

    def test_non_empty_list_still_served_from_cache(self, tmp_config_dir, monkeypatch):
        """Sanity: a non-empty cache continues to be served (regression"""
        svc = _make_service(tmp_config_dir)
        cached_mics = [{"name": "USB Mic", "index": 0}]
        svc._microphones_cache = cached_mics
        svc._microphones_cache_ts = time.monotonic()

        call_count = {"n": 0}

        def fake_list_microphones():
            call_count["n"] += 1
            return []

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == cached_mics
        assert call_count["n"] == 0

    def test_first_call_queries_portaudio(self, tmp_config_dir, monkeypatch):
        """When the cache is ``None`` (never queried), the first"""
        svc = _make_service(tmp_config_dir)
        assert svc._microphones_cache is None

        call_count = {"n": 0}
        real_mics = [{"name": "Built-in Mic", "index": 0}]

        def fake_list_microphones():
            call_count["n"] += 1
            return real_mics

        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            fake_list_microphones,
        )

        result = svc.refresh_microphones()
        assert result == real_mics
        assert call_count["n"] == 1, (
            "XV-5: the first refresh_microphones call (cache is None) must query PortAudio exactly once."
        )
        assert svc._microphones_cache == real_mics
