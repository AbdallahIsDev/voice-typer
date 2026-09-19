"""Tests for PEP 562 dynamic re-export of mutable target-safety globals."""

from __future__ import annotations

import pytest
from voice_typer.server import (
    clipboard as clip_mod,  # noqa: E402
    clipboard_target_safety as safety_mod,  # noqa: E402
)

# The seven mutable globals that MUST be resolved via PEP 562
_MUTABLE_GLOBALS = [
    "_PYATSPI_STATE_FOCUSED",
    "_PYATSPI_UNAVAILABLE_WARNED",
    "_PYOBJC_UNAVAILABLE_WARNED",
    "_UIA_MODULE",
    "_UIA_SINGLETON",
    "_UIA_SINGLETON_INIT_ATTEMPTED",
    "_WE_ELEVATED",
]


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_mutable_global_not_statically_bound(name):
    """``name`` must NOT be a static attribute of the clipboard package."""
    assert name not in clip_mod.__dict__, (
        f"{name!r} is statically bound in clipboard.__dict__, this "
        f"breaks the SI-24 PEP 562 fix. Remove it from the "
        f"``from clipboard_target_safety import (...)`` block in "
        f"clipboard/__init__.py and let __getattr__ resolve it "
        f"dynamically."
    )


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_mutable_global_listed_in_all(name):
    """``name`` must be in ``clipboard.__all__`` for public-surface parity."""
    assert name in clip_mod.__all__, (
        f"{name!r} is missing from clipboard.__all__, "
        f"``from voice_typer.server.clipboard import {name}`` would "
        f"fall back to a non-public ``__getattr__`` lookup that "
        f"static analyzers can't see."
    )


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_monkeypatch_source_visible_via_reexport(name):
    """Mutating ``clipboard_target_safety.<name>`` IS visible via"""
    sentinel = object()
    original = getattr(safety_mod, name)
    setattr(safety_mod, name, sentinel)
    try:
        assert getattr(clip_mod, name) is sentinel, (
            f"clipboard.{name} did not reflect the mutated value on "
            f"clipboard_target_safety.{name}, the PEP 562 "
            f"``__getattr__`` is missing or shadowed by a stale "
            f"static import."
        )
    finally:
        setattr(safety_mod, name, original)


def test_repeated_access_reflects_each_mutation():
    """Each ``clipboard.<name>`` access reads the current source value."""
    name = "_UIA_SINGLETON"
    original = getattr(safety_mod, name)
    try:
        sentinel_a = object()
        sentinel_b = object()
        setattr(safety_mod, name, sentinel_a)
        assert getattr(clip_mod, name) is sentinel_a
        setattr(safety_mod, name, sentinel_b)
        assert getattr(clip_mod, name) is sentinel_b
    finally:
        setattr(safety_mod, name, original)


def test_from_import_still_resolves_via_getattr():
    """``from ... import _PYATSPI_STATE_FOCUSED`` resolves via __getattr__."""
    # Use exec so the ``from ... import`` runs at call time (not at
    namespace: dict = {}
    exec(  # noqa: S102, controlled test fixture
        "from voice_typer.server.clipboard import _PYATSPI_STATE_FOCUSED",
        namespace,
    )
    assert "_PYATSPI_STATE_FOCUSED" in namespace


def test_getattr_raises_for_unknown_name():
    """Unknown names raise ``AttributeError`` (not silently return None)."""
    unknown_name = "_definitely_not_a_real_name"
    with pytest.raises(AttributeError, match="has no attribute '_definitely_not_a_real_name'"):
        getattr(clip_mod, unknown_name)


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_dir_includes_dynamic_names(name):
    """``dir(clip_mod)`` lists the dynamically-resolved globals."""
    assert name in dir(clip_mod), (
        f"{name!r} is missing from dir(clipboard), the PEP 562 "
        f"``__dir__`` hook should append dynamically-resolved "
        f"mutable globals to the default module dir()."
    )
