"""record (with new id, preserving favorite flag and metadata)."""

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
        from voice_typer.server.service import LausuService

        class FakeApp:
            _template_manager = None
            history_db = None

        service = LausuService(FakeApp())
        with pytest.raises(ValueError, match="text"):
            service.restore_history({"text": ""})
        with pytest.raises(ValueError, match="text"):
            service.restore_history({})
        with pytest.raises(ValueError, match="dict"):
            service.restore_history("not a dict")
