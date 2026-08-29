"""Cloud URL allowlist + SSRF defense (extracted from the former ``_secrets.py``).

Part of the :mod:`voice_typer.server.security` package
(consolidation). Hosts the URL-allowlist half of the former
``voice_typer.server._secrets`` module: default trusted hosts, runtime
user extensions (``extend_url_allowlist``), the env-var bootstrap
(``VOICE_TYPER_TRUSTED_HOSTS``), and the SSRF defense checks
(IP-literal blocklist + best-effort DNS-rebinding).

The secret/PII redaction helpers that previously shared the former
``_secrets.py`` now live in :mod:`voice_typer.server.security.redaction`.
"""

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


# ── Cloud URL allowlist ───────────────────────────────────────────────────
#
# Default allowlist of trusted cloud ASR / LLM provider hostnames.
# When a user sets a custom ``cloud_api_url`` or ``llm_api_url``, the
# HTTP client asserts the URL's hostname is in this allowlist (or in
# an explicit user-extended allowlist) before sending any data.
#
# To extend the allowlist at runtime (e.g. for a self-hosted vLLM
# endpoint), call ``extend_url_allowlist(["my-host.example.com"])``.
# Extensions are process-global and apply to all HTTP clients.

_DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        # OpenAI
        "api.openai.com",
        # Groq
        "api.groq.com",
        # Deepgram
        "api.deepgram.com",
        # Anthropic (Claude) — common LLM polish target
        "api.anthropic.com",
        # Google Gemini / Vertex
        "generativelanguage.googleapis.com",
        # Local self-hosted endpoints — explicitly allowed for development
        "localhost",
        "127.0.0.1",
        "::1",
    }
)

_user_extensions: set[str] = set()

# Environment variable name used to extend the URL allowlist at process
# startup. Comma-separated hostnames (whitespace tolerated). This is the
# production config path for self-hosted cloud endpoints on non-loopback
# hosts (e.g. ``my-vllm.lan``); see ``_load_env_allowlist_extensions``.
_ENV_TRUSTED_HOSTS_VAR = "VOICE_TYPER_TRUSTED_HOSTS"


