"""- :func:`_ps_single_quote`: SEC-10 PowerShell single-quote escaping
``voice_typer/server/server_platform.py`` god-module.  Implements:
  - :func:`_universal_launcher_path`: path to ``autostart_launcher.py``
Tests patch ``subprocess.run`` via
``monkeypatch.setattr("voice_typer.server.server_platform.subprocess.run", _fake_run)``
``run`` attribute on the stdlib ``subprocess`` module object (which is
the same object this module imports via ``import subprocess`` at the
top), so ``subprocess.run(...)`` calls below pick up the patch without
any ``_pkg`` indirection.
Tests patch ``SYSTEM`` via
``monkeypatch.setattr(platform_flags, "SYSTEM", "win32"|"linux")`` (in
:mod:`tests.test_platform`).  ``create_launcher_shortcut`` reads
``_platform_flags.SYSTEM`` at call time so the patch takes effect.
``inspect.getsource`` compatibility
``inspect.getsource(_build_powershell_lnk_script)`` etc. continue to
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# Patch-path bridge: read ``SYSTEM`` through the owning
from voice_typer.server._paths import APP_SLUG
from voice_typer.server.branding import APP_NAME
from voice_typer.server.server_platform import platform_flags as _platform_flags

log = logging.getLogger(__name__)


def _generate_icon_ico() -> Path | None:
    """Generate a logo .ico file for the shortcut icon."""
    try:
        from PIL import Image
    except ImportError:
        log.debug("PIL not available, cannot generate icon")
        return None

    # The pre-rendered logo PNG lives at the project root
    _server_dir = Path(__file__).resolve().parent.parent
    icon_png = next(
        (
            p
            for p in (
                _server_dir.parent.parent / "logo-256.png",
                _server_dir / "assets" / "logo-256.png",
            )
            if p.exists()
        ),
        None,
    )
    if icon_png is None:
        log.debug("Pre-rendered logo PNG not found, cannot generate icon")
        return None

    appdata = Path(os.environ.get("APPDATA", Path.home()))
    icon_dir = appdata / APP_SLUG
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
        # include the destination .ico path so operators can tell
        log.warning("[STARTUP] Failed to save icon (ico_path=%s): %s", ico_path, e)
        return None


def _universal_launcher_path() -> Path:
    """Path to autostart_launcher.py, the single universal launch entry point."""
    return Path(__file__).resolve().parent.parent / "autostart_launcher.py"


# Legacy .lnk filename from builds that predate the APP_NAME-derived
_LEGACY_LNK_NAME = "Lausu.lnk"


def _existing_launcher_lnk(directory: Path) -> Path | None:
    """Return the existing launcher .lnk in *directory*, or None."""
    lnk = directory / f"{APP_NAME}.lnk"
    if lnk.exists():
        return lnk
    legacy = directory / _LEGACY_LNK_NAME
    if legacy.exists():
        return legacy
    return None


def _start_menu_programs_dir() -> Path:
    """Windows Start Menu → Programs directory for the current user."""
    return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _ps_single_quote(value: object) -> str:
    """SEC-10: escape a value for embedding in a PowerShell single-quoted string."""
    return "'" + str(value).replace("'", "''") + "'"


def _build_powershell_lnk_script(
    lnk_path: Path,
    target: str,
    arguments: str,
    icon_ico: Path | None,
    description: str,
    working_dir: Path | None = None,
) -> str:
    """SEC-10: build the .lnk-creation PowerShell script as a single string."""
    if working_dir is None:
        working_dir = Path.home()
    lines = [
        "$s = New-Object -ComObject WScript.Shell",
        f"$l = $s.CreateShortcut({_ps_single_quote(lnk_path)})",
        f"$l.TargetPath = {_ps_single_quote(target)}",
        # arguments already has surrounding double quotes from the caller
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
    """SEC-10: the PowerShell fallback now wraps every user-supplied value"""
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
        log.debug("[STARTUP] win32com unavailable, trying PowerShell fallback")
    except OSError as e:
        # include both the destination .lnk path AND the target
        log.warning(
            "[STARTUP] Failed to create .lnk (lnk=%s, target=%s): %s",
            lnk_path,
            target,
            e,
        )
        return False

    # 2) PowerShell fallback. Pass the script directly via `-Command`
    try:
        script = _build_powershell_lnk_script(
            lnk_path=lnk_path,
            target=target,
            arguments=arguments,
            icon_ico=icon_ico,
            description=description,
        )

        # pass the script via ``-Command`` instead of
        run_kwargs: dict = {
            "check": True,
            "capture_output": True,
            "timeout": 30,
        }
        if _platform_flags.SYSTEM == "win32":
            from voice_typer.server.server_platform.autostart import (
                _windows_create_no_window_flags,
            )

            run_kwargs["creationflags"] = _windows_create_no_window_flags()
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            **run_kwargs,
        )
        log.info("[STARTUP] .lnk created via PowerShell fallback: %s", lnk_path)
        return True
    except Exception as e:
        # include the lnk path + target so operators can tell
        log.warning(
            "[STARTUP] PowerShell .lnk creation failed (lnk=%s, target=%s): %s",
            lnk_path,
            target,
            e,
        )
        return False


# AppUserModelID stamped onto the launcher shortcuts so Windows toast
_APP_USER_MODEL_ID = APP_NAME.replace(" ", "")

# System.AppUserModel.ID, the property key Windows reads off the
_APP_USER_MODEL_ID_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
_APP_USER_MODEL_ID_PID = 5


def _build_aumid_powershell_script(lnk_path: Path, aumid: str) -> str:
    """single-quoted via :func:`_ps_single_quote` (SEC-10), and the C#"""
    csharp = (
        "Add-Type -TypeDefinition @'"
        + "\nusing System;\n"
        + "using System.Runtime.InteropServices;\n"
        + "using System.Text;\n"
        + "public static class LnkAumid {\n"
        + '    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]\n'
        + "    private class CShellLink { }\n"
        + '    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), '
        + "InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
        + "    private interface IShellLinkW {\n"
        + "        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder "
        + "pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);\n"
        + "        void GetIDList(out IntPtr ppidl);\n"
        + "        void SetIDList(IntPtr pidl);\n"
        + "        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] "
        + "StringBuilder pszName, int cchMaxName);\n"
        + "        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);\n"
        + "        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] "
        + "StringBuilder pszDir, int cchMaxPath);\n"
        + "        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);\n"
        + "        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder "
        + "pszArgs, int cchMaxPath);\n"
        + "        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);\n"
        + "        void GetHotkey(out short pwHotkey);\n"
        + "        void SetHotkey(short wHotkey);\n"
        + "        void GetShowCmd(out int piShowCmd);\n"
        + "        void SetShowCmd(int iShowCmd);\n"
        + "        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] "
        + "StringBuilder pszIconPath, int cchIconPath, out int piIcon);\n"
        + "        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string "
        + "pszIconPath, int iIcon);\n"
        + "        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string "
        + "pszPathRel, int dwReserved);\n"
        + "        void Resolve(IntPtr hwnd, uint fFlags);\n"
        + "        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);\n"
        + "    }\n"
        + '    [ComImport, Guid("0000010B-0000-0000-C000-000000000046"), '
        + "InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
        + "    private interface IPersistFile {\n"
        + "        void GetClassID(out Guid pClassID);\n"
        + "        int IsDirty();\n"
        + "        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, "
        + "int dwMode);\n"
        + "        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, "
        + "[MarshalAs(UnmanagedType.Bool)] bool fRemember);\n"
        + "        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);\n"
        + "        void GetCurFile(out IntPtr ppszFileName);\n"
        + "    }\n"
        + '    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), '
        + "InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
        + "    private interface IPropertyStore {\n"
        + "        [PreserveSig] int GetCount(out uint cProps);\n"
        + "        [PreserveSig] int GetAt(uint iProp, out PROPERTYKEY pkey);\n"
        + "        [PreserveSig] int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);\n"
        + "        [PreserveSig] int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);\n"
        + "        [PreserveSig] int Commit();\n"
        + "    }\n"
        + "    [StructLayout(LayoutKind.Sequential)]\n"
        + "    private struct PROPERTYKEY { public Guid fmtid; public int pid; }\n"
        + "    [StructLayout(LayoutKind.Sequential)]\n"
        + "    private struct PROPVARIANT {\n"
        + "        public ushort vt;\n"
        + "        public ushort wReserved1, wReserved2, wReserved3;\n"
        + "        public IntPtr p;\n"
        + "    }\n"
        + "    public static int Set(string path, string target, string arguments, "
        + "string workingDir, string description, string iconPath, string aumid) {\n"
        + "        object link = new CShellLink();\n"
        + "        IShellLinkW sl = (IShellLinkW)link;\n"
        + "        sl.SetPath(target);\n"
        + "        if (!string.IsNullOrEmpty(arguments)) sl.SetArguments(arguments);\n"
        + "        if (!string.IsNullOrEmpty(workingDir)) sl.SetWorkingDirectory(workingDir);\n"
        + "        if (!string.IsNullOrEmpty(description)) sl.SetDescription(description);\n"
        + "        if (!string.IsNullOrEmpty(iconPath)) sl.SetIconLocation(iconPath, 0);\n"
        + "        IPropertyStore ps = (IPropertyStore)link;\n"
        + "        PROPERTYKEY key;\n"
        + '        key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");\n'
        + "        key.pid = 5;\n"
        + "        PROPVARIANT pv;\n"
        + "        pv.vt = 31;\n"
        + "        pv.wReserved1 = pv.wReserved2 = pv.wReserved3 = 0;\n"
        + "        pv.p = Marshal.StringToCoTaskMemUni(aumid);\n"
        + "        int hr = ps.SetValue(ref key, ref pv);\n"
        + "        if (hr == 0) hr = ps.Commit();\n"
        + "        if (hr == 0) {\n"
        + "            IPersistFile pf = (IPersistFile)link;\n"
        + "            pf.Save(path, true);\n"
        + "        }\n"
        + "        Marshal.FreeCoTaskMem(pv.p);\n"
        + "        Marshal.ReleaseComObject(ps);\n"
        + "        return hr;\n"
        + "    }\n"
        + "}\n"
        + "'@\n"
    )
    call = (
        "$ErrorActionPreference = 'Stop'\n"
        + "try {\n"
        + "$s = New-Object -ComObject WScript.Shell\n"
        + "$existing = $s.CreateShortcut("
        + _ps_single_quote(lnk_path)
        + ")\n"
        + "$iconPath = $existing.IconLocation\n"
        + "if ($iconPath -and $iconPath.Contains(',')) { $iconPath = $iconPath.Substring(0, $iconPath.IndexOf(',')) }\n"
        + "$hr = [LnkAumid]::Set("
        + _ps_single_quote(lnk_path)
        + ", "
        + "$existing.TargetPath, "
        + "$existing.Arguments, "
        + "$existing.WorkingDirectory, "
        + "$existing.Description, "
        + "$iconPath, "
        + _ps_single_quote(aumid)
        + ")\n"
        + "if ($null -ne $hr -and $hr -ne 0) { exit $hr }\n"
        + "} catch {\n"
        + "exit 1\n"
        + "}"
    )
    return csharp + call


