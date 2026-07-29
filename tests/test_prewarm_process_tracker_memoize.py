"""DJ-47: TTL-memoization regression test for ``process_tracker._process_is_prewarm``.

``is_prewarm_running()`` is called from the ``get_prewarm_status`` IPC
handler, which the About-page UI polls at ~1 Hz. Each call re-walks the
target process's PEB (Windows) or re-reads ``/proc/{pid}/cmdline`` (Linux)
to verify the live PID is actually prewarm. The walk is ~1-5 ms per
call but adds up to constant CPU drain on the UI thread's IPC handler.

DJ-47 adds a 5 s TTL memoization keyed on ``(pid, pid_file_fingerprint)``
where ``pid_file_fingerprint`` is the PID file's ``(mtime_ns, ino, size)``.
The PID file is the source of truth: once we've verified a PID is (or
isn't) prewarm, trust for 5 s. If the PID file is rewritten (new prewarm
run, new PID), the mtime+inode change and the cache is invalidated
immediately.

These tests pin the memoization so a future revert fails loudly.
"""

from __future__ import annotations

import os
import time

import pytest
from voice_typer.server import prewarm
from voice_typer.server.prewarm import process_tracker

# ─── Test isolation ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_pid_check_cache():
    """Clear the ``_prewarm_pid_check_cache`` before AND after each test.

    Without this, a prior test's cached result could leak into the next
    test's assertion (especially since the cache key includes the PID
    file's mtime, which can collide across tests that reuse the same
    tmp_path file).
    """
    process_tracker._invalidate_prewarm_pid_check_cache()
    yield
    process_tracker._invalidate_prewarm_pid_check_cache()


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    """Provide a PID file in tmp_path and patch ``_pid_file_path`` to
    return it. Tests write a PID to this file to simulate a running
    prewarm process.
    """
    f = tmp_path / ".prewarm.pid"
    monkeypatch.setattr(prewarm, "_pid_file_path", lambda: f)
    return f


# ─── Tests ────────────────────────────────────────────────────────────────


