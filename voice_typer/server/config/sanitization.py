"""Non-numeric field validation + warning helpers extracted from ``config.py``.

This module holds the 4 sanitization helpers that
``Config._validate_non_numeric_fields`` uses to coerce + reset
legacy on-disk config values before the dataclass constructor sees
them:

* :func:`_derive_field_type_registry` — build ``{field_name: type}``
  from the Config dataclass annotations.
* :func:`_warn_and_reset` — log + record a "reset to default" event.
* :func:`_warn_and_coerce` — log + record a "coerced to type" event.
* :func:`_validate_non_numeric_fields` — the migration layer that
  iterates the registry, dispatches per-type coercion, and stashes
  warnings in ``data["_load_warnings"]``.

These functions take the ``Config`` class (``cls``) as their first
parameter so subclass overrides of the ``Config._warn_and_reset`` /
``Config._warn_and_coerce`` / ``Config._derive_field_type_registry``
classmethods are respected when ``_validate_non_numeric_fields``
dispatches to them via ``cls._warn_and_*``. The ``Config``
classmethods of the same names are thin delegators that forward to
the functions here (preserving the existing public API used by
``tests/test_config_load_corruption.py`` and
``tests/test_model_idle_unload.py``).
"""

from __future__ import annotations

import logging
import math
import types
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")


