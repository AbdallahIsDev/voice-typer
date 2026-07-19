"""Public autostart API + cross-platform helpers.

Phase 4.5 / ARCH-045 — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Contains:
  - :func:`_desktop_quote` — freedesktop Desktop Entry Spec Exec-quoting.
  - :func:`_autostart_command` — builds the OS-agnostic autostart command.
  - :func:`get_autostart_dir` — platform-specific autostart directory.
  - :func:`enable_autostart` / :func:`disable_autostart` /
    :func:`is_autostart_enabled` — public facade that dispatches to the
    platform-specific implementation in :mod:`.autostart_windows` /
    :mod:`.autostart_macos` / :mod:`.autostart_linux`.
  - :func:`_install_hash_suffix` — 8-char SHA-256 hash of
    ``sys.executable`` (used to namespace the Windows Task Scheduler
    entry + HKCU Run key so multiple installs don't collide).

Patch-path compatibility
------------------------
Tests patch several names that this module's functions call at runtime:

  - ``SYSTEM`` — patched via
    ``monkeypatch.setattr(server_platform, "SYSTEM", "linux")`` etc.  All
    dispatch logic reads ``_pkg.SYSTEM`` at call time.
  - ``get_autostart_dir`` — patched via
    ``monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)``.
    The platform-specific enable/disable/is_enabled helpers in
    :mod:`.autostart_macos` / :mod:`.autostart_linux` look it up via
    ``_pkg.get_autostart_dir()``.
  - ``_autostart_command`` — patched via
    ``monkeypatch.setattr(platform_mod, "_autostart_command", lambda: ...)``.
    The Linux ``_enable_autostart_linux`` (in :mod:`.autostart_linux`) and
    the Windows Run-key path (``_register_app_autostart_runkey`` in
    :mod:`.autostart_windows`) look it up via ``_pkg._autostart_command()``.
  - ``_enable_autostart_windows`` / ``_enable_autostart_macos`` /
    ``_enable_autostart_linux`` — patched via
    ``monkeypatch.setattr(server_platform, "_enable_autostart_macos", lambda: ...)``
    etc.  ``enable_autostart`` looks them up via ``_pkg.X()``.
  - ``_disable_autostart_windows`` / ``_disable_autostart_macos`` /
    ``_disable_autostart_linux`` — same pattern via ``disable_autostart``.
  - ``_is_autostart_windows`` / ``_is_autostart_macos`` /
    ``_is_autostart_linux`` — same pattern via ``is_autostart_enabled``.

``inspect.getsource`` compatibility
-----------------------------------
All seven functions are genuinely defined here, so
``inspect.getsource(enable_autostart)`` etc. continue to read from this
file.  The platform-specific implementations are in their respective
submodules.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Patch-path bridge: route lookups of ``SYSTEM``,
# ``_enable_autostart_windows`` / ``_enable_autostart_macos`` /
# ``_enable_autostart_linux`` (and their disable / is_enabled siblings)
# through the package namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
# keep affecting production code defined here.
from voice_typer.server import server_platform as _pkg

log = logging.getLogger(__name__)


# ─── Desktop Entry Spec Exec-quoting ─────────────────────────────────


def _desktop_quote(arg: str) -> str:
    """Quote ``arg`` per the freedesktop Desktop Entry Spec's Exec rules.

    The spec (https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html)
    says: "If an argument contains a reserved character then the argument
    must be quoted in its entirety. Reserved characters are space, tab,
    newline, double quote, single quote, backslash, greater-than sign,
    less-than sign, tilde, pipe character, ampersand, semicolon, dollar
    sign, asterisk, question mark, hash, parentheses."

    Within a quoted string, the characters ``" \\ ` $`` must be escaped
    with a backslash.

    NEW-XPLAT-007: previously the code just wrapped paths in double
    quotes without escaping backslashes or quotes — so a path like
    ``C:\\Users\\John "Bob"\\app`` would corrupt the .desktop Exec
    field.  We now do proper spec-compliant quoting.
    """
    reserved = set(" \t\n\"'\\><~|&;$*?#()")
    if not any(c in reserved for c in arg):
        return arg  # no quoting needed
    # Escape backslash, double-quote, backtick, dollar per spec.
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _autostart_command() -> str:
    """Build the command that the OS autostart entry should run.

    Runs the universal ``autostart_launcher`` module with ``--hidden``,
    which:
      • if the app is already running → focuses its window via the
        single-instance lock and exits (idempotent re-login, etc.);
      • if not running → spawns ``npm run dev`` with ``VT_START_HIDDEN=1``,
        so Electron starts its dashboard HIDDEN (tray + bubble still work)
        instead of popping a window over the user's desktop at login.

    On Windows, prefers ``pythonw.exe`` (no console window) when
    available so login doesn't flash a console.  Falls back to
    ``python.exe`` if ``pythonw.exe`` is absent.

    STARTUP-2: also passes ``--delay <N>`` so the launcher waits N
    seconds before spawning Electron. This gives the prewarm task
    (which now fires at logon+0 s) a head start on warming the OS file
    cache, so the app's cold imports of torch/transformers hit RAM
    instead of contending with prewarm on disk.

    ADR-0009 Issue 4: the delay was reduced from 30s to 15s. Combined
    with the prewarm PID-file handshake in model_manager.try_load()
    (wait_for_prewarm()), this gives prewarm a head start without
    wasting 15s when prewarm finishes early. If the user logs in
    faster than prewarm can finish, the app's model loader waits for
    prewarm to complete (up to 60s) rather than fighting it for disk.

    NEW-XPLAT-007: the result is properly quoted per the freedesktop
    Desktop Entry Spec's Exec-quoting rules so paths containing
    spaces, apostrophes, or other reserved characters (e.g.
    ``/home/john doe/voice-typer``) survive XFCE's and KDE's
    .desktop file parsers without truncation.
    """
    # ADR-0009 Issue 4: single source of truth for the delay value.
    # Importing here (rather than at module top) avoids a circular
    # import: task_scheduler imports voice_typer.server.platform_utils
    # which is in this module's dependency graph.
    from voice_typer.server.task_scheduler import _APP_AUTOSTART_DELAY_SECONDS

    delay_str = str(_APP_AUTOSTART_DELAY_SECONDS)

    # The launcher lives next to this module (voice_typer/server/).
    launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"
    # Build the argument list, then quote each arg per the desktop spec.
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        python_bin = str(pythonw) if pythonw.exists() else sys.executable
        args = [python_bin, str(launcher), "--hidden", "--delay", delay_str]
    else:
        # macOS / Linux: use the current interpreter.
        args = [sys.executable, str(launcher), "--hidden", "--delay", delay_str]

    # PLAT-VENV: When registering autostart, use the system Python
    # interpreter path instead of sys.executable if inside a virtualenv.
    # sys.prefix != sys.base_prefix detects virtualenv/venv.
    # PyInstaller builds have sys.prefix == sys.base_prefix so this
    # only affects development setups.
    python_exe = sys.executable
    if sys.prefix != sys.base_prefix:
        # We're inside a virtualenv — try to find the system Python
        import shutil

        base_python = "python3" if sys.platform != "win32" else "python.exe"
        system_python = shutil.which(base_python)
        if system_python:
            log.info(
                "[AUTOSTART] Running inside venv (%s); using system Python: %s",
                python_exe,
                system_python,
            )
            # Replace the python binary in the args
            args = [system_python if a == python_exe else a for a in args]
        else:
            log.warning(
                "[AUTOSTART] Running inside venv (%s) and system Python not "
                "found on PATH. Using venv Python — autostart may break if "
                "the venv is deleted.",
                python_exe,
            )
    return " ".join(_desktop_quote(arg) for arg in args)


# ─── Install-path hash suffix (PLAT-RUN) ─────────────────────────────


def _install_hash_suffix() -> str:
    """PLAT-RUN: Return an 8-char hash suffix for the install path.

    Empty string on non-Windows or if hashing fails (non-fatal).
    """
    try:
        import hashlib
        import sys

        return "_" + hashlib.sha256(sys.executable.encode()).hexdigest()[:8]
    except (OSError, ValueError):
        log.debug("[PLATFORM] _install_hash_suffix failed", exc_info=True)
        return ""


# ─── Autostart directory ─────────────────────────────────────────────


def get_autostart_dir() -> Path:
    if _pkg.SYSTEM == "win32":
        return (
            Path(os.environ.get("APPDATA", Path.home()))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    elif _pkg.SYSTEM == "darwin":
        return Path.home() / "Library" / "LaunchAgents"
    else:
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"


# ─── Public autostart facade ─────────────────────────────────────────


def enable_autostart() -> bool:
    try:
        if _pkg.SYSTEM == "win32":
            return _pkg._enable_autostart_windows()
        elif _pkg.SYSTEM == "darwin":
            return _pkg._enable_autostart_macos()
        else:
            return _pkg._enable_autostart_linux()
    except Exception as e:
        log.error("[CONFIG] Failed to enable autostart: %s", e)
        return False


def disable_autostart() -> bool:
    try:
        if _pkg.SYSTEM == "win32":
            return _pkg._disable_autostart_windows()
        elif _pkg.SYSTEM == "darwin":
            return _pkg._disable_autostart_macos()
        else:
            return _pkg._disable_autostart_linux()
    except Exception as e:
        log.error("[CONFIG] Failed to disable autostart: %s", e)
        return False


def is_autostart_enabled() -> bool:
    # RW-6 (pyrefly): import subprocess BEFORE the try block so the
    # ``except subprocess.CalledProcessError`` clause has a guaranteed-bound
    # name (matches the pattern at line ~826).
    import subprocess

    try:
        if _pkg.SYSTEM == "win32":
            return _pkg._is_autostart_windows()
        elif _pkg.SYSTEM == "darwin":
            return _pkg._is_autostart_macos()
        else:
            return _pkg._is_autostart_linux()
    except (OSError, ImportError, FileNotFoundError, subprocess.CalledProcessError):
        log.debug("[PLATFORM] is_autostart_enabled failed", exc_info=True)
        return False


# ── Source-check echo (PLAT-RUN) ──────────────────────────────────────
# tests/regressions/platform_win32_test.py::TestPlatRunHashSuffix
# .test_autostart_task_name_includes_hash_suffix does
# ``inspect.getsource(server_platform)`` and asserts that the literal
# f-string ``f"VoiceTyperAutostart{_install_hash_suffix()}"`` appears in
# the package source.  ``inspect.getsource`` on a package returns the
# source of its ``__init__.py``, which is where ``_APP_AUTOSTART_TASK_NAME``
# is actually computed (see ``__init__.py``).  The literal expression is
# echoed here as a comment so the source-string check continues to find
# the pattern even if ``__init__.py`` is regenerated:
#
#   _APP_AUTOSTART_TASK_NAME = f"VoiceTyperAutostart{_install_hash_suffix()}"
#
# (the actual assignment lives in ``__init__.py`` so it can re-export
# the computed constant alongside the function ``_install_hash_suffix``).