# ``extend_url_allowlist`` was originally audited with ZERO
# production call sites and carried a dead-code notice. Runtime wiring
# has since landed, giving it three production caller families:
#
#   1. ``_load_env_allowlist_extensions`` (in this module) — process
#      startup bootstrap from the ``VOICE_TYPER_TRUSTED_HOSTS`` env var.
#   2. ``Config.load`` (``voice_typer/server/config/__init__.py``) —
#      re-applies the persisted ``trusted_extra_hosts`` list on launch.
#   3. ``ConfigHandlersMixin`` (``voice_typer/server/handlers/
#      config_handlers.py``) — the ``add_trusted_endpoint`` IPC command
#      (runtime extension + persistence) and the ``set_config``
#      ``trusted_extra_hosts`` path.
#
# All three paths normalize through the same audit-logging +
# caller-detection logic below, so every runtime expansion of the
# trusted-host set remains traceable in logs.
def extend_url_allowlist(
    hosts: Iterable[str],
    *,
    caller: str | None = None,
) -> None:
    """Add hostnames to the runtime URL allowlist.

    Production paths: env-var bootstrap
    (``_load_env_allowlist_extensions``), ``Config.load`` re-apply of
    ``trusted_extra_hosts``, and the ``add_trusted_endpoint`` /
    ``set_config`` IPC handlers (see the module-level comment above).

    Hostnames are normalized to lowercase and stripped of port.
    Duplicate additions are idempotent.

    Parameters
    ----------
    hosts : Iterable[str]
        Hostnames (with or without port) to add to the allowlist.
    caller : str, optional
        Identifier of the caller adding the hosts (e.g. ``"env_validation"``,
        ``"cloud_engines"``, ``"config.load"``). When ``None`` (default),
        the caller is auto-detected via :func:`inspect.stack` — the
        caller's module name + function name + line number. Used in the
        WARNING-level audit log so operators can trace every allowlist
        extension back to its origin.

    Every call emits a ``WARNING``-level audit log of the form
    ``[URL-Allowlist] extended by <caller> with hosts: <hosts>``. This
    surfaces every runtime expansion of the trusted-host set in normal
    logs, so a malicious or buggy config file that adds an
    attacker-controlled host is visible without grepping for the
    specific ``extend_url_allowlist`` call site.
    """
    # capture the caller for audit logging. Auto-detect via
    # inspect.stack() when the caller didn't pass an explicit identifier.
    # The frame of interest is the caller of ``extend_url_allowlist`` —
    # i.e. ``stack()[1]`` (frame 0 is this function itself).
    if caller is None:
        try:
            frame = inspect.stack()[1]
            mod = frame.frame.f_globals.get("__name__", "<unknown>")
            func = frame.function or "<unknown>"
            lineno = frame.lineno
            caller = f"{mod}.{func}:L{lineno}"
        except Exception as exc:  # noqa: BLE001 — inspect failures must not break the call
            caller = f"<inspect-failed: {exc}>"

    # Normalize the input hosts (lowercase, strip port, drop empties)
    # so the audit log shows exactly what was added — not the raw input.
    # IPv6 literals (e.g. ``fc00::1`` or ``[fc00::1]:8080``) survive
    # port-stripping intact via ``_normalize_host`` (see HU-35 follow-up).
    normalized: list[str] = []
    for h in hosts:
        if not h:
            continue
        host = _normalize_host(h)
        if host:
            normalized.append(host)

    # calibrate the audit log level. WARNING is reserved for the
    # security-relevant case (actual hosts being added). When the call is
    # a no-op (empty iterable, or every host filtered out), demote to INFO
    # — operators still get an audit trail but no longer see WARNING spam
    # for every empty extend call.
    if normalized:
        log.warning(
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
    """Normalize a hostname: lowercase, strip port, strip whitespace.

    IPv6-aware port stripping: a bare IPv6 literal (``fc00::1``) or a
    bracketed form (``[fc00::1]:8080``) is kept INTACT — the old
    ``h.split(":")[0]`` split on the first colon, mangling IPv6
    literals to their first hextet (``fc00::1`` → ``fc00``) so they
    could never be allowlisted.

    Returns the empty string if the input is empty/whitespace-only.
    Mirrors the normalization used by ``extend_url_allowlist`` (which
    delegates here).
    """
    if not h:
        return ""
    host = h.strip()
    # Bracketed IPv6 with an optional port: ``[fc00::1]`` or
    # ``[fc00::1]:8080`` → ``fc00::1``.
    if host.startswith("["):
        closing = host.find("]")
        if closing > 0:
            inner = host[1:closing]
            try:
                ipaddress.ip_address(inner)
            except ValueError:
                pass  # not an IPv6 literal — fall through to the generic path
            else:
                return inner.lower()
    # Bare IPv6 literal (``fc00::1``) — no port, no brackets.
    if host.count(":") > 1:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Multi-colon string that is NOT a valid IPv6 literal (e.g.
            # ``fc00::1:8080`` — an un-bracketed IPv6-with-port, or
            # ``bad:host:name``). Returning ``""`` makes
            # ``extend_url_allowlist`` DROP the entry, matching the
            # reject semantics of ``_validate_trusted_extra_hosts`` and
            # the ``add_trusted_endpoint`` handler. Pre-fix, this fell
            # through to ``split(":")[0]`` and silently allowlisted a
            # mangled first hextet (``fc00``) via the
            # ``VOICE_TYPER_TRUSTED_HOSTS`` env-var path — identical
            # input was accepted by one path and rejected by another.
            # The bracketed form ``[fc00::1]:8080`` is the documented
            # way to attach a port.
            return ""
        return host.lower()
    # Generic hostname / IPv4: lowercase, strip the first ``:port``.
    return host.split(":")[0].strip().lower()


def _load_env_allowlist_extensions() -> list[str]:
    """Extend the URL allowlist from the ``VOICE_TYPER_TRUSTED_HOSTS`` env var.

    This is the production wiring that lets users self-host LLM/ASR
    endpoints on non-loopback hosts (e.g. ``https://my-vllm.lan/v1``)
    without hitting ``ValueError`` from :func:`assert_url_allowed`.

    The env var is a comma-separated list of hostnames; whitespace
    and empty entries are tolerated. Each hostname is normalized
    (lowercase, port stripped) before being added via
    :func:`extend_url_allowlist`.

    Hosts added here are STILL subject to the SSRF IP-literal blocklist
    (:func:`_is_private_ip`) — a user cannot bypass SSRF defense by
    adding a private IP via env var. The DNS-rebinding check in
    :func:`assert_url_allowed` is also unaffected.

    Safe to call multiple times: re-calling with the same env var
    value is idempotent (``extend_url_allowlist`` deduplicates via
    the ``_user_extensions`` set).

    Returns
    -------
    list[str]
        The normalized hostnames that were added (may be empty if the
        env var is unset or contains only whitespace).
    """
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


# SSRF defense — IP-literal blocklist + best-effort DNS rebinding check ──
#
# The hostname allowlist above only checks the textual hostname.  If a
# trusted hostname (e.g. ``api.openai.com``) is made to resolve to a
# private/reserved IP — via ``/etc/hosts`` tampering, compromised DNS,
# DNS rebinding, or a malicious local DNS resolver — the request is sent
# to the private IP, exfiltrating the API key (in the Authorization
# header) and the request body to the cloud metadata endpoint
# (169.254.169.254) or any internal service.
#
# The two helpers below close that gap:
#
#   * ``_is_ip_literal(host)`` — True if the host string is already an
#     IP literal (e.g. ``"10.0.0.1"``, ``"::1"``).  Used to decide
#     between the IP-literal blocklist path and the DNS-rebinding path.
#
#   * ``_is_private_ip(ip_str)`` — True if the IP is in a
#     private/reserved range.  Covers RFC 1918 (10/8, 172.16/12,
#     192.168/16), link-local (169.254/16, including the cloud metadata
#     endpoint 169.254.169.254), loopback (127/8, ::1), unspecified
#     (0.0.0.0, ::), IPv6 ULA (fc00::/7), IPv6 link-local (fe80::/10),
#     and the various ``ipaddress`` ``is_reserved`` ranges.


def _is_ip_literal(host: str) -> bool:
    """Return True if ``host`` is an IP literal (IPv4 or IPv6).

    used by :func:`assert_url_allowed` to decide between the
        IP-literal blocklist path (host is already an IP) and the DNS-
        rebinding path (host is a hostname that needs resolution).

        ``urlparse().hostname`` strips brackets from IPv6 literals (e.g.
        ``"[::1]"`` → ``"::1"``), so the caller passes the bracket-stripped
        form.  ``ipaddress.ip_address`` accepts both bare IPv4 (``"1.2.3.4"``)
        and bare IPv6 (``"::1"``, ``"fe80::1"``); it rejects hostnames,
        empty strings, and malformed IPs with ``ValueError``.
    """
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private_ip(ip_str: str) -> bool:
    """Return True if ``ip_str`` is a private/reserved IP address.

    SSRF defense — rejects IP literals in private/reserved ranges
        so an attacker cannot use a private-IP endpoint (planted in
        ``/etc/hosts`` or via :func:`extend_url_allowlist`) to receive cloud
        API keys.  Covers:

        * RFC 1918 private: ``10/8``, ``172.16/12``, ``192.168/16``
          (via ``ip.is_private``).
        * Link-local: ``169.254/16`` (including the cloud metadata endpoint
          ``169.254.169.254``) and IPv6 ``fe80::/10`` (via
          ``ip.is_link_local``).
        * Loopback: ``127/8`` and ``::1`` (via ``ip.is_loopback``).
        * Unspecified: ``0.0.0.0`` and ``::`` (via ``ip.is_unspecified``).
        * IPv6 unique-local: ``fc00::/7`` (covered by ``ip.is_private``).
        * Reserved ranges: ``240/4``, ``255.255.255.255`` broadcast, etc.
          (via ``ip.is_reserved``).

        Returns ``False`` for non-IP strings (callers should check
        :func:`_is_ip_literal` first to distinguish "not an IP" from
        "public IP").  Returns ``True`` for any IP that would let an
        attacker reach an internal service or the cloud metadata endpoint.
    """
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False  # not an IP literal — caller should resolve first
    # ``is_private`` for IPv4 includes RFC 1918 + 127/8 + 169.254/16 +
    # a few others (per CPython source).  We OR the other checks for
    # defense-in-depth and to cover IPv6 cases that ``is_private`` may
    # not catch (e.g. ``is_link_local`` is the canonical check for
    # ``fe80::/10``).
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved)


def is_url_allowed(url: str) -> bool:
    """Return True if the URL's host is in the allowlist.

    Empty URLs are rejected (consistent with :func:`assert_url_allowed`
    which raises ``ValueError`` on empty input).  URLs with no hostname
    (e.g. ``javascript:alert(1)``) are also rejected.
    """
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
    """Raise ``ValueError`` if ``url`` is not in the allowlist.

        Parameters
        ----------
        url : str
            The URL to check.
        field_name : str
            The config field name (for the error message).
        client_name : str
            The client name (for the error message).
        require_https : bool
    when True (default), non-loopback hosts must use
            HTTPS. Loopback hosts (localhost, 127.0.0.1, ::1) are exempt
            so local development servers can use HTTP. This prevents a
            loopback IPC attacker from exfiltrating transcribed text to
            ``http://attacker.example.com/steal`` even if the attacker
            somehow adds the host to the allowlist.
        allow_loopback_http : bool
    when True, loopback hosts (localhost, 127.0.0.1, ::1)
            are also exempt from the HTTPS requirement — i.e. plain HTTP
            to ``http://localhost:11434`` is permitted. Defaults to False
            so callers must OPT IN to allowing cleartext loopback traffic.
            Callers that send user-supplied text (``llm_polish``,
            ``cloud_engines``) should set this to True when the user has
            explicitly configured a local HTTP endpoint (Ollama, vLLM,
            LM Studio, etc.). Callers that only validate the URL structure
            (env var validation) should leave it False.

    Pre-, loopback was ALWAYS exempt from the HTTPS
            requirement. This meant a caller that just wanted to verify
            URL structure would silently allow HTTP loopback, even when
            the caller's actual data flow never needed cleartext
            transmission. The opt-in kwarg makes the security posture
            explicit at every call site.
        check_dns_rebinding : bool
    when True (default), after the allowlist + HTTPS checks
            pass, perform an SSRF defense check.  For IP-literal hosts the
            check is a blocklist lookup via :func:`_is_private_ip` (always
            run).  For hostname hosts, the check resolves the hostname via
            :func:`socket.getaddrinfo` and rejects if ANY resolved IP is
            private/reserved (catches DNS rebinding, ``/etc/hosts``
            tampering, and compromised-DNS attacks).  The DNS resolution
            is best-effort: a ``socket.gaierror`` (no DNS, offline,
            sandboxed test env) is silently swallowed and the URL is
            allowed — the actual HTTP layer will surface the DNS error in
            the normal way.  Callers that run in a no-network test
            environment can set this to False to skip the resolution
            entirely (the IP-literal blocklist still runs).

        Raises
        ------
        ValueError
            If the URL's scheme is not http/https or its host is not in
            the allowlist.  The error message does NOT include the URL
            itself, to avoid leaking a potentially-malicious URL into logs.
    """
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
    # cleartext exfiltration of transcribed text + API keys.
    #
    # the loopback exemption is now gated on
    # ``allow_loopback_http``. Pre-fix, loopback was ALWAYS exempt —
    # so a caller that just wanted to validate URL structure would
    # silently allow HTTP loopback, even when the caller's actual
    # data flow never needed cleartext transmission. Now callers
    # must opt in via the kwarg, making the security posture
    # explicit.
    is_loopback = host in _LOOPBACK_HOSTS  # was per-call frozenset literal
    if require_https and parsed.scheme == "http" and (not is_loopback or not allow_loopback_http):
        if is_loopback:
            # loopback HTTP rejected because caller didn't opt in
            # via ``allow_loopback_http=True``. The error message
            # explicitly mentions the kwarg so the operator knows how
            # to fix the call site.
            raise ValueError(
                f"{client_name}: {field_name} must use HTTPS for loopback "
                f"host {host!r} (HTTP requires explicit opt-in via "
                f"allow_loopback_http=True — local development servers "
                f"should be the only consumers of cleartext loopback)."
            )
        raise ValueError(
            f"{client_name}: {field_name} must use HTTPS for non-loopback "
            f"host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1 "
            f"for local development). Cleartext transmission of API keys "
            f"and transcribed text over the public internet is not permitted."
        )

    # SSRF defense — after the allowlist + HTTPS checks pass,
    # verify the host is not a private/reserved IP literal (and
    # best-effort, that a hostname doesn't resolve to a private IP).
    #
    # Loopback IPs (127.0.0.1, ::1) are EXEMPTED because they're
    # explicitly allowlisted for local development — the user has
    # already opted in to sending data to localhost.  All other
    # private/reserved IP literals (10/8, 172.16/12, 192.168/16,
    # 169.254/16 including the cloud metadata endpoint, fc00::/7,
    # fe80::/10, 0.0.0.0, ::, etc.) are REJECTED even if the user
    # explicitly added them to the allowlist — defense-in-depth
    # against an attacker who tricks the user into calling
    # ``extend_url_allowlist(["10.0.0.5"])`` and then sets
    # ``cloud_api_url = "https://10.0.0.5/"`` to exfiltrate the API
    # key to an internal service.
    #
    # For hostnames (e.g. ``api.openai.com``), the check resolves via
    # ``socket.getaddrinfo`` and rejects if ANY resolved IP is
    # private/reserved.  This catches DNS rebinding (attacker's DNS
    # returns a public IP for the first resolution, then a private IP
    # for the actual connection — TOCTOU on DNS) and ``/etc/hosts``
    # tampering.  Best-effort: ``gaierror`` is swallowed (offline test
    # environments) and the URL is allowed.
    if is_loopback:
        # Loopback IPs (127.0.0.1, ::1) are explicitly allowlisted for
        # local development — skip the SSRF check (the user has opted
        # in to sending data to localhost).
        return
    if _is_ip_literal(host):
        # IP-literal blocklist (the minimum  fix).  Even if the
        # user explicitly added a private IP to the allowlist, refuse
        # to send cloud API keys to internal endpoints.
        if _is_private_ip(host):
            raise ValueError(
                f"{client_name}: {field_name} host {host!r} is a "
                f"private/reserved IP literal — refusing to prevent "
                f"SSRF. Even if explicitly allowlisted, "
                f"private/reserved IP literals are rejected to "
                f"prevent exfiltration of API keys to internal "
                f"endpoints (e.g. cloud metadata 169.254.169.254)."
            )
    elif check_dns_rebinding:
        # Best-effort post-resolution check (catches DNS rebinding,
        # /etc/hosts tampering, compromised DNS).  Resolve via
        # getaddrinfo; if any resolved IP is private/reserved, reject.
        # Failure to resolve is NON-FATAL (gaierror swallowed) — the
        # HTTP layer will surface the DNS error in the normal way.
        # This means a no-network test environment won't reject
        # allowlisted hostnames (the IP-literal blocklist above still
        # runs for IP literals).
        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            infos = []
        for _family, _type, _proto, _canonname, sockaddr in infos:
            # sockaddr[0] is the IP address string for both AF_INET
            # (host, port) and AF_INET6 (host, port, flowinfo, scopeid).
            ip = sockaddr[0]
            if _is_private_ip(ip):
                raise ValueError(
                    f"{client_name}: {field_name} host {host!r} resolves "
                    f"to private/reserved IP {ip!r} — refusing to "
                    f"prevent SSRF (DNS rebinding defense). If "
                    f"this is a legitimate local endpoint, use the IP "
                    f"literal directly (e.g. http://127.0.0.1:port) "
                    f"which is allowlisted for local development."
                )


# Module-load bootstrap: extend the URL allowlist from the
# ``VOICE_TYPER_TRUSTED_HOSTS`` env var so users running self-hosted
# cloud endpoints on non-loopback hosts (e.g. ``https://my-vllm.lan``)
# pass ``assert_url_allowed`` without code changes. See
# ``_load_env_allowlist_extensions`` for details.
_load_env_allowlist_extensions()
