"""macOS autostart — LaunchAgent plist.

Phase 4.5 /  — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Implements the
three macOS autostart primitives:

  - :func:`_enable_autostart_macos` — write ``~/Library/LaunchAgents/com.voicetyper.plist``
+ ``launchctl load`` (with a 5 s timeout — ).
  - :func:`_disable_autostart_macos` — ``launchctl bootout`` (modern,
    macOS 10.10+) + ``launchctl remove`` (legacy fallback) + delete the
    plist file.
  - :func:`_is_autostart_macos` — file-existence probe on the plist.
  - :func:`_os_uid` — current user's numeric uid (for the
    ``launchctl bootout gui/<uid>/<label>`` target).

Patch-path compatibility
------------------------
Tests patch ``get_autostart_dir`` via
``monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)``
(in :mod:`tests.test_platform`) and patch ``_os_uid`` via
``monkeypatch.setattr(platform_mod, "_os_uid", lambda: 501)``.  Both
are looked up via ``_pkg.X()`` at call time so the patches take effect.

``Path.home()`` and ``subprocess.run`` are patched globally (via
``monkeypatch.setattr(Path, "home", ...)`` and
``monkeypatch.setattr(subprocess, "run", fake_run)`` in the mig16
``darwin_platform`` fixture) — both resolve to the same stdlib module
objects that this file imports, so the global patches propagate without
any ``_pkg`` indirection.

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

from voice_typer.server import _paths

# Patch-path bridge: route lookups of ``get_autostart_dir`` and
# ``_os_uid`` through the package namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
# keep affecting production code defined here.
from voice_typer.server import server_platform as _pkg

log = logging.getLogger(__name__)


def _enable_autostart_macos() -> bool:
    from xml.sax.saxutils import escape

    plist_dir = _pkg.get_autostart_dir()
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.voicetyper.plist"
    launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"

    # previously the plist's ``WorkingDirectory`` was
    # set to the literal string ``~``.  launchd does NOT expand ``~``
    # in plist values — the WorkingDirectory must be an absolute path.
    # The literal ``~`` caused launchd to fail to chdir into anything
    # (silently on some macOS versions, noisily on others), so the
    # autostarted Python process inherited launchd's ``/`` working
    # directory — which in turn made relative file operations in
    # autostart_launcher.py resolve to the wrong place.
    working_dir = str(Path.home())

    # macOS-VENV-AUTOSTART: for parity with Linux + Windows,
    # probe whether a system Python (if we're in a venv) can import
    # ``voice_typer.server.autostart_launcher`` before swapping. macOS
    # users typically run from a Homebrew Python or a system Python —
    # not a venv — so the swap is usually skipped. But dev-mode users
    # who ``uv venv && source .venv/bin/activate`` would otherwise
    # have their LaunchAgent point at the venv Python, which breaks
    # if the venv is deleted. The probe is the same as Linux/Windows
    # (``_system_python_can_import_launcher``), imported lazily to
    # avoid a circular import.
    python_exe = sys.executable
    if sys.prefix != sys.base_prefix:
        import shutil

        system_python = shutil.which("python3")
        if system_python:
            from voice_typer.server.server_platform.autostart import (
                _system_python_can_import_launcher,
            )

            if _system_python_can_import_launcher(system_python):
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
                    "LaunchAgent — autostart will break if the venv is "
                    "deleted, but works for the current user.",
                    python_exe,
                )

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voicetyper</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(python_exe)}</string>
        <string>{escape(str(launcher))}</string>
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
    # leave a half-truncated plist that launchd refuses to load on next
    # boot. durability=False matches the existing prewarm/autostart
    # pattern — these plist files do not need fsync.
    from voice_typer.server.secure_file_io import _secure_atomic_write

    _secure_atomic_write(plist_path, plist_content, durability=False)
    plist_path.chmod(0o600)
    # ensure the log directory exists with private perms
    # use _paths.config_dir() instead of Path.home() / ".voice-typer"
    # so the plist's StandardOutPath/StandardErrorPath and the actual log
    # directory on disk agree (and respect VOICE_TYPER_CONFIG_DIR /
    # platform-specific paths).
    log_dir = _paths.config_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(log_dir, 0o700)
    # (pyrefly): import subprocess BEFORE the try block so the
    # ``except subprocess.TimeoutExpired`` clause has a guaranteed-bound
    # name. Previously the import was inside the try, so if the import
    # itself failed (extremely unlikely, but pyrefly cannot prove
    # otherwise) the except clause would have raised UnboundLocalError
    # instead of catching the intended exception.

    try:
        # previously ``launchctl load`` had no timeout,
        # so a hung launchd (rare but possible after a macOS upgrade
        # or in a stuck boot) would block this thread forever.  The
        # 5-second timeout matches what the Apple docs say is the
        # upper bound for a healthy launchctl load.
        #
        # inspect the CompletedProcess returncode AND stderr.
        # Pre-fix, this function unconditionally ``return True`` after
        # the subprocess.run call — so a launchctl load failure (e.g.
        # "Loader.Error" or "exited with" in stderr, or a non-zero
        # return code) was swallowed and the renderer showed "Autostart
        # enabled" even though the LaunchAgent was NOT loaded. The user
        # rebooted and Voice Typer didn't start, with no diagnostic.
        completed = subprocess.run(
            ["launchctl", "load", str(plist_path)],
            check=False,
            capture_output=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        log.warning("[CONFIG] launchctl load timed out after 5s — launchd may be unresponsive")
        # surface the timeout to the caller so the renderer can
        # show "Autostart failed: launchctl load timed out" instead of
        # the misleading success toast.
        log.error("[CONFIG] Autostart enable FAILED: launchctl load timed out")
        return False
    except Exception as e:
        log.warning("[CONFIG] launchctl load failed: %s", e)
        log.error("[CONFIG] Autostart enable FAILED: %s", e)
        return False

    # inspect returncode + stderr. ``launchctl load`` exits 0 on
    # success and non-zero on failure. The stderr text contains hints
    # like "Loader.Error: ... exited with" for plist-syntax / path /
    # permission errors. We treat BOTH a non-zero returncode AND the
    # known error-substring patterns as failure (defensive — some
    # launchctl bugs return 0 but still write to stderr).
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
    # Defensive substring check — handles the launchctl bug where
    # returncode is 0 but stderr still reports a Loader.Error.
    if "loader.error" in stderr_lower or "exited with" in stderr_lower:
        err_msg = f"launchctl load reported error (rc=0): {stderr_text.strip()}"
        log.warning("[CONFIG] Autostart enable FAILED: %s", err_msg)
        return False

    log.info("[CONFIG] Autostart enabled (macOS): %s", plist_path)
    return True


def _disable_autostart_macos() -> bool:
    plist_path = _pkg.get_autostart_dir() / "com.voicetyper.plist"
    # Unload the running job BEFORE deleting the plist, otherwise the
    # job keeps running until next logout even though it's "disabled".
    # Prefer the modern `launchctl bootout` (macOS 10.10+) and fall back
    # to the legacy `launchctl remove` for older systems.  Both are
    # best-effort — failure here just means the job lingers until logout.
    label = "com.voicetyper"
    for args in (
        ["launchctl", "bootout", f"gui/{_pkg._os_uid()}/{label}"],
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
            log.debug("[PLATFORM] os.getuid failed — falling back to 501", exc_info=True)
    return 501  # default first user on macOS


def _is_autostart_macos() -> bool:
    return (_pkg.get_autostart_dir() / "com.voicetyper.plist").exists()
