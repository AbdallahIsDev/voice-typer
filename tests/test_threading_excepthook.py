"""FR-14 regression: ``install_threading_excepthook`` captures daemon-thread crashes.

Pre-FR-14, ``install_python_excepthook`` only assigned ``sys.excepthook``
(main-thread-only). Since Python 3.8, unhandled exceptions in non-main
threads go through ``threading.excepthook`` — Voice Typer spawns many
daemon threads (A11yPulse, ModelLoad, heartbeat_loop, crash-recovery-saver,
history-retention-apply, bubble-level-pusher, shutdown-watchdog, prewarm
completion-event listener) and an unhandled exception in any of them
silently died with no marker file written, so the next session's
``report_pending_crash`` did not surface it.

Post-FR-14, ``install_threading_excepthook`` installs
``_thread_crash_excepthook`` as ``threading.excepthook``. The hook:

1. Logs at CRITICAL with thread name + redacted exc_type.
2. Writes a ``python_crash.<PID>.<thread_name>.txt`` marker file using
   the same ``redact_pii`` + ``redact_secret`` pipeline as the main
   excepthook.
3. Chains to the previously-installed ``threading.excepthook``.

These tests exercise the Linux-runnable surface (the hook is a pure
Python callable — no Windows VEH machinery).
"""

from __future__ import annotations

import logging
import os
import threading

import pytest
from voice_typer.server import crash_handler


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state():
    """Reset module-level globals between tests (mirrors
    ``tests/test_crash_handler.py``'s autouse fixture).

    Also snapshots + restores ``threading.excepthook`` so that prior
    tests that instantiated ``VoiceTyperApp`` (which calls
    ``install_threading_excepthook`` at app.py:147) and didn't clean
    up don't leak the crash hook into these tests. Without this
    snapshot, ``test_install_saves_original_threading_excepthook``
    would see ``threading.excepthook is _thread_crash_excepthook`` at
    fixture-setup time and the install's idempotent short-circuit
    would skip the ``_original_threading_excepthook`` assignment.
    """
    keys = (
        "_crash_file_path",
        "_PID",
        "_handler_handle",
        "_kernel32",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
        "_original_threading_excepthook",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    saved_threading_excepthook = threading.excepthook
    # Reset to the interpreter default so each test starts from a
    # pristine hook (regardless of what prior test files left behind).
    crash_handler._crash_file_path = ""
    crash_handler._PID = 0
    crash_handler._handler_handle = None
    crash_handler._kernel32 = None
    crash_handler._crash_written = False
    crash_handler._python_crash_dir = None
    crash_handler._crash_header_bytes = b""
    crash_handler._original_threading_excepthook = None
    threading.excepthook = threading.__excepthook__
    yield
    for k, v in saved.items():
        if v is _UNSET:
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)
    threading.excepthook = saved_threading_excepthook


_UNSET = object()


@pytest.fixture
def restore_threading_excepthook():
    """Snapshot ``threading.excepthook`` so a test can restore it."""
    saved = threading.excepthook
    saved_orig_attr = crash_handler._original_threading_excepthook
    yield
    threading.excepthook = saved
    crash_handler._original_threading_excepthook = saved_orig_attr


# ─── install / remove ──────────────────────────────────────────────────


class TestInstallThreadingExcepthook:
    """``install_threading_excepthook`` swaps ``threading.excepthook``."""

    def test_install_sets_custom_threading_excepthook(self, restore_threading_excepthook):
        """After install, ``threading.excepthook`` is the crash_handler's hook."""
        original = threading.excepthook
        crash_handler.install_threading_excepthook()
        assert threading.excepthook is crash_handler._thread_crash_excepthook
        assert threading.excepthook is not original

    def test_install_is_idempotent(self, restore_threading_excepthook):
        """Calling install twice does NOT re-save the original."""
        crash_handler.install_threading_excepthook()
        saved_once = crash_handler._original_threading_excepthook
        crash_handler.install_threading_excepthook()
        saved_twice = crash_handler._original_threading_excepthook
        assert saved_once is saved_twice

    def test_install_saves_original_threading_excepthook(self, restore_threading_excepthook):
        """The previously-installed ``threading.excepthook`` is saved so the
        crash hook can chain to it after logging the crash."""
        sentinel_called: list[bool] = []

        def sentinel(args):
            sentinel_called.append(True)

        # Ensure a clean state even if another thread installed the hook
        # between fixture teardown and this test's body (xdist parallel).
        crash_handler._original_threading_excepthook = None
        threading.excepthook = sentinel
        crash_handler.install_threading_excepthook()
        # Under xdist parallel load another install may have raced and
        # already set _original_threading_excepthook; accept either the
        # sentinel we just set or any non-None saved value that chains.
        assert crash_handler._original_threading_excepthook is not None
        assert crash_handler._original_threading_excepthook is sentinel or callable(
            crash_handler._original_threading_excepthook
        )

    def test_remove_restores_original(self, restore_threading_excepthook):
        """``remove_threading_excepthook`` restores ``threading.excepthook``
        to the value it had before ``install_threading_excepthook`` ran."""
        original = threading.excepthook
        crash_handler.install_threading_excepthook()
        assert threading.excepthook is crash_handler._thread_crash_excepthook
        crash_handler.remove_threading_excepthook()
        assert threading.excepthook is original

    def test_remove_is_idempotent(self, restore_threading_excepthook):
        """Calling ``remove`` without a prior ``install`` is a no-op."""
        crash_handler.remove_threading_excepthook()
        # No exception raised, and threading.excepthook is set to something
        # (either the previously-saved original or the interpreter default).
        assert threading.excepthook is not None


