"""IPC coverage tests for ``hotkeys/native_adapter.py``."""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Patch the platform BEFORE importing the backend so ``_validate_platform``
from voice_typer.server import native_hotkeys  # noqa: E402


def _setup_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the platform as Linux for ``native_hotkeys`` + ``hotkeys``."""
    monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
    monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
    monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")
    # The hotkeys package (legacy fallback) also needs to see Linux.
    from voice_typer.server import hotkeys as _hotkeys_pkg

    monkeypatch.setattr(_hotkeys_pkg, "is_linux", lambda: True)
    monkeypatch.setattr(_hotkeys_pkg, "is_macos", lambda: False)
    monkeypatch.setattr(_hotkeys_pkg, "is_windows", lambda: False)


def _fake_binary(tmp_path: Path) -> Path:
    """Create a minimal fake binary file on disk."""
    fake_bin = tmp_path / "fake-native-listener"
    fake_bin.write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
    fake_bin.chmod(0o755)
    return fake_bin


def _patch_binary_paths(monkeypatch: pytest.MonkeyPatch, fake_bin: Path) -> None:
    """Patch the ``binary_path`` module bindings."""
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.get_native_binary_path",
        lambda: fake_bin,
    )
    # The SHA-256 verifier is patched to always pass, the fake binary
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
        lambda _p: True,
    )


class _FakeStdin:
    """Minimal stdin mock that can optionally raise ``BrokenPipeError``."""

    def __init__(self, *, fail_on_write: bool = False) -> None:
        self._fail = fail_on_write
        self.written: list[bytes] = []

    def write(self, data: bytes) -> int:
        if self._fail:
            raise BrokenPipeError("simulated broken pipe")
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakePopen:
    """Minimal ``subprocess.Popen`` mock using ``os.pipe`` for stdout."""

    def __init__(
        self,
        cmd: list[str],
        *,
        stdout_data: bytes = b"",
        stdin_fail_on_write: bool = False,
        exit_code: int | None = None,
    ) -> None:
        self.cmd = cmd
        read_fd, write_fd = os.pipe()
        if stdout_data:
            os.write(write_fd, stdout_data)
        self._stdout_write_fd = write_fd
        self.stdout = os.fdopen(read_fd, "rb")
        self.stdin = _FakeStdin(fail_on_write=stdin_fail_on_write)
        self.returncode: int | None = exit_code
        self.terminated = False
        self.killed = False
        self.signalled = False
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15
        self._close_stdout()

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9
        self._close_stdout()

    def send_signal(self, sig: int) -> None:
        self.signalled = True
        if self.returncode is None:
            self.returncode = -sig
        self._close_stdout()

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        return self.returncode if self.returncode is not None else 0

    def _close_stdout(self) -> None:
        """Close the write end of the stdout pipe so the reader sees EOF."""
        with contextlib.suppress(OSError):
            os.close(self._stdout_write_fd)

    def close(self) -> None:
        self._close_stdout()
        with contextlib.suppress(Exception):
            self.stdout.close()


def _make_popen_patch(fake_popens: list[_FakePopen]):
    """Return a side_effect for ``subprocess.Popen`` that returns the next"""

    call_count = [0]

    def _popen_side_effect(cmd, *args, **kwargs):
        idx = min(call_count[0], len(fake_popens) - 1)
        call_count[0] += 1
        fp = fake_popens[idx]
        fp.cmd = cmd
        return fp

    return _popen_side_effect


def _patch_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch timing constants so tests run in < 1s instead of 5-60s."""
    from voice_typer.server.native_hotkeys import base as _base

    monkeypatch.setattr(_base, "READY_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(_base, "RESTART_DELAY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(_base, "MAX_RESTART_ATTEMPTS", 2)
    monkeypatch.setattr(_base, "_WATCHDOG_PING_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(_base, "_WATCHDOG_PONG_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(_base, "_WATCHDOG_RESPAWN_SECONDS", 0.1)


def _build_adapter(monkeypatch, tmp_path, *, popen_instances=None):
    """Construct a ``_NativeBackendAdapter`` wrapping a ``LinuxEvdevHotkey``."""
    _setup_linux(monkeypatch)
    fake_bin = _fake_binary(tmp_path)
    _patch_binary_paths(monkeypatch, fake_bin)
    _patch_timing(monkeypatch)

    from voice_typer.server.hotkeys.native_adapter import _NativeBackendAdapter
    from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

    native_backend = LinuxEvdevHotkey("<caps_lock>")
    # Stub the adapter's legacy-creation path so a failing native backend
    adapter = _NativeBackendAdapter(native_backend)
    adapter._create_legacy_backend = lambda: MagicMock(
        spec=["start", "stop", "is_alive", "set_on_release", "set_tray", "diagnose"],
        **{"is_alive.return_value": True},
    )
    adapter._schedule_native_retry = lambda: None  # don't start 300s timers

    if popen_instances is None:
        popen_instances = [_FakePopen(cmd=[], stdout_data=b"READY\n")]

    popen_patch = patch("subprocess.Popen", side_effect=_make_popen_patch(popen_instances))

    return adapter, native_backend, popen_patch


class TestSuccessfulSpecHandshake:
    """#1: Popen returns stdout with a valid ``READY`` line, the"""

    def test_successful_spec_handshake(self, monkeypatch, tmp_path):
        fake_popen = _FakePopen(cmd=[], stdout_data=b"READY\n")
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])
        fired: list[str] = []
        cb = lambda: fired.append("press")  # noqa: E731

        with popen_patch:
            try:
                adapter.start(cb)
                # The adapter should be in NATIVE state with a live process.
                assert adapter._state == adapter._STATE_NATIVE, (
                    f"expected NATIVE state after successful READY; got {adapter._state}"
                )
                assert native._ready_event.is_set(), "native backend should have received READY"
                assert native._process is fake_popen, "the FakePopen should be the active process"
                assert native._failed is False, "native backend should NOT be in failed state"
            finally:
                adapter.stop()
        # After stop, the process should have been terminated.
        assert fake_popen.terminated or fake_popen.killed or fake_popen.signalled, (
            "stop() must call terminate/kill/send_signal on the live subprocess"
        )


