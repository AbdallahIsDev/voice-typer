"""Backward-compat shim — code moved to ``voice_typer.server.security`` (EO-23).

The no-redirect / HTTPS-only urllib opener helpers moved to
:mod:`voice_typer.server.security.http_safety`. This module re-exports
them so existing import sites (``cloud_engines.py``, ``llm_polish.py``,
``handlers/cloud_test_handlers.py``, tests) keep working unchanged.

The ``LOOPBACK_HOSTS`` import from ``voice_typer.server._paths`` is
retained as a source-text contract: ``tests/test_http_safety.py``
(``TestLoopbackHostsIsDRY.test_loopback_hosts_imported_from_paths``)
inspects this module's source to ensure the loopback set is never
re-declared inline.

New code should import from ``voice_typer.server.security.http_safety``
directly.
"""

# Source-text contract: the LOOPBACK_HOSTS import line must stay
# verbatim — ``TestLoopbackHostsIsDRY`` inspects this module's source.
from voice_typer.server._paths import LOOPBACK_HOSTS  # noqa: F401
from voice_typer.server.security.http_safety import (  # noqa: F401
    _HttpsOnlyHTTPHandler,
    _NoRedirectHandler,
    build_secure_opener,
)

__all__ = [
    "build_secure_opener",
    "_HttpsOnlyHTTPHandler",
    "_NoRedirectHandler",
]
