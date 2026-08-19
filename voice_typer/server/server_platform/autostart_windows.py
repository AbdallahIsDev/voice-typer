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

STARTUP-7: the Run key is tried FIRST because it requires no admin
elevation (HKCU is per-user, always writable).  Task Scheduler is tried
only as a fallback because registering a scheduled task may require UAC
elevation if a previous task was created by an admin install (locked
task).  The Run key fires ~33 s after logon, which is soon enough for
the autostart launcher (which has a ``--delay 15`` internal delay).

PLAT-RUN: append the install-path hash to the task name + Run-key name
so two installations in different directories register distinct entries
and don't conflict.  Pre-fix these were fixed strings — two installs
would overwrite each other's entries.  The hash matches the mutex name
hash in ``app.py`` (SHA-256 of ``sys.executable``, first 8 hex chars).

Patch-path compatibility
------------------------
Tests patch several names that this module's functions call at runtime:

  - ``_register_app_autostart_runkey`` / ``_register_app_autostart_task``
    — patched via ``monkeypatch.setattr(server_platform, "X", lambda: ...)``.
    ``_enable_autostart_windows`` looks them up via ``_pkg.X()``.
  - ``_unregister_app_autostart_task`` / ``_unregister_app_autostart_runkey``
    — patched similarly.  ``_disable_autostart_windows`` looks them up via
    ``_pkg.X()``.
  - ``_is_app_autostart_task_registered`` / ``_is_app_autostart_runkey_registered``
    — patched similarly.  ``_is_autostart_windows`` looks them up via
    ``_pkg.X()``.
  - ``_build_app_autostart_task_xml`` — patched via
    ``monkeypatch.setattr(server_platform, "_build_app_autostart_task_xml", lambda: ...)``.
    ``_register_app_autostart_task`` looks it up via ``_pkg.X()``.
  - ``_app_autostart_command_and_args`` — not patched by any test, but
    ``_build_app_autostart_task_xml`` looks it up via ``_pkg.X()`` for
    consistency with the rest of the bridge.
  - ``_autostart_command`` — patched via
    ``monkeypatch.setattr(platform_mod, "_autostart_command", lambda: ...)``.
    ``_register_app_autostart_runkey`` looks it up via ``_pkg.X()``.
  - ``_APP_AUTOSTART_TASK_NAME`` — module-level constant computed in
    ``__init__.py``.  Read via ``_pkg._APP_AUTOSTART_TASK_NAME`` at call
    time so the constant is available by the time any of these functions
    actually executes (it is NOT available at this module's import time
    because ``__init__.py`` is still loading when this file is imported).

``inspect.getsource`` compatibility
-----------------------------------
All twelve functions are genuinely defined here, so
``inspect.getsource(_register_app_autostart_task)`` etc. continue to
read from this file.  Source-string checks for "no redundant
``sys.platform != 'win32'`` check before ``task_scheduler.is_supported()``"
(in :mod:`tests.test_platform_and_config`) read this file's source.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import sys
from pathlib import Path

# Patch-path bridge: route lookups of the patched names listed in the
# module docstring through the package namespace so test patches of the
# form ``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
# keep affecting production code defined here.
from voice_typer.server import server_platform as _pkg
from voice_typer.server.branding import APP_NAME

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
# NOTE: ``_APP_AUTOSTART_TASK_NAME`` is defined in the package
# ``__init__.py`` (not here) because (a) it depends on
# ``_install_hash_suffix`` (defined in :mod:`.autostart`) which is also
# re-exported by ``__init__.py``, and (b) tests in
# ``tests/regressions/platform_win32_test.py`` do
# ``inspect.getsource(server_platform)`` (which returns the
# ``__init__.py`` source) and assert the literal f-string
# ``f"VoiceTyperAutostart{_install_hash_suffix()}"`` is present.  The
# constant is read at call time via ``_pkg._APP_AUTOSTART_TASK_NAME`` so
# it is available by the time any function below actually executes
# (``__init__.py`` is still loading when this file is first imported).


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
    if _pkg._register_app_autostart_task():
        with contextlib.suppress(Exception):
            _pkg._unregister_app_autostart_runkey()
        with contextlib.suppress(Exception):
            _pkg._unregister_app_autostart_startup()
        return True
    log.warning("[CONFIG] Task Scheduler autostart failed; trying Startup-folder .bat")
    if _pkg._register_app_autostart_startup():
        with contextlib.suppress(Exception):
            _pkg._unregister_app_autostart_runkey()
        return True
    log.warning("[CONFIG] Startup-folder .bat autostart failed; trying HKCU Run key")
    if _pkg._register_app_autostart_runkey():
        # Clean up a stale Startup .bat from a previous session (same
        # hygiene as the task branch above) so autostart can't fire twice.
        with contextlib.suppress(Exception):
            _pkg._unregister_app_autostart_startup()
        return True
    log.warning("[CONFIG] All three autostart mechanisms failed")
    return False


