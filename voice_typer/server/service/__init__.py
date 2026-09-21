"""Service package composition."""

import logging
from typing import TYPE_CHECKING, TypedDict

from voice_typer.server.branding import APP_NAME
from voice_typer.server.config_applier import ConfigApplier  # noqa: F401  -- re-exported for back-compat
from voice_typer.server.service.dictation import DictationMixin
from voice_typer.server.service.history import HistoryMixin
from voice_typer.server.service.microphone_test import MicrophoneTestMixin
from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
from voice_typer.server.service.onboarding import OnboardingMixin
from voice_typer.server.service.status import StatusMixin
from voice_typer.server.service.template import TemplateMixin
from voice_typer.server.service.vocabulary import VocabularyMixin

from .config_service import ConfigMutationMixin
from .privacy import PrivacyMixin

if TYPE_CHECKING:
    # Imported only under ``TYPE_CHECKING`` so the annotation
    from voice_typer.server.providers import AppProtocol  # noqa: F401
    from voice_typer.server.templates import TemplateManager  # noqa: F401

log = logging.getLogger(__name__)


# These replace bare ``dict`` annotations so static type checkers (and


class StatusResponse(TypedDict):
    """Response shape of :meth:`VoiceTyperService.get_status`."""

    status: str
    # The tray-tooltip reason for the current state. MUST stay in lockstep
    message: str
    xruns_since_start: int
    loaded_via: str
    config_dir: str
    offline_pack: dict[str, object]


# the four ``DownloadXxx`` TypedDicts + ``DownloadResult`` union


class ForceCancelResult(TypedDict):
    """Response shape of :meth:`VoiceTyperService.force_cancel_transcription`."""

    success: bool
    message: str


class VoiceTyperService(
    HistoryMixin,
    ModelMixin,
    OnboardingMixin,
    MicrophoneTestMixin,
    VocabularyMixin,
    TemplateMixin,
    StatusMixin,
    DictationMixin,
    PrivacyMixin,
    ConfigMutationMixin,
):
    """Service facade over VoiceTyperApp.

    This class wraps the app's public methods in a transport-agnostic
    interface.  The IPC server (or any future transport) calls these
    methods instead of touching the app directly.

    All domain methods live on the composed mixins
    (``HistoryMixin``, ``ModelMixin``, ``OnboardingMixin``,
    ``MicrophoneTestMixin``, ``VocabularyMixin``, ``TemplateMixin``,
    ``StatusMixin``, ``DictationMixin``, ``PrivacyMixin``,
    ``ConfigMutationMixin``). This class owns
    ONLY ``__init__``, ``restart``, and ``quit``: config-mutation,
    GDPR, and every other domain surface resolve via
    MRO to the mixin copies, which are the single source of truth
    (no method or constant is duplicated on this class).
    """

    def __init__(self, app: "AppProtocol") -> None:
        self._app = app
        # Delegate config side-effects + apply_config to
        self._config_applier = ConfigApplier(self)
        # delegate state initialisation to the owning mixins
        ModelMixin.__init__(self)
        # defensive reads in ``service/onboarding.py`` resolve to a
        self._onboarding = None
        # ``_microphones_cache`` initialised to ``None``.
        MicrophoneTestMixin.__init__(self)

    def restart(self) -> None:
        """Restart the application."""
        self._app.restart_app()

    def quit(self) -> None:
        """Quit the application."""
        self._app.quit_app()


__all__ = [
    "APP_NAME",
    "ConfigApplier",
    "ForceCancelResult",
    "StatusResponse",
    "VoiceTyperService",
    "_MODEL_STATUS_CACHE_TTL_S",
]
