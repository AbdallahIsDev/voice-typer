"""Tests for the OS-level cache prewarm pipeline.

These cover the decision logic (guards), the file-warming primitive, and
the CLI entry point — without actually importing torch or reading real
model weights.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

from voice_typer.server import prewarm

# ─── Guards ─────────────────────────────────────────────────────────────


class TestGuards:
    """Config flag and RAM budget guards short-circuit prewarming safely."""

    def test_fast_startup_default_enabled_bypasses_flag(self, monkeypatch, tmp_path):
        """When fast_startup is True (the default), prewarm proceeds past the
        flag check and reaches the RAM guard.

        PW-3: ``_fast_startup_enabled`` now reads ``Config.fast_startup``
        rather than being a stub. We patch it to True to isolate the
        downstream guards from the user's actual config (the same way
        the other tests in this class do).
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        # ADR-0009 Issue 2: the sentinel check now runs BEFORE the RAM
        # check. Mock _already_warmed to return False so the test is
        # machine-state-independent (matches the pattern in
        # test_unknown_ram_does_not_skip).
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        # Force a low-RAM skip so we don't do real work.
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 100)
        result = prewarm.run(min_ram_mb=1024)
        assert result == prewarm.EXIT_LOW_RAM  # flag is bypassed when True

    def test_fast_startup_disabled_returns_exit_disabled(self, monkeypatch):
        """PW-3: when ``_fast_startup_enabled()`` returns False, ``run()``
        short-circuits with :data:`EXIT_DISABLED` — no prewarming attempted.
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: False)
        # Force a high-RAM value so we know the only reason for skipping
        # is the fast_startup flag, not the RAM guard.
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 99999)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        result = prewarm.run()
        assert result == prewarm.EXIT_DISABLED

    def test_fast_startup_enabled_reads_config_value(self, monkeypatch, tmp_path):
        """PW-3: ``_fast_startup_enabled()`` reads ``Config.fast_startup``.

        Patches ``Config.load`` to return a config with ``fast_startup=False``
        and asserts the function returns False. Also verifies the
        fail-safe: when ``Config.load`` raises, the function returns True
        so a broken config never silently disables prewarm.
        """
        # Branch 1: config says False → function returns False.
        fake_config = MagicMock()
        fake_config.fast_startup = False
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            lambda: fake_config,
        )
        assert prewarm._fast_startup_enabled() is False

        # Branch 2: config says True → function returns True.
        fake_config.fast_startup = True
        assert prewarm._fast_startup_enabled() is True

        # Branch 3: Config.load raises → function falls back to True.
        def _raise() -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            _raise,
        )
        assert prewarm._fast_startup_enabled() is True

    def test_low_ram_returns_exit_low_ram(self, monkeypatch):
        """Free RAM below budget → EXIT_LOW_RAM, no prewarming attempted."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        # ADR-0009 Issue 2: sentinel check runs first. Mock it to return
        # False so the test reaches the RAM guard regardless of prior
        # prewarm runs on this machine.
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 512)
        result = prewarm.run(min_ram_mb=4096)
        assert result == prewarm.EXIT_LOW_RAM

    def test_unknown_ram_does_not_skip(self, monkeypatch):
        """If free RAM can't be queried (None), prewarm should NOT bail on
        the RAM guard — it should proceed (and fail later on imports).

        Round 0: also mock ``_already_warmed`` to return False so the test
        is machine-state-independent. Previously, on machines where the
        prewarm scheduled task had already run in the current boot session
        (sentinel file ``~/.voice-typer/.prewarm-sentinel`` exists and
        matches boot time), ``run()`` short-circuited to EXIT_OK=0 before
        reaching the mocked ``_warm_imports`` — causing the test to fail
        with ``assert 0 == 40``. The boot-session dedup is correct
        production behavior; the test just needs to isolate itself.

        ADR-0009 Issue 2: the sentinel check now runs BEFORE the RAM
        check (the reorder this test anticipated). The explicit
        ``_already_warmed`` mock is now even more important — without
        it, the reordered sentinel check would short-circuit on any
        machine where prewarm already ran this boot session.
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # Bypass the boot-session sentinel so the test always reaches
        # _warm_imports regardless of prior prewarm runs on this machine.
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        # ADR-0009 Issue 4: the PID file is written after the guards pass.
        # Mock the write/remove so the test doesn't leak a real PID file.
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: None)
        monkeypatch.setattr(prewarm, "_remove_pid_file", lambda: None)
        # _warm_imports will raise ImportError on the mocked torch.
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )
        result = prewarm.run()
        assert result == prewarm.EXIT_IMPORT_FAILED

    def test_force_overrides_all_guards(self, monkeypatch):
        """--force skips both config and RAM checks."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        # Even with fast_startup=False and 0 free RAM, force proceeds.
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 0)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # ADR-0009 Issue 4: mock the PID file helpers so the test doesn't
        # leak a real PID file.
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: None)
        monkeypatch.setattr(prewarm, "_remove_pid_file", lambda: None)
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )
        result = prewarm.run(force=True)
        assert result == prewarm.EXIT_IMPORT_FAILED


# ─── ADR-0009 Issue 2: reordered checks (sentinel before RAM) ────────────


class TestSentinelBeforeRam:
    """ADR-0009 Issue 2: the sentinel check runs BEFORE the RAM check.

    Previously ``run()`` checked RAM first, which meant that when the
    trigger re-fired (e.g. on Windows session unlock), the log showed
    "free RAM < budget — skipping" instead of the correct "already ran
    this boot session — skipping". The reorder ensures the cheapest,
    most-correct message wins on re-fire.
    """

    def test_sentinel_short_circuits_before_ram_check(self, monkeypatch):
        """If the sentinel says we already warmed, run() returns EXIT_OK
        WITHOUT calling _free_ram_mb().

        This is the core ADR-0009 Issue 2 invariant: the sentinel check
        must run first and must short-circuit before any RAM probe. We
        verify by making _free_ram_mb raise if called — if the sentinel
        wins, the raise never happens.
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: True)

        def _ram_must_not_be_called():
            raise AssertionError(
                "ADR-0009 Issue 2 regression: _free_ram_mb was called "
                "before _already_warmed short-circuited. The sentinel "
                "check must run FIRST."
            )

        monkeypatch.setattr(prewarm, "_free_ram_mb", _ram_must_not_be_called)

        result = prewarm.run(min_ram_mb=4096)
        assert result == prewarm.EXIT_OK

    def test_sentinel_log_message_on_refire(self, monkeypatch, caplog):
        """When the trigger re-fires and the sentinel catches it, the log
        shows 'already ran this boot session' — NOT 'free RAM < budget'.

        ADR-0009 Issue 2: this is the user-facing fix. The user reported
        a confusing 'free RAM 5705 MB < 6144 MB budget — skipping' log
        line 26 minutes after logon. The root cause was LogonTrigger
        re-firing on session unlock; the sentinel caught it but the RAM
        check ran first and produced the misleading message.
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: True)
        # RAM is below budget — if the check ran, we'd see EXIT_LOW_RAM.
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 100)

        with caplog.at_level("INFO", logger="voice_typer.server.prewarm"):
            result = prewarm.run(min_ram_mb=6144)

        assert result == prewarm.EXIT_OK
        # The sentinel message must be in the log…
        sentinel_msgs = [r for r in caplog.records if "already ran this boot session" in r.message]
        assert sentinel_msgs, (
            "ADR-0009 Issue 2 regression: expected 'already ran this boot "
            "session' log message on sentinel short-circuit"
        )
        # …and the RAM-skipping message must NOT be.
        ram_msgs = [r for r in caplog.records if "free RAM" in r.message and "budget" in r.message]
        assert not ram_msgs, (
            "ADR-0009 Issue 2 regression: the misleading 'free RAM < budget' "
            "message was logged on a sentinel short-circuit. The sentinel "
            "check must run FIRST so this message never appears on re-fire."
        )


