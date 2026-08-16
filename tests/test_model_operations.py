"""Model-operation tests.

Extracted from the original ``tests/test_history_and_models.py`` catch-all
(Epic EC-25 / Entry #23 test-file split). This module pins the
service-layer model-management surface:

* tray submenu construction (``build_models_submenu_data``)
* per-download cancel mechanism (``_register_download`` /
  ``_download_cancel_events`` / ``_unregister_download`` +
  ``cancel_model_download``)
* model deletion via the registry (``delete_model`` for whisper/distil/
  parakeet/qwen — all routed through ``MODEL_REGISTRY``)
* ``get_model_status`` 5 s TTL cache (invalidated by ``delete_model`` and
  successful downloads; ``cache_dir`` probed once per compute)
* download-progress polling scoped to the per-model directory (PERF-21)

Test names + assertions are preserved verbatim from the original file;
only the file boundary moved.
"""

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
        """PI-19 regression: a corrupt ``config.json`` must NOT silently
        fall through to defaults. The tray menu still returns defaults
        (so the user sees a functional menu), but a ``log.debug`` line
        records the failure so it can be diagnosed from
        ``voice-typer.log``. Mirrors the pattern at ``config.py:1043``.
        """
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
    """Verify the cancel mechanism works at the Python service level.

    the legacy single-instance ``_download_cancel_event``
    attribute has been removed.  These tests now exercise the per-download
    API (``_register_download`` / ``_download_cancel_events`` /
    ``_unregister_download``) that production code uses.
    """

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
        # the Event from the dict, so a subsequent cancel returns False.
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
    """SVC-7: ``delete_model`` resolves ``repo_id`` from
    :data:`MODEL_REGISTRY` for ALL models (whisper/distil/parakeet/qwen)
    — the inline ``elif model_name == "parakeet"`` / ``elif model_name ==
    "qwen"`` branches are gone."""

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
        """``delete_model("parakeet")`` looks up the registry's
        ``nvidia/parakeet-tdt-0.6b-v3`` repo_id (NOT a hardcoded branch)."""
        from voice_typer.server.model_registry import get_model_metadata

        service = self._make_service()
        meta = get_model_metadata("parakeet")
        assert meta is not None, "parakeet must be in MODEL_REGISTRY"
        assert meta.repo_id == "visuall/parakeet-tdt-0.6b-v3-onnx-fp16"

        import voice_typer.server.config as cfg_mod

        cache_dir = cfg_mod._config_dir() / "huggingface" / "hub"
        model_dir_name = f"models--{meta.repo_id.replace('/', '--')}"
        (cache_dir / model_dir_name).mkdir(parents=True)

        result = service.delete_model("parakeet")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert not (cache_dir / model_dir_name).exists()

    def test_qwen_uses_registry_repo_id(self, tmp_config_dir):
        """``delete_model("qwen")`` no longer returns "Unknown model"
        — it derives ``andrewleech/qwen3-asr-1.7b-onnx`` from the
        registry (the ONNX export repo, 2026-08-15) and either deletes
        the matching cache dir or returns "not downloaded" when the
        dir is absent."""
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
        """A model name absent from the registry still surfaces the
        existing "Unknown model" error (regression guard)."""
        service = self._make_service()
        result = service.delete_model("definitely-not-a-real-model")
        assert result["success"] is False
        assert "Unknown model" in result["message"]


class TestGetModelStatusCache:
    """SVC-9 / PERF-10: ``get_model_status`` caches its result for 5 s
    and is invalidated by ``delete_model`` + successful downloads."""

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
        """Within the 5 s TTL window, the second call returns the SAME
        dict object — proving the cache served it (not a fresh compute)."""
        service = self._make_service()
        monkeypatch.setattr("os.path.isdir", lambda p: False)
        first = service.get_model_status()
        second = service.get_model_status()
        assert first is second, "Second call within TTL should return the cached dict object"

    def test_invalidate_forces_recompute(self, tmp_config_dir, monkeypatch):
        """``_invalidate_model_status_cache`` causes the next call to
        re-compute (returns a different dict object)."""
        service = self._make_service()
        monkeypatch.setattr("os.path.isdir", lambda p: False)
        first = service.get_model_status()
        service._invalidate_model_status_cache()
        second = service.get_model_status()
        assert first is not second, "After invalidation, the cache should be re-populated with a fresh dict"

    def test_delete_model_invalidates_cache(self, tmp_config_dir, monkeypatch):
        """A successful ``delete_model`` drops the status cache so the
        next ``get_model_status`` IPC call reflects the deletion."""
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
        """SVC-9 / PERF-10: ``cache_dir_exists = os.path.isdir(cache_dir)``
        is hoisted above the loop. The cache_dir root is stat exactly
        ONCE per ``_compute_model_status`` call, not once per model."""
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
    """PERF-21: the download-progress polling loop walks ONLY the
    in-progress model's directory, not the entire HF cache tree."""

    def test_poll_walks_model_dir_not_cache_root(self, tmp_config_dir, monkeypatch):
        """When polling for download progress, the loop calls
        ``rglob`` on ``cache_dir / models--<repo_id>``, NOT on
        ``cache_dir`` itself.

        We verify by inspecting the source — running an actual
        download is impractical in unit tests (snapshot_download +
        threading). The source-level guard catches any future revert
        that re-widens the rglob.

        DR-17: the polling loop was extracted from the original
        monolithic ``VoiceTyperService.download_model`` (now a thin
        dispatcher delegating to ``_download_whisper_family`` /
        ``_download_qwen`` / ``_download_parakeet``) into the
        module-level ``poll_download_progress`` helper in
        ``voice_typer/server/service/_download_helpers.py``. The
        PERF-21 invariant still lives there, so this test introspects
        the helper's source rather than ``download_model``.
        """
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
        # explanatory comment (which mentions cache_dir.rglob in plain
        # English) doesn't trip the assertion. We only want to catch a
        # regression where the actual CODE re-widens the rglob.
        code_only_lines = []
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_only_lines.append(line)
        code_only = "\n".join(code_only_lines)
        assert 'cache_dir.rglob("*")' not in code_only, (
            "PERF-21 regression: poll_download_progress still calls "
            "cache_dir.rglob('*') in actual code — this walks the ENTIRE "
            "HF cache tree every 1 s and was the bug PERF-21 fixed."
        )


