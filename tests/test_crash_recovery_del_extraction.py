"""Regression guard: ``CrashRecovery.__del__`` extraction.

The original ``__del__`` was a 101-line method (lines 1137-1237 in
``crash_recovery.py``) that performed I/O during GC. It has been
refactored into a thin delegate that calls two named helpers:

- ``_cleanup_signal_stop`` — sets ``self._stopped = True`` so the
  background worker knows to exit. Own try/except so a failure here
  does not skip the final save.
- ``_cleanup_flush_pending`` — checks ``_entries`` under ``_lock`` and
  calls ``_save_sync(durability=True)`` if non-empty. Own try/except
  so a ``_save_sync`` failure (e.g. disk full during interpreter
  shutdown) does not propagate out of GC.

``__del__`` itself is now 18 LOC (down from 101) — 5 lines of body
(``try / two helper calls / except BaseException / pass``) plus
docstring + comment. The defense-in-depth outer ``try/except`` in
``__del__`` preserves the original "never raise from GC" contract
even if a helper is replaced with a broken stub.

These tests verify the contract holds:

1. ``__del__`` calls both helpers in the original order (signal-stop
   THEN flush-pending — reversing would risk the worker draining
   mid-flush).
2. Each helper's own try/except swallows internal failures (so one
   failure does not skip the other).
3. ``__del__``'s outer try/except catches a fully-broken helper stub
   (defense-in-depth).
4. The original ``__del__`` save contract (post-shutdown direct
   ``_entries.append`` still persists on GC) still holds — this is
   the regression-guarded behavior from
   ``test_del_saves_unpersisted_post_shutdown_mutations`` in
   ``tests/test_crash_recovery.py``.

Scope: these tests target ONLY the __del__ extraction. The pre-existing
crash-recovery behavior is covered by ``tests/test_crash_recovery.py``
and friends; this file does NOT re-test those contracts (just the
extraction's structural invariants).

Test isolation pattern
----------------------

All tests in this module follow a consistent pattern to avoid the
"patched method called during shutdown cleanup" trap:

1. Construct the CrashRecovery instance.
2. Call ``shutdown()`` FIRST (kill the worker thread + join it).
3. Patch internals via ``monkeypatch`` (auto-undone by pytest AFTER
   the test function returns — but BEFORE the autouse
   ``_drain_crash_recovery_workers`` fixture's post-test cleanup runs
   the second ``shutdown()`` on leaked instances).
4. Directly mutate ``_entries`` via ``cr._lock`` (bypassing ``add()``
   so the patched ``_save_sync`` is not invoked indirectly).
5. Call the helper / ``__del__`` under test.
6. No explicit ``finally: cr.shutdown()`` — the autouse fixture handles it.

The conftest's ``_drain_crash_recovery_workers`` wraps its post-test
``shutdown()`` in ``contextlib.suppress(Exception)``, so even a
patched-method call during cleanup would not crash the suite — but
following the pattern above means the cleanup never sees the patched
method in the first place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


# ── Fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Re-use the canonical ``tmp_config_dir`` fixture from conftest.py.

    ``tmp_config_dir`` patches BOTH ``voice_typer.server.config._config_dir``
    AND ``voice_typer.server.app._config_dir`` (so any code path that
    reads the config dir via either accessor lands in the temp dir).
    """
    return tmp_config_dir


# ── Helpers ──────────────────────────────────────────────────────────


def _make_cr(recovery_dir: Path):
    """Construct a CrashRecovery bound to ``recovery_dir``.

    Imported lazily so the test module loads even if heavy-import
    mocks haven't been installed yet (they have — conftest.py runs
    autouse — but the lazy import is still cleaner).
    """
    from voice_typer.server.crash_recovery import CrashRecovery

    return CrashRecovery(config_dir=recovery_dir)


def _shutdown_and_join(cr) -> None:
    """Shutdown the worker thread + join it. Idempotent."""
    cr.shutdown()
    if cr._save_thread is not None:
        cr._save_thread.join(timeout=2.0)