# ─── ADR-0009 Issue 3: sentinel stores elapsed time ──────────────────────


class TestSentinelElapsed:
    """ADR-0009 Issue 3: the sentinel file stores boot_ts + elapsed_s.

    The get_prewarm_status IPC endpoint reads both lines to populate the
    About page's 'Last run: 20.4s' row without re-probing the cache.
    """

    def test_mark_warmed_writes_two_line_sentinel(self, monkeypatch, tmp_path):
        """_mark_warmed(20.4) writes a 3-line sentinel (review fix H2).

        ADR-0009 Issue 3 + review fix H2: the sentinel now stores THREE
        lines:
          line 1: boot timestamp (dedup key)
          line 2: elapsed seconds (for the About page)
          line 3: wall-clock completion time (ISO 8601) so the UI can
                  show "Last run: 3 hours ago" instead of showing the
                  boot time (which is the same for every prewarm in the
                  same boot session).
        """
        sentinel = tmp_path / ".prewarm-sentinel"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)

        prewarm._mark_warmed(20.4)

        content = sentinel.read_text()
        lines = content.split("\n")
        assert lines[0] == "1720000000"
        assert lines[1] == "20.4"
        # Line 3: ISO 8601 timestamp (non-empty).
        assert len(lines) > 2 and lines[2].strip(), (
            "review fix H2: sentinel must store wall-clock completion time "
            "in line 3 so the UI can show 'Last run: 3 hours ago'"
        )

    def test_already_warmed_reads_only_first_line(self, monkeypatch, tmp_path):
        """_already_warmed() reads only the first line, so the new
        two-line sentinel format is backward-compatible with the old
        single-line parser."""
        sentinel = tmp_path / ".prewarm-sentinel"
        sentinel.write_text("1720000000\n20.4")
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)

        assert prewarm._already_warmed() is True

    def test_already_warmed_reads_old_single_line_sentinel(self, monkeypatch, tmp_path):
        """Old single-line sentinels (written by previous builds) still
        work — _already_warmed() reads only the first line."""
        sentinel = tmp_path / ".prewarm-sentinel"
        sentinel.write_text("1720000000")
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)

        assert prewarm._already_warmed() is True


# ─── ADR-0009 Issue 3: _cache_ratio probe ────────────────────────────────


class TestCacheRatio:
    """ADR-0009 Issue 3: _cache_ratio() estimates the OS cache hit ratio."""

    def test_cache_ratio_empty_file_returns_zero(self, tmp_path):
        """An empty file → 0.0 (nothing to cache)."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert prewarm._cache_ratio(f) == 0.0

    def test_cache_ratio_tiny_file_returns_zero(self, tmp_path):
        """A file smaller than 4K → 0.0 (can't probe a single page)."""
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"\x00" * 100)
        assert prewarm._cache_ratio(f) == 0.0

    def test_cache_ratio_returns_value_between_zero_and_one(self, tmp_path):
        """A normal file → a ratio in [0.0, 1.0]."""
        f = tmp_path / "model.bin"
        f.write_bytes(b"\xab" * (1024 * 1024))  # 1 MB
        ratio = prewarm._cache_ratio(f, samples=5)
        assert 0.0 <= ratio <= 1.0

    def test_cache_ratio_missing_file_returns_zero(self, tmp_path):
        """A non-existent file → 0.0 (stat fails, degrade gracefully)."""
        f = tmp_path / "does-not-exist.bin"
        assert prewarm._cache_ratio(f) == 0.0


# ─── ADR-0009 Issue 3: get_prewarm_status() ──────────────────────────────


class TestGetPrewarmStatus:
    """ADR-0009 Issue 3: get_prewarm_status() returns a UI-ready dict."""

    def test_status_no_sentinel_returns_unknown(self, monkeypatch, tmp_path):
        """No sentinel file + no model dirs → label='unknown'."""
        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "active_dirs_exist", lambda: False)

        status = prewarm.get_prewarm_status()

        assert status["cache_label"] == "unknown"
        assert status["last_run"] is None
        assert status["elapsed_s"] is None
        assert status["cache_ratio"] == 0.0
        assert status["prewarm_running"] is False

    def test_status_with_sentinel_returns_last_run(self, monkeypatch, tmp_path):
        """3-line sentinel → last_run uses line 3 (wall-clock completion time),
        elapsed_s uses line 2.

        Review fix H2: last_run now shows the actual completion time
        (line 3), not the boot time (line 1). This is critical for the
        UI — without it, every prewarm in the same boot session would
        show the same "last run" time.
        """
        sentinel = tmp_path / ".prewarm-sentinel"
        sentinel.write_text("1720000000\n20.4\n2026-07-08T13:48:49")
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "active_dirs_exist", lambda: False)

        status = prewarm.get_prewarm_status()

        # last_run is line 3 (wall-clock completion time), NOT line 1 (boot time).
        assert status["last_run"] == "2026-07-08T13:48:49"
        assert status["elapsed_s"] == 20.4
        # No model dirs → ratio 0.0, label 'cold' (sentinel exists so not 'unknown')
        assert status["cache_ratio"] == 0.0
        assert status["cache_label"] == "cold"

    def test_status_old_single_line_sentinel_still_works(self, monkeypatch, tmp_path):
        """Old single-line sentinel (no elapsed_s) → elapsed_s is None,
        last_run approximated as boot_ts (no elapsed to add)."""
        sentinel = tmp_path / ".prewarm-sentinel"
        sentinel.write_text("1720000000")
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "active_dirs_exist", lambda: False)

        status = prewarm.get_prewarm_status()

        # last_run approximated as boot_ts (backward-compat with 1-line sentinel).
        assert status["last_run"] is not None
        assert status["elapsed_s"] is None  # old format, no elapsed

    def test_status_cached_bytes_weighted_by_file_size(self, monkeypatch, tmp_path):
        """Review fix H3: cached_bytes is a weighted sum, not total * average_ratio.

        Without the fix, a 2.4 GB file at 80% + a 1 MB file at 100%
        would report cached_bytes = 2.401 GB * 0.90 = 2.16 GB (wrong).
        With the fix, cached_bytes = 2.4 GB * 0.80 + 1 MB * 1.0 = 1.92 GB.
        """
        # Build a fake HF cache with two model dirs of different sizes.
        cache = tmp_path / "huggingface" / "hub"
        # Model A: 10 MB, 100% cached (all pages hot).
        snap_a = cache / "models--a--model" / "snapshots" / "abc"
        snap_a.mkdir(parents=True)
        weights_a = snap_a / "model.safetensors"
        weights_a.write_bytes(b"\x00" * (10 * 1024 * 1024))  # 10 MB
        # Model B: 1 MB, 0% cached (all pages cold). We force _cache_ratio
        # to return 0.0 for B and 1.0 for A via mocking.
        snap_b = cache / "models--b--model" / "snapshots" / "def"
        snap_b.mkdir(parents=True)
        weights_b = snap_b / "model.safetensors"
        weights_b.write_bytes(b"\x00" * (1 * 1024 * 1024))  # 1 MB

        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)

        # Mock _active_model_cache_dirs to return our two fake model dirs.
        fake_dirs = [cache / "models--a--model", cache / "models--b--model"]
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: fake_dirs)

        # Mock _cache_ratio to return 1.0 for A and 0.0 for B.
        def fake_cache_ratio(path, samples=20):
            if path == weights_a:
                return 1.0
            if path == weights_b:
                return 0.0
            return 0.5

        monkeypatch.setattr(prewarm, "_cache_ratio", fake_cache_ratio)

        status = prewarm.get_prewarm_status()

        # total_bytes = 10 MB + 1 MB = 11 MB
        assert status["total_bytes"] == 11 * 1024 * 1024
        # cached_bytes = 10 MB * 1.0 + 1 MB * 0.0 = 10 MB (NOT 11 MB * 0.5 = 5.5 MB)
        assert status["cached_bytes"] == 10 * 1024 * 1024, (
            f"H3: cached_bytes should be 10 MB (weighted sum), got {status['cached_bytes']} bytes"
        )
        # cache_ratio = 10 MB / 11 MB ≈ 0.91
        assert status["cache_ratio"] == round(10 / 11, 2)


