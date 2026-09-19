"""Regression tests: ``cloud_test_handlers`` must NOT follow 3xx redirects."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from voice_typer.server._http_safety import (
    _NoRedirectHandler,
    build_secure_opener,
)
from voice_typer.server.handlers import cloud_test_handlers


class TestSecureOpenerWiring:
    """
    The handler module MUST use a secure opener that does NOT follow
    These tests pin the *composition* of ``cloud_test_handlers._opener``
    """

    def test_module_opener_is_build_secure_opener_instance(self):
        """``build_secure_opener()``. We verify by re-building a fresh"""
        opener = cloud_test_handlers._opener
        handler_types = {type(h) for h in opener.handlers}
        assert _NoRedirectHandler in handler_types, (
            "cloud_test_handlers._opener does NOT contain "
            "_NoRedirectHandler, the opener would silently follow 3xx "
            "redirects, retransmitting the Authorization: Bearer <api_key> "
            "header to an attacker-controlled redirect target. The module "
            "must use build_secure_opener() (mirrors cloud_engines._opener)."
        )

    def test_module_opener_matches_fresh_secure_opener_handler_set(self):
        """The handler module's opener must have the SAME handler-class"""
        module_handler_types = {type(h) for h in cloud_test_handlers._opener.handlers}
        fresh_handler_types = {type(h) for h in build_secure_opener().handlers}
        assert module_handler_types == fresh_handler_types, (
            "cloud_test_handlers._opener handler set has drifted from "
            "build_secure_opener(), the module must use the shared "
            "secure opener (single source of truth in _http_safety)."
        )

    def test_module_does_not_import_bare_urlopen(self):
        """default opener that follows 3xx redirects. Post-fix the handler"""
        src = inspect.getsource(cloud_test_handlers)
        # The module must use _opener.open(req, ...), the call site.
        assert "_opener.open(" in src, (
            "cloud_test_handlers does not call _opener.open(...), the secure opener is not wired into the request path."
        )
        # The module must NOT import the bare ``urlopen`` symbol. We
        assert "from urllib.request import Request, urlopen" not in src, (
            "cloud_test_handlers imports bare ``urlopen`` from "
            "urllib.request, the default opener follows 3xx redirects "
            "(SEC-2 bypass). Use _opener.open(req, ...) instead."
        )
        assert "from urllib.request import urlopen" not in src, (
            "cloud_test_handlers imports bare ``urlopen`` from "
            "urllib.request, the default opener follows 3xx redirects "
            "(SEC-2 bypass). Use _opener.open(req, ...) instead."
        )


def _make_handler_with_openai_key(api_key: str) -> cloud_test_handlers.CloudTestHandlersMixin:
    """Build a ``CloudTestHandlersMixin`` instance with a fake app whose"""
    handler = cloud_test_handlers.CloudTestHandlersMixin()
    handler.app = SimpleNamespace(config=SimpleNamespace(openai_api_key=api_key))
    # ``_respond_with_error`` path, which must NOT fire on the redirect
    handler.service = MagicMock()
    handler._send = MagicMock()
    return handler


def _redirect_http_error(code: int = 302, target: str = "https://attacker.example.com/steal") -> HTTPError:
    """Build the ``HTTPError`` that ``_NoRedirectHandler.redirect_request``"""
    return HTTPError(
        url=target,
        code=code,
        msg=f"redirect refused (SEC-2): {code} Found -> {target}",
        hdrs=None,
        fp=None,
    )


class TestHandlerRefusesRedirect:
    """When the provider endpoint returns a 3xx redirect, the handler"""

    def test_redirect_surfaces_as_cloud_test_result_with_redirect_status(self):
        """redirect's status code, NOT a 200 from a silently-followed"""
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        mock_open = MagicMock(side_effect=_redirect_http_error(302))
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            result = handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        assert result is resp
        assert resp["type"] == "cloud_test_result"
        assert resp["data"]["ok"] is False
        # The redirect's status code (302) MUST be surfaced, NOT a 200
        assert resp["data"]["status"] == 302, (
            "Handler reported a status other than 302 for a 302 redirect, "
            "this means the opener silently followed the redirect (SEC-2 "
            "bypass) and reported the final status (typically 200)."
        )

    def test_handler_does_not_retry_or_follow_after_redirect(self):
        """When the opener raises ``HTTPError(302)``, the handler MUST"""
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        mock_open = MagicMock(side_effect=_redirect_http_error(301))
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        assert mock_open.call_count == 1, (
            f"Handler invoked _opener.open {mock_open.call_count} times, "
            "expected exactly 1 (the redirect must NOT be followed by a "
            "second request to the redirect target, which would "
            "retransmit the Authorization: Bearer <api_key> header)."
        )

    def test_authorization_header_was_on_original_request(self):
        """The ``Authorization: Bearer <api_key>`` header MUST be"""
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        captured: dict = {}

        def capture_and_raise(req: Request, timeout: float | None = None):
            captured["headers"] = dict(req.header_items())
            captured["url"] = req.full_url
            raise _redirect_http_error(302)

        mock_open = MagicMock(side_effect=capture_and_raise)
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        # The original request MUST carry the Authorization header.
        auth = captured["headers"].get("Authorization")
        assert auth is not None, (
            "The Authorization header was NOT on the original request "
            "passed to _opener.open, the API key must be attached in "
            "Python (the renderer never sees the key)."
        )
        assert auth.startswith("Bearer sk-test-key-DO-NOT-USE"), (
            f"Authorization header value did not start with the expected "
            f"'Bearer <api_key>' prefix (got {auth!r}). The handler must "
            f"construct 'Bearer <api_key>' for the openai provider."
        )
        # The original request URL MUST be the provider's endpoint, NOT
        assert captured["url"] == "https://api.openai.com/v1/models", (
            f"Original request URL was {captured['url']!r}, expected the "
            "OpenAI test endpoint. If this is the redirect target URL, "
            "the handler followed the redirect (SEC-2 bypass)."
        )

    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_each_redirect_status_is_surfaced_not_followed(self, code: int):
        """``cloud_test_result`` failure with that status, NOT silently"""
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        mock_open = MagicMock(side_effect=_redirect_http_error(code))
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        assert resp["type"] == "cloud_test_result"
        assert resp["data"]["ok"] is False
        assert resp["data"]["status"] == code, (
            f"Handler reported status {resp['data']['status']} for a {code} "
            f"redirect, expected the redirect status itself (the opener "
            f"must NOT follow any 3xx)."
        )
        # No retry / no follow for any 3xx code.
        assert mock_open.call_count == 1
