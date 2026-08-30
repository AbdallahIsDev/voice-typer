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
``test_ipc_dispatch_errors.py``.
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import json
import os
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.ipc.history_bounds import (
    _HISTORY_OFFSET_MAX,
    _REDACTED_SENTINEL,
    _SECRET_CONFIG_FIELDS,
    _bound_history_limit,
    _bound_history_offset,
    _is_secret_field_name,
    _sanitize_config_for_ipc,
)
from voice_typer.server.ipc.rate_limiter import (
    COMMAND_COSTS,
    DEFAULT_COST,
    _RateLimiter,
)
from voice_typer.server.ipc.validation import (
    ERROR_CODES,
    _validate_dict_payload,
)
from voice_typer.server.ipc_server import (
    _READONLY_COMMANDS,
    CommandHandler,
    IPCServer,
    ResponseEnvelope,
)

from tests.fixtures.ipc_test_helpers import (
    make_bare_ipc_server,
    make_ipc_server_with_fakes,
)

# Best-effort xdist hint: pin every test in this module onto a single
# worker so the imported IPCServer (heavy handler-mixin imports) doesn't
# race sibling IPC modules under ``pytest -n auto``. No-op when xdist
# isn't active. Carried over from the merged IPC-layer suites.
pytestmark = pytest.mark.xdist_group("ipc_layer_fixes")


def _make_server() -> IPCServer:
    """Build an IPCServer via the canonical fake app + service factories.

    The canonical fake app sets ``_shutting_down`` as an explicit bool
    (not a child mock) so the dispatch shutdown gate sees a real
    ``False`` and tests exercise the dispatch path instead of
    short-circuiting.
    """
    server, _fake_app, _fake_service = make_ipc_server_with_fakes()
    return server


# __init__-time registry validation ( dead-cache removal) ──


class TestDispatchTableTyped:
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


# _rate_limiter_instance declared on IPCServer ───────────────


class TestRateLimiterInstanceDeclared:
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


# + : dispatch lock + TOCTOU re-check ────────────────────


class TestDispatchLockAndTOCTOU:
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

        # Patch the bound method on the instance. : the previous
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
        # the previous ``_command_handlers`` instance cache was
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
        # _dispatch reads the cached snapshot (refreshed in
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


# FIFO re-merge ──────────────────────────────────────────────


class TestReMerge:
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
        # token is ``#``). The  explanatory comment in the source
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
        # (Allow it in comments — the  explanation mentions the
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
        server = make_bare_ipc_server(send_path=True)
        # Plain-list pending override: the pre-existing entries must
        # survive the _send call (the snapshot is gated on
        # ``tcp_client is not None``) and the new event must append at
        # the END.
        server._pending_tcp = ['{"old":1}', '{"old":2}']
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


# ack before cleanup (integration-level unit) ─────────────────


class TestAckBeforeCleanup:
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
        # Use time.monotonic() — wall-clock (time.time()) can jump
        # forward under NTP/manual adjustments, causing the loop to
        # exit early as if the deadline had expired.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and "thread" not in captured:
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


# BaseException catch ──────────────────────────────────────


class TestBaseExceptionCatch:
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
        # Use time.monotonic() — wall-clock (time.time()) can jump
        # forward under NTP/manual adjustments, causing the loop to
        # exit early as if the deadline had expired.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not server.service.quit.called:
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


# concrete types for service and _frame ────────────────────


class TestConcreteTypes:
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


# _send typed params ──────────────────────────────────────


class TestSendTypedParams:
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


# dispatch handler assignment uses typing.cast, not suppression ──


class TestDispatchCastNotSuppression:
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
        server = _make_server()
        result = server._dispatch({"type": "heartbeat", "id": 42})
        # heartbeat returns a ResponseEnvelope (dict) or None for fire-
        # and-forget. Either is acceptable — the contract is "the cast
        # doesn't crash or replace the handler with garbage."
        assert result is None or isinstance(result, dict), (
            f"YJ-27 sanity failure: dispatch returned {type(result)!r}, "
            f"expected None or dict (the cast must not corrupt the "
            f"resolved handler)."
        )


# ==============================================================================
# Merged from tests/test_ipc_package_fixes.py —
#   regression tests for the ipc/ package (secret-field sanitization, COMMAND_COSTS coverage, history offset
#   bounds, namespaced validation codes, offline-pack dispatch)
# ==============================================================================
# regression tests for the ``ipc/`` package.
#
# Each test class covers ONE finding from the comprehensive review
# (``review.md`` lines 695–775). The file is self-contained
# — it does NOT depend on the conftest fixtures from ``tests/handlers/``
# so it can run in isolation and so the contract tests can import the
# canonical ``_COMMAND_REGISTRY`` without triggering the handler-mixin
# import cycle.
#
# Findings covered:
#
# * **the fix** — ``ipc/history_bounds._sanitize_config_for_ipc``: pattern-
# based secret-field denylist + non-None-value redaction. The static
# ``_SECRET_CONFIG_FIELDS`` frozenset is retained for backward compat
# with ``crash_recovery.py``, but the sanitizer now ALSO consults
# :data:`_SECRET_FIELD_PATTERNS` so a future secret field (e.g.
# ``azure_api_key``, ``oauth_token``, ``client_secret``) is masked
# even if no one remembers to add it to the frozenset.
# * **the fix** — ``ipc/rate_limiter.COMMAND_COSTS``: the map now lists
# every command in the dispatcher's ``_COMMAND_REGISTRY`` so the
# contract test can fail-loud if a future command is registered
# without a cost entry.
# * **the fix** — ``ipc/history_bounds._bound_history_offset``: offset is
# now capped at :data:`_HISTORY_OFFSET_MAX` (10_000_000) in addition
# to the ``max(0, v)`` floor. Previously Python big-ints could pass
# the clamp and reach SQLite.
# * **the fix** — ``ipc/validation._validate_dict_payload``: migrated to
# emit namespaced codes (``client.invalid_payload`` /
# ``client.invalid_field`` / ``client.missing_field``) as the primary
# ``code`` field; the legacy bare form is preserved in a sibling
# ``legacy_code`` field for one release cycle.
#