def _set_lnk_app_user_model_id(lnk_path: Path) -> bool:
    """Best-effort stamp of ``System.AppUserModel.ID`` onto a .lnk."""
    if _platform_flags.SYSTEM != "win32" or not lnk_path.exists():
        return False
    try:
        raw = lnk_path.read_bytes()
        # PKEY_AppUserModel_ID fmtid 9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3
        if bytes.fromhex("55284c9f799f394ba8d0e1d42de1d5f3") in raw and _APP_USER_MODEL_ID.encode("utf-16-le") in raw:
            return True
    except OSError:
        # unreadable .lnk, fall through to the PowerShell stamp which
        pass
    script = _build_aumid_powershell_script(lnk_path, _APP_USER_MODEL_ID)
    try:
        # Hidden spawn on Windows: powershell.exe is a console-subsystem
        aumid_kwargs: dict = {
            "check": True,
            "capture_output": True,
            "timeout": 60,
        }
        if _platform_flags.SYSTEM == "win32":
            from voice_typer.server.server_platform.autostart import (
                _windows_create_no_window_flags,
            )

            aumid_kwargs["creationflags"] = _windows_create_no_window_flags()
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            **aumid_kwargs,
        )
        log.info(
            "[STARTUP] AppUserModelID %s stamped on %s",
            _APP_USER_MODEL_ID,
            lnk_path,
        )
        return True
    except Exception as e:
        # include the lnk path so operators can tell which shortcut
        log.warning(
            "[STARTUP] AppUserModelID stamp failed (lnk=%s): %s",
            lnk_path,
            e,
        )
        return False


