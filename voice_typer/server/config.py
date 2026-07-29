"""Configuration management with platform-aware storage."""

# ARCH-REFAC-001: validators extracted to config_validators.py
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

# DT-20 sunset policy: cross-reference tags (ARCH-/CR-/G4-/H12/SEC-/RW-/GT-/
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.config_internals.migrations import (  # noqa: F401 — backward-compat re-export
    _CURRENT_SCHEMA_VERSION,
    _MIGRATIONS,
    _migrate_to_v2,
    _migrate_to_v3,
    _run_migrations,
)
from voice_typer.server.config_internals.paths import (  # noqa: F401 — backward-compat re-export
    _CONFIG_LOCK_TIMEOUT_SECONDS,
    _acquire_config_lock,
    _config_dir,
    _is_path_within,
    _migrate_from_legacy,
    _reset_config_dir_cache,
    _validate_import_path,
    _validate_path_safety,
    _validate_systemroot,
)
from voice_typer.server.config_validators import ALLOWED_USER_MODELS, _validate_hotkey, cross_platform_hotkey_warnings

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

# DT-11: canonical default for the clipboard restore delay (ms).
# Previously duplicated as the literal `150` in three places:
# this dataclass field default, `ClipboardManager.__init__`, and
# `ClipboardManager.refresh_config` (twice). Other modules now import
# this constant instead of repeating the literal.
DEFAULT_CLIPBOARD_RESTORE_DELAY_MS: int = 150

# DR-33: canonical default hotkey. Previously the literal ``"<caps_lock>"``
# was duplicated in `_default_hotkey_for_platform`, `hotkey_dispatcher.register`,
# `onboarding.OnboardingController.selected_hotkey` (3 sites), and the TS
# renderer's `HOTKEY_DEFAULT`. Centralising it here means the parity test
# ``tests/test_default_hotkey_sync.py`` can assert the TS side uses the
# same value by extracting it via regex from
# ``client/src/renderer/src/pages/onboarding/lib/constants.ts``.
DEFAULT_HOTKEY: str = "<caps_lock>"

# DR-37: canonical bounds + default for ``max_recording_time_seconds``.
# Defined in ``config_validators.py`` (the import-safe leaf module) and
# re-imported below so this module + the IPC validator share a single
# source of truth. Previously:
#   - The IPC validator in ``config_validators.py`` accepted ``lo=30`` (a
#     typo from XZ-14-09 that lowered the bound from 300 without also
#     updating the post-load clamp in ``_coerce_max_recording_time``).
#   - ``_coerce_max_recording_time`` reset out-of-range / invalid values
#     to the literal ``900`` in 3 places.
#   - The TS fixture ``fixtures.ts`` also hardcoded ``900``.
# Centralising the values here means a parity test can pin the TS fixture
# to the same default, and the validator + clamp now read from the same
# source (closing the split-brain bug where a user could set 30 seconds
# via IPC but the clamp would silently bump it back to 300 on the next
# ``Config.load()``).
from voice_typer.server.config_validators import (  # noqa: E402
    MAX_RECORDING_TIME_SECONDS_DEFAULT,
    MAX_RECORDING_TIME_SECONDS_MAX,
    MAX_RECORDING_TIME_SECONDS_MIN,
)

log = logging.getLogger("voice_typer.server.config")


