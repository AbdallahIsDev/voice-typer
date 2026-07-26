"""EC-FIX-2 / EC-9 regression test: ``shutdown`` is in ``_COMMAND_REGISTRY``.

EC-9 background
----------------
ADR-0020 §10 specified a cooperative ``shutdown`` IPC command for the
Tauri host: the host sends ``{"type": "shutdown"}`` to ask the backend
to release the mic / volume / mutex and exit cleanly.

The original implementation (in
:func:`voice_typer.server.sidecar_ws._make_dispatch`) intercepted the
``shutdown`` message BEFORE dispatch, calling ``server.app.quit()``
directly and bypassing the service layer. As a result:

  1. ``shutdown`` was NOT in :data:`IPCServer._COMMAND_REGISTRY`, so
     the TCP / stdin paths had no handler for it (the command silently
     fell through to ``unknown_command``).
  2. Any future shutdown side-effect added to
     :meth:`VoiceTyperService.quit` silently wouldn't run on Tauri
     (the WS path took a shortcut around the service layer).

EC-FIX-2 (this review) registers ``shutdown`` in the shared dispatch
table and routes it through :meth:`IPCServer._handle_shutdown`, which
delegates to ``self.service.quit()`` (the same path ``quit_app``
already takes). The WS transport (owned by EC-FIX-3) is updated
separately to drop its special-case intercept.

These tests pin the registry-level contract so a future regression
(removing the entry, renaming the handler, or routing it back through
``self.app.quit()``) is caught.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.ipc_server import IPCServer


class TestShutdownCommandRegistry:
    """EC-FIX-2 / EC-9: ``shutdown`` is in :data:`_COMMAND_REGISTRY` and
    backed by a real handler method on :class:`IPCServer`.
    """

    def test_shutdown_is_in_command_registry(self) -> None:
        """``shutdown`` MUST be registered in :data:`_COMMAND_REGISTRY`.

        Without this entry the TCP / stdin transports return
        ``unknown_command`` (now ``server.unknown_command``) when the
        host sends a ``shutdown`` frame — stranding the host's
        cooperative-shutdown path and forcing it to fall back to the
        SIGTERM / kill_children hard-timeout backstop.
        """
        assert "shutdown" in IPCServer._COMMAND_REGISTRY, (
            "EC-9 regression: 'shutdown' is not registered in "
            "_COMMAND_REGISTRY — the TCP/stdin transports cannot "
            "handle a cooperative shutdown request from the host."
        )

    def test_registry_maps_shutdown_to_handle_shutdown(self) -> None:
        """The registry entry MUST map to ``_handle_shutdown``.

        A future refactor that renames the handler without updating
        the registry (or vice-versa) would silently break the
        cooperative-shutdown path — the dispatch lookup would return
        ``None`` and the command would fall through to
        ``unknown_command``. Pinning the mapping catches that drift.
        """
        assert IPCServer._COMMAND_REGISTRY["shutdown"] == "_handle_shutdown", (
            "EC-9 regression: _COMMAND_REGISTRY['shutdown'] maps to "
            f"{IPCServer._COMMAND_REGISTRY['shutdown']!r}, expected "
            "'_handle_shutdown'. The handler name and registry entry "
            "have drifted out of sync."
        )

    def test_handle_shutdown_exists_on_ipc_server(self) -> None:
        """``IPCServer._handle_shutdown`` MUST exist as a callable.

        The registry maps command names to handler-method-name strings;
        :meth:`IPCServer._dispatch` resolves the string via
        ``getattr(self, handler_name)`` at call time. If the method is
        renamed or removed without updating the registry, the dispatch
        lookup raises ``AttributeError`` — which the dispatch-level
        ``except Exception`` catches and converts to
        ``server.internal_error``, again stranding the host.
        """
        assert hasattr(IPCServer, "_handle_shutdown"), (
            "EC-9 regression: IPCServer has no '_handle_shutdown' "
            "method — the registry entry points at a non-existent "
            "handler and dispatch will raise AttributeError."
        )
        assert callable(IPCServer._handle_shutdown), "EC-9 regression: IPCServer._handle_shutdown is not callable."

    def test_handle_shutdown_delegates_to_service_quit(self) -> None:
        """``_handle_shutdown`` MUST call ``self.service.quit()`` (NOT
        ``self.app.quit()``).

        EC-9 root cause: the WS path's special-case intercept called
        ``server.app.quit()`` directly, bypassing the service layer.
        Any shutdown side-effect added to
        :meth:`VoiceTyperService.quit` (e.g. flushing pending
        transcriptions, releasing a cloud-session token, writing a
        final crash-recovery marker) silently wouldn't run on Tauri.
        The fix routes through ``service.quit()`` so the side-effects
        run identically across TCP / stdin / WS transports.
        """
        # Build an IPCServer with a MagicMock app + service so we can
        # assert on call patterns without spinning up the real
        # VoiceTyperApp (which would require sounddevice / pystray /
        # faster_whisper — all mocked at conftest level, but the real
        # construction is still heavyweight).
        app = MagicMock()
        service = MagicMock()
        server = IPCServer(app, service=service)

        # Invoke the handler directly with the dispatch-level resp
        # envelope (mirrors what _dispatch passes in).
        resp: dict = {"id": 1}
        result = server._handle_shutdown(data=None, resp=resp)

        # EC-9: the service layer is the canonical shutdown path.
        service.quit.assert_called_once_with()
        # EC-9: ``self.app.quit()`` must NOT be called — that's the
        # bypass-the-service-layer bug EC-9 is fixing.
        app.quit.assert_not_called()

        # EC-9: the ack envelope matches the prior WS-path ack shape
        # (``{"type": "result", "data": {"ack": True}}``) so the
        # Tauri Rust host's ``shutdown`` match arm — which awaits this
        # exact envelope before tearing down — keeps working unchanged.
        assert result is not None, (
            "_handle_shutdown must return the resp envelope (not None) "
            "so the dispatcher sends the ack back to the host."
        )
        assert result["type"] == "result", (
            f"_handle_shutdown must set resp['type'] = 'result'; got {result.get('type')!r}."
        )
        assert result["data"] == {"ack": True}, (
            f"_handle_shutdown must set resp['data'] = {{'ack': True}}; got {result.get('data')!r}."
        )

    def test_handle_shutdown_returns_ack_even_if_service_quit_raises(self) -> None:
        """If ``service.quit()`` raises, the ack MUST still be returned.

        The service-layer shutdown controller is best-effort; a failure
        here (e.g. the tray is mid-teardown, a cloud-session close
        timed out) must NOT strand the host waiting for an ack. The
        host's hard-timeout backstop (kill_children) would fire
        eventually, but a missing ack makes the host wait the full 2s
        timeout instead of proceeding immediately to the backstop.
        """
        app = MagicMock()
        service = MagicMock()
        service.quit.side_effect = RuntimeError("simulated teardown failure")
        server = IPCServer(app, service=service)

        resp: dict = {"id": 42}
        # Must NOT raise — the handler catches the exception, logs it
        # server-side, and still returns the ack.
        result = server._handle_shutdown(data=None, resp=resp)

        service.quit.assert_called_once_with()
        assert result is not None
        assert result["type"] == "result"
        assert result["data"] == {"ack": True}
