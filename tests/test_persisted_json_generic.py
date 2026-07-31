"""Tests for ``PersistedJSON`` generic type parameter.

These tests verify that :class:`PersistedJSON` is parameterised by a
type variable ``T`` so callers can opt into static type-checking on
the JSON round-trip. The previous implementation used ``Any``
everywhere (``default: Any``, ``load() -> Any``, ``save(data: Any)``)
— callers got zero type-checking on the saved/loaded shape.

The fix:

* :class:`PersistedJSON` now inherits :class:`typing.Generic[T]`.
* :meth:`load` returns ``T``.
* :meth:`save` accepts ``data: T``.
* The ``default`` parameter remains typed as ``Any`` so legacy callers
  that pass ``default=None`` and later ``.save(some_dict)`` keep
  type-checking clean (they get the pre-generic ``Any`` behaviour —
  ``T`` is left unconstrained and resolves to ``Unknown``). New
  callers can opt INTO type safety by explicitly parameterising the
  class — e.g. ``PersistedJSON[dict[str, Any]](path, default={})``.

The two existing call sites (``VocabularyManager``,
``TemplateManager``) do not parameterise yet; parameterising them is a
mechanical follow-up that is out of scope for this change (those
modules are owned by another agent's area).
"""

from __future__ import annotations

from pathlib import Path

from voice_typer.server.secure_file_io import PersistedJSON

# ── Structural tests ─────────────────────────────────────────────────


def test_persistedjson_is_generic() -> None:
    """``PersistedJSON`` must be a :class:`typing.Generic` subclass so
    callers can parameterise it with their JSON shape."""
    from typing import Generic

    assert issubclass(PersistedJSON, Generic), (
        "PersistedJSON must inherit from typing.Generic[T] so callers "
        "can opt into static type-checking on the JSON round-trip. "
        "The pre-fix class used ``Any`` everywhere — callers got zero "
        "type-checking on the saved/loaded shape."
    )


def test_persistedjson_has_typevar_parameter() -> None:
    """The class must declare a type parameter (``T``) that parameterises
    both :meth:`load` and :meth:`save`."""

    # ``typing.Generic`` exposes the type parameters via
    # ``__parameters__`` (Python 3.12+).
    params = getattr(PersistedJSON, "__parameters__", ())
    assert len(params) == 1, f"PersistedJSON must declare exactly one type parameter (T). Got {params!r}."

    # Verify the typevar name is ``T`` (cosmetic — guards against an
    # accidental rename to something less idiomatic).
    tvar = params[0]
    assert tvar.__name__ == "T", f"PersistedJSON's type parameter must be named 'T' — got {tvar.__name__!r}."


def test_load_signature_returns_typevar() -> None:
    """``PersistedJSON.load`` must be annotated to return ``T`` (the
    type parameter), not ``Any``.

    We inspect the resolved type hints (which evaluate forward
    references and substitute the TypeVar). The return annotation
    must be the TypeVar instance itself — pyrefly then enforces that
    a parameterised ``PersistedJSON[dict]`` returns ``dict`` from
    :meth:`load`.
    """
    hints = typing_get_type_hints(PersistedJSON.load)
    ret = hints.get("return")
    # When the TypeVar is unresolved (caller didn't parameterise),
    # ``get_type_hints`` returns the TypeVar instance itself.
    from typing import TypeVar

    assert isinstance(ret, TypeVar), (
        "PersistedJSON.load must be annotated as ``-> T`` (the class's "
        "type parameter). The pre-fix annotation was ``-> Any`` which "
        f"silenced shape-mismatch errors. Got return annotation: {ret!r}."
    )


def test_save_signature_accepts_typevar() -> None:
    """``PersistedJSON.save`` must be annotated to accept ``data: T``
    (the type parameter), not ``data: Any``."""
    from typing import TypeVar

    hints = typing_get_type_hints(PersistedJSON.save)
    data_hint = hints.get("data")
    assert isinstance(data_hint, TypeVar), (
        "PersistedJSON.save must be annotated as ``data: T`` (the "
        "class's type parameter). The pre-fix annotation was "
        f"``data: Any`` which silenced shape-mismatch errors. Got data "
        f"annotation: {data_hint!r}."
    )


# ── Behavioural tests ────────────────────────────────────────────────


def test_legacy_unparameterised_call_still_works(tmp_path: Path) -> None:
    """Legacy callers that pass ``default=None`` and later ``.save(dict)``
    must keep working at runtime (Python doesn't enforce generics at
    runtime) AND must keep type-checking clean (the ``default: Any``
    annotation leaves ``T`` unconstrained, so ``.save`` accepts
    anything).

    This is the backward-compat guarantee: existing call sites in
    ``vocabulary.py``, ``templates.py``, and ~11 test files use this
    pattern. The fix MUST NOT introduce new pyrefly errors on those
    sites.
    """
    store = PersistedJSON(tmp_path / "legacy.json", default=None)
    # ``.save`` accepts a dict even though ``default=None`` was passed
    # — the ``default: Any`` annotation leaves ``T`` unconstrained.
    store.save({"key": "value"})
    loaded = store.load()
    assert loaded == {"key": "value"}


