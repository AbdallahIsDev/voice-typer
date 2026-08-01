"""Regression tests for the hotkeys subsystem.

Covers four findings:

IN-24 (Critical): ``WaylandHotkey`` socket-path collision. When
``HotkeyDispatcher`` creates three backends (dictation / ESC /
repaste) on a Wayland session, each backend used to bind the SAME
socket path (``$XDG_RUNTIME_DIR/voice-typer-hotkey.sock``). The
second ``start()`` would ``os.unlink`` the first backend's socket
and bind a new one — silently killing the first backend's IPC
listener. The fix: each backend gets a per-role suffix
(``voice-typer-hotkey-{role}.sock``).

IN-27 (Medium): ``_NativeBackendAdapter._on_permission_granted`` did
not stop the legacy backend before restarting native, leaving both
backends running simultaneously (double-fire on the same keypress).
The fix: stop the legacy backend BEFORE restarting native.

IN-25 / IN-26 (Windows code): statically analyzed on Linux — the
``_hook_callback_queue`` worker thread and the modifier-VK matching
in ``_hook_proc`` are exercised via mock-based tests that don't
require Windows.

These tests run on Linux (the orchestrator's acceptance platform).
Windows-specific code paths are mocked.
"""

from __future__ import annotations

import os
import socket as _socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# --------------------------------------------------------------------------- #
# IN-24: WaylandHotkey per-instance socket path
# --------------------------------------------------------------------------- #


def _make_tmp_xdg(tmp_path: Path) -> str:
    """Return a tmp dir suitable for ``$XDG_RUNTIME_DIR``."""
    xdg = tmp_path / "xdg-runtime"
    xdg.mkdir(mode=0o700, exist_ok=True)
    return str(xdg)


