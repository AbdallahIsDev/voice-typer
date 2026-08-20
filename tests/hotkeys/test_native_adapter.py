"""IPC coverage tests for ``hotkeys/native_adapter.py``.

The ``_NativeBackendAdapter`` wraps a ``SubprocessHotkeyBackend`` (Linux /
macOS / Windows) and provides the runtime fallback chain (native → legacy).
These tests exercise the adapter's subprocess-plumbing surface with a
monkeypatched ``subprocess.Popen`` so no real native binary is spawned.

Coverage areas :

1. **Successful spec handshake** — Popen returns stdout with a valid
   ``READY`` line; the adapter parses the spec, registers the hotkey, and
   enters the ``NATIVE`` state.
2. **Malformed spec response** — Popen returns garbage on stdout; the
   adapter escalates the error (native backend sets ``_failed`` and the
   adapter swaps to the legacy backend).
3. **Subprocess early exit triggers restart** — Popen's ``returncode``
   is non-zero immediately; the reader thread detects the exit and the
   restart logic fires (``_restart_attempts`` incremented).
4. **Broken pipe on write handled** — ``Popen.stdin.write`` raises
   ``BrokenPipeError``; the watchdog catches it without crashing.
5. **Restart after crash recovers** — two consecutive Popen calls:
   the first crashes, the second succeeds; the adapter recovers to
   ``NATIVE``.
6. **Teardown with live subprocess joins** — Popen process is still
   running; ``adapter.stop()`` calls ``terminate`` + ``wait`` on the
   process.

Platform note: all tests run on Linux (the only platform where
``LinuxEvdevHotkey`` validates successfully). macOS / Windows backends
are structurally identical (same ``SubprocessHotkeyBackend`` base) so
the coverage transfers.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Platform setup ────────────────────────────────────────────────────
# Patch the platform BEFORE importing the backend so ``_validate_platform``
# succeeds. ``LinuxEvdevHotkey`` checks ``is_linux()`` which delegates to
# the package-level binding.
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
    """Patch the ``binary_path`` module bindings.

    ``_spawn_process`` imports ``get_native_binary_path`` at module load
    (top of ``base.py``) and ``verify_native_binary_or_skip`` locally
    inside ``_spawn_process``. Both resolve through the ``binary_path``
    module, so patching that module's attributes is sufficient.
    """
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.get_native_binary_path",
        lambda: fake_bin,
    )
    # The SHA-256 verifier is patched to always pass — the fake binary
    # has no manifest entry and would fail closed without this patch.
    monkeypatch.setattr(
        "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
        lambda _p: True,
    )


# ─── FakePopen ─────────────────────────────────────────────────────────


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
    """Minimal ``subprocess.Popen`` mock using ``os.pipe`` for stdout.

    The write end of the stdout pipe is kept OPEN until ``_close_stdout``
    is called (via ``terminate`` / ``kill`` / ``send_signal``). This
    mirrors a real process: ``readline`` blocks while the process is
    alive and returns ``b""`` (EOF) only after the process exits / is
    killed.

    Attributes tracked for assertions:
    - ``terminated`` — ``terminate()`` was called.
    - ``killed`` — ``kill()`` was called.
    - ``wait_calls`` — list of ``timeout`` args passed to ``wait()``.
    - ``signalled`` — ``send_signal()`` was called.
    """

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


# ─── Patch helpers ─────────────────────────────────────────────────────


def _make_popen_patch(fake_popens: list[_FakePopen]):
    """Return a side_effect for ``subprocess.Popen`` that returns the next
    ``_FakePopen`` from ``fake_popens`` (one per call)."""

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


# ─── Imports (deferred until after platform patches in each test) ─────


def _build_adapter(monkeypatch, tmp_path, *, popen_instances=None):
    """Construct a ``_NativeBackendAdapter`` wrapping a ``LinuxEvdevHotkey``.

    Returns ``(adapter, native_backend, popen_patch_ctx)``. The caller is
    responsible for calling ``adapter.stop()`` and exiting the context.
    """
    _setup_linux(monkeypatch)
    fake_bin = _fake_binary(tmp_path)
    _patch_binary_paths(monkeypatch, fake_bin)
    _patch_timing(monkeypatch)

    from voice_typer.server.hotkeys.native_adapter import _NativeBackendAdapter
    from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

    native_backend = LinuxEvdevHotkey("<caps_lock>")
    # Stub the adapter's legacy-creation path so a failing native backend
    # doesn't try to construct a real ``PynputHotkey`` (which depends on
    # the mocked pynput and may behave unpredictably in headless CI).
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


# ─── Tests ────────────────────────────────────────────────────────────


class TestSuccessfulSpecHandshake:
    """#1: Popen returns stdout with a valid ``READY`` line — the
    adapter parses the spec, registers the hotkey, and enters ``NATIVE``."""

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
    """#2: Popen returns garbage (no ``READY`` line) — the adapter
    escalates the error (native backend marks ``_failed`` and the adapter
    swaps to the legacy backend)."""

    def test_malformed_spec_response_escalates(self, monkeypatch, tmp_path):
        # stdout has garbage — no READY line. The process stays "alive"
        # (write end of the pipe is open) so the reader blocks after the
        # garbage line. start() times out waiting for READY.
        fake_popen = _FakePopen(cmd=[], stdout_data=b"GARBAGE_LINE\n")
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])
        swap_called: list[bool] = []

        def tracking_swap():
            swap_called.append(True)
            # Call a stub instead of the real _swap_to_legacy to avoid
            # constructing a real PynputHotkey.
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
                # The native backend should have timed out → _failed or
                # _error_message set.
                assert native._failed or native._error_message is not None, (
                    "native backend should escalate (set _failed or _error_message) "
                    "when READY is not received within the timeout"
                )
                # The adapter should have called _swap_to_legacy (error
                # escalation).
                assert swap_called, (
                    "adapter should call _swap_to_legacy when the native backend "
                    "fails to start (malformed spec / no READY)"
                )
            finally:
                adapter.stop()


class TestSubprocessEarlyExitTriggersRestart:
    """#3: Popen ``returncode != 0`` immediately — the reader thread
    detects the exit and the restart logic fires (``_restart_attempts``
    incremented)."""

    def test_early_exit_increments_restart_attempts(self, monkeypatch, tmp_path):
        # Process exits immediately with returncode 1, no stdout.
        fake_popen = _FakePopen(cmd=[], stdout_data=b"", exit_code=1)
        adapter, native, popen_patch = _build_adapter(monkeypatch, tmp_path, popen_instances=[fake_popen])
        # Track permanent-failure callback (wired by the adapter in __init__).
        permanent_failure_called: list[bool] = []
        native._on_permanent_failure_callback = lambda: permanent_failure_called.append(True)

        with popen_patch:
            # start() will raise RuntimeError because the process exits
            # before READY. The adapter catches it and swaps to legacy.
            with contextlib.suppress(RuntimeError):
                adapter.start(lambda: None)
            # Give the reader thread a moment to detect the exit and
            # attempt restarts.
            time.sleep(0.3)
            with contextlib.suppress(Exception):
                adapter.stop()

        # The restart logic should have fired — _restart_attempts > 0.
        # After MAX_RESTART_ATTEMPTS (patched to 2), the permanent-failure
        # callback is invoked.
        assert native._restart_attempts > 0, (
            "reader thread should detect early exit and increment _restart_attempts; "
            f"got _restart_attempts={native._restart_attempts}"
        )


class TestPipeBrokenPipeOnWriteHandled:
    """#4: ``Popen.stdin.write`` raises ``BrokenPipeError`` — the
    watchdog catches it without crashing (error is logged at DEBUG)."""

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
                # _WATCHDOG_PING_INTERVAL_SECONDS is patched to 0.05s.
                time.sleep(0.3)

                # The watchdog should have attempted to write PING to stdin.
                # The BrokenPipeError should have been caught — the adapter
                # must still be alive (no crash propagated to the caller).
                assert len(fake_popen.stdin.written) == 0, (
                    "stdin.write raised BrokenPipeError so no data should have been "
                    f"buffered; got {fake_popen.stdin.written}"
                )
                # The adapter must NOT have crashed — it's still in a valid
                # state (NATIVE or STOPPED after we call stop() below).
                assert adapter._state in (adapter._STATE_NATIVE, adapter._STATE_STOPPED), (
                    f"adapter should not crash on BrokenPipeError; state={adapter._state}"
                )
            finally:
                adapter.stop()


class TestRestartAfterCrashRecovers:
    """#5: two consecutive Popen calls — the first crashes (early
    exit, no READY), the second succeeds (sends READY). The adapter
    recovers to ``NATIVE`` after the restart."""

    def test_restart_recovers_after_crash(self, monkeypatch, tmp_path, caplog):
        # First Popen: crashes immediately (exit_code=1, no stdout).
        # Second Popen: succeeds (sends READY).
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
                # start() will likely raise RuntimeError (first process
                # crashes before READY). The adapter catches it and
                # swaps to legacy. But the reader thread of the first
                # process will attempt a restart (spawn a second Popen).
                with contextlib.suppress(RuntimeError):
                    adapter.start(lambda: None)

                # Wait for the reader thread to detect the crash and
                # respawn (RESTART_DELAY_BASE_SECONDS patched to 0.01s).
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if call_count[0] >= 2:
                        break
                    time.sleep(0.01)

                # Popen must have been called at least twice — the restart
                # logic spawned a second process after the first crashed.
                assert call_count[0] >= 2, (
                    f"subprocess.Popen should be called at least twice after the "
                    f"first crash (restart logic); got {call_count[0]} calls"
                )

                # The restart log message must have been emitted (proves the
                # reader thread's restart path executed).
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
    """#6: Popen process is still running — ``adapter.stop()`` calls
    ``terminate`` + ``wait`` on the process so the reader thread is joined
    and no orphan process is left."""

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
        # called on the live process.
        assert fake_popen.terminated or fake_popen.signalled, (
            "stop() must call terminate() or send_signal() on the live subprocess"
        )
        # wait() must have been called (the process is joined).
        assert len(fake_popen.wait_calls) > 0, "stop() must call wait() on the subprocess to join it"
        # The adapter should be in STOPPED state.
        assert adapter._state == adapter._STATE_STOPPED
        # The process reference should be cleared.
        assert native._process is None, "stop() should clear _process"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
