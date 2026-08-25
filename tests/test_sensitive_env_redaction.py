"""Sensitive environment-variable redaction in the spawn/launch paths.

Split from the former catch-all test module (2026-08-25).
``_redact_sensitive_env_keys``
redacts values and surfaces only sensitive KEY NAMES for the audit
log line — the helper lives in
``voice_typer.server._electron_build`` and is called by
``electron_launcher.spawn_electron`` and the three
``autostart_launcher`` spawn paths after ``env = dict(os.environ)``.

Related but distinct from ``tests/test_env_validation_sensitive_env.py``
(which covers ``env_validation._validate_env_vars`` stripping of the
Electron FRONTEND child's environment): this file covers the audit-log
redaction helper + its wiring into every backend spawn path.
"""

from __future__ import annotations

import inspect


class TestSensitiveEnvRedaction:
    """Helper that surfaces sensitive env KEY NAMES only.

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
        assert not any("[ENV]" in rec.message for rec in caplog.records), (
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
        priv_records = [r for r in caplog.records if "[ENV]" in r.message]
        assert len(priv_records) == 1, f"Expected exactly one [ENV] audit line, got: {priv_records}"
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
            "electron_launcher.py must call _log_sensitive_env_keys after env = dict(os.environ)"
        )

    def test_autostart_launcher_calls_log_helper_in_all_spawn_paths(self):
        """``autostart_launcher`` has THREE spawn paths (the autostart
        electron spawn, the focus-running-app lean electron spawn, and
        the npm-run-dev fallback). All three must call the helper.
        """
        # The spawn paths live in the autostart subpackage since the
        # launcher was split into an entry facade + leaf modules; scan
        # the facade AND every leaf so the guard covers all of them.
        from voice_typer.server import autostart_launcher
        from voice_typer.server.autostart import (
            electron_spawn,
            focus,
            tauri_spawn,
        )

        source = "\n".join(inspect.getsource(m) for m in (autostart_launcher, electron_spawn, focus, tauri_spawn))
        occurrences = source.count("_log_sensitive_env_keys")
        # One occurrence per spawn path (3 Electron/Tauri spawn paths,
        # plus the Tauri host + focus-probe spawns) + the re-export
        # imports. We require at least 3 (one per original spawn path)
        # to be robust to future import-style changes.
        assert occurrences >= 3, (
            f"the autostart launcher (facade + autostart/ package) must call "
            f"_log_sensitive_env_keys in "
            f"all 3 spawn paths; found {occurrences} references "
            f"(expected >= 3)"
        )
