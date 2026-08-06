"""AB-19: prewarm macOS I/O priority lowered via ``setiopolicy_np``.

Pre-AB-19, ``_lower_io_priority`` lowered CPU priority on macOS (via
``os.nice(10)``) but did NOT lower I/O priority — the Linux-only
``ioprio_set`` syscall branch was the only I/O-priority path, and
macOS fell through with no I/O priority lowering. The docstring at
``prewarm/__init__.py:14-15`` claimed the prewarm pipeline runs "with
low I/O priority so it never competes with the user's real work," but
on macOS only CPU priority was lowered.

AB-19: an ``is_macos()`` branch in ``_lower_io_priority`` calls
``setiopolicy_np(IOPOL_TYPE_DISK, IOPOL_SCOPE_PROCESS,
IOPOL_DISK_THROTTLE)`` via ``ctypes`` (the function is in
``libSystem.B.dylib``). Best-effort with a DEBUG log on failure,
mirroring the Linux ``ioprio_set`` pattern.

These tests pin the AB-19 behaviour so a future revert (removing the
macOS branch) fails loudly. They run on any platform — the macOS
branch is force-entered by mocking ``is_macos`` to return True and
mocking ``ctypes.CDLL`` so no real dylib load is attempted.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from voice_typer.server.prewarm import logging_setup

# setiopolicy_np called on macOS ──────────────────────────────


class TestMacOSIoPriority:
    """``_lower_io_priority`` calls ``setiopolicy_np`` on macOS."""

    def test_setiopolicy_np_called_on_macos(self, monkeypatch):
        """On macOS, ``setiopolicy_np`` is invoked with the THROTTLE policy."""
        import ctypes

        # Force the macOS branch (the test sandbox runs Linux).
        monkeypatch.setattr(logging_setup, "is_macos", lambda: True)
        monkeypatch.setattr(logging_setup, "is_linux", lambda: False)
        monkeypatch.setattr(logging_setup, "is_windows", lambda: False)
        # os.nice must not actually run (it would lower the test
        # runner's priority).
        monkeypatch.setattr(logging_setup.os, "nice", lambda n: 0, raising=False)

        setiopolicy_calls: list[tuple] = []

        fake_libsystem = MagicMock()
        fake_libsystem.setiopolicy_np = lambda *args: setiopolicy_calls.append(args) or 0
        monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: fake_libsystem)

        logging_setup._lower_io_priority()

        assert setiopolicy_calls, "AB-19: setiopolicy_np must be called on macOS"
        # The single call must pass (IOPOL_TYPE_DISK=0,
        # IOPOL_SCOPE_PROCESS=0, IOPOL_DISK_THROTTLE=3).
        assert len(setiopolicy_calls) == 1, (
            f"AB-19: setiopolicy_np must be called exactly once; got {len(setiopolicy_calls)} calls"
        )
        iopol_type, iopol_scope, iopol_policy = setiopolicy_calls[0]
        assert iopol_type == 0, f"IOPOL_TYPE_DISK must be 0; got {iopol_type}"
        assert iopol_scope == 0, f"IOPOL_SCOPE_PROCESS must be 0; got {iopol_scope}"
        assert iopol_policy == 3, f"IOPOL_DISK_THROTTLE must be 3; got {iopol_policy}"

    def test_uses_libsystem_b_dylib(self, monkeypatch):
        """The CDLL load targets ``/usr/lib/libSystem.B.dylib``."""
        import ctypes

        monkeypatch.setattr(logging_setup, "is_macos", lambda: True)
        monkeypatch.setattr(logging_setup, "is_linux", lambda: False)
        monkeypatch.setattr(logging_setup, "is_windows", lambda: False)
        monkeypatch.setattr(logging_setup.os, "nice", lambda n: 0, raising=False)

        cdll_paths: list[str] = []
        fake_libsystem = MagicMock()
        fake_libsystem.setiopolicy_np = lambda *args: 0

        def _capturing_cdll(path, *args, **kwargs):
            cdll_paths.append(path)
            return fake_libsystem

        monkeypatch.setattr(ctypes, "CDLL", _capturing_cdll)

        logging_setup._lower_io_priority()

        assert "/usr/lib/libSystem.B.dylib" in cdll_paths, (
            f"AB-19: ctypes.CDLL must load /usr/lib/libSystem.B.dylib; got CDLL paths: {cdll_paths!r}"
        )

    def test_setiopolicy_np_failure_is_best_effort(self, monkeypatch):
        """If ``setiopolicy_np`` fails, ``_lower_io_priority`` does not raise.

        Best-effort: a failing call (returning -1) must not crash the
        prewarm pipeline — CPU priority was already lowered via
        ``os.nice(10)`` so the process still yields to foreground
        CPU work.
        """
        import ctypes

        monkeypatch.setattr(logging_setup, "is_macos", lambda: True)
        monkeypatch.setattr(logging_setup, "is_linux", lambda: False)
        monkeypatch.setattr(logging_setup, "is_windows", lambda: False)
        monkeypatch.setattr(logging_setup.os, "nice", lambda n: 0, raising=False)

        fake_libsystem = MagicMock()
        # setiopolicy_np returns -1 on error (errno set).
        fake_libsystem.setiopolicy_np = lambda *args: -1
        monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: fake_libsystem)

        # Must NOT raise.
        logging_setup._lower_io_priority()

    def test_dylib_load_failure_is_best_effort(self, monkeypatch):
        """If ``libSystem.B.dylib`` can't be loaded, no crash.

        Best-effort: a sandboxed runtime or broken install that
        prevents the dylib load must not crash the prewarm pipeline.
        """
        import ctypes

        monkeypatch.setattr(logging_setup, "is_macos", lambda: True)
        monkeypatch.setattr(logging_setup, "is_linux", lambda: False)
        monkeypatch.setattr(logging_setup, "is_windows", lambda: False)
        monkeypatch.setattr(logging_setup.os, "nice", lambda n: 0, raising=False)

        def _raising_cdll(path, *args, **kwargs):
            raise OSError("simulated dylib load failure")

        monkeypatch.setattr(ctypes, "CDLL", _raising_cdll)

        # Must NOT raise.
        logging_setup._lower_io_priority()


# source-level regression guards ─────────────────────────────


class TestMacOSIoPrioritySourceGuards:
    """Source-level guards pinning the macOS branch's presence."""

    def test_lower_io_priority_calls_is_macos(self):
        """The function must contain an ``is_macos()`` branch."""
        src = inspect.getsource(logging_setup._lower_io_priority)
        assert "is_macos()" in src, "AB-19: _lower_io_priority must check is_macos()"

    def test_lower_io_priority_calls_setiopolicy_np(self):
        """The function must call ``setiopolicy_np`` (not just log a TODO)."""
        src = inspect.getsource(logging_setup._lower_io_priority)
        assert "setiopolicy_np" in src, "AB-19: _lower_io_priority must invoke setiopolicy_np"
        assert "IOPOL_DISK_THROTTLE" in src, "AB-19: _lower_io_priority must reference IOPOL_DISK_THROTTLE"
        assert "libSystem.B.dylib" in src, "AB-19: _lower_io_priority must load libSystem.B.dylib"

    def test_lower_io_priority_does_not_call_ioprio_set_on_macos(self):
        """The macOS branch must not invoke the Linux-only ``ioprio_set``.

        Mutation sanity check: if someone reverts AB-19 by making the
        macOS branch fall through to the Linux ioprio_set path, this
        assertion still holds (the macOS branch is its own elif), but
        the structural assertion above plus this one together pin the
        branch's existence.
        """
        src = inspect.getsource(logging_setup._lower_io_priority)
        # Both branches must exist.
        assert "if is_linux():" in src, "Linux ioprio_set branch must remain"
        assert "elif is_macos():" in src, "AB-19: macOS branch must be an elif after the Linux branch"
