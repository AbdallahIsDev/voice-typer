"""Default-value constants for the ``config`` package.

Extracted from ``config/__init__.py`` to chip away
at the monolith. The constants here are imported by ``Config`` field
defaults and by external callers (``onboarding.py``,
``hotkey_dispatcher.py``, ``config_applier.py``, ``clipboard/manager.py``)
via ``from voice_typer.server.config import DEFAULT_HOTKEY`` — the
``config/__init__.py`` re-exports them so callers don't need to know
about this leaf module.

Import-safety: this module is imported at the TOP of
``config/__init__.py``. It must NOT import from
``voice_typer.server.config`` (circular) and should keep its top-level
imports limited to leaf stdlib + leaf project modules.
"""

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


# enumerates the user-data subdirs that live under ``_config_dir()``
# and should be removed on a "purge" uninstall. The list is sourced
# from the ``_config_dir()`` docstring + the actual dirs created by
# ``Config.save()`` / ``history_db`` / ``logging_setup`` / ``model_manager``
# / ``single_instance`` / ``credential_store`` / ``crash_recovery`` /
# ``vocabulary`` / ``templates`` / ``onboarding``. Keeping it in one
# place means the Linux prerm, the Windows NSIS uninstall hook, and
# the macOS Uninstall helper can all call the same Python entry point
# instead of each re-implementing (and drifting from) the file list.
# ``_USER_DATA_FILES`` (the file list) is imported from
# ``_user_data_files.py`` (see the import block in ``config/__init__.py``)
# so it is derived from the canonical ``*_FILENAME`` constants owned
# by each artifact's module. ``_USER_DATA_DIRS`` is still defined
# inline here because the five entries (``logs``, ``huggingface``,
# ``crashes``, ``native_logs``, ``electron-profile``) are stable
# directory names owned by several modules (no single canonical
# constant exists for each).
_USER_DATA_DIRS: tuple[str, ...] = (
    "logs",
    "db",  # history.db + -wal/-shm sidecars + corrupt/pre-migration backups (O2)
    "huggingface",  # HF model cache (potentially GB-sized)
    "crashes",
    "native_logs",
    "electron-profile",  # Electron/Chromium profile (caches, Local Storage)
)
