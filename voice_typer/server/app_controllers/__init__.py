"""CR-24: VoiceTyperApp god-class decomposition — Round 2.

This package owns the four ``VoiceTyperApp`` methods that survived
RW-9 Phase 7 because of source-level tests pinning their bodies:

    - ``repaste_last``  → :class:`RepasteController.repaste_last`
    - ``undo_last``     → :class:`UndoController.undo_last`
    - ``_open_config_file`` → :class:`ConfigEditorLauncher.open_config_file`
      (+ :meth:`ConfigEditorLauncher._reload_config_under_lock`, CR-80)
    - ``restart_app``   → :class:`RestartController.restart_app`

``VoiceTyperApp`` keeps thin 1-line delegations on each public method
name so tests that do ``monkeypatch.setattr("voice_typer.server.app.
repaste_last", ...)`` or ``app.repaste_last()`` keep working unchanged.

A note on monkeypatching (mirrors the convention in
``settings_controller.py``): tests like the ``app`` fixture in
``tests/test_app.py`` and ``tests/test_app_cleanup.py`` replace
``voice_typer.server.app.is_windows`` / ``is_macos`` / ``is_linux`` /
``time.sleep`` / ``sys.exit`` / ``os._exit`` /
``_windows_open_with_default_app`` at call time.  To keep those
patches effective, the platform-helper names are looked up DYNAMICALLY
from the ``voice_typer.server.app`` module inside each controller
method rather than being captured at import time.  ``event_bus`` is
imported lazily inside ``RestartController.restart_app`` so the
``monkeypatch.setattr("voice_typer.server.event_bus.publish", ...)``
test seam keeps working (it patches the attribute on the
``event_bus`` module, not on ``app``).

CR-24 / CR-80 / CR-16 / CR-46 are tracked in the IMPROVE-mode run
notes; this package is the F6 deliverable.
"""

from voice_typer.server.app_controllers.config_editor_launcher import ConfigEditorLauncher
from voice_typer.server.app_controllers.repaste_controller import RepasteController
from voice_typer.server.app_controllers.restart_controller import RestartController
from voice_typer.server.app_controllers.undo_controller import UndoController

__all__ = [
    "ConfigEditorLauncher",
    "RepasteController",
    "RestartController",
    "UndoController",
]