@pytest.fixture
def xdg_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``$XDG_RUNTIME_DIR`` to a per-test tmp dir."""
    xdg = _make_tmp_xdg(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", xdg)
    return xdg


class TestWaylandSocketPathPerInstance:
    """IN-24: each ``WaylandHotkey`` instance gets its own socket path.

    Without per-instance paths, the three backends created by
    ``HotkeyDispatcher`` (dictation / ESC / repaste) would collide on
    the same ``voice-typer-hotkey.sock`` — the second ``start()``
    unlinks the first backend's socket and the third unlinks the
    second's, leaving only the last backend's socket alive.
    """

    def test_no_role_uses_historical_path(self, xdg_runtime: str) -> None:
        """A backend constructed without ``role`` uses the historical
        ``voice-typer-hotkey.sock`` path (backward compat with tests
        and direct construction)."""
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        backend = WaylandHotkey("<f8>")
        assert backend.SOCKET_PATH is not None
        assert backend.SOCKET_PATH.endswith("voice-typer-hotkey.sock")
        assert "voice-typer-hotkey-" not in backend.SOCKET_PATH

    def test_dictation_role_uses_suffixed_path(self, xdg_runtime: str) -> None:
        """A backend constructed with ``role="dictation"`` uses
        ``voice-typer-hotkey-dictation.sock``."""
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        backend = WaylandHotkey("<f8>", role="dictation")
        assert backend.SOCKET_PATH is not None
        assert backend.SOCKET_PATH.endswith("voice-typer-hotkey-dictation.sock")

    def test_esc_role_uses_suffixed_path(self, xdg_runtime: str) -> None:
        """A backend constructed with ``role="esc"`` uses
        ``voice-typer-hotkey-esc.sock``."""
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        backend = WaylandHotkey("<esc>", role="esc")
        assert backend.SOCKET_PATH is not None
        assert backend.SOCKET_PATH.endswith("voice-typer-hotkey-esc.sock")

    def test_repaste_role_uses_suffixed_path(self, xdg_runtime: str) -> None:
        """A backend constructed with ``role="repaste"`` uses
        ``voice-typer-hotkey-repaste.sock``."""
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        backend = WaylandHotkey("<f6>", role="repaste")
        assert backend.SOCKET_PATH is not None
        assert backend.SOCKET_PATH.endswith("voice-typer-hotkey-repaste.sock")

    def test_three_roles_produce_three_distinct_paths(self, xdg_runtime: str) -> None:
        """The three roles used by ``HotkeyDispatcher`` produce three
        DISTINCT socket paths — no collision.

        This is the core IN-24 regression: before the fix, all three
        backends shared the same path and the second ``start()``
        unlinked the first's socket.
        """
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        paths = set()
        for role in ("dictation", "esc", "repaste"):
            backend = WaylandHotkey("<f8>", role=role)
            assert backend.SOCKET_PATH is not None
            paths.add(backend.SOCKET_PATH)
        assert len(paths) == 3, f"Expected 3 distinct socket paths for dictation/esc/repaste; got {len(paths)}: {paths}"

    def test_role_sanitized_to_filename_safe(self, xdg_runtime: str) -> None:
        """Hostile / mistyped roles are sanitized to filename-safe
        characters so they can't escape ``$XDG_RUNTIME_DIR`` or inject
        path separators."""
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        # Path separators and special chars are replaced with '-'.
        backend = WaylandHotkey("<f8>", role="../../etc/passwd")
        path = backend.SOCKET_PATH
        assert path is not None
        # The sanitized role must NOT contain '/' or '..'.
        filename = os.path.basename(path)
        assert "/" not in filename
        assert ".." not in filename
        # Must stay inside $XDG_RUNTIME_DIR.
        assert path.startswith(xdg_runtime + os.sep)

    def test_role_none_and_empty_produce_same_path(self, xdg_runtime: str) -> None:
        """``role=None`` and ``role=""`` both produce the historical
        ``voice-typer-hotkey.sock`` path (no suffix)."""
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        none_backend = WaylandHotkey("<f8>", role=None)
        empty_backend = WaylandHotkey("<f8>", role="")
        assert none_backend.SOCKET_PATH == empty_backend.SOCKET_PATH
        assert none_backend.SOCKET_PATH is not None
        assert none_backend.SOCKET_PATH.endswith("voice-typer-hotkey.sock")

    def test_two_backends_with_different_roles_can_bind_simultaneously(self, xdg_runtime: str) -> None:
        """End-to-end: two ``WaylandHotkey`` instances with different
        roles can both ``start()`` and bind their sockets without one
        unlinking the other's socket.

        Before IN-24, the second ``start()`` would unlink the first
        backend's socket (the ``Clean up stale socket`` step in
        ``_start_socket_server``), killing the first backend's IPC
        listener.
        """
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        dictation = WaylandHotkey("<f8>", role="dictation")
        esc = WaylandHotkey("<esc>", role="esc")

        try:
            dictation.start(lambda: None)
            # Give the accept loop a moment to bind.
            time.sleep(0.1)
            assert dictation.is_alive(), "dictation backend should be alive after start()"

            esc.start(lambda: None)
            time.sleep(0.1)
            assert esc.is_alive(), "esc backend should be alive after start()"

            # Both sockets must exist on disk (the second start() did
            # NOT unlink the first's socket).
            assert dictation.SOCKET_PATH is not None
            assert esc.SOCKET_PATH is not None
            assert dictation.SOCKET_PATH != esc.SOCKET_PATH, (
                "IN-24 regression: both backends bound the same socket path — "
                "the second start() unlinked the first's socket."
            )
            assert os.path.exists(dictation.SOCKET_PATH), (
                f"dictation socket must still exist after esc.start(); path={dictation.SOCKET_PATH}"
            )
            assert os.path.exists(esc.SOCKET_PATH), f"esc socket must exist; path={esc.SOCKET_PATH}"

            # Both backends must respond to pings (proving both accept
            # loops are running, not just bound).
            for backend in (dictation, esc):
                client = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                client.settimeout(2.0)
                client.connect(backend.SOCKET_PATH)  # type: ignore[arg-type]
                client.sendall(b"ping")
                response = client.recv(1024)
                client.close()
                assert response == WaylandHotkey.PING_RESPONSE, (
                    f"backend {backend._role} did not respond to ping; got {response!r}"
                )
        finally:
            dictation.stop()
            esc.stop()


# --------------------------------------------------------------------------- #
# IN-24: Factory threads role through to WaylandHotkey
# --------------------------------------------------------------------------- #


class TestFactoryRolePropagation:
    """IN-24: ``create_hotkey_backend(hotkey_str, role=...)`` passes
    ``role`` to ``WaylandHotkey`` on Wayland sessions."""

    def test_factory_passes_role_to_wayland(self, monkeypatch, tmp_path):
        """On a Wayland session, ``create_hotkey_backend(hotkey, role='esc')``
        constructs a ``WaylandHotkey`` with ``role='esc'``."""
        from voice_typer.server.hotkeys import factory as factory_mod

        # Simulate Linux + Wayland.
        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        # Skip the native backend path so we hit the Wayland branch.
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.create_native_backend",
            lambda hotkey_str: None,
            raising=False,
        )
        xdg = tmp_path / "xdg-runtime"
        xdg.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))

        backend = factory_mod.create_hotkey_backend("<f8>", role="esc")
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        assert isinstance(backend, WaylandHotkey), (
            f"Expected WaylandHotkey on Wayland session; got {type(backend).__name__}"
        )
        assert backend._role == "esc", (
            f"Factory did not propagate role='esc' to WaylandHotkey; got role={backend._role!r}"
        )
        assert backend.SOCKET_PATH is not None
        assert backend.SOCKET_PATH.endswith("voice-typer-hotkey-esc.sock"), (
            f"WaylandHotkey socket path must include the role suffix; got {backend.SOCKET_PATH}"
        )
        backend.stop()

    def test_factory_default_role_none(self, monkeypatch, tmp_path):
        """Without ``role``, the factory constructs a ``WaylandHotkey``
        with ``role=None`` (historical path)."""
        from voice_typer.server.hotkeys import factory as factory_mod

        monkeypatch.setattr(factory_mod, "is_linux", lambda: True)
        monkeypatch.setattr(factory_mod, "is_windows", lambda: False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.create_native_backend",
            lambda hotkey_str: None,
            raising=False,
        )
        xdg = tmp_path / "xdg-runtime"
        xdg.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))

        backend = factory_mod.create_hotkey_backend("<f8>")
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        assert isinstance(backend, WaylandHotkey)
        assert backend._role is None
        assert backend.SOCKET_PATH is not None
        assert backend.SOCKET_PATH.endswith("voice-typer-hotkey.sock")
        backend.stop()


# --------------------------------------------------------------------------- #
# IN-24: _NativeBackendAdapter threads role to legacy WaylandHotkey
# --------------------------------------------------------------------------- #


class TestAdapterRolePropagation:
    """IN-24: ``_NativeBackendAdapter(role=...)`` passes ``role`` to a
    legacy ``WaylandHotkey`` when the native backend fails and the
    adapter swaps to legacy."""

    def test_adapter_stores_role(self):
        """``_NativeBackendAdapter(native, role='esc')`` stores
        ``self._role = 'esc'``."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter

        native = MagicMock()
        native.hotkey_str = "<esc>"
        adapter = _NativeBackendAdapter(native, role="esc")
        assert adapter._role == "esc"

    def test_adapter_default_role_none(self):
        """``_NativeBackendAdapter(native)`` defaults to ``role=None``
        (backward compat with existing tests that construct with one
        arg)."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter

        native = MagicMock()
        native.hotkey_str = "<f8>"
        adapter = _NativeBackendAdapter(native)
        assert adapter._role is None

    def test_adapter_passes_role_to_wayland_legacy(self, monkeypatch, tmp_path):
        """When the adapter swaps to a legacy ``WaylandHotkey`` on a
        Wayland session, it passes ``self._role`` so the legacy
        backend gets the correct socket path."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter, factory

        # Simulate Linux + Wayland.
        monkeypatch.setattr(factory, "is_linux", lambda: True)
        monkeypatch.setattr(factory, "is_windows", lambda: False)
        # native_adapter imports is_linux from the package, not from factory.
        # Patch the package-level binding.
        import voice_typer.server.hotkeys as hotkeys_pkg

        monkeypatch.setattr(hotkeys_pkg, "is_linux", lambda: True)
        monkeypatch.setattr(hotkeys_pkg, "is_windows", lambda: False)

        # native_adapter has its own is_linux wrapper; patch the package.
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        xdg = tmp_path / "xdg-runtime"
        xdg.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))

        native = MagicMock()
        native.hotkey_str = "<esc>"
        native.is_alive.return_value = True
        adapter = _NativeBackendAdapter(native, role="esc")

        # Trigger a legacy backend creation.
        legacy = adapter._create_legacy_backend()
        from voice_typer.server.hotkeys.wayland import WaylandHotkey

        assert isinstance(legacy, WaylandHotkey), (
            f"Expected WaylandHotkey legacy on Wayland; got {type(legacy).__name__}"
        )
        assert legacy._role == "esc", (
            f"Adapter did not propagate role='esc' to legacy WaylandHotkey; got role={legacy._role!r}"
        )
        assert legacy.SOCKET_PATH is not None
        assert legacy.SOCKET_PATH.endswith("voice-typer-hotkey-esc.sock")


