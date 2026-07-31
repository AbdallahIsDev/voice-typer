"""Tests for ``ServiceMixinBase`` concrete type annotations.

These tests verify that the shared base class for the eight service
mixins declares its runtime-provided attributes with concrete types
(not ``Any``). The previous ``Any`` scaffold silenced:

* ``bad-override-mutable-attribute`` pyrefly errors when
  :class:`VoiceTyperService.__init__` re-bound the attributes with
  narrowed runtime types (3 baselined errors at
  ``service/__init__.py:199/214/220``).
* ``missing-attribute`` errors on every mixin module that touched
  ``self._app`` / ``self._download_cancel_lock`` / etc. (many
  baselined errors across ``service/dictation.py``,
  ``service/history.py``, ``service/model.py``, etc.) — pyrefly did
  not see the inherited ``Any`` declaration as a real attribute.

The concrete types also enforce that
:meth:`VoiceTyperService.__init__` actually INITIALISES each
attribute (a missing init would manifest as ``AttributeError`` at
runtime the first time a mixin reads it — see the regression test
``test_active_download_id_initialised_to_none`` for the concrete
case of ``_active_download_id`` previously being unset).

Note: we use bare PEP 526 class-level annotations (NOT ``ClassVar``).
``ClassVar`` would forbid instance assignment in
:meth:`VoiceTyperService.__init__` (pyrefly: "Cannot set field
[read-only]"). Bare annotations declare the attribute's type without
binding a value at class-definition time, so subclasses can freely
bind the value via ``self.X = ...`` in ``__init__``.
"""

from __future__ import annotations

import threading
from typing import Any, get_type_hints

from voice_typer.server.service._base import ServiceMixinBase

# ── Static-introspection tests ────────────────────────────────────────


def test_servicemixinbase_declares_all_runtime_attributes() -> None:
    """All 11 runtime-provided attributes are declared on
    :class:`ServiceMixinBase` (so subclasses inherit the type
    contract)."""
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
    """Each declared attribute is annotated with a concrete type — NOT
    ``Any``. ``Any`` was the previous scaffold that silenced
    shape-mismatch errors between the mixin base and the concrete
    :class:`VoiceTyperService` subclass.

    We resolve the type hints via :func:`typing.get_type_hints` (which
    evaluates forward references) and assert each hint is NOT ``Any``.
    """
    # Provide the TYPE_CHECKING-only imports in the localns so the
    # forward references ("AppProtocol", "ConfigApplier",
    # "OnboardingController | None") resolve.
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
            f"{attr_name} is annotated as ``Any`` — this is the pre-fix "
            "scaffold that silenced shape-mismatch errors. Use a concrete "
            "type (threading.Lock, dict[str, threading.Event], str | None, "
            "etc.) instead."
        )


def test_concrete_types_match_runtime_bindings() -> None:
    """The concrete type annotations on :class:`ServiceMixinBase` match
    the types that :meth:`VoiceTyperService.__init__` actually binds at
    runtime. This is the acceptance test for the root cause described
    in DT-48 / UE-37: ``Any`` annotations hid shape mismatches between
    the mixin base and the concrete service; now the types must align.
    """
    from voice_typer.server.config_applier import ConfigApplier
    from voice_typer.server.onboarding import OnboardingController
    from voice_typer.server.providers import AppProtocol

    localns = {
        "AppProtocol": AppProtocol,
        "ConfigApplier": ConfigApplier,
        "OnboardingController": OnboardingController,
    }
    hints = get_type_hints(ServiceMixinBase, include_extras=True, localns=localns)

    # Spot-check the most security-critical attributes (the ones the
    # previous Any scaffold was hiding shape mismatches on).
    assert hints["_download_cancel_events"] == dict[str, threading.Event], (
        "_download_cancel_events must be annotated as "
        "dict[str, threading.Event] to match the runtime binding in "
        "VoiceTyperService.__init__."
    )

    model_status_cache_hint = hints["_model_status_cache"]
    # Use stringified comparison for the union type to avoid
    # `types.UnionType` vs `typing.Union` Python-version differences.
    assert "dict" in str(model_status_cache_hint) and "None" in str(model_status_cache_hint), (
        "_model_status_cache must be annotated as dict[str, object] | None "
        f"to match the runtime binding — got {model_status_cache_hint!r}."
    )

    active_download_id_hint = hints["_active_download_id"]
    assert "str" in str(active_download_id_hint) and "None" in str(active_download_id_hint), (
        "_active_download_id must be annotated as str | None so the "
        "None-init in VoiceTyperService.__init__ type-checks AND so "
        "cancel_model_download's read of the attribute sees a real type."
    )


# ── Runtime-behavior regression tests ─────────────────────────────────


class _FakeApp:
    """Minimal fake app satisfying the AppProtocol surface used by
    :class:`VoiceTyperService.__init__`."""

    config = type("FakeConfig", (), {})()


def test_active_download_id_initialised_to_none(tmp_config_dir) -> None:
    """``_active_download_id`` is initialised to ``None`` by
    :meth:`VoiceTyperService.__init__`.

    Pre-fix, this attribute was only set inside
    :meth:`ModelMixin._register_download`. A call to
    :meth:`ModelMixin.cancel_model_download` BEFORE any download was
    registered raised ``AttributeError`` because ``__init__`` never
    bound the attribute. The concrete type declaration on
    :class:`ServiceMixinBase` makes the type contract explicit; this
    test makes the runtime initialization contract enforceable.
    """
    from voice_typer.server.service import VoiceTyperService

    service = VoiceTyperService(_FakeApp())
    assert service._active_download_id is None, (
        "_active_download_id must be initialised to None in "
        "VoiceTyperService.__init__ so cancel_model_download can "
        "safely read it before any download is registered."
    )


def test_cancel_model_download_returns_false_when_no_download_active(
    tmp_config_dir,
) -> None:
    """End-to-end regression: ``cancel_model_download`` must NOT raise
    ``AttributeError`` on a fresh service with no active download."""
    from voice_typer.server.service import VoiceTyperService

    service = VoiceTyperService(_FakeApp())
    result = service.cancel_model_download()
    assert result == {"cancelled": False}


def test_download_cancel_events_initialised_empty(tmp_config_dir) -> None:
    """The per-download cancellation dict is initialised to ``{}`` and
    its lock is a real :class:`threading.Lock`."""
    from voice_typer.server.service import VoiceTyperService

    service = VoiceTyperService(_FakeApp())
    assert service._download_cancel_events == {}
    assert isinstance(service._download_cancel_lock, type(threading.Lock()))


def test_model_status_cache_state_initialised(tmp_config_dir) -> None:
    """The model-status short-TTL cache state is initialised by
    :meth:`VoiceTyperService.__init__` so :meth:`ModelMixin.get_model_status`
    can read it without an ``AttributeError``."""
    from voice_typer.server.service import VoiceTyperService

    service = VoiceTyperService(_FakeApp())
    assert service._model_status_cache is None
    assert service._model_status_cache_ts == 0.0
    assert isinstance(service._model_status_cache_lock, type(threading.Lock()))


def test_onboarding_initialised_to_none(tmp_config_dir) -> None:
    """``_onboarding`` is initialised to ``None`` so the
    ``getattr(self, "_onboarding", None)`` defensive reads in
    ``service/onboarding.py`` resolve to a typed value AND so the
    concrete type annotation on :class:`ServiceMixinBase` is honoured
    at runtime."""
    from voice_typer.server.service import VoiceTyperService

    service = VoiceTyperService(_FakeApp())
    assert service._onboarding is None
