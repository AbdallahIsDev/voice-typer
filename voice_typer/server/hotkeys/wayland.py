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
    ``$XDG_RUNTIME_DIR/voice-typer-hotkey.sock`` for commands like
    ``toggle`` and ``ping``. External tools (systemd, shell scripts,
    wlr-which-key) can send these commands to trigger dictation.

    M-88 (security): when ``$XDG_RUNTIME_DIR`` is unset, the socket
    server is **refused** (returns ``None`` from ``_socket_path()``)
    and the backend falls back to pynput-only. Previously, the code
    fell back to ``/tmp/voice-typer-hotkey.sock`` — a world-writable
    directory where another local user could pre-create a symlink at
    that path, causing ``bind()`` to write to (and ``chmod()`` to
    secure) an attacker-controlled file (classic /tmp symlink attack).
    The clear warning ``XDG_RUNTIME_DIR unset; Wayland hotkey socket
    disabled — set XDG_RUNTIME_DIR or run via systemd user session.``
    is logged so the user knows how to fix the environment.

    Falls back to pynput if the socket fails (or is disabled), with a
    timer-based safety net that stops the pynput listener if it
    doesn't respond within a timeout (it silently fails on Wayland).

    No-client detection: the socket backend is only useful if an
    external tool actually connects to send commands. On aarch64 Linux
    Wayland (where the native evdev binary isn't built — XPLAT-11),
    users get a socket nobody writes to + a pynput fallback that
    silently no-ops; the dictation hotkey appears dead with no
    actionable error. After ``NO_CLIENT_GRACE_SECONDS`` (30s) with no
    IPC client connected, ``start()``'s grace timer fires an
    actionable WARNING (with the socket path + install instructions)
    and invokes the optional ``_on_no_client`` callback so callers
    can surface a desktop notification via ``tray.notify_safety``.
    Register the callback with ``set_no_client_callback``. The timer
    is canceled as soon as the first client connects (see
    ``_accept_loop``).
    """

    @staticmethod
    def _socket_path() -> str | None:
        """Return the Unix socket path under ``$XDG_RUNTIME_DIR``, or ``None``.

        ``$XDG_RUNTIME_DIR`` is the Freedesktop standard for
        per-user runtime files (typically
        ``/run/user/<uid>/voice-typer-hotkey.sock``). It is only
        accessible by the owning UID, eliminating the cross-user
        TOCTOU window between ``bind()`` and ``chmod()`` that existed
        when the path was hardcoded to the world-writable ``/tmp``.

        M-88: returns ``None`` when ``XDG_RUNTIME_DIR`` is unset.
        Callers (``start``, ``stop``, ``diagnose``) must handle the
        ``None`` case — typically by falling back to pynput-only and
        logging the warning. The previous ``/tmp/voice-typer-hotkey.sock``
        fallback was removed because ``/tmp`` is world-writable and
        vulnerable to a symlink attack (another user pre-creates the
        socket path as a symlink to a sensitive file).
        """
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        if xdg:
            return os.path.join(xdg, "voice-typer-hotkey.sock")
        return None

    # RW-6 (pyrefly): expose the socket path as a read-only property so
    # the rest of the class (and tests) can use ``self.SOCKET_PATH``
    # attribute-style access. Previously the code referenced
    # ``self.SOCKET_PATH`` (uppercase) but only a private
    # ``_socket_path()`` staticmethod existed — a real bug that would
    # have raised AttributeError at runtime on every code path. The
    # property delegates to the staticmethod so the logic stays in one
    # place.
    #
    # M-88: the property is now ``str | None`` — ``None`` signals that
    # ``XDG_RUNTIME_DIR`` is unset and the socket is disabled.
    @property
    def SOCKET_PATH(self) -> str | None:  # noqa: N802 — matches existing attr-access call sites
        return self._socket_path()

    PING_RESPONSE = b"pong\n"
    TOGGLE_RESPONSE = b"toggled\n"

    # No-client grace period: if no IPC client connects to the socket
    # within this many seconds, surface an actionable warning. 30s
    # matches the pynput-fallback timeout (see
    # ``_start_pynput_fallback_with_timeout``) so the two warnings
    # (socket has no client + pynput fallback timed out) fire together
    # and give the user a single, coherent "your hotkey isn't working
    # — here's how to fix it" signal rather than two staggered
    # warnings 30s apart.
    NO_CLIENT_GRACE_SECONDS: float = 30.0

    def __init__(self, hotkey_str: str):
        # AC-23: call super().__init__() so the base class initializes
        # ``self.hotkey_str`` (the public attribute used by every other
        # backend via the ``HotkeyBackend`` interface),
        # ``self._on_release_callback``, and ``self._toggle_on_keyup``.
        # Previously this subclass set only ``self._hotkey_str`` (with
        # underscore) and skipped ``super().__init__()``, which broke
        # the abstract contract: polymorphic code that did
        # ``backend.hotkey_str`` raised ``AttributeError`` on a
        # ``WaylandHotkey`` instance. Keep ``self._hotkey_str`` as a
        # private alias for backward compat with the two internal
        # ``PynputHotkey(self._hotkey_str)`` call sites below.
        super().__init__(hotkey_str)
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
        # No-client detection: the socket backend is useless if no
        # external tool (systemd, wlr-which-key, shell wrapper) ever
        # connects to send "toggle"/"ping" commands. On aarch64 Linux
        # Wayland (where the native evdev binary isn't built), users
        # get a socket nobody writes to + a pynput fallback that
        # silently no-ops — the dictation hotkey appears dead with no
        # actionable error. We track whether ANY client has ever
        # connected and, after a grace period, surface an actionable
        # warning so the user knows how to fix it (install
        # linux-key-listener, or send commands to the socket path).
        # ``threading.Event`` gives us atomic set/is_set across the
        # accept-loop thread, the timer thread, and the main thread.
        self._client_ever_connected: threading.Event = threading.Event()
        self._no_client_timer: threading.Timer | None = None
        # Optional callback (title, message) -> None, invoked once when
        # the no-client grace period elapses. Callers that own a tray
        # reference (e.g. the app) can register
        # ``backend.set_no_client_callback(app.tray.notify_safety)`` so
        # the warning surfaces as a desktop notification in addition to
        # the log line. Defaults to None (log-only).
        self._on_no_client: Callable[[str, str], None] | None = None

    def set_no_client_callback(self, callback: Callable[[str, str], None]) -> None:
        """Register a (title, message) callback fired when no IPC client
        connects to the Wayland socket within the grace period.

        Use this to surface the "Wayland hotkey backend active but no
        external tool is sending commands" warning as a desktop
        notification (e.g. wire it to ``app.tray.notify_safety``). The
        callback is invoked at most once per ``start()`` cycle, from the
        no-client timer thread. If a client connects before the grace
        period elapses, the callback is never invoked.

        The callback is optional — without it, the warning is logged
        only. This keeps the WaylandHotkey backend decoupled from the
        tray module (which lives in a different layer and would create
        an import cycle if imported here).
        """
        self._on_no_client = callback

    def start(self, callback: Callable[[], None]) -> None:
        """Start the Unix socket listener with pynput fallback."""
        self._callback = callback
        self._alive = True
        # Reset the no-client flag on each (re)start so a fresh start()
        # cycle gets a fresh 30s grace period. stop() cancels the timer
        # but doesn't clear the Event; we clear it here so a restart
        # after a successful first run doesn't carry over the "already
        # connected" state from the previous cycle.
        self._client_ever_connected.clear()

        # M-88: refuse to use the /tmp fallback when XDG_RUNTIME_DIR is
        # unset. /tmp is world-writable and another local user could
        # pre-create a symlink at the socket path, causing bind() to
        # write to (and chmod() to secure) an attacker-controlled file.
        # Fall back to pynput-only with a clear, actionable warning.
        if self.SOCKET_PATH is None:
            log.warning(
                "[HOTKEY-WAYLAND] XDG_RUNTIME_DIR unset; Wayland hotkey "
                "socket disabled — set XDG_RUNTIME_DIR or run via systemd "
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
        # within NO_CLIENT_GRACE_SECONDS, surface an actionable warning
        # so the user knows the socket is listening but nobody is
        # sending commands (common on aarch64 Linux Wayland where the
        # native evdev binary isn't built). The timer is canceled in
        # _accept_loop as soon as the first client connects.
        self._start_no_client_timer()

        # Also start pynput as a fallback — on some Wayland setups,
        # XWayland or xdotool may make it partially work. Kill it
        # after a timeout if it doesn't fire.
        self._start_pynput_fallback_with_timeout()

    def _start_no_client_timer(self) -> None:
        """Start the grace timer that warns if no IPC client ever connects.

        The timer fires once after ``NO_CLIENT_GRACE_SECONDS``. When it
        fires, if ``_client_ever_connected`` is still unset, we log an
        actionable WARNING and invoke the optional ``_on_no_client``
        callback (so callers can surface a desktop notification via
        ``tray.notify_safety``). The timer is canceled from
        ``_accept_loop`` as soon as the first client connects, and from
        ``stop()`` during teardown.
        """
        # Cancel any stale timer from a previous start() cycle (defensive
        # — start() clears the timer ref in stop(), but be robust to
        # double-start).
        if self._no_client_timer is not None:
            self._no_client_timer.cancel()
        self._no_client_timer = threading.Timer(self.NO_CLIENT_GRACE_SECONDS, self._on_no_client_timeout)
        self._no_client_timer.daemon = True
        self._no_client_timer.start()

    def _on_no_client_timeout(self) -> None:
        """Timer callback: no IPC client connected within the grace period.

        Log an actionable warning and (if registered) invoke the
        ``_on_no_client`` callback so the tray can surface a desktop
        notification. Idempotent: if a client connected between the
        timer firing and this callback running, the Event is set and we
        silently no-op.
        """
        if not self._alive:
            # stop() was called during the grace period — don't warn.
            return
        if self._client_ever_connected.is_set():
            # A client connected between the timer firing and this
            # callback running — no warning needed.
            return
        socket_path = self.SOCKET_PATH
        if socket_path is None:
            # XDG_RUNTIME_DIR was unset between start() and now
            # (shouldn't happen in practice, but be defensive).
            return
        title = "Voice Typer — Wayland Hotkey Idle"
        message = (
            "Wayland hotkey backend active but no external tool is "
            f"sending commands. Install linux-key-listener, or send "
            f"'toggle' to {socket_path} (e.g. `echo -n toggle | nc -U "
            f"{socket_path}`)."
        )
        log.warning("[HOTKEY-WAYLAND] %s — %s", title, message)
        if self._on_no_client is not None:
            try:
                self._on_no_client(title, message)
            except Exception:
                # PVT-G5-091-style guard: never let a callback failure
                # crash the timer thread (which would silently lose the
                # no-client signal forever). The log.warning above
                # already surfaced the issue; the callback is a
                # best-effort tray notification.
                log.warning(
                    "[HOTKEY-WAYLAND] no-client callback raised; warning was logged but tray notification may be lost",
                    exc_info=True,
                )

    def _start_socket_server(self) -> None:
        """Create and bind the Unix domain socket."""
        import socket as _socket
        import stat

        # M-88: SOCKET_PATH is None when XDG_RUNTIME_DIR is unset.
        # ``start()`` guards this before calling us, but be defensive
        # in case of direct calls — refuse to bind anywhere under /tmp.
        socket_path = self.SOCKET_PATH
        if socket_path is None:
            raise RuntimeError("XDG_RUNTIME_DIR unset; refusing to use /tmp fallback (M-88: /tmp symlink attack)")

        # Clean up stale socket
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        self._server_socket = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        # CR-107: close bind→chmod TOCTOU window with umask(0o077).
        # Use the local ``socket_path`` (already None-checked above per M-88)
        # rather than ``self.SOCKET_PATH`` (which is ``str | None`` after the
        # M-88 fix and would be unsafe to pass to ``bind()`` without a guard).
        old_umask = os.umask(0o077)
        try:
            self._server_socket.bind(socket_path)
        finally:
            os.umask(old_umask)
        # PLAT-WAYLAND: restrict socket to owner-only (0o600). Pre-fix
        # this was 0o666 (world-writable) which allowed any local user
        # to send "toggle" commands to the socket. The socket is only
        # used by the same user's wtype/ydotool wrapper script, so
        # group/other access is unnecessary.
        os.chmod(
            socket_path,
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
                # Mark that at least one IPC client has connected, and
                # cancel the no-client grace timer so we don't fire the
                # "no external tool is sending commands" warning after
                # a late-connecting client. The Event is atomic so this
                # is safe against the timer thread's concurrent
                # is_set() check.
                if not self._client_ever_connected.is_set():
                    self._client_ever_connected.set()
                    if self._no_client_timer is not None:
                        self._no_client_timer.cancel()
                        self._no_client_timer = None
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
                # PVT-G5-091: previously this lost the OSError detail
                # entirely. Include ``exc`` + ``exc_info=True`` so the
                # operator can see errno / strerror (e.g. EBADF vs.
                # ECONNABORTED vs. EMFILE).
                if self._alive:
                    log.warning(
                        "[HOTKEY-WAYLAND] Socket accept error: %s",
                        exc,
                        exc_info=True,
                    )
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
        # Cancel the no-client grace timer so it doesn't fire its
        # warning after stop() has been called (e.g. during app
        # shutdown that happens within the 30s grace period). The
        # timer's callback also re-checks ``self._alive`` so even if it
        # has already fired, it won't surface the warning.
        if self._no_client_timer is not None:
            self._no_client_timer.cancel()
            self._no_client_timer = None
        if self._pynput_timer:
            self._pynput_timer.cancel()
            self._pynput_timer = None
        if self._pynput_fallback:
            self._stop_pynput_fallback()
        if self._server_socket:
            with contextlib.suppress(Exception):
                self._server_socket.close()
        # M-88: SOCKET_PATH may be None if XDG_RUNTIME_DIR was unset at
        # start() time — in that case the socket was never created, so
        # there is nothing to unlink. Guard against None to avoid an
        # AttributeError / TypeError from os.path.exists(None).
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
        # M-88: SOCKET_PATH may be None if XDG_RUNTIME_DIR is unset.
        socket_path = self.SOCKET_PATH
        if socket_path is None:
            socket_desc = "<disabled: XDG_RUNTIME_DIR unset>"
            socket_ok = False
        else:
            socket_desc = socket_path
            socket_ok = os.path.exists(socket_path)
        thread_alive = self._thread is not None and self._thread.is_alive()
        pynput_alive = self._pynput_fallback is not None and self._pynput_fallback.is_alive()
        # Report whether any IPC client has ever connected — the
        # no-client warning fires after NO_CLIENT_GRACE_SECONDS if this
        # is False, so surfacing it in diagnose() lets the user (and
        # the onboarding flow) tell apart "socket listening + clients
        # active" from "socket listening but nobody sending commands".
        client_connected = self._client_ever_connected.is_set()
        return (
            f"WaylandHotkey: socket={socket_desc} (exists={socket_ok}), "
            f"thread_alive={thread_alive}, pynput_fallback={pynput_alive}, "
            f"client_ever_connected={client_connected}"
        )


# CR-66 / verify-compat alias: some downstream callers and the F20
# verify command import the class as ``WaylandHotkeyBackend``. Keep
# both names available — the canonical name remains ``WaylandHotkey``
# (matches the existing ``WaylandHotkey`` references in factory.py,
# native_adapter.py, __init__.py and the existing test suite).
WaylandHotkeyBackend = WaylandHotkey
