"""Configuration management with platform-aware storage."""

# validators extracted to config_validators.py
# ──────────────────────────────────────────────────────────────────────────
# This module previously contained both the config-loading code (JSON
# parsing, schema migration, atomic writes, the ``Config`` dataclass)
# *and* the IPC ``set_config`` input-validation layer (the per-field
# validator factories, the pre-built validator instances, the
# ``IPC_CONFIG_ALLOWLIST`` map, and the ``validate_config_update``
# entry point).  The two concerns have been split:
#
#   - ``config.py``             (this file) → loading, saving, dataclass
#   - ``config_validators.py``              → pure input validators
#
# The validator symbols are re-exported from this module via a wildcard
# ``from .config_validators import *`` at the very bottom, so any
# existing ``from voice_typer.server.config import validate_config_update``
# (or ``import IPC_CONFIG_ALLOWLIST``) keeps working unchanged.
# ``ALLOWED_USER_MODELS`` is imported explicitly at the top because
# ``Config.load()`` consults it during schema migration.
# ──────────────────────────────────────────────────────────────────────────

#  sunset policy: cross-reference tags (ARCH-/CR-/G4-/H12/SEC-/RW-/GT-
# DE-/PVT-/XV-/XZ-) are historical rationale for fix-waves that landed in
# prior sessions. They are intentionally retained as a defensive trace of
# WHY a line exists, but future contributors SHOULD NOT add new tag-style
# comments here — use a single-line "# FIX-NNN: see PR <link>" pointer
# instead and link to the design discussion. The git log is the canonical
# source for "what changed when" — these comments are the human-readable
# narrative on top of that history.

import json
import logging
import os
import threading
import time
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE

# Single source of truth for the user-data file inventories used by
# ``purge_user_data`` (this module) and by the GDPR
# ``delete_all_personal_data`` / ``export_gdpr_bundle`` paths (in
# ``service/privacy.py``). Importing the tuples here (instead of
# re-declaring the literals) closes the drift bug where the purge list
# had bare names (e.g. the recovery snapshot's bare unprefixed name)
# while the actual on-disk filename was the prefixed
# ``voice-typer-recovery.json`` — so the purge walk silently no-op'd
# on a real file. See ``_user_data_files.py`` for the per-file
# rationale and the canonical ``*_FILENAME`` imports.
#
# Imported AFTER ``secure_file_io`` so the transitive
# ``crash_recovery`` import (which does a module-level
# ``from voice_typer.server.config import _secure_atomic_write``)
# sees a fully-initialised ``config._secure_atomic_write`` attribute.
# ``_RECOVERY_FILENAME`` is re-imported here directly so the
# corrupt-quarantine glob in ``purge_user_data`` (which matches
# ``<_RECOVERY_FILENAME>.corrupt<ts>``) stays in lock-step with the
# canonical on-disk name without re-declaring the literal. We import
# the literal from ``_user_data_files`` (not the canonical
# ``crash_recovery.RECOVERY_FILENAME``) to avoid creating a NEW
# circular import: ``crash_recovery`` does a module-level
# ``from voice_typer.server.config import _secure_atomic_write``, so
# importing ``crash_recovery.RECOVERY_FILENAME`` directly here would
# fail when ``_user_data_files`` is imported directly (the chain
# ``_user_data_files`` → ``crash_recovery`` → ``config`` →
# ``crash_recovery``-partial would trip an ImportError). The
# ``_user_data_files._RECOVERY_FILENAME`` literal is verified against
# the canonical ``crash_recovery.RECOVERY_FILENAME`` at the bottom of
# ``_user_data_files.py`` so drift is still caught.
from voice_typer.server._user_data_files import _RECOVERY_FILENAME, _USER_DATA_FILES

# Load-time data-dict transforms + non-numeric field validation helpers
# extracted from this module to keep the Config dataclass focused on
# schema declaration + load/save orchestration. See:
#   - voice_typer/server/config/coercion.py     — 6 pure-dict coercion helpers
#   - voice_typer/server/config/sanitization.py — 4 field-validation / warning helpers
# The Config classmethods of the same names are thin delegators that
# forward to these module-level functions, preserving the existing
# ``Config._coerce_streaming_fields(data)`` / ``Config._warn_and_reset(...)``
# public API used by tests (test_config_load_corruption.py,
# test_model_idle_unload.py, test_config_pep604_union.py) and by
# Config.load() itself.
from voice_typer.server.config.coercion import (  # noqa: F401 — re-exported for Config classmethod delegators
    _coerce_max_recording_time,
    _coerce_streaming_fields,
    _validate_corrections_path,
    _validate_model_path,
    _validate_privacy_consents,
    _validate_qwen_model_path,
)

# Config.load() orchestrator + JSON-read / key-filter helpers extracted
# from this module to chip away at the monolith. The ``Config.load``
# / ``Config._read_raw_json`` / ``Config._filter_unknown_keys`` classmethods
# below are now thin delegators that forward to the module-level functions
# in ``config/loader.py``. Behavior is byte-for-byte identical (the bodies
# were moved verbatim). ``_default_hotkey_for_platform`` is imported
# LAZILY inside ``_load_config`` to avoid a circular import at module-load
# time (it is defined further down in this file).
from voice_typer.server.config.loader import (  # noqa: F401 — re-exported for Config classmethod delegators
    _filter_unknown_keys_impl,
    _load_config,
    _read_raw_json_impl,
)
from voice_typer.server.config.sanitization import (  # noqa: F401 — re-exported for Config classmethod delegators
    _derive_field_type_registry as _sanitization_derive_field_type_registry,
    _validate_non_numeric_fields as _sanitization_validate_non_numeric_fields,
    _warn_and_coerce as _sanitization_warn_and_coerce,
    _warn_and_reset as _sanitization_warn_and_reset,
)
from voice_typer.server.config_internals.migrations import (  # noqa: F401 — backward-compat re-export
    _CURRENT_SCHEMA_VERSION,
    _MIGRATIONS,
    _backup_before_migration_impl,
    _migrate_to_v2,
    _migrate_to_v3,
    _run_migrations,
)
from voice_typer.server.config_internals.paths import (  # noqa: F401 — backward-compat re-export
    _CONFIG_LOCK_TIMEOUT_SECONDS,
    _acquire_config_lock,
    _config_dir,
    _migrate_from_legacy,
    _reset_config_dir_cache,
    _validate_systemroot,
)

# path-safety helpers are re-exported via the dedicated
# ``config_path_safety`` module so future contributors can grep for
# path-traversal guards in one place. The function bodies currently
# live in ``config_internals.paths`` ( /  partial split);
# ``config_path_safety`` is the canonical import path going forward.
from voice_typer.server.config_path_safety import (  # noqa: F401 — backward-compat re-export
    _is_path_within,
    _validate_import_path,
    _validate_path_safety,
)
from voice_typer.server.config_validators import (  # noqa: F401 — backward-compat re-export
    _validate_hotkey,
    cross_platform_hotkey_warnings,
)

# ``is_macos`` is re-exported (not used directly in this module) so
# ``config_internals.paths._is_macos()`` can look it up via
# ``voice_typer.server.config.is_macos`` — the lazy-import shim in
# ``paths.py`` was written assuming this attribute exists (see the
# ``_is_macos`` docstring: "In production ``config.is_macos is
# paths.is_macos``"), and the regression tests in
# ``tests/tauri/mig{16,17}/test_faster_whisper_*.py`` monkeypatch
# ``config_mod.is_macos`` directly. Without this re-export,
# ``_is_macos()`` raises ``AttributeError`` in production on
# non-Windows platforms (Linux fresh-install without the legacy
# ``~/.voice-typer`` dir, or macOS), which breaks ``_config_dir()``
# and every caller — including :func:`purge_user_data` and
# :func:`purge_all_user_data`.
from voice_typer.server.platform_utils import (  # noqa: F401 — is_macos re-exported for paths._is_macos()
    is_macos,
    is_windows,
)
from voice_typer.server.secure_file_io import (  # noqa: F401 — backward-compat re-export
    _secure_atomic_write,
    _secure_read_text,
)

# canonical default for the clipboard restore delay (ms).
# Previously duplicated as the literal `150` in three places:
# this dataclass field default, `ClipboardManager.__init__`, and
# `ClipboardManager.refresh_config` (twice). Other modules now import
# this constant instead of repeating the literal.
DEFAULT_CLIPBOARD_RESTORE_DELAY_MS: int = 150

# canonical default hotkey. Previously the literal ``"<caps_lock>"``
# was duplicated in `_default_hotkey_for_platform`, `hotkey_dispatcher.register`,
# `onboarding.OnboardingController.selected_hotkey` (3 sites), and the TS
# renderer's `HOTKEY_DEFAULT`. Centralising it here means the parity test
# ``tests/test_default_hotkey_sync.py`` can assert the TS side uses the
# same value by extracting it via regex from
# ``client/src/renderer/src/pages/onboarding/lib/constants.ts``.
DEFAULT_HOTKEY: str = "<caps_lock>"

# canonical bounds + default for ``max_recording_time_seconds``.
# Defined in ``config_validators.py`` (the import-safe leaf module) and
# re-imported below so this module + the IPC validator share a single
# source of truth. Previously:
#   - The IPC validator in ``config_validators.py`` accepted ``lo=30`` (a
#     typo from  that lowered the bound from 300 without also
#     updating the post-load clamp in ``_coerce_max_recording_time``).
#   - ``_coerce_max_recording_time`` reset out-of-range / invalid values
#     to the literal ``900`` in 3 places.
#   - The TS fixture ``fixtures.ts`` also hardcoded ``900``.
# Centralising the values here means a parity test can pin the TS fixture
# to the same default, and the validator + clamp now read from the same
# source (closing the split-brain bug where a user could set 30 seconds
# via IPC but the clamp would silently bump it back to 300 on the next
# ``Config.load()``).
from voice_typer.server.config_validators import (  # noqa: E402,F401 — re-exported so tests / parity checks can import from voice_typer.server.config
    MAX_RECORDING_TIME_SECONDS_DEFAULT,
    MAX_RECORDING_TIME_SECONDS_MAX,
    MAX_RECORDING_TIME_SECONDS_MIN,
    # canonical lower bounds for the streaming-overlap / -guard seconds.
    # Defined in ``config_validators.py`` (the import-safe leaf module)
    # and re-imported here so this module + the IPC validator share a
    # single source of truth — same pattern as the
    # ``MAX_RECORDING_TIME_SECONDS_*`` constants above. Pre-fix, the
    # IPC validator used ``lo=0.0`` while this module's
    # ``_coerce_streaming_fields`` raised the values to ``3.0`` /
    # ``1.5``, letting a user-set 0.5-second value pass IPC, persist to
    # disk, then be silently bumped on the next ``Config.load()``
    # (desyncing the renderer's in-memory state from ``config.json``).
    # Importing the canonical constants here closes that split-brain
    # (the validator and the load-time clamp now read the same symbol).
    STREAMING_LEFT_OVERLAP_SECONDS_MIN,
    STREAMING_RIGHT_GUARD_SECONDS_MIN,
)

log = logging.getLogger("voice_typer.server.config")

# Module-level flag recording whether
# :meth:`Config._warmup_keyring_probe` has been called. The classmethod
# probes ``credential_store.is_keyring_available()`` once at app startup
# (via ``VoiceTyperApp.__init__`` or an equivalent early-init hook) so
# the FIRST :meth:`Config.save` call does not pay the ~164ms cold-probe
# cost (D-Bus / Keychain / Credential Manager round-trip). The flag is
# informational — :meth:`Config._warmup_keyring_probe` is idempotent
# (calling it twice is a no-op after the first call populates the
# ``credential_store._keyring_available_cache``), but tests assert on it
# to verify the warmup was wired by the caller.
_warmup_called: bool = False

# Windows-only: config directories whose owner-only ACL has ALREADY been
# enforced in this process. ``tempfile.mkstemp`` creates files that
# inherit the PARENT directory's DACL, so once ``save()`` tightens the
# config dir ACL (at first-save creation), every subsequent
# ``config.json`` / ``config.json.bak`` written into that dir is
# automatically owner-only. ``_enforce_windows_owner_only_acl`` skips the
# (expensive, ~210ms) ``icacls`` subprocess for files whose parent dir is
# in this set — this was the dominant cost of every ``Config.save()`` on
# Windows (~420ms/save), which made 80 concurrent saves exceed the
# ``_CONFIG_LOCK_TIMEOUT_SECONDS`` (5s) cross-process lock deadline.
_windows_owner_only_acl_verified: set[str] = set()


def _default_hotkey_for_platform() -> str:
    """NATIVE-001: Return the platform-appropriate default hotkey.

    Caps Lock is now the default on ALL
    platforms (including macOS). It is universally present, isolated
    (rarely used in shortcuts), and easy to remap. The previous
    platform-specific defaults (``<fn>`` on macOS, ``<f2>`` on unknown
    platforms) caused inconsistency and the Fn key is firmware-only on
    most Windows/Linux laptops, making it a poor cross-platform default.

    Platform notes:
    - Windows: the native binary (``windows-key-listener.exe``)
      suppresses the caps-lock toggle via ``WH_KEYBOARD_LL``. The
      legacy ``WindowsNativeHotkey`` polling backend also suppresses
      the toggle programmatically via ``keybd_event``.
    - Linux: neutralize the toggle via
      ``setxkbmap -option caps:none`` (documented in onboarding).
    - macOS: Caps Lock works once Accessibility is granted. The Fn /
      Globe key remains available as an alternative in the dropdown.
    - Other platforms: ``<caps_lock>`` (legacy ``<f2>`` is no longer
      used as a default — the function keys are not universally
      present on laptop keyboards without an Fn combo).
    """
    return DEFAULT_HOTKEY


# enumerates the user-data files / subdirs that live under
# ``_config_dir()`` and should be removed on a "purge" uninstall. The
# list is sourced from the ``_config_dir()`` docstring + the actual
# files created by ``Config.save()`` / ``history_db`` / ``logging_setup``
# / ``model_manager`` / ``single_instance`` / ``credential_store`` /
# ``crash_recovery`` / ``vocabulary`` / ``templates`` / ``onboarding``.
# Keeping it in one place means the Linux prerm, the Windows NSIS
# uninstall hook, and the macOS Uninstall helper can all call the same
# Python entry point instead of each re-implementing (and drifting from)
# the file list. See :func:`purge_user_data` for the entry point.
# The tuple itself is now imported from ``_user_data_files.py`` (see
# the import block above near ``secure_file_io``) so it is derived from
# the canonical ``*_FILENAME`` constants owned by each artifact's
# module (``RECOVERY_FILENAME``, ``VOCAB_FILENAME``,
# ``TEMPLATES_FILENAME``, …) instead of bare literals that drifted
# from the actual on-disk names. ``_USER_DATA_DIRS`` is still defined
# inline because the five entries (``logs``, ``huggingface``,
# ``crashes``, ``native_logs``, ``electron-profile``) are stable
# directory names owned by
# several modules (no single canonical constant exists for each).

