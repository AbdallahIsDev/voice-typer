"""HistoryDB restore() tests split out of the former ``tests/test_history_and_models.py``.

Domain: history database — soft-delete + restore() reinserts a
record (with new id, preserving favorite flag and metadata).

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed. The shared
``history_db`` and ``templates_dir`` fixtures (temporary SQLite file
+ tmp config dir) are provided by the top-level ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest


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
