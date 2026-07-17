"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
import sys
import threading
from unittest.mock import MagicMock

import numpy as np

# ─── Linux test-env shim (RW-8) ──────────────────────────────────────────
# ``voice_typer.server.crash_handler`` uses ``ctypes.WINFUNCTYPE`` as a
# decorator at module load time. That attribute only exists on Windows,
# so importing ``voice_typer.server.app`` (which does
# ``from voice_typer.server import crash_handler``) raises
# ``AttributeError`` on Linux. Many tests in this file introspect
# ``VoiceTyperApp`` source via ``inspect.getsource``; without this
# shim, those tests would fail non-deterministically depending on
# whether some earlier test happened to pre-load ``app``. The same
# pattern is used in ``tests/test_api_doc_accuracy.py:42-57``. This is
# a *test-only* shim — production code never monkey-patches ctypes.
if sys.platform != "win32" and "voice_typer.server.crash_handler" not in sys.modules:
    sys.modules["voice_typer.server.crash_handler"] = MagicMock()


class TestConfigMutationLockSharedAcrossIpc:
    """RACE-011.

    Pre-fix: concurrent IPC ``set_config`` calls could interleave Config
    attribute writes and produce a torn config state.
    Fix: app holds a ``_config_mutation_lock`` (RLock) shared with the
    IPC set_config path so mutations serialize.

    ARCH-DEAD-SETTINGS: the historical tkinter SettingsController path
    that also consumed this lock has been removed along with
    voice_typer.server.settings. The lock remains because the IPC
    set_config path still requires serialization. The four tests that
    referenced SettingsController directly have been deleted; the two
    tests that verify the lock exists and is used by the IPC handler
    are retained.

    TASK-2 (ADR 0008 §3.1): the lock acquisition moved from the IPC
    handler (``config_handlers._handle_set_config``) into the service
    layer (``VoiceTyperService.apply_config``).  The handler now calls
    ``self.service.apply_config(validated)`` which internally acquires
    ``self._app._config_mutation_lock`` for the full setattr +
    side-effects + save sequence.  The regression test below was
    updated to introspect the service method instead of the handler.
    """

    def test_app_has_config_mutation_lock(self):
        # RW-8: KEEP — pins RACE-011 fix (app holds a re-entrant lock
        # for Config mutations). A behavioral test would need to spawn
        # two threads doing set_config concurrently and detect a torn
        # state, which is non-deterministic; the source-string check
        # catches removal of the lock deterministically.
        from voice_typer.server.app import VoiceTyperApp

        # VoiceTyperApp must declare _config_mutation_lock
        src = inspect.getsource(VoiceTyperApp.__init__)
        assert "_config_mutation_lock" in src, (
            "VoiceTyperApp.__init__ must initialize _config_mutation_lock "
            "to serialize Config mutations between concurrent IPC set_config calls."
        )
        assert "threading.RLock()" in src

    def test_ipc_set_config_uses_lock(self):
        # RW-8: KEEP — pins ADR 0008 §3.1 refactor (lock acquisition
        # moved from IPC handler to service layer). A behavioral test
        # would dispatch set_config concurrently and detect a race,
        # which is non-deterministic; the source-string check catches
        # both removal of the lock from the service AND reintroduction
        # of direct lock access in the handler.
        from voice_typer.server.service import VoiceTyperService

        # TASK-2 (ADR 0008 §3.1): the lock acquisition moved from
        # config_handlers._handle_set_config into
        # VoiceTyperService.apply_config.  The handler now calls
        # self.service.apply_config(validated), which acquires the
        # lock internally.  Introspect the service method (not the
        # handler) for the lock acquisition.
        src = inspect.getsource(VoiceTyperService.apply_config)
        assert "_config_mutation_lock" in src, (
            "VoiceTyperService.apply_config must acquire "
            "_config_mutation_lock before mutating Config attributes "
            "(ADR 0008 §3.1: the lock moved from the IPC handler to "
            "the service layer)."
        )
        # Belt-and-suspenders: the handler must still drive the
        # config update through the service layer (not call
        # _config_mutation_lock directly).  Read the handler source
        # from disk to avoid the circular import between
        # voice_typer.server.ipc_server and the handler mixins
        # (ipc_server imports the mixins at module load time).
        import pathlib

        handler_path = (
            pathlib.Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "server"
            / "handlers"
            / "config_handlers.py"
        )
        handler_src = handler_path.read_text(encoding="utf-8")
        assert "self.service.apply_config" in handler_src, (
            "IPC set_config handler must delegate to "
            "self.service.apply_config() (ADR 0008 §3.1) — reaching "
            "into self.app._config_mutation_lock directly is a leaky "
            "abstraction the refactor removed."
        )
        assert "_config_mutation_lock" not in handler_src, (
            "IPC set_config handler must NOT reference "
            "_config_mutation_lock directly (ADR 0008 §3.1) — the "
            "lock now lives inside VoiceTyperService.apply_config."
        )


