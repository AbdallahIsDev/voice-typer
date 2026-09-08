"""The ADR-0010 6.2 read-after-write guarantee at the repaste boundary.

``_store_result`` enqueues the history row without a blocking flush
(the paste path carries zero DB-durability latency — the writer
commits in the background). The guarantee that ``repaste_last`` reads
the COMMITTED row therefore lives in ``app_undo.repaste_last``: it
must call ``history_db.flush()`` BEFORE ``get_latest_text()``. A
raising flush must degrade to the in-memory fallback (never a crash,
never a stall).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.app_undo import UndoRepasteController


def _make_app(latest_text: str = "hello world") -> MagicMock:
    app = MagicMock()
    app.history_db.get_latest_text.return_value = latest_text
    app._last_transcription = "memory fallback"
    return app


class TestRepasteFlushBoundary:
    def test_flush_precedes_db_read(self) -> None:
        app = _make_app()
        UndoRepasteController(app).repaste_last()
        calls = [c[0] for c in app.history_db.method_calls]
        assert "flush" in calls
        assert calls.index("flush") < calls.index("get_latest_text")

    def test_pasted_text_comes_from_committed_db(self) -> None:
        app = _make_app()
        UndoRepasteController(app).repaste_last()
        # The copy source is the DB read (committed via the flush), not
        # the in-memory fallback.
        app.clipboard.copy.assert_called_once_with("hello world")

    def test_raising_flush_falls_back_to_memory(self) -> None:
        app = _make_app()
        app.history_db.flush.side_effect = RuntimeError("db broken")
        UndoRepasteController(app).repaste_last()
        # A broken flush degrades to the in-memory copy — repaste never
        # crashes and never stalls on the durability wait.
        app.clipboard.copy.assert_called_once_with("memory fallback")

    def test_raising_read_falls_back_to_memory(self) -> None:
        app = _make_app()
        app.history_db.get_latest_text.side_effect = RuntimeError("read broken")
        UndoRepasteController(app).repaste_last()
        app.clipboard.copy.assert_called_once_with("memory fallback")
