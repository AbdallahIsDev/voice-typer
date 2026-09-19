"""§8.18: SmartScreen / MOTW / Gatekeeper: code signing."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import offline_pack


class TestMacOSSigning:
    """§8.18, macOS notarization + Developer ID."""

    def test_returns_none_on_non_macos(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is None

    def test_returns_none_on_windows(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is None

    def test_codesign_not_found_returns_none(self, monkeypatch):
        """When ``codesign`` CLI is missing, returns None."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("codesign not installed")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is None

    def test_codesign_success_spctl_success_returns_true(self, monkeypatch):
        """When both ``codesign --verify`` and ``spctl --assess`` pass."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")

        def fake_run(cmd, **kwargs):
            cp = MagicMock()
            cp.returncode = 0
            cp.stdout = ""
            cp.stderr = ""
            return cp

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is True

    def test_codesign_failure_returns_false(self, monkeypatch):
        """When ``codesign --verify`` fails, returns False (without"""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        call_count = {"n": 0}

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            cp = MagicMock()
            # First call (codesign) fails.
            cp.returncode = 1 if call_count["n"] == 1 else 0
            cp.stdout = ""
            cp.stderr = "code signature failed"
            return cp

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is False

    def test_codesign_success_spctl_failure_returns_false(self, monkeypatch):
        """``codesign`` passes but ``spctl`` fails → notarization invalid."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        call_count = {"n": 0}

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            cp = MagicMock()
            # First call (codesign) passes; second (spctl) fails.
            cp.returncode = 0 if call_count["n"] == 1 else 1
            cp.stdout = ""
            cp.stderr = "" if call_count["n"] == 1 else "not notarized"
            return cp

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is False

    def test_subprocess_timeout_returns_none(self, monkeypatch):
        """A hanging ``codesign`` (timeout) returns None (graceful degrade)."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert offline_pack.verify_offline_pack_signature_macos(Path("/fake/worker")) is None


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