def _derive_field_type_registry(cls: type[Config]) -> dict[str, type]:
    """Build a ``{field_name: expected_type}`` registry from the
    Config dataclass.

    Optional[T] / T | None annotations are unwrapped to T so the
    validator can apply per-type coercion without special-casing each
    Optional field. ``Literal[...]`` annotations (subtype of ``str``)
    are normalized to ``str`` so the validator's str branch handles
    them.

    Replaces the 4 hand-maintained sets (``bool_fields`` /
    ``str_fields`` / ``int_fields`` / ``float_fields``) so the field
    list is sourced from the dataclass declaration itself — adding a
    new field to ``Config`` automatically opts it into validation
    without a parallel edit to ``_validate_non_numeric_fields``.
    """
    import typing

    hints = typing.get_type_hints(cls)
    registry: dict[str, type] = {}
    for name in cls.__dataclass_fields__:
        if name not in hints:
            continue
        ann = hints[name]
        # Unwrap Optional[T] / T | None → T
        #
        # both ``typing.Union`` (the
        # ``Optional[T]`` / ``Union[T, None]`` spelling) AND
        # ``types.UnionType`` (the PEP 604 ``T | None`` spelling)
        # must be unwrapped. ``typing.get_origin(str | None)``
        # returns ``types.UnionType`` — NOT ``typing.Union`` — so
        # the pre-fix ``is typing.Union`` check left every PEP 604
        # ``T | None`` field (microphone, qwen_model_path,
        # parakeet_model_path, corrections_path, custom_theme) in
        # the registry as the union type itself. The downstream
        # ``_validate_non_numeric_fields`` else-branch then
        # ``continue``d on ``types.UnionType`` and silently
        # skipped these fields, letting a hand-edited
        # ``"microphone": 123`` pass through without warning.
        if typing.get_origin(ann) in (typing.Union, types.UnionType):
            args = [a for a in typing.get_args(ann) if a is not type(None)]
            if len(args) == 1:
                ann = args[0]
        # Literal[...] is a subtype of str — normalize to str so the
        # str validation branch handles it (e.g. asr_backend).
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

    extracts the duplicated 5-line pattern
    ``msg = ...; log.warning(...); warnings.append(...);
    data[field_name] = getattr(defaults, field_name)`` that
    appeared 4 times in the original
    ``_validate_non_numeric_fields``. Returns the default value
    so the caller can write
    ``data[field_name] = cls._warn_and_reset(...)``.

    Parameters
    ----------
    cls
        The :class:`Config` subclass — used to look up the
        secret-field-name set via :meth:`Config._secret_field_names`
        so the redaction logic stays in sync with the
        ``credential_store`` provider→field map.
    field_name
        The config field being reset (e.g. ``"autostart"``).
    val
        The invalid value the user had on disk (used in the
        warning message for diagnosis).
    defaults
        A default-constructed ``Config`` instance — the source
        of the fallback value.
    warnings
        The running warnings list (appended in place).
    reason
        A short human-readable reason string that completes the
        sentence ``"Config field '{field_name}' {reason} {val!r},
        resetting to default {default_val!r}"``. Example:
        ``"had non-bool value"`` → ``"... had non-bool value
        'yes', resetting to default True"``.

    Returns
    -------
    Any
        The default value for ``field_name`` (the caller assigns
        this back to ``data[field_name]``).
    """
    default_val = getattr(defaults, field_name)
    # redact ``val`` for secret fields so a
    # malformed-on-disk api_key value (e.g. ``"openai_api_key": 123``
    # — an int instead of a str, which would trigger
    # ``_warn_and_reset`` via the str-validation branch) doesn't
    # get echoed into log files at WARNING level. Pre-fix, the
    # raw value was logged via ``{val!r}`` in the warning message
    # — if a user had pasted a real API key as an int (unlikely
    # but possible via a botched config-restore), the key would
    # land in ``backend.log`` and any crash-diagnostic bundle.
    # The redaction preserves the diagnostic shape (type + length)
    # so the operator can still see "the value was a 25-char
    # string" without leaking the actual content.
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

    extracts the duplicated 4-line pattern
    ``msg = ...; log.warning(...); warnings.append(...);
    data[field_name] = coerced`` that appeared in the int and
    float branches of ``_validate_non_numeric_fields``.

    Parameters
    ----------
    cls
        The :class:`Config` subclass — used to look up the
        secret-field-name set so the redaction logic mirrors
        :func:`_warn_and_reset`.
    field_name
        The config field being coerced (e.g. ``"vad_threshold"``).
    val
        The original on-disk value (used in the warning message
        for diagnosis — the user sees what they had vs. what it
        was coerced to).
    coerced
        The successfully-coerced value.
    warnings
        The running warnings list (appended in place).
    reason
        Short reason string that completes the sentence
        ``"Config field '{field_name}' {reason} {val!r}, coerced
        to {coerced!r}"``. Example: ``"had non-int value"``.

    Returns
    -------
    Any
        The coerced value (the caller assigns this back to
        ``data[field_name]``).
    """
    msg = f"Config field '{field_name}' {reason} {val!r}, coerced to {coerced!r}"
    # mirror the redaction in ``_warn_and_reset``
    # for secret fields. ``_warn_and_coerce`` is reached when the
    # on-disk value is coercible (e.g. ``"openai_api_key": 123``
    # coerced to ``"123"``) — the original int value would be
    # logged via ``{val!r}`` without this guard.
    if field_name in cls._secret_field_names():
        val_repr = f"<redacted {type(val).__name__} length={len(repr(val))}>"
        coerced_repr = f"<redacted {type(coerced).__name__} length={len(repr(coerced))}>"
        msg = f"Config field '{field_name}' {reason} {val_repr}, coerced to {coerced_repr}"
    log.warning("[CONFIG] %s", msg)
    warnings.append(msg)
    return coerced


