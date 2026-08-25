"""SignalsMixin — quit / watchdog / atexit / signal-handler delegates.

Split verbatim out of the pre-split ``shutdown_controller`` module.
Every method is a thin delegate whose body lives in an extracted
module (:mod:`voice_typer.server.shutdown.lifecycle`,
:mod:`voice_typer.server.atexit_safety`,
:mod:`voice_typer.server.signal_handlers`). The delegates preserve the
instance-method API used by tests and the ``VoiceTyperApp`` wiring.
"""

from __future__ import annotations


class SignalsMixin:
    """Lifecycle + signal-handler delegate mixin for :class:`ShutdownController`."""

    # ─── Quit ──────────────────────────────────────────────────────────
    #
    # extraction: the bodies of ``quit`` and
    # ``_arm_shutdown_watchdog`` now live in
    # :mod:`voice_typer.server.shutdown.lifecycle`. The methods below
    # are thin delegates that preserve the instance-method API used by
    # tests (``controller.quit()``, ``controller._arm_shutdown_watchdog(
    # timeout_s)``) and the ``VoiceTyperApp`` wiring (tray menu callbacks
    # invoke ``quit_app`` which calls ``quit``; the watchdog is armed
    # from ``quit`` on a non-main thread).
    #
    # The delegate indirection is kept so:
    #   * tests that ``monkeypatch.setattr(controller, "_arm_shutdown_watchdog",
    #     spy)`` (see ``tests/test_shutdown_controller.py::
    #     TestShutdownWatchdog``) still intercept the call; and
    #   * tests that ``monkeypatch.setattr("voice_typer.server.
    #     shutdown_controller.join_leaked_workers", fake_join)`` (see
    #     ``tests/test_shutdown_parallel_pool_drain.py::
    #     TestWatchdogJoinLeakedWorkers``) still intercept the call
    #     — :func:`voice_typer.server.shutdown.lifecycle.arm_shutdown_watchdog`
    #     looks up ``join_leaked_workers`` DYNAMICALLY from
    #     ``voice_typer.server.shutdown_controller`` (lazy import) so the
    #     patched attribute is what the body sees.

    def quit(self):
        """Shut down the application cleanly.

        body lives in :func:`voice_typer.server.shutdown.lifecycle.quit`.
        This delegate preserves the instance-method API used by tests
        (``controller.quit()``) and the ``VoiceTyperApp`` wiring
        (tray-menu callbacks invoke ``quit_app`` which calls ``quit``).
        """
        from voice_typer.server.shutdown.lifecycle import quit as _quit

        _quit(self)

    def _arm_shutdown_watchdog(self, timeout_s: float) -> None:
        """arm a daemon-thread watchdog that calls
        ``os._exit(0)`` after ``timeout_s`` seconds if the process is
        still alive.

        body lives in
        :func:`voice_typer.server.shutdown.lifecycle.arm_shutdown_watchdog`.
        This delegate preserves the instance-method API used by tests
        (``controller._arm_shutdown_watchdog(timeout_s)`` — see
        ``tests/test_shutdown_controller.py::TestShutdownWatchdog``) and
        the call site inside :func:`lifecycle.quit` (which calls
        ``controller._arm_shutdown_watchdog(...)`` so test spies that
        ``monkeypatch.setattr(controller, "_arm_shutdown_watchdog", spy)``
        still intercept the call).
        """
        from voice_typer.server.shutdown.lifecycle import (
            arm_shutdown_watchdog,
        )

        arm_shutdown_watchdog(self, timeout_s)

    # ─── atexit safety net (: body → voice_typer.server.atexit_safety) ──

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called.

        body lives in :func:`voice_typer.server.atexit_safety.atexit_log`.
        This delegate preserves the instance-method API used by
        ``atexit.register(self._atexit_log)`` in ``VoiceTyperApp.start()``.
        """
        from voice_typer.server.atexit_safety import atexit_log

        atexit_log(self)

    def _atexit_cleanup(self) -> None:
        """atexit handler for critical cleanup paths.

        Idempotent — short-circuits on ``_shutting_down`` and never
        raises (). See :func:`voice_typer.server.atexit_safety.atexit_cleanup`
        for the full behavior contract ( extraction).

        body lives in :mod:`voice_typer.server.atexit_safety`.
        This delegate preserves the instance-method API used by tests
        (``controller._atexit_cleanup()``) and the ``VoiceTyperApp``
        wiring (``atexit.register(self._atexit_cleanup)``).
        """
        from voice_typer.server.atexit_safety import atexit_cleanup

        atexit_cleanup(self)

    # ─── Signal handlers (: body → voice_typer.server.signal_handlers) ──

    def _install_signal_handlers(self):
        """Install SIGINT/SIGTERM/SIGHUP handlers for graceful shutdown.

        body lives in
        :func:`voice_typer.server.signal_handlers.install_signal_handlers`.
        This delegate preserves the instance-method API used by tests
        (``controller._install_signal_handlers()``) and the
        ``VoiceTyperApp`` wiring (``app.start()`` calls
        ``self._install_signal_handlers()``).
        """
        from voice_typer.server.signal_handlers import install_signal_handlers

        install_signal_handlers(self)

    def _signal_watcher_loop(self) -> None:
        """Watcher thread for the POSIX signal handlers.

        body lives in
        :func:`voice_typer.server.signal_handlers.signal_watcher_loop`.
        This delegate is kept so the test fixture that calls
        ``controller._signal_watcher_loop()`` directly continues to work,
        and so legacy code that captured ``target=self._signal_watcher_loop``
        before the  split keeps functioning. New code should call
        ``signal_handlers.signal_watcher_loop(controller)`` directly.
        """
        from voice_typer.server.signal_handlers import signal_watcher_loop

        signal_watcher_loop(self)

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure.

        body lives in
        :func:`voice_typer.server.signal_handlers.install_win32_console_handler`.
        This delegate preserves the instance-method API used by tests
        (``controller._install_win32_console_handler()``) and the
        ``VoiceTyperApp`` wiring (``app.start()`` calls
        ``self._install_win32_console_handler()``).
        """
        from voice_typer.server.signal_handlers import (
            install_win32_console_handler,
        )

        install_win32_console_handler(self)

    def _win32_console_handler(self, ctrl_type):
        """Callback for Windows console control events.

        body lives in
        :func:`voice_typer.server.signal_handlers.win32_console_handler`.
        This delegate preserves the instance-method API used by tests
        (``controller._win32_console_handler(ctrl_type)`` — see
        ``tests/test_shutdown_controller.py::TestWin32ConsoleHandlerRouting``)
        and the ctypes callback wiring (``handler_routine(self._win32_console_handler)``
        inside :func:`signal_handlers.install_win32_console_handler`).
        """
        from voice_typer.server.signal_handlers import win32_console_handler

        return win32_console_handler(self, ctrl_type)