def _direct_append_entry(cr, text: str = "entry-to-flush") -> None:
    """Append an entry to ``_entries`` directly (bypassing ``add()``).

    Used so the patched ``_save_sync`` is NOT invoked indirectly via
    ``_enqueue_save()`` (which ``add()`` calls — and which after
    ``shutdown()`` falls back to a direct ``_save_sync()``).
    """
    with cr._lock:
        cr._entries.append(
            {
                "text": text,
                "timestamp": "2026-08-18T00:00:00",
                "pasted": False,
            }
        )


# ── Tests: __del__ delegates to both helpers in order ────────────────


class TestDelDelegatesToHelpers:
    """``__del__`` must call ``_cleanup_signal_stop`` then
    ``_cleanup_flush_pending`` — order matters because reversing
    would risk the worker draining mid-flush."""

    def test_del_calls_both_helpers_in_order(self, recovery_dir, monkeypatch):
        """Wrap both helpers with sentinels + call originals; verify
        ``__del__`` invokes them in the documented order."""
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

        # Direct call — no GC indeterminacy.
        cr.__del__()

        assert call_order == ["signal", "flush"], (
            f"__del__ must call _cleanup_signal_stop BEFORE _cleanup_flush_pending; got {call_order}"
        )
        # Verify the originals actually ran (not just the sentinels).
        assert cr._stopped is True, "_cleanup_signal_stop must have set _stopped = True"

    def test_del_does_not_raise_on_clean_instance(self, recovery_dir):
        """Baseline: ``__del__`` on a freshly-shutdown instance with
        no entries must be a no-op (no raise, no I/O)."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        # Should not raise.
        cr.__del__()
        assert cr._stopped is True


# ── Tests: _cleanup_signal_stop sets the stop flag ───────────────────


class TestCleanupSignalStop:
    """``_cleanup_signal_stop`` is the extracted helper that sets
    ``self._stopped = True``. It must be idempotent and not raise."""

    def test_sets_stopped_flag(self, recovery_dir):
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        # ``_stopped`` is already True after shutdown — reset to verify
        # the helper actually sets it (not just inherited from shutdown).
        cr._stopped = False
        assert cr._stopped is False, "test setup: _stopped reset to False"

        cr._cleanup_signal_stop()
        assert cr._stopped is True, "_cleanup_signal_stop must set _stopped = True"

    def test_is_idempotent(self, recovery_dir):
        """Calling twice must not raise (``shutdown()`` also sets this
        — both paths must coexist)."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        cr._stopped = False

        cr._cleanup_signal_stop()
        cr._cleanup_signal_stop()
        cr._cleanup_signal_stop()
        assert cr._stopped is True

    def test_does_not_raise_even_if_stopped_assignment_breaks(self, recovery_dir, monkeypatch):
        """If ``self._stopped = True`` somehow raises (e.g. a future
        subclass overrides ``__setattr__`` to validate), the helper's
        own try/except must swallow it so the subsequent
        ``_cleanup_flush_pending`` still runs.

        We simulate this by replacing ``__setattr__`` on the class with
        one that raises for ``_stopped``. The helper's own
        ``try/except BaseException`` catches the ``RuntimeError``.
        """
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        orig_setattr = type(cr).__setattr__

        def failing_setattr(self, name, value):
            if name == "_stopped":
                raise RuntimeError("simulated _stopped assignment failure")
            return orig_setattr(self, name, value)

        monkeypatch.setattr(type(cr), "__setattr__", failing_setattr)

        # The helper must NOT raise — its own try/except catches.
        cr._cleanup_signal_stop()


# ── Tests: _cleanup_flush_pending swallows _save_sync failures ───────


