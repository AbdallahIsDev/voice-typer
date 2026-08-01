"""Tests for PEP 562 dynamic re-export of mutable target-safety globals.

Background (finding SI-24):
    ``voice_typer.server.clipboard.__init__`` historically re-exported a
    handful of MUTABLE module-level globals (``_PYATSPI_STATE_FOCUSED``,
    ``_UIA_SINGLETON``, ``_WE_ELEVATED``, etc.) from
    ``voice_typer.server.clipboard_target_safety`` via the plain
    ``from clipboard_target_safety import (...)`` form.

    Python's ``from X import Y`` binds ``Y`` in the importing module's
    namespace AT IMPORT TIME. Subsequent mutations of the source
    attribute (e.g. ``clipboard_target_safety._UIA_SINGLETON = ...``)
    are NOT visible through the re-export, and tests that monkeypatch
    the source module's global wouldn't affect any caller reading the
    re-export.

    The fix (PEP 562): drop these seven names from the static
    ``from ... import`` block and resolve them dynamically via a
    module-level ``__getattr__`` that delegates to
    ``clipboard_target_safety`` on every access. The names stay in
    ``__all__`` so ``from voice_typer.server.clipboard import
    _PYATSPI_STATE_FOCUSED`` still works (the import machinery consults
    ``__getattr__`` when the name isn't otherwise found).

These tests verify the dynamic-resolution contract on every platform
(they touch no Win32/pyatspi/pyobjc code paths — only the
re-export-binding semantics).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# Make pynput optional — these tests don't exercise any keyboard path,
# but the package's ``__init__`` runs ``import pyperclip`` and the
# .linux/.windows/.manager submodules at first import. Mock both so the
# import succeeds in headless CI sandboxes without the real packages.
sys.modules.setdefault("pynput", MagicMock())
sys.modules.setdefault("pynput.keyboard", MagicMock())
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server import clipboard_target_safety as safety_mod  # noqa: E402

# The seven mutable globals that MUST be resolved via PEP 562
# ``__getattr__`` (not bound at import time).
_MUTABLE_GLOBALS = [
    "_PYATSPI_STATE_FOCUSED",
    "_PYATSPI_UNAVAILABLE_WARNED",
    "_PYOBJC_UNAVAILABLE_WARNED",
    "_UIA_MODULE",
    "_UIA_SINGLETON",
    "_UIA_SINGLETON_INIT_ATTEMPTED",
    "_WE_ELEVATED",
]


# ---------------------------------------------------------------------------
# 1. The seven names are NOT bound as static module attributes.
#    If they were, ``from ... import`` would have copied the import-time
#    value and mutations wouldn't be visible. This is the "before" guard
#    that ensures the fix is actually in place (regression test for
#    someone re-adding them to the ``from ... import (...)`` block).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_mutable_global_not_statically_bound(name):
    """``name`` must NOT be a static attribute of the clipboard package.

    If it were statically bound (via ``from clipboard_target_safety
    import name``), the import-time value would shadow the PEP 562
    ``__getattr__`` hook and mutations of the source global wouldn't
    propagate. This test fails loudly if someone re-adds the static
    import.
    """
    assert name not in clip_mod.__dict__, (
        f"{name!r} is statically bound in clipboard.__dict__ — this "
        f"breaks the SI-24 PEP 562 fix. Remove it from the "
        f"``from clipboard_target_safety import (...)`` block in "
        f"clipboard/__init__.py and let __getattr__ resolve it "
        f"dynamically."
    )


# ---------------------------------------------------------------------------
# 2. The seven names ARE listed in ``__all__`` (so static-analysis
#    tools, ``dir()``, and ``from voice_typer.server.clipboard import X``
#    keep working — the import machinery falls back to ``__getattr__``
#    when the name isn't a normal module attribute).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_mutable_global_listed_in_all(name):
    """``name`` must be in ``clipboard.__all__`` for public-surface parity."""
    assert name in clip_mod.__all__, (
        f"{name!r} is missing from clipboard.__all__ — "
        f"``from voice_typer.server.clipboard import {name}`` would "
        f"fall back to a non-public ``__getattr__`` lookup that "
        f"static analyzers can't see."
    )


# ---------------------------------------------------------------------------
# 3. THE CORE CONTRACT: monkeypatching the SOURCE module's global IS
#    visible through the clipboard package's attribute access. This is
# the test called out in the  acceptance criterion.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_monkeypatch_source_visible_via_reexport(name):
    """Mutating ``clipboard_target_safety.<name>`` IS visible via
    ``voice_typer.server.clipboard.<name>``.

    This is the SI-24 acceptance criterion. Before the PEP 562 fix, the
    re-export bound the import-time value, so this mutation would NOT
    be visible (the assertion would fail with a stale-value mismatch).
    """
    sentinel = object()
    original = getattr(safety_mod, name)
    setattr(safety_mod, name, sentinel)
    try:
        assert getattr(clip_mod, name) is sentinel, (
            f"clipboard.{name} did not reflect the mutated value on "
            f"clipboard_target_safety.{name} — the PEP 562 "
            f"``__getattr__`` is missing or shadowed by a stale "
            f"static import."
        )
    finally:
        setattr(safety_mod, name, original)


# ---------------------------------------------------------------------------
# 4. The dynamic-resolution contract is symmetric: a SECOND mutation
#    after the first access is also visible (proves we're not caching
#    the value on first lookup).
# ---------------------------------------------------------------------------


def test_repeated_access_reflects_each_mutation():
    """Each ``clipboard.<name>`` access reads the current source value.

    Guards against a future "cache the first lookup" regression that
    would re-introduce the SI-24 binding bug under a different guise.
    """
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


# ---------------------------------------------------------------------------
# 5. ``from voice_typer.server.clipboard import _PYATSPI_STATE_FOCUSED``
#    still works (the import machinery consults ``__getattr__`` when the
#    name isn't otherwise found). This guards against an accidental
#    removal from ``__all__`` combined with the static-import removal
#    that would break callers.
# ---------------------------------------------------------------------------


def test_from_import_still_resolves_via_getattr():
    """``from ... import _PYATSPI_STATE_FOCUSED`` resolves via __getattr__.

    The import statement captures the value AT IMPORT TIME (standard
    Python semantics — ``from X import Y`` is not dynamic), so this
    test only verifies the name is resolvable, NOT that subsequent
    mutations are visible through the imported binding. The dynamic
    visibility contract is covered by
    ``test_monkeypatch_source_visible_via_reexport`` above.
    """
    # Use exec so the ``from ... import`` runs at call time (not at
    # module-collection time) and we can observe the resolution.
    namespace: dict = {}
    exec(  # noqa: S102 — controlled test fixture
        "from voice_typer.server.clipboard import _PYATSPI_STATE_FOCUSED",
        namespace,
    )
    assert "_PYATSPI_STATE_FOCUSED" in namespace


# ---------------------------------------------------------------------------
# 6. ``__getattr__`` raises ``AttributeError`` for genuinely-unknown
#    names (preserves the standard module-lookup semantics — important
#    for ``hasattr`` checks and friendly error messages).
# ---------------------------------------------------------------------------


def test_getattr_raises_for_unknown_name():
    """Unknown names raise ``AttributeError`` (not silently return None)."""
    unknown_name = "_definitely_not_a_real_name"
    with pytest.raises(AttributeError, match="has no attribute '_definitely_not_a_real_name'"):
        getattr(clip_mod, unknown_name)


# ---------------------------------------------------------------------------
# 7. ``__dir__`` includes the dynamically-resolved names so ``dir(clip_mod)``
#    and ``hasattr``-based discovery keep working.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _MUTABLE_GLOBALS)
def test_dir_includes_dynamic_names(name):
    """``dir(clip_mod)`` lists the dynamically-resolved globals."""
    assert name in dir(clip_mod), (
        f"{name!r} is missing from dir(clipboard) — the PEP 562 "
        f"``__dir__`` hook should append dynamically-resolved "
        f"mutable globals to the default module dir()."
    )
