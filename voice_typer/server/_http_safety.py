"""HTTP safety helpers for user-configured endpoints."""

# Contract: import line must stay verbatim (TestLoopbackHostsIsDRY inspects source).
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
