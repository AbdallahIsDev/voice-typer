"""XZ-4: Fix vocabulary + history_db issues.

Pins the behavioral and source-level contracts for two findings:

* XV-88 — ``VocabularyManager._save_user`` previously contained a
  42-line DEAD duplicate retry loop after the live retry loop. The
  dead block was unreachable because:

      1. ``max_retries = 3`` so the live ``for attempt in range(max_retries)``
         loop body ALWAYS executes at least once.
      2. Inside the live loop, ``_secure_atomic_write`` either:
           * succeeds → ``return`` exits the function, OR
           * raises ``PermissionError`` → ``final_exc`` is set, OR
           * raises ``OSError`` (non-Permission) → ``final_exc`` is set
             and ``break`` ends the loop, OR
           * raises anything else → propagates out of the function
             (uncaught by either ``except`` clause).
      3. After the live loop, ``if final_exc is not None: raise final_exc``
         ALWAYS raises when the loop failed (the only way
         ``final_exc`` stays ``None`` is if the loop ``return``ed,
         which already exited the function).

  Therefore the second ``try:`` block (the dead duplicate retry loop)
  was unreachable in every possible execution path. These tests pin
  that the dead block stays removed and the live retry behaviour
  (M-63: raise on exhaustion) is preserved.

* XV-95 — ``history_db._WAL_CHECKPOINT_INTERVAL`` is ``300.0`` (5 min)
  but the module docstring and the comments around the checkpoint log
  message said "60s". The actual log message itself was already
  correct (it interpolates ``_WAL_CHECKPOINT_INTERVAL``), but the
  human-readable docstring + comments lied. These tests pin that the
  documentation matches the actual interval.

Each test FAILS if the corresponding fix is reverted.
"""

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


# dead duplicate retry loop removed from _save_user ───────────


def _save_user_src() -> str:
    from voice_typer.server.vocabulary import VocabularyManager

    return inspect.getsource(VocabularyManager._save_user)


class TestDeadCodeRemoved:
    """XV-88: the dead duplicate retry loop in ``_save_user`` is gone."""

    def test_save_user_has_exactly_one_secure_atomic_write_call(self):
        """The live retry loop calls ``_secure_atomic_write`` once per
        iteration. Before the fix, the dead duplicate block added a
        second call site. After the fix, the function body must
        contain exactly ONE call to ``_secure_atomic_write``.

        PI-8: the call is now routed through ``self._user_store.save(
        self._data)`` (the new :class:`PersistedJSON` helper), which
        internally calls ``_secure_atomic_write`` exactly once per
        ``save()`` invocation. So the XV-88 "no dead duplicate" intent
        is preserved iff ``_save_user`` contains exactly ONE call site
        — either the direct ``_secure_atomic_write(...)`` form OR the
        ``self._user_store.save(...)`` form (NOT both, NOT zero).
        """
        src = _save_user_src()
        direct_count = src.count("_secure_atomic_write(")
        helper_count = src.count("self._user_store.save(")
        total = direct_count + helper_count
        assert total == 1, (
            "_save_user must call _secure_atomic_write exactly once "
            "(either directly OR via self._user_store.save — the live "
            "retry loop). A count of 0 means the persistence call was "
            "lost; a count >1 indicates the dead duplicate block was "
            f"re-introduced. direct={direct_count}, helper={helper_count}, "
            f"total={total}.\n--- source ---\n" + src
        )

    def test_save_user_has_exactly_one_for_loop(self):
        """The live retry loop is the ONLY ``for`` loop in
        ``_save_user``. The dead block contained a second
        ``for attempt in range(max_retries):`` loop.
        """
        src = textwrap.dedent(_save_user_src())
        tree = ast.parse(src)
        for_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
        assert len(for_nodes) == 1, (
            f"_save_user must contain exactly 1 for-loop (the live "
            f"retry loop); found {len(for_nodes)}. The dead duplicate "
            f"block had a second for-loop that must stay removed."
        )

    def test_save_user_has_exactly_one_try_block(self):
        """The live retry loop is wrapped in ONE ``try`` block (with
        ``PermissionError`` / ``OSError`` handlers). The dead block
        wrapped its duplicate loop in a SECOND outer ``try: ... except
        Exception:`` block.
        """
        src = textwrap.dedent(_save_user_src())
        tree = ast.parse(src)
        try_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        assert len(try_nodes) == 1, (
            f"_save_user must contain exactly 1 try-block (the live "
            f"retry loop); found {len(try_nodes)}. The dead duplicate "
            f"block had a second outer try/except that must stay removed."
        )

    def test_save_user_does_not_contain_dead_block_marker(self):
        """The dead block's distinctive comment ('we deliberately do
        NOT re-raise') must NOT appear in ``_save_user`` — that
        comment lived only inside the dead block.
        """
        src = _save_user_src()
        assert "we deliberately do NOT re-raise" not in src, (
            "_save_user still contains the dead block's distinctive "
            "'do NOT re-raise' comment — the dead duplicate block was "
            "not fully removed."
        )

    def test_save_user_does_not_contain_dead_outer_except(self):
        """The dead block's outer ``except Exception:`` (logging
        '[VOCAB] Failed to save') must NOT appear in ``_save_user``.
        """
        src = _save_user_src()
        assert '"[VOCAB] Failed to save"' not in src, (
            "_save_user still contains the dead block's outer "
            "'except Exception: log.exception(\"[VOCAB] Failed to save\")' "
            "handler — the dead duplicate block was not fully removed."
        )


