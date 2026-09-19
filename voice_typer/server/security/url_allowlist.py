"""Cloud URL allowlist + SSRF defense (extracted from the former ``_secrets.py``)."""

from __future__ import annotations

import inspect
import ipaddress
import logging
import os
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

from voice_typer.server._paths import LOOPBACK_HOSTS as _LOOPBACK_HOSTS

log = logging.getLogger(__name__)


# Default allowlist of trusted cloud ASR / LLM provider hostnames.

_DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        # OpenAI
        "api.openai.com",
        # Groq
        "api.groq.com",
        # Deepgram
        "api.deepgram.com",
        # Anthropic (Claude), common LLM polish target
        "api.anthropic.com",
        # Google Gemini / Vertex
        "generativelanguage.googleapis.com",
        # Local self-hosted endpoints, explicitly allowed for development
        "localhost",
        "127.0.0.1",
        "::1",
    }
)

_user_extensions: set[str] = set()

# Environment variable name used to extend the URL allowlist at process
_ENV_TRUSTED_HOSTS_VAR = "VOICE_TYPER_TRUSTED_HOSTS"


# ``extend_url_allowlist`` was originally audited with ZERO
def extend_url_allowlist(
    hosts: Iterable[str],
    *,
    caller: str | None = None,
) -> None:
    """Add hostnames to the runtime URL allowlist."""
    # capture the caller for audit logging. Auto-detect via
    if caller is None:
        try:
            frame = inspect.stack()[1]
            mod = frame.frame.f_globals.get("__name__", "<unknown>")
            func = frame.function or "<unknown>"
            lineno = frame.lineno
            caller = f"{mod}.{func}:L{lineno}"
        except Exception as exc:  # noqa: BLE001, inspect failures must not break the call
            caller = f"<inspect-failed: {exc}>"

    # Normalize the input hosts (lowercase, strip port, drop empties)
    normalized: list[str] = []
    for h in hosts:
        if not h:
            continue
        host = _normalize_host(h)
        if host:
            normalized.append(host)

    # Routine allowlist extensions (GitHub pack hosts on every boot,
    if normalized:
        log.info(
            "[URL-Allowlist] extended by %s with hosts: %s",
            caller,
            normalized,
        )
    else:
        log.info(
            "[URL-Allowlist] no-op extend call by %s (no new hosts)",
            caller,
        )

    for host in normalized:
        _user_extensions.add(host)


def get_url_allowlist() -> frozenset[str]:
    """Return the current effective allowlist (defaults + user extensions)."""
    return _DEFAULT_ALLOWED_HOSTS | _user_extensions


