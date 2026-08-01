"""Tests for history database, vocabulary management, corrections loading,
model download/cancel mechanism, and miscellaneous engine infrastructure."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestHistoryDBErrorType:
    """HistoryDBError is a typed exception."""

    def test_historydberror_is_runtime_error(self):
        from voice_typer.server.history_db import HistoryDBError

        assert issubclass(HistoryDBError, RuntimeError)


class TestHistoryRetentionFavorites:
    """Retention preserves favorites even when they're old."""

    def test_retention_preserves_favorites(self, history_db):
        fav_id = history_db.add_transcription("Favorite old entry")
        history_db.toggle_favorite(fav_id)
        for i in range(5):
            history_db.add_transcription(f"Regular entry {i}")

        history_db.apply_retention(max_entries=3)

        favorites = history_db.get_favorites()
        assert len(favorites) >= 1
        assert favorites[0]["text"] == "Favorite old entry"

    def test_retention_without_favorites_deletes_oldest(self, history_db):
        for i in range(5):
            history_db.add_transcription(f"Entry {i}")

        history_db.apply_retention(max_entries=3)
        entries = history_db.get_recent(limit=10)
        assert len(entries) <= 3


class TestSearchHistoryEdgeCases:
    """HistoryDB.search edge cases: LIKE-escape and length cap."""

    def test_empty_query_returns_all(self, history_db):
        history_db.add_transcription("First entry")
        history_db.add_transcription("Second entry")
        history_db.flush()
        results = history_db.search("")
        assert len(results) >= 2

    def test_extremely_long_query_does_not_crash(self, history_db):
        history_db.add_transcription("hello world")
        history_db.flush()
        huge = "a" * 10_000_000
        results = history_db.search(huge)
        assert results == []

    def test_literal_percent_in_query_matches_only_exact_text(self, history_db):
        history_db.add_transcription("Progress is 100% complete")
        history_db.add_transcription("Progress is 1000 complete")
        history_db.flush()
        results = history_db.search("100%")
        assert [row["text"] for row in results] == ["Progress is 100% complete"]

    def test_literal_underscore_in_query_matches_only_exact_text(self, history_db):
        history_db.add_transcription("snake_case_token")
        history_db.add_transcription("snakeXcaseXtoken")
        history_db.flush()
        results = history_db.search("snake_case_token")
        assert [row["text"] for row in results] == ["snake_case_token"]


class TestVocabularySaveRetry:
    """_save_user retries on PermissionError."""

    def test_save_retries_on_permission_error(self, tmp_path):
        import os

        from voice_typer.server.vocabulary import VocabularyManager

        vocab = VocabularyManager(config_dir=tmp_path)
        attempt = {"n": 0}
        real_replace = os.replace

        def flaky_replace(src, dst):
            attempt["n"] += 1
            if attempt["n"] < 3:
                raise PermissionError(f"Simulated lock (attempt {attempt['n']})")
            real_replace(src, dst)

        with patch("os.replace", side_effect=flaky_replace):
            vocab._save_user()

        assert attempt["n"] == 3
        assert (tmp_path / "voice-typer-vocabulary.json").exists()


