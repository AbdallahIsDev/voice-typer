"""Real unit tests for ``voice_typer.server._http_safety``.

DE-64: ``HTTPError.url`` must be redacted (``redact_url(newurl)``) so
callers that log ``e.url`` don't leak embedded ``user:pass@host``
userinfo. Pre-fix only the message string was redacted; the ``.url``
attribute preserved the raw redirect target — a credential-leak
surface.

DE-65: ``build_secure_opener()`` must install a custom ``HTTPHandler``
subclass that REFUSES plaintext HTTP for non-loopback hosts. Pre-fix
``build_opener(HTTPSHandler(), _NoRedirectHandler())`` left the
default ``HTTPHandler`` installed — so a caller passing an
``http://attacker.example.com/steal`` URL would have its request body
(API key + user audio) transmitted in plaintext. The new
``_HttpsOnlyHTTPHandler`` raises ``URLError`` for non-loopback HTTP
URLs (loopback hosts are exempted for local development servers like
Ollama / vLLM / LM Studio).

Test approach: we don't hit the network — we instantiate the opener
and inspect its handler set, and we drive ``_NoRedirectHandler.redirect_request``
directly with a fake ``Request`` to verify the raised ``HTTPError.url``
is redacted. For the HTTPS-enforcement path we drive
``_HttpsOnlyHTTPHandler.http_open`` directly with a constructed
``Request``.
"""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPSHandler, Request

import pytest
from voice_typer.server._http_safety import (
    _HttpsOnlyHTTPHandler,
    _NoRedirectHandler,
    build_secure_opener,
)

# ---------------------------------------------------------------------------
# DE-64: HTTPError.url is redacted
# ---------------------------------------------------------------------------


class TestNoRedirectHandlerRedactsUrl:
    """DE-64: ``_NoRedirectHandler.redirect_request`` raises
    ``HTTPError`` whose ``.url`` attribute is the REDACTED redirect
    target (not the raw ``newurl``)."""

    def test_url_attribute_is_redacted_when_userinfo_present(self):
        """When the redirect target embeds ``user:pass@host``, the
        ``HTTPError.url`` attribute must NOT contain the credentials."""
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
        # DE-64: the .url attribute must NOT contain the raw credentials.
        assert "alice:secret" not in err.url
        # The host + scheme + path must be preserved (redact_url only
        # strips userinfo).
        assert "attacker.example.com" in err.url
        assert err.url.startswith("https://")
        assert "/steal" in err.url

    def test_message_also_redacted_when_userinfo_present(self):
        """The error message must ALSO be redacted (this was already
        true pre-fix; the test pins the contract so a regression to
        message-redaction-only would be caught)."""
        newurl = "https://bob:hunter2@attacker.example.com/exfil"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 301, "Moved", None, newurl)
        msg = str(exc_info.value)
        assert "bob:hunter2" not in msg
        assert "attacker.example.com" in msg

    def test_url_attribute_unchanged_when_no_userinfo(self):
        """When the redirect target has NO embedded credentials,
        ``redact_url`` returns the URL unchanged. The ``HTTPError.url``
        must equal the input URL."""
        newurl = "https://api.openai.com/v1/audio/transcriptions"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 302, "Found", None, newurl)
        assert exc_info.value.url == newurl

    def test_http_error_code_and_message_passthrough(self):
        """The redirect's HTTP status code and message must be passed
        through to the ``HTTPError`` constructor unchanged (callers
        branch on ``e.code`` for 3xx-specific handling)."""
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 307, "Temporary Redirect", None, "https://example.com/x")
        assert exc_info.value.code == 307
        # The message includes both the code and the redirect-refused
        # marker so callers can distinguish a refused-redirect error
        # from a server-returned HTTPError.
        assert "307" in str(exc_info.value)
        assert "SEC-2" in str(exc_info.value)

    def test_credentials_with_special_chars_redacted(self):
        """Userinfo containing URL-special characters (``:``, ``@``,
        ``/``) must still be stripped from ``HTTPError.url``."""
        newurl = "https://user:p%40ss%2Fword@host.example.com/path"
        handler = _NoRedirectHandler()
        with pytest.raises(HTTPError) as exc_info:
            handler.redirect_request(None, None, 302, "Found", None, newurl)
        # The raw credential substring must not appear in .url.
        assert "p%40ss%2Fword" not in exc_info.value.url
        # The host + path must survive.
        assert "host.example.com" in exc_info.value.url


