"""
migration runner must NOT bump ``schema_version`` on failure.
* Do NOT bump ``schema_version`` to ``_CURRENT_SCHEMA_VERSION`` --
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from voice_typer.server import config as config_mod
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield


@pytest.fixture
def _restore_migrations():
    """Snapshot _MIGRATIONS so a test can monkey-patch it and we restore"""
    original = dict(config_mod._MIGRATIONS)
    try:
        yield
    finally:
        config_mod._MIGRATIONS.clear()
        config_mod._MIGRATIONS.update(original)


# schema_version must NOT bump on migrator failure ─────────────


class TestMigratorFailureDoesNotBumpSchemaVersion:
    """on-disk ``schema_version`` MUST stay at the pre-failure version so"""

    def test_failure_at_v2_leaves_schema_version_at_loaded_version(self, tmp_path, monkeypatch):
        """A v2 migrator that raises must leave schema_version at the"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        def _failing_v2(data):
            raise RuntimeError("simulated migrator failure")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        loaded = Config.load()

        assert loaded.schema_version == 0, (
            "regression: schema_version was bumped to "
            f"{loaded.schema_version} even though the v2 migrator raised. "
            "The config is now bricked in a half-migrated state -- the next "
            "launch will see version==current and skip the failed migrator "
            "permanently."
        )

    def test_failure_at_v3_after_v2_succeeds_leaves_schema_version_at_v2(self, tmp_path, monkeypatch):
        """If v2 succeeds but v3 raises, schema_version must be left at"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        def _failing_v3(data):
            raise RuntimeError("v3 failure after v2 success")

        monkeypatch.setitem(config_mod._MIGRATIONS, 3, _failing_v3)

        loaded = Config.load()

        assert loaded.schema_version == 2, (
            "regression: when v2 succeeded but v3 raised, "
            f"schema_version was set to {loaded.schema_version} instead of 2 "
            "(the last successful version).  This means the next launch will "
            "either skip v3 (if bumped to 3) or needlessly re-run v2 (if set "
            "to 0) -- both are wrong."
        )

    def test_happy_path_still_bumps_to_current(self, tmp_path, monkeypatch):
        """would never bump the version."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        loaded = Config.load()

        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION, (
            "over-correction: happy-path migration should still bump "
            f"schema_version to {_CURRENT_SCHEMA_VERSION}, got "
            f"{loaded.schema_version}."
        )


# failed-migration loop must NOT continue to later migrators ────


class TestFailureBreaksMigrationLoop:
    """a failed migrator must BREAK the loop -- later"""

    def test_v3_does_not_run_after_v2_raises(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        call_log = {"v2": 0, "v3": 0}

        def _failing_v2(data):
            call_log["v2"] += 1
            raise RuntimeError("v2 failure")

        def _v3_must_not_run(data):
            call_log["v3"] += 1
            return data

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)
        monkeypatch.setitem(config_mod._MIGRATIONS, 3, _v3_must_not_run)

        Config.load()

        assert call_log["v2"] == 1, "v2 migrator should have run exactly once"
        assert call_log["v3"] == 0, (
            "regression: v3 migrator ran after v2 raised -- later "
            "migrators expect v2-format data and would compound the corruption."
        )


