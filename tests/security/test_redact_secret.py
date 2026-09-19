"""Domain: SEC-9 (flag / key=value forms) + G4-L-06 (generic threshold"""

from __future__ import annotations

from voice_typer.server import _secrets
from voice_typer.server._secrets import (
    redact_for_export,
    redact_secret,
    redact_url,
)
from voice_typer.server.security import redact_pii


class TestRedactSecretFlagForms:
    """SEC-9: ``redact_secret`` must redact ``--token=abc``,"""

    def test_flag_equals_form(self):
        """``--token=abc123`` → ``--token=***``."""
        s = "starting sidecar with --token=abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        assert "abc123" not in redacted
        assert "--token=***" in redacted

    def test_flag_space_form(self):
        """``--token abc123`` → ``--token ***``."""
        s = "starting sidecar with --token abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        assert "abc123" not in redacted
        assert "--token ***" in redacted

    def test_bare_key_value_form(self):
        """``token=abc123`` → ``token=***`` (no flag prefix)."""
        s = "loaded config: token=abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        assert "abc123" not in redacted
        assert "token=***" in redacted

    def test_api_key_underscore_form(self):
        """``--api_key=secret`` → ``--api_key=***``."""
        s = "env: --api_key=sk-live-1234567890abcdef"
        redacted = redact_secret(s)
        assert "sk-live-1234567890abcdef" not in redacted
        assert "--api_key=***" in redacted

    def test_api_key_hyphen_form(self):
        """``--api-key=secret`` → ``--api-key=***``."""
        s = "env: --api-key=sk-live-1234567890abcdef"
        redacted = redact_secret(s)
        assert "sk-live-1234567890abcdef" not in redacted
        assert "--api-key=***" in redacted

    def test_password_form(self):
        """``password=hunter2`` → ``password=***``."""
        s = "db config: password=hunter2-supersecret"
        redacted = redact_secret(s)
        assert "hunter2-supersecret" not in redacted
        assert "password=***" in redacted

    def test_secret_form(self):
        """``--secret=xyz`` → ``--secret=***``."""
        s = "oauth --secret=oauth-client-secret-12345"
        redacted = redact_secret(s)
        assert "oauth-client-secret-12345" not in redacted
        assert "--secret=***" in redacted

    def test_access_token_form(self):
        """``--access_token=xyz`` → ``--access_token=***``."""
        s = "auth: --access_token=ya29-abcdef1234567890"
        redacted = redact_secret(s)
        assert "ya29-abcdef1234567890" not in redacted
        assert "--access_token=***" in redacted

    def test_case_insensitive_keyword(self):
        """Keywords are case-insensitive: ``--TOKEN=abc`` redacts too."""
        s = "starting sidecar with --TOKEN=abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        # The prefix is preserved verbatim (case preserved).
        assert "--TOKEN=***" in redacted

    def test_short_input_with_flag_still_redacted(self):
        """
        flag patterns must fire even on short inputs.
        ``_MIN_REDACT_LEN`` guard. Pre-SEC-9 the function returned
        """
        s = "--token=abc"
        assert len(s) < 20  # sanity: under the generic threshold
        redacted = redact_secret(s)
        assert "abc" not in redacted
        assert "--token=***" in redacted

    def test_does_not_mangle_unrelated_key(self):
        """``\\bkey=`` must NOT match inside larger words like"""
        s = "hotkey=<f2>"
        # The string is short (< 20 chars) AND has no flag-prefixed
        assert redact_secret(s) == s

        # Same check on a longer string with the same hotkey= token.
        s_long = "the current hotkey=<f2> is set in the config file"
        redacted = redact_secret(s_long)
        assert "hotkey=<f2>" in redacted, (
            f"hotkey=<f2> must NOT be redacted (\\b boundary should "
            f"prevent `key=` matching inside `hotkey=`); got {redacted!r}"
        )

    def test_does_not_mangle_url_with_api_subdomain(self):
        """``api.example.com`` must NOT be redacted as ``api_key=``."""
        s = "https://user:pass@api.example.com/v1"
        redacted = redact_secret(s)
        # The hostname must be preserved.
        assert "api.example.com" in redacted

    def test_existing_bearer_pattern_still_works(self):
        """Regression: the existing ``Bearer <value>`` pattern must"""
        s = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact_secret(s)
        assert "Bearer" in redacted
        assert "sk-abcdef" not in redacted
        assert "Bearer ***" in redacted

    def test_existing_token_pattern_still_works(self):
        """Regression: the existing ``Token <value>`` pattern must"""
        s = "Token abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        redacted = redact_secret(s)
        assert "Token" in redacted
        assert "abcdefghijkl" not in redacted
        assert "Token ***" in redacted

    def test_existing_sk_pattern_still_works(self):
        """Regression: the existing ``sk-...`` pattern must still fire."""
        s = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        redacted = redact_secret(s)
        assert "sk-abc" not in redacted
        assert "***" in redacted

    def test_existing_generic_32char_pattern_still_works(self):
        """Regression: the generic 32+ char alphanumeric pattern must"""
        s = "key=0123456789abcdef0123456789abcdef0123456789abcdef"
        redacted = redact_secret(s)
        # The 32+ char value must be redacted. With SEC-9 the
        # original 32+ char run must NOT appear.
        assert "0123456789abcdef0123" not in redacted

    def test_preserves_ordinary_long_text(self):
        """Regression: ordinary long text without secret patterns"""
        s = "This is a perfectly normal error message about a network timeout."
        assert redact_secret(s) == s

    def test_short_string_unchanged(self):
        """Regression: short strings without secret patterns must"""
        assert redact_secret("short") == "short"
        assert redact_secret("1234567890123456789") == "1234567890123456789"

    def test_none_input(self):
        """Regression: ``None`` → ``\"None\"``."""
        assert redact_secret(None) == "None"

    def test_multiple_secrets_in_one_string(self):
        """Multiple secret-bearing tokens in the same string are all"""
        s = "config: --token=abc123 --api_key=def456 password=hunter2"
        redacted = redact_secret(s)
        assert "abc123" not in redacted
        assert "def456" not in redacted
        assert "hunter2" not in redacted
        assert "--token=***" in redacted
        assert "--api_key=***" in redacted
        assert "password=***" in redacted