# ---------------------------------------------------------------------------
# DE-65: build_secure_opener refuses plaintext HTTP for non-loopback
# ---------------------------------------------------------------------------


class TestBuildSecureOpener:
    """DE-65: ``build_secure_opener`` must install an ``HTTPHandler``
    subclass that refuses plaintext HTTP for non-loopback hosts."""

    def test_opener_has_https_only_http_handler(self):
        """The opener's handler chain must include an
        ``_HttpsOnlyHTTPHandler`` instance (the custom subclass), NOT
        the default ``HTTPHandler``. We verify by inspecting the
        opener's ``.handlers`` list."""
        opener = build_secure_opener()
        handler_classes = [type(h) for h in opener.handlers]
        # The custom subclass must be present.
        assert _HttpsOnlyHTTPHandler in handler_classes
        # The default HTTPHandler must NOT be present (the custom
        # subclass replaces it, not adds alongside).
        # Note: build_opener deduplicates by handler-class — the last
        # handler of a given class wins. So if both were present, the
        # custom subclass would be the effective one. But the contract
        # is that the default is REPLACED, so we assert there is no
        # bare ``HTTPHandler`` instance whose class is exactly
        # ``HTTPHandler`` (i.e. not a subclass).
        bare_http_handlers = [h for h in opener.handlers if type(h) is HTTPHandler]
        assert bare_http_handlers == []

    def test_opener_has_https_handler(self):
        """The opener must still have an ``HTTPSHandler`` (for TLS
        requests) — the DE-65 fix only restricts plaintext HTTP, not
        HTTPS."""
        opener = build_secure_opener()
        handler_classes = [type(h) for h in opener.handlers]
        assert HTTPSHandler in handler_classes

    def test_opener_has_no_redirect_handler(self):
        """SEC-2 contract: the opener must use ``_NoRedirectHandler``
        so 3xx redirects are refused (not silently followed)."""
        opener = build_secure_opener()
        handler_classes = [type(h) for h in opener.handlers]
        assert _NoRedirectHandler in handler_classes


class TestHttpsOnlyHTTPHandler:
    """DE-65: ``_HttpsOnlyHTTPHandler.http_open`` refuses plaintext
    HTTP for non-loopback hosts, and allows it for loopback."""

    def test_refuses_http_for_non_loopback_host(self):
        """A plaintext HTTP request to a non-loopback host must raise
        ``URLError`` (no network call attempted)."""
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://attacker.example.com/steal?api_key=abc")
        with pytest.raises(URLError) as exc_info:
            handler.http_open(req)
        # The error message must mention DE-65 and the host.
        msg = str(exc_info.value)
        assert "DE-65" in msg
        assert "attacker.example.com" in msg

    def test_refuses_http_for_localhost_loopback_ipv4(self):
        """Wait — actually loopback hosts ARE exempted. This test pins
        that ``127.0.0.1`` is in the loopback exemption set so local
        development servers can serve plaintext HTTP."""
        # We can't actually perform the HTTP request without a server,
        # but we can verify the handler doesn't raise URLError at the
        # scheme-check stage by giving it a URL whose host is loopback
        # and checking that http_open attempts the superclass method.
        # We mock the superclass's http_open to avoid a real network
        # call.
        handler = _HttpsOnlyHTTPHandler()
        req = Request("http://127.0.0.1:11434/v1/chat")

        called = {"super": False}

        def fake_super_http_open(_self, _req):
            called["super"] = True
            return None  # don't actually do anything

        # Patch the superclass method to verify it was called (i.e.
        # the loopback exemption took effect and the handler delegated
        # to the real HTTP fetch path).
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
        """For a non-loopback host, ``http_open`` must raise BEFORE
        delegating to the superclass's ``http_open`` (otherwise the
        request body could be transmitted in plaintext before the
        check fires)."""
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
        """The loopback exemption set must contain exactly the three
        documented loopback hosts (no more, no less)."""
        assert frozenset({"localhost", "127.0.0.1", "::1"}) == _HttpsOnlyHTTPHandler._LOOPBACK_HOSTS


