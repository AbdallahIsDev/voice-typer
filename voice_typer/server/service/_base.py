"""Shared base for service-layer mixins.

Mirrors the :class:`HandlerMixinBase` pattern from
``voice_typer/server/handlers/_base.py``. The eight service mixins
(``ModelMixin``, ``MicrophoneTestMixin``, ``HistoryMixin``,
``DictationMixin``, ``StatusMixin``, ``OnboardingMixin``,
``TemplateMixin``, ``VocabularyMixin``) all access runtime-provided
attributes (``self._app``, the per-download cancel state, the
microphone short-TTL cache, the model-status short-TTL cache, etc.)
that are only *assigned* inside :meth:`VoiceTyperService.__init__`.

Without a declaration on the mixin's class hierarchy, pyrefly's
null-safety analysis reports "attribute access before assignment" on
every ``self._app`` / ``self._download_cancel_lock`` / ... access in
the mixin modules.

Centralizing the declarations on :class:`ServiceMixinBase` removes the
duplication (each mixin previously had to re-declare the same
annotation block, or relied on the silent fallback to ``Any``).

The annotations are now concrete types (replacing the previous ``Any``
scaffold) so:

* pyrefly's null-safety check sees a declared attribute with a real
  type — closing the loophole where ``Any`` silenced shape-mismatch
  errors between the mixin base and the concrete
  :class:`VoiceTyperService`.
* mixin modules stay decoupled from the concrete service / app types
  via ``TYPE_CHECKING``-only imports of the concrete classes
  (:class:`AppProtocol`, :class:`ConfigApplier`,
  :class:`OnboardingController`). The strings used as forward
  references resolve at type-check time without forcing a runtime
  import (and a possible cycle).
* :class:`VoiceTyperService` composes any number of mixins via
  multiple inheritance without the mixins needing to know about each
  other.

The annotations are bare PEP 526 class-level type hints (NOT
``ClassVar``). ``ClassVar`` would forbid instance assignment in
:meth:`VoiceTyperService.__init__` (pyrefly: "Cannot set field
[read-only]"). Bare annotations declare the attribute's type without
binding a value at class-definition time, so subclasses can freely
bind the value via ``self.X = ...`` in ``__init__``. This is the same
pattern :class:`HandlerMixinBase` uses in
``voice_typer/server/handlers/_base.py``.

Subclasses MUST NOT override these annotations — the runtime binding
happens in :meth:`VoiceTyperService.__init__`, not here.

This class has NO state of its own, NO methods, and NO side effects at
import time. It is a pure type-annotation container.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # TYPE_CHECKING-only imports keep the mixin modules decoupled from
    # the concrete service / app / onboarding types at runtime. The
    # forward references below resolve at type-check time only.
    from voice_typer.server.config_applier import ConfigApplier
    from voice_typer.server.onboarding import OnboardingController
    from voice_typer.server.providers import AppProtocol


class ServiceMixinBase:
    """Common base for service-layer mixins.

        Declares the runtime-provided attributes that every service mixin
        accesses via ``self.X``. The annotations are concrete types (not
        ``Any``) so pyrefly's null-safety check sees a declared attribute
        with a real type AND so the type system can catch shape mismatches
        between this base and the concrete :class:`VoiceTyperService`
        subclass that binds the values at runtime.

        The annotations are bare class-level type hints (NOT ``ClassVar``)
        so :meth:`VoiceTyperService.__init__` can freely bind the values
        via ``self.X = ...``. ``ClassVar`` would forbid instance
        assignment (pyrefly: "Cannot set field [read-only]").

        The attributes are bound at runtime by
        :meth:`VoiceTyperService.__init__` in
        ``voice_typer/server/service/__init__.py``:

        * ``_app`` — the wrapped :class:`AppProtocol` instance.
        * ``_config_applier`` — the :class:`ConfigApplier` that owns the
          config-mutation lock + rollback logic.
        * ``_download_cancel_lock`` / ``_download_cancel_events`` /
          ``_active_download_id`` — per-download cancellation state for
    ``ModelMixin.download_model`` ( / SERVICE-1).
          ``_active_download_id`` is initialised to ``None`` by
          ``VoiceTyperService.__init__`` so ``cancel_model_download`` can
          safely read it before any download has been registered.
        * ``_microphones_cache`` / ``_microphones_cache_ts`` — short-TTL
          cache for ``MicrophoneTestMixin.refresh_microphones``
    (PERF-). Bound by ``MicrophoneTestMixin.__init__``.
        * ``_model_status_cache`` / ``_model_status_cache_ts`` /
          ``_model_status_cache_lock`` — short-TTL cache for
          ``ModelMixin.get_model_status`` (PERF-10 / SVC-9).
        * ``_onboarding`` — live :class:`OnboardingController` held between
          ``OnboardingMixin.onboarding_start`` and
          ``OnboardingMixin.onboarding_apply``. Initialised to ``None`` by
          ``VoiceTyperService.__init__`` so the ``getattr(self,
          "_onboarding", None)`` defensive reads in ``onboarding.py``
          resolve to a typed value.
    """

    # Provided at runtime by VoiceTyperService.__init__ via
    # multiple inheritance. Declared here once so each service mixin
    # doesn't repeat the annotation block. Concrete types (replacing
    # the previous ``Any`` scaffold) so the type system enforces the
    # contract between the mixin base and the concrete service —
    # closing the loophole where ``Any`` silenced shape-mismatch
    # errors.
    #
    # Bare annotations (NOT ClassVar): ``ClassVar`` would forbid
    # instance assignment in VoiceTyperService.__init__. Bare
    # annotations declare the attribute's type without binding a value
    # at class-definition time.
    _app: AppProtocol
    _config_applier: ConfigApplier
    _download_cancel_lock: threading.Lock
    _download_cancel_events: dict[str, threading.Event]
    _active_download_id: str | None
    _microphones_cache: list | None
    _microphones_cache_ts: float
    _model_status_cache: dict[str, object] | None
    _model_status_cache_lock: threading.Lock
    _model_status_cache_ts: float
    _onboarding: OnboardingController | None


__all__ = ["ServiceMixinBase"]
