"""the bool-rejecting history bounders."""

from __future__ import annotations

from voice_typer.server.ipc.history_bounds import (
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _bound_history_limit,
    _bound_history_offset,
)
from voice_typer.server.ipc.validation import _validate_dict_payload


class TestRejectBoolRule:
    """The opt-in ``reject_bool`` rule rejects bool values for fields"""

    def test_reject_bool_true_rejects_bool_value_for_int_tuple_type(self):
        """``type: (int, str)`` → rejected with ``invalid_field``."""
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
                "reject_bool": True,
            }
        }
        validated, err = _validate_dict_payload({"limit": True}, schema)
        assert validated is None
        assert err is not None
        assert err["type"] == "error"
        assert err["data"]["code"] == "client.invalid_field"
        assert "legacy_code" not in err["data"]
        assert err["data"]["field"] == "limit"
        assert "bool" in err["data"]["message"]
        assert "subclass of int" in err["data"]["message"]

    def test_reject_bool_true_rejects_false_value(self):
        """``{\"limit\": False}`` is also rejected (False is a bool)."""
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
                "reject_bool": True,
            }
        }
        validated, err = _validate_dict_payload({"limit": False}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "limit"

    def test_reject_bool_true_rejects_bool_for_bare_int_type(self):
        """``type: int`` → rejected."""
        schema = {"count": {"type": int, "required": True, "reject_bool": True}}
        validated, err = _validate_dict_payload({"count": True}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "count"

    def test_reject_bool_default_is_false_backward_compat(self):
        """
        Schemas that don't set ``reject_bool`` still accept bool
        This is the legacy behavior pinned by
        """
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
            }
        }
        validated, err = _validate_dict_payload({"limit": True}, schema)
        assert err is None
        assert validated is not None
        assert validated["limit"] is True

    def test_reject_bool_false_explicit_still_accepts_bool(self):
        """Setting ``reject_bool: False`` explicitly is the same as"""
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
                "reject_bool": False,
            }
        }
        validated, err = _validate_dict_payload({"limit": True}, schema)
        assert err is None
        assert validated is not None
        assert validated["limit"] is True

    def test_reject_bool_no_op_when_bool_in_declared_type_tuple(self):
        """When ``bool`` is explicitly in the declared type tuple,"""
        schema = {
            "flag": {
                "type": (bool, int),
                "required": True,
                "reject_bool": True,
            }
        }
        validated, err = _validate_dict_payload({"flag": True}, schema)
        assert err is None
        assert validated is not None
        assert validated["flag"] is True

    def test_reject_bool_no_op_when_declared_type_is_bool(self):
        """When the declared type IS ``bool``, the rule is a no-op —"""
        schema = {
            "paused": {
                "type": bool,
                "required": True,
                "reject_bool": True,
            }
        }
        validated, err = _validate_dict_payload({"paused": True}, schema)
        assert err is None
        assert validated is not None
        assert validated["paused"] is True

    def test_reject_bool_true_accepts_non_bool_values(self):
        """The rule ONLY rejects bools, ints, floats, strings still"""
        schema = {
            "limit": {
                "type": (int, str),
                "required": True,
                "reject_bool": True,
            }
        }
        for ok_value in (10, 0, -5, 1000, "25", "0"):
            validated, err = _validate_dict_payload({"limit": ok_value}, schema)
            assert err is None, f"unexpected error for value={ok_value!r}: {err}"
            assert validated is not None
            assert validated["limit"] == ok_value

    def test_reject_bool_with_clamp_range_still_rejects_bool(self):
        """clamp range. Without the rule, ``clamp_range`` already skips"""
        schema = {
            "level": {
                "type": int,
                "required": True,
                "clamp_range": (0, 100),
                "reject_bool": True,
            }
        }
        validated, err = _validate_dict_payload({"level": True}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "level"

    def test_reject_bool_message_includes_expected_type_name(self):
        """The error message must include the declared type name so"""
        schema = {
            "limit": {
                "type": (int, str),
                "required": True,
                "reject_bool": True,
            }
        }
        _, err = _validate_dict_payload({"limit": True}, schema)
        assert err is not None
        msg = err["data"]["message"]
        assert "int|str" in msg, f"expected 'int|str' in message: {msg!r}"

    def test_reject_bool_with_no_declared_type_still_rejects_bool(self):
        """When the schema omits ``type`` but sets ``reject_bool=True``,"""
        schema = {"value": {"required": True, "reject_bool": True}}
        validated, err = _validate_dict_payload({"value": True}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "value"


class TestBoundHistoryLimitRejectsBool:
    """bool, rather than silently coercing ``True`` → 1 or ``False`` → 1"""

    def test_true_returns_default_not_one(self):
        """``_bound_history_limit(True)`` returns ``_HISTORY_LIMIT_DEFAULT``"""
        assert _bound_history_limit(True) == _HISTORY_LIMIT_DEFAULT
        assert _bound_history_limit(True) != 1

    def test_false_returns_default_not_one(self):
        """``_bound_history_limit(False)`` returns the default, NOT 1."""
        assert _bound_history_limit(False) == _HISTORY_LIMIT_DEFAULT

    def test_integer_values_unchanged(self):
        """``[1, _HISTORY_LIMIT_MAX]`` exactly as before."""
        assert _bound_history_limit(0) == 1
        assert _bound_history_limit(1) == 1
        assert _bound_history_limit(50) == 50
        assert _bound_history_limit(500) == 500
        assert _bound_history_limit(1_000_000) == _HISTORY_LIMIT_MAX
        assert _bound_history_limit(-5) == 1

    def test_numeric_string_values_unchanged(self):
        """renderer's form inputs) continue to be coerced via ``int()``."""
        assert _bound_history_limit("25") == 25
        assert _bound_history_limit("0") == 1
        assert _bound_history_limit("999999") == _HISTORY_LIMIT_MAX

    def test_none_returns_default(self):
        """Regression guard: ``None`` returns the default."""
        assert _bound_history_limit(None) == _HISTORY_LIMIT_DEFAULT

    def test_garbage_string_returns_default(self):
        """Regression guard: a non-numeric string returns the default."""
        assert _bound_history_limit("not-a-number") == _HISTORY_LIMIT_DEFAULT


class TestBoundHistoryOffsetRejectsBool:
    """``_bound_history_offset`` falls back to ``0`` when given a bool,"""

    def test_true_returns_zero_not_one(self):
        """``_bound_history_offset(True)`` returns ``0``, NOT 1."""
        assert _bound_history_offset(True) == 0
        assert _bound_history_offset(True) != 1

    def test_false_returns_zero(self):
        """``_bound_history_offset(False)`` returns ``0`` (same as"""
        assert _bound_history_offset(False) == 0

    def test_integer_values_unchanged(self):
        """Regression guard: integer inputs are NOT affected by the"""
        assert _bound_history_offset(0) == 0
        assert _bound_history_offset(1) == 1
        assert _bound_history_offset(100) == 100
        assert _bound_history_offset(-5) == 0

    def test_none_returns_zero(self):
        """Regression guard: ``None`` returns 0."""
        assert _bound_history_offset(None) == 0


class TestValidationAndBounderComposition:
    """validation layer rejects the bool BEFORE the bounder is called —"""

    def test_strict_schema_rejects_bool_at_validation_layer(self):
        """A history-style schema with ``reject_bool=True`` rejects"""
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
                "reject_bool": True,
            },
            "offset": {
                "type": (int, str),
                "required": False,
                "default": 0,
                "reject_bool": True,
            },
        }
        validated, err = _validate_dict_payload({"limit": True, "offset": False}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "limit"

    def test_legacy_schema_accepts_bool_bounder_uses_default(self):
        """A legacy schema (no ``reject_bool``) accepts ``{\"limit\":"""
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
            }
        }
        validated, err = _validate_dict_payload({"limit": True}, schema)
        assert err is None
        assert validated is not None
        assert validated["limit"] is True  # validation passed through the bool

        clamped = _bound_history_limit(validated["limit"])
        assert clamped == _HISTORY_LIMIT_DEFAULT
        assert clamped != 1  # NOT silently coerced to 1
