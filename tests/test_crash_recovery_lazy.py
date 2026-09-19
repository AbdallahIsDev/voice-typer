"""CrashRecovery lazy-load regression tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


@pytest.fixture
def recovery_dir(tmp_config_dir: Path) -> Path:
    """Point ``config._config_dir`` at a temp directory so the recovery"""
    return tmp_config_dir


class TestCrashRecoveryLazyLoad:
    """``_load()`` is deferred from ``__init__`` to"""

    def test_init_does_not_call_load(self, recovery_dir: Path) -> None:
        """``CrashRecovery.__init__`` must NOT call ``_load()``."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr._loaded is False, "__init__ must not call _load(), _loaded should be False"
        cr.shutdown()

    def test_check_on_startup_calls_load(self, recovery_dir: Path) -> None:
        """``check_on_startup()`` must call ``_load()``."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Reset _loaded to False so the mock isn't short-circuited
            cr._loaded = False
            with patch.object(cr, "_load") as mock_load:
                cr.check_on_startup()
                mock_load.assert_called()
        finally:
            cr.shutdown()

    def test_load_is_idempotent(self, recovery_dir: Path) -> None:
        """``_load()`` must be idempotent, calling it multiple times"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # First call loads from disk (file doesn't exist → empty)
            cr._load()
            assert cr._loaded is True

            # Write an entry to disk so a second load WOULD pick it up
            cr.add("test entry", pasted=False)
            cr.flush(timeout=2.0)

            # Second call must be a no-op, the guard short-circuits.
            cr._load()
            assert cr._loaded is True
            # The entry is still there (not lost by a re-load clobber).
            assert cr.count == 1
        finally:
            cr.shutdown()

    def test_read_accessor_triggers_lazy_load(self, recovery_dir: Path) -> None:
        """read accessors (``get_all``, ``count``, ``get_unpasted``)"""
        from voice_typer.server.crash_recovery import CrashRecovery

        # Write an entry to disk so there's something to load.
        cr1 = CrashRecovery(config_dir=recovery_dir)
        cr1.add("lazy-load-test", pasted=False)
        cr1.flush(timeout=2.0)
        cr1.shutdown()
        del cr1

        # Fresh instance, no check_on_startup() call.
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
        """tests or edge cases), a subsequent read accessor must NOT"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        try:
            # Add entries before any read (simulates test pattern where
            cr.add("first", pasted=False)
            cr.add("second", pasted=False)

            # _loaded is still False (no read has triggered _load yet).
            assert cr._loaded is False

            # Reading count triggers _load(), but _load() sees
            assert cr.count == 2, "_load() must not clobber in-memory entries"
            assert cr._loaded is True
        finally:
            cr.shutdown()
