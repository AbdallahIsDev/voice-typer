# extracted from the original
"""History-DB bounds and config sanitization helpers.

Phase 4.5 / , extracted from the original ``ipc_server.py``
god-module.  Contains:

- :func:`_bound_history_limit` / :func:`_bound_history_offset` —
  clamp caller-supplied history pagination params to a safe range
  (SEC-010).
- :func:`_sanitize_config_for_ipc`: return a copy of
  ``config.__dict__`` with secret fields redacted (SEC-003).

SEC-003: ``get_config`` must NOT echo secret fields back to the IPC
client.  Even though the IPC socket is loopback-only, any local process
can connect to it (see SEC-018 for the auth fix).  We return a
sanitized view where API keys are replaced with a presence indicator so
the renderer can render "key configured" UI without ever holding the
actual key value.

(2026-10 fix): the prior implementation relied on a static
``_SECRET_CONFIG_FIELDS`` frozenset of five hand-listed names.  Any
future secret-bearing config field added without updating that frozenset
would be echoed verbatim to the IPC client, a credential disclosure.
The frozenset is retained for backward compat (``crash_recovery.py``
still imports it for its own redaction path), but
:func:`_sanitize_config_for_ipc` now ALSO consults a pattern-based
denylist (:data:`_SECRET_FIELD_PATTERNS`) so a field like
``azure_api_key`` or ``oauth_token`` is masked even if no one remembers
to add it to the frozenset.  Redaction was also tightened: any non-None
value is masked (previously falsy values like ``0`` / ``False`` /
``""`` were preserved verbatim, fine for the empty-string case but
unsafe for ``0`` / ``False`` secrets and inconsistent with the "key is
set" semantic).

(2026-10 fix): :func:`_bound_history_offset` now caps the offset
at :data:`_HISTORY_OFFSET_MAX` (10_000_000) in addition to the
``max(0, v)`` floor.  Previously a client could send
``offset=999999999999`` (or ``int('9'*10000)``) and force SQLite to
scan/skip rows wastefully. Python big-ints are unbounded, so the clamp
alone never tripped.  The cap matches the ``limit`` cap pattern.
"""

# Fields whose values are secrets and must never be echoed back.
from voice_typer.server.config_sanitizer import _SECRET_CONFIG_FIELDS  # noqa: F401

# pattern-based secret-field denylist (defense-in-depth).
_SECRET_FIELD_PATTERNS: tuple[str, ...] = (
    # Suffix patterns (``*_api_key`` glob form).
    "!_api_key",
    "!_token",
    "!_secret",
    "!_password",
    "!_credential",
    "!_bearer",
    # ``!_key`` catches generic key-suffixed fields that the
    "!_key",
    # Exact-match patterns (bare names, must be the WHOLE field name).
    "=password",
    "=credential",
    "=bearer",
    "=secret",
    "=token",
    "=api_key",
    # bare-name exact matches for cryptographic key material
    "=private_key",
    "=secret_key",
    "=signing_key",
)


def _is_secret_field_name(name: str) -> bool:
    """Return True if ``name`` matches a secret-field pattern ().

    A field is considered secret if EITHER:
    - It is listed in :data:`_SECRET_CONFIG_FIELDS` (explicit allowlist,
      kept for backward compat with ``crash_recovery.py``), OR
    - It matches one of :data:`_SECRET_FIELD_PATTERNS` (pattern-based
      denylist, defense-in-depth so a new secret field added to
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


# actual key value (which would be a regression of SEC-003).
_REDACTED_SENTINEL = "<redacted>"


# SEC-010: maximum number of history rows a single IPC call can
_HISTORY_LIMIT_MAX = 500
_HISTORY_LIMIT_DEFAULT = 50

# maximum history ``offset`` accepted from a client.  Python
_HISTORY_OFFSET_MAX = 10_000_000


def _bound_history_limit(raw) -> int:
    """Clamp a caller-supplied history ``limit`` to a safe range.

    Accepts ints, floats, and numeric strings (the renderer sometimes
    sends strings from form inputs).  Rejects anything else with the
    default.  Result is always in ``[1, _HISTORY_LIMIT_MAX]``.

    Defense-in-depth against the bool-as-int type confusion:
    ``bool`` subclasses ``int`` in Python, so ``isinstance(True, int)``
    is ``True`` and ``int(True) == 1``. If validation ever accepts a
    bool ``limit`` (e.g. a schema without ``reject_bool=True``), the
    bounder falls back to the default rather than silently coercing
    ``True`` → 1 or ``False`` → 1 (via the lower-bound clamp). A bool
    is semantically a toggle, not a pagination count, treating it as
    a number masks a caller bug.
    """
    if isinstance(raw, bool):
        # bool subclasses int, reject explicitly so a stray
        return _HISTORY_LIMIT_DEFAULT
    if raw is None:
        return _HISTORY_LIMIT_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _HISTORY_LIMIT_DEFAULT
    return max(1, min(v, _HISTORY_LIMIT_MAX))


def _bound_history_offset(raw) -> int:
    """Clamp a caller-supplied history ``offset`` to a safe range.

    previously the offset was clamped only with ``max(0, v)``
        (no upper bound).  Python big-ints are unbounded, so a client
        sending ``offset=999999999999`` (or ``int('9'*10000)``) could
        force SQLite to scan/skip rows wastefully.  The offset is now
        capped at :data:`_HISTORY_OFFSET_MAX` (10_000_000), far above any
        plausible history size, but small enough that the SQL ``OFFSET n``
        skip stays in the microsecond range.  Mirrors the
        :func:`_bound_history_limit` cap pattern.

        Accepts ints, floats, and numeric strings.  Rejects anything else
        with ``0``.  Result is always in ``[0, _HISTORY_OFFSET_MAX]``.

    Defense-in-depth against the bool-as-int type confusion (see
    :func:`_bound_history_limit`): ``bool`` subclasses ``int``, so a
    stray ``{"offset": true}`` would silently coerce to ``1`` (or
    ``0`` for ``False``). The bounder falls back to ``0`` (the
    sensible default for "no offset") instead.
    """
    if isinstance(raw, bool):
        # bool subclasses int, reject explicitly so a stray
        return 0
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
          based denylist, defense-in-depth so a new secret field added to
          ``Config`` without updating the frozenset is still redacted).

    redaction masks any non-None, non-empty-string value.  ``None``
        AND ``""`` are both preserved: ``""`` is the config schema's
        canonical "no key set" value for every API-key field (see
        ``config/_schema.py``), so the renderer must receive it verbatim
        to distinguish "not configured" from "configured but hidden".
        User-adjudicated product decision (2026-09-16): restoring the
        ``""`` carve-out narrows the earlier all-falsy tightening, but
        ``0`` / ``False`` stay masked (a real key is never ``0`` /
        ``False``; an empty string uniquely means "unset" by schema
        contract).  Any other value is replaced with the ``<redacted>``
        sentinel.
    """
    out = config.__dict__.copy()
    for k in list(out.keys()):
        if not _is_secret_field_name(k):
            continue
        v = out[k]
        # redact any set value.  Both "unset" sentinels (``None`` and
        if v is None or v == "":
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