class TestCleanupFlushPendingResilience:
    """``_cleanup_flush_pending`` must never raise — its own
    ``try/except BaseException`` catches everything, including
    ``BaseException`` subclasses like ``KeyboardInterrupt`` that
    ``except Exception`` would miss."""

    def test_swallows_runtime_error_from_save_sync(self, recovery_dir, monkeypatch):
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated _save_sync failure")

        monkeypatch.setattr(cr, "_save_sync", boom)

        # Must NOT raise — helper's own try/except catches.
        cr._cleanup_flush_pending()

    def test_swallows_keyboard_interrupt_from_save_sync(self, recovery_dir, monkeypatch):
        """``KeyboardInterrupt`` is a ``BaseException`` subclass —
        the helper's ``except BaseException`` (NOT ``except Exception``)
        catches it. This is the documented "never raise from GC"
        contract — a ``KeyboardInterrupt`` during interpreter shutdown
        must not propagate out of ``__del__``."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        def boom(*args, **kwargs):
            raise KeyboardInterrupt("simulated Ctrl+C during save")

        monkeypatch.setattr(cr, "_save_sync", boom)

        cr._cleanup_flush_pending()

    def test_swallows_oserror_from_lock_acquisition(self, recovery_dir, monkeypatch):
        """If ``self._lock`` acquisition raises (e.g. a corrupt lock
        object during interpreter shutdown), the helper must swallow
        it — no save, but also no raise."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        class BrokenLock:
            def __enter__(self):
                raise OSError("simulated broken lock")

            def __exit__(self, *args):
                return False

        # Replace the instance's _lock — the helper reads
        # ``self._lock`` at call time.
        monkeypatch.setattr(cr, "_lock", BrokenLock())

        cr._cleanup_flush_pending()

    def test_no_save_when_entries_empty(self, recovery_dir, monkeypatch):
        """When ``_entries`` is empty, ``_save_sync`` must NOT be
        called — matches the original ``__del__`` behavior (saves
        are only triggered by state changes, not by GC)."""
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
        """When ``_entries`` is non-empty, ``_save_sync(durability=True)``
        must be called — this is the post-shutdown safety-net save."""
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


# ── Tests: __del__ end-to-end (failure isolation across helpers) ────


class TestDelFailureIsolation:
    """The __del__ extraction's KEY contract: a failure inside one
    helper must NOT prevent the other helper from running. Each
    helper owns its own try/except; ``__del__``'s outer try/except
    is defense-in-depth for the case where a helper is replaced
    entirely with a broken stub."""

    def test_save_sync_failure_does_not_skip_signal_stop(self, recovery_dir, monkeypatch):
        """End-to-end: ``_save_sync`` raises → ``_cleanup_flush_pending``
        catches it → ``__del__`` returns normally. Both helpers ran
        (``_stopped`` is True because ``_cleanup_signal_stop`` ran
        first)."""
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

        # _cleanup_signal_stop ran BEFORE _cleanup_flush_pending,
        # so _stopped must be True even though _save_sync failed.
        assert cr._stopped is True, (
            "_cleanup_signal_stop must have set _stopped=True before "
            "_cleanup_flush_pending attempted (and failed) the save"
        )

    def test_signal_stop_helper_failure_does_not_skip_flush(self, recovery_dir, monkeypatch):
        """Replace ``_cleanup_signal_stop`` with a stub that raises
        internally but catches (mimicking the helper's own
        try/except). ``__del__`` must continue to call
        ``_cleanup_flush_pending`` afterward."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        # Reset _stopped so the stub's catch is observable.
        cr._stopped = False

        signal_called: list[bool] = []
        flush_called: list[bool] = []

        real_flush = type(cr)._cleanup_flush_pending

        def stub_signal(self):
            signal_called.append(True)
            # Mimic the helper's own try/except catching an
            # internal failure — this is the contract being tested.
            try:
                raise RuntimeError("simulated _cleanup_signal_stop failure")
            except BaseException:
                pass

        def spy_flush(self):
            flush_called.append(True)
            # Call the real _cleanup_flush_pending so we exercise the
            # actual code path (no entries → no save, no raise).
            return real_flush(self)

        monkeypatch.setattr(type(cr), "_cleanup_signal_stop", stub_signal)
        monkeypatch.setattr(type(cr), "_cleanup_flush_pending", spy_flush)

        # __del__ must not raise.
        cr.__del__()

        assert signal_called, "_cleanup_signal_stop must have been called by __del__"
        assert flush_called, (
            "_cleanup_flush_pending must run AFTER _cleanup_signal_stop's "
            "caught failure — failure isolation contract violated"
        )

    def test_outer_try_except_catches_fully_broken_helper(self, recovery_dir, monkeypatch):
        """Defense-in-depth: even if a helper is replaced with a stub
        that raises WITHOUT catching (simulating a future regression
        where a helper loses its try/except), ``__del__``'s outer
        try/except must still catch and prevent propagation out of GC."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)

        def broken_signal(self):
            raise RuntimeError("helper lost its try/except — broken stub")

        def broken_flush(self):
            raise RuntimeError("helper lost its try/except — broken stub")

        monkeypatch.setattr(type(cr), "_cleanup_signal_stop", broken_signal)
        monkeypatch.setattr(type(cr), "_cleanup_flush_pending", broken_flush)

        # __del__ must NOT raise — outer try/except catches.
        try:
            cr.__del__()
        except BaseException as exc:  # pragma: no cover — defensive
            pytest.fail(f"__del__ must not propagate: {exc!r}")

    def test_keyboard_interrupt_during_save_does_not_propagate(self, recovery_dir, monkeypatch):
        """``KeyboardInterrupt`` (a ``BaseException`` subclass) raised
        during ``_save_sync`` must be caught by ``_cleanup_flush_pending``'s
        ``except BaseException`` (NOT ``except Exception``). End-to-end
        via ``__del__``."""
        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        _direct_append_entry(cr)

        def boom(*args, **kwargs):
            raise KeyboardInterrupt("simulated Ctrl+C during interpreter shutdown")

        monkeypatch.setattr(cr, "_save_sync", boom)

        # __del__ must not propagate the KeyboardInterrupt.
        try:
            cr.__del__()
        except BaseException as exc:  # pragma: no cover — defensive
            pytest.fail(f"__del__ must catch KeyboardInterrupt (BaseException subclass) during _save_sync; got {exc!r}")