# --------------------------------------------------------------------------- #
# IN-27: _on_permission_granted stops legacy before restarting native
# --------------------------------------------------------------------------- #


def _make_mock_native_backend(hotkey_str: str = "<f2>"):
    """Mock SubprocessHotkeyBackend-compatible object."""
    backend = MagicMock()
    backend.hotkey_str = hotkey_str
    backend.diagnose.return_value = "mock native backend"
    backend.is_alive.return_value = True
    backend.start = MagicMock(return_value=None)
    backend.stop = MagicMock(return_value=None)
    backend.set_on_release = MagicMock(return_value=None)
    backend._on_error_callback = None
    backend._on_permanent_failure_callback = None
    return backend


def _make_mock_legacy_backend(hotkey_str: str = "<f2>"):
    """Mock legacy HotkeyBackend."""
    backend = MagicMock()
    backend.hotkey_str = hotkey_str
    backend.is_alive.return_value = True
    backend.start = MagicMock(return_value=None)
    backend.stop = MagicMock(return_value=None)
    backend.set_on_release = MagicMock(return_value=None)
    return backend


class TestPermissionGrantedStopsLegacy:
    """IN-27: ``_on_permission_granted`` must stop the legacy backend
    BEFORE restarting native.

    Before the fix, the legacy backend was left running alongside the
    native backend after a permission-grant recovery — both backends
    would fire the same callback on the same keypress (double-toggle,
    double-ESC-cancel, double-repaste) until the next ``_retry_native``
    cycle (~5 minutes later) cleaned it up.
    """

    def test_permission_granted_stops_legacy_before_native_restart(self, monkeypatch):
        """When ``_on_permission_granted`` is called and the adapter
        is in FALLBACK state (legacy running), the legacy backend must
        be stopped BEFORE ``native.start()`` is called."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter

        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        # Simulate the adapter having fallen back to a legacy backend.
        legacy = _make_mock_legacy_backend()
        adapter._legacy = legacy
        adapter._state = _NativeBackendAdapter._STATE_FALLBACK
        adapter._callback = MagicMock()

        # Track the order of stop() vs start() calls.
        call_order: list[str] = []

        def legacy_stop():
            call_order.append("legacy_stop")

        def native_stop():
            call_order.append("native_stop")

        def native_start(cb):
            call_order.append("native_start")

        legacy.stop.side_effect = legacy_stop
        native.stop.side_effect = native_stop
        native.start.side_effect = native_start

        adapter._on_permission_granted()

        # The legacy backend MUST have been stopped.
        legacy.stop.assert_called_once()
        # The native backend MUST have been (re)started.
        native.start.assert_called_once()
        # The legacy stop must happen BEFORE the native start (so
        # there's no window where both backends are alive).
        assert "legacy_stop" in call_order, f"legacy.stop not called; order={call_order}"
        assert "native_start" in call_order, f"native.start not called; order={call_order}"
        assert call_order.index("legacy_stop") < call_order.index("native_start"), (
            f"IN-27 regression: legacy.stop() must happen BEFORE native.start(); order={call_order}"
        )

    def test_permission_granted_clears_legacy_ref(self, monkeypatch):
        """After ``_on_permission_granted``, ``self._legacy`` must be
        ``None`` (the legacy backend is no longer active)."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter

        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        adapter._legacy = legacy
        adapter._state = _NativeBackendAdapter._STATE_FALLBACK
        adapter._callback = MagicMock()

        adapter._on_permission_granted()

        assert adapter._legacy is None, (
            "IN-27 regression: _legacy must be None after _on_permission_granted "
            "(legacy backend was stopped and replaced by native)."
        )

    def test_permission_granted_no_legacy_does_not_raise(self, monkeypatch):
        """If there's no legacy backend (adapter is already in NATIVE
        state), ``_on_permission_granted`` must not raise."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter

        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        adapter._legacy = None
        adapter._state = _NativeBackendAdapter._STATE_NATIVE
        adapter._callback = MagicMock()

        # Must not raise.
        adapter._on_permission_granted()
        native.start.assert_called_once()

    def test_permission_granted_legacy_stop_failure_does_not_block_restart(self, monkeypatch):
        """If ``legacy.stop()`` raises, the native restart must still
        proceed (best-effort cleanup)."""
        from voice_typer.server.hotkeys import _NativeBackendAdapter

        native = _make_mock_native_backend()
        adapter = _NativeBackendAdapter(native)
        legacy = _make_mock_legacy_backend()
        legacy.stop.side_effect = RuntimeError("legacy stop failed")
        adapter._legacy = legacy
        adapter._state = _NativeBackendAdapter._STATE_FALLBACK
        adapter._callback = MagicMock()

        # Must not raise (the legacy stop failure is logged at debug).
        adapter._on_permission_granted()
        # Native restart must still proceed.
        native.start.assert_called_once()
        # _legacy must still be cleared (we snapped it under the lock).
        assert adapter._legacy is None


# --------------------------------------------------------------------------- #
# IN-25: LL hook callback dispatched to worker thread (Windows, mocked)
# --------------------------------------------------------------------------- #


class TestLLHookCallbackWorker:
    """IN-25: the LL hook proc dispatches callbacks to a dedicated
    worker thread via a bounded queue, so the hook proc returns
    within ~1ms (Windows marks hooks that take longer as unresponsive).

    Windows-specific code is mocked on Linux — these tests verify the
    queue/worker plumbing, not the actual Win32 hook installation.
    """

    def test_hook_callback_queue_initialized_in_init(self):
        """``__init__`` must initialize ``_hook_callback_queue`` and
        ``_hook_callback_thread`` so attribute access before
        ``start()`` doesn't raise."""
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        assert hasattr(backend, "_hook_callback_queue")
        assert hasattr(backend, "_hook_callback_thread")
        assert backend._hook_callback_thread is None
        # Queue must be bounded (so a runaway callback can't OOM).
        assert backend._hook_callback_queue.maxsize > 0

    def test_enqueue_hook_callback_runs_callback_in_worker(self):
        """``_enqueue_hook_callback`` puts the callable on the queue;
        the worker thread runs it asynchronously."""
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend._start_hook_callback_worker()
            fired = threading.Event()

            def cb():
                fired.set()

            backend._enqueue_hook_callback(cb)
            # The worker should run the callback within ~0.5s.
            assert fired.wait(timeout=2.0), (
                "Worker thread did not run the enqueued callback within 2s — "
                "IN-25 regression: callback not dispatched to worker."
            )
        finally:
            backend._stop_event.set()
            backend._enqueue_hook_callback(None)  # sentinel
            if backend._hook_callback_thread is not None:
                backend._hook_callback_thread.join(timeout=2.0)

    def test_enqueue_none_is_noop(self):
        """``_enqueue_hook_callback(None)`` must not enqueue anything
        (the None sentinel is used by ``stop()`` to shut down the
        worker — it must not be treated as a callback)."""
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        # Must not raise.
        backend._enqueue_hook_callback(None)
        # Queue must still be empty (None was not enqueued as a callback).
        assert backend._hook_callback_queue.empty()

    def test_enqueue_drops_on_full_queue(self, caplog):
        """When the queue is full, ``_enqueue_hook_callback`` drops the
        callback and logs a WARNING (does not block the hook proc)."""
        import logging

        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        # Fill the queue to capacity.
        for _ in range(backend._hook_callback_queue.maxsize):
            backend._hook_callback_queue.put_nowait(lambda: None)
        # The next enqueue must not block — it must drop and log.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.hotkeys"):
            backend._enqueue_hook_callback(lambda: None)
        assert any("queue full" in r.getMessage() for r in caplog.records), (
            "IN-25: enqueuing on a full queue must log a WARNING mentioning 'queue full'"
        )