def _validate_non_numeric_fields(cls: type[Config], data: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce bool / str / int / float fields in loaded config data.

    This is a migration layer — collects warnings in
    ``data['_load_warnings']`` so the caller (load()) can surface
    them via the ``last_load_warnings`` instance attribute
    (SCHEMA-1 / MED-I: no longer a dataclass field — see
    :meth:`Config.__post_init__`). Previously warnings were only
    logged; the user had no way to know their config was corrected.

    this is NOT a duplicate of the type coercion that
    ``cls(**data)`` would do.  Python dataclasses do NOT coerce
    ``1`` → ``True`` or ``"true"`` → ``True`` — they store the raw
    value as-is, which would then fail downstream type checks
    (e.g. ``isinstance(cfg.autostart, bool)`` returns False for
    ``1``).  This validator is a migration layer that fixes up
    legacy on-disk configs (written by older versions of the app
    that used ints/strings for bool fields) BEFORE the dataclass
    constructor sees them.  Without it, a config.json with
    ``"autostart": 1`` would silently store ``1`` instead of
    ``True``, breaking every ``if cfg.autostart:`` check.

    The 4 hand-maintained field-name sets (``bool_fields`` /
    ``str_fields`` / ``int_fields`` / ``float_fields``) were
    replaced by :meth:`_derive_field_type_registry`, which derives
    the field list from the ``Config`` dataclass declaration. The
    per-type coercion logic is unchanged; only the field-name
    source changed. The ``optional_str_fields`` allowlist (fields
    that accept ``None`` in addition to ``str``) is preserved
    verbatim — it captures the ``str | None`` fields whose ``None``
    sentinel is meaningful (no microphone / no Qwen path / no
    Parakeet override).

    pre-fix the validator SKIPPED complex types
    (``list[str]``, ``dict[str, ...]``) — only bool/str/int/float
    had explicit branches, and any other annotation fell through
    the loop body without validation. That meant a hand-edited
    config.json with ``"disabled_backends": "whisper"`` (a string
    instead of a list) would silently load as a string, then crash
    ``"whisper" in cfg.disabled_backends`` checks downstream (which
    expect iteration over a list of strings) — or worse, succeed
    accidentally (``"w" in "whisper"`` returns True, masking the
    type error). The fix adds a generic ``else`` branch that uses
    ``typing.get_origin`` to extract the container type (``list``,
    ``dict``, etc.) and resets to default if ``val`` is not an
    instance of that container type.
    """
    import typing

    warnings: list[str] = []
    # str | None fields where None is a meaningful sentinel
    # (no microphone / no Qwen path / no Parakeet override / no
    # corrections file). The str-validation branch allows None for
    # these fields.
    #
    # ``corrections_path`` is also a ``str | None``
    # PEP 604 field. Pre-fix it never reached this branch (the
    # registry left it as the un-unwrapped ``str | None`` alias,
    # and the else-branch skipped it via ``types.UnionType``
    # ``continue``). Now that ``_derive_field_type_registry``
    # unwraps PEP 604 unions to ``str``, the str branch sees
    # ``corrections_path`` and must allow None (the default) —
    # without this entry, a None value (e.g. after the dedicated
    # ``_validate_corrections_path`` resets a bad value to None)
    # would spuriously trip the "had non-string value" reset.
    optional_str_fields = {
        "parakeet_model_path",
        "qwen_model_path",
        "microphone",
        "corrections_path",
    }
    registry = cls._derive_field_type_registry()
    defaults = cls()

    # VALID-3 (MED-L): int / float field coercion.  Mirrors the
    # bool/str pattern — if the on-disk value is not already the
    # correct type, attempt coercion; if coercion fails, reset to
    # default and add a warning so the user knows the field was
    # corrected.  Note: ``bool`` is a subclass of ``int`` in
    # Python, so we explicitly exclude bools from the int coercion
    # (a bool value for an int field is almost certainly a
    # misconfiguration, not a legacy int-as-bool — fall through to
    # the default-reset branch).

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
            # VALID-3 (MED-L): int field coercion.  Accepts ints,
            # floats (truncated via int()), and numeric strings.
            # Rejects bools (bool is a subclass of int but almost
            # certainly indicates a misconfigured field — reset to
            # default).  Rejects anything int() can't parse (lists,
            # dicts, None, non-numeric strings).
            #
            # ``bool`` is a subclass of ``int`` — exclude explicitly
            # so ``True``/``False`` values are treated as invalid
            # (the user probably toggled a checkbox they shouldn't
            # have).
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
                # Already an int (and not a bool — handled above).
                continue
            # Attempt coercion: int("42") → 42, int(3.7) → 3,
            # int("3.7") raises ValueError (int() doesn't accept
            # float-formatted strings — fall through to the
            # catch-all).
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
            # VALID-3 (MED-L): float field coercion.  Accepts
            # floats, ints, and numeric strings.  Rejects bools
            # and anything float() can't parse.
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
                # they ARE valid Python floats — but they poison every
                # downstream range comparison (``nan < lo`` and
                # ``nan > hi`` both return ``False``, so a NaN value
                # silently bypasses every ``if cfg.foo > X`` guard) and
                # ``json.dumps`` writes them back as bare ``NaN`` /
                # ``Infinity`` literals (non-standard JSON) on the next
                # ``save()``. ``scalar._make_float_validator`` flags
                # them with a "must be a finite number" warning, but
                # the validator is advisory — it appends to
                # ``last_load_warnings`` without mutating the field,
                # so without this reset the bad value would persist on
                # the instance and round-trip back to disk on the next
                # save. ``json.loads`` accepts ``NaN`` / ``Infinity``
                # as a non-standard extension by default, so a
                # hand-edited or corrupted ``config.json`` can smuggle
                # one in (e.g. ``"streaming_silence_threshold": NaN``
                # would silently disable the silence detector).
                # Reset to the dataclass default and record a warning
                # so the renderer can surface "your config was
                # corrected" via ``last_load_warnings``.
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
            # A coerced value can also be non-finite — e.g.
            # ``float("inf")`` parses successfully out of a numeric
            # string, and ``float("nan")`` likewise. Guard the coerced
            # value with the same NaN/Inf reset so a hand-edited
            # ``"streaming_silence_threshold": "Infinity"`` string is
            # caught here rather than passing through to the dataclass
            # constructor + save() round-trip.
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
            # types (``list[str]``, ``dict[str, ...]``, ``tuple[...]``,
            # etc.) that the four primitive branches above don't
            # cover. ``expected_type`` here is a ``typing`` generic
            # alias (e.g. ``list[str]``) — ``typing.get_origin``
            # extracts the bare container type (``list`` / ``dict``)
            # so we can ``isinstance``-check without the
            # subscripted-alias TypeError (``isinstance(x, list[str])``
            # raises ``TypeError`` in Python 3.9+; ``isinstance(x, list)``
            # works fine).
            #
            # If ``val`` is already an instance of the container
            # type, no coercion is needed (the contents are
            # validated downstream by the per-field validators +
            # the IPC allowlist). If ``val`` is a different type
            # (e.g. a string where a list was expected), reset to
            # the dataclass default and warn.
            #
            # ``_derive_field_type_registry`` now
            # unwraps both ``typing.Union`` AND ``types.UnionType``
            # to a single non-None arg, so a PEP 604 ``T | None``
            # field arrives here as ``T`` (e.g. ``dict[str, ...]``)
            # rather than the union alias. The Union / UnionType
            # continue guards below are kept as defensive code for
            # any future annotation shape that yields a multi-arg
            # union the registry doesn't unwrap (e.g.
            # ``str | int | None`` — two non-None args, left
            # as-is by the registry's single-arg unwrap filter).
            container_origin = typing.get_origin(expected_type)
            # Skip Union / Optional (``str | None``) annotations —
            # these are handled by the primitive branches above
            # for str/int/float/bool, and for other Union shapes
            # (e.g. ``dict[str, str] | None``) the bare ``None``
            # sentinel is meaningful and a non-None value of the
            # first union member type is best left to the
            # downstream per-field validators (attempting to
            # ``isinstance(val, typing.Union)`` raises TypeError).
            if container_origin is typing.Union or container_origin is types.UnionType:
                continue
            if container_origin is None:
                # Bare type annotation without subscription (e.g.
                # ``dict`` instead of ``dict[str, str]``). Use the
                # annotation directly.
                container_origin = expected_type if isinstance(expected_type, type) else None
            if container_origin is None:
                # Unrecognized annotation shape — skip validation
                # (don't risk a TypeError on an exotic annotation).
                continue
            # ``None`` is acceptable for ``T | None`` fields that
            # survived the Optional-unwrap in
            # ``_derive_field_type_registry`` (e.g. ``custom_theme``
            # is ``dict[str, dict[str, str]] | None`` — when
            # unwrapped, the bare ``dict[...]`` doesn't carry the
            # ``| None``, but the dataclass field's default is
            # ``None`` so a missing or null on-disk value is valid).
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
    # via the ``last_load_warnings`` instance attribute.
    # APPEND to any existing ``_load_warnings`` rather
    # than overwriting — earlier load() stages (e.g.
    # ``_backup_before_downgrade``) may already have populated
    # the list with non-blocking notices (e.g. "config schema is
    # newer than this build supports"). Overwriting here would
    # silently drop those notices, defeating the surface-via-
    # last_load_warnings contract.
    existing_warnings = data.get("_load_warnings")
    if isinstance(existing_warnings, list):
        existing_warnings.extend(warnings)
    else:
        data["_load_warnings"] = warnings
    return data
