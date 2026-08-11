"""XZ-R12-08: regression tests for the persisted onboarding fail counter.

Background
----------
``voice_typer/server/startup_sequence.py`` previously stored the
onboarding fail counter ONLY as an in-memory attribute
(``app._onboarding_fail_count``). The "after 3 failures" circuit
breaker (which marks ``onboarding_completed=True`` +
``onboarding_failed=True`` so the app stays usable) therefore only
tripped if all 3 failures occurred in the SAME process session — a
user whose onboarding failed once per app-start would NEVER hit the
breaker and would be stuck on the onboarding wizard forever.

XZ-R12-08 fix
-------------
Added three module-level helpers in ``startup_sequence.py`` that
persist the counter inside the merged onboarding-status document
(``<config_dir>/.onboarding_status.json``) as the fields
``{"fail_count": <int>, "last_fail_ts": <epoch-float>}``:

* ``_read_onboarding_fail_count()``  → ``(count, last_fail_ts)``
* ``_write_onboarding_fail_count(count, last_fail_ts)``  → ``None``
* ``_reset_onboarding_fail_count()``  → ``None`` (deletes the file)

The startup-sequence onboarding-failure path now reads → increments →
writes the counter, with a 7-day TTL so a stale counter from months
ago doesn't trip on the next transient failure.

These tests pin the helpers' behaviour in isolation (no full
``StartupSequence.run()`` invocation needed, so they don't depend on
the heavy ``app_for_startup`` fixture that has a pre-existing
``monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled")``
setup error on Linux).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from voice_typer.server import startup_sequence as ss_mod

# ── Helpers ────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at a tmp_path so the fail-counter file
    doesn't collide with the real config dir on the host.

    ``startup_sequence._config_dir`` is imported from
    ``voice_typer.server.config``; patching the binding inside
    ``startup_sequence`` is sufficient because the helper functions
    call ``_config_dir()`` (looked up at call time in their module
    scope).
    """
    monkeypatch.setattr(ss_mod, "_config_dir", lambda: tmp_path)
    return tmp_path


# ── _read_onboarding_fail_count ────────────────────────────────────────


