"""IPC handler mixins for :class:`IPCServer`.

extracted from ``voice_typer/server/ipc_server.py``.

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
classes.  The mixins import their helpers directly from the canonical
``ipc/`` leaf submodules (``voice_typer.server.ipc.validation`` for
``_validate_dict_payload`` / ``_error_response``,
``voice_typer.server.ipc.history_bounds`` for ``_bound_history_limit``
/ ``_bound_history_offset`` / ``_sanitize_config_for_ipc``) and from
``voice_typer.server.handlers._base`` / ``handlers._log`` for the
shared base class and logger — they do NOT import from
:mod:`voice_typer.server.ipc_server`, so there is no circular import
to break.  (A ``sys.modules`` canonical-name shim that used to live in
``ipc_server.py`` was removed in , 2026-07-22, after this import
pattern was confirmed.)
"""

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

# (FA16, 2026-07-19): ``PrivacyHandlersMixin`` was defined
# in ``privacy_handlers.py`` ( / ) and imported by
# ``ipc_server.py`` at module load time, but was missing from this
# package's ``__all__`` re-export list — same defect class as the
# ``RepasteHandlersMixin`` fix below. External callers doing
# ``from voice_typer.server.handlers import PrivacyHandlersMixin``
# would have hit ``ImportError``. Adding it here makes the package
# re-export match the actual set of mixin classes.
from voice_typer.server.handlers.privacy_handlers import PrivacyHandlersMixin

# (IMPROVE-mode run, 2026-07-19): ``RepasteHandlersMixin`` was
# defined in ``repaste_handlers.py`` () and imported by
# ``ipc_server.py`` at module load time, but was missing from this
# package's ``__all__`` re-export list. External callers doing
# ``from voice_typer.server.handlers import RepasteHandlersMixin``
# would have hit ``ImportError``. Adding it here makes the package
# re-export match the actual set of mixin classes.
from voice_typer.server.handlers.repaste_handlers import RepasteHandlersMixin
from voice_typer.server.handlers.status_handlers import StatusHandlersMixin
from voice_typer.server.handlers.system_handlers import SystemHandlersMixin
from voice_typer.server.handlers.templates_handlers import TemplatesHandlersMixin
from voice_typer.server.handlers.vocabulary_automation_handlers import (
    VocabularyAutomationHandlersMixin,
)
from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin

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
    # ``PrivacyHandlersMixin`` ( / ) is part of
    # the package re-export surface — it was previously defined in
    # ``privacy_handlers.py`` and imported by ``ipc_server.py`` but
    # missing from ``__all__`` (same defect class as ).
    "PrivacyHandlersMixin",
    # ``RepasteHandlersMixin`` () is part of the package
    # re-export surface — it was previously defined in
    # ``repaste_handlers.py`` and imported by ``ipc_server.py`` but
    # missing from ``__all__``.
    "RepasteHandlersMixin",
]
