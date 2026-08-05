"""Shared ``_FakeApp`` factory for the shutdown test suite.

This module owns the SINGLE canonical ``_FakeApp`` helper used by every
test file that exercises :class:`voice_typer.server.shutdown_controller.
ShutdownController`. Before this module existed, at least 13 inline
copies of the class were sprinkled across the test tree:

- ``tests/test_shutdown_controller.py``
- ``tests/test_shutdown_parallel_pool_drain.py``
- ``tests/test_shutdown_asr_unload.py``
- ``tests/test_shutdown_controller_group_fixes.py``
- ``tests/test_app_cleanup.py``
- ``tests/test_shutdown_posix_release.py``
- ``tests/test_shutdown_recorder_force_closed.py``
- ``tests/test_ipc_send_shutdown_allowlist.py``
- ``tests/test_shutdown_plan_zr17.py``
- ``tests/test_shutdown_race_fixes.py``
- ``tests/test_shutdown_pool_drain.py`` (named ``_FakeAppForDoCleanup``)
- ``tests/test_shutdown_fast_path.py``
- ``tests/test_shutdown_parallel.py``
- ``tests/test_shutdown_deadline.py``

The copies had drifted: some set ``recorder.recording = True`` (force
the recorder.stop() branch), some set it to ``False`` (skip it); some
pre-populated ``waveform_wiring`` / ``_ipc_server`` / ``models`` /
``_do_cleanup`` / the bubble-level worker attrs, others left them
absent. Adding a new ``app.X`` access to ``_do_cleanup`` meant
hand-editing every copy and hoping no test was missed.

Centralising the factory here means future additions to
``ShutdownController._do_cleanup`` only need to update ONE place — this
module — and every shutdown test picks up the new attribute
automatically. The factory is intentionally a SUPERSET of every prior
inline copy: it sets every attribute any of the 13 call sites ever
needed, so each test file can drop its inline ``_FakeApp`` class and
``import`` this one without further per-test configuration. Tests that
need a different value (e.g. ``recorder.recording = True`` to exercise
the stop branch, or ``_electron_pid = 1234`` to exercise the
electron-kill branch) override it after calling
:func:`make_fake_shutdown_app`.

The migration target is documented as Remaining Work: this
module ONLY provides the factory — call sites still use their inline
``_FakeApp`` until a follow-up task migrates them.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock


class _FakeShutdownApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Provides every attribute / method that ``ShutdownController._do_cleanup``
    and ``quit`` touch, mocked so callers can assert call counts. Mirrors
    the collaborators mocked by ``tests/test_app_cleanup.py::
    _stub_restart_environment``.

    This is a class (not a function) so it can be passed directly to
    ``ShutdownController(app)`` (which reads attributes off ``app`` in
    ``__init__``). Use :func:`make_fake_shutdown_app` to construct an
    instance with overrides — that factory is the canonical entry point
    for new tests.
    """

    def __init__(self) -> None:
        # ── Shutdown state (mirrors VoiceTyperApp.__init__) ──────────
        self._shutting_down: bool = False
        self._shutting_down_event: threading.Event = threading.Event()
        self._cleanup_done: bool = False
        self._electron_pid: int | None = None
        self._mutex_handle: Any = None

        # ── Subsystem collaborators (MagicMock so any attribute/method
        # call is recorded and returns a MagicMock by default) ────────
        self.recorder: Any = MagicMock()
        # ``recorder.recording`` differs per inline copy: tests that
        # exercise the stop() branch set it to True, others default
        # False to skip the branch. Default False here (the safer
        # choice — avoids accidentally waiting on a real recorder.stop
        # in tests that don't care about it); override via
        # ``make_fake_shutdown_app(recorder_recording=True)`` or by
        # setting ``app.recorder.recording`` after construction.
        self.recorder.recording = False
        self.recording: Any = MagicMock()
        self.recording._transcription_thread = None
        # ``pop_streaming_session`` returns None by default — tests
        # that care override it.
        self.recording.pop_streaming_session = MagicMock(return_value=None)
        # Legacy get/set accessors as MagicMocks so we can detect if
        # the old (pre-XV-7) code path is still being used.
        self.recording.get_streaming_session = MagicMock(return_value=None)
        self.recording.set_streaming_session = MagicMock(return_value=None)
        self.hotkeys: Any = MagicMock()
        self.hotkeys._hotkey_backend = MagicMock()
        self.hotkeys._esc_backend = MagicMock()
        self.hotkeys._repaste_backend = MagicMock()
        self.history_db: Any = MagicMock()
        self._crash_recovery: Any = MagicMock()
        self.tray: Any = MagicMock()
        self._thread_registry: Any = MagicMock()
        # ``waveform_wiring`` — present on VoiceTyperApp post XV-7;
        # ``_teardown_waveform_wiring`` touches it. Pre-populated so
        # tests that call ``_do_cleanup`` don't AttributeError.
        self.waveform_wiring: Any = MagicMock()
        # ``_teardown_asr_models`` reads ``app.models.registry``.
        self.models: Any = MagicMock()
        self.models.registry: Any = MagicMock()

        # ── Methods on VoiceTyperApp that _do_cleanup calls (kept on
        # the app as delegates to other controllers) ────────────────
        self._cancel_pending_timers: Any = MagicMock()
        self._restore_volume: Any = MagicMock()
        # ``_do_fast_cleanup`` touches these — give them MagicMock
        # defaults so attribute access doesn't raise.
        self._duck_crash_recovery: Any = MagicMock()

        # ── Bubble level worker (optional on VoiceTyperApp — _do_cleanup
        # guards with hasattr; initialize to None so the worker-stop
        # branch is skipped by default) ─────────────────────────────
        self._bubble_level_worker_stop: Any = None
        self._bubble_level_queue: Any = None
        self._bubble_level_worker: Any = None

        # ── ``_do_cleanup`` delegate on VoiceTyperApp. Default to a
        # no-op MagicMock; per-test (or the ``controller`` fixture)
        # wires it to the real body via ``side_effect``. ─────────────
        self._do_cleanup: Any = MagicMock()

        # ── IPC server — left as None here; the test wires it as
        # needed. ``_do_cleanup`` looks up ``app._ipc_server`` for the
        # WS drain. ─────────────────────────────────────────────────
        self._ipc_server: Any = None