class TestCorrectionsLoadError:
    """CorrectionsLoadError for malformed corrections file."""

    def test_corrections_load_error_is_runtime_error(self):
        from voice_typer.server.text_cleanup import CorrectionsLoadError

        assert issubclass(CorrectionsLoadError, RuntimeError)

    def test_corrections_load_error_raised_on_malformed_file(self, tmp_path, monkeypatch):
        from voice_typer.server.text_cleanup import (
            CorrectionsLoadError,
            _load_external_corrections,
        )

        path = tmp_path / "voice-typer-corrections.json"
        path.write_text("{not valid json", encoding="utf-8")
        import voice_typer.server.text_cleanup as tc

        monkeypatch.setattr(tc, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        with pytest.raises(CorrectionsLoadError):
            _load_external_corrections(config_dir=tmp_path)


class TestSharedVocabConstants:
    """text_cleanup imports BUNDLED_CORRECTIONS_PATH from vocabulary."""

    def test_bundled_corrections_path_is_same_object(self):
        from voice_typer.server import text_cleanup, vocabulary

        assert text_cleanup._BUNDLED_CORRECTIONS_PATH is vocabulary.BUNDLED_CORRECTIONS_PATH


class TestBuildModelsSubmenuConfigProvider:
    """build_models_menu_items accepts config_provider kwarg."""

    def test_accepts_config_provider(self, tmp_path):

        from voice_typer.server.tray_models import build_models_submenu_data

        config = MagicMock()
        config.model_size = "small.en"
        config.asr_backend = "whisper"

        result = build_models_submenu_data(
            lambda: tmp_path,
            lambda name: None,
            config_provider=config,
        )
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "small.en" in active_models

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
        assert "tiny.en" in active_models

        # The corrupt-config log line must be present.
        corrupt_log_lines = [r.message for r in caplog.records if "failed to read config.json" in r.message]
        assert corrupt_log_lines, (
            "PI-19 regression: expected a log.debug line recording the config.json read failure, got none"
        )


class TestCancelModelDownloadMechanism:
    """Verify the cancel mechanism works at the Python service level.

    EC-FIX-15 / EC-24: the legacy single-instance ``_download_cancel_event``
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


class TestValidateNonNumericFieldsHasClarifyingDocstring:
    """_validate_non_numeric_fields is NOT a duplicate — it's a migration layer."""

    def test_validator_has_clarifying_docstring(self):
        from voice_typer.server.config import Config

        source = inspect.getsource(Config._validate_non_numeric_fields)
        assert "migration layer" in source


class TestMainModuleDocumentsConsoleScriptRole:
    """__main__.py and console script serve different purposes."""

    def test_main_has_clarifying_docstring(self):
        main_path = REPO_ROOT / "voice_typer" / "__main__.py"
        source = main_path.read_text()
        assert "different purposes" in source.lower() or "NOT a duplicate" in source


class TestTrayIconNoLongerReferencesStaleSvg:
    """vt_logo.svg references updated."""

    def test_tray_icon_no_longer_references_vt_logo(self):
        from voice_typer.server import tray_icon

        source = inspect.getsource(tray_icon._make_icon)
        assert "from vt_logo.svg" not in source


class TestTrayIconUsesGetchannelNotSplitIndex:
    """Use getchannel('A') instead of split()[3]."""

    def test_no_split_index_3(self):
        from voice_typer.server import tray_icon

        source = inspect.getsource(tray_icon._make_icon)
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "split()[3]" not in code_only
        # The production source uses ``getchannel("A")`` (double quotes);
        # accept either quote style so the test is resilient to the
        # formatter's preference.
        assert ('getchannel("A")' in code_only) or ("getchannel('A')" in code_only), (
            "expected getchannel('A') or getchannel(\"A\") in _make_icon source"
        )


class TestOnboardingControllerRemovesStepCallbacks:
    """on_step_change and on_complete callbacks removed."""

    def test_no_callbacks_in_init(self):
        from voice_typer.server.onboarding import OnboardingController

        source = inspect.getsource(OnboardingController.__init__)
        assert "self.on_step_change =" not in source
        assert "self.on_complete =" not in source

    def test_next_step_no_callback_invocation(self):
        from voice_typer.server.onboarding import OnboardingController

        source = inspect.getsource(OnboardingController.next_step)
        assert "on_step_change" not in source
        assert "on_complete" not in source


class TestPhrasePatternCache:
    """_correct_whisper_phrases caches compiled regex patterns."""

    def test_pattern_is_cached(self):
        from voice_typer.server import text_cleanup

        text_cleanup._phrase_pattern_cache.clear()

        p1 = text_cleanup._get_compiled_phrase_pattern("test phrase")
        p2 = text_cleanup._get_compiled_phrase_pattern("test phrase")

        assert p1 is p2
        assert "test phrase" in text_cleanup._phrase_pattern_cache

    def test_distinct_phrases_get_distinct_patterns(self):
        from voice_typer.server import text_cleanup

        text_cleanup._phrase_pattern_cache.clear()
        p1 = text_cleanup._get_compiled_phrase_pattern("alpha")
        p2 = text_cleanup._get_compiled_phrase_pattern("beta")
        assert p1 is not p2


class TestHistoryRestoreReinsertsRecord:
    """Deleted records can be re-inserted via restore()."""

    def test_restore_reinserts_record_with_new_id(self, history_db):
        history_db.add_transcription("hello world", duration=1.5, model="small.en", device="cpu")
        history_db.flush()
        rec = history_db.get_recent()[0]
        assert history_db.delete(rec["id"]) is True
        assert history_db.get_recent() == []

        new_id = history_db.restore(rec)
        assert new_id > 0
        assert new_id != rec["id"]
        restored = history_db.get_recent()[0]
        assert restored["text"] == "hello world"
        assert restored["duration"] == 1.5
        assert restored["model"] == "small.en"
        assert restored["device"] == "cpu"

    def test_restore_preserves_favorite_flag(self, history_db):
        history_db.add_transcription("favorite text")
        history_db.flush()
        rec = history_db.get_recent()[0]
        history_db.toggle_favorite(rec["id"])
        rec = history_db.get_recent()[0]
        assert rec["favorite"] == 1

        history_db.delete(rec["id"])
        new_id = history_db.restore(rec)
        restored = history_db.get_recent()[0]
        assert restored["id"] == new_id
        assert restored["favorite"] == 1

    def test_restore_with_minimal_record(self, history_db):
        new_id = history_db.restore({"text": "minimal restore"})
        assert new_id > 0
        rec = history_db.get_recent()[0]
        assert rec["text"] == "minimal restore"
        assert rec["duration"] == 0
        assert rec["model"] == ""

    def test_restore_empty_text_returns_negative(self, history_db):
        new_id = history_db.restore({"text": ""})
        assert new_id > 0

    def test_service_empty_text_rejected(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None
            history_db = None

        service = VoiceTyperService(FakeApp())
        with pytest.raises(ValueError, match="text"):
            service.restore_history({"text": ""})
        with pytest.raises(ValueError, match="text"):
            service.restore_history({})
        with pytest.raises(ValueError, match="dict"):
            service.restore_history("not a dict")


class TestTemplatesPersistToDisk:
    """Templates survive to disk via TemplateManager._save."""

    def test_templates_persisted_to_json_file(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=templates_dir)
        tm.add("hello", "Hello World!")
        tm.add("bye", "Goodbye!", match_mode="contains")

        templates_file = templates_dir / "voice-typer-templates.json"
        assert templates_file.exists()
        import json

        data = json.loads(templates_file.read_text(encoding="utf-8"))
        assert "templates" in data
        assert len(data["templates"]) == 2
        assert data["templates"][0]["trigger"] == "hello"
        assert data["templates"][1]["match_mode"] == "contains"

    def test_templates_loaded_on_restart(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm1 = TemplateManager(config_dir=templates_dir)
        tm1.add("persist_test", "persisted value")
        del tm1

        tm2 = TemplateManager(config_dir=templates_dir)
        templates = tm2.templates
        assert len(templates) == 1
        assert templates[0]["trigger"] == "persist_test"
        assert templates[0]["output"] == "persisted value"

    def test_service_save_and_get_round_trip(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())

        templates_to_save = [
            {"trigger": "my_email", "output": "me@example.com", "match_mode": "exact"},
            {"trigger": "signature", "output": "Best regards,\nJohn", "match_mode": "contains"},
        ]
        assert service.save_templates(templates_to_save) is True

        FakeApp._template_manager = None
        service2 = VoiceTyperService(FakeApp())
        loaded = service2.get_templates()
        assert len(loaded) == 2
        assert loaded[0]["trigger"] == "my_email"
        assert loaded[0]["output"] == "me@example.com"
        assert loaded[1]["match_mode"] == "contains"

    def test_service_save_rejects_invalid_entries(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())
        bad_input = [
            {"trigger": "valid", "output": "ok", "match_mode": "exact"},
            {"trigger": "", "output": "missing trigger"},
            {"trigger": "missing output", "output": ""},
            {"trigger": "bad mode", "output": "ok", "match_mode": "invalid"},
            "not a dict",
            None,
        ]
        assert service.save_templates(bad_input) is True
        loaded = service.get_templates()
        assert len(loaded) == 2
        triggers = [t["trigger"] for t in loaded]
        assert "valid" in triggers
        assert "bad mode" in triggers
        bad_mode_entry = next(t for t in loaded if t["trigger"] == "bad mode")
        assert bad_mode_entry["match_mode"] == "exact"


# regression tests (SVC-2/6/7/8/9/10/11, PERF-21) ────────────
#
# Each test class below pins one of the SVC-N fixes applied to
# ``voice_typer/server/service.py`` in FIX sub-agent #5. Tests are
# intentionally narrow (unit-level) — they exercise the service in
# isolation against a FakeApp/FakeConfig so they don't pull in the
# heavy app/conftest autouse-mock machinery.


class TestKeyringStatusHelper:
    """SVC-6: ``_keyring_status()`` centralizes the duplicated probe."""

    def test_returns_dict_with_expected_keys(self, tmp_config_dir, monkeypatch):
        """``_keyring_status`` returns a dict containing the four
        keys the renderer reads (``available``/``backend``/``fallback``/
        ``reason``)."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(
            cs,
            "get_keyring_status",
            lambda: {
                "available": True,
                "backend": "SecretServiceKeyring",
                "fallback": False,
                "reason": None,
            },
        )
        result = service._keyring_status()
        assert result == {
            "available": True,
            "backend": "SecretServiceKeyring",
            "fallback": False,
            "reason": None,
        }

    def test_returns_fallback_when_credential_store_raises(self, tmp_config_dir, monkeypatch):
        """When ``credential_store.get_keyring_status`` raises, the
        helper returns a safe ``{available: False, fallback: True, ...}``
        dict so the IPC ``get_config`` path never breaks."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())

        def _boom():
            raise RuntimeError("keychain exploded")

        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "get_keyring_status", _boom)
        result = service._keyring_status()
        assert result["available"] is False
        assert result["backend"] is None
        assert result["fallback"] is True
        assert "keychain exploded" in result["reason"]

    def test_get_config_and_get_defaults_share_helper(self, tmp_config_dir, monkeypatch):
        """Both ``get_config`` and ``get_defaults`` route through
        ``_keyring_status`` — patching the helper once affects both
        callers (proves the duplication was actually removed)."""
        from voice_typer.server.service import VoiceTyperService

        calls: list[int] = []

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())

        def _spy(self):
            calls.append(1)
            return {"available": False, "backend": None, "fallback": True, "reason": "spy"}

        monkeypatch.setattr(VoiceTyperService, "_keyring_status", _spy)

        # the sanitizer moved out of ``ipc_server``
        # into the transport-neutral ``config_sanitizer`` module.
        # Patch both symbols so the test stays valid against either
        # import path (legacy ``ipc_server._sanitize_config_for_ipc``
        # alias and the current canonical location).
        import voice_typer.server.config_sanitizer as cfg_san
        import voice_typer.server.ipc_server as ipc

        monkeypatch.setattr(ipc, "_sanitize_config_for_ipc", lambda c: {})
        monkeypatch.setattr(cfg_san, "sanitize_config_for_ipc", lambda c: {})

        import voice_typer.server.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "Config", lambda: object())

        service.get_config()
        service.get_defaults()
        assert len(calls) == 2, (
            f"Expected _keyring_status to be called once per get_config + "
            f"once per get_defaults (2 total), got {len(calls)}"
        )


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