def _normalize_host(h: str) -> str:
    """Normalize a hostname: lowercase, strip port, strip whitespace."""
    if not h:
        return ""
    host = h.strip()
    # Bracketed IPv6 with an optional port: ``[fc00::1]`` or
    if host.startswith("["):
        closing = host.find("]")
        if closing > 0:
            inner = host[1:closing]
            try:
                ipaddress.ip_address(inner)
            except ValueError:
                pass  # not an IPv6 literal, fall through to the generic path
            else:
                return inner.lower()
    # Bare IPv6 literal (``fc00::1``), no port, no brackets.
    if host.count(":") > 1:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Multi-colon string that is NOT a valid IPv6 literal (e.g.
            return ""
        return host.lower()
    # Generic hostname / IPv4: lowercase, strip the first ``:port``.
    return host.split(":")[0].strip().lower()


def _load_env_allowlist_extensions() -> list[str]:
    """Extend the URL allowlist from the ``VOICE_TYPER_TRUSTED_HOSTS`` env var."""
    raw = os.environ.get(_ENV_TRUSTED_HOSTS_VAR, "")
    if not raw or not raw.strip():
        return []
    raw_hosts = [h.strip() for h in raw.split(",")]
    normalized = [_normalize_host(h) for h in raw_hosts]
    hosts = [h for h in normalized if h]
    if not hosts:
        return []
    extend_url_allowlist(hosts, caller=f"env:{_ENV_TRUSTED_HOSTS_VAR}")
    return hosts


# SSRF defense. IP-literal blocklist + best-effort DNS rebinding check ──


def _is_ip_literal(host: str) -> bool:
    """Return True if ``host`` is an IP literal (IPv4 or IPv6)."""
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private_ip(ip_str: str) -> bool:
    """Return True if ``ip_str`` is a private/reserved IP address."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False  # not an IP literal, caller should resolve first
    # ``is_private`` for IPv4 includes RFC 1918 + 127/8 + 169.254/16 +
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved)


def is_url_allowed(url: str) -> bool:
    """Return True if the URL's host is in the allowlist."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host in get_url_allowlist()


def assert_url_allowed(
    url: str,
    *,
    field_name: str = "url",
    client_name: str = "client",
    require_https: bool = True,
    allow_loopback_http: bool = False,
    check_dns_rebinding: bool = True,
) -> None:
    """Raise ``ValueError`` if ``url`` is not in the allowlist."""
    if not url:
        raise ValueError(f"{client_name}: {field_name} is empty")

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{client_name}: {field_name} is not a valid URL: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{client_name}: {field_name} must use http or https scheme (got {parsed.scheme!r})")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"{client_name}: {field_name} has no hostname")
    if host not in get_url_allowlist():
        raise ValueError(
            f"{client_name}: {field_name} host {host!r} is not in the "
            f"trusted allowlist.  Call extend_url_allowlist() to add it."
        )
    # enforce HTTPS for non-loopback hosts to prevent
    is_loopback = host in _LOOPBACK_HOSTS  # was per-call frozenset literal
    if require_https and parsed.scheme == "http" and (not is_loopback or not allow_loopback_http):
        if is_loopback:
            # loopback HTTP rejected because caller didn't opt in
            raise ValueError(
                f"{client_name}: {field_name} must use HTTPS for loopback "
                f"host {host!r} (HTTP requires explicit opt-in via "
                f"allow_loopback_http=True, local development servers "
                f"should be the only consumers of cleartext loopback)."
            )
        raise ValueError(
            f"{client_name}: {field_name} must use HTTPS for non-loopback "
            f"host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1 "
            f"for local development). Cleartext transmission of API keys "
            f"and transcribed text over the public internet is not permitted."
        )

    # SSRF defense, after the allowlist + HTTPS checks pass,
    if is_loopback:
        # Loopback IPs (127.0.0.1, ::1) are explicitly allowlisted for
        return
    if _is_ip_literal(host):
        # IP-literal blocklist (the minimum  fix).  Even if the
        if _is_private_ip(host):
            raise ValueError(
                f"{client_name}: {field_name} host {host!r} is a "
                f"private/reserved IP literal, refusing to prevent "
                f"SSRF. Even if explicitly allowlisted, "
                f"private/reserved IP literals are rejected to "
                f"prevent exfiltration of API keys to internal "
                f"endpoints (e.g. cloud metadata 169.254.169.254)."
            )
    elif check_dns_rebinding:
        # Best-effort post-resolution check (catches DNS rebinding,
        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            infos = []
        for _family, _type, _proto, _canonname, sockaddr in infos:
            # sockaddr[0] is the IP address string for both AF_INET
            ip = sockaddr[0]
            if _is_private_ip(ip):
                raise ValueError(
                    f"{client_name}: {field_name} host {host!r} resolves "
                    f"to private/reserved IP {ip!r}, refusing to "
                    f"prevent SSRF (DNS rebinding defense). If "
                    f"this is a legitimate local endpoint, use the IP "
                    f"literal directly (e.g. http://127.0.0.1:port) "
                    f"which is allowlisted for local development."
                )


# Module-load bootstrap: extend the URL allowlist from the
_load_env_allowlist_extensions()
