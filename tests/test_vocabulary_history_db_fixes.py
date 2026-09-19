"""XZ-4: Fix vocabulary + history_db issues."""

from __future__ import annotations

import ast
import inspect
import json
import logging
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

VOCAB_SOURCE = Path(inspect.getsourcefile(__import__("voice_typer.server.vocabulary", fromlist=["x"]))).read_text()
HISTORY_DB_SOURCE = Path(inspect.getsourcefile(__import__("voice_typer.server.history_db", fromlist=["x"]))).read_text()


def _save_user_src() -> str:
    from voice_typer.server.vocabulary import VocabularyManager

    return inspect.getsource(VocabularyManager._save_user)


class TestDeadCodeRemoved:
    """XV-88: the dead duplicate retry loop in ``_save_user`` is gone."""

    def test_save_user_has_exactly_one_secure_atomic_write_call(self):
        """The live retry loop calls ``_secure_atomic_write`` once per"""
        src = _save_user_src()
        direct_count = src.count("_secure_atomic_write(")
        helper_count = src.count("self._user_store.save(")
        total = direct_count + helper_count
        assert total == 1, (
            "_save_user must call _secure_atomic_write exactly once "
            "(either directly OR via self._user_store.save, the live "
            "retry loop). A count of 0 means the persistence call was "
            "lost; a count >1 indicates the dead duplicate block was "
            f"re-introduced. direct={direct_count}, helper={helper_count}, "
            f"total={total}.\n--- source ---\n" + src
        )

    def test_save_user_has_exactly_one_for_loop(self):
        """``_save_user``. The dead block contained a second"""
        src = textwrap.dedent(_save_user_src())
        tree = ast.parse(src)
        for_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
        assert len(for_nodes) == 1, (
            f"_save_user must contain exactly 1 for-loop (the live "
            f"retry loop); found {len(for_nodes)}. The dead duplicate "
            f"block had a second for-loop that must stay removed."
        )

    def test_save_user_has_exactly_one_try_block(self):
        """``PermissionError`` / ``OSError`` handlers). The dead block"""
        src = textwrap.dedent(_save_user_src())
        tree = ast.parse(src)
        try_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        assert len(try_nodes) == 1, (
            f"_save_user must contain exactly 1 try-block (the live "
            f"retry loop); found {len(try_nodes)}. The dead duplicate "
            f"block had a second outer try/except that must stay removed."
        )

    def test_save_user_does_not_contain_dead_block_marker(self):
        """
        The dead block's distinctive comment ('we deliberately do
        NOT re-raise') must NOT appear in ``_save_user``, that
        """
        src = _save_user_src()
        assert "we deliberately do NOT re-raise" not in src, (
            "_save_user still contains the dead block's distinctive "
            "'do NOT re-raise' comment, the dead duplicate block was "
            "not fully removed."
        )

    def test_save_user_does_not_contain_dead_outer_except(self):
        """The dead block's outer ``except Exception:`` (logging"""
        src = _save_user_src()
        assert '"[VOCAB] Failed to save"' not in src, (
            "_save_user still contains the dead block's outer "
            "'except Exception: log.exception(\"[VOCAB] Failed to save\")' "
            "handler, the dead duplicate block was not fully removed."
        )


