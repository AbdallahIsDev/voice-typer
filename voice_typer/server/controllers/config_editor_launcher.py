"""Config editor launcher controller.

Extracted from :meth:`voice_typer.server.app.VoiceTyperApp._open_config_file`.
The controller holds a reference to the owning app (``app``) and opens
``config.json`` in the OS's default editor via the existing
:class:`voice_typer.server.config_editor.ConfigEditorLauncher`, which
holds ``_config_mutation_lock`` for the full editor session and
reloads the config from disk afterwards. Behaviour is preserved
verbatim — only the class boundary moved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class ConfigEditorLauncher:
    """Opens ``config.json`` in the user's default editor.

    The app passes itself (``app``) so the controller can resolve the
    config directory (``app.config.config_dir``) and construct the
    underlying launcher with the same app reference (so
    ``_config_mutation_lock``, ``config``, and ``tray`` remain
    accessible to the platform-specific launch path).
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def open(self) -> None:
        """Open ``config.json`` in the user's default editor.

        Constructs the platform-specific
        :class:`voice_typer.server.config_editor.ConfigEditorLauncher`
        with the owning app and delegates to its ``launch()`` method,
        preserving the XPLAT-01 / SEC-audit-011 / B-4 / CR-015 lock +
        reload invariants.
        """
        config_file = self._app.config.config_dir / "config.json"
        # Imported locally to avoid a top-level name clash with this
        # controller class (both are named ``ConfigEditorLauncher``).
        from voice_typer.server.config_editor import (
            ConfigEditorLauncher as _ConfigEditorLauncherImpl,
        )

        _ConfigEditorLauncherImpl(self._app).launch(config_file)
