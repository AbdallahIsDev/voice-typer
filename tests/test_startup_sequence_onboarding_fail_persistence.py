"""XZ-R12-08: regression tests for the persisted onboarding fail counter."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from voice_typer.server.startup_sequence import _phases_early as ss_mod


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at a tmp_path so the fail-counter file"""
    monkeypatch.setattr(ss_mod, "_config_dir", lambda: tmp_path)
    return tmp_path


class TestReadOnboardingFailCount:
    """``_read_onboarding_fail_count`` returns ``(count, last_fail_ts)``"""

    def test_returns_zero_when_file_missing(self, isolated_config_dir: Path) -> None:
        # No file written yet, fresh install / first run.
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


class TestWriteOnboardingFailCount:
    """``_write_onboarding_fail_count`` persists the counter to disk as"""

    def test_writes_count_and_timestamp(self, isolated_config_dir: Path) -> None:
        ss_mod._write_onboarding_fail_count(3, 1234567890.5)
        path = ss_mod._onboarding_fail_counter_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        # The fail counter lives in the merged onboarding-status
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
        # The helper must NOT propagate the exception, the in-memory
        def _raise_oserror(_self: Path, _data: str, **_kwargs: object) -> int:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "write_text", _raise_oserror)
        # Must not raise.
        ss_mod._write_onboarding_fail_count(1, 1.0)


class TestResetOnboardingFailCount:
    """``_reset_onboarding_fail_count`` clears the persisted counter so"""

    def test_zeroes_existing_counter(self, isolated_config_dir: Path) -> None:
        path = ss_mod._onboarding_fail_counter_path()
        path.write_text(json.dumps({"fail_count": 2, "last_fail_ts": 1.0}), encoding="utf-8")
        assert path.exists()
        ss_mod._reset_onboarding_fail_count()
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 0
        assert ts == 0.0

    def test_no_op_when_file_missing(self, isolated_config_dir: Path) -> None:
        # A missing status document is the pre-onboarding state —
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
        # started/completed flags, resetting the counter must NOT
        from voice_typer.server import onboarding_status as os_status

        os_status.write_status(isolated_config_dir, started=True, completed=True, fail_count=5, last_fail_ts=5.0)
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


class TestOnboardingFailCounterTTL:
    """The 7-day TTL constant must be a positive number of seconds —"""

    def test_ttl_is_positive(self) -> None:
        assert ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS > 0

    def test_ttl_is_seven_days(self) -> None:
        seven_days_seconds = 7 * 24 * 60 * 60
        assert seven_days_seconds == ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS

    def test_filename_is_dotted(self) -> None:
        # The status document lives in the config dir alongside
        from voice_typer.server.onboarding_status import ONBOARDING_STATUS_FILENAME

        assert ONBOARDING_STATUS_FILENAME.startswith(".")
        assert "onboarding" in ONBOARDING_STATUS_FILENAME
        assert ONBOARDING_STATUS_FILENAME.endswith(".json")


class TestStaleCounterTTLResetBehavior:
    """``StartupSequence.run`` (not in the read helper itself). These"""

    def test_eight_day_old_counter_is_still_readable(self, isolated_config_dir: Path) -> None:
        eight_days_ago = time.time() - (8 * 24 * 60 * 60)
        ss_mod._write_onboarding_fail_count(2, eight_days_ago)
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 2
        # The caller's TTL check (now - ts > TTL) would be True here,
        assert (time.time() - ts) > ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS

    def test_one_day_old_counter_is_still_within_ttl(self, isolated_config_dir: Path) -> None:
        one_day_ago = time.time() - (1 * 24 * 60 * 60)
        ss_mod._write_onboarding_fail_count(2, one_day_ago)
        count, ts = ss_mod._read_onboarding_fail_count()
        assert count == 2
        # The caller's TTL check (now - ts > TTL) would be False here,
        assert (time.time() - ts) <= ss_mod._ONBOARDING_FAIL_COUNTER_TTL_SECONDS
