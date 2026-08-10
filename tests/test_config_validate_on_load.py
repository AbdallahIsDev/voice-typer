"""Regression test for the fix.

``validate_config()`` in
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
def isolated_config_dir(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at an empty tmp_path for the duration
    of the test so ``Config.load()`` reads/writes only our isolated
    config.json.
    """
    # The canonical ``tmp_config_dir`` fixture already points
    # ``_config_dir()`` at the temp dir. Some Config.load() helpers
    # consult environment variables; clear the ones that would
    # override our isolated path.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    return tmp_config_dir


class TestValidateConfigCalledOnLoad:
    """``Config.load()`` must call ``validate_config`` on
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
            f"a valid config should produce no validate_config warnings. Got: {validate_warnings!r}"
        )


class TestValidateConfigGracefullyHandlesErrors:
    """if ``validate_config`` itself raises, ``Config.load()``
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


class TestNonFiniteFloatFieldsResetOnLoad:
    """NaN / +Inf / -Inf on a float field must be reset to the
    dataclass default at load time, with a warning recorded in
    ``last_load_warnings`` — and the next ``save()`` must NOT round-trip
    the non-finite value back to disk.

    Background: ``json.loads`` accepts ``NaN`` / ``Infinity`` /
    ``-Infinity`` as a non-standard extension, so a hand-edited or
    corrupted ``config.json`` can smuggle a non-finite float into any
    ``float`` dataclass field. Pre-fix, ``_validate_non_numeric_fields``
    did ``if isinstance(val, float): continue`` — a NaN/Inf IS a valid
    Python float, so the value passed through unchanged. The downstream
    ``scalar._make_float_validator`` flagged it with a "must be a finite
    number" warning, but the validator is advisory (it appends to
    ``last_load_warnings`` without mutating the field), so the bad value
    survived on the instance and ``save()`` wrote it back to disk via
    ``json.dumps`` (which by default emits bare ``NaN`` / ``Infinity``
    literals). The fix adds an explicit ``math.isnan`` / ``math.isinf``
    guard in the float branch of ``_validate_non_numeric_fields`` that
    resets the field to its dataclass default + appends a warning, and
    a second guard on the coerced value so a numeric STRING like
    ``"Infinity"`` is caught after ``float(...)`` parses it.

    These tests pin the load-time reset for ``streaming_silence_threshold``
    (a representative ``float`` field with default ``0.003``) across all
    three non-finite shapes (NaN, +Inf, -Inf) plus the numeric-string
    variant, and verify the reset actually closes the disk round-trip
    (``save()`` produces a JSON document with no ``NaN`` / ``Infinity``
    token for the field).
    """

    # ``streaming_silence_threshold: float = 0.003`` is a representative
    # float field on Config — small, has a finite default, and is read
    # by the streaming silence detector. ``nan``/``inf`` here would
    # silently disable the detector (``nan < 0.0`` is False).
    FIELD_NAME = "streaming_silence_threshold"
    FIELD_DEFAULT = 0.003

    def _write_config(self, config_dir: Path, value: object) -> Path:
        """Write a minimal config with ``FIELD_NAME`` set to ``value``.

        ``value`` is written via ``json.dumps`` so non-finite floats
        (NaN, Inf) are emitted as bare ``NaN`` / ``Infinity`` literals —
        the exact shape a hand-edited ``config.json`` would have, and
        the shape ``json.loads`` parses back to the non-finite float.
        """
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

        # Field must be reset to the dataclass default — NOT the NaN
        # that was on disk.
        assert instance.streaming_silence_threshold == self.FIELD_DEFAULT, (
            f"NaN must be reset to default {self.FIELD_DEFAULT!r}; got {instance.streaming_silence_threshold!r}"
        )
        # A warning must be recorded so the renderer can surface
        # "your config was corrected" via last_load_warnings.
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings, (
            "load() should append a 'non-finite float value' warning for "
            f"{self.FIELD_NAME!r}. last_load_warnings="
            f"{instance.last_load_warnings!r}"
        )
        # And the warning should mention the field was reset to the
        # default so the user knows what value they're now running with.
        assert any(repr(self.FIELD_DEFAULT) in w for w in reset_warnings), (
            f"the reset warning should mention the default value {self.FIELD_DEFAULT!r}. Got: {reset_warnings!r}"
        )
        # save() must NOT round-trip the NaN back to disk — the field
        # is now the default, so the on-disk file must NOT contain a
        # ``NaN`` token.
        instance.save()
        saved_text = config_file.read_text(encoding="utf-8")
        assert "NaN" not in saved_text, f"save() must not persist NaN. config.json now reads: {saved_text!r}"
        # And re-loading the saved file should reproduce the default
        # (no NaN, no warning the second time around).
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
        # ``float(...)`` in the float branch's coercion path. Pre-fix
        # this produced a non-finite float that survived to the
        # dataclass constructor + save() round-trip. The fix's second
        # guard (on the coerced value, not just on the raw value)
        # catches this case.
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
        # accidentally fire for a legitimate finite value inside the
        # field's valid range. ``0.05`` is inside the
        # ``streaming_silence_threshold`` range [0.0, 1.0] and well
        # away from the default ``0.003``, so a spurious reset would
        # show up as the field reverting to ``0.003`` and a warning
        # appearing.
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
        # (JSON has no int/float distinction). The non-finite check
        # runs only on floats, so an int must still load via the
        # coercion path without triggering a spurious reset.
        # ``streaming_silence_threshold`` accepts ``0`` (inside [0.0, 1.0]).
        self._write_config(isolated_config_dir, 0)

        instance = Config.load()

        # ``0`` coerces to ``0.0`` via the float branch's coercion path
        # (a separate ``_warn_and_coerce`` call that is unrelated to
        # the non-finite reset).
        assert instance.streaming_silence_threshold == 0.0, (
            f"int 0 must coerce to 0.0 for the float field; got {instance.streaming_silence_threshold!r}"
        )
        reset_warnings = [w for w in (instance.last_load_warnings or []) if self.FIELD_NAME in w and "non-finite" in w]
        assert reset_warnings == [], (
            f"no non-finite reset warning should fire for an int value. Got: {reset_warnings!r}"
        )
