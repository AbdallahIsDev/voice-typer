"""Shared HTTP safety helpers: no-redirect handler, secure opener builder.

extracted from ``cloud_engines.py`` and ``llm_polish.py`` to
eliminate the DRY duplication of ``_NoRedirectHandler`` ( finding
#1: previously the class was duplicated verbatim across both modules).

Both cloud transcription (``cloud_engines._opener``) and LLM polish
(``llm_polish._opener``) need an ``OpenerDirector`` that does NOT
follow HTTP 3xx redirects.  Centralizing the handler + builder here
ensures any future fix to the redirect-refusal logic only has to land
in one place.

Security rationale
-------------------
``urllib.request.build_opener`` ALWAYS installs the default
``HTTPRedirectHandler`` (which silently follows 3xx responses) UNLESS
the caller passes an explicit ``HTTPRedirectHandler`` subclass.  The
previous code passed only ``HTTPSHandler()``, expecting
``build_opener`` to skip the redirect handler — but urllib adds the
default handlers in addition to the caller-provided ones (a handler of
the same *class* replaces the default; ``HTTPSHandler`` replaces
``HTTPSHandler`` but does NOT replace ``HTTPRedirectHandler``).  So
the opener was silently following 3xx redirects despite the SECURITY
comment claiming otherwise.

``_NoRedirectHandler`` overrides ``redirect_request`` to raise
``HTTPError`` so the existing ``except HTTPError`` / ``except
URLError`` branches in the cloud engines handle it as a hard failure
(no silent exfiltration of the request body — which contains user
audio + the API key in the Authorization header — to an attacker-
controlled redirect target).
"""

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
    """refuse to follow HTTP redirects.

    ``urllib.request.build_opener`` ALWAYS installs the default
    ``HTTPRedirectHandler`` (which silently follows 3xx responses)
    UNLESS the caller passes an explicit ``HTTPRedirectHandler``
    subclass. The previous code passed only ``HTTPSHandler()``,
    expecting ``build_opener`` to skip the redirect handler — but
    the urllib source adds the default handlers in addition to the
    caller-provided ones (a handler of the same *class* replaces the
    default; HTTPSHandler replaces HTTPSHandler but does NOT replace
    HTTPRedirectHandler). So the opener was silently following 3xx
    redirects despite the SECURITY comment claiming otherwise.

    This subclass overrides ``redirect_request`` to raise
    ``HTTPError`` so the existing ``except HTTPError`` / ``except
    URLError`` branches in the cloud engines handle it as a hard
    failure (no silent exfiltration of the request body — which
    contains user audio + the API key in the Authorization header —
    to an attacker-controlled redirect target).

    See https://docs.python.org/3/library/urllib.request.html#urllib.request.HTTPRedirectHandler
    for the contract.
    """

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
        # catches it. The error message includes the redirect target
        # (newurl) so the user / operator can diagnose a misconfigured
        # endpoint, but ``redact_url`` is applied by the caller before
        # logging to avoid leaking credentials in the URL.
        #
        # also pass ``url=redact_url(newurl)`` to the HTTPError
        # constructor. The ``url`` attribute is exposed as ``e.url`` to
        # callers and is commonly logged directly (e.g. ``except
        # HTTPError as e: log.warning('failed: %s', e.url)``). Pre-fix,
        # the message was redacted but ``e.url`` preserved the raw
        # redirect target — including any embedded
        # ``user:pass@host`` userinfo — which is a credential-leak
        # surface. Redacting both fields keeps the two representations
        # consistent.
        raise HTTPError(
            url=redact_url(newurl),
            code=code,
            msg=f"redirect refused (SEC-2): {code} {msg} -> {redact_url(newurl)}",
            hdrs=headers,
            fp=fp,
        )


