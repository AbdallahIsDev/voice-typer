"""Regression tests for ``CrashRecovery._save_loop`` exception handling."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


LOGGER_NAME = "voice_typer.server.crash_recovery"


@pytest.fixture
def recovery_dir(tmp_config_dir: Path) -> Path:
    """Point config to a temp directory so the recovery file lands in tmp."""
    return tmp_config_dir


@pytest.fixture
def cr(recovery_dir: Path):
    """Create a CrashRecovery instance with a temp dir + tear it down."""
    from voice_typer.server.crash_recovery import CrashRecovery

    inst = CrashRecovery(config_dir=recovery_dir)
    yield inst
    inst.shutdown()
    if inst._save_thread is not None:
        inst._save_thread.join(timeout=2.0)


class TestSaveLoopSurvivesRegularException:
    """The worker must log-and-continue on a regular ``Exception``."""

    def test_regular_exception_is_logged_and_worker_continues(self, cr, caplog):
        """A regular ``Exception`` from ``_save_sync`` is logged at ERROR"""
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
            deadline = time.monotonic() + 5.0
            while call_count["n"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert call_count["n"] >= 1, (
                "Worker never processed the first save, it may have died before reaching _save_sync."
            )

            # Second add() enqueues another save. If the worker died
            cr.add("second", pasted=False)
            flushed = cr.flush(timeout=5.0)
            assert flushed, (
                "flush() timed out, the worker likely died on the first regular Exception (the dead-except bug)."
            )

        # Worker thread MUST still be alive.
        assert cr._save_thread is not None, "Worker thread was never created."
        assert cr._save_thread.is_alive(), (
            "Worker thread died after a regular Exception, the "
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
        """After a transient exception, the worker still persists state."""
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
            assert cr.flush(timeout=5.0), "flush() timed out, worker died on transient exception."

        reloaded = CrashRecovery(config_dir=cr._path.parent)
        try:
            texts = [e.get("text") for e in reloaded.get_all()]
            assert "second" in texts, (
                "The second save was not persisted, the worker may have "
                "died on the first transient exception (the dead-except bug)."
            )
        finally:
            reloaded.shutdown()
            if reloaded._save_thread is not None:
                reloaded._save_thread.join(timeout=2.0)


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
        """``__del__`` must not raise for ``BaseException`` subclasses."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # Stop the worker so add() below uses the synchronous fallback.
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        # Populate entries so __del__ actually calls _save_sync.
        cr.add("hello", pasted=False)
        # Ensure _save_sync does not short-circuit on the _final_save_done
        cr._final_save_done = False

        real_save_sync = cr._save_sync

        def raising_save_sync(*args, **kwargs):
            raise exc

        # Replace _save_sync with one that raises the BaseException subclass.
        cr._save_sync = raising_save_sync
        try:
            # Must NOT raise, the fixed ``except BaseException: pass``
            cr.__del__()
        finally:
            # Restore so any GC-time __del__ uses the real _save_sync.
            cr._save_sync = real_save_sync
            # Best-effort cleanup of the instance state set by __del__.
            cr._stopped = True
