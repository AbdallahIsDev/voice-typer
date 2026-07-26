"""TY-8: tests for the text-projection + on-demand full-text accessors.

Verifies that ``get_recent`` / ``search`` / ``get_favorites`` return a
500-char ``text`` preview plus ``text_truncated`` / ``text_full_length``
fields, and that ``get_transcription_text(id)`` returns the FULL text
for a single row.

The 500-char projection keeps list responses under the 1 MiB WS frame
cap (``sidecar_ws._MAX_FRAME_BYTES``). Without it, ~50 long-form
dictations with ~10KB text each exceeded the cap and the response was
SILENTLY DROPPED by the Tauri WS layer — the Dashboard's "Total
Dictations" stat never updated.

Test plan (from the TY-FIX-G task spec):
  (a) insert 10 dictations with 2KB text each,
  (b) call ``get_recent(limit=10)``,
  (c) verify each row has ``text_truncated=True``,
      ``text_full_length=2048``, ``text`` is 500 chars,
  (d) call ``get_transcription_text(id)`` for one row,
      verify full 2KB text returned.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history_projection.db")
    yield db_instance
    db_instance.close()


def _make_long_text(size: int = 2048) -> str:
    """Build a deterministic ``size``-char string for projection tests.

    Uses a repeating pattern so we can verify the SUBSTR projection
    returns exactly the first 500 chars (not just *any* 500 chars).
    """
    base = "abcdefghijklmnopqrstuvwxyz0123456789"
    repeats = (size // len(base)) + 1
    return (base * repeats)[:size]


class TestTextProjection:
    """``get_recent`` / ``search`` / ``get_favorites`` return projected text."""

    def test_get_recent_truncates_long_text_to_500_chars(self, db):
        """TY-8: 10 dictations with 2KB text each → all rows have
        ``text_truncated=True``, ``text_full_length=2048``, and
        ``text`` is exactly 500 chars (the SUBSTR preview)."""
        long_text = _make_long_text(2048)
        for _ in range(10):
            db.add_transcription(long_text)
        db.flush()

        rows = db.get_recent(limit=10)
        assert len(rows) == 10
        for row in rows:
            assert row["text_truncated"] is True, (
                f"expected text_truncated=True for row id={row['id']}, "
                f"got {row.get('text_truncated')!r}"
            )
            assert row["text_full_length"] == 2048, (
                f"expected text_full_length=2048 for row id={row['id']}, "
                f"got {row.get('text_full_length')!r}"
            )
            assert len(row["text"]) == 500, (
                f"expected len(text)==500 for row id={row['id']}, "
                f"got len={len(row['text'])}"
            )
            # The preview is the first 500 chars of the long text.
            assert row["text"] == long_text[:500]

    def test_get_recent_does_not_truncate_short_text(self, db):
        """TY-8: rows with text <= 500 chars have ``text_truncated=False``
        and ``text_full_length`` matches the actual length."""
        db.add_transcription("short text")
        db.flush()

        rows = db.get_recent(limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["text_truncated"] is False
        assert row["text_full_length"] == len("short text")
        assert row["text"] == "short text"

    def test_get_recent_text_field_preserved_for_backward_compat(self, db):
        """TY-8: existing callers that read ``row["text"]`` must still work.

        The ``text`` field is preserved (as the preview) so legacy
        callers see a shorter string but don't break.
        """
        long_text = _make_long_text(2048)
        db.add_transcription(long_text)
        db.flush()

        rows = db.get_recent(limit=10)
        assert len(rows) == 1
        # The legacy field is present and is a string.
        assert isinstance(rows[0]["text"], str)
        # It's the preview, not the full text.
        assert len(rows[0]["text"]) == 500

    def test_search_truncates_long_text(self, db):
        """TY-8: ``search`` also applies the 500-char text projection."""
        long_text = "findme " + _make_long_text(2048)
        db.add_transcription(long_text)
        db.add_transcription("short findme match")
        db.flush()

        rows = db.search("findme", limit=10)
        assert len(rows) >= 1
        # At least one row has truncated text.
        long_rows = [r for r in rows if r["text_full_length"] > 500]
        assert len(long_rows) >= 1
        for row in long_rows:
            assert row["text_truncated"] is True
            assert row["text_full_length"] == len(long_text)
            assert len(row["text"]) == 500

    def test_search_short_text_not_truncated(self, db):
        """TY-8: ``search`` does not flag short rows as truncated."""
        db.add_transcription("hello world")
        db.flush()

        rows = db.search("hello", limit=10)
        assert len(rows) == 1
        assert rows[0]["text_truncated"] is False
        assert rows[0]["text_full_length"] == len("hello world")

    def test_get_favorites_truncates_long_text(self, db):
        """TY-8: ``get_favorites`` also applies the 500-char text projection."""
        long_text = _make_long_text(2048)
        db.add_transcription(long_text)
        db.flush()
        # Favorite the row via toggle.
        rows = db.get_recent(limit=1)
        assert len(rows) == 1
        rec_id = rows[0]["id"]
        assert db.toggle_favorite(rec_id) is True

        fav_rows = db.get_favorites(limit=10)
        assert len(fav_rows) == 1
        row = fav_rows[0]
        assert row["text_truncated"] is True
        assert row["text_full_length"] == 2048
        assert len(row["text"]) == 500
        assert row["text"] == long_text[:500]


class TestGetTranscriptionText:
    """``get_transcription_text(id)`` returns the FULL text for one row."""

    def test_returns_full_text_for_long_row(self, db):
        """TY-8 (d): for a 2KB-text row, ``get_transcription_text(id)``
        returns the full 2KB text (not the 500-char preview)."""
        long_text = _make_long_text(2048)
        db.add_transcription(long_text)
        db.flush()
        rows = db.get_recent(limit=1)
        assert len(rows) == 1
        rec_id = rows[0]["id"]

        result = db.get_transcription_text(rec_id)
        assert result["id"] == rec_id
        assert result["text"] == long_text
        assert len(result["text"]) == 2048

    def test_returns_full_text_for_short_row(self, db):
        """TY-8: ``get_transcription_text`` returns the full (short) text."""
        db.add_transcription("short text")
        db.flush()
        rows = db.get_recent(limit=1)
        rec_id = rows[0]["id"]

        result = db.get_transcription_text(rec_id)
        assert result["id"] == rec_id
        assert result["text"] == "short text"

    def test_returns_empty_text_for_missing_id(self, db):
        """TY-8: a non-existent id returns ``{"id": id, "text": ""}`` —
        the sentinel preserves the success-shape contract from ERR-013."""
        result = db.get_transcription_text(99999)
        assert result == {"id": 99999, "text": ""}

    def test_full_text_matches_preview_prefix(self, db):
        """TY-8: the first 500 chars of the full text equal the
        preview returned by ``get_recent`` — no data was lost."""
        long_text = _make_long_text(2048)
        db.add_transcription(long_text)
        db.flush()
        rows = db.get_recent(limit=1)
        rec_id = rows[0]["id"]

        full = db.get_transcription_text(rec_id)
        assert full["text"][:500] == rows[0]["text"]
        assert len(full["text"]) == rows[0]["text_full_length"]
