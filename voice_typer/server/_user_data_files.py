"""User-data filename inventories shared by uninstall-purge and GDPR paths."""

# Canonical names as literals (imports deferred to bottom — circular import).
_RECOVERY_FILENAME: str = "recovery.json"
_LEGACY_RECOVERY_FILENAME: str = "voice-typer-recovery.json"

# Mirrors vocabulary.VOCAB_FILENAME.
_VOCAB_FILENAME: str = "vocabulary.json"
_LEGACY_VOCAB_FILENAME: str = "voice-typer-vocabulary.json"

# Mirrors templates.TEMPLATES_FILENAME.
_TEMPLATES_FILENAME: str = "templates.json"
_LEGACY_TEMPLATES_FILENAME: str = "voice-typer-templates.json"

# Corrections filename, ``text_cleanup.py`` uses the literal
_CORRECTIONS_FILENAME: str = "voice-typer-corrections.json"

# Onboarding state files. ``onboarding_status.py`` persists the
_ONBOARDING_STATUS_MARKER: str = ".onboarding_status.json"
_ONBOARDING_COMPLETE_MARKER: str = ".onboarding_complete"
_ONBOARDING_STARTED_MARKER: str = ".onboarding_started"
_ONBOARDING_FAIL_COUNT_MARKER: str = ".onboarding_fail_count"
_ONBOARDING_PROGRESS_MARKER: str = ".onboarding_progress"

# Personal-data log files. These are not owned by a single Python
_VOICE_TYPER_LOG: str = "voice-typer.log"
_PREWARM_LOG: str = "prewarm.log"
_RENDERER_ERRORS_LOG: str = "legacy-renderer-errors.log"
_RUST_LOG: str = "voice-typer-rust.log"

# Prewarm worker status file (O4: consolidated single JSON, canonical
_PREWARM_STATUS_FILE: str = "prewarm-status.json"
_LEGACY_PREWARM_STATUS_FILE: str = "prewarm_status.json"

# Backend PID file, written by ``single_instance.py`` (see
_BACKEND_PID_FILE: str = "backend.pid"

# Restart token, defensive entry, written by the restart helper to
_RESTART_TOKEN: str = ".restart_token"


# Files removed by the uninstall purge path. Every entry is the actual
_USER_DATA_FILES: tuple[str, ...] = (
    "config.json",
    "config.json.bak",
    "config.json.lock",
    _BACKEND_PID_FILE,
    "history.db",
    "history.db-wal",
    "history.db-shm",
    _RECOVERY_FILENAME,
    _LEGACY_RECOVERY_FILENAME,
    _VOCAB_FILENAME,
    _LEGACY_VOCAB_FILENAME,
    _TEMPLATES_FILENAME,
    _LEGACY_TEMPLATES_FILENAME,
    _CORRECTIONS_FILENAME,
    _ONBOARDING_STATUS_MARKER,
    _ONBOARDING_COMPLETE_MARKER,
    _ONBOARDING_STARTED_MARKER,
    _ONBOARDING_FAIL_COUNT_MARKER,
    _ONBOARDING_PROGRESS_MARKER,
    _VOICE_TYPER_LOG,
    _PREWARM_LOG,
    _RENDERER_ERRORS_LOG,
    _RUST_LOG,
    _PREWARM_STATUS_FILE,
    _LEGACY_PREWARM_STATUS_FILE,
    _RESTART_TOKEN,
)


# Files erased by GDPR Art. 17 ``delete_all_personal_data`` and
_GDPR_PERSONAL_FILES: tuple[str, ...] = (
    "history.db",
    "history.db-wal",
    "history.db-shm",
    _RECOVERY_FILENAME,
    _LEGACY_RECOVERY_FILENAME,
    "config.json",
    _CORRECTIONS_FILENAME,
    _VOCAB_FILENAME,
    _LEGACY_VOCAB_FILENAME,
    _TEMPLATES_FILENAME,
    _LEGACY_TEMPLATES_FILENAME,
    _VOICE_TYPER_LOG,
    _PREWARM_LOG,
    _RENDERER_ERRORS_LOG,
    _RUST_LOG,
    _PREWARM_STATUS_FILE,
    _LEGACY_PREWARM_STATUS_FILE,
    # config.json.bak retains plaintext API keys
    "config.json.bak",
    # config.json.lock can hold stale PID + username
    "config.json.lock",
    _RESTART_TOKEN,
)


