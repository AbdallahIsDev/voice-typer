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
    redact_api_keys,
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


class TestRedactApiKeys:
    """Tests for the ``redact_api_keys`` helper (XV-121 DRY consolidation).

    ``redact_api_keys`` is the canonical API-key redaction helper shared
    by ``redact_secret`` (log-message redaction, default ``"***"``) and
    ``credential_store._redact_sensitive`` (IPC-bound keyring-exception
    redaction, ``"[redacted]"``). These tests pin its contract so a
    future change to ``_KEY_PATTERNS`` can't silently break either
    consumer.
    """

    def test_default_replacement_is_triple_star(self):
        """Without an explicit replacement, ``redact_api_keys`` uses ``"***"``.

        This matches the historical behavior of ``redact_secret`` and
        is what every log-message redaction call site expects.
        """
        s = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        assert redact_api_keys(s) == "***"

    def test_custom_replacement_redacts_sk_prefix(self):
        """The ``replacement`` kwarg controls the substituted marker.

        ``credential_store._redact_sensitive`` uses ``"[redacted]"`` to
        match the convention used for filesystem paths (``"[path]"``)
        in IPC-bound messages.
        """
        s = "backend rejected: sk-abcdefghij1234567890XYZ"
        out = redact_api_keys(s, replacement="[redacted]")
        assert "sk-abcdefghij1234567890XYZ" not in out
        assert "[redacted]" in out

    def test_bearer_prefix_preserved_with_custom_replacement(self):
        """``Bearer <token>`` → ``Bearer [redacted]`` (prefix kept).

        The prefix-group capture in the Bearer pattern means the
        ``Bearer `` label survives redaction — only the secret portion
        is replaced. This is what ``test_rw6_api_key_redaction_bearer_token``
        in ``test_pii_redaction.py`` relies on (with the default
        ``"***"`` replacement); this test pins the same behavior for
        the custom-replacement path used by ``credential_store``.
        """
        s = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        out = redact_api_keys(s, replacement="[redacted]")
        assert out == "Authorization: Bearer [redacted]"

    def test_token_prefix_preserved_with_custom_replacement(self):
        """``Token <token>`` → ``Token [redacted]`` (prefix kept)."""
        s = "Token abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        out = redact_api_keys(s, replacement="[redacted]")
        assert out == "Token [redacted]"

    def test_generic_20char_alphanumeric_run_redacted(self):
        """A bare 20+ char alphanumeric run is redacted (G4-L-06).

        This is the catch-all that catches bare hex/base64 keys without
        a recognizable prefix (e.g. Groq ``gsk_...`` keys, GitLab PATs,
        GitHub PATs). ``credential_store._redact_sensitive`` historically
        required 32+ chars; the canonical helper uses 20+ (G4-L-06),
        so a 20-31 char bare token is now also redacted.
        """
        # 20-char bare token (no prefix, no sk-/Bearer/Token).
        token = "0123456789abcdefghij"
        assert len(token) == 20
        assert redact_api_keys(token, replacement="[redacted]") == "[redacted]"

    def test_no_match_returns_input_unchanged(self):
        """When no pattern matches, the input is returned verbatim.

        This is the pass-through behavior that lets
        ``credential_store._redact_sensitive`` use ``redact_api_keys``
        on clean diagnostic strings (e.g. ``"no usable keyring backend
        (fail backend selected)"``) without mangling them.
        """
        s = "no usable keyring backend (fail backend selected)"
        assert redact_api_keys(s) == s
        assert redact_api_keys(s, replacement="[redacted]") == s

    def test_does_not_apply_flag_patterns(self):
        """``redact_api_keys`` must NOT apply the SEC-9 flag patterns.

        ``--token=shortvalue`` (18 chars, no 20+ char alphanum run,
        no ``sk-``/``Bearer``/``Token`` prefix) is left unchanged by
        ``redact_api_keys``. The full ``redact_secret`` would redact
        it via the SEC-9 flag pattern (producing ``--token=***``),
        but ``redact_api_keys`` is the lower-level API-key-only
        helper. This is the contract ``credential_store._redact_sensitive``
        relies on: it never had the flag patterns, and adding them
        would be a behavior change.
        """
        s = "--token=shortvalue"
        # 'shortvalue' is 10 chars — well under the 20-char generic
        # threshold. No sk-/Bearer/Token prefix. So unchanged.
        assert redact_api_keys(s) == s
        assert redact_api_keys(s, replacement="[redacted]") == s

    def test_redact_secret_delegates_to_redact_api_keys(self):
        """XV-121: ``redact_secret`` delegates the API-key portion to
        ``redact_api_keys``.

        For a string with no SEC-9 flag forms (so the flag-pattern pass
        is a no-op) and length >= 20 (so the short-string early-exit
        doesn't fire), ``redact_secret(s)`` must equal
        ``redact_api_keys(s)``. This locks in the DRY refactor: if
        someone re-inlines the API-key patterns in ``redact_secret``,
        this test catches the regression.
        """
        s = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        assert redact_secret(s) == redact_api_keys(s)


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
