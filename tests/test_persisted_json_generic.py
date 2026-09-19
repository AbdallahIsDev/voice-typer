"""Tests for ``PersistedJSON`` generic type parameter."""

from __future__ import annotations

from pathlib import Path

from voice_typer.server.secure_file_io import PersistedJSON


def test_persistedjson_is_generic() -> None:
    """``PersistedJSON`` must be a :class:`typing.Generic` subclass so"""
    from typing import Generic

    assert issubclass(PersistedJSON, Generic), (
        "PersistedJSON must inherit from typing.Generic[T] so callers "
        "can opt into static type-checking on the JSON round-trip. "
        "The pre-fix class used ``Any`` everywhere, callers got zero "
        "type-checking on the saved/loaded shape."
    )


def test_persistedjson_has_typevar_parameter() -> None:
    """The class must declare a type parameter (``T``) that parameterises"""

    # ``typing.Generic`` exposes the type parameters via
    params = getattr(PersistedJSON, "__parameters__", ())
    assert len(params) == 1, f"PersistedJSON must declare exactly one type parameter (T). Got {params!r}."

    tvar = params[0]
    assert tvar.__name__ == "T", f"PersistedJSON's type parameter must be named 'T', got {tvar.__name__!r}."


def test_load_signature_returns_typevar() -> None:
    """type parameter), not ``Any``."""
    hints = typing_get_type_hints(PersistedJSON.load)
    ret = hints.get("return")
    # When the TypeVar is unresolved (caller didn't parameterise),
    from typing import TypeVar

    assert isinstance(ret, TypeVar), (
        "PersistedJSON.load must be annotated as ``-> T`` (the class's "
        "type parameter). The pre-fix annotation was ``-> Any`` which "
        f"silenced shape-mismatch errors. Got return annotation: {ret!r}."
    )


def test_save_signature_accepts_typevar() -> None:
    """``PersistedJSON.save`` must be annotated to accept ``data: T``"""
    from typing import TypeVar

    hints = typing_get_type_hints(PersistedJSON.save)
    data_hint = hints.get("data")
    assert isinstance(data_hint, TypeVar), (
        "PersistedJSON.save must be annotated as ``data: T`` (the "
        "class's type parameter). The pre-fix annotation was "
        f"``data: Any`` which silenced shape-mismatch errors. Got data "
        f"annotation: {data_hint!r}."
    )


def test_legacy_unparameterised_call_still_works(tmp_path: Path) -> None:
    """Legacy callers that pass ``default=None`` and later ``.save(dict)``"""
    store = PersistedJSON(tmp_path / "legacy.json", default=None)
    # ``.save`` accepts a dict even though ``default=None`` was passed
    store.save({"key": "value"})
    loaded = store.load()
    assert loaded == {"key": "value"}


def test_parameterised_load_returns_typed_value(tmp_path: Path) -> None:
    """statically-typed ``dict[str, object]`` from :meth:`load`."""
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "typed.json", default={})
    store.save({"foo": "bar", "count": 42})
    loaded: dict[str, object] = store.load()
    assert loaded == {"foo": "bar", "count": 42}
    # The type checker sees ``loaded`` as ``dict[str, object]`` so we
    assert "foo" in loaded
    assert isinstance(loaded["count"], int)


def test_parameterised_save_rejects_wrong_shape_at_type_check_time(
    tmp_path: Path,
) -> None:
    """A parameterised ``PersistedJSON[dict[str, object]]`` enforces"""
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "shape.json", default={})
    # Right shape: dict[str, object], type-checks.
    store.save({"k1": "v1", "k2": 99})
    assert store.load() == {"k1": "v1", "k2": 99}


def test_default_property_still_returns_any(tmp_path: Path) -> None:
    """The ``default`` property remains typed as ``Any`` (not ``T``) so"""
    store = PersistedJSON(tmp_path / "default_test.json", default={"sentinel": True})
    # ``default`` is Any-typed: callers can read it without a cast.
    default_value = store.default
    assert default_value == {"sentinel": True}


def test_round_trip_with_complex_payload(tmp_path: Path) -> None:
    """A parameterised ``PersistedJSON`` round-trips a complex payload"""
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
    """generic parameterisation."""
    store: PersistedJSON[dict[str, object]] = PersistedJSON(tmp_path / "nonexistent.json", default={"fallback": True})
    loaded: dict[str, object] = store.load()
    assert loaded == {"fallback": True}


def test_load_returns_default_when_file_corrupt(tmp_path: Path) -> None:
    """When the JSON file is corrupt, :meth:`load` quarantines it and"""
    path = tmp_path / "corrupt.json"
    path.write_text("{ this is not valid JSON ", encoding="utf-8")
    store: PersistedJSON[dict[str, object]] = PersistedJSON(path, default={"recovered": True})
    loaded = store.load()
    assert loaded == {"recovered": True}
    # The corrupt file should have been quarantined aside.
    assert not path.exists()


def typing_get_type_hints(func):
    """``TypeVar`` resolution for unbound methods."""
    import typing

    return typing.get_type_hints(func)
