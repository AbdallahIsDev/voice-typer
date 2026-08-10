"""Windows Scheduled Task registration for the prewarm step.

The prewarm script (``voice_typer.server.prewarm``) only helps if it runs
*before* the user launches Voice Typer after a reboot.  This module
registers a Windows Scheduled Task that fires it:

- **At logon**, after a 45 s delay (lets login settle, avoids contention
  with all the other startup programs fighting for disk).

The task runs with **below-normal priority**, hidden, no network, and
only when on AC power (laptops).  It calls::

    <venv-python> -m voice_typer.server.prewarm

so the prewarm runs in exactly the Python environment that Voice Typer
uses at runtime — warming the same files that will be imported later.

**Admin-free fallback.**  If ``schtasks /Create`` fails (most often because
a previously-registered task got *locked* such that a standard user can't
overwrite or delete it), we fall back to the per-user ``HKCU\\...\\Run``
registry key — the same mechanism the app's autostart uses.  It runs
``pythonw.exe -m voice_typer.server.prewarm --delay 45`` at every logon:
no console window, no admin rights, and the in-process delay replaces the
Task Scheduler logon-trigger delay.  The registry path keeps logon
warmup (the part that matters) working.

Non-Windows platforms get no-op stubs: registration returns False and
``is_prewarm_registered`` returns False, so the Settings toggle simply has
no effect there.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from voice_typer.server import _paths
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger(__name__)

# Namespace (RDNN): the prewarm Task Scheduler task / HKCU Run-key value
# uses the canonical ``com.voicetyper.*`` reverse-DNS namespace, mirroring
# the macOS LaunchAgent label ``com.voicetyper.prewarm`` in
# prewarm_scheduler_posix.py. The pre-2026 bare name is retained as
# ``_LEGACY_TASK_NAME`` so upgraded installs converge on the new name at
# register time and are fully cleaned at unregister/uninstall time.
TASK_NAME = "com.voicetyper.prewarm"

# Pre-namespace-rename task name ("VoiceTyperPrewarm"). Installs that
# predate the com.voicetyper.* rename carry this task + Run-key value;
# register/unregister always touch BOTH so upgrades converge and no
# orphan fires after uninstall.
_LEGACY_TASK_NAME = "VoiceTyperPrewarm"

# Per-user Run registry key — the same mechanism the app's autostart uses.
# HKCU is user-writable, so this needs NO admin privileges.  We fall back to
# it when the Task Scheduler task can't be created (e.g. a previous task got
# locked such that a standard user cannot overwrite it with schtasks).
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# STARTUP-2: previously the Run-key fallback delayed 45 s and the Task
# Scheduler logon trigger also delayed 45 s. The app's HKCU Run key has
# NO delay, so the app always started ~45 s before prewarm, defeating
# the cache-warming benefit (app cold-imports torch/transformers while
# prewarm is still waiting to fire).
#
# Fix: prewarm fires at logon+0 s (low I/O priority prevents user
# disturbance); the app's autostart_launcher gets a --delay 30 flag so
# it waits 30 s after launch before spawning Electron. Prewarm has a
# 30 s head start on its import stage before the app starts contending.
_RUN_KEY_DELAY_SECONDS = 0

# Delay after logon before prewarming. STARTUP-2: was PT45S, now PT0S
# (low I/O priority handles contention; the previous delay guaranteed
# prewarm would lose the race against the app's cold imports).
_LOGON_DELAY = "PT0S"  # ISO 8601 duration: 0 seconds (fire at logon)

# STARTUP-2: delay the app's autostart_launcher waits before spawning
# Electron, giving prewarm a head start on warming the OS file cache.
# Coded as a CLI flag so platform.py can pass it without depending on
# this module's internals.
#
# ADR-0009 Issue 4: reduced from 30s to 15s. Combined with the prewarm
# PID-file handshake in model_manager.try_load() (wait_for_prewarm()),
# this gives prewarm a head start without wasting 15s when prewarm
# finishes early. If the user logs in faster than prewarm can finish,
# the app's model loader waits for prewarm to complete (up to 60s)
# rather than fighting it for disk I/O.
_APP_AUTOSTART_DELAY_SECONDS = 15


# ─── Python interpreter resolution ──────────────────────────────────────


def _prewarm_command() -> str | None:
    """Return the pythonw.exe path for the prewarm action, or None.

    PREWARM-FIX: this MUST be pythonw.exe (the windowless interpreter), not
    the console python.exe. The task runs in the interactive user session
    (InteractiveToken + LogonTrigger), and pythonw.exe starts with no
    console window. A console python.exe would either flash a window or —
    in a non-interactive session — fail to allocate a console and exit
    without running. The previous breakage used BootTrigger/EventTrigger
    (system-start, pre-logon); pythonw + InteractiveToken cannot launch
    there, and neither can a headless python.exe without an interactive
    window station. A LogonTrigger (fires at user logon) makes the
    interactive session available, so pythonw works reliably.

    STARTUP-1: previously returned the *full* command line
    ``"pythonw.exe" -m voice_typer.server.prewarm`` and the XML builder
    wrapped it in ``cmd.exe /c``, which spawned a visible console window
    that stayed alive ~10 min while prewarm ran.

    Now returns just the interpreter path; the module name is appended
    by ``_build_task_xml`` as a separate ``<Arguments>`` element so the
    Task Scheduler action runs ``pythonw.exe`` directly with no cmd host
    (and thus no console window — pythonw.exe has no console by design).

    Mirrors the venv-resolution logic that the Electron main process
    uses to spawn the IPC server: prefer the app venv at
    ``~/.voice-typer/venv/`` (the interpreter Electron spawns), and
    fall back to ``sys.executable``.

    previously this comment referenced
    ``asr_setup.get_voice_typer_python()``, a function that no longer
    exists (it was removed in an earlier refactor).  The comment is
    now updated to describe the actual logic without referencing dead
    code.

    ADR-0020 §5: under the Tauri sidecar path (TAURI_SIDECAR=1 env or
    VOICE_TYPER_PREWARM_EXE env set), the prewarm helper is a frozen
    Nuitka exe — there is no Python interpreter to invoke. Delegate to
    :func:`voice_typer.server.prewarm_resolver.resolve_prewarm_exe`
    which returns the frozen exe path (preferred) or the dev-fallback
    Python command line. When the resolver returns a frozen exe path,
    the caller must NOT append ``_PREWARM_ARGS`` (the frozen exe takes
    no module args — it IS the module). See ``register_prewarm_task``.
    """
    # ADR-0020 §5: Tauri sidecar path — delegate to the shared resolver.
    # The resolver returns either a single path (frozen exe) or a full
    # command line (dev fallback). When it returns a frozen exe path,
    # callers must NOT append _PREWARM_ARGS.
    if os.environ.get("TAURI_SIDECAR") == "1" or os.environ.get("VOICE_TYPER_PREWARM_EXE"):
        from voice_typer.server.prewarm_resolver import resolve_prewarm_exe

        resolved = resolve_prewarm_exe()
        if resolved is not None:
            # Detect whether the resolver returned a single path (frozen
            # exe) or a multi-token command line (dev fallback). A path
            # has no spaces inside the first token (Windows paths may
            # have spaces but they're quoted); a command line has
            # multiple tokens separated by unquoted spaces.
            # Heuristic: if the string contains `` -m `` it's the dev
            # fallback; otherwise it's a frozen exe path.
            if " -m " in resolved:
                # Dev fallback — return the python interpreter path only
                # (extract from the quoted command line) and let the
                # XML builder append _PREWARM_ARGS as before.
                # The dev fallback format is: "<path>" -m voice_typer.server.prewarm
                try:
                    # Strip quotes, take the first token.
                    first = resolved.split(" ", 1)[0].strip('"')
                    if Path(first).is_file():
                        return first
                except (IndexError, ValueError, OSError):
                    # ``IndexError`` if split returned empty list
                    # (shouldn't happen, defensive); ``ValueError`` if
                    # ``Path()`` rejects the string; ``OSError`` if
                    # ``is_file()`` fails on a broken symlink.
                    # Previously a broad ``except Exception: pass``.
                    pass
                # If parsing failed, fall through to the legacy path.
            else:
                # Frozen exe path — return as-is. The caller (via
                # _build_task_xml) will see no _PREWARM_ARGS suffix is
                # needed because register_prewarm_task checks this case.
                return resolved
        # Resolver returned None — fall through to legacy pythonw path.

    # Try pythonw.exe first (no console window).
    # use _paths.venv_pythonw() so the venv path respects the
    # platform-aware _config_dir() logic instead of the previous
    # hardcoded Path.home() / ".voice-typer".
    venv_pythonw = _paths.venv_pythonw()
    if venv_pythonw.exists():
        return str(venv_pythonw.resolve())
    # Fall back to pythonw.exe next to the current interpreter.
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw.resolve())
    # Final fallback: sys.executable itself.  On Windows this is python.exe
    # (which has a console), but we still set Hidden=true and the task runs
    # in a non-interactive session so the console is normally not visible.
    # NOTE: this fallback only triggers when pythonw.exe is unavailable,
    # which is unusual on a normal Python install.
    python = sys.executable
    if not Path(python).exists():
        log.warning("[TASK] Python interpreter not found: %s", python)
        return None
    log.debug("[TASK] pythonw.exe not found — falling back to %s", python)
    return python


# Arguments passed to the pythonw interpreter for the prewarm action.
# Kept as a module constant so tests can verify the XML uses it.
# includes --trigger logon so the log records that the task fired
# via the LogonTrigger (the user logged on).
_PREWARM_ARGS = "-m voice_typer.server.prewarm --trigger logon"


def _prewarm_pythonw() -> str | None:
    """Resolve the pythonw.exe to run prewarm hidden (no console window).

    The Run registry key launches the program at logon in the user's
    session.  Using ``pythonw.exe`` (rather than ``python.exe`` wrapped in
    ``cmd.exe /c timeout ... start``) is the documented way to run a Python
    script with no console, so nothing flashes on screen at login.  The
    delay is handled in-process by prewarm's own ``--delay`` flag, avoiding
    a ``timeout.exe``/``cmd.exe`` dependency that would itself open a console.
    """
    # use _paths.venv_pythonw() for the same reasons as
    # _prewarm_command() — see the docstring there for details.
    venv_pythonw = _paths.venv_pythonw()
    if venv_pythonw.exists():
        return str(venv_pythonw.resolve())
    # Fall back to pythonw.exe next to the current interpreter.
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw.resolve())
    log.warning("[TASK] pythonw.exe not found — prewarm would show a console")
    return None


def _registry_command() -> str | None:
    """Build the command line stored in the HKCU Run key.

    Runs ``pythonw.exe -m voice_typer.server.prewarm --delay <n>``.  The
    ``--delay`` lets login settle (replacing the Task Scheduler logon
    trigger's delay); it runs in-process so no console window is needed.
    """
    pythonw = _prewarm_pythonw()
    if pythonw is None:
        return None
    return f'"{pythonw}" -m voice_typer.server.prewarm --delay {_RUN_KEY_DELAY_SECONDS} --trigger logon'


# ─── HKCU Run-key fallback (no admin needed) ──────────────────────────────


def _register_prewarm_registry(command: str) -> bool:
    """Register prewarm via the HKCU Run registry key (no admin needed).

    The Run key makes the program launch at every user logon.  We only use
    it as a fallback when the Task Scheduler task can't be registered
    (e.g. a locked task left behind by an earlier install).  Returns True
    on success, False otherwise.
    """
    if not is_windows():
        return False
    try:
        import winreg
    except ImportError:
        return False  # not Windows (e.g. test host with patched sys.platform)
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, command)
            # Converge upgraded installs: drop the pre-rename Run-key
            # value (``VoiceTyperPrewarm``) so it can't fire a second
            # prewarm at the next logon. Non-fatal if absent.
            for legacy in (_LEGACY_TASK_NAME,):
                with contextlib.suppress(FileNotFoundError, OSError):
                    winreg.DeleteValue(key, legacy)
        finally:
            winreg.CloseKey(key)
        log.info("[TASK] Prewarm registered via HKCU Run key (no admin)")
        return True
    except OSError as exc:
        log.warning("[TASK] Could not register prewarm via registry: %s", exc)
        return False


def _unregister_prewarm_registry() -> bool:
    """Remove prewarm from the HKCU Run registry key.

    Returns True on success (including when the value was already absent).
    """
    if not is_windows():
        return False
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            # Remove the current task name AND the pre-rename legacy name
            # (upgrades converge: after this call only the new name exists).
            for task_name in (TASK_NAME, _LEGACY_TASK_NAME):
                winreg.DeleteValue(key, task_name)
        finally:
            winreg.CloseKey(key)
        log.info("[TASK] Prewarm removed from HKCU Run key")
        return True
    except FileNotFoundError:
        return True  # already absent
    except OSError as exc:
        log.warning("[TASK] Could not remove prewarm from registry: %s", exc)
        return False


def _is_prewarm_registered_registry() -> bool:
    """Return True if the HKCU Run key has the prewarm value."""
    if not is_windows():
        return False
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, TASK_NAME)
            return bool(val)
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError as exc:
        # parity with the sibling ``_register_prewarm_registry``
        # and ``_unregister_prewarm_registry`` helpers, which both log at
        # WARNING on registry access failures. The previous silent
        # ``return False`` here made a registry permissions issue (or a
        # transient kernel resource exhaustion) indistinguishable from
        # "value not present" — the caller (``is_prewarm_registered``)
        # then fell through to a schtasks /Query, masking the root cause.
        # ``debug`` (rather than ``warning``) keeps the noise low on the
        # common case where the Run key is simply absent on a fresh
        # install (``FileNotFoundError`` above is the expected path and
        # returns False without logging). ``OSError`` here means the key
        # exists but couldn't be opened / queried — worth a diagnostic
        # breadcrumb.
        log.debug("[TASK] _is_prewarm_registered_registry: OSError reading HKCU Run key: %s", exc)
        return False


# ─── Task XML definition ──────────────────────────────────────────────────


def _build_task_xml(python_exe: str, arguments: str | None = None) -> str:
    """Build the Task Scheduler XML definition.

    Hand-rolled rather than via ``win32com.client`` (which pywin32 may not
    ship in the runtime venv).  ``schtasks /Create /XML`` accepts this
    directly.

    STARTUP-1: previously this wrapped the prewarm command in
    ``cmd.exe /c``, which spawned a visible console window that stayed
    alive ~10 min while prewarm ran. Now ``<Command>`` is the pythonw.exe
    path directly (no cmd host) and ``<Arguments>`` carries the
    ``-m voice_typer.server.prewarm`` flag. pythonw.exe has no console
    by design, so no window ever appears.

    Parameters
    ----------
    python_exe : str
        Absolute path to the Python interpreter (pythonw.exe preferred)
        OR the frozen prewarm exe path (under the Tauri sidecar path).
    arguments : str, optional
        Command-line arguments for the interpreter. Defaults to
        ``-m voice_typer.server.prewarm`` (the legacy pythonw path).

        ADR-0020 §5 fix: when the caller passes an EMPTY STRING (``""``),
        NO ``<Arguments>`` element is emitted at all. This is the correct
        behavior for a frozen Nuitka exe (which IS the module and takes
        no ``-m`` flag). Previously, passing ``None`` would silently fall
        back to ``_PREWARM_ARGS``, which broke the frozen-exe path by
        appending ``-m voice_typer.server.prewarm --trigger logon`` to a
        binary that can't accept it. The distinction:

        - ``arguments=None``  → legacy default (``_PREWARM_ARGS``)
        - ``arguments=""``    → NO ``<Arguments>`` element (frozen exe)
        - ``arguments="..."`` → use the provided string verbatim
    """
    if arguments is None:
        arguments = _PREWARM_ARGS
    root = ET.Element(
        "Task",
        {
            "version": "1.4",
            "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task",
        },
    )

    # ── RegistrationInfo ──────────────────────────────────────────────
    reg = ET.SubElement(root, "RegistrationInfo")
    desc = ET.SubElement(reg, "Description")
    desc.text = (
        "Warms the OS file cache for Voice Typer (torch + model weights) "
        "so the app starts fast after a reboot. Safe to disable or delete."
    )
    uri = ET.SubElement(reg, "URI")
    uri.text = f"{TASK_NAME}"

    # ── Triggers ──────────────────────────────────────────────────────
    #
    # PREWARM-FIX: a single LogonTrigger (fires when the user logs on).
    #
    # The earlier design used BootTrigger + EventTrigger (Event ID 12,
    # "OS started") so prewarm would run at system boot, BEFORE the user
    # logged on. That is fundamentally incompatible with how this task
    # runs: it uses InteractiveToken (the current interactive user) and
    # pythonw.exe, both of which REQUIRE a live interactive session.
    # Boot/Event triggers fire pre-logon, so the task could never start
    # and sat at Last Result 0x41303 ("never run"). A LogonTrigger fires
    # once the user has logged on — an interactive session now exists —
    # so pythonw starts reliably and warms the cache well before the app
    # is opened. The 45s delay lets login settle and avoids disk
    # contention with other startup programs.
    #
    # On Windows Fast Startup (which hibernates the kernel session)
    # LogonTrigger still fires on the user's next logon, so coverage is
    # preserved. The in-process boot sentinel (prewarm._already_warmed)
    # dedups any re-fire on session unlock, so the work happens at most
    # once per boot.
    triggers = ET.SubElement(root, "Triggers")

    # Logon — fires when the current user logs on.
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    # STARTUP-2: fire at logon+0 (_LOGON_DELAY == "PT0S") so prewarm gets a
    # head start before the app's cold imports contend for disk. Low I/O
    # priority handles any login-time contention.
    ET.SubElement(logon, "Delay").text = _LOGON_DELAY

    # PREWARM-001: The IdleTrigger that previously lived here fired every
    # time the system went idle for 15+ minutes, causing prewarm to run
    # 5+ times per session. After the first run the OS file cache is
    # already warm; subsequent runs are pure wasted I/O and — under memory
    # pressure — actively harmful because they re-read ~6 GB of files
    # that the OS had just evicted to free RAM. Prewarm is a cold-boot
    # optimization; running it once per login is sufficient.

    # ── Principal — run as the current user, no elevation, hidden ────
    # PREWARM-FIX: keep InteractiveToken (run in the live interactive user
    # session). This is REQUIRED because the trigger is a LogonTrigger
    # (fires when the user logs on, an interactive session now exists) and
    # because pythonw.exe needs an interactive window station to start.
    # BootTrigger / EventTrigger (system-start) fire BEFORE any user logs
    # on, where an InteractiveToken task cannot launch — that left the
    # previous task at Last Result 0x41303 ("never run"). A LogonTrigger
    # runs reliably once the user logs on, well before the app is opened.
    #
    # Omitting <UserId> in a Principal with id="Author" defaults to the
    # registering (current) user. This avoids fragile SID extraction via
    # ctypes that produced an empty <UserId> that Task Scheduler rejected
    # with "(1,485):UserId incorrectly formatted".
    principal = ET.SubElement(root, "Principals")
    princ = ET.SubElement(principal, "Principal", {"id": "Author"})
    ET.SubElement(princ, "LogonType").text = "InteractiveToken"
    ET.SubElement(princ, "RunLevel").text = "LeastPrivilege"

    # ── Settings ─────────────────────────────────────────────────────
    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "ExecutionTimeLimit").text = "PT10M"  # 10 min max
    ET.SubElement(settings, "Hidden").text = "true"
    ET.SubElement(settings, "RunOnlyIfNetworkAvailable").text = "false"
    ET.SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    ET.SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"

    # PREWARM-001: the <IdleSettings> block that previously lived here was
    # vestigial — once the <IdleTrigger> was removed (it caused prewarm to
    # fire 5+ times per session), these settings had no effect on
    # scheduling.  They were left behind in the earlier cleanup, which was
    # misleading (the XML still advertised an idle behavior the task no
    # longer had).  Prewarm is logon/boot-only on all platforms now.

    # ── Actions ──────────────────────────────────────────────────────
    # STARTUP-1: <Command> is pythonw.exe directly (NO cmd.exe wrapper).
    # pythonw.exe has no console; the previous cmd.exe /c wrapper kept
    # the cmd host alive for the duration of the prewarm run (~10 min),
    # showing a ghost window.
    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    exec_el = ET.SubElement(actions, "Exec")
    ET.SubElement(exec_el, "Command").text = python_exe
    # ADR-0020 §5 fix: only emit <Arguments> when the value is truthy.
    # A frozen Nuitka exe (the Tauri sidecar path) IS the module and
    # takes no -m flag — passing "" must produce NO <Arguments> element
    # (not an empty one, which Task Scheduler still passes as argv[1]="").
    if arguments:
        ET.SubElement(exec_el, "Arguments").text = arguments

    # ET.tostring adds the encoding declaration schtasks expects.
    return ET.tostring(root, encoding="unicode")


# ─── schtasks wrappers ──────────────────────────────────────────────────


def _schtasks(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Run ``schtasks`` with *args*. Returns (returncode, combined output).

    ``schtasks /Create`` can block for up to 30s if the
    Windows Task Scheduler service is hung. This function is now called
    from a background thread (via ``_startup_parallel_work`` in app.py)
    so it doesn't block the main startup sequence.
    """
    cmd = ["schtasks"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output
    except FileNotFoundError:
        log.warning("[TASK] schtasks.exe not found (not Windows?)")
        return 127, "schtasks not found"
    except subprocess.TimeoutExpired:
        log.error("[TASK] schtasks timed out: %s", cmd)
        return 124, "schtasks timed out"


def _schtasks_elevated(args: list[str], *, timeout_ms: int = 60000) -> tuple[int, str]:
    """Run ``schtasks`` with *args* via UAC elevation prompt.

    Used when a non-elevated schtasks call fails with "Access is denied"
    (e.g. overwriting a task created by an admin install).  Shows the
    standard Windows UAC consent dialog and waits for the user to accept
    or reject.

    Returns (returncode, combined_output).  If the user cancels UAC,
    the ShellExecuteExW fails and we return (1223, "user cancelled").
    """
    import ctypes
    import ctypes.wintypes

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("fMask", ctypes.wintypes.ULONG),
            ("hwnd", ctypes.wintypes.HWND),
            ("lpVerb", ctypes.wintypes.LPCWSTR),
            ("lpFile", ctypes.wintypes.LPCWSTR),
            ("lpParameters", ctypes.wintypes.LPCWSTR),
            ("lpDirectory", ctypes.wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.wintypes.HINSTANCE),
            ("lpIDList", ctypes.wintypes.LPVOID),
            ("lpClass", ctypes.wintypes.LPCWSTR),
            ("hKeyClass", ctypes.wintypes.HKEY),
            ("dwHotKey", ctypes.wintypes.DWORD),
            ("hMonitor", ctypes.wintypes.HANDLE),
            ("hProcess", ctypes.wintypes.HANDLE),
        ]

    see_mask_noclose = 0x00000040
    sw_hide = 0

    # build the arg string for schtasks using
    # ``subprocess.list2cmdline`` (the same helper ``subprocess.Popen``
    # uses on Windows internally). The previous hand-rolled join —
    # ``" ".join(f'"{a}"' if " " in a or "&" in a else a for a in args)``
    # — only quoted args containing a space or ``&`` and NEVER escaped
    # embedded ``"`` characters. A malicious or misconfigured arg
    # containing ``"`` could break out of the quoting and inject
    # arbitrary cmd.exe metacharacters into the ``cmd_line`` below
    # (which is then wrapped in another layer of ``cmd.exe /c "..."``
    # quoting via ``sei.lpParameters``). ``list2cmdline`` handles the
    # full Windows command-line quoting rules: it double-quotes any
    # arg containing whitespace, ``"``, or other special chars, and
    # escapes embedded ``"`` as ``\\"`` so the resulting string parses
    # back to the original argv on the cmd.exe side. ``schtasks`` args
    # today are all safe (task name, /Query, /TN, etc.), but the
    # function is a generic helper — hardening it removes a latent
    # injection vector if a future caller passes a user-supplied arg
    # (e.g. a custom ``--trigger`` value).
    arg_str = subprocess.list2cmdline(args)

    # Redirect output to a temp file so we can read it back
    with tempfile.NamedTemporaryFile(mode="w+t", suffix=".txt", delete=False, encoding="utf-8") as out_file:
        out_path = out_file.name

    try:
        # Launch via cmd.exe /c with redirection so we capture output
        cmd_line = f'schtasks {arg_str} > "{out_path}" 2>&1'
        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = see_mask_noclose
        sei.lpVerb = "runas"
        sei.lpFile = "cmd.exe"
        sei.lpParameters = f'/c "{cmd_line}"'
        sei.nShow = sw_hide

        # parity with the non-elevated ``_schtasks`` helper
        # (which logs WARNING on ``FileNotFoundError`` and ERROR on
        # ``TimeoutExpired``). The elevated path previously had ZERO
        # log lines — a UAC-cancel or stale-temp-file failure was
        # silently swallowed, leaving the caller (e.g.
        # ``register_prewarm_task``) to retry blind or give up with no
        # diagnostic trail. Each failure mode now logs at the same
        # severity as the sibling helper per
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.WinError()
            log.warning(
                "[TASK] _schtasks_elevated: ShellExecuteExW failed (UAC declined or shell error) for args=%r: %s",
                args,
                err,
            )
            return 1223, f"UAC elevation failed: {err}"

        # Wait for the process to finish. ``WaitForSingleObject`` returns
        # WAIT_TIMEOUT (258) if the process didn't exit within
        # ``timeout_ms`` — surface that as a warning so a hung schtasks
        # doesn't look like a silent success.
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(
            sei.hProcess,
            timeout_ms,
        )
        # check both documented non-success return values.
        # ``WAIT_TIMEOUT`` (258) means the process is still running
        # after ``timeout_ms`` — log.error so a hung schtasks is
        # visible. ``WAIT_FAILED`` (0xFFFFFFFF) means the wait itself
        # failed (e.g. ``sei.hProcess`` is invalid) — log.warning so
        # the failure is diagnosable before ``GetExitCodeProcess``
        # reads garbage. The finding's suggested ``WAIT_TIMEOUT=124``
        # is incorrect (124 is ETIMEDOUT, not a Win32 wait code); the
        # correct value is 258 (``STATUS_TIMEOUT`` = ``0x102``).
        if wait_result == 258:  # WAIT_TIMEOUT
            log.error(
                "[TASK] _schtasks_elevated: WaitForSingleObject timed out after "
                "%dms (schtasks may still be running) for args=%r",
                timeout_ms,
                args,
            )
        elif wait_result == 0xFFFFFFFF:  # WAIT_FAILED
            log.warning(
                "[TASK] _schtasks_elevated: WaitForSingleObject returned WAIT_FAILED "
                "(handle invalid?) for args=%r — GetExitCodeProcess may return stale value",
                args,
            )

        exit_code = ctypes.wintypes.DWORD()
        # ``GetExitCodeProcess`` returns a BOOL (nonzero on
        # success, zero on failure). The previous call discarded the
        # return value, so a failure (e.g. invalid handle) silently
        # left ``exit_code`` at its zero-initialized value — the caller
        # saw ``rc=0`` (success) and treated a failed read as a
        # successful schtasks run. Now log the failure and fall
        # through with ``STILL_ACTIVE`` (259) sentinel so the caller's
        # ``rc != 0`` branch (which logs warning + retries) fires.
        get_exit_ok = ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
        if not get_exit_ok:
            log.warning(
                "[TASK] _schtasks_elevated: GetExitCodeProcess failed for args=%r "
                "(handle may be invalid); treating as STILL_ACTIVE (259)",
                args,
            )
            exit_code.value = 259  # STILL_ACTIVE
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

        # Read output from the temp file. The empty-output case (e.g.
        # schtasks exited 0 but produced no stdout) is logged at debug
        # so a silent-success is distinguishable from a failed read.
        # An ``OSError`` here (temp file deleted by AV, permissions,
        # etc.) is logged at warning — same severity as the sibling
        # ``_schtasks`` uses for ``FileNotFoundError``.
        output = ""
        try:
            with open(out_path, encoding="utf-8") as f:
                output = f.read()
        except OSError as e:
            log.warning(
                "[TASK] _schtasks_elevated: could not read schtasks output file %s: %s",
                out_path,
                e,
            )
        else:
            if not output:
                log.debug(
                    "[TASK] _schtasks_elevated: schtasks produced empty output for args=%r (exit_code=%d)",
                    args,
                    exit_code.value,
                )

        if exit_code.value != 0:
            log.warning(
                "[TASK] _schtasks_elevated: schtasks exited %d for args=%r (output: %r)",
                exit_code.value,
                args,
                output[:200],
            )
        else:
            log.debug(
                "[TASK] _schtasks_elevated: schtasks exited 0 for args=%r",
                args,
            )

        return exit_code.value, output

    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)


