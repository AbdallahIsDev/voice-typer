"""Tests for ``voice_typer.server.prewarm.process_tracker`` (CR-76).

This module covers the PID-file + process-liveness + status-query helpers
that were extracted from the original ``prewarm.py`` god-module into
:mod:`voice_typer.server.prewarm.process_tracker` (ARCH-045 / SPLIT-4).

Patch-path bridge
-----------------
Production code in ``process_tracker.py`` looks up cross-submodule helpers
through ``_pkg.X()`` (where ``_pkg`` is the package namespace
``voice_typer.server.prewarm``), so tests patch the helpers on the
package via ``monkeypatch.setattr(prewarm, "X", ...)`` — the same pattern
used by ``tests/test_prewarm.py``. This ensures the patches take effect
for code that lives in the submodule file rather than the re-exporting
``__init__.py``.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest
from voice_typer.server import prewarm
from voice_typer.server.prewarm import process_tracker

# ─── Helpers ────────────────────────────────────────────────────────────


def _make_pid_file(tmp_path, pid: int) -> None:
    """Write a prewarm-style PID file pointing at ``pid``."""
    pid_file = tmp_path / ".prewarm.pid"
    pid_file.write_text(str(pid))


# ─── Basic PID-file + liveness ──────────────────────────────────────────


class TestProcessTrackerBasic:
    """Basic single-call coverage for the PID-file helpers."""

    def test_is_prewarm_running_no_pid_file(self, monkeypatch, tmp_path):
        """``is_prewarm_running`` returns False when no PID file exists."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert process_tracker.is_prewarm_running() is False

    def test_is_prewarm_running_with_dead_pid(self, monkeypatch, tmp_path):
        """PID file pointing at a dead PID returns False and cleans up."""
        pid_file = tmp_path / ".prewarm.pid"
        # 2**31 - 1 — a PID that is effectively never running in tests.
        pid_file.write_text("2147483647")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # _process_is_prewarm is only reached when _process_alive returns
        # True, which won't happen for our dead PID. So no patch needed.
        assert process_tracker.is_prewarm_running() is False
        # Dead-PID branch does NOT delete the file (only the PID-recycled
        # branch does — see is_prewarm_running docstring). Just verify
        # the function returned the right value.
        assert pid_file.exists()

    def test_is_prewarm_running_with_corrupt_pid_file(self, monkeypatch, tmp_path):
        """Garbage contents in the PID file must not crash — return False."""
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("not-a-number")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert process_tracker.is_prewarm_running() is False

    def test_is_prewarm_running_pid_recycled_clears_stale_file(self, monkeypatch, tmp_path):
        """A PID file pointing at a live, non-prewarm process is stale.

        ADR-0009 Issue 4 (H4): the PID-recycling guard calls
        ``_process_is_prewarm`` to confirm the live PID actually IS
        prewarm. If it isn't, the stale PID file is removed and
        ``is_prewarm_running`` returns False so the caller doesn't block
        for the full 60s timeout in ``wait_for_prewarm``.
        """
        pid_file = tmp_path / ".prewarm.pid"
        # Use our own PID — it's alive, but it's pytest, not prewarm.
        pid_file.write_text(str(os.getpid()))
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # Force the "is this prewarm?" check to say no.
        monkeypatch.setattr(prewarm, "_process_is_prewarm", lambda pid: False)
        removed: list[bool] = []
        monkeypatch.setattr(
            prewarm,
            "_remove_pid_file",
            lambda: removed.append(True),
        )

        assert process_tracker.is_prewarm_running() is False
        assert removed == [True], "stale PID file was not cleaned up"

    def test_is_prewarm_running_pid_recycled_and_actually_prewarm(self, monkeypatch, tmp_path):
        """A live PID that looks like prewarm keeps the file intact."""
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text(str(os.getpid()))
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_process_is_prewarm", lambda pid: True)

        assert process_tracker.is_prewarm_running() is True
        assert pid_file.exists(), "live prewarm PID file must not be deleted"

    def test_is_prewarm_running_invalid_pid_in_file(self, monkeypatch, tmp_path):
        """Negative / zero PIDs are treated as "not running"."""
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("0")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # _process_alive(0) returns False on every platform.
        assert process_tracker.is_prewarm_running() is False


