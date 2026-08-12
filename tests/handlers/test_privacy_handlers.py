"""Direct regression tests for ``voice_typer/server/handlers/privacy_handlers.py``.

The historical review entry (XS-79) cited this module as a 217-LOC file
with two untested IPC handler methods (``_handle_delete_all_personal_data``
and ``_handle_export_gdpr_bundle``). Those handler methods were removed
during the Tauri migration — the underlying GDPR service-layer methods
(``PrivacyMixin.delete_all_personal_data`` / ``export_gdpr_bundle``) are
now invoked by dedicated Rust commands with their own allowlist entries,
not via the generic Python dispatch path.

The service-layer methods are exhaustively covered by
``tests/test_gdpr_delete.py``, ``tests/test_gdpr_export.py`` and
``tests/test_privacy_helpers.py`` — those suites test the real
behavioral surface and continue to pass unchanged after the migration.

This file adds DIRECT tests for the handler-module STUB so that:

* the public re-export ``from voice_typer.server.handlers import
  PrivacyHandlersMixin`` keeps resolving (the MRO of
  :class:`IPCServer` depends on it);
* the mixin continues to inherit from :class:`HandlerBase` so the
  shared error-envelope machinery is available if a future change
  re-introduces privacy-specific IPC handlers here;
* the removed ``_handle_*`` methods do NOT silently come back as dead
  code (regression guard against re-introducing the legacy generic-
  dispatch route while the Tauri allowlist is the documented path);
* the module exposes the documented ``__all__`` surface so downstream
  wildcard imports remain stable.

These tests deliberately do NOT construct a live ``IPCServer`` — the
stub has no behavior to exercise through the dispatch path. They are
structural / import-time contracts.
"""

from __future__ import annotations

import inspect

import pytest
from voice_typer.server.handlers import PrivacyHandlersMixin as ReExportedMixin
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers.privacy_handlers import (
    PrivacyHandlersMixin,
    __all__ as privacy_handlers_all,
)


def test_privacy_handlers_module_is_importable() -> None:
    """The module must be importable without side effects.

    The ``IPCServer`` MRO pulls in ``PrivacyHandlersMixin`` at class
    construction time; an import error here would break the entire
    IPC server bootstrap. A bare ``import`` already happened at the
    top of this test file — this assertion exists so the test name
    documents the contract explicitly.
    """
    assert PrivacyHandlersMixin is not None


def test_re_export_matches_canonical_class() -> None:
    """``from voice_typer.server.handlers import PrivacyHandlersMixin``
    must resolve to the SAME class object as the canonical import.

    The package ``__all__`` re-export was previously missing for this
    mixin (a stale bug that has since been fixed); this test guards
    against a regression that re-removes the re-export.
    """
    assert ReExportedMixin is PrivacyHandlersMixin


def test_mixin_inherits_handler_base() -> None:
    """The mixin MUST inherit from :class:`HandlerBase`.

    Even though the stub has no handler methods, inheriting
    :class:`HandlerBase` ensures ``_respond_with_error`` is available
    if a future change re-introduces privacy-specific IPC handlers.
    Dropping the base class would silently break that contract.
    """
    assert issubclass(PrivacyHandlersMixin, HandlerBase)


def test_mixin_is_constructible_without_args() -> None:
    """The stub mixin must construct without ``app`` / ``service``.

    ``HandlerBase`` declares ``service`` / ``app`` / ``_send`` as
    class-level ``Any`` annotations (no defaults) — construction
    must still succeed because the annotations are not descriptors.
    If a future refactor turns them into ``__init__`` parameters,
    this test will fail loudly so the call sites can be updated.
    """
    instance = PrivacyHandlersMixin()
    assert isinstance(instance, PrivacyHandlersMixin)


