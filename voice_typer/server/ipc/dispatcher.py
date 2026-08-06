"""Dispatcher mixin for the IPC server (split from ``ipc_server.py``).

Contains the :class:`DispatcherMixin` class — the command-dispatch
methods (``_dispatch`` / ``_shutting_down_error`` /
``_handle_unknown_command`` / ``_handle_tray_click`` / ``_handle_shutdown``)
that are mixed into :class:`IPCServer` via multiple inheritance.

The mixin accesses instance state (``self._dispatch_lock``,
``self._cached_shutting_down``, ``self._COMMAND_REGISTRY``,
``self._shutdown_started``, ``self.service``, ``self.app``) which is
declared on :class:`IPCServer` itself — the mixin provides only the
method bodies.

Source-string-pinning tests (``tests/test_ipc_server.py``,
``tests/server/test_ipc_server_regressions.py``,
``tests/test_ipc_tray_click_validation.py``,
``tests/test_ipc_server_lifecycle_fixes.py``) use
``inspect.getsource(IPCServer._dispatch)`` / ``._handle_tray_click`` /
``._handle_shutdown`` and assert substrings appear in the source.
Because ``IPCServer._dispatch`` resolves through MRO to
``DispatcherMixin._dispatch``, ``inspect.getsource`` returns the source
from this module — the bodies are moved verbatim so every pinned
substring (``with self._dispatch_lock:``, ``_cached_shutting_down``,
``OutputMixin._send`` / ``ipc/sender.py``, ``_validate_dict_payload``,
``"id": {"type": str, "required": True}``, ``except BaseException``,
``_shutdown_started``) is preserved.
"""

from __future__ import annotations

import threading
import typing

from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.registry import _READONLY_COMMANDS
from voice_typer.server.ipc.validation import (
    CommandHandler,
    ErrorCodes,
    ResponseEnvelope,
    _error_response,
    _validate_dict_payload,
)
from voice_typer.server.log import reset_correlation_id, set_correlation_id


