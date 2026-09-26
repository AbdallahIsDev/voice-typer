"""Native hotkey backend, {name}."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

log = logging.getLogger(__name__)

# Legacy per-PID diagnostic files (``native-<backend>-<pid>.log``): one file
_LEGACY_PID_LOG_RE = re.compile(r"^native-.+-(\d+)\.log$")


def _resolve_canonical_logs_dir() -> Path | None:
    """Return the canonical ``<config_dir>/logs`` dir, or ``None``.

    Uses the single source of truth (``paths._config_dir`` +
    ``log.get_logs_dir``) so native diagnostics live beside every other
    log, including ``VOICE_TYPER_CONFIG_DIR`` overrides and the
    Windows ``%APPDATA%`` location for new installs. Local imports avoid
    a module-load cycle (``native_hotkeys`` is imported early).
    """
    try:
        from voice_typer.server.config_internals import paths as _paths_mod
        from voice_typer.server.log import get_logs_dir

        return get_logs_dir(_paths_mod._config_dir())
    except Exception:
        return None


def _legacy_home_logs_dir() -> Path | None:
    """Return the pre-canonical ``~/.lausu/logs`` dir, or ``None``."""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return None
    if not str(home) or str(home) == ".":
        return None
    return home / ".lausu" / "logs"


def _sweep_legacy_per_pid_logs(keep: Path | None = None) -> None:
    """Delete legacy ``native-<backend>-<pid>.log`` orphans (best-effort).

    Sweeps both the canonical logs dir and the legacy home logs dir (old
    builds hardcoded the legacy path, so new-install machines may hold
    orphans in a dir the canonical sweep never visits). Never deletes
    ``keep`` (the stable per-backend file). Never raises.
    """
    try:
        dirs: list[Path] = []
        if keep is not None:
            dirs.append(keep.parent)
        else:
            canonical = _resolve_canonical_logs_dir()
            if canonical is not None:
                dirs.append(canonical)
        legacy = _legacy_home_logs_dir()
        if legacy is not None and all(d != legacy for d in dirs):
            dirs.append(legacy)
        seen: set[Path] = set()
        for d in dirs:
            try:
                resolved = d.resolve()
            except OSError:
                resolved = d
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                if not d.is_dir():
                    continue
            except OSError:
                continue
            try:
                candidates = list(d.glob("native-*-*.log"))
            except OSError:
                continue
            for f in candidates:
                try:
                    if keep is not None and f == keep:
                        continue
                    if not f.is_file():
                        continue
                    if not _LEGACY_PID_LOG_RE.match(f.name):
                        continue
                    f.unlink()
                    log.debug("[NATIVE-HOTKEY] removed legacy per-PID log %s", f.name)
                except OSError:
                    continue
    except Exception as exc:  # deliberate broad catch: cleanup must never block the spawn
        log.debug("[NATIVE-HOTKEY] legacy native-log sweep failed: %s", exc)


class _SpawnMixin:
    # Human-readable backend name used in log messages. Provided by the
    platform_name: str

    # Members provided by the composed ``SubprocessHotkeyBackend``
    hotkey_str: str
    _binary_path: Path | None
    _native_log_path: Path | None
    _process: subprocess.Popen | None
    _reader_thread: threading.Thread | None
    _watchdog_thread: threading.Thread | None
    _watchdog_stop_event: threading.Event
    _failed: bool
    _error_message: str | None
    _last_event_received_at: float
    _last_pong_received_at: float

    if TYPE_CHECKING:
        # Methods provided by the sibling mixins (``_reader.py`` /
        def _reader_loop(self) -> None: ...

        def _watchdog_loop(self) -> None: ...

    def _spawn_process(self) -> None:
        """Spawn the native binary with the hotkey spec as argv[1].

        re-verify the binary's SHA-256 against the
        manifest BEFORE every spawn. The factory's
        ``verify_native_binary_or_skip`` call at construction time
        covers the FIRST spawn, but the watchdog respawns the binary
        on liveness timeout (every ``_WATCHDOG_RESPAWN_SECONDS=60s``
        of inactivity) by calling ``stop()`` + ``start()`` →
        ``_spawn_process()`` WITHOUT going back through the factory.
        A TOCTOU window opens between the original verification and
        the respawn: an attacker swapping the binary on disk during
        that window achieves native-code execution as the user.
        Re-running the checksum at the top of ``_spawn_process`` closes
        the window for both the initial spawn and every respawn. On
        verification failure we set ``_failed=True`` and return early
        so the caller's ``_ready_event.wait(timeout=...)`` times out
        and raises a clear ``RuntimeError`` (rather than spawning an
        untrusted binary).

        TOCTOU mitigation between ``verify_native_binary_or_skip``
        and ``subprocess.Popen``. The previous code did::

            if not verify_native_binary_or_skip(self._binary_path):
                return
            subprocess.Popen([str(self._binary_path), ...])

        Between the verify and Popen, an attacker with write access to
        the binary path could swap the file on disk. The verify reads
        bytes via ``path.read_bytes()``; Popen re-resolves the path →
        execve, so a swap between the two achieves native-code
        execution as the user with a verified-clean path.

        Mitigation (POSIX only: see Windows limitation below):

        1. Open the file with ``os.open(path, O_RDONLY | O_CLOEXEC)``
           BEFORE the verify, pinning the inode at that moment.
        2. Run the existing SHA-256 verify (unchanged, uses
           ``path.read_bytes()`` so the existing tests' patch of
           ``verify_native_binary_or_skip`` continues to take effect).
        3. After verify, ``fstat`` the fd → capture
           ``(st_dev, st_ino, st_mtime_ns, st_size)``. This is the
           stat of the inode the fd pinned.
        4. Just before ``Popen``, ``os.stat`` the path and compare to
           the fstat. If the quartet differs, the file was swapped or
           modified between the os.open and the Popen, refuse to
           spawn.

        The fd does NOT need to be the same inode as what the verify
        read. If the file was swapped between os.open and verify, the
        os.stat check at step 4 catches it (path's stat ≠ fd's stat).
        If the file was swapped between verify and os.stat, the
        os.stat check catches it (path's stat ≠ fd's stat, because fd
        still pins the original inode). The only residual TOCTOU is
        between os.stat and the execve inside Popen, a sub-microsecond
        window.

        Residual TOCTOU (POSIX): an attacker who can win the race
        between os.stat and execve (e.g. via inotify + a tight rename
        loop) could still swap the binary. Closing this fully requires
        ``fexecve(fd, argv, envp)`` (exec from the fd, not the path),
        which is not exposed by ``subprocess.Popen`` and is not
        portable to older Linux kernels. The fd is closed AFTER
        ``Popen`` returns so the pinned inode stays referenced for the
        duration of the spawn (defense-in-depth: an attacker who
        unlinks+replaces the path can't reclaim the verified inode's
        disk blocks while the fd is open).

        Windows limitation: Windows does not have ``O_CLOEXEC`` and
        ``subprocess.Popen`` on Windows does not accept an open fd as
        argv[0] (it must be a path string). The TOCTOU window on
        Windows is the same as the pre- code (between verify
        and the ``CreateProcess`` call inside Popen). This is
        documented as a known limitation; the mitigation on Windows
        is the existing SHA-256 manifest gate (still in place) plus
        the assumption that the install dir is not writable by an
        untrusted user.
        """
        if self._binary_path is None:
            self._failed = True
            self._error_message = (
                f"Native {self.platform_name} key-listener binary not found. "
                f"Set VOICE_TYPER_NATIVE_BINARY or rebuild the project."
            )
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            return
        # Local import to avoid an import-cycle (binary_path.py imports
        from .binary_path import verify_native_binary_or_skip

        # on POSIX, open the file with O_RDONLY | O_CLOEXEC
        on_posix = _native_hotkeys_pkg.is_macos() or _native_hotkeys_pkg.is_linux()
        fd: int | None = None
        pinned_stat: tuple | None = None
        if on_posix:
            try:
                # ``O_CLOEXEC`` does not exist on Windows (pre-Python
                o_cloexec = getattr(os, "O_CLOEXEC", 0)
                fd = os.open(
                    str(self._binary_path),
                    os.O_RDONLY | o_cloexec,
                )
            except OSError as exc:
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary open failed "
                    f"during TOCTOU re-verify (path={self._binary_path}): {exc}"
                )
                log.exception("[NATIVE-HOTKEY] %s", self._error_message)
                return

        if not verify_native_binary_or_skip(self._binary_path):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            self._failed = True
            self._error_message = (
                f"Native {self.platform_name} binary failed SHA-256 verification "
                f"on spawn/respawn (path={self._binary_path}). Refusing to spawn "
                f"an untrusted binary, falling back to the legacy backend."
            )
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            return

        # capture the pinned inode's stat for the pre-Popen
        if fd is not None:
            try:
                st = os.fstat(fd)
                pinned_stat = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
            except OSError as exc:
                with contextlib.suppress(OSError):
                    os.close(fd)
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary fstat failed "
                    f"during TOCTOU re-verify (path={self._binary_path}): {exc}"
                )
                log.exception("[NATIVE-HOTKEY] %s", self._error_message)
                return

        # pre-Popen stat check. If the path's stat differs
        if pinned_stat is not None:
            try:
                pst = os.stat(str(self._binary_path))
                path_stat = (pst.st_dev, pst.st_ino, pst.st_mtime_ns, pst.st_size)
            except OSError as exc:
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary stat failed pre-Popen (path={self._binary_path}): {exc}"
                )
                log.exception("[NATIVE-HOTKEY] %s", self._error_message)
                return
            if path_stat != pinned_stat:
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary stat changed "
                    f"between verify and Popen (path={self._binary_path}), "
                    f"possible TOCTOU swap. Refusing to spawn an untrusted binary."
                )
                log.error("[NATIVE-HOTKEY] %s", self._error_message)
                return

        # A backend spawns its own native listener process here only when
        cmd = [str(self._binary_path), self.hotkey_str]
        # Pass the binary its diagnostic log path (``--log-file``): the
        native_log_path: Path | None
        try:
            native_log_path = self._compute_native_log_path()
        except Exception as exc:  # deliberate broad catch: logging setup must never block the spawn
            log.debug(
                "[NATIVE-HOTKEY] %s native log path computation failed, spawning without --log-file: %s",
                self.platform_name,
                exc,
            )
            native_log_path = None
        if native_log_path is not None:
            cmd.extend(["--log-file", str(native_log_path)])
        else:
            log.debug(
                "[NATIVE-HOTKEY] %s spawning without --log-file (log path unresolvable)",
                self.platform_name,
            )
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # change stdin from DEVNULL to PIPE so the
                stdin=subprocess.PIPE,
                # No console window on Windows
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if _native_hotkeys_pkg.is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
                # Reset signal handlers in child so SIGTERM works cleanly
                start_new_session=on_posix,
            )
        except OSError as exc:
            self._failed = True
            self._error_message = f"Failed to spawn {self.platform_name} binary: {exc}"
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise RuntimeError(self._error_message) from exc

        # close the pinned fd now that Popen has spawned.
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

        # reset the liveness timestamps so the freshly-spawned
        self._last_event_received_at = time.time()
        self._last_pong_received_at = 0.0

        # Start the reader thread
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"{self.platform_name}-hotkey-reader",
            daemon=True,
        )
        self._reader_thread.start()

        # start (or restart) the liveness watchdog thread.
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop_event.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name=f"{self.platform_name}-hotkey-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def _compute_native_log_path(self) -> Path | None:
        """Resolve the diagnostic log path passed to the native binary
        via ``--log-file <path>``.

        The path is ``<config_dir>/logs/native-<backend>.log`` (stable,
        no PID) where ``<backend>`` is ``self.platform_name.lower()``
        and ``<config_dir>`` is the canonical config dir
        (``paths._config_dir``, honors ``VOICE_TYPER_CONFIG_DIR`` and
        the Windows ``%APPDATA%`` location). The directory is created
        on first call (parents=True, exist_ok=True). The file itself is
        NOT created here, the native binary opens it with fopen("a").

        A stable name bounds storage to ONE file per backend: the binary
        appends a few lines per launch, and the existing session-start
        sweep (7-day age / 25 MB size) caps it. The previous per-PID
        name (``native-<backend>-<pid>.log``) created one 1 KB file per
        launch; legacy orphans are deleted best-effort here (both the
        canonical and the legacy home logs dirs).

        Returns ``None`` if the path can't be resolved. In that case
        the binary is spawned without ``--log-file`` and its
        diagnostics go to stderr only (which the Python parent merges
        into stdout via STDERR=STDOUT).

        Memoised in ``self._native_log_path`` so the same path is reused
        across respawns and restarts (the binary appends).
        """
        if self._native_log_path is not None:
            return self._native_log_path
        # Canonical location first (single source of truth for all logs).
        log_dir = _resolve_canonical_logs_dir()
        if log_dir is None:
            log_dir = _legacy_home_logs_dir()
        if log_dir is None:
            return None
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Can't create the log dir (read-only home, sandbox, etc.).
            return None
        backend = (self.platform_name or "native").lower()
        path = log_dir / f"native-{backend}.log"
        try:
            _sweep_legacy_per_pid_logs(keep=path)
        except Exception as exc:  # deliberate broad catch: cleanup must never block the spawn
            log.debug("[NATIVE-HOTKEY] legacy native-log sweep failed: %s", exc)
        self._native_log_path = path
        return path