class TestLiveRetryBehaviourPreserved:
    """XV-88: removing the dead block must NOT change the live retry"""

    @pytest.fixture
    def vm(self, tmp_config_dir):
        """Build a VocabularyManager pointed at a temp dir."""
        bundled = tmp_config_dir / "corrections.json"
        bundled.write_text(
            json.dumps(
                {
                    "misspellings": {"teh": "the"},
                    "phrase_corrections": [],
                    "extra_word_patterns": [],
                    "technical_terms": {},
                    "names": {},
                    "products": {},
                }
            ),
            encoding="utf-8",
        )
        from voice_typer.server.vocabulary import VocabularyManager

        return VocabularyManager(config_dir=tmp_config_dir, bundled_path=bundled)

    def test_success_on_first_try_does_not_retry(self, vm, monkeypatch):
        """When ``_secure_atomic_write`` succeeds on the first"""
        call_count = 0

        def fake_write(path, content, *, durability=True):
            nonlocal call_count
            call_count += 1
            path.write_text(content, encoding="utf-8")

        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", fake_write)
        # Should not raise.
        vm._save_user()
        assert call_count == 1, (
            f"_save_user should call _secure_atomic_write exactly once on first-try success; got {call_count} calls."
        )

    def test_permission_error_is_retried_then_raised(self, vm, monkeypatch):
        """M-63 contract: a persistent ``PermissionError`` must be"""
        call_count = 0

        def always_fails(path, content, **kwargs):
            nonlocal call_count
            call_count += 1
            raise PermissionError(f"simulated lock #{call_count}")

        # Make the backoff sleep a no-op so the test is fast.
        monkeypatch.setattr("time.sleep", lambda _s: None)
        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", always_fails)

        with pytest.raises(PermissionError, match="simulated lock"):
            vm._save_user()

        assert call_count == 3, (
            f"_save_user should retry PermissionError exactly 3 times "
            f"(max_retries); got {call_count} calls. The live retry "
            f"loop must be the one running."
        )

    def test_os_error_breaks_loop_and_raises_immediately(self, vm, monkeypatch):
        """retry loop on the first occurrence and raise immediately"""
        call_count = 0

        def fails_with_oserror(path, content, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OSError("disk full (simulated)")

        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", fails_with_oserror)

        with pytest.raises(OSError, match="disk full"):
            vm._save_user()

        assert call_count == 1, (
            f"_save_user should NOT retry a non-Permission OSError; got {call_count} calls (expected 1)."
        )

    def test_permission_error_then_success_retries_and_succeeds(self, vm, monkeypatch):
        """A transient ``PermissionError`` on the first attempt"""
        call_count = 0

        def fails_once_then_succeeds(path, content, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise PermissionError("transient lock")
            path.write_text(content, encoding="utf-8")

        monkeypatch.setattr("time.sleep", lambda _s: None)
        monkeypatch.setattr(
            "voice_typer.server.config._secure_atomic_write",
            fails_once_then_succeeds,
        )

        vm._save_user()  # must not raise
        assert call_count == 2, (
            f"_save_user should retry once after a transient PermissionError then succeed; got {call_count} calls."
        )


# WAL checkpoint docstring/log says 300s, matching the actual interval ──


class TestCheckpointIntervalDocs:
    """XV-95: documentation of the WAL checkpoint interval must match"""

    def test_checkpoint_interval_is_300_seconds(self):
        """The actual constant is 300.0 (5 minutes). Pinning this"""
        from voice_typer.server.history_db import _WAL_CHECKPOINT_INTERVAL

        assert _WAL_CHECKPOINT_INTERVAL == 300.0, (
            f"_WAL_CHECKPOINT_INTERVAL must remain 300.0 (5 minutes); "
            f"got {_WAL_CHECKPOINT_INTERVAL}. XV-95 fixes the DOCS to "
            f"match reality, not the other way around."
        )

    def test_module_docstring_says_300s_not_60s(self):
        """``history_db.py`` must say 'every 300s' (matching the actual"""
        # The docstring is the first statement in the module.
        assert "every 300s" in HISTORY_DB_SOURCE, (
            "history_db.py module docstring must say 'every 300s' to match _WAL_CHECKPOINT_INTERVAL = 300.0."
        )
        # The stale 'every 60s' (in the checkpoint context) must be
        overview_block = HISTORY_DB_SOURCE.split("Architecture overview::", 1)[1]
        overview_block = overview_block.split("Why this design exists", 1)[0]
        assert "every 60s" not in overview_block, (
            "history_db.py architecture-overview docstring still says "
            "'every 60s' for the WAL checkpoint cadence, must be "
            "'every 300s' to match _WAL_CHECKPOINT_INTERVAL."
        )

    def test_run_checkpoint_comment_references_constant_not_hardcoded(self):
        """``_run_checkpoint`` (about log-flood avoidance) must reference"""
        from voice_typer.server.history_db_internals.writer import _run_checkpoint as _run_checkpoint_impl

        src = inspect.getsource(_run_checkpoint_impl)
        assert "_WAL_CHECKPOINT_INTERVAL" in src, (
            "_run_checkpoint comment must reference '_WAL_CHECKPOINT_INTERVAL' "
            "(the constant) instead of a hardcoded literal, drift-free."
        )
        assert "every 60s" not in src, "_run_checkpoint comment still says 'every 60s', the stale cadence."

    def test_run_checkpoint_retry_comment_references_constant_not_hardcoded(self):
        """The comment about 'next checkpoint attempt will retry'"""
        from voice_typer.server.history_db_internals.writer import _run_checkpoint as _run_checkpoint_impl

        src = inspect.getsource(_run_checkpoint_impl)
        assert "_WAL_CHECKPOINT_INTERVAL" in src, (
            "_run_checkpoint OperationalError-handling comment must "
            "reference '_WAL_CHECKPOINT_INTERVAL' (the constant) instead of a hardcoded literal, drift-free."
        )
        assert "attempt in 60s will retry" not in src, (
            "_run_checkpoint OperationalError-handling comment still "
            "says 'attempt in 60s will retry', the stale cadence."
        )

    def test_checkpoint_skipped_log_uses_constant_not_hardcoded_60(self):
        """``_run_checkpoint``'s OperationalError handler must format"""
        from voice_typer.server.history_db_internals.writer import _run_checkpoint as _run_checkpoint_impl

        src = inspect.getsource(_run_checkpoint_impl)
        # The format string + the constant reference must both be
        assert "will retry in %.0fs" in src, (
            "_run_checkpoint log message must use the %.0fs format placeholder for the retry interval."
        )
        assert "_WAL_CHECKPOINT_INTERVAL" in src, (
            "_run_checkpoint log message must pass _WAL_CHECKPOINT_INTERVAL "
            "as the retry interval (not a hardcoded number)."
        )

    def test_no_stale_60s_in_checkpoint_context(self):
        """history_db.py must be the one describing"""
        # _run_checkpoint source must have zero '60s' references.
        from voice_typer.server.history_db_internals.writer import _run_checkpoint as _run_checkpoint_impl

        run_checkpoint_src = inspect.getsource(_run_checkpoint_impl)
        assert "60s" not in run_checkpoint_src, (
            "_run_checkpoint must not reference '60s' anywhere, the "
            "actual cadence is 300s. Found stale 60s reference:\n" + run_checkpoint_src
        )
        # The module-level docstring's architecture overview block
        overview = HISTORY_DB_SOURCE.split("Architecture overview::", 1)[1]
        overview = overview.split("Why this design exists", 1)[0]
        assert "60s" not in overview, (
            "The architecture-overview docstring must not reference '60s' for the WAL checkpoint cadence."
        )

    def test_60s_for_write_future_timeout_is_preserved(self):
        """Sanity check: the ``_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0``"""
        assert "_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0" in HISTORY_DB_SOURCE, (
            "_WRITE_FUTURE_TOTAL_TIMEOUT must remain 60.0, this is a "
            "DIFFERENT constant from _WAL_CHECKPOINT_INTERVAL and is "
            "NOT in scope for XV-95."
        )
        assert "60s is" in HISTORY_DB_SOURCE, (
            "The comment '60s is 2× the per-retry timeout' for "
            "_WRITE_FUTURE_TOTAL_TIMEOUT must be preserved, it "
            "correctly describes that constant (which IS 60s)."
        )


class TestCheckpointLogBehaviour:
    """``OperationalError``, the log message must report the ACTUAL"""

    def test_skipped_checkpoint_log_says_300s(self, tmp_path, caplog):
        """'will retry in Ns' log line reports 300s (the actual"""
        from voice_typer.server.history_db import _WAL_CHECKPOINT_INTERVAL, HistoryDB

        # Sanity: the constant is what we expect.
        assert _WAL_CHECKPOINT_INTERVAL == 300.0

        db = HistoryDB(db_path=tmp_path / "ckpt.db")
        try:
            # Wait for the writer thread to be ready so conn exists.
            assert db._writer_ready.wait(timeout=10.0), "writer thread not ready"
            # Acquire the writer's connection via the same internal
            rigged = MagicMock(spec=sqlite3.Connection)
            rigged.execute.side_effect = sqlite3.OperationalError("database table is locked (simulated)")
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db_internals.writer"):
                # _run_checkpoint must not raise, OperationalError is
                db._run_checkpoint(rigged)
            # The log line must report the actual interval (300s).
            skipped_msgs = [r.getMessage() for r in caplog.records if "WAL checkpoint skipped" in r.getMessage()]
            assert skipped_msgs, (
                "Expected a 'WAL checkpoint skipped (will retry in Ns)' "
                "log line at DEBUG level when checkpoint raises "
                "OperationalError; got records: " + repr([r.getMessage() for r in caplog.records])
            )
            # The interpolated value must be 300 (the actual
            assert any("will retry in 300s" in m for m in skipped_msgs), (
                "The 'WAL checkpoint skipped' log message must report "
                "'will retry in 300s' (matching _WAL_CHECKPOINT_INTERVAL), "
                "not 'will retry in 60s'. Got: " + repr(skipped_msgs)
            )
        finally:
            db.close()