class TestProcessTrackerWriteRemovePidFile:
    """``_write_pid_file`` / ``_remove_pid_file`` round-trip."""

    def test_write_pid_file_creates_file_with_current_pid(self, monkeypatch, tmp_path):
        pid_file = tmp_path / "nested" / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        process_tracker._write_pid_file()
        assert pid_file.exists()
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_write_pid_file_swallows_os_error(self, monkeypatch, tmp_path):
        """A failing ``_secure_atomic_write`` must not raise."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)

        from voice_typer.server import config

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(config, "_secure_atomic_write", _boom)
        # Must not raise.
        process_tracker._write_pid_file()

    def test_remove_pid_file_idempotent(self, monkeypatch, tmp_path):
        """``_remove_pid_file`` is a no-op when the file is already gone."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # File doesn't exist — must not raise.
        process_tracker._remove_pid_file()
        assert not pid_file.exists()

    def test_remove_pid_file_after_write(self, monkeypatch, tmp_path):
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        process_tracker._write_pid_file()
        assert pid_file.exists()
        process_tracker._remove_pid_file()
        assert not pid_file.exists()


class TestReadPrewarmPid:
    """``_read_prewarm_pid`` parses the PID file robustly."""

    def test_returns_none_when_file_missing(self, monkeypatch, tmp_path):
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert process_tracker._read_prewarm_pid() is None

    def test_returns_pid_when_valid(self, monkeypatch, tmp_path):
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("  4242  \n")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert process_tracker._read_prewarm_pid() == 4242

    def test_returns_none_on_garbage(self, monkeypatch, tmp_path):
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("garbage")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert process_tracker._read_prewarm_pid() is None


class TestProcessAlive:
    """``_process_alive`` is cross-platform; on Linux uses ``os.kill``."""

    def test_alive_for_self(self):
        assert process_tracker._process_alive(os.getpid()) is True

    def test_dead_for_invalid_pid_zero(self):
        assert process_tracker._process_alive(0) is False

    def test_dead_for_invalid_pid_negative(self):
        assert process_tracker._process_alive(-1) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only ESRCH check")
    def test_dead_for_unused_high_pid(self):
        # 2**31 - 1 is effectively never allocated by the Linux kernel.
        assert process_tracker._process_alive(2147483647) is False


class TestProcessIsPrewarm:
    """``_process_is_prewarm`` is the PID-recycling guard."""

    def test_returns_false_for_invalid_pid(self):
        assert process_tracker._process_is_prewarm(0) is False
        assert process_tracker._process_is_prewarm(-1) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only /proc test")
    def test_returns_false_for_self_on_linux(self):
        """pytest's own PID is alive but does not look like prewarm.

        The cmdline for the current process does not contain both
        "prewarm" and "voice_typer" in the required pattern.
        """
        # This test is best-effort: if pytest happens to be invoked from
        # a path that contains "voice_typer" AND "prewarm" the assertion
        # could be flaky. We mitigate by checking the cmdline directly.
        try:
            cmdline = (
                __import__("pathlib")
                .Path(f"/proc/{os.getpid()}/cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode("utf-8", "ignore")
            )
        except OSError:
            pytest.skip("/proc unavailable")
        result = process_tracker._process_is_prewarm(os.getpid())
        # Cross-check: result must match the literal substring test.
        expected = "prewarm" in cmdline and "voice_typer" in cmdline
        assert result is expected


class TestWaitForPrewarm:
    """``wait_for_prewarm`` blocks until prewarm finishes or timeout."""

    def test_returns_true_when_not_running(self, monkeypatch, tmp_path):
        """No PID file → ``wait_for_prewarm`` returns True immediately."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        t0 = time.perf_counter()
        result = process_tracker.wait_for_prewarm(timeout_s=60.0)
        elapsed = time.perf_counter() - t0
        assert result is True
        assert elapsed < 0.5, "wait_for_prewarm must return instantly when not running"

    def test_event_wait_success_skips_polling(self, monkeypatch, tmp_path):
        """If ``_wait_for_completion_event`` returns True, we're done."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: True)
        monkeypatch.setattr(prewarm, "_wait_for_completion_event", lambda timeout: True)

        result = process_tracker.wait_for_prewarm(timeout_s=60.0)
        assert result is True

    def test_poll_fallback_after_event_wait_timeout(self, monkeypatch, tmp_path):
        """Event wait returns False → poll loop takes over and finishes."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)

        # State: first poll says still running, second poll says done.
        # The poll loop sleeps 1s between checks; to keep the test fast
        # we replace time.sleep with a no-op AND make is_prewarm_running
        # flip to False after the first call from the poll loop.
        #
        # Note: wait_for_prewarm calls is_prewarm_running once at the top
        # (the guard) and then again from inside the poll loop. The
        # guard call returns True so we reach the poll loop. The first
        # poll-loop call returns True (still running), the second
        # returns False (finished).
        call_count = {"n": 0}

        def fake_running():
            call_count["n"] += 1
            # Guard call (#1) → True. Poll-loop call #1 (after first
            # sleep) → True. Poll-loop call #2 (after second sleep) →
            # False, which exits the loop with True.
            return call_count["n"] < 3

        monkeypatch.setattr(prewarm, "is_prewarm_running", fake_running)
        monkeypatch.setattr(prewarm, "_wait_for_completion_event", lambda timeout: False)
        # Patch sleep so the test is fast.
        monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)

        result = process_tracker.wait_for_prewarm(timeout_s=60.0)
        assert result is True
        assert call_count["n"] >= 3

    def test_poll_loop_times_out(self, monkeypatch, tmp_path):
        """If prewarm never finishes, ``wait_for_prewarm`` returns False."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: True)
        monkeypatch.setattr(prewarm, "_wait_for_completion_event", lambda timeout: False)
        # Patch sleep + perf_counter so the timeout fires immediately.
        # We simulate "deadline already passed" by making perf_counter
        # jump forward on every call after the first.
        fake_time = {"t": 0.0}

        def fake_perf_counter():
            fake_time["t"] += 100.0  # 100s per call → always past deadline
            return fake_time["t"]

        monkeypatch.setattr(time, "perf_counter", fake_perf_counter)
        monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)

        result = process_tracker.wait_for_prewarm(timeout_s=60.0)
        assert result is False


