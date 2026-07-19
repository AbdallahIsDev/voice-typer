"""Windows desktop shortcut (.lnk) creation.

Phase 4.5 / ARCH-045 — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Implements:

  - :func:`_generate_icon_ico` — render the logo PNG to a .ico file
    (skipped if the .ico already exists and is newer than the source
    PNG).
  - :func:`_universal_launcher_path` — path to ``autostart_launcher.py``
    (the single universal launch entry point).
  - :func:`_start_menu_programs_dir` — Windows Start Menu → Programs
    directory for the current user.
  - :func:`_ps_single_quote` — SEC-10 PowerShell single-quote escaping
    (disables all variable expansion, command substitution, escape
    sequences, etc.).
  - :func:`_build_powershell_lnk_script` — build the .lnk-creation
    PowerShell script as a single string (extracted for unit testing).
  - :func:`_create_lnk_shortcut` — create a single .lnk shortcut
    (win32com first, PowerShell fallback).
  - :func:`create_launcher_shortcut` — create Desktop + Start Menu
    shortcuts for Voice Typer.

Patch-path compatibility
------------------------
Tests patch ``subprocess.run`` via
``monkeypatch.setattr("voice_typer.server.server_platform.subprocess.run", _fake_run)``
(in :mod:`tests.test_sec_8_9_10_security_fixes`).  This patches the
``run`` attribute on the stdlib ``subprocess`` module object (which is
the same object this module imports via ``import subprocess`` at the
top), so ``subprocess.run(...)`` calls below pick up the patch without
any ``_pkg`` indirection.

Tests patch ``SYSTEM`` via
``monkeypatch.setattr(server_platform, "SYSTEM", "win32"|"linux")`` (in
:mod:`tests.test_platform`).  ``create_launcher_shortcut`` reads
``_pkg.SYSTEM`` at call time so the patch takes effect.

``inspect.getsource`` compatibility
-----------------------------------
All seven functions are genuinely defined here, so
``inspect.getsource(_build_powershell_lnk_script)`` etc. continue to
read from this file.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path

# Patch-path bridge: route lookups of ``SYSTEM`` through the package
# namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.SYSTEM", "linux")``
# keep affecting production code defined here (specifically
# ``create_launcher_shortcut``'s ``SYSTEM != "win32"`` early return).
from voice_typer.server import server_platform as _pkg
from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


# ─── Launcher shortcut ────────────────────────────────────────────────


def _generate_icon_ico() -> Path | None:
    """Generate a logo .ico file for the shortcut icon.

    Uses the pre-rendered logo PNG (from ``client/scripts/logo.svg``,
    rendered by ``generate-icons.mjs``).  Saves to
    ``%APPDATA%/voice-typer/icon.ico`` and returns the path, or None
    on failure.

    Skips regeneration if the icon already exists and is newer than the
    source PNG — avoids wasteful disk writes on every startup.
    """
    try:
        from PIL import Image
    except ImportError:
        log.debug("PIL not available — cannot generate icon")
        return None

    icon_png = Path(__file__).resolve().parent.parent / "assets" / "logo-256.png"
    if not icon_png.exists():
        log.debug("Pre-rendered logo PNG not found — cannot generate icon")
        return None

    appdata = Path(os.environ.get("APPDATA", Path.home()))
    icon_dir = appdata / "voice-typer"
    icon_dir.mkdir(parents=True, exist_ok=True)
    ico_path = icon_dir / "icon.ico"

    # Skip if icon already exists and is newer than the source PNG
    if ico_path.exists() and ico_path.stat().st_mtime >= icon_png.stat().st_mtime:
        return ico_path

    img = Image.open(str(icon_png)).convert("RGBA")

    try:
        img.save(str(ico_path), format="ICO", sizes=[(256, 256)])
        log.info("[STARTUP] Shortcut icon saved: %s", ico_path)
        return ico_path
    except OSError as e:
        log.warning("[STARTUP] Failed to save icon: %s", e)
        return None


def _universal_launcher_path() -> Path:
    """Path to autostart_launcher.py — the single universal launch entry point."""
    return Path(__file__).resolve().parent.parent / "autostart_launcher.py"


def _start_menu_programs_dir() -> Path:
    """Windows Start Menu → Programs directory for the current user.

    Shortcuts placed here are discoverable via Start Menu search, so the
    user can open/focus the app from the Start Menu.
    """
    return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _ps_single_quote(value: object) -> str:
    """SEC-10: escape a value for embedding in a PowerShell single-quoted string.

    Wraps *value* in single quotes and doubles any embedded single
    quotes (``'`` → ``''``), which is the ONLY escaping required
    inside a PowerShell single-quoted string.

    Why single-quoted strings (defense-in-depth):
        PowerShell single-quoted strings disable **all** of the
        following expansions that the previous double-quoted
        generator left vulnerable:

          * Variable expansion:      ``$env:USERNAME``
          * Sub-expression:          ``$(Get-Process)``
          * Command substitution:    ``& whoami``
          * Backtick escape sequences: `` `n ``, `` `t ``, `` `$ ``
          * Statement chaining:      ``; Remove-Item C:\\ -Recurse``
          * Pipeline operator:       ``| Out-File evil.txt``
          * Redirection:             ``> evil.txt``, ``< input.txt``
          * Grouping:                ``(...)`` as expression
          * Newline as separator:    multi-line injection

        The previous generator only escaped ``"`` as ``""`` (the
        double-quote escape inside double-quoted strings), leaving
        every character above injectable. A malicious or merely
        unusual path / description / arguments value could break
        out of the double-quoted context and execute arbitrary
        PowerShell.

    Note:
        Single-quoted strings in PowerShell natively support
        embedded newlines, so multi-line values are safe without
        any special handling.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _build_powershell_lnk_script(
    lnk_path: Path,
    target: str,
    arguments: str,
    icon_ico: Path | None,
    description: str,
    working_dir: Path | None = None,
) -> str:
    """SEC-10: build the .lnk-creation PowerShell script as a single string.

    Extracted from ``_create_lnk_shortcut`` so the script body can be
    unit-tested directly (without spawning ``powershell.exe`` or
    mocking ``subprocess.run``). Each user-supplied value is wrapped
    via :func:`_ps_single_quote` so no character can break out of the
    string literal and inject PowerShell code.
    """
    if working_dir is None:
        working_dir = Path.home()
    lines = [
        "$s = New-Object -ComObject WScript.Shell",
        f"$l = $s.CreateShortcut({_ps_single_quote(lnk_path)})",
        f"$l.TargetPath = {_ps_single_quote(target)}",
        # arguments already has surrounding double quotes from the caller
        # (e.g. '"C:\\launcher.py"'); _ps_single_quote wraps the whole
        # string in single quotes so the inner double quotes are literal.
        f"$l.Arguments = {_ps_single_quote(arguments)}",
        f"$l.Description = {_ps_single_quote(description)}",
        f"$l.WorkingDirectory = {_ps_single_quote(working_dir)}",
    ]
    if icon_ico:
        lines.append(f"$l.IconLocation = {_ps_single_quote(icon_ico)}")
    lines.append("$l.Save()")
    return "\n".join(lines)


def _create_lnk_shortcut(
    lnk_path: Path,
    target: str,
    arguments: str,
    icon_ico: Path | None,
    description: str,
) -> bool:
    """Create a single .lnk shortcut. Returns True on success.

    Tries win32com first (fast, native COM).  Falls back to a PowerShell
    script written to a temp file — always available on Windows, no extra
    packages needed, and avoids string-escaping problems.

    SEC-10: the PowerShell fallback now wraps every user-supplied value
    (path, target, arguments, description, icon, working directory) in
    a single-quoted PowerShell string via :func:`_ps_single_quote`,
    which disables all variable expansion, command substitution, and
    escape-sequence processing. Previously only ``"`` was escaped (as
    ``""``), leaving ``$``, backtick, ``;``, ``|``, ``&``, ``()``,
    ``<>``, and newlines injectable.
    """
    # 1) win32com path (native COM, fastest).
    try:
        import win32com.client  # noqa: F811

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        shortcut.Targetpath = target
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = str(Path.home())
        shortcut.Description = description
        if icon_ico:
            shortcut.IconLocation = str(icon_ico)
        shortcut.save()
        return True
    except ImportError:
        log.debug("[STARTUP] win32com unavailable — trying PowerShell fallback")
    except OSError as e:
        log.warning("[STARTUP] Failed to create .lnk (%s): %s", lnk_path, e)
        return False

    # 2) PowerShell fallback — write a temp .ps1 to avoid escaping issues.
    # SEC-10: every user-supplied value is wrapped in a single-quoted
    # PowerShell string (see _build_powershell_lnk_script /
    # _ps_single_quote). This is defense-in-depth against path /
    # description / arguments values that contain PowerShell
    # metacharacters — even though Voice Typer controls most of these
    # values today, a future change (e.g. user-customizable shortcut
    # description) shouldn't silently introduce an injection vector.
    import os as _os
    import tempfile

    tmp = None
    try:
        script = _build_powershell_lnk_script(
            lnk_path=lnk_path,
            target=target,
            arguments=arguments,
            icon_ico=icon_ico,
            description=description,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f:
            f.write(script)
            tmp = f.name

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp],
            check=True,
            capture_output=True,
            timeout=30,
        )
        log.info("[STARTUP] .lnk created via PowerShell fallback: %s", lnk_path)
        return True
    except Exception as e:
        log.warning("[STARTUP] PowerShell .lnk creation failed: %s", e)
        return False
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                _os.unlink(tmp)


def create_launcher_shortcut() -> Path | None:
    """Create Desktop + Start Menu shortcuts for Voice Typer.

    Both shortcuts point at the **universal launcher** (autostart_launcher.py)
    WITHOUT ``--hidden``, so a user click:
      • if the app is already running → focuses its window (via the
        Electron single-instance lock), no second instance;
      • if not running → starts Electron + backend with the dashboard visible.

    This fixes the old bug where the desktop shortcut ran the backend ONLY
    (``pythonw -m voice_typer``), which meant the bubble overlay never
    appeared and Electron never connected to that process.

    Skips any shortcut that already exists and points at the same target,
    avoiding wasteful .lnk overwrites on every startup.

    Returns the path to the Desktop shortcut (the primary one), or None on
    unsupported platforms / failure.
    """
    if _pkg.SYSTEM != "win32":
        log.info("[STARTUP] Launcher shortcut only supported on Windows")
        return None

    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.exists():
        log.warning("[STARTUP] pythonw.exe not found at %s — cannot create console-free launcher", pythonw)
        return None

    launcher = _universal_launcher_path()
    desktop = Path.home() / "Desktop"
    start_menu = _start_menu_programs_dir()
    icon_ico = _generate_icon_ico()

    # Primary: Desktop .lnk pointing at the universal launcher (no --hidden).
    primary_path: Path | None = None
    lnk_desktop = desktop / "Voice Typer.lnk"

    # Skip if the Desktop shortcut already exists — no need to recreate
    # on every startup now that the legacy .bat → .lnk migration is done.
    if lnk_desktop.exists():
        primary_path = lnk_desktop
    else:
        if _create_lnk_shortcut(
            lnk_desktop,
            target=str(pythonw),
            arguments=f'"{launcher}"',
            icon_ico=icon_ico,
            description=f"{APP_NAME} — voice-to-text dictation",
        ):
            log.info("[STARTUP] Desktop .lnk created: %s", lnk_desktop)
            primary_path = lnk_desktop
        else:
            log.warning("[STARTUP] Could not create desktop .lnk — install pywin32 or check logs")

    # Secondary: Start Menu copy so Start search finds "Voice Typer".
    try:
        start_menu.mkdir(parents=True, exist_ok=True)
        lnk_start = start_menu / "Voice Typer.lnk"
        if lnk_start.exists():
            pass
        elif _create_lnk_shortcut(
            lnk_start,
            target=str(pythonw),
            arguments=f'"{launcher}"',
            icon_ico=icon_ico,
            description=f"{APP_NAME} — voice-to-text dictation",
        ):
            log.info("[STARTUP] Start Menu .lnk created: %s", lnk_start)
    except OSError as e:
        log.debug("[STARTUP] Start Menu shortcut skipped: %s", e)

    return primary_path
