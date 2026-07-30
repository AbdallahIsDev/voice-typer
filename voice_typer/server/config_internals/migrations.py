"""Schema migration runner + per-version migrators.

Extracted verbatim from ``voice_typer.server.config`` (FZ-S4 / DT-24
partial split).  Every public symbol here is re-exported from
``config.py`` (via ``from voice_typer.server.config_internals.migrations
import _CURRENT_SCHEMA_VERSION, _MIGRATIONS, _migrate_to_v2,
_migrate_to_v3, _run_migrations``) so existing callers — including the
test suite, which mutates ``config_mod._MIGRATIONS`` in place via
``monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)`` and
``config_mod._MIGRATIONS.clear() / .update(original)`` — keep working
unchanged.

Object-identity contract: ``config._MIGRATIONS`` MUST be the same
``dict`` object as ``migrations._MIGRATIONS`` so in-place mutations
performed via one name are visible via the other.  This is satisfied
by ``from ... import _MIGRATIONS`` (which binds the same object, not
a copy).

``_CURRENT_SCHEMA_VERSION`` is also re-exported because
``Config.schema_version: int = _CURRENT_SCHEMA_VERSION`` uses it as a
dataclass field default, ``Config._backup_before_migration`` /
``Config._backup_before_downgrade`` compare against it, and several
test modules do ``from voice_typer.server.config import
_CURRENT_SCHEMA_VERSION`` (read-only — no monkeypatching).

No circular imports: this module depends only on the stdlib.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("voice_typer.server.config")

_CURRENT_SCHEMA_VERSION = 3

# NEW-DEAD-018: _MIGRATIONS infrastructure for schema version migrations.
# G4-L-22: v3 prunes deprecated dead-code keys.
# T1-F3 / GT-D1-7: typed as ``dict[int, Callable[[dict[str, Any]], dict[str, Any]]]``
# so static checkers can verify that every registered migration is a function
# taking a config dict and returning a (possibly mutated) config dict.
# The keys/values are deliberately ``Any`` (not a TypedDict) because the
# migration functions freely add/remove/rename arbitrary keys on the raw
# JSON-loaded dict before it is fed to ``Config(**data)``.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _migrate_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate config from schema v1 to v2 (ADR 0007 -- filter chain).

    G4-M-13: each rename logs at INFO.
    G4-L-23: warnings appended to data["_load_warnings"].
    G4-M-16: preset_existed captured BEFORE the rename block.
    """
    data.setdefault("_load_warnings", [])
    preset_existed = "audio_preset" in data

    preset = data.get("audio_preset", "auto")
    if preset == "recommended":
        log.info("[CONFIG] migrating schema v1 -> v2: renaming audio_preset 'recommended' -> 'auto'")
        data["_load_warnings"].append("audio_preset 'recommended' renamed to 'auto' (schema v2 migration)")
        data["audio_preset"] = "auto"
    elif preset == "none":
        log.info("[CONFIG] migrating schema v1 -> v2: renaming audio_preset 'none' -> 'off'")
        data["_load_warnings"].append("audio_preset 'none' renamed to 'off' (schema v2 migration)")
        data["audio_preset"] = "off"

    if data.get("noise_filter_enabled") is False and not preset_existed:
        log.info("[CONFIG] migrating schema v1 -> v2: noise_filter_enabled=False -> setting audio_preset='off'")
        data["_load_warnings"].append(
            "audio_preset set to 'off' because noise_filter_enabled was False (schema v2 migration)"
        )
        data["audio_preset"] = "off"

    if data.get("noise_filter_rnnoise") is True and "noise_suppression_method" not in data:
        log.info("[CONFIG] migrating schema v1 -> v2: noise_filter_rnnoise=True -> noise_suppression_method='rnnoise'")
        data["_load_warnings"].append(
            "noise_suppression_method set to 'rnnoise' because noise_filter_rnnoise was True (schema v2 migration)"
        )
        data["noise_suppression_method"] = "rnnoise"

    return data