# ---------------------------------------------------------------------------
# YJ-26: no ``# type: ignore[override]`` suppression on urllib overrides
# ---------------------------------------------------------------------------


class TestYJ26NoOverrideSuppression:
    """YJ-26: ``_NoRedirectHandler.redirect_request`` and
    ``_HttpsOnlyHTTPHandler.http_open`` must NOT carry a
    ``# type: ignore[override]`` suppression marker. The overrides are
    typed to match the parent class signatures exactly (per typeshed),
    so the suppression is unnecessary and would silently mask future
    type drift between the override and ``urllib.request``."""

    def _method_def_line(self, cls: type, name: str) -> str:
        """Return the source line of the ``def <name>(...)`` header
        for the given class+method (no body, no decorators)."""
        import inspect

        src = inspect.getsource(getattr(cls, name))
        # The first non-empty source line is the ``def ...`` header
        # (inspect.getsource on a method does NOT include decorators
        # when the method has none, but it DOES include the docstring
        # body — we want just the ``def`` line).
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("def "):
                return stripped
        # If we somehow don't find a def line, return empty so the
        # ``"type: ignore" not in`` assertion below passes trivially
        # and the assertion on ``startswith("def")`` catches the
        # regression.
        return ""

    def test_redirect_request_has_no_override_suppression(self):
        """``_NoRedirectHandler.redirect_request`` must not carry a
        ``# type: ignore[override]`` marker (YJ-26 line ``:76``)."""
        def_line = self._method_def_line(_NoRedirectHandler, "redirect_request")
        assert def_line.startswith("def redirect_request(")
        assert "type: ignore" not in def_line, (
            "YJ-26 regression: `# type: ignore` reintroduced on "
            "`_NoRedirectHandler.redirect_request`. The override is "
            "typed to match the parent signature exactly — see the "
            "YJ-26 fix commit in _http_safety.py for the rationale."
        )

    def test_http_open_has_no_override_suppression(self):
        """``_HttpsOnlyHTTPHandler.http_open`` must not carry a
        ``# type: ignore[override]`` marker (YJ-26 line ``:129``).
        The return type is ``http.client.HTTPResponse`` to match the
        parent ``HTTPHandler.http_open`` signature per typeshed."""
        def_line = self._method_def_line(_HttpsOnlyHTTPHandler, "http_open")
        assert def_line.startswith("def http_open(")
        assert "type: ignore" not in def_line, (
            "YJ-26 regression: `# type: ignore` reintroduced on "
            "`_HttpsOnlyHTTPHandler.http_open`. The override return "
            "type is `http.client.HTTPResponse` (matching the parent "
            "typeshed signature) — no suppression is needed."
        )

    def test_http_open_return_type_matches_parent(self):
        """The override's return annotation must be the parent's
        return type (``http.client.HTTPResponse`` per typeshed), NOT
        ``object`` or ``Any`` — widening the return type violates
        covariance and was the original reason the ``# type: ignore``
        marker was added."""
        import http.client
        import typing

        # inspect.signature returns the resolved annotation only if
        # ``from __future__ import annotations`` is NOT in effect.
        # The module DOES use ``from __future__ import annotations``
        # (line 34), so we resolve via ``typing.get_type_hints``.
        hints = typing.get_type_hints(_HttpsOnlyHTTPHandler.http_open)
        # ``get_type_hints`` on an unbound method includes ``self`` and
        # the ``return`` key. PEP 563 + ``from __future__ import
        # annotations`` means the annotation strings are resolved
        # against the module globals (which include ``http.client``
        # and ``Request``).
        assert "return" in hints, (
            "YJ-26 regression: `http_open` has no return annotation — "
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
        # Sanity check that ``req`` is typed as ``Request`` (so the
        # override signature is fully aligned with the parent).
        assert "req" in hints, "YJ-26 regression: `http_open` is missing the `req` parameter annotation."
        from urllib.request import Request as UrllibRequest

        assert hints["req"] is UrllibRequest