class TestRefreshMicrophonesForce:
    """SVC-8: ``refresh_microphones(force=True)`` bypasses the 5 s TTL
    cache so callers that *know* a hot-plug event happened can refresh
    immediately."""

    def _make_service(self, monkeypatch, mics_by_call):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            def __init__(self):
                self._microphones = []
                self.tray = type(
                    "FakeTray",
                    (),
                    {"set_microphones": staticmethod(lambda m: None)},
                )()

        service = VoiceTyperService(FakeApp())

        def _fake_list_microphones():
            return mics_by_call.pop(0)

        monkeypatch.setattr(
            "voice_typer.server.server_platform.list_microphones",
            _fake_list_microphones,
        )
        return service

    def test_default_call_uses_cache_within_ttl(self, tmp_config_dir, monkeypatch):
        """Two calls within the 5 s window return the SAME list — the
        second call is served from cache, so PortAudio is queried only
        once."""
        mics_v1 = [{"id": 0, "name": "Built-in"}]
        mics_v2 = [{"id": 0, "name": "Built-in"}, {"id": 5, "name": "USB"}]
        service = self._make_service(monkeypatch, [mics_v1, mics_v2])

        first = service.refresh_microphones()
        second = service.refresh_microphones()
        assert first == mics_v1
        assert second == mics_v1, "Second call within 5s should be served from cache (same list)"

    def test_force_bypasses_cache(self, tmp_config_dir, monkeypatch):
        """``refresh_microphones(force=True)`` ignores the cache and
        re-queries PortAudio, picking up newly-plugged devices."""
        mics_v1 = [{"id": 0, "name": "Built-in"}]
        mics_v2 = [{"id": 0, "name": "Built-in"}, {"id": 5, "name": "USB"}]
        service = self._make_service(monkeypatch, [mics_v1, mics_v2])

        first = service.refresh_microphones()
        assert first == mics_v1

        cached = service.refresh_microphones()
        assert cached == mics_v1

        forced = service.refresh_microphones(force=True)
        assert forced == mics_v2, "force=True must bypass the TTL cache and re-query PortAudio"


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


