"""§8.5 — Metered connection detection (Windows NLM only).

Spec (§8.5):

  Windows — ``NLM`` API via ``ctypes``/``comtypes``. Linux/macOS — no
  reliable detection; the setting is manual ("Download offline engine
  later"). Default: auto-download on Windows, manual on Linux/macOS
  (until a reliable detection API is found).

Tested behaviors:

  1. On Linux/macOS, ``is_metered_connection_windows`` returns None
     (detection unavailable — manual setting).
  2. On Windows, when NLM COM is unavailable (returns None from the
     internal helper), the function returns None (graceful degrade).
  3. On Windows, when NLM reports unmetered (helper returns False),
     the function returns False.
  4. On Windows, when NLM reports metered (helper returns True), the
     function returns True.
  5. ``pytest.importorskip`` is NOT used — the test runs on all
     platforms by monkeypatching ``platform.system``.
"""

from __future__ import annotations

import platform

import pytest
from voice_typer.server.service import pack


class TestMeteredDetection:
    """§8.5 — Windows NLM metered detection; None elsewhere."""

    def test_non_windows_returns_none(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        assert pack.is_metered_connection_windows() is None

    def test_macos_returns_none(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        assert pack.is_metered_connection_windows() is None

    def test_windows_nlm_unavailable_returns_none(self, monkeypatch):
        """When NLM COM is unavailable, the function returns None."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")

        # Replace the internal NLM helper with one that raises (COM
        # not registered). The public function should swallow the
        # exception and return None.
        def boom(ctypes_mod, wintypes_mod):
            raise OSError("NLM COM not registered")

        monkeypatch.setattr(pack, "_nlm_detect_metered", boom)
        assert pack.is_metered_connection_windows() is None

    def test_windows_nlm_reports_unmetered(self, monkeypatch):
        """NLM reports unmetered (cost flags = FIXED or UNRESTRICTED)."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(pack, "_nlm_detect_metered", lambda c, w: False)
        assert pack.is_metered_connection_windows() is False

    def test_windows_nlm_reports_metered(self, monkeypatch):
        """NLM reports metered (cost flags = VARIABLE / ROAMING / OVERDATALIMIT)."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(pack, "_nlm_detect_metered", lambda c, w: True)
        assert pack.is_metered_connection_windows() is True

    def test_windows_attribute_error_returns_none(self, monkeypatch):
        """A broken ctypes install (AttributeError) returns None."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")

        def boom(ctypes_mod, wintypes_mod):
            raise AttributeError("ctypes.windll missing")

        monkeypatch.setattr(pack, "_nlm_detect_metered", boom)
        assert pack.is_metered_connection_windows() is None


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
