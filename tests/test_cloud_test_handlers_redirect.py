"""Regression tests: ``cloud_test_handlers`` must NOT follow 3xx redirects.

Pre-fix, ``cloud_test_handlers._handle_test_cloud_connection`` issued the
authenticated GET via the bare ``urllib.request.urlopen(req, timeout=...)``
call. The default ``urlopen`` opener installs ``HTTPRedirectHandler``,
which silently follows 3xx responses. A malicious or compromised provider
endpoint that returned ``302 Location: https://attacker.example.com/steal``
would have caused urllib to re-issue the request — with the
``Authorization: Bearer <api_key>`` header still attached — to the
attacker-controlled target. That is an API-key exfiltration surface.

Post-fix, the handler uses the module-level secure opener
``_opener = build_secure_opener()`` (mirroring ``cloud_engines._opener``).
``build_secure_opener`` installs ``_NoRedirectHandler``, whose
``redirect_request`` override raises ``HTTPError`` on a 3xx — so the
handler's existing ``except HTTPError`` branch catches the redirect and
surfaces it as a hard ``cloud_test_result`` failure with the redirect's
status code, rather than silently following it.

These tests verify BOTH layers of the fix:

1. **Structural** — the module-level ``_opener`` is a
   ``build_secure_opener()`` instance whose handler chain contains
   ``_NoRedirectHandler`` (and the module does NOT import bare
   ``urlopen``). If a future refactor reverts to ``urlopen(req, ...)`` or
   swaps the opener for the default one, the structural test fails.

2. **Behavioral** — when the provider endpoint returns a 3xx, the
   handler surfaces a ``cloud_test_result`` envelope with the redirect's
   status code (NOT a 200 from a silently-followed redirect), and the
   opener is invoked exactly once (no retransmission to the redirect
   target). The behavioral test mocks ``_opener.open`` so NO real network
   call is made.
"""

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

# ---------------------------------------------------------------------------
# Structural: module-level opener composition
# ---------------------------------------------------------------------------


class TestSecureOpenerWiring:
    """The handler module MUST use a secure opener that does NOT follow
    redirects.

    These tests pin the *composition* of ``cloud_test_handlers._opener``
    so a regression that swaps it back to the default ``urlopen`` opener
    (or removes ``_NoRedirectHandler`` from the handler chain) is caught
    at test time, without any network call.
    """

    def test_module_opener_is_build_secure_opener_instance(self):
        """``cloud_test_handlers._opener`` MUST be an opener built by
        ``build_secure_opener()``. We verify by re-building a fresh
        secure opener and confirming the handler module's opener has the
        same handler-class set (HTTPSHandler, _HttpsOnlyHTTPHandler,
        _NoRedirectHandler). A bare ``urlopen``-style default opener
        would NOT contain ``_NoRedirectHandler``."""
        opener = cloud_test_handlers._opener
        # The secure opener must install _NoRedirectHandler — the
        # default ``urlopen`` opener does NOT.
        handler_types = {type(h) for h in opener.handlers}
        assert _NoRedirectHandler in handler_types, (
            "cloud_test_handlers._opener does NOT contain "
            "_NoRedirectHandler — the opener would silently follow 3xx "
            "redirects, retransmitting the Authorization: Bearer <api_key> "
            "header to an attacker-controlled redirect target. The module "
            "must use build_secure_opener() (mirrors cloud_engines._opener)."
        )

    def test_module_opener_matches_fresh_secure_opener_handler_set(self):
        """The handler module's opener must have the SAME handler-class
        set as a freshly-built ``build_secure_opener()`` instance. If a
        future refactor installs additional handlers (or removes one),
        the drift is surfaced."""
        module_handler_types = {type(h) for h in cloud_test_handlers._opener.handlers}
        fresh_handler_types = {type(h) for h in build_secure_opener().handlers}
        assert module_handler_types == fresh_handler_types, (
            "cloud_test_handlers._opener handler set has drifted from "
            "build_secure_opener() — the module must use the shared "
            "secure opener (single source of truth in _http_safety)."
        )

    def test_module_does_not_import_bare_urlopen(self):
        """The handler module source MUST NOT import ``urlopen`` from
        ``urllib.request``. The bare ``urlopen`` function uses the
        default opener that follows 3xx redirects. Post-fix the handler
        uses ``_opener.open(req, ...)`` exclusively.

        We inspect the module source (not the module namespace) so a
        future contributor who adds ``from urllib.request import
        urlopen`` is flagged even if the imported name is unused — the
        import itself is the regression smell.
        """
        src = inspect.getsource(cloud_test_handlers)
        # The module must use _opener.open(req, ...) — the call site.
        assert "_opener.open(" in src, (
            "cloud_test_handlers does not call _opener.open(...) — the "
            "secure opener is not wired into the request path."
        )
        # The module must NOT import the bare ``urlopen`` symbol. We
        # match the specific import-statement form rather than the
        # substring ``urlopen`` (which appears in comments / docstrings
        # legitimately) so the assertion is precise.
        assert "from urllib.request import Request, urlopen" not in src, (
            "cloud_test_handlers imports bare ``urlopen`` from "
            "urllib.request — the default opener follows 3xx redirects "
            "(SEC-2 bypass). Use _opener.open(req, ...) instead."
        )
        assert "from urllib.request import urlopen" not in src, (
            "cloud_test_handlers imports bare ``urlopen`` from "
            "urllib.request — the default opener follows 3xx redirects "
            "(SEC-2 bypass). Use _opener.open(req, ...) instead."
        )