class TestDeleteStaleActiveModel:
    """(STALE-ACTIVE): deleting an active-but-missing model clears the
    stale config selection instead of refusing.

    The configured active model can be removed from disk out-of-band
    (deleted folder / moved cache / wiped disk) while ``config.json``
    still points at it. ``delete_model`` must NOT refuse with "Cannot
    delete the active model" in that case — there is nothing on disk to
    protect. It clears the stale selection (switching to the first
    downloaded model, if any) via the canonical ``apply_config`` path,
    pushes ``config_changed``, invalidates the status cache, and returns
    success so the renderer drops the phantom "Active" state.
    """

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

    def test_active_missing_clears_config_to_downloaded_fallback(self, tmp_config_dir):
        """tiny is active but its files are gone; large-v3-turbo IS on
        disk. delete_model('tiny') succeeds AND switches the active model
        to large-v3-turbo so no phantom 'Active' state remains."""
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        fallback_meta = get_model_metadata("large-v3-turbo")
        assert fallback_meta is not None
        fallback_dir = cache_dir / f"models--{fallback_meta.repo_id.replace('/', '--')}"
        fallback_dir.mkdir(parents=True)

        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert "tiny" in result["message"]
        # The message reports the switch (truthful — apply_config committed).
        assert "switched to" in result["message"], (
            f"stale-clear success must report the switch, got: {result}"
        )
        # The stale selection was cleared: active model switched to the
        # downloaded fallback via apply_config.
        assert app.config.model_size == "large-v3-turbo", (
            f"delete_model must clear the stale active config to the "
            f"downloaded fallback, got model_size={app.config.model_size!r}"
        )
        assert app.config.asr_backend == "whisper"
        # Status cache invalidated so the next poll reflects the truth.
        assert service._model_status_cache is None, (
            "delete_model must invalidate the get_model_status cache"
        )

    def test_active_missing_apply_config_failure_does_not_claim_switch(self, tmp_config_dir):
        """If the config-clear (``apply_config``) fails and rolls back, the
        delete still succeeds but the message must NOT claim the active
        model was switched — the phantom config value is still live."""
        from unittest.mock import Mock

        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        fallback_meta = get_model_metadata("large-v3-turbo")
        assert fallback_meta is not None
        (cache_dir / f"models--{fallback_meta.repo_id.replace('/', '--')}").mkdir(
            parents=True
        )

        app = self._make_app(model_size="tiny")
        # save_strict raises -> apply_config rolls the in-memory config back
        # and re-raises; _clear_stale_active_model catches it.
        app.config.save_strict = Mock(side_effect=RuntimeError("disk full"))
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"delete must still succeed, got: {result}"
        # The switch did NOT commit (rolled back) — message must not claim it.
        assert "switched to" not in result["message"], (
            f"message must not claim a switch that rolled back, got: {result}"
        )
        assert app.config.model_size == "tiny", (
            "apply_config rollback must leave the config pointing at the "
            "old (phantom) model after a save_strict failure"
        )

    def test_active_missing_no_fallback_enters_no_model_state(self, tmp_config_dir):
        """No model is downloaded at all — there is no valid replacement.
        The delete still succeeds and the config enters the genuine
        "no model selected" state (``model_size == ""``, the
        ``NO_MODEL_SIZE`` sentinel) instead of keeping a phantom model
        that the app would otherwise try to load."""
        from voice_typer.server.model_registry import NO_MODEL_SIZE
        from voice_typer.server.service import VoiceTyperService

        self._make_cache_dir(tmp_config_dir)  # empty hub
        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert "no model selected" in result["message"], (
            f"message must say no model is selected, got: {result}"
        )
        assert "switched to" not in result["message"], (
            f"no fallback -> message must not claim a switch, got: {result}"
        )
        assert app.config.model_size == NO_MODEL_SIZE, (
            "config must enter the 'no model selected' state when no "
            f"downloaded fallback exists, got model_size={app.config.model_size!r}"
        )
        assert app.config.asr_backend == "whisper"

    def test_active_on_disk_still_refused(self, tmp_config_dir):
        """The original guard is preserved: an active model that IS on
        disk cannot be deleted (deleting it would break the running ASR
        backend)."""
        from voice_typer.server.model_registry import get_model_metadata
        from voice_typer.server.service import VoiceTyperService

        cache_dir = self._make_cache_dir(tmp_config_dir)
        tiny_meta = get_model_metadata("tiny")
        assert tiny_meta is not None
        tiny_dir = cache_dir / f"models--{tiny_meta.repo_id.replace('/', '--')}"
        tiny_dir.mkdir(parents=True)

        app = self._make_app(model_size="tiny")
        service = VoiceTyperService(app)

        result = service.delete_model("tiny")
        assert result["success"] is False
        assert "Cannot delete the active model" in result["message"], (
            f"active model on disk must still be refused, got: {result}"
        )
        # Files untouched.
        assert tiny_dir.exists()
