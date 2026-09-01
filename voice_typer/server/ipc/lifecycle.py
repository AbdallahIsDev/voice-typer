"""Lifecycle mixin for the IPC server (split from ``ipc_server.py``).

Contains the :class:`LifecycleMixin` class — the per-instance lifecycle
methods (``start`` / ``stop`` / heartbeat watchdog / tray-state hook /
relaunch-ack coordination) that are mixed into :class:`IPCServer` via
multiple inheritance.

The mixin accesses instance state (``self._running``, ``self._lock``,
``self._tcp_client``, ``self._tcp_server_socket``, ``self._tcp_worker_pool``,
``self._tcp_dispatch_pool``, ``self._push_fn``, ``self._cached_shutting_down``,
``self._heartbeat_stop_event``, ``self._heartbeat_thread``,
``self._stdin_thread``, ``self._relaunch_ack_event``,
``self._last_heartbeat_at``, ``self._ready_emitted``, ``self.app``,
``self._tcp_mode``, etc.) which is declared on :class:`IPCServer` itself —
the mixin provides only the method bodies.

Source-string-pinning tests (``tests/test_ipc_server.py``,
``tests/test_ipc_send_shutdown_allowlist.py``,
``tests/test_security_fixes.py``,
``tests/security/test_tcp_accept_worker_pool.py``) use
``inspect.getsource(IPCServer.start)`` / ``.stop`` and assert substrings
appear in the source. Because ``IPCServer.start`` resolves through MRO
to ``LifecycleMixin.start``, ``inspect.getsource`` returns the source from
this module — the bodies are moved verbatim so every pinned substring
(``_cached_shutting_down = False``, ``_cached_shutting_down = True``,
``_tcp_worker_pool``, ``shutdown``, ``_tcp_server_socket``,
``_STDIN_IPC_ENV_VAR``, ``os.environ.get(_STDIN_IPC_ENV_VAR) == "1"``)
is preserved.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
import typing

from voice_typer.server import event_bus
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc._helpers import _STDIN_IPC_ENV_VAR
from voice_typer.server.ipc.rate_limiter import (
    _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS,
    _HEARTBEAT_INTERVAL_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
)
from voice_typer.server.ipc.validation import ResponseEnvelope

# PERF-SHUTDOWN-001: the TCP dispatch pool's ``thread_name_prefix``,
# used as a fallback self-join detector (see ``_in_pool_worker``).
_TCP_DISPATCH_POOL_PREFIX = "tcp-dispatch"


def _in_pool_worker(pool) -> bool:
    """Return ``True`` when the current thread is a worker of ``pool``.

    PERF-SHUTDOWN-001: detects the quit-path self-join. ``quit_app``
    is dispatched via ``_tcp_dispatch_and_respond`` onto the
    ``tcp-dispatch`` pool, so the quit handler calls ``app.quit()`` →
    ``_do_cleanup()`` → ``ipc_server.stop()`` FROM INSIDE one of that
    pool's own workers. Draining the pool there is a self-join that
    can never complete — ``shutdown(wait=True)`` waits for EVERY
    worker, including the caller blocked inside ``stop()`` — so the
    drain burned its full 5s timeout on every quit (measured: quit
    took 8.6s, of which 5s was this deadlock).

    ``ThreadPoolExecutor`` exposes its live worker threads as the
    private ``_threads`` set; membership there is exact and stable
    across CPython 3.9+ (populated at worker start, cleared at exit).
    Fall back to the ``thread_name_prefix`` we construct the pool with
    when that set is absent, empty, or does not (yet) contain the
    running worker:

      - absent → monkeypatched executors in tests;
      - empty → CPython 3.12+ ``_adjust_thread_count`` calls
        ``t.start()`` BEFORE ``self._threads.add(t)``, so during
        that window a live worker is not a member yet.  Without the
        fallback the quit-path self-join gate would misread the
        worker as "outside the pool" and drain the pool from inside
        itself (burning the full 5s timeout on every quit).
    """
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


class LifecycleMixin:
    """Lifecycle methods for :class:`IPCServer`.

    Provides ``start``, ``stop``, ``_reset_ready_emitted``,
    ``_heartbeat_loop``, ``_check_heartbeat_timeout``,
    ``_handle_heartbeat``, ``_handle_relaunch_ack``,
    ``wait_for_relaunch_ack`` and ``_hook_tray_set_state``. The mixin
    assumes the host class declares the lifecycle instance attributes
    (``_running``, ``_lock``, ``_tcp_client``, ``_tcp_server_socket``,
    ``_tcp_worker_pool``, ``_tcp_dispatch_pool``, ``_push_fn``,
    ``_cached_shutting_down``, ``_heartbeat_stop_event``,
    ``_heartbeat_thread``, ``_stdin_thread``, ``_relaunch_ack_event``,
    ``_last_heartbeat_at``, ``_ready_emitted``, ``_shutdown_started``).
    """

    # Declare the lifecycle attributes the host normally initializes in
    # ``IPCServer.__init__`` so the mixin's own method bodies type-check
    # (pyrefly types ``self`` as ``LifecycleMixin`` here). The nullable
    # unions mirror the host's declarations: the heartbeat watchdog's
    # ``_last_heartbeat_at`` starts ``None`` until the first heartbeat,
    # and ``_stdin_thread`` may be ``None`` (gated-off stdin listener).
    _stdin_thread: threading.Thread | None
    _heartbeat_thread: threading.Thread | None
    _heartbeat_stop_event: threading.Event
    # Set by ``shutdown.cleanup.do_cleanup`` when ``_do_cleanup()``
    # finishes; the heartbeat force-exit watchdog waits on it so a
    # healthy-but-slow quit() is not force-killed mid-teardown (see
    # ``_check_heartbeat_timeout``).
    _shutdown_completed_event: threading.Event
    _relaunch_ack_event: threading.Event
    _last_heartbeat_at: float | None
    # host app object — declared (mirroring ``TCPTransportMixin``) so
    # the mixin's ``self.app`` accesses type-check; ``Any`` avoids an
    # override conflict with the host's concrete ``app`` attribute.
    app: typing.Any
    # transport-liveness probe registered by ``TCPTransportMixin.start_tcp``
    # and unregistered here in ``stop()``. Declared with the SAME type
    # as ``TCPTransportMixin`` so mypy merges the two base-class
    # definitions instead of flagging an MRO conflict (the assignment
    # in ``stop()`` alone would infer ``None``).
    _transport_live_probe: typing.Callable[[], bool] | None

    def _reset_ready_emitted(self) -> None:
        """Test-only: reset the per-instance ``_ready_emitted`` flag.

        in production, ``_ready_emitted`` is set to ``True`` on the
        first authenticated WS connection and never reset — this is the
        intended behavior so a transient WS reconnect after a drop does
        NOT re-emit the ``ready`` event. However, tests that construct a
        single ``IPCServer`` and call ``sidecar_ws.run(server)`` multiple
        times in the same process need to reset the flag between runs to
        verify the "first connection emits ready" path.

        The cleaner alternative — constructing a fresh ``IPCServer`` per
        test — is what we recommend, and is what the per-instance move
        enables (a fresh instance starts with ``_ready_emitted = False``
        automatically). This helper exists for the small number of tests
        that, for fixture-sharing reasons, must reuse the same instance.

        Marked "test-only" by convention (leading underscore + docstring)
        rather than by a runtime guard — the cost of an accidental
        production call is just a duplicate ``ready`` event, which the
        host already tolerates (it's idempotent on the UI side).
        """
        self._ready_emitted = False

    def start(self) -> None:
        """Start the IPC server in a daemon thread.

        Also hooks ``app.tray.set_state`` so that every state change emits
        a ``status_change`` push event back to the frontend.
        """
        self._running = True
        # Refresh the cached shutdown flag. ``start()`` is called once at
        # server boot (when the host connects) and again after a
        # stop()/restart cycle in tests, so this is the canonical
        # "we're not shutting down" transition point.
        self._cached_shutting_down = False
        # Expose the server on the app so listeners (waveform bubble,
        # streaming partials, etc.) can push events without an explicit
        # reference being threaded through every call site.
        self.app._ipc_server = self
        # ALSO register the push function at module level.  This is
        # the bullet-proof path: any code (waveform listeners, hot
        # paths, audio callback) can call ``event_bus.publish(msg)``
        # without holding a reference to the app or the server.
        # _set_push_event now adds to a registry instead
        # of stomping a single global.  We track our own push callable
        # so stop() can unregister just ours without affecting other
        # active servers.
        # Subscribe through the event_bus directly.
        self._push_fn = self.push
        event_bus.subscribe(self._push_fn)
        self._hook_tray_set_state()
        #  Do NOT start the stdin
        # listener in TCP/WS mode. A direct-terminal invocation
        # (``python -m voice_typer.server.ipc_server --port N``) would
        # otherwise accept unauthenticated JSON commands on stdin while
        # the TCP socket enforces the VOICE_TYPER_IPC_TOKEN handshake.
        # The stdin listener is only for the legacy stdin/stdout IPC mode
        # (``_tcp_mode`` is False). In TCP mode stdin is unused (inherited
        # from Electron, connected to /dev/null or NUL).
        #
        #  (High): the unauthenticated stdin IPC path is gated
        # behind ``VOICE_TYPER_ALLOW_STDIN_IPC=1``. When ``_tcp_mode`` is
        # False (the legacy stdin/stdout path) AND the env var is not
        # set, the stdin listener is REFUSED — a WARNING is logged and
        # ``_stdin_thread`` is set to ``None``. This prevents an
        # unauthenticated command channel from opening on the user's
        # terminal: on Linux TIOCSTI injection is possible, and on every
        # platform an accidental paste of JSON into the terminal triggers
        # unintended IPC commands. Direct API users and tests that need
        # the stdin listener must set ``VOICE_TYPER_ALLOW_STDIN_IPC=1``
        # (the ``--allow-stdin`` CLI flag in :func:`parse_ipc_args` is
        # the alternative gate — it sets the env var).
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
                # listener. ``_tcp_mode`` is False (so the caller did
                # NOT explicitly opt into TCP/WS mode) AND the env-var
                # gate is unset — this is the "unprotected stdin IPC
                # path is still the default" scenario the gate exists
                # to close. Log a WARNING (not an error: the server is
                # still usable for TCP/WS dispatch via the methods on
                # ``self``; only the stdin listener is gated off) and
                # leave ``_stdin_thread = None`` so ``stop()`` /
                # ``_thread_registry`` see no thread to join.
                log.warning(
                    "[IPC] stdin listener gated off — set %s=1 (or pass "
                    "--allow-stdin) to enable unauthenticated stdin/stdout "
                    "IPC mode. Refusing to start the listener.",
                    _STDIN_IPC_ENV_VAR,
                )
                self._stdin_thread = None
        else:
            self._stdin_thread = None
        # start the Electron-alive heartbeat watchdog.  Daemon
        # thread so it doesn't block shutdown.  The thread refuses to
        # fire ``app.quit()`` until the first heartbeat lands, so a
        # slow Electron cold start (10+ seconds for torch import)
        # doesn't trigger a false-positive exit.
        # ADR-0020 §2 + §10: under the Tauri sidecar path
        # (TAURI_SIDECAR=1), the Python-side heartbeat watchdog
        # (ADR-0018) is disabled. The Tauri Rust host owns liveness
        # via TWO mechanisms: (1) WS-close / process exit triggers
        # respawn, and (2) the Rust host dispatches a
        # ``heartbeat`` command every 10s and triggers respawn
        # on 3 consecutive misses (≥30s unresponsive — catches GIL
        # contention / infinite loops / blocking C calls that keep
        # the socket open but don't respond to dispatches). The
        # Python ``_handle_heartbeat`` handler is registered in
        # ``_COMMAND_REGISTRY`` and updates ``_last_heartbeat_at``
        # for the (disabled) watchdog's bookkeeping. See
        # ``src-tauri/src/sidecar/ws.rs`` (reconnect_ws heartbeat
        # task) and ``voice_typer/server/sidecar_ws.py`` (Heartbeat
        # docstring) for the full picture.
        _tauri_sidecar = os.environ.get("TAURI_SIDECAR") == "1"
        if _tauri_sidecar:
            log.info(
                "[IPC] TAURI_SIDECAR=1 — skipping heartbeat-watchdog thread "
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
        # registry (if the app provides one) so ``shutdown_all()`` can
        # signal and join them during ``VoiceTyperApp.quit()``.
        #
        # heartbeat-watchdog: registers WITH a stop_event
        # (``_heartbeat_stop_event``) because the loop wakes on
        # ``Event.wait(timeout)`` — setting the event unblocks it
        # immediately and the thread exits cleanly.
        #
        # ipc-server (stdin listener): registers with ``stop_event=None``
        # because the thread blocks on ``for line in iter(stdin)`` —
        # there is no event it checks between reads. The existing
        # ``stop()`` path closes the TCP client socket and sets
        # ``_running = False`` (checked between lines), but the stdin
        # loop only exits naturally on EOF/OSError. The registry's
        # ``shutdown_all()`` will still JOIN the stdin thread (with a
        # short timeout) to verify it's tracked; the existing per-site
        # ``stop()`` is responsible for the actual cleanup.
        registry = getattr(getattr(self, "app", None), "_thread_registry", None)
        if registry is not None:
            # ADR-0020 §10: heartbeat-watchdog is skipped under TAURI_SIDECAR=1,
            # so only register it if it actually exists.
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
        # ..." line is the single INFO startup marker for the server.
        log.debug("[IPC] server started; push hook registered")

    def stop(self) -> None:
        """Signal the stdin loop and TCP accept loop to stop.

        previously ``stop()`` only set ``_running = False``
        and cleared the push hook, but the TCP accept loop checked
        ``getattr(self, '_stopped', False)`` — a flag that was never
        set anywhere — and the listening socket was a local variable
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
        sufficient — the thread exits naturally on stdin EOF/OSError.
        """
        self._running = False
        # Refresh the cached shutdown flag. ``stop()`` is the canonical
        # "we're shutting down" transition point. ``_send`` reads
        # ``self._cached_shutting_down`` (defensively via ``getattr``) on
        # every push event and short-circuits the TCP write for
        # non-critical events when this is True — see
        # ``_SHUTDOWN_ALLOWLIST`` for the allowlist of events that MUST
        # still be delivered.
        #
        # NOTE: ``restart_app`` sets ``self.app._shutting_down = True``
        # BEFORE ``stop()`` is called, so during the brief window between
        # that set and this ``stop()`` call, the cache is stale (still
        # False). This is acceptable — see the ``__init__`` comment for
        # ``_cached_shutting_down``.
        self._cached_shutting_down = True
        # Unregister our push callable.  Other servers in the registry
        # are unaffected.
        # Unsubscribe through the event_bus directly.
        push_fn = getattr(self, "_push_fn", None)
        if push_fn is not None:
            event_bus.unsubscribe(push_fn)
            self._push_fn = None
        # Unregister the transport-liveness probe registered by
        # ``start_tcp`` (no-op when the TCP transport never started,
        # e.g. the Tauri WS sidecar path).
        event_bus.unregister_transport_probe(getattr(self, "_transport_live_probe", None))
        self._transport_live_probe = None
        if self._tcp_client is not None:
            self._tcp_client.close()
            self._tcp_client = None
        # Close the listening socket to unblock the accept() loop.
        # The accept loop catches OSError and breaks out.
        server_sock = self._tcp_server_socket
        if server_sock is not None:
            with contextlib.suppress(OSError):
                server_sock.close()
            self._tcp_server_socket = None
        # SEC-8: shut down the TCP worker pools so queued (not-yet-
        # started) connection handoffs AND dispatch submissions are
        # dropped and in-flight workers' teardown is no longer tracked.
        # The accept loop also shuts the pools down when it exits
        # naturally; this is the belt-and-suspenders path for callers
        # that close the listening socket directly (e.g. test fixtures)
        # without waiting for the accept thread to observe the close.
        # The dispatch pool is torn down first so its in-flight
        # dispatches can finish writing responses before the connection
        # handlers' sockets are torn down.
        dispatch_pool = self._tcp_dispatch_pool
        if dispatch_pool is not None:
            dispatch_pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_dispatch_pool = None
            # PERF-SHUTDOWN-001: skip the drain wait when ``stop()`` is
            # called from inside the dispatch pool itself.  ``quit_app``
            # runs on a ``tcp-dispatch`` worker, so draining the pool
            # here would wait on a worker that is blocked inside this
            # very ``stop()`` call — a self-join that always burned the
            # full 5s timeout on every quit.  The caller exits right
            # after ``stop()`` returns, ``cancel_futures=True`` already
            # dropped queued work, and the accept-loop's own drain
            # covers any remaining in-flight handlers.
            #
            # The thread-membership check alone is NOT enough: the
            # production shutdown path runs ``stop()`` on a separate
            # helper thread — ``_do_cleanup()`` → ``_run_with_timeout(
            # "ipc_server.stop", ...)`` spawns a ``cleanup-*`` thread —
            # NOT on the pool worker itself. The ``quit_app`` dispatch
            # worker is then *transitively* blocked waiting for that
            # thread inside ``_do_cleanup``, and draining the pool
            # waits once more on the same worker → guaranteed full-5s
            # timeout on every shutdown (measured: quit took 8.8s, of
            # which 5s was this deadlock). Gate the drain on the
            # app-level shutdown flag as well: during quit/restart the
            # in-flight dispatcher finishes as soon as ``stop()``
            # returns and the process is exiting anyway, so skipping
            # the wait is safe. ``is not True`` keeps old drain
            # behavior for test mocks whose ``_shutting_down`` is a
            # truthy child Mock.
            app_ref = getattr(self, "app", None)
            if not _in_pool_worker(dispatch_pool) and getattr(app_ref, "_shutting_down", False) is not True:
                dispatch_join = threading.Thread(target=dispatch_pool.shutdown, kwargs={"wait": True}, daemon=True)
                dispatch_join.start()
                dispatch_join.join(timeout=5.0)
                if dispatch_join.is_alive():
                    log.warning("[SHUTDOWN] tcp_dispatch_pool did not drain in 5s — proceeding anyway")
        pool = self._tcp_worker_pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_worker_pool = None
            # PERF-SHUTDOWN-002: same shutdown gate as the dispatch drain
            # above. The connection read-loop worker blocks in ``recv`` on
            # the client socket while the client keeps it open during the
            # quit handshake, and Windows does NOT unblock that recv from
            # ``close()`` — so the drain join below would burn its full
            # 5s timeout on EVERY quit (measured end-to-end: 8.8s, of
            # which 5s was this worker-pool join; the dispatch drain
            # fixed the other 5s). During app shutdown the process exits
            # right after cleanup, so in-flight connection handlers are
            # daemon-thread reaped — nothing to wait for.
            if getattr(getattr(self, "app", None), "_shutting_down", False) is not True:
                # Bound the in-flight handler drain so teardown doesn't
                # race with running handlers. ``shutdown(wait=False)`` only
                # cancels queued futures; in-flight handlers keep running on the
                # pool's worker threads. We drain them with a hard 5s deadline
                # on a daemon thread so this ``stop()`` call never blocks
                # indefinitely.
                join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
                join_thread.start()
                join_thread.join(timeout=5.0)
                if join_thread.is_alive():
                    log.warning("[SHUTDOWN] tcp_worker_pool did not drain in 5s — proceeding anyway")
        # signal the heartbeat watchdog to exit.  The thread
        # sleeps on ``_heartbeat_stop_event.wait(timeout=INTERVAL)``;
        # setting the event wakes it immediately so it doesn't linger
        # past shutdown.  (It's a daemon thread, so even if it lingered
        # it wouldn't block process exit — but explicit shutdown is
        # cleaner for test start/stop cycles.)
        self._heartbeat_stop_event.set()
        # THREAD-REGISTRY: unregister both IPC threads so a subsequent
        # ``start()`` cycle (common in tests) re-registers cleanly
        # without triggering the "Re-registering name" warning. Safe to
        # call when no entry exists (unregister is a no-op for unknown
        # names).
        registry = getattr(getattr(self, "app", None), "_thread_registry", None)
        if registry is not None:
            registry.unregister("heartbeat-watchdog")
            registry.unregister("ipc-server")
        # Join the stdin thread so it doesn't leak in test
        # start/stop cycles.  The thread is a daemon that blocks on
        # ``for line in iter(stdin)``, so a 0.5s timeout is sufficient
        # — the thread exits naturally on stdin EOF/OSError (set by
        # closing the TCP client socket above) or when _running becomes
        # False (checked between lines).
        stdin_thread = getattr(self, "_stdin_thread", None)
        if stdin_thread is not None and stdin_thread.is_alive():
            stdin_thread.join(timeout=0.5)
        # Keep the app-level reference so existing closures still
        # work after a stop+start cycle in tests.

    # ── Heartbeat watchdog () ───────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """daemon thread that watches for Electron heartbeat timeouts.

        Wakes every ``_HEARTBEAT_INTERVAL_SECONDS`` (5s) and calls
        :meth:`_check_heartbeat_timeout`.  When the timeout fires
        (9 missed heartbeats = 45s without a heartbeat from Electron;
        reduced from 120s/24 misses to align with the Rust-side
        ~30-45s supervisor respawn window), the loop returns —
        ``app.quit()`` has already been triggered, which runs the
        shared ``_do_cleanup()`` path from  (restores volume,
        flushes recovery, releases the mutex, closes PortAudio) and
        breaks the pystray loop so the process exits.

        The thread is a daemon so it doesn't block shutdown.  ``stop()``
        sets ``_heartbeat_stop_event`` to wake the thread immediately
        on a planned shutdown.
        """
        while not self._heartbeat_stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
            if self._check_heartbeat_timeout():
                return  # app.quit() was called; thread exits

    def _check_heartbeat_timeout(self) -> bool:
        """Return True and call ``app.quit()`` if the heartbeat is overdue.

        extracted as a separate method so tests can invoke it
        directly without spinning up the daemon thread (and without
        waiting for the real-time 45s timeout to elapse).

        Returns ``True`` when ``app.quit()`` was called, ``False``
        otherwise.  The ``False`` cases are:

        - ``_last_heartbeat_at is None``: Electron has not yet sent
          its first heartbeat.  The watchdog must NOT fire here, or a
          slow Electron cold start (10+ seconds for the torch import)
          would cause a false-positive exit.
        - ``now - last <= _HEARTBEAT_TIMEOUT_SECONDS``: the most
          recent heartbeat is fresh enough; Electron is still alive.

        The ``True`` case calls ``self.app.quit()`` — which runs the
        shared ``_do_cleanup()`` cleanup path () so the mic
        stream, hotkeys, volume duck, and single-instance mutex are
        properly released before the process exits.  ``app.quit()``
        also calls ``tray.stop()`` which breaks the pystray loop,
        letting ``app.start()`` return and the process exit naturally
        (quit() only calls ``sys.exit()`` from the main thread; from
        a daemon thread it relies on tray.stop() to unwind the main
        loop).

        if ``tray.stop()`` hangs (observed on certain Linux
        backends + Windows Server), the daemon thread scheduled here
        force-exits the process via ``os._exit(1)`` after the grace
        period. The thread waits on the shutdown-completion event
        (set when ``_do_cleanup()`` finishes) instead of a bare sleep,
        so a healthy-but-slow quit() that completes cleanup within the
        grace window is NOT force-killed — the event is set and the
        thread returns without ``os._exit(1)``. Only the genuine-hang
        case (no completion signal before the grace elapses) fires the
        hard force-exit. See the inline comment in the ``True`` branch.
        """
        last = self._last_heartbeat_at
        if last is None:
            # No heartbeat yet — Electron hasn't connected.  Don't
            # fire.  This is the critical guard that prevents a false
            # positive during a slow Electron cold start.
            return False
        now = time.monotonic()
        if now - last <= _HEARTBEAT_TIMEOUT_SECONDS:
            return False
        log.warning(
            "[HEARTBEAT] No heartbeat from Electron in %.1fs (>%0.1fs) "
            "— backend will quit (Electron likely crashed or was "
            "force-killed)",
            now - last,
            _HEARTBEAT_TIMEOUT_SECONDS,
        )
        try:
            self.app.quit()
        except Exception:
            log.exception("[HEARTBEAT] app.quit() raised during heartbeat timeout")

        # force-exit fallback if ``tray.stop()`` hangs.
        #
        # ``app.quit()`` from a daemon thread relies on
        # ``tray.stop()`` breaking the pystray loop so ``app.start()``
        # returns and the process exits naturally (``quit()`` only
        # calls ``sys.exit(0)`` from the main thread). pystray on
        # certain Linux backends (AppIndicator with stale dbus) and on
        # Windows Server (with RDP session disconnects) has been
        # observed to hang inside ``stop()`` — leaving the process
        # stuck with the mic open and the single-instance mutex held.
        #
        # Mitigation: schedule a daemon thread that waits on the
        # shutdown-completion event (wired to the end of
        # ``_do_cleanup()``) for the grace period, then calls
        # ``os._exit(1)`` if the event was never set. If ``quit()``
        # succeeded (cleanup finished within the grace window), the
        # event is set and the thread returns WITHOUT ``os._exit(1)`` —
        # the process exits naturally. If ``quit()`` hung, the grace
        # elapses, the event is never set, and the thread force-exits.
        #
        # Previously the thread used a bare ``time.sleep(grace)`` with
        # no completion signal, so a healthy-but-slow quit() (>10s:
        # PortAudio teardown + history-DB flush + mutex release) was
        # force-killed mid-cleanup without the supervisor being able
        # to distinguish a genuine hang from a slow-but-valid shutdown.
        #
        # ``os._exit`` (not ``sys.exit``) bypasses Python's normal
        # shutdown sequence (no atexit handlers, no finally blocks) —
        # appropriate here because the graceful ``_do_cleanup()`` path
        # already ran inside ``app.quit()`` above. We use ``os._exit(1)``
        # (non-zero) so the supervisor treats this as a
        # crash and respawns with backoff, rather than silently exiting
        # and looking like a clean shutdown.
        try:
            import threading as _threading

            _shutdown_completed_event = self._shutdown_completed_event

            def _force_exit_after_grace() -> None:
                # Wait for the cleanup-completion event instead of a bare
                # sleep. If ``_do_cleanup()`` finishes within the grace
                # window (the event is set), the process is exiting
                # cleanly — return without force-exiting. Only the
                # genuine-hang case (no completion signal before the
                # grace elapses) fires the hard force-exit.
                if _shutdown_completed_event.wait(_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS):
                    return
                log.error(
                    "[HEARTBEAT] app.quit() did not exit within %ds — "
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
                "[HEARTBEAT] failed to schedule force-exit watchdog — process may hang if tray.stop() is stuck"
            )
        return True

    def _handle_heartbeat(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Handle the ``heartbeat`` IPC command ().

        Electron's main process sends this every 5 seconds (see
        ``client/src/main/index.ts``) once the TCP connection is
        established.  The handler updates ``_last_heartbeat_at`` so
        the :meth:`_heartbeat_loop` daemon thread knows Electron is
        still alive.

        The response is a trivial ``heartbeat_ack`` — Electron does
        not act on it (the heartbeat is fire-and-forget), but
        returning a well-formed response keeps the IPC dispatcher's
        ``result.setdefault('data', {})`` path happy and lets
        ``sendToPython()`` resolve its promise instead of timing out.
        """
        self._last_heartbeat_at = time.monotonic()
        resp["type"] = "heartbeat_ack"
        return resp

    def _handle_relaunch_ack(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """PERF-005: Electron ack that it has received and is processing the
        ``relaunch_electron`` request.

        ``restart_app`` waits on ``self._relaunch_ack_event`` (bounded by a
        2s timeout) instead of a fixed ``time.sleep(0.3)``, so the tray
        thread is unblocked as soon as Electron acks — rather than always
        blocking 300ms.  The handler returns ``None`` (no response body):
        restart_app owns the socket teardown, and any response write races
        the imminent shutdown, so there is nothing meaningful to return.
        """
        self._relaunch_ack_event.set()
        return None

    def wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Wait for Electron's ``relaunch_ack`` signal (PERF-005).

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

    # ── Tray state hook ─────────────────────────────────────────────────

    def _hook_tray_set_state(self) -> None:
        """Monkey-patch ``app.tray.set_state`` to emit push events.

        Every call to ``set_state`` will also send a ``status_change``
        push event with the new state value.

        Idempotent: guarded so a ``start()`` → ``stop()`` → ``start()``
        cycle (common in tests and possible during restart) does not
        stack another wrapper on top of an already-wrapped
        ``set_state``. Without the guard, each state change would emit
        N ``status_change`` events after N start cycles.
        """
        # Already wrapped on a prior start() — leave the existing
        # wrapper in place so push events stay deduplicated.
        if getattr(self.app.tray.set_state, "_vt_wrapped", False):
            return

        original = self.app.tray.set_state

        def wrapped(state, message=""):
            original(state, message)
            # The ``message`` argument carries the human-readable
            # context that the tray itself already shows in its
            # tooltip (e.g. ``"Transcription failed: …"``). Forwarding
            # it in the push payload lets the renderer surface the
            # same diagnostic instead of seeing only the bare state
            # value. The field is always present so consumers can
            # branch on ``data.message`` without a separate
            # ``hasOwnProperty`` check; the empty-string default
            # mirrors the ``set_state`` signature.
            #
            # Published through ``event_bus`` (not ``server.push``)
            # so BOTH runtimes deliver it: in TCP mode the server's
            # own ``_push_fn`` subscriber (installed at start(),
            # lifecycle.py) bridges the bus to the TCP client — the
            # same single delivery the old direct ``push`` call
            # produced — while in WS mode the sidecar writer task's
            # ``_push_to_ws`` subscriber delivers it over the
            # WebSocket. A direct ``self.push`` dead-ends in the
            # TCP-only ``_pending_tcp`` buffer in WS mode (no TCP
            # client ever exists there — same rationale as
            # ``_emit_ready_if_first``'s documented WS fix, which
            # converted the ``ready`` push for exactly this reason).
            # The dead-end buffer is capped (SEC-008), so the
            # redundant TCP-path delivery attempt in WS mode is
            # bounded and harmless.
            event_bus.publish(
                {
                    "type": "status_change",
                    "data": {"status": state.value, "message": message},
                }
            )

        wrapped._vt_wrapped = True
        self.app.tray.set_state = wrapped

    def _handle_transcribe_offline(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Master plan §7.4 — handle the transcribe_offline IPC command.

        FORWARDER handler. The renderer invokes this to run an offline
        transcription through the runtime-pack worker (slim core →
        worker over the worker's dedicated WS hop). The worker-side
        ASR is implemented in
        ``voice_typer/worker/_transcribe.py`` + the
        ``transcribe_offline`` branch of
        ``voice_typer/worker/_ws_server.py::_handle_connection`` — the
        worker transcribes the file and pushes
        ``transcribe_offline_result`` back.

        The slim-core → worker forwarding hop (sidecar WS client +
        the Rust host's worker spawn) is the remaining Phase 2a/2b
        wiring: until it lands, this handler acks the request and the
        actual transcription cannot complete end-to-end. The ack
        keeps the renderer's ``call()`` from timing out while the
        architecture is being completed.

        Pinned by tests/test_event_types_parity.py.

        Phase 2d degradation matrix (§8.10): when the offline pack is
        NOT installed, the request cannot ever complete — respond with
        ``queued: False`` + ``degraded: True`` + ``reason:
        "offline_pack_missing"`` so the renderer surfaces the
        "offline engine unavailable" state instead of queueing
        silently forever. When the pack IS present, ack with
        ``queued: True`` as before (the result arrives via
        ``transcribe_offline_result``).
        """
        resp["type"] = "ack"
        # ResponseEnvelope is dict[str, object], so setdefault's static
        # return type is `object` — cast to the dict it actually is at
        # runtime so the resp_data["queued"] writes type-check. Named
        # resp_data (not `data`) because the handler's REQUEST parameter
        # is already `data`.
        resp_data = typing.cast(dict[str, object], resp.setdefault("data", {}))
        # Cheap existence check — no hashing (§8.10).
        pack_missing = True
        try:
            from voice_typer.server.service import update_check

            pack_missing = update_check._local_offline_pack_version() is None
        except Exception:  # noqa: BLE001 — fail-safe: assume missing (degrade, don't queue silently)
            log.debug("[PACK] transcribe_offline pack check failed", exc_info=True)
        if pack_missing:
            resp_data["queued"] = False
            resp_data["degraded"] = True
            resp_data["reason"] = "offline_pack_missing"
        else:
            # Minimal ack so the renderer's call() resolves instead of
            # timing out. The actual transcription comes back via the
            # transcribe_offline_result push event (see
            # ALLOWED_EVENT_TYPES in
            # src-tauri/src/sidecar/ws/event_protocol.rs).
            resp_data["queued"] = True
        return resp

    def _handle_check_offline_pack_update(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Auto-update feature (docs/auto-update-feature.md) — pack update check.

        Delegates to ``update_check.handle_check_offline_pack_update_ipc`` which
        fetches the remote ``pack-manifest.json`` from GitHub Releases
        (C-DATA-1 category-2 allowed: silent update check against the
        GitHub API) and, if a newer pack is available, triggers a
        background download — gated on ``config.offline_pack_consent``
        (C-DATA-1 category-3 model-download consent; the download
        refuses to start without the user's opt-in flag).

        Registered in ``_COMMAND_REGISTRY`` (``check_offline_pack_update``) +
        the TS ``ALLOWED_COMMANDS`` Set + the Rust
        ``allowed_commands()`` literal in lockstep.
        """
        try:
            from voice_typer.server.service.update_check import handle_check_offline_pack_update_ipc

            result = handle_check_offline_pack_update_ipc(self.app, data if isinstance(data, dict) else None)
        except Exception as exc:  # noqa: BLE001 — IPC handlers must never raise
            log.exception("[UPDATE] check_offline_pack_update IPC handler failed: %s", exc)
            result = {"success": False, "error": str(exc), "reason": "handler_error"}
        resp["type"] = "ack"
        resp.setdefault("data", {}).update(result)
        return resp