# ── Tests: __del__ preserves the original post-shutdown save contract ──


class TestDelPreservesOriginalContract:
    """E14 (regression prevention): the extraction must NOT regress
    the original ``__del__`` save contract. The key regression guard
    is ``test_del_saves_unpersisted_post_shutdown_mutations`` in
    ``tests/test_crash_recovery.py`` — we re-verify the contract here
    using the new helper boundaries to ensure the extraction is
    behaviorally equivalent."""

    def test_del_still_saves_post_shutdown_direct_mutation(self, recovery_dir):
        """After ``shutdown()``, directly mutate ``_entries`` (bypassing
        ``add()`` / ``_enqueue_save()``). ``__del__`` (via
        ``_cleanup_flush_pending``) must persist the mutation.

        This is the Finding A3 regression guard — the original ``__del__``
        had an ``is_alive()`` check that skipped the save after
        ``shutdown()`` killed the worker. The current code saves whenever
        ``_entries`` is non-empty regardless of worker state. The
        extraction preserves this — ``_cleanup_flush_pending`` reads
        ``_entries`` under ``_lock`` and saves if non-empty.
        """
        import json

        cr = _make_cr(recovery_dir)
        _shutdown_and_join(cr)
        assert cr._save_thread is None or not cr._save_thread.is_alive()

        # Mutate _entries directly, bypassing add()/_enqueue_save()
        # so the ONLY path to disk is __del__'s save.
        with cr._lock:
            cr._entries.append(
                {
                    "text": "del-only-mutation-vp40",
                    "timestamp": "2026-08-18T00:00:00",
                    "pasted": False,
                }
            )

        # Sanity: the entry is in memory but NOT on disk yet.
        recovery_file = recovery_dir / "voice-typer-recovery.json"
        if recovery_file.exists():
            pre = json.loads(recovery_file.read_text(encoding="utf-8"))
            assert all(e.get("text") != "del-only-mutation-vp40" for e in pre.get("entries", [])), (
                "test setup error: entry should not be on disk before __del__"
            )

        # Force GC of the instance — worker is dead, so __del__ fires.
        # We call __del__ explicitly to avoid GC timing indeterminacy.
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
        """After ``clear()`` removes all entries, ``__del__`` must
        NOT call ``_save_sync`` (no data to lose — matches the original
        ``if self._entries:`` short-circuit)."""
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