# Glob-style inventories for corrupt-quarantine and pre-migration
_USER_DATA_GLOBS: tuple[str, ...] = (
    "history.db.corrupt-*",
    "history.db.corrupt-*-wal",
    "history.db.corrupt-*-shm",
    "history.db.pre-migration-v*.bak",
    "history.db.pre-migration-v*.bak-wal",
    "history.db.pre-migration-v*.bak-shm",
)

# GDPR personal-data globs, same set as ``_USER_DATA_GLOBS`` but
_GDPR_PERSONAL_GLOBS: tuple[str, ...] = (
    "history.db.corrupt-*",
    "history.db.corrupt-*-wal",
    "history.db.corrupt-*-shm",
    "history.db.pre-migration-v*.bak",
    "history.db.pre-migration-v*.bak-wal",
    "history.db.pre-migration-v*.bak-shm",
)


# Sanity check: verify the literals above match the canonical constants
try:
    from voice_typer.server.crash_recovery import (
        RECOVERY_FILENAME as _CANONICAL_RECOVERY_FILENAME,
    )
    from voice_typer.server.templates import (
        TEMPLATES_FILENAME as _CANONICAL_TEMPLATES_FILENAME,
    )
    from voice_typer.server.vocabulary import (
        VOCAB_FILENAME as _CANONICAL_VOCAB_FILENAME,
    )

    assert _RECOVERY_FILENAME == _CANONICAL_RECOVERY_FILENAME, (
        f"_RECOVERY_FILENAME drifted: literal {_RECOVERY_FILENAME!r} != "
        f"canonical {_CANONICAL_RECOVERY_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match crash_recovery.RECOVERY_FILENAME."
    )
    assert _VOCAB_FILENAME == _CANONICAL_VOCAB_FILENAME, (
        f"_VOCAB_FILENAME drifted: literal {_VOCAB_FILENAME!r} != "
        f"canonical {_CANONICAL_VOCAB_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match vocabulary.VOCAB_FILENAME."
    )
    assert _TEMPLATES_FILENAME == _CANONICAL_TEMPLATES_FILENAME, (
        f"_TEMPLATES_FILENAME drifted: literal {_TEMPLATES_FILENAME!r} != "
        f"canonical {_CANONICAL_TEMPLATES_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match templates.TEMPLATES_FILENAME."
    )

    # Onboarding markers are owned by ``onboarding_status.py``, the
    from voice_typer.server import onboarding_status

    assert _ONBOARDING_STATUS_MARKER == onboarding_status.ONBOARDING_STATUS_FILENAME, (
        f"_ONBOARDING_STATUS_MARKER drifted: literal {_ONBOARDING_STATUS_MARKER!r} != "
        f"canonical {onboarding_status.ONBOARDING_STATUS_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match onboarding_status.ONBOARDING_STATUS_FILENAME."
    )
    assert _ONBOARDING_COMPLETE_MARKER == onboarding_status._LEGACY_COMPLETE_MARKER, (
        f"_ONBOARDING_COMPLETE_MARKER drifted: literal {_ONBOARDING_COMPLETE_MARKER!r} != "
        f"canonical {onboarding_status._LEGACY_COMPLETE_MARKER!r}. Update the literal "
        f"in _user_data_files.py to match onboarding_status._LEGACY_COMPLETE_MARKER."
    )
    assert _ONBOARDING_STARTED_MARKER == onboarding_status._LEGACY_STARTED_MARKER, (
        f"_ONBOARDING_STARTED_MARKER drifted: literal {_ONBOARDING_STARTED_MARKER!r} != "
        f"canonical {onboarding_status._LEGACY_STARTED_MARKER!r}. Update the literal "
        f"in _user_data_files.py to match onboarding_status._LEGACY_STARTED_MARKER."
    )
    assert _ONBOARDING_FAIL_COUNT_MARKER == onboarding_status._LEGACY_FAIL_COUNT_MARKER, (
        f"_ONBOARDING_FAIL_COUNT_MARKER drifted: literal {_ONBOARDING_FAIL_COUNT_MARKER!r} != "
        f"canonical {onboarding_status._LEGACY_FAIL_COUNT_MARKER!r}. Update the literal "
        f"in _user_data_files.py to match onboarding_status._LEGACY_FAIL_COUNT_MARKER."
    )
except ImportError:
    # Circular import, happens when this module is imported directly
    pass


__all__ = [
    "_USER_DATA_FILES",
    "_GDPR_PERSONAL_FILES",
    "_USER_DATA_GLOBS",
    "_GDPR_PERSONAL_GLOBS",
]
