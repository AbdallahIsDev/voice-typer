"""macOS autostart. LaunchAgent plist.

Extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Implements the
three macOS autostart primitives:

  - :func:`_enable_autostart_macos`: write ``~/Library/LaunchAgents/com.voicetyper.plist``
+ ``launchctl load`` (with a 5 s timeout, ).
  - :func:`_disable_autostart_macos`: ``launchctl bootout`` (modern,
    macOS 10.10+) + ``launchctl remove`` (legacy fallback) + delete the
    plist file.
  - :func:`_is_autostart_macos`: file-existence probe on the plist.
  - :func:`_os_uid`: current user's numeric uid (for the
    ``launchctl bootout gui/<uid>/<label>`` target).

Patch-path compatibility
------------------------
Tests patch ``get_autostart_dir`` via
``monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)``
(in :mod:`tests.test_platform`) and patch ``_os_uid`` HERE (it is defined in
this module): ``monkeypatch.setattr(autostart_macos, "_os_uid", lambda: 501)``.
``get_autostart_dir`` is owned by :mod:`.autostart` and read through the
bound module attribute (``_autostart_mod.get_autostart_dir()``) at call
time; ``_os_uid`` resolves as a plain module-global lookup at call time.

``Path.home()`` and ``subprocess.run`` are patched globally (via
``monkeypatch.setattr(Path, "home", ...)`` and
``monkeypatch.setattr(subprocess, "run", fake_run)`` in the mig16
``darwin_platform`` fixture), both resolve to the same stdlib module
objects that this file imports, so the global patches propagate without
any extra indirection.

``inspect.getsource`` compatibility
-----------------------------------
``_enable_autostart_macos`` / ``_disable_autostart_macos`` /
``_is_autostart_macos`` / ``_os_uid`` are genuinely defined here, so
``inspect.getsource(_enable_autostart_macos)`` (used by
:mod:`tests.test_platform_and_config` to assert the plist uses an
absolute WorkingDirectory + a launchctl timeout) continues to read
from this file.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path

# Patch-path bindings. ``_autostart_mod`` binds the owning sibling module
from voice_typer.server import _paths
from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)


def _enable_autostart_macos() -> bool:
    from xml.sax.saxutils import escape

    plist_dir = _autostart_mod.get_autostart_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.voicetyper.plist"

    # Packaged (frozen, no-Python) installs: register the app binary
    try:
        packaged = _autostart_mod._packaged_tauri_target()
    except Exception:
        packaged = None
    if packaged is not None:
        tauri_bin, tauri_args = packaged
        program_args = [tauri_bin, *tauri_args]
    else:
        launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"

    # previously the plist's ``WorkingDirectory`` was
    working_dir = str(Path.home())

    if packaged is None:
        # macOS-VENV-AUTOSTART: for parity with Linux + Windows,
        python_exe = sys.executable
        if sys.prefix != sys.base_prefix:
            from voice_typer.server.server_platform.autostart import (
                _probe_system_python,
            )

            system_python = _probe_system_python("python3")
            if system_python:
                log.info(
                    "[AUTOSTART] Running inside venv (%s); using system Python for macOS plist: %s",
                    python_exe,
                    system_python,
                )
                python_exe = system_python
            else:
                log.warning(
                    "[AUTOSTART] Running inside venv (%s) but system Python "
                    "cannot import voice_typer.server.autostart_launcher "
                    "(probe failed). Keeping venv Python for the macOS "
                    "LaunchAgent, autostart will break if the venv is "
                    "deleted, but works for the current user.",
                    python_exe,
                )
        program_args = [python_exe, str(launcher)]

    program_args_xml = "\n".join(f"        <string>{escape(arg)}</string>" for arg in program_args)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voicetyper</string>
    <key>ProgramArguments</key>
    <array>
{program_args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>WorkingDirectory</key>
    <string>{escape(working_dir)}</string>
    <key>StandardOutPath</key>
    <string>{escape(str(_paths.autostart_log()))}</string>
    <key>StandardErrorPath</key>
    <string>{escape(str(_paths.autostart_log()))}</string>
</dict>
</plist>"""
    # Atomic write (temp + os.replace) so a crash mid-write cannot
    from voice_typer.server.secure_file_io import _secure_atomic_write

    _secure_atomic_write(plist_path, plist_content, durability=False)
    plist_path.chmod(0o600)
    # ensure the log directory exists with private perms
    log_dir = _paths.config_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(log_dir, 0o700)
    # (pyrefly): import subprocess BEFORE the try block so the

    try:
        # previously ``launchctl load`` had no timeout,
        completed = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{_os_uid()}", str(plist_path)],
            check=False,
            capture_output=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        log.warning("[CONFIG] launchctl bootstrap timed out after 5s, launchd may be unresponsive")
        # surface the timeout to the caller so the renderer can
        log.exception("[CONFIG] Autostart enable FAILED: launchctl bootstrap timed out")
        return False
    except Exception as e:
        log.warning("[CONFIG] launchctl bootstrap failed: %s", e)
        log.exception("[CONFIG] Autostart enable FAILED: %s", e)
        return False

    # known error-substring patterns as failure (defensive, some
    stderr_text = ""
    try:
        if completed.stderr is not None:
            stderr_text = (
                completed.stderr.decode("utf-8", errors="replace")
                if isinstance(completed.stderr, (bytes, bytearray))
                else str(completed.stderr)
            )
    except Exception:
        stderr_text = ""
    stderr_lower = stderr_text.lower()

    bootstrap_succeeded = (
        completed.returncode == 0 and "loader.error" not in stderr_lower and "exited with" not in stderr_lower
    )

    if not bootstrap_succeeded:
        # fall back to the legacy ``launchctl load`` for older
        log.info(
            "[CONFIG] launchctl bootstrap rc=%s stderr=%r, falling back to legacy launchctl load",
            completed.returncode,
            stderr_text.strip(),
        )
        try:
            completed = subprocess.run(
                ["launchctl", "load", str(plist_path)],
                check=False,
                capture_output=True,
                timeout=5.0,
            )
        except subprocess.TimeoutExpired:
            log.warning("[CONFIG] launchctl load timed out after 5s, launchd may be unresponsive")
            log.exception("[CONFIG] Autostart enable FAILED: launchctl load timed out")
            return False
        except Exception as e:
            log.warning("[CONFIG] launchctl load failed: %s", e)
            log.exception("[CONFIG] Autostart enable FAILED: %s", e)
            return False
        # Re-read stderr from the load call so the failure inspection
        stderr_text = ""
        try:
            if completed.stderr is not None:
                stderr_text = (
                    completed.stderr.decode("utf-8", errors="replace")
                    if isinstance(completed.stderr, (bytes, bytearray))
                    else str(completed.stderr)
                )
        except Exception:
            stderr_text = ""
        stderr_lower = stderr_text.lower()

    if completed.returncode != 0:
        err_msg = f"launchctl load exit {completed.returncode}: {stderr_text.strip() or '(no stderr)'}"
        log.warning("[CONFIG] Autostart enable FAILED: %s", err_msg)
        return False
    # Defensive substring check, handles the launchctl bug where
    if "loader.error" in stderr_lower or "exited with" in stderr_lower:
        err_msg = f"launchctl load reported error (rc=0): {stderr_text.strip()}"
        log.warning("[CONFIG] Autostart enable FAILED: %s", err_msg)
        return False

    log.info("[CONFIG] Autostart enabled (macOS): %s", plist_path)
    return True