# ─── ADR-0009 Issue 4: PID file + is_prewarm_running ─────────────────────


class TestPidFileHandshake:
    """ADR-0009 Issue 4: PID file + is_prewarm_running() handshake."""

    def test_is_prewarm_running_no_pid_file(self, monkeypatch, tmp_path):
        """No PID file → False."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert prewarm.is_prewarm_running() is False

    def test_is_prewarm_running_pid_file_dead_pid(self, monkeypatch, tmp_path):
        """PID file pointing at a dead process → False.

        We use PID 1 (init) on Linux — but init is always alive, so we
        use a clearly-dead PID (2^31-1, which no real process has).
        """
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("2147483647")  # impossibly high PID
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert prewarm.is_prewarm_running() is False

    def test_is_prewarm_running_pid_file_self(self, monkeypatch, tmp_path):
        """PID file pointing at the current process → True (when process
        identity check passes).

        ADR-0009 Issue 4 (review fix H4): is_prewarm_running() now also
        calls _process_is_prewarm() to guard against PID recycling. We
        mock it to return True here because we're testing the PID file
        handshake, not the process-identity check (the pytest process's
        cmdline doesn't contain 'voice_typer' literally).
        """
        import os

        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text(str(os.getpid()))
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # Mock the process-identity check so the test focuses on the PID
        # file handshake logic, not on whether pytest's cmdline matches
        # the prewarm signature.
        monkeypatch.setattr(prewarm, "_process_is_prewarm", lambda pid: True)
        assert prewarm.is_prewarm_running() is True

    def test_is_prewarm_running_corrupt_pid_file(self, monkeypatch, tmp_path):
        """PID file with non-integer content → False (degrade gracefully)."""
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("not-a-number")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert prewarm.is_prewarm_running() is False

    def test_is_prewarm_running_pid_recycled(self, monkeypatch, tmp_path):
        """ADR-0009 Issue 4 (review fix H4): PID file pointing at a live
        but non-prewarm process → False + stale PID file removed.

        After prewarm is killed (SIGKILL, finally doesn't run), the OS
        may recycle the PID for an unrelated process. Without the
        _process_is_prewarm() check, is_prewarm_running() returns True
        for the unrelated process, and wait_for_prewarm() blocks the
        model load for the full 60s timeout on every app launch.
        """
        import os

        pid_file = tmp_path / ".prewarm.pid"
        # Point at the current process (alive, but not prewarm).
        pid_file.write_text(str(os.getpid()))
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # _process_is_prewarm returns False (pytest is not prewarm).
        # Don't mock it — we want to verify the real check works.
        # _remove_pid_file is called to clean up the stale PID file.
        removed = []
        monkeypatch.setattr(
            prewarm,
            "_remove_pid_file",
            lambda: removed.append(True),
        )
        assert prewarm.is_prewarm_running() is False
        assert removed, "stale PID file was not removed after PID recycling detected"

    def test_wait_for_prewarm_no_pid_file_returns_immediately(self, monkeypatch, tmp_path):
        """If no PID file exists, wait_for_prewarm returns True instantly."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        # Use a tiny timeout so the test fails fast if the implementation
        # ignores the "not running" short-circuit.
        import time

        t0 = time.perf_counter()
        result = prewarm.wait_for_prewarm(timeout_s=60.0)
        elapsed = time.perf_counter() - t0
        assert result is True
        assert elapsed < 0.1, "wait_for_prewarm should return instantly when no PID file exists"

    def test_wait_for_prewarm_dead_pid_returns_immediately(self, monkeypatch, tmp_path):
        """If the PID file points at a dead process, wait_for_prewarm
        returns True instantly (the dead PID means prewarm already finished)."""
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("2147483647")  # dead PID
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        import time

        t0 = time.perf_counter()
        result = prewarm.wait_for_prewarm(timeout_s=60.0)
        elapsed = time.perf_counter() - t0
        assert result is True
        assert elapsed < 0.1

    def test_wait_for_prewarm_running_prefers_event_wait(self, monkeypatch, tmp_path):
        """REGRESSION (CPU-04): when prewarm is genuinely running,
        wait_for_prewarm must call _wait_for_completion_event rather than
        crashing with NameError. The previous implementation referenced a
        never-defined _wait_for_completion_event().

        The event wait stub returns True (completion signaled), so the
        function should return True WITHOUT hitting the 1s poll loop.
        """
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("12345")  # alive-looking PID
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "is_prewarm_running", lambda: True)

        called = {}

        def fake_event_wait(timeout_s):
            called["event_wait"] = True
            return True

        monkeypatch.setattr(prewarm, "_wait_for_completion_event", fake_event_wait)

        import time

        t0 = time.perf_counter()
        result = prewarm.wait_for_prewarm(timeout_s=60.0)
        elapsed = time.perf_counter() - t0

        assert result is True
        assert called.get("event_wait") is True, "wait_for_prewarm did not call _wait_for_completion_event"
        assert elapsed < 0.5, "event-based wait path was not taken"

    def test_wait_for_prewarm_event_wait_false_falls_back_to_poll(self, monkeypatch, tmp_path):
        """REGRESSION (CPU-04): if the event-based wait returns False (no
        platform support, or it timed out), wait_for_prewarm must fall back
        to the 1s poll loop and still respect the timeout budget.

        Here the event wait reports False, then the poll loop finds prewarm
        already gone on the first iteration and returns True promptly.
        """
        pid_file = tmp_path / ".prewarm.pid"
        pid_file.write_text("12345")
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)

        def fake_event_wait(timeout_s):
            return False  # simulate "no event support"

        monkeypatch.setattr(prewarm, "_wait_for_completion_event", fake_event_wait)

        # Prewarm "finishes" on the first poll iteration: is_prewarm_running
        # flips to False.
        states = {"n": 0}

        def fake_running():
            states["n"] += 1
            return states["n"] < 2

        monkeypatch.setattr(prewarm, "is_prewarm_running", fake_running)

        import time

        t0 = time.perf_counter()
        result = prewarm.wait_for_prewarm(timeout_s=60.0)
        elapsed = time.perf_counter() - t0

        assert result is True
        assert elapsed < 2.0, "poll fallback did not detect completion promptly"

    def test_wait_for_completion_event_no_pid_file_returns_false(self, monkeypatch, tmp_path):
        """_wait_for_completion_event returns False when there is no PID file,
        so wait_for_prewarm degrades gracefully to the poll loop instead of
        raising."""
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        assert prewarm._wait_for_completion_event(timeout_s=1.0) is False