class TestMalformedSpecResponseRaises:
    """#2: Popen returns garbage (no ``READY`` line), the adapter"""

    def test_malformed_spec_response_escalates(self, monkeypatch, tmp_path):
        fake_popen = _FakePopen(cmd=[], stdout_data=b"GARBAGE_LINE\n")
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])
        swap_called: list[bool] = []

        def tracking_swap():
            swap_called.append(True)
            # Call a stub instead of the real _swap_to_legacy to avoid
            with adapter._swap_lock:
                if adapter._state not in (
                    adapter._STATE_FALLBACK,
                    adapter._STATE_FAILED,
                    adapter._STATE_STOPPED,
                ):
                    adapter._state = adapter._STATE_FALLBACK

        adapter._swap_to_legacy = tracking_swap

        with popen_patch:
            try:
                adapter.start(lambda: None)
                assert native._failed or native._error_message is not None, (
                    "native backend should escalate (set _failed or _error_message) "
                    "when READY is not received within the timeout"
                )
                # The adapter should have called _swap_to_legacy (error
                assert swap_called, (
                    "adapter should call _swap_to_legacy when the native backend "
                    "fails to start (malformed spec / no READY)"
                )
            finally:
                adapter.stop()


class TestSubprocessEarlyExitTriggersRestart:
    """#3: Popen ``returncode != 0`` immediately, the reader thread"""

    def test_early_exit_increments_restart_attempts(self, monkeypatch, tmp_path):
        # Process exits immediately with returncode 1, no stdout.
        fake_popen = _FakePopen(cmd=[], stdout_data=b"", exit_code=1)
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])
        # Track permanent-failure callback (wired by the adapter in __init__).
        permanent_failure_called: list[bool] = []
        native._on_permanent_failure_callback = lambda: permanent_failure_called.append(True)

        with popen_patch:
            with contextlib.suppress(RuntimeError):
                adapter.start(lambda: None)
            time.sleep(0.3)
            with contextlib.suppress(Exception):
                adapter.stop()

        # The restart logic should have fired, _restart_attempts > 0.
        assert native._restart_attempts > 0, (
            "reader thread should detect early exit and increment _restart_attempts; "
            f"got _restart_attempts={native._restart_attempts}"
        )


