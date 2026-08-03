"""History retention policy tests.

Extracted from the original ``tests/test_history_and_models.py`` catch-all
(Epic EC-25 / Entry #23 test-file split). This module pins the behavior of
:meth:`voice_typer.server.history_db.HistoryDB.apply_retention` — the
policy that trims the SQLite history table to a configured ``max_entries``
while preserving user-favorited rows even when they fall outside the
retention window.

Test names + assertions are preserved verbatim from the original file;
only the file boundary moved.
"""

from __future__ import annotations


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
