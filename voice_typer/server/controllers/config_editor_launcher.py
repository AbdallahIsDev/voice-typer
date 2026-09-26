"""Extracted from :meth:`voice_typer.server.app.LausuApp._open_config_file`.
The controller holds a reference to the owning app (``app``) and opens
``config.json`` in the OS's default editor via the existing
holds ``_config_mutation_lock`` for the full editor session and
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.app import LausuApp

log = logging.getLogger(__name__)


class ConfigEditorLauncher:
    """Opens ``config.json`` in the user's default editor."""

    def __init__(self, app: LausuApp) -> None:
        self._app = app

    def open(self) -> None:
        """Open ``config.json`` in the user's default editor."""
        config_file = self._app.config.config_dir / "config.json"
        # Imported locally to avoid a top-level name clash with this
        from voice_typer.server.config_editor import (
            ConfigEditorLauncher as _ConfigEditorLauncherImpl,
        )

        _ConfigEditorLauncherImpl(self._app).launch(config_file)