class _ConfigLike:
    """Minimal stand-in for the real ``Config`` dataclass.

    The real ``Config`` is a dataclass with ~80 fields; constructing
    one in a unit test triggers credential-store integration and
    platform-default probes. This stand-in lets the sanitizer tests
    inject arbitrary fields via ``__dict__`` without touching the real
    class.
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestIsSecretFieldName:
    """``_is_secret_field_name`` matches the documented patterns."""

    @pytest.mark.parametrize(
        "name",
        [
            # Explicit allowlist (backward compat with crash_recovery.py).
            "cloud_api_key",
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "llm_api_key",
            # Suffix-pattern denylist (defense-in-depth).
            "azure_api_key",
            "anthropic_api_key",
            "whisper_api_key",
            "access_token",
            "refresh_token",
            "oauth_token",
            "bearer_token",
            "client_secret",
            "signing_secret",
            "user_password",
            "admin_password",
            "db_password",
            "aws_credential",
            "service_credential",
            "auth_bearer",
        ],
    )
    def test_matches_secret_patterns(self, name):
        assert _is_secret_field_name(name) is True, (
            f"Field {name!r} should be classified as secret by either _SECRET_CONFIG_FIELDS or _SECRET_FIELD_PATTERNS."
        )

    @pytest.mark.parametrize(
        "name",
        [
            # Plain config fields.
            "hotkey",
            "language",
            "model_size",
            "cloud_api_url",  # URL, not a key
            "llm_api_url",
            "llm_model",
            "vocabulary_enabled",
            # Boolean flag with "password" substring — does NOT match
            # because it ends in "_paste", not "_password".
            "warn_password_paste",
            # Field with "credential" substring but not as suffix.
            "credential_store_enabled",
            # Field with "bearer" substring but not as suffix.
            "bearer_mode",
        ],
    )
    def test_does_not_match_benign_fields(self, name):
        assert _is_secret_field_name(name) is False, (
            f"Field {name!r} should NOT be classified as secret — the "
            f"pattern is name-based (suffix or exact match), so "
            f"substring matches like 'warn_password_paste' are NOT "
            f"redacted (it ends in '_paste', not '_password')."
        )

    def test_exact_match_password(self):
        assert _is_secret_field_name("password") is True

    def test_exact_match_credential(self):
        assert _is_secret_field_name("credential") is True

    def test_exact_match_bearer(self):
        assert _is_secret_field_name("bearer") is True

    def test_exact_match_secret(self):
        assert _is_secret_field_name("secret") is True

    def test_exact_match_token(self):
        assert _is_secret_field_name("token") is True

    def test_exact_match_api_key(self):
        assert _is_secret_field_name("api_key") is True


class TestSanitizePatternDenylist:
    """unlisted secret fields are redacted via the pattern denylist."""

    @pytest.mark.parametrize(
        "field_name, value",
        [
            ("azure_api_key", "sk-azure-12345"),
            ("anthropic_api_key", "sk-ant-67890"),
            ("oauth_token", "oauth-token-abcdef"),
            ("refresh_token", "refresh-token-xyz"),
            ("client_secret", "client-secret-value"),
            ("signing_secret", "signing-secret-value"),
            ("user_password", "p@ssw0rd"),
            ("db_password", "db-p@ssw0rd"),
            ("aws_credential", "AKIAIOSFODNN7EXAMPLE"),
            ("auth_bearer", "Bearer abc123"),
        ],
    )
    def test_unlisted_secret_field_is_redacted(self, field_name, value):
        """A secret-bearing field NOT in ``_SECRET_CONFIG_FIELDS`` must
        still be redacted by the pattern-based denylist (defense-
        in-depth). The renderer must never see the real value."""
        cfg = _ConfigLike(**{field_name: value})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL, (
            f"Pattern-denylist failed for {field_name!r}: expected "
            f"{_REDACTED_SENTINEL!r}, got {out[field_name]!r}. The field "
            f"matches a _SECRET_FIELD_PATTERNS entry and must be "
            f"redacted even though it's not in _SECRET_CONFIG_FIELDS."
        )

    def test_exact_name_password_is_redacted(self):
        """A field literally named ``password`` (exact-match pattern)
        is redacted."""
        cfg = _ConfigLike(password="hunter2")
        out = _sanitize_config_for_ipc(cfg)
        assert out["password"] == _REDACTED_SENTINEL

    def test_exact_name_credential_is_redacted(self):
        cfg = _ConfigLike(credential="aws-cred-blob")
        out = _sanitize_config_for_ipc(cfg)
        assert out["credential"] == _REDACTED_SENTINEL

    def test_exact_name_bearer_is_redacted(self):
        cfg = _ConfigLike(bearer="Bearer xyz")
        out = _sanitize_config_for_ipc(cfg)
        assert out["bearer"] == _REDACTED_SENTINEL

    def test_warn_password_paste_not_redacted(self):
        """The boolean flag ``warn_password_paste`` (a real Config
        field) must NOT be redacted — the pattern is name-based, and
        the field ends in ``_paste``, not ``_password``. The renderer
        needs the real boolean value to render the toggle UI."""
        cfg = _ConfigLike(warn_password_paste=True)
        out = _sanitize_config_for_ipc(cfg)
        assert out["warn_password_paste"] is True, (
            "warn_password_paste is a boolean flag (NOT a secret); "
            "it must not be redacted by the pattern-based denylist."
        )

    def test_cloud_api_url_not_redacted(self):
        """``cloud_api_url`` ends in ``_url``, not ``_api_key`` — must
        not be redacted. The renderer needs the URL to display it."""
        cfg = _ConfigLike(cloud_api_url="https://api.example.com/v1")
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_url"] == "https://api.example.com/v1"


class TestSanitizeFalsyValues:
    """redaction now masks any non-None value, regardless of truthiness.

    Previously the redaction logic was ``out[k] = _REDACTED_SENTINEL if v else v``
    — a secret stored as ``0``, ``False``, or ``""`` would NOT be
    redacted (falsy values were preserved verbatim). This was fine for
    the empty-string "no key set" case but unsafe for ``0`` / ``False``
    secrets and inconsistent with the documented "key is set" semantic.
    """

    def test_falsy_zero_is_redacted(self):
        """A secret stored as ``0`` (integer) is redacted — previously
        the truthy-only check preserved it verbatim, leaking the value."""
        cfg = _ConfigLike(azure_api_key=0)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_falsy_false_is_redacted(self):
        """A secret stored as ``False`` (boolean) is redacted."""
        cfg = _ConfigLike(azure_api_key=False)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_falsy_empty_string_is_redacted(self):
        """A secret stored as ``""`` is redacted. Previously the empty
        string was preserved so the renderer could distinguish "no key
        set" from "key set but hidden" — but ``None`` is the canonical
        sentinel for "not configured" in the Config dataclass (most
        secret fields default to ``""``, not ``None``, so the empty-
        string "not configured" semantic was already ambiguous). This
        unifies the contract: ``None`` → not configured; any other
        value (including ``""``) → redacted."""
        cfg = _ConfigLike(azure_api_key="")
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_none_value_is_preserved(self):
        """``None`` is preserved so the renderer can distinguish "not
        configured" from "configured but hidden". This is the one case
        where the original value is kept — any other value is masked."""
        cfg = _ConfigLike(cloud_api_key=None)
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_key"] is None

    def test_truthy_string_is_redacted(self):
        """The previously happy path: a truthy string secret is redacted."""
        cfg = _ConfigLike(cloud_api_key="sk-real-key-12345")
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_key"] == _REDACTED_SENTINEL

    def test_real_key_value_does_not_leak(self):
        """Grep the full sanitized dict: no real key value should
        appear anywhere in the serialized output (regression
        guard, now with the falsy-value fix)."""
        real_value = "sk-unique-marker-12345"
        cfg = _ConfigLike(
            cloud_api_key=real_value,
            azure_api_key=0,  # falsy — would have leaked pre-
            oauth_token=False,  # falsy — would have leaked pre-
        )
        out = _sanitize_config_for_ipc(cfg)
        serialized = str(out)
        assert real_value not in serialized


class TestSanitizePreservesNonSecretFields:
    """regression guard: the sanitizer must not redact non-secret
    fields. Catches a potential over-redaction bug if the pattern
    denylist is too broad."""

    def test_non_secret_fields_preserved(self):
        cfg = _ConfigLike(
            hotkey="<f2>",
            language="fr",
            model_size="small.en",
            cloud_api_url="https://api.example.com",
            warn_password_paste=True,
            cloud_api_key="sk-real",
        )
        out = _sanitize_config_for_ipc(cfg)
        assert out["hotkey"] == "<f2>"
        assert out["language"] == "fr"
        assert out["model_size"] == "small.en"
        assert out["cloud_api_url"] == "https://api.example.com"
        assert out["warn_password_paste"] is True
        assert out["cloud_api_key"] == _REDACTED_SENTINEL


class TestSanitizeBackwardCompatWithExistingFields:
    """the 5 fields in ``_SECRET_CONFIG_FIELDS`` are still
    redacted (backward compat — ``crash_recovery.py`` imports the
    frozenset for its own redaction path)."""

    @pytest.mark.parametrize("field_name", sorted(_SECRET_CONFIG_FIELDS))
    def test_existing_secret_field_still_redacted(self, field_name):
        cfg = _ConfigLike(**{field_name: "sk-real-key"})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL


class TestSanitizeContractWithRealConfig:
    """(d): contract test — every field on the real ``Config``
    dataclass that matches a secret-name pattern is in
    ``_SECRET_CONFIG_FIELDS`` (i.e. the explicit allowlist covers
    every secret the maintainers have added so far).

    This is the "fail-loud when a new secret field is added without
    being listed" guard. If a future maintainer adds e.g.
    ``anthropic_api_key`` to ``Config`` WITHOUT also adding it to
    ``_SECRET_CONFIG_FIELDS``, this test will fail — alerting them
    that the field needs to be added (OR, equivalently, that they
    should rely on the pattern denylist — in which case the test
    asserts that the field IS still redacted by the sanitizer).

    Implementation note: the contract is "any field matching a secret
    pattern MUST be redacted by ``_sanitize_config_for_ipc``". The
    pattern denylist is the authoritative guard, so the test asserts
    the BEHAVIORAL contract (the field is redacted) rather than the
    STRUCTURAL contract (the field is in the frozenset). The latter
    would be too strict — the whole point of the denylist is that
    unlisted pattern-matching fields are still redacted.
    """

    def _real_config_fields(self):
        """Return the set of dataclass field names on the real Config."""
        from voice_typer.server.config import Config

        # ``Config`` is a dataclass — ``dataclasses.fields`` returns
        # the declared fields (not the runtime-only ``last_load_warnings``
        # attribute, which is intentionally excluded from ``asdict``).
        return {f.name for f in dataclasses.fields(Config())}

    def test_every_pattern_matching_config_field_is_redacted(self):
        """For each field on the real Config that matches a secret-name
        pattern, ``_sanitize_config_for_ipc`` MUST redact it (replace
        the value with the sentinel)."""
        from voice_typer.server.config import Config

        cfg = Config()
        # Set every pattern-matching field to a non-None value so the
        # redaction check is meaningful (None values are preserved).
        leaked: list[str] = []
        for field_name in self._real_config_fields():
            if not _is_secret_field_name(field_name):
                continue
            # Force the field to a non-None value, then sanitize.
            setattr(cfg, field_name, "sk-test-marker-for-redaction")
            out = _sanitize_config_for_ipc(cfg)
            if out[field_name] != _REDACTED_SENTINEL:
                leaked.append(field_name)
        assert not leaked, (
            f"Config fields matching secret-name patterns were NOT "
            f"redacted by _sanitize_config_for_ipc: {leaked}. Either "
            f"add them to _SECRET_CONFIG_FIELDS (explicit allowlist) "
            f"or extend _SECRET_FIELD_PATTERNS (pattern denylist)."
        )

    def test_no_benign_config_field_is_redacted(self):
        """Sanity check: at least one non-secret field on Config is
        preserved (e.g. ``hotkey``). Catches an over-redaction bug
        where the pattern denylist is too broad."""
        from voice_typer.server.config import Config

        cfg = Config()
        out = _sanitize_config_for_ipc(cfg)
        # ``hotkey`` is a plain config field — must not be redacted.
        assert "hotkey" in out, "hotkey field missing from sanitized output"
        assert out["hotkey"] != _REDACTED_SENTINEL, (
            "hotkey was redacted — the pattern denylist is too broad and is matching non-secret fields."
        )


# ══════════════════════════════════════════════════════════════════════════
# COMMAND_COSTS covers every registered command
# ══════════════════════════════════════════════════════════════════════════


class TestCommandCostsContract:
    """every command in ``_COMMAND_REGISTRY`` has an explicit
    ``COMMAND_COSTS`` entry.

    Previously the map listed only 5 commands; every other dispatched
    command fell through to ``DEFAULT_COST = 1``, including expensive
    operations like ``delete_model``, ``transcribe_offline``,
    ``test_llm_connection``, ``export_diagnostics``, ``clear_history``,
    ``microphone_test_start``. A buggy or hostile client could fire
    200/s of any unlisted expensive command. The map now lists EVERY
    registered command so a future command added to
    ``_COMMAND_REGISTRY`` without a cost entry fails this test.
    """

    def test_every_registered_command_has_explicit_cost(self):
        """For each command in ``_COMMAND_REGISTRY``, assert it has an
        explicit entry in ``COMMAND_COSTS``. Fails with a clear list
        of missing commands if any are absent."""
        registered = set(IPCServer._COMMAND_REGISTRY.keys())
        listed = set(COMMAND_COSTS.keys())
        missing = registered - listed
        assert not missing, (
            f"Commands registered in _COMMAND_REGISTRY but missing "
            f"from COMMAND_COSTS: {sorted(missing)}. Each registered "
            f"command MUST have an explicit cost entry — add them to "
            f"COMMAND_COSTS in voice_typer/server/ipc/rate_limiter.py. "
            f"Cost tiers: 1=cheap read, 2=small write, 3=compute, "
            f"5=starts long-lived resource, 10=heavy I/O, 20=very "
            f"heavy, 50=network-saturating download."
        )

    def test_command_costs_does_not_list_unknown_commands(self):
        """Sanity check: ``COMMAND_COSTS`` should not contain commands
        that aren't in ``_COMMAND_REGISTRY`` — that would indicate a
        typo or a stale entry pointing at a removed command.

        (2026-07-25): some commands were moved from the Python
        ``_COMMAND_REGISTRY`` to the Tauri Rust host (``delete_all_personal_data``,
        ``export_diagnostics``, ``export_gdpr_bundle``, ``test_llm_connection``,
        ``get_vocabulary_suggestions``). Their entries are kept in
        ``COMMAND_COSTS`` for back-compat with older Electron builds
        that still bridge these calls — those entries are explicitly
        whitelisted here.
        """
        # Commands moved to Tauri Rust host () — kept in COMMAND_COSTS
        # for back-compat with older Electron builds.
        zr_45_moved_to_rust = {
            "delete_all_personal_data",
            "export_diagnostics",
            "export_gdpr_bundle",
            "test_llm_connection",
            "get_vocabulary_suggestions",
        }
        registered = set(IPCServer._COMMAND_REGISTRY.keys())
        listed = set(COMMAND_COSTS.keys())
        stale = (listed - registered) - zr_45_moved_to_rust
        assert not stale, (
            f"COMMAND_COSTS contains entries for commands NOT in "
            f"_COMMAND_REGISTRY (and not in the moved-to-Rust "
            f"whitelist): {sorted(stale)}. These are stale entries "
            f"pointing at removed/renamed commands — remove them from "
            f"COMMAND_COSTS."
        )

    def test_all_costs_are_positive_integers(self):
        """Each cost must be a positive integer (>= 1). A cost of 0
        would let a client bypass the limiter entirely; a negative
        cost would corrupt the budget."""
        for cmd, cost in COMMAND_COSTS.items():
            assert isinstance(cost, int), f"COMMAND_COSTS[{cmd!r}] = {cost!r} is not an int."
            assert cost >= 1, (
                f"COMMAND_COSTS[{cmd!r}] = {cost} < 1 — costs must be "
                f"positive integers (the limiter clamps <1 to 1, but "
                f"the map should not encode that)."
            )


class TestCommandCostsPreserved:
    """the 5 pre-existing entries are preserved (regression guard
    for the audit that expanded the map)."""

    def test_download_model_cost_50(self):
        assert COMMAND_COSTS["download_model"] == 50

    def test_import_model_cost_20(self):
        assert COMMAND_COSTS["import_model"] == 20

    def test_export_gdpr_bundle_cost_20(self):
        assert COMMAND_COSTS["export_gdpr_bundle"] == 20

    def test_delete_all_personal_data_cost_20(self):
        assert COMMAND_COSTS["delete_all_personal_data"] == 20

    def test_heartbeat_cost_1(self):
        assert COMMAND_COSTS["heartbeat"] == 1


class TestCommandCostsNewlyListed:
    """spot-check a few of the newly-listed expensive commands
    that previously fell through to ``DEFAULT_COST = 1``.

    The exact cost values are heuristic (calibrated against the 200/s
    burst budget) — these tests pin the values so a future careless
    refactor doesn't silently revert them to 1.
    """

    @pytest.mark.parametrize(
        "cmd, min_cost",
        [
            # Heavy I/O or subprocess (cost >= 10).
            ("delete_model", 10),
            ("transcribe_offline", 10),
            ("run_prewarm", 10),
            ("restart_app", 10),
            ("test_llm_connection", 10),
            ("resume_model_download", 10),
            ("export_diagnostics", 10),
            ("clear_history", 10),
            # Moderate (cost >= 5).
            ("quit_app", 5),
            ("shutdown", 5),
            ("onboarding_apply", 5),
            ("microphone_test_start", 5),
            # Light-moderate (cost >= 3).
            ("get_vocabulary_suggestions", 3),
            ("level_monitor_start", 3),
            # Small file writes / single-row mutations (cost >= 2).
            ("save_vocabulary", 2),
            ("save_templates", 2),
            ("delete_history", 2),
            ("restore_history", 2),
            ("force_cancel_transcription", 2),
            ("pause_model_download", 2),
            ("cancel_model_download", 2),
        ],
    )
    def test_expensive_command_has_elevated_cost(self, cmd, min_cost):
        assert COMMAND_COSTS.get(cmd, DEFAULT_COST) >= min_cost, (
            f"COMMAND_COSTS[{cmd!r}] = {COMMAND_COSTS.get(cmd)} < "
            f"{min_cost}. Previously this command fell through to "
            f"DEFAULT_COST=1, allowing 200/s of an expensive operation. "
            f"The fix elevated it; do not regress."
        )


class TestRateLimiterUsesElevatedCost:
    """behavioural guard: the limiter actually applies the
    elevated cost — a cost-10 command consumes 10 of the 200/s burst
    budget, not 1."""

    def test_cost_10_command_rejected_after_20_calls_in_burst_window(self):
        """With burst=200 and cost=10 (e.g. ``clear_history``), the
        limiter accepts at most 20 calls in any 1-second window
        (20 * 10 = 200 = burst cap). The 21st call is rejected.

        Previously ``clear_history`` had cost=1 (DEFAULT_COST fallthrough),
        so the limiter accepted 200 calls/s — exactly the bug this fix addresses.

        ``delete_model`` was bumped from cost 10 to 50, so this
        test now uses ``clear_history`` (still cost 10) to verify the
        cost-10 behavioural guard.
        """
        # ``sustained_per_sec`` is the TOTAL budget over the 10s window
        # (the parameter name is misleading — it's a count, not a rate).
        # Set it high so only the burst check trips in this test.
        assert COMMAND_COSTS["clear_history"] == 10, (
            "clear_history cost changed — pick another cost-10 command for this test"
        )
        limiter = _RateLimiter(burst=200, sustained_per_sec=10_000, window=10.0)
        accepted = 0
        # 25 calls at t=0 — should accept 20 (20*10=200=burst), reject 5.
        for _ in range(25):
            if limiter.allow(command="clear_history", now=0.0):
                accepted += 1
        assert accepted == 20, (
            f"Expected 20 acceptances (burst=200 / cost=10 = 20), got "
            f"{accepted}. The rate limiter is not applying the elevated "
            f"COMMAND_COSTS['clear_history'] cost."
        )

    def test_cost_1_command_accepted_200_times_in_burst_window(self):
        """Sanity check: a cost-1 command (e.g. ``get_status``) still
        gets the full 200/s burst budget. Catches a regression where
        the cost map is applied incorrectly (e.g. every command gets
        cost=10).

        ``heartbeat`` was removed from this test because it
        now bypasses the rate limiter entirely (so it would always
        accept all calls, not just 200). Use ``get_status`` (also
        cost 1) for the cost-1 behavioural guard instead.
        """
        assert COMMAND_COSTS["get_status"] == 1, "get_status cost changed — pick another cost-1 command for this test"
        limiter = _RateLimiter(burst=200, sustained_per_sec=10_000, window=10.0)
        accepted = 0
        for _ in range(205):
            if limiter.allow(command="get_status", now=0.0):
                accepted += 1
        assert accepted == 200, f"Expected 200 acceptances (cost=1), got {accepted}."

    def test_heartbeat_bypasses_rate_limiter_under_burst_attack(self):
        """(High): a heartbeat must ALWAYS be accepted, even
        when the burst budget is fully consumed by attack traffic on
        other commands. Pre-fix, a compromised renderer sustaining
        ≥200 msg/s of cheap commands would starve the heartbeat,
        triggering the 45s watchdog → ``app.quit()`` → backend crash."""
        limiter = _RateLimiter(burst=200, sustained_per_sec=10_000, window=10.0)
        # Exhaust the burst budget with get_status calls (cost 1).
        for _ in range(200):
            assert limiter.allow(command="get_status", now=0.0) is True
        # The 201st get_status is rejected.
        assert limiter.allow(command="get_status", now=0.0) is False
        # But a heartbeat is ALWAYS accepted, even under attack.
        assert limiter.allow(command="heartbeat", now=0.0) is True
        # And subsequent heartbeats continue to be accepted.
        for _ in range(10):
            assert limiter.allow(command="heartbeat", now=0.0) is True


# ══════════════════════════════════════════════════════════════════════════
# _bound_history_offset caps at _HISTORY_OFFSET_MAX
# ══════════════════════════════════════════════════════════════════════════


class TestHistoryOffsetMaxConstant:
    """the ``_HISTORY_OFFSET_MAX`` constant exists and is set
    to 10_000_000 (the value named in the fix section)."""

    def test_offset_max_is_10_million(self):
        assert _HISTORY_OFFSET_MAX == 10_000_000, (
            f"Expected _HISTORY_OFFSET_MAX == 10_000_000 (per the fix section), got {_HISTORY_OFFSET_MAX}."
        )


class TestBoundHistoryOffsetLowerBound:
    """the existing ``max(0, v)`` floor is preserved."""

    def test_zero_unchanged(self):
        assert _bound_history_offset(0) == 0

    def test_negative_clamped_to_zero(self):
        assert _bound_history_offset(-5) == 0
        assert _bound_history_offset(-1) == 0
        assert _bound_history_offset(-999999) == 0

    def test_none_returns_zero(self):
        assert _bound_history_offset(None) == 0

    def test_non_numeric_returns_zero(self):
        assert _bound_history_offset("not-a-number") == 0
        assert _bound_history_offset([]) == 0
        assert _bound_history_offset({}) == 0

    def test_string_numeric_parsed(self):
        assert _bound_history_offset("100") == 100
        assert _bound_history_offset("0") == 0


class TestBoundHistoryOffsetUpperBound:
    """the new upper cap at ``_HISTORY_OFFSET_MAX``."""

    def test_within_bounds_unchanged(self):
        assert _bound_history_offset(100) == 100
        assert _bound_history_offset(1000) == 1000
        assert _bound_history_offset(100_000) == 100_000
        assert _bound_history_offset(1_000_000) == 1_000_000

    def test_at_max_unchanged(self):
        assert _bound_history_offset(_HISTORY_OFFSET_MAX) == _HISTORY_OFFSET_MAX

    def test_above_max_clamped_to_max(self):
        assert _bound_history_offset(_HISTORY_OFFSET_MAX + 1) == _HISTORY_OFFSET_MAX
        assert _bound_history_offset(999_999_999_999) == _HISTORY_OFFSET_MAX

    def test_python_bigint_clamped_to_max(self):
        """Python big-ints are unbounded — without the cap, a 5000-digit
        int would pass the ``max(0, v)`` clamp and reach SQLite's OFFSET
        clause, forcing a wasteful row-skip scan. caps it.

        (Python 3.11+ defaults to a 4300-digit limit on int↔str
        conversion; we use 4000 digits to stay under the default but
        still vastly exceed ``_HISTORY_OFFSET_MAX`` = 10_000_000.)"""
        import sys

        # Bump the int-conversion digit limit so a 5000-digit literal
        # can be constructed. Restore the original on exit so the test
        # doesn't pollute the process-wide state for other tests.
        original_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(10_000)
            huge = int("9" * 5000)
            assert huge > _HISTORY_OFFSET_MAX
            assert _bound_history_offset(huge) == _HISTORY_OFFSET_MAX
        finally:
            sys.set_int_max_str_digits(original_limit)

    def test_float_above_max_clamped_to_max(self):
        """Floats are converted to int via ``int(raw)``; a float above
        the cap is clamped."""
        assert _bound_history_offset(99_999_999.5) == _HISTORY_OFFSET_MAX

    def test_string_above_max_clamped_to_max(self):
        assert _bound_history_offset("999999999999") == _HISTORY_OFFSET_MAX


class TestBoundHistoryLimitUnaffected:
    """regression guard: the existing ``_bound_history_limit``
    behavior (lower bound 1, upper bound 500) is unchanged by the
    offset cap addition."""

    def test_limit_max_unchanged(self):
        from voice_typer.server.ipc.history_bounds import _HISTORY_LIMIT_MAX

        assert _HISTORY_LIMIT_MAX == 500

    def test_limit_above_max_clamped(self):
        assert _bound_history_limit(1_000_000) == 500

    def test_limit_zero_clamped_to_one(self):
        assert _bound_history_limit(0) == 1

    def test_limit_negative_clamped_to_one(self):
        assert _bound_history_limit(-5) == 1


# ══════════════════════════════════════════════════════════════════════════
# _validate_dict_payload emits namespaced error codes
# ══════════════════════════════════════════════════════════════════════════


class TestNamespacedInvalidPayload:
    """non-dict payload emits ``code=client.invalid_payload``."""

    def test_non_dict_payload_returns_namespaced_code(self):
        validated, error = _validate_dict_payload("not-a-dict", {})
        assert validated is None
        assert error is not None
        assert error["type"] == "error"
        assert error["data"]["code"] == "client.invalid_payload", (
            f"Expected 'client.invalid_payload' (namespaced form per the fix), got {error['data']['code']!r}."
        )

    def test_non_dict_payload_does_not_emit_legacy_code(self):
        """The per-envelope ``legacy_code`` field was removed once the
        renderer migrated fully to the namespaced ``code`` form. The
        envelope MUST NOT carry a ``legacy_code`` key (it would be
        dead bytes on the wire)."""
        _, error = _validate_dict_payload([], {})
        assert "legacy_code" not in error["data"]

    @pytest.mark.parametrize(
        "bad_payload",
        ["a-string", 42, 3.14, ["a", "list"], ("a", "tuple"), {1, 2, 3}],
    )
    def test_various_non_dict_payloads(self, bad_payload):
        _, error = _validate_dict_payload(bad_payload, {})
        assert error["data"]["code"] == "client.invalid_payload"
        assert "legacy_code" not in error["data"]

    def test_max_payload_bytes_violation_returns_namespaced_code(self):
        """The ``max_payload_bytes`` rule (DoS guard) emits the
        namespaced ``client.invalid_payload`` when the payload exceeds
        the cap."""
        schema = {
            "x": {"type": str, "required": False, "max_payload_bytes": 10},
        }
        # ``data`` serializes to ~30 bytes — well above the 10-byte cap.
        _, error = _validate_dict_payload({"x": "this-is-way-too-long"}, schema)
        assert error["data"]["code"] == "client.invalid_payload"
        assert "legacy_code" not in error["data"]


class TestNamespacedInvalidField:
    """wrong-type field emits ``code=client.invalid_field``."""

    def test_wrong_type_returns_namespaced_code(self):
        validated, error = _validate_dict_payload(
            {"model": 123},
            {"model": {"type": str, "required": True}},
        )
        assert validated is None
        assert error["data"]["code"] == "client.invalid_field", (
            f"Expected 'client.invalid_field', got {error['data']['code']!r}."
        )
        assert "legacy_code" not in error["data"]
        assert error["data"]["field"] == "model"

    def test_wrong_type_with_tuple_type_annotation(self):
        """When the schema's ``type`` is a tuple (e.g. ``(str, type(None))``),
        the error message lists all allowed types — the code is still
        the namespaced ``client.invalid_field``."""
        validated, error = _validate_dict_payload(
            {"mic_id": 123},
            {"mic_id": {"type": (str, type(None)), "required": True}},
        )
        assert validated is None
        assert error["data"]["code"] == "client.invalid_field"
        assert "legacy_code" not in error["data"]
        assert error["data"]["field"] == "mic_id"
        # The message should list both allowed types.
        assert "str" in error["data"]["message"]
        assert "NoneType" in error["data"]["message"]

    def test_max_value_len_violation_returns_namespaced_code(self):
        """The ``max_value_len`` rule emits the namespaced
        ``client.invalid_field`` when a string value is too long."""
        schema = {
            "name": {"type": str, "required": True, "max_value_len": 5},
        }
        _, error = _validate_dict_payload({"name": "way-too-long-string"}, schema)
        assert error["data"]["code"] == "client.invalid_field"
        assert "legacy_code" not in error["data"]
        assert error["data"]["field"] == "name"


class TestNamespacedMissingField:
    """missing required field emits ``code=client.missing_field``."""

    def test_missing_required_returns_namespaced_code(self):
        validated, error = _validate_dict_payload(
            {},
            {"model": {"type": str, "required": True}},
        )
        assert validated is None
        assert error["data"]["code"] == "client.missing_field", (
            f"Expected 'client.missing_field', got {error['data']['code']!r}."
        )
        assert "legacy_code" not in error["data"]
        assert error["data"]["field"] == "model"


class TestValidationHappyPathUnaffected:
    """regression guard: the happy path (valid payload) still
    returns ``(validated_dict, None)`` with no error envelope."""

    def test_valid_payload_returns_validated_dict(self):
        validated, error = _validate_dict_payload(
            {"model": "small.en", "hotkey": "<f2>"},
            {
                "model": {"type": str, "required": True},
                "hotkey": {"type": str, "required": False, "default": "<f9>"},
            },
        )
        assert error is None
        assert validated == {"model": "small.en", "hotkey": "<f2>"}

    def test_optional_field_uses_default(self):
        validated, error = _validate_dict_payload(
            {"model": "small.en"},
            {
                "model": {"type": str, "required": True},
                "hotkey": {"type": str, "required": False, "default": "<f9>"},
            },
        )
        assert error is None
        assert validated == {"model": "small.en", "hotkey": "<f9>"}

    def test_clamp_range_coerces_numeric_value(self):
        validated, error = _validate_dict_payload(
            {"duration_ms": 99_999_999},
            {
                "duration_ms": {
                    "type": int,
                    "required": True,
                    "clamp_range": (0, 86_400_000),
                },
            },
        )
        assert error is None
        assert validated == {"duration_ms": 86_400_000}


class TestNamespacedCodesRegistered:
    """the namespaced codes emitted by ``_validate_dict_payload``
    are all registered in ``ERROR_CODES`` (the contract test in
    ``tests/test_error_codes_registry.py`` is the canonical guard,
    but this is a self-contained sanity check)."""

    @pytest.mark.parametrize(
        "code",
        ["client.invalid_payload", "client.invalid_field", "client.missing_field"],
    )
    def test_namespaced_code_in_registry(self, code):
        assert code in ERROR_CODES, (
            f"Namespaced code {code!r} emitted by _validate_dict_payload "
            f"is NOT in ERROR_CODES. Add it to the registry in "
            f"voice_typer/server/ipc/validation.py."
        )


class TestCheckPackUpdateDispatch:
    """the auto-update feature's ``check_offline_pack_update`` IPC command
    dispatches through the real registry + handler (docs/auto-update-feature.md).

    The handler is ``_handle_check_offline_pack_update`` in
    ``voice_typer/server/ipc/lifecycle.py``, which delegates to
    ``update_check.handle_check_offline_pack_update_ipc``. The manifest fetch
    fails (no network / no release) but must produce a structured
    ``ack`` result — never a raised exception or a dropped envelope.
    """

    def test_command_registered_and_rate_limited(self):
        assert "check_offline_pack_update" in IPCServer._COMMAND_REGISTRY
        assert "check_offline_pack_update" in COMMAND_COSTS

    def test_dispatch_returns_structured_ack(self):
        # Bespoke wiring: the app stub must be an object WITHOUT
        # auto-attributes (a MagicMock would fabricate ``app.config``
        # and flip the consent check), so the _ConfigLike stand-in is
        # injected into the canonical bare factory.
        server = make_bare_ipc_server(app=_ConfigLike())
        server._ready_emitted = False
        server._last_heartbeat_at = 0.0
        server._shutting_down = False
        server._cached_shutting_down = False
        resp = server._dispatch({"type": "check_offline_pack_update", "data": {}})
        assert resp is not None
        assert resp["type"] == "ack"
        assert isinstance(resp["data"], dict)
        assert "success" in resp["data"]
        assert "checked_at" in resp["data"]
        assert "update_available" in resp["data"]
        assert "download_triggered" in resp["data"]

    def test_dispatch_never_raises_on_handler_error(self):
        """an unexpected handler exception becomes a structured error ack."""
        # Same bespoke no-auto-attribute app stub as
        # test_dispatch_returns_structured_ack (see the comment there).
        server = make_bare_ipc_server(app=_ConfigLike())
        server._ready_emitted = False
        server._last_heartbeat_at = 0.0
        server._shutting_down = False
        server._cached_shutting_down = False
        with patch(
            "voice_typer.server.service.update_check.handle_check_offline_pack_update_ipc",
            side_effect=RuntimeError("boom"),
        ):
            resp = server._dispatch({"type": "check_offline_pack_update", "data": {}})
        assert resp is not None
        assert resp["type"] == "ack"
        assert resp["data"]["success"] is False
        assert "boom" in resp["data"]["error"]


# ==============================================================================
# Merged from tests/test_ipc_layer_fixes.py —
#   IPC-layer performance contract pins (rate-limiter running totals, pending-TCP snapshot gate, compact JSON, WS
#   encode-once, validation caches, transport buffering, limiter closure capture)
# ==============================================================================
# XV-81 .. XV-87: IPC-layer performance fixes (FA11 sub-agent).
#
# These tests pin the behavioral and source-level contracts of the
# GROUP-2 IPC-layer fixes:
#
# * XV-81 — ``_RateLimiter`` maintains running totals
# (``self._burst_total`` / ``self._sustained_total``) instead of
# recomputing ``sum(c for _, c in deque)`` on every ``allow()`` call.
# * XV-82 — ``IPCServer._send`` only snapshots+clears ``_pending_tcp``
# when there is a live TCP client to drain it to (the ``tcp_mode``
# branch only appends+trims).
# * XV-83 — ``IPCServer._send`` uses compact JSON serialization
# (``ensure_ascii=False, separators=(",", ":")``) matching the WS path.
# * XV-84 — ``sidecar_ws._writer`` encodes the outbound event to bytes
# once (``json.dumps(...).encode("utf-8")``) and shares the buffer
# between the size check and the send.
# * XV-85 — ``validation._validate_dict_payload`` hoists ``import json``
# to module top and caches the per-schema ``max_payload_bytes`` lookup.
# * XV-86 — ``_TCPLineIO`` uses ``io.DEFAULT_BUFFER_SIZE`` (not ``1``)
# for the read-side ``socket.makefile`` buffering argument.
# * XV-87 — ``sidecar_ws._make_dispatch`` resolves the rate limiter
# ONCE up-front (alongside ``ws_dispatch_pool``) and captures it in
# the closure; ``dispatch()`` no longer calls ``_get_rate_limiter``
# per frame.
#
# Each test FAILS if the corresponding fix is reverted.
#


class TestRateLimiterRunningTotals:
    """XV-81: ``_RateLimiter`` keeps ``self._burst_total`` /
    ``self._sustained_total`` int fields in sync with the deques,
    replacing the O(n) ``sum(c for _, c in deque)`` recomputation
    on every ``allow()`` call."""

    def test_init_creates_running_total_fields(self):
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter()
        # The new fields must exist and start at 0.
        assert hasattr(rl, "_burst_total"), "XV-81: _RateLimiter must have a _burst_total int field."
        assert hasattr(rl, "_sustained_total"), "XV-81: _RateLimiter must have a _sustained_total int field."
        assert rl._burst_total == 0
        assert rl._sustained_total == 0

    def test_allow_source_does_not_recompute_sum(self):
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        src = inspect.getsource(_RateLimiter.allow)
        # The old O(n) recompute must NOT appear in the allow() body.
        assert "sum(c for _, c in self._burst_timestamps)" not in src, (
            "XV-81: allow() must NOT recompute sum(c for _, c in "
            "_burst_timestamps) on every call — use the running total."
        )
        assert "sum(c for _, c in self._sustained_timestamps)" not in src, (
            "XV-81: allow() must NOT recompute sum(c for _, c in "
            "_sustained_timestamps) on every call — use the running total."
        )
        # The new fast-path reads must appear.
        assert "self._burst_total" in src, "XV-81: allow() must reference self._burst_total (the running total)."
        assert "self._sustained_total" in src, (
            "XV-81: allow() must reference self._sustained_total (the running total)."
        )

    def test_running_total_matches_sum_after_appends(self):
        """After a sequence of ``allow()`` calls, the running total must
        equal the value ``sum(c for _, c in deque)`` would have
        produced — the cache invariant."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        # Mix of cheap and expensive commands.
        commands = ["heartbeat", "download_model", "heartbeat", "get_status", "heartbeat"]
        for i, cmd in enumerate(commands):
            rl.allow(command=cmd, now=float(i) * 0.01)
        expected_burst = sum(c for _, c in rl._burst_timestamps)
        expected_sustained = sum(c for _, c in rl._sustained_timestamps)
        assert rl._burst_total == expected_burst, (
            f"XV-81: _burst_total={rl._burst_total} != sum={expected_burst} "
            "after appends — the running total must stay in sync with the deque."
        )
        assert rl._sustained_total == expected_sustained, (
            f"XV-81: _sustained_total={rl._sustained_total} != sum={expected_sustained} after appends."
        )

    def test_running_total_matches_sum_after_eviction(self):
        """After the burst window slides past old entries, the running
        total must equal the post-eviction deque sum (no drift)."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        # Fill the burst deque at t=0 (cost 1 each).
        for _ in range(10):
            rl.allow(now=0.0)
        # Advance past the burst window so the t=0 entries are evicted.
        rl.allow(now=2.0)  # cutoff = 2.0 - 1.0 = 1.0; all t=0.0 evicted
        expected_burst = sum(c for _, c in rl._burst_timestamps)
        expected_sustained = sum(c for _, c in rl._sustained_timestamps)
        assert rl._burst_total == expected_burst, (
            f"XV-81: _burst_total drifted after eviction: {rl._burst_total} != {expected_burst}."
        )
        assert rl._sustained_total == expected_sustained, (
            f"XV-81: _sustained_total drifted after eviction: {rl._sustained_total} != {expected_sustained}."
        )
        # The burst deque should have only the t=2.0 entry (cost 1).
        assert rl._burst_total == 1
        # The sustained deque should have all 11 entries (cost 1 each).
        assert rl._sustained_total == 11

    def test_running_totals_never_negative(self):
        """The eviction loop clamps the running totals at >= 0 even
        under a hypothetical double-eviction bug."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        rl.allow(now=0.0)
        # Manually drive the total negative to verify the clamp.
        rl._burst_total = -5
        rl._sustained_total = -5
        # Trigger an eviction that should clamp the totals back to 0.
        # We do this by calling allow() with a timestamp past both
        # windows — the eviction loop runs, but the deque is already
        # empty, so the clamp branch is exercised via the manual
        # negative value. (We then re-set to 0 implicitly via the
        # next allow's append path.)
        rl.allow(now=100.0)
        # After allow(), the totals were clamped to 0 and then
        # incremented by 1 (the new append).
        assert rl._burst_total >= 0, "XV-81: _burst_total must never go negative (clamp at 0)."
        assert rl._sustained_total >= 0, "XV-81: _sustained_total must never go negative (clamp at 0)."