def create_launcher_shortcut() -> Path | None:
    """Create Desktop + Start Menu shortcuts for the app.

    Returns the path to the Desktop shortcut (the primary one), or None on
    """
    if _platform_flags.SYSTEM != "win32":
        log.info("[STARTUP] Launcher shortcut only supported on Windows")
        return None

    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw.exists():
        log.warning("[STARTUP] pythonw.exe not found at %s, cannot create console-free launcher", pythonw)
        return None

    launcher = _universal_launcher_path()
    desktop = Path.home() / "Desktop"
    start_menu = _start_menu_programs_dir()
    icon_ico = _generate_icon_ico()

    # Primary: Desktop .lnk pointing at the universal launcher (no --hidden).
    primary_path: Path | None = None
    lnk_desktop = desktop / f"{APP_NAME}.lnk"

    # Skip if the Desktop shortcut already exists, no need to recreate
    existing_desktop = _existing_launcher_lnk(desktop)
    if existing_desktop is not None:
        primary_path = existing_desktop
        # Windows toast notifications attribute their icon via the Start
        _set_lnk_app_user_model_id(existing_desktop)
    else:
        if _create_lnk_shortcut(
            lnk_desktop,
            target=str(pythonw),
            arguments=f'"{launcher}"',
            icon_ico=icon_ico,
            description=f"{APP_NAME}, voice-to-text dictation",
        ):
            log.info("[STARTUP] Desktop .lnk created: %s", lnk_desktop)
            primary_path = lnk_desktop
            _set_lnk_app_user_model_id(lnk_desktop)
        else:
            # include the operation inputs (target + destination)
            log.warning(
                "[STARTUP] Could not create desktop .lnk (target=%s, lnk=%s). Install pywin32 or check logs",
                pythonw,
                lnk_desktop,
            )

    # Secondary: Start Menu copy so Start search finds the app.
    try:
        start_menu.mkdir(parents=True, exist_ok=True)
        lnk_start = start_menu / f"{APP_NAME}.lnk"
        existing_start = _existing_launcher_lnk(start_menu)
        if existing_start is not None:
            _set_lnk_app_user_model_id(existing_start)
        elif _create_lnk_shortcut(
            lnk_start,
            target=str(pythonw),
            arguments=f'"{launcher}"',
            icon_ico=icon_ico,
            description=f"{APP_NAME}, voice-to-text dictation",
        ):
            log.info("[STARTUP] Start Menu .lnk created: %s", lnk_start)
            _set_lnk_app_user_model_id(lnk_start)
    except OSError as e:
        log.debug("[STARTUP] Start Menu shortcut skipped: %s", e)

    return primary_path
