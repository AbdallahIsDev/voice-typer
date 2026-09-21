"""Default-value constants for the ``config`` package."""

# canonical default for the clipboard restore delay (ms).
DEFAULT_CLIPBOARD_RESTORE_DELAY_MS: int = 150

# canonical default hotkey. Previously the literal ``"<caps_lock>"``
DEFAULT_HOTKEY: str = "<caps_lock>"


def _default_hotkey_for_platform() -> str:
    """Return the platform-appropriate default hotkey."""
    return DEFAULT_HOTKEY


# enumerates the user-data subdirs that live under ``_config_dir()``
_USER_DATA_DIRS: tuple[str, ...] = (
    "logs",
    "db",  # history.db + -wal/-shm sidecars + corrupt/pre-migration backups (O2)
    "huggingface",  # HF model cache (potentially GB-sized)
    "crashes",
    "native_logs",
    "legacy-profile",  # LEGACY Chromium profile directory (caches, Local Storage)
)
