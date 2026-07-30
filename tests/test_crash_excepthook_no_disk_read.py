"""AB-33 regression: the crash excepthook MUST NOT do disk I/O on the
crashing thread.

Pre-AB-33, ``_crash_excepthook`` / ``_thread_crash_excepthook`` called
``_get_active_asr_backend()`` → ``Config.load()`` (a JSON disk read) on
the crashing thread.  During interpreter shutdown or on a failing disk,
this disk read could hang the crash report itself — defeating the
crash-marker purpose.

Post-AB-33, the backend is cached at install time
(``install_python_excepthook`` / ``install_threading_excepthook`` /
``set_crash_handler_config_dir``) and the excepthook reads the cached
value via ``_get_cached_asr_backend()`` (no disk I/O).

These tests verify:
- ``_get_active_asr_backend`` is NOT called from the excepthook.
- ``_get_cached_asr_backend`` is called instead.
- The cache is populated at install time.
- The cache is refreshed on each ``install_python_excepthook`` call.
- ``_secure_atomic_write`` is called with ``durability=False``.
- The flush loop is bounded by a wall-clock budget (no per-handler
  guarantee, but the total loop time is bounded).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from unittest import mock

import pytest
from voice_typer.server import crash_handler

_UNSET = object()


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state():
    """Reset module-level globals between tests.

    Mirrors the autouse fixture in ``tests/test_threading_excepthook.py``.
    Snapshots + restores ``sys.excepthook`` and ``threading.excepthook``
    so prior tests don't leak the crash hook into these tests.
    """
    keys = (
        "_crash_file_path",
        "_PID",
        "_handler_handle",
        "_kernel32",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
        "_original_excepthook",
        "_original_threading_excepthook",
        "_cached_active_backend",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    saved_sys_excepthook = sys.excepthook
    saved_threading_excepthook = threading.excepthook
    crash_handler._crash_file_path = ""
    crash_handler._PID = 0
    crash_handler._handler_handle = None
    crash_handler._kernel32 = None
    crash_handler._crash_written = False
    crash_handler._python_crash_dir = None
    crash_handler._crash_header_bytes = b""
    crash_handler._original_excepthook = None
    crash_handler._original_threading_excepthook = None
    crash_handler._cached_active_backend = None
    sys.excepthook = sys.__excepthook__
    threading.excepthook = threading.__excepthook__
    yield
    for k, v in saved.items():
        if v is _UNSET:
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)
    sys.excepthook = saved_sys_excepthook
    threading.excepthook = saved_threading_excepthook


@pytest.fixture
def restore_excepthook():
    saved = sys.excepthook
    saved_orig_attr = crash_handler._original_excepthook
    yield
    sys.excepthook = saved
    crash_handler._original_excepthook = saved_orig_attr


@pytest.fixture
def restore_threading_excepthook():
    saved = threading.excepthook
    saved_orig_attr = crash_handler._original_threading_excepthook
    yield
    threading.excepthook = saved
    crash_handler._original_threading_excepthook = saved_orig_attr


# ─── Cache population ───────────────────────────────────────────────────


class TestCachePopulatedAtInstallTime:
    """``install_python_excepthook`` / ``install_threading_excepthook``
    refresh the cached ASR backend via ``_refresh_cached_asr_backend``."""

    def test_install_python_excepthook_populates_cache(self, restore_excepthook):
        """After ``install_python_excepthook``, ``_cached_active_backend``
        is non-None (populated by ``_refresh_cached_asr_backend``)."""
        assert crash_handler._cached_active_backend is None
        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_active_asr_backend",
            return_value="whisper",
        ):
            crash_handler.install_python_excepthook()
        assert crash_handler._cached_active_backend == "whisper"

    def test_install_threading_excepthook_populates_cache(self, restore_threading_excepthook):
        """After ``install_threading_excepthook``, ``_cached_active_backend``
        is non-None."""
        assert crash_handler._cached_active_backend is None
        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_active_asr_backend",
            return_value="parakeet",
        ):
            crash_handler.install_threading_excepthook()
        assert crash_handler._cached_active_backend == "parakeet"

    def test_set_crash_handler_config_dir_populates_cache(self, tmp_path):
        """``set_crash_handler_config_dir`` also refreshes the cache
        (so the cache is populated even if install_python_excepthook
        hasn't been called yet — e.g. very early in startup)."""
        assert crash_handler._cached_active_backend is None
        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_active_asr_backend",
            return_value="qwen",
        ):
            crash_handler.set_crash_handler_config_dir(tmp_path)
        assert crash_handler._cached_active_backend == "qwen"