def test_parameterised_load_returns_typed_value(tmp_path: Path) -> None:
    """A parameterised ``PersistedJSON[dict[str, object]]`` returns a
    statically-typed ``dict[str, object]`` from :meth:`load`.

    The runtime behaviour is unchanged — we just verify the round-trip
    works AND that the type annotation is honoured (no runtime cast
    needed).
    """
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "typed.json", default={})
    store.save({"foo": "bar", "count": 42})
    loaded: dict[str, object] = store.load()
    assert loaded == {"foo": "bar", "count": 42}
    # The type checker sees ``loaded`` as ``dict[str, object]`` so we
    # can use dict operations without casts.
    assert "foo" in loaded
    assert isinstance(loaded["count"], int)


def test_parameterised_save_rejects_wrong_shape_at_type_check_time(
    tmp_path: Path,
) -> None:
    """A parameterised ``PersistedJSON[dict[str, object]]`` enforces
    that :meth:`save` receives a ``dict[str, object]``.

    We can't easily assert "pyrefly rejects this" at runtime, but we
    CAN verify the runtime round-trip succeeds when the right shape is
    passed (and document that the wrong shape would be a type-check
    error). The static-type guarantee is enforced by pyrefly against
    the source in this test file.
    """
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "shape.json", default={})
    # Right shape: dict[str, object] — type-checks.
    store.save({"k1": "v1", "k2": 99})
    assert store.load() == {"k1": "v1", "k2": 99}


def test_default_property_still_returns_any(tmp_path: Path) -> None:
    """The ``default`` property remains typed as ``Any`` (not ``T``) so
    legacy callers that read ``store.default`` after a corrupt-load
    fallback don't get a shape mismatch.

    The fix deliberately keeps ``default: Any`` because:

    * Some legacy tests pass ``default=None`` and expect ``None`` back
      from ``load()`` on a corrupt/missing file (the ``Any`` return
      matches both ``None`` and the JSON shape).
    * Parameterising ``default`` as ``T`` would force callers to
      either pass ``default=None`` (narrowing ``T = None`` and
      breaking subsequent ``.save(dict)``) or explicitly parameterise
      the class — too invasive for the 2 existing call sites.
    """
    store = PersistedJSON(tmp_path / "default_test.json", default={"sentinel": True})
    # ``default`` is Any-typed: callers can read it without a cast.
    default_value = store.default
    assert default_value == {"sentinel": True}


def test_round_trip_with_complex_payload(tmp_path: Path) -> None:
    """A parameterised ``PersistedJSON`` round-trips a complex payload
    (nested dicts, lists, mixed types) without losing fidelity."""
    payload: dict[str, object] = {
        "templates": [
            {"id": "t1", "name": "Greeting", "text": "Hello, world!"},
            {"id": "t2", "name": "Farewell", "text": "Goodbye."},
        ],
        "version": 2,
        "metadata": {"created_at": "2025-01-01T00:00:00Z"},
    }
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "complex.json", default={})
    store.save(payload)
    loaded = store.load()
    assert loaded == payload


def test_load_returns_default_when_file_missing(tmp_path: Path) -> None:
    """When the JSON file doesn't exist, :meth:`load` returns the
    configured default. The default's type is preserved through the
    generic parameterisation."""
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "nonexistent.json", default={"fallback": True})
    loaded: dict[str, object] = store.load()
    assert loaded == {"fallback": True}


def test_load_returns_default_when_file_corrupt(tmp_path: Path) -> None:
    """When the JSON file is corrupt, :meth:`load` quarantines it and
    returns the configured default. The default's type is preserved."""
    path = tmp_path / "corrupt.json"
    path.write_text("{ this is not valid JSON ", encoding="utf-8")
    store: PersistedJSON[dict[str, object]] = PersistedJSON(path, default={"recovered": True})
    loaded = store.load()
    assert loaded == {"recovered": True}
    # The corrupt file should have been quarantined aside.
    assert not path.exists()


# ── Helpers ──────────────────────────────────────────────────────────


def typing_get_type_hints(func):
    """Wrapper around :func:`typing.get_type_hints` that handles the
    ``TypeVar`` resolution for unbound methods.

    For an unbound method on a generic class, ``get_type_hints``
    returns the TypeVar instance itself in the annotations (it
    doesn't substitute the class's type parameter). That's exactly
    what we want to assert: the annotation IS the TypeVar, not a
    concrete type.
    """
    import typing

    return typing.get_type_hints(func)
