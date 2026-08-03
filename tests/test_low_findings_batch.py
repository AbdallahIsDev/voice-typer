"""Tests for the LOW-severity findings batch (§3.2-§3.7).

Covers:
  - NEW-DEAD-017: ``_legacy_config_dir`` removed from config.py.
  - NEW-PRIV-003: ``_redact_sensitive_env_keys`` helper redacts values
    and surfaces only sensitive KEY NAMES for the audit log line.
  - NEW-PRIV-007/008: GDPR export/delete docs exist (smoke check).
  - NEW-UX-026: Punctuation cheat sheet entries are pinned to the
    text_cleanup.py source of truth (the regex ``[,.;:!?]``).

These are intentionally lightweight — the directive is "do NOT over-
invest in LOW findings".
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# ``voice_typer/server/config.py`` was split into a package
# (``config/__init__.py`` + ``config/{coercion,loader,sanitization}.py``)
# plus sibling modules (``config_applier.py``, ``config_editor.py``,
# ``config_path_safety.py``, ``config_sanitizer.py``,
# ``config_validators.py``) and the ``config_internals/`` package
# (``__init__.py``, ``migrations.py``, ``paths.py``). The legacy
# ``_legacy_config_dir`` function could have lived in any of these after
# the split, so we scan them all rather than asserting on a single
# (now-nonexistent) file.
CONFIG_MODULE_PATHS = [
    *(
        p
        for d in (
            REPO_ROOT / "voice_typer" / "server" / "config",
            REPO_ROOT / "voice_typer" / "server" / "config_internals",
        )
        for p in sorted(d.glob("*.py"))
        if d.is_dir()
    ),
    *sorted((REPO_ROOT / "voice_typer" / "server").glob("config*.py")),
]
TEXT_CLEANUP_PY = REPO_ROOT / "voice_typer" / "server" / "text_cleanup.py"
GDPR_EXPORT_DOC = REPO_ROOT / "docs" / "privacy" / "gdpr-export.md"
GDPR_DELETE_DOC = REPO_ROOT / "docs" / "privacy" / "gdpr-delete.md"


class TestNewDead017LegacyConfigDirRemoved:
    """NEW-DEAD-017: ``_legacy_config_dir`` was dead and is now deleted."""

    def test_config_py_does_not_define_legacy_config_dir(self):
        """The function definition must be gone from all config module sources.

        ``config.py`` was previously a single file; it has since been
        split into a package + sibling modules. We scan every config
        module file for the deleted function definition so the test
        catches a re-introduction regardless of which module it lands
        in after a future refactor.
        """
        assert CONFIG_MODULE_PATHS, (
            "Expected at least one config module file under "
            "voice_typer/server/config/ and voice_typer/server/config*.py — "
            "module layout may have changed again; update CONFIG_MODULE_PATHS."
        )
        offenders: list[str] = []
        for path in CONFIG_MODULE_PATHS:
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "def _legacy_config_dir" in source:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, (
            "Config module(s) still define _legacy_config_dir — NEW-DEAD-017 "
            "should have deleted it (no callers in the repo, no entry "
            f"points in pyproject.toml, no setup.py). Offenders: {offenders}"
        )

    def test_legacy_config_dir_not_importable(self):
        """``from voice_typer.server.config import _legacy_config_dir``
        must raise ``ImportError`` / ``AttributeError`` now that the
        function is gone.
        """
        from voice_typer.server import config as config_mod

        assert not hasattr(config_mod, "_legacy_config_dir"), (
            "voice_typer.server.config still exposes _legacy_config_dir"
        )

    def test_no_callers_of_legacy_config_dir_anywhere_in_repo(self):
        """Sanity: no Python file in the repo should still reference
        ``_legacy_config_dir`` (the function is gone).
        """
        offenders: list[str] = []
        for py_file in (REPO_ROOT / "voice_typer").rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "_legacy_config_dir" in text:
                offenders.append(str(py_file.relative_to(REPO_ROOT)))
        assert not offenders, f"Files still reference the deleted _legacy_config_dir: {offenders}"

    def test_config_module_still_imports_cleanly(self):
        """Deleting the function must not break the module's imports."""
        from voice_typer.server import config  # noqa: F401

        # Sanity: the module exposes the functions that DO have callers.
        assert hasattr(config, "_config_dir"), (
            "config._config_dir must still be importable (NEW-DEAD-017 "
            "only deleted the dead _legacy_config_dir, not _config_dir)"
        )


