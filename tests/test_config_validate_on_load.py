"""Regression test for the XZ-CFG-04 fix.

XZ-CFG-04 (High): ``validate_config()`` in
``voice_typer/server/config_validators.py`` was declared but NEVER
called from any production code path. The docstring's "Agent 2-a is
coordinated (via the worklog) to call it" comment was a stale
cross-agent TODO that was never executed. ``Config.load()`` did not
invoke the full-config validator, so a hand-edited ``config.json``
with an out-of-range value (e.g. ``noise_suppression_method="speex"``
left over from a pre-enum-tightening migration) loaded silently.

The fix calls ``validate_config(instance)`` at the end of
``Config.load()`` (after ``apply_preset``) and appends any returned
error strings to ``instance.last_load_warnings`` so the UI can
surface "your config has invalid values" instead of silently running
with a malformed state.

These tests pin the call site and verify the warnings flow through to
``last_load_warnings``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from voice_typer.server.config import Config


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at an empty tmp_path for the duration
    of the test so ``Config.load()`` reads/writes only our isolated
    config.json.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    # Some Config.load() helpers consult environment variables; clear
    # the ones that would override our isolated path.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    return tmp_path


class TestValidateConfigCalledOnLoad:
    """XZ-CFG-04: ``Config.load()`` must call ``validate_config`` on
    the constructed instance and append any errors to
    ``instance.last_load_warnings``.
    """

    def test_validate_config_is_called_on_load(
        self,
        isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Write a minimal valid config so load() exercises the full
        # path (file exists → parse → construct → validate).
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper"}),
            encoding="utf-8",
        )

        # Spy on ``validate_config`` — we don't want to change its
        # behaviour, just verify it was called with the constructed
        # Config instance.
        call_count = 0
        captured_instance: list[Config] = []

        from voice_typer.server import config_validators as cv

        real_validate = cv.validate_config

        def _spy(cfg):
            nonlocal call_count
            call_count += 1
            captured_instance.append(cfg)
            return real_validate(cfg)

        monkeypatch.setattr(cv, "validate_config", _spy)
        # Also patch the name ``Config.load`` looks up — it does a
        # local ``from voice_typer.server.config_validators import
        # validate_config`` inside the function body, so we need to
        # patch the attribute on the module BEFORE load() runs.
        # monkeypatch.setattr already does this; the ``from ... import``
        # inside load() re-binds to the patched attribute at call time.

        instance = Config.load()

        assert call_count >= 1, (
            "XZ-CFG-04: Config.load() must call validate_config() on the constructed instance. The call did not happen."
        )
        assert captured_instance[0] is instance, (
            "XZ-CFG-04: validate_config must be called with the constructed Config instance as its argument."
        )

    def test_validate_config_errors_appended_to_last_load_warnings(
        self,
        isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Write a config with a deliberately INVALID value that
        # ``validate_config`` will flag: ``language="invalid_lang"``
        # is rejected by the language validator (it's not in the
        # ALLOWED_LANGUAGES set).
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper", "language": "invalid_lang_xx"}),
            encoding="utf-8",
        )

        instance = Config.load()

        # The validator should have flagged the language field. The
        # warning lands in ``last_load_warnings`` as
        # ``"validate_config: language: <error>"``.
        validate_warnings = [w for w in instance.last_load_warnings if "validate_config:" in w]
        assert validate_warnings, (
            "XZ-CFG-04: validate_config() should flag the invalid "
            f"language value. last_load_warnings={instance.last_load_warnings!r}"
        )
        assert any("language" in w for w in validate_warnings), (
            "XZ-CFG-04: at least one validate_config warning should "
            f"mention the 'language' field. Got: {validate_warnings!r}"
        )

    def test_validate_config_no_warnings_for_valid_config(
        self,
        isolated_config_dir: Path,
    ) -> None:
        # A pristine default config has no invalid values, so
        # ``validate_config`` returns an empty list and no
        # ``validate_config:`` warnings appear in last_load_warnings.
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper"}),
            encoding="utf-8",
        )

        instance = Config.load()

        validate_warnings = [w for w in instance.last_load_warnings if "validate_config:" in w]
        assert validate_warnings == [], (
            f"XZ-CFG-04: a valid config should produce no validate_config warnings. Got: {validate_warnings!r}"
        )


class TestValidateConfigGracefullyHandlesErrors:
    """XZ-CFG-04: if ``validate_config`` itself raises, ``Config.load()``
    must NOT propagate the exception — the config still loads (the
    validator is best-effort / advisory).
    """

    def test_load_succeeds_when_validate_config_raises(
        self,
        isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper"}),
            encoding="utf-8",
        )

        from voice_typer.server import config_validators as cv

        def _boom(_cfg):
            raise RuntimeError("simulated validate_config failure")

        monkeypatch.setattr(cv, "validate_config", _boom)

        # Should NOT raise — validate_config is wrapped in try/except.
        instance = Config.load()
        assert isinstance(instance, Config)