class TestSpawnBackgroundPrewarm:
    """``spawn_background_prewarm`` launches a detached prewarm subprocess."""

    def test_returns_pid_on_success(self, monkeypatch):
        """A successful spawn returns the subprocess PID (int)."""
        from unittest.mock import MagicMock

        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            fake_popen,
        )

        pid = process_tracker.spawn_background_prewarm(force=True, trigger="manual")
        assert pid == 99999
        assert fake_popen.called

    def test_returns_none_on_file_not_found(self, monkeypatch):
        """If the Python executable is missing, return None (no raise)."""
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)

        def _boom(*args, **kwargs):
            raise FileNotFoundError("no python")

        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            _boom,
        )
        pid = process_tracker.spawn_background_prewarm(force=True)
        assert pid is None

    def test_returns_none_on_oserror(self, monkeypatch):
        """OSError from Popen must also degrade to None."""
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)

        def _boom(*args, **kwargs):
            raise OSError("nope")

        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            _boom,
        )
        pid = process_tracker.spawn_background_prewarm(force=True)
        assert pid is None

    def test_force_false_omits_force_flag(self, monkeypatch):
        """``force=False`` must NOT add ``--force`` to the command line."""
        from unittest.mock import MagicMock

        captured: dict = {}

        fake_proc = MagicMock()
        fake_proc.pid = 1234

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return fake_proc

        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            fake_popen,
        )

        process_tracker.spawn_background_prewarm(force=False, trigger="app-start")
        assert "--force" not in captured["cmd"]
        # The trigger must always be passed.
        assert "--trigger" in captured["cmd"]
        assert "app-start" in captured["cmd"]


# spawn_background_prewarm must check is_prewarm_running ──────────


