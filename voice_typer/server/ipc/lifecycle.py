"""Lifecycle mixin for the IPC server (split from ``ipc_server.py``)."""

from __future__ import annotations

import contextlib
import os
import threading
import time
import typing

from voice_typer.server import event_bus, worker_pending
from voice_typer.server.duration import format_duration
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc._helpers import _STDIN_IPC_ENV_VAR
from voice_typer.server.ipc.rate_limiter import (
    _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS,
    _HEARTBEAT_INTERVAL_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
)
from voice_typer.server.ipc.validation import ErrorCodes, ResponseEnvelope, _error_response
from voice_typer.server.keyboard_ownership import keyboard_ownership
from voice_typer.server.tray_types import is_tauri_sidecar

# PERF-SHUTDOWN-001: the TCP dispatch pool's ``thread_name_prefix``,
_TCP_DISPATCH_POOL_PREFIX = "tcp-dispatch"


def _in_pool_worker(pool) -> bool:
    """Return ``True`` when the current thread is a worker of ``pool``."""
    if pool is None:
        return False
    current = threading.current_thread()
    workers = getattr(pool, "_threads", None)
    if workers is not None:
        if current in workers:
            return True
        if workers:
            return False
    return current.name.startswith(_TCP_DISPATCH_POOL_PREFIX)


def _validate_transcribe_offline_payload(
    data: object | None,
) -> tuple[dict[str, object] | None, tuple[str, str] | None]:
    """Check the renderer payload; return ``(payload, None)`` or ``(None, (code, message))``."""
    if not isinstance(data, dict):
        return None, (ErrorCodes.INVALID_PAYLOAD, "data must be an object")
    audio_path = data.get("audio_path")
    if not isinstance(audio_path, str) or not audio_path.strip() or len(audio_path) > 4096:
        return None, (ErrorCodes.INVALID_FIELD, "'audio_path' must be a non-empty string")
    sample_rate = data.get("sample_rate")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        return None, (ErrorCodes.INVALID_FIELD, "'sample_rate' must be a positive int")
    language = data.get("language")
    if language is not None and not isinstance(language, str):
        return None, (ErrorCodes.INVALID_FIELD, "'language' must be a string or null")
    return {"audio_path": audio_path, "sample_rate": sample_rate, "language": language}, None


