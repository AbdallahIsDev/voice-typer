"""Public autostart API + cross-platform helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

# Patch-path bindings. Sibling submodules are bound as MODULE objects so
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.server_platform import (
    autostart_linux as _autostart_linux_mod,
    autostart_macos as _autostart_macos_mod,
    autostart_windows as _autostart_windows_mod,
    platform_flags as _platform_flags,
)

log = logging.getLogger(__name__)


def _desktop_quote(arg: str) -> str:
    """Quote ``arg`` per the freedesktop Desktop Entry Spec's Exec rules."""
    # A literal newline / carriage-return inside the
    if "\n" in arg or "\r" in arg:
        raise ValueError(f"_desktop_quote: arg contains newline/carriage-return: {arg!r}")
    reserved = set(" \t\n\"'\\><~|&;$*?#()")
    if not any(c in reserved for c in arg):
        return arg  # no quoting needed
    # Escape backslash, double-quote, backtick, dollar per spec.
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _resolve_tauri_binary_for_autostart() -> str | None:
    """Resolve the installed Tauri binary path for autostart fallback."""
    try:
        from voice_typer.server.autostart_launcher import _tauri_binary

        return _tauri_binary()
    except Exception:
        log.debug("[AUTOSTART] _resolve_tauri_binary_for_autostart failed", exc_info=True)
        return None


def _launcher_script_path() -> Path:
    """Return the source-tree path of the OS-facing launcher script."""
    return Path(__file__).resolve().parent.parent / "autostart_launcher.py"


def _is_frozen_autostart_context() -> bool:
    """True when running inside a packaged (frozen, no-Python) sidecar."""
    if getattr(sys, "frozen", False):
        return True
    exe_name = os.path.basename(sys.executable or "").lower()
    if exe_name.startswith("voice-typer-tauri") or exe_name.startswith("python-sidecar"):
        return True
    if getattr(sys, "_nuitka_version", None) is not None:
        return True
    if getattr(sys, "_nuitka_binary", None) is not None:
        return True
    try:
        import builtins

        if getattr(builtins, "__compiled__", None) is not None:
            return True
    except Exception:
        pass
    return False


def _is_launcher_script_missing() -> bool:
    """True when ``autostart_launcher.py`` is missing or unimportable."""
    try:
        if not _launcher_script_path().is_file():
            return True
    except OSError:
        return True
    try:
        import importlib.util

        spec = importlib.util.find_spec("voice_typer.server.autostart_launcher")
        if spec is None or spec.origin is None:
            return True
        if spec.origin in ("built-in", "frozen"):
            return True
        try:
            if not Path(spec.origin).is_file():
                return True
        except OSError:
            return True
    except Exception:
        return True
    return False


def _packaged_tauri_target() -> tuple[str, list[str]] | None:
    """Return the direct-binary autostart target for packaged installs.

    Returns ``(binary, ["--hidden", "--delay", N])`` whenever an
    installed Tauri binary resolves. The direct-binary entry is the
    canonical logon target once a packaged install exists: it needs no
    interpreter, no launcher script on disk, and no fail-closed
    manifest hop (the OS spawns the binary itself, the host honors
    ``--hidden``/``--delay`` argv). This fires for frozen sidecars,
    for checkouts whose launcher script is gone, AND for plain dev
    interpreters on machines with a packaged install (logon must start
    the installed release build, not a transient dev process).
    Pure-dev machines (no installed binary) get ``None`` and keep the
    ``python launcher.py`` path.
    """
    try:
        tauri_bin = _resolve_tauri_binary_for_autostart()
    except Exception:
        return None
    if not tauri_bin:
        return None
    try:
        from voice_typer.server.task_scheduler import _APP_AUTOSTART_DELAY_SECONDS

        delay_str = str(_APP_AUTOSTART_DELAY_SECONDS)
    except Exception:
        delay_str = "3"
    return (tauri_bin, ["--hidden", "--delay", delay_str])


