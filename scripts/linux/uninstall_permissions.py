#!/usr/bin/env python3
"""Voice Typer — Linux keyboard permission uninstaller.

Thin wrapper around ``install_permissions.py --uninstall``. Kept as a
separate script so package managers can reference it directly in prerm
scripts without passing arguments.

Called by:
- Debian ``prerm`` (as root, during ``apt remove voice-typer``)
- RPM ``%preun`` (as root, during ``dnf remove voice-typer``)

S2-CR-70 (SA-6): this script now also handles an optional ``--purge``
flag (or ``VOICE_TYPER_PURGE=1`` env var) that removes the per-user
Voice Typer data directory (HuggingFace model cache, venv, history DB,
logs) BEFORE delegating to the system-level uninstaller. The purge is
OFF by default so users who reinstall keep their models; pass it
explicitly to reclaim disk:

    # Uninstall system files only (default — preserves user data):
    sudo uninstall_permissions.py

    # Uninstall system files AND purge all user data (GBs of models):
    sudo uninstall_permissions.py --purge

    # Same, via env var (useful with apt/dnf which can't pass argv):
    sudo VOICE_TYPER_PURGE=1 apt remove voice-typer

The purge runs BEFORE the ``os.execv`` delegation because
``os.execv`` replaces the current process image — anything after it
would never execute. The order is safe because the user-data purge is
per-user (runs as the user via ``SUDO_USER``) and the system-level
uninstall runs as root; the purge is best-effort and logs warnings on
failure rather than aborting the system uninstall.

Exit codes: same as install_permissions.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ─── S2-CR-70 (SA-6): --purge flag handling ─────────────────────────────
#
# Parse --purge out of argv BEFORE delegating to install_permissions.py
# (which doesn't understand the flag and would error). The remaining
# argv (with --purge stripped) is passed through to the delegated
# uninstaller.
#
# The purge is also activated by the VOICE_TYPER_PURGE=1 env var so
# package-manager-driven uninstalls (apt remove / dnf remove) can opt
# into a purge without modifying the prerm script. The user would set
# the env var via ``sudo VOICE_TYPER_PURGE=1 apt remove voice-typer``
# (apt's prerm inherits the env from the sudo session). Both the flag
# and the env var are OFF by default so the default uninstall
# preserves user data (users who reinstall keep their models).
_purge_requested = "--purge" in sys.argv or os.environ.get("VOICE_TYPER_PURGE", "").strip() in ("1", "true", "yes")
if "--purge" in sys.argv:
    # Build a new argv without --purge so the delegated
    # install_permissions.py --uninstall doesn't see it (it would
    # error with "unknown argument").
    sys.argv = [a for a in sys.argv if a != "--purge"]


def _purge_user_data_for(username: str, data_dir: Path) -> None:
    """Remove the per-user Voice Typer data dir for ``username``.

    S2-CR-70 (SA-6): Voice Typer stores ALL user data (HuggingFace
    model cache, venv, history DB, logs, crash-recovery snapshots,
    single-instance lockfiles) inside ``<config_dir>``. The purge
    removes each known subpath individually (NOT a blanket
    ``rm -rf <config_dir>``) so an accidental shared
    ``$XDG_DATA_HOME`` doesn't take out unrelated user files.

    Uses ``sudo -u <username> -- rm -rf <subpath>`` per subpath so the
    deletion runs as the user (not root) — this preserves file
    ownership semantics and works even when the data dir contains
    files owned by the user that root would otherwise need to chown
    (e.g. venv files created with the user's umask).

    Best-effort: logs warnings on failure but does not raise.
    """
    print(
        f"[voice-typer-permissions] --purge: removing user data for '{username}' at {data_dir}",
        file=sys.stderr,
    )
    # The subpaths list mirrors
    # voice_typer/server/_paths.py::user_data_subpaths_for_purge() — kept
    # inline here (rather than imported) because this script runs as root
    # during prerm and may not be able to import the voice_typer package
    # (the bundled PyInstaller executable is the sidecar, not this
    # script). The list is intentionally exhaustive — covers every file /
    # subdirectory Voice Typer creates inside the config dir.
    subpaths = [
        "huggingface",  # HF model cache (GBs)
        "venv",  # Python venv (hundreds of MB)
        "logs",  # rotating log files
        "history.db",  # SQLite history DB
        "history.db-wal",  # SQLite WAL (may not exist)
        "history.db-shm",  # SQLite SHM (may not exist)
        "crash_recovery.json",  # crash-recovery snapshot
        "backend.lock",  # single-instance POSIX lockfile
        "backend.pid",  # backend PID file (Windows + POSIX)
        "autostart.log",  # macOS LaunchAgent autostart log
        "prewarm-launchagent.log",  # macOS LaunchAgent prewarm log
        "onboarding.marker",  # onboarding completion sentinel
    ]
    for sub in subpaths:
        target = data_dir / sub
        if not target.exists():
            continue
        # Use sudo -u <user> -- rm -rf so deletion runs as the user
        # (preserves ownership; avoids root-owned cruft in user's
        # home). subprocess.run with check=False so a single failed
        # subpath doesn't abort the whole purge.
        try:
            result = subprocess.run(
                ["sudo", "-u", username, "--", "rm", "-rf", str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,  # bound the rm — a hung NFS / FUSE mount shouldn't stall prerm
            )
            if result.returncode != 0:
                print(
                    f"[voice-typer-permissions] WARNING: --purge: failed to remove {target}: {result.stderr.strip()}",
                    file=sys.stderr,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            print(
                f"[voice-typer-permissions] WARNING: --purge: failed to remove {target}: {exc}",
                file=sys.stderr,
            )
    # Try to remove the now-empty data dir itself (best-effort; will
    # fail if there are non-Voice-Typer files inside, which is fine —
    # we only created the listed subpaths).
    try:
        result = subprocess.run(
            ["sudo", "-u", username, "--", "rmdir", str(data_dir)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # rmdir exits non-zero if the dir is non-empty — that's expected
        # and not worth warning about.
        if result.returncode != 0 and data_dir.exists():
            # The dir still exists — check if it's because it's non-empty
            # (expected — we didn't create it) or because rmdir failed.
            try:
                remaining = list(data_dir.iterdir())
                if remaining:
                    print(
                        f"[voice-typer-permissions] --purge: {data_dir} still "
                        f"contains {len(remaining)} item(s) not created by "
                        "Voice Typer — left in place",
                        file=sys.stderr,
                    )
            except OSError:
                pass
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(
            f"[voice-typer-permissions] WARNING: --purge: failed to rmdir {data_dir}: {exc}",
            file=sys.stderr,
        )


def _purge_user_data() -> None:
    """Remove the per-user Voice Typer data directory.

    S2-CR-70 (SA-6): resolves the user(s) whose data dir should be
    purged. When invoked via sudo, ``SUDO_USER`` identifies the user.
    When invoked directly as root (e.g. during prerm), ``SUDO_USER`` is
    unset and we scan ``/home/*`` for any user with a Voice Typer data
    dir. Best-effort: errors are logged to stderr but do NOT abort the
    uninstall.
    """
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if sudo_user:
        # SUDO_USER is set — purge just that user's data dir.
        import pwd  # POSIX-only; the script is Linux-only per its docstring

        try:
            pwent = pwd.getpwnam(sudo_user)
        except KeyError:
            print(
                f"[voice-typer-permissions] WARNING: SUDO_USER '{sudo_user}' not found — skipping user-data purge",
                file=sys.stderr,
            )
            return
        home = Path(pwent.pw_dir)
        # Check both XDG default and legacy path (config_dir() checks both).
        xdg_path = home / ".local" / "share" / "voice-typer"
        legacy_path = home / ".voice-typer"
        for candidate in (xdg_path, legacy_path):
            if candidate.is_dir():
                _purge_user_data_for(sudo_user, candidate)
        return

    # No SUDO_USER — scan /home for any user with a Voice Typer data dir.
    # This is the prerm codepath (apt/dnf run prerm as root with no
    # SUDO_USER). Best-effort: if no user has a Voice Typer data dir, the
    # purge is a no-op (the system-level uninstall still runs).
    home_root = Path("/home")
    if not home_root.is_dir():
        return
    for home in home_root.iterdir():
        if not home.is_dir():
            continue
        # Check both the XDG default and the legacy ~/.voice-typer path
        # (the config_dir() resolver checks both — see
        # voice_typer/server/config.py).
        xdg_path = home / ".local" / "share" / "voice-typer"
        legacy_path = home / ".voice-typer"
        for candidate in (xdg_path, legacy_path):
            if candidate.is_dir():
                _purge_user_data_for(home.name, candidate)


if _purge_requested:
    _purge_user_data()


# Delegate to install_permissions.py --uninstall
installer_path = Path(__file__).resolve().parent / "install_permissions.py"
if not installer_path.is_file():
    print("[voice-typer-permissions] ERROR: install_permissions.py not found", file=sys.stderr)
    sys.exit(1)

# Use exec to replace this process — cleaner than subprocess for a wrapper.
#
# CRITICAL: always inject ``--uninstall`` into the delegated argv.
# install_permissions.py::main() branches on ``"--uninstall" in sys.argv``:
#   present → uninstall()
#   absent  → install()   ← would RE-INSTALL udev rules / XKB / user group
# So if the wrapper were called with no args (the normal prerm / `sudo
# uninstall_permissions.py` invocation) and we forwarded only the
# caller's argv, install_permissions.py would default to ``install()``
# and the "uninstall" would actually re-grant permissions. Always pass
# ``--uninstall``; also forward any other args the caller supplied
# (after stripping ``--purge`` above and de-duping ``--uninstall`` so
# we don't pass it twice).
other_args = [a for a in sys.argv[1:] if a != "--uninstall"]
os.execv(
    sys.executable,
    [sys.executable, str(installer_path), "--uninstall", *other_args],
)
