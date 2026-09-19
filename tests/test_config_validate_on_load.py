"""Regression test for the fix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from voice_typer.server.config import Config


@pytest.fixture
def isolated_config_dir(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at an empty tmp_path for the duration"""
    # The canonical ``tmp_config_dir`` fixture already points
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    return tmp_config_dir


class TestValidateConfigCalledOnLoad:
    """``instance.last_load_warnings``."""

    def test_validate_config_is_called_on_load(
        self,
        isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Write a minimal valid config so load() exercises the full
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper"}),
            encoding="utf-8",
        )

        # Spy on ``validate_config``, we don't want to change its
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

        instance = Config.load()

        assert call_count >= 1, (
            "Config.load() must call validate_config() on the constructed instance. The call did not happen."
        )
        assert captured_instance[0] is instance, (
            "validate_config must be called with the constructed Config instance as its argument."
        )

    def test_validate_config_errors_appended_to_last_load_warnings(
        self,
        isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper", "language": "invalid_lang_xx"}),
            encoding="utf-8",
        )

        instance = Config.load()

        validate_warnings = [w for w in instance.last_load_warnings if "validate_config:" in w]
        assert validate_warnings, (
            "validate_config() should flag the invalid "
            f"language value. last_load_warnings={instance.last_load_warnings!r}"
        )
        assert any("language" in w for w in validate_warnings), (
            f"at least one validate_config warning should mention the 'language' field. Got: {validate_warnings!r}"
        )

    def test_validate_config_no_warnings_for_valid_config(
        self,
        isolated_config_dir: Path,
    ) -> None:
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper"}),
            encoding="utf-8",
        )

        instance = Config.load()

        validate_warnings = [w for w in instance.last_load_warnings if "validate_config:" in w]
        assert validate_warnings == [], (
            f"a valid config should produce no validate_config warnings. Got: {validate_warnings!r}"
        )


class TestValidateConfigGracefullyHandlesErrors:
    """if ``validate_config`` itself raises, ``Config.load()``"""

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

        # Should NOT raise, validate_config is wrapped in try/except.
        instance = Config.load()
        assert isinstance(instance, Config)


class TestNonFiniteFloatFieldsResetOnLoad:
    """``last_load_warnings``, and the next ``save()`` must NOT round-trip"""

    # ``streaming_silence_threshold: float = 0.003`` is a representative
    FIELD_NAME = "streaming_silence_threshold"
    FIELD_DEFAULT = 0.003

    def _write_config(self, config_dir: Path, value: object) -> Path:
        """Write a minimal config with ``FIELD_NAME`` set to ``value``."""
        config_file = config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "asr_backend": "whisper",
                    self.FIELD_NAME: value,
                }
            ),
            encoding="utf-8",
        )
        return config_file

    def test_nan_float_is_reset_to_default(
        self,
        isolated_config_dir: Path,
    ) -> None:
        config_file = self._write_config(isolated_config_dir, float("nan"))

        instance = Config.load()

        # Field must be reset to the dataclass default, NOT the NaN
        assert instance.streaming_silence_threshold == self.FIELD_DEFAULT, (
            f"NaN must be reset to default {self.FIELD_DEFAULT!r}; got {instance.streaming_silence_threshold!r}"
        )
        # A warning must be recorded so the renderer can surface
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings, (
            "load() should append a 'non-finite float value' warning for "
            f"{self.FIELD_NAME!r}. last_load_warnings="
            f"{instance.last_load_warnings!r}"
        )
        assert any(repr(self.FIELD_DEFAULT) in w for w in reset_warnings), (
            f"the reset warning should mention the default value {self.FIELD_DEFAULT!r}. Got: {reset_warnings!r}"
        )
        # save() must NOT round-trip the NaN back to disk, the field
        # is now the default, so the on-disk file must NOT contain a
        instance.save()
        saved_text = config_file.read_text(encoding="utf-8")
        assert "NaN" not in saved_text, f"save() must not persist NaN. config.json now reads: {saved_text!r}"
        # And re-loading the saved file should reproduce the default
        reloaded = Config.load()
        assert reloaded.streaming_silence_threshold == self.FIELD_DEFAULT

    def test_positive_infinity_float_is_reset_to_default(
        self,
        isolated_config_dir: Path,
    ) -> None:
        config_file = self._write_config(isolated_config_dir, float("inf"))

        instance = Config.load()

        assert instance.streaming_silence_threshold == self.FIELD_DEFAULT, (
            f"+Inf must be reset to default {self.FIELD_DEFAULT!r}; got {instance.streaming_silence_threshold!r}"
        )
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings, (
            "load() should append a 'non-finite float value' warning for "
            f"+Inf on {self.FIELD_NAME!r}. last_load_warnings="
            f"{instance.last_load_warnings!r}"
        )
        instance.save()
        saved_text = config_file.read_text(encoding="utf-8")
        assert "Infinity" not in saved_text, f"save() must not persist +Infinity. config.json now reads: {saved_text!r}"

    def test_negative_infinity_float_is_reset_to_default(
        self,
        isolated_config_dir: Path,
    ) -> None:
        config_file = self._write_config(isolated_config_dir, float("-inf"))

        instance = Config.load()

        assert instance.streaming_silence_threshold == self.FIELD_DEFAULT, (
            f"-Inf must be reset to default {self.FIELD_DEFAULT!r}; got {instance.streaming_silence_threshold!r}"
        )
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings, (
            "load() should append a 'non-finite float value' warning for "
            f"-Inf on {self.FIELD_NAME!r}. last_load_warnings="
            f"{instance.last_load_warnings!r}"
        )
        instance.save()
        saved_text = config_file.read_text(encoding="utf-8")
        assert "-Infinity" not in saved_text, (
            f"save() must not persist -Infinity. config.json now reads: {saved_text!r}"
        )

    def test_numeric_string_infinity_is_reset_to_default(
        self,
        isolated_config_dir: Path,
    ) -> None:
        # A numeric STRING like ``"Infinity"`` parses successfully via
        self._write_config(isolated_config_dir, "Infinity")

        instance = Config.load()

        assert instance.streaming_silence_threshold == self.FIELD_DEFAULT, (
            f"'Infinity' string must be coerced then reset to default "
            f"{self.FIELD_DEFAULT!r}; got {instance.streaming_silence_threshold!r}"
        )
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings, (
            "load() should append a 'non-finite float value' warning when "
            f"a numeric string parses to a non-finite float. "
            f"last_load_warnings={instance.last_load_warnings!r}"
        )

    def test_finite_float_still_loads_without_reset_warning(
        self,
        isolated_config_dir: Path,
    ) -> None:
        # Regression guard: the new non-finite check must NOT
        self._write_config(isolated_config_dir, 0.05)

        instance = Config.load()

        assert instance.streaming_silence_threshold == 0.05, (
            f"a finite in-range float must load unchanged; got {instance.streaming_silence_threshold!r}"
        )
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings == [], (
            f"no non-finite reset warning should fire for a legitimate finite value. Got: {reset_warnings!r}"
        )

    def test_int_value_for_float_field_still_loads_without_reset_warning(
        self,
        isolated_config_dir: Path,
    ) -> None:
        # Regression guard: int values are accepted for float fields
        self._write_config(isolated_config_dir, 0)

        instance = Config.load()

        # ``0`` coerces to ``0.0`` via the float branch's coercion path
        assert instance.streaming_silence_threshold == 0.0, (
            f"int 0 must coerce to 0.0 for the float field; got {instance.streaming_silence_threshold!r}"
        )
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings == [], (
            f"no non-finite reset warning should fire for an int value. Got: {reset_warnings!r}"
        )
