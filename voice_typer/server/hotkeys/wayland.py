"""Wayland-compatible hotkey backend (Unix domain socket + pynput fallback).

Split out from the original ``hotkeys.py`` god-file in Phase 4.5
(ARCH-045).
"""

import contextlib
import os
import threading
from collections.abc import Callable
from typing import Any

from .base import HotkeyBackend, log
from .pynput_backend import PynputHotkey


class WaylandHotkey(HotkeyBackend):
    """Wayland-compatible hotkey backend using a Unix domain socket.

    On Wayland compositors, pynput's X11-based keyboard listener doesn't
    work. This backend listens on a Unix domain socket at
    ``$XDG_RUNTIME_DIR/voice-typer-hotkey.sock`` (falling back to
    ``/tmp/voice-typer-hotkey.sock``) for commands like ``toggle`` and
    ``ping``. External tools (systemd, shell scripts, wlr-which-key)
    can send these commands to trigger dictation.

    Falls back to pynput if the socket fails, with a timer-based
    safety net that stops the pynput listener if it doesn't respond
    within a timeout (it silently fails on Wayland).
    """

    @staticmethod
    def _socket_path() -> str:
        """Return the Unix socket path, preferring ``$XDG_RUNTIME_DIR``.

        ``$XDG_RUNTIME_DIR`` is the Freedesktop standard for
        per-user runtime files (typically
        ``/run/user/<uid>/voice-typer-hotkey.sock``). It is only
        accessible by the owning UID, eliminating the cross-user
        TOCTOU window between ``bind()`` and ``chmod()`` that existed
        when the path was hardcoded to the world-writable ``/tmp``.

        Falls back to ``/tmp/voice-typer-hotkey.sock`` when
        ``$XDG_RUNTIME_DIR`` is not set.
        """
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        if xdg:
            return os.path.join(xdg, "voice-typer-hotkey.sock")
        return "/tmp/voice-typer-hotkey.sock"

    # RW-6 (pyrefly): expose the socket path as a read-only property so
    # the rest of the class (and tests) can use ``self.SOCKET_PATH``
    # attribute-style access. Previously the code referenced
    # ``self.SOCKET_PATH`` (uppercase) but only a private
    # ``_socket_path()`` staticmethod existed — a real bug that would
    # have raised AttributeError at runtime on every code path. The
    # property delegates to the staticmethod so the logic stays in one
    # place.
    @property
    def SOCKET_PATH(self) -> str:  # noqa: N802 — matches existing attr-access call sites
        return self._socket_path()

    PING_RESPONSE = b"pong\n"
    TOGGLE_RESPONSE = b"toggled\n"

    def __init__(self, hotkey_str: str):
        self._hotkey_str = hotkey_str
        self._callback: Callable[[], None] | None = None
        # TASK-10: typed as Any — socket is created lazily inside start()
        # and remains None if the socket bind fails. _accept_loop checks
        # self._alive before touching this socket, but pyrefly cannot
        # prove the narrowing across the thread boundary.
        self._server_socket: Any = None
        self._thread: threading.Thread | None = None
        self._alive = False
        self._pynput_fallback: PynputHotkey | None = None
        self._pynput_timer: threading.Timer | None = None

    def start(self, callback: Callable[[], None]) -> None:
        """Start the Unix socket listener with pynput fallback."""
        self._callback = callback
        self._alive = True

        # Try Unix socket first
        try:
            self._start_socket_server()
            log.info("[HOTKEY-WAYLAND] Unix socket server started at %s", self.SOCKET_PATH)
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Failed to start socket server: %s", exc)
            self._start_pynput_fallback()
            return

        # Also start pynput as a fallback — on some Wayland setups,
        # XWayland or xdotool may make it partially work. Kill it
        # after a timeout if it doesn't fire.
        self._start_pynput_fallback_with_timeout()

    def _start_socket_server(self) -> None:
        """Create and bind the Unix domain socket."""
        import socket as _socket
        import stat

        # Clean up stale socket
        if os.path.exists(self.SOCKET_PATH):
            os.unlink(self.SOCKET_PATH)

        self._server_socket = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        self._server_socket.bind(self.SOCKET_PATH)
        # PLAT-WAYLAND: restrict socket to owner-only (0o600). Pre-fix
        # this was 0o666 (world-writable) which allowed any local user
        # to send "toggle" commands to the socket. The socket is only
        # used by the same user's wtype/ydotool wrapper script, so
        # group/other access is unnecessary.
        os.chmod(
            self.SOCKET_PATH,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)

        # RACE-008: daemon=True is acceptable because the accept loop
        # only handles incoming IPC connections (no critical cleanup).
        # stop() closes the listening socket, which causes accept() to
        # raise and the thread exits. On force-kill, the OS reclaims
        # the socket FD automatically.
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        """Accept connections and handle commands."""
        while self._alive:
            try:
                conn, _ = self._server_socket.accept()
                try:
                    data = conn.recv(1024).decode("utf-8").strip()
                    if data == "toggle" and self._callback:
                        log.info("[HOTKEY-WAYLAND] Received toggle command")
                        self._callback()
                        conn.sendall(self.TOGGLE_RESPONSE)
                    elif data == "ping":
                        conn.sendall(self.PING_RESPONSE)
                    else:
                        conn.sendall(b"unknown command\n")
                finally:
                    conn.close()
            except TimeoutError:
                continue
            except OSError:
                if self._alive:
                    log.warning("[HOTKEY-WAYLAND] Socket accept error")
                break

    def _start_pynput_fallback(self) -> None:
        """Start pynput as a direct fallback (no socket)."""
        # TASK-10: _callback may be None if start() was never called with
        # one — guard before forwarding to PynputHotkey.start(), which
        # has a non-Optional callback contract.
        if self._callback is None:
            log.warning("[HOTKEY-WAYLAND] Cannot start pynput fallback — no callback registered")
            return
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (direct)")
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Pynput fallback also failed: %s", exc)

    def _start_pynput_fallback_with_timeout(self) -> None:
        """Start pynput with a timeout — kill it if it doesn't respond."""
        # TASK-10: same callback guard as _start_pynput_fallback.
        if self._callback is None:
            log.warning("[HOTKEY-WAYLAND] Cannot start pynput fallback — no callback registered")
            return
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (with timeout)")

            # Set a timer to stop pynput if it doesn't fire within 30s
            # On Wayland, pynput usually silently fails — the timer
            # cleans it up so it doesn't waste resources.
            self._pynput_timer = threading.Timer(30.0, self._stop_pynput_fallback)
            self._pynput_timer.daemon = True
            self._pynput_timer.start()
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Pynput fallback failed: %s", exc)

    def _stop_pynput_fallback(self) -> None:
        """Stop the pynput fallback if it's still running."""
        if self._pynput_fallback and self._pynput_fallback.is_alive():
            try:
                self._pynput_fallback.stop()
                log.info("[HOTKEY-WAYLAND] Pynput fallback stopped (timeout)")
            except Exception:
                pass
        self._pynput_fallback = None

    def stop(self) -> None:
        """Stop the socket server and any pynput fallback."""
        self._alive = False
        if self._pynput_timer:
            self._pynput_timer.cancel()
            self._pynput_timer = None
        if self._pynput_fallback:
            self._stop_pynput_fallback()
        if self._server_socket:
            with contextlib.suppress(Exception):
                self._server_socket.close()
        if os.path.exists(self.SOCKET_PATH):
            with contextlib.suppress(Exception):
                os.unlink(self.SOCKET_PATH)
        log.info("[HOTKEY-WAYLAND] Stopped")

    def is_alive(self) -> bool:
        """Return True if the socket server thread is running."""
        return self._alive and (self._thread is not None and self._thread.is_alive())

    def diagnose(self) -> str:
        """Return diagnostic information about the Wayland hotkey backend."""
        socket_ok = os.path.exists(self.SOCKET_PATH)
        thread_alive = self._thread is not None and self._thread.is_alive()
        pynput_alive = self._pynput_fallback is not None and self._pynput_fallback.is_alive()
        return (
            f"WaylandHotkey: socket={self.SOCKET_PATH} (exists={socket_ok}), "
            f"thread_alive={thread_alive}, pynput_fallback={pynput_alive}"
        )
