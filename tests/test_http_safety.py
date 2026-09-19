"""Real unit tests for ``voice_typer.server._http_safety``."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPSHandler, Request

import pytest
from voice_typer.server._http_safety import (
    _HttpsOnlyHTTPHandler,
    _NoRedirectHandler,
    build_secure_opener,
)


class TestNoRedirectHandlerRedactsUrl:
    """DE-64: ``_NoRedirectHandler.redirect_request`` raises"""

    def test_url_attribute_is_redacted_when_userinfo_present(self):
        """When the redirect target embeds ``user:pass@host``, the"""
        newurl = "https://alice:secret@attacker.example.com/steal"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(
                req=None,  # not read by the handler
                fp=None,
                code=302,
                msg="Found",
                headers=None,
                newurl=newurl,
            )
        err = exc_info.value
        # the .url attribute must NOT contain the raw credentials.
        assert "alice:secret" not in err.url
        # The host + scheme + path must be preserved (redact_url only
        assert "attacker.example.com" in err.url
        assert err.url.startswith("https://")
        assert "/steal" in err.url

    def test_message_also_redacted_when_userinfo_present(self):
        """The error message must ALSO be redacted (this was already"""
        newurl = "https://bob:hunter2@attacker.example.com/exfil"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 301, "Moved", None, newurl)
        msg = str(exc_info.value)
        assert "bob:hunter2" not in msg
        assert "attacker.example.com" in msg

    def test_url_attribute_unchanged_when_no_userinfo(self):
        """When the redirect target has NO embedded credentials,"""
        newurl = "https://api.openai.com/v1/audio/transcriptions"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 302, "Found", None, newurl)
        assert exc_info.value.url == newurl

    def test_http_error_code_and_message_passthrough(self):
        """The redirect's HTTP status code and message must be passed"""
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 307, "Temporary Redirect", None, "https://example.com/x")
        assert exc_info.value.code == 307
        # The message includes both the code and the redirect-refused
        assert "307" in str(exc_info.value)
        assert "SEC-2" in str(exc_info.value)

    def test_credentials_with_special_chars_redacted(self):
        """Userinfo containing URL-special characters (``:``, ``@``,"""
        newurl = "https://user:p%40ss%2Fword@host.example.com/path"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 302, "Found", None, newurl)
        # The raw credential substring must not appear in .url.
        assert "p%40ss%2Fword" not in exc_info.value.url
        # The host + path must survive.
        assert "host.example.com" in exc_info.value.url


class TestBuildSecureOpener:
    """DE-65: ``build_secure_opener`` must install an ``HTTPHandler``"""

    def test_opener_has_https_only_http_handler(self):
        """``_HttpsOnlyHTTPHandler`` instance (the custom subclass), NOT"""
        opener = build_secure_opener()
        handler_classes = [type(h) for h in opener.handlers]
        # The custom subclass must be present.
        assert _HttpsOnlyHTTPHandler in handler_classes
        # The default HTTPHandler must NOT be present (the custom
        bare_http_handlers = [h for h in opener.handlers if type(h) is HTTPHandler]
        assert bare_http_handlers == []

    def test_opener_has_https_handler(self):
        """The opener must still have an ``HTTPSHandler`` (for TLS"""
        opener = build_secure_opener()
        handler_classes = [type(h) for h in opener.handlers]
        assert HTTPSHandler in handler_classes

    def test_opener_has_no_redirect_handler(self):
        """SEC-2 contract: the opener must use ``_NoRedirectHandler``"""
        opener = build_secure_opener()
        handler_classes = [type(h) for h in opener.handlers]
        assert _NoRedirectHandler in handler_classes


class TestHttpsOnlyHTTPHandler:
    """DE-65: ``_HttpsOnlyHTTPHandler.http_open`` refuses plaintext"""

    def test_refuses_http_for_non_loopback_host(self):
        """A plaintext HTTP request to a non-loopback host must raise"""
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://attacker.example.com/steal?api_key=abc")
        with pytest.raises(URLError) as exc_info:
            handler.http_open(req)
        # The error message must mention  and the host.
        msg = str(exc_info.value)
        assert "DE-65" in msg
        assert "attacker.example.com" in msg

    def test_refuses_http_for_localhost_loopback_ipv4(self):
        """Wait, actually loopback hosts ARE exempted. This test pins"""
        # We can't actually perform the HTTP request without a server,
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://127.0.0.1:11434/v1/chat")

        called = {"super": False}

        def fake_super_http_open(_self, _req):
            called["super"] = True
            return None  # don't actually do anything

        # Patch the superclass method to verify it was called (i.e.
        original = HTTPHandler.http_open
        try:
            HTTPHandler.http_open = fake_super_http_open  # type: ignore[assignment]
            handler.http_open(req)
        finally:
            HTTPHandler.http_open = original  # type: ignore[assignment]
        assert called["super"] is True

    def test_refuses_http_for_localhost_loopback_ipv6(self):
        """Same as above for the IPv6 loopback ``::1``."""
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://[::1]:8080/v1/chat")

        called = {"super": False}

        def fake_super_http_open(_self, _req):
            called["super"] = True
            return None

        original = HTTPHandler.http_open
        try:
            HTTPHandler.http_open = fake_super_http_open  # type: ignore[assignment]
            handler.http_open(req)
        finally:
            HTTPHandler.http_open = original  # type: ignore[assignment]
        assert called["super"] is True

    def test_refuses_http_for_localhost_named(self):
        """Same as above for the named loopback ``localhost``."""
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://localhost:8080/v1/chat")

        called = {"super": False}

        def fake_super_http_open(_self, _req):
            called["super"] = True
            return None

        original = HTTPHandler.http_open
        try:
            HTTPHandler.http_open = fake_super_http_open  # type: ignore[assignment]
            handler.http_open(req)
        finally:
            HTTPHandler.http_open = original  # type: ignore[assignment]
        assert called["super"] is True

    def test_does_not_call_super_for_non_loopback(self):
        """For a non-loopback host, ``http_open`` must raise BEFORE"""
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://attacker.example.com/steal")

        called = {"super": False}

        def fake_super_http_open(_self, _req):
            called["super"] = True
            return None

        original = HTTPHandler.http_open
        try:
            HTTPHandler.http_open = fake_super_http_open  # type: ignore[assignment]
            with pytest.raises(URLError):
                handler.http_open(req)
        finally:
            HTTPHandler.http_open = original  # type: ignore[assignment]
        assert called["super"] is False

    def test_loopback_set_is_documented(self):
        """The loopback exemption set must contain exactly the three"""
        assert frozenset({"localhost", "127.0.0.1", "::1"}) == _HttpsOnlyHTTPHandler._LOOPBACK_HOSTS