# ─── No disk I/O on the crashing thread ────────────────────────────────


class TestNoDiskReadOnCrashingThread:
    """The excepthook MUST NOT call ``_get_active_asr_backend`` (the
    disk-read function) on the crashing thread.  It reads the cached
    value via ``_get_cached_asr_backend`` instead."""

    def test_crash_excepthook_uses_cached_backend(self, restore_excepthook, tmp_path):
        """``_crash_excepthook`` reads the cached backend, not the disk."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        # Populate the cache.
        crash_handler._cached_active_backend = "whisper"

        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_active_asr_backend"
        ) as mock_disk_read:
            try:
                raise ValueError("test crash")
            except ValueError as exc:
                crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
            # The disk-read function MUST NOT be called from the excepthook.
            mock_disk_read.assert_not_called()

        # The marker file should contain the cached backend.
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        assert "asr_backend=whisper" in content

    def test_thread_crash_excepthook_uses_cached_backend(self, restore_threading_excepthook, tmp_path):
        """``_thread_crash_excepthook`` reads the cached backend, not the disk."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler._cached_active_backend = "parakeet"

        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_active_asr_backend"
        ) as mock_disk_read:
            try:
                raise RuntimeError("thread crash")
            except RuntimeError as exc:
                args = threading.ExceptHookArgs([type(exc), exc, exc.__traceback__, threading.current_thread()])
                crash_handler._thread_crash_excepthook(args)
            mock_disk_read.assert_not_called()

        thread_name = threading.current_thread().name
        safe_name = crash_handler._sanitize_thread_name_for_filename(thread_name)
        marker = tmp_path / f"python_crash.{os.getpid()}.{safe_name}.txt"
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        assert "asr_backend=parakeet" in content

    def test_crash_excepthook_calls_get_cached_asr_backend(self, restore_excepthook, tmp_path):
        """The excepthook explicitly calls ``_get_cached_asr_backend``
        (the no-disk-I/O accessor), not ``_get_active_asr_backend``
        (the disk-read function)."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler._cached_active_backend = "whisper"

        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_cached_asr_backend",
            wraps=crash_handler._get_cached_asr_backend,
        ) as mock_cached_read:
            try:
                raise ValueError("test crash")
            except ValueError as exc:
                crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
            mock_cached_read.assert_called_once()

    def test_falls_back_to_unknown_if_cache_empty(self, restore_excepthook, tmp_path):
        """If the cache is empty (e.g. the hook fired before install),
        the excepthook falls back to ``<unknown>`` rather than reading
        disk."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        # Explicitly clear the cache (set_crash_handler_config_dir
        # above may have populated it).
        crash_handler._cached_active_backend = None
        assert crash_handler._cached_active_backend is None

        # Block any disk read — the excepthook must NOT do disk I/O.
        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._get_active_asr_backend"
        ) as mock_disk_read:
            mock_disk_read.side_effect = AssertionError("AB-33: excepthook must not read disk when cache is empty")
            try:
                raise ValueError("test crash")
            except ValueError as exc:
                crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)

        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        assert "asr_backend=<unknown>" in content


# ─── durability=False ──────────────────────────────────────────────────


