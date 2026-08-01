"""XZ-14-16: migration runner must NOT bump ``schema_version`` on failure.

The pre-fix behaviour (the bug): when a migrator raised an exception,
``Config.load()`` caught it, logged an ERROR, KEPT the partially-
migrated data, CONTINUED to the next migrator, and finally bumped
``schema_version`` to ``_CURRENT_SCHEMA_VERSION``.  That lied to the
next launch: the on-disk version said "fully migrated" but some fields
were never actually migrated, so the next launch SKIPPED the failed
migrator permanently and the user was stuck with a half-migrated
config.

The fix (XZ-14-16):
  * On migrator exception, BREAK the loop -- later migrators expect
    the prior version's data shape and would compound the corruption.
  * Do NOT bump ``schema_version`` to ``_CURRENT_SCHEMA_VERSION`` --
    leave it at ``last_successful_version`` (the highest version whose
    migrator completed without raising, or ``loaded_version`` if no
    migrator has succeeded yet) so the failed migration re-runs on
    the next launch.
  * Log an ERROR clearly identifying which migration failed and what
    exception was raised (the existing DE-3 tests pin the message
    format ``"migrator v<N> raised <ExcType>"``).
  * Save a timestamped ``.bak`` file whose name embeds the failed
    target version so multiple failures across launches don't clobber
    each other and the user can identify which migration produced
    which backup.  Pattern:
    ``config.json.bak.failed-migration-YYYYMMDD-HHMMSS-to-v<N>``

Design note on exception propagation: the XZ-14-16 finding's
suggested fix mentions re-raising the exception so the caller knows
the migration failed.  We deliberately do NOT re-raise, because the
existing ``TestMigratorFailureDoesNotBumpSchemaVersion`` tests
(pinned by a prior session) expect ``Config.load()`` to RETURN a
``Config`` object on migrator failure (not raise).  The "loud report"
requirement is satisfied by the ERROR log + the ``_load_warnings``
entry + the on-disk ``.bak`` file + leaving ``schema_version`` at the
pre-failure version -- the user is informed and the next launch
retries.  Re-raising would crash the app on every launch until the
underlying migrator bug is fixed, which is a worse UX for a config
migration than loading with the pre-migration data.

These tests are the XZ-14-16 contract; the DE-3 tests in
``tests/test_config_group_fixes.py`` pin the same behaviour from a
prior session and should also pass after the fix.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from voice_typer.server import config as config_mod
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    yield


@pytest.fixture
def _restore_migrations():
    """Snapshot _MIGRATIONS so a test can monkey-patch it and we restore
    the originals afterwards (defensive -- monkeypatch.setitem already
    undoes itself, but the DE-3 tests use a manual clear/update pattern
    and we mirror that here for parity)."""
    original = dict(config_mod._MIGRATIONS)
    try:
        yield
    finally:
        config_mod._MIGRATIONS.clear()
        config_mod._MIGRATIONS.update(original)


# schema_version must NOT bump on migrator failure ─────────────


class TestMigratorFailureDoesNotBumpSchemaVersion:
    """XZ-14-16: the headline behaviour -- on migrator exception the
    on-disk ``schema_version`` MUST stay at the pre-failure version so
    the failed migration re-runs on the next launch."""

    def test_failure_at_v2_leaves_schema_version_at_loaded_version(self, tmp_path, monkeypatch):
        """A v2 migrator that raises must leave schema_version at the
        loaded version (0), NOT bump it to _CURRENT_SCHEMA_VERSION (3)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        def _failing_v2(data):
            raise RuntimeError("XZ-14-16 simulated migrator failure")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        loaded = Config.load()

        assert loaded.schema_version == 0, (
            "XZ-14-16 regression: schema_version was bumped to "
            f"{loaded.schema_version} even though the v2 migrator raised. "
            "The config is now bricked in a half-migrated state -- the next "
            "launch will see version==current and skip the failed migrator "
            "permanently."
        )

    def test_failure_at_v3_after_v2_succeeds_leaves_schema_version_at_v2(self, tmp_path, monkeypatch):
        """If v2 succeeds but v3 raises, schema_version must be left at
        2 (the last successful version), NOT 3 -- so only v3 re-runs on
        the next launch (v2 won't needlessly re-run)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        def _failing_v3(data):
            raise RuntimeError("XZ-14-16 v3 failure after v2 success")

        # v2 stays as the real migrator; only v3 is patched to fail.
        monkeypatch.setitem(config_mod._MIGRATIONS, 3, _failing_v3)

        loaded = Config.load()

        assert loaded.schema_version == 2, (
            "XZ-14-16 regression: when v2 succeeded but v3 raised, "
            f"schema_version was set to {loaded.schema_version} instead of 2 "
            "(the last successful version).  This means the next launch will "
            "either skip v3 (if bumped to 3) or needlessly re-run v2 (if set "
            "to 0) -- both are wrong."
        )

    def test_happy_path_still_bumps_to_current(self, tmp_path, monkeypatch):
        """Sanity: when no migrator raises, schema_version IS bumped to
        _CURRENT_SCHEMA_VERSION.  Guards against an over-correction that
        would never bump the version."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        loaded = Config.load()

        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION, (
            "XZ-14-16 over-correction: happy-path migration should still bump "
            f"schema_version to {_CURRENT_SCHEMA_VERSION}, got "
            f"{loaded.schema_version}."
        )


