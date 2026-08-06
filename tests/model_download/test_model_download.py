"""Model download / delete / status tests split out of the former ``tests/test_history_and_models.py``.

Domain: model management — cancel mechanism (per-download Event
registry), delete_model via MODEL_REGISTRY, get_model_status cache
(SVC-9 / PERF-10), and download-progress poll scoped to model_dir
(PERF-21).

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed. The shared
``tmp_config_dir`` fixture is provided by the top-level
``tests/conftest.py``.
"""

from __future__ import annotations

import inspect
import os


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
                {"asr_backend": "whisper", "model_size": "small.en"},
            )()

        return VoiceTyperService(FakeApp())

    def test_parakeet_uses_registry_repo_id(self, tmp_config_dir, monkeypatch):
        """``delete_model("parakeet")`` looks up the registry's
        ``nvidia/parakeet-tdt-0.6b-v3`` repo_id (NOT a hardcoded branch)."""
        from voice_typer.server.model_registry import get_model_metadata

        service = self._make_service()
        meta = get_model_metadata("parakeet")
        assert meta is not None, "parakeet must be in MODEL_REGISTRY"
        assert meta.repo_id == "nvidia/parakeet-tdt-0.6b-v3"

        import voice_typer.server.config as cfg_mod

        cache_dir = cfg_mod._config_dir() / "huggingface" / "hub"
        model_dir_name = f"models--{meta.repo_id.replace('/', '--')}"
        (cache_dir / model_dir_name).mkdir(parents=True)

        result = service.delete_model("parakeet")
        assert result["success"] is True, f"Expected success, got: {result}"
        assert not (cache_dir / model_dir_name).exists()

    def test_qwen_uses_registry_repo_id(self, tmp_config_dir):
        """``delete_model("qwen")`` no longer returns "Unknown model"
        — it derives ``Qwen/Qwen-Audio`` from the registry and either
        deletes the matching cache dir or returns "not downloaded"
        when the dir is absent."""
        from voice_typer.server.model_registry import get_model_metadata

        service = self._make_service()
        meta = get_model_metadata("qwen")
        assert meta is not None, "qwen must be in MODEL_REGISTRY"
        assert meta.repo_id == "Qwen/Qwen-Audio"

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
                {"asr_backend": "whisper", "model_size": "tiny.en"},
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
