"""Focused tests for the opt-in ``reject_bool`` validation rule and
the bool-rejecting history bounders.

Covers the bool-as-int type confusion gap documented in
``tests/handlers/test_ipc_validation_coverage.py``::

    test_bool_limit_accepted_due_to_int_subclass

which pins the legacy behavior (``{"limit": True}`` is silently
accepted and coerced to ``limit=1`` via ``int(True)``). This file
documents the new opt-in ``reject_bool`` rule added to
:func:`voice_typer.server.ipc.validation._validate_dict_payload`
and the defense-in-depth bool rejection added to the history
bounders (:func:`voice_typer.server.ipc.history_bounds._bound_history_limit`
and :func:`voice_typer.server.ipc.history_bounds._bound_history_offset`).

The opt-in rule is backward compatible: existing schemas that don't
set ``reject_bool=True`` continue to accept bool values exactly as
before (so the existing ``test_bool_limit_accepted_due_to_int_subclass``
contract pin still passes). Schemas that want strict bool rejection
(e.g. numeric pagination fields where a bool is never meaningful)
set ``reject_bool=True`` explicitly.
"""

from __future__ import annotations

from voice_typer.server.ipc.history_bounds import (
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _bound_history_limit,
    _bound_history_offset,
)
from voice_typer.server.ipc.validation import _validate_dict_payload

# ══════════════════════════════════════════════════════════════════════════
# _validate_dict_payload — opt-in reject_bool rule
# ══════════════════════════════════════════════════════════════════════════


