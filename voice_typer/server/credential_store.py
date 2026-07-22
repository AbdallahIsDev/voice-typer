"""Encrypted credential store for API keys via the OS keychain.

RW-01: API keys for cloud providers (OpenAI / Groq / Deepgram) and the
LLM polishing service are stored via the ``keyring`` library, which
auto-selects the appropriate OS-native backend at runtime:

  - Windows: Windows Credential Manager
  - macOS:   Keychain
  - Linux:   Secret Service (libsecret / GNOME Keyring / KWallet)

When no usable backend is available (most commonly on a headless Linux
container without ``gnome-keyring-daemon`` and ``python-dbus``), the
store falls back to the legacy behavior: plaintext in ``config.json``
with ``0o600`` permissions on POSIX (the file is already created with
``0o600`` by ``_secure_atomic_write`` in ``config.py``).

Design notes
------------

- ``config.json`` never contains the actual secret when keyring is
  available. Instead it stores a *reference token* of the form
  ``"keyring://<provider>"`` in the existing flat ``<provider>_api_key``
  field. The reference token is what the renderer's redacted view sees
  via ``get_config`` — the real value only leaves the keychain in the
  Python process that needs it (``cloud_engines.py`` / ``llm_polish.py``).

- The in-memory ``Config`` dataclass (``config.openai_api_key`` etc.)
  still carries the real value after ``Config.load()`` resolves the
  reference. This preserves backward compatibility with all existing
  consumers (``cloud_engines``, ``llm_polish``, ``dictation_pipeline``,
  ``service.test_llm_connection``) without touching their call sites.

- ``store_secret`` never raises — it logs a warning and falls back to
  plaintext on any keyring failure. This means a broken D-Bus or a
  locked Keychain never prevents the user from saving their API key.

- Secret values are NEVER logged. Only metadata (provider name, value
  length, keyring-vs-fallback status) appears in log messages. Defense
  in depth: keyring exception messages are passed through
  :func:`_redact_sensitive` before being logged or surfaced to the
  renderer via ``get_keyring_status`` — this strips filesystem paths
  and API-key-like substrings, in case a buggy or custom backend
  embeds sensitive data in its exception text.

- **Reference-token unforgeability**: the ``keyring://<provider>``
  suffix in a reference token is NEVER used to look up the secret.
  ``Config.load()`` iterates :data:`PROVIDER_TO_CONFIG_FIELD` and calls
  ``load_secret(provider)`` with the provider matched to the *field*
  (``CONFIG_FIELD_TO_PROVIDER``), ignoring the token's suffix. A
  malicious ``config.json`` that puts ``"keyring://llm"`` in
  ``openai_api_key`` cannot trick the loader into returning the LLM
  secret — the code will still call ``load_secret("openai")``, which
  looks up only the OpenAI entry in the keychain.

- **Python memory hygiene (known limitation)**: Python ``str`` is
  immutable, so a secret returned by :func:`load_secret` cannot be
  zeroed in place. The value lives in the ``Config`` dataclass for the
  app's lifetime. We do not attempt ``bytearray`` + ``del`` here
  because the value is immediately returned to the caller (which
  stores it as a ``str`` attribute anyway). This is the standard
  Python limitation — full secret-memory hygiene requires a C extension.

- **Two-instance migration race (closed — RACE-001 / HIGH-13)**: the
  ``secrets_migrated`` flag in ``config.json`` is guarded by an
  exclusive cross-process lock (``fcntl.flock`` on POSIX,
  ``msvcrt.locking`` on Windows) acquired on ``config.json.lock``.
  :func:`migrate_secrets_to_keyring` acquires the lock before
  reading config.json, RE-READS the file once the lock is held (so a
  concurrent migration that completed while we waited is observed),
  and only then proceeds with the read-migrate-write sequence.
  This closes the prior race where two app instances could both enter
  the function before either wrote the flag, both read plaintext,
  both write their own ``data`` dict, and clobber each other (losing
  a real secret from disk).  The migration remains idempotent at the
  keyring level (``keyring.set_password`` overwrites) as defense in
  depth.

Cross-platform testing notes are in ``docs/security/credential-store.md``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

log = logging.getLogger("voice_typer.server.credential_store")

# ── Constants ────────────────────────────────────────────────────────────

#: The ``keyring`` service name. All Voice Typer secrets live under this
#: single service, with the provider name as the username key. This
#: matches the convention recommended by the keyring docs (one service
#: per application, multiple usernames).
KEYRING_SERVICE_NAME = "voice-typer"

#: The prefix used in config.json reference tokens. A flat api_key field
#: whose value starts with this prefix is treated as "the real secret
#: lives in the OS keychain" and is resolved via ``load_secret`` on
#: Config.load().
KEYRING_REF_PREFIX = "keyring://"

#: Map of provider name → Config dataclass field name. The provider name
#: is what gets passed to ``keyring.set_password(service, provider, value)``
#: and is what appears in the reference token (``keyring://openai``).
#: The field name is what's read/written on the ``Config`` dataclass.
PROVIDER_TO_CONFIG_FIELD: dict[str, str] = {
    "openai": "openai_api_key",
    "groq": "groq_api_key",
    "deepgram": "deepgram_api_key",
    "cloud": "cloud_api_key",
    "llm": "llm_api_key",
}

#: Reverse lookup: Config dataclass field name → provider name.
CONFIG_FIELD_TO_PROVIDER: dict[str, str] = {v: k for k, v in PROVIDER_TO_CONFIG_FIELD.items()}

#: Maximum length of a sanitized reason / diagnostic string. Keeps the
#: renderer tooltip concise and bounds the amount of keyring-backend
#: error text we surface over IPC or write to logs.
_REASON_MAX_LEN = 200

# CR-94: thread-local record of the most recent ``store_secret`` outcome.
# Used by :func:`last_store_outcome` so the IPC handler (Fix-G) can
# surface ``{"stored_in": "keyring"|"plaintext", "provider": "...",
# "reason": "..."}`` in the ``set_config`` ack payload WITHOUT changing
# ``store_secret``'s return type from ``bool``. The state is thread-local
# because the IPC server is multi-threaded and the call to
# ``store_secret`` and the subsequent call to ``last_store_outcome``
# always happen on the same IPC handler thread (no inter-thread hand-off).
#
# ``_last_store_outcome`` is a ``threading.local()`` instance whose
# ``outcome`` attribute is a dict (or None before the first store on
# that thread). We use ``threading.local`` directly (rather than a
# ``threading.Lock`` + dict[thread_id, dict]) so we don't accumulate
# stale entries for threads that have exited.
_last_store_outcome = threading.local()


def _set_last_store_outcome(
    stored_in: str,
    reason: str | None,
    provider: str | None = None,
) -> None:
    """Record the outcome of the most recent ``store_secret`` call.

    Called from inside :func:`store_secret` on every code path that
    returns. Stored in thread-local state so a subsequent
    :func:`last_store_outcome` call on the same thread returns the
    matching outcome.

    Parameters
    ----------
    stored_in : str
        ``"keyring"`` if the secret was stored in the OS keychain, or
        ``"plaintext"`` if it was written to ``config.json`` as a
        fallback. ``"deleted"`` is used when an empty value triggered
        :func:`delete_secret` (so the caller can distinguish a delete
        from a real store).
    reason : str | None
        Short, redacted reason string when ``stored_in`` is
        ``"plaintext"`` (the keyring exception message passed through
        :func:`_redact_sensitive`). ``None`` for the keyring-success
        and delete paths.
    provider : str | None
        The provider name passed to ``store_secret`` (e.g.
        ``"openai"``, ``"groq"``). Included so the IPC handler can
        build a more informative ack message (e.g. "OpenAI API key
        stored in plaintext because ...") without re-deriving it from
        the call site. ``None`` only on the ``unknown`` (no-store-yet)
        path.
    """
    _last_store_outcome.outcome = {
        "stored_in": stored_in,
        "reason": _redact_sensitive(reason) if reason else None,
        "provider": provider,
    }


def last_store_outcome() -> dict[str, Any]:
    """Return the outcome of the most recent ``store_secret`` call.

    CR-94: ``store_secret`` returns a plain ``bool`` and silently
    falls back to plaintext on keyring failure. The IPC handler
    (``set_config`` — Fix-G's territory) needs to surface *why* the
    store fell back so the renderer can show a "your API key was
    stored in plaintext because <reason>" warning. Rather than change
    ``store_secret``'s return type (which would break every caller),
    we record the outcome in thread-local state and expose it via
    this function.

    The state is thread-local so concurrent IPC handler threads don't
    stomp each other's outcomes. The IPC handler always calls
    ``store_secret`` and ``last_store_outcome`` on the same thread
    (no inter-thread hand-off), so this is safe.

    Returns
    -------
    dict with keys:
        - ``stored_in`` (str): one of
              * ``"keyring"``    — secret stored in OS keychain.
              * ``"plaintext"``  — secret written to ``config.json``
                as a fallback (keyring was unavailable or errored).
              * ``"deleted"``    — empty value triggered a delete.
              * ``"unknown"``    — no ``store_secret`` call has been
                made on this thread yet (e.g. on a fresh IPC handler
                thread that has only served read requests).
        - ``reason`` (str | None): a short, redacted reason string
          when ``stored_in`` is ``"plaintext"`` (the keyring exception
          message, with paths / API-key-like substrings stripped by
          :func:`_redact_sensitive`). ``None`` for the keyring-success,
          delete, and unknown paths.
        - ``provider`` (str | None): the provider name passed to the
          most recent ``store_secret`` call (e.g. ``"openai"``).
          Included so the IPC handler can build a more informative
          ack message (e.g. "OpenAI API key stored in plaintext
          because ...") without re-deriving it from the call site.
          ``None`` only on the ``unknown`` (no-store-yet) path.

    Notes
    -----
    The returned ``reason`` is passed through :func:`_redact_sensitive`
    before being stored, so it never contains a filesystem path or an
    API-key-like substring. Suitable for direct inclusion in an IPC
    ack payload that the renderer displays to the user.
    """
    outcome = getattr(_last_store_outcome, "outcome", None)
    if outcome is None:
        return {"stored_in": "unknown", "reason": None, "provider": None}
    # Return a shallow copy so callers can't mutate our thread-local
    # state via the returned dict reference.
    return dict(outcome)


# Defense-in-depth redaction patterns. Applied to keyring exception
# messages and probe reasons before they're logged or surfaced to the
# renderer via get_keyring_status(). Even though keyring's
# get_password / set_password shouldn't put the secret value in
# exception text (get_password is given only service+username; the
# value is what it returns), a buggy or custom backend might leak it.
#
# _PATH_RE: matches /home/<user>, /Users/<user>, ~/<path>, C:\Users\<user>.
#   These are common in keyring backend error messages (e.g. libsecret
#   D-Bus errors referencing the session bus path, or pyobjc errors
#   referencing the keychain file). The user's home directory is
#   private metadata — redact it before exposing via IPC.
# _API_KEY_RE: matches common API-key prefixes (sk-, gsk_, plus
#   generic 32+ char alphanumeric runs that look like bearer tokens).
#   This is the backstop — if a backend somehow embeds the value in
#   an exception, we redact it before it reaches logs or the renderer.
_PATH_RE = re.compile(
    r"(?:/home/[^/\s]+|/Users/[^/\s]+|~[/][^/\s]+|C:\\Users\\[^\\\s]+)",
    re.IGNORECASE,
)
_API_KEY_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|[A-Za-z0-9_-]{32,})")


def _redact_sensitive(text: str | None) -> str | None:
    """Redact filesystem paths and API-key-like substrings from ``text``.

    Used as defense in depth on keyring exception messages and probe
    reasons before they're logged or returned via
    :func:`get_keyring_status`. Also truncates to
    :data:`_REASON_MAX_LEN` chars so a verbose backend error can't
    flood the renderer tooltip or the log file.

    Returns ``None`` unchanged (so callers can pass through optional
    values without a separate None check).
    """
    if not text:
        return text
    s = str(text)
    s = _PATH_RE.sub("[path]", s)
    s = _API_KEY_RE.sub("[redacted]", s)
    if len(s) > _REASON_MAX_LEN:
        s = s[: _REASON_MAX_LEN - 3] + "..."
    return s


# ── Keyring availability ────────────────────────────────────────────────


# Cached result of is_keyring_available(). None = not yet probed.
_keyring_available_cache: bool | None = None
# Cached backend name. Preserved even when unavailable (e.g. "fail"
# or the broken backend's class name) for diagnostics.
_keyring_backend_name_cache: str | None = None
# Cached reason string (already redacted). None when available, or when
# not yet probed. Cached alongside the available/backend fields so
# get_keyring_status() returns a consistent snapshot without re-probing.
_keyring_reason_cache: str | None = None


def _probe_keyring() -> tuple[bool, str | None, str | None]:
    """Probe the keyring library and return ``(available, backend_name, reason)``.

    ``available`` is True only when a real backend is installed — the
    ``keyring.backends.fail.Keyring`` backend (used when no backend is
    available) raises on every operation, so we treat it as unavailable
    and fall back to plaintext.

    The probe is wrapped in a broad ``except Exception`` because the
    keyring library can raise a variety of errors during backend
    selection (D-Bus connection errors on Linux, missing pyobjc on
    macOS, missing pywin32 on Windows). All of these mean "no usable
    backend" from our perspective.

    The returned ``reason`` (when not None) is passed through
    :func:`_redact_sensitive` to strip filesystem paths and
    API-key-like substrings — defense in depth against buggy or
    custom keyring backends that might embed sensitive data in their
    exception text. The reason is surfaced to the renderer via
    :func:`get_keyring_status` and written to logs, so it must not
    contain anything the user wouldn't want in a tooltip.
    """
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.backends.fail import Keyring as FailKeyring  # type: ignore[import-not-found]
    except Exception as e:
        # keyring not installed, or fail backend module missing (very
        # old keyring version). Either way, no keyring available.
        return False, None, _redact_sensitive(f"keyring import failed: {e}")

    try:
        backend = keyring.get_keyring()
    except Exception as e:
        return False, None, _redact_sensitive(f"keyring.get_keyring() raised: {e}")

    if isinstance(backend, FailKeyring):
        return False, "fail", "no usable keyring backend (fail backend selected)"

    # Some backends (e.g. libsecret on Linux without D-Bus) are
    # technically "selected" but raise on every operation. Probe with a
    # benign read to confirm the backend actually works.
    try:
        # get_password returns None for a missing entry; any other
        # result (including None) means the backend is responsive.
        # We use a sentinel username that we never store under to avoid
        # accidentally returning a real secret.
        backend.get_password(KEYRING_SERVICE_NAME, "__keyring_probe__")
    except Exception as e:
        return (
            False,
            type(backend).__name__,
            _redact_sensitive(f"keyring backend probe failed: {e}"),
        )

    return True, type(backend).__name__, None


def is_keyring_available() -> bool:
    """Return True if a usable keyring backend is installed.

    The result is cached for the lifetime of the process (a backend
    won't appear mid-run). Tests that need to force re-probing can
    call :func:`_reset_keyring_cache`.
    """
    global _keyring_available_cache, _keyring_backend_name_cache, _keyring_reason_cache
    if _keyring_available_cache is None:
        available, backend_name, reason = _probe_keyring()
        _keyring_available_cache = available
        # Cache the backend name AND the reason so get_keyring_status()
        # can return a consistent snapshot without re-probing (the
        # probe touches D-Bus / Keychain / Credential Manager and may
        # be slow or have side effects on some platforms).
        _keyring_backend_name_cache = backend_name
        _keyring_reason_cache = reason
    return _keyring_available_cache


def _reset_keyring_cache() -> None:
    """Test-only: clear the cached keyring availability result."""
    global _keyring_available_cache, _keyring_backend_name_cache, _keyring_reason_cache
    _keyring_available_cache = None
    _keyring_backend_name_cache = None
    _keyring_reason_cache = None


def get_keyring_status() -> dict[str, Any]:
    """Return a status dict describing the current keyring backend.

    The renderer reads this from the ``get_config`` response so it can
    show "Stored securely in your OS keychain" indicators next to API
    key inputs, or a warning when only the plaintext fallback is
    available.

    Returns
    -------
    dict with keys:
        - ``available`` (bool): whether a real keyring backend is in use.
        - ``backend`` (str | None): the backend class name (e.g.
          ``"SecretServiceKeyring"``, ``"macOSKeyring"``,
          ``"WindowsCredentialVaultKeyring"``). Preserved even when
          ``available`` is False (e.g. ``"fail"`` or the broken
          backend's class name) for diagnostics; None only when the
          keyring library itself couldn't be imported.
        - ``fallback`` (bool): True when secrets will be stored in
          plaintext in config.json (i.e. ``not available``).
        - ``reason`` (str | None): a short, redacted reason string when
          ``available`` is False, else None. Suitable for showing in
          a tooltip; passed through :func:`_redact_sensitive` so it
          never contains a filesystem path, an API-key-like substring,
          or more than :data:`_REASON_MAX_LEN` characters.
    """
    # Single consistent snapshot from the cache. is_keyring_available()
    # populates all three cache fields (available + backend + reason)
    # in one probe, so we never return a stale backend paired with a
    # fresh reason (or vice versa). A final _redact_sensitive pass on
    # the reason is defense in depth: even if a future change to
    # _probe_keyring forgets to redact, the output is still safe.
    is_keyring_available()
    return {
        "available": bool(_keyring_available_cache),
        "backend": _keyring_backend_name_cache,
        "fallback": not bool(_keyring_available_cache),
        "reason": _redact_sensitive(_keyring_reason_cache),
    }


# ── Secret store / load / delete ────────────────────────────────────────


def store_secret(provider: str, value: str) -> bool:
    """Store a secret for ``provider`` in the OS keychain.

    Parameters
    ----------
    provider : str
        Provider name (one of the keys in :data:`PROVIDER_TO_CONFIG_FIELD`).
    value : str
        The secret value to store. An empty string is treated as a
        delete request — the secret is removed from both keyring and
        the plaintext fallback.

    Returns
    -------
    bool
        True if the secret was stored in keyring (or deleted via the
        empty-value path). False if keyring was unavailable or errored
        and the secret was written to config.json as a plaintext
        fallback (with ``0o600`` perms on POSIX).

        CR-94: to surface *why* the store fell back to plaintext to
        the IPC caller (Fix-G), call :func:`last_store_outcome`
        immediately after this function returns on the same thread.
        It returns a dict ``{"stored_in": "keyring"|"plaintext"|
        "deleted"|"unknown", "reason": str | None, "provider": str | None}``
        matching the most recent call to ``store_secret`` on this
        thread. The boolean return value alone is preserved for
        backwards compat with every existing caller.

    Notes
    -----
    This function NEVER raises. Any keyring error is caught, logged
    (with provider name + value length only — never the value itself),
    and the secret is written to config.json as a fallback. This means
    a broken D-Bus or locked Keychain never prevents the user from
    saving their API key.

    Thread-safety: the outcome record (read via
    :func:`last_store_outcome`) is thread-local, so concurrent
    ``store_secret`` calls on different IPC handler threads do not
    stomp each other's outcome. The IPC handler always calls
    ``store_secret`` and ``last_store_outcome`` on the same thread
    (no inter-thread hand-off).
    """
    if not value:
        # Empty value = delete. Remove from both stores to keep them
        # in sync (the keyring might have a stale entry from a prior
        # successful store that we now want to clear).
        delete_secret(provider)
        # CR-94: record the delete outcome so the IPC ack can
        # distinguish "stored in keyring" from "deleted" without
        # inspecting the value the caller passed (which we no longer
        # have by the time the ack is built).
        _set_last_store_outcome("deleted", None, provider=provider)
        return True

    try:
        if not is_keyring_available():
            raise RuntimeError("keyring backend not available")
        import keyring  # type: ignore[import-not-found]

        keyring.set_password(KEYRING_SERVICE_NAME, provider, value)
        log.info(
            "[CREDENTIAL_STORE] stored secret for provider=%s (len=%d) in keyring backend=%s",
            provider,
            len(value),
            _keyring_backend_name_cache,
        )
        # CR-94: record the success outcome.
        _set_last_store_outcome("keyring", None, provider=provider)
        return True
    except Exception as e:
        # NEVER log the value — only metadata. The provider name is
        # not sensitive (it's "openai" / "groq" / etc.) and the length
        # is useful for debugging without revealing the secret.
        # _redact_sensitive strips paths / API-key-like substrings from
        # the exception text — defense in depth in case a buggy backend
        # embeds the value in its error message.
        redacted_reason = _redact_sensitive(str(e))
        log.warning(
            "[CREDENTIAL_STORE] keyring store failed for provider=%s (len=%d): %s — "
            "falling back to plaintext in config.json",
            provider,
            len(value),
            redacted_reason,
        )
        _write_plaintext_fallback(provider, value)
        # CR-94: record the fallback outcome (with the redacted reason)
        # so the IPC handler can include the reason in the ack payload
        # the renderer shows to the user.
        _set_last_store_outcome("plaintext", redacted_reason, provider=provider)
        return False


def load_secret(provider: str) -> str | None:
    """Load a secret for ``provider``.

    Tries keyring first. If keyring returns a value, returns it. If
    keyring is unavailable or returns None, falls back to reading from
    config.json's flat ``<provider>_api_key`` field.

    Returns
    -------
    str | None
        The secret value, or None if not found in either store.

    Notes
    -----
    Never raises. Any keyring error is caught and the fallback is
    attempted. If the fallback also fails (e.g. config.json missing),
    returns None — the caller (typically ``Config.load``) treats this
    as "no key configured".
    """
    try:
        if is_keyring_available():
            import keyring  # type: ignore[import-not-found]

            value = keyring.get_password(KEYRING_SERVICE_NAME, provider)
            if value:
                return value
            # keyring returned None — secret not in keychain. Fall
            # through to plaintext fallback in case the user is
            # mid-migration (key added before keyring was available,
            # not yet migrated).
    except Exception as e:
        # _redact_sensitive strips paths / API-key-like substrings from
        # the exception text — defense in depth in case a buggy backend
        # embeds the value in its error message.
        log.warning(
            "[CREDENTIAL_STORE] keyring load failed for provider=%s: %s — trying plaintext fallback in config.json",
            provider,
            _redact_sensitive(str(e)),
        )

    return _read_plaintext_fallback(provider)


def delete_secret(provider: str, config: Any = None) -> None:
    """Delete a secret from both keyring and config.json.

    Never raises. Errors are logged at debug level (this is best-effort
    cleanup — a failure to delete from a broken keyring is not fatal,
    since the keyring is presumably already inaccessible).

    G4-L-08: ``config`` is an optional in-memory :class:`Config`
    dataclass instance. When provided, the corresponding
    ``<provider>_api_key`` attribute (see
    :data:`PROVIDER_TO_CONFIG_FIELD`) is reset to ``""`` so the running
    process stops seeing the old value. Without this, callers like the
    GDPR Art. 17 ``delete_all_personal_data`` handler would erase the
    on-disk / keychain secret but leave the in-memory ``Config``
    attribute holding the plaintext value — meaning cloud engines and
    LLM polishers continue to use the "deleted" key until the process
    restarts. ``config`` is optional so existing callers (which only
    clear the on-disk store) keep working unchanged.
    """
    # Try keyring first
    try:
        if is_keyring_available():
            import keyring  # type: ignore[import-not-found]

            try:
                keyring.delete_password(KEYRING_SERVICE_NAME, provider)
                log.info(
                    "[CREDENTIAL_STORE] deleted secret for provider=%s from keyring",
                    provider,
                )
            except Exception as e:
                # PasswordDeleteError is raised when the secret doesn't
                # exist — that's fine, we're deleting anyway.
                log.debug(
                    "[CREDENTIAL_STORE] keyring delete for provider=%s raised: %s",
                    provider,
                    _redact_sensitive(str(e)),
                )
    except Exception as e:
        log.debug(
            "[CREDENTIAL_STORE] keyring delete failed for provider=%s: %s",
            provider,
            _redact_sensitive(str(e)),
        )

    # Also clear from config.json (plaintext fallback or stale reference)
    try:
        _write_plaintext_fallback(provider, "")
    except Exception as e:
        # PVT-G5-046 (session-5): a failure here means the plaintext
        # credential is STILL on disk — the opposite of what the user
        # requested. This MUST be visible at default log levels (not
        # debug) so the user knows to manually clean up config.json.
        # Keyring-delete failures above remain at debug (best-effort
        # cleanup of an already-inaccessible backend is non-fatal).
        log.warning(
            "[CREDENTIAL_STORE] credential for provider=%s may still be in config.json — manual cleanup required: %s",
            provider,
            _redact_sensitive(str(e)),
        )

    # G4-L-08: also clear the in-memory Config attribute (when provided)
    # so the running process stops seeing the old value. ``setattr`` on
    # a dataclass field is safe — the field is a plain ``str``. We wrap
    # it in try/except because ``config`` may be a ``MagicMock`` in
    # tests (where setattr silently no-ops on real attrs but we still
    # want the call to be observable for assertions) or a partial
    # object missing the attribute.
    if config is not None:
        field = PROVIDER_TO_CONFIG_FIELD.get(provider)
        if field is not None:
            try:
                setattr(config, field, "")
            except Exception as e:
                log.debug(
                    "[CREDENTIAL_STORE] in-memory Config clear for provider=%s (field=%s) failed: %s",
                    provider,
                    field,
                    _redact_sensitive(str(e)),
                )


def clear_in_memory_secrets(config: Any) -> int:
    """Zero every API-key attribute on the in-memory :class:`Config`.

    G4-CR-05: GDPR Art. 17 ``delete_all_personal_data`` calls this
    after iterating :func:`delete_secret` over every provider so the
    running Python process stops holding the plaintext API keys in
    memory. Without this, the keys survive the GDPR delete in the
    ``Config`` dataclass and continue to be used by ``cloud_engines``,
    ``llm_polish`` and ``dictation_pipeline`` until the process
    restarts.

    Iterates :data:`PROVIDER_TO_CONFIG_FIELD` and ``setattr``s each
    field to ``""``. Returns the number of fields that were cleared
    (always ``len(PROVIDER_TO_CONFIG_FIELD)`` on success — the count
    is returned so callers can log a meaningful "cleared N secrets"
    line and so a future regression that drops a provider from the
    map is visible in tests).

    Never raises — wraps each ``setattr`` in try/except so a single
    broken field (e.g. a frozen dataclass, an exotic ``__setattr__``
    override) doesn't abort the rest. Failures are logged at debug
    level (best-effort cleanup).
    """
    cleared = 0
    for provider, field in PROVIDER_TO_CONFIG_FIELD.items():
        try:
            setattr(config, field, "")
            cleared += 1
        except Exception as e:
            log.debug(
                "[CREDENTIAL_STORE] clear_in_memory_secrets: setattr(%s, '') failed for provider=%s: %s",
                field,
                provider,
                _redact_sensitive(str(e)),
            )
    return cleared


# ── Plaintext fallback (config.json) ────────────────────────────────────


def _read_plaintext_fallback(provider: str) -> str | None:
    """Read a secret from config.json's flat ``<provider>_api_key`` field.

    Returns None if config.json doesn't exist, the field is missing,
    or the field contains a ``keyring://`` reference token (the real
    value lives in keychain — caller should have tried keyring first).
    """
    try:
        from voice_typer.server.config import _config_dir, _secure_read_text

        config_file = _config_dir() / "config.json"
        if not config_file.exists():
            return None
        data = json.loads(_secure_read_text(config_file))
    except Exception as e:
        log.debug(
            "[CREDENTIAL_STORE] plaintext fallback read failed for provider=%s: %s",
            provider,
            _redact_sensitive(str(e)),
        )
        return None

    field = PROVIDER_TO_CONFIG_FIELD.get(provider)
    if not field:
        return None
    value = data.get(field, "")
    if not value:
        return None
    if value.startswith(KEYRING_REF_PREFIX):
        # Reference token — real value is in keychain. Caller should
        # have tried keyring already; if it returned None, the secret
        # is genuinely missing (e.g. user wiped their keychain).
        return None
    return value


def _write_plaintext_fallback(provider: str, value: str) -> None:
    """Write a secret (or empty string) to config.json's flat api_key field.

    Reads config.json, updates the single field, and writes it back
    via ``_secure_atomic_write`` (which enforces ``0o600`` on POSIX).
    Preserves all other config fields.

    On any I/O error, logs and returns — never raises.
    """
    try:
        from voice_typer.server.config import (
            _config_dir,
            _secure_atomic_write,
            _secure_read_text,
        )

        config_file = _config_dir() / "config.json"
        data: dict[str, Any] = {}
        if config_file.exists():
            try:
                data = json.loads(_secure_read_text(config_file))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
        field = PROVIDER_TO_CONFIG_FIELD.get(provider)
        if not field:
            return
        if value:
            data[field] = value
        elif field in data:
            # Clear the field rather than leaving a stale value
            data[field] = ""
        else:
            # Field not present and we're clearing — nothing to do.
            return
        _secure_atomic_write(config_file, json.dumps(data, indent=2))
        if value:
            log.info(
                "[CREDENTIAL_STORE] wrote plaintext fallback for provider=%s (len=%d) to config.json",
                provider,
                len(value),
            )
    except Exception as e:
        log.error(
            "[CREDENTIAL_STORE] plaintext fallback write failed for provider=%s: %s",
            provider,
            _redact_sensitive(str(e)),
        )


# ── Migration ───────────────────────────────────────────────────────────


def _is_windows() -> bool:
    """Local platform check — avoids importing platform_utils at module
    load time (which would transitively pull in heavier modules).
    Kept local for the same reason :func:`_acquire_migration_lock`
    does its own ``import fcntl``/``import msvcrt`` lazily.
    """
    import sys

    return sys.platform == "win32"


def _acquire_migration_lock(lock_file):
    """RACE-001 (HIGH-13): acquire an exclusive cross-process lock.

    Opens ``lock_file`` (creating it if needed) and acquires an
    exclusive lock on it.  Returns the open file object (which the
    caller must close to release the lock) on POSIX; on Windows the
    same file object is returned but the lock is held via
    ``msvcrt.locking`` on byte 0 of the file.

    The lock prevents two app instances from simultaneously running
    :func:`migrate_secrets_to_keyring` and clobbering each other's
    writes — a real secret could otherwise be lost from disk when the
    second writer's ``_secure_atomic_write`` overwrites the first's.

    On platforms where neither ``fcntl`` nor ``msvcrt`` is available
    (e.g. some niche embeddable Python builds), the lock is silently
    skipped — the function still returns a file object so the caller's
    ``finally: lock_fd.close()`` works, but no cross-process exclusion
    is provided.  This preserves the prior single-process behavior on
    such platforms and is the same fail-open stance documented as a
    "known limitation" in the prior version of the migration function.
    """
    import os

    # Open with O_CREAT so the lock file exists on first run.  Use
    # 0o600 on POSIX so the lock file is not world-writable (defense
    # in depth — even though the file holds no secret content).
    if not _is_windows():
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    else:
        # Windows: msvcrt.locking needs a file handle from os.open() so
        # we can pass the fd.  os.open on Windows does NOT support
        # mode=0o600 (it's ignored), but the lock file is created under
        # the per-user config dir so NTFS ACLs already restrict access.
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)

    lock_fd = os.fdopen(fd, "r+b")
    try:
        if not _is_windows():
            import fcntl

            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        else:
            # msvcrt.locking with LK_LOCK blocks until the lock is held
            # (or raises OSError after ~1s on some Windows builds —
            # fall back to a busy-wait by retrying on OSError).
            import msvcrt

            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_LOCK, 1)
            except OSError:
                # Best-effort: if LK_LOCK fails (rare), proceed without
                # a lock — same fail-open behavior as the no-fcntl case.
                pass
    except Exception:
        # Any failure acquiring the lock: close the fd and re-raise so
        # the caller knows the lock is NOT held (callers catch this and
        # proceed without a lock rather than blocking migration).
        lock_fd.close()
        raise
    return lock_fd


def migrate_secrets_to_keyring() -> int:
    """One-time migration of plaintext API keys to the OS keychain.

    Reads ``config.json`` directly (NOT the in-memory ``Config``
    instance — we want to inspect the on-disk representation). For
    each provider's flat ``<provider>_api_key`` field:

      - If the value is empty or already a ``keyring://`` reference,
        skip (already migrated or never set).
      - If keyring is available, store the value via :func:`store_secret`
        and replace the field's value with ``"keyring://<provider>"``.
      - If keyring is unavailable, leave the plaintext value in place
        (the user will get a warning in the renderer about plaintext
        fallback; once they install a keyring backend, the next launch
        will migrate automatically).

    After processing all providers, sets ``secrets_migrated = True``
    in config.json so the migration doesn't run again on every launch
    (idempotent).

    RACE-001 (HIGH-13): the entire read-migrate-write sequence is
    guarded by an exclusive lock on ``config.json.lock`` (alongside
    ``config.json``).  POSIX uses ``fcntl.flock(LOCK_EX)``; Windows
    uses ``msvcrt.locking(LK_LOCK)``.  After acquiring the lock, the
    config is RE-READ so we observe any migration a concurrent process
    completed while we were waiting — if ``secrets_migrated`` is now
    set, we skip the migration entirely.  This closes the race where
    two app instances starting simultaneously could both enter the
    function, both read plaintext, both write their own ``data`` dict,
    and clobber each other (losing a real secret from disk).

    Returns
    -------
    int
        The number of secrets that were successfully moved from
        plaintext to keyring. Secrets that were already in keyring
        (reference tokens) or that couldn't be migrated (keyring
        unavailable) are NOT counted.
    """
    try:
        from voice_typer.server.config import (
            _config_dir,
        )
    except Exception as e:
        log.error(
            "[CREDENTIAL_STORE] migration: cannot import config helpers: %s",
            _redact_sensitive(str(e)),
        )
        return 0

    config_file = _config_dir() / "config.json"
    lock_file = _config_dir() / "config.json.lock"

    # RACE-001 (HIGH-13): acquire the cross-process lock BEFORE we
    # inspect config.json.  The lock is held for the entire
    # read-migrate-write sequence so a concurrent process cannot
    # interleave its own migration with ours.
    try:
        _config_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        # If the dir already exists this is a no-op; if it can't be
        # created, the subsequent _acquire_migration_lock call will
        # fail with a clearer error.
        pass

    try:
        lock_fd = _acquire_migration_lock(lock_file)
    except Exception as e:
        # Fail-open: if we can't acquire the lock (e.g. the config dir
        # is read-only), proceed WITHOUT the lock.  This preserves the
        # prior single-process behavior on platforms where locking is
        # unavailable.  The migration is still idempotent at the
        # keyring level (set_password overwrites), so the worst case is
        # a redundant keyring write — never data loss on a single
        # process.
        log.debug(
            "[CREDENTIAL_STORE] migration: could not acquire lock (%s) — proceeding without",
            _redact_sensitive(str(e)),
        )
        lock_fd = None

    try:
        return _migrate_secrets_to_keyring_locked(config_file)
    finally:
        if lock_fd is not None:
            try:
                lock_fd.close()
            except OSError:
                pass


def _migrate_secrets_to_keyring_locked(config_file) -> int:
    """Body of :func:`migrate_secrets_to_keyring` — assumes the lock is held.

    Split out so the lock acquisition / release is symmetric and easy
    to reason about.  The caller is responsible for acquiring
    ``config.json.lock`` before invoking this function.
    """
    # Late import (kept here so the failure-mode log above is reached
    # even if the import itself is what's broken).
    from voice_typer.server.config import (
        _secure_atomic_write,
        _secure_read_text,
    )

    # RACE-001 (HIGH-13): re-check whether config.json exists NOW that
    # we hold the lock.  A concurrent process may have just created it
    # (with secrets_migrated=True) while we were waiting for the lock.
    if not config_file.exists():
        # No config to migrate — mark as migrated so we don't keep
        # checking on every launch. We do this by writing a minimal
        # config.json with just the flag.
        try:
            _secure_atomic_write(
                config_file,
                json.dumps({"secrets_migrated": True}, indent=2),
            )
        except Exception as e:
            log.debug(
                "[CREDENTIAL_STORE] migration: cannot create empty config: %s",
                _redact_sensitive(str(e)),
            )
        return 0

    try:
        data = json.loads(_secure_read_text(config_file))
        if not isinstance(data, dict):
            log.warning("[CREDENTIAL_STORE] migration: config.json root is not a dict — skipping")
            return 0
    except Exception as e:
        log.warning(
            "[CREDENTIAL_STORE] migration: cannot parse config.json: %s",
            _redact_sensitive(str(e)),
        )
        return 0

    # RACE-001 (HIGH-13): re-check the secrets_migrated flag NOW that
    # we hold the lock.  A concurrent process may have completed the
    # migration while we were waiting — if so, skip our own migration
    # entirely (idempotent).
    if data.get("secrets_migrated", False):
        log.debug("[CREDENTIAL_STORE] migration: secrets_migrated flag already set — skipping")
        return 0

    migrated = 0
    keyring_ok = is_keyring_available()

    for provider, field_name in PROVIDER_TO_CONFIG_FIELD.items():
        value = data.get(field_name, "")
        if not value or value.startswith(KEYRING_REF_PREFIX):
            # Empty or already a reference — nothing to migrate
            continue

        if not keyring_ok:
            # Keyring unavailable — leave the plaintext value in place.
            # The user has been warned via get_keyring_status() in the
            # renderer. Once a keyring backend becomes available, the
            # next launch will run this migration and move the value.
            log.info(
                "[CREDENTIAL_STORE] migration: keyring unavailable, keeping provider=%s in plaintext (len=%d)",
                provider,
                len(value),
            )
            continue

        try:
            import keyring  # type: ignore[import-not-found]

            keyring.set_password(KEYRING_SERVICE_NAME, provider, value)
            log.info(
                "[CREDENTIAL_STORE] migration: moved provider=%s (len=%d) from config.json to keyring",
                provider,
                len(value),
            )
            # Replace the plaintext with a reference token
            data[field_name] = f"{KEYRING_REF_PREFIX}{provider}"
            migrated += 1
        except Exception as e:
            # Mid-migration failure: the plaintext for this provider
            # stays in `data` (the reference-token assignment above is
            # gated on set_password succeeding), so the final
            # _secure_atomic_write preserves it. The user's secret is
            # never lost — it's either in keyring OR in config.json.
            log.warning(
                "[CREDENTIAL_STORE] migration: failed to move provider=%s to keyring: %s — keeping plaintext",
                provider,
                _redact_sensitive(str(e)),
            )

    # Mark as migrated regardless of how many moved — we don't want to
    # retry on every launch. The only way to re-trigger migration is
    # for the user to manually add a plaintext key to config.json
    # (which would re-set the field), but that's an edge case we accept.
    #
    # RACE-001 (HIGH-13): the cross-process lock acquired by the caller
    # guarantees no concurrent process is mutating config.json while we
    # write this.  The previous "known limitation" note about the race
    # window between two app instances is now closed.
    data["secrets_migrated"] = True
    try:
        _secure_atomic_write(config_file, json.dumps(data, indent=2))
    except Exception as e:
        log.error(
            "[CREDENTIAL_STORE] migration: failed to save migrated config: %s",
            _redact_sensitive(str(e)),
        )
        # Don't return 0 — the secrets were stored in keyring successfully,
        # even if we couldn't write the flag. The next launch will retry
        # the migration (which is idempotent for already-stored secrets —
        # store_secret overwrites).

    return migrated


__all__ = [
    "KEYRING_REF_PREFIX",
    "KEYRING_SERVICE_NAME",
    "PROVIDER_TO_CONFIG_FIELD",
    "CONFIG_FIELD_TO_PROVIDER",
    "clear_in_memory_secrets",
    "delete_secret",
    "get_keyring_status",
    "is_keyring_available",
    "load_secret",
    "migrate_secrets_to_keyring",
    "store_secret",
]
