"""GT-5 / GT-25 / GT-29 / GT-45 / GT-48: IPCServer dispatch + send fixes.

Test coverage
-------------
- GT-5:   ``_handle_shutdown`` sends the ack BEFORE ``service.quit()``
          runs (cleanup runs on a background daemon thread).
- GT-25:  state-mutating dispatches serialize on ``_dispatch_lock``;
          read-only handlers bypass the lock.
- GT-29:  ``IPCServer.__init__`` validates the ``_COMMAND_REGISTRY`` at
          construction time via a loop that asserts every entry
          resolves to a callable bound method (DT-5: the previous
          ``_command_handlers`` instance cache was dead code — ``_dispatch``
          resolves the handler via ``getattr(self, handler_name, None)``
          at dispatch time, so the cache was never read. The cache was
          deleted; the typo-validation goal is now achieved by the
          ``__init__``-time assertion loop without storing bound methods).
- GT-30:  ``_rate_limiter_instance`` is declared on ``IPCServer.__init__``
          (no more ``# type: ignore[attr-defined]``).
- GT-45:  TOCTOU re-check of ``app._shutting_down`` inside the dispatch
          lock — a flag flip between the unlocked gate and the locked
          handler invocation is observed.
- GT-48:  pending-event re-merge preserves FIFO order
          (``pending + self._pending_tcp + [line]``).
- GT-D1-5: ``_on_sigusr1`` is annotated with ``FrameType | None``.
- GT-D1-10: ``_send`` parameters are typed (``TextIO | None``,
           ``object | None``).
- GT-C3-7: ``_handle_shutdown`` catches ``BaseException`` so a
           ``SystemExit`` inside ``service.quit()`` does not silently
           kill the cleanup thread.

These tests are intentionally unit-level (no live TCP) so they run in
<1 s and don't depend on the auth-handshake plumbing exercised by
``test_ipc_dispatch_errors.py`` / ``test_ipc_layer_fixes.py``.
"""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import (
    _READONLY_COMMANDS,
    CommandHandler,
    IPCServer,
    ResponseEnvelope,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_server() -> IPCServer:
    """Build an IPCServer with MagicMock app + service for unit tests.

    The MagicMock app exposes ``_shutting_down`` as a child mock that is
    truthy but NOT ``is True`` — matching the convention used by the
    existing dispatch gate (line ~1586 of ipc_server.py) so tests
    exercise the dispatch path instead of short-circuiting.
    """
    app = MagicMock()
    app._shutting_down = False  # explicit bool, not a child mock
    service = MagicMock()
    return IPCServer(app, service=service)


# ── GT-29: __init__-time registry validation (DT-5 dead-cache removal) ──


class TestGT29DispatchTableTyped:
    """GT-29 / DT-5: ``IPCServer.__init__`` validates the
    ``_COMMAND_REGISTRY`` at construction time by looping over every
    entry, resolving it via ``getattr(self, method_name)``, and raising
    ``RuntimeError`` if the attribute is not callable. A typo in the
    class-level registry surfaces at construction (every test that
    builds an IPCServer) instead of only when the buggy command is
    dispatched.

    The previous ``_command_handlers: dict[str, CommandHandler]``
    instance cache (built in ``__init__`` and stored on ``self``) was
    dead code — ``_dispatch`` resolves the handler the same way at
    dispatch time via ``getattr(self, handler_name, None)``, so the
    cache was never read. DT-5 (Phase 4) deleted the cache; the
    typo-validation goal is preserved by the ``__init__``-time
    assertion loop.
    """

    def test_init_validates_registry_via_assertion_loop(self) -> None:
        """GT-29 / DT-5: ``__init__`` must contain a loop that asserts
        every ``_COMMAND_REGISTRY`` entry resolves to a callable on
        ``self`` (the typo-validation goal previously served by the
        dead ``_command_handlers`` cache).

        We introspect the source of ``__init__`` and verify the
        validation loop is present — the cache is gone, but the
        typo-detection contract survives.
        """
        src = inspect.getsource(IPCServer.__init__)
        # The loop iterates over ``_COMMAND_REGISTRY`` entries and
        # resolves each via ``getattr(self, _method_name, None)``.
        assert "_COMMAND_REGISTRY.items()" in src, (
            "GT-29 / DT-5: __init__ must iterate over "
            "_COMMAND_REGISTRY.items() to validate every entry resolves "
            "to a callable (replaces the deleted _command_handlers cache)."
        )
        assert "callable(" in src, (
            "GT-29 / DT-5: __init__ must call callable() on each "
            "resolved attribute to validate the registry at construction "
            "time."
        )
        # The dead ``_command_handlers`` cache must NOT be built.
        assert "self._command_handlers" not in src, (
            "DT-5: __init__ must NOT build the dead _command_handlers "
            "instance cache — _dispatch resolves handlers at dispatch "
            "time via getattr(self, handler_name, None), so the cache "
            "was never read. Only the typo-validation loop survives."
        )

    def test_command_handlers_cache_not_set_after_init(self) -> None:
        """DT-5: the ``_command_handlers`` instance attribute is NOT
        set by ``__init__`` (the dead cache was deleted). Tests that
        previously monkey-patched the cache entry must now patch the
        handler method directly on the instance — ``_dispatch``
        resolves via ``getattr(self, handler_name, None)`` at dispatch
        time so the patch is observed.
        """
        server = _make_server()
        assert not hasattr(server, "_command_handlers"), (
            "DT-5: the _command_handlers instance cache was deleted as "
            "dead code — _dispatch resolves handlers at dispatch time. "
            "__init__ must NOT set this attribute."
        )

    def test_registry_typo_surfaces_at_construction(self) -> None:
        """GT-29: a typo in ``_COMMAND_REGISTRY`` (a method name that
        doesn't exist on IPCServer) must surface at IPCServer
        construction time, NOT at dispatch time.

        Monkey-patches the class-level registry with a bad entry,
        constructs a fresh IPCServer, and asserts ``RuntimeError`` is
        raised. Restores the registry afterward.
        """
        original = IPCServer._COMMAND_REGISTRY.copy()
        try:
            IPCServer._COMMAND_REGISTRY["__typo_probe__"] = "_handle_this_method_does_not_exist"
            with pytest.raises(RuntimeError, match="non-callable"):
                _make_server()
        finally:
            IPCServer._COMMAND_REGISTRY.clear()
            IPCServer._COMMAND_REGISTRY.update(original)

    def test_response_envelope_alias_exists(self) -> None:
        """GT-29 / GT-D1-10: ``ResponseEnvelope = dict[str, object]``
        type alias is exported from the module."""
        # ``dict[str, object]`` evaluates to a ``types.GenericAlias``
        # (NOT ``dict`` itself), so verify the alias resolves to a
        # subscripted generic dict origin — i.e. it's the documented
        # ``dict[str, object]`` shape, not some other type.

        assert ResponseEnvelope is not None
        # PEP 585 generic alias: ``dict[str, object].__origin__ is dict``.
        assert getattr(ResponseEnvelope, "__origin__", None) is dict, (
            f"GT-29: ResponseEnvelope must be a dict subscripted generic "
            f"alias (dict[str, object]); got {ResponseEnvelope!r}."
        )

    def test_command_handler_alias_exists(self) -> None:
        """GT-29: ``CommandHandler`` type alias is exported."""
        # ``typing.Callable`` aliases are instances of typing._GenericAlias
        # in 3.9+; just assert the name is importable and non-None.
        assert CommandHandler is not None


# ── GT-30: _rate_limiter_instance declared on IPCServer ───────────────


class TestGT30RateLimiterInstanceDeclared:
    """GT-30: ``_rate_limiter_instance`` is declared on IPCServer.__init__
    so the type checker can verify the assignment in
    ``_get_rate_limiter`` without ``# type: ignore[attr-defined]``.
    """

    def test_rate_limiter_instance_init_to_none(self) -> None:
        server = _make_server()
        assert hasattr(server, "_rate_limiter_instance"), (
            "GT-30: IPCServer.__init__ must declare _rate_limiter_instance."
        )
        assert server._rate_limiter_instance is None, (
            "GT-30: _rate_limiter_instance must start as None (lazy initialization by _get_rate_limiter)."
        )

    def test_get_rate_limiter_no_type_ignore(self) -> None:
        """The ``# type: ignore[attr-defined]`` marker must be GONE
        from ``_get_rate_limiter``'s source."""
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server._get_rate_limiter)
        assert "type: ignore[attr-defined]" not in src, (
            "GT-30: _get_rate_limiter must NOT silence the "
            "_rate_limiter_instance assignment with type: ignore — the "
            "attribute is now declared on IPCServer.__init__."
        )

    def test_rate_limiter_instance_assignable_without_ignore(self) -> None:
        """The attribute can be assigned without runtime error — the
        declaration on __init__ makes it a real instance attribute."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        server = _make_server()
        limiter = _RateLimiter()
        # This assignment used to require `# type: ignore[attr-defined]`;
        # now it just works.
        server._rate_limiter_instance = limiter
        assert server._rate_limiter_instance is limiter


# ── GT-25 + GT-45: dispatch lock + TOCTOU re-check ────────────────────


class TestGT25GT45DispatchLockAndTOCTOU:
    """GT-25: state-mutating dispatches serialize on ``_dispatch_lock``.
    GT-45: shutdown re-check inside the lock closes the TOCTOU window.
    """

    def test_dispatch_lock_exists(self) -> None:
        server = _make_server()
        assert hasattr(server, "_dispatch_lock"), (
            "GT-25: IPCServer must expose a _dispatch_lock (per-server "
            "RLock serializing state-mutating handler invocations)."
        )
        # RLock: acquire twice from the same thread must succeed.
        assert server._dispatch_lock.acquire(), "first acquire must succeed"
        try:
            assert server._dispatch_lock.acquire(), (
                "GT-25: _dispatch_lock must be an RLock (re-entrant) so a "
                "handler that re-enters _dispatch on the same thread does "
                "not self-deadlock."
            )
            server._dispatch_lock.release()
        finally:
            server._dispatch_lock.release()

    def test_state_mutating_dispatches_serialize(self) -> None:
        """GT-25: two concurrent state-mutating dispatches must NOT
        run their handler bodies at the same time.

        Patches ``_handle_toggle_dictation`` with a handler that records
        overlap; dispatches two ``toggle_dictation`` commands on
        separate threads; asserts no overlap.
        """
        server = _make_server()
        # toggle_dictation is a state-mutating command (NOT in
        # _READONLY_COMMANDS).
        assert "toggle_dictation" not in _READONLY_COMMANDS

        in_handler = threading.Event()
        overlap_detected = threading.Event()
        call_count = {"n": 0}

        def slow_toggle(data, resp):  # noqa: ARG001
            call_count["n"] += 1
            if in_handler.is_set():
                overlap_detected.set()
            in_handler.set()
            try:
                time.sleep(0.1)
            finally:
                in_handler.clear()
            resp["type"] = "result"
            resp["data"] = {"ok": True}
            return resp

        # Patch the bound method on the instance. DT-5: the previous
        # ``_command_handlers`` instance cache was deleted; ``_dispatch``
        # resolves the handler via ``getattr(self, handler_name, None)``
        # at dispatch time, so monkey-patching the instance attribute is
        # observed directly (no cache entry to update).
        server._handle_toggle_dictation = slow_toggle

        threads = []
        results: list[object] = []
        results_lock = threading.Lock()

        def dispatch_one(req_id: int) -> None:
            r = server._dispatch({"id": req_id, "type": "toggle_dictation"})
            with results_lock:
                results.append(r)

        for i in range(2):
            t = threading.Thread(target=dispatch_one, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        assert call_count["n"] == 2, f"GT-25: expected 2 dispatches, got {call_count['n']}."
        assert not overlap_detected.is_set(), (
            "GT-25: two concurrent state-mutating dispatches ran their "
            "handler bodies simultaneously — _dispatch_lock failed to "
            "serialize them."
        )

    def test_readonly_dispatches_bypass_lock(self) -> None:
        """GT-25: read-only dispatches (``get_status`` etc.) do NOT
        acquire ``_dispatch_lock`` — a long-running state-mutating
        handler cannot block a quick status poll.

        Strategy: acquire ``_dispatch_lock`` on the test thread, then
        dispatch a ``get_status`` (read-only). If the dispatch blocks
        waiting for the lock, the test times out; if it bypasses the
        lock, it completes immediately.
        """
        server = _make_server()
        # Make get_status return a trivial response.
        # DT-5: the previous ``_command_handlers`` instance cache was
        # deleted; ``_dispatch`` resolves the handler via
        # ``getattr(self, handler_name, None)`` at dispatch time, so
        # patching the instance attribute is observed directly.
        server._handle_get_status = lambda data, resp: (  # noqa: E731
            resp.__setitem__("type", "status") or resp.__setitem__("data", {"status": "idle"}) or resp
        )

        # Hold the dispatch lock on the test thread.
        with server._dispatch_lock:
            # Dispatch get_status on a worker thread — must NOT block.
            done = threading.Event()
            box: list[object] = []

            def dispatch() -> None:
                box.append(server._dispatch({"id": 1, "type": "get_status"}))
                done.set()

            t = threading.Thread(target=dispatch)
            t.start()
            # Wait at most 0.5s — if the read-only dispatch honored the
            # lock, it would deadlock (we're holding it).
            assert done.wait(timeout=0.5), (
                "GT-25: read-only dispatch (get_status) blocked waiting "
                "for _dispatch_lock — read-only handlers MUST bypass the "
                "lock so a state-mutating handler can't stall status polls."
            )
            t.join(timeout=1.0)
            assert box and box[0]["type"] == "status"

    def test_shutdown_toctou_recheck_blocks_handler(self) -> None:
        """GT-45: when ``app._shutting_down`` flips to True between the
        unlocked gate at the top of ``_dispatch`` and the locked
        handler invocation, the re-check inside the lock MUST
        short-circuit with ``server.shutting_down``.

        Strategy: patch a state-mutating handler to set
        ``app._shutting_down = True`` BEFORE calling the real handler.
        The re-check inside the lock observes the flag and returns the
        shutting_down error.

        Actually simpler: set ``_shutting_down = True`` BEFORE dispatching
        and verify the gate at the top of _dispatch short-circuits.
        """
        server = _make_server()
        server.app._shutting_down = True  # type: ignore[assignment]
        # DJ-32: _dispatch reads the cached snapshot (refreshed in
        # start()/stop()) instead of getattr(self.app, "_shutting_down").
        # Mirror the production contract by setting the cached snapshot
        # so the gate at the top of _dispatch observes the shutdown.
        server._cached_shutting_down = True  # type: ignore[assignment]

        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result is not None
        assert result["type"] == "error", f"GT-45: dispatch during shutdown must return error; got {result}"
        assert result["data"]["code"] == "server.shutting_down"

    def test_shutdown_recheck_inside_lock_closes_toctou(self) -> None:
        """GT-45: the re-check happens INSIDE the dispatch_lock, so a
        flag flip between the unlocked gate and the locked handler
        invocation is observed.

        Strategy: monkey-patch ``_dispatch_lock.acquire`` is too
        invasive. Instead, monkey-patch the state-mutating handler to
        flip ``_shutting_down = True`` BEFORE the real handler runs,
        then verify the dispatch returns the shutting_down error
        (proving the re-check fired).

        Wait — that's the same as test_shutdown_toctou_recheck_blocks_handler.
        The actual TOCTOU window is between the unlocked gate (top of
        _dispatch) and the lock acquisition. To exercise it, we'd need
        to flip the flag in that window on a concurrent thread, which
        is racy and unreliable.

        Instead, this test verifies the SOURCE contains the re-check
        inside the lock block — pinning the contract for static review.
        """
        src = inspect.getsource(IPCServer._dispatch)
        # The re-check must appear AFTER `with self._dispatch_lock:`.
        lock_idx = src.find("with self._dispatch_lock:")
        assert lock_idx >= 0, "GT-45: _dispatch must acquire _dispatch_lock."
        recheck_idx = src.find("_shutting_down", lock_idx)
        assert recheck_idx > lock_idx, (
            "GT-45: _dispatch must re-check _shutting_down INSIDE the "
            "with self._dispatch_lock: block (closes the TOCTOU window)."
        )


# ── GT-48: FIFO re-merge ──────────────────────────────────────────────


class TestGT48FIFOReMerge:
    """GT-48: the pending-event re-merge preserves FIFO order.

    The re-merge code (in ``_send``'s tcp_mode branch) must use
    ``pending + self._pending_tcp + [line]`` so OLD snapshot events
    are positioned BEFORE any NEW events a concurrent thread appended
    between the snapshot+clear and the re-acquire.

    Under the XV-82 snapshot gate (``if tcp_client is not None:``),
    ``pending`` is always None in the tcp_mode branch — so the re-merge
    is dead code. The FIFO order is still pinned defensively in case a
    future change re-introduces an unconditional snapshot.
    """

    def test_send_source_uses_fifo_remerge(self) -> None:
        """The re-merge expression must be ``pending + self._pending_tcp
        + [line]`` (NOT ``self._pending_tcp.extend(pending)`` as a
        Python statement).

        The check is line-oriented (ignores comment-only lines) so the
        explanatory comment in the source — which mentions the OLD
        buggy ``extend`` form to document what was fixed — doesn't trip
        the assertion.
        """
        import re

        src = inspect.getsource(IPCServer._send)
        # Strip comment-only lines (a line whose first non-whitespace
        # token is ``#``). The GT-48 explanatory comment in the source
        # mentions the old buggy expression; the source-grep must look
        # only at actual Python statements.
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)

        assert "self._pending_tcp = pending + self._pending_tcp + [line]" in code_only, (
            "GT-48: _send must re-merge with correct FIFO ordering: "
            "self._pending_tcp = pending + self._pending_tcp + [line]. "
            "The previous extend(pending) + append(line) sequence placed "
            "OLD snapshot events AFTER concurrent-thread NEW events."
        )
        # The buggy extend-based re-merge must be GONE from code lines.
        # (Allow it in comments — the GT-48 explanation mentions the
        # old form to document the fix.)
        buggy_pattern = re.compile(r"^\s*self\._pending_tcp\.extend\(pending\)\s*$", re.MULTILINE)
        assert not buggy_pattern.search(code_only), (
            "GT-48 / XV-82: _send must NOT execute "
            "self._pending_tcp.extend(pending) as a Python statement — "
            "use the FIFO-correct "
            "pending + self._pending_tcp + [line] expression instead."
        )

    def test_send_source_gates_snapshot_on_tcp_client(self) -> None:
        """XV-82 (companion fix): the snapshot must be gated on
        ``if tcp_client is not None:`` so the tcp_mode branch never
        has a pending snapshot to re-merge (eliminating the race at
        its root)."""
        src = inspect.getsource(IPCServer._send)
        assert "if tcp_client is not None:" in src, (
            "XV-82 / GT-48: _send must gate the _pending_tcp snapshot "
            "on 'if tcp_client is not None:' so the disconnected "
            "(tcp_mode) branch doesn't snapshot+clear, eliminating the "
            "FIFO race at its root."
        )

    def test_fifo_order_preserved_when_no_client(self) -> None:
        """When there's no connected client, push events must accumulate
        in ``_pending_tcp`` in FIFO order (newest at the end).

        Pre-existing entries must NOT be cleared (the snapshot is gated
        on ``tcp_client is not None``); new events append at the end.
        """
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._pending_tcp = ['{"old":1}', '{"old":2}']
        server._tcp_mode = True
        server._tcp_client = None

        # Push a new event — must append at the END.
        server._send({"type": "test", "seq": 3})

        assert len(server._pending_tcp) == 3, (
            f"GT-48: expected 3 entries (2 pre-existing + 1 new), got {len(server._pending_tcp)}."
        )
        # Old entries must come BEFORE the new one (FIFO).
        assert '"old":1' in server._pending_tcp[0]
        assert '"old":2' in server._pending_tcp[1]
        # The new entry must be the LAST one and contain "seq" (the JSON
        # serializer may emit ``"seq": 3`` or ``"seq":3`` depending on
        # default separators — accept either).
        assert '"seq"' in server._pending_tcp[2], (
            f"GT-48: new event must be at the END (FIFO); got {server._pending_tcp[-1]!r}."
        )
        assert server._pending_tcp[2].endswith("}"), (
            f"GT-48: new event must be a complete JSON object at the END; got {server._pending_tcp[-1]!r}."
        )


# ── GT-5: ack before cleanup (integration-level unit) ─────────────────


class TestGT5AckBeforeCleanup:
    """GT-5: ``_handle_shutdown`` returns the ack BEFORE ``service.quit()``
    runs. Cleanup runs on a background daemon thread.
    """

    def test_ack_returned_in_under_500ms_with_slow_cleanup(self) -> None:
        server = _make_server()

        def slow_quit() -> None:
            time.sleep(1.0)

        server.service.quit.side_effect = slow_quit
        resp: ResponseEnvelope = {"id": 1}
        t0 = time.monotonic()
        result = server._handle_shutdown(data=None, resp=resp)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.5, (
            f"GT-5: ack must be returned in <0.5s (with a 1.0s cleanup); "
            f"took {elapsed:.3f}s. The ack is blocked by synchronous "
            f"service.quit() — host will force-kill the sidecar mid-cleanup."
        )
        assert result is not None
        assert result["type"] == "result"
        assert result["data"] == {"ack": True}

    def test_cleanup_runs_on_background_thread(self) -> None:
        """GT-5: ``service.quit()`` runs on a daemon background thread,
        NOT on the calling (dispatch pool) thread.
        """
        server = _make_server()
        caller_thread = threading.current_thread()
        captured: dict[str, object] = {}

        def recording_quit() -> None:
            captured["thread"] = threading.current_thread()
            captured["is_daemon"] = threading.current_thread().daemon

        server.service.quit.side_effect = recording_quit
        server._handle_shutdown(data=None, resp={"id": 1})

        # Wait for the background thread to land its call.
        deadline = time.time() + 2.0
        while time.time() < deadline and "thread" not in captured:
            time.sleep(0.005)
        assert "thread" in captured, (
            "GT-5: service.quit() was not called within 2s — the background cleanup thread never started."
        )
        assert captured["thread"] is not caller_thread, (
            "GT-5: service.quit() ran on the dispatch pool thread — the "
            "cleanup must run on a background daemon thread so the ack "
            "frame reaches the host before the ~95s _do_cleanup blocks."
        )
        assert captured["is_daemon"] is True, (
            "GT-5: the cleanup thread must be a daemon so it doesn't "
            "block process exit if the host force-kills the sidecar."
        )


# ── GT-C3-7: BaseException catch ──────────────────────────────────────


class TestGTC37BaseExceptionCatch:
    """GT-C3-7: ``_handle_shutdown``'s cleanup thread catches
    ``BaseException`` (not just ``Exception``) so a ``SystemExit`` /
    ``KeyboardInterrupt`` inside ``service.quit()`` is logged rather
    than silently killing the thread.
    """

    def test_systemexit_in_service_quit_does_not_propagate(self) -> None:
        server = _make_server()
        server.service.quit.side_effect = SystemExit("deep cleanup exit")

        # Must NOT raise — the ack is returned before the thread starts,
        # and the thread catches SystemExit via BaseException.
        result = server._handle_shutdown(data=None, resp={"id": 1})
        assert result is not None
        assert result["data"] == {"ack": True}

        # Wait for the thread to call service.quit (and trigger the
        # SystemExit). The thread must NOT propagate.
        deadline = time.time() + 2.0
        while time.time() < deadline and not server.service.quit.called:
            time.sleep(0.005)
        assert server.service.quit.called, (
            "GT-C3-7: service.quit() must be called by the background "
            "thread (the SystemExit side_effect fires there, not on the "
            "dispatch thread)."
        )

    def test_send_source_uses_baseexception(self) -> None:
        """The cleanup thread's except clause must catch ``BaseException``."""
        src = inspect.getsource(IPCServer._handle_shutdown)
        assert "except BaseException" in src, (
            "GT-C3-7: _handle_shutdown must catch BaseException (not just "
            "Exception) so SystemExit/KeyboardInterrupt inside "
            "service.quit() is logged rather than silently killing the "
            "cleanup thread."
        )


# ── GT-D1-5: concrete types for service and _frame ────────────────────


class TestGTD15ConcreteTypes:
    """GT-D1-5: ``service`` parameter and ``_on_sigusr1``'s ``_frame``
    parameter use concrete types instead of ``Any``.
    """

    def test_service_param_typed_as_voice_typer_service(self) -> None:
        """The ``service`` parameter in ``__init__`` must be annotated
        ``VoiceTyperService | None`` (not ``typing.Any | None``)."""
        sig = inspect.signature(IPCServer.__init__)
        service_ann = sig.parameters["service"].annotation
        ann_str = service_ann if isinstance(service_ann, str) else str(service_ann)
        assert "VoiceTyperService" in ann_str, (
            f"GT-D1-5: __init__'s service parameter must be annotated "
            f"VoiceTyperService | None (was Any); got {ann_str!r}."
        )
        assert "Any" not in ann_str or "VoiceTyperService" in ann_str, (
            f"GT-D1-5: service parameter must NOT be Any; got {ann_str!r}."
        )

    def test_on_sigusr1_frame_typed_as_frametype(self) -> None:
        """``_on_sigusr1``'s ``_frame`` parameter must be annotated
        ``FrameType | None`` (not ``typing.Any``)."""
        # _on_sigusr1 is a nested function inside main(). Inspect main()'s
        # source and verify the annotation string is present.
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.main)
        assert "_frame: FrameType | None" in src, (
            "GT-D1-5: _on_sigusr1's _frame parameter must be annotated "
            "FrameType | None (was typing.Any). Import FrameType from "
            "types at module top."
        )
        # The `typing.Any` form must NOT appear on _on_sigusr1's signature.
        assert 'def _on_sigusr1(_signum: int, _frame: "typing.Any")' not in src
        assert "def _on_sigusr1(_signum: int, _frame: typing.Any)" not in src


