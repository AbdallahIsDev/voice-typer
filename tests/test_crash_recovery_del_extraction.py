"""Regression guard: ``CrashRecovery.__del__`` extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Re-use the canonical ``tmp_config_dir`` fixture from conftest.py."""
    return tmp_config_dir


def _make_cr(recovery_dir: Path):
    """Construct a CrashRecovery bound to ``recovery_dir``."""
    from voice_typer.server.crash_recovery import CrashRecovery

    return CrashRecovery(config_dir=recovery_dir)


def _shutdown_and_join(cr) -> None:
    """Shutdown the worker thread + join it. Idempotent."""
    cr.shutdown()
    if cr._save_thread is not None:
        cr._save_thread.join(timeout=2.0)


def _direct_append_entry(cr, text: str = "entry-to-flush") -> None:
    """Append an entry to ``_entries`` directly (bypassing ``add()``)."""
    with cr._lock:
        cr._entries.append(
            {
                "text": text,
                "timestamp": "2026-08-18T00:00:00",
                "pasted": False,
            }
        )


class TestDelDelegatesToHelpers:
    """``__del__`` must call ``_cleanup_signal_stop`` then"""

    def test_del_calls_both_helpers_in_order(self, recovery_dir, monkeypatch):
        """Wrap both helpers with sentinels + call originals; verify"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        call_order: list[str] = []
        orig_signal = type(cr)._cleanup_signal_stop
        orig_flush = type(cr)._cleanup_flush_pending

        def spy_signal(self):
            call_order.append("signal")
            return orig_signal(self)

        def spy_flush(self):
            call_order.append("flush")
            return orig_flush(self)

        monkeypatch.setattr(type(cr), "_cleanup_signal_stop", spy_signal)
        monkeypatch.setattr(type(cr), "_cleanup_flush_pending", spy_flush)

        # Direct call, no GC indeterminacy.
        cr.__del__()

        assert call_order == ["signal", "flush"], (
            f"__del__ must call _cleanup_signal_stop BEFORE _cleanup_flush_pending; got {call_order}"
        )
        # Verify the originals actually ran (not just the sentinels).
        assert cr._stopped is True, "_cleanup_signal_stop must have set _stopped = True"

    def test_del_does_not_raise_on_clean_instance(self, recovery_dir):
        """Baseline: ``__del__`` on a freshly-shutdown instance with"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        # Should not raise.
        cr.__del__()
        assert cr._stopped is True