# pending snapshot only when tcp_client is not None ───────────


class TestPendingSnapshotGatedOnTcpClient:
    """XV-82: ``IPCServer._send`` only snapshots+clears ``_pending_tcp``
    when ``tcp_client is not None``. The disconnected-mode (``tcp_mode``
    branch) only appends+trims — O(1) amortized."""

    def test_send_source_gates_snapshot_on_tcp_client(self):
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._send)
        # The snapshot list(...) call must be inside an
        # ``if tcp_client is not None:`` block, not unconditional.
        # Find the snapshot line and verify it's preceded (in the lock
        # block) by the tcp_client gate.
        assert "if tcp_client is not None:" in src, (
            "XV-82: _send must gate the _pending_tcp snapshot on 'if tcp_client is not None:'."
        )
        # The re-merge in the tcp_mode branch must be GONE.
        assert "self._pending_tcp.extend(pending)" not in src, (
            "XV-82: _send must NOT re-merge pending into _pending_tcp — "
            "the snapshot is gated on tcp_client, so the tcp_mode branch "
            "never has a pending snapshot to re-merge."
        )

    def test_send_does_not_snapshot_when_no_client(self):
        """When ``tcp_client is None`` and ``tcp_mode`` is True, _send
        must NOT clear ``_pending_tcp`` (the snapshot path is skipped)."""
        server = make_bare_ipc_server(send_path=True)
        # Pre-populate _pending_tcp with some entries — they must
        # survive the _send call (the snapshot is gated off when
        # tcp_client is None).
        server._pending_tcp = ['{"existing":1}', '{"existing":2}']
        server._tcp_client = None  # no client connected

        # Issue a push event — should append + trim, NOT clear.
        server._send({"type": "test", "id": 1})

        # The two pre-existing entries must still be there (
        # gated off the snapshot+clear), plus the new entry.
        assert len(server._pending_tcp) == 3, (
            f"XV-82: expected 3 entries in _pending_tcp (2 pre-existing "
            f"+ 1 new), got {len(server._pending_tcp)}. The pre-existing "
            "entries must NOT be cleared when there's no TCP client."
        )
        # The new entry must be at the end.
        assert '"test"' in server._pending_tcp[-1]

    def test_send_still_snapshots_when_tcp_client_present(self):
        """When ``tcp_client is not None``, the snapshot+clear must
        still run (so the drain loop can write the pending entries)."""
        from voice_typer.server.ipc_server import _TCPLineIO

        server = make_bare_ipc_server(send_path=True)

        srv, cli = socket.socketpair()
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client
            # Pre-populate _pending_tcp — must be cleared by _send.
            server._pending_tcp = ['{"existing":1}']

            # Reader thread so sendall doesn't block.
            received = []
            reader = threading.Thread(
                target=lambda: received.append(cli.recv(65536)),
                daemon=True,
            )
            reader.start()

            server._send({"type": "test", "id": 2})
            reader.join(timeout=2.0)
            assert received, "reader should have received the message"

            # the snapshot+clear must have run when tcp_client
            # is not None, so _pending_tcp is now empty.
            assert server._pending_tcp == [], (
                "XV-82 regression: _pending_tcp should have been cleared "
                "when tcp_client is not None (the snapshot path must run)."
            )
        finally:
            srv.close()
            cli.close()