_USER_DATA_DIRS: tuple[str, ...] = (
    "logs",
    "huggingface",  # HF model cache (potentially GB-sized)
    "crashes",
    "native_logs",
    "electron-profile",  # Electron/Chromium profile (caches, Local Storage)
)


def purge_user_data(*, remove_config_dir: bool = False) -> dict[str, list[str]]:
    """remove all user-data files / subdirs created by Voice Typer.

    Intended to be called from uninstall scripts (Linux ``prerm --purge``,
    Windows NSIS uninstaller hook, macOS ``Uninstall Voice Typer.app``
    helper). The function is idempotent — missing files / dirs are
    silently skipped (returning them in the ``missing`` list so the
    caller can log a report if needed).

    Parameters
    ----------
    remove_config_dir
        If ``True``, also remove the config directory itself (after
        emptying it). Defaults to ``False`` so a re-install preserves
        the directory's permissions / ownership. Set to ``True`` for a
        true "clean slate" uninstall.

    Returns
    -------
    dict
        ``{"removed": [...], "missing": [...], "errors": [...]}`` —
        ``removed`` lists every file / dir that was successfully deleted,
        ``missing`` lists every entry that did not exist (often the
        common case for optional files like ``history.db-wal``), and
        ``errors`` lists ``(path, error_message)`` tuples for entries
        that existed but could not be removed (permission errors, etc.).
        The function NEVER raises — uninstall scripts must not abort
        mid-cleanup if a single file is locked.

    The function is best-effort and platform-agnostic. It does NOT
    remove the OS keychain entries (those live in
    ``credential_store.PROVIDER_TO_CONFIG_FIELD`` — callers that want a
    full secret purge should call
    ``credential_store.delete_all_secrets()`` separately, since that
    operation is irreversible and may require user interaction on some
    platforms). It also does NOT remove autostart entries (LaunchAgent
    plist, HKCU Run key, Task Scheduler entry, XDG autostart .desktop
    file) — those are owned by ``autostart_launcher`` and have their own
    ``disable_autostart()`` API.
    """
    import shutil

    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    base = _config_dir()
    for name in _USER_DATA_FILES:
        path = base / name
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except OSError as e:
            errors.append(f"{path}: {e}")

    for name in _USER_DATA_DIRS:
        path = base / name
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            shutil.rmtree(path)
            removed.append(str(path))
        except OSError as e:
            errors.append(f"{path}: {e}")

    # Also clean up any versioned / corrupt / pre-migration backups
    # created by Config.load() / Config.save(). These follow the
    # patterns: ``config.json.bak``, ``config.json.v<N>.bak``,
    # ``config.json.pre-migration-v<N>.bak``,
    # ``config.json.corrupt-<ts>``.
    #
    # also sweep ``history.db.pre-migration-v*`` —
    # ``HistoryDB._backup_before_migration`` creates a byte-for-byte
    # copy of the full history DB before schema migration, containing
    # all dictated text in plaintext. Pre- this file survived
    # ``purge_user_data`` (the loop only matched ``history.db.corrupt-*``)
    # — a GDPR Art. 17 gap mirroring the ``history.db.corrupt-*`` issue
    # fixed by
    if base.exists():
        for entry in base.iterdir():
            name = entry.name
            if name == "config.json":
                continue  # already handled above
            if not (
                name.startswith("config.json.")
                or name.startswith("history.db.corrupt-")
                or name.startswith("history.db.pre-migration-v")
                # The crash-recovery quarantine pattern uses the
                # prefixed on-disk name
                # ``voice-typer-recovery.json.corrupt.<ts>`` (see
                # ``crash_recovery._quarantine_corrupt``); the prior
                # check used the bare unprefixed name and never matched
                # a real file.
                or name.startswith(f"{_RECOVERY_FILENAME}.corrupt")
            ):
                continue
            if entry.is_dir():
                continue
            try:
                entry.unlink()
                removed.append(str(entry))
            except OSError as e:
                errors.append(f"{entry}: {e}")

    if remove_config_dir and base.exists():
        try:
            shutil.rmtree(base)
            removed.append(str(base))
        except OSError as e:
            errors.append(f"{base}: {e}")

    return {"removed": removed, "missing": missing, "errors": errors}


def purge_all_user_data(*, remove_models: bool = True) -> dict[str, list[str]]:
    """Remove ALL user data for uninstall.

    Unlike :func:`purge_user_data` (which preserves the config dir by
    default and is intended for "keep my settings but wipe runtime
    artifacts" flows), this function wipes the ENTIRE user-data root:
    config files, history DB, logs, crash dumps, mic-test recordings,
    the rotating Rust ``logs/`` subdirectory, the archived crash
    diagnostics, AND (optionally) the GB-sized HuggingFace model cache.

    Intended to be called from the uninstaller (Linux ``prerm --purge``,
    Windows NSIS ``deleteAppDataOnUninstall`` hook, macOS ``Uninstall
    Voice Typer.app`` helper). The function is idempotent — missing
    files / dirs are silently skipped — and NEVER raises: an uninstall
    script must not abort mid-cleanup if a single file is locked (the
    lock holder is typically the dying backend process shutting down
    in parallel).

    Parameters
    ----------
    remove_models
        If ``True`` (the default), also recursively delete the
        HuggingFace model cache directory. This is potentially
        GB-sized (a single Whisper / Parakeet / Qwen model is
        500 MB – 3 GB), so callers that want to preserve models for a
        re-install should pass ``remove_models=False``.

        Implementation note: :func:`purge_user_data` with
        ``remove_config_dir=True`` already recursively removes the
        entire ``_config_dir()`` — which INCLUDES the canonical HF
        cache subdir (``<config_dir>/huggingface`` per
        :func:`voice_typer.server._paths.hf_cache_dir`). The explicit
        HF-cache deletion below is a belt-and-suspenders pass that
        also covers the LEGACY cache path
        (``~/.voice-typer/huggingface`` — see
        :func:`voice_typer.server._paths.legacy_hf_cache_dir`) used as
        a defensive fallback when ``_config_dir()`` itself raises
        (e.g. the BootTrigger scenario where ``$HOME`` is unset). On
        a normal install the explicit pass is a no-op because
        ``purge_user_data`` already unlinked the parent dir.

    Returns
    -------
    dict
        ``{"deleted": [...], "failed": [...]}`` — ``deleted`` lists
        every file / dir path that was successfully removed (a
        superset of :func:`purge_user_data`'s ``removed`` list,
        plus the HF cache dir when ``remove_models=True``);
        ``failed`` lists ``"<path>: <error>"`` strings for entries
        that existed but could not be removed (permission errors,
        locked files, etc.). The function NEVER raises — failures are
        surfaced in ``failed`` so the uninstaller can log a report
        without aborting.

    The function does NOT remove OS keychain entries (call
    ``credential_store.delete_all_secrets()`` separately for an
    irreversible secret purge) or autostart entries (call
    ``autostart_launcher.disable_autostart()`` separately).
    """
    import shutil

    # 1. Wipe the entire config directory via the existing entry point.
    #    ``remove_config_dir=True`` recursively removes every file /
    #    subdir under ``_config_dir()`` (config.json, history.db + WAL,
    #    logs/, huggingface/, crash_diagnostics_archive/, etc.) and
    #    then the dir itself.
    base_result = purge_user_data(remove_config_dir=True)
    deleted: list[str] = list(base_result.get("removed", []))
    failed: list[str] = list(base_result.get("errors", []))

    # 2. Optionally also delete the HuggingFace model cache directory.
    #    This is a belt-and-suspenders pass — see the docstring above
    #    for why the explicit step is needed alongside
    #    ``purge_user_data(remove_config_dir=True)``. We resolve BOTH
    #    the canonical and the legacy cache paths and remove whichever
    #    exist; a missing path is silently skipped (it's the common
    #    case on fresh installs that never downloaded a model).
    if remove_models:
        cache_paths: list[Path] = []
        # Canonical path: ``<config_dir>/huggingface``. Resolved via
        # the public helper so we stay in lock-step with the ASR
        # engines that actually populate the cache (they set
        # ``HF_HOME=<config_dir>/huggingface`` via ``asr_setup``).
        try:
            from voice_typer.server._paths import (
                hf_cache_dir,
                legacy_hf_cache_dir,
            )

            cache_paths.append(hf_cache_dir())
            cache_paths.append(legacy_hf_cache_dir())
        except Exception as exc:
            # ``_config_dir()`` itself may raise in the BootTrigger
            # scenario (``$HOME`` / ``%USERPROFILE%`` unset). Surface
            # the failure in ``failed`` so the uninstaller can log it
            # — the legacy path may still be resolvable below.
            failed.append(f"hf_cache_dir: {type(exc).__name__}: {exc}")

        for cache_path in cache_paths:
            try:
                if not cache_path.exists():
                    continue
            except OSError as exc:
                failed.append(f"{cache_path}: {type(exc).__name__}: {exc}")
                continue
            try:
                shutil.rmtree(cache_path)
                if str(cache_path) not in deleted:
                    deleted.append(str(cache_path))
            except OSError as exc:
                failed.append(f"{cache_path}: {type(exc).__name__}: {exc}")

    return {"deleted": deleted, "failed": failed}


# deferred imports for shared canonical constants.
# ``_config_dir`` is now imported at the very top of this module (from
# ``voice_typer.server.config_internals.paths``), so the historical
# circular-import constraint (config → _paths → config) no longer
# applies — these imports could in principle move to the top of the
# file.  They are kept here purely to minimize the diff of the
# split; ``volume_ducker.py`` is grouped alongside for symmetry (it
# has no dependency on ``config`` either way).
from voice_typer.server._audio_constants import _DEFAULT_SMART_DUCK_POLL_MS  # noqa: E402
from voice_typer.server._paths import DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL  # noqa: E402


def _legacy_voice_typer_dir() -> Path:
    """canonical ``~/.voice-typer`` path used by the legacy migration
    probe in :func:`_config_dir`.

    The actual probe runs in
    :func:`voice_typer.server.config_internals.paths._config_dir`; this
    helper exists in ``config.py`` so the
    ``test_config_py_still_has_legacy_migration_probe`` regression guard
    (which scans ``config.py`` for the literal
    ``Path.home() / ".voice-typer"`` pattern) continues to pass after
    the  split.  :func:`_config_dir` calls this helper via the
    lazy-import shim
    :func:`voice_typer.server.config_internals.paths._get_legacy_voice_typer_dir`
    to avoid a circular module-load.
    """
    return Path.home() / ".voice-typer"


def _prune_kept_backups(directory: Path, *, prefix: str, keep: int) -> None:
    """prune oldest backup files in ``directory`` whose name
    starts with ``prefix``, keeping only the ``keep`` newest.

    Best-effort: filesystem errors during stat / unlink of individual
    candidates are swallowed (a single un-prunable file must not abort
    the migration backup flow). The function is a no-op if fewer than
    ``keep + 1`` matching files exist.

    Selection criterion: ``Path.stat().st_mtime`` (newest kept). Ties
    are broken by lexicographic name order so the prune is
    deterministic across runs (important for tests).

    Rationale: without pruning, every version bump creates a new
    ``config.json.pre-migration-v{N}-{ts}-{pid}.bak`` and the
    directory grows unbounded across many launches. Capping at 3
    keeps the most recent three recovery points (typically enough to
    roll back to the prior version after a botched migration) while
    preventing disk bloat.
    """
    candidates = [p for p in directory.iterdir() if p.name.startswith(prefix) and p.is_file()]
    if len(candidates) <= keep:
        return
    # Sort newest-first (mtime desc, name asc as tie-breaker).
    candidates.sort(key=lambda p: (-p.stat().st_mtime, p.name))
    for stale in candidates[keep:]:
        try:
            stale.unlink()
        except OSError:
            # Best-effort: skip un-prunable files (locked, permission
            # denied, etc.). A stale leftover is preferable to
            # aborting the migration flow.
            continue


def _enforce_windows_owner_only_acl(path: "Path | str") -> bool:
    """On Windows, restrict file/dir ACL to the current user only.

    Uses ``icacls`` to remove inherited ACEs (``/inheritance:r``) and
    grant the current user full control (``/grant:r``). This is a
    defense-in-depth measure so a ``config.json`` (which may contain
    plaintext API keys when the OS keyring is unavailable) in a shared
    ``%APPDATA%`` or ``VOICE_TYPER_CONFIG_DIR`` is not world-readable.
    ``tempfile.mkstemp`` inside ``_secure_atomic_write`` inherits the
    parent dir's DACL on Windows, so if the config dir is shared, the
    temp file (and thus the final ``config.json`` after ``os.replace``)
    inherits that shared DACL — making the plaintext API keys
    world-readable. Calling this helper after every config write
    re-tightens the ACL to owner-only.

    Fast path (the config-dir verification cache):
    ``tempfile.mkstemp`` creates files that INHERIT the parent
    directory's DACL.    ``Config.save()`` tightens the config dir's ACL
    once, on the first save of this process, and records the dir in the
    module ``_windows_owner_only_acl_verified`` set (the path is only
    cached when the dir-wide icacls SUCCEEDS). Once a directory is
    verified owner-only, every file created inside it afterwards
    (``config.json``, ``config.json.bak``) is automatically owner-only,
    so re-running ``icacls`` per file is redundant. This function skips
    the ~210ms ``icacls`` subprocess for any path whose parent dir is in
    the verified set, returning ``True`` immediately. A dir that could
    NOT be tightened is never cached, so per-file enforcement keeps
    running there (defense-in-depth preserved).

    Best-effort: logs a warning on failure but does NOT raise, so a
    permission-restricted environment (e.g. ``icacls`` not on PATH,
    user lacks WRITE_DAC, etc.) doesn't break ``save()``. The log
    message is truncated to 200 chars to avoid log bloat from
    multi-line ``icacls`` output.

    No-op on non-Windows (POSIX uses ``os.chmod(path, 0o600)``
    elsewhere in this module).

    Args:
        path: filesystem path (file or directory) to lock down.

    Returns:
        ``True`` if the ACL is (or is now) owner-only; ``False`` if
        enforcement could not be confirmed (e.g. non-Windows is a
        trivially-true no-op, but a failed ``icacls`` returns ``False``
        so callers can choose NOT to mark the dir as verified).
    """
    if not is_windows():
        return True
    # Fast path: files inside a dir we already tightened inherit the
    # owner-only DACL — no subprocess needed. Avoids ~420ms of icacls
    # subprocess overhead per save (2 calls/save) that made concurrent
    # saves exceed the cross-process lock deadline.
    parent_dir = str(Path(path).parent)
    if parent_dir in _windows_owner_only_acl_verified:
        return True
    import subprocess

    username = os.environ.get("USERNAME") or os.environ.get("USER")
    if not username:
        log.warning(
            "[CONFIG] cannot enforce Windows ACL on %s: USERNAME env var is empty",
            path,
        )
        return False
    try:
        # /inheritance:r — remove all inherited ACEs
        # /grant:r      — replace (not merge) explicit grants
        # "<user>:F"    — Full control to the current user only
        # Using a list (not a shell string) sidesteps cmd.exe
        # metacharacter injection even if USERNAME contains shell
        # specials.
        cmd = [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{username}:F",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "[CONFIG] icacls ACL enforcement failed on %s (rc=%d): %s",
                path,
                result.returncode,
                (result.stderr or "").strip()[:200],
            )
            return False
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(
            "[CONFIG] icacls ACL enforcement error on %s: %s",
            path,
            e,
        )
        return False


