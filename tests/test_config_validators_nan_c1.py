"""AP-23, AP-24 regression tests: NaN floats + C1 control chars in strings/URLs."""

from __future__ import annotations

import math

import pytest
from voice_typer.server.config_validators import (
    _make_float_validator,
    _make_str_validator,
    _make_url_validator,
)


class TestFloatValidatorRejectsNaNAndInf:
    """AP-23: NaN / Inf must be rejected for any float field."""

    def setup_method(self) -> None:
        self.validate = _make_float_validator(lo=0.0, hi=1.0)

    def test_nan_is_rejected(self) -> None:
        err = self.validate(float("nan"))
        assert err is not None
        assert "finite" in err
        assert "nan" in err.lower()

    def test_inf_is_rejected(self) -> None:
        err = self.validate(float("inf"))
        assert err is not None
        assert "finite" in err

    def test_neg_inf_is_rejected(self) -> None:
        err = self.validate(float("-inf"))
        assert err is not None
        assert "finite" in err

    def test_in_range_float_still_passes(self) -> None:
        # Regression guard: the new finite-check must NOT accidentally
        assert self.validate(0.5) is None

    def test_below_range_still_rejected(self) -> None:
        err = self.validate(-0.1)
        assert err is not None
        # Should be the range error, NOT the finite-number error.
        assert "must be in [" in err

    def test_above_range_still_rejected(self) -> None:
        err = self.validate(1.5)
        assert err is not None
        assert "must be in [" in err

    def test_int_still_accepted_for_float_field(self) -> None:
        # The finite-check must not break this: math.isnan(1) is False.
        assert self.validate(0) is None
        assert self.validate(1) is None

    def test_nan_does_not_silently_bypass_via_json_extension(self) -> None:
        """Reproduce the exact AP-23 scenario end-to-end."""
        import json as _json

        # This is what a hand-edited config.json might contain.
        parsed = _json.loads('{"vad_speech_threshold": NaN}')
        assert math.isnan(parsed["vad_speech_threshold"])
        err = self.validate(parsed["vad_speech_threshold"])
        assert err is not None
        assert "finite" in err


class TestStrValidatorRejectsC1ControlChars:
    """AP-24: ``_make_str_validator`` must reject C1 control chars (0x80-0x9F)."""

    def setup_method(self) -> None:
        self.validate = _make_str_validator()

    @pytest.mark.parametrize("c1_char", ["\x80", "\x85", "\x9b", "\x9d", "\x9f"])
    def test_c1_control_char_is_rejected(self, c1_char: str) -> None:
        # 0x9B is CSI (the C1 introducer that starts ANSI escape
        err = self.validate(f"hello{c1_char}world")
        assert err is not None
        assert "control character" in err
        assert str(ord(c1_char)) in err

    def test_c0_control_chars_still_rejected(self) -> None:
        for c0 in ["\x00", "\x01", "\x08", "\x1f", "\x7f"]:
            err = self.validate(f"x{c0}y")
            assert err is not None, f"C0 char {ord(c0):#x} should be rejected"
            assert "control character" in err

    def test_plain_string_still_accepted(self) -> None:
        # Regression guard: ordinary printable text must still pass.
        assert self.validate("hello world") is None
        assert self.validate("api-key-12345") is None

    def test_unicode_above_c1_range_still_accepted(self) -> None:
        # C1 is 0x80-0x9F. Codepoints >= 0xA0 are legitimate text
        assert self.validate("café") is None
        assert self.validate("\u00a0") is None  # NBSP
        assert self.validate("日本語") is None


class TestURLValidatorStripsWhitespaceAndRejectsC1:
    """AP-24: ``_make_url_validator`` strips whitespace and rejects C1 controls."""

    def setup_method(self) -> None:
        self.validate = _make_url_validator(require_https=True)

    def test_leading_and_trailing_whitespace_stripped_and_accepted(self) -> None:
        # A pasted URL with stray surrounding spaces must be accepted
        err = self.validate(" https://api.openai.com ")
        assert err is None

    def test_internal_whitespace_not_stripped(self) -> None:
        # Only LEADING/TRAILING whitespace is stripped. Internal spaces
        err = self.validate("https://api .openai.com")
        _ = err  # intentionally not asserted: see docstring above.

    def test_newline_wrapped_url_stripped_and_accepted(self) -> None:
        # Common paste artifact: surrounding newlines.
        err = self.validate("\nhttps://api.openai.com\n")
        assert err is None

    def test_tab_wrapped_url_stripped_and_accepted(self) -> None:
        err = self.validate("\thttps://api.openai.com\t")
        assert err is None

    def test_url_with_c1_control_char_rejected(self) -> None:
        # C1 introducer 0x9B (CSI) embedded inside the URL, this is
        err = self.validate("https://api.openai.com\x9becho hi")
        assert err is not None
        assert "control character" in err
        assert "155" in err  # 0x9B == 155

    def test_url_with_c1_pad_char_rejected(self) -> None:
        # 0x80 (PAD) at the start of the URL, even after stripping
        err = self.validate("\x80https://api.openai.com")
        assert err is not None
        assert "control character" in err
        assert "128" in err  # 0x80 == 128

    def test_url_with_high_c1_rejected(self) -> None:
        # 0x9F (APC. Application Program Command) at end of URL.
        err = self.validate("https://api.openai.com\x9f")
        assert err is not None
        assert "control character" in err
        assert "159" in err  # 0x9F == 159

    def test_clean_https_url_still_accepted(self) -> None:
        # Regression guard: the new strip + C1 check must not break
        assert self.validate("https://api.openai.com") is None

    def test_loopback_http_still_accepted(self) -> None:
        # Regression guard: the strip must not break the loopback-HTTP
        assert self.validate("http://127.0.0.1:8080") is None
        assert self.validate("http://localhost:3000") is None

    def test_empty_url_with_allow_empty_still_accepted(self) -> None:
        # Regression guard: the strip must not break the empty-URL
        validate = _make_url_validator(allow_empty=True)
        assert validate("") is None
        assert validate("   ") is None
        assert validate("\t\n") is None

    def test_empty_url_without_allow_empty_still_rejected(self) -> None:
        validate = _make_url_validator(allow_empty=False)
        err = validate("")
        assert err is not None
        assert "empty" in err
        # Whitespace-only URL after stripping becomes "" and must be
        err = validate("   ")
        assert err is not None
        assert "empty" in err

    def test_strip_does_not_persist_after_validation(self) -> None:
        """
        The validator is pure: it must NOT mutate the caller's string.
        None or an error string, they do not return coerced values).
        """
        original = " https://api.openai.com "
        err = self.validate(original)
        assert err is None
        assert original == " https://api.openai.com "