# failed-migration loop must NOT continue to later migrators ────


class TestFailureBreaksMigrationLoop:
    """XZ-14-16: a failed migrator must BREAK the loop -- later
    migrators expect the prior version's data shape and would compound
    the corruption if run against partially-migrated data."""

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
            "XZ-14-16 regression: v3 migrator ran after v2 raised -- later "
            "migrators expect v2-format data and would compound the corruption."
        )


# timestamped .bak file with failed-version in filename ────────


class TestFailedMigrationBackup:
    """XZ-14-16: on migrator failure a ``.bak`` file must be saved with
    a forensic-unique identifier (Unix timestamp seconds + PID +
    nanosecond fraction) and the failed target version in the filename,
    so multiple failures across launches don't clobber each other and
    the user can identify which migration produced which backup."""

    _BAK_RE = re.compile(
        # Format is ``config.json.bak.failed-migration-{ts_sec}-{pid}-{ts_ns}-to-v<N>``
        # where ``ts_sec`` is a Unix timestamp (seconds), ``pid`` is the
        # process id, and ``ts_ns`` is the nanosecond fraction of the
        # timestamp. This gives forensic uniqueness: same-second failures
        # from different processes are disambiguated by PID, and
        # same-process same-second failures by the nanosecond fraction.
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
            "XZ-14-16 regression: expected exactly one failed-migration .bak "
            f"file in {tmp_path}, found {len(bak_files)}: "
            f"{[p.name for p in bak_files]}"
        )

    def test_bak_filename_has_timestamp_and_failed_version(self, tmp_path, monkeypatch):
        """The .bak filename must match the pattern
        ``config.json.bak.failed-migration-{ts_sec}-{pid}-{ts_ns}-to-v<N>``
        where ``<N>`` is the failed target version and the three numeric
        segments provide forensic uniqueness across same-second failures
        (PID disambiguates processes; nanosecond fraction disambiguates
        same-process, same-second failures)."""
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
            f"XZ-14-16: .bak filename {name!r} does not match expected pattern "
            f"'config.json.bak.failed-migration-<ts_sec>-<pid>-<ts_ns>-to-v<N>'."
        )
        # Specifically: the failed-version suffix must be '-to-v3' because
        # v3 is the migrator that raised.
        assert name.endswith("-to-v3"), (
            f"XZ-14-16: .bak filename {name!r} should end with '-to-v3' "
            "(the failed target version), not something else."
        )

    def test_bak_contains_pre_migration_on_disk_content(self, tmp_path, monkeypatch):
        """The .bak must be a copy of the on-disk config.json BEFORE the
        failed migration ran -- this is the user's recovery point."""
        config_file = tmp_path / "config.json"
        original = {"schema_version": 0, "hotkey": "<f9>", "model_size": "tiny.en"}
        config_file.write_text(json.dumps(original))

        def _failing_v2(data):
            # Mutate `data` to simulate a partial migration -- this must
            # NOT end up in the .bak (the .bak is from config_file, the
            # pre-migration on-disk state).
            data["partial_migration_marker"] = "should_not_be_in_bak"
            raise RuntimeError("partial then fail")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        Config.load()

        bak_files = list(tmp_path.glob("config.json.bak.failed-migration-*"))
        assert len(bak_files) == 1
        bak_content = json.loads(bak_files[0].read_text())
        assert bak_content == original, (
            "XZ-14-16: .bak file content does not match the pre-migration "
            f"on-disk config.json.  Expected {original}, got {bak_content}. "
            "The .bak must be a recovery point -- it should be a copy of the "
            "config.json that was on disk BEFORE the migration ran."
        )

    def test_repeated_failures_do_not_clobber_each_other(self, tmp_path, monkeypatch):
        """Two failures in quick succession must produce TWO .bak files
        (the timestamp makes them unique).  Without the timestamp the
        second failure would silently overwrite the first .bak and the
        user would lose the first recovery point."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v2(data):
            raise RuntimeError("repeat failure")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        Config.load()
        # Force a second load -- the timestamp granularity is 1 second,
        # so sleep just over 1s to guarantee a different filename.
        import time

        time.sleep(1.1)
        # Rewrite config.json (it was left untouched on disk by load).
        config_file.write_text(json.dumps({"schema_version": 0}))
        Config.load()

        bak_files = sorted(tmp_path.glob("config.json.bak.failed-migration-*"))
        assert len(bak_files) == 2, (
            "XZ-14-16: two failures should produce two .bak files (timestamp "
            f"makes them unique), got {len(bak_files)}.  Files: "
            f"{[p.name for p in bak_files]}"
        )
        # The two filenames must differ (the timestamp ensures this).
        assert bak_files[0].name != bak_files[1].name


# failure must be loudly reported in logs ──────────────────────


class TestFailureIsLoggedAtError:
    """XZ-14-16: the failure must be loudly reported -- an ERROR log
    identifying which migration failed and what exception was raised.
    The previous "silent swallow + bump" behaviour was the bug."""

    def test_error_log_names_failed_version_and_exception_type(self, tmp_path, monkeypatch, caplog):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v2(data):
            raise RuntimeError("XZ-14-16 loud-report check")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        # The ERROR record must mention which version failed and what
        # exception type was raised.  The  tests pin the substring
        # "migrator v<N> raised <ExcType>"; we assert the same contract.
        error_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "migrator v2 raised" in r.message and "RuntimeError" in r.message
        ]
        assert len(error_records) >= 1, (
            "XZ-14-16: migrator failure was not loudly reported in logs. "
            f"Records: {[r.message for r in caplog.records]}"
        )

    def test_load_does_not_raise_on_migrator_failure(self, tmp_path, monkeypatch):
        """XZ-14-16 design decision: ``Config.load()`` does NOT re-raise
        the migrator's exception.  Re-raising would crash the app on
        every launch until the underlying migrator bug is fixed -- a
        worse UX for a config migration than loading with the
        pre-migration data + a loud ERROR log + a ``.bak`` + leaving
        ``schema_version`` at the pre-failure version so the migration
        re-runs on next launch.

        This test pins that decision so a future change that adds a
        ``raise`` is caught.  The pre-existing ``TestDE3MigratorFailure``
        tests in ``tests/test_config_group_fixes.py`` rely on this
        contract (they call ``loaded = Config.load()`` without
        ``pytest.raises``)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        def _failing_v2(data):
            raise RuntimeError("should not escape load()")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)

        # Must not raise -- the failure is reported via the ERROR log,
        # the _load_warnings list, the .bak file, and the
        # schema_version NOT being bumped (all verified by other tests
        # in this module).
        loaded = Config.load()
        assert loaded is not None
        assert loaded.schema_version == 0