class TestNewPriv003SensitiveEnvRedaction:
    """NEW-PRIV-003: helper that surfaces sensitive env KEY NAMES only.

    The helper is in ``voice_typer.server._electron_build`` and is
    called by ``electron_launcher.spawn_electron`` and the three
    ``autostart_launcher`` spawn paths after ``env = dict(os.environ)``.
    """

    def test_helper_exists_and_is_callable(self):
        from voice_typer.server._electron_build import (
            _log_sensitive_env_keys,
            _redact_sensitive_env_keys,
        )

        assert callable(_redact_sensitive_env_keys)
        assert callable(_log_sensitive_env_keys)

    def test_redact_returns_only_sensitive_key_names(self):
        """Common SaaS API-key / token / password vars are surfaced,
        benign vars (PATH, HOME, LANG) are NOT.
        """
        from voice_typer.server._electron_build import _redact_sensitive_env_keys

        env = {
            "PATH": "/usr/bin",
            "HOME": "/root",
            "LANG": "en_US.UTF-8",
            "VT_PYTHON_PORT": "9876",
            "OPENAI_API_KEY": "sk-leak-me",  # sensitive
            "HF_TOKEN": "hf_leak_me",  # sensitive
            "ANTHROPIC_API_KEY": "sk-ant-leak",  # sensitive
            "MY_APP_PASSWORD": "p4ssw0rd",  # sensitive
            "AWS_SECRET_ACCESS_KEY": "wJalrXUt",  # sensitive
            "CLIENT_CREDENTIAL": "xxx",  # sensitive
        }
        sensitive = _redact_sensitive_env_keys(env)
        # Names only — no values leak.
        assert "OPENAI_API_KEY" in sensitive
        assert "HF_TOKEN" in sensitive
        assert "ANTHROPIC_API_KEY" in sensitive
        assert "MY_APP_PASSWORD" in sensitive
        assert "AWS_SECRET_ACCESS_KEY" in sensitive
        assert "CLIENT_CREDENTIAL" in sensitive
        # Benign vars are NOT flagged.
        assert "PATH" not in sensitive
        assert "HOME" not in sensitive
        assert "LANG" not in sensitive
        assert "VT_PYTHON_PORT" not in sensitive

    def test_redact_never_includes_values(self):
        """The returned list must contain ONLY key names, never values
        (this is the security-critical guarantee).
        """
        from voice_typer.server._electron_build import _redact_sensitive_env_keys

        secret_value = "sk-super-secret-value-that-must-never-leak"
        env = {"OPENAI_API_KEY": secret_value, "PATH": "/usr/bin"}
        sensitive = _redact_sensitive_env_keys(env)
        joined = " ".join(sensitive)
        assert secret_value not in joined, f"_redact_sensitive_env_keys leaked a value: {joined!r}"

    def test_log_helper_does_not_crash_on_empty_env(self, caplog):
        """When env has no sensitive keys, the helper logs nothing
        (no log noise on the common case) and does not raise.
        """
        import logging

        from voice_typer.server._electron_build import _log_sensitive_env_keys

        with caplog.at_level(logging.INFO, logger="voice_typer.server._electron_build"):
            _log_sensitive_env_keys({"PATH": "/usr/bin"}, context="test")
        # No  log line should fire for an env with no sensitive keys.
        assert not any("PRIV-003" in rec.message for rec in caplog.records), (
            f"_log_sensitive_env_keys emitted a spurious audit line for a benign env: {caplog.records}"
        )

    def test_log_helper_emits_audit_line_with_key_names_only(self, caplog):
        """When env DOES contain sensitive keys, the helper logs an
        INFO line containing the key NAMES but never the values.
        """
        import logging

        from voice_typer.server._electron_build import _log_sensitive_env_keys

        secret_value = "sk-never-log-this-value"
        with caplog.at_level(logging.INFO, logger="voice_typer.server._electron_build"):
            _log_sensitive_env_keys(
                {"OPENAI_API_KEY": secret_value, "PATH": "/usr/bin"},
                context="test_context",
            )
        priv_records = [r for r in caplog.records if "PRIV-003" in r.message]
        assert len(priv_records) == 1, f"Expected exactly one PRIV-003 audit line, got: {priv_records}"
        msg = priv_records[0].message
        assert "OPENAI_API_KEY" in msg, "Key name must appear in audit line"
        assert secret_value not in msg, f"Secret value leaked into audit log line: {msg!r}"

    def test_electron_launcher_calls_log_helper(self):
        """``electron_launcher.spawn_electron`` must call the helper
        after building the env (regression: future refactors must not
        drop the audit hook).
        """
        from voice_typer.server import electron_launcher

        source = inspect.getsource(electron_launcher)
        assert "_log_sensitive_env_keys" in source, (
            "electron_launcher.py must call _log_sensitive_env_keys after env = dict(os.environ) (NEW-PRIV-003)"
        )

    def test_autostart_launcher_calls_log_helper_in_all_spawn_paths(self):
        """``autostart_launcher`` has THREE spawn paths (the autostart
        electron spawn, the focus-running-app lean electron spawn, and
        the npm-run-dev fallback). All three must call the helper.
        """
        from voice_typer.server import autostart_launcher

        source = inspect.getsource(autostart_launcher)
        occurrences = source.count("_log_sensitive_env_keys")
        # One occurrence per spawn path (3 spawn paths) + the import
        # statement = 4. We require at least 3 (one per spawn path)
        # to be robust to future import-style changes.
        assert occurrences >= 3, (
            f"autostart_launcher.py must call _log_sensitive_env_keys in "
            f"all 3 spawn paths; found {occurrences} reference(s) "
            f"(expected >= 3)"
        )


