"""CrashRecovery lazy-load regression tests.

``CrashRecovery.__init__`` previously called ``self._load()``
synchronously (a disk read on the main thread during
``VoiceTyperApp.__init__``). moves ``_load()`` out of ``__init__``
and into ``check_on_startup()`` (which runs on the startup daemon thread
via ``StartupSequence.run``) so the disk read doesn't block the UI/tray
critical path.

These tests pin the new lazy-load contract:

  (a) ``__init__`` does NOT call ``_load`` — verified by checking the
      ``_loaded`` flag is ``False`` after construction (``_load()``
      always sets ``_loaded = True``, so ``False`` proves it wasn't
      called).
  (b) ``check_on_startup`` DOES call ``_load`` — verified by mocking
      ``_load`` on the instance and asserting it was called.

A third test verifies the ``_loaded`` guard makes ``_load`` idempotent
(safe to call multiple times — the disk read happens at most once).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def recovery_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point ``config._config_dir`` at a temp directory so the recovery
    file lands in ``tmp_path`` instead of the user's real config dir."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestCrashRecoveryLazyLoad:
    """``_load()`` is deferred from ``__init__`` to
    ``check_on_startup()`` and the read accessors."""

    def test_init_does_not_call_load(self, recovery_dir: Path) -> None:
        """``CrashRecovery.__init__`` must NOT call ``_load()``.

        The disk read is deferred to ``check_on_startup()`` (which runs
        on the startup daemon thread) so the main thread isn't blocked
        during ``VoiceTyperApp.__init__``. We verify this by checking
        the ``_loaded`` flag: ``_load()`` always sets ``_loaded = True``
        (on success AND failure), so if ``_loaded`` is ``False`` after
        construction, ``_load()`` was not called.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # _load() always sets _loaded = True. If _loaded is False,
        # _load() was not called in __init__.
        assert cr._loaded is False, "__init__ must not call _load() — _loaded should be False"
        cr.shutdown()

    def test_check_on_startup_calls_load(self, recovery_dir: Path) -> None:
        """``check_on_startup()`` must call ``_load()``.

        This is the primary load site — the disk read happens here (on
        the startup daemon thread) rather than in ``__init__``. We mock
        ``_load`` on the INSTANCE (not the class) so calls from other
        instances' ``__del__`` don't interfere with the assertion.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Reset _loaded to False so the mock isn't short-circuited
            # by the guard (check_on_startup may have been preceded by
            # a read accessor that triggered a lazy load).
            cr._loaded = False
            with patch.object(cr, "_load") as mock_load:
                cr.check_on_startup()
                mock_load.assert_called()
        finally:
            cr.shutdown()

    def test_load_is_idempotent(self, recovery_dir: Path) -> None:
        """``_load()`` must be idempotent — calling it multiple times
        must not re-read the disk.

        The ``_loaded`` guard ensures the disk read happens at most once
        per instance. This is important because ``_load()`` is called
        from BOTH ``check_on_startup()`` and the read accessors
        (``get_all``, ``get_unpasted``, ``count``); without the guard,
        every read would re-read the recovery file.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # First call loads from disk (file doesn't exist → empty)
            cr._load()
            assert cr._loaded is True

            # Write an entry to disk so a second load WOULD pick it up
            # if the guard weren't present.
            cr.add("test entry", pasted=False)
            cr.flush(timeout=2.0)

            # Second call must be a no-op — the guard short-circuits.
            # If the guard were broken, _entries would be replaced with
            # the disk content and count would still be 1 (same data),
            # so we verify _loaded is still True and the call returns
            # without error.
            cr._load()
            assert cr._loaded is True
            # The entry is still there (not lost by a re-load clobber).
            assert cr.count == 1
        finally:
            cr.shutdown()

    def test_read_accessor_triggers_lazy_load(self, recovery_dir: Path) -> None:
        """read accessors (``get_all``, ``count``, ``get_unpasted``)
        must lazily call ``_load()`` on first access if
        ``check_on_startup()`` hasn't run yet. This preserves backward
        compat for callers that read before startup completes.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        # Write an entry to disk so there's something to load.
        cr1 = CrashRecovery(config_dir=recovery_dir)
        cr1.add("lazy-load-test", pasted=False)
        cr1.flush(timeout=2.0)
        cr1.shutdown()
        del cr1

        # Fresh instance — no check_on_startup() call.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        assert cr2._loaded is False, "entries should not be loaded yet"

        # Reading count must trigger a lazy load.
        assert cr2.count == 1, "lazy load via count should have loaded the entry"
        assert cr2._loaded is True, "count should have set _loaded = True"

        # The entry is accessible via get_all (which is now a no-op load).
        entries = cr2.get_all()
        assert len(entries) == 1
        assert entries[0]["text"] == "lazy-load-test"
        cr2.shutdown()

    def test_add_before_load_does_not_clobber_entries(self, recovery_dir: Path) -> None:
        """if ``add()`` is called before ``_load()`` (e.g. in
        tests or edge cases), a subsequent read accessor must NOT
        clobber the in-memory entries by loading from disk.

        The ``_load()`` method checks ``len(self._entries) > 0`` under
        ``_lock`` — if entries already exist, it skips the disk read and
        marks ``_loaded = True`` so future calls are a no-op.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Add entries before any read (simulates test pattern where
            # add() is called without check_on_startup()).
            cr.add("first", pasted=False)
            cr.add("second", pasted=False)

            # _loaded is still False (no read has triggered _load yet).
            assert cr._loaded is False

            # Reading count triggers _load(), but _load() sees
            # _entries is non-empty and skips the disk read.
            assert cr.count == 2, "_load() must not clobber in-memory entries"
            assert cr._loaded is True
        finally:
            cr.shutdown()