class TestSpawnSkipsWhenPrewarmRunning:
    """YJ-52 (review-fix-C2-rework): ``spawn_background_prewarm`` must
    call ``_pkg.is_prewarm_running()`` at the top and short-circuit
    (returning the existing PID) if a prewarm subprocess is already
    running.

    The original ``spawn_background_prewarm`` always spawned a new
    subprocess — when ``wait_for_prewarm`` timed out at 60s (the first
    prewarm was still running), the caller would spawn a SECOND
    prewarm, racing with the existing one for disk I/O and
    double-writing the PID file. The YJ-52 fix adds an
    ``is_prewarm_running()`` guard at the top.

    These tests pin the YJ-52 fix so a future revert (removing the
    ``is_prewarm_running()`` check) fails loudly. Mutation sanity
    check: temporarily comment out the ``is_prewarm_running()`` check
    in ``spawn_background_prewarm`` and these tests FAIL.
    """

    def test_returns_existing_pid_and_skips_spawn_when_running(self, monkeypatch, caplog):
        """When ``is_prewarm_running()`` returns True, ``spawn_background_prewarm``
        returns the existing PID and does NOT call ``subprocess.Popen``.

        YJ-52 assertions:
          1. Return value matches ``_read_prewarm_pid()`` (12345).
          2. ``subprocess.Popen`` is NOT called (no second spawn).
          3. An INFO log mentioning "spawn skipped" is emitted.
        """
        import logging
        from unittest.mock import MagicMock

        # Mock is_prewarm_running to return True (prewarm already running).
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: True)
        # Mock _read_prewarm_pid to return the existing PID.
        monkeypatch.setattr(prewarm, "_read_prewarm_pid", lambda: 12345)
        # Mock is_windows to a stable value (the spawn path checks it).
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)
        # Mock subprocess.Popen so we can detect if it's called.
        fake_popen = MagicMock(
            side_effect=AssertionError(
                "YJ-52: subprocess.Popen must NOT be called when "
                "is_prewarm_running() returns True (the existing "
                "prewarm should be reused, not double-spawned)."
            )
        )
        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            fake_popen,
        )

        with caplog.at_level(logging.INFO, logger="voice_typer.server.prewarm"):
            result = process_tracker.spawn_background_prewarm(force=True, trigger="manual")

        # (1) Return value is the existing PID, not a new spawn PID.
        assert result == 12345, (
            f"YJ-52: spawn_background_prewarm should return the existing "
            f"PID (12345) when is_prewarm_running() is True; got {result!r}."
        )
        # (2) subprocess.Popen was NOT called.
        assert not fake_popen.called, (
            "YJ-52: subprocess.Popen was called even though "
            "is_prewarm_running() returned True — the existing prewarm "
            "should have been reused."
        )
        # (3) An INFO log mentioning "spawn skipped" was emitted.
        info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        skipped_logs = [m for m in info_messages if "spawn skipped" in m]
        assert skipped_logs, (
            f"YJ-52: expected an INFO log mentioning 'spawn skipped' "
            f"when is_prewarm_running() is True; got INFO logs: "
            f"{info_messages!r}"
        )
        # The log should mention the existing PID so operators can
        # trace which prewarm was reused.
        assert any("12345" in m for m in skipped_logs), (
            f"YJ-52: 'spawn skipped' log should mention the existing PID 12345; got {skipped_logs!r}"
        )

    def test_falls_through_to_spawn_when_is_prewarm_running_false(self, monkeypatch):
        """When ``is_prewarm_running()`` returns False, spawn proceeds
        normally (the YJ-52 guard short-circuits cleanly).

        This test pins the complementary branch of YJ-52: the guard
        must NOT prevent normal spawning when no prewarm is running.
        """
        from unittest.mock import MagicMock

        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)

        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(
            "voice_typer.server.prewarm.process_tracker.subprocess.Popen",
            fake_popen,
        )

        pid = process_tracker.spawn_background_prewarm(force=True, trigger="manual")
        assert pid == 99999, (
            f"YJ-52: when is_prewarm_running() is False, spawn should "
            f"return the new subprocess PID (99999); got {pid!r}."
        )
        assert fake_popen.called, (
            "YJ-52: when is_prewarm_running() is False, "
            "subprocess.Popen MUST be called — the guard short-circuited "
            "incorrectly."
        )


class TestGetPrewarmStatus:
    """``get_prewarm_status`` returns a UI-ready snapshot dict."""

    def test_returns_unknown_when_no_sentinel_no_dirs(self, monkeypatch, tmp_path):
        sentinel = tmp_path / "sentinel"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: tmp_path / ".prewarm.pid")
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        status = process_tracker.get_prewarm_status()
        assert status["cache_label"] == "unknown"
        assert status["last_run"] is None
        assert status["elapsed_s"] is None
        assert status["cache_ratio"] == 0.0
        assert status["cached_bytes"] == 0
        assert status["total_bytes"] == 0
        assert status["prewarm_running"] is False

    def test_reads_sentinel_three_line_format(self, monkeypatch, tmp_path):
        """3-line sentinel: boot_ts / elapsed_s / iso_timestamp."""
        sentinel = tmp_path / "sentinel"
        sentinel.write_text("1700000000\n20.5\n2026-01-02T03:04:05\n")
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: tmp_path / ".prewarm.pid")
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        status = process_tracker.get_prewarm_status()
        assert status["last_run"] == "2026-01-02T03:04:05"
        assert status["elapsed_s"] == 20.5

    def test_label_hot_when_cache_ratio_high(self, monkeypatch, tmp_path):
        """Cache ratio ≥ 0.9 → label "hot"."""
        sentinel = tmp_path / "sentinel"
        sentinel.write_text("1700000000\n20.5\n2026-01-02T03:04:05\n")
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: tmp_path / ".prewarm.pid")

        # Monkey-patch _cache_ratio so the weighted-sum gives ≥ 0.9.
        # We need _active_model_cache_dirs to return a dir with a
        # snapshots/<snapshot>/model.safetensors file.
        model_dir = tmp_path / "models--nvidia--parakeet"
        snapshot_dir = model_dir / "snapshots" / "abc"
        snapshot_dir.mkdir(parents=True)
        weights = snapshot_dir / "model.safetensors"
        weights.write_bytes(b"\x00" * 1024)

        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [model_dir])
        monkeypatch.setattr(prewarm, "_cache_ratio", lambda path: 0.95)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: False)

        status = process_tracker.get_prewarm_status()
        assert status["cache_label"] == "hot"
        assert status["cache_ratio"] == 0.95
        assert status["cached_bytes"] > 0
        assert status["total_bytes"] > 0