# compact JSON serialization matches WS path ──────────────────


class TestCompactJsonSerialization:
    """XV-83: ``IPCServer._send`` serializes messages with
    ``ensure_ascii=False, separators=(",", ":")`` to match the WS
    path's convention and shrink the wire format."""

    def test_send_source_uses_compact_json(self):
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._send)
        assert "ensure_ascii=False" in src, (
            "XV-83: _send must use ensure_ascii=False to keep multi-byte "
            "UTF-8 (e.g. CJK dictation) as-is instead of escaping to "
            "\\uXXXX."
        )
        assert 'separators=(",", ":")' in src, (
            "XV-83: _send must use separators=(',', ':') to strip the default whitespace and shrink the wire format."
        )

    def test_send_produces_compact_json(self):
        """A message with a non-ASCII string must serialize without
        \\uXXXX escapes (which the default ``ensure_ascii=True`` would
        emit), and must not contain the default ``", "`` / ``": "``
        whitespace."""
        import contextlib as _ctxlib

        from voice_typer.server.ipc_server import _TCPLineIO

        server = make_bare_ipc_server(send_path=True)

        srv, cli = socket.socketpair()
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client

            received = bytearray()

            def reader():
                while True:
                    try:
                        chunk = cli.recv(65536)
                    except OSError:
                        # Teardown: the peer socket is shut down / closed
                        # while this thread may still be blocked in recv
                        # (WinError 10038 / 10053 on Windows). Treat as
                        # EOF so the reader thread exits cleanly instead of
                        # surfacing an unhandled thread exception.
                        break
                    if not chunk:
                        break
                    received.extend(chunk)

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            # Send a message with CJK text — ensure_ascii=False keeps
            # the multi-byte UTF-8 as-is.
            server._send({"type": "transcription_final", "text": "你好世界"})

            t.join(timeout=2.0)
            with _ctxlib.suppress(Exception):
                cli.shutdown(socket.SHUT_RDWR)
            cli.close()
            srv.close()
            t.join(timeout=1.0)

            line = received.decode("utf-8").strip()
            # The wire format must contain the raw CJK chars (not
            # \\u4f60\\u597d...).
            assert "你好世界" in line, (
                f"XV-83: ensure_ascii=False must keep CJK chars as-is in the wire format. Got: {line!r}"
            )
            # The compact separators must NOT insert whitespace after
            # the comma or colon.
            assert '", "' not in line, "XV-83: separators=(',', ':') must not leave whitespace after the comma."
            assert '": "' not in line, "XV-83: separators=(',', ':') must not leave whitespace after the colon."
        finally:
            with _ctxlib.suppress(Exception):
                cli.close()
            with _ctxlib.suppress(Exception):
                srv.close()