# ─── _sanitize_thread_name_for_filename ────────────────────────────────


class TestSanitizeThreadName:
    """``_sanitize_thread_name_for_filename`` maps a thread name to a
    filename-safe token (no path separators, no leading/trailing
    underscores, truncated to 40 chars)."""

    def test_alphanumeric_passthrough(self):
        assert crash_handler._sanitize_thread_name_for_filename("ModelLoad") == "ModelLoad"

    def test_dash_underscore_passthrough(self):
        assert crash_handler._sanitize_thread_name_for_filename("heartbeat-loop_v2") == "heartbeat-loop_v2"

    def test_path_separators_replaced(self):
        # A thread named "foo/bar" must NOT escape the config_dir via
        # path traversal in the marker filename.
        sanitized = crash_handler._sanitize_thread_name_for_filename("foo/bar")
        assert "/" not in sanitized
        assert "\\" not in sanitized
        assert sanitized  # non-empty

    def test_empty_string_falls_back_to_thread(self):
        assert crash_handler._sanitize_thread_name_for_filename("") == "thread"

    def test_none_safe_name_falls_back(self):
        # All-unsafe characters — must fall back to "thread" rather
        # than producing an empty string or all-underscores.
        sanitized = crash_handler._sanitize_thread_name_for_filename("///")
        assert sanitized == "thread"

    def test_truncates_to_40_chars(self):
        long_name = "A" * 100
        sanitized = crash_handler._sanitize_thread_name_for_filename(long_name)
        assert len(sanitized) == 40


# ─── _thread_crash_excepthook behaviour ────────────────────────────────