# ─── Task 5: spawn_background_prewarm ────────────────────────────────────


class TestSpawnBackgroundPrewarm:
    """Task 5: spawn_background_prewarm() launches a detached prewarm subprocess."""

    def test_spawn_returns_pid(self, monkeypatch):
        """spawn_background_prewarm() returns a PID (int) on success."""
        import subprocess

        # Mock subprocess.Popen to return a fake process.
        class FakeProc:
            pid = 12345

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: FakeProc())
        # Avoid actually spawning — we just verify the return value.
        pid = prewarm.spawn_background_prewarm(force=True)
        assert pid == 12345

    def test_spawn_includes_force_flag(self, monkeypatch):
        """When force=True, the command includes '--force'."""
        import subprocess

        captured_cmd = []

        class FakeProc:
            pid = 99

        def fake_popen(cmd, **kw):
            captured_cmd.extend(cmd)
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        prewarm.spawn_background_prewarm(force=True)
        assert "--force" in captured_cmd, "force=True must pass --force to bypass the boot-sentinel dedup"

    def test_spawn_omits_force_flag_when_false(self, monkeypatch):
        """When force=False, the command does NOT include '--force'."""
        import subprocess

        captured_cmd = []

        class FakeProc:
            pid = 99

        def fake_popen(cmd, **kw):
            captured_cmd.extend(cmd)
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        prewarm.spawn_background_prewarm(force=False)
        assert "--force" not in captured_cmd

    def test_spawn_returns_none_on_file_not_found(self, monkeypatch):
        """If the Python interpreter can't be spawned, returns None."""
        import subprocess

        def raise_fnf(*a, **kw):
            raise FileNotFoundError("python not found")

        monkeypatch.setattr(subprocess, "Popen", raise_fnf)
        pid = prewarm.spawn_background_prewarm(force=True)
        assert pid is None

    def test_spawn_returns_none_on_oserror(self, monkeypatch):
        """If the OS refuses to spawn (e.g. too many processes), returns None."""
        import subprocess

        def raise_oserr(*a, **kw):
            raise OSError("resource exhausted")

        monkeypatch.setattr(subprocess, "Popen", raise_oserr)
        pid = prewarm.spawn_background_prewarm(force=True)
        assert pid is None

    def test_spawn_uses_detached_process_group(self, monkeypatch):
        """On POSIX, spawn uses start_new_session=True (detached).

        Task 5 requirement: the background prewarm must run in a separate
        process group so it survives the app's exit and isn't killed when
        the app's process group receives a signal.
        """
        import subprocess

        captured_kwargs = {}

        class FakeProc:
            pid = 1

        def fake_popen(cmd, **kw):
            captured_kwargs.update(kw)
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        # Force the POSIX path (we're on Linux in CI).
        monkeypatch.setattr(prewarm, "is_windows", lambda: False)

        prewarm.spawn_background_prewarm(force=True)
        # On POSIX, start_new_session must be True for detachment.
        if not prewarm.is_windows():
            assert captured_kwargs.get("start_new_session") is True, (
                "Task 5: background prewarm must use start_new_session=True on POSIX so it survives the app's exit"
            )


# ─── Task 1: Windows PEB walk struct regression tests ───────────────────


class TestWindowsPebWalkStructs:
    """Task 1: regression tests for the Windows PEB walk struct definitions.

    The previous code used ``wintypes.ULONG_PTR`` which doesn't exist in
    Python's ctypes.wintypes module, causing an AttributeError on Windows.
    These tests verify the struct definitions are valid (even when running
    on Linux, we can import the function and check it doesn't reference
    non-existent types).
    """

    def test_read_process_cmdline_windows_function_exists(self):
        """The Windows PEB walk function exists and is importable.

        This catches import-time errors (e.g. referencing wintypes.ULONG_PTR
        at module level). The actual Windows API calls are only exercised
        on Windows, but the function definition must be valid on all
        platforms so the module imports cleanly.
        """
        assert hasattr(prewarm, "_read_process_cmdline_windows")
        assert callable(prewarm._read_process_cmdline_windows)

    def test_process_is_prewarm_function_exists(self):
        """_process_is_prewarm exists and is callable on all platforms."""
        assert hasattr(prewarm, "_process_is_prewarm")
        assert callable(prewarm._process_is_prewarm)

    def test_process_is_prewarm_returns_false_for_dead_pid(self):
        """A dead PID returns False on all platforms (no crash).

        This verifies the function handles the 'process not found' case
        without raising — critical for the PID recycling guard.
        """
        # PID 2147483647 is impossibly high — no real process has it.
        assert prewarm._process_is_prewarm(2147483647) is False

    def test_process_is_prewarm_returns_false_for_invalid_pid(self):
        """Invalid PIDs (<=0) return False without raising."""
        assert prewarm._process_is_prewarm(0) is False
        assert prewarm._process_is_prewarm(-1) is False

    def test_process_is_prewarm_returns_false_for_self(self):
        """The current process (pytest) is NOT prewarm — returns False.

        Task 1 requirement: 'verify that _process_is_prewarm(os.getpid())
        returns False when called from a non-prewarm Python process (like
        pytest) on both platforms.'

        On Linux: pytest's cmdline contains 'prewarm' (the test file path)
        but NOT 'voice_typer', so the check returns False.
        On Windows: the PEB walk reads the actual cmdline, which also
        contains 'prewarm' but not 'voice_typer', so it returns False.
        """
        import os

        result = prewarm._process_is_prewarm(os.getpid())
        assert result is False, (
            "Task 1: _process_is_prewarm(os.getpid()) must return False "
            "for a non-prewarm process (pytest). This is the core PID "
            "recycling guard — if it returns True for pytest, the guard "
            "is broken."
        )


# ─── ADR-0009 Issue 4: run() writes + removes PID file ──────────────────