def _disable_autostart_macos() -> bool:
    plist_path = _autostart_mod.get_autostart_dir() / "com.voicetyper.plist"
    # Unload the running job BEFORE deleting the plist, otherwise the
    label = "com.voicetyper"
    for args in (
        ["launchctl", "bootout", f"gui/{_os_uid()}/{label}"],
        ["launchctl", "remove", label],
    ):
        with contextlib.suppress(Exception):
            subprocess.run(
                args,
                check=False,
                capture_output=True,
                timeout=5,
            )
    if plist_path.exists():
        plist_path.unlink()
    log.info("[CONFIG] Autostart disabled (macOS)")
    return True


def _os_uid() -> int:
    """Return the current user's numeric uid (for launchctl bootout target)."""
    _getuid = getattr(os, "getuid", None)
    if _getuid is not None:
        try:
            return int(_getuid())
        except OSError:
            log.debug("[PLATFORM] os.getuid failed, falling back to 501", exc_info=True)
    return 501  # default first user on macOS


def _plist_program_arguments_exist(plist_path: Path) -> bool:
    """Validate that a LaunchAgent plist's program paths exist on disk.

    AUTOSTART-CMD-VALIDATE: the plist's existence alone is not enough —
    the ``ProgramArguments`` array points at the python interpreter and
    the launcher script, and if either was deleted (e.g. the user
    removed their venv or moved the install directory), the login item
    silently fails to launch. Parses the plist with ``plistlib`` and
    checks the first two ``ProgramArguments`` entries (python + launcher)
    exist.

    Mirrors the CONSERVATIVE-DELETE policy from the Windows
    ``_validate_runkey_command`` helper: malformed / unparseable plists
    report ``True`` (valid) rather than risk deleting an entry we can't
    parse confidently. Returns ``False`` only when we're CERTAIN a
    program path doesn't exist.
    """
    try:
        import plistlib

        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        # Malformed plist, conservatively report valid (can't parse).
        log.debug("[AUTOSTART] macOS plist unparseable, treating as valid: %s", plist_path)
        return True
    program_args = data.get("ProgramArguments") if isinstance(data, dict) else None
    if not isinstance(program_args, list) or not program_args:
        # Also accept the legacy ``Program`` key (a single string).
        program = data.get("Program") if isinstance(data, dict) else None
        if isinstance(program, str) and program:
            try:
                if _autostart_mod._is_legacy_stale_autostart_reference(program):
                    return False
            except Exception:
                pass
            return Path(program).exists()
        log.debug("[AUTOSTART] macOS plist has no parseable program args, treating as valid: %s", plist_path)
        return True
    # Stale-migration: Electron / pip-era shapes are certain-stale even
    try:
        joined = " ".join(entry for entry in program_args if isinstance(entry, str))
        if _autostart_mod._is_legacy_stale_autostart_reference(joined):
            log.warning(
                "[AUTOSTART] macOS plist references legacy runtime, treating autostart as disabled: %s",
                plist_path,
            )
            return False
        if _autostart_mod._references_missing_launcher_script(joined):
            log.warning(
                "[AUTOSTART] macOS plist references missing launcher script, treating autostart as disabled: %s",
                plist_path,
            )
            return False
        if _autostart_mod._launcher_entry_superseded(joined):
            log.warning(
                "[AUTOSTART] macOS plist targets the Python launcher while a packaged "
                "Tauri binary is installed, treating autostart as disabled (migrates "
                "to direct-binary on next sync): %s",
                plist_path,
            )
            return False
    except Exception:
        pass
    # Check program paths, skipping CLI flags and bare values. The
    for entry in program_args:
        if not isinstance(entry, str) or not entry:
            continue
        if entry.startswith("-"):
            continue
        try:
            float(entry)
            continue
        except (TypeError, ValueError):
            pass
        if not Path(entry).exists():
            log.warning(
                "[AUTOSTART] macOS plist references missing program path: %s, treating autostart as disabled",
                entry,
            )
            return False
    return True


def _is_autostart_macos() -> bool:
    """True if the LaunchAgent plist exists AND its program paths
    exist on disk.

    AUTOSTART-CMD-VALIDATE: mirrors the validation in the Windows
    ``_is_app_autostart_startup_registered``: the plist's existence
    alone is not enough; we also verify the python / launcher paths it
    points at exist. A stale plist (venv deleted or install moved)
    reports disabled so Settings shows the true state instead of a
    misleading "Autostart: enabled".
    """
    plist_path = _autostart_mod.get_autostart_dir() / "com.voicetyper.plist"
    if not plist_path.exists():
        return False
    return _plist_program_arguments_exist(plist_path)
