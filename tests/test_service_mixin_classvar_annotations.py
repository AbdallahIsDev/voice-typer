"""Tests for ``ServiceMixinBase`` concrete type annotations."""

from __future__ import annotations

import threading
from typing import Any, get_type_hints

from voice_typer.server.service._base import ServiceMixinBase


def test_servicemixinbase_declares_all_runtime_attributes() -> None:
    """:class:`ServiceMixinBase` (so subclasses inherit the type"""
    declared = set(getattr(ServiceMixinBase, "__annotations__", {}).keys())
    expected = {
        "_app",
        "_config_applier",
        "_download_cancel_lock",
        "_download_cancel_events",
        "_active_download_id",
        "_microphones_cache",
        "_microphones_cache_ts",
        "_model_status_cache",
        "_model_status_cache_lock",
        "_model_status_cache_ts",
        "_onboarding",
    }
    missing = expected - declared
    assert not missing, (
        "ServiceMixinBase must declare every runtime-provided attribute "
        "so the type contract is inherited by every service mixin. "
        f"Missing declarations: {sorted(missing)}"
    )


def test_annotations_are_concrete_types_not_any() -> None:
    """Each declared attribute is annotated with a concrete type, NOT"""
    from voice_typer.server.config_applier import ConfigApplier
    from voice_typer.server.onboarding import OnboardingController
    from voice_typer.server.providers import AppProtocol

    localns = {
        "AppProtocol": AppProtocol,
        "ConfigApplier": ConfigApplier,
        "OnboardingController": OnboardingController,
    }
    hints = get_type_hints(ServiceMixinBase, include_extras=True, localns=localns)
    for attr_name in (
        "_app",
        "_config_applier",
        "_download_cancel_lock",
        "_download_cancel_events",
        "_active_download_id",
        "_microphones_cache",
        "_microphones_cache_ts",
        "_model_status_cache",
        "_model_status_cache_lock",
        "_model_status_cache_ts",
        "_onboarding",
    ):
        assert attr_name in hints, f"{attr_name} missing from type hints"
        hint = hints[attr_name]
        # Annotation must NOT be Any (the previous scaffold).
        assert hint is not Any, (
            f"{attr_name} is annotated as ``Any``, this is the pre-fix "
            "scaffold that silenced shape-mismatch errors. Use a concrete "
            "type (threading.Lock, dict[str, threading.Event], str | None, "
            "etc.) instead."
        )


def test_concrete_types_match_runtime_bindings() -> None:
    """The concrete type annotations on :class:`ServiceMixinBase` match"""
    from voice_typer.server.config_applier import ConfigApplier
    from voice_typer.server.onboarding import OnboardingController
    from voice_typer.server.providers import AppProtocol

    localns = {
        "AppProtocol": AppProtocol,
        "ConfigApplier": ConfigApplier,
        "OnboardingController": OnboardingController,
    }
    hints = get_type_hints(ServiceMixinBase, include_extras=True, localns=localns)

    assert hints["_download_cancel_events"] == dict[str, threading.Event], (
        "_download_cancel_events must be annotated as "
        "dict[str, threading.Event] to match the runtime binding in "
        "LausuService.__init__."
    )

    model_status_cache_hint = hints["_model_status_cache"]
    # Use stringified comparison for the union type to avoid
    assert "dict" in str(model_status_cache_hint) and "None" in str(model_status_cache_hint), (
        "_model_status_cache must be annotated as dict[str, object] | None "
        f"to match the runtime binding, got {model_status_cache_hint!r}."
    )

    active_download_id_hint = hints["_active_download_id"]
    assert "str" in str(active_download_id_hint) and "None" in str(active_download_id_hint), (
        "_active_download_id must be annotated as str | None so the "
        "None-init in LausuService.__init__ type-checks AND so "
        "cancel_model_download's read of the attribute sees a real type."
    )


class _FakeApp:
    """Minimal fake app satisfying the AppProtocol surface used by"""

    config = type("FakeConfig", (), {})()


def test_active_download_id_initialised_to_none(tmp_config_dir) -> None:
    """:meth:`LausuService.__init__`."""
    from voice_typer.server.service import LausuService

    service = LausuService(_FakeApp())
    assert service._active_download_id is None, (
        "_active_download_id must be initialised to None in "
        "LausuService.__init__ so cancel_model_download can "
        "safely read it before any download is registered."
    )


def test_cancel_model_download_returns_false_when_no_download_active(
    tmp_config_dir,
) -> None:
    """End-to-end regression: ``cancel_model_download`` must NOT raise"""
    from voice_typer.server.service import LausuService

    service = LausuService(_FakeApp())
    result = service.cancel_model_download()
    assert result == {"cancelled": False}


def test_download_cancel_events_initialised_empty(tmp_config_dir) -> None:
    """The per-download cancellation dict is initialised to ``{}`` and"""
    from voice_typer.server.service import LausuService

    service = LausuService(_FakeApp())
    assert service._download_cancel_events == {}
    assert isinstance(service._download_cancel_lock, type(threading.Lock()))


def test_model_status_cache_state_initialised(tmp_config_dir) -> None:
    """:meth:`LausuService.__init__` so :meth:`ModelMixin.get_model_status`"""
    from voice_typer.server.service import LausuService

    service = LausuService(_FakeApp())
    assert service._model_status_cache is None
    assert service._model_status_cache_ts == 0.0
    assert isinstance(service._model_status_cache_lock, type(threading.Lock()))


def test_onboarding_initialised_to_none(tmp_config_dir) -> None:
    """concrete type annotation on :class:`ServiceMixinBase` is honoured"""
    from voice_typer.server.service import LausuService

    service = LausuService(_FakeApp())
    assert service._onboarding is None
