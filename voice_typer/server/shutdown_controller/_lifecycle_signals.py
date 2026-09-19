"""instance-method API used by tests and the ``VoiceTyperApp`` wiring.
Split verbatim out of the pre-split ``shutdown_controller`` module.
"""

from __future__ import annotations


class SignalsMixin:
    """Lifecycle + signal-handler delegate mixin for :class:`ShutdownController`."""

    # extraction: the bodies of ``quit`` and

    def quit(self):
        """(``controller.quit()``) and the ``VoiceTyperApp`` wiring"""
        from voice_typer.server.shutdown.lifecycle import quit as _quit

        _quit(self)

    def _arm_shutdown_watchdog(self, timeout_s: float) -> None:
        """arm a daemon-thread watchdog that calls"""
        from voice_typer.server.shutdown.lifecycle import (
            arm_shutdown_watchdog,
        )

        arm_shutdown_watchdog(self, timeout_s)

    def _atexit_log(self) -> None:
        """``atexit.register(self._atexit_log)`` in ``VoiceTyperApp.start()``."""
        from voice_typer.server.atexit_safety import atexit_log

        atexit_log(self)

    def _atexit_cleanup(self) -> None:
        """(``controller._atexit_cleanup()``) and the ``VoiceTyperApp``"""
        from voice_typer.server.atexit_safety import atexit_cleanup

        atexit_cleanup(self)

    def _install_signal_handlers(self):
        """``VoiceTyperApp`` wiring (``app.start()`` calls"""
        from voice_typer.server.signal_handlers import install_signal_handlers

        install_signal_handlers(self)

    def _signal_watcher_loop(self) -> None:
        """Watcher thread for the POSIX signal handlers."""
        from voice_typer.server.signal_handlers import signal_watcher_loop

        signal_watcher_loop(self)

    def _install_win32_console_handler(self):
        """``VoiceTyperApp`` wiring (``app.start()`` calls"""
        from voice_typer.server.signal_handlers import (
            install_win32_console_handler,
        )

        install_win32_console_handler(self)

    def _win32_console_handler(self, ctrl_type):
        """Callback for Windows console control events."""
        from voice_typer.server.signal_handlers import win32_console_handler

        return win32_console_handler(self, ctrl_type)
