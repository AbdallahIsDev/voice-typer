"""Backward-compat shim — code moved to ``voice_typer.server.security`` (EO-23).

The secret/PII redaction helpers moved to
:mod:`voice_typer.server.security.redaction` and the cloud URL allowlist
+ SSRF defense moved to :mod:`voice_typer.server.security.url_allowlist`.
This module re-exports every public AND private name the codebase and
tests reference so existing import sites keep working unchanged.

New code should import from the package directly:
``voice_typer.server.security.redaction`` /
``voice_typer.server.security.url_allowlist``.
"""

from voice_typer.server._paths import LOOPBACK_HOSTS as _LOOPBACK_HOSTS  # noqa: F401
from voice_typer.server.security.redaction import (  # noqa: F401
    _BARE_KEY_VALUE_PATTERN,
    _FLAG_KEY_PATTERNS,
    _FLAG_VALUE_PATTERN,
    _KEY_PATTERNS,
    _KEYWORD_ALT,
    _MIN_REDACT_LEN,
    _PUBLIC_ENV_VAR_NAMES,
    _SECRET_KEYWORDS,
    _flag_sub,
    _redact_home_path,
    _resolve_home_dirs,
    redact_api_keys,
    redact_for_export,
    redact_secret,
    redact_url,
)
from voice_typer.server.security.url_allowlist import (  # noqa: F401
    _DEFAULT_ALLOWED_HOSTS,
    _ENV_TRUSTED_HOSTS_VAR,
    _is_ip_literal,
    _is_private_ip,
    _load_env_allowlist_extensions,
    _normalize_host,
    _user_extensions,
    assert_url_allowed,
    extend_url_allowlist,
    get_url_allowlist,
    is_url_allowed,
)

__all__ = [
    "redact_secret",
    "redact_api_keys",
    "redact_url",
    "redact_for_export",
    "extend_url_allowlist",
    "get_url_allowlist",
    "is_url_allowed",
    "assert_url_allowed",
]