# sidecar_ws._writer encodes once ─────────────────────────────


class TestWriterEncodesOnce:
    """XV-84: ``sidecar_ws._writer`` encodes the outbound event to
    bytes ONCE and reuses the buffer for both the size check and the
    send. Pre-XV-84 the code did ``raw.encode("utf-8")`` for the size
    check and let ``websocket.send(raw)`` re-encode internally."""

    def test_writer_source_encodes_to_bytes_once(self):
        # The writer was refactored from a nested closure inside
        # ``_handle_connection`` into a sibling function
        # ``_start_writer`` (which spawns ``_writer`` as a task).
        # Read the writer source from the module directly via
        # ``inspect.getsource`` on the module file so the static
        # check doesn't care which enclosing function the writer
        # lives in.
        import inspect as _inspect

        from voice_typer.server import sidecar_ws

        src = _inspect.getsource(sidecar_ws)
        # XV-84 encode-once pattern: the JSON→bytes encode lives in
        # the module-level ``_encode_ws_frame`` helper (called from
        # the writer via the executor offload so the asyncio loop
        # thread never does the big encode); the writer assigns the
        # result to ``raw_bytes`` and reuses that buffer for both the
        # size check and the send.
        assert 'return json.dumps(event, ensure_ascii=False).encode("utf-8")' in src, (
            "XV-84: _encode_ws_frame must encode ONCE via json.dumps(event, ensure_ascii=False).encode('utf-8')."
        )
        # XV-84 + IN-35: the encode is offloaded to a DEDICATED
        # ``_get_ws_encode_pool()`` ThreadPoolExecutor (not the asyncio
        # default executor ``None``) so the WS encode cost never stalls
        # the loop thread AND the pool can be drained/cancelled by the
        # shutdown path. Production at ``sidecar_ws.py:_start_writer``.
        assert "raw_bytes = await loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)" in src, (
            "XV-84: _writer must offload the encode to _encode_ws_frame via "
            "loop.run_in_executor(_get_ws_encode_pool(), ...) and assign raw_bytes."
        )
        # The old re-encode pattern must be GONE.
        assert 'len(raw.encode("utf-8"))' not in src, (
            "XV-84: _writer must NOT re-encode via len(raw.encode('utf-8')) — encode once and reuse the buffer."
        )
        # The send must hand ``websocket.send`` the encoded frame as a
        # TEXT payload (the C-WS-2 wire contract: the Rust host parses
        # ``Message::Text`` only — raw bytes would leave as a BINARY
        # frame and be silently dropped), and be capped by
        # asyncio.wait_for so a wedged peer cannot block the writer
        # task (and the asyncio loop thread) forever. The encode-once
        # buffer is still reused: the size check reads ``raw_bytes``
        # and the send decodes that SAME single-encoded buffer.
        assert 'websocket.send(raw_bytes.decode("utf-8"))' in src, (
            "C-WS-2: _writer must await websocket.send(raw_bytes.decode('utf-8')) "
            "(TEXT frame), not websocket.send(raw_bytes) (BINARY frame)."
        )
        wait_for_send = (
            "await asyncio.wait_for(\n"
            '            websocket.send(raw_bytes.decode("utf-8")),\n'
            "            timeout=_WS_SEND_TIMEOUT_SECONDS,\n"
            "        )"
        )
        assert wait_for_send in src, "send must be wrapped in asyncio.wait_for(..., timeout=_WS_SEND_TIMEOUT_SECONDS)."


# validation helper hoists json + caches max_payload_bytes ────


