"""Service mixin base."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # TYPE_CHECKING-only imports keep the mixin modules decoupled from
    from voice_typer.server.config_applier import ConfigApplier
    from voice_typer.server.onboarding import OnboardingController
    from voice_typer.server.providers import AppProtocol


class ServiceMixinBase:
    """``Any``) so pyrefly's null-safety check sees a declared attribute
    assignment (pyrefly: "Cannot set field [read-only]").
      ``ModelMixin.get_model_status`` (PERF-10 / SVC-9).
      "_onboarding", None)`` defensive reads in ``onboarding.py``
    """

    # Provided at runtime by VoiceTyperService.__init__ via
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