class TestDurabilityFalse:
    """``_secure_atomic_write`` is called with ``durability=False`` so
    the crash marker write doesn't fsync on a process that's already
    terminating."""

    def test_crash_excepthook_passes_durability_false(self, restore_excepthook, tmp_path):
        """The crash marker is written with ``durability=False``."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler._cached_active_backend = "whisper"

        with mock.patch(
            "voice_typer.server.crash_handler._python_excepthook._secure_atomic_write",
            create=True,
        ) as mock_atomic:
            # The excepthook imports ``_secure_atomic_write`` from
            # ``voice_typer.server.config`` lazily inside the hook body.
            # Patch the module-level name in the config module so the
            # ``from ... import _secure_atomic_write`` inside the hook
            # picks up our mock.
            pass

        # Re-do with the proper patch target.
        from voice_typer.server import config as _config_mod

        with mock.patch.object(
            _config_mod, "_secure_atomic_write", wraps=_config_mod._secure_atomic_write
        ) as mock_atomic:
            try:
                raise ValueError("test crash")
            except ValueError as exc:
                crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
            mock_atomic.assert_called_once()
            # AB-33: durability=False — fsync on a terminating process
            # provides no durability benefit and can hang the crashing
            # thread on a stuck disk.
            assert mock_atomic.call_args.kwargs.get("durability") is False

    def test_thread_crash_excepthook_passes_durability_false(self, restore_threading_excepthook, tmp_path):
        """The thread crash marker is also written with ``durability=False``."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler._cached_active_backend = "parakeet"

        from voice_typer.server import config as _config_mod

        with mock.patch.object(
            _config_mod, "_secure_atomic_write", wraps=_config_mod._secure_atomic_write
        ) as mock_atomic:
            try:
                raise RuntimeError("thread crash")
            except RuntimeError as exc:
                args = threading.ExceptHookArgs([type(exc), exc, exc.__traceback__, threading.current_thread()])
                crash_handler._thread_crash_excepthook(args)
            mock_atomic.assert_called_once()
            assert mock_atomic.call_args.kwargs.get("durability") is False


# ─── Flush loop budget ─────────────────────────────────────────────────


class TestFlushLoopBudget:
    """The ``handler.flush()`` loop is bounded by a wall-clock budget
    so a stuck handler can't hang the crashing thread."""

    def test_flush_loop_breaks_after_budget(self, restore_excepthook, tmp_path):
        """Multiple stuck handlers do NOT accumulate their full sleep
        time — the loop breaks as soon as ``time.perf_counter()``
        exceeds the budget.

        Note: a SINGLE stuck handler can still block (the budget check
        happens BEFORE the flush call).  The budget caps the TOTAL loop
        time across N handlers, not any single handler's flush.
        """
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler._cached_active_backend = "whisper"

        # Add 5 stuck handlers, each sleeping 0.3s.  Without the
        # budget cap, the loop would block for 5 * 0.3s = 1.5s.  With
        # the 0.5s budget, the loop breaks after ~2 handlers (0.6s),
        # saving ~0.9s.
        import time as _time

        class _StuckHandler(logging.Handler):
            def __init__(self, sleep_s: float):
                super().__init__()
                self._sleep_s = sleep_s

            def emit(self, record):
                pass

            def flush(self):
                _time.sleep(self._sleep_s)

        vt_logger = logging.getLogger("voice_typer")
        handlers = [_StuckHandler(0.3) for _ in range(5)]
        for h in handlers:
            vt_logger.addHandler(h)
        try:
            start = _time.perf_counter()
            try:
                raise ValueError("test crash")
            except ValueError as exc:
                crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
            elapsed = _time.perf_counter() - start
            # Without the budget cap, the loop would block for 1.5s.
            # With the 0.5s budget, the loop breaks after ~2 handlers
            # (0.6s + a bit of slack for the perf_counter call).
            # Allow generous slack for slow CI machines.
            assert elapsed < 1.2, (
                f"AB-33: flush loop should be bounded by ~0.5s budget; "
                f"took {elapsed:.2f}s (multiple stuck handlers may have "
                f"accumulated)"
            )
        finally:
            for h in handlers:
                vt_logger.removeHandler(h)
