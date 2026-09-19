"""Model-operation tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock


class TestBuildModelsSubmenuConfigProvider:
    """build_models_menu_items accepts config_provider kwarg."""

    def test_accepts_config_provider(self, tmp_path):

        from voice_typer.server.tray_models import build_models_submenu_data

        config = MagicMock()
        config.model_size = "tiny"
        config.asr_backend = "whisper"

        result = build_models_submenu_data(
            lambda: tmp_path,
            lambda name: None,
            config_provider=config,
        )
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "tiny" in active_models

    def test_corrupt_config_json_falls_back_to_defaults_and_logs(self, tmp_path, caplog):
        """PI-19 regression: a corrupt ``config.json`` must NOT silently"""
        import logging

        from voice_typer.server.tray_models import build_models_submenu_data

        config_dir = tmp_path
        config_path = config_dir / "config.json"
        # Write corrupt JSON that json.load will reject.
        config_path.write_text("{not valid json at all", encoding="utf-8")

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.tray_models"):
            result = build_models_submenu_data(
                lambda: config_dir,
                lambda name: None,
                config_provider=None,
            )

        # Defaults must be returned so the tray menu is still usable.
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "tiny" in active_models

        # The corrupt-config log line must be present.
        corrupt_log_lines = [r.message for r in caplog.records if "failed to read config.json" in r.message]
        assert corrupt_log_lines, (
            "PI-19 regression: expected a log.debug line recording the config.json read failure, got none"
        )


class TestCancelModelDownloadMechanism:
    """Verify the cancel mechanism works at the Python service level."""

    def test_cancel_returns_false_when_no_download_active(self, tmp_config_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_cancel_returns_true_when_download_active(self, tmp_config_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        download_id = service._register_download("test-model")
        event = service._download_cancel_events[download_id]
        assert not event.is_set()
        assert service._active_download_id == download_id

        result = service.cancel_model_download()
        assert result == {"cancelled": True}
        assert event.is_set()
        # Cleanup so the dict doesn't leak between tests.
        service._unregister_download(download_id)

    def test_cancel_event_is_clearable(self, tmp_config_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        download_id = service._register_download("test-model")
        service.cancel_model_download()
        # Unregistering the download clears the active id and removes
        service._unregister_download(download_id)
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_download_cancel_events_starts_empty(self, tmp_config_dir):
        """A fresh service has no registered downloads."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        assert service._download_cancel_events == {}
        assert service._active_download_id is None


class TestDeleteModelUsesRegistryUnconditionally:
    """:data:`MODEL_REGISTRY` for ALL models (whisper/distil/parakeet/qwen)"""

    def _make_service(self):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type(
                "FakeConfig",
                (),
                {"asr_backend": "whisper", "model_size": "tiny"},
            )()

        return VoiceTyperService(FakeApp())

    def test_parakeet_uses_registry_repo_id(self, tmp_config_dir, monkeypatch):
        """``delete_model(\"parakeet\")`` looks up the registry's"""
        from voice_typer.server.model_registry import get_model_metadata

        service = self._make_service()
        meta = get_model_metadata("parakeet")
        assert meta is not None, "parakeet must be in MODEL_REGISTRY"
        assert meta.repo_id == "grikdotnet/parakeet-tdt-0.6b-fp16"

        import voice_typer.server.config as cfg_mod

        cache_dir = cfg_mod._config_dir() / "huggingface" / "hub"
        model_dir_name = f"models--{meta.repo_id.replace('/', '--')}"
        (cache_dir / model_dir_name).mkdir(parents=True)

        result = service.delete_model("parakeet")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert not (cache_dir / model_dir_name).exists()

    def test_qwen_uses_registry_repo_id(self, tmp_config_dir):
        """``delete_model(\"qwen\")`` no longer returns \"Unknown model\""""
        from voice_typer.server.model_registry import get_model_metadata

        service = self._make_service()
        meta = get_model_metadata("qwen")
        assert meta is not None, "qwen must be in MODEL_REGISTRY"
        assert meta.repo_id == "andrewleech/qwen3-asr-1.7b-onnx"

        result = service.delete_model("qwen")
        assert result["success"] is False
        assert "not downloaded" in result["message"], (
            f"Expected 'not downloaded' message for absent qwen cache, got: {result}"
        )

    def test_unknown_model_still_errors(self, tmp_config_dir):
        """A model name absent from the registry still surfaces the"""
        service = self._make_service()
        result = service.delete_model("definitely-not-a-real-model")
        assert result["success"] is False
        assert "Unknown model" in result["message"]


