"""Windows autostart — Task Scheduler + Startup .bat + HKCU Run key.

Phase 4.5 /  — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Implements three
parallel autostart mechanisms on Windows:

  - **Task Scheduler with a LogonTrigger** (preferred — fires at
    logon+0, split Command/Arguments, hidden; may need UAC elevation
    for a locked task created by an admin install).
  - **Startup-folder .bat** (admin-free fallback — always processed by
    Explorer at logon, no command-line parsing).
  - **HKCU Run key** (last resort — its raw command line can be
    rejected by the Windows 11 StartupApp launcher; see
    ``_validate_runkey_command``).

STARTUP-7 (superseded by AUTOSTART-ORDER-FIX): the enable order is
Task Scheduler first (its logon trigger fires earlier and more
predictably than Explorer-gated mechanisms), then the admin-free
Startup-folder .bat, with the HKCU Run key as the last resort (its raw
command line can be rejected by the Windows 11 StartupApp launcher -
see ``_validate_runkey_command``).

PLAT-RUN: append the install-path hash to the task name + Run-key name
so two installations in different directories register distinct entries
and don't conflict.  Pre-fix these were fixed strings — two installs
would overwrite each other's entries.  The hash matches the mutex name
hash in ``app.py`` (SHA-256 of ``sys.executable``, first 8 hex chars).

Submodule layout
----------------
This module is the FACADE for the Windows autostart mechanisms. It owns
the mechanism clusters that tests patch directly and the orchestrators
that sequence them; mechanism submodules hold the rest:

  - :mod:`._autostart_windows_task` — pure Task Scheduler XML parsers
    (``_extract_command_from_task_xml`` / ``_extract_arguments_from_task_xml``).
  - :mod:`._autostart_windows_sweep` — the one-time legacy-entry sweep
    (``sweep_legacy_autostart_entries`` + its ``_sweep_legacy_*`` /
    ``_entry_targets_this_install`` / ``_legacy_sweep_marker_path`` /
    ``_sweep_v1_marker_files`` helpers).
  - :mod:`._autostart_windows_uninstall` — uninstaller-path cleanup
    (``_unregister_all_voicetyper_runkeys`` / ``_unregister_all_voicetyper_tasks``).
  - :mod:`._autostart_windows_startup_bat` — the Startup-folder .bat
    register / unregister / is-registered trio.
  - :mod:`._autostart_windows_runkey` — the HKCU Run-key register /
    unregister / is-registered trio
    (``_register_app_autostart_runkey`` / ``_unregister_app_autostart_runkey`` /
    ``_is_app_autostart_runkey_registered``).

Every name defined in a submodule is re-imported at the bottom of this
module (after the facade's own definitions) so all existing imports of
``voice_typer.server.server_platform.autostart_windows`` and the
package-level re-exports keep resolving unchanged.

Patch-path compatibility
------------------------
Tests patch several names that these functions call at runtime, always
on the OWNING module. Resolution rules:

  - Names defined in THIS facade (orchestrators, the Task Scheduler
    register/unregister/is cluster, the Run-key naming/validation
    helpers ``_run_key_name`` / ``_validate_runkey_command`` /
    ``_cleanup_stale_runkey_entry``,
    ``_startup_bat_name`` / ``_startup_bat_path``) are resolved as
    plain module-global lookups at call time — tests patch them via
    ``monkeypatch.setattr(autostart_windows, "X", ...)``.
  - Names re-imported from the submodules (the bottom import block)
    resolve through the SAME facade module-global lookup, so facade
    patches on those names are seen by facade callers too. Submodule
    code that calls a facade-owned name reads it lazily through the
    facade module object (``from voice_typer.server.server_platform
    import autostart_windows as _aw`` inside the function) at call
    time — so facade patches propagate into submodule behavior as
    well.
  - ``_autostart_command`` / ``get_autostart_dir`` /
    ``_install_hash`` / ``_install_hash_suffix`` /
    ``_install_identifier`` / ``_resolve_tauri_binary_for_autostart`` /
    ``_APP_AUTOSTART_TASK_NAME`` — owned by :mod:`.autostart`; patched
    via ``monkeypatch.setattr(autostart_mod, "X", ...)``.  This module
    (and every ``_autostart_windows_*`` submodule) binds that module as
    ``_autostart_mod`` and resolves all of them through its attribute
    at call time.
  - ``is_windows`` — owned by :mod:`.platform_flags` (re-exported from
    :mod:`voice_typer.server.platform_utils`); bound into this module's
    namespace at import time and called directly, so tests patch
    ``monkeypatch.setattr(autostart_windows, "is_windows", ...)``.
    Submodule code reads it through the facade module object (lazy
    ``_aw.is_windows()``) for the same reason.

``inspect.getsource`` compatibility
-----------------------------------
The orchestrators and the Task Scheduler register / unregister /
is-registered cluster are genuinely defined here, so
``inspect.getsource(_register_app_autostart_task)`` etc. continue to
read from this file. Source-string checks for "no redundant
``sys.platform != 'win32'`` check before ``task_scheduler.is_supported()``"
(in :mod:`tests.test_platform_and_config`) read this file's source.
Moved functions (Run-key trio, sweep, uninstall, startup .bat, task-XML
parsers) report their new submodule file via getsource; no
source-string pin targets them.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
import sys
from pathlib import Path

# Patch-path bindings. Names defined in THIS module are resolved as
# plain module-global lookups at call time (tests patch
# ``monkeypatch.setattr(autostart_windows, "X", ...)``). ``_autostart_mod``
# binds the owning sibling module so ``_autostart_command`` /
# ``get_autostart_dir`` / ``_install_hash`` / ``_install_hash_suffix`` /
# ``_install_identifier`` / ``_resolve_tauri_binary_for_autostart`` /
# ``_APP_AUTOSTART_TASK_NAME`` resolve through ITS attribute at call time
# (tests patch that module). ``is_windows`` is bound from
# :mod:`.platform_flags` at import time and called directly.
from voice_typer.server.branding import APP_NAME
from voice_typer.server.server_platform import autostart as _autostart_mod
from voice_typer.server.server_platform.platform_flags import is_windows

log = logging.getLogger(__name__)

# STARTUP-7: Task Scheduler logon trigger fires earlier and more
# predictably than HKCU Run keys (which are gated by Windows Explorer's
# startup sequencing). We prefer the Task Scheduler path; the
# Startup-folder .bat is the admin-free fallback, and the HKCU Run key
# remains as the last resort (its raw command line can be rejected by
# the Windows 11 StartupApp launcher at logon — see
# ``_validate_runkey_command``).
#
# PLAT-RUN: append the install-path hash to the task name so two
# installations in different directories register distinct schtasks
# entries and don't conflict. Pre-fix this was a fixed string
# "VoiceTyperAutostart" — two installs would overwrite each other's
# task. The hash matches the mutex name hash in app.py (SHA-256 of
# sys.executable, first 8 hex chars).
#
# NOTE: ``_APP_AUTOSTART_TASK_NAME`` is defined in :mod:`.autostart`
# (next to its ``_install_hash_suffix`` dependency) and read at call
# time via ``_autostart_mod._APP_AUTOSTART_TASK_NAME`` so the value
# always reflects the owning module's attribute (patchable in tests).


def _enable_autostart_windows() -> bool:
    """STARTUP-7: register app autostart via Task Scheduler (preferred),
    Windows Startup-folder .bat (fallback), or HKCU Run key (tertiary).

    AUTOSTART-ORDER-FIX: the order is Task Scheduler FIRST, Startup
    .bat SECOND, HKCU Run key LAST:

      - **Task Scheduler** fires at logon+0 with split Command/Arguments
        fields (immune to command-line parsing), is hidden, and is the
        documented-preferred mechanism. Creating a task may require
        UAC elevation when a previous task was created by an admin
        install (locked task) — the ``_schtasks_elevated`` fallback
        handles that.
      - **Startup-folder .bat** needs NO admin elevation and is ALWAYS
        processed by Explorer at logon — the reliable fallback for
        standard users and locked-task machines.
      - **HKCU Run key** was previously FIRST because it needs no
        elevation, but its value is a raw command line that the
        Windows 11 StartupApp launcher can reject at logon (observed:
        Shell-Core 9707/9708 with PID 0 on every logon for a malformed
        value — see ``_validate_runkey_command``). It stays as the last
        resort with correct quoting (``subprocess.list2cmdline``).
    """
    if _register_app_autostart_task():
        with contextlib.suppress(Exception):
            _unregister_app_autostart_runkey()
        with contextlib.suppress(Exception):
            _unregister_app_autostart_startup()
        return True
    log.warning("[CONFIG] Task Scheduler autostart failed; trying Startup-folder .bat")
    if _register_app_autostart_startup():
        with contextlib.suppress(Exception):
            _unregister_app_autostart_runkey()
        return True
    log.warning("[CONFIG] Startup-folder .bat autostart failed; trying HKCU Run key")
    if _register_app_autostart_runkey():
        # Clean up a stale Startup .bat from a previous session (same
        # hygiene as the task branch above) so autostart can't fire twice.
        with contextlib.suppress(Exception):
            _unregister_app_autostart_startup()
        return True
    log.warning("[CONFIG] All three autostart mechanisms failed")
    return False


def _disable_autostart_windows() -> bool:
    """STARTUP-7: remove app autostart from ALL mechanisms."""
    removed_task = _unregister_app_autostart_task()
    removed_reg = _unregister_app_autostart_runkey()
    removed_startup = _unregister_app_autostart_startup()
    return removed_task or removed_reg or removed_startup


def _is_autostart_windows() -> bool:
    """STARTUP-7: True if autostart is registered via ANY of the three mechanisms."""
    return (
        _is_app_autostart_task_registered()
        or _is_app_autostart_runkey_registered()
        or _is_app_autostart_startup_registered()
    )


# ── Task Scheduler autostart (preferred) ──────────────────────────────


def _app_autostart_command_and_args() -> tuple[str, str]:
    """Return (pythonw_path, arguments) for the app autostart task.

        STARTUP-7: same launcher + --hidden + --delay <N> as the Run-key path,
        but split into Command + Arguments for the Task Scheduler XML so we
        avoid the cmd.exe wrapper (mirrors the prewarm task fix).

        ADR-0009 Issue 4: the delay was reduced from 30s to 15s (see
        _autostart_command() for the full rationale).

        PLAT-VENV: Uses system Python if running inside a virtualenv.

    WINDOWS-VENV-AUTOSTART: previously the code swapped to the
        system Python unconditionally when running in a venv, WITHOUT
        checking whether the system Python can actually import
        ``voice_typer.server.autostart_launcher``. If the user installed
        Voice Typer only inside the venv, the autostart task would use the
        system Python — which would fail at login with
        ``ModuleNotFoundError`` and silently never start the app. We now
        probe the system Python before swapping; if the probe fails, we
        keep the venv Python (and log a warning) so the autostart entry
        works for the current user.
    """
    from voice_typer.server.task_scheduler import _APP_AUTOSTART_DELAY_SECONDS

    delay_str = str(_APP_AUTOSTART_DELAY_SECONDS)

    launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    python_bin = str(pythonw) if pythonw.exists() else sys.executable

    # PLAT-VENV: detect virtualenv and use system Python instead.
    # probe whether the system Python can import
    # voice_typer.server.autostart_launcher BEFORE swapping — if the
    # venv is the only place voice_typer is installed, the system
    # Python would fail at login.
    if sys.prefix != sys.base_prefix:
        import shutil

        system_python = shutil.which("python.exe")
        if system_python:
            from voice_typer.server.server_platform.autostart import (
                _system_python_can_import_launcher,
            )

            if _system_python_can_import_launcher(system_python):
                python_bin = system_python
            else:
                log.warning(
                    "[AUTOSTART] Running inside venv (%s) but system Python "
                    "cannot import voice_typer.server.autostart_launcher "
                    "(probe failed). Keeping venv Python for the Windows "
                    "Task Scheduler entry — autostart will break if the "
                    "venv is deleted, but works for the current user.",
                    sys.executable,
                )
    # PLAT-VENV/SILENT-LOGON: the probe above may have swapped the
    # interpreter to the system python.exe (a console-subsystem binary).
    # Re-apply the pythonw.exe preference to the FINAL interpreter so
    # the logon task never flashes a console window (same preference as
    # the initial pick above, which only covered sys.executable).
    pythonw = Path(python_bin).parent / "pythonw.exe"
    if pythonw.exists():
        python_bin = str(pythonw)

    # AUTOSTART-CMD-VALIDATE: verify the resolved Python interpreter
    # path exists. If it doesn't (venv deleted, dev-mode install moved),
    # fall back to the Tauri binary with empty args (the Tauri binary
    # is the autostart target directly — no Python launcher needed).
    if not Path(python_bin).exists():
        log.warning(
            "[AUTOSTART] Resolved Python interpreter does not exist: %s "
            "— attempting Tauri binary fallback for Task Scheduler entry",
            python_bin,
        )
        try:
            from voice_typer.server.autostart_launcher import _tauri_binary

            tauri_bin = _tauri_binary()
        except Exception:
            tauri_bin = None
        if tauri_bin:
            log.info(
                "[AUTOSTART] Using Tauri binary for Task Scheduler entry (no Python interpreter available): %s",
                tauri_bin,
            )
            return tauri_bin, ""
        log.error(
            "[AUTOSTART] No Python interpreter AND no Tauri binary "
            "available — Task Scheduler entry will be non-functional"
        )
    args = f'"{launcher}" --hidden --delay {delay_str}'
    log.info("[AUTOSTART] Resolved Task Scheduler command: %s %s", python_bin, args)
    return python_bin, args


def _build_app_autostart_task_xml() -> str:
    """Build the Task Scheduler XML for the app autostart task.

    STARTUP-7: fires at logon with PT0S delay (Run keys fire ~33 s
    after logon; Task Scheduler logon triggers fire immediately).
    The launcher's ``--delay <N>`` flag gives prewarm a head start on
    warming the OS file cache.

    ADR-0009 Issue 4: the delay was reduced from 30s to 15s (see
    _autostart_command() for the full rationale).
    """
    import xml.etree.ElementTree as ET

    python_exe, arguments = _app_autostart_command_and_args()

    root = ET.Element(
        "Task",
        {
            "version": "1.4",
            "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task",
        },
    )
    reg = ET.SubElement(root, "RegistrationInfo")
    desc = ET.SubElement(reg, "Description")
    desc.text = (
        f"Launches {APP_NAME} at user logon. Safe to disable or delete; "
        "the app can still be started manually from the Start Menu."
    )
    uri = ET.SubElement(reg, "URI")
    uri.text = f"{_autostart_mod._APP_AUTOSTART_TASK_NAME}"

    triggers = ET.SubElement(root, "Triggers")
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    # PT0S: fire immediately at logon (launcher's --delay <N> handles
    # the prewarm head-start; no Task Scheduler delay needed).
    ET.SubElement(logon, "Delay").text = "PT0S"

    principal = ET.SubElement(root, "Principals")
    princ = ET.SubElement(principal, "Principal", {"id": "Author"})
    ET.SubElement(princ, "LogonType").text = "InteractiveToken"
    ET.SubElement(princ, "RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "ExecutionTimeLimit").text = "PT0S"  # no limit
    ET.SubElement(settings, "Hidden").text = "true"
    ET.SubElement(settings, "RunOnlyIfNetworkAvailable").text = "false"
    ET.SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    ET.SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"

    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    exec_el = ET.SubElement(actions, "Exec")
    # STARTUP-1 lesson: use pythonw.exe directly, no cmd.exe wrapper.
    ET.SubElement(exec_el, "Command").text = python_exe
    ET.SubElement(exec_el, "Arguments").text = arguments

    return ET.tostring(root, encoding="unicode")


def _register_app_autostart_task() -> bool:
    """Register the app autostart Task Scheduler task. Returns True on success.

    Bug fix: removed redundant sys.platform != 'win32' check —
    task_scheduler.is_supported() already handles platform detection
    and is the single source of truth. The redundant check made the
    function untestable on non-Windows without monkeypatching sys.platform.
    """
    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        xml_def = _build_app_autostart_task_xml()
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as tf:
            tf.write(xml_def)
            temp_xml = tf.name
        try:
            with contextlib.suppress(Exception):
                task_scheduler._schtasks(
                    ["/Delete", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/F"], capture=True
                )
            rc, output = task_scheduler._schtasks(
                ["/Create", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                capture=True,
            )
            if rc != 0 and "access is denied" in (output or "").lower():
                log.info("[CONFIG] Non-elevated schtasks failed — retrying with UAC elevation prompt")
                rc, output = task_scheduler._schtasks_elevated(
                    ["/Create", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                )
            if rc == 0:
                log.info("[CONFIG] App autostart registered via Task Scheduler (logon trigger)")
                return True
            log.warning("[CONFIG] Task Scheduler autostart registration failed: %s", output.strip())
            return False
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp_xml)
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart registration raised: %s", e)
        return False


def _unregister_app_autostart_task() -> bool:
    """Remove the app autostart Task Scheduler task. Returns True on success.

    Bug fix: removed redundant sys.platform != 'win32' check.
    """
    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        rc, output = task_scheduler._schtasks(
            ["/Delete", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/F"],
            capture=True,
        )
        if rc == 0:
            log.info("[CONFIG] App autostart Task Scheduler task removed")
            return True
        # already absent
        return "cannot find" in output.lower() or "does not exist" in output.lower()
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart removal raised: %s", e)
        return False


def _is_app_autostart_task_registered() -> bool:
    """True if the app autostart Task Scheduler task exists AND its
    command path is valid (points at an existing file).

    Bug fix: removed redundant sys.platform != 'win32' check.

    AUTOSTART-CMD-VALIDATE: previously this function returned True if
    the schtasks /Query succeeded (task exists), WITHOUT verifying the
    task's <Command> path actually exists on disk. If the venv was
    deleted after registration, the task would still "exist" but its
    command would point at a nonexistent pythonw.exe — the task would
    fire at login, fail silently, and the Settings toggle would show
    "autostart enabled" while the app never started. We now parse the
    task XML, extract the <Command> element, and verify the path
    exists. If the path is dead, we return False (the task is stale).
    """
    # (pyrefly): import subprocess BEFORE the try block so the
    # ``except subprocess.CalledProcessError`` clause has a guaranteed-bound
    # name (matches the pattern at line ~826).
    import subprocess

    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        rc, output = task_scheduler._schtasks(["/Query", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/XML"])
        if rc != 0:
            return False
        # AUTOSTART-CMD-VALIDATE: parse the task XML and verify the
        # <Command> path exists. If the command points at a deleted
        # pythonw.exe (venv removed), the task is stale — report False
        # so the Settings toggle reflects the actual state.
        command_path = _extract_command_from_task_xml(output)
        if command_path is None:
            # Could not parse the XML — conservatively report True
            # (the task exists; we just can't validate the command).
            log.debug(
                "[AUTOSTART] Could not parse <Command> from task XML — "
                "reporting task as registered (cannot validate path)"
            )
            return True
        if not Path(command_path).exists():
            log.warning(
                "[AUTOSTART] Task Scheduler task exists but its command "
                "path does not exist: %s — reporting as NOT registered "
                "(stale task)",
                command_path,
            )
            return False
        log.debug(
            "[AUTOSTART] Task Scheduler task registered with valid command: %s",
            command_path,
        )
        return True
    except (OSError, subprocess.CalledProcessError, FileNotFoundError):
        log.debug("[PLATFORM] _is_app_autostart_task_registered failed", exc_info=True)
        return False


# ── HKCU Run-key autostart (fallback) ─────────────────────────────────
#
# The register / unregister / is-registered trio lives in
# :mod:`._autostart_windows_runkey` (same layout as the Startup .bat
# trio in :mod:`._autostart_windows_startup_bat`). The naming helper
# (``_run_key_name``), the shared raw-string command validator
# (``_validate_runkey_command`` — the doubled-backslash check that
# makes the self-heal work), and the stale-entry cleanup helper stay
# here: every mechanism (Run key, Startup .bat, legacy sweep) reads
# them through the facade module object.


def _run_key_name() -> str:
    """PLAT-RUN: Return a deterministic registry value name based on install
    path, in the canonical ``com.voicetyper.*`` reverse-DNS namespace.

    Uses the same stable per-install hash as the Task Scheduler task
    name and the Startup-folder .bat (see
    ``autostart._install_hash``) so all three autostart mechanisms
    agree on one name regardless of which interpreter launched the
    process. Different installs (e.g. stable vs dev) hash differently
    and don't conflict, and stale entries from removed installs can be
    cleaned up.

    The hash MUST NOT depend on ``sys.executable``: the app can launch
    via the console shim (``python.exe`` / ``voice-typer.exe``), the
    dev venv, or the autostart launcher (``pythonw.exe``) — each has a
    different ``sys.executable``, so a name derived from it would be
    registered by one process and never found by the next (the
    perpetual "Config says autostart=true but it is disabled --
    enabling" re-registration loop).
    """
    return f"com.voicetyper.autostart_{_autostart_mod._install_hash()}"


def _validate_runkey_command(value: str) -> bool:
    """Validate that a Run-key command line's exe path exists on disk.

    Parses the command line with ``shlex.split(value, posix=False)``
    (the cross-platform-safe Windows-command-line splitter), extracts
    the first token (the exe path), strips surrounding quotes, and
    checks if the path exists.

    Returns ``True`` if the exe path exists (or if the command line is
    empty/ambiguous — we err on the side of "valid" to avoid deleting
    entries we can't parse confidently). Returns ``False`` only when
    we're CERTAIN the exe path doesn't exist (quoted path or unquoted
    single-token path that doesn't exist on disk).

    This mirrors the CONSERVATIVE-DELETE policy from the stale-entry
    cleanup loop in :func:`_register_app_autostart_runkey`.
    """
    if not value or not isinstance(value, str):
        return True  # empty/None — don't claim stale (caller checks truthy)
    tokens = shlex.split(value, posix=False)
    if not tokens:
        return True  # malformed — don't claim stale
    exe_token = tokens[0]
    exe_path = exe_token.strip('"')
    if not exe_path:
        return True  # malformed — don't claim stale
    # AUTOSTART-QUOTING-FIX: a doubled backslash inside a Windows path
    # (e.g. ``"C:\\Users\\11\\...pythonw.exe"``) is a MALFORMED command
    # line — the freedesktop Exec quoting bug baked ``\\`` into every
    # Run-key value. ``Path.exists()`` collapses ``\\`` to ``\``, so it
    # reports such paths as existing and the broken entry is never
    # re-registered (the "autostart fixed but still broken at logon"
    # loop). Detect the malformed value here and report it as stale so
    # the caller cleans it up and re-registers with correct quoting.
    # UNC paths (``\\server\share``) legitimately start with a doubled
    # separator and are exempt.
    if is_windows() and "\\\\" in exe_path and not exe_path.startswith("\\\\"):
        return False
    was_quoted = exe_token.startswith('"')
    has_multiple_tokens = len(tokens) > 1
    # CONSERVATIVE-DELETE: only claim stale when we're CERTAIN —
    # quoted path or unquoted single-token path that doesn't exist.
    # Ambiguous unquoted spaced paths are preserved (can't recover
    # the full exe path without quotes).
    if not was_quoted and has_multiple_tokens:
        return True  # ambiguous — preserve (can't validate confidently)
    return Path(exe_path).exists()


def _cleanup_stale_runkey_entry(reg_key_name: str) -> None:
    """Best-effort cleanup of a stale HKCU Run-key entry.

    Opens the Run key with ``KEY_SET_VALUE`` and deletes the named
    value. Non-fatal: any error (key not found, permission denied,
    etc.) is logged at debug and swallowed so the caller
    (``_is_app_autostart_runkey_registered``) doesn't raise.
    """
    try:
        import winreg
    except ImportError:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, reg_key_name)
            log.info("[AUTOSTART] Cleaned up stale Run-key entry: %s", reg_key_name)
        except FileNotFoundError:
            pass  # already gone
        finally:
            winreg.CloseKey(key)
    except OSError as exc:
        log.debug(
            "[AUTOSTART] Could not clean up stale Run-key entry %s: %s",
            reg_key_name,
            exc,
        )


# ── Windows Startup-folder .bat naming (mechanism trio in
# ._autostart_windows_startup_bat) ──────────────────────────────────────
#
# The register / unregister / is-registered trio lives in
# :mod:`._autostart_windows_startup_bat`. The naming helpers stay here
# (their f-strings are drift-pinned literals and every mechanism reads
# them through the facade module object).


def _startup_bat_name() -> str:
    """Return the Startup-folder .bat file name (hash-suffixed).

    Uses the same install-path hash as the Run-key name (PLAT-RUN) so
    two installations in different directories register distinct .bat
    files and don't conflict.  The name lives in the canonical
    ``com.voicetyper.*`` reverse-DNS namespace (RDNN), consistent with
    the Run-key value name and Task Scheduler task names.
    """
    return f"com.voicetyper.autostart{_autostart_mod._install_hash_suffix()}.bat"


def _startup_bat_path() -> Path:
    """Return the full path to the Startup-folder .bat file.

    Delegates to :func:`get_autostart_dir` (in :mod:`.autostart`) which
    returns the platform-specific autostart directory. On Windows this
    is ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``.
    """
    return _autostart_mod.get_autostart_dir() / _startup_bat_name()


# ─── Facade re-exports (moved mechanism submodules) ───────────────────
# These names are genuinely defined in the ``_autostart_windows_*``
# sibling modules (see the docstring's "Submodule layout"); they are
# re-imported here so ``voice_typer.server.server_platform.autostart_windows``
# keeps exposing the exact same namespace it always did. Facade callers
# resolve them as plain module-global lookups at call time, so facade
# patches (``monkeypatch.setattr(autostart_windows, "X", ...)``) are
# seen.
from ._autostart_windows_runkey import (  # noqa: E402, F401
    _is_app_autostart_runkey_registered,
    _register_app_autostart_runkey,
    _unregister_app_autostart_runkey,
)
from ._autostart_windows_startup_bat import (  # noqa: E402, F401
    _is_app_autostart_startup_registered,
    _register_app_autostart_startup,
    _unregister_app_autostart_startup,
)
from ._autostart_windows_sweep import (  # noqa: E402, F401
    _entry_targets_this_install,
    _legacy_sweep_marker_path,
    _sweep_legacy_runkeys,
    _sweep_legacy_startup_bats,
    _sweep_legacy_tasks,
    _sweep_v1_marker_files,
    sweep_legacy_autostart_entries,
)
from ._autostart_windows_task import (  # noqa: E402, F401
    _extract_arguments_from_task_xml,
    _extract_command_from_task_xml,
)
from ._autostart_windows_uninstall import (  # noqa: E402, F401
    _unregister_all_voicetyper_runkeys,
    _unregister_all_voicetyper_tasks,
)