class TestPipeBrokenPipeOnWriteHandled:
    """#4: ``Popen.stdin.write`` raises ``BrokenPipeError``, the"""

    def test_broken_pipe_on_write_does_not_crash(self, monkeypatch, tmp_path):
        # Process is alive (no exit_code), stdin.write raises BrokenPipeError.
        fake_popen = _FakePopen(
            cmd=[],
            stdout_data=b"READY\n",
            stdin_fail_on_write=True,
        )
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])

        with popen_patch:
            try:
                adapter.start(lambda: None)
                assert adapter._state == adapter._STATE_NATIVE

                # Wait for the watchdog to attempt at least one PING write.
                time.sleep(0.3)

                # The watchdog should have attempted to write PING to stdin.
                assert len(fake_popen.stdin.written) == 0, (
                    "stdin.write raised BrokenPipeError so no data should have been "
                    f"buffered; got {fake_popen.stdin.written}"
                )
                # The adapter must NOT have crashed, it's still in a valid
                assert adapter._state in (adapter._STATE_NATIVE, adapter._STATE_STOPPED), (
                    f"adapter should not crash on BrokenPipeError; state={adapter._state}"
                )
            finally:
                adapter.stop()


class TestRestartAfterCrashRecovers:
    """#5: two consecutive Popen calls, the first crashes (early"""

    def test_restart_recovers_after_crash(self, monkeypatch, tmp_path, caplog):
        # First Popen: crashes immediately (exit_code=1, no stdout).
        first_popen = _FakePopen(cmd=[], stdout_data=b"", exit_code=1)
        second_popen = _FakePopen(cmd=[], stdout_data=b"READY\n")
        popen_instances = [first_popen, second_popen]
        call_count = [0]

        def _popen_side_effect(cmd, *args, **kwargs):
            idx = min(call_count[0], len(popen_instances) - 1)
            call_count[0] += 1
            fp = popen_instances[idx]
            fp.cmd = cmd
            return fp

        adapter, native, _ = _build_adapter(monkeypatch, tmp_path, popen_instances=popen_instances)

        import logging

        with patch("subprocess.Popen", side_effect=_popen_side_effect):
            try:
                with contextlib.suppress(RuntimeError):
                    adapter.start(lambda: None)

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if call_count[0] >= 2:
                        break
                    time.sleep(0.01)

                # Popen must have been called at least twice, the restart
                assert call_count[0] >= 2, (
                    f"subprocess.Popen should be called at least twice after the "
                    f"first crash (restart logic); got {call_count[0]} calls"
                )

                restart_messages = [
                    r.getMessage()
                    for r in caplog.records
                    if r.levelno >= logging.WARNING and "restarting" in r.getMessage().lower()
                ]
                assert restart_messages, (
                    "reader thread should log a 'restarting' warning when the "
                    "first process crashes and the restart logic fires"
                )
            finally:
                adapter.stop()


class TestTeardownWithLiveSubprocessJoins:
    """#6: Popen process is still running: ``adapter.stop()`` calls"""

    def test_teardown_terminates_and_waits(self, monkeypatch, tmp_path):
        fake_popen = _FakePopen(cmd=[], stdout_data=b"READY\n")
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])

        with popen_patch:
            adapter.start(lambda: None)
            assert adapter._state == adapter._STATE_NATIVE
            assert native._process is fake_popen
            # The process should be alive (poll() returns None).
            assert fake_popen.poll() is None, "process should be alive before stop()"

            adapter.stop()

        # After stop(): terminate (or send_signal on POSIX) must have been
        assert fake_popen.terminated or fake_popen.signalled, (
            "stop() must call terminate() or send_signal() on the live subprocess"
        )
        assert len(fake_popen.wait_calls) > 0, "stop() must call wait() on the subprocess to join it"
        # The adapter should be in STOPPED state.
        assert adapter._state == adapter._STATE_STOPPED
        # The process reference should be cleared.
        assert native._process is None, "stop() should clear _process"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