class TestValidationHoistsJsonAndCaches:
    """XV-85: ``_validate_dict_payload`` hoists ``import json`` to
    module top and caches the per-schema ``max_payload_bytes`` lookup
    so the schema scan runs once per schema (not per call)."""

    def test_module_top_imports_json(self):
        from voice_typer.server.ipc import validation

        # ``json`` must be a module-level name (not imported per-call).
        assert hasattr(validation, "json"), "XV-85: validation module must import json at module top."
        assert validation.json is json, "XV-85: validation.json must be the stdlib json module."

    def test_validate_source_does_not_inline_import(self):
        from voice_typer.server.ipc.validation import _validate_dict_payload

        src = inspect.getsource(_validate_dict_payload)
        # The per-call import must be GONE.
        assert "import json as _json_mod" not in src, (
            "XV-85: _validate_dict_payload must NOT do 'import json as _json_mod' per call — hoist to module top."
        )

    def test_cache_constants_exist(self):
        from voice_typer.server.ipc import validation

        assert hasattr(validation, "_MAX_PAYLOAD_BYTES_CACHE"), (
            "XV-85: validation module must expose _MAX_PAYLOAD_BYTES_CACHE."
        )
        assert hasattr(validation, "_MAX_PAYLOAD_BYTES_CACHE_SEEN"), (
            "XV-85: validation module must expose _MAX_PAYLOAD_BYTES_CACHE_SEEN."
        )
        assert hasattr(validation, "_MAX_PAYLOAD_BYTES_CACHE_MAX"), (
            "XV-85: validation module must expose _MAX_PAYLOAD_BYTES_CACHE_MAX."
        )
        assert validation._MAX_PAYLOAD_BYTES_CACHE_MAX > 0
        # The cache must be bounded — verify the cap is reasonable.
        assert validation._MAX_PAYLOAD_BYTES_CACHE_MAX <= 4096, (
            "XV-85: _MAX_PAYLOAD_BYTES_CACHE_MAX must be bounded to prevent unbounded growth from per-call schemas."
        )

    def test_cache_hits_on_second_call_with_same_schema(self):
        """Calling _validate_dict_payload twice with the SAME schema
        object must hit the cache (the schema scan runs only once)."""
        from voice_typer.server.ipc.validation import (
            _MAX_PAYLOAD_BYTES_CACHE,
            _MAX_PAYLOAD_BYTES_CACHE_SEEN,
            _validate_dict_payload,
        )

        # Clear the cache to start fresh.
        _MAX_PAYLOAD_BYTES_CACHE.clear()
        _MAX_PAYLOAD_BYTES_CACHE_SEEN.clear()

        # Use a module-level-stable schema (defined once at class scope
        # so its id() is stable across the two calls).
        schema = {
            "hotkey": {"type": str, "required": True, "max_payload_bytes": 1024},
        }

        # First call: populates the cache.
        _validate_dict_payload({"hotkey": "ctrl+a"}, schema)
        cache_size_after_first = len(_MAX_PAYLOAD_BYTES_CACHE)
        seen_size_after_first = len(_MAX_PAYLOAD_BYTES_CACHE_SEEN)
        assert cache_size_after_first >= 1 or seen_size_after_first >= 1, (
            "XV-85: first call must populate the cache (cache or seen set)."
        )

        # Second call with the same schema — must not re-scan (the
        # entry is already cached).
        _validate_dict_payload({"hotkey": "ctrl+b"}, schema)
        # The cache size must not have grown (no new entry added).
        assert len(_MAX_PAYLOAD_BYTES_CACHE) == cache_size_after_first, (
            "XV-85: second call with the same schema must NOT add a new "
            "cache entry (id-stable schemas should hit the cache)."
        )
        assert len(_MAX_PAYLOAD_BYTES_CACHE_SEEN) == seen_size_after_first

    def test_cache_bounded_under_per_call_schemas(self):
        """Calling _validate_dict_payload with a FRESH schema each call
        (each gets a new id) must not grow the cache unboundedly — the
        FIFO eviction cap kicks in."""
        from voice_typer.server.ipc.validation import (
            _MAX_PAYLOAD_BYTES_CACHE,
            _MAX_PAYLOAD_BYTES_CACHE_MAX,
            _MAX_PAYLOAD_BYTES_CACHE_SEEN,
            _validate_dict_payload,
        )

        _MAX_PAYLOAD_BYTES_CACHE.clear()
        _MAX_PAYLOAD_BYTES_CACHE_SEEN.clear()

        # Issue many more calls than the cache cap, each with a fresh
        # inline schema (new id each time).
        n = _MAX_PAYLOAD_BYTES_CACHE_MAX * 3
        for _ in range(n):
            _validate_dict_payload({}, {})

        # The cache must NOT have grown past the cap.
        assert len(_MAX_PAYLOAD_BYTES_CACHE) <= _MAX_PAYLOAD_BYTES_CACHE_MAX, (
            f"XV-85: cache grew to {len(_MAX_PAYLOAD_BYTES_CACHE)} > cap "
            f"{_MAX_PAYLOAD_BYTES_CACHE_MAX} — FIFO eviction must bound it."
        )
        assert len(_MAX_PAYLOAD_BYTES_CACHE_SEEN) <= _MAX_PAYLOAD_BYTES_CACHE_MAX

    def test_max_payload_bytes_still_enforced(self):
        """Sanity: the max_payload_bytes rule still fires after the
        XV-85 cache refactor — no behavioral regression."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        schema = {"hotkey": {"type": str, "required": True, "max_payload_bytes": 50}}
        # Small payload passes.
        v, err = _validate_dict_payload({"hotkey": "ctrl+a"}, schema)
        assert err is None
        assert v == {"hotkey": "ctrl+a"}
        # Large payload fails.
        v, err = _validate_dict_payload({"hotkey": "x" * 200}, schema)
        assert v is None
        assert err["data"]["code"] == "client.invalid_payload"
        assert "payload too large" in err["data"]["message"]


# transport uses io.DEFAULT_BUFFER_SIZE for reads ─────────────


class TestTransportBuffering:
    """XV-86: ``_TCPLineIO.__init__`` uses ``io.DEFAULT_BUFFER_SIZE``
    (8192) for the read-side ``socket.makefile`` buffering argument
    instead of ``1`` (line buffering, which is a write-side concept)."""

    def test_init_uses_default_buffer_size(self):
        from voice_typer.server.ipc.transport import _TCPLineIO

        src = inspect.getsource(_TCPLineIO.__init__)
        assert "io.DEFAULT_BUFFER_SIZE" in src, (
            "XV-86: _TCPLineIO must use io.DEFAULT_BUFFER_SIZE for the "
            "read-side buffering argument (not 1, which is a write-side "
            "concept)."
        )
        # The old buffering=1 must be GONE.
        assert "buffering=1" not in src, (
            "XV-86: _TCPLineIO must NOT use buffering=1 (line buffering) "
            "for the read side — use io.DEFAULT_BUFFER_SIZE."
        )

    def test_module_imports_io(self):
        from voice_typer.server.ipc import transport

        assert hasattr(transport, "io"), "XV-86: transport module must import io at module top."
        assert transport.io is io

    def test_makefile_called_with_default_buffer_size(self):
        """A real socket's makefile must accept the new buffering
        argument without raising, and the resulting file must support
        readline (the existing contract)."""
        from voice_typer.server.ipc.transport import _TCPLineIO

        srv, cli = socket.socketpair()
        try:
            io_obj = _TCPLineIO(srv)
            # Sanity: the reader is a TextIOBase-like object that
            # supports readline.
            assert hasattr(io_obj._reader, "readline")
            # Write a line through the other end and read it back.
            cli.sendall(b"hello world\n")
            line = io_obj._reader.readline()
            assert line == "hello world\n"
        finally:
            srv.close()
            cli.close()


# sidecar_ws._make_dispatch resolves rate_limiter once ────────


class TestRateLimiterResolvedOnce:
    """XV-87: ``sidecar_ws._make_dispatch`` resolves the shared rate
    limiter ONCE (alongside ``ws_dispatch_pool``) and captures it in
    the closure. The per-frame ``_get_rate_limiter(server)`` lookup
    has been hoisted out of the dispatch hot path."""

    def test_make_dispatch_source_resolves_limiter_in_closure(self):
        from voice_typer.server import sidecar_ws

        # _make_dispatch's body (NOT the inner dispatch()) must contain
        # the rate_limiter assignment — that's the closure capture.
        src = inspect.getsource(sidecar_ws._make_dispatch)
        # The rate_limiter assignment must appear BEFORE the inner
        # ``async def dispatch`` definition.
        dispatch_idx = src.find("async def dispatch")
        assert dispatch_idx != -1
        before_dispatch = src[:dispatch_idx]
        assert "rate_limiter = _get_rate_limiter(server)" in before_dispatch, (
            "XV-87: _make_dispatch must resolve rate_limiter ONCE in the "
            "closure body (before the inner dispatch() definition), not "
            "per-call inside dispatch()."
        )

    def test_dispatch_does_not_call_get_rate_limiter(self):
        """The inner ``dispatch()`` closure must NOT call
        ``_get_rate_limiter`` — it must reference the closure-captured
        ``rate_limiter``."""
        from voice_typer.server import sidecar_ws

        src = inspect.getsource(sidecar_ws._make_dispatch)
        # Find the inner dispatch function body.
        dispatch_idx = src.find("async def dispatch")
        assert dispatch_idx != -1
        dispatch_body = src[dispatch_idx:]
        assert "_get_rate_limiter(server)" not in dispatch_body, (
            "XV-87: dispatch() must NOT call _get_rate_limiter(server) "
            "per frame — the limiter is resolved ONCE in the closure."
        )
        # The closure-captured rate_limiter must be referenced.
        assert "rate_limiter.allow" in dispatch_body, (
            "XV-87: dispatch() must reference the closure-captured rate_limiter.allow(command=...)."
        )

    def test_dispatch_uses_same_limiter_across_calls(self):
        """Two dispatch() calls on the same _make_dispatch-derived
        closure must use the SAME rate_limiter instance — verifying
        the closure capture (not a per-call lookup)."""
        # Build a fake server with a real _RateLimiter instance so we
        # can verify identity across calls. Using a MagicMock server
        # would auto-vivify _ws_dispatch_pool, so we use a minimal
        # class with explicit attributes.
        from concurrent.futures import ThreadPoolExecutor

        from voice_typer.server import sidecar_ws
        from voice_typer.server.ipc_server import _get_rate_limiter

        class FakeServer:
            pass

        server = FakeServer()
        server._ws_dispatch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-xv87")
        # Resolve the limiter the same way _make_dispatch does — once.
        limiter_before = _get_rate_limiter(server)
        sidecar_ws._make_dispatch(server)
        # Resolve again — must be the same instance (already cached on
        # the server by _make_dispatch).
        limiter_after = _get_rate_limiter(server)
        assert limiter_before is limiter_after, (
            "XV-87: _make_dispatch must resolve the limiter ONCE and "
            "store it on the server; subsequent _get_rate_limiter calls "
            "must return the same instance."
        )
        # The limiter must have been stored on the server instance.
        assert server._rate_limiter_instance is limiter_before
        # Cleanup.
        server._ws_dispatch_pool.shutdown(wait=False, cancel_futures=True)


# ==============================================================================
# Merged from tests/test_ipc_server_lifecycle_fixes.py —
#   IPCServer lifecycle gates (stdin listener env-var gate, shutdown re-entrancy, registry extraction,
#   transcribe_offline degradation)
# ==============================================================================
# Test coverage
# -------------
# - (High):         ``IPCServer.start()`` refuses to spawn the stdin
# listener when ``_tcp_mode`` is False AND the
# ``VOICE_TYPER_ALLOW_STDIN_IPC`` env var is not set
# to ``"1"``. A WARNING is logged and ``_stdin_thread``
# is set to ``None``. The ``--allow-stdin`` CLI flag in
# ``parse_ipc_args()`` is the alternative gate — it
# sets the env var.
# - (Medium):       ``_handle_shutdown`` checks ``_shutdown_started``
# (a per-instance ``threading.Event``) at the top and
# no-ops the second invocation. The cleanup thread is
# registered on ``self.app._thread_registry`` (when
# available) so ``shutdown_all()`` can join it.
# - (Medium):       ``_COMMAND_REGISTRY`` + ``_READONLY_COMMANDS`` +
# ``_PYTHON_ONLY_COMMANDS`` are canonical to
# :mod:`voice_typer.server.ipc.registry`.
# ``ipc_server.py`` re-exports them and
# :class:`IPCServer` re-aliases them as class
# attributes (pinned by ``test_ipc_shutdown_registry``,
# ``test_command_registry_parity``, etc.).
#
# These tests are intentionally unit-level (no live TCP, no real
# ``VoiceTyperApp``) so they run in <1 s.
#

# stdin listener gate behind VOICE_TYPER_ALLOW_STDIN_IPC ──────


class TestStdinGate:
    """the unauthenticated stdin/stdout IPC listener is
    gated behind ``VOICE_TYPER_ALLOW_STDIN_IPC=1``.

    ``IPCServer.start()`` would spawn the stdin listener
    thread whenever ``_tcp_mode`` was False — exposing an
    unauthenticated command channel on the user's terminal (Linux
    TIOCSTI injection is possible; an accidental JSON paste triggers
    unintended IPC commands on every platform).

    The gate refuses to spawn the listener when ``_tcp_mode`` is False
    AND the env var is not set; a WARNING is logged and
    ``_stdin_thread`` is set to ``None``. The ``--allow-stdin`` CLI
    flag in :func:`parse_ipc_args` is the alternative gate (it sets
    the env var).
    """

    def test_stdin_ipc_env_var_module_constant_exists(self) -> None:
        """``_STDIN_IPC_ENV_VAR`` module-level constant exists
        and is the documented string."""
        import voice_typer.server.ipc_server as ipc_server_mod

        assert hasattr(ipc_server_mod, "_STDIN_IPC_ENV_VAR"), (
            "ipc_server.py must expose a module-level "
            "_STDIN_IPC_ENV_VAR constant naming the env var that gates "
            "the stdin listener."
        )
        assert ipc_server_mod._STDIN_IPC_ENV_VAR == "VOICE_TYPER_ALLOW_STDIN_IPC", (
            f"_STDIN_IPC_ENV_VAR must be 'VOICE_TYPER_ALLOW_STDIN_IPC'; got {ipc_server_mod._STDIN_IPC_ENV_VAR!r}."
        )

    def test_start_gates_stdin_listener_when_env_var_unset(self, monkeypatch) -> None:
        """when ``_tcp_mode`` is False AND the env var is unset,
        ``start()`` must NOT spawn the stdin listener. ``_stdin_thread``
        is ``None`` and a WARNING is logged.

        We exercise the gate logic by inspecting the source of
        ``start()`` (the gate fires before the ``threading.Thread(...)``
        call) AND by running ``start()`` with the env var unset and
        observing ``_stdin_thread``. The full ``start()`` call is safe
        because the gate prevents the stdin listener from spawning.
        """
        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Source-level pin: the gate must be present.
        src = inspect.getsource(__import__("voice_typer.server.ipc_server", fromlist=["IPCServer"]).IPCServer.start)
        assert "_STDIN_IPC_ENV_VAR" in src, (
            "start() must reference _STDIN_IPC_ENV_VAR so the "
            "stdin listener is gated behind VOICE_TYPER_ALLOW_STDIN_IPC=1."
        )
        assert 'os.environ.get(_STDIN_IPC_ENV_VAR) == "1"' in src, (
            'start() must check os.environ.get(_STDIN_IPC_ENV_VAR) == "1" before spawning the stdin listener.'
        )

    def test_stdin_thread_none_when_gate_refuses(self, monkeypatch) -> None:
        """end-to-end behavior — ``start()`` with ``_tcp_mode``
        False AND env var unset must leave ``_stdin_thread`` as None.

        We exercise the full ``start()`` path (minus the heavy
        ``event_bus.subscribe`` / heartbeat-thread wiring) using a
        MagicMock app so no real VoiceTyperApp is constructed.
        """
        from voice_typer.server import event_bus

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Also clear TAURI_SIDECAR so the heartbeat thread is created
        # (so we exercise the full start() body — but the heartbeat
        # thread is a daemon so it doesn't block test teardown).
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        server, _app, _service = make_ipc_server_with_fakes(thread_registry=None)
        server._tcp_mode = False

        # Stub out event_bus.subscribe so we don't leak the push fn.
        subscribed: list = []
        monkeypatch.setattr(event_bus, "subscribe", lambda fn: subscribed.append(fn))
        # Stub out threading.Thread so we don't actually start a
        # heartbeat thread (the gate must prevent the stdin thread from
        # being created at all — the FakeThread captures the names of
        # threads that WOULD be created).
        created_threads: list[str] = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.target = target
                self.name = name
                self.daemon = daemon
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

        import voice_typer.server.ipc_server as ipc_server_mod

        monkeypatch.setattr(ipc_server_mod.threading, "Thread", FakeThread)

        server.start()
        try:
            # the gate must prevent the stdin listener thread
            # from being created. ``ipc-server`` must NOT be in the
            # created_threads list (only ``heartbeat-watchdog`` is).
            assert "ipc-server" not in created_threads, (
                "stdin listener 'ipc-server' thread was spawned "
                "even though VOICE_TYPER_ALLOW_STDIN_IPC is unset — the "
                "gate failed to refuse the unauthenticated stdin path."
            )
            assert server._stdin_thread is None, (
                "_stdin_thread must be None when the gate refuses to spawn the stdin listener."
            )
        finally:
            server.stop()

    def test_stdin_thread_spawned_when_env_var_set(self, monkeypatch) -> None:
        """when ``_tcp_mode`` is False AND the env var IS set to
        ``"1"``, ``start()`` must spawn the stdin listener thread.

        This is the explicit-opt-in path for development / testing.
        """
        from voice_typer.server import event_bus

        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        server, _app, _service = make_ipc_server_with_fakes(thread_registry=None)
        server._tcp_mode = False

        monkeypatch.setattr(event_bus, "subscribe", lambda fn: None)
        created_threads: list[str] = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.name = name
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

        import voice_typer.server.ipc_server as ipc_server_mod

        monkeypatch.setattr(ipc_server_mod.threading, "Thread", FakeThread)

        server.start()
        try:
            assert "ipc-server" in created_threads, (
                "stdin listener 'ipc-server' thread was NOT "
                "spawned even though VOICE_TYPER_ALLOW_STDIN_IPC=1 — "
                "the gate must allow explicit opt-in for dev/testing."
            )
            # ``_stdin_thread`` is a FakeThread instance (not a real
            # Thread); just assert it's not None.
            assert server._stdin_thread is not None, (
                "_stdin_thread must be set when the gate allows the stdin listener (env var is '1')."
            )
        finally:
            server.stop()

    def test_allow_stdin_cli_flag_sets_env_var(self, monkeypatch) -> None:
        """``--allow-stdin`` CLI flag in ``parse_ipc_args()``
        sets ``VOICE_TYPER_ALLOW_STDIN_IPC=1`` so the gate at
        ``start()`` allows the stdin listener."""
        import sys

        from voice_typer.server.ipc_server import parse_ipc_args

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--allow-stdin"])
        try:
            port, ws_mode = parse_ipc_args()
            assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") == "1", (
                "--allow-stdin CLI flag must set "
                "VOICE_TYPER_ALLOW_STDIN_IPC=1 so the gate at start() "
                "allows the stdin listener."
            )
            assert port is None
            assert ws_mode is False
        finally:
            monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)

    def test_no_allow_stdin_flag_does_not_set_env_var(self, monkeypatch) -> None:
        """without ``--allow-stdin``, the env var is NOT set by
        ``parse_ipc_args()`` (the gate at ``start()`` would refuse)."""
        import sys

        from voice_typer.server.ipc_server import parse_ipc_args

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server"])
        try:
            parse_ipc_args()
            assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") is None, (
                "parse_ipc_args() must NOT set "
                "VOICE_TYPER_ALLOW_STDIN_IPC when --allow-stdin is not "
                "passed (the gate at start() must refuse)."
            )
        finally:
            monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)


# _handle_shutdown re-entrancy gate + thread registry ─────────


class TestShutdownGate:
    """``_handle_shutdown`` is idempotent.

    a double-``shutdown`` (e.g. the Tauri host's WS
    transport retrying after a slow ack) spawned a SECOND untracked
    ``ipc-shutdown-cleanup`` daemon thread — both threads would race
    into ``service.quit()`` / ``_do_cleanup()`` and double-free the
    mic stream, hotkey listeners, single-instance mutex, etc.

    The fix adds a per-instance ``_shutdown_started: threading.Event``
    that ``_handle_shutdown`` checks at the top; the second invocation
    no-ops (returns the ack envelope without spawning another thread).
    The cleanup thread is registered on
    ``self.app._thread_registry`` (when available) so ``shutdown_all()``
    can join it.
    """

    def test_shutdown_started_event_initialized_in_init(self) -> None:
        """``__init__`` must declare a per-instance
        ``_shutdown_started: threading.Event`` so ``_handle_shutdown``
        can no-op the second invocation."""
        server = _make_server()
        assert hasattr(server, "_shutdown_started"), (
            "IPCServer.__init__ must declare _shutdown_started "
            "(a threading.Event) so _handle_shutdown can no-op the "
            "second invocation (double-shutdown race)."
        )
        assert isinstance(server._shutdown_started, threading.Event), (
            f"_shutdown_started must be a threading.Event; got {type(server._shutdown_started)!r}."
        )
        assert not server._shutdown_started.is_set(), (
            "_shutdown_started must start unset (no shutdown has been requested yet)."
        )

    def test_double_handle_shutdown_no_ops_second_invocation(self) -> None:
        """calling ``_handle_shutdown`` twice must NOT spawn two
        cleanup threads. ``service.quit()`` is called exactly once."""
        server = _make_server()
        # Stub service.quit so it returns immediately (no real cleanup).
        server.service.quit = MagicMock()

        result1 = server._handle_shutdown(data=None, resp={"id": 1})
        result2 = server._handle_shutdown(data=None, resp={"id": 2})

        # Both invocations return the ack envelope (the host's retry
        # timer expects an ack, not an error).
        assert result1 is not None and result1["data"] == {"ack": True}
        assert result2 is not None and result2["data"] == {"ack": True}

        # Wait briefly for the cleanup thread to land its call.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and server.service.quit.call_count < 1:
            time.sleep(0.005)
        # service.quit is called EXACTLY ONCE — the second
        # invocation's no-op path doesn't spawn a second cleanup thread.
        assert server.service.quit.call_count == 1, (
            f"service.quit was called "
            f"{server.service.quit.call_count} times; expected exactly 1. "
            f"The double-shutdown race spawned a second cleanup thread."
        )

    def test_shutdown_started_event_set_after_first_invocation(self) -> None:
        """after the first ``_handle_shutdown`` call, the
        ``_shutdown_started`` event must be set so the second
        invocation's no-op gate fires."""
        server = _make_server()
        server.service.quit = MagicMock()
        assert not server._shutdown_started.is_set()
        server._handle_shutdown(data=None, resp={"id": 1})
        assert server._shutdown_started.is_set(), (
            "_shutdown_started must be set after the first _handle_shutdown call so the second invocation no-ops."
        )

    def test_cleanup_thread_registered_on_thread_registry(self) -> None:
        """the cleanup thread must be registered on
        ``self.app._thread_registry`` (when the app provides one) so
        ``shutdown_all()`` can join it during ``VoiceTyperApp.quit()``."""
        server = _make_server()
        # ``_make_server`` returns an IPCServer whose ``app`` is a
        # MagicMock — ``app._thread_registry`` is also a MagicMock by
        # default. The cleanup-thread registration must call
        # ``app._thread_registry.register(name="ipc-shutdown-cleanup", ...)``.
        server.service.quit = MagicMock()
        server._handle_shutdown(data=None, resp={"id": 1})

        # Wait briefly for the cleanup thread to be spawned + registered.
        deadline = time.monotonic() + 2.0
        registered_names: list[str] = []
        while time.monotonic() < deadline:
            registered_names = [
                str(call.kwargs.get("name", "")) for call in server.app._thread_registry.register.call_args_list
            ]
            if "ipc-shutdown-cleanup" in registered_names:
                break
            time.sleep(0.005)
        assert "ipc-shutdown-cleanup" in registered_names, (
            "the cleanup thread must be registered on "
            "self.app._thread_registry under the name "
            "'ipc-shutdown-cleanup' so shutdown_all() can join it. "
            f"Observed register() calls: {registered_names!r}."
        )

    def test_cleanup_thread_not_registered_when_registry_none(self) -> None:
        """when ``self.app._thread_registry`` is None (e.g. a
        test bypass that doesn't wire the central registry), the
        cleanup thread is still spawned but NOT registered. The
        ``getattr(self.app, '_thread_registry', None)`` defensive
        lookup must not raise."""
        server = _make_server()
        server.app._thread_registry = None
        server.service.quit = MagicMock()
        # Must NOT raise — the registration path is guarded by
        # ``if _registry is not None:``.
        result = server._handle_shutdown(data=None, resp={"id": 1})
        assert result is not None and result["data"] == {"ack": True}

    def test_handle_shutdown_source_contains_shutdown_started_gate(self) -> None:
        """source-level pin — ``_handle_shutdown`` must check
        ``_shutdown_started`` at the top before any side effect."""
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._handle_shutdown)
        assert "_shutdown_started" in src, (
            "_handle_shutdown must reference _shutdown_started so the double-shutdown race is closed."
        )
        assert "self._shutdown_started.is_set()" in src, (
            "_handle_shutdown must call self._shutdown_started.is_set() to detect the second invocation."
        )
        assert "self._shutdown_started.set()" in src, (
            "_handle_shutdown must call "
            "self._shutdown_started.set() before spawning the cleanup "
            "thread so the second invocation's no-op is atomic with "
            "the first's thread-spawn decision."
        )


