"""Tests for voice_typer.server._secrets — RELIABILITY-004.

Verifies:
- API key redaction from arbitrary strings (log messages, URLs, exceptions)
- URL userinfo redaction
- Cloud URL allowlist (default hosts, runtime extension, assertion behavior)
"""

import os

import pytest
from voice_typer.server import _secrets
from voice_typer.server._secrets import (
    _redact_home_path,
    assert_url_allowed,
    extend_url_allowlist,
    get_url_allowlist,
    is_url_allowed,
    redact_api_keys,
    redact_for_export,
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

    def test_yj48_short_bare_secret_not_redacted_by_default(self):
        """YJ-48: a BARE short secret (no ``Bearer``/``Token``/``--token=``
        prefix) shorter than ``_MIN_REDACT_LEN`` is NOT redacted by default.
        This is the documented gap — the short-string guard exists to
        avoid false-positives on ordinary words. Callers in
        security-critical contexts where bare short secrets are plausible
        should pass ``aggressive=True`` (see
        ``test_yj48_aggressive_redacts_short_bare_secret``).
        """
        # 12-char bare API key — below the 20-char guard.
        bare_short_secret = "sk-abcd1234567"
        assert len(bare_short_secret) < _secrets._MIN_REDACT_LEN
        # Default behaviour: NOT redacted (the documented gap).
        assert redact_secret(bare_short_secret) == bare_short_secret

    def test_yj48_aggressive_redacts_short_bare_secret(self):
        """YJ-48: ``aggressive=True`` bypasses the short-string guard so
        a bare short secret IS redacted via :func:`redact_api_keys`. This
        is the opt-in path for security-critical callers (crash
        excepthook, env-var audit) where bare short secrets are plausible.
        """
        # 12-char bare API key with the OpenAI ``sk-`` prefix — below the
        # 20-char guard but the ``sk-`` prefix is one of the canonical
        # API-key patterns in ``_KEY_PATTERNS``.
        bare_short_secret = "sk-abcd1234567"
        assert len(bare_short_secret) < _secrets._MIN_REDACT_LEN
        redacted = redact_secret(bare_short_secret, aggressive=True)
        # The secret portion MUST be replaced (the ``sk-`` pattern is
        # matched by ``redact_api_keys`` regardless of length when the
        # guard is bypassed).
        assert bare_short_secret not in redacted, (
            f"YJ-48: aggressive=True must redact short bare secrets; got {redacted!r}"
        )
        assert "***" in redacted

    def test_yj48_aggressive_does_not_break_long_secret_redaction(self):
        """YJ-48: ``aggressive=True`` does NOT break redaction of long
        secrets (those above ``_MIN_REDACT_LEN``). It only bypasses the
        short-string early-exit guard.
        """
        long_secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        assert len(long_secret) >= _secrets._MIN_REDACT_LEN
        # Both modes should redact long secrets.
        default_redacted = redact_secret(long_secret)
        aggressive_redacted = redact_secret(long_secret, aggressive=True)
        assert default_redacted == aggressive_redacted
        assert "sk-abcdef" not in default_redacted
        assert "***" in default_redacted


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


class TestPublicEnvVarNamesNotRedacted:
    """T-1-PYTEST-ENV-REDACT-V2: env-var NAMES are public (documented
    in docs / ADRs / source code) and must NOT be redacted. Only their
    VALUES should be redacted. Redacting the name destroys operability
    (operators can't tell which env var is misconfigured from a log
    line like ``[ENV] Invalid value for ***=<redacted>``).

    The generic 20+ char alphanumeric pattern in ``_KEY_PATTERNS`` was
    matching env-var names like ``VOICE_TYPER_CONFIG_DIR`` (21 chars,
    all caps + underscores) and replacing them with ``***``. The fix
    adds a whitelist (``_PUBLIC_ENV_VAR_NAMES``) that skips redaction
    for known names. Real API keys are still redacted.
    """

    @pytest.mark.parametrize(
        "name",
        sorted(_secrets._PUBLIC_ENV_VAR_NAMES),
    )
    def test_whitelisted_env_var_name_survives_redact_secret(self, name):
        """Every name in ``_PUBLIC_ENV_VAR_NAMES`` must survive
        ``redact_secret`` unchanged, both bare and in a typical
        ``[ENV] Invalid value for <NAME>=<redacted>`` log line."""
        # Bare name.
        assert redact_secret(name) == name, (
            f"env var name {name!r} was redacted by redact_secret; "
            f"got {redact_secret(name)!r}"
        )
        # Inside a realistic log line (mirrors env_validation.py).
        line = f"[ENV] Invalid value for {name}=<redacted> -- expected valid path."
        out = redact_secret(line)
        assert name in out, (
            f"env var name {name!r} was redacted inside a log line; got {out!r}"
        )

    def test_whitelisted_env_var_name_survives_redact_api_keys(self):
        """``redact_api_keys`` (the lower-level helper) also preserves
        env-var names — the whitelist lives in the shared code path."""
        for name in _secrets._PUBLIC_ENV_VAR_NAMES:
            assert redact_api_keys(name) == name
            assert redact_api_keys(name, replacement="[redacted]") == name

    def test_unlisted_env_var_shaped_token_is_redacted(self):
        """SEC-003 REGRESSION GUARD: the defense-in-depth
        ``_ENV_VAR_NAME_RE`` heuristic was REMOVED because it also
        exempted real all-caps base64-style secret VALUES (e.g.
        ``SECRET_TOKEN_LIKE_THING_0123456789``), silently downgrading
        redaction. Only names in the explicit ``_PUBLIC_ENV_VAR_NAMES``
        whitelist survive; any other 20+ char uppercase-with-underscore
        token must be redacted."""
        # 24-char all-caps-with-underscore token, NOT in the whitelist.
        unlisted = "UNLISTED_TOKEN_LIKE_THING_0123456789"
        assert redact_secret(unlisted) == "***"
        assert redact_api_keys(unlisted) == "***"
        # A real all-caps base64-style secret VALUE must be masked too.
        token = "SECRET_TOKEN_LIKE_THING_0123456789"
        assert len(token) >= 20
        assert redact_secret(f"value={token}") == "value=***"
        assert len(unlisted) >= 20
        assert unlisted not in _secrets._PUBLIC_ENV_VAR_NAMES
        assert redact_secret(unlisted) == "***"

    def test_real_api_key_still_redacted(self):
        """REGRESSION GUARD: real API keys (sk-…, Bearer …, Token …,
        bare 20+ char alphanumerics) MUST still be redacted. The fix
        narrows the generic pattern's scope; it does NOT disable
        redaction."""
        # OpenAI-style sk- key (mixed case + digits, has hyphen).
        assert redact_secret("sk-abc123def456ghi789jkl") == "***"
        # Bare 20+ char lowercase hex token.
        bare_hex = "0123456789abcdef0123"  # 20 chars
        assert len(bare_hex) == 20
        assert redact_secret(bare_hex) == "***"
        # Bare 20+ char all-caps token WITHOUT underscore (not env-var-
        # like) — must still be redacted.
        bare_caps = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # 26 chars, no underscore
        assert len(bare_caps) == 26
        assert redact_secret(bare_caps) == "***"
        # Bearer prefix + secret.
        out = redact_secret("Authorization: Bearer sk-abc123def456ghi789jkl")
        assert "sk-abc123def456ghi789jkl" not in out
        assert "Bearer" in out

    def test_env_validation_log_lines_preserve_names(self):
        """End-to-end: the exact log lines emitted by
        ``env_validation._validate_env_vars`` survive ``redact_secret``
        with the env-var name intact. This is the regression that
        broke the env_validation pytest suite when the PIIRedactionFilter
        was attached during the full test run."""
        cases = [
            (
                "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=<redacted> "
                "-- expected valid path. Resetting to empty.",
                "VOICE_TYPER_CONFIG_DIR",
            ),
            (
                "[SIDECAR-ENV] expected env var VOICE_TYPER_IPC_TOKEN is unset "
                "(expected <non-empty>)",
                "VOICE_TYPER_IPC_TOKEN",
            ),
            (
                "[ENV] Sensitive env var HUGGING_FACE_HUB_TOKEN was set in the "
                "parent shell — Voice Typer does not read it from env.",
                "HUGGING_FACE_HUB_TOKEN",
            ),
            (
                "[ENV] Invalid value for HF_HOME=<redacted> -- expected valid "
                "path. Resetting to empty.",
                "HF_HOME",
            ),
            (
                "[ENV] HF_ENDPOINT=<redacted> rejected — must use https:// scheme.",
                "HF_ENDPOINT",
            ),
        ]
        for line, name in cases:
            out = redact_secret(line)
            assert name in out, (
                f"env var name {name!r} was redacted from log line; "
                f"input={line!r}; output={out!r}"
            )


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

    # query-string API keys must be redacted ──────────

    def test_ue5_f5_redacts_query_string_api_key(self):
        """UE-5-F5: a ``?key=sk-…`` query-string secret is masked.

        Pre-fix, ``redact_url`` only stripped the userinfo component
        (``user:pass@host``) — secrets in the query string survived
        verbatim. Any caller logging the URL (e.g.
        :class:`_http_safety._NoRedirectHandler` puts the redirect
        target into ``HTTPError.url``) would leak the query-string
        secret.
        """
        url = "https://api.example.com/v1/chat?key=sk-abcdefghijklmnopqrstuvwxyz1234567890"
        out = redact_url(url)
        # The secret MUST NOT appear in the redacted URL.
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in out
        # The ``key=`` prefix is preserved (so support can see a key
        # WAS supplied); only the value is masked.
        assert "key=" in out
        # The host + path are preserved.
        assert "api.example.com" in out
        assert "/v1/chat" in out

    def test_ue5_f5_redacts_query_string_access_token(self):
        """UE-5-F5: ``?access_token=…`` is also masked (the SEC-9
        ``_BARE_KEY_VALUE_PATTERN`` keyword list includes
        ``access_token``)."""
        url = "https://api.example.com/v1/listen?access_token=abcdefghij1234567890XYZ"
        out = redact_url(url)
        assert "abcdefghij1234567890XYZ" not in out
        assert "access_token=" in out

    def test_ue5_f5_redacts_bare_bearer_in_url(self):
        """UE-5-F5: a ``Bearer …`` substring in the URL is masked
        (via the ``_KEY_PATTERNS`` Bearer pattern, not the query-
        string flag pattern)."""
        url = "https://api.example.com/auth?header=Bearer%20sk-abcdefghijklmnopqrstuvwxyz1234567890"
        out = redact_url(url)
        # The sk-… secret portion must not appear (whether percent-
        # encoded or not — the ``sk-`` pattern matches the bare form).
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in out

    def test_ue5_f5_userinfo_plus_query_string_secret_both_redacted(self):
        """UE-5-F5: both the userinfo AND a query-string secret are
        masked in a single call (defense in depth — the userinfo
        strip runs first, then the ``redact_secret`` chained pass
        catches the query-string form)."""
        url = "https://user:pass@api.example.com/v1?key=sk-abcdefghijklmnopqrstuvwxyz1234567890"
        out = redact_url(url)
        # Userinfo gone.
        assert "user:pass" not in out
        # Query-string secret masked.
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in out
        # Host preserved.
        assert "api.example.com" in out

    def test_ue5_f5_redact_url_does_not_mangle_benign_query_strings(self):
        """UE-5-F5: a benign query string with no secret keywords
        passes through unchanged (false-positive guard)."""
        url = "https://api.example.com/v1/listen?model=whisper&language=en"
        out = redact_url(url)
        assert out == url, f"benign query string was mangled: {out!r}"


# _redact_home_path ──────────────────────────────────


class TestRedactHomePath:
    """UE-5-F2: ``_redact_home_path`` replaces the user-home prefix
    with ``~`` so filesystem paths embedded in the diagnostic bundle
    (``sentinel_path``, ``pid_file_path``, ``bundle_path``) don't leak
    the OS username via the home-directory prefix.
    """

    def _expect_home_prefix(self, *parts: str) -> str:
        """Build the platform-correct expected output for a home-
        redacted path.

        ``_redact_home_path`` normalizes via ``os.path.normpath``, so
        the emitted separator is ``os.sep`` (``/`` on POSIX, ``\\`` on
        Windows). Expected values must be built with ``os.sep`` rather
        than hard-coded forward slashes to stay green on both.
        """
        return "~" + os.sep + os.sep.join(parts)

    def test_replaces_posix_home_prefix(self, monkeypatch):
        """A POSIX home path ``/home/alice/.voice-typer/...`` becomes
        ``~/.voice-typer/...``."""
        monkeypatch.setattr("os.path.expanduser", lambda p: "/home/alice" if p == "~" else p)
        out = _redact_home_path("/home/alice/.voice-typer/.prewarm-sentinel")
        assert out == self._expect_home_prefix(".voice-typer", ".prewarm-sentinel"), out

    def test_replaces_macos_home_prefix(self, monkeypatch):
        """A macOS home path ``/Users/alice/...`` becomes ``~/...``."""
        monkeypatch.setattr("os.path.expanduser", lambda p: "/Users/alice" if p == "~" else p)
        out = _redact_home_path("/Users/alice/.voice-typer/diagnostics.zip")
        assert out == self._expect_home_prefix(".voice-typer", "diagnostics.zip"), out

    def test_replaces_windows_home_prefix(self, monkeypatch):
        """A Windows home path ``C:\\Users\\alice\\...`` becomes
        ``~\\...`` (case-insensitive comparison)."""
        monkeypatch.setattr("os.path.expanduser", lambda p: "C:\\Users\\alice" if p == "~" else p)
        monkeypatch.setattr("os.name", "nt", raising=False)
        # ``os.path.normpath`` on POSIX collapses backslashes inside
        # the path string differently than on Windows; the helper
        # uses ``os.sep`` semantics. We test with the platform-
        # appropriate normalisation by mocking both ``expanduser``
        # and ``os.name``.
        out = _redact_home_path("C:\\Users\\alice\\.voice-typer\\diagnostics.zip")
        assert out.startswith("~"), out
        assert "alice" not in out, f"username leaked: {out!r}"

    def test_path_outside_home_unchanged(self, monkeypatch):
        """A path that does NOT start with the home prefix is
        returned unchanged (no spurious ``~`` insertion)."""
        monkeypatch.setattr("os.path.expanduser", lambda p: "/home/alice" if p == "~" else p)
        out = _redact_home_path("/etc/passwd")
        assert out == "/etc/passwd"

    def test_relative_path_unchanged(self, monkeypatch):
        """A relative path is returned unchanged."""
        monkeypatch.setattr("os.path.expanduser", lambda p: "/home/alice" if p == "~" else p)
        out = _redact_home_path("relative/path/to/file")
        assert out == "relative/path/to/file"

    def test_pathlike_input_accepted(self, monkeypatch):
        """``PathLike`` inputs (e.g. ``pathlib.Path``) are stringified
        and the home prefix is still redacted."""
        from pathlib import Path

        monkeypatch.setattr("os.path.expanduser", lambda p: "/home/alice" if p == "~" else p)
        out = _redact_home_path(Path("/home/alice/.voice-typer/.prewarm-sentinel"))
        assert out == self._expect_home_prefix(".voice-typer", ".prewarm-sentinel"), out

    def test_empty_home_returns_path_unchanged(self, monkeypatch):
        """If ``os.path.expanduser('~')`` returns ``'~'`` (cannot
        determine home), the path is returned unchanged (no infinite
        recursion, no spurious ``~`` insertion)."""
        monkeypatch.setattr("os.path.expanduser", lambda p: "~")
        out = _redact_home_path("/home/alice/.voice-typer")
        assert out == "/home/alice/.voice-typer"

    def test_trailing_slash_in_home_handled(self, monkeypatch):
        """If the home dir has a trailing slash (some platforms add
        one), the prefix comparison still works (via ``normpath``
        normalization)."""
        # expanduser normally returns without trailing slash; simulate
        # the edge case where it does (or where the test path has a
        # double slash).
        monkeypatch.setattr("os.path.expanduser", lambda p: "/home/alice/" if p == "~" else p)
        out = _redact_home_path("/home/alice/.voice-typer/.prewarm-sentinel")
        assert out == self._expect_home_prefix(".voice-typer", ".prewarm-sentinel"), out


# redact_for_export unified pipeline ───────


class TestRedactForExport:
    """UE-5-F4: ``redact_for_export`` is the unified PII + secret
    redaction pipeline used by both ``diagnostics_export`` (live log
    + archived crash dumps) and ``ipc_diagnostics`` (startup-error
    traceback). UE-5-F7: it passes ``aggressive=True`` to
    :func:`redact_secret` so short bare secrets are caught.
    """

    def test_redacts_bearer_token(self):
        """A Bearer token is masked, prefix preserved."""
        out = redact_for_export("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert "Bearer" in out
        assert "sk-abcdef" not in out
        assert "***" in out

    def test_redacts_pii_email(self):
        """An email address is masked with the ``[EMAIL]`` token."""
        out = redact_for_export("contact: alice@example.com")
        assert "alice@example.com" not in out
        assert "[EMAIL]" in out

    def test_redacts_pii_phone(self):
        """An international phone number is masked with ``[PHONE]``."""
        out = redact_for_export("call me at +1 (415) 555-2671")
        assert "555-2671" not in out
        assert "[PHONE]" in out

    def test_redacts_url_userinfo(self):
        """URL-embedded ``user:pass@`` credentials are stripped.

        Note: the ``pass@api.example.com`` substring looks like an
        email to the PII pattern matcher, so ``redact_pii`` may
        redact it as ``[EMAIL]`` rather than just stripping the
        userinfo — either way, the credential is masked.
        """
        out = redact_for_export("fetching https://aliceuser:secretpass@api.example.com/v1")
        # The credential MUST NOT appear in the redacted output.
        assert "secretpass" not in out
        assert "aliceuser:secretpass" not in out
        # The output should mention either the host (if userinfo strip
        # ran first) or an ``[EMAIL]`` token (if the PII matcher fired
        # first). Both are acceptable — the credential is masked
        # either way.
        assert "api.example.com" in out or "[EMAIL]" in out, out

    def test_redacts_url_query_string_api_key(self):
        """UE-5-F5 integration: a ``?key=sk-…`` query-string secret
        in the URL is masked (because ``redact_for_export`` calls
        ``redact_pii`` which calls ``redact_url`` which now chains
        through ``redact_secret``)."""
        out = redact_for_export("GET https://api.example.com/?key=sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in out
        assert "api.example.com" in out

    def test_ue5_f7_aggressive_redacts_short_bare_secret(self):
        """UE-5-F7: a BARE short secret (no ``Bearer``/``--token=``
        prefix, under 20 chars) IS redacted by ``redact_for_export``
        because the unified pipeline passes ``aggressive=True``.

        Without ``aggressive=True``, the short-string guard from
        :func:`redact_secret` would skip the generic 20+ char
        alphanumeric pattern application on short inputs. The bare
        ``sk-or-...`` prefix is matched by ``_KEY_PATTERNS[2]``
        regardless of length, but a hypothetical 12-char bare token
        with no prefix would be missed without aggressive.
        """
        # ``sk-`` prefix is matched by _KEY_PATTERNS regardless of
        # length when aggressive=True; without aggressive, the
        # short-string guard (< 20 chars) returns it unchanged.
        short_bare_secret = "sk-abcd1234567"  # 14 chars
        assert len(short_bare_secret) < _secrets._MIN_REDACT_LEN
        out = redact_for_export(f"key={short_bare_secret}")
        assert short_bare_secret not in out, f"UE-5-F7: aggressive=True must redact short bare secrets; got {out!r}"

    def test_idempotent_on_already_redacted_text(self):
        """Running ``redact_for_export`` on already-redacted text
        returns it unchanged (the ``***`` mask doesn't match the
        secret patterns)."""
        once = redact_for_export("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890")
        twice = redact_for_export(once)
        assert once == twice

    def test_preserves_ordinary_long_text(self):
        """A long non-secret-bearing log line passes through
        unchanged (false-positive guard)."""
        line = "This is a perfectly normal log line about a network timeout error."
        out = redact_for_export(line)
        assert out == line


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

    def test_empty_url_rejected(self):
        """Empty URLs are rejected (consistent with assert_url_allowed)."""
        assert is_url_allowed("") is False

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
