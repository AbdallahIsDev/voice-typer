"""IPC handler mixins for :class:`IPCServer`."""

from voice_typer.server.handlers.cloud_test_handlers import (
    CloudTestHandlersMixin,
)
from voice_typer.server.handlers.config_handlers import ConfigHandlersMixin
from voice_typer.server.handlers.dictation_handlers import DictationHandlersMixin
from voice_typer.server.handlers.history_handlers import HistoryHandlersMixin
from voice_typer.server.handlers.level_monitor_handlers import (
    LevelMonitorHandlersMixin,
)
from voice_typer.server.handlers.microphone_handlers import MicrophoneHandlersMixin
from voice_typer.server.handlers.microphone_test_handlers import (
    MicrophoneTestHandlersMixin,
)
from voice_typer.server.handlers.model_handlers import ModelHandlersMixin
from voice_typer.server.handlers.onboarding_handlers import OnboardingHandlersMixin
from voice_typer.server.handlers.privacy_handlers import PrivacyHandlersMixin
from voice_typer.server.handlers.repaste_handlers import RepasteHandlersMixin
from voice_typer.server.handlers.status_handlers import StatusHandlersMixin
from voice_typer.server.handlers.system_handlers import SystemHandlersMixin
from voice_typer.server.handlers.templates_handlers import TemplatesHandlersMixin
from voice_typer.server.handlers.vocabulary_automation_handlers import (
    VocabularyAutomationHandlersMixin,
)
from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin

__all__ = [
    "CloudTestHandlersMixin",
    "ConfigHandlersMixin",
    "StatusHandlersMixin",
    "DictationHandlersMixin",
    "HistoryHandlersMixin",
    "MicrophoneHandlersMixin",
    "VocabularyHandlersMixin",
    "TemplatesHandlersMixin",
    "OnboardingHandlersMixin",
    "MicrophoneTestHandlersMixin",
    "LevelMonitorHandlersMixin",
    "ModelHandlersMixin",
    "SystemHandlersMixin",
    "VocabularyAutomationHandlersMixin",
    "PrivacyHandlersMixin",
    # ``RepasteHandlersMixin`` () is part of the package
    "RepasteHandlersMixin",
]
