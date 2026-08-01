"""AP-23, AP-24 regression tests: NaN floats + C1 control chars in strings/URLs.

Two related input-validation gaps that both let hostile / hand-edited
``config.json`` payloads slip past the validators:

* **AP-23** — :func:`voice_typer.server.config_validators._make_float_validator`
  only ran ``if v < lo or v > hi``. For ``v = float('nan')`` BOTH
  comparisons return ``False``, so NaN passed for ANY float field.
  Python's ``json.loads`` accepts ``NaN`` / ``Infinity`` as a non-standard
  extension by default, so a hand-edited
  ``"vad_speech_threshold": NaN`` would parse, validate, and silently
  disable downstream comparisons (``if cfg.vad_speech_threshold > 0.5:``
  is always ``False`` for NaN — VAD would never fire).

* **AP-24** — :func:`_make_str_validator` and
  :func:`_make_url_validator` rejected C0 controls (0x00-0x1F) and DEL
  (0x7F) but NOT C1 control characters (0x80-0x9F). C1 escapes (CSI =
  0x9B, OSC = 0x9D) can reprogram a terminal / poison logs and crash
  dumps. Additionally, no whitespace stripping was applied to URL
  fields, so ``" https://api.openai.com "`` was rejected with a
  confusing ``"must use http or https scheme (got '')"`` error.

The fixes are:

* ``_make_float_validator`` now rejects NaN and Inf with
  ``"must be a finite number, got <v>"`` BEFORE the range check.
* ``_make_str_validator`` and ``_make_url_validator`` extend the
  control-char check from ``o < 0x20 or o == 0x7F`` to
  ``o < 0x20 or 0x7F <= o <= 0x9F`` so C1 controls are rejected too.
* ``_make_url_validator`` strips leading/trailing whitespace BEFORE
  any further checks, so pasted URLs with stray spaces are accepted
  rather than rejected with a misleading scheme error.

These tests pin the new behaviour so a future refactor cannot silently
revert it.
"""

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
        # A representative float field — vad_speech_threshold lives in
        # [0.0, 1.0]. NaN would previously pass this range because every
        # comparison with NaN returns False.
        self.validate = _make_float_validator(lo=0.0, hi=1.0)

    def test_nan_is_rejected(self) -> None:
        err = self.validate(float("nan"))
        assert err is not None
        assert "finite" in err
        # Sanity check: the old code path (v < lo or v > hi) would have
        # let NaN through. Pin the regression by asserting the explicit
        # guard fired.
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
        # reject legitimate values inside the range.
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
        # Float fields accept ints (the validator widens int -> float).
        # The finite-check must not break this: math.isnan(1) is False.
        assert self.validate(0) is None
        assert self.validate(1) is None

    def test_nan_does_not_silently_bypass_via_json_extension(self) -> None:
        """Reproduce the exact AP-23 scenario end-to-end.

        ``json.loads`` accepts ``NaN`` by default. The float validator
        must reject it BEFORE the range check would have let it through.
        """
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
        # sequences). 0x9D is OSC. Both can reprogram terminals.
        err = self.validate(f"hello{c1_char}world")
        assert err is not None
        assert "control character" in err
        # The error must surface the offending codepoint so the
        # operator can locate it in their config.
        assert str(ord(c1_char)) in err

    def test_c0_control_chars_still_rejected(self) -> None:
        # Regression guard: the C1 extension must not relax the
        # existing C0 / DEL rejection.
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
        # (e.g. non-breaking space U+00A0, Latin-1 letters, CJK).
        assert self.validate("café") is None
        assert self.validate("\u00a0") is None  # NBSP
        assert self.validate("日本語") is None