class TestProcessIsPrewarmMemoize:
    """DJ-47: ``_process_is_prewarm`` must memoize results for 5 s."""

    def test_repeated_calls_within_ttl_use_cache(self, monkeypatch, pid_file):
        """Within the 5 s TTL, repeated calls do NOT re-invoke the
        underlying platform check (``_process_is_prewarm_uncached``).
        """
        pid_file.write_text(str(os.getpid()))

        call_count = {"n": 0}

        def counting_uncached(pid):
            call_count["n"] += 1
            return True  # pretend the PID is prewarm

        # Patch the uncached inner directly on the module — the wrapper
        # looks it up by name at call time, so the patch takes effect.
        monkeypatch.setattr(
            process_tracker, "_process_is_prewarm_uncached", counting_uncached
        )

        # First call: cache miss → invokes uncached.
        assert process_tracker._process_is_prewarm(os.getpid()) is True
        assert call_count["n"] == 1, (
            "DJ-47: first call should be a cache miss and invoke the uncached check"
        )

        # Second call within TTL: cache hit → does NOT invoke uncached.
        assert process_tracker._process_is_prewarm(os.getpid()) is True
        assert call_count["n"] == 1, (
            f"DJ-47: second call within TTL should hit cache; instead "
            f"_process_is_prewarm_uncached was called {call_count['n']} times. "
            f"If this is 2, the memoization was reverted."
        )

        # Third call: still within TTL — still 1 invocation.
        assert process_tracker._process_is_prewarm(os.getpid()) is True
        assert call_count["n"] == 1

    def test_pid_file_change_invalidates_cache(self, monkeypatch, pid_file):
        """When the PID file is rewritten (new prewarm run), the cache
        must be invalidated so the next call re-checks.
        """
        pid_file.write_text(str(os.getpid()))

        call_count = {"n": 0}

        def counting_uncached(pid):
            call_count["n"] += 1
            return True

        monkeypatch.setattr(
            process_tracker, "_process_is_prewarm_uncached", counting_uncached
        )

        # First call populates cache.
        process_tracker._process_is_prewarm(os.getpid())
        assert call_count["n"] == 1

        # Second call within TTL: cache hit.
        process_tracker._process_is_prewarm(os.getpid())
        assert call_count["n"] == 1

        # Rewrite PID file (new mtime_ns) — cache should be invalidated.
        # Sleep 0.01 s to ensure mtime_ns differs (Linux mtime granularity
        # is ~1 ns but the file-content rewrite may not bump mtime if the
        # write happens within the same mtime tick).
        time.sleep(0.05)
        pid_file.write_text(str(os.getpid()))

        # Next call: cache miss (different pid_file_key).
        process_tracker._process_is_prewarm(os.getpid())
        assert call_count["n"] == 2, (
            f"DJ-47: PID file change should invalidate cache; call_count="
            f"{call_count['n']} (expected 2: one for the original check, "
            f"one for the post-rewrite re-check). If call_count is 1, the "
            f"PID-file fingerprint was not included in the cache key."
        )

    def test_invalid_pid_skips_cache(self, monkeypatch, pid_file):
        """``pid <= 0`` returns False immediately without populating the
        cache (no point caching an obviously-invalid PID).
        """
        pid_file.write_text("12345")

        call_count = {"n": 0}

        def counting_uncached(pid):
            call_count["n"] += 1
            return True

        monkeypatch.setattr(
            process_tracker, "_process_is_prewarm_uncached", counting_uncached
        )

        assert process_tracker._process_is_prewarm(0) is False
        assert process_tracker._process_is_prewarm(-1) is False
        assert call_count["n"] == 0, (
            f"DJ-47: invalid PIDs (0, -1) should short-circuit before the "
            f"uncached check is called; got {call_count['n']} invocations."
        )

    def test_ttl_is_5_seconds(self):
        """The TTL constant must be 5 s (per the DJ-47 spec)."""
        assert process_tracker._PREWARM_PID_CHECK_TTL_S == 5.0, (
            f"DJ-47: _PREWARM_PID_CHECK_TTL_S should be 5.0 s, got "
            f"{process_tracker._PREWARM_PID_CHECK_TTL_S}. A longer TTL "
            f"risks serving stale results after a PID recycling attack; "
            f"a shorter TTL doesn't amortise the cmdline read across the "
            f"typical ~1 Hz UI poll."
        )

    def test_invalidate_prewarm_pid_check_cache_clears_cache(
        self, monkeypatch, pid_file
    ):
        """``_invalidate_prewarm_pid_check_cache`` must force a re-check
        on the next call.
        """
        pid_file.write_text(str(os.getpid()))

        call_count = {"n": 0}

        def counting_uncached(pid):
            call_count["n"] += 1
            return True

        monkeypatch.setattr(
            process_tracker, "_process_is_prewarm_uncached", counting_uncached
        )

        # First call: cache miss.
        process_tracker._process_is_prewarm(os.getpid())
        assert call_count["n"] == 1

        # Second call within TTL: cache hit.
        process_tracker._process_is_prewarm(os.getpid())
        assert call_count["n"] == 1

        # Invalidate the cache.
        process_tracker._invalidate_prewarm_pid_check_cache()

        # Next call: cache miss again.
        process_tracker._process_is_prewarm(os.getpid())
        assert call_count["n"] == 2, (
            f"DJ-47: after _invalidate_prewarm_pid_check_cache, the next "
            f"call should re-invoke the uncached check; got call_count="
            f"{call_count['n']} (expected 2)."
        )

    def test_missing_pid_file_bypasses_cache(self, monkeypatch, pid_file):
        """When the PID file does not exist, the cache key uses
        ``pid_file_key = None`` and the result is still memoized — but a
        subsequent call (after the PID file is created) must re-check
        because the key changed.
        """
        # PID file does NOT exist initially.
        assert not pid_file.exists()

        call_count = {"n": 0}

        def counting_uncached(pid):
            call_count["n"] += 1
            return False  # not prewarm (no PID file → not running)

        monkeypatch.setattr(
            process_tracker, "_process_is_prewarm_uncached", counting_uncached
        )

        # First call: PID file missing → cache key = (pid, None).
        assert process_tracker._process_is_prewarm(os.getpid()) is False
        assert call_count["n"] == 1

        # Second call: same key (PID file still missing) → cache hit.
        assert process_tracker._process_is_prewarm(os.getpid()) is False
        assert call_count["n"] == 1

        # Create the PID file — cache key changes.
        pid_file.write_text(str(os.getpid()))

        # Next call: cache miss (key is now (pid, (mtime, ino, size))).
        # Patch the uncached to return True this time (simulating prewarm
        # is now running).
        monkeypatch.setattr(
            process_tracker,
            "_process_is_prewarm_uncached",
            lambda pid: (call_count.__setitem__("n", call_count["n"] + 1) or True),
        )
        assert process_tracker._process_is_prewarm(os.getpid()) is True
        assert call_count["n"] == 2, (
            f"DJ-47: PID file appearance should change the cache key and "
            f"force a re-check; got call_count={call_count['n']}"
        )

    def test_different_pids_use_different_cache_entries(self, monkeypatch, pid_file):
        """Two different PIDs must NOT share a cache entry (each PID gets
        its own check, even when the PID file is the same).
        """
        pid_file.write_text("1000")

        call_count = {"n": 0}
        results = {1000: True, 2000: False}

        def counting_uncached(pid):
            call_count["n"] += 1
            return results.get(pid, False)

        monkeypatch.setattr(
            process_tracker, "_process_is_prewarm_uncached", counting_uncached
        )

        # Check PID 1000 → True.
        assert process_tracker._process_is_prewarm(1000) is True
        assert call_count["n"] == 1

        # Check PID 2000 → False. Different cache key (different pid).
        assert process_tracker._process_is_prewarm(2000) is False
        assert call_count["n"] == 2, (
            f"DJ-47: different PIDs must use different cache entries; "
            f"got call_count={call_count['n']} (expected 2: one per PID)."
        )

        # Re-check PID 1000 — should be a cache hit (same key).
        assert process_tracker._process_is_prewarm(1000) is True
        assert call_count["n"] == 2

        # Re-check PID 2000 — should be a cache hit.
        assert process_tracker._process_is_prewarm(2000) is False
        assert call_count["n"] == 2