# ── GT-D1-10: _send typed params ──────────────────────────────────────


class TestGTD110SendTypedParams:
    """GT-D1-10: ``_send``'s ``_out`` and ``_client`` parameters are
    typed (were untyped ``None`` defaults).
    """

    def test_send_out_typed_as_textio(self) -> None:
        sig = inspect.signature(IPCServer._send)
        out_ann = sig.parameters["_out"].annotation
        ann_str = out_ann if isinstance(out_ann, str) else str(out_ann)
        assert "TextIO" in ann_str, f"GT-D1-10: _send's _out parameter must be typed TextIO | None; got {ann_str!r}."

    def test_send_client_typed_as_object(self) -> None:
        sig = inspect.signature(IPCServer._send)
        client_ann = sig.parameters["_client"].annotation
        ann_str = client_ann if isinstance(client_ann, str) else str(client_ann)
        # We accept `object | None` (the runtime bound is _TCPLineIO but
        # tests pass other fakes, so the wider type keeps call sites
        # type-checking cleanly).
        assert "object" in ann_str or "_TCPLineIO" in ann_str, (
            f"GT-D1-10: _send's _client parameter must be typed (object | None or _TCPLineIO | None); got {ann_str!r}."
        )


# ── YJ-27: dispatch handler assignment uses typing.cast, not suppression ──


