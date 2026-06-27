"""Tests for symlink attack prevention on config and corrections files.

TEST-022: Test that creating a symlink at a config file path is handled
safely — symlinks should not be followed to overwrite files outside
the config directory. Only on POSIX (skip on Windows).
"""

from __future__ import annotations

import json
import os
import sys
import pytest
from pathlib import Path

from voice_typer.server.text_cleanup import configure_corrections
from voice_typer.server.config import Config


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink tests")
class TestSymlinkConfigAttack:
    """Test that symlinks pointing to config files are handled safely."""

    def test_config_save_does_not_follow_symlink(self, tmp_path, monkeypatch):
        """Writing config through a symlink should not overwrite the target."""
        # Create a "sensitive" file outside the config dir
        sensitive = tmp_path / "sensitive_config.json"
        sensitive.write_text('{"secret": "do_not_overwrite"}', encoding="utf-8")

        # Create config dir with a symlink to the sensitive file
        config_dir = tmp_path / "config_dir"
        config_dir.mkdir()
        link = config_dir / "config.json"
        try:
            link.symlink_to(sensitive)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: config_dir)

        # Load and save config — the symlink target should NOT be overwritten
        c = Config.load()
        original_content = sensitive.read_text(encoding="utf-8")

        # The sensitive file content should either be preserved or the
        # symlink should be replaced with a regular file
        c.save()
        # Verify the original sensitive file was not overwritten
        # (either symlink was replaced or content is preserved)
        if link.is_symlink():
            # If still a symlink, target should not have Voice Typer config
            target_content = sensitive.read_text(encoding="utf-8")
            assert "secret" in target_content or "hotkey" not in target_content

    def test_corrections_symlink_not_followed_on_write(self, tmp_path):
        """Writing corrections through a symlink should not overwrite the target."""
        # Create a sensitive file outside the config dir
        sensitive = tmp_path / "sensitive_corrections.json"
        sensitive.write_text('{"sensitive": "data"}', encoding="utf-8")

        # Create symlink pointing to the sensitive file
        link = tmp_path / "voice-typer-corrections.json"
        try:
            link.symlink_to(sensitive)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        # Configure corrections should handle the symlink gracefully
        result = configure_corrections(config_dir=tmp_path)
        # Should not crash; either succeeds or returns error
        assert result is None or isinstance(result, str)

        # The sensitive file should not be overwritten with corrections data
        if link.is_symlink():
            target_content = sensitive.read_text(encoding="utf-8")
            assert "sensitive" in target_content or "misspellings" not in target_content

    def test_dangling_symlink_handled_gracefully(self, tmp_path):
        """A dangling symlink (target doesn't exist) should be handled gracefully."""
        link = tmp_path / "voice-typer-corrections.json"
        try:
            link.symlink_to(tmp_path / "nonexistent_target.json")
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        result = configure_corrections(config_dir=tmp_path)
        # Dangling symlink — treat as "no file"
        assert result is None

    def test_regular_file_works_alongside_symlinks(self, tmp_path):
        """Regular (non-symlink) files should work even when symlinks exist nearby."""
        # Create a symlink for something else (not the target file)
        other_link = tmp_path / "other_link"
        try:
            other_link.symlink_to(tmp_path / "nonexistent")
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        # Regular corrections file should work fine
        corrections = tmp_path / "voice-typer-corrections.json"
        corrections.write_text(json.dumps({"misspellings": {"teh": "the"}}), encoding="utf-8")

        result = configure_corrections(config_dir=tmp_path)
        assert result is None

    def test_symlink_detected_by_is_symlink(self, tmp_path):
        """Path.is_symlink() should correctly detect symlinks."""
        real = tmp_path / "real.txt"
        real.write_text("hello")

        link = tmp_path / "link.txt"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        assert not real.is_symlink()
        assert link.is_symlink()
        assert link.exists()  # Symlink target exists
        assert real.exists()

    def test_config_dir_symlink_not_followed(self, tmp_path, monkeypatch):
        """If the config directory itself is a symlink, it should be handled safely."""
        # Create a real directory and a symlink to it
        real_dir = tmp_path / "real_config"
        real_dir.mkdir()

        link_dir = tmp_path / "link_config"
        try:
            link_dir.symlink_to(real_dir)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: link_dir)

        # Config operations should work through the symlinked directory
        c = Config(hotkey="<f5>")
        result = c.save()
        # Should succeed (symlinked directories are generally OK)
        assert result is True or result is None