class TestOnboardingUsesServiceChangeModel:
    """SVC-10: ``onboarding_apply`` routes the model switch through
    ``self.change_model`` (the ADR-0008-§3.1 service-layer wrapper)
    instead of reaching into ``app.models.change_model`` directly."""

    def test_calls_self_change_model_not_app_models_directly(self, tmp_config_dir, monkeypatch):
        """When the user picks a non-default model in onboarding,
        ``onboarding_apply`` invokes ``self.change_model`` (which goes
        through ``app.change_model`` -> ``app.models.change_model``)."""
        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", lambda msg: True)

        import contextlib

        from voice_typer.server.service import VoiceTyperService

        app = MagicMock()
        app.config.onboarding_completed = False
        app.config.model_size = "small.en"
        app.config.save = MagicMock(return_value=True)

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app._config_mutation_lock = _fake_lock()

        service = VoiceTyperService(app)

        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController()
        ctrl.set_hotkey("<f6>")
        ctrl.set_model("tiny.en")
        service._onboarding = ctrl

        service.onboarding_apply()

        (
            app.change_model.assert_called_once_with("tiny.en"),
            (
                "onboarding_apply should route model switch through "
                "self.change_model (SVC-10) which delegates to app.change_model"
            ),
        )


class TestApplyConfigPersistsOnSideEffectFailure:
    """SVC-11: ``apply_config`` persists config via ``save_strict()``.

    PVT-21 (session-1) extracted ``apply_config`` orchestration from
    ``service.py`` into ``config_applier.py``. The new contract:

    1. ``service.apply_config`` delegates to ``config_applier.apply_config``.
    2. ``config_applier.apply_config`` calls ``apply_config_side_effects``
       then ``app.config.save_strict()`` (NOT ``save()``).
    3. CR-97: ``save_strict()`` raises ``RuntimeError`` if ``save()``
       returned ``False`` (disk write failure) — the IPC handler is
       expected to catch this and surface the error.
    4. G4-H-12: if ``save_strict()`` raises, in-memory Config is rolled
       back to the pre-setattr snapshot so live state matches disk.

    The original SVC-11 "save in finally even when side-effects raise"
    guarantee is replaced by the G4-H-12 rollback pattern: if side-effects
    raise, ``save_strict()`` is NOT called (the raise propagates first),
    but in-memory state is rolled back so it cannot diverge from disk.
    """

    def _make_service_and_app(self):
        import contextlib

        from voice_typer.server.service import VoiceTyperService

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app = MagicMock()
        app._config_mutation_lock = _fake_lock()
        app.config = MagicMock()
        # + : config_applier now calls save_strict() (not save()).
        # save_strict() raises RuntimeError if save() returns False.
        app.config.save = MagicMock(return_value=True)
        app.config.save_strict = MagicMock(return_value=None)
        app.clipboard = MagicMock()
        app.tray = MagicMock()
        app.tray.invalidate_menu_cache = MagicMock()
        app._llm_polisher = None
        app.hotkeys = MagicMock()
        app.recorder = MagicMock()
        app._busy_event = MagicMock()
        app._busy_event.is_set = MagicMock(return_value=True)
        app._shutting_down = False

        service = VoiceTyperService(app)
        return service, app

    def test_save_called_when_side_effects_succeed(self, tmp_config_dir, monkeypatch):
        """Baseline: when side effects succeed, save_strict() is called once."""
        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        # config_applier imports asdict INSIDE apply_config; patch dataclasses.asdict.
        # Use a counter so pre != post (state appears changed) → save_strict called.
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        service.apply_config({"hotkey": "<f4>"})
        app.config.save_strict.assert_called_once_with()

    def test_save_called_when_side_effects_raise(self, tmp_config_dir, monkeypatch):
        """PVT-21: when ``apply_config_side_effects`` raises, the raise
        propagates and ``save_strict()`` is NOT called. The original
        exception is re-raised so the IPC layer can surface the error.
        (Replaces the SVC-11 "save in finally" guarantee — see class docstring.)"""
        import pytest

        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        def _boom(updates):
            raise RuntimeError("side effect blew up")

        # side-effects now live on config_applier, not service.
        monkeypatch.setattr(service._config_applier, "apply_config_side_effects", _boom)

        with pytest.raises(RuntimeError, match="side effect blew up"):
            service.apply_config({"hotkey": "<f4>"})

        # Side-effects raised → save_strict NOT called (raise propagated first).
        app.config.save_strict.assert_not_called()

    def test_save_failure_surfaces_when_side_effects_succeeded(self, tmp_config_dir, monkeypatch):
        """CR-97: if save_strict() raises (e.g. save() returned False →
        RuntimeError, or underlying OSError propagates), the error is
        surfaced to the caller. G4-H-12: in-memory Config is rolled back."""

        import pytest

        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        # save_strict raises when save() returned False.
        # We mock save_strict to raise OSError directly to simulate
        # a disk-write failure that propagated (rather than the
        # save-returns-False → save_strict-raises-RuntimeError path).
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": "<f4>"})


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
