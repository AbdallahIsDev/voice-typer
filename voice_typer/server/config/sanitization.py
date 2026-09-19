"""Non-numeric field validation + warning helpers extracted from ``config.py``."""

from __future__ import annotations

import logging
import math
import types
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")


def _derive_field_type_registry(cls: type[Config]) -> dict[str, type]:
    """Build a ``{field_name: expected_type}`` registry from the"""
    import typing

    hints = typing.get_type_hints(cls)
    registry: dict[str, type] = {}
    for name in cls.__dataclass_fields__:
        if name not in hints:
            continue
        ann = hints[name]
        # Unwrap Optional[T] / T | None → T
        if typing.get_origin(ann) in (typing.Union, types.UnionType):
            args = [a for a in typing.get_args(ann) if a is not type(None)]
            if len(args) == 1:
                ann = args[0]
        # Literal[...] is a subtype of str, normalize to str so the
        if typing.get_origin(ann) is typing.Literal:
            ann = str
        registry[name] = ann
    return registry


def _warn_and_reset(
    cls: type[Config],
    field_name: str,
    val: Any,
    defaults: Config,
    warnings: list[str],
    *,
    reason: str,
) -> Any:
    """Reset ``field_name`` to its default value with a logged warning.

    Returns
    """
    default_val = getattr(defaults, field_name)
    # redact ``val`` for secret fields so a
    if field_name in cls._secret_field_names():
        val_repr = f"<redacted {type(val).__name__} length={len(repr(val))}>"
    else:
        val_repr = repr(val)
    msg = f"Config field '{field_name}' {reason} {val_repr}, resetting to default {default_val!r}"
    log.warning("[CONFIG] %s", msg)
    warnings.append(msg)
    return default_val


def _warn_and_coerce(
    cls: type[Config],
    field_name: str,
    val: Any,
    coerced: Any,
    warnings: list[str],
    *,
    reason: str,
) -> Any:
    """Record a coercion warning and return the coerced value.

    Returns
    """
    msg = f"Config field '{field_name}' {reason} {val!r}, coerced to {coerced!r}"
    # mirror the redaction in ``_warn_and_reset``
    if field_name in cls._secret_field_names():
        val_repr = f"<redacted {type(val).__name__} length={len(repr(val))}>"
        coerced_repr = f"<redacted {type(coerced).__name__} length={len(repr(coerced))}>"
        msg = f"Config field '{field_name}' {reason} {val_repr}, coerced to {coerced_repr}"
    log.warning("[CONFIG] %s", msg)
    warnings.append(msg)
    return coerced


def _validate_non_numeric_fields(cls: type[Config], data: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce bool / str / int / float fields in loaded config data."""
    import typing

    warnings: list[str] = []
    # str | None fields where None is a meaningful sentinel
    optional_str_fields = {
        "parakeet_model_path",
        "qwen_model_path",
        "microphone",
        "corrections_path",
    }
    # ``int | None`` / ``float | None`` fields where ``None`` is a
    optional_numeric_fields = {
        "bubble_x",
        "bubble_y",
        "bubble_scale",
        "test_duration_seconds",
    }
    registry = cls._derive_field_type_registry()
    defaults = cls()

    # int / float field coercion. Mirrors the

    for field_name, expected_type in registry.items():
        if field_name not in data:
            continue
        val = data[field_name]

        if expected_type is bool:
            if isinstance(val, bool):
                continue
            # Coerce truthy/falsy values
            if val in (1, "1", "true", "True", "yes"):
                data[field_name] = cls._warn_and_coerce(
                    field_name,
                    val,
                    True,
                    warnings,
                    reason="had non-bool value",
                )
            elif val in (0, "0", "false", "False", "no", ""):
                data[field_name] = cls._warn_and_coerce(
                    field_name,
                    val,
                    False,
                    warnings,
                    reason="had non-bool value",
                )
            else:
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had invalid value",
                )

        elif expected_type is str:
            if isinstance(val, str):
                continue
            if val is None and field_name in optional_str_fields:
                continue
            data[field_name] = cls._warn_and_reset(
                field_name,
                val,
                defaults,
                warnings,
                reason="had non-string value",
            )

        elif expected_type is int:
            # ``int | None`` fields: ``None`` is a meaningful sentinel
            if val is None and field_name in optional_numeric_fields:
                continue
            # int field coercion. Accepts ints,
            if isinstance(val, bool):
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had bool value",
                )
                continue
            if isinstance(val, int):
                # Already an int (and not a bool, handled above).
                continue
            # Attempt coercion: int("42") → 42, int(3.7) → 3,
            try:
                coerced = int(val)
            except (TypeError, ValueError):
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had non-int value",
                )
                continue
            data[field_name] = cls._warn_and_coerce(
                field_name,
                val,
                coerced,
                warnings,
                reason="had non-int value",
            )

        elif expected_type is float:
            # ``float | None`` fields: ``None`` is a meaningful
            if val is None and field_name in optional_numeric_fields:
                continue
            # float field coercion. Accepts
            if isinstance(val, bool):
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had bool value",
                )
                continue
            if isinstance(val, float):
                # NaN / Inf survive the dataclass constructor because
                if math.isnan(val) or math.isinf(val):
                    data[field_name] = cls._warn_and_reset(
                        field_name,
                        val,
                        defaults,
                        warnings,
                        reason="had non-finite float value",
                    )
                    continue
                continue
            try:
                coerced = float(val)
            except (TypeError, ValueError):
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had non-float value",
                )
                continue
            # A coerced value can also be non-finite: e.g.
            if math.isnan(coerced) or math.isinf(coerced):
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had non-finite float value",
                )
                continue
            data[field_name] = cls._warn_and_coerce(
                field_name,
                val,
                coerced,
                warnings,
                reason="had non-float value",
            )

        else:
            # generic branch for complex container
            container_origin = typing.get_origin(expected_type)
            # Skip Union / Optional (``str | None``) annotations —
            if container_origin is typing.Union or container_origin is types.UnionType:
                continue
            if container_origin is None:
                # Bare type annotation without subscription (e.g.
                container_origin = expected_type if isinstance(expected_type, type) else None
            if container_origin is None:
                # Unrecognized annotation shape, skip validation
                continue
            # ``None`` is acceptable for ``T | None`` fields that
            if val is None:
                continue
            if isinstance(val, container_origin):
                continue
            data[field_name] = cls._warn_and_reset(
                field_name,
                val,
                defaults,
                warnings,
                reason=f"had non-{container_origin.__name__} value",
            )

    # stash warnings so load() can surface them
    existing_warnings = data.get("_load_warnings")
    if isinstance(existing_warnings, list):
        existing_warnings.extend(warnings)
    else:
        data["_load_warnings"] = warnings
    return data