class DispatcherMixin:
    """Command-dispatch methods for :class:`IPCServer`.

    Provides ``_dispatch``, ``_shutting_down_error``,
    ``_handle_unknown_command``, ``_handle_tray_click`` and
    ``_handle_shutdown``. The mixin assumes the host class declares
    ``_dispatch_lock``, ``_cached_shutting_down``, ``_COMMAND_REGISTRY``
    (class attribute) and ``_shutdown_started`` (instance attribute).
    """

    def _dispatch(self, msg: dict) -> dict | None:
        """Route a command and return the response dict.

        Returns ``None`` for commands that send their response internally
        (e.g. ``restart_app`` / ``quit_app`` which kill the process).

        REFACTOR: previously this was a 54-branch if/elif chain spanning
        ~880 lines. It is now a single dict lookup against
        ``_COMMAND_REGISTRY``, with each command implemented as a
        dedicated ``_handle_<cmd>`` method. This improves:
          - Testability: each handler can be unit-tested in isolation.
          - Readability: the dispatch logic is one screen, not 20.
          - Maintainability: adding a command is one method + one
            registry entry, not inserting into a giant elif chain.
        The handler bodies are identical to the old elif blocks -- this
        is a mechanical refactor with zero behavior change.

         state-mutating handler invocations are serialized
        on ``self._dispatch_lock`` with a TOCTOU-closing re-check of
        ``app._shutting_down`` inside the lock. Read-only handlers (see
        ``_READONLY_COMMANDS``) bypass the lock; their best-effort
        shutdown re-check is done unlocked (mirroring the original
         gate).
        """
        # First-line defense: validate that ``msg`` is a dict before
        # any ``msg.get(...)`` access. Non-dict JSON (lists, ints,
        # strings, ``None``) is valid JSON but would crash
        # ``msg.get("type")`` with ``AttributeError``, killing the IPC
        # thread silently. The TCP and stdin transports pre-check this
        # before calling ``_dispatch``; this gate is the single
        # chokepoint so a future transport that forgets the pre-check
        # (or a direct test caller) cannot crash the dispatcher.
        # Returns a structured ``server.unknown_command`` envelope
        # (NOT ``client.invalid_payload``) to mirror the
        # ``_handle_unknown_command`` shape and let clients branch on
        # the same code as for unrecognized commands.
        if not isinstance(msg, dict):
            # ErrorEnvelope contract — see validation.py
            err: ResponseEnvelope = {
                "type": "error",
                "data": {
                    "code": ErrorCodes.UNKNOWN_COMMAND,
                    "message": "message must be a JSON object",
                    "command": msg,
                },
            }
            return err

        # cooperative shutdown gate. When the app is shutting
        # down (``app._shutting_down is True``), reject all NEW dispatch
        # requests with a structured ``shutting_down`` error so the client
        # can stop retrying and tear down cleanly. ``is True`` (rather than
        # a truthiness check) mirrors the existing ``_send`` shutdown-
        # suppress gate (see the ``_cached_shutting_down`` read in
        # ``OutputMixin._send`` in ``voice_typer/server/ipc/sender.py``)
        # so MagicMock-based test fixtures — which expose
        # ``_shutting_down`` as a child mock that is truthy but not
        # ``is True`` — keep exercising the dispatch path instead of
        # short-circuiting here.
        #
        # read the cached snapshot (refreshed in start()/stop())
        # via a defensive ``getattr(self, ...)`` so test fixtures that bypass
        # ``__init__`` (mirroring the sender.py:224 pattern) keep working
        # without explicitly setting the field. ``getattr`` traversal of
        # ``self`` is cheaper than ``getattr(self.app, '_shutting_down',
        # False)`` because ``self`` is a direct local whereas ``self.app``
        # is an attribute chain that always invokes ``__getattribute__``.
        if getattr(self, "_cached_shutting_down", False) is True:
            return self._shutting_down_error(msg)

        # NOTE: the per-process rate limiter is NO LONGER enforced
        # here. Each transport chokepoint applies the limiter BEFORE
        # calling ``_dispatch`` — TCP at ``transport_tcp.py`` (the
        # ``rate_limiter.allow(command=msg_type)`` gate inside
        # ``_handle_tcp_connection``'s read loop) and WS at
        # ``sidecar_ws._make_dispatch`` (the closure-captured
        # ``rate_limiter.allow(command=msg_type)`` gate). The stdin
        # path applies the limiter in ``stdin_runner._run`` before
        # the ``self._dispatch(msg)`` call. Enforcing the limiter here
        # AS WELL would double-charge every accepted command against
        # the burst/sustained budget: each dispatched command
        # would consume its cost twice, halving the effective burst
        # budget and tripping the sustained cap in half the expected
        # time. The transport-side gates are the single chokepoint —
        # a future transport that forgets its own gate is uncovered
        # until a follow-up adds the gate at the new transport's read
        # loop (mirroring the TCP/WS/stdin pattern).
        cmd = msg.get("type")
        data = msg.get("data")
        resp: ResponseEnvelope = {"id": msg.get("id")} if "id" in msg else {}

        # propagate the inbound request id as a correlation id for
        # the duration of this dispatch.  Every log emitted by a handler
        # (and any code it calls synchronously) now carries
        # correlation_id=<request id>, so a client's request and all the
        # server-side log lines it triggered can be tied together in a
        # JSON log backend without threading the id through every call.
        # The token is reset in the ``finally`` below so concurrent
        # requests (each on its own call to _dispatch) don't leak ids
        # into one another.  ``msg.get("id")`` may be None/absent for
        # fire-and-forget notifications — in that case no correlation id
        # is set and logs fall back to the no-correlation schema.
        _corr_token = None
        _req_id = msg.get("id") if isinstance(msg, dict) else None
        if _req_id is not None:
            _corr_token = set_correlation_id(str(_req_id))
        #  (pyrefly): ``_COMMAND_REGISTRY`` is typed ``dict[str, str]``
        # and ``dict.get`` requires a ``str`` key. ``msg.get("type")``
        # returns ``Unknown | None`` because the inbound JSON dict has no
        # static value-type, so the lookup below would be flagged
        # ``bad-argument-type``. Coerce to ``str`` here so the registry
        # lookup type-checks cleanly; the unknown-command path still
        # receives the original value (including ``None``) for the error
        # message, preserving the previous wire behaviour.
        cmd_key = cmd if isinstance(cmd, str) else ""
        # resolve the handler via the class-level ``_COMMAND_REGISTRY``
        # (the introspection source-of-truth) plus ``getattr`` so test-time
        # monkey-patches (``monkeypatch.setattr(server, '_handle_<cmd>', ...)``)
        # are observed at dispatch time. Registry-typo validation is
        # performed once at IPCServer construction (see ``__init__``); there
        # is NO instance-level cache — the previous ``_command_handlers``
        # dict was dead code (built but never consulted at dispatch time)
        # and has been removed. The ``CommandHandler`` annotation on the
        # local ``handler`` variable gives the type checker a ``Callable``
        # value type instead of ``Any``.
        handler_name = self._COMMAND_REGISTRY.get(cmd_key)
        handler: CommandHandler | None = None
        if handler_name is not None:
            _resolved = getattr(self, handler_name, None)
            if callable(_resolved):
                # ``getattr(self, name, None)`` returns ``Any``;
                # ``callable()`` narrows that to a callable type whose
                # return is inferred as ``object`` (pyrefly infers the
                # narrowest callable supertype). Direct assignment to
                # ``handler`` would fail ``bad-assignment`` because
                # ``(...) -> object`` is not assignable to ``CommandHandler``
                # (whose return type is ``ResponseEnvelope | None`` — a
                # narrower type than ``object``, and return types are
                # covariant). ``typing.cast`` is the typed, intentional
                # assertion that the resolved attribute matches the
                # ``CommandHandler`` contract: every entry in
                # ``_COMMAND_REGISTRY`` maps to a ``_handle_<cmd>``
                # method on this class, and the ``__init__``
                # registry-typo validation loop () asserts each
                # entry resolves to a callable attribute at construction
                # time — so a non-CommandHandler resolution would have
                # surfaced as an ``IPCServer.__init__`` test failure
                # before reaching this line. ``cast`` is preferred over
                # the previous ``# type: ignore[assignment]`` suppression
                # because it (1) preserves the type checker's ability
                # to flag genuine ``CommandHandler``-shape mismatches
                # on the assignment LHS, (2) does not silently mask
                # future type errors on this line, and (3) keeps the
                # cast local — if 's full handler annotation
                # migration ever lands, the cast can be removed without
                # touching anything else.
                handler = typing.cast(CommandHandler, _resolved)
        try:
            if handler is None:
                result = self._handle_unknown_command(cmd, data, resp)
            elif cmd_key in _READONLY_COMMANDS:
                # read-only handlers bypass the dispatch lock —
                # they don't mutate shared app/service state, so a
                # long-running state-mutating handler on another thread
                # can't block a quick status poll.
                # best-effort unlocked re-check (the initial
                #  gate already covered the common case).
                if getattr(self, "_cached_shutting_down", False) is True:
                    result = self._shutting_down_error(msg)
                else:
                    result = handler(data, resp)
            else:
                #  + : state-mutating handlers serialize on the
                # per-server dispatch lock; the shutdown re-check happens
                # INSIDE the lock so the (locked) handler invocation is
                # atomic with the (locked, on the ShutdownController side)
                # shutdown-flag set — closing the TOCTOU window between
                # the unlocked gate at the top of ``_dispatch`` and the
                # handler call.
                with self._dispatch_lock:
                    if getattr(self, "_cached_shutting_down", False) is True:
                        result = self._shutting_down_error(msg)
                    else:
                        result = handler(data, resp)
        except ConsentRequiredError as exc:
            # consent errors get a structured ``consent_required``
            # envelope (NOT the generic ``server.internal_error`` toast)
            # so the renderer's consent-dialog logic can surface a
            # provider-specific dialog. This clause MUST come before any
            # generic ``except Exception`` (at the call sites) — otherwise
            # the consent signal would be swallowed into a generic toast.
            resp["type"] = "error"
            resp["data"] = {
                "code": "server.consent_required",
                "message": str(exc),
                "provider": getattr(exc, "provider", ""),
                "scope": getattr(exc, "scope", ""),
            }
            log.warning(
                "[IPC] consent required for %s: provider=%s scope=%s",
                cmd_key,
                getattr(exc, "provider", ""),
                getattr(exc, "scope", ""),
            )
            result = resp
        except Exception as exc:
            # Top-level catch-all so a handler bug (e.g. an uncaught
            # ``RuntimeError`` / ``KeyError`` / ``ValueError`` raised
            # inside a ``_handle_<cmd>`` body) does NOT propagate out
            # of ``_dispatch`` and kill the calling IPC thread. The
            # TCP path has an analogous catch in
            # ``_tcp_dispatch_and_respond``; the stdin path catches
            # via ``_run``'s ``except Exception`` clause. Catching
            # here too means ALL three transports get the same
            # defense-in-depth — a future transport that forgets its
            # own catch-all is still protected. The envelope uses
            # ``_error_response`` (R13-F3) so clients branching on
            # ``code`` see the namespaced ``server.handler_error``
            # (matching the handler-level catch-alls) rather than
            # the bare ``internal_error`` the stdin path emits —
            # and the full traceback is logged server-side at ERROR
            # with ``exc_info=True`` for operator diagnosis. The
            # ``ConsentRequiredError`` clause above MUST come first
            # so the typed consent signal is preserved.
            result = _error_response(resp, "internal error", code=ErrorCodes.HANDLER_ERROR)
            log.error(
                "[IPC] handler %s raised: %s",
                cmd_key,
                exc,
                exc_info=True,
            )
        finally:
            if _corr_token is not None:
                reset_correlation_id(_corr_token)

        # ensure every response has a `data` field so the
        # client can always read `resp.data` without a defensive guard.
        # Commands that return None (restart_app/quit_app) send their
        # response internally and skip this.
        if result is not None:
            result.setdefault("data", {})
            # stamp the inbound request id onto the response
            # envelope so clients using id-based request/response
            # correlation (the standard JSON-RPC-like pattern in
            # ``usePython.ts``) can match the response back to the
            # originating request. Pre-fix, ``_validate_dict_payload``
            # returned a FRESH error-envelope dict with no ``id`` field;
            # every handler that did ``if error: return error`` discarded
            # the ``resp`` dict (which had ``id`` pre-populated) — so
            # validation rejections orphaned the pending request and the
            # renderer would time out instead of resolving the rejection.
            # Stamping here (in ``_dispatch``) is the defensive single
            # chokepoint: it catches validation errors, handler-thrown
            # exception envelopes, and any future error path that
            # forgets to propagate ``id``.
            if isinstance(msg, dict) and "id" in msg and "id" not in result:
                result["id"] = msg["id"]

        return result

    def _shutting_down_error(self, msg: dict) -> ResponseEnvelope:
        """Build a structured ``server.shutting_down`` error envelope.

         aligned to the namespaced ``server.*`` form so
        the WS path (sidecar_ws.py) and the TCP / stdin path produce
        identical envelopes — restoring the  parity contract.

        Factored out of ``_dispatch`` () so the initial
        gate and the per-handler-call TOCTOU re-check share a single
        source of truth for the envelope shape.

        The return type is ``ResponseEnvelope``
        (``dict[str, object]``) rather than :class:`ErrorEnvelope`
        because TypedDicts are invariant and not subtypes of ``dict``;
        the construction-site ``# ErrorEnvelope contract — see
        validation.py`` comment documents the contract without
        enforcing it at the type level.
        """
        # ErrorEnvelope contract — see validation.py
        err: ResponseEnvelope = {
            "type": "error",
            "data": {
                "code": "server.shutting_down",
                "message": "server is shutting down",
            },
        }
        if isinstance(msg, dict) and "id" in msg:
            err["id"] = msg["id"]
        return err

    # the inline _COMMAND_REGISTRY dict literal previously lived
    # here (~180 lines, including the ~30 "REMOVED" historical comments).
    # It has been extracted to
    # :mod:`voice_typer.server.ipc.registry` as the canonical single
    # source of truth (same dict, same keys, same values —
    # behavior-preserving extraction). The class-level alias declared
    # at the top of the class body (``_COMMAND_REGISTRY: dict[str, str]
    # = _COMMAND_REGISTRY``) re-exports it as a class attribute so
    # every existing ``IPCServer._COMMAND_REGISTRY`` call site keeps
    # working unchanged. The "REMOVED" historical comments were
    # consolidated into a ``# Registry history`` block at the top of
    # ``ipc/registry.py`` (the regression guard in
    # ``tests/test_dead_code_stays_removed.py`` already pins the
    # removals independently).

    def _handle_tray_click(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """ADR-0020 §6.5 / §16: dispatch a Tauri tray-menu click by item id.

        Looks the clicked ``id`` up via the tray's ``dispatch_tray_action``
        and returns ``{"ok": True}`` on success.  A missing ``id`` yields a
        ``missing_field`` error; an id the tray doesn't recognise yields a
        distinct ``unknown_tray_item`` error (so the host can tell "malformed
        request" from "item not found").

        validation is delegated to the shared
        ``_validate_dict_payload`` helper (the contract source of truth)
        rather than an inline ``isinstance`` check, so the error envelope
        (``invalid_payload`` / ``invalid_field`` / ``missing_field``)
        matches every other handler in the codebase.

        The return type remains ``ResponseEnvelope`` (not
        :class:`ErrorEnvelope`) because this handler has a non-error
        success path returning ``{"type": "result", "data": {"ok": True}}``.
        The two error-construction sites below are still governed by the
        :class:`ErrorEnvelope` contract (see ``validation.py``).
        """
        validated, error = _validate_dict_payload(
            data,
            {
                "id": {"type": str, "required": True},
            },
        )
        if error:
            return error

        item_id = validated["id"]
        tray = getattr(self.app, "tray", None)
        #  align to the namespaced
        # ``server.unknown_tray_item`` form so the renderer's
        # ``ErrorEvent.code`` narrowing switches on a single canonical
        # prefix (``server.*``) across all error emitters.
        if tray is None or not hasattr(tray, "dispatch_tray_action"):
            # ErrorEnvelope contract — see validation.py
            resp["type"] = "error"
            resp["data"] = {"code": "server.unknown_tray_item", "id": item_id}
            return resp

        handled = tray.dispatch_tray_action(item_id)
        if not handled:
            # ErrorEnvelope contract — see validation.py
            resp["type"] = "error"
            resp["data"] = {"code": "server.unknown_tray_item", "id": item_id}
            return resp

        return {"type": "result", "data": {"ok": True}}

    def _handle_unknown_command(
        self, cmd: object | None, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope:
        """Handle the ``__unknown__`` IPC command."""
        # ErrorEnvelope contract — see validation.py
        resp["type"] = "error"
        # include a structured `code` field so clients can
        # distinguish "unknown command" (caller bug / version skew)
        # from "command handler raised" (server-side fault). The
        # previous payload only had a free-text `message`, which
        # forced clients to substring-match the message to tell
        # the two cases apart.
        #  align to the namespaced ``server.unknown_command``
        # form so the renderer's ``ErrorEvent.code`` narrowing can
        # switch on a single canonical prefix (``server.*``).
        resp["data"] = {
            "code": "server.unknown_command",
            "message": f"Unknown command: {cmd}",
            "command": cmd,
        }
        # No ``cast`` — ``resp`` has been mutated in place to match the
        # :class:`ErrorEnvelope` shape. The return type is
        # ``ResponseEnvelope`` (``dict[str, object]``) rather than
        # :class:`ErrorEnvelope` because TypedDicts are invariant and not
        # subtypes of ``dict``.
        return resp

    def _handle_shutdown(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Handle the ``shutdown`` IPC command ( / ).

        ADR-0020 §10: cooperative shutdown. The Tauri host sends this
        to ask the backend to release the mic / volume / mutex and
        exit cleanly. Previously this command was intercepted by
        ``sidecar_ws._make_dispatch`` BEFORE dispatch, calling
        ``server.app.quit()`` directly and bypassing the service layer
        — so any future shutdown side-effect added to
        :meth:`VoiceTyperService.quit` silently wouldn't run on Tauri.

        The fix registers ``shutdown`` in :data:`_COMMAND_REGISTRY` so
        the command flows through the shared dispatch table on every
        transport (TCP / stdin / WS) and delegates to
        :meth:`self.service.quit` (the same path ``quit_app`` already
        takes). The ack is returned synchronously; the actual teardown
        happens on the service layer's shutdown controller (which
        schedules cleanup on a background thread, so the ack frame
        reaches the host before the process exits).

        The response shape (``{"type": "result", "data": {"ack": True}}``)
        matches the prior WS-path ack so the Tauri Rust host's
        ``shutdown`` match arm (which awaits this exact envelope) keeps
        working unchanged.

        the ack is set on ``resp`` and returned BEFORE
        ``self.service.quit()`` is invoked. ``service.quit()`` runs
        ``_do_cleanup()`` synchronously (30+ steps, ~95s worst case);
        the Tauri host's ``SHUTDOWN_ACK_TIMEOUT_MS=2000ms`` fires long
        before cleanup completes, force-killing the sidecar
        mid-cleanup. Running cleanup on a daemon background thread lets
        the dispatch loop flush the ack frame immediately — the host
        receives the ack within milliseconds and proceeds to its
        graceful-wait while the sidecar's cleanup runs concurrently.

        the background-thread cleanup catches ``BaseException``
        (NOT just ``Exception``) so a ``SystemExit`` / ``KeyboardInterrupt``
        raised inside ``service.quit()`` is logged server-side rather
        than silently killing the cleanup thread. The ack is unaffected
        — it was already returned before the thread started.
        """
        #  (Medium): per-instance shutdown re-entrancy gate. The
        # Tauri host's WS transport can legitimately send ``shutdown``
        # twice (e.g. a slow ack + a supervisor retry, or a WS-close
        # race with the cooperative-shutdown frame). Pre-, the
        # second invocation spawned a SECOND untracked
        # ``ipc-shutdown-cleanup`` daemon thread — both threads would
        # race into ``service.quit()`` / ``_do_cleanup()`` and
        # double-free the mic stream, hotkey listeners, single-instance
        # mutex, etc. The ``_shutdown_started`` event is set BEFORE the
        # cleanup thread is spawned so the second invocation's no-op is
        # atomic with the first's thread-spawn decision; the second
        # invocation still returns the ack envelope (the host's
        # ``SHUTDOWN_ACK_TIMEOUT_MS`` retry expects it).
        if self._shutdown_started.is_set():
            # Already shutting down — return the same ack envelope so
            # the host's retry timer resolves immediately. No second
            # cleanup thread is spawned; the first one (already running
            # on the ``ipc-shutdown-cleanup`` daemon thread) owns the
            # ``service.quit()`` invocation.
            resp["type"] = "result"
            resp["data"] = {"ack": True}
            return resp
        self._shutdown_started.set()

        # Runbook §6.6 / ADR-0020 §10: log the cooperative-shutdown
        # reception so operators can grep the sidecar log for
        # "[SIDECAR-WS] shutdown received" (the Windows validation
        # runbook's §6.6 pass criterion). The handler is shared across
        # transports; the message keeps the historical runbook text.
        log.info("[SIDECAR-WS] shutdown received — releasing mic and exiting")

        # build the ack envelope FIRST and return it. The dispatch
        # loop flushes the wire frame before the background cleanup
        # thread can make progress (the daemon thread doesn't get
        # scheduled until the dispatch loop yields or blocks on I/O).
        resp["type"] = "result"
        resp["data"] = {"ack": True}

        #  + : run service.quit() on a background daemon
        # thread so the synchronous ~95s _do_cleanup does NOT block the
        # dispatch pool thread that's about to flush the ack frame. The
        # host's 2s SHUTDOWN_ACK_TIMEOUT_MS fires long before cleanup
        # completes; without the background thread, the host force-kills
        # the sidecar mid-cleanup (crash_recovery/history_db flush,
        # recorder.stop, hotkey unregisters, PID file clear, tray.stop,
        # Win32 mutex CloseHandle are all interrupted).
        def _bg_cleanup() -> None:
            # delegate to the service layer (NOT
            # self.app.quit()) so shutdown side-effects added to
            # VoiceTyperService.quit run identically across TCP / stdin
            # / WS transports.
            try:
                self.service.quit()
            except BaseException as e:  # noqa: BLE001 —
                # The service-layer shutdown controller is best-effort;
                # a failure here (e.g. the tray is mid-teardown, a
                # KeyboardInterrupt during a sleep, or a SystemExit
                # raised deep inside _do_cleanup) must NOT silently kill
                # the cleanup thread and leave resources held. Log
                # server-side so the operator can diagnose; the host's
                # hard-timeout backstop (kill_children) fires either way.
                # ``BaseException`` (rather than ``Exception``) catches
                # ``SystemExit`` / ``KeyboardInterrupt`` too — the ack
                # was already returned, so there's nothing to recover.
                log.error(
                    "[IPC] shutdown: service.quit() raised: %s",
                    e,
                    exc_info=True,
                )

        # register the cleanup thread on the central
        # ``_thread_registry`` (if the app provides one) so
        # ``shutdown_all()`` can join it during ``VoiceTyperApp.quit()``
        # — pre- the thread was untracked, so a fast process exit
        # could orphan it mid-cleanup and leave resources held.
        cleanup_thread = threading.Thread(
            target=_bg_cleanup,
            name="ipc-shutdown-cleanup",
            daemon=True,
        )
        _registry = getattr(self.app, "_thread_registry", None)
        if _registry is not None:
            _registry.register(
                name="ipc-shutdown-cleanup",
                thread=cleanup_thread,
                stop_event=None,
                join_timeout=2.0,
            )
        cleanup_thread.start()
        return resp
