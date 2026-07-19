# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""Process-level metadata setter (BRAND-METADATA)."""

# BRAND-METADATA: On Windows the Python backend appears as a generic
# pythonw.exe in Task Manager.  We call the platform helper to set
# the console title and AppUserModelID, which improves the process
# identity wherever the OS supports it.


def _set_process_metadata() -> None:
    """Set process-level metadata (console title, AppUserModelID, etc.).

    BRAND-METADATA: On Windows the Python backend appears as a generic
    pythonw.exe in Task Manager.  We call the platform helper to set
    the console title and AppUserModelID, which improves the process
    identity wherever the OS supports it.
    """
    from voice_typer.server.branding import APP_NAME
    from voice_typer.server.platform_utils import _set_windows_process_metadata

    _set_windows_process_metadata(APP_NAME)


__all__ = ["_set_process_metadata"]
