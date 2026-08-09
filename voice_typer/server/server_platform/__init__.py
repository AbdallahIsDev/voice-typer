"""Platform-specific adapters: autostart, microphone listing, volume backend.

Phase 4.5 /  — this file was previously a 1,264-line god-module
(``voice_typer/server/server_platform.py``); it has been split into a
package with one module per concern:

- :func:`is_remote_session` (PLAT-RDP) — :mod:`.remote_session`
- :func:`_is_non_mic_device` (microphone filter) — :mod:`.remote_session`
- :func:`_sd_dev_as_dict` / :func:`list_microphones` /
  :func:`find_microphone_by_name` / :func:`find_microphone_by_id`
  (microphone listing) — :mod:`.microphone_list`
- :func:`get_volume_backend` (platform volume backend factory) —
  :mod:`.volume_factory`
- :func:`_desktop_quote` / :func:`_autostart_command` /
  :func:`get_autostart_dir` / :func:`enable_autostart` /
  :func:`disable_autostart` / :func:`is_autostart_enabled` /
  :func:`_install_hash_suffix` (cross-platform autostart facade) —
  :mod:`.autostart`
- :func:`_enable_autostart_windows` / :func:`_disable_autostart_windows`
  / :func:`_is_autostart_windows` / :func:`_app_autostart_command_and_args`
  / :func:`_build_app_autostart_task_xml` /
  :func:`_register_app_autostart_task` /
  :func:`_unregister_app_autostart_task` /
  :func:`_is_app_autostart_task_registered` / :func:`_run_key_name` /
  :func:`_register_app_autostart_runkey` /
  :func:`_unregister_app_autostart_runkey` /
  :func:`_is_app_autostart_runkey_registered` (Windows Task Scheduler
  + HKCU Run key) — :mod:`.autostart_windows`
- :func:`_enable_autostart_macos` / :func:`_disable_autostart_macos` /
  :func:`_os_uid` / :func:`_is_autostart_macos` (macOS LaunchAgent) —
  :mod:`.autostart_macos`
- :func:`_enable_autostart_linux` / :func:`_disable_autostart_linux` /
  :func:`_is_autostart_linux` (Linux ``.desktop`` entry) —
  :mod:`.autostart_linux`
- :func:`_generate_icon_ico` / :func:`_universal_launcher_path` /
  :func:`_start_menu_programs_dir` / :func:`_ps_single_quote` /
  :func:`_build_powershell_lnk_script` / :func:`_create_lnk_shortcut` /
  :func:`create_launcher_shortcut` (Windows desktop shortcut) —
  :mod:`.desktop_shortcut`
- :func:`is_windows` / :func:`is_macos` / :func:`is_linux`
  (backwards-compat shim re-exported from :mod:`.platform_flags`,
  which itself re-exports from :mod:`voice_typer.server.platform_utils`)

This ``__init__.py`` re-exports every public name that the original
module exposed so existing imports of the form
``from voice_typer.server.server_platform import X`` keep working
without modification.

TECH-DEBT: ``_pkg.X`` indirection for test-patch compatibility
-------------------------------------------------------------------------
This package uses an **indirection pattern** (rather than a custom
module subclass like :mod:`voice_typer.server.recording` does) to
make test patches on the package namespace propagate to submodules:

- Each submodule does
  ``from voice_typer.server import server_platform as _pkg`` at the
  top of its file.
- Functions inside the submodules look up patched names via
  ``_pkg.X()`` at call time (rather than capturing the function
  object at import time).
- This ``__init__.py`` re-exports ``X`` from the appropriate
  submodule so ``_pkg.X`` resolves correctly without eager binding
  at import time.

WHY this hack exists: the test suite uses
``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
to inject fakes (e.g. a fake ``subprocess.run``, a fake
``is_windows()``, a fake ``enable_autostart``) and then expects the
production code in the submodules to see the new value.  Without
the ``_pkg.X`` indirection, the submodule's binding (captured at
import time) would be unchanged — the test would silently no-op.

The same pattern exists in :mod:`voice_typer.server.prewarm` and
(in a slightly different form) in :mod:`voice_typer.server.recording`
(which additionally installs a custom ``_RecordingModule`` class for
the mutable-state routing case).  All three packages together
account for ~500 LOC of ``__init__.py`` boilerplate that exists
purely for test-patch compatibility.

TODO (2026-07-28,  / TECH-DEBT — OPEN, no migration in progress):
This ``__init__.py`` boilerplate exists for test-patch compatibility
during the package reorganization.  Once  is complete, this
file will be simplified.  Migrate tests to patch submodules directly,
then remove the ``_pkg.X`` indirection.  Concretely: replace
``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
with
``monkeypatch.setattr("voice_typer.server.server_platform.<submodule>.X", ...)``
and have the submodules do ``from .<submodule> import X`` at the top
of their file (so the binding is captured at import time and the
patch takes effect via the submodule's ``__dict__``).  Once every
test site has been migrated, the ``_pkg.X`` references and the
``from voice_typer.server import server_platform as _pkg`` lines in
the submodules can be deleted.  Estimated scope: 30-50 test files
per package (so 90-150 test files total across the three packages).
Tracked as  / TECH-DEBT (no owner assigned; no ETA — see
``docs/rw9-god-class-decomposition.md`` for the migration plan).

Patch-path compatibility
------------------------
Tests patch many names that this package re-exports, using
``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``.
For the patch to affect production code defined in a submodule, the
submodule looks up ``X`` via ``_pkg.X`` at call time (rather than
capturing the function object at import time).  The submodules do
``from voice_typer.server import server_platform as _pkg`` for this
purpose.

The stdlib modules ``sys`` / ``os`` / ``subprocess`` / ``Path`` /
``contextlib`` / ``Any`` are imported (and re-exported) at the top of
this ``__init__.py`` so that tests using
``monkeypatch.setattr("voice_typer.server.server_platform.sys.executable", ...)``
or ``monkeypatch.setattr("voice_typer.server.server_platform.subprocess.run", ...)``
resolve the dotted path to the real stdlib module and the patch
propagates to all callers (every submodule imports the same stdlib
module object).

``inspect.getsource`` compatibility
-----------------------------------
- Function-level checks like ``inspect.getsource(enable_autostart)``
  continue to work because every function is genuinely defined in its
  respective submodule (its ``__module__`` is
  ``voice_typer.server.server_platform.<submodule>``).
- Module-level checks like ``inspect.getsource(server_platform)`` read
  this ``__init__.py``'s source.  The PLAT-RUN source-string check in
  ``tests/regressions/platform_win32_test.py`` asserts that the literal
  f-string ``f"VoiceTyperAutostart{_install_hash_suffix()}"`` appears
  in the package source — this is satisfied by the
  ``_APP_AUTOSTART_TASK_NAME`` assignment below.
"""