# ─── Public API ──────────────────────────────────────────────────────────


def is_supported() -> bool:
    """Return True if prewarm scheduling is supported on this platform.

    STARTUP-5: previously Windows-only. Now also returns True on macOS
    and Linux (via prewarm_scheduler_posix). The actual scheduling layer
    differs by platform (Task Scheduler / LaunchAgent / systemd user timer).

    Returns:
        True if the platform supports scheduled prewarm tasks.
    """
    if is_windows():
        return Path(os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32\schtasks.exe").exists()
    # STARTUP-5: POSIX platforms use prewarm_scheduler_posix.
    # Bug fix: use exact match instead of startswith("linux") for
    # consistency with prewarm_scheduler_posix.is_supported().
    return is_macos() or is_linux()


def is_prewarm_registered() -> bool:
    """Return True if prewarm is registered via EITHER mechanism.

    The Task Scheduler task is preferred; the HKCU Run key is the fallback.
    On POSIX, delegates to prewarm_scheduler_posix.

    Returns:
        True if a prewarm task is currently registered.
    """
    # STARTUP-5: delegate to POSIX scheduler on macOS/Linux.
    if not is_windows():
        try:
            from voice_typer.server.prewarm_scheduler_posix import is_prewarm_registered as _posix_is

            return _posix_is()
        except Exception as exc:
            # parity with the sibling ``register_prewarm_task``
            # and ``unregister_prewarm_task`` POSIX delegates (which log
            # at WARNING on ``Exception``). The previous silent
            # ``return False`` here masked import failures (e.g. the
            # ``prewarm_scheduler_posix`` module not yet shipped on this
            # platform) and runtime failures (e.g. a LaunchAgent/
            # systemd query subprocess crash) — the caller's
            # ``is_prewarm_registered`` simply reported "not registered",
            # the Settings toggle was a no-op, and the user had no
            # diagnostic trail to follow. ``warning`` (not ``debug``)
            # because the POSIX path is the ONLY path on macOS/Linux —
            # a failure here is never an expected state.
            log.warning("[TASK] POSIX is_prewarm_registered raised: %s", exc)
            return False
    if not is_supported():
        return False
    rc, _ = _schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    return rc == 0 or _is_prewarm_registered_registry()


def register_prewarm_task() -> bool:
    """Register (or update) the prewarm task.

    Returns True on success, False on failure.  Safe to call repeatedly —
    existing tasks are overwritten with ``/F``.

    STARTUP-5: on macOS/Linux, delegates to prewarm_scheduler_posix which
    registers a LaunchAgent (macOS) or systemd user timer (Linux).

    Returns:
        True if the task was registered successfully, False otherwise.
    """
    # STARTUP-5: delegate to POSIX scheduler on macOS/Linux.
    if not is_windows():
        try:
            from voice_typer.server.prewarm_scheduler_posix import register_prewarm_task as _posix_reg

            return _posix_reg()
        except Exception as e:
            log.warning("[TASK] POSIX prewarm registration raised: %s", e)
            return False
    if not is_supported():
        log.info("[TASK] Scheduled Tasks not supported on this platform — skipping")
        return False

    command = _prewarm_command()
    if command is None:
        log.warning("[TASK] cannot resolve prewarm command — skipping registration")
        return False

    # ADR-0020 §5: when the resolver returned a frozen exe path (no
    # ``-m voice_typer.server.prewarm`` module args), the task action
    # is just the exe path itself — no <Arguments> element. When the
    # resolver returned the legacy pythonw path, we keep _PREWARM_ARGS
    # (the module name + --trigger logon) as before.
    if os.environ.get("TAURI_SIDECAR") == "1" or os.environ.get("VOICE_TYPER_PREWARM_EXE"):
        # The resolver may have returned either a frozen exe path or
        # fallen back to the dev python-module path. Detect by checking
        # whether the command points at a file with no ``-m`` suffix.
        # If it's a frozen exe, pass "" so _build_task_xml emits NO
        # <Arguments> element (the frozen exe IS the module).
        from voice_typer.server.prewarm_resolver import resolve_prewarm_exe

        resolved = resolve_prewarm_exe()
        if resolved and " -m " not in resolved:
            # Frozen exe — pass empty string, NOT None (None falls back
            # to _PREWARM_ARGS which would break the frozen exe).
            xml_def = _build_task_xml(command, "")
            log.info("[TASK] Tauri sidecar mode — prewarm frozen exe registered without module args")
        else:
            # Dev fallback — use the legacy args.
            xml_def = _build_task_xml(command, _PREWARM_ARGS)
    else:
        xml_def = _build_task_xml(command, _PREWARM_ARGS)

    # Write XML to a temp file — schtasks /Create /XML needs a path, and
    # passing huge inline args via cmd.exe is fragile.
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as tf:
            tf.write(xml_def)
            temp_xml = tf.name
    except OSError:
        log.exception("[TASK] could not write task XML")
        return False

    try:
        # Delete any existing (possibly locked) task BEFORE creating.
        # A task created with the old, broken UserId XML can become
        # locked so that `schtasks /Create /F` fails with "Access is
        # denied".  An explicit /Delete /F first clears it cleanly;
        # if it doesn't exist, the error is harmless.  Also deletes
        # the pre-rename legacy task name so upgrades converge on the
        # com.voicetyper.* namespace (no duplicate logon trigger).
        #
        # (this session): the pre-fix code used
        # ``with contextlib.suppress(Exception): _schtasks(...)`` —
        # this swallowed *every* exception including ``TypeError``,
        # ``AttributeError``, ``ImportError`` (e.g. if a future edit
        # introduced a typo in ``TASK_NAME`` and ``_schtasks`` was
        # called with a ``None`` arg, the resulting ``TypeError``
        # would be silently swallowed and the subsequent ``/Create``
        # would fail with a confusing "task already exists" instead
        # of pointing at the real bug). Narrow to the documented
        # failure modes (``subprocess.SubprocessError`` + ``OSError``)
        # and log at DEBUG so the pre-create cleanup is visible in
        # debug logs without polluting INFO. Unexpected exceptions
        # propagate so genuine bugs surface.
        for task_name in (TASK_NAME, _LEGACY_TASK_NAME):
            try:
                _schtasks(["/Delete", "/TN", task_name, "/F"], capture=True)
            except (subprocess.SubprocessError, OSError) as exc:
                log.debug(
                    "[TASK] pre-create /Delete failed (task may not exist or be locked): %s",
                    exc,
                )

        # /F forces overwrite if the task already exists.
        rc, output = _schtasks(
            ["/Create", "/TN", TASK_NAME, "/XML", temp_xml, "/F"],
            capture=True,
        )
        # If non-elevated attempt fails with Access denied, try via UAC
        if rc != 0 and "access is denied" in (output or "").lower():
            log.info("[TASK] Non-elevated schtasks failed — retrying with UAC elevation prompt")
            rc, output = _schtasks_elevated(
                ["/Create", "/TN", TASK_NAME, "/XML", temp_xml, "/F"],
            )
        if rc == 0:
            log.info("[TASK] Prewarm task %s registered OK", TASK_NAME)
            # We switched to the Task Scheduler path — remove any stale
            # Run-key fallback left by a previous (admin-less) run so we
            # don't prewarm twice at the next logon.
            if _is_prewarm_registered_registry():
                _unregister_prewarm_registry()
            return True
        # Fall back to the HKCU Run key (no admin needed).  The most common
        # cause of the /Create failure is a task left locked by a previous
        # admin-created install that a standard user cannot overwrite; the
        # Run key gives us a working, admin-free logon trigger regardless.
        log.warning(
            "[TASK] schtasks registration failed (%s) — falling back to HKCU Run key",
            output.strip(),
        )
        reg_cmd = _registry_command()
        if reg_cmd is None:
            log.warning("[TASK] Cannot build Run-key command — aborting")
            return False
        return _register_prewarm_registry(reg_cmd)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp_xml)