class TestNoOverrideSuppression:
    """``# type: ignore[override]`` suppression marker. The overrides are"""

    def _method_def_line(self, cls: type, name: str) -> str:
        """Return the source line of the ``def <name>(...)`` header"""
        import inspect

        src = inspect.getsource(getattr(cls, name))
        # The first non-empty source line is the ``def ...`` header
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("def "):
                return stripped
        return ""

    def test_redirect_request_has_no_override_suppression(self):
        """``_NoRedirectHandler.redirect_request`` must not carry a"""
        def_line = self._method_def_line(_NoRedirectHandler, "redirect_request")
        assert def_line.startswith("def redirect_request(")
        assert "type: ignore" not in def_line, (
            "YJ-26 regression: `# type: ignore` reintroduced on "
            "`_NoRedirectHandler.redirect_request`. The override is "
            "typed to match the parent signature exactly: see the "
            "YJ-26 fix commit in _http_safety.py for the rationale."
        )

    def test_http_open_has_no_override_suppression(self):
        """``# type: ignore[override]`` marker (YJ-26 line ``:129``)."""
        def_line = self._method_def_line(_HttpsOnlyHTTPHandler, "http_open")
        assert def_line.startswith("def http_open(")
        assert "type: ignore" not in def_line, (
            "YJ-26 regression: `# type: ignore` reintroduced on "
            "`_HttpsOnlyHTTPHandler.http_open`. The override return "
            "type is `http.client.HTTPResponse` (matching the parent "
            "typeshed signature), no suppression is needed."
        )

    def test_http_open_return_type_matches_parent(self):
        """The override's return annotation must be the parent's"""
        import http.client
        import typing

        hints = typing.get_type_hints(_HttpsOnlyHTTPHandler.http_open)
        assert "return" in hints, (
            "YJ-26 regression: `http_open` has no return annotation, "
            "the override MUST be typed `-> http.client.HTTPResponse` "
            "to match the parent signature."
        )
        assert hints["return"] is http.client.HTTPResponse, (
            f"YJ-26 regression: `http_open` return type is "
            f"`{hints['return']!r}`, expected "
            f"`http.client.HTTPResponse`. Widening to `object` or "
            f"`Any` would require a `# type: ignore[override]` "
            f"suppression marker (which YJ-26 explicitly removed)."
        )
        assert "req" in hints, "YJ-26 regression: `http_open` is missing the `req` parameter annotation."
        from urllib.request import Request as UrllibRequest

        assert hints["req"] is UrllibRequest


class TestLoopbackHostsIsDRY:
    """FR-35: ``_HttpsOnlyHTTPHandler._LOOPBACK_HOSTS`` must be"""

    def test_loopback_hosts_value_matches_canonical(self):
        """The class attribute's VALUE must equal the canonical"""
        from voice_typer.server._http_safety import _HttpsOnlyHTTPHandler
        from voice_typer.server._paths import LOOPBACK_HOSTS

        assert _HttpsOnlyHTTPHandler._LOOPBACK_HOSTS == LOOPBACK_HOSTS, (
            "FR-35 regression: _HttpsOnlyHTTPHandler._LOOPBACK_HOSTS does not "
            "match the canonical _paths.LOOPBACK_HOSTS, the two definitions "
            "have drifted, which means a future change to the canonical set "
            "won't propagate to the HTTP-safety gate."
        )

    def test_loopback_hosts_source_not_hardcoded_literal(self):
        """``_HttpsOnlyHTTPHandler`` class body, the value must come"""
        import inspect

        from voice_typer.server._http_safety import _HttpsOnlyHTTPHandler

        src = inspect.getsource(_HttpsOnlyHTTPHandler)
        # The class body must NOT re-declare the literal set.
        assert 'frozenset({"localhost", "127.0.0.1", "::1"})' not in src, (
            "FR-35 regression: _HttpsOnlyHTTPHandler class body re-declares "
            "the loopback set as an inline frozenset literal. Import "
            "LOOPBACK_HOSTS from voice_typer.server._paths instead."
        )

    def test_loopback_hosts_imported_from_paths(self):
        """The ``_http_safety`` module must import ``LOOPBACK_HOSTS``"""
        import inspect

        from voice_typer.server import _http_safety

        src = inspect.getsource(_http_safety)
        assert "from voice_typer.server._paths import LOOPBACK_HOSTS" in src, (
            "FR-35 regression: _http_safety.py does not import LOOPBACK_HOSTS "
            "from voice_typer.server._paths, the DRY violation is back."
        )