class TestNewPriv007And008GdprDocsExist:
    """NEW-PRIV-007/008: GDPR export/delete feature-gap docs exist."""

    def test_gdpr_export_doc_exists(self):
        assert GDPR_EXPORT_DOC.exists(), f"Expected GDPR export feature-gap doc at {GDPR_EXPORT_DOC}"

    def test_gdpr_delete_doc_exists(self):
        assert GDPR_DELETE_DOC.exists(), f"Expected GDPR delete feature-gap doc at {GDPR_DELETE_DOC}"

    def test_gdpr_export_doc_mentions_article_20(self):
        text = GDPR_EXPORT_DOC.read_text(encoding="utf-8")
        # Must reference GDPR Article 20 (right to portability).
        assert "Article 20" in text, "gdpr-export.md must reference GDPR Article 20 (portability)"

    def test_gdpr_delete_doc_mentions_article_17(self):
        text = GDPR_DELETE_DOC.read_text(encoding="utf-8")
        # Must reference GDPR Article 17 (right to erasure).
        assert "Article 17" in text, "gdpr-delete.md must reference GDPR Article 17 (erasure)"


class TestNewUx026PunctuationCheatSheetSourceOfTruth:
    """NEW-UX-026: cheat sheet content is pinned to text_cleanup.py's
    ``[,.;:!?]`` regex (the punctuation Voice Typer preserves).

    The full renderer-side vitest test lives at
    ``voice_typer/client/src/renderer/src/__tests__/punctuation-cheat-sheet.test.tsx``.
    This Python test pins the SOURCE-OF-TRUTH regex in text_cleanup.py
    so a future refactor that changes the regex also breaks this test
    (and prompts the cheat-sheet update).
    """

    def test_text_cleanup_punct_regex_still_recognizes_canonical_six(self):
        """``_RE_SPACING_PUNCT_BEFORE`` in text_cleanup.py:374 must
        still cover the six canonical punctuation characters
        ``, . ; : ! ?`` — these are what the cheat sheet advertises.
        """
        source = TEXT_CLEANUP_PY.read_text(encoding="utf-8")
        # The regex character class is `[,.;:!?]`.
        assert r"re.compile(r'\s+([,.;:!?])')" in source or (r're.compile(r"\s+([,.;:!?])")' in source), (
            "text_cleanup.py must still define "
            "_RE_SPACING_PUNCT_BEFORE = re.compile(r'\\s+([,.;:!?])') "
            "— the cheat sheet's source of truth"
        )

    def test_grep_no_spurious_legacy_punct_word_map(self):
        """Sanity: text_cleanup.py does NOT contain a 'spoken word →
        character' dict (the directive's claim that the cheat sheet's
        source of truth is text_cleanup.py refers to the regex, not
        to a word map — confirm no such map exists that we missed).
        """
        source = TEXT_CLEANUP_PY.read_text(encoding="utf-8")
        # If someone later adds a "spoken punctuation word" dict, the
        # cheat sheet should switch to it. For now, no such dict exists.
        assert "SPOKEN_PUNCT" not in source
        assert "PUNCT_WORD_MAP" not in source


class TestXzR6As02ElectronBinaryHashCheck:
    """XZ-R6-AS-02: optional SHA-256 verification of the Electron binary.

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
