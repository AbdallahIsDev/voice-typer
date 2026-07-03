"""IPC handler mixins for :class:`IPCServer`.

ARCH-REFAC-002: extracted from ``voice_typer/server/ipc_server.py``.

Each module in this package defines a *mixin* class containing the
``_handle_<cmd>`` methods for one logical group of IPC commands (config,
status, history, etc.).  The mixins access ``self.app`` and
``self.service`` via the host :class:`IPCServer` instance — they have no
state of their own.

:class:`IPCServer` inherits from all of these mixins, so the existing
``_COMMAND_REGISTRY`` lookup (``getattr(self, handler_name)``) finds the
``_handle_*`` methods via the normal MRO.  This is a mechanical split:
zero behavior change, no new public API, no IPC protocol change.

Importing this package has no side effects beyond defining the mixin
classes.  The mixins themselves import module-level helpers (``log``,
``_push_event_now``, ``_bound_history_limit`` …) from
:mod:`voice_typer.server.ipc_server` at module load time; those helpers
are defined *before* the ``IPCServer`` class body in ``ipc_server.py``,
so the circular import is safe (Python resolves it via the partially
initialized module that is already in ``sys.modules``).
"""

from voice_typer.server.handlers.config_handlers import ConfigHandlersMixin
from voice_typer.server.handlers.status_handlers import StatusHandlersMixin
from voice_typer.server.handlers.dictation_handlers import DictationHandlersMixin
from voice_typer.server.handlers.history_handlers import HistoryHandlersMixin
from voice_typer.server.handlers.microphone_handlers import MicrophoneHandlersMixin
from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin
from voice_typer.server.handlers.templates_handlers import TemplatesHandlersMixin
from voice_typer.server.handlers.onboarding_handlers import OnboardingHandlersMixin
from voice_typer.server.handlers.microphone_test_handlers import (
    MicrophoneTestHandlersMixin,
)
from voice_typer.server.handlers.level_monitor_handlers import (
    LevelMonitorHandlersMixin,
)
from voice_typer.server.handlers.model_handlers import ModelHandlersMixin
from voice_typer.server.handlers.system_handlers import SystemHandlersMixin
from voice_typer.server.handlers.vocabulary_automation_handlers import (
    VocabularyAutomationHandlersMixin,
)

__all__ = [
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
]
