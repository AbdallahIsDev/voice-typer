"""Direct regression tests for ``voice_typer/server/handlers/privacy_handlers.py``."""

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
    """The module must be importable without side effects."""
    assert PrivacyHandlersMixin is not None


def test_re_export_matches_canonical_class() -> None:
    """``from voice_typer.server.handlers import PrivacyHandlersMixin``"""
    assert ReExportedMixin is PrivacyHandlersMixin


def test_mixin_inherits_handler_base() -> None:
    """
    The mixin MUST inherit from :class:`HandlerBase`.
    Dropping the base class would silently break that contract.
    """
    assert issubclass(PrivacyHandlersMixin, HandlerBase)


def test_mixin_is_constructible_without_args() -> None:
    """The stub mixin must construct without ``app`` / ``service``."""
    instance = PrivacyHandlersMixin()
    assert isinstance(instance, PrivacyHandlersMixin)


def test_removed_handler_methods_are_absent() -> None:
    """
    The legacy ``_handle_delete_all_personal_data`` /
    ``_handle_export_gdpr_bundle`` methods MUST NOT come back.
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
    """``__all__`` must expose exactly ``[\"PrivacyHandlersMixin\"]``."""
    assert privacy_handlers_all == ["PrivacyHandlersMixin"]


def test_module_remains_a_thin_stub() -> None:
    """
    The module must stay a thin stub (no inline handler logic).
    Per C-ARCH-1 (and the spirit of the original privacy-handler
    """
    source = inspect.getsource(
        __import__(
            "voice_typer.server.handlers.privacy_handlers",
            fromlist=["__doc__"],
        )
    )
    assert len(source.splitlines()) <= 120, (
        "privacy_handlers.py has grown past 120 lines, verify the "
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
    """The class must inherit the runtime-provided ``Any`` annotations"""
    import typing

    hints = typing.get_type_hints(PrivacyHandlersMixin)
    assert attr_name in hints, (
        f"PrivacyHandlersMixin must inherit the '{attr_name}: Any' "
        "annotation from HandlerMixinBase; an inheritance-chain "
        "refactor has dropped it."
    )


def test_respond_with_error_helper_is_inherited() -> None:
    """``_respond_with_error`` must be inherited from :class:`HandlerBase`."""
    assert hasattr(PrivacyHandlersMixin, "_respond_with_error")
    assert callable(PrivacyHandlersMixin._respond_with_error)
