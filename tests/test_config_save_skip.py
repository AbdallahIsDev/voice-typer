"""Config.save() write-skip regression tests.

``Config._save_unlocked()`` previously always performed the full
``_secure_atomic_write`` (temp file + ``os.replace`` + optional fsync)
even when the serialized content was byte-for-byte identical to what was
already on disk. adds a diff-cache check at the top of the save
path: if ``_last_saved_bytes`` (populated after each successful save)
matches the new ``content_bytes``, the write is skipped entirely.

This mirrors the ``PersistedJSON._last_written_bytes`` pattern in
``secure_file_io.py:541-549``.

These tests pin the new skip-write contract:

  (a) ``Config.save()`` with unchanged content skips the write —
      verified by mocking ``_secure_atomic_write`` and asserting it
      was NOT called on the second save (identical content).
  (b) ``Config.save()`` with changed content writes — verified by
      mocking ``_secure_atomic_write`` and asserting it WAS called.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point ``config._config_dir`` at a temp directory so config.json
    lands in ``tmp_path`` instead of the user's real config dir."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestConfigSaveSkip:
    """``Config.save()`` skips the write when content is unchanged."""

    def test_save_skips_write_when_content_unchanged(
        self, config_dir: Path
    ) -> None:
        """``Config.save()`` with unchanged content must NOT call
        ``_secure_atomic_write``.

        The ``_last_saved_bytes`` cache is populated after the first
        successful save. On the second save with identical content,
        the diff-cache check at the top of ``_save_unlocked`` returns
        ``True`` early, skipping the write entirely.
        """
        from voice_typer.server.config import Config

        cfg = Config()

        # First save — writes to disk and populates _last_saved_bytes.
        # We let this go through the real write so the cache is set
        # to the actual serialized bytes.
        assert cfg.save() is True
        # The cache should now be populated (not None).
        assert cfg._last_saved_bytes is not None, (
            "first save should have populated _last_saved_bytes"
        )

        # Second save — identical content. Mock _secure_atomic_write
        # to verify it is NOT called (the diff-cache check should
        # short-circuit before reaching the write).
        with patch(
            "voice_typer.server.config._secure_atomic_write"
        ) as mock_write:
            result = cfg.save()
            assert result is True, "save() should return True on skip"
            mock_write.assert_not_called(), (
                "_secure_atomic_write must NOT be called when "
                "content is unchanged — the diff-cache check should skip "
                "the write entirely"
            )

    def test_save_writes_when_content_changed(self, config_dir: Path) -> None:
        """``Config.save()`` with changed content must call
        ``_secure_atomic_write``.

        After changing a config field, the serialized bytes differ from
        the cached ``_last_saved_bytes``, so the diff-cache check falls
        through to the real write.
        """
        from voice_typer.server.config import Config

        cfg = Config()

        # First save — populates the cache.
        assert cfg.save() is True
        cached_bytes = cfg._last_saved_bytes
        assert cached_bytes is not None

        # Change a config field so the serialized content differs.
        cfg.hotkey = "<f2>"

        # Second save — changed content. Mock _secure_atomic_write to
        # verify it IS called.
        with patch(
            "voice_typer.server.config._secure_atomic_write"
        ) as mock_write:
            result = cfg.save()
            assert result is True
            mock_write.assert_called(), (
                "_secure_atomic_write must be called when content "
                "has changed — the diff-cache check should fall through"
            )

        # The cache should have been updated to the new bytes.
        assert cfg._last_saved_bytes is not None
        assert cfg._last_saved_bytes != cached_bytes, (
            "cache should be updated after a changed-content save"
        )

    def test_first_save_always_writes(self, config_dir: Path) -> None:
        """A fresh ``Config()`` instance (``_last_saved_bytes is None``)
        must always perform the write on the first save, even if the
        content happens to match what's on disk.

        The ``is not None`` guard in the diff-cache check ensures the
        first save after construction is never skipped.
        """
        from voice_typer.server.config import Config

        cfg = Config()
        # Fresh instance — cache is None.
        assert cfg._last_saved_bytes is None

        with patch(
            "voice_typer.server.config._secure_atomic_write"
        ) as mock_write:
            result = cfg.save()
            assert result is True
            mock_write.assert_called(), (
                "first save (cache is None) must always write — "
                "the 'is not None' guard prevents skipping"
            )

        # Cache is now populated.
        assert cfg._last_saved_bytes is not None