class TestRunPidFileLifecycle:
    """ADR-0009 Issue 4: run() writes the PID file during warming and
    removes it in a finally block, even on early exit."""

    def test_run_writes_pid_file_during_warming(self, monkeypatch, tmp_path):
        """When run() reaches the warming phase, the PID file is written."""
        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # Track PID file writes during the warming phase.
        pid_writes = []
        original_write = prewarm._write_pid_file

        def tracking_write():
            pid_writes.append(True)
            original_write()

        monkeypatch.setattr(prewarm, "_write_pid_file", tracking_write)
        monkeypatch.setattr(prewarm, "_remove_pid_file", lambda: None)
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )

        result = prewarm.run()
        assert result == prewarm.EXIT_IMPORT_FAILED
        assert pid_writes, "PID file was not written during the warming phase"

    def test_run_removes_pid_file_on_import_failure(self, monkeypatch, tmp_path):
        """If the import stage fails, the PID file is still removed
        (finally block)."""
        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        remove_calls = []
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: None)
        monkeypatch.setattr(
            prewarm,
            "_remove_pid_file",
            lambda: remove_calls.append(True),
        )
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )

        result = prewarm.run()
        assert result == prewarm.EXIT_IMPORT_FAILED
        assert remove_calls, "PID file was not removed in the finally block after import failure"

    def test_run_does_not_write_pid_file_on_sentinel_short_circuit(self, monkeypatch, tmp_path):
        """If the sentinel short-circuits, the PID file is NOT written
        (the process bailed out before doing any warming work)."""
        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: True)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 100)

        pid_writes = []
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: pid_writes.append(True))

        result = prewarm.run(min_ram_mb=4096)
        assert result == prewarm.EXIT_OK
        assert not pid_writes, (
            "PID file was written on a sentinel short-circuit — it should "
            "only be written when the warming phase actually starts"
        )


# ─── ADR-0009 Issue 1: _resolve_hf_cache_dir ─────────────────────────────


class TestResolveHfCacheDir:
    """ADR-0009 Issue 1: _resolve_hf_cache_dir() finds the HF cache
    robustly, even when fired by BootTrigger before the session is
    fully initialized."""

    def test_uses_config_dir_when_cache_exists(self, monkeypatch, tmp_path):
        """When _config_dir() returns a path with an existing huggingface/
        subdir, that path is returned (primary resolution path)."""
        # Build a fake config dir with an existing huggingface/ subdir.
        fake_config = tmp_path / "config"
        (fake_config / "huggingface").mkdir(parents=True)
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: fake_config,
        )
        result = prewarm._resolve_hf_cache_dir()
        assert result == fake_config / "huggingface"

    def test_returns_config_dir_path_even_when_cache_missing(self, monkeypatch, tmp_path):
        """When _config_dir() returns a path with NO huggingface/ subdir,
        the path is still returned (best-effort; caller checks .exists())."""
        fake_config = tmp_path / "config"
        fake_config.mkdir(parents=True)
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: fake_config,
        )
        result = prewarm._resolve_hf_cache_dir()
        assert result == fake_config / "huggingface"


# ─── File warming ────────────────────────────────────────────────────────


class TestWarmFile:
    """_warm_file reads every byte of a file sequentially."""

    def test_warm_file_reads_all_bytes(self, tmp_path):
        """_warm_file returns the exact file size and reads all content."""
        payload = b"\x00\x01\x02" * 1000  # 3000 bytes
        f = tmp_path / "weights.bin"
        f.write_bytes(payload)

        read = prewarm._warm_file(f)
        assert read == len(payload)

    def test_warm_file_empty_file(self, tmp_path):
        """An empty file is a no-op returning 0 bytes read."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert prewarm._warm_file(f) == 0

    def test_warm_file_large_file_uses_small_buffer(self, tmp_path, monkeypatch):
        """The 4 MB read buffer is used (verify chunked read by spying on
        the number of read() calls)."""
        # Write 10 MB of data.
        f = tmp_path / "big.bin"
        f.write_bytes(b"\xab" * (10 * 1024 * 1024))

        # Spy on read calls to confirm chunking.
        original_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open
        call_sizes = []

        class _SpyReader:
            def __init__(self, real):
                self._real = real

            def read(self, n=-1):
                call_sizes.append(n)
                return self._real.read(n)

            def __enter__(self):
                self._real.__enter__()
                return self

            def __exit__(self, *a):
                self._real.__exit__(*a)

        def spy_open(path, mode="r", *a, **kw):
            real = original_open(path, mode, *a, **kw)
            if "b" in mode and "r" in mode:
                return _SpyReader(real)
            return real

        monkeypatch.setattr("builtins.open", spy_open)
        prewarm._warm_file(f)

        # All reads except possibly the last should be the chunk size.
        assert prewarm._READ_CHUNK_BYTES in call_sizes
        # 10 MB / 4 MB = 3 full chunks → at least 3 reads at chunk size.
        assert call_sizes.count(prewarm._READ_CHUNK_BYTES) >= 2


# ─── Weights discovery ──────────────────────────────────────────────────


class TestFindWeights:
    """_find_parakeet_weights locates the cached safetensors or returns None."""

    def test_returns_none_when_cache_absent(self, monkeypatch):
        """No cache directory → None."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: Path("C:/nonexistent/path/that/does/not/exist"),
        )
        assert prewarm._find_parakeet_weights() is None

    def test_returns_path_when_cached(self, monkeypatch, tmp_path):
        """A snapshot dir with model.safetensors → that path."""
        # Build a fake HF cache layout.
        cache = tmp_path / "huggingface" / "hub"
        model_dir = cache / "models--nvidia--parakeet-tdt-0.6b-v3"
        snap = model_dir / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        weights = snap / "model.safetensors"
        weights.write_bytes(b"fake weights")

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        result = prewarm._find_parakeet_weights()
        assert result == weights

    def test_returns_none_when_snapshot_has_no_weights(self, monkeypatch, tmp_path):
        """Snapshot dir exists but model.safetensors is missing → None."""
        cache = tmp_path / "huggingface" / "hub"
        snap = cache / "models--nvidia--parakeet-tdt-0.6b-v3" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")  # no weights

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        assert prewarm._find_parakeet_weights() is None


# ─── CLI ────────────────────────────────────────────────────────────────