class _HttpsOnlyHTTPHandler(HTTPHandler):
    """SEC: refuse plaintext HTTP requests.

    ``urllib.request.build_opener`` ALWAYS installs the default
    handler set in addition to caller-provided handlers — this
    includes ``HTTPHandler`` (plaintext HTTP). The function name and
    docstring of :func:`build_secure_opener` imply HTTPS-only, but
    nothing previously prevented a caller from passing an ``http://``
    URL, which would be transmitted in plaintext (exposing the API
    key + audio body).

    This subclass overrides :meth:`HTTPHandler.http_open` to raise a
    hard ``URLError`` for any plaintext HTTP request to a NON-loopback
    host, so the opener genuinely cannot be used to send data over an
    unencrypted channel to the public internet. Loopback hosts
    (``localhost``, ``127.0.0.1``, ``::1``) are exempted because local
    development servers (Ollama, vLLM, LM Studio, etc.) commonly
    serve plaintext HTTP on loopback.

    The same class is reused to build a default ``HTTPHandler``
    replacement for :func:`build_secure_opener` (passing the class
    itself, not an instance, so ``build_opener`` recognizes it as a
    handler-class and replaces the default ``HTTPHandler`` instead of
    adding a second one in parallel).
    """

    # the loopback exemption set is imported from the canonical
    # source of truth (``voice_typer.server._paths.LOOPBACK_HOSTS``)
    # rather than re-declared inline. Pre-fix, this attribute was a
    # separate inline frozenset literal of the three loopback hosts
    # — a DRY violation: if the canonical set ever changes, two files
    # would need to be edited in sync, and drift would silently either
    # over-block (breaking local dev servers like Ollama / vLLM) or
    # under-block (SSRF surface). The class-attribute name
    # ``_LOOPBACK_HOSTS`` is preserved for backward compatibility with
    # ``tests/test_http_safety.py::test_loopback_set_is_documented``
    # and ``tests/test_http_safety_ssrf.py`` (both inspect
    # ``_HttpsOnlyHTTPHandler._LOOPBACK_HOSTS`` directly).
    _LOOPBACK_HOSTS = LOOPBACK_HOSTS

    def http_open(self, req: Request) -> http.client.HTTPResponse:
        # the return type now matches the parent
        # ``HTTPHandler.http_open`` signature exactly (per typeshed:
        # ``http.client.HTTPResponse``). Previously this override was
        # annotated ``-> object`` with a ``# type: ignore[override]``
        # suppression marker because ``object`` is a *supertype* of
        # ``HTTPResponse`` and return-type covariance forbids widening
        # the return type in a subclass override. The body either
        # delegates to ``super().http_open(req)`` (returning an
        # ``HTTPResponse`` for loopback hosts) or raises ``URLError``
        # for non-loopback hosts (no value escapes the function), so
        # ``http.client.HTTPResponse`` is the precise return type and
        # no suppression is required.
        # Determine the host from the request URL. ``req.full_url`` is
        # the full URL the caller passed to ``opener.open(url, ...)``.
        try:
            parsed = urlparse(req.full_url)
        except (ValueError, TypeError):
            parsed = None
        host = (parsed.hostname or "").lower() if parsed else ""
        if host in self._LOOPBACK_HOSTS:
            # Local development server — allow plaintext HTTP.
            return super().http_open(req)
        # Non-loopback plaintext HTTP request — refuse.
        raise URLError(
            "SEC: plaintext HTTP refused for non-loopback host "
            f"{host!r}; use https:// (DE-65). The 'secure opener' is "
            "HTTPS-only by default. To allow a local plaintext "
            "endpoint, host it on localhost / 127.0.0.1 / ::1."
        )


def build_secure_opener():
    """Build a urllib opener that does NOT follow HTTP redirects and
    refuses plaintext HTTP for non-loopback hosts.

    pass ``_NoRedirectHandler()`` so the opener does NOT follow
    3xx redirects (the default ``HTTPRedirectHandler`` would silently
    POST the request body — user audio + API key — to an attacker-
    controlled redirect target).

    also install :class:`_HttpsOnlyHTTPHandler` so the opener
    refuses plaintext HTTP requests to non-loopback hosts. Pre-fix,
    ``build_opener(HTTPSHandler(), _NoRedirectHandler())`` left the
    default ``HTTPHandler`` installed — so a caller passing
    ``http://attacker.example.com/steal`` would have its request body
    (API key + user audio) transmitted in plaintext. Passing
    ``_HttpsOnlyHTTPHandler`` as a *class* (not an instance) makes
    ``build_opener`` replace the default ``HTTPHandler`` instead of
    adding a parallel one, so the override actually takes effect.

    PERF- the returned ``OpenerDirector`` reuses TCP
    connections across requests (like ``requests.Session``).  Callers
    should stash the returned opener at module level and reuse it
    rather than calling this function per request.

    Returns
    -------
    OpenerDirector
        A reusable opener configured with ``HTTPSHandler`` (for TLS),
        ``_HttpsOnlyHTTPHandler`` (to refuse plaintext HTTP for
        non-loopback hosts), and ``_NoRedirectHandler`` (to refuse
        redirects).

    Notes
    -----
    Caller responsibility: even with this enforcement, callers that
    accept user-supplied URLs should still    validate the scheme + host
    via :func:`voice_typer.server.security.assert_url_allowed` BEFORE
    constructing the request. The handler-level enforcement is a
    defense-in-depth backstop, not a replacement for input validation.
    """
    # Pass ``_HttpsOnlyHTTPHandler`` (the class, not an instance) so
    # ``build_opener`` treats it as a handler-class and REPLACES the
    # default ``HTTPHandler``. Passing an instance would cause
    # ``build_opener`` to ADD a second ``HTTPHandler``-shaped handler
    # alongside the default — and the default would still handle
    # ``http://`` requests in plaintext, defeating the override.
    return build_opener(
        HTTPSHandler(),
        _HttpsOnlyHTTPHandler,
        _NoRedirectHandler(),
    )
