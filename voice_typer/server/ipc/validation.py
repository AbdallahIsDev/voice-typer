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
ERROR_CODES: frozenset[str] = frozenset(
    {
        # Client-originated errors (4xx analog).
        "client.invalid_field",
        "client.missing_field",
        "client.invalid_payload",
        "client.rate_limited",
        "client.path_not_allowed",
        "client.not_found",
        "client.auth_failed",
        # Server-originated errors (5xx analog).
        "server.internal_error",
        "server.handler_error",
        "server.file_locked",
        "server.model_switch_failed",
        "server.shutting_down",
        "server.unknown_command",
        "server.unknown_tray_item",
    }
)


def _validate_dict_payload(data, schema):
    """Validate IPC ``data`` against a declarative *schema*.

    Parameters
    ----------
    data : Any
        The ``data`` field from the IPC message.
    schema : dict[str, dict]
        Mapping of field name → validation rules.  Each rule dict
        supports:

        - ``type`` (required): the expected Python type (e.g. ``str``,
          ``list``).
        - ``required`` (bool): if ``True``, the field MUST be present
          in ``data``.  Mutually exclusive with ``default``.
        - ``default``: default value when the field is absent.  Only
          valid when ``required=False``.
        - ``max_value_len`` (int, optional): if the value is a string
          longer than N characters, return an ``invalid_field`` error.
          R4-F5: replaces the ad-hoc per-value length loops in
          ``save_vocabulary`` and ``show_electron_notification``.
        - ``clamp_range`` (tuple ``(lo, hi)``, optional): if the
          value is a number, coerce it to ``max(lo, min(value, hi))``
          before storing it in ``validated``.  R4-F5: replaces the
          inline ``max(0, min(int(duration_ms), 24*60*60*1000))`` in
          ``show_electron_notification``.
        - ``max_payload_bytes`` (int, optional): if the WHOLE
          ``data`` dict serializes to more than N bytes, return an
          ``invalid_payload`` error.  R4-F5: replaces the inline
          1 MB cap in ``save_vocabulary``.  Note: this rule is keyed
          off any field but applies to the WHOLE payload — it's
          checked ONCE before the per-field loop, so it should be
          specified on at most one field (the helper checks the
          first field that declares it).

    Returns
    -------
    tuple[dict | None, dict | None]
        ``(validated_dict, None)`` on success.
        ``(None, error_response)`` on failure — the error_response
        is a dict ready to be returned as ``resp`` from the handler.
    """
    if not isinstance(data, dict):
        return None, {
            "type": "error",
            "data": {
                "code": "invalid_payload",
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
                return None, {
                    "type": "error",
                    "data": {
                        "code": "invalid_payload",
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
                    "type": "error",
                    "data": {
                        "code": "invalid_field",
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
                    "type": "error",
                    "data": {
                        "code": "invalid_field",
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
                "type": "error",
                "data": {
                    "code": "missing_field",
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


__all__ = ["_validate_dict_payload", "_error_response", "ERROR_CODES"]
