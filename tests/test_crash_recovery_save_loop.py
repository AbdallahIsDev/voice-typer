"""Regression tests for ``CrashRecovery._save_loop`` exception handling.

The save-loop worker wraps its body in a two-clause ``try/except``:

1. A propagation clause for interpreter-shutdown signals
   (``KeyboardInterrupt``, ``SystemExit``, ``GeneratorExit``) so the
   worker dies cleanly when the process is exiting.
2. A log-and-continue clause for ordinary ``Exception`` subclasses so
   a transient failure (e.g. a ``RuntimeError`` from a corrupted
   queue, or a ``ValueError`` escaping ``_save_sync``) does NOT kill
   the worker — it logs at ERROR and keeps draining the queue.

Pre-fix, clause (1) was written as ``except BaseException: raise``.
Because every ``Exception`` is also a ``BaseException``, clause (1)
matched everything and clause (2) was unreachable dead code. Any
regular exception that escaped ``_save_sync`` (or any other line in
the loop body) was re-raised, killing the worker thread silently;
subsequent ``add()`` calls enqueued saves that were never drained,
and only the shutdown / atexit save path could persist anything.

Post-fix, clause (1) is restricted to the explicit tuple
``(KeyboardInterrupt, SystemExit, GeneratorExit)`` so ordinary
``Exception`` subclasses fall through to clause (2).

This module also covers the sibling fix in ``CrashRecovery.__del__``:
the "``__del__`` must never raise" contract is now honored for
``BaseException`` subclasses (not just ``Exception``), so a
``KeyboardInterrupt`` raised during interpreter shutdown while
``__del__`` is mid-save is swallowed instead of propagating out of GC.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest import mock

import pytest

LOGGER_NAME = "voice_typer.server.crash_recovery"


@pytest.fixture
def recovery_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point config to a temp directory so the recovery file lands in tmp."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def cr(recovery_dir: Path):
    """Create a CrashRecovery instance with a temp dir + tear it down."""
    from voice_typer.server.crash_recovery import CrashRecovery

    inst = CrashRecovery(config_dir=recovery_dir)
    yield inst
    inst.shutdown()
    if inst._save_thread is not None:
        inst._save_thread.join(timeout=2.0)


# ─── save-loop: regular Exception must not kill the worker ─────────────


class TestSaveLoopSurvivesRegularException:
    """The worker must log-and-continue on a regular ``Exception``."""

    def test_regular_exception_is_logged_and_worker_continues(self, cr, caplog):
        """A regular ``Exception`` from ``_save_sync`` is logged at ERROR
        and the worker stays alive to process subsequent saves.

        Pre-fix, the ``except BaseException: raise`` clause matched the
        ``ValueError`` (a regular ``Exception`` subclass) before the
        ``except Exception:`` clause could run, so the worker died
        silently and ``flush()`` on a later save timed out.
        """
        original_save_sync = cr._save_sync
        call_count = {"n": 0}

        def flaky_save_sync(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("simulated transient failure")
            return original_save_sync(*args, **kwargs)

        with (
            mock.patch.object(cr, "_save_sync", side_effect=flaky_save_sync),
            caplog.at_level(logging.ERROR, logger=LOGGER_NAME),
        ):
            # First add() triggers the ValueError path in the worker.
            cr.add("first", pasted=False)

            # Wait for the worker to actually attempt the failing
            # save. If the worker died on the exception (the bug),
            # call_count stays at 0 and this assertion fails.
            deadline = time.monotonic() + 5.0
            while call_count["n"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert call_count["n"] >= 1, (
                "Worker never processed the first save — it may have died before reaching _save_sync."
            )

            # Second add() enqueues another save. If the worker died
            # on the first exception, this save is never drained and
            # flush() times out.
            cr.add("second", pasted=False)
            flushed = cr.flush(timeout=5.0)
            assert flushed, (
                "flush() timed out — the worker likely died on the first regular Exception (the dead-except bug)."
            )

        # Worker thread MUST still be alive.
        assert cr._save_thread is not None, "Worker thread was never created."
        assert cr._save_thread.is_alive(), (
            "Worker thread died after a regular Exception — the "
            "``except Exception:`` log-and-continue clause is unreachable "
            "(the propagating clause is too broad)."
        )

        # The ERROR log proves the ``except Exception:`` clause ran.
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, (
            "Expected an ERROR log from the worker's log-and-continue "
            "clause; got none. The ``except Exception:`` clause may not "
            "be reachable."
        )

    def test_subsequent_save_persists_after_transient_failure(self, cr, caplog):
        """After a transient exception, the worker still persists state.

        This is the user-visible consequence of the fix: a single
        transient failure no longer silently disables crash recovery
        for the rest of the session.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        original_save_sync = cr._save_sync
        call_count = {"n": 0}

        def flaky_save_sync(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated transient failure")
            return original_save_sync(*args, **kwargs)

        with (
            mock.patch.object(cr, "_save_sync", side_effect=flaky_save_sync),
            caplog.at_level(logging.ERROR, logger=LOGGER_NAME),
        ):
            cr.add("first", pasted=False)  # raises, logged, swallowed
            # Wait for the failing save to be attempted.
            deadline = time.monotonic() + 5.0
            while call_count["n"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            cr.add("second", pasted=False)  # should succeed
            assert cr.flush(timeout=5.0), "flush() timed out — worker died on transient exception."

        # The second entry must have been persisted to disk. Reload a
        # fresh instance from the same path to verify.
        reloaded = CrashRecovery(config_dir=cr._path.parent)
        try:
            texts = [e.get("text") for e in reloaded.get_all()]
            assert "second" in texts, (
                "The second save was not persisted — the worker may have "
                "died on the first transient exception (the dead-except bug)."
            )
        finally:
            reloaded.shutdown()
            if reloaded._save_thread is not None:
                reloaded._save_thread.join(timeout=2.0)


# ─── __del__: must never raise, even for BaseException subclasses ──────


class TestDelNeverRaisesBaseException:
    """``__del__`` must swallow ``BaseException`` subclasses too."""

    @pytest.mark.parametrize(
        "exc",
        [
            KeyboardInterrupt("simulated shutdown interrupt"),
            SystemExit("simulated shutdown exit"),
            GeneratorExit("simulated generator close"),
        ],
    )
    def test_del_swallows_base_exception_subclass(self, recovery_dir, exc):
        """``__del__`` must not raise for ``BaseException`` subclasses.

        Pre-fix, ``__del__`` used ``except Exception: pass``, which does
        NOT catch ``KeyboardInterrupt`` / ``SystemExit`` / ``GeneratorExit``
        (all ``BaseException`` subclasses that are NOT ``Exception``
        subclasses). A shutdown signal arriving while ``__del__`` was
        mid-save would propagate out of GC, crashing the interpreter.

        Post-fix, ``__del__`` uses ``except BaseException: pass`` so the
        "never raise" contract is honored in full.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # Stop the worker so add() below uses the synchronous fallback.
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        # Populate entries so __del__ actually calls _save_sync.
        cr.add("hello", pasted=False)
        # Ensure _save_sync does not short-circuit on the _final_save_done
        # flag (only _atexit_flush_all sets it, but be defensive).
        cr._final_save_done = False

        real_save_sync = cr._save_sync

        def raising_save_sync(*args, **kwargs):
            raise exc

        # Replace _save_sync with one that raises the BaseException subclass.
        cr._save_sync = raising_save_sync
        try:
            # Must NOT raise — the fixed ``except BaseException: pass``
            # swallows it. Pre-fix this raised (KeyboardInterrupt etc.
            # are not caught by ``except Exception:``).
            cr.__del__()
        finally:
            # Restore so any GC-time __del__ uses the real _save_sync.
            cr._save_sync = real_save_sync
            # Best-effort cleanup of the instance state set by __del__.
            cr._stopped = True