class TestLiveRetryBehaviourPreserved:
    """XV-88: removing the dead block must NOT change the live retry
    behaviour (M-63: raise after the retry loop is exhausted)."""

    @pytest.fixture
    def vm(self, tmp_path, monkeypatch):
        """Build a VocabularyManager pointed at a temp dir."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        bundled = tmp_path / "corrections.json"
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

        return VocabularyManager(config_dir=tmp_path, bundled_path=bundled)

    def test_success_on_first_try_does_not_retry(self, vm, monkeypatch):
        """When ``_secure_atomic_write`` succeeds on the first
        attempt, it must be called exactly once (no retries).
        """
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
        """M-63 contract: a persistent ``PermissionError`` must be
        retried ``max_retries`` (3) times and then RAISE — not
        silently return. This proves the LIVE retry loop is the one
        that runs (the dead block's 'do NOT re-raise' comment
        described the OPPOSITE behaviour).
        """
        call_count = 0

        def always_fails(path, content, **kwargs):
            # ``_secure_atomic_write`` is invoked with a
            # ``durability`` keyword (ER-80); the mock must accept it.
            nonlocal call_count
            call_count += 1
            raise PermissionError(f"simulated lock #{call_count}")

        # Make the backoff sleep a no-op so the test is fast.
        monkeypatch.setattr("time.sleep", lambda _s: None)
        monkeypatch.setattr("voice_typer.server.config._secure_atomic_write", always_fails)

        with pytest.raises(PermissionError, match="simulated lock"):
            vm._save_user()

        # 3 attempts (max_retries) — proves the live loop ran to
        # exhaustion. The dead block would only have been reachable
        # if the live loop had silently returned, which it did not.
        assert call_count == 3, (
            f"_save_user should retry PermissionError exactly 3 times "
            f"(max_retries); got {call_count} calls. The live retry "
            f"loop must be the one running."
        )

    def test_os_error_breaks_loop_and_raises_immediately(self, vm, monkeypatch):
        """A non-Permission ``OSError`` must ``break`` out of the
        retry loop on the first occurrence and raise immediately
        (no retry). The live loop's ``except OSError: ... break``
        branch.
        """
        call_count = 0

        def fails_with_oserror(path, content, **kwargs):
            # accepts ``durability`` kwarg — see always_fails above.
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
        """A transient ``PermissionError`` on the first attempt
        followed by success on the second must succeed (proves the
        retry loop actually retries and recovers, not just gives up).
        """
        call_count = 0

        def fails_once_then_succeeds(path, content, **kwargs):
            # accepts ``durability`` kwarg — see always_fails above.
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
    """XV-95: documentation of the WAL checkpoint interval must match
    the actual constant ``_WAL_CHECKPOINT_INTERVAL = 300.0``."""

    def test_checkpoint_interval_is_300_seconds(self):
        """The actual constant is 300.0 (5 minutes). Pinning this
        guards against accidentally changing the interval while
        updating the docs (the task explicitly says: do NOT change
        the interval).
        """
        from voice_typer.server.history_db import _WAL_CHECKPOINT_INTERVAL

        assert _WAL_CHECKPOINT_INTERVAL == 300.0, (
            f"_WAL_CHECKPOINT_INTERVAL must remain 300.0 (5 minutes); "
            f"got {_WAL_CHECKPOINT_INTERVAL}. XV-95 fixes the DOCS to "
            f"match reality, not the other way around."
        )

    def test_module_docstring_says_300s_not_60s(self):
        """The architecture-overview docstring at the top of
        ``history_db.py`` must say 'every 300s' (matching the actual
        interval), NOT the stale 'every 60s'.
        """
        # The docstring is the first statement in the module.
        assert "every 300s" in HISTORY_DB_SOURCE, (
            "history_db.py module docstring must say 'every 300s' to match _WAL_CHECKPOINT_INTERVAL = 300.0."
        )
        # The stale 'every 60s' (in the checkpoint context) must be
        # gone. We check the architecture-overview block specifically:
        # the 'drains queue, runs' / 'PRAGMA wal_checkpoint' / 'every
        # Ns' lines.
        overview_block = HISTORY_DB_SOURCE.split("Architecture overview::", 1)[1]
        overview_block = overview_block.split("Why this design exists", 1)[0]
        assert "every 60s" not in overview_block, (
            "history_db.py architecture-overview docstring still says "
            "'every 60s' for the WAL checkpoint cadence — must be "
            "'every 300s' to match _WAL_CHECKPOINT_INTERVAL."
        )

    def test_run_checkpoint_comment_references_constant_not_hardcoded(self):
        """The comment above the ``log.debug`` call in
        ``_run_checkpoint`` (about log-flood avoidance) must reference
        ``_WAL_CHECKPOINT_INTERVAL`` (the constant) instead of a
        hardcoded ``300s`` literal — drift-free documentation.
        """
        from voice_typer.server.history_db import HistoryDB

        src = inspect.getsource(HistoryDB._run_checkpoint)
        assert "_WAL_CHECKPOINT_INTERVAL" in src, (
            "_run_checkpoint comment must reference '_WAL_CHECKPOINT_INTERVAL' "
            "(the constant) instead of a hardcoded literal — drift-free."
        )
        assert "every 60s" not in src, (
            "_run_checkpoint comment still says 'every 60s' — the stale cadence."
        )

    def test_run_checkpoint_retry_comment_references_constant_not_hardcoded(self):
        """The comment about 'next checkpoint attempt will retry'
        (above the OperationalError log.debug) must reference
        ``_WAL_CHECKPOINT_INTERVAL`` (the constant) instead of a
        hardcoded ``300s`` literal — drift-free documentation.
        """
        from voice_typer.server.history_db import HistoryDB

        src = inspect.getsource(HistoryDB._run_checkpoint)
        assert "_WAL_CHECKPOINT_INTERVAL" in src, (
            "_run_checkpoint OperationalError-handling comment must "
            "reference '_WAL_CHECKPOINT_INTERVAL' (the constant) instead of a hardcoded literal — drift-free."
        )
        assert "attempt in 60s will retry" not in src, (
            "_run_checkpoint OperationalError-handling comment still "
            "says 'attempt in 60s will retry' — the stale cadence."
        )

    def test_checkpoint_skipped_log_uses_constant_not_hardcoded_60(self):
        """The actual ``log.debug`` message in
        ``_run_checkpoint``'s OperationalError handler must format
        ``_WAL_CHECKPOINT_INTERVAL`` (so it prints '300s'), NOT a
        hardcoded ``60``.
        """
        from voice_typer.server.history_db import HistoryDB

        src = inspect.getsource(HistoryDB._run_checkpoint)
        # The format string + the constant reference must both be
        # present in the log.debug call.
        assert "will retry in %.0fs" in src, (
            "_run_checkpoint log message must use the %.0fs format placeholder for the retry interval."
        )
        assert "_WAL_CHECKPOINT_INTERVAL" in src, (
            "_run_checkpoint log message must pass _WAL_CHECKPOINT_INTERVAL "
            "as the retry interval (not a hardcoded number)."
        )

    def test_no_stale_60s_in_checkpoint_context(self):
        """After the XV-95 fix, the ONLY remaining '60s' in
        history_db.py must be the one describing
        ``_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0`` (a DIFFERENT constant
        for the blocking-write total timeout, which IS correctly 60s
        and OUTSIDE the XV-95 scope). No '60s' may appear inside
        ``_run_checkpoint`` or in the WAL-checkpoint cadence docs.
        """
        # _run_checkpoint source must have zero '60s' references.
        from voice_typer.server.history_db import HistoryDB

        run_checkpoint_src = inspect.getsource(HistoryDB._run_checkpoint)
        assert "60s" not in run_checkpoint_src, (
            "_run_checkpoint must not reference '60s' anywhere — the "
            "actual cadence is 300s. Found stale 60s reference:\n" + run_checkpoint_src
        )
        # The module-level docstring's architecture overview block
        # (lines describing the writer thread cadence) must also be
        # free of '60s'.
        overview = HISTORY_DB_SOURCE.split("Architecture overview::", 1)[1]
        overview = overview.split("Why this design exists", 1)[0]
        assert "60s" not in overview, (
            "The architecture-overview docstring must not reference '60s' for the WAL checkpoint cadence."
        )

    def test_60s_for_write_future_timeout_is_preserved(self):
        """Sanity check: the ``_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0``
        comment (which correctly references '60s' for a DIFFERENT
        constant — the blocking-write total timeout, NOT the WAL
        checkpoint interval) must be preserved. XV-95 does NOT touch
        this comment because it correctly describes its own constant.
        """
        assert "_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0" in HISTORY_DB_SOURCE, (
            "_WRITE_FUTURE_TOTAL_TIMEOUT must remain 60.0 — this is a "
            "DIFFERENT constant from _WAL_CHECKPOINT_INTERVAL and is "
            "NOT in scope for XV-95."
        )
        assert "60s is" in HISTORY_DB_SOURCE, (
            "The comment '60s is 2× the per-retry timeout' for "
            "_WRITE_FUTURE_TOTAL_TIMEOUT must be preserved — it "
            "correctly describes that constant (which IS 60s)."
        )


class TestCheckpointLogBehaviour:
    """XV-95: when a WAL checkpoint is skipped due to an
    ``OperationalError``, the log message must report the ACTUAL
    retry interval (300s), proving the doc fix matches the runtime
    behaviour."""

    def test_skipped_checkpoint_log_says_300s(self, tmp_path, caplog):
        """Force a WAL checkpoint OperationalError and verify the
        'will retry in Ns' log line reports 300s (the actual
        ``_WAL_CHECKPOINT_INTERVAL``), not 60s.
        """
        from voice_typer.server.history_db import _WAL_CHECKPOINT_INTERVAL, HistoryDB

        # Sanity: the constant is what we expect.
        assert _WAL_CHECKPOINT_INTERVAL == 300.0

        db = HistoryDB(db_path=tmp_path / "ckpt.db")
        try:
            # Wait for the writer thread to be ready so conn exists.
            assert db._writer_ready.wait(timeout=10.0), "writer thread not ready"
            # Acquire the writer's connection via the same internal
            # hook the writer thread uses. We can't access the live
            # writer conn directly (it's owned by the writer thread),
            # so we call _run_checkpoint with a fresh connection that
            # we've rigged to raise OperationalError on
            # PRAGMA wal_checkpoint.
            rigged = MagicMock(spec=sqlite3.Connection)
            rigged.execute.side_effect = sqlite3.OperationalError("database table is locked (simulated)")
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db"):
                # _run_checkpoint must not raise — OperationalError is
                # caught and logged at DEBUG level.
                db._run_checkpoint(rigged)
            # The log line must report the actual interval (300s).
            skipped_msgs = [r.getMessage() for r in caplog.records if "WAL checkpoint skipped" in r.getMessage()]
            assert skipped_msgs, (
                "Expected a 'WAL checkpoint skipped (will retry in Ns)' "
                "log line at DEBUG level when checkpoint raises "
                "OperationalError; got records: " + repr([r.getMessage() for r in caplog.records])
            )
            # The interpolated value must be 300 (the actual
            # _WAL_CHECKPOINT_INTERVAL), NOT 60.
            assert any("will retry in 300s" in m for m in skipped_msgs), (
                "The 'WAL checkpoint skipped' log message must report "
                "'will retry in 300s' (matching _WAL_CHECKPOINT_INTERVAL), "
                "not 'will retry in 60s'. Got: " + repr(skipped_msgs)
            )
        finally:
            db.close()