class TestCli:
    """The argparse entry point forwards to run()."""

    def test_parse_args_defaults(self):
        args = prewarm._parse_args([])
        assert args.force is False
        assert args.min_ram_mb == prewarm.DEFAULT_MIN_FREE_RAM_MB

    def test_parse_args_force(self):
        args = prewarm._parse_args(["--force"])
        assert args.force is True

    def test_parse_args_custom_ram(self):
        args = prewarm._parse_args(["--min-ram-mb", "2048"])
        assert args.min_ram_mb == 2048

    def test_main_returns_exit_code(self, monkeypatch):
        """main() returns run()'s exit code (low RAM guard)."""
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: 100)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(sys, "argv", ["prewarm"])
        assert prewarm.main() == prewarm.EXIT_LOW_RAM

    # Task 4: --status CLI flag tests.

    def test_parse_args_status_flag(self):
        """--status sets args.status=True (does NOT run the pipeline)."""
        args = prewarm._parse_args(["--status"])
        assert args.status is True

    def test_parse_args_no_status_by_default(self):
        """Without --status, args.status is False (runs the pipeline)."""
        args = prewarm._parse_args([])
        assert args.status is False

    def test_main_status_short_circuits_before_run(self, monkeypatch, tmp_path, capsys):
        """main() with --status prints JSON and returns 0 WITHOUT calling run().

        Task 4: --status is a pure read-only diagnostic. It must NOT
        invoke run() (which would start the warming pipeline, import
        torch, etc.). We verify by making run() raise if called.
        """
        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "active_dirs_exist", lambda: False)

        def run_must_not_be_called(*a, **kw):
            raise AssertionError(
                "Task 4: --status must NOT call run() — it's a read-only diagnostic that prints cache state and exits."
            )

        monkeypatch.setattr(prewarm, "run", run_must_not_be_called)
        monkeypatch.setattr(sys, "argv", ["prewarm", "--status"])

        exit_code = prewarm.main()
        assert exit_code == 0

        # Verify the output is valid JSON with the expected fields.
        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert "last_run" in data
        assert "elapsed_s" in data
        assert "cache_ratio" in data
        assert "cache_label" in data
        assert "cached_bytes" in data
        assert "total_bytes" in data
        assert "prewarm_running" in data
        assert "sentinel_path" in data  # Task 4: diagnostic field
        assert "pid_file_path" in data  # Task 4: diagnostic field
        assert data["cache_label"] == "unknown"  # no sentinel, no model dirs

    def test_main_status_includes_sentinel_path(self, monkeypatch, tmp_path, capsys):
        """--status output includes the sentinel_path field for diagnostics."""
        sentinel = tmp_path / ".prewarm-sentinel"
        sentinel.write_text("1720000000\n20.4\n2026-07-08T13:48:49")
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [])
        monkeypatch.setattr(prewarm, "active_dirs_exist", lambda: False)
        monkeypatch.setattr(sys, "argv", ["prewarm", "--status"])

        exit_code = prewarm.main()
        assert exit_code == 0

        import json

        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["sentinel_path"] == str(sentinel)
        assert data["pid_file_path"] == str(pid_file)
        assert data["last_run"] == "2026-07-08T13:48:49"
        assert data["elapsed_s"] == 20.4

    # Task 3: --run and --background flag tests.

    def test_parse_args_run_flag(self):
        """--run sets args.run=True."""
        args = prewarm._parse_args(["--run"])
        assert args.run is True

    def test_parse_args_background_flag(self):
        """--background sets args.background=True."""
        args = prewarm._parse_args(["--background"])
        assert args.background is True

    def test_parse_args_run_with_background(self):
        """--run --background sets both flags."""
        args = prewarm._parse_args(["--run", "--background"])
        assert args.run is True
        assert args.background is True

    def test_main_run_without_background_calls_run_with_force(self, monkeypatch):
        """main() with --run (no --background) calls run(force=True) inline.

        Task 3: --run is an alias for --force — both bypass the guards
        and run the warming pipeline inline.
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: None)
        monkeypatch.setattr(prewarm, "_remove_pid_file", lambda: None)
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )
        monkeypatch.setattr(sys, "argv", ["prewarm", "--run"])

        # --run should behave identically to --force.
        assert prewarm.main() == prewarm.EXIT_IMPORT_FAILED

    def test_main_run_background_spawns_subprocess(self, monkeypatch, capsys):
        """main() with --run --background spawns a detached subprocess.

        Task 3: --run --background should call spawn_background_prewarm()
        and print the PID, then exit immediately without running the
        warming pipeline inline.
        """
        spawn_called = []
        monkeypatch.setattr(
            prewarm,
            "spawn_background_prewarm",
            lambda force=True, trigger=None: spawn_called.append(force) or 12345,
        )

        # run() must NOT be called — --background exits immediately.
        def run_must_not_be_called(*a, **kw):
            raise AssertionError(
                "Task 3: --run --background must NOT call run() inline — "
                "it spawns a detached subprocess and exits immediately."
            )

        monkeypatch.setattr(prewarm, "run", run_must_not_be_called)
        monkeypatch.setattr(sys, "argv", ["prewarm", "--run", "--background"])

        exit_code = prewarm.main()
        assert exit_code == prewarm.EXIT_OK
        assert spawn_called == [True], "spawn_background_prewarm must be called with force=True"
        # The PID must be printed so scripts can track it.
        output = capsys.readouterr().out
        assert "12345" in output, "main() with --run --background must print the spawned PID"

    def test_main_background_without_run_is_noop(self, monkeypatch):
        """--background without --run or --force is a no-op (warns + exits).

        Task 3: --background is only meaningful with --run or --force.
        Without one of those, it should warn and return EXIT_DISABLED
        rather than silently doing nothing.
        """

        # run() must NOT be called.
        def run_must_not_be_called(*a, **kw):
            raise AssertionError("Task 3: --background without --run must NOT call run()")

        monkeypatch.setattr(prewarm, "run", run_must_not_be_called)

        # spawn_background_prewarm must NOT be called.
        def spawn_must_not_be_called(*a, **kw):
            raise AssertionError("Task 3: --background without --run must NOT spawn a subprocess")

        monkeypatch.setattr(
            prewarm,
            "spawn_background_prewarm",
            spawn_must_not_be_called,
        )
        monkeypatch.setattr(sys, "argv", ["prewarm", "--background"])

        exit_code = prewarm.main()
        assert exit_code == prewarm.EXIT_DISABLED

    def test_main_force_background_also_spawns(self, monkeypatch):
        """--force --background also spawns a subprocess (force is equivalent to run).

        Task 3: --run and --force are both valid triggers for --background.
        """
        spawn_called = []
        monkeypatch.setattr(
            prewarm,
            "spawn_background_prewarm",
            lambda force=True, trigger=None: spawn_called.append(force) or 99,
        )
        monkeypatch.setattr(sys, "argv", ["prewarm", "--force", "--background"])

        exit_code = prewarm.main()
        assert exit_code == prewarm.EXIT_OK
        assert spawn_called == [True]

    def test_main_force_without_background_still_works(self, monkeypatch):
        """--force without --background still runs inline (backward compat).

        Task 3: --force is the legacy flag; it must still work exactly
        as before (run inline, no subprocess).
        """
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)
        monkeypatch.setattr(prewarm, "_free_ram_mb", lambda: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(prewarm, "_write_pid_file", lambda: None)
        monkeypatch.setattr(prewarm, "_remove_pid_file", lambda: None)
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            MagicMock(side_effect=ImportError("no torch")),
        )
        monkeypatch.setattr(sys, "argv", ["prewarm", "--force"])

        assert prewarm.main() == prewarm.EXIT_IMPORT_FAILED


# ─── STARTUP-4: active-model filter ─────────────────────────────────────


class TestPrewarmFiltersToActiveModelAndFallback:
    """STARTUP-4: prewarm should only warm the active model + declared fallback.

    Previously prewarm walked ALL models--* dirs in the HF cache, warming
    ~2.1 GB of inactive Whisper variants when the active backend was parakeet.
    Now it only warms dirs returned by _active_model_cache_dirs().
    """

    def test_parakeet_backend_warms_parakeet_and_tiny_en_fallback(
        self,
        monkeypatch,
        tmp_path,
    ):
        """Active backend = parakeet → warm parakeet + tiny.en fallback only."""
        # Set up fake HF cache with multiple model dirs
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)
        # Parakeet cache dir (active)
        (hf_cache / "models--nvidia--parakeet-tdt-0.6b-v3" / "snapshots" / "abc").mkdir(parents=True)
        # tiny.en cache dir (fallback target)
        (hf_cache / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "def").mkdir(parents=True)
        # Inactive whisper variants — must NOT be warmed
        (hf_cache / "models--Systran--faster-whisper-small.en" / "snapshots" / "ghi").mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-medium.en" / "snapshots" / "jkl").mkdir(parents=True)

        # Mock Config.load() to return parakeet config
        fake_cfg = MagicMock(asr_backend="parakeet", model_size="small.en")
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )

        dirs = prewarm._active_model_cache_dirs()
        dir_names = [d.name for d in dirs]
        # Must include parakeet (active) and tiny.en (fallback)
        assert "models--nvidia--parakeet-tdt-0.6b-v3" in dir_names
        assert "models--Systran--faster-whisper-tiny.en" in dir_names
        # Must NOT include inactive Whisper variants
        assert "models--Systran--faster-whisper-small.en" not in dir_names
        assert "models--Systran--faster-whisper-medium.en" not in dir_names

    def test_whisper_backend_warms_active_size_only(
        self,
        monkeypatch,
        tmp_path,
    ):
        """Active backend = whisper, model_size = small.en → warm small.en + tiny.en fallback."""
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-small.en" / "snapshots" / "abc").mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "def").mkdir(parents=True)
        # Inactive
        (hf_cache / "models--Systran--faster-whisper-medium.en" / "snapshots" / "ghi").mkdir(parents=True)
        (hf_cache / "models--nvidia--parakeet-tdt-0.6b-v3" / "snapshots" / "jkl").mkdir(parents=True)

        fake_cfg = MagicMock(asr_backend="whisper", model_size="small.en")
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )

        dirs = prewarm._active_model_cache_dirs()
        dir_names = [d.name for d in dirs]
        assert "models--Systran--faster-whisper-small.en" in dir_names
        assert "models--Systran--faster-whisper-tiny.en" in dir_names  # fallback
        # Inactive models NOT included
        assert "models--Systran--faster-whisper-medium.en" not in dir_names
        assert "models--nvidia--parakeet-tdt-0.6b-v3" not in dir_names

    def test_whisper_tiny_en_active_no_duplicate(self, monkeypatch, tmp_path):
        """If tiny.en is already the active model, don't add it twice."""
        hf_cache = tmp_path / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "abc").mkdir(parents=True)

        fake_cfg = MagicMock(asr_backend="whisper", model_size="tiny.en")
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )

        dirs = prewarm._active_model_cache_dirs()
        # Only one dir (no duplicate)
        assert len(dirs) == 1
        assert dirs[0].name == "models--Systran--faster-whisper-tiny.en"

    def test_no_cache_returns_empty_list(self, monkeypatch, tmp_path):
        """No HF cache → empty list (nothing to warm)."""
        fake_cfg = MagicMock(asr_backend="whisper", model_size="small.en")
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,  # no huggingface/hub subdir
        )
        dirs = prewarm._active_model_cache_dirs()
        assert dirs == []


