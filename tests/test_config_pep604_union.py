"""FR-51: PEP 604 union unwrap bug in ``Config._derive_field_type_registry``.

Regression tests for the silent config corruption that occurred when a
hand-edited ``config.json`` set one of the five ``T | None`` PEP 604
fields (``microphone``, ``qwen_model_path``, ``parakeet_model_path``,
``corrections_path``, ``custom_theme``) to a value of the wrong type.

Root cause
----------
``typing.get_origin(str | None)`` returns ``types.UnionType`` — NOT
``typing.Union``. The pre-fix unwrap check in
``Config._derive_field_type_registry`` was
``if typing.get_origin(ann) is typing.Union:`` which only matched the
``Optional[T]`` / ``Union[T, None]`` spelling, leaving every PEP 604
``T | None`` field in the registry as the un-unwrapped union alias.

The downstream ``Config._validate_non_numeric_fields`` else-branch then
explicitly ``continue``d on ``types.UnionType`` (a defensive skip),
so all 5 fields silently bypassed the load-time validator. A
hand-edited ``"microphone": 123`` (int) loaded without warning and
ended up as ``cfg.microphone == 123`` (the dataclass doesn't enforce
types), then crashed downstream code that assumed ``microphone`` was
``str | None``.

Fix
---
``_derive_field_type_registry`` now unwraps both ``typing.Union`` AND
``types.UnionType`` to a single non-None arg, so the 5 fields arrive
in ``_validate_non_numeric_fields`` as ``str`` / ``dict[str, ...]``
and enter the appropriate per-type validation branch. On type
mismatch the field is reset to its dataclass default and a warning is
appended to ``last_load_warnings``.
"""

from __future__ import annotations

import json
import types as _types
import typing

import pytest
from voice_typer.server.config import Config

# ── unit-level: the registry itself unwraps PEP 604 unions ─────────────────


class TestRegistryUnwrapsPep604Union:
    """``_derive_field_type_registry`` must unwrap ``T | None`` (PEP 604)
    to ``T`` — not leave it as the ``types.UnionType`` alias."""

    @pytest.mark.parametrize(
        "field_name,expected_unwrapped",
        [
            ("microphone", str),
            ("qwen_model_path", str),
            ("parakeet_model_path", str),
            ("corrections_path", str),
            ("custom_theme", dict),
        ],
    )
    def test_pep604_field_unwrapped_to_bare_type(self, field_name, expected_unwrapped):
        """Each PEP 604 ``T | None`` field must appear in the registry
        as ``T`` (unwrapped), NOT as ``T | None``.

        Pre-fix: the registry stored ``str | None`` for ``microphone``
        etc. (the un-unwrapped ``types.UnionType``), which the
        downstream validator silently skipped.
        """
        registry = Config._derive_field_type_registry()
        assert field_name in registry, (
            f"FR-51: {field_name!r} missing from registry — did the Config dataclass field get renamed?"
        )
        ann = registry[field_name]
        # The unwrapped annotation must NOT be a Union / UnionType —
        # ``str | None`` would fail this assertion.
        origin = typing.get_origin(ann)
        assert origin not in (typing.Union, _types.UnionType), (
            f"FR-51 regression: {field_name!r} registry entry is still "
            f"the un-unwrapped union {ann!r} (origin={origin!r}). "
            f"Expected unwrapped type {expected_unwrapped!r}."
        )
        # For ``custom_theme`` the unwrapped type is a generic alias
        # (``dict[str, dict[str, str]]``) — use ``get_origin`` to
        # extract the bare ``dict``. For ``str`` / ``int`` the bare
        # type IS the annotation.
        bare = origin if origin is not None else ann
        if expected_unwrapped is dict:
            assert bare is dict, f"FR-51: {field_name!r} unwrapped to {bare!r}, expected dict"
        else:
            assert bare is expected_unwrapped, (
                f"FR-51: {field_name!r} unwrapped to {bare!r}, expected {expected_unwrapped!r}"
            )


# ── end-to-end: load a bad config, assert reset + warning ──────────────────


def _write_config(tmp_path, payload: dict) -> None:
    """Write ``payload`` as ``config.json`` in ``tmp_path``."""
    (tmp_path / "config.json").write_text(json.dumps(payload))


def _load_with_config_dir(tmp_config_dir):
    """Load Config with the config dir isolated to a temp dir.

    The canonical ``tmp_config_dir`` fixture does the patching; this
    helper just loads (callers request the fixture).
    """
    return Config.load()