def unregister_prewarm_task() -> bool:
    """Delete the prewarm scheduled task AND its Run-key fallback.

    Returns True if prewarm is fully removed (or was never present).  We
    always clean up both mechanisms: a standard user may not be able to
    delete a locked Task Scheduler task, but the Run key is always
    user-writable, so the prewarm is reliably managed.

    Both the current ``com.voicetyper.prewarm`` names and the pre-rename
    legacy ``VoiceTyperPrewarm`` task/Run-key value are removed, so
    installs that predate the namespace rename are fully cleaned too.

    STARTUP-5: on macOS/Linux, delegates to prewarm_scheduler_posix.

    Returns:
        True if the prewarm task was fully removed or was not present.
    """
    # STARTUP-5: delegate to POSIX scheduler on macOS/Linux.
    if not is_windows():
        try:
            from voice_typer.server.prewarm_scheduler_posix import unregister_prewarm_task as _posix_unreg

            return _posix_unreg()
        except Exception as e:
            log.warning("[TASK] POSIX prewarm removal raised: %s", e)
            return False
    if not is_supported():
        return False

    removed_task = False
    for task_name in (TASK_NAME, _LEGACY_TASK_NAME):
        rc, output = _schtasks(["/Delete", "/TN", task_name, "/F"], capture=True)
        if rc == 0:
            log.info("[TASK] Prewarm scheduled task %s removed", task_name)
            removed_task = True
        elif rc == 1 and ("cannot find" in output.lower() or "does not exist" in output.lower()):
            log.info("[TASK] Prewarm scheduled task %s was already absent", task_name)
            removed_task = True
        elif "access is denied" in output.lower():
            # A locked task the standard user can't delete.  The Run-key
            # path below still succeeds, and the next logon won't relaunch
            # prewarm from the registry.  The orphaned task is inert: it
            # points at the deleted prewarm binary and the prewarm module
            # skips when free RAM is low (EXIT_LOW_RAM).
            log.warning(
                "[TASK] Cannot delete locked scheduled task %s without admin; "
                "the Run-key fallback is removed and prewarm will no-op via config.",
                task_name,
            )
        else:
            log.warning("[TASK] task %s removal failed (rc=%d): %s", task_name, rc, output.strip())

    # Always remove the Run-key fallback (user-writable, never locked).
    removed_reg = _unregister_prewarm_registry()

    return removed_task or removed_reg or not is_prewarm_registered()