def _migrate_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate config from schema v2 to v3 (G4-L-22 -- prune deprecated fields).

    ADR 0007 deprecated several fields that the filter chain no longer
    reads.  v3 explicitly pop()s them from the on-disk dict.

    GT-58: ``noise_filter_enabled`` and ``noise_filter_post_capture`` were
    previously in this scrub list but are actually RUNTIME switches (read
    by ``level_monitor.py`` and synced by ``config_applier.py``) — they
    must NOT be pruned here. Only the 7 truly-dead fields below are
    scrubbed. See ADR 0009 §5 for the canonical field-by-field status.

    The 7 dead fields are KEPT in this scrub list even though they were
    also removed from the ``Config`` dataclass — this guarantees that
    existing ``config.json`` files written by older app versions (which
    still carry these keys) load without raising ``TypeError`` from
    ``cls(**data)``. The keys are silently popped before construction.
    """
    data.setdefault("_load_warnings", [])
    deprecated_keys = (
        "silence_rms_threshold",
        "silence_peak_threshold",
        "normalize_audio",
        "normalize_target_peak",
        "volume_duck_per_session",
        "volume_duck_smart",
        "noise_filter_gate_threshold",
    )
    for key in deprecated_keys:
        if key in data:
            log.info("[CONFIG] migrating schema v2 -> v3: pruning deprecated key %r", key)
            data["_load_warnings"].append(f"deprecated key {key!r} pruned (schema v3 migration)")
            data.pop(key)
    return data


_MIGRATIONS[2] = _migrate_to_v2
_MIGRATIONS[3] = _migrate_to_v3


def _run_migrations(
    data: dict[str, Any],
    loaded_version: Any,
    config_file,
) -> tuple[dict[str, Any], int, bool]:
    """M3: run forward schema migrations from ``loaded_version`` to ``_CURRENT_SCHEMA_VERSION``.

    Extracted verbatim from ``Config._run_migrations`` (FZ-S4 / DT-24).
    Returns ``(data, final_schema_version, migrations_ran)`` where
    ``migrations_ran`` is ``True`` iff at least one migrator was
    attempted (whether successful or not).

    XZ-14-16: On migrator exception, do NOT bump schema_version to
    ``_CURRENT_SCHEMA_VERSION`` and do NOT continue to the next
    migrator.  Leave schema_version at ``last_successful_version``
    (= ``loaded_version`` if no migrator has succeeded yet) so the
    failed migration re-runs on the next launch.  Previously the
    runner silently swallowed the exception, kept the
    partially-migrated data, and bumped the version to
    ``_CURRENT_SCHEMA_VERSION`` — that bricked the config: the next
    launch saw version==current and skipped the failed migrator
    permanently, leaving the user with a half-migrated config that
    claimed to be fully migrated.

    When ``loaded_version`` is missing or non-int (fresh install /
    corrupt file), there is nothing to migrate — default to
    ``_CURRENT_SCHEMA_VERSION`` so a fresh config gets the current
    schema.
    """
    migrations_ran = False
    last_successful_version = loaded_version if isinstance(loaded_version, int) else _CURRENT_SCHEMA_VERSION
    if isinstance(loaded_version, int):
        for version in range(loaded_version + 1, _CURRENT_SCHEMA_VERSION + 1):
            migrator = _MIGRATIONS.get(version)
            if migrator is not None:
                # G4-M-13: log the migration BEFORE
                # calling the migrator.
                log.info(
                    "[CONFIG] migrating schema v%d -> v%d",
                    max(loaded_version, version - 1),
                    version,
                )
                # G4-CR-07 / XZ-14-16: wrap each
                # migrator in try/except.  On exception:
                # log ERROR with the failed version and
                # exception type, save a timestamped +
                # version-stamped .bak so the user can
                # recover the pre-failure on-disk state,
                # then BREAK the loop.  Later migrators
                # expect the prior version's data shape
                # and would compound the corruption if
                # run.  schema_version is left at
                # ``last_successful_version`` (NOT bumped
                # to _CURRENT_SCHEMA_VERSION) so the
                # migration re-runs on next launch.
                try:
                    data = migrator(data)
                    migrations_ran = True
                    last_successful_version = version
                except Exception as migrator_exc:
                    log.error(
                        "[CONFIG] migrator v%d raised %s: %s -- "
                        "aborting migration loop; schema_version will "
                        "remain at v%d so this migration re-runs on next launch",
                        version,
                        type(migrator_exc).__name__,
                        migrator_exc,
                        last_successful_version,
                    )
                    data.setdefault("_load_warnings", []).append(
                        f"schema migration v{version} raised "
                        f"{type(migrator_exc).__name__}: {migrator_exc} -- "
                        f"schema_version kept at v{last_successful_version}; "
                        "migration will re-run on next launch"
                    )
                    migrations_ran = True
                    # XZ-14-16: save a timestamped .bak
                    # with the failed target version in
                    # the filename so multiple failures
                    # across launches don't clobber each
                    # other and the user can identify
                    # which migration produced which
                    # backup.  Best-effort -- a backup
                    # failure must not mask the original
                    # migrator failure.
                    try:
                        # XE-10-2: use the SEC-002 symlink-TOCTOU-safe
                        # read+write pair (mirrors
                        # ``_backup_before_downgrade`` /
                        # ``_backup_before_migration`` in config.py)
                        # instead of ``shutil.copy2``.  ``copy2`` follows
                        # symlinks, so a ``config.json`` symlinked at
                        # an attacker-controlled path would have been
                        # transparently copied here — defeating the
                        # symlink-TOCTOU guard the rest of the load()
                        # path enforces.  ``_secure_read_text`` opens
                        # with ``O_NOFOLLOW`` (POSIX) / reparse-point
                        # check (Windows) so a planted symlink is
                        # rejected; ``_secure_atomic_write`` writes via
                        # a temp file + atomic rename.
                        import os

                        from voice_typer.server.secure_file_io import (
                            _secure_atomic_write,
                            _secure_read_text,
                        )

                        # XE-10-3: the previous ``time.strftime("%Y%m%d-
                        # %H%M%S", time.gmtime())`` suffix had 1-second
                        # resolution — two failures in the same second
                        # (e.g. renderer-triggered reload + backend
                        # independent load during startup) silently
                        # overwrote each other via ``shutil.copy2``,
                        # destroying the first failure's forensic
                        # recovery point.  Mirror config.py:1676's
                        # ``{int(time.time())}-{os.getpid()}-{time.time_ns()
                        # % 1_000_000}`` format: PID disambiguates
                        # same-second loads from DIFFERENT processes,
                        # the microsecond fraction disambiguates
                        # same-process same-second loads.
                        ts_sec = int(time.time())
                        pid = os.getpid()
                        ts_ns = time.time_ns() % 1_000_000
                        failed_bak = config_file.parent / (
                            f"config.json.bak.failed-migration-{ts_sec}-{pid}-{ts_ns}-to-v{version}"
                        )
                        raw_text = _secure_read_text(config_file)
                        _secure_atomic_write(failed_bak, raw_text)
                        log.warning(
                            "[CONFIG] migrator to v%d failed; saved pre-failure config.json backup to %s",
                            version,
                            failed_bak,
                        )
                        # XE-10-3: cap retained failed-migration backups
                        # to 5 (oldest pruned).  Mirrors the
                        # ``_prune_kept_backups`` call in
                        # ``_backup_before_migration`` (config.py:1824)
                        # so the directory doesn't grow unbounded
                        # across many failed launches.  Looked up via
                        # the ``config`` module attribute (lazy import)
                        # to avoid a circular module-load (``config.py``
                        # imports this module at the top of the file).
                        try:
                            from voice_typer.server import config as _cfg_module

                            _cfg_module._prune_kept_backups(
                                config_file.parent,
                                prefix="config.json.bak.failed-migration-",
                                keep=5,
                            )
                        except OSError as prune_exc:
                            log.debug(
                                "[CONFIG] failed to prune old failed-migration backups: %s",
                                prune_exc,
                            )
                    except OSError as backup_exc:
                        log.warning(
                            "[CONFIG] migrator to v%d failed AND pre-failure backup also failed: %s",
                            version,
                            backup_exc,
                        )
                    break  # XZ-14-16: do NOT run later migrators
    return data, last_successful_version, migrations_ran
