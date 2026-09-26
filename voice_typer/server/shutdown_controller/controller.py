"""ShutdownController orchestration."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    from voice_typer.server.app import LausuApp

from ._cleanup import CleanupMixin
from ._lifecycle_signals import SignalsMixin
from ._plans import SequencingMixin
from ._teardowns import TeardownsMixin


class ShutdownController(CleanupMixin, SequencingMixin, TeardownsMixin, SignalsMixin):
    """Owns the shutdown / cleanup lifecycle of ``LausuApp``.
    Extracted from ``LausuApp``. The app passes itself
     kept on ``LausuApp``).
     kept on ``LausuApp``).
    """

    # the ordered list of every ``_teardown_*`` phase method that
    _PARALLEL_TEARDOWN_PHASE_NAMES: tuple[str, ...] = (
        "_teardown_timers_and_recording",
        "_teardown_recorder",
        "_teardown_history_db",
        "_teardown_crash_recovery",
        "_teardown_asr_models",
        "_teardown_restore_volume",
        "_teardown_waveform_wiring",
        "_teardown_sounddevice",
        "_teardown_pid_file",
        "_teardown_mutex_handle",
        "_teardown_devnull_files",
        "_teardown_level_monitor",
        "_teardown_hotkeys",
        "_teardown_host_child",
        "_teardown_event_bus",
    )

    def __init__(self, app: LausuApp) -> None:
        self._app = app
        # POSIX signal handlers must be
        self._shutdown_signal_event: threading.Event = threading.Event()
        self._shutdown_signum: int | None = None
        self._signal_watcher_started = False
        # Counter for the number of POSIX signals received. Incremented
        self._signal_count: int = 0
        # dedicated lock for the check-then-set-then-shutdown_all
        self._quit_lock: threading.Lock = threading.Lock()

        # dedicated lock for the ``_host_pid`` read-terminate-clear
        self._host_pid_lock: threading.Lock = threading.Lock()

        # shared state between ``_teardown_recorder`` and
        self._recorder_teardown_done: threading.Event = threading.Event()
        self._recorder_force_closed: bool = False

        # Published by ``_do_cleanup`` so ``_run_plan`` can apply
        self._shutdown_deadline: float | None = None
        # Published by ``_do_cleanup`` alongside ``_shutdown_deadline``
        self._shutdown_skipped: list[str] | None = None