class TestGetModelStatusCache:
    """SVC-9 / PERF-10: ``get_model_status`` caches its result for 5 s"""

    def _make_service(self):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type(
                "FakeConfig",
                (),
                {"asr_backend": "whisper", "model_size": "tiny"},
            )()

        return VoiceTyperService(FakeApp())

    def test_two_consecutive_calls_return_same_cached_object(self, tmp_config_dir, monkeypatch):
        """Within the 5 s TTL window, the second call returns the SAME"""
        service = self._make_service()
        monkeypatch.setattr("os.path.isdir", lambda p: False)
        first = service.get_model_status()
        second = service.get_model_status()
        assert first is second, "Second call within TTL should return the cached dict object"

    def test_invalidate_forces_recompute(self, tmp_config_dir, monkeypatch):
        """``_invalidate_model_status_cache`` causes the next call to"""
        service = self._make_service()
        monkeypatch.setattr("os.path.isdir", lambda p: False)
        first = service.get_model_status()
        service._invalidate_model_status_cache()
        second = service.get_model_status()
        assert first is not second, "After invalidation, the cache should be re-populated with a fresh dict"

    def test_delete_model_invalidates_cache(self, tmp_config_dir, monkeypatch):
        """A successful ``delete_model`` drops the status cache so the"""
        from voice_typer.server.model_registry import get_model_metadata

        service = self._make_service()
        monkeypatch.setattr("os.path.isdir", lambda p: False)
        service.get_model_status()
        assert service._model_status_cache is not None

        import voice_typer.server.config as cfg_mod

        cache_dir = cfg_mod._config_dir() / "huggingface" / "hub"
        meta = get_model_metadata("parakeet")
        assert meta is not None
        model_dir_name = f"models--{meta.repo_id.replace('/', '--')}"
        (cache_dir / model_dir_name).mkdir(parents=True)

        result = service.delete_model("parakeet")
        assert result["success"] is True

        assert service._model_status_cache is None, "delete_model must invalidate the get_model_status cache (SVC-9)"

    def test_cache_dir_exists_probed_once_per_compute(self, tmp_config_dir, monkeypatch):
        """SVC-9 / PERF-10: ``cache_dir_exists = os.path.isdir(cache_dir)``"""
        service = self._make_service()

        isdir_calls: list[str] = []

        def _spy_isdir(p):
            isdir_calls.append(str(p))
            return False

        monkeypatch.setattr("os.path.isdir", _spy_isdir)
        service._compute_model_status()
        cache_dir_root_probes = [c for c in isdir_calls if c.endswith(f"huggingface{os.sep}hub")]
        assert len(cache_dir_root_probes) == 1, (
            f"cache_dir root should be stat exactly once per compute_model_status "
            f"call (hoisted above the loop). Got {len(cache_dir_root_probes)} probes: "
            f"{cache_dir_root_probes}"
        )