def _launcher_entry_superseded(value: str) -> bool:
    """True when a python-launcher entry must migrate to direct-binary.

    A command that launches ``autostart_launcher.py`` through a Python
    interpreter is superseded the moment an installed Tauri binary
    resolves: the builder (``_packaged_tauri_target``) would emit a
    direct-binary entry for the same machine, so an entry still
    pointing at the interpreter is a stale previous generation (it
    boots the launcher, which fails closed on the manifest or exits
    without a binary). Validators report it as NOT registered so
    ``sync_autostart`` re-registers once and converges. Pure-dev
    machines (no installed binary) are unaffected: their launcher
    entries keep validating.
    """
    if not value or not isinstance(value, str):
        return False
    if "autostart_launcher" not in value.lower():
        return False
    try:
        return _resolve_tauri_binary_for_autostart() is not None
    except Exception:
        return False


def _is_legacy_stale_autostart_reference(value: str) -> bool:
    """True when an autostart command is CERTAIN-stale legacy shape."""
    if not value or not isinstance(value, str):
        return False
    if "electron" in value.lower():
        return True
    import re

    return re.search(r"(?:^|\s)-m\s+voice_typer", value) is not None


def _references_missing_launcher_script(value: str) -> bool:
    """True when *value* embeds an ``autostart_launcher.py`` path missing on disk."""
    if not value or not isinstance(value, str):
        return False
    if "autostart_launcher.py" not in value.lower():
        return False
    import shlex

    try:
        tokens = shlex.split(value, posix=False)
    except ValueError:
        tokens = value.split()
    for token in tokens:
        stripped = token.strip("\"'")
        if stripped.lower().endswith("autostart_launcher.py"):
            try:
                if not Path(stripped).exists():
                    return True
            except OSError:
                return True
    return False