class TestCleanupSignalStop:
    """``_cleanup_signal_stop`` is the extracted helper that sets"""

    def test_sets_stopped_flag(self, recovery_dir):
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        # ``_stopped`` is already True after shutdown, reset to verify
        cr._stopped = False
        assert cr._stopped is False, "test setup: _stopped reset to False"

        cr._cleanup_signal_stop()
        assert cr._stopped is True, "_cleanup_signal_stop must set _stopped = True"

    def test_is_idempotent(self, recovery_dir):
        """Calling twice must not raise (``shutdown()`` also sets this"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        cr._stopped = False

        cr._cleanup_signal_stop()
        cr._cleanup_signal_stop()
        cr._cleanup_signal_stop()
        assert cr._stopped is True

    def test_does_not_raise_even_if_stopped_assignment_breaks(self, recovery_dir, monkeypatch):
        """If ``self._stopped = True`` somehow raises (e.g. a future"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        orig_setattr = type(cr).__setattr__

        def failing_setattr(self, name, value):
            if name == "_stopped":
                raise RuntimeError("simulated _stopped assignment failure")
            return orig_setattr(self, name, value)

        monkeypatch.setattr(type(cr), "__setattr__", failing_setattr)

        # The helper must NOT raise, its own try/except catches.
        cr._cleanup_signal_stop()


class TestCleanupFlushPendingResilience:
    """``_cleanup_flush_pending`` must never raise, its own"""

    def test_swallows_runtime_error_from_save_sync(self, recovery_dir, monkeypatch):
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated _save_sync failure")

        monkeypatch.setattr(cr, "_save_sync", boom)

        # Must NOT raise, helper's own try/except catches.
        cr._cleanup_flush_pending()

    def test_swallows_keyboard_interrupt_from_save_sync(self, recovery_dir, monkeypatch):
        """``KeyboardInterrupt`` is a ``BaseException`` subclass —"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        def boom(*args, **kwargs):
            raise KeyboardInterrupt("simulated Ctrl+C during save")

        monkeypatch.setattr(cr, "_save_sync", boom)

        cr._cleanup_flush_pending()

    def test_swallows_oserror_from_lock_acquisition(self, recovery_dir, monkeypatch):
        """If ``self._lock`` acquisition raises (e.g. a corrupt lock"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        class BrokenLock:
            def __enter__(self):
                raise OSError("simulated broken lock")

            def __exit__(self, *args):
                return False

        # Replace the instance's _lock, the helper reads
        monkeypatch.setattr(cr, "_lock", BrokenLock())

        cr._cleanup_flush_pending()

    def test_no_save_when_entries_empty(self, recovery_dir, monkeypatch):
        """When ``_entries`` is empty, ``_save_sync`` must NOT be"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        # No entries appended.

        save_calls: list[bool] = []
        orig_save = cr._save_sync

        def spy_save(*args, **kwargs):
            save_calls.append(True)
            return orig_save(*args, **kwargs)

        monkeypatch.setattr(cr, "_save_sync", spy_save)

        cr._cleanup_flush_pending()
        assert save_calls == [], f"_save_sync must not be called when _entries is empty; got {save_calls}"

    def test_saves_when_entries_non_empty(self, recovery_dir, monkeypatch):
        """When ``_entries`` is non-empty, ``_save_sync(durability=True)``"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        save_calls: list[dict] = []
        orig_save = cr._save_sync

        def spy_save(*args, **kwargs):
            save_calls.append({"args": args, "kwargs": kwargs})
            return orig_save(*args, **kwargs)

        monkeypatch.setattr(cr, "_save_sync", spy_save)

        cr._cleanup_flush_pending()
        assert len(save_calls) == 1, f"_save_sync must be called exactly once; got {save_calls}"
        assert save_calls[0]["kwargs"].get("durability") is True, (
            f"_save_sync must be called with durability=True for the "
            f"final GC save; got kwargs={save_calls[0]['kwargs']}"
        )


class TestDelFailureIsolation:
    """The __del__ extraction's KEY contract: a failure inside one"""

    def test_save_sync_failure_does_not_skip_signal_stop(self, recovery_dir, monkeypatch):
        """End-to-end: ``_save_sync`` raises → ``_cleanup_flush_pending``"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)
        # Reset _stopped so we can verify __del__ re-sets it.
        cr._stopped = False

        def boom(*args, **kwargs):
            raise RuntimeError("simulated _save_sync failure")

        monkeypatch.setattr(cr, "_save_sync", boom)

        # __del__ must not raise.
        cr.__del__()

        assert cr._stopped is True, (
            "_cleanup_signal_stop must have set _stopped=True before "
            "_cleanup_flush_pending attempted (and failed) the save"
        )

    def test_signal_stop_helper_failure_does_not_skip_flush(self, recovery_dir, monkeypatch):
        """Replace ``_cleanup_signal_stop`` with a stub that raises"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        # Reset _stopped so the stub's catch is observable.
        cr._stopped = False

        signal_called: list[bool] = []
        flush_called: list[bool] = []

        real_flush = type(cr)._cleanup_flush_pending

        def stub_signal(self):
            signal_called.append(True)
            try:
                raise RuntimeError("simulated _cleanup_signal_stop failure")
            except BaseException:
                pass

        def spy_flush(self):
            flush_called.append(True)
            return real_flush(self)

        monkeypatch.setattr(type(cr), "_cleanup_signal_stop", stub_signal)
        monkeypatch.setattr(type(cr), "_cleanup_flush_pending", spy_flush)

        # __del__ must not raise.
        cr.__del__()

        assert signal_called, "_cleanup_signal_stop must have been called by __del__"
        assert flush_called, (
            "_cleanup_flush_pending must run AFTER _cleanup_signal_stop's "
            "caught failure, failure isolation contract violated"
        )

    def test_outer_try_except_catches_fully_broken_helper(self, recovery_dir, monkeypatch):
        """Defense-in-depth: even if a helper is replaced with a stub"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        def broken_signal(self):
            raise RuntimeError("helper lost its try/except, broken stub")

        def broken_flush(self):
            raise RuntimeError("helper lost its try/except, broken stub")

        monkeypatch.setattr(type(cr), "_cleanup_signal_stop", broken_signal)
        monkeypatch.setattr(type(cr), "_cleanup_flush_pending", broken_flush)

        # __del__ must NOT raise, outer try/except catches.
        try:
            cr.__del__()
        except BaseException as exc:  # pragma: no cover, defensive
            pytest.fail(f"__del__ must not propagate: {exc!r}")

    def test_keyboard_interrupt_during_save_does_not_propagate(self, recovery_dir, monkeypatch):
        """``KeyboardInterrupt`` (a ``BaseException`` subclass) raised"""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        def boom(*args, **kwargs):
            raise KeyboardInterrupt("simulated Ctrl+C during interpreter shutdown")

        monkeypatch.setattr(cr, "_save_sync", boom)

        # __del__ must not propagate the KeyboardInterrupt.
        try:
            cr.__del__()
        except BaseException as exc:  # pragma: no cover, defensive
            pytest.fail(f"__del__ must catch KeyboardInterrupt (BaseException subclass) during _save_sync; got {exc!r}")


class TestDelPreservesOriginalContract:
    """
    E14 (regression prevention): the extraction must NOT regress
    ``tests/test_crash_recovery.py``, we re-verify the contract here
    """

    def test_del_still_saves_post_shutdown_direct_mutation(self, recovery_dir):
        """After ``shutdown()``, directly mutate ``_entries`` (bypassing"""
        import json

        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        assert cr._save_thread is None or not cr._save_thread.is_alive()

        # Mutate _entries directly, bypassing add()/_enqueue_save()
        with cr._lock:
            cr._entries.append(
                {
                    "text": "del-only-mutation-vp40",
                    "timestamp": "2026-08-18T00:00:00",
                    "pasted": False,
                }
            )

        # Sanity: the entry is in memory but NOT on disk yet.
        recovery_file = recovery_dir / "recovery.json"
        if recovery_file.exists():
            pre = json.loads(recovery_file.read_text(encoding="utf-8"))
            assert all(e.get("text") != "del-only-mutation-vp40" for e in pre.get("entries", [])), (
                "test setup error: entry should not be on disk before __del__"
            )

        # Force GC of the instance, worker is dead, so __del__ fires.
        cr.__del__()

        # Re-instantiate and verify the bypassed mutation survived.
        from voice_typer.server.crash_recovery import CrashRecovery

        cr2 = CrashRecovery(config_dir=recovery_dir)
        try:
            texts = [e.get("text", "") for e in cr2.get_all()]
            assert "del-only-mutation-vp40" in texts, (
                f"__del__ (via _cleanup_flush_pending) must save post-shutdown _entries mutations; got texts: {texts}"
            )
        finally:
            cr2.shutdown()

    def test_del_is_noop_when_entries_empty_post_clear(self, recovery_dir, monkeypatch):
        """After ``clear()`` removes all entries, ``__del__`` must"""
        cr = _make_cr(recovery_dir)
        # Use add() + clear() BEFORE shutdown so the worker drains them.
        cr.add("entry-1", pasted=False)
        cr.clear()
        cr.flush(timeout=2.0)
        _shutdown_and_join(cr)

        save_calls: list[bool] = []
        orig_save = cr._save_sync

        def spy_save(*args, **kwargs):
            save_calls.append(True)
            return orig_save(*args, **kwargs)

        monkeypatch.setattr(cr, "_save_sync", spy_save)

        cr.__del__()
        assert save_calls == [], (
            f"__del__ must not call _save_sync when _entries is empty after clear(); got {save_calls}"
        )
