"""Tests for ``voice_typer/server/_electron_build.py`` (build/pack domain).

Split from the former catch-all test module
(2026-08-25). Covers the Electron dev-mode binary resolution
and the built-bundle completeness gate:

* Optional SHA-256 verification of the Electron binary.
* RACE-011 — ``_main_entry_built`` must require main + renderer +
  preload bundles (a partial build previously spawned a hidden
  zombie window holding the single-instance lock).
"""

from __future__ import annotations

import pytest


class TestElectronBinaryHashCheck:
    """Optional SHA-256 verification of the Electron binary.

    ``_electron_build._electron_binary`` returns the dev-mode Electron
    binary path with no integrity check by default. The fix adds an
    OPT-IN check: when ``VOICE_TYPER_ELECTRON_SHA256`` is set to a
    64-char hex SHA-256, the binary is hashed on disk and compared.
    On mismatch / unreadable file / malformed env var, the function
    returns ``None`` (forcing fallback to ``npm run dev``). When the
    env var is unset, behaviour is unchanged.
    """

    def test_no_env_var_returns_path_when_binary_exists(self, monkeypatch, tmp_path):
        """Without ``VOICE_TYPER_ELECTRON_SHA256``, return the path."""
        from voice_typer.server import _electron_build as eb

        fake_client = tmp_path / "voice_typer" / "client"
        (fake_client / "node_modules" / "electron" / "dist").mkdir(parents=True)
        binary = fake_client / "node_modules" / "electron" / "dist" / "electron"
        binary.write_bytes(b"fake-electron-binary")
        monkeypatch.setattr(eb, "CLIENT_DIR", fake_client)
        monkeypatch.setattr(eb, "is_windows", lambda: False)
        monkeypatch.delenv("VOICE_TYPER_ELECTRON_SHA256", raising=False)
        assert eb._electron_binary() == str(binary)

    def test_matching_sha256_returns_path(self, monkeypatch, tmp_path):
        """When the env var matches the binary's SHA-256, return path."""
        import hashlib

        from voice_typer.server import _electron_build as eb

        fake_client = tmp_path / "voice_typer" / "client"
        (fake_client / "node_modules" / "electron" / "dist").mkdir(parents=True)
        binary = fake_client / "node_modules" / "electron" / "dist" / "electron"
        binary.write_bytes(b"known-good-electron-binary")
        monkeypatch.setattr(eb, "CLIENT_DIR", fake_client)
        monkeypatch.setattr(eb, "is_windows", lambda: False)
        expected = hashlib.sha256(b"known-good-electron-binary").hexdigest()
        monkeypatch.setenv("VOICE_TYPER_ELECTRON_SHA256", expected)
        assert eb._electron_binary() == str(binary)

    def test_mismatching_sha256_returns_none(self, monkeypatch, tmp_path, caplog):
        """When the env var does NOT match, return ``None``."""
        import logging

        from voice_typer.server import _electron_build as eb

        fake_client = tmp_path / "voice_typer" / "client"
        (fake_client / "node_modules" / "electron" / "dist").mkdir(parents=True)
        binary = fake_client / "node_modules" / "electron" / "dist" / "electron"
        binary.write_bytes(b"tampered-electron-binary")
        monkeypatch.setattr(eb, "CLIENT_DIR", fake_client)
        monkeypatch.setattr(eb, "is_windows", lambda: False)
        wrong_sha = "0" * 64
        monkeypatch.setenv("VOICE_TYPER_ELECTRON_SHA256", wrong_sha)
        with caplog.at_level(logging.ERROR, logger="voice_typer.server._electron_build"):
            result = eb._electron_binary()
        assert result is None
        assert any("CHECKSUM MISMATCH" in r.message for r in caplog.records)

    def test_malformed_env_var_returns_none(self, monkeypatch, tmp_path, caplog):
        """A non-hex env var value is rejected (rather than guessing)."""
        import logging

        from voice_typer.server import _electron_build as eb

        fake_client = tmp_path / "voice_typer" / "client"
        (fake_client / "node_modules" / "electron" / "dist").mkdir(parents=True)
        binary = fake_client / "node_modules" / "electron" / "dist" / "electron"
        binary.write_bytes(b"electron-binary")
        monkeypatch.setattr(eb, "CLIENT_DIR", fake_client)
        monkeypatch.setattr(eb, "is_windows", lambda: False)
        # "xyz" * 32 = 96 chars, not a valid 64-char hex SHA-256.
        monkeypatch.setenv("VOICE_TYPER_ELECTRON_SHA256", "xyz" * 32)
        with caplog.at_level(logging.ERROR, logger="voice_typer.server._electron_build"):
            result = eb._electron_binary()
        assert result is None
        assert any("not a 64-char hex SHA-256" in r.message for r in caplog.records)

    def test_missing_binary_returns_none_with_env_var_set(self, monkeypatch, tmp_path):
        """No binary on disk + env var set → ``None``."""
        from voice_typer.server import _electron_build as eb

        fake_client = tmp_path / "voice_typer" / "client"
        monkeypatch.setattr(eb, "CLIENT_DIR", fake_client)
        monkeypatch.setattr(eb, "is_windows", lambda: False)
        monkeypatch.setenv("VOICE_TYPER_ELECTRON_SHA256", "0" * 64)
        assert eb._electron_binary() is None


class TestMainEntryBuiltRequiresAllBundles:
    """RACE-011: ``_main_entry_built`` must require main + renderer + preload.

    A pre-built check that only looked at ``out/main/index.js`` let the
    launchers spawn ``electron .`` with a missing renderer bundle — the
    window never showed (did-fail-load ERR_FILE_NOT_FOUND) and the
    process lingered as a hidden zombie holding the single-instance
    lock, silently killing every later launch.
    """

    def _built_tree(self, tmp_path):
        from voice_typer.server import _electron_build as eb

        fake_client = tmp_path / "voice_typer" / "client"
        out = fake_client / "out"
        (out / "main").mkdir(parents=True)
        (out / "preload").mkdir(parents=True)
        (out / "renderer").mkdir(parents=True)
        (out / "main" / "index.js").write_text("// main")
        (out / "preload" / "index.js").write_text("// preload")
        (out / "renderer" / "index.html").write_text("<html></html>")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(eb, "CLIENT_DIR", fake_client)
        return eb, monkeypatch

    def test_true_when_all_three_bundles_exist(self, tmp_path):
        eb, m = self._built_tree(tmp_path)
        try:
            assert eb._main_entry_built() is True
        finally:
            m.undo()

    def test_false_when_renderer_missing(self, tmp_path):
        eb, m = self._built_tree(tmp_path)
        try:
            (tmp_path / "voice_typer" / "client" / "out" / "renderer" / "index.html").unlink()
            assert eb._main_entry_built() is False
        finally:
            m.undo()

    def test_false_when_preload_missing(self, tmp_path):
        eb, m = self._built_tree(tmp_path)
        try:
            (tmp_path / "voice_typer" / "client" / "out" / "preload" / "index.js").unlink()
            assert eb._main_entry_built() is False
        finally:
            m.undo()

    def test_false_when_main_missing(self, tmp_path):
        eb, m = self._built_tree(tmp_path)
        try:
            (tmp_path / "voice_typer" / "client" / "out" / "main" / "index.js").unlink()
            assert eb._main_entry_built() is False
        finally:
            m.undo()
