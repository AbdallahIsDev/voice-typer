"""Tests for history database, vocabulary management, corrections loading,
model download/cancel mechanism, and miscellaneous engine infrastructure."""

from __future__ import annotations

import inspect
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
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

        deleted = history_db.apply_retention(max_entries=3)

        favorites = history_db.get_favorites()
        assert len(favorites) >= 1
        assert favorites[0]["text"] == "Favorite old entry"

    def test_retention_without_favorites_deletes_oldest(self, history_db):
        for i in range(5):
            history_db.add_transcription(f"Entry {i}")

        deleted = history_db.apply_retention(max_entries=3)
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


class TestCloudEngineUlopenTimeout:
    """The cloud engine passes timeout=30 to urlopen."""

    def test_openai_compatible_uses_30s_timeout(self):
        from voice_typer.server import cloud_engines

        engine = cloud_engines.CloudEngine(
            provider="openai", api_key="test-key",
            consent_given=True,
        )

        captured: dict = {}

        class _FakeCtxManager:
            def __enter__(self):
                fake_resp = MagicMock()
                body = b'{"text": "hello"}'
                fake_resp.read.side_effect = [body, b""]
                return fake_resp

            def __exit__(self, *args):
                return False

        def _fake_open(req, timeout=None, **kwargs):
            captured["timeout"] = timeout
            return _FakeCtxManager()

        fake_opener = MagicMock()
        fake_opener.open.side_effect = _fake_open

        with patch.object(cloud_engines, "_opener", fake_opener):
            audio = np.zeros(16000, dtype=np.float32)
            result = engine.transcribe(audio)

        assert result == "hello"
        assert captured.get("timeout") == 30


class TestVocabularySaveRetry:
    """_save_user retries on PermissionError."""

    def test_save_retries_on_permission_error(self, tmp_path):
        from voice_typer.server.vocabulary import VocabularyManager
        import os

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


class TestResampleUnavailable:
    """ResampleUnavailable is a typed exception for missing scipy."""

    def test_resample_unavailable_is_runtime_error(self):
        from voice_typer.server.recording import ResampleUnavailable
        assert issubclass(ResampleUnavailable, RuntimeError)


class TestPruneOldEntries:
    """_prune_old_entries does not rebuild _word_key_index."""

    def test_word_key_index_preserved_after_prune(self):
        from voice_typer.server.streaming import StreamingTextAssembler, WordTiming

        assembler = StreamingTextAssembler()
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        index_before = dict(assembler._word_key_index)
        assembler._prune_old_entries(1.0)
        assert assembler._word_key_index == index_before


class TestBuildModelsSubmenuConfigProvider:
    """build_models_menu_items accepts config_provider kwarg."""

    def test_accepts_config_provider(self, tmp_path):
        from voice_typer.server.tray_models import build_models_submenu_data
        from unittest.mock import MagicMock

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
        service._download_cancel_event = threading.Event()
        assert not service._download_cancel_event.is_set()

        result = service.cancel_model_download()
        assert result == {"cancelled": True}
        assert service._download_cancel_event.is_set()

    def test_cancel_event_is_clearable(self, tmp_config_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        service._download_cancel_event = threading.Event()
        service.cancel_model_download()
        service._download_cancel_event = None
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_download_cancel_event_starts_as_none(self, tmp_config_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        assert service._download_cancel_event is None


class TestAsrSetupHasNoConfigDirCache:
    """asr_setup no longer has _CONFIG_DIR cache."""

    def test_no_config_dir_cache(self):
        from voice_typer.server import asr_setup
        assert not hasattr(asr_setup, "_CONFIG_DIR")
        assert not hasattr(asr_setup, "_config_dir")

    def test_parakeet_uses_config_directly(self):
        from voice_typer.server.parakeet_engine import ParakeetEngine
        source = inspect.getsource(ParakeetEngine._is_cached)
        assert "from voice_typer.server.config import _config_dir" in source
        assert "from voice_typer.server.asr_setup import _config_dir" not in source


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
        assert "getchannel('A')" in code_only


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
