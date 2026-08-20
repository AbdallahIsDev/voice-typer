"""Tests for the in-memory log ring buffer (MemoryHandler) attached to
the ``voice_typer`` root logger.

The buffer is flushed by the VEH callback after writing the
crash-diagnostics body so the most-recent ~200 log records land in
``<config_dir>/voice-typer-crash-buffer.log``.

These tests are platform-agnostic — they exercise the
``install_memory_buffer`` / ``flush_memory_handler`` /
``uninstall_memory_buffer`` API directly without invoking the actual
VEH callback (which is Windows-only).
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import pytest
from voice_typer.server import crash_handler as _ch
from voice_typer.server.crash_handler._memory_buffer import (
    flush_memory_handler,
    install_memory_buffer,
    uninstall_memory_buffer,
)


@pytest.fixture(autouse=True)
def _reset_memory_buffer():
    """Remove any MemoryHandler installed by a previous test so each
    test starts from a clean state."""
    uninstall_memory_buffer()
    yield
    uninstall_memory_buffer()


class TestMemoryBufferInstallation:
    """``install_memory_buffer`` attaches a MemoryHandler to the
    ``voice_typer`` root logger with a RotatingFileHandler target."""

    def test_install_creates_memory_handler_on_voice_typer_root(self, tmp_path: Path):
        install_memory_buffer(tmp_path)
        assert _ch._memory_handler is not None, "MemoryHandler must be installed"
        assert isinstance(_ch._memory_handler, logging.handlers.MemoryHandler)

    def test_install_attaches_handler_to_voice_typer_logger(self, tmp_path: Path):
        voice_typer_root = logging.getLogger("voice_typer")
        n_before = len(voice_typer_root.handlers)
        install_memory_buffer(tmp_path)
        n_after = len(voice_typer_root.handlers)
        assert n_after == n_before + 1, "MemoryHandler must be attached to the voice_typer root logger"
        assert _ch._memory_handler in voice_typer_root.handlers

    def test_install_creates_target_rotating_file_handler(self, tmp_path: Path):
        install_memory_buffer(tmp_path)
        assert _ch._crash_buffer_handler is not None, "target RotatingFileHandler must be installed"
        assert isinstance(_ch._crash_buffer_handler, logging.handlers.RotatingFileHandler)

    def test_target_file_is_voice_typer_crash_buffer_log(self, tmp_path: Path):
        install_memory_buffer(tmp_path)
        # ``RotatingFileHandler.baseFilename`` is the absolute path of
        # the file it writes to.
        assert _ch._crash_buffer_handler.baseFilename == str(tmp_path / "logs" / "voice-typer-crash-buffer.log")

    def test_install_is_idempotent(self, tmp_path: Path):
        """Calling ``install_memory_buffer`` twice with the same
        config_dir must NOT attach a second MemoryHandler to the
        voice_typer logger."""
        voice_typer_root = logging.getLogger("voice_typer")
        install_memory_buffer(tmp_path)
        n_after_first = len(voice_typer_root.handlers)
        install_memory_buffer(tmp_path)
        n_after_second = len(voice_typer_root.handlers)
        assert n_after_second == n_after_first, "idempotent install must not double-attach the MemoryHandler"

    def test_memory_handler_level_is_info(self, tmp_path: Path):
        """The MemoryHandler must be at INFO level so INFO records
        are captured in the buffer (DEBUG records are dropped, matching
        the production file-handler level)."""
        install_memory_buffer(tmp_path)
        assert _ch._memory_handler.level == logging.INFO

    def test_memory_handler_capacity_is_200(self, tmp_path: Path):
        """The buffer capacity must be 200 records (per the spec)."""
        install_memory_buffer(tmp_path)
        assert _ch._memory_handler.capacity == 200


class TestMemoryBufferFlush:
    """``flush_memory_handler`` pushes the buffered records to the
    target file."""

    def test_flush_writes_buffered_records_to_file(self, tmp_path: Path):
        install_memory_buffer(tmp_path)
        # Emit a few records BEFORE flushing.
        test_log = logging.getLogger("voice_typer.test.crash_buffer")
        test_log.warning("crash-buffer test message A")
        test_log.warning("crash-buffer test message B")
        # Close the target so the file is flushed to disk for reading.
        flush_memory_handler()
        # The MemoryHandler.flush() pushes records to the target; the
        # RotatingFileHandler buffers in its stream. Close the target
        # to flush to disk.
        if _ch._crash_buffer_handler is not None:
            _ch._crash_buffer_handler.flush()
        buffer_path = tmp_path / "logs" / "voice-typer-crash-buffer.log"
        assert buffer_path.exists(), "crash-buffer log file must exist after flush"
        contents = buffer_path.read_text(encoding="utf-8", errors="replace")
        assert "crash-buffer test message A" in contents
        assert "crash-buffer test message B" in contents

    def test_flush_is_noop_when_buffer_not_installed(self, tmp_path: Path):
        """Calling ``flush_memory_handler`` before
        ``install_memory_buffer`` must be a safe no-op (the VEH
        callback relies on this — it can't know whether the buffer
        was successfully installed)."""
        # Must not raise.
        flush_memory_handler()

    def test_flush_does_not_raise_on_target_failure(self, tmp_path: Path, monkeypatch):
        """If the target's ``emit`` raises (e.g. disk full), the flush
        must swallow the error so the VEH callback is not affected."""
        install_memory_buffer(tmp_path)

        def _raise(*_args, **_kwargs):
            raise OSError("simulated disk full")

        # Patch the target's emit to raise. ``MemoryHandler.flush``
        # calls ``target.handle(record)`` per buffered record; we make
        # ``handle`` raise. ``MemoryHandler.flush`` is wrapped in our
        # ``flush_memory_handler`` which catches everything.
        if _ch._crash_buffer_handler is not None:
            monkeypatch.setattr(_ch._crash_buffer_handler, "handle", _raise)
        # Emit a record so flush has something to push.
        logging.getLogger("voice_typer.test.crash_buffer").warning("hello")
        # Must not raise.
        flush_memory_handler()


class TestUninstallMemoryBuffer:
    """``uninstall_memory_buffer`` removes the MemoryHandler and closes
    the target file handle."""

    def test_uninstall_removes_memory_handler(self, tmp_path: Path):
        install_memory_buffer(tmp_path)
        assert _ch._memory_handler is not None
        uninstall_memory_buffer()
        assert _ch._memory_handler is None

    def test_uninstall_removes_target_handler(self, tmp_path: Path):
        install_memory_buffer(tmp_path)
        assert _ch._crash_buffer_handler is not None
        uninstall_memory_buffer()
        assert _ch._crash_buffer_handler is None

    def test_uninstall_detaches_from_logger(self, tmp_path: Path):
        voice_typer_root = logging.getLogger("voice_typer")
        install_memory_buffer(tmp_path)
        # Sanity check: handler is attached.
        assert _ch._memory_handler in voice_typer_root.handlers
        uninstall_memory_buffer()
        # The handler must no longer be on the logger.
        # NOTE: the reference is captured before uninstall; we compare
        # identity against the (now-cleared) ``_ch._memory_handler``
        # which is None, and also check the logger doesn't have a
        # MemoryHandler anymore.
        assert not any(isinstance(h, logging.handlers.MemoryHandler) for h in voice_typer_root.handlers), (
            "MemoryHandler must be detached from the logger after uninstall"
        )


class TestSetCrashHandlerConfigDirInstallsBuffer:
    """``set_crash_handler_config_dir`` must install the MemoryHandler
    buffer alongside the crash file path / header."""

    def test_set_crash_handler_config_dir_installs_memory_handler(self, tmp_path: Path):
        # The buffer is installed best-effort inside
        # ``set_crash_handler_config_dir``; verify it's attached after
        # the call returns.
        _ch.set_crash_handler_config_dir(tmp_path)
        try:
            assert _ch._memory_handler is not None, "set_crash_handler_config_dir must install the MemoryHandler"
        finally:
            uninstall_memory_buffer()
            # Reset the rest of the crash_handler state so this test
            # doesn't leak into other test modules.
            _ch._crash_file_path = ""
            _ch._python_crash_dir = None
            _ch._crash_written = False
            _ch._crash_header_bytes = b""


class TestHu8PiiFilterFailClosed:
    """HU-8: the crash-buffer MemoryHandler attaches ``PIIRedactionFilter``
    LAZILY (on the first record) and fails CLOSED — if the security import
    cannot succeed, the record is DROPPED (never buffered) instead of being
    silently buffered unredacted (the pre-fix ``except Exception: pass``
    that would leak PII into ``voice-typer-crash-buffer.log`` on flush).

    The lazy retry makes the failure self-healing: as soon as the
    ``voice_typer.server.security`` import succeeds again, the filter is
    attached and records flow normally.
    """

    @staticmethod
    def _reset_flags() -> None:
        from voice_typer.server.crash_handler._memory_buffer import _CrashBufferMemoryHandler

        # Class-level state persists across instances/tests — reset so
        # each test exercises the lazy-attach path from scratch.
        _CrashBufferMemoryHandler._pii_attached = False
        _CrashBufferMemoryHandler._pii_failed_once = False

    def test_filter_attached_lazily_on_first_record(self, tmp_path: Path):
        from voice_typer.server.crash_handler._memory_buffer import _CrashBufferMemoryHandler

        self._reset_flags()
        install_memory_buffer(tmp_path)
        handler = _ch._memory_handler
        assert isinstance(handler, _CrashBufferMemoryHandler)
        # Lazily attached: no filter at install time.
        assert handler.filters == [], "HU-8: filter must NOT be attached at install time"
        assert handler._pii_attached is False

        record = logging.LogRecord("voice_typer.test.hu8", logging.WARNING, __file__, 1, "hello", (), None)
        # ``Handler.handle`` returns the filter-chain result (a logging
        # filter may return the record itself) — the fail-closed
        # override returns literal ``False`` ONLY when the record is
        # dropped, so ``is not False`` is the correct success assertion.
        assert handler.handle(record) is not False
        assert handler._pii_attached is True
        assert len(handler.filters) == 1
        assert type(handler.filters[0]).__name__ == "PIIRedactionFilter"
        assert len(handler.buffer) == 1, "record must be buffered once the filter is attached"

    def test_import_failure_drops_record_fail_closed(self, tmp_path: Path, caplog, monkeypatch):
        self._reset_flags()
        install_memory_buffer(tmp_path)
        handler = _ch._memory_handler
        # Force the lazy ``from voice_typer.server.security import
        # PIIRedactionFilter`` to fail (``None`` in sys.modules raises
        # ImportError on import).
        monkeypatch.setitem(sys.modules, "voice_typer.server.security", None)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.crash_handler._memory_buffer"):
            accepted = handler.handle(
                logging.LogRecord(
                    "voice_typer.test.hu8",
                    logging.WARNING,
                    __file__,
                    1,
                    "SECRET-UNREDACTED-DATA",
                    (),
                    None,
                )
            )

        assert accepted is False, "HU-8: fail-closed — record must be DROPPED when the filter can't be attached"
        assert handler._pii_attached is False
        assert len(handler.buffer) == 0, "HU-8: dropped record must never be buffered unredacted"
        # First failure surfaces a WARNING so operators see the degradation.
        assert any("PIIRedactionFilter unavailable" in r.getMessage() for r in caplog.records)
        # The dropped record's content must not leak into any log record.
        assert not any("SECRET-UNREDACTED-DATA" in r.getMessage() for r in caplog.records)

        # Second consecutive failure: the anti-spam throttle kicks in —
        # DEBUG only, no second WARNING.
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.crash_handler._memory_buffer"):
            assert (
                handler.handle(
                    logging.LogRecord(
                        "voice_typer.test.hu8",
                        logging.WARNING,
                        __file__,
                        1,
                        "SECRET-UNREDACTED-DATA-2",
                        (),
                        None,
                    )
                )
                is False
            )
        assert not any(
            "PIIRedactionFilter unavailable" in r.getMessage() and "still unavailable" not in r.getMessage()
            for r in caplog.records
        ), "HU-8: repeated failures must NOT re-log the WARNING (anti-spam)"
        assert any("still unavailable" in r.getMessage() for r in caplog.records)

    def test_self_heals_when_import_recovers(self, tmp_path: Path, monkeypatch):
        self._reset_flags()
        install_memory_buffer(tmp_path)
        handler = _ch._memory_handler

        real_security = sys.modules["voice_typer.server.security"]
        monkeypatch.setitem(sys.modules, "voice_typer.server.security", None)
        assert (
            handler.handle(logging.LogRecord("voice_typer.test.hu8", logging.WARNING, __file__, 1, "dropped", (), None))
            is False
        )

        # Import recovers — the NEXT record must attach the filter and
        # be buffered (self-healing lazy retry).
        monkeypatch.setitem(sys.modules, "voice_typer.server.security", real_security)
        assert (
            handler.handle(
                logging.LogRecord("voice_typer.test.hu8", logging.WARNING, __file__, 1, "after-recovery", (), None)
            )
            is not False
        )
        assert handler._pii_attached is True
        assert len(handler.filters) == 1
        assert type(handler.filters[0]).__name__ == "PIIRedactionFilter"
        assert len(handler.buffer) == 1, "only the post-recovery record is buffered"
