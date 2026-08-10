"""URL allowlist tests split out of ``tests/test_security_fixes.py``.

Domain: G4-M-55 (``extend_url_allowlist`` emits a WARNING-level audit
log) + G4-M-56 (``assert_url_allowed`` gains an opt-in
``allow_loopback_http`` kwarg so HTTP loopback is no longer accepted
by default).

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations

import inspect

import pytest
from voice_typer.server import _secrets
from voice_typer.server._secrets import (
    assert_url_allowed,
    extend_url_allowlist,
)


class TestExtendUrlAllowlistAuditLog:
    """G4-M-55: ``extend_url_allowlist`` emits a WARNING-level audit
    log on every call so operators can trace every runtime expansion
    of the trusted-host set back to its origin."""

    def test_warning_emitted_with_hosts(self, caplog):
        """G4-M-55: a WARNING is emitted with the hosts being added."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.security.url_allowlist"):
                extend_url_allowlist(
                    ["audit-test.example.com"],
                    caller="test_g4_m_55_warning_emitted",
                )
            # The WARNING must mention the URL-Allowlist extension.
            assert any("[URL-Allowlist]" in r.message and r.levelname == "WARNING" for r in caplog.records), (
                f"expected WARNING-level URL-Allowlist log; got: {caplog.records!r}"
            )
            # The host must be in the log message.
            assert any("audit-test.example.com" in r.message for r in caplog.records), (
                f"host must be in the log message; got: {caplog.records!r}"
            )
        finally:
            _secrets._user_extensions.discard("audit-test.example.com")

    def test_warning_includes_caller(self, caplog):
        """G4-M-55: the WARNING includes the caller identifier."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.security.url_allowlist"):
                extend_url_allowlist(
                    ["caller-test.example.com"],
                    caller="explicit-caller-id",
                )
            joined = " ".join(r.message for r in caplog.records)
            assert "explicit-caller-id" in joined, f"caller identifier must appear in the log message; got: {joined!r}"
        finally:
            _secrets._user_extensions.discard("caller-test.example.com")

    def test_warning_auto_detects_caller_when_not_passed(self, caplog):
        """G4-M-55: when ``caller=None``, the caller is auto-detected
        via ``inspect.stack()`` and included in the WARNING."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.security.url_allowlist"):
                # Don't pass caller — auto-detection should kick in.
                extend_url_allowlist(["auto-caller.example.com"])
            joined = " ".join(r.message for r in caplog.records)
            # The auto-detected caller should include this test function
            # name (or the test module name).
            assert (
                "test_warning_auto_detects_caller_when_not_passed" in joined
                or "TestG4M55" in joined
                or "test_url_allowlist" in joined
            ), f"auto-detected caller must reference this test; got: {joined!r}"
        finally:
            _secrets._user_extensions.discard("auto-caller.example.com")

    def test_info_emitted_for_empty_input(self, caplog):
        """YJ-44: a no-op call (empty hosts iterable) emits an INFO
        audit record — not a WARNING. WARNING is reserved for the
        security-relevant case (actual hosts being added). The no-op
        case is still audited (so operators can trace every attempt to
        extend the allowlist) but demoted to INFO to avoid WARNING
        spam when callers pass an empty iterable defensively."""
        with caplog.at_level("INFO", logger="voice_typer.server.security.url_allowlist"):
            extend_url_allowlist([], caller="test-empty-input")
        # An INFO record with the URL-Allowlist tag must be emitted.
        assert any("[URL-Allowlist]" in r.message and r.levelname == "INFO" for r in caplog.records), (
            "a no-op extend_url_allowlist call must emit an INFO audit record"
        )
        # And NO WARNING record should be emitted for the no-op case.
        assert not any(r.levelname == "WARNING" and "[URL-Allowlist]" in r.message for r in caplog.records), (
            "a no-op extend_url_allowlist call must NOT emit a WARNING "
            "(YJ-44: WARNING reserved for actual host additions)"
        )


class TestAssertUrlAllowedLoopbackOptIn:
    """G4-M-56: ``assert_url_allowed`` gains an ``allow_loopback_http``
    kwarg (default ``False``). Pre-fix, loopback hosts (localhost,
    127.0.0.1, ::1) were ALWAYS exempt from the HTTPS requirement.
    Post-fix, callers must opt in via the kwarg."""

    def test_http_loopback_rejected_by_default(self):
        """G4-M-56: ``http://localhost:11434`` is REJECTED by default
        (``allow_loopback_http=False``). Pre-fix, it was accepted."""
        with pytest.raises(ValueError, match="HTTPS for loopback"):
            assert_url_allowed(
                "http://localhost:11434/v1/chat/completions",
                field_name="llm_api_url",
                client_name="test",
            )

    def test_http_loopback_allowed_when_opted_in(self):
        """G4-M-56: ``http://localhost:11434`` is ACCEPTED when the
        caller passes ``allow_loopback_http=True``."""
        # Should NOT raise.
        assert_url_allowed(
            "http://localhost:11434/v1/chat/completions",
            field_name="llm_api_url",
            client_name="test",
            allow_loopback_http=True,
        )

    def test_http_127_local_allowed_when_opted_in(self):
        """G4-M-56: ``http://127.0.0.1:8000`` is ACCEPTED when opted in."""
        assert_url_allowed(
            "http://127.0.0.1:8000/v1",
            allow_loopback_http=True,
        )

    def test_http_ipv6_loopback_allowed_when_opted_in(self):
        """G4-M-56: ``http://[::1]:8000`` is ACCEPTED when opted in."""
        assert_url_allowed(
            "http://[::1]:8000/v1",
            allow_loopback_http=True,
        )

    def test_https_loopback_allowed_without_opt_in(self):
        """G4-M-56: HTTPS to loopback is still accepted without opt-in
        (the kwarg only gates HTTP, not HTTPS)."""
        assert_url_allowed("https://localhost:8443/v1")

    def test_https_non_loopback_allowed(self):
        """G4-M-56: regression — HTTPS to a normal allowlisted host
        still works without opt-in."""
        assert_url_allowed("https://api.openai.com/v1/chat/completions")

    def test_http_non_loopback_rejected_even_with_opt_in(self):
        """G4-M-56: ``allow_loopback_http=True`` does NOT open the
        door to HTTP for non-loopback hosts — only loopback is exempted."""
        with pytest.raises(ValueError, match="HTTPS for non-loopback"):
            assert_url_allowed(
                "http://api.openai.com/v1/chat/completions",
                allow_loopback_http=True,
            )

    def test_loopback_http_error_message_mentions_kwarg(self):
        """G4-M-56: the error message for a rejected HTTP loopback URL
        mentions ``allow_loopback_http=True`` so the operator knows
        how to fix the call site."""
        with pytest.raises(ValueError, match="allow_loopback_http=True"):
            assert_url_allowed("http://localhost:11434/v1")

    def test_default_kwarg_value_is_false(self):
        """G4-M-56: the default value of ``allow_loopback_http`` is
        ``False`` — callers must explicitly opt in."""
        sig = inspect.signature(assert_url_allowed)
        param = sig.parameters["allow_loopback_http"]
        assert param.default is False, f"allow_loopback_http default must be False; got {param.default!r}"