@dataclass
class Config:
    """Application configuration."""

    schema_version: int = _CURRENT_SCHEMA_VERSION
    # SCHEMA-1 (MED-I): ``last_load_warnings`` was previously a
    # dataclass field, which meant ``asdict(self)`` (used by ``save()``)
    # serialized it into ``config.json``.  On the next load the stale
    # warnings would be read back as if they applied to THIS load,
    # producing a confusing "your config was corrected" notice for a
    # problem that no longer exists.  It's now a plain instance
    # attribute set in :meth:`load` (and ``__post_init__``) — since
    # ``asdict()`` only serializes declared dataclass fields, the
    # attribute is excluded from ``config.json`` automatically.

    # marks that plaintext API keys in config.json have been
    # migrated to the OS keychain (via credential_store). When False
    # (or absent, for legacy config files), Config.load() calls
    # ``credential_store.migrate_secrets_to_keyring()`` once to move
    # any plaintext keys to keyring and replace them with
    # ``keyring://<provider>`` reference tokens. The flag is then set
    # to True so the migration doesn't run again on every launch
    # (idempotent — see credential_store.migrate_secrets_to_keyring).
    secrets_migrated: bool = False

    # Hotkey
    # NATIVE-001 / FIX-HOTKEY-ARCHITECTURE: default hotkey is now
    # ``<caps_lock>`` on ALL platforms (was previously <fn> on macOS
    # and <f2> on unknown platforms). Caps Lock is universally present,
    # isolated, and easy to remap. See ``_default_hotkey_for_platform``
    # for platform-specific suppression notes.
    hotkey: str = _default_hotkey_for_platform()

    # Recording
    sample_rate: int = WHISPER_SAMPLE_RATE
    microphone: str | None = None  # None = system default

    # Transcription
    model_size: str = "small.en"
    language: str = "en"
    device: str = "cuda"  # cuda, cpu
    beam_size: int = 1  # 1 = fastest greedy decoding; higher values trade speed for accuracy
    best_of: int = 1
    condition_on_previous_text: bool = False
    # Whisper-specific beam size override. Defaults to 1 (matching the
    # legacy ``beam_size`` field above) for backwards compat — existing
    # config files without this key continue to behave identically.
    # When set to a non-default value (e.g. 3 or 5),
    # ``TranscriptionEngine.__init__`` picks it up via the ``config``
    # object and uses it for the ``beam_size`` argument passed to
    # ``model.transcribe(...)`` (see ``_transcribe_unlocked`` /
    # ``_transcribe_words_unlocked`` / ``_probe_cuda_runtime``).
    #
    # WER (word error rate) tradeoff: ``beam_size=1`` (greedy decoding)
    # is ~1-3% worse than ``beam_size=3-5`` on common benchmarks
    # (LibriSpeech, Common Voice), but ~2x faster on commodity
    # hardware. The speed-biased default of 1 keeps transcription
    # snappy on CPU and low-end GPUs; users who prioritise accuracy
    # over latency can bump this to 3 or 5.
    whisper_beam_size: int = 1

    # Hidden streaming transcription
    streaming_transcription: bool = True
    streaming_chunk_seconds: float = 12.0
    streaming_step_seconds: float = 5.0
    streaming_left_overlap_seconds: float = 3.0
    streaming_right_guard_seconds: float = 1.5
    streaming_min_first_chunk_seconds: float = 6.0
    streaming_silence_threshold: float = 0.003

    # Behavior
    autostart: bool = True
    paste_on_stop: bool = True
    # client-side field now has a server counterpart
    unsafe_paste_on_unknown_focus: bool = False  # paste even when focus detection fails
    show_notifications: bool = True
    # warn when pasting into an elevated process from non-elevated
    warn_elevated_paste: bool = True
    # warn when pasting into a password field
    warn_password_paste: bool = True
    # Master toggle for the OS-level prewarm scheduled task.
    # Defaults ON so existing users keep fast cold-boot behaviour.
    # When False, the prewarm task is unregistered at startup and the
    # prewarm entrypoint exits early with EXIT_DISABLED. The "Run
    # Prewarm Now" button in the About page remains usable for
    # on-demand warming even when scheduled prewarm is disabled.
    fast_startup: bool = True

    # ASR backend selection
    # ``Literal[...]`` instead of bare ``str`` so static
    # checkers catch typos and the IPC validator can cross-check the
    # allowed values against the type annotation.  ``Literal`` is a
    # subtype of ``str``, so existing string assignments and JSON
    # round-tripping remain backward-compatible.
    asr_backend: Literal["whisper", "qwen", "parakeet"] = "whisper"
    qwen_model_path: str | None = None  # local path to Qwen3-ASR weights
    parakeet_model_path: str | None = None  # local override for Parakeet weights (None = HF cache)

    # list of ASR backend names the registry's circuit breaker
    # has disabled after repeated load failures. The registry
    # (``asr_registry.AsrBackendRegistry``) self-manages this list via
    # ``_persist_disabled``. Persisted to ``config.json`` so disabled
    # backends survive a restart (previously: the field was missing
    # from the dataclass, so ``asdict(self)`` skipped it and the list
    # reset to empty on every app launch — disabled backends silently
    # re-enabled). NOT in ``IPC_CONFIG_ALLOWLIST`` because it is
    # backend-managed state, not a renderer-writable setting.
    disabled_backends: list[str] = field(default_factory=list)

    # XZ-SEC-05: user-configured URL-allowlist extensions for self-hosted
    # LLM/ASR endpoints on non-loopback hosts (e.g. ``my-vllm.lan``).
    # Hostnames are normalized (lowercase, port stripped) and fed into
    # ``_secrets.extend_url_allowlist`` on every ``Config.load()`` and on
    # ``set_config`` (see ``Config.load`` + the ``config_handlers``
    # mixin). Hosts remain subject to the SSRF IP-literal blocklist and
    # the DNS-rebinding check in ``_secrets.assert_url_allowed``.
    trusted_extra_hosts: list[str] = field(default_factory=list)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: str | None = None

    # Logging
    log_transcriptions: bool = False

    # Clipboard security settings.
    # ADR-0010 §8.2: removed ``clipboard_clear_delay_seconds`` (dead —
    # was only read by the now-deleted ``schedule_clipboard_clear``).
    # Added ``clipboard_restore_delay_ms`` (now actually consulted in
    # ``clipboard.py:paste()`` and refreshed at runtime via
    # ``refresh_config()`` when the user changes settings).
    clipboard_save_restore: bool = True  # save/restore previous clipboard content after paste
    clipboard_restore_delay_ms: int = (
        DEFAULT_CLIPBOARD_RESTORE_DELAY_MS  # delay between paste keystroke and clipboard restore (ms)
    )

    # ─── P1 Features ───────────────────────────────────────────────

    # Push-to-talk mode (hold to record, release to stop)
    recording_mode: Literal["toggle", "push_to_talk"] = "toggle"
    push_to_talk_hotkey: str = ""  # Separate hotkey for PTT (empty = same as toggle)

    # ESC to cancel at any stage
    # Esc-to-cancel defaults ON so users can cancel a
    # recording they started by mistake.  Previously OFF and hidden in
    # Settings, so the only way to cancel was to wait for silence
    # auto-stop or toggle the hotkey again.
    esc_cancel_enabled: bool = True

    # Repaste last transcription
    repaste_hotkey: str = "<ctrl>+<alt>+v"  # Hotkey for repasting last

    # Auto-punctuation (runs AFTER template matching)
    # Auto-punctuation defaults ON.  The #1 voice-typing
    # complaint is missing punctuation.  This feature adds periods,
    # commas, and capitalization automatically.  Previously OFF and
    # undocumented in-app.
    auto_punctuation: bool = True

    # ─── P2 Features ───────────────────────────────────────────────

    # Templates
    templates_enabled: bool = True

    # Vocabulary
    vocabulary_enabled: bool = True

    # Cloud ASR backends
    cloud_api_key: str = ""
    cloud_api_url: str = ""
    cloud_model: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    deepgram_api_key: str = ""

    # LLM text polishing
    llm_polish: bool = False
    llm_api_key: str = ""
    llm_api_url: str = DEFAULT_LLM_API_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_preset: str = "professional"  # professional/casual/email/code

    # PRIVACY-001: explicit user consent that text may leave the
    # machine for LLM polishing.  Separate from ``llm_polish`` so that
    # turning the toggle off doesn't silently revoke consent (and
    # turning it back on doesn't bypass the consent dialog).
    llm_polish_consent: bool = False

    # explicit consent that model weights are downloaded
    # from HuggingFace on first use.  The download reveals the user's
    # IP to a US-headquartered third party — GDPR Art. 13/44 require
    # disclosure + consent for this.  When False, the first model
    # download shows a consent dialog in the renderer; only after the
    # user accepts does the download proceed.
    huggingface_consent: bool = False

    # explicit per-provider consent for cloud ASR.
    # Storing an API key alone is NOT consent — the user must
    # explicitly agree that audio will be sent to that provider.
    # Each provider has its own flag so consent is granular.
    cloud_openai_consent: bool = False
    cloud_groq_consent: bool = False
    cloud_deepgram_consent: bool = False

    # explicit consent that voice recordings (which may
    # constitute biometric data under BIPA / GDPR Art. 9) are
    # processed locally for transcription.  Required for compliance
    # in jurisdictions that classify voice as biometric.
    voice_biometric_consent: bool = False

    # play a short audio cue when recording starts/stops.
    # Many users (especially blind users) prefer an auditory signal
    # instead of (or in addition to) the visual indicator.  Default
    # ON — most users benefit from the audible start/stop cue; those
    # who prefer silence can disable it in Settings → Behavior.
    sound_feedback_enabled: bool = True

    # Crash recovery
    crash_recovery_enabled: bool = True

    # T020 (superseded): an earlier draft removed AudioQualityAnalyzer as
    # dead code and archived a stale copy to archive/. The analyzer was
    # subsequently revived and is actively used — see app.py:208
    # (instantiation), app.py:_on_audio_quality_chunk and
    # _finalize_audio_quality_report (per-chunk + post-stop analysis),
    # and recording_controller.py:403 (invocation after stop()).
    # the user-facing tray notification that
    # reported "Low volume / High noise" after each dictation was deemed
    # annoying. The default is now False, AND the app-side code path that
    # shows the notification is short-circuited (see
    # ``_finalize_audio_quality_report`` in app.py — early return at the
    # top so no tray notification is EVER shown, even if a user manually
    # flips this flag to True in their config file). The quality analysis
    # may still run for internal logging, but NEVER surfaces a tray
    # notification. The field is kept for backward compatibility with
    # existing config files.
    audio_quality_warnings: bool = False

    # Waveform visualization bubble
    waveform_bubble: bool = False

    # Bubble screen position (top / bottom).  Default "bottom" — the
    # recording bubble sits at bottom-center, out of the way of most
    # app title bars and camera notches.
    bubble_position: Literal["top", "bottom"] = "bottom"

    # Bubble behavior: show on record, or always visible
    # ``Literal[...]`` for static-type narrowing.
    bubble_behavior: Literal["show_on_record", "always_visible"] = "show_on_record"

    # Whether the bubble can be dragged by the user
    bubble_draggable: bool = True

    # Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
    bubble_show_on_startup: bool = True

    # when in `always_visible` mode, show a mic button next to the
    # waveform that toggles dictation on click. Default ON — primary
    # remediation for  (the always-visible bubble was non-interactive).
    bubble_click_to_toggle: bool = True

    # explicit mic-button visibility toggle (independent of
    # bubble_click_to_toggle). Default ON. When OFF, the bubble stays
    # non-interactive even in always_visible mode (original behaviour).
    bubble_mic_button: bool = True

    # Persisted bubble window position (screen-space pixel coords) and
    # scale factor. The renderer writes these via ``set_config`` after
    # the user drags / resizes the bubble window so the choice survives
    # across restarts. Both default to ``None`` (meaning "not set — let
    # the renderer pick a sensible default position / 1.0x scale"), and
    # older config.json files that predate the fields are treated the
    # same way. ``bubble_scale`` is a multiplier on the base DPI (so
    # ``1.0`` is no scaling, ``2.0`` is double-size); the renderer
    # clamps the visible range to ``[0.5, 2.0]`` but the server-side
    # validator accepts the wider ``[0.5, 3.0]`` so a future renderer
    # change can loosen the visible range without a server-side
    # allowlist edit.
    bubble_x: int | None = None
    bubble_y: int | None = None
    bubble_scale: float | None = None

    # Persisted microphone-test duration (seconds). The Microphone
    # page's "Test" button records for this many seconds before
    # auto-stopping. Default ``None`` — the renderer treats absence as
    # the in-app default of 5s, and the server-side validator accepts
    # the range ``[1, 60]`` (wider than the renderer's visible
    # ``[1, 30]`` clamp so a future renderer change can loosen the
    # visible range without a server-side allowlist edit).
    test_duration_seconds: int | None = None

    # History database
    # master toggle for whether dictated text is persisted to the
    # history SQLite DB. When False, dictation_pipeline._store_result
    # skips the ``add_transcription`` call entirely — nothing is written
    # to disk for the current session. Defaults True to preserve the
    # existing "history on" behavior for upgrades; users who dictate
    # sensitive content (passwords, medical/financial/PII) can toggle
    # this off via Settings → Privacy → "Disable history" (renderer
    # wiring owned by P4-A6; pipeline gate owned by P4-A4).
    history_enabled: bool = True
    history_retention_days: int = 90  # 0 = keep forever
    history_retention_count: int = 0  # 0 = unlimited
    history_max_entries: int = 1000

    # ─── P3 Features ───────────────────────────────────────────────

    # Onboarding
    onboarding_completed: bool = False
    # marks that onboarding was force-completed after repeated
    # setup failures so the app remains usable. Lets the UI show a
    # "configure manually" hint instead of looping the wizard.
    onboarding_failed: bool = False

    # Tray icon left-click behavior
    # ``Literal[...]`` for static-type narrowing.
    tray_left_click_action: Literal["open_app", "toggle_dictation"] = "open_app"

    # Theme mode (system/light/dark)
    # ``Literal[...]`` for static-type narrowing.
    theme_mode: Literal["system", "light", "dark"] = "system"
    # Theme preset — a built-in colour scheme applied on top of the
    # current theme_mode. "default" means no overrides.
    # ``Literal[...]`` enumerates the built-in presets.
    theme_preset: Literal[
        "default",
        "amoled",
        "nord",
        "dracula",
        "sepia",
        "solarized",
        "monokai",
        "ayu",
        "github",
        "catppuccin",
        "tokyo-night",
        "custom",
    ] = "default"
    # User-customised theme colours (only used when theme_preset == "custom").
    # Stored as nested dict: {"light": {var: val, ...}, "dark": {var: val, ...}}
    # parameterised the bare ``dict`` annotation so static checkers
    # can verify the nested structure that the renderer writes.
    custom_theme: dict[str, dict[str, str]] | None = None

    # Accessibility
    text_size: int = 14

    # Wayland hotkey fallback warning
    wayland_warned: bool = False

    # Silent mic disconnection (H12)
    silence_warning_seconds: float = 20.0
    stop_on_silence_seconds: float = 60.0
    #  SIMPLIFY-001: single explicit field replaces the previous 3-field split
    # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu, and
    # max_recording_time_seconds=0). The old GPU/CPU auto-selection was invisible
    # to users and the "0 = automatic" convention was user-hostile. Now the field
    # is always a concrete value with min 300 (5 min) / max 3600 (60 min).
    max_recording_time_seconds: int = 900  # 15 minutes

    # NOTE: dead_air_timeout (float) was REMOVED in
    # It was redundant with stop_on_silence_seconds — both called the same
    # on_silence_auto_stop callback. Auto-stop already resets on every speech
    # detection, so the "only after speech" condition dead air added was
    # unnecessary. Do NOT re-add. See RecordingSettingsSection.tsx comment.

    # silence_rms_threshold / silence_peak_threshold were REMOVED
    # from the Config dataclass — they were declared, validated, and
    # persisted, but never read by any runtime code path (ADR 0007 §4.3).
    # Existing config.json files that still carry these keys are silently
    # scrubbed by the v3 schema migration (``_migrate_to_v3``), so loading
    # an old config does NOT raise — the keys are simply dropped before
    # construction. Do NOT re-add.

    # Idle-unload timer for the active ASR backend. After this
    # many minutes with no dictation activity (no ``touch_active_model``
    # call from the transcription pipeline), ModelManager unloads the
    # active backend and calls ``release_gpu_memory()`` to return the
    # ~2.4 GB of VRAM held by Parakeet (or the Whisper weights / CUDA
    # caching allocator blocks) to the OS. The model is reloaded on the
    # next ``toggle_dictation`` via the existing
    # ``ensure_active_engine_loaded()`` lazy-init path.
    #
    # The default is 30 minutes — keeps the model warm for short
    # conversational gaps (sub-30-minute silences) while still
    # unloading it for genuinely long idle periods (lunch breaks,
    # meetings, overnight). This is the right tradeoff for the typical
    # tray-app usage pattern on laptops where GPU/CPU memory and ~5-15 W
    # of idle GPU power are worth reclaiming after a real "stepped away
    # from keyboard" gap. Users with abundant VRAM who want the model
    # resident for the lifetime of the process can set this to 0
    # (disables the feature — current "always loaded" behaviour is
    # preserved exactly). Cold-reload latency (2-5 s warm, 5-15 s cold)
    # is off the critical path of the next dictation because the
    # ``ensure_active_engine_loaded()`` reload path runs on
    # ``toggle_dictation`` before recording starts.
    # default bumped from 0 (disabled) to 30 minutes. The
    # idle-unload path arms a threading.Timer that calls
    # release_gpu_memory() after the configured idle period. On laptops
    # this frees GPU/CPU memory during long no-dictation periods (the
    # common case for a tray app). Users who need always-loaded behavior
    # (e.g. always-on desktop) can set this back to 0.
    model_idle_unload_minutes: int = 30

    # VAD configuration for the recording callback.
    # ADR 0007 §4.1: use_silero_vad defaults to True (torch is installed).
    # Falls back to RMS if Silero is unavailable.
    use_silero_vad: bool = True  # ADR 0007: was False, now True (torch available)
    vad_speech_threshold: float = 0.5  # Silero VAD prob > this → speech candidate
    vad_silence_threshold: float = 0.3  # Silero VAD prob < this → silence candidate
    # ER-42: auto-calibrate VAD thresholds from the ambient noise floor
    # during the first ~1.5s of each session (RMS path; Silero-prob path
    # when use_silero_vad is active). Consumed by VadProcessor
    # (vad_processor.py) via Recorder._vad_auto_calibrate. Was previously
    # read via getattr() fallback while unregistered here — the flag could
    # never be enabled, leaving the calibration feature dead.
    vad_auto_calibrate: bool = False

    # AUDIO-CH: number of channels to request from the input device.
    # Default 1 (mono) — appropriate for dictation. Set to 0 for
    # device default (auto-detect from device's max_input_channels).
    recording_channels: int = 1

    # AUDIO-PRE: pre-roll buffer captures audio before recording starts.
    # 0 = disabled (default, for privacy). When > 0, continuously
    # records N seconds of audio into a ring buffer and prepends it
    # when the user presses the hotkey, reducing cold-start latency.
    pre_roll_buffer_seconds: float = 0.0

    # ADR 0007 §5.2: normalize_audio and normalize_target_peak REMOVED.
    # Replaced by the Compressor filter in the audio filter chain.
    # the dataclass fields themselves were removed — they were
    # declared, validated, and persisted, but never read at runtime (the
    # Compressor filter supersedes them entirely). Existing config.json
    # files that still carry these keys are silently scrubbed by the v3
    # schema migration (``_migrate_to_v3``). Do NOT re-add.

    # ─── Volume ducking (v1.1.0) ────────────────────────────────────
    # Reduces system volume during dictation to prevent speaker output
    # from bleeding into the microphone.
    #
    # the Settings UI was simplified to just two controls:
    #   1. Auto Duck Volume (on/off)
    #   2. Duck Level (0–50%)
    # The remaining fields are internal (not exposed in the UI) and have
    # sensible defaults. They're kept in the config for backward compat
    # (existing user configs with custom values still load) and for
    # power users who edit config.json directly.
    volume_duck_enabled: bool = True
    volume_duck_level: float = 0.20  # 0.0–1.0 perceptual-linear (20% duck)
    #  ``volume_duck_per_session`` REMOVED from the Config
    # dataclass — ducking now always applies to the master volume
    # cross-platform. Existing config.json files that still carry the key
    # are silently scrubbed by the v3 schema migration. Do NOT re-add.
    # fade duration is now a fixed 200ms default (was 150ms).
    # Not exposed in the UI. Power users can override in config.json.
    volume_duck_fade_ms: int = 200  # 0–1000, 0 = instant
    #  ``volume_duck_smart`` REMOVED from the Config dataclass —
    # smart duck is now ALWAYS ON when ``volume_duck_enabled`` is True.
    # Existing config.json files that still carry the key are silently
    # scrubbed by the v3 schema migration. Do NOT re-add.
    # smart-duck poll interval is now a fixed 500ms default.
    # Not exposed in the UI. Power users can override in config.json.
    # the canonical default lives in
    # ``volume_ducker._DEFAULT_SMART_DUCK_POLL_MS``; imported here so
    # the dataclass default and the ``VolumeDucker`` constructor stay
    # in sync.
    volume_duck_smart_poll_interval_ms: int = _DEFAULT_SMART_DUCK_POLL_MS

    # ─── Audio enhancement preset (ADR 0007) ─────────────────────────
    # Preset name that controls the entire filter chain:
    #   "auto"        — all filters ON, RNNoise (best for 90% of users)
    #   "studio"      — minimal processing (quiet room, good mic)
    #   "noisy_room"  — aggressive, DeepFilterNet
    #   "off"         — all filters OFF
    #   "custom"      — user controls each filter individually
    # The preset is applied at startup (Config.load) and on explicit
    # set_config. See voice_typer/server/audio_presets.py for the
    # single source of truth.
    # ``Literal[...]`` includes legacy values
    # ("recommended", "none") so a stale config.json loaded BEFORE
    # the v2 migration renames them is still statically typed; the
    # migration then rewrites them to "auto"/"off".
    audio_preset: Literal[
        "auto",
        "studio",
        "noisy_room",
        "off",
        "custom",
        "none",
        "recommended",
    ] = "auto"

    # ─── Noise filtering (ADR 0007 — filter chain) ───────────────────
    # Each filter has an enable flag + parameters. The filter chain
    # (voice_typer/server/audio_filters/) is built from these fields
    # by audio_chain_builder.build_chain(). Chain order:
    #   HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter
    #
    #  ADR 0009: ``noise_filter_enabled`` and
    # ``noise_filter_post_capture`` are RUNTIME switches, NOT deprecated.
    # They are actively read by ``level_monitor.py`` and synced by
    # ``config_applier.py`` (which sets ``noise_filter_enabled =
    # audio_preset != "off"``). The legacy ``noise_filter_rnnoise`` field
    # is still kept for backward compat with old config.json files but is
    # migrated/ignored per ADR 0007 §5.
    noise_filter_enabled: bool = True  # runtime switch — see ADR 0009
    noise_filter_highpass: bool = True
    noise_filter_highpass_cutoff_hz: float = 80.0  # 20–500
    noise_filter_gate: bool = True
    # ``noise_filter_gate_threshold`` REMOVED from the Config
    # dataclass — replaced by the open/close threshold pair below per
    # ADR 0007. Existing config.json files that still carry the key are
    # silently scrubbed by the v3 schema migration. Do NOT re-add.
    noise_filter_gate_hold_ms: float = 200.0  # ADR 0007: was 150, now 200 (matches OBS)
    noise_filter_rnnoise: bool = True  # ADR 0007: was False, now True (RNNoise is default dep)
    noise_filter_post_capture: bool = True  # runtime switch — see ADR 0009

    # ADR 0007 §5.1: New filter chain fields
    # Noise suppressor backend selection.
    # ``Literal[...]`` matches ``NOISE_SUPPRESSION_METHODS``
    # in ``config_validators.py`` (the authoritative allowlist). The
    # historical ``"speex"`` option was never implemented — there is
    # no speex backend in ``audio_filters/noise_suppressor.py`` — and
    # is intentionally omitted so static type-checkers reject it.
    noise_suppression_method: Literal["rnnoise", "deepfilternet", "none"] = "rnnoise"

    # NoiseGate (OBS-style, replaces single threshold)
    noise_filter_gate_open_threshold_db: float = -26.0
    noise_filter_gate_close_threshold_db: float = -32.0
    noise_filter_gate_attack_ms: float = 25.0
    noise_filter_gate_release_ms: float = 150.0
    # when True, gate samples the first ~500ms of audio to estimate
    # the ambient noise floor and derives open/close thresholds from it.
    noise_filter_gate_adaptive: bool = False

    # Equalizer (3-band)
    noise_filter_eq: bool = True
    noise_filter_eq_low_db: float = -3.0
    noise_filter_eq_mid_db: float = 3.0
    noise_filter_eq_high_db: float = 2.0

    # Compressor (replaces normalize_audio + _agc_update)
    noise_filter_compressor: bool = True
    noise_filter_compressor_threshold_db: float = -18.0
    noise_filter_compressor_ratio: float = 3.0
    noise_filter_compressor_attack_ms: float = 6.0
    noise_filter_compressor_release_ms: float = 60.0
    noise_filter_compressor_output_gain_db: float = 0.0

    # Limiter (brick-wall)
    noise_filter_limiter: bool = True
    noise_filter_limiter_ceiling_db: float = -6.0
    noise_filter_limiter_release_ms: float = 60.0

    # Notch filter (50/60Hz hum) — optional, default OFF
    noise_filter_notch: bool = False
    noise_filter_notch_frequency_hz: float = 0.0  # 0 = auto-detect (60Hz Americas default)

    # ─── P4: AI grammar / punctuation / capitalization ─────────────
    # Rule-based, offline enhancement applied AFTER LLM polish and
    # BEFORE the result is stored to history / pasted.  See
    # ``voice_typer/server/ai_enhancement.py``.  The master toggle
    # defaults to OFF — the user must explicitly opt in via Settings
    # → AI Enhancement so existing users don't see behavior changes
    # after upgrading.  The three sub-toggles default to True so
    # that, once the master toggle is flipped, the feature "just
    # works" without further configuration.
    ai_enhancement_enabled: bool = False  # master toggle (opt-in)
    auto_capitalize: bool = True  # capitalize sentence starts + proper nouns
    auto_punctuate: bool = True  # add periods at sentence boundaries
    fix_grammar_basics: bool = True  # fix bare "i", contractions, double spaces

    # ─── P5: Vocabulary automation ─────────────────────────────────
    # Confidence-score-based auto-correction suggestions.  When the
    # master toggle is ON, the dictation pipeline analyzes each
    # transcription for low-confidence words and suggests vocabulary
    # corrections.  Suggestions above ``vocabulary_auto_apply_threshold``
    # are auto-applied; the rest are queued for the user to review.
    # Defaults to OFF — the user must explicitly opt in via Settings.
    vocabulary_automation_enabled: bool = False  # master toggle (opt-in)
    # Below this segment-confidence, suggest corrections.  0.7 is a
    # common Whisper "low confidence" threshold (the model emits
    # avg_logprob values around -1.0 for uncertain words; the
    # pipeline normalizes to a 0–1 confidence where 0.7 corresponds
    # to roughly avg_logprob -0.4).
    vocabulary_auto_confidence_threshold: float = 0.7
    # Above this confidence, auto-apply suggestions without asking.
    # 0.95 is high enough that false positives are rare but low
    # enough that the auto-apply path actually fires in practice.
    vocabulary_auto_apply_threshold: float = 0.95

    def __post_init__(self) -> None:
        """SCHEMA-1 (MED-I): initialize the transient ``last_load_warnings``
        attribute.

        ``last_load_warnings`` was previously a dataclass field (which
        meant ``asdict()`` serialized it into ``config.json`` and stale
        warnings were read back on the next load).  It's now a plain
        instance attribute so ``asdict()`` skips it.  We initialise it
        here so freshly-constructed ``Config()`` instances (e.g. the
        defaults fallback in :meth:`load`) have the attribute — callers
        that read ``instance.last_load_warnings`` get ``None`` instead
        of ``AttributeError``.
        """
        # Use object.__setattr__ to bypass any frozen/dataclass
        # machinery — Config is not frozen, but this is forward-
        # compatible if it ever is.
        object.__setattr__(self, "last_load_warnings", None)
        # cache the bytes of the last successfully-persisted
        # config.json. The next ``save()`` call compares its in-memory
        # serialized content against this cache; if they match, the
        # entire backup block (which reads ``config.json`` from disk
        # via ``Path.read_bytes`` and writes ``config.json.bak``) is
        # skipped. This avoids one filesystem read per identical resave
        # (which is the common case for ``set_config`` calls that
        # don't change any persisted field, and for ``heartbeat`` /
        # ``get_config``-style calls that round-trip through ``save``).
        object.__setattr__(self, "_last_saved_bytes", None)
        # Dirty flag — True when a persisted field has been
        # mutated since the last successful save (or since construction).
        # Checked at the TOP of ``_save_unlocked`` to short-circuit the
        # entire save (before the expensive ``asdict(self)`` +
        # ``json.dumps``) when nothing has changed. Set to True by the
        # ``__setattr__`` override on every user-facing field mutation;
        # set to False after a successful save. Using
        # ``object.__setattr__`` here so the ``__setattr__`` override
        # does not fire during ``__post_init__`` (which would set it
        # to True redundantly — harmless, but the explicit init makes
        # the intent clear).
        object.__setattr__(self, "_dirty", True)
        # Flag set to True by ``_save_unlocked`` after it
        # routes API-key fields through ``credential_store``. Readers
        # (e.g. ``config_applier.apply_config``) check this flag to
        # decide whether to run a redundant ``store_secret`` loop.
        # Default False (not yet routed); set True after routing.
        object.__setattr__(self, "_secrets_routed_in_save", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Track mutations to persisted dataclass fields via the
        ``_dirty`` flag.

        ``_dirty`` is set to True on every assignment to a persisted
        field (any attribute whose name does NOT start with ``_`` and
        is not the transient ``last_load_warnings`` attribute). Internal
        bookkeeping attributes (``_last_saved_bytes``, ``_dirty`` itself,
        ``_secrets_routed_in_save``, ``_mutation_lock``,
        ``last_load_warnings``) bypass the flag via ``object.__setattr__``
        at their call sites, so this override only fires for genuine
        user-facing field mutations (e.g. ``cfg.hotkey = "<f2>"`` or
        ``setattr(app.config, k, v)`` in ``apply_config``).

        The flag is checked at the top of ``_save_unlocked`` to skip
        the entire save (including ``asdict(self)`` + ``json.dumps``)
        when nothing has changed since the last successful save — the
        common case for ``set_config`` IPC round-trips that echo back
        the same config the server already has.
        """
        object.__setattr__(self, name, value)
        if not name.startswith("_") and name != "last_load_warnings":
            object.__setattr__(self, "_dirty", True)

    # class-level reference to an in-process mutation lock.
    # When set (via :meth:`set_mutation_lock`), :meth:`save` acquires
    # this lock around the actual save work (:meth:`_save_unlocked`)
    # so two threads concurrently mutating and saving the Config
    # produce a consistent on-disk snapshot rather than a torn
    # half-and-half write. ``ClassVar`` ensures ``asdict(self)``
    # skips it (an ``RLock`` is not JSON-serializable and would
    # crash save()). Defaults to ``None`` for backward-compat —
    # freshly-constructed ``Config()`` instances (e.g. tests)
    # save without locking.
    #
    # NOTE: the annotation is a STRING because ``threading.RLock`` is
    # a callable factory (not a type) at runtime, so
    # ``threading.RLock | None`` would raise TypeError when evaluated.
    _mutation_lock: ClassVar[Any] = None

    def set_mutation_lock(self, lock: "threading.RLock | None") -> None:
        """register an in-process mutation lock for ``save()``.

        ``VoiceTyperApp`` owns a ``self._config_mutation_lock =
        threading.RLock()`` that ``service.apply_config`` and
        ``onboarding_apply`` acquire for the full read-modify-save
        sequence. Calling this method installs the same lock on the
        ``Config`` instance so :meth:`save` acquires it automatically
        — making the lock impossible to forget at the 10+ other
        ``config.save()`` call sites (``settings_controller``,
        ``hotkey_dispatcher``, ``model_manager``, ``recorder._persist_mic``,
        ``startup_sequence``, etc.).

        The reference is stored as an INSTANCE attribute (shadowing
        the ``ClassVar`` default of ``None``) so each ``Config``
        instance can have its own lock — multiple ``VoiceTyperApp``
        instances in the same process (rare but possible in tests)
        don't share a single global lock.

        Passing ``None`` clears the lock (disables locking).
        """
        # Use the instance dict directly so the ClassVar is shadowed
        # per-instance (rather than mutating the class attribute, which
        # would leak across instances).
        self.__dict__["_mutation_lock"] = lock

    @classmethod
    def _warmup_keyring_probe(cls) -> None:
        """Eagerly probe ``credential_store.is_keyring_available()``
        once at app startup so the FIRST :meth:`save` call does not pay
        the ~164ms cold-probe cost (D-Bus / Keychain / Credential
        Manager round-trip on Linux / macOS / Windows respectively).

        The probe is idempotent: ``credential_store.is_keyring_available``
        caches its result at module level
        (``credential_store._keyring_available_cache``), so subsequent
        calls — including the first :meth:`save` — read the cached
        value in O(1). Calling this classmethod more than once is a
        no-op after the first call (the module-level
        ``_warmup_called`` flag records the first invocation; tests
        assert on it to verify the warmup was wired by the caller).

        Callers should invoke this once during app startup, ideally in
        ``VoiceTyperApp.__init__`` (or an equivalent early-init hook
        that runs BEFORE the first :meth:`Config.save` call — e.g.
        before ``Config.load()`` if load routes secrets). The probe
        touches the OS keyring backend and may take up to
        ``credential_store._KEYRING_TIMEOUT_SECONDS`` on a hung backend,
        so callers that need to avoid blocking the main thread should
        spawn a background thread::

            import threading
            threading.Thread(
                target=Config._warmup_keyring_probe,
                name="keyring-warmup",
                daemon=True,
            ).start()

        The probe is wrapped in ``credential_store.is_keyring_available``'s
        own broad ``except Exception`` (which catches D-Bus connection
        errors, missing pyobjc / pywin32, etc.) — this classmethod does
        NOT add its own try/except so a genuine import error in
        ``credential_store`` surfaces at the call site rather than
        being silently swallowed. The module-level ``_warmup_called``
        flag is set to True even if the probe itself returns False
        (keyring unavailable) — the WARMUP happened; the unavailability
        is the cached result, not a warmup failure.
        """
        global _warmup_called
        if _warmup_called:
            # Idempotent: a prior call already populated the
            # ``credential_store._keyring_available_cache``. Skip the
            # re-probe (which would be a no-op anyway thanks to the
            # cache, but the flag check avoids the function-call
            # overhead and the global-statement side effect).
            return
        from voice_typer.server import credential_store

        # Touch the probe — the result is cached inside
        # ``credential_store`` (``_keyring_available_cache``) for the
        # process lifetime (positive) or until the re-probe interval
        # (negative). The return value is intentionally ignored here:
        # the caller does not need to know whether keyring is
        # available; the cache is what matters.
        credential_store.is_keyring_available()
        _warmup_called = True

    def save(self) -> bool:
        """Save config to disk atomically via temp file + os.replace.

        Returns True on success, False on failure. Errors are logged but not raised.

        on POSIX, restricts file permissions to 0o600
        (owner-read/write only) and directory permissions to 0o700.
        Without this, default umask leaves config.json world-readable
        (0o644), leaking API keys and other settings to any
        co-located user.  On Windows the chmod is a no-op (NTFS ACLs
        are not affected by os.chmod, but the config dir is already
        under %APPDATA% which is per-user).

        uses os.open with O_NOFOLLOW on POSIX to prevent
        symlink TOCTOU attacks. A local attacker who pre-creates
        config.json as a symlink to ~/.bashrc would previously have
        their target overwritten via os.replace. O_NOFOLLOW refuses to
        follow symlinks on open, so the write fails instead.

        API key fields are routed through ``credential_store``
        before serialization. When a usable keyring backend is
        available, the secret is stored in the OS keychain and the
        on-disk field is replaced with a ``"keyring://<provider>"``
        reference token (so config.json contains no plaintext secrets).
        When keyring is unavailable, the plaintext value is written to
        config.json (with ``0o600`` perms via ``_secure_atomic_write``)
        — preserving the pre- behavior so users on headless
        Linux without ``gnome-keyring-daemon`` aren't blocked.

        when a mutation lock has been registered via
        :meth:`set_mutation_lock`, this method acquires it (reentrant
        ``RLock``) around the actual save work so concurrent
        read-modify-save cycles from different threads produce a
        consistent on-disk snapshot. Without the lock, a mic-fallback
        save on a background thread can interleave with an in-flight
        ``apply_config`` IPC call and persist a torn snapshot. When no
        lock is set (e.g. tests), saves proceed without locking —
        preserving backward compat.
        """
        # Windows-only, best-effort: tighten the config DIR's ACL when
        # this save CREATES the directory. We cannot run ``icacls <dir>
        # /inheritance:r`` on an existing dir — while ANY file in it is
        # held open (``config.json.lock`` during every save), the ACL
        # rewrite poisons the open file on Python < 3.11.13 (where
        # ``os.open`` lacks ``FILE_SHARE_DELETE`` on Windows), failing
        # every subsequent ``Config.save()`` in the process with
        # ``PermissionError`` (reproduced on 3.11.9 — the CI 3.11 leg
        # failed ~20 config tests); the same rewrite on a dir that
        # already contains files breaks writes to those files too. A
        # directory we just created is guaranteed empty, so the icacls
        # there is safe and every file created afterwards
        # (``config.json.lock``, the ``tempfile.mkstemp`` tmp,
        # ``config.json``, ``config.json.bak``) inherits the owner-only
        # dir DACL. Note this is narrow belt-and-suspenders: in the
        # normal flow the config dir is created by logging/history init
        # BEFORE the first save (and per-user ``%APPDATA%`` is
        # owner-only by default anyway) — the meaningful hardening is
        # the per-file icacls on ``config.json`` / ``config.json.bak``
        # in ``_save_unlocked``. Guarded by the same dirty-flag
        # short-circuit as ``_save_unlocked`` so no-op saves skip it;
        # the broad catch keeps ``save()``'s never-raises contract even
        # if ``_config_dir()`` raises in an edge scenario.
        if is_windows() and (self._dirty or self._last_saved_bytes is None):
            try:
                config_dir = _config_dir()
                config_dir.mkdir(parents=True, exist_ok=True)
                # Tighten the config DIR's ACL on the FIRST save of this
                # process, whether or not this call created the dir
                # (Config.__init__ may already have created it, or a
                # prior run left it behind). The path is only cached
                # when the dir-wide icacls SUCCEEDS, so a dir that can't
                # be tightened keeps per-file enforcement. Once verified,
                # every file created by mkstemp inside the dir inherits
                # the owner-only DACL, so the per-file icacls calls in
                # _save_unlocked become cheap no-ops (see
                # _enforce_windows_owner_only_acl fast path). Skipping
                # re-verification avoids re-running dir-wide icacls on
                # an existing dir.
                if (
                    str(config_dir) not in _windows_owner_only_acl_verified
                    and _enforce_windows_owner_only_acl(config_dir)
                ):
                    _windows_owner_only_acl_verified.add(str(config_dir))
            except Exception:
                pass
        try:
            with _acquire_config_lock():
                return self._save_with_mutation_lock()
        except TimeoutError as e:
            log.warning("[CONFIG] %s", e)
            return False
        except (OSError, PermissionError) as e:
            log.error("[CONFIG] Failed to save config: %s", e)
            return False
        except (TypeError, ValueError) as e:
            # ``json.dumps`` (called inside
            # :meth:`_save_unlocked` via ``asdict(self)``) can raise
            # ``TypeError`` when a field holds a non-JSON-serializable
            # value (e.g. a ``set`` / ``datetime`` / custom object
            # smuggled in via ``setattr`` or a botched migration), and
            # ``ValueError`` for circular references. The previous
            # ``except`` tuple only caught ``TimeoutError`` /
            # ``OSError`` / ``PermissionError`` — a ``TypeError``
            # propagated to the caller, violating the ``save()``
            # docstring's "never raises" contract (which the IPC
            # ``set_config`` path relies on: a ``TypeError`` would
            # crash the IPC handler thread instead of returning a
            # ``False`` ack the renderer can surface as a save-failed
            # toast). Widen the tuple to include both serialization
            # failure modes and return ``False`` (the underlying
            # ``OSError``/``TypeError`` is logged at ERROR so the
            # operator can diagnose which field is non-serializable).
            log.error("[CONFIG] Failed to serialize config for save: %s", e)
            return False

    def _save_with_mutation_lock(self) -> bool:
        """acquire the mutation lock (if set) and delegate to
        :meth:`_save_unlocked`.

        Assumes the cross-process file lock is already held (caller
        :meth:`save` acquires it). The mutation lock is an in-process
        ``RLock`` that serialises concurrent ``save()`` calls from
        different threads within THIS process (the cross-process file
        lock only serialises across processes).
        """
        lock = self._mutation_lock
        if lock is None:
            return self._save_unlocked()
        with lock:
            return self._save_unlocked()

    def _save_unlocked(self) -> bool:
        """Body of :meth:`save` -- assumes the cross-process lock AND
        the in-process mutation lock (if set) are held.

        best-effort single-slot backup of the existing
        config.json BEFORE we overwrite it.  The backup preserves
        the EXACT bytes that were on disk (byte-for-byte) so the user
        can manually recover dropped fields after a downgrade save.

        when the in-memory serialized content matches the
        previously-persisted bytes (``_last_saved_bytes``), the entire
        backup block is skipped — no ``Path.read_bytes`` call, no
        ``config.json.bak`` write, no ``os.chmod``.  This is the common
        case for ``set_config`` round-trips that don't change any
        persisted field.

        A ``_dirty`` flag (set True by ``__setattr__`` on every
        persisted-field mutation, set False after a successful save)
        is checked at the TOP of this method. When False AND
        ``_last_saved_bytes`` is populated, the entire save is
        short-circuited BEFORE the expensive ``asdict(self)`` +
        ``json.dumps`` calls — the common case for back-to-back
        ``save()`` calls with no intervening mutation (e.g. a
        ``set_config`` IPC round-trip whose ``updates`` dict was a
        no-op after the per-key dirty-check in ``apply_config``).
        """
        # Dirty-flag short-circuit. If no persisted field has
        # been mutated since the last successful save (and we have in
        # fact saved at least once), there is nothing to do — skip the
        # entire save including ``asdict(self)`` + ``json.dumps`` +
        # ``_secure_atomic_write`` + ``.bak`` write. The ``_dirty`` flag
        # is set True by ``__setattr__`` on every persisted-field
        # mutation and set False at the bottom of this method after a
        # successful write. The ``_last_saved_bytes is not None`` guard
        # ensures a fresh ``Config()`` (which has ``_dirty=True`` from
        # ``__post_init__``) always falls through to the real write on
        # its first save — even if ``_dirty`` were manually cleared,
        # the cache would still be ``None`` and the guard below would
        # fall through. Belt-and-suspenders.
        if not self._dirty and self._last_saved_bytes is not None:
            return True
        path = _config_dir()
        path.mkdir(parents=True, exist_ok=True)
        if not is_windows():
            try:
                os.chmod(path, 0o700)
            except OSError as e:
                log.warning("[CONFIG] Failed to chmod config dir: %s", e)
        # The config DIR's ACL is tightened in ``save()`` BEFORE the
        # cross-process lock is acquired, NOT here — this method is
        # always called with ``config.json.lock`` held open, and
        # running ``icacls <dir> /inheritance:r`` while the lock file
        # is open poisons it on Python < 3.11.13 (``os.open`` lacks
        # ``FILE_SHARE_DELETE`` on Windows), failing every subsequent
        # save() in the process. The secret-holding files
        # (``config.json`` and ``config.json.bak``) are tightened
        # individually after each write below.
        config_file = path / "config.json"
        data = asdict(self)
        # Reset the ``_secrets_routed_in_save`` flag at the
        # start of the routing block. Set to True below ONLY if the
        # routing try-block completes (whether keyring was available
        # or not — the routing was "attempted" and the secret is
        # either in keyring or persisted as plaintext in config.json
        # by the final ``_secure_atomic_write``). Readers
        # (``config_applier.apply_config``) check this flag to decide
        # whether to run a redundant ``store_secret`` loop after
        # ``save_strict`` succeeds; the loop only runs when routing
        # did NOT happen (e.g. ``Config.save`` was mocked to skip
        # routing in a test).
        object.__setattr__(self, "_secrets_routed_in_save", False)
        # route API key fields through credential_store.
        try:
            from voice_typer.server import credential_store

            if credential_store.is_keyring_available():
                for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
                    value = data.get(field_name, "")
                    # defensive type guard for non-string api_key
                    # values. ``asdict(self)`` reflects whatever the
                    # in-memory Config instance carries — normally a
                    # str (the dataclass field type) but a buggy IPC
                    # caller or a monkeypatched test instance could
                    # set a non-string value, which would crash here
                    # with AttributeError on ``.startswith()`` (and
                    # propagate up through ``Config.save``'s outer
                    # ``except Exception``, logging a warning and
                    # aborting the entire save).
                    #
                    # Coerce int/float (excluding bool, which is a
                    # subclass of int in Python) to str — backward
                    # compat with old configs that stored api_key as
                    # an int. Skip other non-string truthy types
                    # (dict, list) with a warning so the save can
                    # proceed for the remaining providers.
                    if not isinstance(value, str):
                        if not value:
                            # Falsy (None, 0, [], {}, "") — nothing
                            # to route to credential_store.
                            continue
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            log.warning(
                                "[CONFIG] DE-23: %s field has non-string value (type=%s) — coercing to str",
                                field_name,
                                type(value).__name__,
                            )
                            value = str(value)
                            data[field_name] = value
                        else:
                            log.warning(
                                "[CONFIG] DE-23: %s field has non-string value (type=%s)"
                                " — skipping credential_store routing",
                                field_name,
                                type(value).__name__,
                            )
                            continue
                    if value and not value.startswith(credential_store.KEYRING_REF_PREFIX):
                        # pass ``_caller_holds_config_lock=True``
                        # so ``store_secret`` → ``_write_plaintext_fallback`` does
                        # NOT re-acquire ``config.json.lock`` (which would deadlock
                        # — fcntl.flock is per-open-file-description on Linux, so a
                        # second ``open()`` + ``flock(LOCK_EX | LOCK_NB)`` on the
                        # same lock file from THIS process fails with EWOULDBLOCK
                        # and spins until the 5s ``_CONFIG_LOCK_TIMEOUT_SECONDS``
                        # deadline, then raises TimeoutError — pre-fix, that was
                        # caught by ``_write_plaintext_fallback``'s broad
                        # ``except Exception`` and logged at ERROR, silently
                        # dropping the user's API key when keyring failed mid-save).
                        # We're inside ``_save_unlocked`` (caller
                        # :meth:`save` acquired the lock at line ~1118 via
                        # ``with _acquire_config_lock():``), so the lock IS held.
                        # Check the return value: store_secret returns False when
                        # keyring was probed "available" but set_password transiently
                        # fails and falls back to _write_plaintext_fallback. In that
                        # case, leave data[field_name] as the plaintext value so the
                        # final _secure_atomic_write below persists it in one write —
                        # simultaneously (a) eliminating redundant per-provider
                        # read-modify-write cycles and (b) preserving the secret on
                        # disk (previously the reference token overwrite caused silent
                        # API-key data loss when keyring flaked mid-save).
                        stored_to_keyring = credential_store.store_secret(
                            provider, value, _caller_holds_config_lock=True
                        )
                        if stored_to_keyring:
                            data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"
                        # else: leave data[field_name] as the plaintext value —
                        # the final _secure_atomic_write will persist it.
            # Routing was attempted (keyring available OR not —
            # if not available, the plaintext value is persisted by
            # the final ``_secure_atomic_write`` below, which is the
            # equivalent "routing" for the no-keyring path). Signal
            # ``config_applier.apply_config`` that its redundant
            # ``store_secret`` loop can be skipped.
            object.__setattr__(self, "_secrets_routed_in_save", True)
        except Exception as e:
            # log only the exception TYPE (not the message) —
            # credential_store exceptions can echo the secret value
            # being stored, which would leak into log files.
            log.warning(
                "[CONFIG] credential_store routing failed: %s — writing config with current api_key values",
                type(e).__name__,
            )
            # Leave ``_secrets_routed_in_save`` at False (set
            # above before the try-block) so ``apply_config``'s
            # redundant ``store_secret`` loop runs as a safety net.
        content = json.dumps(data, indent=2)
        content_bytes = content.encode("utf-8")

        # Skip the write ENTIRELY when the new content matches the
        # previously-persisted bytes. The ``_last_saved_bytes`` cache
        # is populated after each successful ``save()`` (line ~1546
        # below) and is ``None`` on a fresh ``Config()`` instance
        # (set in ``__post_init__``). When the cache is populated and
        # the new ``content_bytes`` match it, there is nothing to do —
        # the on-disk file already has the exact bytes we would write.
        # This mirrors the ``PersistedJSON._last_written_bytes`` pattern
        # in ``secure_file_io.py:541-549`` (load → cache, save → diff
        # → skip). The common case is a ``set_config`` IPC round-trip
        # that doesn't change any persisted field (e.g. the renderer
        # echoes back the same config the server already has): without
        # this skip, every such call paid the full ``_secure_atomic_write``
        # cost (temp file, ``os.replace``, optional fsync) plus the
        # ``config.json.bak`` backup read+write — pure I/O churn for an
        # identical result. The ``is not None`` guard ensures a fresh
        # instance (cache never populated) always falls through to the
        # real write, so the first save after construction/load is
        # never skipped.
        #
        # Note: the ``_dirty`` short-circuit at the top of this
        # method already handles the common case (no mutation since
        # last save). This byte-level check is a SECOND layer of
        # defense: it catches the rare case where ``_dirty`` is True
        # (a field was mutated) but the mutation is a no-op (e.g.
        # ``cfg.hotkey = cfg.hotkey``) or the field was mutated and
        # then mutated back. Without this check, those no-op
        # mutations would trigger a full write unnecessarily.
        if self._last_saved_bytes is not None and self._last_saved_bytes == content_bytes:
            # Clear the dirty flag here too — the content
            # matches what's on disk, so the in-memory state is
            # effectively "clean" relative to disk.
            object.__setattr__(self, "_dirty", False)
            return True

        # Short-circuit the entire backup block when the new
        # content matches the previously-persisted bytes. The cached
        # bytes are only updated after a successful write below, so a
        # previous failed save (or a fresh Config() that has never
        # saved) falls through to the full backup path.
        #
        # When ``_last_saved_bytes`` is populated, use it directly as
        # ``existing_bytes`` instead of re-reading ``config.json`` via
        # ``_secure_read_text``. The cache reflects the exact bytes we
        # wrote on the last successful save, which (barring external
        # modification) equals the current on-disk content. This skips
        # one filesystem read (the ``_secure_read_text`` open + read +
        # inode-verify) per modified save — the .bak WRITE still
        # happens (the content has changed, so the backup is needed),
        # but the READ is eliminated. The ``_secure_read_text`` path
        # is retained as a fallback for the first save (cache is
        # ``None``) so the symlink-TOCTOU-safe read is still used
        # when we have no cached bytes to compare against.
        if self._last_saved_bytes != content_bytes and config_file.exists():
            # best-effort backup before overwrite.
            try:
                if self._last_saved_bytes is not None:
                    # Use the cached bytes from the last
                    # successful save. This is the bytes-identical
                    # content we wrote last time; barring external
                    # modification it equals the current on-disk
                    # content. Skips the ``_secure_read_text`` open +
                    # read + inode-verify.
                    existing_bytes = self._last_saved_bytes
                    existing_text = existing_bytes.decode("utf-8")
                else:
                    # Fallback: first save (cache is None) —
                    # read the existing config.json via
                    # ``_secure_read_text`` (O_NOFOLLOW + inode
                    # re-verify) instead of ``config_file.read_bytes()``
                    # which calls ``open()`` internally and FOLLOWS
                    # SYMLINKS. A local attacker who replaces
                    # config.json with a symlink to ~/.bashrc between
                    # saves would otherwise get ~/.bashrc content
                    # copied into config.json.bak (info disclosure via
                    # the .bak). The subsequent ``_secure_atomic_write``
                    # uses ``os.replace`` which replaces the SYMLINK
                    # itself (safe), so the actual config.json write
                    # is fine — but the .bak was already poisoned.
                    existing_text = _secure_read_text(config_file)
                    existing_bytes = existing_text.encode("utf-8")
                if existing_bytes != content_bytes:
                    bak_path = path / "config.json.bak"
                    #  (cont.): also route the .bak WRITE through
                    # ``_secure_atomic_write`` so the destination path
                    # is created with O_NOFOLLOW (no symlink-following
                    # on the destination either) + fsync + 0o600 perms.
                    _secure_atomic_write(bak_path, existing_text)
                    if not is_windows():
                        try:
                            os.chmod(bak_path, 0o600)
                        except OSError as e:
                            log.debug("[CONFIG] Failed to chmod config.json.bak: %s", e)
                    else:
                        # enforce owner-only ACL on the
                        # backup file on Windows — it contains the
                        # same plaintext API keys as config.json.
                        _enforce_windows_owner_only_acl(bak_path)
            except (OSError, ValueError) as e:
                # OSError covers filesystem errors; ValueError covers
                # the SEC-002 inode-changed-during-read guard (symlink
                # TOCTOU detection). Both are best-effort failures —
                # the actual config.json write (below) still proceeds.
                log.debug(
                    "[CONFIG] Failed to back up existing config.json to config.json.bak: %s",
                    e,
                )

        _secure_atomic_write(config_file, content)
        if is_windows():
            # ``_secure_atomic_write`` creates the temp
            # file via ``tempfile.mkstemp``, which on Windows
            # inherits the parent dir's DACL. If the config dir is
            # shared, ``config.json`` (with plaintext API keys when
            # keyring is unavailable) becomes world-readable. Re-tighten
            # the ACL on the destination after the rename.
            _enforce_windows_owner_only_acl(config_file)
        # record the bytes we just persisted so the next
        # identical save can short-circuit the backup block above.
        # Updated only AFTER a successful write — a failed write
        # leaves the cache stale, which forces the next save through
        # the full backup path (safe-but-slower fallback).
        object.__setattr__(self, "_last_saved_bytes", content_bytes)
        # Clear the dirty flag — the in-memory state now
        # matches the on-disk state. The next ``save()`` call (with no
        # intervening mutation) will short-circuit at the top of this
        # method via the ``not self._dirty`` check.
        object.__setattr__(self, "_dirty", False)
        return True

    #  back-compat alias: the original pre-refactor name was
    # ``_save_locked`` (referring to the cross-process file lock).
    # Kept as an alias so any external callers / tests that still
    # reference the old name continue to work.
    _save_locked = _save_unlocked

    def save_strict(self) -> None:
        """PERSIST-1 (MED-N): save config to disk; raise on failure.

        Wraps :meth:`save` and raises :class:`RuntimeError` if the
        underlying save returned ``False`` (which indicates an
        ``OSError`` or ``PermissionError`` was caught and logged by
        ``save()``).  Callers who care about persistence — i.e. IPC
        handlers that return an ``ack`` to the renderer only when the
        config actually landed on disk — should call this instead of
        ``save()`` so a silent disk failure is surfaced as an IPC error
        rather than a successful-but-empty ack.

        The error message is intentionally generic (it does NOT embed
        the underlying ``OSError`` message) because the renderer may
        display the error string to the user — the underlying message
        could contain a filesystem path that we don't want to leak
        across the IPC boundary.  ``save()`` already logs the full
        error message on the server side.

         wiring: ``apply_config`` (in ``config_applier.py``) and
        ``reset_config_to_defaults`` (in ``service/config_service.py``)
        both call ``save_strict()`` so a silent disk failure is
        surfaced as an IPC error rather than a successful-but-empty
        ack. The "follow-up task" note in earlier revisions of this
        docstring is now stale ().
        """
        ok = self.save()
        if not ok:
            raise RuntimeError("failed to persist config to disk")

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults.

        failure-mode enumeration.  The previous implementation
        caught ``Exception`` and silently returned defaults — that hid
        genuine bugs (e.g. ``KeyError`` from a missing ``data[key]``
        access, or ``AttributeError`` from an unexpected ``None``) and
        system-level failures (``MemoryError``).  We now enumerate the
        *expected* failure modes that indicate a corrupt or unreadable
        config file and fall back to defaults with a WARNING log so
        the user can see *why* their settings were reset.

        Caught (fall back to defaults + WARNING log):

        * ``OSError`` (incl. ``PermissionError``) — file missing,
          locked, or unreadable.
        * ``json.JSONDecodeError`` — corrupt JSON syntax.
        * ``TypeError`` — parsed JSON is not a dict (e.g. ``[]`` or
          ``42``), or a field has an uncoercible type (e.g. ``null``
          for a float field).
        * ``ValueError`` — a field has the right type but an invalid
          string form (e.g. ``"abc"`` for ``float()``).

        Propagated (NOT caught — indicates a bug in our code or a
        system-level failure that should not be silently hidden):

        * ``KeyError`` — we use ``.get()`` everywhere; a ``KeyError``
          means someone introduced a ``data[...]`` access without a
          default.  Surface it as a bug.
        * ``AttributeError`` — same reasoning; an unexpected ``None``
          where a dict was assumed.
        * ``MemoryError`` / ``KeyboardInterrupt`` / ``SystemExit`` —
          system-level, never silently swallowed.

        The 556-line body was split into 10 named helpers (see
        ``_read_raw_json`` / ``_filter_unknown_keys`` /
        ``_run_migrations`` / ``_backup_before_migration`` /
        ``_coerce_streaming_fields`` / ``_coerce_max_recording_time`` /
        ``_validate_model_path`` / ``_validate_qwen_model_path`` /
        ``_validate_corrections_path`` / ``_validate_privacy_consents``).
        ``load()`` is now a ~50-line orchestrator that delegates to
        those helpers; behavior is preserved verbatim (all comments +
        control flow migrated unchanged into the helpers).
        """
        return _load_config(cls)

    # ── ``load()`` helpers (extracted from the original 556-line body) ──

    @classmethod
    def _read_raw_json(cls, config_file) -> dict | None:
        """Read + parse ``config_file`` as JSON; return the parsed dict (or None).

        Extracted verbatim from ``load()``. Uses
        :func:`_secure_read_text` (SEC-002 / SEC-audit-011) to prevent
        symlink-TOCTOU attacks when reading ``config.json``.

        Returns ``None`` if the parsed JSON is not a dict (a valid JSON
        scalar like ``null`` / ``true`` / ``42`` / ``"x"`` / ``[]`` is
        not a valid config). The caller raises ``TypeError`` so the
        outer ``except`` in ``load()`` catches it, logs a WARNING, and
        moves the corrupt file aside.
        """
        return _read_raw_json_impl(config_file)

    @classmethod
    def _filter_unknown_keys(cls, parsed: dict, config_file) -> dict:
        """Filter unknown keys from ``parsed``; log a WARNING for each dropped key.

        Extracted verbatim from ``load()``. : log a WARNING
        if the on-disk config contains keys this build doesn't recognize.
        These keys are silently dropped by the filter.
        """
        return _filter_unknown_keys_impl(cls, parsed, config_file)

    @classmethod
    def _run_migrations(
        cls,
        data: dict[str, Any],
        loaded_version: Any,
        config_file,
    ) -> tuple[dict[str, Any], int, bool]:
        """M3: run forward schema migrations from ``loaded_version`` to ``_CURRENT_SCHEMA_VERSION``.

        Thin delegate to the module-level
        :func:`voice_typer.server.config_internals.migrations._run_migrations`
        (extracted in  / ).  See that function for the full
         fail-soft semantics (do NOT bump schema_version on
        migrator exception; leave it at ``last_successful_version`` so
        the failed migration re-runs on next launch).
        """
        return _run_migrations(data, loaded_version, config_file)

    @classmethod
    def _backup_before_migration(cls, config_file, loaded_version: Any) -> None:
        """Best-effort backup of ``config.json`` BEFORE any migration runs.

        S5-implementation extracted to
        :func:`voice_typer.server.config_internals.migrations._backup_before_migration_impl`
        to chip away at this module's monolith. This classmethod is now
        a thin delegating wrapper so existing callers (and tests that
        call ``Config._backup_before_migration(config_file, 0)``
        directly) keep working unchanged. The wrapper also preserves the
        test-patch surface: tests that monkeypatch
        ``config_mod._secure_read_text`` /
        ``config_mod._secure_atomic_write`` /
        ``config_mod._prune_kept_backups`` keep taking effect because
        the impl function looks those up via the ``config`` module
        namespace (lazy import).

        See the impl function's docstring for the full rationale
        (symlink-TOCTOU-safe read, atomic write, timestamped filename,
        retention cap of 3).
        """
        _backup_before_migration_impl(config_file, loaded_version)

    @classmethod
    def _backup_before_downgrade(
        cls,
        config_file,
        loaded_version: Any,
        data: dict[str, Any],
    ) -> None:
        """best-effort versioned backup when an older build loads a
        newer-version config.

        Called from :meth:`load` ONLY when ``loaded_version >
        _CURRENT_SCHEMA_VERSION`` (i.e. the user ran a newer build of
        Voice Typer and then downgraded). The in-memory ``data`` dict
        already has the higher-version fields filtered out by
        :meth:`_filter_unknown_keys`; without a backup, the next
        :meth:`save` would atomically overwrite the on-disk file with a
        config that has the higher version number but is missing the
        higher-version fields — silently destroying the user's data.

        This method copies the on-disk ``config.json`` (NOT the in-memory
        ``data`` — the on-disk bytes still have all the higher-version
        fields) to a timestamped ``config.json.v{loaded_version}-{ts}-{pid}-{ns}.bak``
        so two backup events never collide.

        previously the filename was single-slot
        ``config.json.v{loaded_version}.bak`` (no timestamp/PID) and
        ``_backup_before_downgrade`` was called UNCONDITIONALLY on every
        load meeting the version condition. After the first downgrade
        load (backup captures original high-version config), any
        ``Config.save()`` writes the degraded config (schema_version=N
        but MISSING all v{N} fields) to ``config.json``. On next
        restart, ``load()`` sees ``loaded_version=N > current``, calls
        ``_backup_before_downgrade`` AGAIN, reads the DEGRADED on-disk
        file, and overwrites ``config.json.v{N}.bak`` with degraded
        content — destroying the original v{N} fields. The fix mirrors
        ``_backup_before_migration``: embed timestamp + PID +
        sub-second nanoseconds in the filename and prune to keep=3 so
        the original high-version backup survives subsequent degraded
        loads.

        Also appends a non-blocking warning to ``data["_load_warnings"]``
        so the renderer can surface it via ``last_load_warnings`` — the
        user gets an honest signal that they ran an older build against
        a newer config and that a backup was created at a specific path.

        Best-effort: if the copy fails (read-only filesystem, out of
        disk, etc.) the warning is logged at WARNING level so the
        operator can investigate. The load itself is NOT aborted — the
        user can still use the app with the older build's known fields.
        """
        if not isinstance(loaded_version, int):
            return
        # embed schema version + epoch seconds + PID +
        # sub-second nanoseconds in the filename so two backup events
        # never collide (even within the same second from different
        # processes — e.g. two app instances launched in parallel
        # against the same user account during a downgrade). Mirrors
        # ``_backup_before_migration`` at line 1879.
        ts_sec = int(time.time())
        pid = os.getpid()
        ts_ns = time.time_ns() % 1_000_000
        versioned_bak = config_file.parent / f"config.json.v{loaded_version}-{ts_sec}-{pid}-{ts_ns}.bak"
        # use the secure read/write helpers (O_NOFOLLOW + atomic
        # os.replace + fsync + 0o600) instead of ``shutil.copy2``.
        # ``shutil.copy2`` is (a) non-atomic (file-by-file copy — an
        # interrupted copy leaves a partial .bak that gives a false
        # sense of recoverability), (b) follows symlinks on both SOURCE
        # and DEST (a local attacker who replaces config.json with a
        # symlink to ~/.bashrc between the user's downgrade-launch and
        # the copy2 call gets ~/.bashrc content copied into the .bak —
        # info disclosure via the .bak file), (c) no fsync (the .bak
        # may not be durable across power loss). Mirrors the
        # fix prescribed for ``_backup_before_migration``.
        try:
            raw_text = _secure_read_text(config_file)
            _secure_atomic_write(versioned_bak, raw_text)
            log.warning(
                "[CONFIG] downgraded build loaded newer config schema_version=%d "
                "(supported=%d); backed up original to %s before any save can overwrite",
                loaded_version,
                _CURRENT_SCHEMA_VERSION,
                versioned_bak,
            )
            data.setdefault("_load_warnings", []).append(
                f"Config file schema_version={loaded_version} is newer than this build "
                f"supports ({_CURRENT_SCHEMA_VERSION}). Unknown fields were dropped from "
                f"the in-memory config. The original file was backed up to "
                f"{versioned_bak.name} before any save can overwrite it — restore this "
                f"file manually after upgrading to a newer build."
            )
        except (OSError, ValueError) as e:
            # OSError covers filesystem errors (read-only fs, out of
            # disk, permission denied); ValueError covers the
            # SEC-002 inode-changed-during-read guard (symlink TOCTOU
            # detection). Both are best-effort failures — the load
            # itself is NOT aborted.
            log.warning(
                "[CONFIG] failed to back up newer-version config to %s before downgrade save: %s",
                versioned_bak,
                e,
            )
            data.setdefault("_load_warnings", []).append(
                f"Config file schema_version={loaded_version} is newer than this build "
                f"supports ({_CURRENT_SCHEMA_VERSION}). Unknown fields were dropped. "
                f"WARNING: backup of the original file failed ({e}) — downgrading and "
                f"saving will irrecoverably lose the higher-version fields."
            )
            return
        # cap retained versioned-downgrade backups to 3
        # (oldest pruned) so the directory doesn't grow unbounded
        # across many version bumps + restart cycles. Mirrors the
        # ``_backup_before_migration`` prune call. The prefix
        # ``config.json.v`` matches BOTH the old single-slot
        # ``config.json.v<N>.bak`` (kept for backward-compat with
        # existing on-disk backups from pre- builds) AND the new
        # timestamped ``config.json.v<N>-<ts>-<pid>-<ns>.bak``.
        try:
            _prune_kept_backups(
                config_file.parent,
                prefix="config.json.v",
                keep=3,
            )
        except OSError as prune_exc:
            log.debug(
                "[CONFIG] failed to prune old versioned-downgrade backups: %s",
                prune_exc,
            )

    @classmethod
    def _coerce_streaming_fields(cls, data: dict[str, Any]) -> None:
        """Coerce streaming_* fields with min/max clamping + invariant checks.

        Delegates to :func:`voice_typer.server.config.coercion._coerce_streaming_fields`.
        See that function for the full rationale (VALID-1 / MED-K per-field
        try/except, AudioWindowPlanner invariants, load-time reset + warning
        instead of silent clamp).
        """
        _coerce_streaming_fields(data)

    @classmethod
    def _coerce_max_recording_time(cls, data: dict[str, Any]) -> None:
        """Clamp ``max_recording_time_seconds`` to valid range [300, 3600].

        Delegates to :func:`voice_typer.server.config.coercion._coerce_max_recording_time`.
        """
        _coerce_max_recording_time(data)

    @classmethod
    def _validate_model_path(cls, data: dict[str, Any]) -> None:
        """Validate ``model_size`` against :data:`ALLOWED_USER_MODELS`.

        Delegates to :func:`voice_typer.server.config.coercion._validate_model_path`.
        """
        _validate_model_path(data)

    @classmethod
    def _validate_qwen_model_path(cls, data: dict[str, Any]) -> None:
        """Validate ``qwen_model_path``: must be an existing directory if set.

        Delegates to :func:`voice_typer.server.config.coercion._validate_qwen_model_path`.
        """
        _validate_qwen_model_path(data)

    @classmethod
    def _validate_corrections_path(cls, data: dict[str, Any]) -> None:
        """Validate ``corrections_path``: must be an existing file if set.

        Delegates to :func:`voice_typer.server.config.coercion._validate_corrections_path`.
        """
        _validate_corrections_path(data)

    @classmethod
    def _validate_privacy_consents(cls, data: dict[str, Any]) -> None:
        """Warn the user about privacy implications when ``log_transcriptions`` is enabled.

        Delegates to :func:`voice_typer.server.config.coercion._validate_privacy_consents`.
        """
        _validate_privacy_consents(data)

    @classmethod
    def _derive_field_type_registry(cls: type["Config"]) -> dict[str, type]:
        """Build a ``{field_name: expected_type}`` registry from the Config dataclass.

        Delegates to
        :func:`voice_typer.server.config.sanitization._derive_field_type_registry`.
        """
        return _sanitization_derive_field_type_registry(cls)

    # High-impact ``Literal[...]`` enum fields whose invalid values
    # should be reset to defaults on load (rather than merely warned
    # about). These are the user-facing enum choices that drive
    # discrete runtime behavior branches (ASR backend selection,
    # recording mode, bubble placement, tray click action, theme,
    # audio preset, noise suppressor). An invalid value here causes
    # downstream code to either crash (KeyError in a dispatch dict) or
    # silently take the wrong branch (a stale ``"speex"`` value for
    # ``noise_suppression_method`` would fall through the
    # ``noise_suppressor.py`` dispatch and produce no filter at all).
    #
    # The set is intentionally a hardcoded allowlist rather than
    # "every Literal field on Config" — ``audio_preset`` is the one
    # Literal field whose Literal ALSO includes the legacy
    # ``"none"`` / ``"recommended"`` values (kept for static-typing
    # backward-compat with pre-migration config.json). The migration
    # already rewrites those before this reset runs, but to be safe we
    # only reset fields explicitly in this list and rely on the
    # Literal's own allowed-values set for the truth — so if
    # ``audio_preset="none"`` somehow survives migration, this reset
    # will NOT touch it (the migration handles it; touching it here
    # would mask a migration bug).
    _ENUM_FIELDS_TO_RESET_ON_LOAD: ClassVar[frozenset[str]] = frozenset(
        {
            "asr_backend",
            "noise_suppression_method",
            "audio_preset",
            "theme_mode",
            "theme_preset",
            "bubble_position",
            "bubble_behavior",
            "tray_left_click_action",
            "recording_mode",
        }
    )

    @classmethod
    def _reset_invalid_enum_fields(cls, instance: "Config") -> None:
        """Reset invalid ``Literal[...]`` enum fields to their defaults.

        ``validate_config(instance)`` (called from :meth:`load` just
        before this helper) flags invalid enum values and appends
        human-readable errors to ``instance.last_load_warnings``, but
        it does NOT mutate the field — the invalid value remains on
        the instance and propagates to runtime code, which either
        crashes (KeyError in a dispatch dict) or silently takes the
        wrong branch.

        This helper closes that gap. For each field in
        :data:`_ENUM_FIELDS_TO_RESET_ON_LOAD`:

        1. Look up the field's ``Literal[...]`` annotation via
           :func:`typing.get_type_hints`.
        2. Read the current value from ``instance`` via ``getattr``.
        3. If the value is not in the Literal's allowed set (via
           :func:`typing.get_args`), reset to the default from a
           freshly-constructed ``Config()`` and append a warning to
           ``instance.last_load_warnings``.

        Non-str values (e.g. a hand-edited ``"asr_backend": 123``)
        are also reset — they can never be in a ``Literal[str, ...]``
        allowed set. The ``_validate_non_numeric_fields`` pre-pass
        normally coerces such values to ``str`` first, but this
        helper is defensive against a value that slipped through
        (e.g. a complex type that the str branch didn't catch).

        The reset is idempotent: a value already at the default is a
        no-op (it's in the allowed set). The reset is also safe to
        re-run — calling it twice produces no extra warnings.

        Warnings are appended to ``instance.last_load_warnings`` (NOT
        ``data["_load_warnings"]``, which has already been popped and
        transferred to the instance by the time this runs — see the
        :meth:`load` orchestrator). The warning text mirrors the
        format used by the per-field reset helpers
        (``_validate_model_path`` etc.) so the renderer can display
        them with the same UI treatment.
        """
        import typing

        try:
            hints = typing.get_type_hints(cls)
        except Exception:
            # ``typing.get_type_hints`` resolves forward refs and can
            # raise if a referenced name isn't importable in the
            # current sandbox. Fall back to the raw ``__annotations__``
            # (no forward-ref resolution) — for Literal[...] fields
            # the raw annotation IS the Literal, so this works.
            hints = dict(getattr(cls, "__annotations__", {}))

        # Build the defaults instance ONCE (not per-field) — Config()
        # construction is cheap but not free, and the per-field loop
        # may reset multiple values.
        defaults = cls()

        for field_name in cls._ENUM_FIELDS_TO_RESET_ON_LOAD:
            ann = hints.get(field_name)
            if ann is None:
                # Field was removed or renamed — skip silently (the
                # set is a ClassVar that should stay in sync with the
                # dataclass declaration, but a stale entry shouldn't
                # crash load).
                continue
            # Unwrap ``T | None`` / ``Optional[T]`` — none of the 9
            # fields are optional, but the unwrap is cheap insurance
            # against a future contributor adding an optional enum.
            if typing.get_origin(ann) in (typing.Union, types.UnionType):
                args = [a for a in typing.get_args(ann) if a is not type(None)]
                if len(args) == 1:
                    ann = args[0]
            if typing.get_origin(ann) is not typing.Literal:
                # Field's annotation isn't a Literal (e.g. it was
                # widened to bare ``str`` in a future refactor). Skip
                # — we can't enumerate allowed values without a
                # Literal. ``validate_config`` (via the IPC
                # allowlist) still catches genuinely invalid values.
                continue
            allowed = set(typing.get_args(ann))
            current = getattr(instance, field_name, None)
            if current in allowed:
                continue
            default_value = getattr(defaults, field_name)
            # Defensive: if the default ITSELF isn't in the allowed
            # set (shouldn't happen — the dataclass declaration
            # defines both — but guards against a malformed Literal),
            # pick the first allowed value rather than resetting to
            # an invalid default.
            if default_value not in allowed and allowed:
                default_value = sorted(allowed)[0]
            log.warning(
                "[CONFIG] %s=%r not in Literal allowed values %s; resetting to default %r",
                field_name,
                current,
                sorted(allowed),
                default_value,
            )
            # Use ``object.__setattr__`` to mirror the ``__post_init__``
            # pattern (Config is not frozen, but this is forward-
            # compatible and avoids triggering any future
            # ``__setattr__`` override).
            object.__setattr__(instance, field_name, default_value)
            # Append to ``last_load_warnings`` — initialize the list
            # if it's ``None`` (the ``__post_init__`` default).
            warnings = getattr(instance, "last_load_warnings", None)
            if warnings is None:
                warnings = []
                object.__setattr__(instance, "last_load_warnings", warnings)
            warnings.append(
                f"Config field {field_name!r}={current!r} not in allowed values "
                f"{sorted(allowed)}, reset to default {default_value!r}"
            )

    # ── : shared coercion helpers ───────────────────────────────
    #
    # The original ``_validate_non_numeric_fields`` had 4 near-identical
    # branches (bool / str / int / float) that each duplicated the same
    # 5-line "build msg → log.warning → warnings.append → reset
    # data[field_name]" pattern, 6 times total. The two helpers below
    # extract that pattern so each branch's tail is a single call.
    #
    # The helpers are static methods (not classmethods) because they
    # don't need ``cls`` — they operate on the passed-in ``defaults``
    # Config instance. Keeping them on the ``Config`` class (rather
    # than module-level functions) lets ``Config`` subclasses override
    # them if a future variant needs different warning formatting
    # (e.g. structured logging).

    # the set of Config dataclass field names that
    # hold secret material (API keys / tokens). Used by
    # :meth:`_warn_and_reset` to redact ``val`` before logging so a
    # malformed-on-disk api_key value doesn't get echoed into log
    # files at WARNING level. The set is sourced from
    # ``credential_store.PROVIDER_TO_CONFIG_FIELD.values()`` (the
    # canonical provider→field map).
    #
    # (fail-closed): the historical fallback literal
    # ``_SECRET_FIELD_NAMES_FALLBACK`` (a 5-field hardcoded set) is
    # RETAINED for parity assertions in tests, but the
    # :meth:`_secret_field_names` classmethod NO LONGER returns it on
    # import failure. Instead, the helper logs ``CRITICAL`` and
    # RE-RAISES — mirroring the fail-closed pattern in
    # :func:`voice_typer.server.config_sanitizer._derive_secret_fields`
    # (lines 76-115). A silent fallback to a stale literal would leave
    # any newly added provider's API key un-redacted in
    # :meth:`_warn_and_reset` / :meth:`_warn_and_coerce` log lines
    # (``val_repr = repr(val)``) whenever the fallback kicks in — a
    # security degradation. Failing loudly surfaces the breakage at
    # the first call site (typically ``Config.load()`` redaction).
    #
    # The set is computed lazily on first access (via the classmethod
    # :meth:`_secret_field_names`) to avoid an import-cycle at module
    # load time (``credential_store`` imports from ``_secrets`` which
    # imports from ``secure_file_io`` — none of those import
    # ``config``, so the cycle is currently safe, but the lazy pattern
    # is forward-compatible if a future refactor adds a back-edge).
    _SECRET_FIELD_NAMES_FALLBACK: ClassVar[frozenset[str]] = frozenset(
        {
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "cloud_api_key",
            "llm_api_key",
        }
    )

    @classmethod
    def _secret_field_names(cls) -> frozenset[str]:
        """return the set of Config field names holding secrets.

        Lazily imports ``credential_store.PROVIDER_TO_CONFIG_FIELD``
        (the canonical provider→field map) so the secret-field list
        stays in sync with the credential-store definition.

        SECURITY (fail-closed): if the import of
        ``PROVIDER_TO_CONFIG_FIELD`` fails for ANY reason (broken
        install, sandbox without the package, partial-import during
        test collection, future refactor that breaks the import path),
        we log ``CRITICAL`` and RE-RAISE. We do NOT fall back to the
        historical ``_SECRET_FIELD_NAMES_FALLBACK`` literal: a silent
        fallback to a stale 5-field set would leave any newly added
        provider's API key un-redacted in ``_warn_and_reset`` /
        ``_warn_and_coerce`` log lines (``val_repr = repr(val)``)
        whenever the fallback kicks in (SEC-003 regression analog).
        Failing the import loudly surfaces the breakage at the first
        call site (typically ``Config.load()`` redaction), which is
        strictly safer than silently degrading the redaction
        boundary. Mirrors the fail-closed pattern in
        :func:`voice_typer.server.config_sanitizer._derive_secret_fields`
        (lines 76-115) so the two paths handle the SAME failure
        identically.
        """
        try:
            from voice_typer.server import credential_store

            return frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.values())
        except Exception as exc:
            # Fail-closed: do NOT fall back to the hardcoded
            # ``_SECRET_FIELD_NAMES_FALLBACK`` literal. A silent
            # fallback would mask a broken install / sandbox and could
            # leave newly added provider API keys un-redacted in log
            # lines (the sanitizer fail-closed analog prevents the same
            # leak over IPC). Re-raise so the breakage is loud and
            # immediate at the first call site (Config.load()
            # redaction). The existing tests will surface any
            # early-startup path that relied on the silent fallback.
            log.critical(
                "[CONFIG] could not import credential_store for "
                "_secret_field_names — secret-field redaction may be "
                "incomplete. Refusing to fall back to a hardcoded "
                "literal (fail-closed). Original error: %s",
                exc,
            )
            raise

    @classmethod
    def _warn_and_reset(
        cls,
        field_name: str,
        val: Any,
        defaults: "Config",
        warnings: list[str],
        *,
        reason: str,
    ) -> Any:
        """Reset ``field_name`` to its default value with a logged warning.

        Delegates to
        :func:`voice_typer.server.config.sanitization._warn_and_reset`.
        The module-level function takes ``cls`` so subclass overrides of
        :meth:`_secret_field_names` are respected when redacting secret
        fields. Converted from ``@staticmethod`` to ``@classmethod`` so
        ``cls`` flows through; this is backward-compatible with the
        existing ``Config._warn_and_reset(field_name, val, ...)``
        call sites in ``tests/test_config_load_corruption.py``.
        """
        return _sanitization_warn_and_reset(cls, field_name, val, defaults, warnings, reason=reason)

    @classmethod
    def _warn_and_coerce(
        cls,
        field_name: str,
        val: Any,
        coerced: Any,
        warnings: list[str],
        *,
        reason: str,
    ) -> Any:
        """Record a coercion warning and return the coerced value.

        Delegates to
        :func:`voice_typer.server.config.sanitization._warn_and_coerce`.
        Converted from ``@staticmethod`` to ``@classmethod`` so ``cls``
        flows through for the secret-field redaction lookup.
        """
        return _sanitization_warn_and_coerce(cls, field_name, val, coerced, warnings, reason=reason)

    @classmethod
    def _validate_non_numeric_fields(cls: type["Config"], data: dict[str, Any]) -> dict[str, Any]:
        """Validate and coerce bool / str / int / float fields in loaded config data.

        This is a migration layer — NOT a duplicate of the type coercion
        that ``cls(**data)`` would do. Python dataclasses do NOT coerce
        ``1`` → ``True`` or ``"true"`` → ``True`` — they store the raw
        value as-is, which would then fail downstream type checks. This
        validator fixes up legacy on-disk configs (written by older
        versions of the app that used ints/strings for bool fields)
        BEFORE the dataclass constructor sees them.

        Delegates to
        :func:`voice_typer.server.config.sanitization._validate_non_numeric_fields`,
        which dispatches back through ``cls._warn_and_reset`` /
        ``cls._warn_and_coerce`` / ``cls._derive_field_type_registry``
        so subclass overrides of those methods are respected.
        """
        return _sanitization_validate_non_numeric_fields(cls, data)

    @property
    def config_dir(self) -> Path:
        return _config_dir()