class TestDownloadPollScopedToModelDir:
    """PERF-21: the download-progress polling loop walks ONLY the"""

    def test_poll_walks_model_dir_not_cache_root(self, tmp_config_dir, monkeypatch):
        """When polling for download progress, the loop calls"""
        import inspect

        from voice_typer.server.service._download_helpers import poll_download_progress

        src = inspect.getsource(poll_download_progress)
        assert "model_dir = cache_dir / f\"models--{repo_id.replace('/', '--')}\"" in src, (
            "PERF-21: poll_download_progress must compute model_dir = "
            "cache_dir / models--<repo_id> and walk THAT, not the whole cache"
        )
        assert 'model_dir.rglob("*")' in src, (
            "PERF-21: progress polling must call model_dir.rglob('*'), not cache_dir.rglob('*')"
        )
        # Strip Python comments before checking so the PERF-21
        code_only_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_only_lines.append(line)
        code_only = "\n".join(code_only_lines)
        assert 'cache_dir.rglob("*")' not in code_only, (
            "PERF-21 regression: poll_download_progress still calls "
            "cache_dir.rglob('*') in actual code, this walks the ENTIRE "
            "HF cache tree every 1 s and was the bug PERF-21 fixed."
        )


class TestDeleteStaleActiveModel:
    """stale config selection instead of refusing."""

    @staticmethod
    def _make_app(model_size: str):
        from unittest.mock import MagicMock

        app = MagicMock()
        app.config.qwen_model_path = None
        app.config.parakeet_model_path = None
        app.config.asr_backend = "whisper"
        app.config.model_size = model_size
        return app

    @staticmethod
    def _make_cache_dir(tmp_config_dir):
        cache_dir = tmp_config_dir / "huggingface" / "hub"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def test_active_missing_clears_config_to_downloaded_fallback(self, tmp_config_dir, monkeypatch):
        """disk. delete_model('tiny') succeeds AND switches the active model"""
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        fallback_meta = get_model_metadata("large-v3-turbo")
        assert fallback_meta is not None
        fallback_dir = cache_dir / f"models--{fallback_meta.repo_id.replace('/', '--')}"
        fallback_dir.mkdir(parents=True)
        # (pinned in tests/model_download/).
        monkeypatch.setattr(
            "voice_typer.server.transcription_download.is_model_snapshot_complete",
            lambda repo_id: (cache_dir / f"models--{repo_id.replace('/', '--')}").is_dir(),
        )

        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert not result.get("message"), f"success must omit message, got: {result}"
        assert result.get("reason") == "stale_cleared_switched", f"got: {result}"
        assert app.config.model_size == "large-v3-turbo", (
            f"delete_model must clear the stale active config to the "
            f"downloaded fallback, got model_size={app.config.model_size!r}"
        )
        assert app.config.asr_backend == "whisper"
        # Status cache invalidated so the next poll reflects the truth.
        assert service._model_status_cache is None, "delete_model must invalidate the get_model_status cache"

    def test_active_missing_apply_config_failure_does_not_claim_switch(self, tmp_config_dir, monkeypatch):
        """delete still succeeds but the message must NOT claim the active"""
        from unittest.mock import Mock

        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        fallback_meta = get_model_metadata("large-v3-turbo")
        assert fallback_meta is not None
        (cache_dir / f"models--{fallback_meta.repo_id.replace('/', '--')}").mkdir(parents=True)
        monkeypatch.setattr(
            "voice_typer.server.transcription_download.is_model_snapshot_complete",
            lambda repo_id: (cache_dir / f"models--{repo_id.replace('/', '--')}").is_dir(),
        )

        app = self._make_app(model_size="tiny")
        app.config.save_strict = Mock(side_effect=RuntimeError("disk full"))
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"delete must still succeed, got: {result}"
        # Success omits ``message``; reason must not claim a switch.
        assert not result.get("message"), f"success must omit message, got: {result}"
        assert result.get("reason") == "stale_nothing_to_delete", f"got: {result}"
        assert app.config.model_size == "tiny", (
            "apply_config rollback must leave the config pointing at the "
            "old (phantom) model after a save_strict failure"
        )

    def test_active_missing_no_fallback_enters_no_model_state(self, tmp_config_dir):
        """No model is downloaded at all, there is no valid replacement."""
        from voice_typer.server.model_registry import NO_MODEL_SIZE
        from voice_typer.server.service import VoiceTyperService

        self._make_cache_dir(tmp_config_dir)  # empty hub
        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert not result.get("message"), f"success must omit message, got: {result}"
        assert result.get("reason") == "stale_cleared_no_model", f"got: {result}"
        assert app.config.model_size == NO_MODEL_SIZE, (
            "config must enter the 'no model selected' state when no "
            f"downloaded fallback exists, got model_size={app.config.model_size!r}"
        )
        assert app.config.asr_backend == "whisper"
        # Tray must show the no-model error immediately (not keep
        from unittest.mock import ANY

        from voice_typer.server.tray_types import AppState

        app.tray.set_state.assert_called_once_with(AppState.ERROR, ANY)
        assert "No model selected" in app.tray.set_state.call_args[0][1]

    def test_active_on_disk_deletes_and_switches(self, tmp_config_dir, monkeypatch):
        """old refuse-and-switch guard dead-ended single-model users,"""
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        tiny_meta = get_model_metadata("tiny")
        assert tiny_meta is not None
        tiny_dir = cache_dir / f"models--{tiny_meta.repo_id.replace('/', '--')}"
        tiny_dir.mkdir(parents=True)
        (tiny_dir / "config.json").write_text("{}")
        fallback_meta = get_model_metadata("large-v3-turbo")
        assert fallback_meta is not None
        fallback_dir = cache_dir / f"models--{fallback_meta.repo_id.replace('/', '--')}"
        fallback_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "voice_typer.server.transcription_download.is_model_snapshot_complete",
            lambda repo_id: (cache_dir / f"models--{repo_id.replace('/', '--')}").is_dir(),
        )

        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"active delete must succeed, got: {result}"
        assert not result.get("message"), f"success must omit message, got: {result}"
        assert result.get("reason") == "deleted_switched", f"got: {result}"
        # Engine was unloaded before the files went.
        app.models.unload_backend_for_delete.assert_called_once_with("whisper")
        # Files gone, selection moved, cache invalidated.
        assert not tiny_dir.exists()
        assert fallback_dir.exists()
        assert app.config.model_size == "large-v3-turbo"
        assert service._model_status_cache is None
        for call in app.tray.set_state.call_args_list:
            assert "No model selected" not in str(call)

    def test_active_on_disk_no_fallback_enters_no_model_state(self, tmp_config_dir, monkeypatch):
        """ACTIVE-DELETE with nothing else downloaded: files go and"""
        from voice_typer.server.model_registry import NO_MODEL_SIZE, get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        tiny_meta = get_model_metadata("tiny")
        assert tiny_meta is not None
        tiny_dir = cache_dir / f"models--{tiny_meta.repo_id.replace('/', '--')}"
        tiny_dir.mkdir(parents=True)
        (tiny_dir / "config.json").write_text("{}")
        monkeypatch.setattr(
            "voice_typer.server.transcription_download.is_model_snapshot_complete",
            lambda repo_id: (cache_dir / f"models--{repo_id.replace('/', '--')}").is_dir(),
        )

        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"active delete must succeed, got: {result}"
        assert not result.get("message"), f"success must omit message, got: {result}"
        assert result.get("reason") == "deleted_no_model", f"got: {result}"
        assert not tiny_dir.exists()
        assert app.config.model_size == NO_MODEL_SIZE
        # Tray shows the no-model error immediately.
        from unittest.mock import ANY

        from voice_typer.server.tray_types import AppState

        app.tray.set_state.assert_called_once_with(AppState.ERROR, ANY)
        assert "No model selected" in app.tray.set_state.call_args[0][1]

    def test_active_delete_refused_while_recording(self, tmp_config_dir):
        """logged reason), files untouched."""
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        tiny_meta = get_model_metadata("tiny")
        assert tiny_meta is not None
        tiny_dir = cache_dir / f"models--{tiny_meta.repo_id.replace('/', '--')}"
        tiny_dir.mkdir(parents=True)

        app = self._make_app(model_size="tiny")
        app.recorder.recording = True
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is False
        assert "Stop the current dictation" in result["message"], f"got: {result}"
        assert tiny_dir.exists()
