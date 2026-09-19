"""Tests for the fixes"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from voice_typer.server.config_validators import (
    _ALLOWED_LANGUAGES,
    _ALLOWED_LANGUAGES_SOURCE,
    _HOTKEY_FIELD_NAMES,
    _check_cross_field_hotkey_conflicts,
    _cross_platform_hotkey_warning,
    _make_custom_theme_validator,
    _validate_language,
    cross_platform_hotkey_warnings,
    validate_config,
    validate_config_update,
)


class TestCrossFieldHotkeyConflicts:
    """Direct tests for :func:`_check_cross_field_hotkey_conflicts`."""

    def test_no_conflict_when_both_differ(self) -> None:
        errors = _check_cross_field_hotkey_conflicts(
            {
                "hotkey": "<f5>",
                "repaste_hotkey": "<f6>",
            }
        )
        assert errors == []

    def test_conflict_between_hotkey_and_repaste(self) -> None:
        errors = _check_cross_field_hotkey_conflicts(
            {
                "hotkey": "<ctrl>+<space>",
                "repaste_hotkey": "<ctrl>+<space>",
            }
        )
        assert len(errors) == 1
        assert "'hotkey'" in errors[0]
        assert "'repaste_hotkey'" in errors[0]
        assert "<ctrl>+<space>" in errors[0]

    def test_none_values_do_not_conflict(self) -> None:
        errors = _check_cross_field_hotkey_conflicts({"hotkey": None, "repaste_hotkey": None})
        assert errors == []

    def test_empty_strings_do_not_conflict(self) -> None:
        # Empty strings are treated as "not set", two unset hotkeys
        errors = _check_cross_field_hotkey_conflicts({"hotkey": "", "repaste_hotkey": ""})
        assert errors == []

    def test_case_insensitive_matching(self) -> None:
        # <CTRL>+<SPACE> and <ctrl>+<space> must normalize to the same
        errors = _check_cross_field_hotkey_conflicts(
            {
                "hotkey": "<CTRL>+<SPACE>",
                "repaste_hotkey": "<ctrl>+<space>",
            }
        )
        assert len(errors) == 1
        # The canonical spec string in the error message is lowercase.
        assert "<ctrl>+<space>" in errors[0]

    def test_modifier_order_invariant(self) -> None:
        errors = _check_cross_field_hotkey_conflicts(
            {
                "hotkey": "<ctrl>+<alt>+<v>",
                "repaste_hotkey": "<alt>+<ctrl>+<v>",
            }
        )
        assert len(errors) == 1

    def test_hotkey_field_names_constant(self) -> None:
        # Sanity: the constant must list all hotkey fields, in a stable
        assert _HOTKEY_FIELD_NAMES == (
            "hotkey",
            "repaste_hotkey",
        )


class TestCrossFieldViaValidateConfigUpdate:
    """Integration: the cross-field check runs in ``validate_config_update``."""

    def test_conflict_between_hotkey_and_repaste_via_ipc(self) -> None:
        validated, errors = validate_config_update({"hotkey": "<f5>", "repaste_hotkey": "<f5>"})
        assert any("Hotkey conflict" in e for e in errors), f"expected a cross-field conflict error, got: {errors}"
        assert "hotkey" in validated
        assert "repaste_hotkey" in validated

    def test_no_conflict_when_only_one_hotkey_in_payload(self) -> None:
        # A partial update with only one hotkey field can't conflict
        validated, errors = validate_config_update({"hotkey": "<f5>"})
        assert errors == []
        assert validated == {"hotkey": "<f5>"}

    def test_invalid_hotkey_does_not_participate_in_cross_field_check(
        self,
    ) -> None:
        validated, errors = validate_config_update({"hotkey": "<alt>+<tab>", "repaste_hotkey": "<alt>+<tab>"})
        # Both should be rejected as reserved, the cross-field check
        per_field_errors = [e for e in errors if "Hotkey conflict" not in e]
        cross_field_errors = [e for e in errors if "Hotkey conflict" in e]
        assert len(per_field_errors) >= 1, f"expected at least one per-field reserved-shortcut error, got: {errors}"
        assert cross_field_errors == [], (
            f"cross-field check should not run on invalid hotkeys, got: {cross_field_errors}"
        )


class TestCrossFieldViaValidateConfig:
    """Integration: the cross-field check runs in ``validate_config`` (load path)."""

    def test_conflict_between_hotkey_and_repaste_caught_at_load(self) -> None:
        cfg = SimpleNamespace(
            hotkey="<f5>",
            repaste_hotkey="<f5>",  # conflicts with hotkey
        )
        errors = validate_config(cfg)
        assert any("'hotkey'" in e and "'repaste_hotkey'" in e for e in errors), (
            f"expected a hotkey/repaste conflict, got: {errors}"
        )

    def test_no_conflict_in_clean_config(self) -> None:
        cfg = SimpleNamespace(
            hotkey="<f5>",
            repaste_hotkey="<f6>",
        )
        errors = validate_config(cfg)
        assert not any("Hotkey conflict" in e for e in errors), (
            f"unexpected cross-field conflict in clean config: {errors}"
        )


class TestCrossPlatformWarnings:
    """:func:`cross_platform_hotkey_warnings`."""

    def test_cmd_q_warns_on_non_darwin_platform(self) -> None:
        # <cmd>+<q> is reserved on darwin (Cmd+Q = quit).  On Linux
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<cmd>+<q>", "hotkey")
        assert warning is not None
        assert "darwin" in warning
        assert "hotkey" in warning
        assert "portable" in warning

    def test_win_l_warns_on_non_win32_platform(self) -> None:
        # <win>+<l> is reserved on win32 (Win+L = lock).  On Linux it
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<win>+<l>", "repaste_hotkey")
        assert warning is not None
        assert "win32" in warning
        assert "repaste_hotkey" in warning

    def test_cmd_b_warns_on_non_darwin_platform(self) -> None:
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<cmd>+<b>", "hotkey")
        assert warning is not None
        assert "hotkey" in warning
        assert "portable" in warning
        assert "Cmd+B" in warning
        assert "macOS" in warning

    def test_win_a_warns_on_non_win32_platform(self) -> None:
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<win>+<a>", "hotkey")
        assert warning is not None
        assert "hotkey" in warning
        assert "portable" in warning
        assert "Windows key combinations" in warning

    def test_cmd_z_warns_on_non_darwin_platform(self) -> None:
        # <cmd>+<z> (Cmd+Z = undo on macOS) is NOT in the darwin
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<cmd>+<z>", "repaste_hotkey")
        assert warning is not None
        assert "repaste_hotkey" in warning
        assert "portable" in warning
        assert "Cmd+Z" in warning
        assert "macOS" in warning

    def test_win_s_warns_on_non_win32_platform(self) -> None:
        # <win>+<s> (Win+S = search on Windows) is NOT in the win32
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<win>+<s>", "repaste_hotkey")
        assert warning is not None
        assert "repaste_hotkey" in warning
        assert "portable" in warning
        assert "Windows key combinations" in warning

    def test_alt_shift_aliases_warn_on_non_win32_platform(self) -> None:
        # <alt_l>+<shift_l> parses to ``["alt", "shift"]`` (the parser
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<alt_l>+<shift_l>", "hotkey")
        assert warning is not None
        assert "hotkey" in warning
        assert "portable" in warning
        assert "Alt+Shift" in warning
        assert "Windows" in warning

    def test_blanket_blocked_combos_via_cross_platform_warnings(self) -> None:
        # Integration: ``cross_platform_hotkey_warnings(cfg)`` must
        cfg = SimpleNamespace(
            hotkey="<cmd>+<b>",  # darwin blanket rule (Cmd+letter)
            repaste_hotkey="<win>+<a>",  # win32 blanket rule (Win+*)
        )
        with patch.object(sys, "platform", "linux"):
            warnings = cross_platform_hotkey_warnings(cfg)
        assert len(warnings) == 2, f"expected 2 blanket-rule warnings, got {len(warnings)}: {warnings}"
        assert any("hotkey" in w and "Cmd+B" in w for w in warnings), warnings
        assert any("repaste_hotkey" in w and "Windows key combinations" in w for w in warnings), warnings

    def test_no_warning_for_unreserved_hotkey(self) -> None:
        # <f5> is not reserved on any platform.
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<f5>", "hotkey")
        assert warning is None

    def test_no_warning_for_empty_value(self) -> None:
        with patch.object(sys, "platform", "linux"):
            assert _cross_platform_hotkey_warning("", "hotkey") is None
            assert _cross_platform_hotkey_warning("   ", "hotkey") is None

    def test_no_warning_for_non_string_value(self) -> None:
        with patch.object(sys, "platform", "linux"):
            assert _cross_platform_hotkey_warning(None, "hotkey") is None  # type: ignore[arg-type]
            assert _cross_platform_hotkey_warning(123, "hotkey") is None  # type: ignore[arg-type]
            assert _cross_platform_hotkey_warning([], "hotkey") is None  # type: ignore[arg-type]

    def test_current_platform_not_consulted(self) -> None:
        with patch.object(sys, "platform", "linux"):
            warning = _cross_platform_hotkey_warning("<alt>+<tab>", "hotkey")
        assert warning is None

    def test_warnings_for_multiple_fields_on_cfg(self) -> None:
        cfg = SimpleNamespace(
            hotkey="<cmd>+<q>",  # darwin reserved
            repaste_hotkey="<win>+<l>",  # win32 reserved
        )
        with patch.object(sys, "platform", "linux"):
            warnings = cross_platform_hotkey_warnings(cfg)
        assert len(warnings) == 2
        assert any("hotkey" in w and "darwin" in w for w in warnings)
        assert any("repaste_hotkey" in w and "win32" in w for w in warnings)

    def test_no_warnings_for_clean_cfg(self) -> None:
        cfg = SimpleNamespace(
            hotkey="<f5>",
            repaste_hotkey="<f6>",
        )
        with patch.object(sys, "platform", "linux"):
            warnings = cross_platform_hotkey_warnings(cfg)
        assert warnings == []

    def test_no_warnings_when_all_hotkeys_none(self) -> None:
        cfg = SimpleNamespace(
            hotkey=None,
            repaste_hotkey=None,
        )
        with patch.object(sys, "platform", "linux"):
            warnings = cross_platform_hotkey_warnings(cfg)
        assert warnings == []

    def test_skips_missing_attributes(self) -> None:
        # A Config object without a hotkey attribute should not
        cfg = SimpleNamespace(
            hotkey="<cmd>+<q>",
        )
        with patch.object(sys, "platform", "linux"):
            warnings = cross_platform_hotkey_warnings(cfg)
        assert len(warnings) == 1
        assert "hotkey" in warnings[0]

    def test_warnings_are_not_errors(self) -> None:
        # must NOT affect ``validate_config`` (which returns ERRORS).
        # current platform must NOT appear in validate_config's errors.
        cfg = SimpleNamespace(
            hotkey="<cmd>+<q>",  # valid on linux, reserved on darwin
            repaste_hotkey="<f6>",
        )
        with patch.object(sys, "platform", "linux"):
            errors = validate_config(cfg)
        assert not any("cmd" in e.lower() or "portable" in e.lower() for e in errors), (
            f"cross-platform warning leaked into validate_config errors: {errors}"
        )


class TestLanguageValidator:
    """Tests for :func:`_validate_language` and the allowlist."""

    @pytest.mark.parametrize(
        "code",
        [
            "en",
            "fr",
            "de",
            "es",
            "ru",
            "ko",
            "ja",
            "pt",
            "it",
            "ar",
            "hi",
            "zh",
            "nl",
            "sv",
            "tr",
            "pl",
            "uk",
            "he",
        ],
    )
    def test_valid_language_codes_accepted(self, code: str) -> None:
        assert _validate_language(code) is None, (
            f"valid language code {code!r} was rejected: {_validate_language(code)}"
        )

    @pytest.mark.parametrize(
        "code",
        [
            "zzzzz",  # 5-letter nonsense
            "english",  # full name, not ISO 639-1
            "french",  # full name
            "auto",  # renderer display fallback, NOT a real code
            "EN",  # uppercase (Whisper codes are lowercase)
            "Fr",  # mixed case
            "e",  # 1 letter
            "enn",  # 3 letters
            "en-US",  # IETF tag (Whisper wants bare 2-letter)
        ],
    )
    def test_invalid_language_codes_rejected(self, code: str) -> None:
        err = _validate_language(code)
        assert err is not None, f"invalid language code {code!r} was accepted"
        assert "Invalid language code" in err
        assert "ISO 639-1" in err

    def test_empty_string_accepted_as_auto_detect(self) -> None:
        # The renderer's ``value={config.language || "auto"}`` fallback
        assert _validate_language("") is None

    def test_nul_byte_still_rejected_as_control_char(self) -> None:
        # Regression test for test_str_validator_via_ipc_rejects_nul_in_language
        err = _validate_language("en\x00fr")
        assert err is not None
        assert "control" in err.lower()

    def test_non_string_rejected(self) -> None:
        assert _validate_language(None) is not None  # type: ignore[arg-type]
        assert _validate_language(123) is not None  # type: ignore[arg-type]
        assert _validate_language([]) is not None  # type: ignore[arg-type]

    def test_allowlist_has_at_least_50_codes(self) -> None:
        # Whisper's LANGUAGES dict has 99 entries; the hardcoded
        assert len(_ALLOWED_LANGUAGES) >= 50, (
            f"allowlist too small: {len(_ALLOWED_LANGUAGES)} codes (source={_ALLOWED_LANGUAGES_SOURCE})"
        )

    def test_allowlist_source_is_documented(self) -> None:
        # The source must be one of the two documented values so we
        assert _ALLOWED_LANGUAGES_SOURCE in (
            "whisper.tokenizer.LANGUAGES",
            "hardcoded fallback (whisper not importable)",
        ), f"unexpected allowlist source: {_ALLOWED_LANGUAGES_SOURCE!r}"

    def test_allowlist_includes_yue(self) -> None:
        # ``yue`` (Cantonese) is the newest addition to Whisper's
        assert "yue" in _ALLOWED_LANGUAGES

    def test_validator_wired_into_allowlist(self) -> None:
        # The IPC allowlist entry for ``language`` must use the new
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        _field_type, validator = IPC_CONFIG_ALLOWLIST["language"]
        # The validator is the _validate_language function itself (not
        assert validator is _validate_language or validator.__name__ == "_validate_language", (
            f"language validator is {validator!r}, expected _validate_language"
        )

    def test_invalid_language_rejected_via_ipc(self) -> None:
        # End-to-end: the new validator is invoked by
        validated, errors = validate_config_update({"language": "zzzzz"})
        assert errors == [
            "field 'language' Invalid language code 'zzzzz', expected a 2-letter ISO 639-1 code like 'en', 'zh', 'ja'"
        ]
        assert "language" not in validated

    def test_valid_language_accepted_via_ipc(self) -> None:
        validated, errors = validate_config_update({"language": "fr"})
        assert errors == []
        assert validated == {"language": "fr"}

    # Regression guard: when the whisper package IS importable but its

    def test_try_load_returns_none_when_languages_dict_empty(self) -> None:
        # Inject a fake ``whisper.tokenizer`` module whose LANGUAGES dict
        import sys as _sys
        import types as _types

        from voice_typer.server.config_validators import (
            language as _lang_mod,
        )

        fake_tokenizer = _types.ModuleType("whisper.tokenizer")
        fake_tokenizer.LANGUAGES = {}  # type: ignore[attr-defined]
        fake_whisper = _types.ModuleType("whisper")
        fake_whisper.tokenizer = fake_tokenizer  # type: ignore[attr-defined]

        with patch.dict(
            _sys.modules,
            {
                "whisper": fake_whisper,
                "whisper.tokenizer": fake_tokenizer,
            },
        ):
            result = _lang_mod._try_load_whisper_languages()
        assert result is None, f"expected None (trigger fallback) when LANGUAGES is empty, got {result!r}"

    def test_try_load_returns_none_when_languages_dict_too_small(self) -> None:
        # Boundary: a dict with exactly 49 entries (one below the 50-code
        import sys as _sys
        import types as _types

        from voice_typer.server.config_validators import (
            language as _lang_mod,
        )

        tiny_langs = {f"l{i:02d}": f"lang{i:02d}" for i in range(49)}
        fake_tokenizer = _types.ModuleType("whisper.tokenizer")
        fake_tokenizer.LANGUAGES = tiny_langs  # type: ignore[attr-defined]
        fake_whisper = _types.ModuleType("whisper")
        fake_whisper.tokenizer = fake_tokenizer  # type: ignore[attr-defined]

        with patch.dict(
            _sys.modules,
            {
                "whisper": fake_whisper,
                "whisper.tokenizer": fake_tokenizer,
            },
        ):
            result = _lang_mod._try_load_whisper_languages()
        assert result is None, f"expected None (trigger fallback) when LANGUAGES has <50 entries, got {result!r}"

    def test_try_load_succeeds_when_languages_dict_large_enough(self) -> None:
        # Boundary complement: a dict with exactly 50 entries (the floor)
        import sys as _sys
        import types as _types

        from voice_typer.server.config_validators import (
            language as _lang_mod,
        )

        langs = {f"l{i:02d}": f"lang{i:02d}" for i in range(50)}
        fake_tokenizer = _types.ModuleType("whisper.tokenizer")
        fake_tokenizer.LANGUAGES = langs  # type: ignore[attr-defined]
        fake_whisper = _types.ModuleType("whisper")
        fake_whisper.tokenizer = fake_tokenizer  # type: ignore[attr-defined]

        with patch.dict(
            _sys.modules,
            {
                "whisper": fake_whisper,
                "whisper.tokenizer": fake_tokenizer,
            },
        ):
            result = _lang_mod._try_load_whisper_languages()
        assert result is not None, "expected a (frozenset, source) tuple when LANGUAGES has >=50 entries, got None"
        allowed, source = result
        assert source == "whisper.tokenizer.LANGUAGES"
        assert len(allowed) == 50

    def test_build_allowed_languages_falls_back_when_dict_empty(self) -> None:
        # End-to-end through `_build_allowed_languages`: with LANGUAGES
        import sys as _sys
        import types as _types

        from voice_typer.server.config_validators import (
            language as _lang_mod,
        )

        fake_tokenizer = _types.ModuleType("whisper.tokenizer")
        fake_tokenizer.LANGUAGES = {}  # type: ignore[attr-defined]
        fake_whisper = _types.ModuleType("whisper")
        fake_whisper.tokenizer = fake_tokenizer  # type: ignore[attr-defined]

        with patch.dict(
            _sys.modules,
            {
                "whisper": fake_whisper,
                "whisper.tokenizer": fake_tokenizer,
            },
        ):
            allowed, source = _lang_mod._build_allowed_languages()
        assert source == "hardcoded fallback (whisper not importable)"
        assert len(allowed) >= 50
        assert "en" in allowed
        assert "yue" in allowed

    def test_validate_en_passes_when_whisper_languages_empty(self) -> None:
        # End-to-end: with whisper.tokenizer.LANGUAGES mocked empty,
        import sys as _sys
        import types as _types

        from voice_typer.server.config_validators import (
            language as _lang_mod,
        )

        fake_tokenizer = _types.ModuleType("whisper.tokenizer")
        fake_tokenizer.LANGUAGES = {}  # type: ignore[attr-defined]
        fake_whisper = _types.ModuleType("whisper")
        fake_whisper.tokenizer = fake_tokenizer  # type: ignore[attr-defined]

        with patch.dict(
            _sys.modules,
            {
                "whisper": fake_whisper,
                "whisper.tokenizer": fake_tokenizer,
            },
        ):
            allowed, source = _lang_mod._build_allowed_languages()

        with (
            patch.object(_lang_mod, "_ALLOWED_LANGUAGES", allowed),
            patch.object(_lang_mod, "_ALLOWED_LANGUAGES_SOURCE", source),
        ):
            assert _lang_mod._validate_language("en") is None, (
                "valid code 'en' was rejected when LANGUAGES was empty, fallback did not fire"
            )
            assert _lang_mod._validate_language("fr") is None
            assert _lang_mod._validate_language("yue") is None
            # Sanity: an invalid code is still rejected by the fallback.
            err = _lang_mod._validate_language("zzzzz")
            assert err is not None
            assert "Invalid language code" in err


class TestBoundsFixes:
    """Tests for the lowered / raised lower bounds on three int fields."""

    def test_max_recording_time_seconds_300_now_accepted(self) -> None:
        validated, errors = validate_config_update({"max_recording_time_seconds": 300})
        assert errors == []
        assert validated == {"max_recording_time_seconds": 300}

    def test_max_recording_time_seconds_299_still_rejected(self) -> None:
        # The current lower bound is 300, so 299 must still be rejected.
        validated, errors = validate_config_update({"max_recording_time_seconds": 299})
        assert len(errors) == 1
        assert "[300, 3600]" in errors[0]
        assert "299" in errors[0]
        assert "max_recording_time_seconds" not in validated

    def test_max_recording_time_seconds_900_still_accepted(self) -> None:
        # Regression: existing tests use 900 (15 min), must still pass.
        validated, errors = validate_config_update({"max_recording_time_seconds": 900})
        assert errors == []
        assert validated == {"max_recording_time_seconds": 900}

    def test_recording_channels_1_now_accepted(self) -> None:
        # Was always accepted (1 > 0); sanity check the new bound.
        validated, errors = validate_config_update({"recording_channels": 1})
        assert errors == []
        assert validated == {"recording_channels": 1}

    def test_recording_channels_0_now_rejected(self) -> None:
        # (0 channels is nonsensical).
        validated, errors = validate_config_update({"recording_channels": 0})
        assert len(errors) == 1
        assert "[1, 8]" in errors[0]
        assert "0" in errors[0]
        assert "recording_channels" not in validated

    def test_recording_channels_8_still_accepted(self) -> None:
        # Upper bound unchanged, 8 channels (7.1 surround) still ok.
        validated, errors = validate_config_update({"recording_channels": 8})
        assert errors == []

    def test_history_max_entries_0_now_accepted(self) -> None:
        # (matches history_retention_count semantics, 0 = disable).
        validated, errors = validate_config_update({"history_max_entries": 0})
        assert errors == []
        assert validated == {"history_max_entries": 0}

    def test_history_max_entries_10_still_accepted(self) -> None:
        validated, errors = validate_config_update({"history_max_entries": 10})
        assert errors == []

    def test_history_max_entries_consistent_with_retention_count(self) -> None:
        # The fix aligns history_max_entries lo with history_retention_count lo
        validated, errors = validate_config_update({"history_max_entries": 0, "history_retention_count": 0})
        assert errors == []
        assert validated == {
            "history_max_entries": 0,
            "history_retention_count": 0,
        }


def _valid_mode_dict() -> dict[str, str]:
    """Return a per-mode dict with all 6 required CSS-variable keys."""
    return {
        "--background": "#ffffff",
        "--foreground": "#000000",
        "--primary": "#0066cc",
        "--bg-subtle": "#f0f0f0",
        "--border": "#cccccc",
        "--text-muted": "#666666",
    }


def _valid_theme() -> dict[str, object]:
    return {"light": _valid_mode_dict(), "dark": _valid_mode_dict()}


class TestCustomThemeCaps:
    """Tests for the key-count caps added to ``_make_custom_theme_validator``."""

    def test_valid_theme_still_accepted(self) -> None:
        # Regression: a well-formed 2-mode, 6-key-per-mode theme must
        validator = _make_custom_theme_validator()
        assert validator(_valid_theme()) is None

    def test_too_many_top_level_keys_rejected(self) -> None:
        # 65 top-level keys > 64 cap.
        validator = _make_custom_theme_validator()
        big = {f"k{i}": {} for i in range(65)}
        err = validator(big)
        assert err is not None
        assert "too many top-level keys" in err

    def test_exactly_64_top_level_keys_passes_top_level_cap(self) -> None:
        # 64 is the boundary, must NOT trip the top-level cap.  (It
        validator = _make_custom_theme_validator()
        big = {f"k{i}": {} for i in range(64)}
        err = validator(big)
        # The top-level cap (> 64) should NOT fire, the error should
        assert err is not None
        assert "too many top-level keys" not in err, (
            f"top-level cap fired at exactly 64 keys (should fire only at >64): {err}"
        )

    def test_too_many_per_mode_keys_rejected(self) -> None:
        # ``light`` has 65 keys > 64 cap.
        validator = _make_custom_theme_validator()
        big_mode = {**_valid_mode_dict(), **{f"--extra-{i}": "#ffffff" for i in range(65)}}
        theme = {"light": big_mode, "dark": _valid_mode_dict()}
        err = validator(theme)
        assert err is not None
        assert "light" in err
        assert "too many keys" in err

    def test_exactly_64_per_mode_keys_passes_cap(self) -> None:
        # 64 keys in ``light`` (6 required + 58 extra), must NOT trip
        validator = _make_custom_theme_validator()
        ok_mode = {**_valid_mode_dict(), **{f"--extra-{i}": "#ffffff" for i in range(58)}}
        assert len(ok_mode) == 64
        theme = {"light": ok_mode, "dark": _valid_mode_dict()}
        err = validator(theme)
        assert err is None, f"64-key mode should pass but got: {err}"

    def test_color_value_over_32_chars_rejected(self) -> None:
        # A 33-char color value (# + 32 chars) > 32 cap.
        validator = _make_custom_theme_validator()
        long_color = "#" + "f" * 32  # 33 chars total
        assert len(long_color) == 33
        theme = {"light": {**_valid_mode_dict(), "--background": long_color}, "dark": _valid_mode_dict()}
        err = validator(theme)
        assert err is not None
        assert "color value too long" in err
        assert "light.--background" in err

    def test_color_value_exactly_32_chars_passes_length_cap(self) -> None:
        # must NOT fire.  The hex-digit count check WILL fire (31 is
        validator = _make_custom_theme_validator()
        boundary_color = "#" + "f" * 31  # 32 chars total
        assert len(boundary_color) == 32
        theme = {"light": {**_valid_mode_dict(), "--background": boundary_color}, "dark": _valid_mode_dict()}
        err = validator(theme)
        assert err is not None
        assert "color value too long" not in err, (
            f"32-char cap fired at exactly 32 chars (should fire only at >32): {err}"
        )

    def test_normal_7_char_color_passes(self) -> None:
        # Regression: a legitimate #RRGGBB (7 chars) must pass.
        validator = _make_custom_theme_validator()
        # _valid_theme() already uses 7-char colors, explicit check:
        theme = {
            "light": {**_valid_mode_dict(), "--background": "#abcdef"},
            "dark": _valid_mode_dict(),
        }
        assert validator(theme) is None

    def test_normal_9_char_color_passes(self) -> None:
        # Regression: a legitimate #RRGGBBAA (9 chars) must pass.
        validator = _make_custom_theme_validator()
        theme = {
            "light": {**_valid_mode_dict(), "--background": "#abcdef99"},
            "dark": _valid_mode_dict(),
        }
        assert validator(theme) is None

    def test_cap_blocks_pathological_10000_key_dict(self) -> None:
        # The attack scenario from the finding: a 10000-key theme dict
        validator = _make_custom_theme_validator()
        pathological = {f"k{i}": _valid_mode_dict() for i in range(10000)}
        err = validator(pathological)
        assert err is not None
        assert "too many top-level keys" in err

    def test_cap_blocks_pathological_10000_key_mode_dict(self) -> None:
        # The per-mode attack: a single mode with 10000 keys.
        validator = _make_custom_theme_validator()
        pathological_mode = {
            **_valid_mode_dict(),
            **{f"--extra-{i}": "#ffffff" for i in range(10000)},
        }
        theme = {"light": pathological_mode, "dark": _valid_mode_dict()}
        err = validator(theme)
        assert err is not None
        assert "light" in err
        assert "too many keys" in err


class TestCrossFieldCloudConfig:
    """PI-18: ``_check_cross_field_cloud_config`` catches cloud/LLM"""

    def test_cloud_url_without_key_raises(self) -> None:
        """Setting ``cloud_api_url`` to a non-empty value while"""
        _, errors = validate_config_update(
            {
                "cloud_api_url": "https://api.openai.com/v1/audio/transcriptions",
                "cloud_api_key": "",
            }
        )
        assert any("cloud_api_key" in e and "required" in e for e in errors), errors

    def test_cloud_key_without_url_raises(self) -> None:
        """Setting ``cloud_api_key`` to a non-empty value while"""
        _, errors = validate_config_update(
            {
                "cloud_api_url": "",
                "cloud_api_key": "sk-test-key",
            }
        )
        assert any("cloud_api_url" in e and "required" in e for e in errors), errors

    def test_llm_polish_without_key_raises(self) -> None:
        """the same update is rejected, the polish path would silently"""
        _, errors = validate_config_update(
            {
                "llm_polish": True,
                "llm_api_key": "",
            }
        )
        assert any("llm_api_key" in e and "required" in e for e in errors), errors

    def test_llm_polish_without_consent_raises(self) -> None:
        """Enabling ``llm_polish`` while ``llm_polish_consent`` is"""
        _, errors = validate_config_update(
            {
                "llm_polish": True,
                "llm_polish_consent": False,
            }
        )
        assert any("llm_polish_consent" in e and "True" in e for e in errors), errors

    def test_cloud_consent_without_key_is_valid_resting_state(self) -> None:
        """Granting a ``cloud_*_consent`` flag while ``cloud_api_key``"""
        _, errors = validate_config_update(
            {
                "cloud_openai_consent": True,
                "cloud_api_key": "",
            }
        )
        assert not any("cloud_api_key" in e and "required" in e for e in errors), errors

    def test_cloud_config_valid_when_all_fields_set(self) -> None:
        """When all cloud/LLM fields are set consistently in the same"""
        _, errors = validate_config_update(
            {
                "cloud_api_url": "https://api.openai.com/v1/audio/transcriptions",
                "cloud_api_key": "sk-test-key",
                "cloud_openai_consent": True,
                "llm_polish": True,
                "llm_api_key": "sk-llm-key",
                "llm_polish_consent": True,
            }
        )
        cloud_field_errors = [
            e
            for e in errors
            if any(
                name in e
                for name in (
                    "cloud_api_key",
                    "cloud_api_url",
                    "llm_api_key",
                    "llm_polish_consent",
                )
            )
        ]
        assert cloud_field_errors == [], cloud_field_errors

    def test_partial_update_does_not_trigger_false_positive(self) -> None:
        """PI-18 regression guard: when the renderer pushes only ONE"""
        _, errors = validate_config_update(
            {
                "cloud_api_url": "https://api.openai.com/v1/audio/transcriptions",
            }
        )
        assert not any("cloud_api_key" in e and "required" in e for e in errors), errors

    def test_full_config_load_catches_inconsistency(self) -> None:
        """hand-edited ``config.json`` that sets ``llm_polish=True`` but"""
        cfg = SimpleNamespace(
            llm_polish=True,
            llm_api_key="",
            llm_polish_consent=True,
            cloud_api_key="",
            cloud_api_url="",
            cloud_openai_consent=False,
            cloud_groq_consent=False,
            cloud_deepgram_consent=False,
        )
        errors = validate_config(cfg)
        assert any("llm_api_key" in e and "required" in e for e in errors), errors