class TestFailedMigrationBackup:
    """a forensic-unique identifier (Unix timestamp seconds + PID +"""

    _BAK_RE = re.compile(
        # Format is ``config.json.bak.failed-migration-{ts_sec}-{pid}-{ts_ns}-to-v<N>``
        r"^config\.json\.bak\.failed-migration-\d+-\d+-\d+-to-v\d+$"
    )

    def test_failed_migration_creates_bak_file(self, tmp_path, monkeypatch):
        """A failed migrator must produce a .bak file in the config dir."""
        config_file = tmp_path / "config.json"
        original_content = json.dumps({"schema_version": 0, "hotkey": "<f5>", "model_size": "small.en"})
        config_file.write_text(original_content)

        def _failing_v2(data):
            raise RuntimeError("trigger .bak")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        Config.load()

        bak_files = list(tmp_path.glob("config.json.bak.failed-migration-*"))
        assert len(bak_files) == 1, (
            "regression: expected exactly one failed-migration .bak "
            f"file in {tmp_path}, found {len(bak_files)}: "
            f"{[p.name for p in bak_files]}"
        )

    def test_bak_filename_has_timestamp_and_failed_version(self, tmp_path, monkeypatch):
        """The .bak filename must match the pattern"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v3(data):
            raise RuntimeError("v3 failure for filename check")

        # Patch v3 to fail (v2 must succeed first so we reach v3).
        monkeypatch.setitem(config_mod._MIGRATIONS, 3, _failing_v3)

        Config.load()

        bak_files = list(tmp_path.glob("config.json.bak.failed-migration-*"))
        assert len(bak_files) == 1
        name = bak_files[0].name
        assert self._BAK_RE.match(name), (
            f".bak filename {name!r} does not match expected pattern "
            f"'config.json.bak.failed-migration-<ts_sec>-<pid>-<ts_ns>-to-v<N>'."
        )
        # Specifically: the failed-version suffix must be '-to-v3' because
        assert name.endswith("-to-v3"), (
            f".bak filename {name!r} should end with '-to-v3' (the failed target version), not something else."
        )

    def test_bak_contains_pre_migration_on_disk_content(self, tmp_path, monkeypatch):
        """The .bak must be a copy of the on-disk config.json BEFORE the"""
        config_file = tmp_path / "config.json"
        original = {"schema_version": 0, "hotkey": "<f9>", "model_size": "tiny.en"}
        config_file.write_text(json.dumps(original))

        def _failing_v2(data):
            # Mutate `data` to simulate a partial migration -- this must
            data["partial_migration_marker"] = "should_not_be_in_bak"
            raise RuntimeError("partial then fail")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        Config.load()

        bak_files = list(tmp_path.glob("config.json.bak.failed-migration-*"))
        assert len(bak_files) == 1
        bak_content = json.loads(bak_files[0].read_text())
        assert bak_content == original, (
            ".bak file content does not match the pre-migration "
            f"on-disk config.json.  Expected {original}, got {bak_content}. "
            "The .bak must be a recovery point -- it should be a copy of the "
            "config.json that was on disk BEFORE the migration ran."
        )

    def test_repeated_failures_do_not_clobber_each_other(self, tmp_path, monkeypatch):
        """Two failures in quick succession must produce TWO .bak files"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v2(data):
            raise RuntimeError("repeat failure")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        Config.load()
        # Force a second load -- the timestamp granularity is 1 second,
        import time

        time.sleep(1.1)
        # Rewrite config.json (it was left untouched on disk by load).
        config_file.write_text(json.dumps({"schema_version": 0}))
        Config.load()

        bak_files = sorted(tmp_path.glob("config.json.bak.failed-migration-*"))
        assert len(bak_files) == 2, (
            "two failures should produce two .bak files (timestamp "
            f"makes them unique), got {len(bak_files)}.  Files: "
            f"{[p.name for p in bak_files]}"
        )
        # The two filenames must differ (the timestamp ensures this).
        assert bak_files[0].name != bak_files[1].name


class TestFailureIsLoggedAtError:
    """the failure must be loudly reported -- an ERROR log"""

    def test_error_log_names_failed_version_and_exception_type(self, tmp_path, monkeypatch, caplog):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v2(data):
            raise RuntimeError("loud-report check")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        # "migrator v<N> raised <ExcType>"; we assert the same contract.
        error_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "migrator v2 raised" in r.message and "RuntimeError" in r.message
        ]
        assert len(error_records) >= 1, (
            f"migrator failure was not loudly reported in logs. Records: {[r.message for r in caplog.records]}"
        )

    def test_load_does_not_raise_on_migrator_failure(self, tmp_path, monkeypatch):
        """design decision: ``Config.load()`` does NOT re-raise"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v2(data):
            raise RuntimeError("should not escape load()")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        # Must not raise -- the failure is reported via the ERROR log,
        loaded = Config.load()
        assert loaded is not None
        assert loaded.schema_version == 0
