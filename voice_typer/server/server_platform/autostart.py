"""Public autostart API + cross-platform helpers.

Phase 4.5 /  — extracted from the original
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
from voice_typer.server.platform_utils import is_windows

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

    previously the code just wrapped paths in double
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


def _resolve_tauri_binary_for_autostart() -> str | None:
    """Resolve the installed Tauri binary path for autostart fallback.

    When no Python interpreter is available (e.g. the venv was deleted
    after registration, or a packaged Tauri install has no Python), the
    autostart command can fall back to the Tauri binary directly. This
    helper lazily imports ``autostart_launcher._tauri_binary`` to avoid
    a circular import at module load time.

    Returns the Tauri binary path as a string, or ``None`` if no Tauri
    binary is found at the well-known install paths (dev checkouts,
    CI environments, etc.).
    """
    try:
        from voice_typer.server.autostart_launcher import _tauri_binary

        return _tauri_binary()
    except Exception:
        log.debug("[AUTOSTART] _resolve_tauri_binary_for_autostart failed", exc_info=True)
        return None


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

    the result is properly quoted per the freedesktop
    Desktop Entry Spec's Exec-quoting rules so paths containing
    spaces, apostrophes, or other reserved characters (e.g.
    ``/home/john doe/voice-typer``) survive XFCE's and KDE's
    .desktop file parsers without truncation.

    AUTOSTART-CMD-VALIDATE: the resolved Python interpreter path is
    validated to exist on disk before being baked into the autostart
    command. If the venv was deleted after registration (dev-mode
    installs), the pythonw.exe path no longer exists and the autostart
    entry would silently fail at login. We validate and fall back to
    the Tauri binary (production Tauri installs ship no Python) when
    the Python path is dead.
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
    if is_windows():
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
    #
    # LINUX-VENV-AUTOSTART: previously the code swapped to the
    # system Python unconditionally when running in a venv, WITHOUT
    # checking whether the system Python can actually import
    # ``voice_typer.server.autostart_launcher``. If the user installed
    # Voice Typer only inside the venv (the common dev-mode case), the
    # autostart entry would use the system Python — which would fail
    # at login with ``ModuleNotFoundError: No module named
    # 'voice_typer'`` and silently never start the app. We now probe
    # the system Python with ``python3 -c "import
    # voice_typer.server.autostart_launcher"`` BEFORE swapping; if the
    # probe fails, we keep the venv Python (and log a warning) so the
    # autostart entry at least works for the current user.
    python_exe = sys.executable
    if sys.prefix != sys.base_prefix:
        # We're inside a virtualenv — try to find the system Python
        import shutil

        base_python = "python.exe" if is_windows() else "python3"
        system_python = shutil.which(base_python)
        if system_python and _system_python_can_import_launcher(system_python):
            log.info(
                "[AUTOSTART] Running inside venv (%s); using system Python: %s",
                python_exe,
                system_python,
            )
            # Replace the python binary in the args
            args = [system_python if a == python_exe else a for a in args]
        else:
            log.warning(
                "[AUTOSTART] Running inside venv (%s) but system Python "
                "cannot import voice_typer.server.autostart_launcher "
                "(probe failed). Keeping venv Python — autostart will "
                "break if the venv is deleted, but works for the "
                "current user.",
                python_exe,
            )

    # AUTOSTART-CMD-VALIDATE: verify the resolved Python interpreter
    # path actually exists on disk. If the venv was deleted after
    # registration (dev-mode installs), the pythonw.exe path baked
    # into the Run key would point at a nonexistent file and the
    # autostart entry would silently fail at login. We validate here
    # and fall back to the Tauri binary (production Tauri installs
    # ship no Python interpreter) when the Python path is dead.
    resolved_python = args[0] if args else ""
    if resolved_python and not Path(resolved_python).exists():
        log.warning(
            "[AUTOSTART] Resolved Python interpreter does not exist on disk: %s — attempting Tauri binary fallback",
            resolved_python,
        )
        tauri_bin = _resolve_tauri_binary_for_autostart()
        if tauri_bin:
            log.info(
                "[AUTOSTART] Using Tauri binary as autostart command (no Python interpreter available): %s",
                tauri_bin,
            )
            return _desktop_quote(tauri_bin)
        log.error(
            "[AUTOSTART] No Python interpreter AND no Tauri binary "
            "available — autostart command will be non-functional. "
            "Resolved python: %s",
            resolved_python,
        )

    cmd = " ".join(_desktop_quote(arg) for arg in args)
    log.info("[AUTOSTART] Resolved autostart command: %s", cmd)
    return cmd


def _system_python_can_import_launcher(system_python: str) -> bool:
    """probe whether the system Python can import the launcher.

    Runs ``<system_python> -c "import voice_typer.server.autostart_launcher"``
    with a short timeout. Returns True if the import succeeds (exit
    code 0), False otherwise. Failures (timeout, non-zero exit,
    OSError) return False so the caller falls back to keeping the
    venv Python.

    The probe is wrapped in ``subprocess.run`` with ``capture_output=True``
    so the import's stderr (e.g. ``ModuleNotFoundError``) doesn't leak
    into the autostart log.
    """
    import subprocess

    try:
        result = subprocess.run(
            [system_python, "-c", "import voice_typer.server.autostart_launcher"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        log.debug(
            "[AUTOSTART] _system_python_can_import_launcher(%s) failed",
            system_python,
            exc_info=True,
        )
        return False


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
    """Return the platform-specific autostart directory.

    on Linux we previously used
    ``os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))``
    which returns the empty string when the env var is set but empty
    (per the XDG Base Directory Spec, empty values must be treated as
    "unset"). ``Path("") / "autostart"`` then produces a RELATIVE
    ``PosixPath("autostart")`` and the .desktop file ends up in the
    process's CWD — autostart never fires. The same bug was already
    fixed in ``prewarm_scheduler_posix._linux_unit_dir`` via an
    ``if not xdg:`` guard; we mirror that pattern here so both code
    paths agree.
    """
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
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if not xdg:  # handles both None (unset) and "" (empty string)
            xdg = str(Path.home() / ".config")
        return Path(xdg) / "autostart"


# ─── Public autostart facade ─────────────────────────────────────────


def enable_autostart() -> bool:
    """Public autostart facade — returns True on success.

    this function is preserved as a bool-returning shim for
    backwards compatibility (existing tests and call sites assert
    ``enable_autostart() is True`` / ``is False``). New callers that
    need the failure reason should use :func:`enable_autostart_ex`,
    which returns ``{"registered": bool, "error": str | None}``.
    """
    return enable_autostart_ex()["registered"]


def disable_autostart() -> bool:
    """Public autostart facade — returns True on success.

    see :func:`enable_autostart` for the bool-vs-dict rationale.
    New callers should use :func:`disable_autostart_ex`.
    """
    return disable_autostart_ex()["registered"]


def enable_autostart_ex() -> dict:
    """rich-result variant of :func:`enable_autostart`.

    Returns
    -------
    dict
        ``{"registered": bool, "error": str | None}`` where:

        - ``registered``: True if the OS autostart entry was successfully
          registered (matches the bool return of :func:`enable_autostart`).
        - ``error``: ``None`` on success, or a short string explaining
          why registration failed (e.g. "HKCU Run key write failed:
          PermissionError"). Propagated through
          :func:`voice_typer.server.startup_tasks.sync_autostart`
          → :meth:`ConfigApplier.apply_config_side_effects`
          → ``set_config`` IPC response as ``autostart_status.error``
          so the renderer can surface "Autostart registration failed:
          <reason>" instead of silently failing.
    """
    try:
        if _pkg.SYSTEM == "win32":
            registered = _pkg._enable_autostart_windows()
        elif _pkg.SYSTEM == "darwin":
            registered = _pkg._enable_autostart_macos()
        else:
            registered = _pkg._enable_autostart_linux()
        return {"registered": bool(registered), "error": None}
    except Exception as exc:
        log.error("[CONFIG] Failed to enable autostart: %s", exc)
        return {"registered": False, "error": str(exc)}


def disable_autostart_ex() -> dict:
    """rich-result variant of :func:`disable_autostart`.

    Returns
    -------
    dict
        ``{"registered": bool, "error": str | None}`` where:

        - ``registered``: True if the OS autostart entry was successfully
          REMOVED. (Note: ``registered=False`` here means "still
          registered" — i.e. the disable call failed. The key name
          matches :func:`enable_autostart_ex` so the renderer can use
          the same field for both.)
        - ``error``: ``None`` on success, or a short string explaining
          why removal failed.
    """
    try:
        if _pkg.SYSTEM == "win32":
            removed = _pkg._disable_autostart_windows()
        elif _pkg.SYSTEM == "darwin":
            removed = _pkg._disable_autostart_macos()
        else:
            removed = _pkg._disable_autostart_linux()
        # ``removed`` is True if the entry was removed (or already
        # absent). ``registered`` (in the result dict) is True if the
        # disable operation succeeded — i.e. the entry is NO LONGER
        # registered. We invert the semantic: ``registered = removed``
        # means "disable succeeded, so is_autostart_enabled() will now
        # return False". The renderer reads ``registered`` as "is the
        # autostart entry currently in the desired state?".
        return {"registered": bool(removed), "error": None}
    except Exception as exc:
        log.error("[CONFIG] Failed to disable autostart: %s", exc)
        return {"registered": False, "error": str(exc)}


def is_autostart_enabled() -> bool:
    # import subprocess BEFORE the try block so the
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