# ─── Task 6: Integration test — full run() pipeline ──────────────────────


class TestPrewarmIntegration:
    """Task 6: end-to-end integration test exercising the full prewarm pipeline.

    Unlike the unit tests above (which mock _warm_imports, _free_ram_mb,
    _already_warmed, etc. in isolation), these tests run the real run()
    function end-to-end with ONLY _warm_imports mocked (to avoid importing
    torch, which is 4+ GB of I/O and would take minutes).

    Verifies:
      - run() with force=True writes the sentinel file after success
      - the PID file is created during the run and removed after
      - the exit code is EXIT_OK (0) on success
      - get_prewarm_status() returns correct values after the run
      - a second run() (no force) short-circuits via sentinel dedup

    Uses a temporary HF cache directory with small dummy model files
    (a few KB, not GB) so the test runs in milliseconds, not minutes.
    """

    def _setup_fake_cache(self, tmp_path: Path) -> Path:
        """Create a fake HF cache with a tiny model.safetensors file.

        Returns the tmp_path (to be used as _config_dir via monkeypatch).
        The cache layout matches what _active_model_cache_dirs() expects:
          tmp_path/huggingface/hub/models--<repo>/snapshots/<hash>/model.safetensors
        """
        cache = tmp_path / "huggingface" / "hub"
        # Use a whisper model dir (the default backend) so
        # _active_model_cache_dirs() finds it without needing a
        # parakeet_engine import.
        model_dir = cache / "models--Systran--faster-whisper-tiny.en"
        snap = model_dir / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        # Write a tiny model.safetensors (1 KB — enough to warm, tiny I/O).
        (snap / "model.safetensors").write_bytes(b"\xab" * 1024)
        # Write a config.json so the snapshot looks complete.
        (snap / "config.json").write_text("{}")
        return tmp_path

    def _mock_config(self, monkeypatch, tmp_path: Path):
        """Mock Config.load() and _config_dir to point at tmp_path."""
        fake_cfg = MagicMock(asr_backend="whisper", model_size="tiny.en")
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir",
            lambda: tmp_path,
        )
        # Also mock _sentinel_path and _pid_file_path to use tmp_path
        # so the test doesn't touch the real ~/.voice-typer.
        sentinel = tmp_path / ".prewarm-sentinel"
        pid_file = tmp_path / ".prewarm.pid"
        monkeypatch.setattr(prewarm, "_sentinel_path", lambda: sentinel)
        monkeypatch.setattr(prewarm, "_pid_file_path", lambda: pid_file)
        return sentinel, pid_file

    def test_full_run_writes_sentinel_and_pid_lifecycle(self, monkeypatch, tmp_path):
        """run(force=True) writes the sentinel, manages the PID file, returns EXIT_OK.

        Task 6: this is the core integration test. It runs the REAL
        run() function (not a mock) with only _warm_imports replaced
        by a no-op. Everything else — sentinel, PID file, cache walk,
        _warm_file, _mark_warmed — runs for real.
        """
        self._setup_fake_cache(tmp_path)
        sentinel, pid_file = self._mock_config(monkeypatch, tmp_path)

        # Mock ONLY _warm_imports (avoids importing torch — 4+ GB).
        # Everything else runs for real.
        monkeypatch.setattr(prewarm, "_warm_imports", lambda: None)
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # Force past all guards (sentinel, RAM, fast_startup).
        # Also mock _boot_time so the sentinel has a known value.
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)

        # Track PID file existence DURING the run. We use a side effect
        # on _warm_file to check: while warming is happening, the PID
        # file must exist.
        pid_file_during_run = []
        original_warm_file = prewarm._warm_file

        def tracking_warm_file(path):
            pid_file_during_run.append(pid_file.exists())
            return original_warm_file(path)

        monkeypatch.setattr(prewarm, "_warm_file", tracking_warm_file)

        # Run with force=True (bypass sentinel + RAM guards).
        exit_code = prewarm.run(force=True)

        # ── Verify exit code ────────────────────────────────────────
        assert exit_code == prewarm.EXIT_OK, f"run(force=True) should return EXIT_OK, got {exit_code}"

        # ── Verify sentinel was written (3-line format) ─────────────
        assert sentinel.exists(), "sentinel file must exist after successful run"
        content = sentinel.read_text()
        lines = content.split("\n")
        assert lines[0] == "1720000000", f"line 1 must be boot_ts, got {lines[0]!r}"
        assert len(lines) > 1, "sentinel must have at least 2 lines (boot_ts + elapsed)"
        # Line 2: elapsed seconds (float).
        elapsed = float(lines[1])
        assert elapsed >= 0.0, f"elapsed_s must be non-negative, got {elapsed}"
        # Line 3: wall-clock completion time (ISO 8601) — review fix H2.
        assert len(lines) > 2 and lines[2].strip(), "sentinel must have line 3 (wall-clock completion time)"

        # ── Verify PID file was created DURING the run ──────────────
        assert pid_file_during_run, "_warm_file was never called — the cache walk didn't run"
        assert any(pid_file_during_run), (
            "PID file must exist DURING the warming phase (run() writes it "
            "before warming and removes it in a finally block)"
        )

        # ── Verify PID file was removed AFTER the run ───────────────
        assert not pid_file.exists(), "PID file must be removed after run() completes (finally block)"

    def test_get_prewarm_status_after_run(self, monkeypatch, tmp_path):
        """get_prewarm_status() returns correct values after a successful run.

        Task 6: after run() writes the sentinel, get_prewarm_status()
        must report last_run, elapsed_s, and a cache_label (not 'unknown').
        """
        self._setup_fake_cache(tmp_path)
        sentinel, pid_file = self._mock_config(monkeypatch, tmp_path)

        monkeypatch.setattr(prewarm, "_warm_imports", lambda: None)
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)

        # Run prewarm.
        exit_code = prewarm.run(force=True)
        assert exit_code == prewarm.EXIT_OK

        # Now query status.
        status = prewarm.get_prewarm_status()

        # last_run must be populated (from sentinel line 3).
        assert status["last_run"] is not None, "last_run must be populated after a successful run"
        # elapsed_s must be a non-negative float.
        assert status["elapsed_s"] is not None
        assert status["elapsed_s"] >= 0.0
        # cache_ratio must be in [0.0, 1.0] — the tiny file is fully
        # cached after warming, so it should be 1.0 (hot).
        assert 0.0 <= status["cache_ratio"] <= 1.0
        # cache_label must NOT be 'unknown' (sentinel exists + model
        # dir exists, so we know the state).
        assert status["cache_label"] != "unknown", (
            "cache_label must not be 'unknown' after a successful run "
            f"(sentinel + model dir both exist). Got: {status['cache_label']}"
        )
        # prewarm_running must be False (run() finished, PID file removed).
        assert status["prewarm_running"] is False, "prewarm_running must be False after run() completes"
        # total_bytes must be > 0 (we wrote a 1 KB model file).
        assert status["total_bytes"] > 0, "total_bytes must be > 0 (the fake model.safetensors exists)"

    def test_sentinel_dedup_short_circuits_second_run(self, monkeypatch, tmp_path):
        """A second run() (no force) short-circuits via sentinel dedup.

        Task 6: after the first successful run writes the sentinel, a
        second run() WITHOUT --force must return EXIT_OK immediately
        (sentinel short-circuit) WITHOUT calling _warm_imports or
        _warm_file.
        """
        self._setup_fake_cache(tmp_path)
        sentinel, pid_file = self._mock_config(monkeypatch, tmp_path)

        # First run: force=True, writes the sentinel.
        monkeypatch.setattr(prewarm, "_warm_imports", lambda: None)
        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)
        exit_code_1 = prewarm.run(force=True)
        assert exit_code_1 == prewarm.EXIT_OK
        assert sentinel.exists()

        # Second run: NO force. _warm_imports must NOT be called.
        warm_imports_called = []
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            lambda: warm_imports_called.append(True),
        )
        # _free_ram_mb must NOT be called either (sentinel check is first).
        ram_called = []
        monkeypatch.setattr(
            prewarm,
            "_free_ram_mb",
            lambda: ram_called.append(True) or 99999,
        )

        exit_code_2 = prewarm.run(force=False)

        assert exit_code_2 == prewarm.EXIT_OK, "second run() must return EXIT_OK (sentinel short-circuit)"
        assert not warm_imports_called, "second run() must NOT call _warm_imports (sentinel dedup)"
        assert not ram_called, "second run() must NOT call _free_ram_mb (sentinel check runs FIRST — ADR-0009 Issue 2)"

    def test_pid_file_removed_on_import_failure(self, monkeypatch, tmp_path):
        """If _warm_imports fails, the PID file is still removed (finally block).

        Task 6: verifies the PID-file finally block works even when the
        warming pipeline raises. Without this, a stale PID file would
        block the app's wait_for_prewarm() for 60s on every launch.
        """
        self._setup_fake_cache(tmp_path)
        sentinel, pid_file = self._mock_config(monkeypatch, tmp_path)

        monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        monkeypatch.setattr(prewarm, "_boot_time", lambda: 1720000000)
        # Make _warm_imports raise ImportError (simulates missing torch).
        monkeypatch.setattr(
            prewarm,
            "_warm_imports",
            lambda: (_ for _ in ()).throw(ImportError("no torch")),
        )

        exit_code = prewarm.run(force=True)

        assert exit_code == prewarm.EXIT_IMPORT_FAILED
        # PID file must be removed even though the pipeline failed.
        assert not pid_file.exists(), (
            "PID file must be removed in the finally block even when "
            "_warm_imports raises (otherwise wait_for_prewarm blocks 60s)"
        )
        # Sentinel must NOT be written (the run didn't succeed).
        assert not sentinel.exists(), "sentinel must NOT be written when the run fails"