# ---------------------------------------------------------------------------
# Behavioral: handler refuses to follow a 3xx redirect
# ---------------------------------------------------------------------------


def _make_handler_with_openai_key(api_key: str) -> cloud_test_handlers.CloudTestHandlersMixin:
    """Build a ``CloudTestHandlersMixin`` instance with a fake app whose
    ``config.openai_api_key`` returns *api_key*.

    The handler reads the API key from ``self.app.config.<field>`` (NOT
    from ``service.get_config()`` which returns sanitized sentinels), so
    a plain ``SimpleNamespace`` config is sufficient. No IPC server /
    service / send-channel is needed because the redirect path returns
    early (the catch-all ``_respond_with_error`` is never reached).
    """
    handler = cloud_test_handlers.CloudTestHandlersMixin()
    handler.app = SimpleNamespace(config=SimpleNamespace(openai_api_key=api_key))
    # ``service`` / ``_send`` are referenced by the catch-all
    # ``_respond_with_error`` path, which must NOT fire on the redirect
    # branch. A MagicMock satisfies the ``Any`` annotation and lets the
    # test assert the catch-all was NOT invoked (via call-count).
    handler.service = MagicMock()
    handler._send = MagicMock()
    return handler


def _redirect_http_error(code: int = 302, target: str = "https://attacker.example.com/steal") -> HTTPError:
    """Build the ``HTTPError`` that ``_NoRedirectHandler.redirect_request``
    raises on a 3xx response. Mirrors the real constructor call in
    ``_http_safety._NoRedirectHandler.redirect_request`` so the
    behavioral test exercises the same exception shape the production
    opener produces."""
    return HTTPError(
        url=target,
        code=code,
        msg=f"redirect refused (SEC-2): {code} Found -> {target}",
        hdrs=None,
        fp=None,
    )