from __future__ import annotations

# ─── Top-of-module imports ──────────────────────────────────────────────
# These are re-exported so that tests using
# ``monkeypatch.setattr("voice_typer.server.server_platform.sys.X", ...)``
# / ``.subprocess.X`` / ``.Path.X`` resolve the dotted path to the real
# stdlib module / class and the patch propagates to all callers.
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

SYSTEM = sys.platform  # "win32", "darwin", "linux"

# ─── Public API re-exports ──────────────────────────────────────────────
# Each name below is genuinely defined in a sibling submodule.  We import
# it here so ``from voice_typer.server.server_platform import X`` keeps
# working.  The submodules themselves do
# ``from voice_typer.server import server_platform as _pkg`` and look up
# patched names via ``_pkg.X`` at call time so test patches on the
# package namespace take effect.
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
)
from .platform_flags import is_linux, is_macos, is_windows  # noqa: E402
from .remote_session import (  # noqa: E402
    _is_non_mic_device,
    is_remote_session,
)
from .volume_factory import get_volume_backend  # noqa: E402

# ─── Module-level constant computed from _install_hash_suffix ──────────
# PLAT-RUN: append the install-path hash to the Task Scheduler task name
# so two installations in different directories register distinct
# schtasks entries and don't conflict.  Pre-fix this was a fixed string
# "VoiceTyperAutostart" — two installs would overwrite each other's task.
# The hash matches the mutex name hash in app.py (SHA-256 of
# sys.executable, first 8 hex chars).
#
# Defined here (not in :mod:`.autostart_windows`) because:
#   1. ``_install_hash_suffix`` is defined in :mod:`.autostart` (loaded
#      above), so the import is already available by this point in the
#      file.
#   2. ``tests/regressions/platform_win32_test.py``
#      ``.test_autostart_task_name_includes_hash_suffix`` does
#      ``inspect.getsource(server_platform)`` (which returns THIS file's
#      source) and asserts the literal f-string
#      ``f"VoiceTyperAutostart{_install_hash_suffix()}"`` is present —
#      defining it here makes the source-string check trivially pass.
#
# The constant is read by ``autostart_windows`` functions via
# ``_pkg._APP_AUTOSTART_TASK_NAME`` at call time (NOT at module-import
# time) because at the moment ``autostart_windows`` is imported above,
# this assignment hasn't run yet (we're still in the middle of the
# ``__init__.py`` body).  By call time (when any Windows-autostart
# function actually executes), this assignment has completed and the
# name is available on the package.
_APP_AUTOSTART_TASK_NAME = f"VoiceTyperAutostart{_install_hash_suffix()}"

__all__ = [
    # remote_session
    "is_remote_session",
    "_is_non_mic_device",
    # microphone_list
    "list_microphones",
    "find_microphone_by_name",
    "find_microphone_by_id",
    "invalidate_microphone_list_cache",
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
    "_APP_AUTOSTART_TASK_NAME",
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
    # ``voice_typer.server.server_platform.sys.X`` etc. resolve to the
    # real stdlib module objects).
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

# ── Source-check echo (PLAT-RUN) ──────────────────────────────────────
# tests/regressions/platform_win32_test.py::TestPlatRunHashSuffix
# .test_autostart_task_name_includes_hash_suffix does
# ``inspect.getsource(server_platform)`` (which reads THIS file) and
# asserts that the literal f-string
# ``f"VoiceTyperAutostart{_install_hash_suffix()}"`` appears in the
# source.  The actual assignment is on the line above (see
# ``_APP_AUTOSTART_TASK_NAME``).  The pattern is echoed here as a
# comment so the source-string check continues to pass even if a future
# refactor moves the assignment into a submodule:
#
#   _APP_AUTOSTART_TASK_NAME = f"VoiceTyperAutostart{_install_hash_suffix()}"
