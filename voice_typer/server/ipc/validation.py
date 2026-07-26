# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""Shared IPC payload validation + error-envelope helpers.

PR-3-FINDING-3: validates an IPC ``data`` argument against a declarative
schema.  Returns ``(validated_dict, None)`` on success, or
``(None, error_response_dict)`` on validation failure so the handler
can ``return resp`` immediately.

R13-F3 (IMPROVE-mode run, 2026-07-19): added :func:`_error_response`
to standardize the error envelope produced by handler-level
``except Exception`` catch-alls.  Pre-R13-F3 each handler built its
own ``resp["data"] = {"message": str(e)}`` ad-hoc, omitting the
``code`` field that the TCP/WS dispatch paths (and the validation
helper) include in every other error envelope.  Clients branching on
``code`` (e.g. the renderer's toast-dispatch logic) silently fell
through to a generic "unknown error" path for handler exceptions.
The helper stamps ``code: "handler_error"`` and a sanitized message
(the full exception is logged server-side at ERROR with
``exc_info=True`` by the caller).

Schema format::

    schema = {
        "field_name": {
            "type": str,          # required: the expected Python type
            "required": True,     # field MUST be present in data
            "default": "val",    # optional default (only for
                                  #   required=False)
        }
    }

Example::

    validated, error = _validate_dict_payload(data, {
        "hotkey": {"type": str, "required": True},
        "model": {"type": str, "required": False, "default": "small.en"},
    })
    if error:
        return error

R4-F5 (IMPROVE-mode run, 2026-07-19): the schema now supports three
optional rules that the previous inline checks in
``save_vocabulary`` / ``show_electron_notification`` reimplemented
ad-hoc:

- ``max_value_len`` (int): reject string values longer than N chars.
- ``max_payload_bytes`` (int): reject the whole payload if its
  ``json.dumps`` size exceeds N bytes (DoS guard).
- ``clamp_range`` (tuple ``(lo, hi)``): coerce a numeric value to
  ``max(lo, min(value, hi))`` instead of rejecting out-of-range
  values (used by ``duration_ms`` in ``show_electron_notification``).
"""

# ``TypedDict`` is needed for the schema + error-envelope type
# contracts declared below. Imported at module load (not under
# ``TYPE_CHECKING``) so the TypedDicts are real runtime classes that
# introspection tests can reference.
from typing import TypedDict

# G4-M-22: canonical namespaced error-code registry.
#
# Every ``code`` field stamped on an IPC error envelope SHOULD come
# from this set. The registry is the single source of truth so the
# renderer's ``usePython.ts`` switch statement can be generated /
# audited against it (see ``tests/test_error_codes_registry.py`` for
# the contract test that asserts every emitted ``"code": "..."``
# literal in the server tree is either registered here or is a
# documented legacy alias).
#
# Naming convention:
# - ``client.*`` — the request was malformed / invalid / unauthorized.
#   The renderer can fix the request and retry (e.g. highlight the
#   invalid field, prompt the user for the missing value).
# - ``server.*`` — the server could not process a well-formed request.
#   The renderer surfaces a generic "something went wrong" message and
#   logs the detail for support.
#
# Legacy non-namespaced aliases (``internal_error``, ``shutting_down``,
# ``unknown_command``, ``unknown_tray_item``, ``auth_failed``,
# ``rate_limited``, ``invalid_payload``, ``invalid_field``,
# ``missing_field``, ``model_switch_failed``, ``payload_too_large``)
# are still emitted by some paths for backward compat — new code MUST
# use the namespaced form. The renderer must accept both forms (treat
# the legacy form as an alias). The contract test in
# ``tests/test_error_codes_registry.py`` is the regression guard.
#
# DE-36 (2026-10): the validation helper was migrated to emit the
# namespaced form (client.invalid_payload / client.invalid_field /
# client.missing_field) as the primary code field. The legacy bare
# form is preserved in a sibling legacy_code field on the same error
# envelope for one release cycle so the renderer (and any tests still
# asserting the old form) can switch to the namespaced form without a
# hard cutover. The three literals listed above are STILL emitted (as
# legacy_code values), so the contract test's LEGACY_ALIASES registry
# remains accurate. Drop the legacy_code field once the renderer
# migrates.
ERROR_CODES: frozenset[str] = frozenset(
    {
        # Client-originated errors (4xx analog).
        "client.invalid_field",
        "client.missing_field",
        "client.invalid_payload",
        "client.payload_too_large",
        "client.rate_limited",
        "client.path_not_allowed",
        "client.not_found",
        "client.auth_failed",
        # Structured consent error — the renderer surfaces a consent
        # dialog (deep-linked to the exact toggle in Settings via the
        # structured ``engine_name`` / ``consent_field`` fields)
        # instead of a generic error toast. Emitted by
        # ``handlers/_base.py`` when a cloud/LLM engine requires
        # biometric-data consent that the user has not yet granted.
        "client.consent_required",
        # Server-originated errors (5xx analog).
        "server.internal_error",
        "server.handler_error",
        "server.file_locked",
        "server.model_switch_failed",
        "server.shutting_down",
        "server.unknown_command",
        "server.unknown_tray_item",
        "server.not_found",
        # DE-31: structured consent-required envelope emitted by the
        # IPC dispatcher when a ``ConsentRequiredError`` is raised by
        # a cloud/LLM handler. Distinct from ``client.consent_required``
        # above (which is emitted by ``handlers/_base.py``); this form
        # is the dispatcher-level wrapper around the typed exception
        # and carries ``provider`` + ``scope`` fields so the renderer
        # can deep-link to the exact Settings toggle.
        "server.consent_required",
        # Typed cloud/LLM exception hierarchy — distinct codes for
        # each cloud error category so the renderer can distinguish
        # "API key invalid" (user must re-enter) from "rate limited"
        # (backoff) from "transient network" (auto-retry) from "missing
        # config" (open Settings). See ``voice_typer/server/asr_errors.py``
        # for the typed exception classes and
        # ``voice_typer/server/handlers/_base.py`` for the isinstance
        # mapping.
        "server.cloud_auth_failed",
        "server.cloud_rate_limited",
        "server.cloud_server_error",
        "server.cloud_network_error",
        "server.cloud_config_error",
        "server.cloud_engine_error",
    }
)

# EC-10: legacy non-namespaced aliases still emitted by some paths for
# backward compat (TCP ``shutting_down``, dispatcher ``internal_error``,
# handler ``handler_error``, etc.). New emitters MUST use the namespaced
# form above. This registry is the single source of truth for the
# renderer's ``ErrorCodes`` union (see
# ``voice_typer/client/src/renderer/src/types/ipc.ts``) and for the
# contract test in ``tests/test_error_codes_registry.py``. Keeping the
# legacy set explicit (instead of an open-ended ``str``) lets us audit
# which aliases are still emitted and remove them once the renderer
# migrates fully to the namespaced form.
LEGACY_ERROR_CODES: frozenset[str] = frozenset(
    {
        "internal_error",
        "shutting_down",
        "unknown_command",
        "unknown_tray_item",
        "auth_failed",
        "rate_limited",
        "invalid_payload",
        "invalid_field",
        "missing_field",
        "model_switch_failed",
        "payload_too_large",
        "handler_error",
        "not_initialized",
        "disallowed_command",
        "disallowed_window",
        "sidecar_disconnected",
    }
)

# EC-10: convenience union for validation / contract tests. Every
# ``code`` value emitted on the wire MUST be in this set (the contract
# test asserts this). Use ``ALL_ERROR_CODES`` for membership checks;
# prefer ``ERROR_CODES`` for new emitters.
ALL_ERROR_CODES: frozenset[str] = ERROR_CODES | LEGACY_ERROR_CODES


# Typed schema for the declarative validation rule dict consumed
# by :func:`_validate_dict_payload`. ``total=False`` because every rule
# key is optional except ``type`` (and even ``type`` may be omitted in
# the rare case where a field is being declared only for ``default`` /
# ``clamp_range`` / ``max_value_len`` purposes). The loose ``object``
# value types preserve the prior ``Any``-style flexibility — call
# sites build these dicts inline at every handler, and tightening the
# value types further would require touching all 12+ schemas.


class FieldRule(TypedDict, total=False):
    """Declarative per-field validation rule for :func:`_validate_dict_payload`.

    See the module docstring of :func:`_validate_dict_payload` for the
    full semantics of each key. All keys are optional — the helper
    reads each via ``rules.get(<key>)`` and treats absence as "rule
    not applied".
    """

    type: type | tuple[type, ...]
    required: bool
    default: object
    max_value_len: int
    clamp_range: tuple[int | float, int | float]
    max_payload_bytes: int


# A schema is a mapping from field name to its rule dict. Used as
# the second parameter to :func:`_validate_dict_payload`.
Schema = dict[str, FieldRule]


# Typed contract for the IPC error envelope. The TS side has a
# matching ``ErrorEvent`` interface (``ipc.ts:119-152``); these
# TypedDicts are the Python-side mirror so the ad-hoc dict literals
# constructed at the 6+ emitter sites have a documented shape.
#
# ``ErrorData`` is ``total=False`` because emitters selectively
# include only the keys relevant to the specific error code (e.g.
# ``legacy_code`` is only present during the one-release-cycle
# migration window; ``field`` is only present for ``invalid_field`` /
# ``missing_field``; ``command`` is only present for
# ``unknown_command``).
class ErrorData(TypedDict, total=False):
    code: str
    legacy_code: str
    message: str
    field: str
    command: str
    id: str | int


class _ErrorEnvelopeRequired(TypedDict):
    """Required keys on every error envelope.

    Split out so :class:`ErrorEnvelope` can extend it with ``id`` as
    an optional key (ad-hoc emitters only set ``id`` when a request id
    is available to echo back).
    """

    type: str  # always ``"error"`` for an error envelope
    data: ErrorData


class ErrorEnvelope(_ErrorEnvelopeRequired, total=False):
    """Canonical IPC error envelope.

    Required keys: ``type`` (``"error"``), ``data`` (an
    :class:`ErrorData` mapping). Optional key: ``id`` (echoed request
    id when available). This is the documented *contract* for every
    error envelope constructed in the IPC layer; ad-hoc dict literals
    at the construction sites are not type-checked against this
    TypedDict (the contract is documentation, not runtime
    enforcement — the return-type annotation on
    :func:`_validate_dict_payload` is plain ``dict[str, object]`` rather
    than :class:`ErrorEnvelope` because TypedDicts are invariant and
    not subtypes of ``dict``, so annotating the return as
    :class:`ErrorEnvelope` would flag every caller that returns the
    error directly from a ``-> dict | None`` handler. The contract is
    documented at construction sites via the
    ``# ErrorEnvelope contract — see validation.py`` comments and
    verified by ``tests/test_error_codes_registry.py``).
    """

    id: object | None


def _validate_dict_payload(data: object, schema: Schema) -> tuple[dict[str, object] | None, "dict[str, object] | None"]:
    """Validate IPC ``data`` against a declarative *schema*.

    Parameters
    ----------
    data :
        The ``data`` field from the IPC message.
    schema :
        Mapping of field name → validation rules.  Each rule dict
        supports:

        - ``type`` (required): the expected Python type (e.g. ``str``,
          ``list``).
        - ``required`` (bool): if ``True``, the field MUST be present
          in ``data``.  Mutually exclusive with ``default``.
        - ``default``: default value when the field is absent.  Only
          valid when ``required=False``.
        - ``max_value_len`` (int, optional): if the value is a string
          longer than N characters, return an ``client.invalid_field``
          error. R4-F5: replaces the ad-hoc per-value length loops in
          ``save_vocabulary`` and ``show_electron_notification``.
        - ``clamp_range`` (tuple ``(lo, hi)``, optional): if the
          value is a number, coerce it to ``max(lo, min(value, hi))``
          before storing it in ``validated``.  R4-F5: replaces the
          inline ``max(0, min(int(duration_ms), 24*60*60*1000))`` in
          ``show_electron_notification``.
        - ``max_payload_bytes`` (int, optional): if the WHOLE
          ``data`` dict serializes to more than N bytes, return an
          ``client.invalid_payload`` error. R4-F5: replaces the inline
          1 MB cap in ``save_vocabulary``.  Note: this rule is keyed
          off any field but applies to the WHOLE payload — it's
          checked ONCE before the per-field loop, so it should be
          specified on at most one field (the helper checks the
          first field that declares it).

    Returns
    -------
    tuple[dict[str, object] | None, dict[str, object] | None]
        ``(validated_dict, None)`` on success.
        ``(None, error_response)`` on failure — the error_response
        is a dict ready to be returned as ``resp`` from the handler.
        The error_response dict conforms to the :class:`ErrorEnvelope`
        contract (``{"type": "error", "data": {"code": ..., ...}}``);
        the return type is plain ``dict[str, object]`` (not
        :class:`ErrorEnvelope`) because TypedDicts are invariant and
        not subtypes of ``dict``, so annotating the return as
        :class:`ErrorEnvelope` would flag every caller that returns
        the error directly from a ``-> dict | None`` handler. The
        contract is documented at construction sites via the
        ``# ErrorEnvelope contract — see validation.py`` comments and
        verified by ``tests/test_error_codes_registry.py``.
    """
    if not isinstance(data, dict):
        # ErrorEnvelope contract — see validation.py
        return None, {
            "type": "error",
            "data": {
                # DE-36: emit the namespaced ``client.invalid_payload``
                # as the primary ``code`` (per G4-M-22). The legacy
                # bare ``invalid_payload`` is preserved in
                # ``legacy_code`` for one release cycle so the renderer
                # (and any tests still asserting the old form) can
                # switch to the namespaced form without a hard cutover.
                # Drop ``legacy_code`` once the renderer migrates.
                "code": "client.invalid_payload",
                "legacy_code": "invalid_payload",
                "message": "data must be an object",
            },
        }

    # R4-F5: ``max_payload_bytes`` is a whole-payload rule. Scan the
    # schema for the first field that declares it and enforce the cap
    # before the per-field loop. The previous ``save_vocabulary`` impl
    # did ``len(json.dumps(data))`` inline; centralizing here means
    # every handler that opts into the rule gets the same DoS guard
    # without re-implementing the size check.
    import json as _json_mod

    for _field, _rules in schema.items():
        max_bytes = _rules.get("max_payload_bytes")
        if max_bytes is not None:
            payload_size = len(_json_mod.dumps(data))
            if payload_size > max_bytes:
                # ErrorEnvelope contract — see validation.py
                return None, {
                    "type": "error",
                    "data": {
                        # DE-36: namespaced form (primary) + legacy
                        # alias (one-release-cycle compat).
                        "code": "client.invalid_payload",
                        "legacy_code": "invalid_payload",
                        "message": (f"payload too large ({payload_size} bytes; max {max_bytes})"),
                    },
                }
            # Only check once — break after the first field that
            # declares the rule, regardless of whether it tripped.
            break

    validated = {}
    for field_name, rules in schema.items():
        if field_name in data:
            value = data[field_name]
            expected_type = rules.get("type")
            if expected_type is not None and not isinstance(value, expected_type):
                # IPC-3: format the expected-type name for the error
                # message.  ``expected_type`` may be a single type
                # (``str``) or a tuple of types (``(str, type(None))``)
                # — the latter is the standard ``isinstance`` idiom for
                # "any of these types".  A tuple has no ``__name__``,
                # so format the names of all the allowed types and
                # join them with ``|`` (e.g. ``"str|NoneType"``).
                if isinstance(expected_type, tuple):
                    expected_name = "|".join(t.__name__ for t in expected_type)
                else:
                    expected_name = expected_type.__name__
                return None, {
                    # ErrorEnvelope contract — see validation.py
                    "type": "error",
                    "data": {
                        # DE-36: namespaced form (primary) + legacy
                        # alias (one-release-cycle compat).
                        "code": "client.invalid_field",
                        "legacy_code": "invalid_field",
                        "field": field_name,
                        "message": f"'{field_name}' must be of type {expected_name}, got {type(value).__name__}",
                    },
                }
            # R4-F5: per-value length cap. Only applies to string
            # values; non-string values pass through (the type check
            # above already rejected wrong-type values).
            max_value_len = rules.get("max_value_len")
            if max_value_len is not None and isinstance(value, str) and len(value) > max_value_len:
                return None, {
                    # ErrorEnvelope contract — see validation.py
                    "type": "error",
                    "data": {
                        # DE-36: namespaced form (primary) + legacy
                        # alias (one-release-cycle compat).
                        "code": "client.invalid_field",
                        "legacy_code": "invalid_field",
                        "field": field_name,
                        "message": (f"'{field_name}' value too long ({len(value)} > {max_value_len})"),
                    },
                }
            # R4-F5: clamp_range. Coerce numeric values into [lo, hi].
            # Booleans are a subclass of int — skip them so
            # ``critical: True`` isn't accidentally coerced to 1.
            clamp_range = rules.get("clamp_range")
            if clamp_range is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
                lo, hi = clamp_range
                value = max(lo, min(value, hi))
            validated[field_name] = value
        elif rules.get("required", False):
            return None, {
                # ErrorEnvelope contract — see validation.py
                "type": "error",
                "data": {
                    # DE-36: namespaced form (primary) + legacy alias
                    # (one-release-cycle compat).
                    "code": "client.missing_field",
                    "legacy_code": "missing_field",
                    "field": field_name,
                    "message": f"Missing required field '{field_name}'",
                },
            }
        elif "default" in rules:
            validated[field_name] = rules["default"]

    return validated, None


def _error_response(resp: dict, message: str, *, code: str = "server.handler_error") -> dict:
    """Stamp an error envelope on ``resp`` and return it.

    R13-F3: standardizes the catch-all ``except Exception`` envelope
    produced by handler mixins. Pre-R13-F3 each handler did::

        except Exception as e:
            log.error("[IPC] <cmd> failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    The ad-hoc envelope omitted the ``code`` field that every other
    error path (validation, dispatch safety net, rate limiter) sets.
    Clients branching on ``code`` silently fell through to a generic
    "unknown error" path for handler exceptions. The helper stamps
    ``code: "server.handler_error"`` (G4-M-22 namespaced form; was
    ``"handler_error"`` pre-G4-M-22) and a sanitized message (the caller is
    responsible for logging the full exception server-side at ERROR
    with ``exc_info=True``).

    Parameters
    ----------
    resp : dict
        The response dict pre-populated by ``_dispatch`` (carries the
        request ``id``). Mutated in place.
    message : str
        The client-facing message. Should be sanitized (no Python
        internals, no PII). The caller decides what's safe to expose.
    code : str, optional
        The error code. Defaults to ``"server.handler_error"`` (G4-M-22 namespaced form; was
        ``"handler_error"`` pre-G4-M-22) — the standard
        for an unexpected exception caught by a handler's catch-all.
        Override for known-error paths that still want the helper's
        envelope shape (e.g. ``"not_initialized"``).

    Returns
    -------
    dict
        The same ``resp`` dict, mutated to be an error envelope.
    """
    resp["type"] = "error"
    resp["data"] = {"code": code, "message": message}
    return resp


__all__ = [
    "_validate_dict_payload",
    "_error_response",
    "ERROR_CODES",
    "LEGACY_ERROR_CODES",
    "ALL_ERROR_CODES",
    # Typed contract exports.
    "FieldRule",
    "Schema",
    "ErrorData",
    "ErrorEnvelope",
]