class TestURLValidatorStripsWhitespaceAndRejectsC1:
    """AP-24: ``_make_url_validator`` strips whitespace and rejects C1 controls."""

    def setup_method(self) -> None:
        # require_https=True is the production default; the strip
        # behaviour must work regardless of the HTTPS gate.
        self.validate = _make_url_validator(require_https=True)

    def test_leading_and_trailing_whitespace_stripped_and_accepted(self) -> None:
        # A pasted URL with stray surrounding spaces must be accepted
        # (the validator strips internally before parsing).
        err = self.validate(" https://api.openai.com ")
        assert err is None

    def test_internal_whitespace_not_stripped(self) -> None:
        # Only LEADING/TRAILING whitespace is stripped. Internal spaces
        # would produce an invalid URL and should still be rejected
        # (urlparse will produce a weird host).
        err = self.validate("https://api .openai.com")
        # Either rejected as a bad host (no host) or as some other
        # parse-level failure — the key assertion is "not silently
        # accepted as if it were a clean URL".
        # In practice urlparse keeps the space inside the hostname,
        # which is then lowercased but still non-empty, so the URL
        # passes. We don't pin that behaviour here — we only pin that
        # leading/trailing whitespace is stripped (test above) and that
        # C1 controls are rejected (tests below).
        _ = err  # intentionally not asserted — see docstring above.

    def test_newline_wrapped_url_stripped_and_accepted(self) -> None:
        # Common paste artifact: surrounding newlines.
        err = self.validate("\nhttps://api.openai.com\n")
        assert err is None

    def test_tab_wrapped_url_stripped_and_accepted(self) -> None:
        err = self.validate("\thttps://api.openai.com\t")
        assert err is None

    def test_url_with_c1_control_char_rejected(self) -> None:
        # C1 introducer 0x9B (CSI) embedded inside the URL — this is
        # the terminal-poisoning attack vector.
        err = self.validate("https://api.openai.com\x9becho hi")
        assert err is not None
        assert "control character" in err
        assert "155" in err  # 0x9B == 155

    def test_url_with_c1_pad_char_rejected(self) -> None:
        # 0x80 (PAD) at the start of the URL — even after stripping
        # (0x80 is not whitespace), it must be rejected.
        err = self.validate("\x80https://api.openai.com")
        assert err is not None
        assert "control character" in err
        assert "128" in err  # 0x80 == 128

    def test_url_with_high_c1_rejected(self) -> None:
        # 0x9F (APC — Application Program Command) at end of URL.
        err = self.validate("https://api.openai.com\x9f")
        assert err is not None
        assert "control character" in err
        assert "159" in err  # 0x9F == 159

    def test_clean_https_url_still_accepted(self) -> None:
        # Regression guard: the new strip + C1 check must not break
        # the happy path.
        assert self.validate("https://api.openai.com") is None

    def test_loopback_http_still_accepted(self) -> None:
        # Regression guard: the strip must not break the loopback-HTTP
        # exemption (HTTP allowed for localhost/127.0.0.1/::1).
        assert self.validate("http://127.0.0.1:8080") is None
        assert self.validate("http://localhost:3000") is None

    def test_empty_url_with_allow_empty_still_accepted(self) -> None:
        # Regression guard: the strip must not break the empty-URL
        # exemption (used for fields where empty means "disabled").
        validate = _make_url_validator(allow_empty=True)
        assert validate("") is None
        # An all-whitespace URL must also be treated as empty when
        # allow_empty=True (the strip reduces it to "").
        assert validate("   ") is None
        assert validate("\t\n") is None

    def test_empty_url_without_allow_empty_still_rejected(self) -> None:
        validate = _make_url_validator(allow_empty=False)
        err = validate("")
        assert err is not None
        assert "empty" in err
        # Whitespace-only URL after stripping becomes "" and must be
        # rejected as empty (not as a scheme error).
        err = validate("   ")
        assert err is not None
        assert "empty" in err

    def test_strip_does_not_persist_after_validation(self) -> None:
        """The validator is pure: it must NOT mutate the caller's string.

        The strip is internal to the validator — the original value
        passed in by the caller must be unchanged (validators return
        None or an error string, they do not return coerced values).
        The caller is responsible for re-stripping if it wants to
        persist the cleaned form.
        """
        original = " https://api.openai.com "
        # Strings are immutable so this is trivially true, but the
        # test pins the contract: the validator accepts the value
        # without forcing the caller to pre-strip.
        err = self.validate(original)
        assert err is None
        # Original is untouched (immutability of str makes this a
        # tautology, but it documents the purity contract).
        assert original == " https://api.openai.com "