class TestHandlerRefusesRedirect:
    """When the provider endpoint returns a 3xx redirect, the handler
    MUST surface it as a ``cloud_test_result`` failure with the
    redirect's status code — NOT silently follow the redirect and report
    a 200 from the redirect target.

    These tests mock ``cloud_test_handlers._opener.open`` so NO real
    network call is made. The mock raises the same ``HTTPError`` that
    ``_NoRedirectHandler`` raises in production, so the test exercises
    the handler's ``except HTTPError`` branch end-to-end.
    """

    def test_redirect_surfaces_as_cloud_test_result_with_redirect_status(self):
        """A 302 redirect from the provider MUST be surfaced as
        ``cloud_test_result`` with ``ok=False`` and ``status=302`` — the
        redirect's status code, NOT a 200 from a silently-followed
        redirect target.

        Pre-fix, ``urlopen`` would have followed the 302 to
        ``attacker.example.com`` and returned the (attacker-controlled)
        final status — typically 200 — so the handler would have
        reported ``ok=True, status=200`` and the user would believe
        their key was valid while it had just been exfiltrated.
        """
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        mock_open = MagicMock(side_effect=_redirect_http_error(302))
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            result = handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        assert result is resp
        assert resp["type"] == "cloud_test_result"
        assert resp["data"]["ok"] is False
        # The redirect's status code (302) MUST be surfaced — NOT a 200
        # from a silently-followed redirect target.
        assert resp["data"]["status"] == 302, (
            "Handler reported a status other than 302 for a 302 redirect — "
            "this means the opener silently followed the redirect (SEC-2 "
            "bypass) and reported the final status (typically 200)."
        )

    def test_handler_does_not_retry_or_follow_after_redirect(self):
        """When the opener raises ``HTTPError(302)``, the handler MUST
        invoke ``_opener.open`` exactly ONCE — it must NOT retry the
        request against the redirect target (which would retransmit the
        ``Authorization`` header).

        Pre-fix, ``urlopen`` followed the redirect internally (so the
        handler saw a single ``open`` call but the underlying urllib
        had made a SECOND request to the redirect target with the
        Authorization header attached). Post-fix, ``_opener.open`` raises
        on the 3xx, so the handler sees a single call and the redirect
        is NOT followed at all.
        """
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        mock_open = MagicMock(side_effect=_redirect_http_error(301))
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        assert mock_open.call_count == 1, (
            f"Handler invoked _opener.open {mock_open.call_count} times — "
            "expected exactly 1 (the redirect must NOT be followed by a "
            "second request to the redirect target, which would "
            "retransmit the Authorization: Bearer <api_key> header)."
        )

    def test_authorization_header_was_on_original_request(self):
        """The ``Authorization: Bearer <api_key>`` header MUST be
        present on the original request passed to ``_opener.open``.
        This pins the contract that the API key is constructed in
        Python (not in the renderer) AND that — because the redirect is
        refused — the header is NOT retransmitted to the redirect
        target.

        We assert the header value on the ``Request`` object the handler
        built; combined with the previous test (``open`` called exactly
        once), this proves the key was on the original request and the
        redirect target never received it.
        """
        handler = _make_handler_with_openai_key("sk-test-key-DO-NOT-USE")
        resp: dict = {"type": "", "data": {}}

        captured: dict = {}

        def capture_and_raise(req: Request, timeout: float | None = None):
            # Snapshot the Request's headers BEFORE raising, so the
            # assertion runs against the actual request the handler
            # built (not a mock-arg copy that might not preserve the
            # header dict).
            captured["headers"] = dict(req.header_items())
            captured["url"] = req.full_url
            raise _redirect_http_error(302)

        mock_open = MagicMock(side_effect=capture_and_raise)
        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = mock_open
            handler._handle_test_cloud_connection({"provider": "openai"}, resp)

        # The original request MUST carry the Authorization header.
        # ``Request.header_items()`` lowercases header names.
        auth = captured["headers"].get("Authorization")
        assert auth is not None, (
            "The Authorization header was NOT on the original request "
            "passed to _opener.open — the API key must be attached in "
            "Python (the renderer never sees the key)."
        )
        assert auth.startswith("Bearer sk-test-key-DO-NOT-USE"), (
            f"Authorization header value did not start with the expected "
            f"'Bearer <api_key>' prefix (got {auth!r}). The handler must "
            f"construct 'Bearer <api_key>' for the openai provider."
        )
        # The original request URL MUST be the provider's endpoint, NOT
        # the redirect target — proving the redirect was not followed.
        assert captured["url"] == "https://api.openai.com/v1/models", (
            f"Original request URL was {captured['url']!r} — expected the "
            "OpenAI test endpoint. If this is the redirect target URL, "
            "the handler followed the redirect (SEC-2 bypass)."
        )

    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_each_redirect_status_is_surfaced_not_followed(self, code: int):
        """Every 3xx status code MUST be surfaced as a
        ``cloud_test_result`` failure with that status — NOT silently
        followed. ``_NoRedirectHandler`` raises ``HTTPError`` for all
        3xx codes (its ``redirect_request`` override is called by
        urllib for any 3xx, regardless of the specific code)."""
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
            f"redirect — expected the redirect status itself (the opener "
            f"must NOT follow any 3xx)."
        )
        # No retry / no follow for any 3xx code.
        assert mock_open.call_count == 1