def _autostart_command() -> str:
    """Build the command that the OS autostart entry should run."""
    # ADR-0009 Issue 4: single source of truth for the delay value.
    from voice_typer.server.task_scheduler import _APP_AUTOSTART_DELAY_SECONDS

    delay_str = str(_APP_AUTOSTART_DELAY_SECONDS)

    # Packaged (frozen, no-Python) Tauri installs: register the app
    packaged = _packaged_tauri_target()
    if packaged is not None:
        tauri_bin, tauri_args = packaged
        if is_windows():
            cmd = subprocess.list2cmdline([tauri_bin, *tauri_args])
        else:
            cmd = " ".join(_desktop_quote(arg) for arg in [tauri_bin, *tauri_args])
        log.info("[AUTOSTART] Resolved packaged autostart command: %s", cmd)
        return cmd

    # The launcher lives next to this module (voice_typer/server/).
    launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"
    # Build the argument list, then quote each arg per the desktop spec.
    if is_windows():
        python_bin = _prefer_pythonw(sys.executable)
        args = [python_bin, str(launcher), "--hidden", "--delay", delay_str]
    else:
        # macOS / Linux: use the current interpreter.
        args = [sys.executable, str(launcher), "--hidden", "--delay", delay_str]

    # ``voice_typer.server.autostart_launcher``. If the user installed
    python_exe = sys.executable
    if sys.prefix != sys.base_prefix:
        # We're inside a virtualenv, try to find the system Python
        base_python = "python.exe" if is_windows() else "python3"
        system_python = _probe_system_python(base_python)
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
                "[AUTOSTART] Running inside venv (%s) but system Python "
                "cannot import voice_typer.server.autostart_launcher "
                "(probe failed). Keeping venv Python, autostart will "
                "break if the venv is deleted, but works for the "
                "current user.",
                python_exe,
            )

    # PLAT-VENV/SILENT-LOGON: the probe above may have replaced
    if is_windows() and args:
        args[0] = _prefer_pythonw(args[0])

    # AUTOSTART-CMD-VALIDATE: verify the resolved Python interpreter
    resolved_python = args[0] if args else ""
    if resolved_python and not Path(resolved_python).exists():
        log.warning(
            "[AUTOSTART] Resolved Python interpreter does not exist on disk: %s, attempting Tauri binary fallback",
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
                return subprocess.list2cmdline([tauri_bin])
            return _desktop_quote(tauri_bin)
        log.error(
            "[AUTOSTART] No Python interpreter AND no Tauri binary "
            "available, autostart command will be non-functional. "
            "Resolved python: %s",
            resolved_python,
        )

    # ``subprocess.list2cmdline`` (the exact quoting Windows itself
    cmd = subprocess.list2cmdline(args) if is_windows() else " ".join(_desktop_quote(arg) for arg in args)
    log.info("[AUTOSTART] Resolved autostart command: %s", cmd)
    return cmd


def _windows_create_no_window_flags() -> int:
    """Return the Win32 ``CREATE_NO_WINDOW`` creation flag."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _system_python_can_import_launcher(system_python: str) -> bool:
    """probe whether the system Python can import the launcher."""
    import subprocess

    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 5.0,
        "check": False,
    }
    if is_windows():
        # CREATE_NO_WINDOW, prevents a console flash when probing
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


def _prefer_pythonw(python_bin: str) -> str:
    """Prefer a sibling ``pythonw.exe`` for a Windows interpreter path.

    BP-127 shared core: the pythonw preference was copy-pasted across
    the generic command builder and the Windows Task Scheduler
    resolver (initial pick + post-probe re-apply). Returns the
    ``pythonw.exe`` sibling when it exists, else the input unchanged.
    Windows-only by construction (``pythonw.exe`` never exists on
    POSIX), callers keep their own ``is_windows()`` gates so output
    shapes stay byte-identical (C-CROSS-1/2).
    """
    pythonw = Path(python_bin).parent / "pythonw.exe"
    return str(pythonw) if pythonw.exists() else python_bin


def _probe_system_python(which_name: str) -> str | None:
    """Shared venv→system-Python probe (BP-127).

    Returns a swappable system interpreter, or ``None`` when no swap
    """
    import shutil

    system_python = shutil.which(which_name)
    if system_python and _system_python_can_import_launcher(system_python):
        return system_python
    return None


def _install_identifier() -> str:
    """PLAT-RUN: Return a STABLE per-install identifier for autostart naming."""
    return str(Path(__file__).resolve().parent.parent / "autostart_launcher.py")


def _install_hash() -> str:
    """PLAT-RUN: 8-char hex hash of the stable install identifier."""
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


# Canonical ``com.voicetyper.*`` reverse-DNS task name with the
_APP_AUTOSTART_TASK_NAME = f"com.voicetyper.autostart{_install_hash_suffix()}"


def get_autostart_dir() -> Path:
    """Return the platform-specific autostart directory."""
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


def enable_autostart() -> bool:
    """Public autostart facade, returns True on success."""
    return enable_autostart_ex()["registered"]


def disable_autostart() -> bool:
    """Public autostart facade, returns True on success."""
    return disable_autostart_ex()["registered"]


def enable_autostart_ex() -> dict:
    """rich-result variant of :func:`enable_autostart`.

    Returns
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
    """
    try:
        if _platform_flags.SYSTEM == "win32":
            removed = _autostart_windows_mod._disable_autostart_windows()
        elif _platform_flags.SYSTEM == "darwin":
            removed = _autostart_macos_mod._disable_autostart_macos()
        else:
            removed = _autostart_linux_mod._disable_autostart_linux()
        # ``removed`` is True if the entry was removed (or already
        return {"registered": bool(removed), "error": None}
    except Exception as exc:
        log.exception("[CONFIG] Failed to disable autostart: %s", exc)
        return {"registered": False, "error": str(exc)}


def is_autostart_enabled() -> bool:
    # import subprocess BEFORE the try block so the
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


# tests/regressions/test_platform_win32.py::TestPlatRunAutostartTaskHashed
