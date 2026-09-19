"""Config.save() write-skip regression tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class TestConfigSaveSkip:
    """``Config.save()`` skips the write when content is unchanged."""

    def test_save_skips_write_when_content_unchanged(self, tmp_config_dir: Path) -> None:
        """``Config.save()`` with unchanged content must NOT call"""
        from voice_typer.server.config import Config

        cfg = Config()

        # First save, writes to disk and populates _last_saved_bytes.
        assert cfg.save() is True
        # The cache should now be populated (not None).
        assert cfg._last_saved_bytes is not None, "first save should have populated _last_saved_bytes"

        # Second save, identical content. Mock _secure_atomic_write
        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            result = cfg.save()
            assert result is True, "save() should return True on skip"
            (
                mock_write.assert_not_called(),
                (
                    "_secure_atomic_write must NOT be called when "
                    "content is unchanged, the diff-cache check should skip "
                    "the write entirely"
                ),
            )

    def test_save_writes_when_content_changed(self, tmp_config_dir: Path) -> None:
        """``Config.save()`` with changed content must call"""
        from voice_typer.server.config import Config

        cfg = Config()

        # First save, populates the cache.
        assert cfg.save() is True
        cached_bytes = cfg._last_saved_bytes
        assert cached_bytes is not None

        # Change a config field so the serialized content differs.
        cfg.hotkey = "<f2>"

        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            result = cfg.save()
            assert result is True
            (
                mock_write.assert_called(),
                (
                    "_secure_atomic_write must be called when content "
                    "has changed, the diff-cache check should fall through"
                ),
            )

        # The cache should have been updated to the new bytes.
        assert cfg._last_saved_bytes is not None
        assert cfg._last_saved_bytes != cached_bytes, "cache should be updated after a changed-content save"

    def test_first_save_always_writes(self, tmp_config_dir: Path) -> None:
        """A fresh ``Config()`` instance (``_last_saved_bytes is None``)"""
        from voice_typer.server.config import Config

        cfg = Config()
        # Fresh instance, cache is None.
        assert cfg._last_saved_bytes is None

        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            result = cfg.save()
            assert result is True
            (
                mock_write.assert_called(),
                ("first save (cache is None) must always write, the 'is not None' guard prevents skipping"),
            )

        # Cache is now populated.
        assert cfg._last_saved_bytes is not None