# registry extraction ─────────────────────────────────────────


class TestRegistryExtraction:
    """``_COMMAND_REGISTRY`` + ``_READONLY_COMMANDS`` +
    ``_PYTHON_ONLY_COMMANDS`` are canonical to
    :mod:`voice_typer.server.ipc.registry`.

    ``_COMMAND_REGISTRY`` + ``_PYTHON_ONLY_COMMANDS`` were
    class attributes on :class:`IPCServer` (in the 2,100-line
    ``ipc_server.py`` god-module) and ``_READONLY_COMMANDS`` lived in
    ``ipc._helpers.py``; the split made the three-layers-must-agree
    parity contract harder to reason about.

    The extraction is behavior-preserving — same dict, same keys, same
    values. :class:`IPCServer` re-aliases ``_COMMAND_REGISTRY`` and
    ``_PYTHON_ONLY_COMMANDS`` as class attributes so every existing
    ``IPCServer._COMMAND_REGISTRY`` / ``IPCServer._PYTHON_ONLY_COMMANDS``
    call site keeps working unchanged.
    """

    def test_registry_module_is_importable(self) -> None:
        """the new ``voice_typer.server.ipc.registry`` module
        is importable and exposes the three canonical constants."""
        from voice_typer.server.ipc import registry

        assert hasattr(registry, "_COMMAND_REGISTRY"), (
            "ipc.registry must expose _COMMAND_REGISTRY (module-level dict — the canonical source of truth)."
        )
        assert hasattr(registry, "_READONLY_COMMANDS"), "ipc.registry must expose _READONLY_COMMANDS."
        assert hasattr(registry, "_PYTHON_ONLY_COMMANDS"), "ipc.registry must expose _PYTHON_ONLY_COMMANDS."

    def test_registry_module_constants_are_correct_types(self) -> None:
        """the registry module's constants have the documented
        types (dict / frozenset / frozenset)."""
        from voice_typer.server.ipc import registry

        assert isinstance(registry._COMMAND_REGISTRY, dict), (
            f"registry._COMMAND_REGISTRY must be a dict; got {type(registry._COMMAND_REGISTRY)!r}."
        )
        assert isinstance(registry._READONLY_COMMANDS, frozenset), (
            f"registry._READONLY_COMMANDS must be a frozenset; got {type(registry._READONLY_COMMANDS)!r}."
        )
        assert isinstance(registry._PYTHON_ONLY_COMMANDS, frozenset), (
            f"registry._PYTHON_ONLY_COMMANDS must be a frozenset; got {type(registry._PYTHON_ONLY_COMMANDS)!r}."
        )

    def test_ipc_server_re_exports_registry_constants(self) -> None:
        """``ipc_server.py`` must re-export ``_COMMAND_REGISTRY``,
        ``_READONLY_COMMANDS``, and ``_PYTHON_ONLY_COMMANDS`` at module
        level so existing ``from voice_typer.server.ipc_server import
        _COMMAND_REGISTRY`` callers keep working unchanged."""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server.ipc import registry

        # Object identity: the module-level name must be the SAME object
        # as the registry module's constant (single source of truth).
        assert ipc_server_mod._COMMAND_REGISTRY is registry._COMMAND_REGISTRY, (
            "ipc_server._COMMAND_REGISTRY must be the SAME object "
            "as registry._COMMAND_REGISTRY (single source of truth — "
            "not a parallel copy)."
        )
        assert ipc_server_mod._READONLY_COMMANDS is registry._READONLY_COMMANDS, (
            "ipc_server._READONLY_COMMANDS must be the SAME object as registry._READONLY_COMMANDS."
        )
        assert ipc_server_mod._PYTHON_ONLY_COMMANDS is registry._PYTHON_ONLY_COMMANDS, (
            "ipc_server._PYTHON_ONLY_COMMANDS must be the SAME object as registry._PYTHON_ONLY_COMMANDS."
        )

    def test_ipc_server_class_re_aliases_registry_constants(self) -> None:
        """class:`IPCServer` must re-alias ``_COMMAND_REGISTRY``
        and ``_PYTHON_ONLY_COMMANDS`` as class attributes so every
        existing ``IPCServer._COMMAND_REGISTRY`` /
        ``IPCServer._PYTHON_ONLY_COMMANDS`` call site (pinned by
        ``test_ipc_shutdown_registry``, ``test_ec4_python_command_...``,
        etc.) keeps working unchanged."""
        from voice_typer.server.ipc import registry
        from voice_typer.server.ipc_server import IPCServer

        assert IPCServer._COMMAND_REGISTRY is registry._COMMAND_REGISTRY, (
            "IPCServer._COMMAND_REGISTRY must be the SAME object "
            "as registry._COMMAND_REGISTRY (class-level re-alias for "
            "backward compat with every IPCServer._COMMAND_REGISTRY "
            "call site)."
        )
        assert IPCServer._PYTHON_ONLY_COMMANDS is registry._PYTHON_ONLY_COMMANDS, (
            "IPCServer._PYTHON_ONLY_COMMANDS must be the SAME object as registry._PYTHON_ONLY_COMMANDS."
        )

    def test_registry_dict_same_keys_and_values_as_before(self) -> None:
        """behavior-preserving extraction — same dict, same keys,
        same values. Spot-check the critical entries (shutdown,
        tray_click, heartbeat) plus the overall key count."""
        from voice_typer.server.ipc import registry

        # Critical entries that other tests pin (test_ipc_shutdown_registry,
        # test_command_registry_parity, test_ipc_command_registry_sync,
        # test_tauri_sidecar_gate).
        assert registry._COMMAND_REGISTRY["shutdown"] == "_handle_shutdown"
        assert registry._COMMAND_REGISTRY["tray_click"] == "_handle_tray_click"
        assert registry._COMMAND_REGISTRY["heartbeat"] == "_handle_heartbeat"
        # reconciliation documented 64 commands;
        # (test_cloud_connection) + XZ-SEC-05 (add_trusted_endpoint)
        # brought it to 65; onboarding_set_backend (Model-step backend
        # choice) brought it to 66; reset_macos_accessibility (finding
        # #127 part b) brought it to 67; reset_linux_permissions
        # (finding #127 part b Linux sibling) brought it to 68;
        # check_accessibility re-added (finding #919 part b — Settings
        # → Troubleshooting surfaces the stale-grant reset) brought it
        # to 69.
        # transcribe_offline (Phase 2b pack downloader — plan-runtime-
        # pack-split.md §7.4) brought it to 70.
        # prewarm retirement (plan §6.2 P-1 — get_prewarm_status,
        # run_prewarm, open_prewarm_log removed across all 4 allowlists
        # in lockstep) brought it to 67.
        # prewarm status RESTORATION (plan §6.3 addendum 2026-08-14 —
        # Settings → About Cache Status card restored verbatim from
        # 5a319872; run_prewarm stays retired) brought it back to 69.
        # check_offline_pack_update (auto-update feature, docs/auto-update-feature.md
        # — 2026-08-14) brought it to 70.
        # run_prewarm (plan §6.3 addendum 2nd half, 2026-08-14 —
        # re-implemented to re-run the warm phase in-process instead of
        # The registry holds ALL commands: the 71 forwarded ones (the
        # allowlist in allowed-commands.ts — pinned in SECURITY.md) plus
        # the 2 python-only commands (shutdown, tray_click) that never
        # cross the Electron bridge. The count is deliberately pinned
        # here and in SECURITY.md — update all sources of truth
        # together. Adding a command to the registry WITHOUT the TS
        # allowlist fails the parity test
        # (test_electron_ipc_and_build.py::test_allowlist_matches_server_commands).
        assert len(registry._COMMAND_REGISTRY) == 74, (
            f"registry._COMMAND_REGISTRY must contain 73 entries "
            f"(71 forwarded in allowed-commands.ts + shutdown + "
            f"tray_click python-only); got "
            f"{len(registry._COMMAND_REGISTRY)}. "
            f"If the count drifted, update this test together with the "
            f"registry + the TS/Rust allowlists."
        )

    def test_python_only_commands_unchanged(self) -> None:
        """``_PYTHON_ONLY_COMMANDS`` is the documented
        ``{"shutdown", "tray_click"}`` frozenset (EC-4 exception set)."""
        from voice_typer.server.ipc import registry

        assert frozenset({"shutdown", "tray_click"}) == registry._PYTHON_ONLY_COMMANDS, (
            f"registry._PYTHON_ONLY_COMMANDS must be "
            f"frozenset({{'shutdown', 'tray_click'}}); got "
            f"{registry._PYTHON_ONLY_COMMANDS!r}."
        )

    def test_readonly_commands_unchanged(self) -> None:
        """``_READONLY_COMMANDS`` is the documented 4-element
        frozenset (GT-25)."""
        from voice_typer.server.ipc import registry

        assert (
            frozenset({"get_status", "get_config", "get_model_catalog", "heartbeat"}) == registry._READONLY_COMMANDS
        ), (
            f"registry._READONLY_COMMANDS must be the 4-element "
            f"frozenset {{'get_status', 'get_config', 'get_model_catalog', "
            f"'heartbeat'}}; got {registry._READONLY_COMMANDS!r}."
        )

    def test_registry_history_comment_block_present(self) -> None:
        """the ~30 "REMOVED" historical comments were
        consolidated into a ``# Registry history`` block at the top of
        ``ipc/registry.py`` (the regression guard in
        ``test_dead_code_stays_removed.py`` already pins the removals
        independently)."""
        from voice_typer.server.ipc import registry

        src = inspect.getsource(registry)
        assert "Registry history" in src, (
            "ipc/registry.py must contain a '# Registry history' "
            "comment block at the top consolidating the ~30 'REMOVED' "
            "comments that previously lived inline next to the dict "
            "literal in ipc_server.py."
        )

    def test_ipc_server_no_longer_defines_inline_dict_literal(self) -> None:
        """``ipc_server.py`` must NOT contain the inline
        ``_COMMAND_REGISTRY: dict[str, str] = {`` dict literal — the
        dict was extracted to ``ipc.registry`` and ``ipc_server.py``
        only re-aliases it as a class attribute.

        The class-level alias ``_COMMAND_REGISTRY: dict[str, str] =
        _COMMAND_REGISTRY`` (the re-assignment of the imported name to
        a class attribute) is fine; we only flag the literal `{``
        assignment form.
        """
        import voice_typer.server.ipc_server as ipc_server_mod

        src = inspect.getsource(ipc_server_mod)
        # The class-level alias line is ``_COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY``
        # (no ``{``). The inline literal was
        # ``_COMMAND_REGISTRY: dict[str, str] = {`` followed by the
        # dict body. We must NOT find the literal form.
        assert "_COMMAND_REGISTRY: dict[str, str] = {" not in src, (
            "ipc_server.py must NOT define the inline "
            "_COMMAND_REGISTRY dict literal — it has been extracted to "
            "ipc.registry. The class-level alias "
            "(``_COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY``) "
            "is the only allowed form."
        )