def _disable_autostart_windows() -> bool:
    """STARTUP-7: remove app autostart from ALL mechanisms."""
    removed_task = _pkg._unregister_app_autostart_task()
    removed_reg = _pkg._unregister_app_autostart_runkey()
    removed_startup = _pkg._unregister_app_autostart_startup()
    return removed_task or removed_reg or removed_startup


def _is_autostart_windows() -> bool:
    """STARTUP-7: True if autostart is registered via ANY of the three mechanisms."""
    return (
        _pkg._is_app_autostart_task_registered()
        or _pkg._is_app_autostart_runkey_registered()
        or _pkg._is_app_autostart_startup_registered()
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

    python_exe, arguments = _pkg._app_autostart_command_and_args()

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
    uri.text = f"{_pkg._APP_AUTOSTART_TASK_NAME}"

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
        xml_def = _pkg._build_app_autostart_task_xml()
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as tf:
            tf.write(xml_def)
            temp_xml = tf.name
        try:
            with contextlib.suppress(Exception):
                task_scheduler._schtasks(["/Delete", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/F"], capture=True)
            rc, output = task_scheduler._schtasks(
                ["/Create", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                capture=True,
            )
            if rc != 0 and "access is denied" in (output or "").lower():
                log.info("[CONFIG] Non-elevated schtasks failed — retrying with UAC elevation prompt")
                rc, output = task_scheduler._schtasks_elevated(
                    ["/Create", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
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
        rc, output = task_scheduler._schtasks(["/Delete", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/F"], capture=True)
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
        rc, output = task_scheduler._schtasks(["/Query", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/XML"])
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


def _extract_command_from_task_xml(xml_str: str) -> str | None:
    """Extract the ``<Command>`` element's text from a Task Scheduler XML.

    Returns the command path as a string, or ``None`` if the XML is
    malformed or has no ``<Command>`` element. Used by
    :func:`_is_app_autostart_task_registered` to validate that the
    task's command path actually exists on disk (stale-task detection).

    The Task Scheduler XML uses the namespace
    ``http://schemas.microsoft.com/windows/2004/02/mit/task``. We
    search for any element whose local name is ``Command`` (ignoring
    the namespace) so the parse is robust to namespace prefix changes.
    """
    if not xml_str:
        return None
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        # Search for any element with local name "Command" (the Task
        # Scheduler XML places it under Actions/Exec/Command, but we
        # search recursively to be robust to schema variations).
        for elem in root.iter():
            tag = elem.tag
            # Strip namespace prefix if present (e.g. "{ns}Command").
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            if tag == "Command" and elem.text:
                return elem.text.strip()
    except Exception:
        log.debug("[AUTOSTART] _extract_command_from_task_xml parse failed", exc_info=True)
    return None


def _extract_arguments_from_task_xml(xml_str: str) -> str | None:
    """Extract the ``<Arguments>`` element's text from a Task Scheduler XML.

    Companion to :func:`_extract_command_from_task_xml` — used by the
    legacy-entry sweep (:func:`_sweep_legacy_tasks`) to inspect the
    task's command-line arguments (which embed the per-install
    ``autostart_launcher.py`` path). Returns the arguments string, or
    ``None`` if the XML is malformed or has no ``<Arguments>`` element.
    """
    if not xml_str:
        return None
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        for elem in root.iter():
            tag = elem.tag
            # Strip namespace prefix if present (e.g. "{ns}Arguments").
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            if tag == "Arguments" and elem.text:
                return elem.text.strip()
    except Exception:
        log.debug("[AUTOSTART] _extract_arguments_from_task_xml parse failed", exc_info=True)
    return None


# ── HKCU Run-key autostart (fallback) ─────────────────────────────────


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
    return f"com.voicetyper.autostart_{_pkg._install_hash()}"


def _register_app_autostart_runkey() -> bool:
    """Register app autostart via HKCU Run key (admin-free fallback)."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: Use deterministic key name based on install path
    # to prevent conflicting entries from different installs
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            cmd = _pkg._autostart_command()
            winreg.SetValueEx(key, reg_key_name, 0, winreg.REG_SZ, cmd)
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart enabled via HKCU Run key (fallback): %s", cmd)

        # PLAT-RUN: Clean stale entries whose path no longer exists.
        # Matches BOTH the current reverse-DNS scheme (com.voicetyper.*)
        # and the pre-rename bare scheme (VoiceTyper_*).
        #
        # parse the Run-key command line with a Windows-aware
        # splitter before extracting the exe path. Pre-fix, the code did
        # ``value.strip('"').split('"')[0] if '"' in value else value.split()[0]``
        # which misparses UNQUOTED spaced paths (e.g.
        # ``C:\Program Files\VoiceTyper\app.exe --delay 15``) — the
        # ``value.split()[0]`` branch returns ``C:\Program`` (NOT a real
        # path), ``Path('C:\\Program').exists()`` is False, and the
        # cleanup silently DELETES the other install's Run-key entry.
        # This breaks multi-install autostart (a PLAT-RUN supported
        # scenario) when any install lives in a spaced path (common:
        # ``C:\Program Files\...``).
        #
        # ``shlex.split(value, posix=False)`` parses a Windows-style
        # command line: it preserves backslashes, treats double quotes
        # as argument delimiters (the quoted token is returned as a
        # single element WITH the surrounding quotes preserved), and
        # splits on whitespace outside quotes. The first token is the
        # exe path (quoted or not); we strip the surrounding quotes to
        # get the actual filesystem path.
        #
        # CONSERVATIVE-DELETE policy: an UNQUOTED value with multiple
        # tokens (spaces in the command line) is ambiguous — the actual
        # exe path might be a longer space-separated prefix that we
        # can't recover without quotes. For such entries, we DO NOT
        # delete even if the first token doesn't exist as a file,
        # because deleting a legitimate entry is worse than leaving a
        # stale one in the registry. We only delete when we're CERTAIN
        # the entry is stale:
        #   - quoted path that doesn't exist (unambiguous), OR
        #   - unquoted single-token path that doesn't exist (unambiguous).
        #
        # Note: ``shlex.split(value, posix=False)`` is the documented
        # cross-platform-safe Windows-command-line splitter that does
        # NOT require the Windows-only ``shell32.CommandLineToArgvW``
        # — which keeps this code testable on non-Windows CI.
        try:
            run_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS
            )
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(run_key, i)
                    if (
                        name.startswith(("VoiceTyper", "com.voicetyper"))
                        and name != reg_key_name
                        and isinstance(value, str)
                    ):
                        # use shlex.split(posix=False) so quoted
                        # spaced paths are parsed correctly (the quoted
                        # token is a single element). For unquoted
                        # spaced paths, the parse is inherently ambiguous
                        # — see the CONSERVATIVE-DELETE policy above.
                        tokens = shlex.split(value, posix=False)
                        if not tokens:
                            # Malformed / empty value — skip cleanup.
                            i += 1
                            continue
                        exe_token = tokens[0]
                        # shlex.split(posix=False) preserves the
                        # surrounding quotes in the token; strip them so
                        # we get the actual filesystem path.
                        exe_path = exe_token.strip('"')
                        if not exe_path:
                            # Malformed entry (e.g. just quotes) — skip.
                            i += 1
                            continue
                        was_quoted = exe_token.startswith('"')
                        has_multiple_tokens = len(tokens) > 1
                        path_exists = Path(exe_path).exists()
                        if not path_exists:
                            # Only delete if we're CERTAIN the entry is
                            # stale (see CONSERVATIVE-DELETE policy).
                            # Ambiguous unquoted spaced paths are
                            # preserved (never deleted) to avoid
                            # breaking legitimate multi-install autostart.
                            if was_quoted or not has_multiple_tokens:
                                winreg.DeleteValue(run_key, name)
                                log.info("[AUTOSTART] Removed stale entry: %s", name)
                                continue
                            # else: ambiguous unquoted spaced path —
                            # be conservative, skip deletion.
                            log.debug(
                                "[AUTOSTART] Skipping ambiguous unquoted "
                                "spaced-path entry (cannot determine if "
                                "stale): %s",
                                name,
                            )
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(run_key)
        except Exception:
            log.debug("[AUTOSTART] registry Run-key cleanup failed", exc_info=True)

        return True
    except OSError as e:
        log.warning("[CONFIG] HKCU Run key autostart failed: %s", e)
        return False


def _unregister_app_autostart_runkey() -> bool:
    """Remove app autostart from HKCU Run key."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, reg_key_name)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart removed from HKCU Run key")
        return True
    except OSError:
        return False


def _is_app_autostart_runkey_registered() -> bool:
    """True if the HKCU Run key has the VoiceTyper entry AND the command
    path it points at actually exists on disk.

    AUTOSTART-CMD-VALIDATE: previously this function returned True if
    the registry value existed (existence-only check). If the venv was
    deleted after registration, the Run-key value would still exist
    but its command would point at a nonexistent pythonw.exe — the
    Run key would fire at login, fail silently, and the Settings toggle
    would show "autostart enabled" while the app never started. We now
    parse the stored command line, extract the exe path, and verify it
    exists. If the path is dead, we delete the stale entry (best-effort)
    and return False so the Settings toggle reflects the actual state.
    """
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, reg_key_name)
            if not val:
                return False
            # AUTOSTART-CMD-VALIDATE: verify the command's exe path
            # exists on disk. If the path is dead (venv deleted), the
            # Run-key entry is stale — clean it up and return False.
            if not _validate_runkey_command(val):
                log.warning(
                    "[AUTOSTART] Run-key entry %s exists but its command "
                    "path is stale (target file does not exist): %s — "
                    "cleaning up stale entry",
                    reg_key_name,
                    val,
                )
                _cleanup_stale_runkey_entry(reg_key_name)
                return False
            log.debug(
                "[AUTOSTART] Run-key entry %s has valid command: %s",
                reg_key_name,
                val,
            )
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError:
        return False


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
    if _pkg.is_windows() and "\\\\" in exe_path and not exe_path.startswith("\\\\"):
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


# ─── One-time legacy-entry sweep (AUTOSTART-LEGACY) ──────────────────
#
# PLAT-RUN renamed the autostart entries from fixed strings (and later
# from a ``sys.executable``-derived hash) to a stable install-path hash.
# Upgraded installs can therefore carry legacy ``VoiceTyper*`` entries
# that ALL point at the same install and ALL fire at logon — duplicate
# autostart. The sweep below removes those legacy entries once per
# install (marker-gated), while preserving the current install's own
# entry and other installs' entries (multi-install support).


def _entry_targets_this_install(value: str) -> bool:
    """True if an autostart command line points at THIS install.

    The sweep must only delete entries targeting the SAME install —
    other installs' entries (PLAT-RUN multi-install support) must be
    preserved. A command targets this install when either:

      • it references the current install's autostart launcher script
        (``autostart_launcher.py`` — the stable per-install identifier
        from :func:`autostart._install_identifier`). Every Python-backed
        entry embeds it in the arguments, so this covers the
        overwhelming majority of legacy entries regardless of which
        interpreter registered them.
      • its executable is the current install's Tauri binary (the
        ``_tauri_binary()`` fallback used when no Python interpreter is
        available — a bare binary with no launcher in the arguments).

    Conservative by design: if neither check matches, the entry is
    treated as NOT belonging to this install and is left alone.
    """
    if not value:
        return False
    launcher = _pkg._install_identifier()
    if launcher and os.path.normcase(launcher) in os.path.normcase(value):
        return True
    try:
        tokens = shlex.split(value, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False
    exe = tokens[0].strip('"')
    if not exe:
        return False
    tauri_bin = _pkg._resolve_tauri_binary_for_autostart()
    return bool(tauri_bin and os.path.normcase(exe) == os.path.normcase(tauri_bin))


def _sweep_legacy_runkeys() -> list[str]:
    """Remove legacy ``VoiceTyper*`` HKCU Run-key values for this install.

    Enumerates every value under ``HKCU\\...\\Run`` whose name starts
    with ``VoiceTyper``, and deletes those whose name differs from the
    current install's ``_run_key_name()`` AND whose command targets this
    install (:func:`_entry_targets_this_install`). The current entry is
    never touched; other installs' entries (different launcher path /
    exe) are preserved.

    Returns the list of deleted value names. Non-fatal: registry errors
    are logged and skipped so a single bad value can't abort the sweep.
    """
    try:
        import winreg
    except ImportError:
        return []  # not Windows
    current_name = _run_key_name()
    deleted: list[str] = []
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS,
        )
    except OSError as exc:
        log.warning("[AUTOSTART] Could not open HKCU Run key for legacy sweep: %s", exc)
        return deleted
    try:
        i = 0
        while True:
            try:
                name, value, _vtype = winreg.EnumValue(key, i)
            except OSError:
                # End of enumeration (Windows signals via OSError).
                break
            if (
                isinstance(name, str)
                and name.startswith("VoiceTyper")
                and name != current_name
                and isinstance(value, str)
                and _entry_targets_this_install(value)
            ):
                try:
                    winreg.DeleteValue(key, name)
                    deleted.append(name)
                    log.info("[AUTOSTART] Legacy sweep removed duplicate Run key: %s", name)
                    # Don't increment i — the next value shifts into the
                    # current slot after DeleteValue.
                    continue
                except OSError as exc:
                    log.warning(
                        "[AUTOSTART] Legacy sweep failed to delete Run key %r: %s",
                        name,
                        exc,
                    )
            i += 1
    finally:
        with contextlib.suppress(OSError):
            winreg.CloseKey(key)
    return deleted


def _sweep_legacy_tasks() -> list[str] | None:
    """Remove legacy ``VoiceTyperAutostart*`` scheduled tasks for this install.

    Enumerates matching tasks via PowerShell ``Get-ScheduledTask``
    (schtasks does not support wildcards in ``/TN``), then for each
    candidate whose name differs from the current
    ``_APP_AUTOSTART_TASK_NAME`` queries its XML and deletes it only if
    its command targets this install (:func:`_entry_targets_this_install`
    on the ``<Command>`` path + ``<Arguments>`` text).

    Returns:
      - ``list`` — the deleted task names. An empty list means the sweep
        ran and found nothing (or the platform doesn't use scheduled
        tasks);
      - ``None`` — the enumeration FAILED (PowerShell unavailable /
        non-zero exit / exception). ``None`` tells the marker-gated
        orchestrator NOT to write the completion marker, so the task
        portion is retried on the next startup instead of being lost
        to a transient failure.

    The PowerShell call is bounded by a 15s timeout (not the
    uninstaller's 60s) because this runs from ``sync_autostart`` on the
    startup path — a hung PowerShell must not stall app startup.
    """
    if not _pkg.is_windows():
        return []
    try:
        from voice_typer.server import task_scheduler
    except Exception:
        return []
    if not task_scheduler.is_supported():
        return []
    import subprocess

    current_name = _pkg._APP_AUTOSTART_TASK_NAME
    deleted: list[str] = []
    try:
        ps_cmd = (
            "Get-ScheduledTask -TaskName 'VoiceTyperAutostart*' "
            "-ErrorAction SilentlyContinue | "
            "ForEach-Object { Write-Output $_.TaskName }"
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            # CREATE_NO_WINDOW (0x08000000) prevents a console window
            # from flashing during the sweep.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        if result.returncode != 0:
            log.debug(
                "[AUTOSTART] Legacy task sweep enumeration failed (rc=%s) — will retry next startup",
                result.returncode,
            )
            return None
        for line in (result.stdout or "").splitlines():
            name = line.strip()
            if not name.startswith("VoiceTyperAutostart") or name == current_name:
                continue
            rc, xml = task_scheduler._schtasks(["/Query", "/TN", name, "/XML"])
            if rc != 0:
                continue
            command_path = _extract_command_from_task_xml(xml) or ""
            arguments = _extract_arguments_from_task_xml(xml) or ""
            if not (_entry_targets_this_install(command_path) or _entry_targets_this_install(arguments)):
                continue
            rc_del, _out = task_scheduler._schtasks(
                ["/Delete", "/TN", name, "/F"],
                capture=True,
            )
            if rc_del == 0:
                deleted.append(name)
                log.info("[AUTOSTART] Legacy sweep removed duplicate task: %s", name)
    except Exception:
        log.debug("[AUTOSTART] Legacy task sweep failed", exc_info=True)
        return None
    return deleted


def _sweep_legacy_startup_bats() -> list[str]:
    """Remove legacy ``VoiceTyper*.bat`` Startup-folder files for this install.

    Enumerates ``VoiceTyper*.bat`` files under the Windows Startup folder
    and deletes those whose name differs from the current
    ``_startup_bat_name()`` AND whose content targets this install
    (:func:`_entry_targets_this_install` on the file text).

    Returns the list of deleted file names. Best-effort — unreadable /
    undeletable files are logged and skipped.
    """
    if not _pkg.is_windows():
        return []
    current_name = _startup_bat_name()
    try:
        autostart_dir = _pkg.get_autostart_dir()
    except Exception:
        return []
    if not autostart_dir.is_dir():
        return []
    deleted: list[str] = []
    for bat_path in autostart_dir.glob("VoiceTyper*.bat"):
        if bat_path.name == current_name:
            continue
        try:
            content = bat_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _entry_targets_this_install(content):
            try:
                bat_path.unlink()
                deleted.append(bat_path.name)
                log.info(
                    "[AUTOSTART] Legacy sweep removed duplicate Startup .bat: %s",
                    bat_path.name,
                )
            except OSError as exc:
                log.warning(
                    "[AUTOSTART] Legacy sweep failed to remove %s: %s",
                    bat_path.name,
                    exc,
                )
    return deleted


def _legacy_sweep_marker_path(config_dir: Path) -> Path:
    """Path to the per-install legacy-sweep completion marker.

    Keyed by the install hash so each install (installs share the
    per-user config dir) sweeps its own legacy entries exactly once.

    The filename is version-scoped (``v2``): the Windows autostart /
    prewarm names moved from the bare ``VoiceTyper*`` scheme into the
    canonical ``com.voicetyper.*`` reverse-DNS namespace, so installs
    that already ran the v1 sweep (old marker name) MUST re-run the
    sweep once — the version bump makes the v1 marker miss and the new
    sweep removes the pre-rename entries that would otherwise linger as
    duplicate autostart triggers.
    """
    return config_dir / f"autostart-sweep-v2-{_pkg._install_hash()}.done"


def sweep_legacy_autostart_entries(config_dir: Path) -> dict:
    """One-time cleanup of legacy same-install autostart entries (PLAT-RUN).

    Upgraded installs can carry legacy ``VoiceTyper*`` Run-key values,
    ``VoiceTyperAutostart*`` scheduled tasks, and ``VoiceTyper*.bat``
    Startup-folder files registered under the OLD naming schemes (the
    fixed pre-PLAT-RUN names, the buggy ``sys.executable``-derived
    hashes, or the bare ``VoiceTyper*`` names used before the move to
    the canonical ``com.voicetyper.*`` reverse-DNS namespace).
    Because the OLD hash differed between ``python.exe`` and
    ``pythonw.exe`` launch contexts, one install can accumulate MULTIPLE
    live entries that all fire at logon.

    This sweep removes every legacy entry whose command targets THIS
    install (:func:`_entry_targets_this_install`) while preserving:
      • the current install's own entry (``_run_key_name`` /
        ``_APP_AUTOSTART_TASK_NAME`` / ``_startup_bat_name``), and
      • other installs' entries (different launcher path → different
        install → preserved — PLAT-RUN multi-install support).

    It runs AT MOST ONCE per install: after the sweep (whether or not
    anything was removed) a marker file keyed by the install hash is
    written into ``config_dir``, so the expensive Task Scheduler
    enumeration (PowerShell) is paid once and steady-state startup cost
    is a single ``Path.exists()`` check. Callers invoke it on every
    startup (e.g. from ``sync_autostart``); the marker makes it
    one-time.

    Returns ``{"swept": bool, "removed": {"runkeys": [...],
    "tasks": [...], "bats": [...]}}``.
    """
    marker = _legacy_sweep_marker_path(config_dir)
    if marker.exists():
        return {"swept": False, "removed": {"runkeys": [], "tasks": [], "bats": []}}
    # Windows-only + test-safety gate. The run-key sweep needs winreg,
    # and ``tests/conftest.py`` blocks the REAL ``winreg`` module
    # (``sys.modules["winreg"] = None``) so tests can never touch the
    # developer's actual HKCU registry — that same guard makes this
    # whole sweep inert under test unless a ``fake_winreg`` fixture is
    # injected, which also stops the task/bat sweeps (real PowerShell /
    # real Startup folder) from running during pytest.
    try:
        import winreg  # noqa: F401
    except ImportError:
        return {"swept": False, "removed": {"runkeys": [], "tasks": [], "bats": []}}
    removed = {
        "runkeys": _sweep_legacy_runkeys(),
        "tasks": _sweep_legacy_tasks(),
        "bats": _sweep_legacy_startup_bats(),
    }
    if removed["tasks"] is None:
        # The task enumeration failed (PowerShell unavailable / non-zero
        # exit). Do NOT write the completion marker — the sweep retries
        # next startup so a transient failure can't permanently skip the
        # task portion. The run-key / .bat sweeps already ran (both are
        # idempotent, so re-running them is harmless).
        log.warning("[AUTOSTART] Legacy autostart sweep: task enumeration failed — will retry on next startup")
        return {
            "swept": False,
            "removed": {"runkeys": removed["runkeys"], "tasks": [], "bats": removed["bats"]},
        }
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
    except OSError as exc:
        # Best-effort: if the marker can't be written the sweep re-runs
        # next startup (still safe — every sub-sweep is idempotent).
        log.warning(
            "[AUTOSTART] Could not write legacy-sweep marker %s: %s",
            marker,
            exc,
        )
    return {"swept": True, "removed": removed}


# Uninstaller helper ( Windows part) ────────────────────────


def _unregister_all_voicetyper_runkeys() -> list[str]:
    """Remove ALL Voice Typer HKCU Run-key entries.

    Unlike :func:`_unregister_app_autostart_runkey` (which removes ONLY
    the current install's hash-suffixed entry), this function enumerates
    every value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
    whose name starts with the app's namespace — ``com.voicetyper``
    (current canonical rename-DNS names) OR ``VoiceTyper`` (pre-rename
    bare names) — and deletes it. It is intended
    for the **uninstaller** path (NSIS ``customUnInstall`` macro /
    Tauri ``preRemoveScript`` / manual ``uninstall_permissions.py``
    invocation) where the goal is to leave the registry CLEAN of any
    Voice Typer autostart entry — including stale entries from previous
    installs at different paths (different hashes — see PLAT-RUN).

    Returns the list of value names that were deleted (empty list if
    nothing matched / not Windows / registry inaccessible). The caller
    can log the list for the uninstall summary.

    Non-fatal: any per-value error (e.g. a value vanishes between
    EnumValue and DeleteValue) is logged and skipped so a single bad
    value doesn't abort the whole sweep.

    Tested on Linux via the ``fake_winreg`` fixture pattern (see
    ``tests/test_uninstall_windows.py``) — the ``winreg`` import is
    deferred to call time so the module imports cleanly on non-Windows
    hosts.
    """
    try:
        import winreg
    except ImportError:
        return []  # not Windows — caller (uninstall script) logs + exits 0
    deleted: list[str] = []
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS,
        )
    except OSError as exc:
        log.warning("[UNINSTALL] Could not open HKCU Run key for cleanup: %s", exc)
        return deleted
    try:
        i = 0
        while True:
            try:
                name, _value, _vtype = winreg.EnumValue(key, i)
            except OSError:
                # End of enumeration (Windows signals "no more values"
                # via OSError, not StopIteration).
                break
            if isinstance(name, str) and name.startswith(("VoiceTyper", "com.voicetyper")):
                try:
                    winreg.DeleteValue(key, name)
                    deleted.append(name)
                    log.info("[UNINSTALL] Removed HKCU Run key: %s", name)
                    # Don't increment i — the next value shifts into the
                    # current slot after DeleteValue (same pattern as the
                    # stale-entry cleanup loop in _register_app_autostart_runkey).
                    continue
                except OSError as exc:
                    log.warning("[UNINSTALL] Failed to delete HKCU Run key %r: %s", name, exc)
            i += 1
    finally:
        with contextlib.suppress(OSError):
            winreg.CloseKey(key)
    return deleted


def _unregister_all_voicetyper_tasks() -> list[str]:
    """Remove ALL Voice Typer Task Scheduler tasks.

    Companion to :func:`_unregister_all_voicetyper_runkeys`. The Task
    Scheduler ``schtasks`` CLI does NOT accept wildcards in ``/TN``,
    so we shell out to PowerShell's ``Get-ScheduledTask`` (which DOES
    support ``-TaskName`` wildcards) to enumerate matching
    tasks, then ``schtasks /Delete`` each one.  The wildcard union
    covers the current canonical names (``com.voicetyper.autostart*``,
    ``com.voicetyper.prewarm``) AND the pre-rename bare names
    (``VoiceTyperAutostart*``, ``VoiceTyperPrewarm``) so installs that
    predate the namespace rename are fully cleaned too.

    Returns the list of task names deleted (best-effort — empty list on
    any failure including non-Windows or Task Scheduler not running).
    The caller can log the list for the uninstall summary.

    Non-fatal: a single task delete failure (e.g. locked task created
    by an admin install) is logged and skipped.
    """
    try:
        from voice_typer.server import task_scheduler
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("[UNINSTALL] task_scheduler import failed: %s", exc)
        return []
    if not task_scheduler.is_supported():
        return []
    import subprocess

    deleted: list[str] = []
    try:
        # PowerShell pipeline: Get-ScheduledTask returns matching tasks,
        # ForEach-Object runs schtasks /Delete for each. We capture stdout
        # and parse the task names from the Get-ScheduledTask output as a
        # best-effort log (the actual delete happens via the pipeline).
        ps_cmd = (
            "Get-ScheduledTask -TaskName 'VoiceTyper*','com.voicetyper*' "
            "-ErrorAction SilentlyContinue | "
            "ForEach-Object { schtasks.exe /Delete /TN $_.TaskName /F; "
            "Write-Output $_.TaskName }"
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            # CREATE_NO_WINDOW (0x08000000) prevents a console
            # window from flashing on the user's screen during the
            # uninstall sweep. The sweep runs at uninstall time, often
            # from a UI-driven flow where a flashing console would look
            # broken. ``getattr`` guard is defensive (the constant
            # exists on every Python 3.x Windows build).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.startswith(("VoiceTyper", "com.voicetyper")):
                    deleted.append(line)
                    log.info("[UNINSTALL] Removed Task Scheduler task: %s", line)
        else:
            log.warning(
                "[UNINSTALL] PowerShell task sweep failed (rc=%s): %s",
                result.returncode,
                (result.stderr or "").strip(),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("[UNINSTALL] Task Scheduler sweep raised: %s", exc)
    return deleted


# ── Windows Startup-folder .bat fallback (tertiary) ────────────────────
#
# AUTOSTART-STARTUP-FALLBACK: when BOTH the HKCU Run key and Task
# Scheduler registration fail (e.g. HKCU locked by group policy,
# schtasks unavailable, UAC declined), we write a ``.bat`` file to the
# Windows Startup folder as a tertiary mechanism. The Startup folder
# (``%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup``) is
# always honored by Windows Explorer at login and requires no special
# permissions — it's the most reliable fallback.
#
# The .bat sets ``VT_START_HIDDEN=1`` (so the Tauri app / Electron
# launcher starts hidden) and spawns the autostart command via
# ``start "" /B`` (no console window flash). The file is named
# ``com.voicetyper.autostart_<hash>.bat`` to match the Run-key naming
# convention (PLAT-RUN — multi-install support via the install-path hash,
# canonical ``com.voicetyper.*`` reverse-DNS namespace).


def _startup_bat_name() -> str:
    """Return the Startup-folder .bat file name (hash-suffixed).

    Uses the same install-path hash as the Run-key name (PLAT-RUN) so
    two installations in different directories register distinct .bat
    files and don't conflict.  The name lives in the canonical
    ``com.voicetyper.*`` reverse-DNS namespace (RDNN), consistent with
    the Run-key value name and Task Scheduler task names.
    """
    return f"com.voicetyper.autostart{_pkg._install_hash_suffix()}.bat"


def _startup_bat_path() -> Path:
    """Return the full path to the Startup-folder .bat file.

    Delegates to :func:`get_autostart_dir` (in :mod:`.autostart`) which
    returns the platform-specific autostart directory. On Windows this
    is ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``.
    """
    return _pkg.get_autostart_dir() / _startup_bat_name()


def _register_app_autostart_startup() -> bool:
    """Register app autostart via a Windows Startup-folder .bat file.

    Writes a ``.bat`` to ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\
    Programs\\Startup\\VoiceTyper_<hash>.bat`` that sets
    ``VT_START_HIDDEN=1`` and spawns the autostart command via
    ``start "" /B`` (no console window flash).

    Returns ``True`` on success, ``False`` on failure (e.g. the
    Startup folder is not writable, or the autostart command can't be
    resolved). Non-Windows platforms return ``False`` (the Startup
    folder concept doesn't apply).
    """
    if not _pkg.is_windows():
        return False
    try:
        cmd = _pkg._autostart_command()
        bat_path = _startup_bat_path()
        bat_path.parent.mkdir(parents=True, exist_ok=True)
        # Build the .bat content. ``@echo off`` suppresses command
        # echo; ``set VT_START_HIDDEN=1`` passes the hidden flag to
        # the spawned app (the Run key / Task Scheduler can't set env
        # vars, but the .bat can). ``start "" /B`` spawns the command
        # without a new console window and doesn't wait for it.
        bat_content = f'@echo off\r\nset VT_START_HIDDEN=1\r\nstart "" /B {cmd}\r\n'
        bat_path.write_text(bat_content, encoding="utf-8")
        log.info(
            "[CONFIG] Autostart enabled via Windows Startup-folder .bat: %s",
            bat_path,
        )
        return True
    except OSError as exc:
        log.warning("[CONFIG] Could not write Startup-folder .bat: %s", exc)
        return False
    except Exception as exc:
        log.warning("[CONFIG] Startup-folder .bat registration raised: %s", exc)
        return False


def _unregister_app_autostart_startup() -> bool:
    """Remove the Windows Startup-folder .bat file.

    Returns ``True`` on success (including when the file was already
    absent — idempotent). Returns ``False`` only if the file exists
    but couldn't be deleted (e.g. permission denied).
    """
    try:
        bat_path = _startup_bat_path()
    except Exception:
        return False
    if not bat_path.exists():
        return True  # already absent — idempotent success
    try:
        bat_path.unlink()
        log.info("[CONFIG] Removed Windows Startup-folder .bat: %s", bat_path)
        return True
    except OSError as exc:
        log.warning("[CONFIG] Could not remove Startup-folder .bat: %s", exc)
        return False


def _is_app_autostart_startup_registered() -> bool:
    """True if the Windows Startup-folder .bat exists AND its target
    command is valid (points at an existing file).

    AUTOSTART-CMD-VALIDATE: mirrors the validation in
    :func:`_is_app_autostart_runkey_registered` — the .bat file's
    existence alone is not enough; we also verify the spawned command's
    exe path exists on disk. If the .bat is stale (target deleted), we
    clean it up and return False.
    """
    try:
        bat_path = _startup_bat_path()
    except Exception:
        return False
    if not bat_path.exists():
        return False
    try:
        content = bat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Extract the command from the ``start "" /B <cmd>`` line.
    # We look for the line starting with ``start ""`` and parse the
    # remainder as a Windows command line.
    target_cmd = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith('start ""'):
            # Everything after ``start "" /B `` is the command.
            # Find the command portion (skip ``start ""`` and optional ``/B``).
            after_start = stripped[len('start ""') :].strip()
            # Strip leading ``/B`` flag if present.
            if after_start.upper().startswith("/B"):
                after_start = after_start[2:].strip()
            target_cmd = after_start
            break
    if not target_cmd:
        # Malformed .bat — can't validate. Conservatively report True
        # (the file exists; we just can't parse it).
        log.debug(
            "[AUTOSTART] Startup .bat exists but could not parse command: %s",
            bat_path,
        )
        return True
    # Validate the target command's exe path exists.
    if not _validate_runkey_command(target_cmd):
        log.warning(
            "[AUTOSTART] Startup .bat exists but its target command is stale: %s — cleaning up stale .bat",
            target_cmd,
        )
        with contextlib.suppress(OSError):
            bat_path.unlink()
        return False
    log.debug(
        "[AUTOSTART] Startup .bat registered with valid command: %s",
        target_cmd,
    )
    return True