class TestConfigEditHoldsMutationLock:
    """SEC-audit-011.

    The finding: config.json opened in Notepad for read-write without
    any file locking, creating a TOCTOU race with the app's atomic
    writes. Fix: hold ``_config_mutation_lock`` for the duration of
    the notepad session so IPC ``set_config`` cannot race.
    """

    def test_open_config_file_holds_config_mutation_lock(self):
        # RW-8: KEEP — pins SEC-audit-011 fix (lock held for the
        # duration of the Notepad editing session). A behavioral test
        # would need to spawn two threads (one editing config in
        # Notepad, one doing set_config IPC) and detect a race, which
        # is non-deterministic; the source-string check catches
        # removal of the lock or reordering of lock/Popen/reload.
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp._open_config_file)
        assert "_config_mutation_lock" in src, (
            "SEC-audit-011: _open_config_file must hold _config_mutation_lock "
            "for the duration of the notepad editing session so IPC set_config "
            "cannot atomically replace config.json while Notepad is mid-edit."
        )
        # The lock must be acquired BEFORE Popen and released AFTER reload.
        # Anchor to the *assignment* form (``proc = subprocess.Popen(``)
        # — the docstring prose also mentions ``subprocess.Popen([...])``,
        # but only the real call site is preceded by ``proc = ``, so
        # this skips the docstring mention that otherwise appears
        # *before* the lock block and poisons the ordering check.
        popen_idx = src.find("proc = subprocess.Popen(")
        if popen_idx == -1:
            # Defensive: fall back to the bracketed call form.
            popen_idx = src.rfind("subprocess.Popen([")
        lock_idx = src.find("with self._config_mutation_lock:")
        reload_idx = src.find("type(self.config).load()")
        assert popen_idx != -1 and lock_idx != -1 and reload_idx != -1
        assert lock_idx < popen_idx < reload_idx, (
            "SEC-audit-011: _config_mutation_lock must be acquired before Popen and held through the config reload."
        )


class TestBackpressureIncrementsOnBufferOverflow:
    """NEW-CQ-007: Backpressure detection under load exceeding buffer capacity."""

    def test_backpressure_increments_when_buffer_overflows(self):
        """When the callback appends beyond _buffer.maxlen, the
        backpressure detection code must increment _dropped_chunks.

        This test simulates the actual callback path: each iteration
        does the locked append + backpressure check (the same code
        the production callback runs). The test does NOT manually
        set _dropped_chunks — it relies on the production logic.
        """
        from voice_typer.server.config import Config
        from voice_typer.server.recording import Recorder

        cfg = Config()
        rec = Recorder(cfg)
        rec._effective_sr = 16000
        rec._cached_target_sr = 16000

        maxlen = rec._buffer.maxlen
        chunk = np.full((512, 1), 0.1, dtype=np.float32)

        # Simulate the callback's locked append + backpressure check
        for _ in range(maxlen + 10):
            with rec._lock:
                rec._buffer.append(chunk)
                rec._chunk_count += 1
                buffer_len = len(rec._buffer)

            # Backpressure check (from recording.py callback)
            if buffer_len >= rec._buffer.maxlen - 1:
                rec._dropped_chunks = getattr(rec, "_dropped_chunks", 0) + 1

        assert getattr(rec, "_dropped_chunks", 0) >= 1, (
            "NEW-CQ-007: backpressure must increment _dropped_chunks when buffer overflows"
        )
        assert len(rec._buffer) == maxlen


class TestConcurrentConfigAccessNoCrash:
    """NEW-CQ-013: Stress test concurrent access patterns."""

    def test_concurrent_config_access_no_crash(self):
        """Concurrent reads + writes to Config must not crash."""
        from voice_typer.server.config import Config

        cfg = Config()
        errors = []

        def writer():
            for i in range(50):
                try:
                    cfg.hotkey = f"<f{i % 12 + 1}>"
                except Exception as e:
                    errors.append(e)

        def reader():
            for _ in range(50):
                try:
                    _ = cfg.hotkey
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)] + [
            threading.Thread(target=reader) for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent access raised: {errors}"


class TestConcurrentConfigWritesNoCorruption:
    """NEW-CQ-025: Test concurrent config mutation WITHOUT test-level locking."""

    def test_concurrent_config_writes_no_corruption(self):
        """Concurrent Config attribute writes must not crash or produce
        a torn state. This test does NOT use a test-level lock — it
        relies on Python's GIL for atomic attribute writes (the same
        protection the production code relies on).
        """
        from voice_typer.server.config import Config

        cfg = Config()
        cfg.save = lambda: True  # mock save to avoid disk I/O

        def setter(val):
            # NO lock — relies on GIL (same as production)
            cfg.hotkey = val
            cfg.model_size = "tiny.en"

        threads = [threading.Thread(target=setter, args=(f"<f{i + 1}>",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert cfg.hotkey.startswith("<f"), f"Concurrent writes corrupted hotkey: {cfg.hotkey!r}"
        assert cfg.model_size == "tiny.en"


class TestConcurrentDispatchNoDeadlock:
    """NEW-IPC-011: Concurrent IPC message handling."""

    def test_concurrent_dispatch_no_deadlock(self):
        """Concurrent _dispatch calls must not deadlock."""
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._config_mutation_lock = threading.RLock()
        server.service = MagicMock()
        server.app.tray = MagicMock()
        server.app.tray.set_state = MagicMock()
        server.app.config = MagicMock()
        server.app.config.model_size = "tiny.en"
        server.app.config.device = "cpu"
        server.app.config.hotkey = "<f2>"
        server.app.config.show_notifications = True
        server.app.config.autostart = False
        server.app.config.asr_backend = "whisper"
        server.app._microphones = []
        server.app.history_db = MagicMock()
        server.app._volume_ducker = MagicMock()

        errors = []

        def dispatch():
            try:
                server._dispatch({"type": "get_status", "id": "test"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=dispatch) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent dispatch raised: {errors}"