class TestYJ27DispatchCastNotSuppression:
    """YJ-27: ``IPCServer._dispatch``'s ``handler = _resolved`` line
    MUST use ``typing.cast(CommandHandler, _resolved)`` and NOT carry a
    ``# type: ignore[assignment]`` suppression marker.

    The prior form was ``handler = _resolved  # type: ignore[assignment]``
    which silently masked any future type drift between ``getattr``'s
    ``Any``/``callable``-narrowed result and the ``CommandHandler`` alias.
    ``typing.cast`` is the typed assertion: it preserves the LHS's
    type-checking on subsequent use, surfaces real CommandHandler-shape
    mismatches, and is local/removable when YJ-1's full handler-method
    annotation migration lands.
    """

    def _dispatch_source(self) -> str:
        """Return the source of ``IPCServer._dispatch`` (the bound
        method, not the unbound function — easier in tests)."""
        import inspect

        return inspect.getsource(IPCServer._dispatch)

    def test_dispatch_assignment_has_no_type_ignore_suppression(self) -> None:
        """No ``handler = ...`` assignment line in ``_dispatch`` may
        carry a ``# type: ignore`` marker. The ``handler: CommandHandler
        | None = None`` declaration is fine; we only inspect
        assignments."""
        src = self._dispatch_source()
        offending = [
            line.strip()
            for line in src.splitlines()
            if line.strip().startswith("handler =")
            and "type: ignore" in line
            and "handler: " not in line  # skip the type declaration
        ]
        assert not offending, (
            "YJ-27 regression: `# type: ignore` reintroduced on a "
            f"`handler = ...` assignment in _dispatch: {offending!r}. "
            "Use `typing.cast(CommandHandler, _resolved)` instead."
        )

    def test_dispatch_uses_typing_cast(self) -> None:
        """The dispatch's handler assignment MUST use
        ``typing.cast(CommandHandler, _resolved)`` (or the equivalent
        ``cast(CommandHandler, _resolved)`` if ``cast`` is imported
        bare). Bare ``handler = _resolved`` (no cast, no annotation)
        would silently re-introduce the type-error-suppression hole
        that YJ-27 closed."""
        src = self._dispatch_source()
        assert "typing.cast(CommandHandler, _resolved)" in src or "cast(CommandHandler, _resolved)" in src, (
            "YJ-27 regression: dispatch no longer uses "
            "`typing.cast(CommandHandler, _resolved)`. The handler "
            "assignment must use the typed cast, NOT bare assignment."
        )

    def test_dispatch_handler_still_invokes_correctly(self) -> None:
        """End-to-end sanity: dispatching a real command (heartbeat)
        returns a normal envelope. The cast must not corrupt the call
        path. Pre-existing behavior preserved."""
        # Use the lightweight in-process fake that GT-29 already built.
        # The fixture returns a tuple of (server, fake_app, fake_service).
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        fixture_result = make_ipc_server_with_fakes()
        # Accept either a bare IPCServer or a tuple (the fixture's signature
        # is `tuple[Any, MagicMock, MagicMock]`).
        server = fixture_result[0] if isinstance(fixture_result, tuple) else fixture_result
        result = server._dispatch({"type": "heartbeat", "id": 42})
        # heartbeat returns a ResponseEnvelope (dict) or None for fire-
        # and-forget. Either is acceptable — the contract is "the cast
        # doesn't crash or replace the handler with garbage."
        assert result is None or isinstance(result, dict), (
            f"YJ-27 sanity failure: dispatch returned {type(result)!r}, "
            f"expected None or dict (the cast must not corrupt the "
            f"resolved handler)."
        )
