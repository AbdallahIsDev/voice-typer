"""§8.11 — App-data folder write blocked: fallback directory.

Spec (§8.11):

  Fall back to the user's roaming folder (Windows) / ``~/.voice-typer``
  (POSIX). If that's blocked too, run in "core-only mode" with one
  clear message. Rare, but handled.

Tested behaviors:

  1. ``fallback_pack_root`` returns a Windows roaming path on Windows.
  2. ``fallback_pack_root`` returns ``~/.voice-typer/runtime-pack`` on
     POSIX.
  3. ``fallback_pack_root`` returns None when ``$HOME`` is unset (the
     "core-only mode" escape).
  4. ``pack_dir_for_version`` uses the fallback root when passed
     explicitly.
  5. The fallback path resolves correctly under a non-default ``root``.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


class TestFallbackPackRoot:
    """§8.11 — fallback pack root resolution."""

    def test_windows_roaming_fallback(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
        root = offline_pack.fallback_offline_pack_root()
        assert root is not None
        assert "Roaming" in str(root)
        assert "voice-typer" in str(root)
        assert "runtime-pack" in str(root)

    def test_posix_home_voice_typer_fallback(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(Path, "home", lambda *a, **kw: tmp_path)
        root = offline_pack.fallback_offline_pack_root()
        assert root is not None
        assert root == tmp_path / ".voice-typer" / "runtime-pack"

    def test_posix_no_home_returns_none(self, monkeypatch):
        """When ``$HOME`` is unset, no fallback is available → None."""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        # Make ``Path.home()`` return an empty Path (simulates
        # ``$HOME`` unset on a misconfigured system).
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("")))
        root = offline_pack.fallback_offline_pack_root()
        assert root is None

    def test_windows_no_appdata_uses_home(self, monkeypatch):
        """Windows: when ``%APPDATA%`` is unset, fall back to ``$HOME``."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.delenv("APPDATA", raising=False)
        fake_home = Path("/fake/home")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        root = offline_pack.fallback_offline_pack_root()
        assert root is not None
        assert "Roaming" in str(root)
        assert "voice-typer" in str(root)


class TestPackDirUsesFallback:
    """§8.11 — pack_dir_for_version honors explicit fallback root."""

    def test_pack_dir_uses_explicit_root(self, tmp_path: Path):
        fallback = tmp_path / "fallback" / "runtime-pack"
        d = offline_pack.offline_pack_dir_for_version("v2", root=fallback)
        assert d == fallback / "v2"

    def test_pack_dir_default_root_uses_env(self, monkeypatch, tmp_path: Path):
        """When no root is passed, the default root honors ``VT_PACK_ROOT``."""
        monkeypatch.setenv("VT_PACK_ROOT", str(tmp_path / "custom-pack"))
        d = offline_pack.offline_pack_dir_for_version("v1")
        assert d == tmp_path / "custom-pack" / "v1"

    def test_manifest_path_uses_explicit_root(self, tmp_path: Path):
        fallback = tmp_path / "fb" / "runtime-pack"
        p = offline_pack.offline_pack_manifest_path("v3", root=fallback)
        assert p == fallback / "v3" / "pack-manifest.json"

    def test_partial_path_uses_explicit_root(self, tmp_path: Path):
        fallback = tmp_path / "fb" / "runtime-pack"
        p = offline_pack.offline_pack_partial_path("v3", root=fallback)
        assert p == fallback / "v3" / "pack-v3.partial"

    def test_lock_path_uses_explicit_root(self, tmp_path: Path):
        fallback = tmp_path / "fb" / "runtime-pack"
        p = offline_pack.offline_pack_lock_path("v3", root=fallback)
        assert p == fallback / "v3" / "pack-v3.lock"


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
