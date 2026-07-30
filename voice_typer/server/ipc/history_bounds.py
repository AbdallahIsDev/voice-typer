# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""History-DB bounds and config sanitization helpers.

Phase 4.5 / ARCH-045 — extracted from the original ``ipc_server.py``
god-module.  Contains:

- :func:`_bound_history_limit` / :func:`_bound_history_offset` —
  clamp caller-supplied history pagination params to a safe range
  (SEC-010).
- :func:`_sanitize_config_for_ipc` — return a copy of
  ``config.__dict__`` with secret fields redacted (SEC-003).

SEC-003: ``get_config`` must NOT echo secret fields back to the IPC
client.  Even though the IPC socket is loopback-only, any local process
can connect to it (see SEC-018 for the auth fix).  We return a
sanitized view where API keys are replaced with a presence indicator so
the renderer can render "key configured" UI without ever holding the
actual key value.

DE-33 (2026-10 fix): the prior implementation relied on a static
``_SECRET_CONFIG_FIELDS`` frozenset of five hand-listed names.  Any
future secret-bearing config field added without updating that frozenset
would be echoed verbatim to the IPC client — a credential disclosure.
The frozenset is retained for backward compat (``crash_recovery.py``
still imports it for its own redaction path), but
:func:`_sanitize_config_for_ipc` now ALSO consults a pattern-based
denylist (:data:`_SECRET_FIELD_PATTERNS`) so a field like
``azure_api_key`` or ``oauth_token`` is masked even if no one remembers
to add it to the frozenset.  Redaction was also tightened: any non-None
value is masked (previously falsy values like ``0`` / ``False`` /
``""`` were preserved verbatim — fine for the empty-string case but
unsafe for ``0`` / ``False`` secrets and inconsistent with the "key is
set" semantic).

DE-35 (2026-10 fix): :func:`_bound_history_offset` now caps the offset
at :data:`_HISTORY_OFFSET_MAX` (10_000_000) in addition to the
``max(0, v)`` floor.  Previously a client could send
``offset=999999999999`` (or ``int('9'*10000)``) and force SQLite to
scan/skip rows wastefully — Python big-ints are unbounded, so the clamp
alone never tripped.  The cap matches the ``limit`` cap pattern.
"""

# Fields whose values are secrets and must never be echoed back.
# DE-33: this frozenset is the EXPLICIT allowlist — kept for backward
# compat with ``crash_recovery.py`` (which imports it for its own
# config.json redaction path). The PATTERN-based denylist below is the
# authoritative defense-in-depth: a new secret field that matches a
# pattern is redacted EVEN IF no one remembers to add it here.
_SECRET_CONFIG_FIELDS = frozenset(
    {
        "cloud_api_key",
        "openai_api_key",
        "groq_api_key",
        "deepgram_api_key",
        "llm_api_key",
    }
)

# DE-33: pattern-based secret-field denylist (defense-in-depth).
#
# Each entry is either:
# - ``"!<suffix>"`` (e.g. ``"!_api_key"``) — matches any field name
#   ending with the suffix. This is the ``"*_api_key"`` glob form,
#   encoded as ``"!"`` prefix so the matcher is unambiguous (no shell
#   glob chars in Python string literals).
# - ``"=name"`` (e.g. ``"=password"``) — matches a field name that is
#   EXACTLY ``name``. Used for bare names like ``password`` /
#   ``credential`` / ``bearer`` that don't have a natural suffix.
#
# The patterns cover the conventional secret-bearing field names:
# - ``*_api_key`` — cloud/vendor API keys (cloud_api_key, openai_api_key,
#   azure_api_key, anthropic_api_key, whisper_api_key, ...)
# - ``*_token`` — OAuth / refresh / bearer tokens (access_token,
#   refresh_token, id_token, bearer_token, ...)
# - ``*_secret`` — HMAC secrets, client secrets (client_secret,
#   signing_secret, ...)
# - ``password`` / ``*_password`` — password fields (user_password,
#   admin_password, db_password, ...)
# - ``credential`` / ``*_credential`` — credential blobs (aws_credential,
#   service_credential, ...)
# - ``bearer`` / ``*_bearer`` — bearer tokens (auth_bearer, ...)
#
# IMPORTANT: the patterns are NAME-based, not value-based. A field like
# ``warn_password_paste`` (boolean flag in Config) does NOT match
# because it doesn't end in ``_password`` (it ends in ``_paste``).
_SECRET_FIELD_PATTERNS: tuple[str, ...] = (
    # Suffix patterns (``*_api_key`` glob form).
    "!_api_key",
    "!_token",
    "!_secret",
    "!_password",
    "!_credential",
    "!_bearer",
    # XE-2-2: ``!_key`` catches generic key-suffixed fields that the
    # narrower ``!_api_key`` suffix missed — ``secret_key``,
    # ``signing_key``, ``private_key``, ``hmac_key``, ``aes_key``,
    # ``encryption_key``. The pattern is NAME-based so a non-secret
    # field like ``keyboard_layout_key`` (a configurable key code)
    # WOULD match — but the conservative redaction stance is
    # preferable to silently leaking a signing key.
    "!_key",
    # Exact-match patterns (bare names — must be the WHOLE field name).
    "=password",
    "=credential",
    "=bearer",
    "=secret",
    "=token",
    "=api_key",
    # XE-2-2: bare-name exact matches for cryptographic key material
    # that doesn't carry a vendor prefix. ``private_key`` /
    # ``secret_key`` / ``signing_key`` are the conventional names for
    # PEM-encoded key blobs; without these exact matches a Config
    # field literally named ``private_key`` would be echoed verbatim
    # to the IPC client (credential disclosure).
    "=private_key",
    "=secret_key",
    "=signing_key",
)


def _is_secret_field_name(name: str) -> bool:
    """Return True if ``name`` matches a secret-field pattern (DE-33).

    A field is considered secret if EITHER:
    - It is listed in :data:`_SECRET_CONFIG_FIELDS` (explicit allowlist,
      kept for backward compat with ``crash_recovery.py``), OR
    - It matches one of :data:`_SECRET_FIELD_PATTERNS` (pattern-based
      denylist — defense-in-depth so a new secret field added to
      ``Config`` without updating the frozenset is still redacted).

    The pattern match is name-based, not value-based, so a boolean flag
    like ``warn_password_paste`` is NOT redacted (it doesn't end in
    ``_password``).
    """
    if name in _SECRET_CONFIG_FIELDS:
        return True
    for pat in _SECRET_FIELD_PATTERNS:
        if pat.startswith("!"):
            # Suffix pattern: ``"!_api_key"`` → ``name.endswith("_api_key")``.
            suffix = pat[1:]
            if name.endswith(suffix):
                return True
        elif pat.startswith("="):
            # Exact-match pattern: ``"=password"`` → ``name == "password"``.
            if name == pat[1:]:
                return True
    return False


# Sentinel returned in place of a secret value.  The renderer treats
# this as "key is set, do not display" — it must NOT treat this as the
# actual key value (which would be a regression of SEC-003).
_REDACTED_SENTINEL = "<redacted>"


# SEC-010: maximum number of history rows a single IPC call can
# materialize.  Without this cap, ``{"limit": 100000000}`` would
# force SQLite to scan and the dispatcher to materialize a million
# rows before slicing — a trivial DoS.
_HISTORY_LIMIT_MAX = 500
_HISTORY_LIMIT_DEFAULT = 50

# DE-35: maximum history ``offset`` accepted from a client.  Python
# big-ints are unbounded, so without this cap a client sending
# ``offset=999999999999`` (or ``int('9'*10000)``) could force SQLite to
# scan/skip rows wastefully even though the result set is empty.  10M
# is far above any plausible history size (a 24/7 dictation user
# accumulates ~100K rows/year) but small enough that SQLite's
# ``OFFSET n`` skip is microseconds.  See :func:`_bound_history_offset`.
_HISTORY_OFFSET_MAX = 10_000_000


def _bound_history_limit(raw) -> int:
    """Clamp a caller-supplied history ``limit`` to a safe range.

    Accepts ints, floats, and numeric strings (the renderer sometimes
    sends strings from form inputs).  Rejects anything else with the
    default.  Result is always in ``[1, _HISTORY_LIMIT_MAX]``.
    """
    if raw is None:
        return _HISTORY_LIMIT_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _HISTORY_LIMIT_DEFAULT
    return max(1, min(v, _HISTORY_LIMIT_MAX))


def _bound_history_offset(raw) -> int:
    """Clamp a caller-supplied history ``offset`` to a safe range.

    DE-35: previously the offset was clamped only with ``max(0, v)``
    (no upper bound).  Python big-ints are unbounded, so a client
    sending ``offset=999999999999`` (or ``int('9'*10000)``) could
    force SQLite to scan/skip rows wastefully.  The offset is now
    capped at :data:`_HISTORY_OFFSET_MAX` (10_000_000) — far above any
    plausible history size, but small enough that the SQL ``OFFSET n``
    skip stays in the microsecond range.  Mirrors the
    :func:`_bound_history_limit` cap pattern.

    Accepts ints, floats, and numeric strings.  Rejects anything else
    with ``0``.  Result is always in ``[0, _HISTORY_OFFSET_MAX]``.
    """
    if raw is None:
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(v, _HISTORY_OFFSET_MAX))


def _sanitize_config_for_ipc(config) -> dict:
    """Return a copy of ``config.__dict__`` with secret fields redacted.

    A field is considered secret if EITHER:

    - It is listed in :data:`_SECRET_CONFIG_FIELDS` (the explicit
      allowlist, kept for backward compat with ``crash_recovery.py``'s
      own config.json redaction path), OR
    - It matches one of :data:`_SECRET_FIELD_PATTERNS` (the pattern-
      based denylist — defense-in-depth so a new secret field added to
      ``Config`` without updating the frozenset is still redacted).

    DE-33: redaction now masks any non-None value, regardless of
    truthiness.  Previously falsy values like ``0`` / ``False`` /
    ``""`` were preserved verbatim — fine for the empty-string "no key
    set" case but unsafe for ``0`` / ``False`` secrets and inconsistent
    with the "key is set" semantic.  ``None`` is still preserved (so
    the renderer can distinguish "no key configured" from "key set but
    hidden"); any other value (including ``""``) is replaced with the
    ``<redacted>`` sentinel.
    """
    out = config.__dict__.copy()
    for k in list(out.keys()):
        if not _is_secret_field_name(k):
            continue
        v = out[k]
        # DE-33: redact any non-None value, regardless of truthiness.
        # Previously ``v if not v else _REDACTED_SENTINEL`` would skip
        # falsy non-None values (``0``, ``False``, ``""``) — fine for
        # ``""`` (the "no key set" case) but unsafe for ``0`` /
        # ``False`` secrets and inconsistent with the documented
        # "key is set" semantic.  ``None`` is preserved so the
        # renderer can distinguish "not configured" from "configured
        # but hidden".
        if v is None:
            continue
        out[k] = _REDACTED_SENTINEL
    return out


__all__ = [
    "_bound_history_limit",
    "_bound_history_offset",
    "_sanitize_config_for_ipc",
    "_is_secret_field_name",
    "_SECRET_CONFIG_FIELDS",
    "_SECRET_FIELD_PATTERNS",
    "_REDACTED_SENTINEL",
    "_HISTORY_LIMIT_MAX",
    "_HISTORY_LIMIT_DEFAULT",
    "_HISTORY_OFFSET_MAX",
]