# ─── Package-file warmup (replaces `import torch`) ─────────────────────────


class TestWarmPackageFiles:
    """``_warm_package_files`` pages a package's bytes into the OS cache
    WITHOUT importing it — the optimization that replaced the old
    ``import torch`` / ``import transformers`` warmup step.
    """

    def test_reads_files_without_importing(self, monkeypatch, tmp_path):
        """The whole point: warm the bytes but never execute the package.

        A regression back to ``import torch`` would drop ``torch`` (or the
        warmed package) into sys.modules; this asserts it never does.
        """
        # NOTE: use a warmable suffix (.json) — the production
        # _WARM_PACKAGE_SUFFIXES set deliberately excludes .bin (no
        # package reads raw .bin data at import time).
        (tmp_path / "a.json").write_bytes(b"x" * 1024)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.json").write_bytes(b"y" * 2048)

        class _FakeSpec:
            submodule_search_locations = [str(tmp_path)]
            origin = None

        reads = []
        monkeypatch.setattr(prewarm.importlib.util, "find_spec", lambda name: _FakeSpec())
        monkeypatch.setattr(prewarm, "_warm_file", lambda p: reads.append(Path(p)) or 0)

        total = prewarm._warm_package_files("fakepkg")

        assert "fakepkg" not in sys.modules
        assert total == 0  # fake _warm_file returns 0 bytes
        assert set(reads) == {tmp_path / "a.json", sub / "b.json"}

    def test_missing_package_returns_zero_and_does_not_import(self, monkeypatch):
        """``find_spec`` returning None must be a safe no-op, not an import."""
        monkeypatch.setattr(prewarm.importlib.util, "find_spec", lambda name: None)
        assert prewarm._warm_package_files("does-not-exist") == 0
        assert "does-not-exist" not in sys.modules