class TestRedactSecretThreshold20:
    """G4-L-06: the generic ``[A-Za-z0-9_\\-]{N,}`` pattern threshold"""

    def test_20_char_bare_token_redacted(self):
        """G4-L-06: a bare 20-char alphanumeric token is redacted."""
        # Exactly 20 chars, no keyword prefix, no sk-/Bearer/Token.
        token = "0123456789abcdefghij"  # 20 chars
        assert len(token) == 20
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_24_char_bare_token_redacted(self):
        """G4-L-06: a 24-char bare token (e.g. GitLab PAT) is redacted."""
        token = "0123456789abcdefghij1234"  # 24 chars
        assert len(token) == 24
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_32_char_bare_token_redacted(self):
        """threshold) is now redacted."""
        token = "0123456789abcdefghij123456789abc"  # 32 chars
        assert len(token) == 32
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_19_char_bare_token_preserved(self):
        """G4-L-06: a 19-char bare token is still preserved (below"""
        token = "0123456789abcdefghi"  # 19 chars
        assert len(token) == 19
        # Note: redact_secret returns short strings unchanged when no
        assert redact_secret(token) == token

    def test_32_char_bare_token_still_redacted(self):
        """G4-L-06: regression, the existing 32+ char behavior still"""
        token = "0123456789abcdef0123456789abcdef"  # 32 chars
        assert len(token) == 32
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_generic_pattern_threshold_constant_is_20(self):
        """G4-L-06: the regex threshold in ``_KEY_PATTERNS[-1]`` is"""
        import re

        # The last pattern in _KEY_PATTERNS is the generic catch-all.
        generic_pattern = _secrets._KEY_PATTERNS[-1]
        assert isinstance(generic_pattern, re.Pattern)
        pattern_str = generic_pattern.pattern
        # The pattern string is ``\b[A-Za-z0-9_\-]{20,}\b``.
        assert "{20,}" in pattern_str, (
            f"expected generic pattern threshold to be {{20,}} after G4-L-06; got pattern {pattern_str!r}"
        )
        assert "{32,}" not in pattern_str, (
            f"the pre-fix {{32,}} threshold must NOT appear in the generic "
            f"pattern after G4-L-06; got pattern {pattern_str!r}"
        )


