"""Shared HTTP safety helpers: no-redirect handler, secure opener builder."""

from __future__ import annotations

import http.client
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

from voice_typer.server._paths import LOOPBACK_HOSTS
from voice_typer.server.security.redaction import redact_url

_log = logging.getLogger(__name__)


class _NoRedirectHandler(HTTPRedirectHandler):
    """refuse to follow HTTP redirects."""

    def redirect_request(
        self,
        req: Request,
        fp,
        code: int,
        msg: str,
        headers,
        newurl: str,
    ) -> Request | None:
        # Raise HTTPError so the caller's ``except HTTPError`` branch
        raise HTTPError(
            url=redact_url(newurl),
            code=code,
            msg=f"redirect refused (SEC-2): {code} {msg} -> {redact_url(newurl)}",
            hdrs=headers,
            fp=fp,
        )


class _HttpsOnlyHTTPHandler(HTTPHandler):
    """SEC: refuse plaintext HTTP requests."""

    # the loopback exemption set is imported from the canonical
    _LOOPBACK_HOSTS = LOOPBACK_HOSTS

    def http_open(self, req: Request) -> http.client.HTTPResponse:
        # the return type now matches the parent
        try:
            parsed = urlparse(req.full_url)
        except (ValueError, TypeError):
            parsed = None
        host = (parsed.hostname or "").lower() if parsed else ""
        if host in self._LOOPBACK_HOSTS:
            # Local development server, allow plaintext HTTP.
            return super().http_open(req)
        # Non-loopback plaintext HTTP request, refuse.
        raise URLError(
            "SEC: plaintext HTTP refused for non-loopback host "
            f"{host!r}; use https:// (DE-65). The 'secure opener' is "
            "HTTPS-only by default. To allow a local plaintext "
            "endpoint, host it on localhost / 127.0.0.1 / ::1."
        )


def build_secure_opener():
    """Build a urllib opener that does NOT follow HTTP redirects and

    Returns
    """
    # Pass ``_HttpsOnlyHTTPHandler`` (the class, not an instance) so
    return build_opener(
        HTTPSHandler(),
        _HttpsOnlyHTTPHandler,
        _NoRedirectHandler(),
    )