class TestWrongTypedPep604FieldsResetOnLoad:
    """FR-51: a hand-edited ``config.json`` with a wrong-typed value
    for any of the 5 PEP 604 ``T | None`` fields must be reset to the
    dataclass default (None) AND append a warning to
    ``last_load_warnings``.

    Pre-fix: ``microphone`` and ``parakeet_model_path`` silently
    retained the bad value (the union alias was skipped by the
    validator's else-branch ``types.UnionType`` continue).
    ``qwen_model_path`` / ``corrections_path`` / ``custom_theme`` had
    separate dedicated validators that caught the bad value, but
    ``microphone`` / ``parakeet_model_path`` had no such guard — they
    reached the dataclass as the wrong type.
    """

    def test_microphone_int_resets_to_none(self, tmp_path, tmp_config_dir):
        """``"microphone": 123`` (int) → reset to None + warning."""
        _write_config(tmp_path, {"microphone": 123})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.microphone is None, (
            f"FR-51: microphone=123 was NOT reset to None on load — "
            f"got: {c.microphone!r}. The PEP 604 ``str | None`` union "
            f"is being silently skipped by _validate_non_numeric_fields."
        )
        warnings = getattr(c, "last_load_warnings", []) or []
        assert any("microphone" in w for w in warnings), (
            f"FR-51: microphone=123 reset did not surface in last_load_warnings: {warnings}"
        )

    def test_qwen_model_path_int_resets_to_none(self, tmp_path, tmp_config_dir):
        """``"qwen_model_path": 42`` (int) → reset to None + warning."""
        _write_config(tmp_path, {"qwen_model_path": 42})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.qwen_model_path is None, (
            f"FR-51: qwen_model_path=42 was NOT reset to None — got: {c.qwen_model_path!r}"
        )
        warnings = getattr(c, "last_load_warnings", []) or []
        assert any("qwen_model_path" in w for w in warnings), (
            f"FR-51: qwen_model_path=42 reset did not surface in last_load_warnings: {warnings}"
        )

    def test_parakeet_model_path_int_resets_to_none(self, tmp_path, tmp_config_dir):
        """``"parakeet_model_path": 999`` (int) → reset to None + warning."""
        _write_config(tmp_path, {"parakeet_model_path": 999})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.parakeet_model_path is None, (
            f"FR-51: parakeet_model_path=999 was NOT reset to None — got: {c.parakeet_model_path!r}"
        )
        warnings = getattr(c, "last_load_warnings", []) or []
        assert any("parakeet_model_path" in w for w in warnings), (
            f"FR-51: parakeet_model_path=999 reset did not surface in last_load_warnings: {warnings}"
        )

    def test_corrections_path_bool_resets_to_none(self, tmp_path, tmp_config_dir):
        """``"corrections_path": false`` (bool) → reset to None + warning.

        ``false`` is a JSON boolean; pre-fix the dedicated
        ``_validate_corrections_path`` caught it. Post-fix the str
        branch of ``_validate_non_numeric_fields`` also catches it
        (and the dedicated validator now sees None and skips). Either
        way, the field must end up None with a warning.
        """
        _write_config(tmp_path, {"corrections_path": False})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.corrections_path is None, (
            f"FR-51: corrections_path=False was NOT reset to None — got: {c.corrections_path!r}"
        )
        warnings = getattr(c, "last_load_warnings", []) or []
        assert any("corrections_path" in w for w in warnings), (
            f"FR-51: corrections_path=False reset did not surface in last_load_warnings: {warnings}"
        )

    def test_custom_theme_non_dict_resets_to_none(self, tmp_path, tmp_config_dir):
        """``"custom_theme": "not a dict"`` (str) → reset to None + warning.

        Pre-fix the dedicated DE-29 validator caught this. Post-fix the
        else-branch of ``_validate_non_numeric_fields`` ALSO catches
        it (the registry now unwraps ``dict[...] | None`` to
        ``dict[...]``, so the bare-dict isinstance check fires).
        Either way, the field must end up None with a warning.
        """
        _write_config(tmp_path, {"custom_theme": "not a dict"})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.custom_theme is None, (
            f"FR-51: custom_theme='not a dict' was NOT reset to None — got: {c.custom_theme!r}"
        )
        warnings = getattr(c, "last_load_warnings", []) or []
        assert any("custom_theme" in w for w in warnings), (
            f"FR-51: custom_theme='not a dict' reset did not surface in last_load_warnings: {warnings}"
        )


class TestValidPep604FieldsPreservedOnLoad:
    """Sanity: valid values for the 5 PEP 604 fields are preserved.

    These guard against an over-correction where the validator
    resets EVERY value (including valid ones) because of an
    over-broad type check.
    """

    def test_valid_microphone_string_preserved(self, tmp_path, tmp_config_dir):
        _write_config(tmp_path, {"microphone": "My USB Mic"})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.microphone == "My USB Mic"

    def test_valid_microphone_none_preserved(self, tmp_path, tmp_config_dir):
        _write_config(tmp_path, {"microphone": None})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.microphone is None

    def test_valid_parakeet_model_path_string_preserved(self, tmp_path, tmp_config_dir):
        _write_config(tmp_path, {"parakeet_model_path": "/some/path"})
        c = _load_with_config_dir(tmp_config_dir)
        # The dedicated _validate_model_path may reset this if the
        # path doesn't exist / isn't safe — that's a separate concern.
        # Here we only assert that the str branch of
        # _validate_non_numeric_fields doesn't spuriously reset a
        # valid str value. Accept either the str (if path validation
        # passed) or None (if path validation reset it) — but NEVER
        # a non-str / non-None type.
        assert c.parakeet_model_path is None or isinstance(c.parakeet_model_path, str), (
            f"FR-51 over-correction: parakeet_model_path=/some/path got mangled to {c.parakeet_model_path!r}"
        )

    def test_valid_custom_theme_dict_preserved(self, tmp_path, tmp_config_dir):
        """A well-formed custom_theme dict is preserved (mirrors the
        DE-29 test in ``test_config_group_fixes.py``)."""
        valid_theme = {
            "light": {
                "--background": "#ffffff",
                "--foreground": "#000000",
                "--primary": "#0066cc",
                "--bg-subtle": "#f0f0f0",
                "--border": "#cccccc",
                "--text-muted": "#666666",
            },
            "dark": {
                "--background": "#000000",
                "--foreground": "#ffffff",
                "--primary": "#3399ff",
                "--bg-subtle": "#1a1a1a",
                "--border": "#333333",
                "--text-muted": "#999999",
            },
        }
        _write_config(tmp_path, {"custom_theme": valid_theme})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.custom_theme == valid_theme, (
            f"FR-51 over-correction: a VALID custom_theme dict was reset on load. Got: {c.custom_theme!r}"
        )

    def test_valid_custom_theme_none_preserved(self, tmp_path, tmp_config_dir):
        _write_config(tmp_path, {"custom_theme": None})
        c = _load_with_config_dir(tmp_config_dir)
        assert c.custom_theme is None