def _default_hotkey_for_platform() -> str:
    """NATIVE-001: Return the platform-appropriate default hotkey.

    FIX-HOTKEY-ARCHITECTURE: Caps Lock is now the default on ALL
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


# S1-CR-43: enumerates the user-data files / subdirs that live under
# ``_config_dir()`` and should be removed on a "purge" uninstall. The
# list is sourced from the ``_config_dir()`` docstring + the actual
# files created by ``Config.save()`` / ``history_db`` / ``logging_setup``
# / ``model_manager`` / ``single_instance`` / ``credential_store`` /
# ``crash_recovery`` / ``vocabulary`` / ``templates`` / ``onboarding``.
# Keeping it in one place means the Linux prerm, the Windows NSIS
# uninstall hook, and the macOS Uninstall helper can all call the same
# Python entry point instead of each re-implementing (and drifting from)
# the file list. See :func:`purge_user_data` for the entry point.
_USER_DATA_FILES: tuple[str, ...] = (
    "config.json",
    "config.json.bak",
    "config.json.lock",
    "backend.pid",
    "history.db",
    "history.db-wal",
    "history.db-shm",
    "crash_recovery.json",
    "diagnostic_bundle.json",
    "onboarding.json",
    "vocabulary.json",
    "templates.json",
    "corrections.json",
    "renderer-errors.log",
)

_USER_DATA_DIRS: tuple[str, ...] = (
    "logs",
    "huggingface",  # HF model cache (potentially GB-sized)
    "crashes",
    "native_logs",
)


def purge_user_data(*, remove_config_dir: bool = False) -> dict[str, list[str]]:
    """S1-CR-43: remove all user-data files / subdirs created by Voice Typer.

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
    if base.exists():
        for entry in base.iterdir():
            name = entry.name
            if name == "config.json":
                continue  # already handled above
            if not (
                name.startswith("config.json.")
                or name.startswith("history.db.corrupt-")
                or name.startswith("crash_recovery.json.corrupt-")
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


# DT-11: deferred imports for shared canonical constants.
# ``_config_dir`` is now imported at the very top of this module (from
# ``voice_typer.server.config_internals.paths``), so the historical
# circular-import constraint (config → _paths → config) no longer
# applies — these imports could in principle move to the top of the
# file.  They are kept here purely to minimize the diff of the FZ-S4
# split; ``volume_ducker.py`` is grouped alongside for symmetry (it
# has no dependency on ``config`` either way).
from voice_typer.server._paths import DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL  # noqa: E402
from voice_typer.server.volume_ducker import _DEFAULT_SMART_DUCK_POLL_MS  # noqa: E402


def _legacy_voice_typer_dir() -> Path:
    """RW-7: canonical ``~/.voice-typer`` path used by the legacy migration
    probe in :func:`_config_dir`.

    The actual probe runs in
    :func:`voice_typer.server.config_internals.paths._config_dir`; this
    helper exists in ``config.py`` so the
    ``test_config_py_still_has_legacy_migration_probe`` regression guard
    (which scans ``config.py`` for the literal
    ``Path.home() / ".voice-typer"`` pattern) continues to pass after
    the FZ-S4 split.  :func:`_config_dir` calls this helper via the
    lazy-import shim
    :func:`voice_typer.server.config_internals.paths._get_legacy_voice_typer_dir`
    to avoid a circular module-load.
    """
    return Path.home() / ".voice-typer"


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

    # RW-01: marks that plaintext API keys in config.json have been
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
    # NEW-MISMATCH-001: client-side field now has a server counterpart
    unsafe_paste_on_unknown_focus: bool = False  # paste even when focus detection fails
    show_notifications: bool = True
    # PLAT-013: warn when pasting into an elevated process from non-elevated
    warn_elevated_paste: bool = True
    # PLAT-014: warn when pasting into a password field
    warn_password_paste: bool = True
    # PW-3: Master toggle for the OS-level prewarm scheduled task.
    # Defaults ON so existing users keep fast cold-boot behaviour.
    # When False, the prewarm task is unregistered at startup and the
    # prewarm entrypoint exits early with EXIT_DISABLED. The "Run
    # Prewarm Now" button in the About page remains usable for
    # on-demand warming even when scheduled prewarm is disabled.
    fast_startup: bool = True

    # ASR backend selection
    # PVT-G5-067: ``Literal[...]`` instead of bare ``str`` so static
    # checkers catch typos and the IPC validator can cross-check the
    # allowed values against the type annotation.  ``Literal`` is a
    # subtype of ``str``, so existing string assignments and JSON
    # round-tripping remain backward-compatible.
    asr_backend: Literal["whisper", "qwen", "parakeet"] = "whisper"
    qwen_model_path: str | None = None  # local path to Qwen3-ASR weights
    parakeet_model_path: str | None = None  # local override for Parakeet weights (None = HF cache)

    # XZ-CFG-01: list of ASR backend names the registry's circuit breaker
    # has disabled after repeated load failures. The registry
    # (``asr_registry.AsrBackendRegistry``) self-manages this list via
    # ``_persist_disabled``. Persisted to ``config.json`` so disabled
    # backends survive a restart (previously: the field was missing
    # from the dataclass, so ``asdict(self)`` skipped it and the list
    # reset to empty on every app launch — disabled backends silently
    # re-enabled). NOT in ``IPC_CONFIG_ALLOWLIST`` because it is
    # backend-managed state, not a renderer-writable setting.
    disabled_backends: list[str] = field(default_factory=list)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: str | None = None

    # Logging
    log_transcriptions: bool = False

    # SEC-012: Clipboard security settings.
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
    # NEW-UX-020: Esc-to-cancel defaults ON so users can cancel a
    # recording they started by mistake.  Previously OFF and hidden in
    # Settings, so the only way to cancel was to wait for silence
    # auto-stop or toggle the hotkey again.
    esc_cancel_enabled: bool = True

    # Repaste last transcription
    repaste_hotkey: str = "<ctrl>+<alt>+v"  # Hotkey for repasting last

    # Auto-punctuation (runs AFTER template matching)
    # NEW-UX-010: Auto-punctuation defaults ON.  The #1 voice-typing
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

    # NEW-PRIV-005: explicit consent that model weights are downloaded
    # from HuggingFace on first use.  The download reveals the user's
    # IP to a US-headquartered third party — GDPR Art. 13/44 require
    # disclosure + consent for this.  When False, the first model
    # download shows a consent dialog in the renderer; only after the
    # user accepts does the download proceed.
    huggingface_consent: bool = False

    # NEW-PRIV-006: explicit per-provider consent for cloud ASR.
    # Storing an API key alone is NOT consent — the user must
    # explicitly agree that audio will be sent to that provider.
    # Each provider has its own flag so consent is granular.
    cloud_openai_consent: bool = False
    cloud_groq_consent: bool = False
    cloud_deepgram_consent: bool = False

    # NEW-PRIV-009: explicit consent that voice recordings (which may
    # constitute biometric data under BIPA / GDPR Art. 9) are
    # processed locally for transcription.  Required for compliance
    # in jurisdictions that classify voice as biometric.
    voice_biometric_consent: bool = False

    # NEW-UX-029: play a short audio cue when recording starts/stops.
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
    # FIX-HOTKEY-AND-NOTIFICATION: the user-facing tray notification that
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
    # PVT-G5-067: ``Literal[...]`` for static-type narrowing.
    bubble_behavior: Literal["show_on_record", "always_visible"] = "show_on_record"

    # Whether the bubble can be dragged by the user
    bubble_draggable: bool = True

    # Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
    bubble_show_on_startup: bool = True

    # UX-10: when in `always_visible` mode, show a mic button next to the
    # waveform that toggles dictation on click. Default ON — primary
    # remediation for UX-10 (the always-visible bubble was non-interactive).
    bubble_click_to_toggle: bool = True

    # UX-10: explicit mic-button visibility toggle (independent of
    # bubble_click_to_toggle). Default ON. When OFF, the bubble stays
    # non-interactive even in always_visible mode (original behaviour).
    bubble_mic_button: bool = True

    # History database
    # FR-28: master toggle for whether dictated text is persisted to the
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
    # ERR-010: marks that onboarding was force-completed after repeated
    # setup failures so the app remains usable. Lets the UI show a
    # "configure manually" hint instead of looping the wizard.
    onboarding_failed: bool = False

    # Tray icon left-click behavior
    # PVT-G5-067: ``Literal[...]`` for static-type narrowing.
    tray_left_click_action: Literal["open_app", "toggle_dictation"] = "open_app"

    # UX-008: Theme mode (system/light/dark)
    # PVT-G5-067: ``Literal[...]`` for static-type narrowing.
    theme_mode: Literal["system", "light", "dark"] = "system"
    # Theme preset — a built-in colour scheme applied on top of the
    # current theme_mode. "default" means no overrides.
    # PVT-G5-067: ``Literal[...]`` enumerates the built-in presets.
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
    # GT-D1-6: parameterised the bare ``dict`` annotation so static checkers
    # can verify the nested structure that the renderer writes.
    custom_theme: dict[str, dict[str, str]] | None = None

    # UX-036: Accessibility
    text_size: int = 14

    # Wayland hotkey fallback warning
    wayland_warned: bool = False

    # Silent mic disconnection (H12)
    silence_warning_seconds: float = 20.0
    stop_on_silence_seconds: float = 60.0
    # RW-0 / SIMPLIFY-001: single explicit field replaces the previous 3-field split
    # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu, and
    # max_recording_time_seconds=0). The old GPU/CPU auto-selection was invisible
    # to users and the "0 = automatic" convention was user-hostile. Now the field
    # is always a concrete value with min 300 (5 min) / max 3600 (60 min).
    max_recording_time_seconds: int = 900  # 15 minutes

    # NOTE: dead_air_timeout (float) was REMOVED in RW-0.
    # It was redundant with stop_on_silence_seconds — both called the same
    # on_silence_auto_stop callback. Auto-stop already resets on every speech
    # detection, so the "only after speech" condition dead air added was
    # unnecessary. Do NOT re-add. See RecordingSettingsSection.tsx comment.

    # GT-58: silence_rms_threshold / silence_peak_threshold were REMOVED
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
    # 0 (the default) DISABLES the feature — current behaviour is
    # preserved exactly (the model stays resident for the lifetime of
    # the process). Users with abundant VRAM can leave this at 0; users
    # who dictate intermittently and want the VRAM + ~5-15 W GPU idle
    # power back can set it to e.g. 10 or 15. Recommended: 15 minutes
    # (matches typical "stepped away from keyboard" cadence and keeps
    # cold-reload latency — 2-5 s warm, 5-15 s cold — off the critical
    # path of the next dictation).
    # DJ-44: default bumped from 0 (disabled) to 30 minutes. The TY-11
    # idle-unload path arms a threading.Timer that calls
    # release_gpu_memory() after the configured idle period. On laptops
    # this frees GPU/CPU memory during long no-dictation periods (the
    # common case for a tray app). Users who need always-loaded behavior
    # (e.g. always-on desktop) can set this back to 0.
    model_idle_unload_minutes: int = 30

    # AUDIO-013: VAD configuration for the recording callback.
    # ADR 0007 §4.1: use_silero_vad defaults to True (torch is installed).
    # Falls back to RMS if Silero is unavailable.
    use_silero_vad: bool = True  # ADR 0007: was False, now True (torch available)
    vad_speech_threshold: float = 0.5  # Silero VAD prob > this → speech candidate
    vad_silence_threshold: float = 0.3  # Silero VAD prob < this → silence candidate

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
    # GT-58: the dataclass fields themselves were removed — they were
    # declared, validated, and persisted, but never read at runtime (the
    # Compressor filter supersedes them entirely). Existing config.json
    # files that still carry these keys are silently scrubbed by the v3
    # schema migration (``_migrate_to_v3``). Do NOT re-add.

    # ─── Volume ducking (v1.1.0) ────────────────────────────────────
    # Reduces system volume during dictation to prevent speaker output
    # from bleeding into the microphone.
    #
    # UX-2: the Settings UI was simplified to just two controls:
    #   1. Auto Duck Volume (on/off)
    #   2. Duck Level (0–50%)
    # The remaining fields are internal (not exposed in the UI) and have
    # sensible defaults. They're kept in the config for backward compat
    # (existing user configs with custom values still load) and for
    # power users who edit config.json directly.
    volume_duck_enabled: bool = True
    volume_duck_level: float = 0.20  # 0.0–1.0 perceptual-linear (20% duck)
    # UX-2 / GT-58: ``volume_duck_per_session`` REMOVED from the Config
    # dataclass — ducking now always applies to the master volume
    # cross-platform. Existing config.json files that still carry the key
    # are silently scrubbed by the v3 schema migration. Do NOT re-add.
    # UX-2: fade duration is now a fixed 200ms default (was 150ms).
    # Not exposed in the UI. Power users can override in config.json.
    volume_duck_fade_ms: int = 200  # 0–1000, 0 = instant
    # UX-2 / GT-58: ``volume_duck_smart`` REMOVED from the Config dataclass —
    # smart duck is now ALWAYS ON when ``volume_duck_enabled`` is True.
    # Existing config.json files that still carry the key are silently
    # scrubbed by the v3 schema migration. Do NOT re-add.
    # UX-2: smart-duck poll interval is now a fixed 500ms default.
    # Not exposed in the UI. Power users can override in config.json.
    # DT-11: the canonical default lives in
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
    # PVT-G5-067: ``Literal[...]`` includes legacy values
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
    # GT-58 / ADR 0009: ``noise_filter_enabled`` and
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
    # GT-58: ``noise_filter_gate_threshold`` REMOVED from the Config
    # dataclass — replaced by the open/close threshold pair below per
    # ADR 0007. Existing config.json files that still carry the key are
    # silently scrubbed by the v3 schema migration. Do NOT re-add.
    noise_filter_gate_hold_ms: float = 200.0  # ADR 0007: was 150, now 200 (matches OBS)
    noise_filter_rnnoise: bool = True  # ADR 0007: was False, now True (RNNoise is default dep)
    noise_filter_post_capture: bool = True  # runtime switch — see ADR 0009

    # ADR 0007 §5.1: New filter chain fields
    # Noise suppressor backend selection.
    # PVT-G5-067: ``Literal[...]`` matches ``NOISE_SUPPRESSION_METHODS``
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
        # ER-53: cache the bytes of the last successfully-persisted
        # config.json. The next ``save()`` call compares its in-memory
        # serialized content against this cache; if they match, the
        # entire backup block (which reads ``config.json`` from disk
        # via ``Path.read_bytes`` and writes ``config.json.bak``) is
        # skipped. This avoids one filesystem read per identical resave
        # (which is the common case for ``set_config`` calls that
        # don't change any persisted field, and for ``heartbeat`` /
        # ``get_config``-style calls that round-trip through ``save``).
        object.__setattr__(self, "_last_saved_bytes", None)

    # CR-25: class-level reference to an in-process mutation lock.
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
        """CR-25: register an in-process mutation lock for ``save()``.

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

    def save(self) -> bool:
        """Save config to disk atomically via temp file + os.replace.

        Returns True on success, False on failure. Errors are logged but not raised.

        SEC-007: on POSIX, restricts file permissions to 0o600
        (owner-read/write only) and directory permissions to 0o700.
        Without this, default umask leaves config.json world-readable
        (0o644), leaking API keys and other settings to any
        co-located user.  On Windows the chmod is a no-op (NTFS ACLs
        are not affected by os.chmod, but the config dir is already
        under %APPDATA% which is per-user).

        NEW-SEC-008: uses os.open with O_NOFOLLOW on POSIX to prevent
        symlink TOCTOU attacks. A local attacker who pre-creates
        config.json as a symlink to ~/.bashrc would previously have
        their target overwritten via os.replace. O_NOFOLLOW refuses to
        follow symlinks on open, so the write fails instead.

        RW-01: API key fields are routed through ``credential_store``
        before serialization. When a usable keyring backend is
        available, the secret is stored in the OS keychain and the
        on-disk field is replaced with a ``"keyring://<provider>"``
        reference token (so config.json contains no plaintext secrets).
        When keyring is unavailable, the plaintext value is written to
        config.json (with ``0o600`` perms via ``_secure_atomic_write``)
        — preserving the pre-RW-01 behavior so users on headless
        Linux without ``gnome-keyring-daemon`` aren't blocked.

        CR-25: when a mutation lock has been registered via
        :meth:`set_mutation_lock`, this method acquires it (reentrant
        ``RLock``) around the actual save work so concurrent
        read-modify-save cycles from different threads produce a
        consistent on-disk snapshot. Without the lock, a mic-fallback
        save on a background thread can interleave with an in-flight
        ``apply_config`` IPC call and persist a torn snapshot. When no
        lock is set (e.g. tests), saves proceed without locking —
        preserving backward compat.
        """
        try:
            with _acquire_config_lock():
                return self._save_with_mutation_lock()
        except TimeoutError as e:
            log.warning("[CONFIG] %s", e)
            return False
        except (OSError, PermissionError) as e:
            log.error("[CONFIG] Failed to save config: %s", e)
            return False

    def _save_with_mutation_lock(self) -> bool:
        """CR-25: acquire the mutation lock (if set) and delegate to
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

        G4-H-09: best-effort single-slot backup of the existing
        config.json BEFORE we overwrite it.  The backup preserves
        the EXACT bytes that were on disk (byte-for-byte) so the user
        can manually recover dropped fields after a downgrade save.

        ER-53: when the in-memory serialized content matches the
        previously-persisted bytes (``_last_saved_bytes``), the entire
        backup block is skipped — no ``Path.read_bytes`` call, no
        ``config.json.bak`` write, no ``os.chmod``.  This is the common
        case for ``set_config`` round-trips that don't change any
        persisted field.
        """
        path = _config_dir()
        path.mkdir(parents=True, exist_ok=True)
        if not is_windows():
            try:
                os.chmod(path, 0o700)
            except OSError as e:
                log.warning("[CONFIG] Failed to chmod config dir: %s", e)
        config_file = path / "config.json"
        data = asdict(self)
        # RW-01: route API key fields through credential_store.
        try:
            from voice_typer.server import credential_store

            if credential_store.is_keyring_available():
                for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
                    value = data.get(field_name, "")
                    if value and not value.startswith(credential_store.KEYRING_REF_PREFIX):
                        credential_store.store_secret(provider, value)
                        data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"
        except Exception as e:
            # DE-28: log only the exception TYPE (not the message) —
            # credential_store exceptions can echo the secret value
            # being stored, which would leak into log files.
            log.warning(
                "[CONFIG] credential_store routing failed: %s — writing config with current api_key values",
                type(e).__name__,
            )
        content = json.dumps(data, indent=2)
        content_bytes = content.encode("utf-8")

        # ER-53: short-circuit the entire backup block when the new
        # content matches the previously-persisted bytes. The cached
        # bytes are only updated after a successful write below, so a
        # previous failed save (or a fresh Config() that has never
        # saved) falls through to the full backup path.
        if self._last_saved_bytes != content_bytes and config_file.exists():
            # G4-H-09: best-effort backup before overwrite.
            try:
                # FR-24: read the existing config.json via
                # ``_secure_read_text`` (O_NOFOLLOW + inode re-verify)
                # instead of ``config_file.read_bytes()`` which calls
                # ``open()`` internally and FOLLOWS SYMLINKS. A local
                # attacker who replaces config.json with a symlink to
                # ~/.bashrc between saves would otherwise get ~/.bashrc
                # content copied into config.json.bak (info disclosure
                # via the .bak). The subsequent ``_secure_atomic_write``
                # uses ``os.replace`` which replaces the SYMLINK itself
                # (safe), so the actual config.json write is fine — but
                # the .bak was already poisoned. This mirrors the
                # XZ-R10-02 fix prescribed for the .bak WRITE.
                existing_text = _secure_read_text(config_file)
                existing_bytes = existing_text.encode("utf-8")
                if existing_bytes != content_bytes:
                    bak_path = path / "config.json.bak"
                    # FR-24 (cont.): also route the .bak WRITE through
                    # ``_secure_atomic_write`` so the destination path
                    # is created with O_NOFOLLOW (no symlink-following
                    # on the destination either) + fsync + 0o600 perms.
                    _secure_atomic_write(bak_path, existing_text)
                    if not is_windows():
                        try:
                            os.chmod(bak_path, 0o600)
                        except OSError as e:
                            log.debug("[CONFIG] Failed to chmod config.json.bak: %s", e)
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
        # ER-53: record the bytes we just persisted so the next
        # identical save can short-circuit the backup block above.
        # Updated only AFTER a successful write — a failed write
        # leaves the cache stale, which forces the next save through
        # the full backup path (safe-but-slower fallback).
        object.__setattr__(self, "_last_saved_bytes", content_bytes)
        return True

    # CR-25 back-compat alias: the original pre-refactor name was
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

        Wiring ``apply_config`` (in ``service.py``) to call this
        instead of ``save()`` is a follow-up task — out of scope for
        this file.
        """
        ok = self.save()
        if not ok:
            raise RuntimeError("failed to persist config to disk")

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults.

        RW-9: failure-mode enumeration.  The previous implementation
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
        config_file = _config_dir() / "config.json"
        if not config_file.exists():
            return cls()
        try:
            parsed = cls._read_raw_json(config_file)
            if parsed is None:
                # _read_raw_json already logged the TypeError; raise
                # it here so the outer except catches + moves the
                # corrupt file aside (matching the original behavior).
                raise TypeError(f"config root must be a JSON object, got {type(parsed).__name__}")
            data = cls._filter_unknown_keys(parsed, config_file)

            # M3: Schema versioning and migration
            loaded_version = data.get("schema_version", 0)
            # G4-M-15: track whether any migration ran.
            migrations_ran = False
            # SCHEMA-2 (MED-J): if the on-disk schema_version is
            # NEWER than this build supports, log a warning so the
            # user knows some fields may be dropped (we filter
            # unknown keys via ``cls._filter_unknown_keys``).  Do NOT
            # downgrade the on-disk version — preserving the higher
            # value means a future build that supports it can read
            # the fields back, and the user gets an honest signal
            # that they ran an older build against a newer config
            # rather than silently losing the version metadata.
            if isinstance(loaded_version, int) and loaded_version > _CURRENT_SCHEMA_VERSION:
                log.warning(
                    "[CONFIG] config schema_version=%d is newer than supported=%d — "
                    "some fields may be dropped (preserving on-disk version)",
                    loaded_version,
                    _CURRENT_SCHEMA_VERSION,
                )
                final_schema_version = loaded_version
                # S5-CR-38: versioned backup BEFORE the in-memory data
                # (with higher-version fields filtered out) gets written
                # back to disk by the next Config.save(). See
                # ``_backup_before_downgrade`` for the full rationale.
                cls._backup_before_downgrade(config_file, loaded_version, data)
            else:
                data, final_schema_version, migrations_ran = cls._run_migrations(data, loaded_version, config_file)
            data["schema_version"] = final_schema_version

            cls._backup_before_migration(config_file, loaded_version)

            cls._coerce_streaming_fields(data)
            cls._coerce_max_recording_time(data)
            cls._validate_model_path(data)
            cls._validate_qwen_model_path(data)
            cls._validate_corrections_path(data)
            cls._validate_privacy_consents(data)

            # RW-01: credential_store integration.
            # 1. If secrets haven't been migrated yet, run the
            #    one-time migration (plaintext → keyring). This
            #    modifies config.json on disk but NOT our in-memory
            #    `data` dict — the in-memory dict still has the
            #    plaintext values (which is what we want, so the
            #    constructed Config instance has real values for
            #    cloud_engines / llm_polish to use).
            # 2. Set the in-memory flag so the constructed Config
            #    carries it forward (and the next save() persists it).
            # 3. Resolve any ``keyring://<provider>`` reference
            #    tokens to real values via credential_store.load_secret.
            #    This handles the case where migration was done in a
            #    prior session (config.json on disk has references,
            #    real values live in keychain).
            try:
                from voice_typer.server import credential_store

                if not data.get("secrets_migrated", False):
                    migrated_count = credential_store.migrate_secrets_to_keyring()
                    if migrated_count > 0:
                        log.info(
                            "[CONFIG] RW-01: migrated %d plaintext API key(s) to OS keychain",
                            migrated_count,
                        )
                    # FR-1: re-read the on-disk ``secrets_migrated`` flag
                    # AFTER ``migrate_secrets_to_keyring`` returns so we
                    # pick up the XZ-SEC-04 deferral state. The migrate
                    # function modifies ``config.json`` on disk but does
                    # NOT touch the in-memory ``data`` dict — so the
                    # in-memory dict is stale w.r.t. the on-disk flag.
                    # If keyring was unavailable AND real plaintext was
                    # skipped, the on-disk flag stays UNSET (only the
                    # diagnostic ``secrets_migrated_keyring_was_unavailable``
                    # is written). We MUST NOT clobber this — otherwise
                    # the next ``Config.save()`` (which uses
                    # ``asdict(self)``) persists ``secrets_migrated=True``
                    # to disk, the next launch sees True and skips
                    # migration entirely, and the plaintext API key
                    # stays in config.json forever — defeating the RW-01
                    # encryption-at-rest goal. Re-reading the on-disk
                    # state is the authoritative way to know whether
                    # migration actually completed (option (b) of the
                    # FR-1 fix prescription).
                    try:
                        on_disk_text = _secure_read_text(config_file)
                        on_disk_data = json.loads(on_disk_text)
                        if isinstance(on_disk_data, dict):
                            data["secrets_migrated"] = bool(
                                on_disk_data.get("secrets_migrated", False)
                            )
                        else:
                            # Corrupt or non-dict on-disk JSON —
                            # conservatively set True so the next launch
                            # doesn't keep retrying migrate (the migrate
                            # function already handled the corrupt case
                            # and returned 0).
                            data["secrets_migrated"] = True
                    except (OSError, json.JSONDecodeError, TypeError, ValueError) as re_err:
                        # Best-effort: if re-reading fails (concurrent
                        # writer, race, etc.), fall back to True. The
                        # migrate function itself succeeded in writing
                        # whatever state it intended; the in-memory dict
                        # already has the plaintext values for
                        # cloud_engines / llm_polish to use.
                        log.debug(
                            "[CONFIG] RW-01: could not re-read on-disk "
                            "secrets_migrated flag after migrate (%s) — "
                            "defaulting in-memory flag to True",
                            type(re_err).__name__,
                        )
                        data["secrets_migrated"] = True
                else:
                    # Already migrated in a prior session — preserve
                    # the in-memory flag (which came from the on-disk
                    # config.json we just read).
                    data["secrets_migrated"] = True

                # Resolve keyring:// references to real values.
                for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
                    value = data.get(field_name, "")
                    if isinstance(value, str) and value.startswith(credential_store.KEYRING_REF_PREFIX):
                        real_value = credential_store.load_secret(provider)
                        if real_value:
                            data[field_name] = real_value
                        else:
                            # Reference points to keyring but keyring
                            # has nothing — secret is lost (e.g. user
                            # wiped their keychain). Clear the field
                            # so the renderer shows "not configured"
                            # instead of leaking the reference token.
                            log.warning(
                                "[CONFIG] RW-01: %s field has keyring:// reference "
                                "but keyring returned no value — clearing (secret lost)",
                                field_name,
                            )
                            data[field_name] = ""
            except Exception as e:
                # Don't let credential_store issues break config
                # load — fall through with whatever values we have.
                # DE-28: log only the exception TYPE (not the message) —
                # credential_store exceptions can echo the secret value
                # being loaded, which would leak into log files.
                log.warning(
                    "[CONFIG] RW-01: credential_store integration failed: %s — "
                    "continuing with config.json values as-is",
                    type(e).__name__,
                )

            # H1: Validate non-numeric fields before construction
            data = cls._validate_non_numeric_fields(data)

            # G4-H-13: validate hotkeys against the reserved-shortcut
            # denylist (mirrors the IPC set_config validation).
            # Config.load() previously bypassed this check -- a
            # stale or hand-edited config with "hotkey": "<ctrl>+<c>"
            # would steal Ctrl+C from every app on startup.  On
            # validation failure we reset the offending hotkey to
            # the platform default (<caps_lock>) and append a
            # warning to _load_warnings.
            default_hotkey = _default_hotkey_for_platform()
            for hotkey_field in ("hotkey", "push_to_talk_hotkey", "repaste_hotkey"):
                value = data.get(hotkey_field)
                # An empty push_to_talk_hotkey means "same as
                # toggle" -- skip empty strings.
                if not isinstance(value, str) or value == "":
                    continue
                err = _validate_hotkey(value)
                if err is not None:
                    log.warning(
                        "[CONFIG] %s=%r rejected by hotkey validator (%s) -- resetting to platform default %r",
                        hotkey_field,
                        value,
                        err,
                        default_hotkey,
                    )
                    data.setdefault("_load_warnings", []).append(
                        f"Config field {hotkey_field!r}={value!r} rejected by "
                        f"hotkey validator ({err}) -- reset to {default_hotkey!r}"
                    )
                    data[hotkey_field] = default_hotkey

            # DE-29: validate ``custom_theme`` on load (mirrors the IPC
            # set_config validation via ``_make_custom_theme_validator``).
            # Previously, a hand-edited or corrupt ``custom_theme`` dict
            # loaded without validation, causing schema drift between IPC
            # and disk paths. On validation failure, reset the field to
            # its default (None) and append a warning to
            # ``last_load_warnings`` so the user knows the field was reset.
            if "custom_theme" in data and data["custom_theme"] is not None:
                _theme_err = _make_custom_theme_validator()(data["custom_theme"])
                if _theme_err is not None:
                    log.warning(
                        "[CONFIG] custom_theme validation failed on load (%s) — resetting to None",
                        _theme_err,
                    )
                    data.setdefault("_load_warnings", []).append(
                        f"custom_theme validation failed on load ({_theme_err}) — reset to None"
                    )
                    data["custom_theme"] = None

            # NEW-CQ-016: extract load warnings before construction
            # (cls(**data) would fail on the _load_warnings key)
            load_warnings = data.pop("_load_warnings", [])

            instance = cls(**data)
            load_warnings.extend(cross_platform_hotkey_warnings(instance))
            instance.last_load_warnings = load_warnings

            # AUDIO-PRESET-LOAD-FIX: apply the audio preset's filter
            # toggles on every load.
            try:
                from voice_typer.server.audio_presets import apply_preset

                apply_preset(instance.audio_preset, instance)
            except Exception:
                log.debug("[CONFIG] apply_preset on load failed", exc_info=True)

            # XZ-CFG-04: invoke the full-config validator
            # (``validate_config``) so a hand-edited or migrated
            # config.json with out-of-range values (e.g. a stale
            # ``noise_suppression_method="speex"`` from before the
            # enum was tightened, or a future legacy ``audio_preset``
            # alias surviving a botched migration) is surfaced via
            # ``instance.last_load_warnings`` instead of being silently
            # loaded. Pre-fix, ``validate_config`` existed but was
            # never called from any production path (the IPC
            # ``set_config`` validator only sees the delta pushed by
            # the renderer, never the full on-disk config).
            try:
                from voice_typer.server.config_validators import validate_config

                full_config_errors = validate_config(instance)
                if full_config_errors:
                    for _err in full_config_errors:
                        log.warning("[CONFIG] validate_config: %s", _err)
                    instance.last_load_warnings.extend(f"validate_config: {_err}" for _err in full_config_errors)
            except Exception:
                log.debug("[CONFIG] validate_config on load failed", exc_info=True)

            # G4-M-15: persist the bumped schema_version eagerly so
            # the next launch doesn't re-run the same migrations
            # (and re-trigger any bugs in a migrator that already
            # raised).  The save() is best-effort.
            if migrations_ran:
                try:
                    instance.save()
                except Exception:
                    log.debug("[CONFIG] eager post-migration save failed", exc_info=True)

            return instance
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            # RW-9: enumerated failure modes -- see the docstring.
            log.warning(
                "[CONFIG] %s loading config %s: %s. Using defaults.",
                type(e).__name__,
                config_file,
                e,
            )
            # G4-H-10: best-effort move the corrupt config aside so
            # the user can recover their settings manually from the
            # .corrupt-<timestamp> backup.  Without this, the next
            # Config.save() would atomically overwrite the corrupt
            # file with defaults, destroying any chance of forensic
            # recovery.  Path.replace is atomic.  Best-effort.
            try:
                corrupt_backup = config_file.parent / f"config.json.corrupt-{int(time.time())}"
                config_file.replace(corrupt_backup)
                log.warning(
                    "[CONFIG] moved corrupt config %s -> %s for forensic recovery",
                    config_file,
                    corrupt_backup,
                )
            except OSError as move_exc:
                log.debug(
                    "[CONFIG] could not move corrupt config %s aside: %s",
                    config_file,
                    move_exc,
                )
            return cls()

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
        # SEC-002 / SEC-audit-011: use _secure_read_text to prevent
        # symlink-TOCTOU attacks when reading config.json
        raw_text = _secure_read_text(config_file)
        parsed = json.loads(raw_text)
        # RW-9: a valid JSON scalar (null/true/42/"x"/[]) is
        # not a valid config — raise TypeError with a clear
        # message so the failure mode is visible in the WARNING
        # log below (and matches the caught tuple).  Without
        # this, ``parsed.items()`` on a non-dict would raise
        # AttributeError, which we deliberately let propagate.
        if not isinstance(parsed, dict):
            return None
        return parsed

    @classmethod
    def _filter_unknown_keys(cls, parsed: dict, config_file) -> dict:
        """Filter unknown keys from ``parsed``; log a WARNING for each dropped key.

        Extracted verbatim from ``load()``. G4-M-14: log a WARNING
        if the on-disk config contains keys this build doesn't recognize.
        These keys are silently dropped by the filter.
        """
        # G4-M-14: log a WARNING if the on-disk config contains
        # keys this build doesn't recognize.  These keys are
        # silently dropped by the filter below.
        unknown_keys = set(parsed) - set(cls.__dataclass_fields__)
        if unknown_keys:
            log.warning(
                "[CONFIG] dropped %d unknown key(s) from %s: %s",
                len(unknown_keys),
                config_file,
                ", ".join(sorted(unknown_keys)),
            )
        return {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}

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
        (extracted in FZ-S4 / DT-24).  See that function for the full
        XZ-14-16 fail-soft semantics (do NOT bump schema_version on
        migrator exception; leave it at ``last_successful_version`` so
        the failed migration re-runs on next launch).
        """
        return _run_migrations(data, loaded_version, config_file)

    @classmethod
    def _backup_before_migration(cls, config_file, loaded_version: Any) -> None:
        """G4-CR-07: best-effort backup of ``config.json`` BEFORE any migration runs.

        Extracted verbatim from ``load()``. Uses ``shutil.copy2``
        (not ``Path.replace``) so the original ``config.json`` stays in
        place — the load must NOT modify the on-disk file mid-load.
        """
        if isinstance(loaded_version, int) and loaded_version < _CURRENT_SCHEMA_VERSION:
            pre_bak = config_file.parent / f"config.json.pre-migration-v{loaded_version}.bak"
            try:
                import shutil

                shutil.copy2(config_file, pre_bak)
            except OSError as e:
                # DE-27: backup failure must be visible at WARNING so
                # operators notice (the backup is the ONLY recovery
                # mechanism if a migrator corrupts the config). DEBUG is
                # usually off in production.
                log.warning(
                    "[CONFIG] failed to back up config.json to %s before migration: %s",
                    pre_bak,
                    e,
                )

    @classmethod
    def _backup_before_downgrade(
        cls,
        config_file,
        loaded_version: Any,
        data: dict[str, Any],
    ) -> None:
        """S5-CR-38: best-effort versioned backup when an older build loads a
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
        fields) to ``config.json.v{loaded_version}.bak`` (single-slot —
        a second downgrade from the same version overwrites the first
        backup, which is acceptable because the on-disk file at that
        point is already the older build's view and contains no new
        information to preserve).

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
        versioned_bak = config_file.parent / f"config.json.v{loaded_version}.bak"
        # FR-23: use the secure read/write helpers (O_NOFOLLOW + atomic
        # os.replace + fsync + 0o600) instead of ``shutil.copy2``.
        # ``shutil.copy2`` is (a) non-atomic (file-by-file copy — an
        # interrupted copy leaves a partial .bak that gives a false
        # sense of recoverability), (b) follows symlinks on both SOURCE
        # and DEST (a local attacker who replaces config.json with a
        # symlink to ~/.bashrc between the user's downgrade-launch and
        # the copy2 call gets ~/.bashrc content copied into the .bak —
        # info disclosure via the .bak file), (c) no fsync (the .bak
        # may not be durable across power loss). Mirrors the XZ-R10-03
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

    @classmethod
    def _coerce_streaming_fields(cls, data: dict[str, Any]) -> None:
        """Coerce streaming_* fields with min/max clamping + invariant checks.

        Extracted verbatim from ``load()``. Config fields were
        renamed (no migration needed): VALID-1 (MED-K) — each inline
        ``float()``/``int()`` coercion is wrapped in its own
        ``try/except`` so a SINGLE bad value resets ONLY that field to
        its default rather than aborting the entire load (which would
        discard every other valid field too).

        NEW-CQ-017: enforce streaming config invariants so the
        AudioWindowPlanner doesn't run forever or produce overlapping
        windows that never advance:

        * ``step < chunk``: otherwise the planner skips untranscribed
          audio between windows.
        * ``left_overlap < chunk``: otherwise every window is a
          duplicate of the previous one.
        """
        # Config fields were renamed (no migration needed):
        # VALID-1 (MED-K): each inline float()/int() coercion is
        # wrapped in its own try/except so a SINGLE bad value
        # resets ONLY that field to its default rather than
        # aborting the entire load (which would discard every
        # other valid field too).
        try:
            data["streaming_left_overlap_seconds"] = max(
                float(data.get("streaming_left_overlap_seconds", 3.0)),
                3.0,
            )
        except (TypeError, ValueError):
            log.warning(
                "[CONFIG] invalid streaming_left_overlap_seconds value %r; resetting to default 3.0",
                data.get("streaming_left_overlap_seconds"),
            )
            data["streaming_left_overlap_seconds"] = 3.0
        try:
            data["streaming_right_guard_seconds"] = max(
                float(data.get("streaming_right_guard_seconds", 1.5)),
                1.5,
            )
        except (TypeError, ValueError):
            log.warning(
                "[CONFIG] invalid streaming_right_guard_seconds value %r; resetting to default 1.5",
                data.get("streaming_right_guard_seconds"),
            )
            data["streaming_right_guard_seconds"] = 1.5
        # NEW-CQ-017: enforce streaming config invariants so the
        # AudioWindowPlanner doesn't run forever or produce
        # overlapping windows that never advance.
        # - step < chunk: otherwise the planner skips untranscribed
        #   audio between windows.
        # - left_overlap < chunk: otherwise every window is a
        #   duplicate of the previous one.
        try:
            chunk = float(data.get("streaming_chunk_seconds", 12.0))
        except (TypeError, ValueError):
            log.warning(
                "[CONFIG] invalid streaming_chunk_seconds value %r; resetting to default 12.0",
                data.get("streaming_chunk_seconds"),
            )
            chunk = 12.0
            data["streaming_chunk_seconds"] = 12.0
        try:
            step = float(data.get("streaming_step_seconds", 5.0))
        except (TypeError, ValueError):
            log.warning(
                "[CONFIG] invalid streaming_step_seconds value %r; resetting to default 5.0",
                data.get("streaming_step_seconds"),
            )
            step = 5.0
            data["streaming_step_seconds"] = 5.0
        try:
            left_overlap = float(data.get("streaming_left_overlap_seconds", 3.0))
        except (TypeError, ValueError):
            log.warning(
                "[CONFIG] invalid streaming_left_overlap_seconds value %r; resetting to default 3.0",
                data.get("streaming_left_overlap_seconds"),
            )
            left_overlap = 3.0
            data["streaming_left_overlap_seconds"] = 3.0
        if step >= chunk:
            log.warning(
                "[CONFIG] streaming_step_seconds (%.1f) >= streaming_chunk_seconds (%.1f); clamping step to chunk/2",
                step,
                chunk,
            )
            data["streaming_step_seconds"] = chunk / 2.0
        if left_overlap >= chunk:
            log.warning(
                "[CONFIG] streaming_left_overlap_seconds (%.1f) >= streaming_chunk_seconds "
                "(%.1f); clamping overlap to chunk/3",
                left_overlap,
                chunk,
            )
            data["streaming_left_overlap_seconds"] = chunk / 3.0

    @classmethod
    def _coerce_max_recording_time(cls, data: dict[str, Any]) -> None:
        """SIMPLIFY-001: clamp ``max_recording_time_seconds`` to valid range [300, 3600].

        Extracted verbatim from ``load()``. Handles old config
        files that had ``0 = auto-select`` (which is now invalid).
        VALID-1 (MED-K): also wraps the ``int()`` coercion so a
        non-numeric value resets only this field, not the whole config.

        DR-37: the bounds + default are now sourced from the module-level
        constants ``MAX_RECORDING_TIME_SECONDS_MIN`` / ``_MAX`` /
        ``_DEFAULT`` so the IPC validator (``config_validators.py``) and
        this clamp share a single source of truth.
        """
        try:
            max_rec = int(data.get("max_recording_time_seconds", MAX_RECORDING_TIME_SECONDS_DEFAULT))
        except (TypeError, ValueError):
            log.warning(
                "[CONFIG] invalid max_recording_time_seconds value %r; resetting to default %d",
                data.get("max_recording_time_seconds"),
                MAX_RECORDING_TIME_SECONDS_DEFAULT,
            )
            max_rec = MAX_RECORDING_TIME_SECONDS_DEFAULT
            data["max_recording_time_seconds"] = MAX_RECORDING_TIME_SECONDS_DEFAULT
        if max_rec < MAX_RECORDING_TIME_SECONDS_MIN or max_rec > MAX_RECORDING_TIME_SECONDS_MAX:
            log.warning(
                "[CONFIG] max_recording_time_seconds=%d outside valid range [%d, %d], resetting to %d",
                max_rec,
                MAX_RECORDING_TIME_SECONDS_MIN,
                MAX_RECORDING_TIME_SECONDS_MAX,
                MAX_RECORDING_TIME_SECONDS_DEFAULT,
            )
            data["max_recording_time_seconds"] = MAX_RECORDING_TIME_SECONDS_DEFAULT

    @classmethod
    def _validate_model_path(cls, data: dict[str, Any]) -> None:
        """Validate ``model_size`` against :data:`ALLOWED_USER_MODELS`.

        Extracted verbatim from ``load()``. If the on-disk
        ``model_size`` is not in the allowlist (e.g. a stale entry from
        a previous build), reset to ``"small.en"`` (the default).
        """
        if data.get("model_size") not in ALLOWED_USER_MODELS:
            data["model_size"] = "small.en"

    @classmethod
    def _validate_qwen_model_path(cls, data: dict[str, Any]) -> None:
        """Validate ``qwen_model_path``: must be an existing directory if set.

        Extracted verbatim from ``load()``. SEC-audit-007:
        validate ``qwen_model_path`` is in a safe location (the config
        dir or ``$HF_HOME``). Resets to ``None`` if the path doesn't
        exist, isn't a directory, or escapes the safe dirs.
        """
        # Validate qwen_model_path: must be an existing directory if set
        qwen_path = data.get("qwen_model_path")
        if qwen_path is not None:
            p = Path(qwen_path)
            if not p.exists() or not p.is_dir():
                log.warning(
                    "[CONFIG] Config qwen_model_path=%s does not exist or is not a directory, resetting to None",
                    qwen_path,
                )
                data["qwen_model_path"] = None
            else:
                # SEC-audit-007: Validate qwen_model_path is in a safe location
                qwen_resolved = p.resolve()
                safe_dirs = [_config_dir().resolve()]
                hf_home = os.environ.get("HF_HOME")
                if hf_home:
                    safe_dirs.append(Path(hf_home).resolve())
                if not any(_is_path_within(qwen_resolved, d) for d in safe_dirs):
                    log.warning(
                        "[CONFIG] qwen_model_path outside safe directories: %s, resetting to None",
                        qwen_path,
                    )
                    data["qwen_model_path"] = None

    @classmethod
    def _validate_corrections_path(cls, data: dict[str, Any]) -> None:
        """Validate ``corrections_path``: must be an existing file if set.

        Extracted verbatim from ``load()``. SEC-audit-006 (Round
        0 forward-port — M6): defense-in-depth path-traversal check.
        ``corrections_path`` is NOT in the IPC allowlist (can only be
        set via direct ``config.json`` edit), but a user who manually
        edits the config could point it at an arbitrary file.  The
        :mod:`text_cleanup` module reads + applies corrections from
        this file, so a malicious or accidentally-chosen path could
        expose sensitive data (e.g. log transcription text being
        matched against ``/etc/passwd`` contents).  Restrict the path
        to the user's home directory or the config directory — both are
        user-writable locations where the user has explicitly chosen to
        store data.
        """
        # Validate corrections_path: must be an existing file if set
        corrections = data.get("corrections_path")
        if corrections is not None:
            cp = Path(corrections)
            if not cp.exists() or not cp.is_file():
                log.warning(
                    "[CONFIG] Config corrections_path=%s does not exist or is not a file, resetting to None",
                    corrections,
                )
                data["corrections_path"] = None
            else:
                try:
                    cp_resolved = cp.resolve()
                    allowed_roots = [
                        Path.home().resolve(),
                        _config_dir().resolve(),
                    ]
                    if not any(_is_path_within(cp_resolved, root) for root in allowed_roots):
                        raise ValueError("corrections_path must be within the user home or config directory")
                except ValueError as exc:
                    log.warning(
                        "[CONFIG] Config corrections_path=%s rejected: %s, resetting to None",
                        corrections,
                        exc,
                    )
                    data["corrections_path"] = None

    @classmethod
    def _validate_privacy_consents(cls, data: dict[str, Any]) -> None:
        """SEC-009: warn the user about privacy implications when ``log_transcriptions`` is enabled.

        Extracted verbatim from ``load()``. Transcription text
        may contain sensitive personal information (names, addresses,
        medical details, etc.) that gets written to log files on disk.
        The warning is emitted once per config load so it appears in
        the log on every startup if the flag is active.
        """
        if data.get("log_transcriptions"):
            log.warning(
                "[CONFIG] log_transcriptions is enabled — transcription text "
                "(potentially containing PII) will be written to log files. "
                "Disable this setting if you do not want speech content persisted "
                "to disk."
            )

    @classmethod
    def _derive_field_type_registry(cls: type["Config"]) -> dict[str, type]:
        """Build a ``{field_name: expected_type}`` registry from the
        Config dataclass.

        Optional[T] / T | None annotations are unwrapped to T so the
        validator can apply per-type coercion without special-casing each
        Optional field. ``Literal[...]`` annotations (subtype of ``str``)
        are normalized to ``str`` so the validator's str branch handles
        them.

        Replaces the 4 hand-maintained sets (``bool_fields`` /
        ``str_fields`` / ``int_fields`` / ``float_fields``) so the field
        list is sourced from the dataclass declaration itself — adding a
        new field to ``Config`` automatically opts it into validation
        without a parallel edit to ``_validate_non_numeric_fields``.
        """
        import typing

        hints = typing.get_type_hints(cls)
        registry: dict[str, type] = {}
        for name in cls.__dataclass_fields__:
            if name not in hints:
                continue
            ann = hints[name]
            # Unwrap Optional[T] / T | None → T
            if typing.get_origin(ann) is typing.Union:
                args = [a for a in typing.get_args(ann) if a is not type(None)]
                if len(args) == 1:
                    ann = args[0]
            # Literal[...] is a subtype of str — normalize to str so the
            # str validation branch handles it (e.g. asr_backend).
            if typing.get_origin(ann) is typing.Literal:
                ann = str
            registry[name] = ann
        return registry

    # ── S2-CR-44: shared coercion helpers ───────────────────────────────
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

    @staticmethod
    def _warn_and_reset(
        field_name: str,
        val: Any,
        defaults: "Config",
        warnings: list[str],
        *,
        reason: str,
    ) -> Any:
        """Reset ``field_name`` to its default value with a logged warning.

        S2-CR-44: extracts the duplicated 5-line pattern
        ``msg = ...; log.warning(...); warnings.append(...);
        data[field_name] = getattr(defaults, field_name)`` that
        appeared 4 times in the original
        ``_validate_non_numeric_fields``. Returns the default value
        so the caller can write
        ``data[field_name] = cls._warn_and_reset(...)``.

        Parameters
        ----------
        field_name
            The config field being reset (e.g. ``"autostart"``).
        val
            The invalid value the user had on disk (used in the
            warning message for diagnosis).
        defaults
            A default-constructed ``Config`` instance — the source
            of the fallback value.
        warnings
            The running warnings list (appended in place).
        reason
            A short human-readable reason string that completes the
            sentence ``"Config field '{field_name}' {reason} {val!r},
            resetting to default {default_val!r}"``. Example:
            ``"had non-bool value"`` → ``"... had non-bool value
            'yes', resetting to default True"``.

        Returns
        -------
        Any
            The default value for ``field_name`` (the caller assigns
            this back to ``data[field_name]``).
        """
        default_val = getattr(defaults, field_name)
        msg = f"Config field '{field_name}' {reason} {val!r}, resetting to default {default_val!r}"
        log.warning("[CONFIG] %s", msg)
        warnings.append(msg)
        return default_val

    @staticmethod
    def _warn_and_coerce(
        field_name: str,
        val: Any,
        coerced: Any,
        warnings: list[str],
        *,
        reason: str,
    ) -> Any:
        """Record a coercion warning and return the coerced value.

        S2-CR-44: extracts the duplicated 4-line pattern
        ``msg = ...; log.warning(...); warnings.append(...);
        data[field_name] = coerced`` that appeared in the int and
        float branches of ``_validate_non_numeric_fields``.

        Parameters
        ----------
        field_name
            The config field being coerced (e.g. ``"vad_threshold"``).
        val
            The original on-disk value (used in the warning message
            for diagnosis — the user sees what they had vs. what it
            was coerced to).
        coerced
            The successfully-coerced value.
        warnings
            The running warnings list (appended in place).
        reason
            Short reason string that completes the sentence
            ``"Config field '{field_name}' {reason} {val!r}, coerced
            to {coerced!r}"``. Example: ``"had non-int value"``.

        Returns
        -------
        Any
            The coerced value (the caller assigns this back to
            ``data[field_name]``).
        """
        msg = f"Config field '{field_name}' {reason} {val!r}, coerced to {coerced!r}"
        log.warning("[CONFIG] %s", msg)
        warnings.append(msg)
        return coerced

    @classmethod
    def _validate_non_numeric_fields(cls: type["Config"], data: dict[str, Any]) -> dict[str, Any]:
        """Validate and coerce bool / str / int / float fields in loaded config data.

        NEW-CQ-016: collects warnings in ``data['_load_warnings']`` so
        the caller (load()) can surface them via the
        ``last_load_warnings`` instance attribute (SCHEMA-1 / MED-I:
        no longer a dataclass field — see :meth:`__post_init__`).
        Previously warnings were only logged; the user had no way to
        know their config was corrected.

        NEW-DUP-005: this is NOT a duplicate of the type coercion that
        ``cls(**data)`` would do.  Python dataclasses do NOT coerce
        ``1`` → ``True`` or ``"true"`` → ``True`` — they store the raw
        value as-is, which would then fail downstream type checks
        (e.g. ``isinstance(cfg.autostart, bool)`` returns False for
        ``1``).  This validator is a migration layer that fixes up
        legacy on-disk configs (written by older versions of the app
        that used ints/strings for bool fields) BEFORE the dataclass
        constructor sees them.  Without it, a config.json with
        ``"autostart": 1`` would silently store ``1`` instead of
        ``True``, breaking every ``if cfg.autostart:`` check.

        The 4 hand-maintained field-name sets (``bool_fields`` /
        ``str_fields`` / ``int_fields`` / ``float_fields``) were
        replaced by :meth:`_derive_field_type_registry`, which derives
        the field list from the ``Config`` dataclass declaration. The
        per-type coercion logic is unchanged; only the field-name
        source changed. The ``optional_str_fields`` allowlist (fields
        that accept ``None`` in addition to ``str``) is preserved
        verbatim — it captures the ``str | None`` fields whose ``None``
        sentinel is meaningful (no microphone / no Qwen path / no
        Parakeet override).
        """
        warnings: list[str] = []
        # str | None fields where None is a meaningful sentinel
        # (no microphone / no Qwen path / no Parakeet override). The
        # str-validation branch allows None for these fields.
        optional_str_fields = {"parakeet_model_path", "qwen_model_path", "microphone"}
        registry = cls._derive_field_type_registry()
        defaults = cls()

        # VALID-3 (MED-L): int / float field coercion.  Mirrors the
        # bool/str pattern — if the on-disk value is not already the
        # correct type, attempt coercion; if coercion fails, reset to
        # default and add a warning so the user knows the field was
        # corrected.  Note: ``bool`` is a subclass of ``int`` in
        # Python, so we explicitly exclude bools from the int coercion
        # (a bool value for an int field is almost certainly a
        # misconfiguration, not a legacy int-as-bool — fall through to
        # the default-reset branch).

        for field_name, expected_type in registry.items():
            if field_name not in data:
                continue
            val = data[field_name]

            if expected_type is bool:
                if isinstance(val, bool):
                    continue
                # Coerce truthy/falsy values
                if val in (1, "1", "true", "True", "yes"):
                    data[field_name] = cls._warn_and_coerce(
                        field_name,
                        val,
                        True,
                        warnings,
                        reason="had non-bool value",
                    )
                elif val in (0, "0", "false", "False", "no", ""):
                    data[field_name] = cls._warn_and_coerce(
                        field_name,
                        val,
                        False,
                        warnings,
                        reason="had non-bool value",
                    )
                else:
                    data[field_name] = cls._warn_and_reset(
                        field_name,
                        val,
                        defaults,
                        warnings,
                        reason="had invalid value",
                    )

            elif expected_type is str:
                if isinstance(val, str):
                    continue
                if val is None and field_name in optional_str_fields:
                    continue
                data[field_name] = cls._warn_and_reset(
                    field_name,
                    val,
                    defaults,
                    warnings,
                    reason="had non-string value",
                )

            elif expected_type is int:
                # VALID-3 (MED-L): int field coercion.  Accepts ints,
                # floats (truncated via int()), and numeric strings.
                # Rejects bools (bool is a subclass of int but almost
                # certainly indicates a misconfigured field — reset to
                # default).  Rejects anything int() can't parse (lists,
                # dicts, None, non-numeric strings).
                #
                # ``bool`` is a subclass of ``int`` — exclude explicitly
                # so ``True``/``False`` values are treated as invalid
                # (the user probably toggled a checkbox they shouldn't
                # have).
                if isinstance(val, bool):
                    data[field_name] = cls._warn_and_reset(
                        field_name,
                        val,
                        defaults,
                        warnings,
                        reason="had bool value",
                    )
                    continue
                if isinstance(val, int):
                    # Already an int (and not a bool — handled above).
                    continue
                # Attempt coercion: int("42") → 42, int(3.7) → 3,
                # int("3.7") raises ValueError (int() doesn't accept
                # float-formatted strings — fall through to the
                # catch-all).
                try:
                    coerced = int(val)
                except (TypeError, ValueError):
                    data[field_name] = cls._warn_and_reset(
                        field_name,
                        val,
                        defaults,
                        warnings,
                        reason="had non-int value",
                    )
                    continue
                data[field_name] = cls._warn_and_coerce(
                    field_name,
                    val,
                    coerced,
                    warnings,
                    reason="had non-int value",
                )

            elif expected_type is float:
                # VALID-3 (MED-L): float field coercion.  Accepts
                # floats, ints, and numeric strings.  Rejects bools
                # and anything float() can't parse.
                if isinstance(val, bool):
                    data[field_name] = cls._warn_and_reset(
                        field_name,
                        val,
                        defaults,
                        warnings,
                        reason="had bool value",
                    )
                    continue
                if isinstance(val, float):
                    continue
                try:
                    coerced = float(val)
                except (TypeError, ValueError):
                    data[field_name] = cls._warn_and_reset(
                        field_name,
                        val,
                        defaults,
                        warnings,
                        reason="had non-float value",
                    )
                    continue
                data[field_name] = cls._warn_and_coerce(
                    field_name,
                    val,
                    coerced,
                    warnings,
                    reason="had non-float value",
                )

        # NEW-CQ-016: stash warnings so load() can surface them
        # via the ``last_load_warnings`` instance attribute.
        # S5-CR-38: APPEND to any existing ``_load_warnings`` rather
        # than overwriting — earlier load() stages (e.g.
        # ``_backup_before_downgrade``) may already have populated
        # the list with non-blocking notices (e.g. "config schema is
        # newer than this build supports"). Overwriting here would
        # silently drop those notices, defeating the surface-via-
        # last_load_warnings contract.
        existing_warnings = data.get("_load_warnings")
        if isinstance(existing_warnings, list):
            existing_warnings.extend(warnings)
        else:
            data["_load_warnings"] = warnings
        return data

    @property
    def config_dir(self) -> Path:
        return _config_dir()


# ──────────────────────────────────────────────────────────────────────────
# ARCH-REFAC-001: validator block moved to ``config_validators.py``.
# RW-06: the wildcard ``from voice_typer.server.config_validators import *``
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
    _make_optional_str_validator,
    _make_str_validator,
    _make_url_validator,
    validate_config,
    validate_config_update,
)