def make_fake_shutdown_app(**overrides: Any) -> _FakeShutdownApp:
    """Build a ``_FakeShutdownApp`` with the canonical shutdown surface.

    Returns a fresh instance of :class:`_FakeShutdownApp` — a
    duck-typed stand-in for :class:`voice_typer.server.app.VoiceTyperApp`
    that exposes every attribute / method touched by
    ``ShutdownController._do_cleanup`` and ``quit``. Every subsystem
    is a :class:`unittest.mock.MagicMock` so call counts and call
    orderings can be asserted without running real teardown code.

    Keyword overrides
    -----------------
    Any keyword argument is treated as an attribute assignment on the
    returned instance — e.g.::

        # Make the recorder look mid-recording so the stop() branch runs.
        app = make_fake_shutdown_app()
        app.recorder.recording = True

        # Skip the cleanup-done short-circuit for an idempotency test.
        app = make_fake_shutdown_app(_cleanup_done=True)

        # Same thing via the overrides shortcut:
        app = make_fake_shutdown_app(_cleanup_done=True)

    The overrides shortcut is convenient for top-level scalar attributes
    (``_shutting_down``, ``_cleanup_done``, ``_electron_pid``,
    ``_mutex_handle``, ``_ipc_server``). For nested overrides (e.g.
    ``recorder.recording``), mutate the returned instance directly —
    Python doesn't have a clean syntax for nested kwargs and pretending
    otherwise leads to error-prone ``overrides={"recorder.recording":
    True}`` dictionaries that lose static-analysis support.

    Migration target
    ----------------
    This factory is the SINGLE canonical place to update when a new
    attribute is added to ``ShutdownController._do_cleanup``. The 13
    inline ``_FakeApp`` copies across the shutdown test files are
    documented as Remaining Work — they will be migrated in a
    follow-up task. New shutdown test files SHOULD import this helper
    instead of defining their own ``_FakeApp``.

    Returns
    -------
    _FakeShutdownApp
        A configured fake app instance. Caller is free to mutate any
        attribute before passing it to ``ShutdownController(app)``.
    """
    app = _FakeShutdownApp()
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


__all__ = [
    "_FakeShutdownApp",
    "make_fake_shutdown_app",
]