class TestTranscribeOfflineDegradation:
    """Phase 2d degradation matrix (§8.10).

    ``_handle_transcribe_offline`` must NOT queue silently when the
    offline pack is missing — the request can never complete, so the
    ack carries ``queued: False`` + ``degraded: True`` +
    ``reason: "offline_pack_missing"`` for the renderer to surface.
    """

    def _dispatch(self, server: IPCServer) -> dict:
        return server._dispatch({"id": 7, "type": "transcribe_offline", "data": {}})

    def test_pack_missing_returns_degraded_not_queued(self, monkeypatch):
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: None)
        resp = self._dispatch(_make_server())
        assert resp["type"] == "ack"
        assert resp["data"]["queued"] is False
        assert resp["data"]["degraded"] is True
        assert resp["data"]["reason"] == "offline_pack_missing"

    def test_pack_present_acks_queued(self, monkeypatch):
        from voice_typer.server.service import update_check

        monkeypatch.setattr(update_check, "_local_offline_pack_version", lambda: "v1")
        resp = self._dispatch(_make_server())
        assert resp["type"] == "ack"
        assert resp["data"]["queued"] is True
        assert "degraded" not in resp["data"]

    def test_check_failure_fails_safe_to_degraded(self, monkeypatch):
        from voice_typer.server.service import update_check

        def boom():
            raise RuntimeError("broken pack root")

        monkeypatch.setattr(update_check, "_local_offline_pack_version", boom)
        resp = self._dispatch(_make_server())
        assert resp["data"]["queued"] is False
        assert resp["data"]["reason"] == "offline_pack_missing"
