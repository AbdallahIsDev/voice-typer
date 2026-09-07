"""Native hotkey backend — {name}."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

log = logging.getLogger(__name__)


class _SpawnMixin:
    # Human-readable backend name used in log messages. Provided by the
    # composing backend class (``_core.py`` sets ``platform_name: str =
    # "subprocess"``); declared here so the mixin's own methods typecheck
    # (same pattern as ``_ReaderMixin`` in ``_reader.py``).
    platform_name: str

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

        Mitigation (POSIX only — see Windows limitation below):

        1. Open the file with ``os.open(path, O_RDONLY | O_CLOEXEC)``
           BEFORE the verify, pinning the inode at that moment.
        2. Run the existing SHA-256 verify (unchanged — uses
           ``path.read_bytes()`` so the existing tests' patch of
           ``verify_native_binary_or_skip`` continues to take effect).
        3. After verify, ``fstat`` the fd → capture
           ``(st_dev, st_ino, st_mtime_ns, st_size)``. This is the
           stat of the inode the fd pinned.
        4. Just before ``Popen``, ``os.stat`` the path and compare to
           the fstat. If the quartet differs, the file was swapped or
           modified between the os.open and the Popen — refuse to
           spawn.

        The fd does NOT need to be the same inode as what the verify
        read. If the file was swapped between os.open and verify, the
        os.stat check at step 4 catches it (path's stat ≠ fd's stat).
        If the file was swapped between verify and os.stat, the
        os.stat check catches it (path's stat ≠ fd's stat, because fd
        still pins the original inode). The only residual TOCTOU is
        between os.stat and the execve inside Popen — a sub-microsecond
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
        # from .spec_parser at module load; base.py also imports from
        # .spec_parser — keeping this local avoids any chance of a
        # cycle if binary_path.py grows additional deps).
        from .binary_path import verify_native_binary_or_skip

        # on POSIX, open the file with O_RDONLY | O_CLOEXEC
        # BEFORE the verify so the fd pins the inode. The fd is held
        # across the verify and Popen so we can detect tampering via
        # a stat mismatch (path-stat vs fd-stat) just before Popen.
        # On Windows the fd-based check is skipped (see the docstring's
        # "Windows limitation" section).
        on_posix = _native_hotkeys_pkg.is_macos() or _native_hotkeys_pkg.is_linux()
        fd: int | None = None
        pinned_stat: tuple | None = None
        if on_posix:
            try:
                # ``O_CLOEXEC`` does not exist on Windows (pre-Python
                # 3.13); ``getattr`` resolves it to 0 there so tests
                # that monkeypatch a POSIX platform while running on
                # Windows take the fd-pinning path without raising
                # AttributeError.
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
                f"an untrusted binary — falling back to the legacy backend."
            )
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            return

        # capture the pinned inode's stat for the pre-Popen
        # check. fstat reads metadata directly from the fd (no path
        # re-resolution), so this is the stat of the inode the fd
        # pinned at os.open time — unaffected by any later path swap.
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
        # from the pinned fd's stat, the file was swapped or modified
        # between os.open and now (which includes the verify window).
        # Refuse to spawn — this is the TOCTOU gate.
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
                    f"between verify and Popen (path={self._binary_path}) — "
                    f"possible TOCTOU swap. Refusing to spawn an untrusted binary."
                )
                log.error("[NATIVE-HOTKEY] %s", self._error_message)
                return

        # A backend spawns its own native listener process here only when
        # it is NOT delegated. ``HotkeyDispatcher`` creates three backend
        # instances (dictation / ESC / repaste) but marks the ESC and
        # repaste ones ``_delegated=True``: their ``start()`` skips
        # spawning, and their specs are matched as extra matchers on the
        # shared dictation backend's event stream (the binary emits ALL
        # keystroke events on stdout; the Python side does the matching).
        # So on native platforms exactly ONE native listener process,
        # reader thread, IPC pipe, and TOCTOU-verify + watchdog cycle
        # serves all three roles. The per-role three-process model
        # applies only when the factory falls back to non-poolable
        # backends. See the architecture note in
        # ``hotkey_dispatcher.HotkeyDispatcher``.
        cmd = [str(self._binary_path), self.hotkey_str]
        # Pass the binary its diagnostic log path (``--log-file``): the
        # native binaries (linux/windows/macos key-listeners) parse the
        # flag and append timestamped init / permission / hook-install
        # diagnostics there, giving support bundles a native-side trace
        # for hotkey problems. Error-tolerant by design: diagnostics
        # must NEVER break the spawn — if the path can't be computed we
        # spawn without the flag (the binary's stderr still reaches the
        # parent via the merged stdout pipe).
        native_log_path: Path | None
        try:
            native_log_path = self._compute_native_log_path()
        except Exception as exc:  # deliberate broad catch: logging setup must never block the spawn
            log.debug(
                "[NATIVE-HOTKEY] %s native log path computation failed — spawning without --log-file: %s",
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
                # liveness watchdog can write ``PING\n`` to the binary's
                # stdin.  The binary is expected to respond with ``PONG\n``;
                # if it doesn't implement the PING/PONG protocol, the
                # watchdog's ``_pong_supported`` flag stays False and
                # respawn-on-PONG-absence is suppressed (see
                # ``_watchdog_loop`` for the rationale).
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
        # The child has its own reference to the binary via the
        # execve, so the parent's fd is no longer needed. Closing
        # here avoids leaking fds across respawns.
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

        # reset the liveness timestamps so the freshly-spawned
        # binary gets a full 60s grace period before the watchdog
        # considers it hung.  ``_pong_supported`` is NOT reset here —
        # once we've seen a PONG from any spawn of this binary, we know
        # it supports the protocol and future spawns should too.
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
        # The watchdog writes ``PING\n`` to the binary's stdin every
        # 30s and respawns the binary if it stops responding.  We use
        # a dedicated ``_watchdog_stop_event`` (separate from
        # ``_stop_event``) so the watchdog can be torn down
        # independently in ``stop()`` after the reader has exited.
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop_event.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name=f"{self.platform_name}-hotkey-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def _compute_native_log_path(self) -> Path | None:
        """Resolve the per-session diagnostic log path passed to the
        native binary via ``--log-file <path>``.

        The path is ``~/.voice-typer/logs/native-<backend>-<pid>.log``
        where ``<backend>`` is ``self.platform_name.lower()`` and
        ``<pid>`` is the current process's PID. The directory is created
        on first call (parents=True, exist_ok=True). The file itself is
        NOT created here — the native binary opens it with fopen("a").

        Returns ``None`` if the path can't be resolved (e.g. ``HOME``
        unset on POSIX, or ``USERPROFILE`` unset on Windows). In that
        case the binary is spawned without ``--log-file`` and its
        diagnostics go to stderr only (which the Python parent merges
        into stdout via STDERR=STDOUT).

        Memoised in ``self._native_log_path`` so the same path is reused
        across respawns (the binary appends, so all respawns of one
        backend land in the same file).
        """
        if self._native_log_path is not None:
            return self._native_log_path
        # Resolve the user's home directory. On POSIX this is ``$HOME``;
        # on Windows it's ``%USERPROFILE%`` (which ``Path.home`` reads
        # via ``os.path.expanduser``). Fall back to None on failure so
        # we don't crash the spawn just because logging can't be set up.
        try:
            home = Path.home()
        except (RuntimeError, OSError):
            return None
        if not str(home) or str(home) == ".":
            # ``Path.home()`` returns ``.`` when ``HOME`` is unset on
            # some POSIX systems — treat that as "no home available".
            return None
        log_dir = home / ".voice-typer" / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Can't create the log dir (read-only home, sandbox, etc.).
            # Spawn without --log-file — the binary's stderr still goes
            # to the parent's merged stdout pipe.
            return None
        backend = (self.platform_name or "native").lower()
        path = log_dir / f"native-{backend}-{os.getpid()}.log"
        self._native_log_path = path
        return path