class TestReadOnboardingFailCount:
    """``_read_onboarding_fail_count`` returns ``(count, last_fail_ts)``
    from the persisted JSON file, with safe defaults on any read
    failure."""

    def test_returns_zero_when_file_missing(self, isolated_config_dir: Path) -> None:
        # No file written yet — fresh install / first run.
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_reads_count_and_timestamp(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(
            json.dumps({"fail_count": 2, "last_fail_ts": 1234567890.5}),
            encoding="utf-8",
        )
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 2
        assert ts == 1234567890.5

    def test_returns_zero_on_corrupt_json(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text("not json {{{", encoding="utf-8")
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_returns_zero_on_non_dict_root(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_returns_zero_on_negative_count(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": -1, "last_fail_ts": 1.0}), encoding="utf-8")
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0, "negative count must fall back to 0 (safe default)"

    def test_returns_zero_on_non_int_count(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": "two", "last_fail_ts": 1.0}), encoding="utf-8")
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0

    def test_coerces_int_timestamp_to_float(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": 1, "last_fail_ts": 1234567890}), encoding="utf-8")
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 1
        assert isinstance(ts, float)
        assert ts == 1234567890.0

    def test_handles_non_numeric_timestamp_gracefully(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": 1, "last_fail_ts": "yesterday"}), encoding="utf-8")
        count, ts = ss_mod._read_onboarding_fail_count()
        # Count is still valid; the bad timestamp falls back to 0.0.
        assert count == 1
        assert ts == 0.0


# ── _write_onboarding_fail_count ───────────────────────────────────────


class TestWriteOnboardingFailCount:
    """``_write_onboarding_fail_count`` persists the counter to disk as
    a JSON document so it survives process restarts."""

    def test_writes_count_and_timestamp(self, isolated_config_dir: Path) -> None:
        ss_mod._write_onboarding_fail_count(3, 1234567890.5)
        path = ss_mod._onboarding_fail_counter_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        # The fail counter lives in the merged onboarding-status
        # document, so the persisted dict also carries the
        # started/completed flags.
        assert data["fail_count"] == 3
        assert data["last_fail_ts"] == 1234567890.5

    def test_overwrites_existing_file(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": 1, "last_fail_ts": 1.0}), encoding="utf-8")
        ss_mod._write_onboarding_fail_count(2, 2.0)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["fail_count"] == 2
        assert data["last_fail_ts"] == 2.0

    def test_round_trips_through_read(self, isolated_config_dir: Path) -> None:
        ss_mod._write_onboarding_fail_count(7, 99999.0)
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 7
        assert ts == 99999.0

    def test_write_failure_is_swallowed(self, isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate a read-only filesystem by making write_text raise.
        # The helper must NOT propagate the exception — the in-memory
        # counter on ``app._onboarding_fail_count`` is still updated by
        # the caller, so the circuit breaker can still trip in-session
        # even if persistence is broken.
        def _raise_oserror(_self: Path, _data: str, **_kwargs: object) -> int:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "write_text", _raise_oserror)
        # Must not raise.
        ss_mod._write_onboarding_fail_count(1, 1.0)


# ── _reset_onboarding_fail_count ───────────────────────────────────────


class TestResetOnboardingFailCount:
    """``_reset_onboarding_fail_count`` clears the persisted counter so
    a future transient failure starts fresh."""

    def test_zeroes_existing_counter(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": 2, "last_fail_ts": 1.0}), encoding="utf-8")
        assert path.exists()
        ss_mod._reset_onboarding_fail_count()
        # The counter is zeroed; the status document itself is kept so
        # the started/completed flags survive the reset.
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_no_op_when_file_missing(self, isolated_config_dir: Path) -> None:
        # A missing status document is the pre-onboarding state —
        # calling reset again must be a no-op (not raise) and leave the
        # counter at zero.
        path = ss_mod._onboarding_fail_counter_path()
        assert not path.exists()
        ss_mod._reset_onboarding_fail_count()
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_read_returns_zero_after_reset(self, isolated_config_dir: Path) -> None:
        ss_mod._write_onboarding_fail_count(5, 5.0)
        ss_mod._reset_onboarding_fail_count()
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_reset_preserves_started_completed_flags(self, isolated_config_dir: Path) -> None:
        # The fail counter shares the status document with the
        # started/completed flags — resetting the counter must NOT
        # un-complete onboarding (this is exactly why the reset writes
        # fail_count=0 instead of deleting the file).
        from voice_typer.server import onboarding_status as os_status

        os_status.write_status(
            isolated_config_dir, started=True, completed=True, fail_count=5, last_fail_ts=5.0
        )
        ss_mod._reset_onboarding_fail_count()
        data = os_status.read_status(isolated_config_dir)
        assert data["fail_count"] == 0
        assert data["started"] is True
        assert data["completed"] is True

    def test_reset_failure_is_swallowed(self, isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": 1, "last_fail_ts": 1.0}), encoding="utf-8")

        def _raise_oserror(*_args: object, **_kwargs: object) -> None:
            raise OSError("permission denied")

        from voice_typer.server import onboarding_status as os_status

        monkeypatch.setattr(os_status, "_write", _raise_oserror)
        # Must not raise.
        ss_mod._reset_onboarding_fail_count()


# ── TTL constant sanity ────────────────────────────────────────────────


class TestOnboardingFailCounterTTL:
    """The 7-day TTL constant must be a positive number of seconds —
    pinning it prevents an accidental zero/negative value from
    disabling the stale-counter reset logic."""

    def test_ttl_is_positive(self) -> None:
        assert ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS > 0

    def test_ttl_is_seven_days(self) -> None:
        # The docstring says "7 days matches the onboarding wizard's
        # won't bother the user again cadence". Pin the value so a
        # future edit that changes the cadence is a deliberate review
        # point, not a silent regression.
        seven_days_seconds = 7 * 24 * 60 * 60
        assert seven_days_seconds == ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS

    def test_filename_is_dotted(self) -> None:
        # The status document lives in the config dir alongside
        # config.json and uses a leading dot (matching the
        # .dictation-in-flight convention) so it doesn't show up in
        # default directory listings and is clearly an internal state
        # file.
        from voice_typer.server.onboarding_status import ONBOARDING_STATUS_FILENAME

        assert ONBOARDING_STATUS_FILENAME.startswith(".")
        assert "onboarding" in ONBOARDING_STATUS_FILENAME
        assert ONBOARDING_STATUS_FILENAME.endswith(".json")


# ── Stale-counter TTL reset (integration with the read helper) ─────────


class TestStaleCounterTTLResetBehavior:
    """The TTL reset logic lives in the onboarding-failure path of
    ``StartupSequence.run`` (not in the read helper itself). These
    tests pin the *contract* of the helpers so the TTL logic in
    ``run()`` can rely on them.

    Specifically: a counter written 8 days ago is still readable (the
    read helper doesn't apply the TTL — that's the caller's job); the
    caller's TTL check is ``(now - last_fail_ts) > TTL_SECONDS``.
    """

    def test_eight_day_old_counter_is_still_readable(self, isolated_config_dir: Path) -> None:
        eight_days_ago = time.time() - (8 * 24 * 60 * 60)
        ss_mod._write_onboarding_fail_count(2, eight_days_ago)
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 2
        # The caller's TTL check (now - ts > TTL) would be True here,
        # so the caller would reset to 0 before incrementing. The
        # helper itself returns the raw persisted value.
        assert (time.time() - ts) > ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS

    def test_one_day_old_counter_is_still_within_ttl(self, isolated_config_dir: Path) -> None:
        one_day_ago = time.time() - (1 * 24 * 60 * 60)
        ss_mod._write_onboarding_fail_count(2, one_day_ago)
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 2
        # The caller's TTL check (now - ts > TTL) would be False here,
        # so the caller would increment the existing count.
        assert (time.time() - ts) <= ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS
