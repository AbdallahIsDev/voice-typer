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
the mixin modules (47 errors at the time this base was introduced).

Centralizing the declarations on :class:`ServiceMixinBase` removes the
duplication (each mixin previously had to re-declare the same
``Any`` annotation block, or relied on the silent fallback to
``Any``). The annotations are ``Any`` (not a Protocol) so:

* pyrefly's null-safety check sees a declared attribute (no
  "attribute access before assignment" error);
* mixin modules stay decoupled from the concrete
  :class:`VoiceTyperService` / :class:`VoiceTyperApp` types
  (MagicMock fixtures in ``tests/handlers/`` and ``tests/app/`` satisfy
  the loose ``Any`` typing without needing the real service classes);
* :class:`VoiceTyperService` composes any number of mixins via
  multiple inheritance without the mixins needing to know about each
  other.

Subclasses MUST NOT override these annotations — the runtime binding
happens in :meth:`VoiceTyperService.__init__`, not here.

This class has NO state of its own, NO methods, and NO side effects at
import time. It is a pure type-annotation container.
"""

from __future__ import annotations

from typing import Any


class ServiceMixinBase:
    """Common base for service-layer mixins.

    Declares the runtime-provided attributes that every service mixin
    accesses via ``self.X``. The annotations are ``Any`` so pyrefly's
    null-safety check sees a declared attribute while keeping the
    mixins decoupled from the concrete service / app types (see the
    module docstring for the full rationale).

    The attributes are bound at runtime by
    :meth:`VoiceTyperService.__init__` in
    ``voice_typer/server/service/__init__.py``:

    * ``_app`` — the wrapped :class:`VoiceTyperApp` instance.
    * ``_config_applier`` — the :class:`ConfigApplier` that owns the
      config-mutation lock + rollback logic.
    * ``_download_cancel_lock`` / ``_download_cancel_events`` /
      ``_active_download_id`` — per-download cancellation state for
      ``ModelMixin.download_model`` (HIGH-8 / SERVICE-1).
    * ``_microphones_cache`` / ``_microphones_cache_ts`` — short-TTL
      cache for ``MicrophoneTestMixin.refresh_microphones``
      (PERF-FIX-1).
    * ``_model_status_cache`` / ``_model_status_cache_ts`` /
      ``_model_status_cache_lock`` — short-TTL cache for
      ``ModelMixin.get_model_status`` (PERF-10 / SVC-9).
    * ``_onboarding`` — live :class:`OnboardingController` held between
      ``OnboardingMixin.onboarding_start`` and
      ``OnboardingMixin.onboarding_apply``.
    """

    # Provided at runtime by VoiceTyperService.__init__ via
    # multiple inheritance. Declared here once so each service mixin
    # doesn't repeat the annotation block.
    _app: Any
    _config_applier: Any
    _download_cancel_lock: Any
    _download_cancel_events: Any
    _active_download_id: Any
    _microphones_cache: Any
    _microphones_cache_ts: Any
    _model_status_cache: Any
    _model_status_cache_lock: Any
    _model_status_cache_ts: Any
    _onboarding: Any


__all__ = ["ServiceMixinBase"]