class TestRejectBoolRule:
    """The opt-in ``reject_bool`` rule rejects bool values for fields
    whose declared type would otherwise accept them via the
    bool-subclasses-int loophole."""

    def test_reject_bool_true_rejects_bool_value_for_int_tuple_type(self):
        """``{"limit": True}`` with ``reject_bool=True`` and
        ``type: (int, str)`` → rejected with ``invalid_field``.

        Pre-rule, ``isinstance(True, (int, str))`` is ``True`` (bool
        subclasses int), so the bool silently passes the type check
        and is later coerced to ``1`` by ``int(True)``. The opt-in
        rule closes the loophole.
        """
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
        # ``legacy_code`` was REMOVED when the renderer migrated fully
        # to the namespaced ``code`` form. The wire contract is now
        # ``data.code`` ONLY.
        assert "legacy_code" not in err["data"]
        assert err["data"]["field"] == "limit"
        # the message must call out the bool/int subclass relationship
        # so the caller (renderer dev) understands WHY the value was
        # rejected — without this hint, the error is confusing because
        # ``True`` looks like an ``int`` to a Python-unaware caller.
        assert "bool" in err["data"]["message"]
        assert "subclass of int" in err["data"]["message"]

    def test_reject_bool_true_rejects_false_value(self):
        """``{"limit": False}`` is also rejected (False is a bool)."""
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
        """``{"count": True}`` with ``reject_bool=True`` and
        ``type: int`` → rejected.

        ``isinstance(True, int)`` is ``True`` so the bare-int type
        check passes; the rule catches the bool regardless.
        """
        schema = {"count": {"type": int, "required": True, "reject_bool": True}}
        validated, err = _validate_dict_payload({"count": True}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "count"

    def test_reject_bool_default_is_false_backward_compat(self):
        """Schemas that don't set ``reject_bool`` still accept bool
        (backward compat with the 8+ already-validated handlers).

        This is the legacy behavior pinned by
        ``test_bool_limit_accepted_due_to_int_subclass`` — the opt-in
        rule must NOT change it.
        """
        schema = {
            "limit": {
                "type": (int, str),
                "required": False,
                "default": 50,
            }
        }
        validated, err = _validate_dict_payload({"limit": True}, schema)
        # no error — bool is accepted via the int-subclass loophole.
        assert err is None
        assert validated is not None
        assert validated["limit"] is True

    def test_reject_bool_false_explicit_still_accepts_bool(self):
        """Setting ``reject_bool: False`` explicitly is the same as
        omitting the rule (backward compat)."""
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
        """When ``bool`` is explicitly in the declared type tuple,
        the rule is a no-op — the schema explicitly accepts bools,
        so the rule would contradict the schema's intent.

        E.g. ``type: (bool, int)`` for a field that accepts either a
        bool flag or an int code (rare but valid).
        """
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
        """When the declared type IS ``bool``, the rule is a no-op —
        rejecting bool would defeat the schema's purpose.
        """
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
        """The rule ONLY rejects bools — ints, floats, strings still
        pass through unchanged.
        """
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
        """The ``reject_bool`` rule fires BEFORE ``clamp_range`` — a
        bool value is rejected even when the schema also declares a
        clamp range. Without the rule, ``clamp_range`` already skips
        bools (to avoid ``critical: True`` → 1), so the bool would
        pass through unmodified — a silent type-confusion.
        """
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
        """The error message must include the declared type name so
        the caller knows what type was expected."""
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
        """When the schema omits ``type`` but sets ``reject_bool=True``,
        the rule still rejects bools (the rule is independent of the
        type check). The error message uses ``non-bool`` as the
        expected type since no type was declared.
        """
        schema = {"value": {"required": True, "reject_bool": True}}
        validated, err = _validate_dict_payload({"value": True}, schema)
        assert validated is None
        assert err is not None
        assert err["data"]["code"] == "client.invalid_field"
        assert err["data"]["field"] == "value"


# ══════════════════════════════════════════════════════════════════════════
# _bound_history_limit — defense-in-depth bool rejection
# ══════════════════════════════════════════════════════════════════════════


class TestBoundHistoryLimitRejectsBool:
    """``_bound_history_limit`` falls back to the default when given a
    bool, rather than silently coercing ``True`` → 1 or ``False`` → 1
    (via the lower-bound clamp).

    This is defense-in-depth: the validation layer (with
    ``reject_bool=True``) should catch bools first, but if a schema
    omits the rule (backward compat with the 8+ existing handlers),
    the bounder must not silently coerce a bool to a numeric count.
    """

    def test_true_returns_default_not_one(self):
        """``_bound_history_limit(True)`` returns ``_HISTORY_LIMIT_DEFAULT``
        (50), NOT 1.

        Pre-fix, ``int(True) == 1`` and ``max(1, min(1, 500)) == 1``
        silently coerced the bool to a count of 1.
        """
        assert _bound_history_limit(True) == _HISTORY_LIMIT_DEFAULT
        assert _bound_history_limit(True) != 1

    def test_false_returns_default_not_one(self):
        """``_bound_history_limit(False)`` returns the default, NOT 1.

        Pre-fix, ``int(False) == 0`` and ``max(1, min(0, 500)) == 1``
        (the lower-bound clamp coerced 0 → 1).
        """
        assert _bound_history_limit(False) == _HISTORY_LIMIT_DEFAULT

    def test_integer_values_unchanged(self):
        """Regression guard: integer inputs are NOT affected by the
        bool rejection — they continue to be clamped to
        ``[1, _HISTORY_LIMIT_MAX]`` exactly as before.
        """
        assert _bound_history_limit(0) == 1
        assert _bound_history_limit(1) == 1
        assert _bound_history_limit(50) == 50
        assert _bound_history_limit(500) == 500
        assert _bound_history_limit(1_000_000) == _HISTORY_LIMIT_MAX
        assert _bound_history_limit(-5) == 1

    def test_numeric_string_values_unchanged(self):
        """Regression guard: numeric string inputs (sent by the
        renderer's form inputs) continue to be coerced via ``int()``.
        """
        assert _bound_history_limit("25") == 25
        assert _bound_history_limit("0") == 1
        assert _bound_history_limit("999999") == _HISTORY_LIMIT_MAX

    def test_none_returns_default(self):
        """Regression guard: ``None`` returns the default."""
        assert _bound_history_limit(None) == _HISTORY_LIMIT_DEFAULT

    def test_garbage_string_returns_default(self):
        """Regression guard: a non-numeric string returns the default."""
        assert _bound_history_limit("not-a-number") == _HISTORY_LIMIT_DEFAULT


# ══════════════════════════════════════════════════════════════════════════
# _bound_history_offset — defense-in-depth bool rejection
# ══════════════════════════════════════════════════════════════════════════


class TestBoundHistoryOffsetRejectsBool:
    """``_bound_history_offset`` falls back to ``0`` when given a bool,
    rather than silently coercing ``True`` → 1 or ``False`` → 0.

    Defense-in-depth for the same reason as
    :class:`TestBoundHistoryLimitRejectsBool`.
    """

    def test_true_returns_zero_not_one(self):
        """``_bound_history_offset(True)`` returns ``0``, NOT 1.

        Pre-fix, ``int(True) == 1`` and ``max(0, min(1, 10_000_000))
        == 1`` silently coerced the bool to an offset of 1 (skipping
        the first history row).
        """
        assert _bound_history_offset(True) == 0
        assert _bound_history_offset(True) != 1

    def test_false_returns_zero(self):
        """``_bound_history_offset(False)`` returns ``0`` (same as
        pre-fix, since ``int(False) == 0`` and the lower bound is 0)."""
        assert _bound_history_offset(False) == 0

    def test_integer_values_unchanged(self):
        """Regression guard: integer inputs are NOT affected by the
        bool rejection."""
        assert _bound_history_offset(0) == 0
        assert _bound_history_offset(1) == 1
        assert _bound_history_offset(100) == 100
        assert _bound_history_offset(-5) == 0

    def test_none_returns_zero(self):
        """Regression guard: ``None`` returns 0."""
        assert _bound_history_offset(None) == 0


# ══════════════════════════════════════════════════════════════════════════
# Integration: validation + bounder composition
# ══════════════════════════════════════════════════════════════════════════


class TestValidationAndBounderComposition:
    """When ``reject_bool=True`` is set on a history-style schema, the
    validation layer rejects the bool BEFORE the bounder is called —
    the bounder never sees a bool in this case.

    When the schema omits ``reject_bool`` (legacy backward-compat
    path), the bounder catches the bool as defense-in-depth and uses
    the default instead of silently coercing to 1.
    """

    def test_strict_schema_rejects_bool_at_validation_layer(self):
        """A history-style schema with ``reject_bool=True`` rejects
        ``{"limit": True}`` at the validation layer — the bounder
        is never called.
        """
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
        # the FIRST field that fails (in schema iteration order) is
        # reported — ``limit`` is checked before ``offset``.
        assert err["data"]["field"] == "limit"

    def test_legacy_schema_accepts_bool_bounder_uses_default(self):
        """A legacy schema (no ``reject_bool``) accepts ``{"limit":
        True}`` at the validation layer — but the bounder then uses
        the default instead of silently coercing to 1.

        This is the defense-in-depth path: even when validation opts
        out of strict bool rejection (for backward compat with the
        8+ existing handlers), the bounder catches the bool.
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
        assert validated["limit"] is True  # validation passed through the bool

        # the bounder is the next step in the handler pipeline — it
        # catches the bool and uses the default.
        clamped = _bound_history_limit(validated["limit"])
        assert clamped == _HISTORY_LIMIT_DEFAULT
        assert clamped != 1  # NOT silently coerced to 1
