"""Tests for voice_typer.server._secrets — RELIABILITY-004.

Verifies:
- API key redaction from arbitrary strings (log messages, URLs, exceptions)
- URL userinfo redaction
- Cloud URL allowlist (default hosts, runtime extension, assertion behavior)
"""

import pytest

from voice_typer.server import _secrets
from voice_typer.server._secrets import (
    assert_url_allowed,
    extend_url_allowlist,
    get_url_allowlist,
    is_url_allowed,
    redact_secret,
    redact_url,
)


class TestRedactSecret:
    def test_short_string_unchanged(self):
        """Short strings (under 20 chars) are returned unchanged."""
        assert redact_secret("short") == "short"
        assert redact_secret("1234567890123456789") == "1234567890123456789"

    def test_openai_key_redacted(self):
        """OpenAI-style keys (sk-...) are redacted."""
        s = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        redacted = redact_secret(s)
        assert "sk-abc" not in redacted
        assert "***" in redacted

    def test_bearer_token_redacted(self):
        """Bearer tokens in headers are redacted, prefix preserved."""
        s = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact_secret(s)
        assert "Bearer" in redacted
        assert "sk-abcdef" not in redacted

    def test_token_keyword_redacted(self):
        """Deepgram-style Token auth is redacted."""
        s = "Token abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        redacted = redact_secret(s)
        assert "Token" in redacted
        assert "abcdefghijkl" not in redacted

    def test_generic_long_hex_redacted(self):
        """Any 32+ char alphanumeric run is treated as a potential key."""
        s = "key=0123456789abcdef0123456789abcdef0123456789abcdef"
        redacted = redact_secret(s)
        assert "0123456789abcdef0123" not in redacted

    def test_url_with_userinfo_redacted(self):
        """URLs with embedded credentials have userinfo stripped."""
        # Use a 32+ char password so the generic-redaction pattern fires.
        s = "https://user:password1234567890ABCDEFpassword1234567890ABCDEF@api.example.com/v1"
        redacted = redact_secret(s)
        # Password portion should not appear in output
        assert "password1234567890ABCDEFpassword1234567890ABCDEF" not in redacted

    def test_non_string_input(self):
        """Non-string inputs are stringified then redacted."""
        # An integer that's longer than 20 chars when stringified
        n = 10**40  # 41 digits
        redacted = redact_secret(n)
        assert isinstance(redacted, str)
        assert "***" in redacted or len(redacted) < 20

    def test_none_input(self):
        assert redact_secret(None) == "None"

    def test_preserves_ordinary_long_text(self):
        """Long non-key-like text should pass through (heuristic)."""
        s = "This is a perfectly normal error message about a network timeout."
        # Note: this string is > 20 chars but contains no 32+ char
        # alphanumeric runs and no sk-/Bearer/Token patterns, so it
        # should pass through unchanged.
        assert redact_secret(s) == s


class TestRedactUrl:
    def test_strips_userinfo(self):
        url = "https://user:pass@api.example.com/v1/audio"
        assert redact_url(url) == "https://api.example.com/v1/audio"

    def test_preserves_url_without_userinfo(self):
        url = "https://api.example.com/v1/audio"
        assert redact_url(url) == url

    def test_preserves_port(self):
        url = "https://user:pass@localhost:8080/v1"
        assert redact_url(url) == "https://localhost:8080/v1"

    def test_empty_url(self):
        assert redact_url("") == ""

    def test_invalid_url_returns_input(self):
        # Malformed URL — should be returned as-is rather than crash
        bad = "not a url at all"
        assert redact_url(bad) == bad


class TestUrlAllowlist:
    def test_default_hosts_allowed(self):
        assert is_url_allowed("https://api.openai.com/v1/audio/transcriptions")
        assert is_url_allowed("https://api.groq.com/openai/v1/audio/transcriptions")
        assert is_url_allowed("https://api.deepgram.com/v1/listen")
        assert is_url_allowed("https://api.anthropic.com/v1/messages")

    def test_localhost_allowed(self):
        """Local self-hosted endpoints (Ollama, vLLM) are allowed."""
        assert is_url_allowed("http://localhost:11434/v1/chat/completions")
        assert is_url_allowed("http://127.0.0.1:8000/v1")

    def test_unknown_host_rejected(self):
        assert not is_url_allowed("https://evil.example.com/exfiltrate")
        assert not is_url_allowed("https://192.168.1.50/steal")

    def test_empty_url_allowed(self):
        """Empty URLs fail later with a clearer HTTP error."""
        assert is_url_allowed("") is True

    def test_no_hostname_rejected(self):
        assert not is_url_allowed("javascript:alert(1)")
        assert not is_url_allowed("file:///etc/passwd")

    def test_runtime_extension(self):
        """extend_url_allowlist adds hosts at runtime."""
        try:
            extend_url_allowlist(["my-self-hosted.example.com"])
            assert is_url_allowed("https://my-self-hosted.example.com/v1/chat")
        finally:
            # Clean up — _user_extensions is module-global
            _secrets._user_extensions.discard("my-self-hosted.example.com")

    def test_extension_normalizes_host(self):
        """Hostnames are lowercased and stripped of port."""
        try:
            extend_url_allowlist(["My-Host.Example.COM:8080"])
            assert "my-host.example.com" in get_url_allowlist()
        finally:
            _secrets._user_extensions.discard("my-host.example.com")


class TestAssertUrlAllowed:
    def test_allowed_url_passes(self):
        # Should not raise
        assert_url_allowed("https://api.openai.com/v1/chat/completions")

    def test_disallowed_url_raises(self):
        with pytest.raises(ValueError, match="not in the trusted allowlist"):
            assert_url_allowed("https://evil.example.com/steal")

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="is empty"):
            assert_url_allowed("")

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValueError, match="http or https"):
            assert_url_allowed("javascript:alert(1)")

    def test_no_hostname_raises(self):
        with pytest.raises(ValueError, match="no hostname"):
            assert_url_allowed("https:///path-only")

    def test_error_message_excludes_url(self):
        """Error message should NOT include the URL itself (avoids
        leaking a potentially-malicious URL into logs)."""
        url = "https://evil.example.com/steal?token=secret"
        with pytest.raises(ValueError) as exc_info:
            assert_url_allowed(url)
        assert url not in str(exc_info.value)

    def test_custom_field_and_client_names(self):
        with pytest.raises(ValueError, match="cloud/openai"):
            assert_url_allowed(
                "https://evil.example.com",
                field_name="cloud_api_url",
                client_name="cloud/openai",
            )
