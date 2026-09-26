"""Wayland hotkey backend helpers."""

import contextlib
import os
import threading
from collections.abc import Callable
from typing import Any

from voice_typer.server.branding import APP_NAME

from .base import HotkeyBackend, log
from .pynput_backend import PynputHotkey


class WaylandHotkey(HotkeyBackend):
    """Wayland-compatible hotkey backend using a Unix domain socket."""

    # Per-instance socket path suffix. When ``HotkeyDispatcher``
    @staticmethod
    def _sanitize_role(role: str | None) -> str:
        """Return a filename-safe suffix derived from *role*."""
        if not role:
            return ""
        safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in role).lower().strip("-_")
        return safe

    @staticmethod
    def _socket_path(role: str | None = None) -> str | None:
        """Return the Unix socket path under ``$XDG_RUNTIME_DIR``, or ``None``."""
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        if xdg:
            suffix = WaylandHotkey._sanitize_role(role)
            filename = f"lausu-hotkey-{suffix}.sock" if suffix else "lausu-hotkey.sock"
            return os.path.join(xdg, filename)
        return None

    #  (pyrefly): expose the socket path as a read-only property so
    @property
    def SOCKET_PATH(self) -> str | None:  # noqa: N802, matches existing attr-access call sites
        return self._socket_path(self._role)

    PING_RESPONSE = b"pong\n"
    TOGGLE_RESPONSE = b"toggled\n"

    # No-client grace period: if no IPC client connects to the socket
    NO_CLIENT_GRACE_SECONDS: float = 30.0

    # pynput is started as a belt-and-suspenders fallback for
    PYNPUT_FALLBACK_DEFER_SECONDS: float = 5.0

    def __init__(self, hotkey_str: str, role: str | None = None):
        # ``role`` is a short identifier ("dictation", "esc",
        self._role: str | None = role
        # call super().__init__() so the base class initializes
        super().__init__(hotkey_str)
        self._hotkey_str = hotkey_str
        self._callback: Callable[[], None] | None = None
        # typed as Any, socket is created lazily inside start()
        self._server_socket: Any = None
        self._thread: threading.Thread | None = None
        self._alive = False
        self._pynput_fallback: PynputHotkey | None = None
        self._pynput_timer: threading.Timer | None = None
        # No-client detection: the socket backend is useless if no
        self._client_ever_connected: threading.Event = threading.Event()
        self._no_client_timer: threading.Timer | None = None
        # deferred-pynput-fallback timer. ``None`` when not
        self._pynput_deferred_timer: threading.Timer | None = None
        # Optional callback (title, message) -> None, invoked once when
        self._on_no_client: Callable[[str, str], None] | None = None

    def set_no_client_callback(self, callback: Callable[[str, str], None]) -> None:
        """Register a (title, message) callback fired when no IPC client"""
        self._on_no_client = callback

    def start(self, callback: Callable[[], None]) -> None:
        """Start the Unix socket listener with pynput fallback."""
        self._callback = callback
        self._alive = True
        # Reset the no-client flag on each (re)start so a fresh start()
        self._client_ever_connected.clear()

        # Refuse to use the /tmp fallback when XDG_RUNTIME_DIR is
        if self.SOCKET_PATH is None:
            log.warning(
                "[HOTKEY-WAYLAND] XDG_RUNTIME_DIR unset; Wayland hotkey "
                "socket disabled. Set XDG_RUNTIME_DIR or run via systemd "
                "user session."
            )
            self._start_pynput_fallback()
            return

        # Try Unix socket first
        try:
            self._start_socket_server()
            log.info("[HOTKEY-WAYLAND] Unix socket server started at %s", self.SOCKET_PATH)
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Failed to start socket server: %s", exc)
            self._start_pynput_fallback()
            return

        # Start the no-client grace timer. If no IPC client connects
        self._start_no_client_timer()

        # defer the pynput fallback by
        self._schedule_deferred_pynput_fallback()

    def _schedule_deferred_pynput_fallback(self) -> None:
        """Schedule the pynput fallback to start after a short defer."""
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("DISPLAY"):
            log.debug(
                "[HOTKEY-WAYLAND] Skipping pynput fallback. Wayland session "
                "with no DISPLAY (pynput's X11 backend cannot initialize)"
            )
            return
        if self._pynput_deferred_timer is not None:
            self._pynput_deferred_timer.cancel()
        self._pynput_deferred_timer = threading.Timer(
            self.PYNPUT_FALLBACK_DEFER_SECONDS,
            self._on_pynput_deferred_timeout,
        )
        self._pynput_deferred_timer.daemon = True
        self._pynput_deferred_timer.start()

    def _on_pynput_deferred_timeout(self) -> None:
        """Deferred-fallback timer callback: start pynput if no client yet."""
        if not self._alive:
            return
        if self._client_ever_connected.is_set():
            log.debug("[HOTKEY-WAYLAND] IPC client connected during pynput defer window, suppressing pynput fallback")
            return
        self._start_pynput_fallback_with_timeout()

    def _start_no_client_timer(self) -> None:
        """Start the grace timer that warns if no IPC client ever connects."""
        # Cancel any stale timer from a previous start() cycle (defensive
        if self._no_client_timer is not None:
            self._no_client_timer.cancel()
        self._no_client_timer = threading.Timer(self.NO_CLIENT_GRACE_SECONDS, self._on_no_client_timeout)
        self._no_client_timer.daemon = True
        self._no_client_timer.start()

    def _on_no_client_timeout(self) -> None:
        """Timer callback: no IPC client connected within the grace period."""
        if not self._alive:
            # stop() was called during the grace period, don't warn.
            return
        if self._client_ever_connected.is_set():
            # A client connected between the timer firing and this
            return
        socket_path = self.SOCKET_PATH
        if socket_path is None:
            # (shouldn't happen in practice, but be defensive).
            return
        title = f"{APP_NAME}. Wayland Hotkey Idle"
        message = (
            "Wayland hotkey backend active but no external tool is "
            f"sending commands. Install linux-key-listener, or send "
            f"'toggle' to {socket_path} (e.g. `echo -n toggle | nc -U "
            f"{socket_path}`)."
        )
        log.warning("[HOTKEY-WAYLAND] %s, %s", title, message)
        if self._on_no_client is not None:
            try:
                self._on_no_client(title, message)
            except Exception:
                #  guard: never let a callback failure
                log.warning(
                    "[HOTKEY-WAYLAND] no-client callback raised; warning was logged but tray notification may be lost",
                    exc_info=True,
                )

    def _start_socket_server(self) -> None:
        """Create and bind the Unix domain socket."""
        import socket as _socket
        import stat

        # ``start()`` guards this before calling us, but be defensive
        socket_path = self.SOCKET_PATH
        if socket_path is None:
            raise RuntimeError("XDG_RUNTIME_DIR unset; refusing to use /tmp fallback (/tmp symlink attack)")

        # Clean up stale socket
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        # The socket's parent directory may not exist (e.g. the
        parent_dir = os.path.dirname(socket_path)
        if parent_dir and not os.path.isdir(parent_dir):
            os.makedirs(parent_dir, mode=0o700, exist_ok=True)

        self._server_socket = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        # close bind→chmod TOCTOU window with umask(0o077).
        old_umask = os.umask(0o077)
        try:
            self._server_socket.bind(socket_path)
        finally:
            os.umask(old_umask)
        # PLAT-WAYLAND: restrict socket to owner-only (0o600). Pre-fix
        os.chmod(
            socket_path,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)

        # daemon=True is acceptable because the accept loop
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        """Accept connections and handle commands."""
        while self._alive:
            try:
                conn, _ = self._server_socket.accept()
                # Mark that at least one IPC client has connected, and
                if not self._client_ever_connected.is_set():
                    self._client_ever_connected.set()
                    if self._no_client_timer is not None:
                        self._no_client_timer.cancel()
                        self._no_client_timer = None
                    # cancel the deferred pynput fallback —
                    if self._pynput_deferred_timer is not None:
                        self._pynput_deferred_timer.cancel()
                        self._pynput_deferred_timer = None
                    log.info("[HOTKEY-WAYLAND] First IPC client connected; no-client grace timer canceled.")
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
            except OSError as exc:
                # previously this lost the OSError detail
                if self._alive:
                    log.warning(
                        "[HOTKEY-WAYLAND] Socket accept error: %s",
                        exc,
                        exc_info=True,
                    )
                break

    def _start_pynput_fallback(self) -> None:
        """Start pynput as a direct fallback (no socket)."""
        # _callback may be None if start() was never called with
        if self._callback is None:
            log.warning("[HOTKEY-WAYLAND] Cannot start pynput fallback, no callback registered")
            return
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (direct)")
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Pynput fallback also failed: %s", exc)

    def _start_pynput_fallback_with_timeout(self) -> None:
        """Start pynput with a timeout, kill it if it doesn't respond."""
        # same callback guard as _start_pynput_fallback.
        if self._callback is None:
            log.warning("[HOTKEY-WAYLAND] Cannot start pynput fallback, no callback registered")
            return
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (with timeout)")

            # Set a timer to stop pynput if it doesn't fire within 30s
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
                # Pynput's stop() may raise implementation-specific
                log.debug("[HOTKEY-WAYLAND] pynput fallback stop failed", exc_info=True)
        self._pynput_fallback = None

    def stop(self) -> None:
        """Stop the socket server and any pynput fallback."""
        self._alive = False
        # Cancel the no-client grace timer so it doesn't fire its
        if self._no_client_timer is not None:
            self._no_client_timer.cancel()
            self._no_client_timer = None
        # cancel the deferred pynput fallback timer too, if
        if self._pynput_deferred_timer is not None:
            self._pynput_deferred_timer.cancel()
            self._pynput_deferred_timer = None
        if self._pynput_timer:
            self._pynput_timer.cancel()
            self._pynput_timer = None
        if self._pynput_fallback:
            self._stop_pynput_fallback()
        if self._server_socket:
            with contextlib.suppress(Exception):
                self._server_socket.close()
        # join the accept-loop thread so we don't leave a daemon
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        # SOCKET_PATH may be None if XDG_RUNTIME_DIR was unset at
        socket_path = self.SOCKET_PATH
        if socket_path is not None and os.path.exists(socket_path):
            with contextlib.suppress(Exception):
                os.unlink(socket_path)
        log.info("[HOTKEY-WAYLAND] Stopped")

    def is_alive(self) -> bool:
        """Return True if the socket server thread is running."""
        return self._alive and (self._thread is not None and self._thread.is_alive())

    def diagnose(self) -> str:
        """Return diagnostic information about the Wayland hotkey backend."""
        # SOCKET_PATH may be None if XDG_RUNTIME_DIR is unset.
        socket_path = self.SOCKET_PATH
        if socket_path is None:
            socket_desc = "<disabled: XDG_RUNTIME_DIR unset>"
            socket_ok = False
        else:
            socket_desc = socket_path
            socket_ok = os.path.exists(socket_path)
        thread_alive = self._thread is not None and self._thread.is_alive()
        pynput_alive = self._pynput_fallback is not None and self._pynput_fallback.is_alive()
        # Report whether any IPC client has ever connected, the
        client_connected = self._client_ever_connected.is_set()
        return (
            f"WaylandHotkey: socket={socket_desc} (exists={socket_ok}), "
            f"thread_alive={thread_alive}, pynput_fallback={pynput_alive}, "
            f"client_ever_connected={client_connected}"
        )


#  verify-compat alias: some downstream callers and the F20
WaylandHotkeyBackend = WaylandHotkey
