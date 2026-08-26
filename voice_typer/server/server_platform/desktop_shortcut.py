"""Windows desktop shortcut (.lnk) creation.

Phase 4.5 /  — extracted from the original
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
(in :mod:`tests.test_security_fixes`).  This patches the
``run`` attribute on the stdlib ``subprocess`` module object (which is
the same object this module imports via ``import subprocess`` at the
top), so ``subprocess.run(...)`` calls below pick up the patch without
any ``_pkg`` indirection.

Tests patch ``SYSTEM`` via
``monkeypatch.setattr(platform_flags, "SYSTEM", "win32"|"linux")`` (in
:mod:`tests.test_platform`).  ``create_launcher_shortcut`` reads
``_platform_flags.SYSTEM`` at call time so the patch takes effect.

``inspect.getsource`` compatibility
-----------------------------------
All seven functions are genuinely defined here, so
``inspect.getsource(_build_powershell_lnk_script)`` etc. continue to
read from this file.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# Patch-path bridge: read ``SYSTEM`` through the owning
# :mod:`.platform_flags` module attribute at call time so test patches of
# the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.platform_flags.SYSTEM", "linux")``
# keep affecting production code defined here (specifically
# ``create_launcher_shortcut``'s ``SYSTEM != "win32"`` early return).
from voice_typer.server._paths import APP_SLUG
from voice_typer.server.branding import APP_NAME
from voice_typer.server.server_platform import platform_flags as _platform_flags

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

    # The pre-rendered logo PNG lives at the project root
    # (``logo-256.png``), NOT under ``voice_typer/server/assets/`` (that
    # directory was REMOVED from the MANIFEST.in / packaging entries, so
    # it is not shipped in installed or bundled builds — confirmed via
    # the MANIFEST.in cleanup that removed the dead ``recursive-include
    # voice_typer/server/assets *`` entry; the dir may still exist in a
    # dev checkout but is not part of the package). Probe the
    # project-root location first (dev / editable installs), then the
    # packaged ``assets/`` location (forward-compat if the asset is later
    # co-located with the server package). Without this fix the icon
    # was ALWAYS skipped because the old single-candidate path never
    # existed.
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
        log.debug("Pre-rendered logo PNG not found — cannot generate icon")
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
        # which file the icon save attempted to write when it failed
        # (APPDATA path, custom install path, etc.) without having to
        # cross-reference the earlier "Shortcut icon saved" info line.
        log.warning("[STARTUP] Failed to save icon (ico_path=%s): %s", ico_path, e)
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
        # include both the destination .lnk path AND the target
        # executable so operators can tell which shortcut + which
        # underlying executable the win32com COM call failed on without
        # having to grep adjacent log lines for the lnk path / target.
        log.warning(
            "[STARTUP] Failed to create .lnk (lnk=%s, target=%s): %s",
            lnk_path,
            target,
            e,
        )
        return False

    # 2) PowerShell fallback — pass the script directly via `-Command`
    # to avoid the temp-file TOCTOU window that the previous
    # ``-File <tmp>`` invocation opened (). The script is
    # already string-built by ``_build_powershell_lnk_script`` (which
    # single-quotes every user-supplied value via ``_ps_single_quote``,
    # so PowerShell metacharacters cannot break out of the string
    # literals), and ``-EncodedCommand`` is unnecessary here because
    # we control the script content end-to-end (no Windows codepage
    # quoting concerns for our pure-ASCII generated script).
    #
    # SEC-10: every user-supplied value is wrapped in a single-quoted
    # PowerShell string (see _build_powershell_lnk_script /
    # _ps_single_quote). This is defense-in-depth against path /
    # description / arguments values that contain PowerShell
    # metacharacters — even though Voice Typer controls most of these
    # values today, a future change (e.g. user-customizable shortcut
    # description) shouldn't silently introduce an injection vector.
    try:
        script = _build_powershell_lnk_script(
            lnk_path=lnk_path,
            target=target,
            arguments=arguments,
            icon_ico=icon_ico,
            description=description,
        )

        # pass the script via ``-Command`` instead of
        # writing it to a temp .ps1 file and invoking ``-File <tmp>``.
        # The temp-file path opened a TOCTOU window between the write
        # and the powershell read: a local attacker with write access
        # to ``%TEMP%`` could swap the .ps1 file between the
        # ``NamedTemporaryFile`` write (``delete=False``) and the
        # ``powershell -File`` invocation, substituting arbitrary
        # PowerShell code that would then execute with the user's
        # privileges. The ``-Command`` form passes the script as a
        # single process argument — no on-disk artifact exists for an
        # attacker to swap.
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            capture_output=True,
            timeout=30,
        )
        log.info("[STARTUP] .lnk created via PowerShell fallback: %s", lnk_path)
        return True
    except Exception as e:
        # include the lnk path + target so operators can tell
        # which shortcut + which underlying executable the PowerShell
        # fallback failed on without having to grep adjacent log lines.
        log.warning(
            "[STARTUP] PowerShell .lnk creation failed (lnk=%s, target=%s): %s",
            lnk_path,
            target,
            e,
        )
        return False


# AppUserModelID stamped onto the launcher shortcuts so Windows toast
# notifications resolve to the Voice Typer icon instead of the Electron
# default (electron.exe). Must match the Electron side's
# ``app.setAppUserModelId("VoiceTyper")`` (bootstrap.ts) and the Python
# side's ``SetCurrentProcessExplicitAppUserModelID`` (platform_utils.py)
# — all three compute the same value: APP_NAME with spaces removed.
_APP_USER_MODEL_ID = APP_NAME.replace(" ", "")

# System.AppUserModel.ID — the property key Windows reads off the
# Start Menu shortcut to attribute toast notifications to an app and
# pick its icon (electronjs.org/docs/latest/tutorial/notifications).
_APP_USER_MODEL_ID_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
_APP_USER_MODEL_ID_PID = 5


def _build_aumid_powershell_script(lnk_path: Path, aumid: str) -> str:
    """Build a PowerShell script that stamps ``System.AppUserModel.ID``
    onto an existing .lnk.

    The WScript.Shell COM interface used for .lnk creation cannot set
    arbitrary property-store values, so we compile a tiny C# helper
    (Add-Type) that drives the **shell-link object's own** property
    store — the only store whose ``Commit`` actually persists
    ``System.AppUserModel.ID`` into the .lnk file:

    1. read the existing shortcut's fields (target, arguments, working
       dir, description, icon) via ``WScript.Shell`` COM;
    2. ``CoCreateInstance(CLSID_ShellLink)`` (a fresh ``CShellLink``
       COM class), set ALL of those fields back via ``IShellLinkW``,
       cast the SAME object to ``IPropertyStore`` and ``SetValue`` +
       ``Commit`` the AppUserModel.ID property key;
    3. ``IPersistFile.Save`` — without this the property is never
       written to disk.

    This is the exact pattern Squirrel.Windows uses in ``ShellFile.cs``
    (``SetAppUserModelId`` — ``(IPropertyStore)linkW`` +
    ``IPersistFile.Save``), which is what produces shortcuts whose
    ``System.AppUserModel.ID`` shows up in ``lnk-parser`` and makes
    Windows toast notifications use the app icon. Two empirically
    verified pitfalls (2026-08-15) shaped this implementation:

    - ``SHGetPropertyStoreFromParsingName`` + ``Commit`` reports
      success and reads back in-process, but NEVER writes the property
      to the file (mtime changes, bytes don't).
    - ``IPersistFile.Load`` on the existing link + ``SetValue`` on the
      LOADED object fails with ``E_INVALIDARG``/``STG_E_READFAULT``.
      The working variant is a FRESH coclass with every field re-set
      (``SetPath``/``SetArguments``/``SetWorkingDirectory``/
      ``SetDescription``/``SetIconLocation``) — verified: all HRESULTs
      0 and the property GUID + UTF-16 value physically present in the
      output .lnk at the expected offsets.

    All user-supplied values (the .lnk path and the AUMID) are
    single-quoted via :func:`_ps_single_quote` (SEC-10), and the C#
    body is a here-string (``@'...'@``) so no interpolation can occur.

    The script exits with the HRESULT from the property write so the
    Python caller can log a precise failure reason.
    """
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
    """Best-effort stamp of ``System.AppUserModel.ID`` onto a .lnk.

    Runs the PowerShell snippet built by
    :func:`_build_aumid_powershell_script` with the module's canonical
    AUMID (:data:`_APP_USER_MODEL_ID`). Never raises — a failure is
    logged and returns False so shortcut creation is never broken by a
    property-stamp problem.

    Idempotency fast-path: the property is written into the .lnk as a
    ``1SPS`` serialized property-store block containing the AUMID
    property key's GUID bytes + the UTF-16 AUMID value. If both are
    already present in the raw file, the stamp is skipped entirely —
    this keeps the per-startup cost near zero after the first stamp
    instead of recompiling the Add-Type C# helper (seconds) on every
    boot. (The byte pattern is how Squirrel-generated shortcuts verify
    with ``lnk-parser``; the property-store read-back APIs are
    unreliable for this — a fresh-process ``GetValue`` returns empty
    even when the bytes are in the file.)
    """
    if _platform_flags.SYSTEM != "win32" or not lnk_path.exists():
        return False
    try:
        raw = lnk_path.read_bytes()
        # PKEY_AppUserModel_ID fmtid 9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3
        # as little-endian bytes (GUID mixed-endian field order).
        if bytes.fromhex("55284c9f799f394ba8d0e1d42de1d5f3") in raw and _APP_USER_MODEL_ID.encode("utf-16-le") in raw:
            return True
    except OSError:
        # unreadable .lnk — fall through to the PowerShell stamp which
        # will log its own failure.
        pass
    script = _build_aumid_powershell_script(lnk_path, _APP_USER_MODEL_ID)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            capture_output=True,
            timeout=60,
        )
        log.info(
            "[STARTUP] AppUserModelID %s stamped on %s",
            _APP_USER_MODEL_ID,
            lnk_path,
        )
        return True
    except Exception as e:
        # include the lnk path so operators can tell which shortcut
        # failed the AUMID stamp without grepping adjacent log lines.
        log.warning(
            "[STARTUP] AppUserModelID stamp failed (lnk=%s): %s",
            lnk_path,
            e,
        )
        return False


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
    if _platform_flags.SYSTEM != "win32":
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
        # Windows toast notifications attribute their icon via the Start
        # Menu shortcut's System.AppUserModel.ID. Existing shortcuts (from
        # before the AUMID stamp was added) lack the property, so toasts
        # fall back to the Electron default icon — stamp idempotently.
        _set_lnk_app_user_model_id(lnk_desktop)
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
            _set_lnk_app_user_model_id(lnk_desktop)
        else:
            # include the operation inputs (target + destination)
            # so operators can tell which path / launcher failed without
            # having to dig through the rest of the startup log.
            log.warning(
                "[STARTUP] Could not create desktop .lnk (target=%s, lnk=%s) — install pywin32 or check logs",
                pythonw,
                lnk_desktop,
            )

    # Secondary: Start Menu copy so Start search finds "Voice Typer".
    try:
        start_menu.mkdir(parents=True, exist_ok=True)
        lnk_start = start_menu / "Voice Typer.lnk"
        if lnk_start.exists():
            _set_lnk_app_user_model_id(lnk_start)
        elif _create_lnk_shortcut(
            lnk_start,
            target=str(pythonw),
            arguments=f'"{launcher}"',
            icon_ico=icon_ico,
            description=f"{APP_NAME} — voice-to-text dictation",
        ):
            log.info("[STARTUP] Start Menu .lnk created: %s", lnk_start)
            _set_lnk_app_user_model_id(lnk_start)
    except OSError as e:
        log.debug("[STARTUP] Start Menu shortcut skipped: %s", e)

    return primary_path
