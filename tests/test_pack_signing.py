"""§8.18 — SmartScreen / MOTW / Gatekeeper: code signing.

Spec (§8.18):

  Windows: the worker exe is signed with the same Authenticode
  certificate. The MOTW is removed after verification.

  macOS: the worker is signed with Developer ID + notarized via
  ``notarytool`` + stapled. Gatekeeper handles the quarantine.

  Linux: unsigned by design.

Tested behaviors (use ``pytest.importorskip`` for platform-specific
tests — non-Windows/non-macOS hosts skip the corresponding tests):

  1. ``verify_pack_signature_windows`` returns None on non-Windows.
  2. ``verify_pack_signature_macos`` returns None on non-macOS.
  3. On Windows, when ``wintrust.dll`` is unavailable (the internal
     ``_wintrust_verify`` returns None), the function returns None
     (graceful degrade).
  4. On macOS, when ``codesign`` is unavailable (FileNotFoundError),
     the function returns None.
  5. On macOS, when ``codesign --verify`` succeeds + ``spctl --assess``
     succeeds, returns True.
  6. On macOS, when ``codesign --verify`` fails, returns False.
  7. The signing-tool-unavailable case is documented as a SKIP, not a
     failure (per the slice spec: "skip if signing tools unavailable").
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import pack


class TestWindowsSigning:
    """§8.18 — Windows Authenticode."""

    def test_returns_none_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        assert pack.verify_pack_signature_windows(Path("/fake/worker.exe")) is None

    def test_returns_none_on_macos(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        assert pack.verify_pack_signature_windows(Path("/fake/worker.exe")) is None

    def test_wintrust_unavailable_returns_none(self, monkeypatch):
        """When ``wintrust.dll`` can't be loaded, returns None."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        # Make the internal helper return None (no wintrust).
        monkeypatch.setattr(pack, "_wintrust_verify", lambda c, p: None)
        assert pack.verify_pack_signature_windows(Path("/fake/worker.exe")) is None

    def test_wintrust_verify_true_passes_through(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(pack, "_wintrust_verify", lambda c, p: True)
        assert pack.verify_pack_signature_windows(Path("/fake/worker.exe")) is True

    def test_wintrust_verify_false_passes_through(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(pack, "_wintrust_verify", lambda c, p: False)
        assert pack.verify_pack_signature_windows(Path("/fake/worker.exe")) is False

    def test_wintrust_attribute_error_returns_none(self, monkeypatch):
        """A broken ctypes install (AttributeError) returns None."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")

        def boom(ctypes_mod, path):
            raise AttributeError("ctypes.windll missing")

        monkeypatch.setattr(pack, "_wintrust_verify", boom)
        assert pack.verify_pack_signature_windows(Path("/fake/worker.exe")) is None


class TestMacOSSigning:
    """§8.18 — macOS notarization + Developer ID."""

    def test_returns_none_on_non_macos(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is None

    def test_returns_none_on_windows(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is None

    def test_codesign_not_found_returns_none(self, monkeypatch):
        """When ``codesign`` CLI is missing, returns None."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("codesign not installed")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is None

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
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is True

    def test_codesign_failure_returns_false(self, monkeypatch):
        """When ``codesign --verify`` fails, returns False (without
        even calling ``spctl``)."""
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
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is False

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
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is False

    def test_subprocess_timeout_returns_none(self, monkeypatch):
        """A hanging ``codesign`` (timeout) returns None (graceful degrade)."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert pack.verify_pack_signature_macos(Path("/fake/worker")) is None


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