class LifecycleMixin:
    """Lifecycle methods for :class:`IPCServer`."""

    # and ``_stdin_thread`` may be ``None`` (gated-off stdin listener).
    _stdin_thread: threading.Thread | None
    _heartbeat_thread: threading.Thread | None
    _heartbeat_stop_event: threading.Event
    # Set by ``shutdown.cleanup.do_cleanup`` when ``_do_cleanup()``
    _shutdown_completed_event: threading.Event
    _relaunch_ack_event: threading.Event
    _last_heartbeat_at: float | None
    # host app object; ``Any`` avoids an override conflict with the
    app: typing.Any
    # transport-liveness probe (historically registered by the TCP
    _transport_live_probe: typing.Callable[[], bool] | None

    def _reset_ready_emitted(self) -> None:
        """Test-only: reset the per-instance ``_ready_emitted`` flag."""
        self._ready_emitted = False

    def _on_ipc_client_disconnect(self, reason: str) -> None:
        """Reset keyboard ownership when the IPC client disconnects."""
        if not self._running:
            # Server is shutting down (stop() was called). Don't
            log.debug("[IPC] client disconnect during shutdown; skipping keyboard ownership reset")
            return
        keyboard_ownership().reset()
        # Also clear the ESC-pending-capture-exit Event on the hotkey
        _hotkeys = getattr(self.app, "hotkeys", None)
        if _hotkeys is not None:
            with contextlib.suppress(AttributeError):
                _hotkeys._esc_pending_capture_exit_event.clear()

    def start(self) -> None:
        """Start the IPC server in a daemon thread."""
        self._running = True
        # Refresh the cached shutdown flag. ``start()`` is called once at
        self._cached_shutting_down = False
        # Expose the server on the app so listeners (waveform bubble,
        self.app._ipc_server = self
        # ALSO register the push function at module level.  This is
        self._push_fn = self.push
        event_bus.subscribe(self._push_fn)
        self._hook_tray_set_state()
        # NOTE: see docs/code-notes/ipc.md
        if not self._tcp_mode:
            if os.environ.get(_STDIN_IPC_ENV_VAR) == "1":
                self._stdin_thread = threading.Thread(
                    target=self._run,
                    name="ipc-server",
                    daemon=True,
                )
                self._stdin_thread.start()
            else:
                # refuse to start the unauthenticated stdin
                log.warning(
                    "[IPC] stdin listener gated off. Set %s=1 to enable "
                    "unauthenticated stdin/stdout IPC mode. Refusing to start "
                    "the listener.",
                    _STDIN_IPC_ENV_VAR,
                )
                self._stdin_thread = None
        else:
            self._stdin_thread = None
        # thread so it doesn't block shutdown.  The thread refuses to
        _tauri_sidecar = is_tauri_sidecar()
        if _tauri_sidecar:
            log.info(
                "[IPC] TAURI_SIDECAR=1: skipping heartbeat-watchdog thread "
                "(Tauri Rust host owns liveness via WS-close + heartbeat dispatch)"
            )
            self._heartbeat_thread = None
        else:
            self._heartbeat_stop_event.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="heartbeat-watchdog",
                daemon=True,
            )
            self._heartbeat_thread.start()
        # THREAD-REGISTRY: register both IPC threads with the central
        registry = getattr(getattr(self, "app", None), "_thread_registry", None)
        if registry is not None:
            # ADR-0020 §10: heartbeat-watchdog is skipped under TAURI_SIDECAR=1,
            if self._heartbeat_thread is not None:
                registry.register(
                    name="heartbeat-watchdog",
                    thread=self._heartbeat_thread,
                    stop_event=self._heartbeat_stop_event,
                    join_timeout=2.0,
                )
            if self._stdin_thread is not None:
                registry.register(
                    name="ipc-server",
                    thread=self._stdin_thread,
                    stop_event=None,
                    join_timeout=0.5,
                )
        # DEBUG: the entrypoint's "[IPC] TCP server listening on port
        log.debug("[IPC] server started; push hook registered")
        # wiring; the invalidator daemon thread is spawned only here,
        self.wire_background_integrations()

    def stop(self) -> None:
        """Signal the stdin loop and TCP accept loop to stop.

        Runs the WS graceful-shutdown hook FIRST (when the WebSocket
        layer attached one: see
        ``sidecar_ws_internals.graceful_shutdown._attach_ws_graceful_shutdown``
        which installs it into ``self._ws_stop_hook``): the hook sends
        close(1001) to authenticated WS connections, bounded-waits for
        in-flight dispatch futures, and stops the WS loop BEFORE the TCP
        teardown tears down shared state. The hook is best-effort, an
        exception is logged at DEBUG and the TCP teardown STILL runs (a
        failure in the WS close path must never prevent the server from
        stopping). Previously this ordering was implemented by wrapping
        the bound ``stop`` method at instance level (``server.stop =
        wrapped_stop``), which hid the call chain from the type checker;
        the explicit hook slot keeps the same contract statically
        visible.

        previously ``stop()`` only set ``_running = False``
        and cleared the push hook, but the TCP accept loop checked
        ``getattr(self, '_stopped', False)``: a flag that was never
        set anywhere, and the listening socket was a local variable
        in ``_accept_tcp`` with no external reference.  The result was
        that ``stop()`` could not unblock a daemon thread sitting in
        ``server.accept()``; the thread (and socket) leaked until
        process exit.  We now (a) reuse ``_running`` as the lifecycle
        flag the accept loop checks, and (b) close the listening socket
        here so ``accept()`` raises ``OSError`` and the loop exits
        cleanly.

        stop() now unregisters OUR push callable from the
        module-level registry instead of clearing the global outright.
        Other active servers in the same process keep working.

        The ``_stdin_thread`` is now joined with a short timeout so
        the thread is properly tracked and doesn't leak in test
        start/stop cycles. The stdin thread is a daemon that blocks
        on ``for line in iter(stdin)``, so a 0.5s timeout is
        sufficient, the thread exits naturally on stdin EOF/OSError.
        """
        self._running = False
        # Refresh the cached shutdown flag. ``stop()`` is the canonical
        self._cached_shutting_down = True
        # WS graceful-shutdown hook FIRST (see docstring): best-effort,
        stop_hook = getattr(self, "_ws_stop_hook", None)
        if stop_hook is not None:
            try:
                stop_hook()
            except Exception:
                log.debug("[IPC] _ws_stop_hook raised, continuing TCP teardown", exc_info=True)
        # Unregister our push callable.  Other servers in the registry
        push_fn = getattr(self, "_push_fn", None)
        if push_fn is not None:
            event_bus.unsubscribe(push_fn)
            self._push_fn = None
        # Unregister the transport-liveness probe registered by
        event_bus.unregister_transport_probe(getattr(self, "_transport_live_probe", None))
        self._transport_live_probe = None
        if self._tcp_client is not None:
            self._tcp_client.close()
            self._tcp_client = None
        # Close the listening socket to unblock the accept() loop.
        server_sock = self._tcp_server_socket
        if server_sock is not None:
            with contextlib.suppress(OSError):
                server_sock.close()
            self._tcp_server_socket = None
        # SEC-8: shut down the TCP worker pools so queued (not-yet-
        dispatch_pool = self._tcp_dispatch_pool
        if dispatch_pool is not None:
            dispatch_pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_dispatch_pool = None
            # PERF-SHUTDOWN-001: skip the drain wait when ``stop()`` is
            app_ref = getattr(self, "app", None)
            if not _in_pool_worker(dispatch_pool) and getattr(app_ref, "_shutting_down", False) is not True:
                dispatch_join = threading.Thread(target=dispatch_pool.shutdown, kwargs={"wait": True}, daemon=True)
                dispatch_join.start()
                dispatch_join.join(timeout=5.0)
                if dispatch_join.is_alive():
                    log.warning("[SHUTDOWN] tcp_dispatch_pool did not drain in 5s, proceeding anyway")
        pool = self._tcp_worker_pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_worker_pool = None
            # PERF-SHUTDOWN-002: same shutdown gate as the dispatch drain
            if getattr(getattr(self, "app", None), "_shutting_down", False) is not True:
                # Bound the in-flight handler drain so teardown doesn't
                join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
                join_thread.start()
                join_thread.join(timeout=5.0)
                if join_thread.is_alive():
                    log.warning("[SHUTDOWN] tcp_worker_pool did not drain in 5s, proceeding anyway")
        # signal the heartbeat watchdog to exit.  The thread
        self._heartbeat_stop_event.set()
        # THREAD-REGISTRY: unregister both IPC threads so a subsequent
        registry = getattr(getattr(self, "app", None), "_thread_registry", None)
        if registry is not None:
            registry.unregister("heartbeat-watchdog")
            registry.unregister("ipc-server")
        # Join the stdin thread so it doesn't leak in test
        stdin_thread = getattr(self, "_stdin_thread", None)
        if stdin_thread is not None and stdin_thread.is_alive():
            stdin_thread.join(timeout=0.5)
        # Keep the app-level reference so existing closures still

    def _heartbeat_loop(self) -> None:
        """daemon thread that watches for predecessor heartbeat timeouts."""
        while not self._heartbeat_stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
            if self._check_heartbeat_timeout():
                return  # app.quit() was called; thread exits

    def _check_heartbeat_timeout(self) -> bool:
        """Return True and call ``app.quit()`` if the heartbeat is overdue."""
        last = self._last_heartbeat_at
        if last is None:
            # No heartbeat yet. predecessor hasn't connected.  Don't
            return False
        now = time.monotonic()
        if now - last <= _HEARTBEAT_TIMEOUT_SECONDS:
            return False
        log.warning(
            "[HEARTBEAT] No heartbeat from predecessor in %.1fs (>%0.1fs) "
            "— backend will quit (predecessor likely crashed or was "
            "force-killed)",
            now - last,
            _HEARTBEAT_TIMEOUT_SECONDS,
        )
        try:
            self.app.quit()
        except Exception:
            log.exception("[HEARTBEAT] app.quit() raised during heartbeat timeout")

        # NOTE: see docs/code-notes/ipc.md
        try:
            import threading as _threading

            _shutdown_completed_event = self._shutdown_completed_event

            def _force_exit_after_grace() -> None:
                # Wait for the cleanup-completion event instead of a bare
                if _shutdown_completed_event.wait(_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS):
                    return
                log.error(
                    "[HEARTBEAT] app.quit() did not exit within %ds, "
                    "force-exiting via os._exit(1) (tray.stop() likely hung)",
                    int(_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS),
                )
                import os as _os

                _os._exit(1)

            _threading.Thread(
                target=_force_exit_after_grace,
                name="heartbeat-force-exit",
                daemon=True,
            ).start()
        except Exception:
            log.exception(
                "[HEARTBEAT] failed to schedule force-exit watchdog, process may hang if tray.stop() is stuck"
            )
        return True

    def _handle_heartbeat(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Handle the ``heartbeat`` IPC command ()."""
        self._last_heartbeat_at = time.monotonic()
        resp["type"] = "heartbeat_ack"
        return resp

    def _handle_relaunch_ack(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """PERF-005: predecessor ack that it has received and is processing the
        ``the legacy relaunch event name`` request.

        ``restart_app`` waits on ``self._relaunch_ack_event`` (bounded by a
        2s timeout) instead of a fixed ``time.sleep(0.3)``, so the tray
        thread is unblocked as soon as predecessor acks, rather than always
        blocking 300ms.  The handler returns ``None`` (no response body):
        restart_app owns the socket teardown, and any response write races
        the imminent shutdown, so there is nothing meaningful to return.
        """
        self._relaunch_ack_event.set()
        return None

    def wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Wait for the predecessor's ``relaunch_ack`` signal (PERF-005).

        Public wrapper around the private ``_relaunch_ack_event`` so
        :class:`voice_typer.server.app.VoiceTyperApp` does not have to
        reach into IPC-server private state during ``restart_app``.

        The event is cleared before waiting so a stale ack from a prior
        restart cycle cannot satisfy a fresh one. Returns ``True`` if the
        ack was signalled within ``timeout`` seconds, ``False`` on
        timeout.

        Parameters
        ----------
        timeout :
            Maximum seconds to wait for the ack (mirrors the original
            ``2.0`` hardcoded value used by ``restart_app``).
        """
        self._relaunch_ack_event.clear()
        return self._relaunch_ack_event.wait(timeout=timeout)

    def _hook_tray_set_state(self) -> None:
        """Monkey-patch ``app.tray.set_state`` to emit push events."""
        # Already wrapped on a prior start(), leave the existing
        if getattr(self.app.tray.set_state, "_vt_wrapped", False):
            return

        original = self.app.tray.set_state

        def wrapped(state, message=""):
            original(state, message)
            # The dead-end buffer is capped (SEC-008), so the
            event_bus.publish(
                {
                    "type": "status_change",
                    "data": {"status": state.value, "message": message},
                }
            )

        wrapped._vt_wrapped = True
        self.app.tray.set_state = wrapped

    def _handle_transcribe_offline(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Master plan §7.4, handle the transcribe_offline IPC command.

        FORWARDER handler. The renderer invokes this to run an offline
        transcription through the runtime-pack worker (slim core →
        worker over the worker's dedicated WS hop). The worker-side
        ASR is implemented in
        ``voice_typer/worker/_transcribe.py`` + the
        ``transcribe_offline`` branch of
        ``voice_typer/worker/_ws_server.py::_handle_connection``, the
        worker transcribes the file and pushes
        ``transcribe_offline_result`` back.

        Path: validate the payload, refuse when the pack is missing,
        otherwise forward ``{audio_path, sample_rate, language}`` via
        ``worker_client`` when the worker is ready. When the worker is
        not ready yet the request is queued in
        ``voice_typer/server/worker_pending.py`` and drains when the
        ready path runs. The ack is immediate (the worker takes
        seconds, the result arrives via ``transcribe_offline_result``);
        malformed payloads get an error envelope, never an exception.

        Pinned by tests/test_event_types_parity.py.

        Runtime-pack degradation matrix (§8.10): when the offline pack is
        NOT installed, the request cannot ever complete, respond with
        ``queued: False`` + ``degraded: True`` + ``reason:
        "offline_pack_missing"`` so the renderer surfaces the
        "offline engine unavailable" state instead of queueing
        silently forever.
        """
        resp["type"] = "ack"
        # ResponseEnvelope is dict[str, object], so setdefault's static
        resp_data = typing.cast(dict[str, object], resp.setdefault("data", {}))
        # Cheap existence check, no hashing (§8.10).
        pack_missing = True
        try:
            from voice_typer.server.service import update_check

            pack_missing = update_check._local_offline_pack_version() is None
        except Exception:  # noqa: BLE001, fail-safe: assume missing (degrade, don't queue silently)
            log.debug("[PACK] transcribe_offline pack check failed", exc_info=True)
        if pack_missing:
            resp_data["queued"] = False
            resp_data["degraded"] = True
            resp_data["reason"] = "offline_pack_missing"
            return resp
        payload, error = _validate_transcribe_offline_payload(data)
        if error is not None:
            log.debug("[WORKER] transcribe_offline malformed payload: %s", error[1])
            return _error_response(resp, error[1], code=error[0])
        assert payload is not None
        t0 = time.perf_counter()
        try:
            forwarded = worker_pending.try_forward(payload)
        except Exception:  # noqa: BLE001, IPC handlers must never raise
            log.exception("[WORKER] transcribe_offline forward raised: queueing")
            forwarded = False
        if forwarded:
            resp_data["queued"] = True
            resp_data["forwarded"] = True
            log.info("[WORKER] transcribe_offline forwarded%s", format_duration(time.perf_counter() - t0))
            return resp
        try:
            depth = worker_pending.enqueue(payload)
        except Exception:  # noqa: BLE001, IPC handlers must never raise
            log.exception("[WORKER] transcribe_offline enqueue raised")
            return _error_response(resp, "failed to queue offline transcription", code=ErrorCodes.HANDLER_ERROR)
        resp_data["queued"] = True
        resp_data["forwarded"] = False
        resp_data["reason"] = "worker_not_ready"
        log.info("[WORKER] transcribe_offline queued (worker not ready, depth=%d)", depth)
        return resp

    def _handle_check_offline_pack_update(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Auto-update feature (docs/auto-update-feature.md), pack update check.

        Delegates to ``update_check.handle_check_offline_pack_update_ipc`` which
        fetches the remote ``pack-manifest.json`` from GitHub Releases
        (C-DATA-1 category-2 allowed) and, if a newer pack is available,
        triggers a background download. Pack updates are always-on
        (user product decision; no consent gate, not disableable).

        Registered in ``_COMMAND_REGISTRY`` (``check_offline_pack_update``) +
        the Rust ``allowed_commands()`` literal in lockstep.
        """
        try:
            from voice_typer.server.service.update_check import handle_check_offline_pack_update_ipc

            result = handle_check_offline_pack_update_ipc(self.app, data if isinstance(data, dict) else None)
        except Exception as exc:  # noqa: BLE001, IPC handlers must never raise
            log.exception("[UPDATE] check_offline_pack_update IPC handler failed: %s", exc)
            result = {"success": False, "error": str(exc), "reason": "handler_error"}
        resp["type"] = "ack"
        resp.setdefault("data", {}).update(result)
        return resp