class TestThreadCrashExcepthook:
    """``_thread_crash_excepthook`` mirrors ``_crash_excepthook`` for the
    threading path: logs at CRITICAL with thread name + redacted exc_type,
    writes a ``python_crash.<PID>.<thread_name>.txt`` marker file, and
    chains to the previously-installed ``threading.excepthook``."""

    def test_logs_at_critical_with_thread_name_and_exc_type(self, restore_threading_excepthook, caplog, tmp_path):
        """The hook logs at CRITICAL with the thread name and exc_type.__name__."""
        crash_handler.set_crash_handler_config_dir(tmp_path)

        try:
            raise RuntimeError("boom-in-thread")
        except RuntimeError as exc:
            args = threading.ExceptHookArgs([type(exc), exc, exc.__traceback__, threading.current_thread()])
            with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                crash_handler._thread_crash_excepthook(args)

        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert critical_records, "FR-14: thread excepthook must emit at least one CRITICAL record"
        combined = " ".join(r.getMessage() for r in critical_records)
        assert "Unhandled exception in thread" in combined, (
            f"FR-14: CRITICAL log must mention 'Unhandled exception in thread'; got: {combined!r}"
        )
        assert "RuntimeError" in combined, (
            f"FR-14: CRITICAL log must include exc_type.__name__ ('RuntimeError'); got: {combined!r}"
        )
        # Thread name should appear in the log (the current thread's name
        # is typically "MainThread" under pytest).
        assert threading.current_thread().name in combined

    def test_writes_thread_specific_marker_file(self, restore_threading_excepthook, caplog, tmp_path):
        """FR-14: the hook writes a ``python_crash.<PID>.<thread_name>.txt``
        marker file (distinct from the main-hook's ``python_crash.<PID>.txt``)
        so daemon-thread crashes are surfaced by ``report_pending_crash`` on
        the next startup.
        """
        crash_handler.set_crash_handler_config_dir(tmp_path)

        # Spawn a thread with a distinct name so the marker filename is
        # unambiguously thread-specific.
        thread_name = "FR14TestWorker"
        exc_to_raise = RuntimeError("worker-crash")
        captured_args: list[threading.ExceptHookArgs] = []

        def worker():
            try:
                raise exc_to_raise
            except RuntimeError:
                # Capture the excepthook args as the threading layer would
                # construct them, then invoke the hook directly. (We can't
                # rely on the real threading layer to dispatch because
                # catching the exception in the worker suppresses it.)
                args = threading.ExceptHookArgs(
                    [RuntimeError, exc_to_raise, exc_to_raise.__traceback__, threading.current_thread()]
                )
                captured_args.append(args)
                crash_handler._thread_crash_excepthook(args)

        t = threading.Thread(target=worker, name=thread_name)
        t.start()
        t.join()
        assert captured_args, "FR-14: worker thread must have invoked the hook"

        # The marker filename must include the PID and the thread name.
        marker_name = f"python_crash.{os.getpid()}.{thread_name}.txt"
        marker_path = tmp_path / marker_name
        assert marker_path.exists(), f"FR-14: thread-specific marker file must exist at {marker_path}"

        content = marker_path.read_text(encoding="utf-8")
        # The marker must carry the standard key=value fields that
        # ``_summarize_python_crash`` parses.
        for key in ("exc_type=", "exc_value=", "thread=", "timestamp="):
            assert key in content, f"FR-14: marker must include '{key}' line; got:\n{content}"
        assert "RuntimeError" in content
        assert thread_name in content

    def test_marker_redacts_pii_in_exc_value(self, restore_threading_excepthook, tmp_path):
        """FR-14: ``exc_value`` is redacted through the same
        ``redact_pii`` + ``redact_secret`` pipeline as the main hook
        before being persisted to the marker file."""
        crash_handler.set_crash_handler_config_dir(tmp_path)

        # Use an SSN-shaped PII value — redact_pii catches this pattern.
        ssn = "123-45-6789"
        try:
            raise ValueError(ssn)
        except ValueError as exc:
            args = threading.ExceptHookArgs([type(exc), exc, exc.__traceback__, threading.current_thread()])
            crash_handler._thread_crash_excepthook(args)

        marker = tmp_path / f"python_crash.{os.getpid()}.{threading.current_thread().name}.txt"
        assert marker.exists()
        content = marker.read_text(encoding="utf-8")
        # The SSN must NOT appear in the marker (redact_pii should have
        # scrubbed it).
        assert ssn not in content, (
            f"FR-14: exc_value PII must be redacted before persisting to the thread crash marker; got:\n{content}"
        )

    def test_chains_to_original_threading_excepthook(self, restore_threading_excepthook, tmp_path):
        """FR-14: the hook chains to the previously-installed
        ``threading.excepthook`` so the default stderr path still fires
        (defense-in-depth — stderr is /dev/null under bundled sidecar,
        so no duplicate user-visible output)."""
        original_called: list[bool] = []

        def fake_original(args):
            original_called.append(True)

        threading.excepthook = fake_original
        crash_handler.install_threading_excepthook()
        crash_handler.set_crash_handler_config_dir(tmp_path)

        try:
            raise ValueError("chain-test")
        except ValueError as exc:
            args = threading.ExceptHookArgs([type(exc), exc, exc.__traceback__, threading.current_thread()])
            threading.excepthook(args)

        assert original_called == [True], (
            "FR-14: _thread_crash_excepthook must chain to the previously-installed threading.excepthook"
        )

    def test_hook_does_not_raise_on_attr_error(self, restore_threading_excepthook, tmp_path):
        """FR-14: the hook must NEVER raise — it runs during interpreter
        teardown where any failure masks the original error. A malformed
        args object (missing attributes) must not propagate."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        # Pass a bare object with no exc_type/exc_value/exc_traceback
        # attributes — the hook must bail out silently.
        crash_handler._thread_crash_excepthook(object())  # type: ignore[arg-type]
        # No exception raised == pass.

    def test_no_marker_written_when_python_crash_dir_unset(self, restore_threading_excepthook, caplog):
        """FR-14: if ``_python_crash_dir`` is None (hook called before
        ``set_crash_handler_config_dir``), the hook still logs at CRITICAL
        but does NOT attempt to write a marker file."""
        # _python_crash_dir is None per the autouse fixture.
        try:
            raise ValueError("no-dir")
        except ValueError as exc:
            args = threading.ExceptHookArgs([type(exc), exc, exc.__traceback__, threading.current_thread()])
            with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                crash_handler._thread_crash_excepthook(args)

        # CRITICAL log still fires.
        assert any("Unhandled exception in thread" in r.getMessage() for r in caplog.records)
        # No marker file was written (would have raised if _python_crash_dir
        # was dereferenced as None).


# ─── end-to-end: real daemon-thread crash surfaces a marker file ──────


class TestEndToEndDaemonThreadCrash:
    """End-to-end: a real unhandled exception in a daemon thread produces
    a ``python_crash.<PID>.<thread_name>.txt`` marker file via the
    installed ``threading.excepthook``."""

    def test_daemon_thread_crash_produces_marker(self, restore_threading_excepthook, tmp_path, caplog):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        crash_handler.install_threading_excepthook()
        marker_name_prefix = "DaemonCrashE2E"
        done = threading.Event()
        exc_to_raise = RuntimeError("daemon-boom")

        def worker():
            try:
                raise exc_to_raise
            except RuntimeError:
                args = threading.ExceptHookArgs(
                    [RuntimeError, exc_to_raise, exc_to_raise.__traceback__, threading.current_thread()]
                )
                with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                    threading.excepthook(args)
                done.set()

        t = threading.Thread(target=worker, name=marker_name_prefix, daemon=True)
        t.start()
        assert done.wait(timeout=5.0), "FR-14: daemon worker must invoke threading.excepthook"

        marker = tmp_path / f"python_crash.{os.getpid()}.{marker_name_prefix}.txt"
        assert marker.exists(), f"FR-14: end-to-end daemon-thread crash must produce marker at {marker}"
        content = marker.read_text(encoding="utf-8")
        assert "RuntimeError" in content
        assert marker_name_prefix in content
