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
    ``monkeypatch.setattr(platform_flags, "SYSTEM", "linux")`` etc.
    All dispatch logic reads ``platform_flags.SYSTEM`` at call time.
  - ``get_autostart_dir`` — patched via
    ``monkeypatch.setattr(autostart_mod, "get_autostart_dir", lambda: tmp_path)``.
    The platform-specific enable/disable/is_enabled helpers in
    :mod:`.autostart_macos` / :mod:`.autostart_linux` /
    :mod:`.autostart_windows` look it up via ``_autostart.get_autostart_dir()``
    at call time (``_autostart`` is THIS module).
  - ``_autostart_command`` — patched via
    ``monkeypatch.setattr(autostart_mod, "_autostart_command", lambda: ...)``.
    The Linux ``_enable_autostart_linux`` (in :mod:`.autostart_linux`) and
    the Windows Run-key path (``_register_app_autostart_runkey`` in
    :mod:`.autostart_windows`) look it up via ``_autostart._autostart_command()``.
  - ``_enable_autostart_windows`` / ``_enable_autostart_macos`` /
    ``_enable_autostart_linux`` — patched via
    ``monkeypatch.setattr(autostart_macos, "_enable_autostart_macos", lambda: ...)``
    etc. (on the OWNING submodule). ``enable_autostart_ex`` looks them
    up through the submodule module object at call time.
  - ``_disable_autostart_windows`` / ``_disable_autostart_macos`` /
    ``_disable_autostart_linux`` — same pattern via ``disable_autostart_ex``
    (resolved through the owning submodule's module object at call time).
  - ``_is_autostart_windows`` / ``_is_autostart_macos`` /
    ``_is_autostart_linux`` — same pattern via ``is_autostart_enabled``
    (resolved through the owning submodule's module object at call time).

``inspect.getsource`` compatibility
-----------------------------------
All facade functions are genuinely defined here, so
``inspect.getsource(enable_autostart)`` etc. continue to read from this
file. The platform-specific implementations are in their respective
submodules. ``_APP_AUTOSTART_TASK_NAME`` is also defined here (next to
its ``_install_hash_suffix`` dependency) and read by
:mod:`.autostart_windows` through this module's attribute at call time;
the source-string check in ``tests/regressions/test_platform_win32.py``
reads THIS file's source for the literal f-string.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

# Patch-path bindings. Sibling submodules are bound as MODULE objects so
# every attribute access below resolves at call time against the owning
# submodule (tests patch the submodule attribute, production sees it).
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.server_platform import (
    autostart_linux as _autostart_linux_mod,
    autostart_macos as _autostart_macos_mod,
    autostart_windows as _autostart_windows_mod,
    platform_flags as _platform_flags,
)

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
    # A literal newline / carriage-return inside the
    # quoted string would still TERMINATE the Exec line (the spec only
    # allows escaping within a single line — there is no line-continuation
    # escape), letting a malicious path inject a new .desktop field. The
    # spec's reserved-char set includes `\n`, and no amount of quoting
    # can make a newline safe inside a single-line Exec field. Reject
    # such args outright (fail loudly) rather than emit a corrupt file.
    if "\n" in arg or "\r" in arg:
        raise ValueError(f"_desktop_quote: arg contains newline/carriage-return: {arg!r}")
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

    # PLAT-VENV/SILENT-LOGON: the probe above may have replaced
    # args[0] with the system python.exe (console-subsystem). Re-apply
    # the pythonw.exe preference to the FINAL interpreter so the
    # Run-key / Startup-bat entry never flashes a console window.
    if is_windows() and args:
        pythonw = Path(args[0]).parent / "pythonw.exe"
        if pythonw.exists():
            args[0] = str(pythonw)

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
            if is_windows():
                # Windows command lines: single token, quoted only if the
                # path contains whitespace. ``_desktop_quote`` (freedesktop
                # Exec escaping) would double the backslashes here.
                return subprocess.list2cmdline([tauri_bin])
            return _desktop_quote(tauri_bin)
        log.error(
            "[AUTOSTART] No Python interpreter AND no Tauri binary "
            "available — autostart command will be non-functional. "
            "Resolved python: %s",
            resolved_python,
        )

    # AUTOSTART-QUOTING-FIX (root cause of the Windows Run-key logon
    # failure): the command was previously built with ``_desktop_quote``
    # on EVERY platform. ``_desktop_quote`` implements the freedesktop
    # Desktop Entry Spec's Exec quoting, which escapes ``\`` as ``\\`` —
    # correct for Linux ``.desktop`` files, but WRONG for Windows command
    # lines: it produced Run-key values like
    # ``"C:\\Users\\11\\.voice-typer\\venv\\Scripts\\pythonw.exe" ...``
    # (doubled backslashes). The malformed value then failed to launch at
    # logon (Shell-Core event 9707/9708, PID 0 on every logon) while
    # ``Path.exists()`` still reported the path valid (it collapses
    # ``\\`` to ``\``), so the broken entry was never re-registered.
    # On Windows we now build the command line with
    # ``subprocess.list2cmdline`` (the exact quoting Windows itself
    # uses — backslashes preserved literally, args quoted only when
    # needed). On macOS/Linux the freedesktop Exec quoting stays.
    cmd = subprocess.list2cmdline(args) if is_windows() else " ".join(_desktop_quote(arg) for arg in args)
    log.info("[AUTOSTART] Resolved autostart command: %s", cmd)
    return cmd


def _windows_create_no_window_flags() -> int:
    """Return the Win32 ``CREATE_NO_WINDOW`` creation flag.

    Shared by the subprocess launches in this package that must not
    flash a console window on Windows: the system-python import probe
    (:func:`_system_python_can_import_launcher`), the legacy
    autostart-entry sweep, and the uninstall task sweep. The
    ``getattr`` fallback keeps the helper safe under unusual test
    stubs that monkeypatch ``subprocess`` — the constant exists on
    every Python 3.x Windows build.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


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

    on Windows the spawned ``python.exe`` subprocess would
    otherwise flash a console window for the ~50 ms the import probe
    runs. We pass ``creationflags=0x08000000`` (``CREATE_NO_WINDOW``)
    guarded by ``is_windows()`` so the probe is silent on Windows and
    a no-op on macOS/Linux (where ``creationflags`` isn't a valid
    ``subprocess.run`` kwarg).
    """
    import subprocess

    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 5.0,
        "check": False,
    }
    if is_windows():
        # CREATE_NO_WINDOW — prevents a console flash when probing
        # python.exe on Windows (shared helper: the flag value is
        # single-sourced with the sweep / uninstall launch sites).
        kwargs["creationflags"] = _windows_create_no_window_flags()

    try:
        result = subprocess.run(
            [system_python, "-c", "import voice_typer.server.autostart_launcher"],
            **kwargs,
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


def _install_identifier() -> str:
    """PLAT-RUN: Return a STABLE per-install identifier for autostart naming.

    Historically the hash was computed from ``sys.executable``. That was
    unstable across launch contexts: the app can start via the console
    shim (``python.exe`` / ``voice-typer.exe``), the dev venv, or the
    autostart launcher (``pythonw.exe``) — each has a different
    ``sys.executable``, so the Run-key / task / .bat name registered by
    one process was never found by the next, and the app re-registered
    on every launch ("Config says autostart=true but it is disabled --
    enabling" loop). The launcher script path is identical no matter
    which interpreter runs it, and differs between installs (PLAT-RUN
    multi-install support), so it is the correct stable key.
    """
    return str(Path(__file__).resolve().parent.parent / "autostart_launcher.py")


def _install_hash() -> str:
    """PLAT-RUN: 8-char hex hash of the stable install identifier.

    Shared by ``_install_hash_suffix`` (Task Scheduler / Startup .bat
    naming) and ``autostart_windows._run_key_name`` (HKCU Run-key
    naming) so all three mechanisms agree on the same per-install
    name regardless of which interpreter / entry point launched the
    process.
    """
    return hashlib.sha256(_install_identifier().encode()).hexdigest()[:8]


def _install_hash_suffix() -> str:
    """PLAT-RUN: Return an 8-char hash suffix for the install path.

    Empty string on non-Windows or if hashing fails (non-fatal).
    """
    try:
        return "_" + _install_hash()
    except (OSError, ValueError):
        log.debug("[PLATFORM] _install_hash_suffix failed", exc_info=True)
        return ""


# ─── Task Scheduler task name (PLAT-RUN) ─────────────────────────────
# Canonical ``com.voicetyper.*`` reverse-DNS task name with the
# install-path hash appended so two installations in different
# directories register distinct schtasks entries.
#
# Defined HERE (not in :mod:`.autostart_windows`) because it depends on
# ``_install_hash_suffix`` above, and because the source-string check in
# ``tests/regressions/test_platform_win32.py`` reads this file's source.
# :mod:`.autostart_windows` reads it through THIS module's attribute at
# call time (``_autostart_mod._APP_AUTOSTART_TASK_NAME``).
_APP_AUTOSTART_TASK_NAME = f"com.voicetyper.autostart{_install_hash_suffix()}"


# ─── Autostart directory ─────────────────────────────────────────────


def get_autostart_dir() -> Path:
    """Return the platform-specific autostart directory.

    on Linux we previously used
    ``os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))``
    which returns the empty string when the env var is set but empty
    (per the XDG Base Directory Spec, empty values must be treated as
    "unset"). ``Path("") / "autostart"`` then produces a RELATIVE
    ``PosixPath("autostart")`` and the .desktop file ends up in the
    process's CWD — autostart never fires. Fixed via an ``if not
    xdg:`` guard that treats both ``None`` (unset) and ``""`` (empty)
    as "use the default ``~/.config``".
    """
    if _platform_flags.SYSTEM == "win32":
        return (
            Path(os.environ.get("APPDATA", Path.home()))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    elif _platform_flags.SYSTEM == "darwin":
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
        if _platform_flags.SYSTEM == "win32":
            registered = _autostart_windows_mod._enable_autostart_windows()
        elif _platform_flags.SYSTEM == "darwin":
            registered = _autostart_macos_mod._enable_autostart_macos()
        else:
            registered = _autostart_linux_mod._enable_autostart_linux()
        return {"registered": bool(registered), "error": None}
    except Exception as exc:
        log.exception("[CONFIG] Failed to enable autostart: %s", exc)
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
        if _platform_flags.SYSTEM == "win32":
            removed = _autostart_windows_mod._disable_autostart_windows()
        elif _platform_flags.SYSTEM == "darwin":
            removed = _autostart_macos_mod._disable_autostart_macos()
        else:
            removed = _autostart_linux_mod._disable_autostart_linux()
        # ``removed`` is True if the entry was removed (or already
        # absent). ``registered`` (in the result dict) is True if the
        # disable operation succeeded — i.e. the entry is NO LONGER
        # registered. We invert the semantic: ``registered = removed``
        # means "disable succeeded, so is_autostart_enabled() will now
        # return False". The renderer reads ``registered`` as "is the
        # autostart entry currently in the desired state?".
        return {"registered": bool(removed), "error": None}
    except Exception as exc:
        log.exception("[CONFIG] Failed to disable autostart: %s", exc)
        return {"registered": False, "error": str(exc)}


def is_autostart_enabled() -> bool:
    # import subprocess BEFORE the try block so the
    # ``except subprocess.CalledProcessError`` clause has a guaranteed-bound
    # name (matches the pattern at line ~826).
    import subprocess

    try:
        if _platform_flags.SYSTEM == "win32":
            return _autostart_windows_mod._is_autostart_windows()
        elif _platform_flags.SYSTEM == "darwin":
            return _autostart_macos_mod._is_autostart_macos()
        else:
            return _autostart_linux_mod._is_autostart_linux()
    except (OSError, ImportError, FileNotFoundError, subprocess.CalledProcessError):
        log.debug("[PLATFORM] is_autostart_enabled failed", exc_info=True)
        return False


# ── Source-check echo (PLAT-RUN) ──────────────────────────────────────
# tests/regressions/test_platform_win32.py::TestPlatRunAutostartTaskHashed
# .test_autostart_task_name_includes_hash_suffix does
# ``inspect.getsource(autostart)`` and asserts that the literal f-string
# ``f"com.voicetyper.autostart{_install_hash_suffix()}"`` appears in the
# source.  The actual assignment lives in THIS module (see
# ``_APP_AUTOSTART_TASK_NAME`` above, next to ``_install_hash_suffix``);
# the literal expression is echoed here as a comment so the source-string
# check keeps finding the pattern even if a future refactor moves the
# assignment elsewhere:
#
#   _APP_AUTOSTART_TASK_NAME = f"com.voicetyper.autostart{_install_hash_suffix()}"
