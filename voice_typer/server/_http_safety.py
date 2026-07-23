"""Shared HTTP safety helpers: no-redirect handler, secure opener builder.

EC-FIX-8: extracted from ``cloud_engines.py`` and ``llm_polish.py`` to
eliminate the DRY duplication of ``_NoRedirectHandler`` (EC-17 finding
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

from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener

from voice_typer.server._secrets import redact_url


class _NoRedirectHandler(HTTPRedirectHandler):
    """SEC-2: refuse to follow HTTP redirects.

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

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        # Raise HTTPError so the caller's ``except HTTPError`` branch
        # catches it. The error message includes the redirect target
        # (newurl) so the user / operator can diagnose a misconfigured
        # endpoint, but ``redact_url`` is applied by the caller before
        # logging to avoid leaking credentials in the URL.
        raise HTTPError(
            url=newurl,
            code=code,
            msg=f"redirect refused (SEC-2): {code} {msg} -> {redact_url(newurl)}",
            hdrs=headers,
            fp=fp,
        )


def build_secure_opener():
    """Build a urllib opener that does NOT follow HTTP redirects.

    SEC-2: pass ``_NoRedirectHandler()`` so the opener does NOT follow
    3xx redirects (the default ``HTTPRedirectHandler`` would silently
    POST the request body — user audio + API key — to an attacker-
    controlled redirect target).

    PERF-NEW-010: the returned ``OpenerDirector`` reuses TCP
    connections across requests (like ``requests.Session``).  Callers
    should stash the returned opener at module level and reuse it
    rather than calling this function per request.

    Returns
    -------
    OpenerDirector
        A reusable opener configured with ``HTTPSHandler`` (for TLS)
        and ``_NoRedirectHandler`` (to refuse redirects).
    """
    return build_opener(HTTPSHandler(), _NoRedirectHandler())
