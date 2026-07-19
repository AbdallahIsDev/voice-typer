# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""Shared IPC payload validation helper.

PR-3-FINDING-3: validates an IPC ``data`` argument against a declarative
schema.  Returns ``(validated_dict, None)`` on success, or
``(None, error_response_dict)`` on validation failure so the handler
can ``return resp`` immediately.

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
"""


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


__all__ = ["_validate_dict_payload"]