# ─── Stale lock file recovery (ADR-0009 Issue 4) ────────────────────────


class TestStaleLockFileRecovery:
    """A PID file from a crashed process must not block ``is_prewarm_running``."""

    def test_stale_pid_file_with_dead_pid_recovers(self, monkeypatch, tmp_path):
        """A PID file pointing at a dead process is treated as "not running".

        This is the silent-recovery path: the prewarm process crashed
        before its ``finally`` block could call ``_remove_pid_file``.
        The next ``is_prewarm_running`` call must detect the dead PID
        and return False (rather than blocking for 60s in
        ``wait_for_prewarm``).
        """
        pid_file = tmp_path / ".prewarm.pid"
        # PID 2147483647 is never allocated on Linux, so _process_alive
        # returns False without needing a patch.
        pid_file.write_text("2147483647")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)

        assert process_tracker.is_prewarm_running() is False

    def test_stale_pid_file_with_recycled_pid_recovers(self, monkeypatch, tmp_path):
        """A PID file pointing at a live, non-prewarm process is cleaned up.

        The previous prewarm process exited, the OS recycled its PID for
        another process, and the stale PID file now points at the wrong
        process. ``is_prewarm_running`` must detect this via
        ``_process_is_prewarm`` and remove the stale file.
        """
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text(str(os.getpid()))
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_process_is_prewarm", lambda pid: False)
        removed: list[bool] = []
        monkeypatch.setattr(
            prewarm,
            "_remove_pid_file",
            lambda: removed.append(True),
        )

        assert process_tracker.is_prewarm_running() is False
        assert removed == [True]


# ─── Concurrency ─────────────────────────────────────────────────────────


class TestProcessTrackerConcurrency:
    """Concurrent calls must not lose updates or crash.

    These tests don't try to prove the ABSENCE of races (which would
    require a stress-test harness). They prove that under moderate
    concurrency the API surface is internally consistent: writes are
    atomic enough that a reader sees either the old or new value, never
    a corrupted one, and the functions don't crash under contention.
    """

    def test_concurrent_is_prewarm_running_no_crash(self, monkeypatch, tmp_path):
        """8 threads calling ``is_prewarm_running`` in a loop must not raise."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # Toggle the PID file existence from another thread to maximise
        # the chance of hitting the existence-check / read race.
        stop = threading.Event()
        errors: list[BaseException] = []

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    if i % 2 == 0:
                        pid_file.write_text(str(os.getpid()))
                    else:
                        pid_file.unlink(missing_ok=True)
                    i += 1
                except OSError:
                    pass

        def reader():
            try:
                for _ in range(50):
                    # _process_is_prewarm returns False because pytest's
                    # cmdline doesn't match the prewarm pattern, so the
                    # stale-PID cleanup branch runs — but only when the
                    # PID file happens to exist when is_prewarm_running
                    # checks. The race is exactly what we want to probe.
                    process_tracker.is_prewarm_running()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        writer_thread = threading.Thread(target=writer)
        writer_thread.start()
        readers = [threading.Thread(target=reader) for _ in range(8)]
        for r in readers:
            r.start()
        for r in readers:
            r.join(timeout=10)
        stop.set()
        writer_thread.join(timeout=2)

        assert not errors, f"concurrent readers raised: {errors!r}"

    def test_concurrent_write_remove_pid_file_no_corruption(self, monkeypatch, tmp_path):
        """Concurrent ``_write_pid_file`` / ``_remove_pid_file`` must not
        leave a partially-written PID file or raise.
        """
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)

        stop = threading.Event()
        errors: list[BaseException] = []

        def writer():
            try:
                while not stop.is_set():
                    process_tracker._write_pid_file()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def remover():
            try:
                while not stop.is_set():
                    process_tracker._remove_pid_file()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=remover)]
        for t in threads:
            t.start()
        # Let them race for a short, deterministic window.
        time.sleep(0.1)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"concurrent write/remove raised: {errors!r}"
        # Final state must be one of {file absent, file with valid PID} —
        # never a truncated/garbage file.
        if pid_file.exists():
            content = pid_file.read_text().strip()
            assert content.isdigit(), f"PID file corrupted: {content!r}"
