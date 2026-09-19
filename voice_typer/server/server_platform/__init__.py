"""Platform-specific adapters: autostart, microphone listing, volume backend."""

from __future__ import annotations

# These are re-exported so that tests using
import contextlib  # noqa: F401
import logging
import os  # noqa: F401
import subprocess  # noqa: F401
import sys  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import TYPE_CHECKING, Any  # noqa: F401

from voice_typer.server import _paths  # noqa: F401
from voice_typer.server.branding import APP_NAME  # noqa: F401

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.volume_backend_base import VolumeBackend  # noqa: F401

log = logging.getLogger(__name__)

# Each name below is genuinely defined in a sibling submodule.  We import
from .autostart import (  # noqa: E402
    _autostart_command,
    _desktop_quote,
    _install_hash,
    _install_hash_suffix,
    _install_identifier,
    _resolve_tauri_binary_for_autostart,
    disable_autostart,
    enable_autostart,
    get_autostart_dir,
    is_autostart_enabled,
)
from .autostart_linux import (  # noqa: E402
    _disable_autostart_linux,
    _enable_autostart_linux,
    _is_autostart_linux,
)
from .autostart_macos import (  # noqa: E402
    _disable_autostart_macos,
    _enable_autostart_macos,
    _is_autostart_macos,
    _os_uid,
)
from .autostart_windows import (  # noqa: E402
    _app_autostart_command_and_args,
    _build_app_autostart_task_xml,
    _cleanup_stale_runkey_entry,
    _disable_autostart_windows,
    _enable_autostart_windows,
    _extract_arguments_from_task_xml,
    _extract_command_from_task_xml,
    _is_app_autostart_runkey_registered,
    _is_app_autostart_startup_registered,
    _is_app_autostart_task_registered,
    _is_autostart_windows,
    _register_app_autostart_runkey,
    _register_app_autostart_startup,
    _register_app_autostart_task,
    _run_key_name,
    _startup_bat_name,
    _startup_bat_path,
    _unregister_app_autostart_runkey,
    _unregister_app_autostart_startup,
    _unregister_app_autostart_task,
    _validate_runkey_command,
    sweep_legacy_autostart_entries,
)
from .desktop_shortcut import (  # noqa: E402
    _build_powershell_lnk_script,
    _create_lnk_shortcut,
    _generate_icon_ico,
    _ps_single_quote,
    _start_menu_programs_dir,
    _universal_launcher_path,
    create_launcher_shortcut,
)
from .microphone_list import (  # noqa: E402
    _sd_dev_as_dict,
    find_microphone_by_id,
    find_microphone_by_name,
    invalidate_microphone_list_cache,
    list_microphones,
    resolve_mic_id_to_device_index,
)
from .platform_flags import SYSTEM, is_linux, is_macos, is_windows  # noqa: E402
from .remote_session import (  # noqa: E402
    _is_invalid_device_name,
    _is_non_mic_device,
    is_remote_session,
)
from .volume_factory import get_volume_backend  # noqa: E402

__all__ = [
    # remote_session
    "is_remote_session",
    "_is_non_mic_device",
    "_is_invalid_device_name",
    # microphone_list
    "list_microphones",
    "find_microphone_by_name",
    "find_microphone_by_id",
    "invalidate_microphone_list_cache",
    "resolve_mic_id_to_device_index",
    "_sd_dev_as_dict",
    # volume_factory
    "get_volume_backend",
    # autostart
    "enable_autostart",
    "disable_autostart",
    "is_autostart_enabled",
    "get_autostart_dir",
    "_autostart_command",
    "_desktop_quote",
    "_install_hash",
    "_install_hash_suffix",
    "_install_identifier",
    "_resolve_tauri_binary_for_autostart",
    # autostart_windows
    "_enable_autostart_windows",
    "_disable_autostart_windows",
    "_is_autostart_windows",
    "_extract_arguments_from_task_xml",
    "_app_autostart_command_and_args",
    "_build_app_autostart_task_xml",
    "_register_app_autostart_task",
    "_unregister_app_autostart_task",
    "_is_app_autostart_task_registered",
    "_run_key_name",
    "_register_app_autostart_runkey",
    "_unregister_app_autostart_runkey",
    "_is_app_autostart_runkey_registered",
    "_validate_runkey_command",
    "_cleanup_stale_runkey_entry",
    "_extract_command_from_task_xml",
    "_register_app_autostart_startup",
    "_unregister_app_autostart_startup",
    "_is_app_autostart_startup_registered",
    "_startup_bat_name",
    "_startup_bat_path",
    "sweep_legacy_autostart_entries",
    # autostart_macos
    "_enable_autostart_macos",
    "_disable_autostart_macos",
    "_os_uid",
    "_is_autostart_macos",
    # autostart_linux
    "_enable_autostart_linux",
    "_disable_autostart_linux",
    "_is_autostart_linux",
    # desktop_shortcut
    "_generate_icon_ico",
    "_universal_launcher_path",
    "_start_menu_programs_dir",
    "_ps_single_quote",
    "_build_powershell_lnk_script",
    "_create_lnk_shortcut",
    "create_launcher_shortcut",
    # platform_flags (re-exported from platform_utils for backwards compat)
    "is_windows",
    "is_macos",
    "is_linux",
    # module-level constants / proxies (re-exported so test patches of
    "SYSTEM",
    "log",
    "APP_NAME",
    "contextlib",
    "os",
    "subprocess",
    "sys",
    "Path",
    "Any",
]