def test_sec9_flag_patterns_module_constants():
    """SEC-9: ``_secrets`` module must expose the new flag-pattern"""
    assert hasattr(_secrets, "_FLAG_KEY_PATTERNS")
    assert hasattr(_secrets, "_FLAG_VALUE_PATTERN")
    assert hasattr(_secrets, "_BARE_KEY_VALUE_PATTERN")
    assert hasattr(_secrets, "_SECRET_KEYWORDS")
    assert "token" in _secrets._SECRET_KEYWORDS
    assert "key" in _secrets._SECRET_KEYWORDS
    assert "password" in _secrets._SECRET_KEYWORDS
    assert "api_key" in _secrets._SECRET_KEYWORDS


class TestRedactUrl:
    """HU-37: ``redact_url`` strips ``user:pass@`` userinfo AND chains"""

    def test_strips_userinfo_from_url(self):
        """``user:pass@`` userinfo is removed; scheme/host/path are"""
        assert redact_url("https://user:pass@api.example.com/path") == "https://api.example.com/path"

    def test_strips_password_only_userinfo(self):
        """A userinfo with only a password (no username) is stripped too."""
        assert redact_url("https://:hunter2@api.example.com/v1") == "https://api.example.com/v1"

    def test_masks_query_string_api_key(self):
        """aggressive ``redact_secret`` chain (pre-fix this survived"""
        out = redact_url("https://api.example.com/?key=sk-abc123xyz456")
        assert "sk-abc123xyz456" not in out
        assert "key=" in out  # the flag name survives for debugging
        assert "***" in out

    def test_masks_query_string_access_token(self):
        """``?access_token=...`` is masked the same way."""
        out = redact_url("https://api.example.com/?access_token=abcdef1234567890")
        assert "abcdef1234567890" not in out
        assert "access_token=" in out
        assert "***" in out

    def test_short_query_string_secret_masked_via_aggressive(self):
        """bypasses the short-string guard (``redact_secret`` alone would"""
        out = redact_url("https://api.example.com/?key=abc")
        assert "***" in out
        # The 3-char secret value must not survive as ``?key=abc``.
        assert "key=abc" not in out

    def test_plain_url_unchanged(self):
        """A URL with no credentials is returned unchanged (no"""
        assert redact_url("https://api.openai.com/v1/audio/transcriptions") == (
            "https://api.openai.com/v1/audio/transcriptions"
        )

    def test_empty_url_returned_as_is(self):
        """Empty / non-URL input is returned unchanged."""
        assert redact_url("") == ""


class TestRedactPiiAndForExport:
    """log/crash/export paths) and ``redact_for_export`` (the unified PII"""

    def test_redact_pii_masks_email_and_phone(self):
        out = redact_pii("contact user@example.com or 555-123-4567")
        assert "[EMAIL]" in out
        assert "[PHONE]" in out
        assert "user@example.com" not in out
        assert "555-123-4567" not in out

    def test_redact_pii_masks_ssn_and_cc(self):
        out = redact_pii("SSN 123-45-6789 and card 4111111111111111")
        assert "[SSN]" in out
        assert "[CC]" in out
        assert "123-45-6789" not in out
        assert "4111111111111111" not in out

    def test_redact_pii_masks_api_key_bearer(self):
        """``redact_pii`` also applies ``redact_secret`` + ``redact_url``"""
        out = redact_pii("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456")
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out

    def test_redact_for_export_masks_pii_and_secrets(self):
        """The unified diagnostic-export pipeline masks both PII and"""
        out = redact_for_export("api key sk-abcdefghijklmnop qrstuvwx from user@example.com")
        assert "user@example.com" not in out
        assert "[EMAIL]" in out
        assert "sk-abcdefghijklmnop" not in out
        assert "***" in out