# ──────────────────────────────────────────────────────────────────────────
# validator block moved to ``config_validators.py``.
# the wildcard ``from voice_typer.server.config_validators import *``
# re-exported every symbol listed in ``config_validators.__all__``.  Wildcard
# imports make it impossible for static analysis (ruff F403, pyrefly) to
# distinguish genuinely-unused re-exports from genuinely-used ones, and they
# silently propagate any new symbol added to ``__all__`` — including future
# underscore-prefixed helpers — into this module's public surface.
#
# The explicit import below mirrors ``config_validators.__all__`` *exactly*
# (minus ``ALLOWED_USER_MODELS``, which is already imported at the top of
# this file for use by ``Config.load()``).  Re-importing it here would
# trip ruff F811 (redefinition of unused name) without changing the
# module's public surface, so it is intentionally omitted from this list.
#
# If a future change to ``config_validators.__all__`` adds a new symbol
# that callers expect to reach via ``from voice_typer.server.config import …``,
# it MUST be added to this list explicitly — that's the whole point of
# replacing the wildcard.
# ──────────────────────────────────────────────────────────────────────────
from voice_typer.server.config_validators import (  # noqa: E402,F401 — backward-compat bottom-of-file re-export
    _MAX_API_KEY_LEN,
    _MAX_STRING_LEN,
    _VALIDATOR_API_KEY,
    _VALIDATOR_API_URL,
    _VALIDATOR_CLOUD_MODEL,
    _VALIDATOR_HOTKEY,
    _VALIDATOR_LANGUAGE,
    _VALIDATOR_LLM_API_URL,
    _VALIDATOR_LLM_MODEL,
    _VALIDATOR_MICROPHONE,
    _VALIDATOR_PUSH_TO_TALK_HOTKEY,
    _VALIDATOR_REPASTE_HOTKEY,
    IPC_CONFIG_ALLOWLIST,
    FieldSpec,
    ValidatorFn,
    _bool_validator,
    _is_float_or_int_not_bool,
    _is_int_not_bool,
    _is_str,
    _make_custom_theme_validator,
    _make_enum_validator,
    _make_float_validator,
    _make_int_validator,
    _make_optional_float_validator,
    _make_optional_int_validator,
    _make_optional_str_validator,
    _make_str_validator,
    _make_url_validator,
    validate_config,
    validate_config_update,
)