def test_removed_handler_methods_are_absent() -> None:
    """The legacy ``_handle_delete_all_personal_data`` /
    ``_handle_export_gdpr_bundle`` methods MUST NOT come back.

    The Tauri migration moved GDPR IPC dispatch to dedicated Rust
    commands (with their own allowlist + consent prompts). The
    Python-side handler envelopes were deleted. Re-introducing them
    here would resurrect a parallel dispatch route that bypasses the
    Rust allowlist — a security regression.

    The class body is intentionally empty; if a future commit adds
    either method back, this test will fail so the reviewer is
    forced to either update the Tauri allowlist docs or revert.
    """
    assert not hasattr(PrivacyHandlersMixin, "_handle_delete_all_personal_data"), (
        "_handle_delete_all_personal_data was removed during the Tauri "
        "migration; the GDPR delete route is now a dedicated Rust "
        "command. Re-introducing the Python dispatch handler creates a "
        "parallel route that bypasses the Tauri allowlist."
    )
    assert not hasattr(PrivacyHandlersMixin, "_handle_export_gdpr_bundle"), (
        "_handle_export_gdpr_bundle was removed during the Tauri "
        "migration; the GDPR export route is now a dedicated Rust "
        "command. Re-introducing the Python dispatch handler creates a "
        "parallel route that bypasses the Tauri allowlist."
    )


def test_module_all_surface_is_stable() -> None:
    """``__all__`` must expose exactly ``["PrivacyHandlersMixin"]``.

    Downstream code (the ``voice_typer.server.handlers`` package
    ``__init__.py`` re-export, ``IPCServer`` MRO, external type
    stubs) depends on this exact surface. Silent additions or
    removals would break wildcard imports.
    """
    assert privacy_handlers_all == ["PrivacyHandlersMixin"]


def test_module_remains_a_thin_stub() -> None:
    """The module must stay a thin stub (no inline handler logic).

    Per C-ARCH-1 (and the spirit of the original privacy-handler
    refactor), the module exists to host privacy-specific IPC
    handlers IF they are ever re-introduced. Today it is an empty
    subclass of :class:`HandlerBase`. If the module grows past a
    small LOC budget, that is a signal the architecture is
    regressing toward inline dispatch logic and the change should
    be split into a focused module under ``handlers/``.

    The threshold is intentionally generous (120 LOC) to allow
    docstrings + future handler additions without flapping; the
    guard exists to catch a runaway re-implementation, not to
    enforce a strict line count.
    """
    source = inspect.getsource(
        __import__(
            "voice_typer.server.handlers.privacy_handlers",
            fromlist=["__doc__"],
        )
    )
    assert len(source.splitlines()) <= 120, (
        "privacy_handlers.py has grown past 120 lines — verify the "
        "module has not regressed into inline dispatch logic. If the "
        "growth is intentional (e.g. handler methods were re-added), "
        "bump this threshold deliberately and add focused tests for "
        "the new handlers."
    )


@pytest.mark.parametrize(
    "attr_name",
    ["service", "app", "_send"],
)
def test_inherited_annotations_are_present_on_class(attr_name: str) -> None:
    """The class must inherit the runtime-provided ``Any`` annotations
    (``service`` / ``app`` / ``_send``) from :class:`HandlerMixinBase`.

    These are pure annotations (no default values), so ``hasattr``
    returns ``False`` — the check goes through ``__annotations__``
    via :func:`typing.get_type_hints` which walks the MRO and
    materializes inherited annotations. If a future refactor
    detaches the inheritance (e.g. by re-declaring
    ``HandlerMixinBase`` directly), this test will fail because the
    annotations would no longer resolve.
    """
    import typing

    hints = typing.get_type_hints(PrivacyHandlersMixin)
    assert attr_name in hints, (
        f"PrivacyHandlersMixin must inherit the '{attr_name}: Any' "
        "annotation from HandlerMixinBase; an inheritance-chain "
        "refactor has dropped it."
    )


def test_respond_with_error_helper_is_inherited() -> None:
    """``_respond_with_error`` must be inherited from :class:`HandlerBase`.

    Unlike the bare ``Any`` annotations (which have no default
    values), ``_respond_with_error`` is a real method — so
    ``hasattr`` is the correct check here. If the inheritance is
    broken, the method disappears.
    """
    assert hasattr(PrivacyHandlersMixin, "_respond_with_error")
    assert callable(PrivacyHandlersMixin._respond_with_error)