# --------------------------------------------------------------------------- #
# IN-26: Modifier-only hotkeys use the LL hook (Windows, mocked)
# --------------------------------------------------------------------------- #


class TestModifierOnlyLLHook:
    """IN-26: modifier-only hotkeys (e.g. ``<alt>``) use the LL hook
    instead of being forced onto the 125Hz polling loop.

    The fix drops the ``not self._is_modifier_only`` guard on
    ``simple_key`` and extends ``_hook_proc`` to match modifier VKs
    when ``backend._vk is None``.
    """

    def test_compute_modifier_vks_alt(self):
        """``_compute_modifier_vks(_MOD_ALT)`` returns ``[VK_MENU]``."""
        from voice_typer.server.hotkeys.win32_vk import _MOD_ALT, _VK_MENU
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        vks = WindowsNativeHotkey._compute_modifier_vks(_MOD_ALT)
        assert _VK_MENU in vks

    def test_compute_modifier_vks_win_includes_both_lwin_and_rwin(self):
        """``_compute_modifier_vks(_MOD_WIN)`` returns BOTH ``VK_LWIN``
        and ``VK_RWIN`` (the Win key has no combined VK like
        ``VK_MENU`` for Alt)."""
        from voice_typer.server.hotkeys.win32_vk import _MOD_WIN, _VK_LWIN, _VK_RWIN
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        vks = WindowsNativeHotkey._compute_modifier_vks(_MOD_WIN)
        assert _VK_LWIN in vks
        assert _VK_RWIN in vks

    def test_compute_modifier_vks_empty_for_no_modifiers(self):
        """``_compute_modifier_vks(0)`` returns an empty list (no
        modifiers configured)."""
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        vks = WindowsNativeHotkey._compute_modifier_vks(0)
        assert vks == []

    def test_modifier_vks_for_hook_populated_at_start_time(self):
        """``self._modifier_vks_for_hook`` must be populated in
        ``start()`` so the LL hook proc closure can read it.

        This test uses mocking to avoid the Windows-only ``ctypes.windll``
        calls — we verify the attribute is set, not that the hook is
        actually installed."""
        import ctypes

        from voice_typer.server.hotkeys.win32_vk import _VK_MENU
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # Before start(), the list is empty (initialized in __init__).
        assert backend._modifier_vks_for_hook == []

        # Mock ctypes.windll so start() doesn't raise on Linux.
        mock_windll = MagicMock()
        mock_user32 = MagicMock()
        mock_kernel32 = MagicMock()
        mock_winmm = MagicMock()
        mock_user32.RegisterHotKey.return_value = 0  # fail -> skip WM_HOTKEY path
        mock_user32.SetWindowsHookExW.return_value = 0  # fail -> skip LL hook
        mock_user32.GetAsyncKeyState.return_value = 0
        mock_kernel32.GetLastError.return_value = 0
        mock_kernel32.Sleep = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32
        mock_windll.winmm = mock_winmm

        original_windll = getattr(ctypes, "windll", None)
        ctypes.windll = mock_windll  # type: ignore[attr-defined]
        try:
            backend.start(MagicMock())
        finally:
            if original_windll is not None:
                ctypes.windll = original_windll  # type: ignore[attr-defined]
            else:
                del ctypes.windll  # type: ignore[attr-defined]
            backend.stop()

        # After start(), the list must contain the modifier VKs for <alt>.
        assert _VK_MENU in backend._modifier_vks_for_hook, (
            f"IN-26: _modifier_vks_for_hook must contain VK_MENU for <alt>; got {backend._modifier_vks_for_hook}"
        )

    def test_simple_key_guard_dropped_for_modifier_only(self):
        """IN-26: the ``not self._is_modifier_only`` guard on
        ``simple_key`` must be removed so modifier-only specs use the
        LL hook path. Verified via source inspection.

        Note: there's a SEPARATE ``not self._is_modifier_only`` guard
        earlier in ``start()`` that raises ``ValueError`` when the
        hotkey can't be parsed at all (no VK AND no modifiers) — that
        guard is correct and must stay. This test only checks the
        ``simple_key`` assignment."""
        import inspect

        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        source = inspect.getsource(WindowsNativeHotkey.start)
        # The simple_key assignment must NOT contain the modifier guard.
        assert "simple_key = self._on_release_callback is None and not self._is_modifier_only" not in source, (
            "IN-26 regression: the ``not self._is_modifier_only`` guard on "
            "simple_key was not removed — modifier-only hotkeys are still "
            "forced onto the polling loop."
        )
        # The new simple_key assignment must be present.
        assert "simple_key = self._on_release_callback is None" in source, (
            "IN-26: simple_key must be ``self._on_release_callback is None`` (without the modifier-only guard)."
        )
