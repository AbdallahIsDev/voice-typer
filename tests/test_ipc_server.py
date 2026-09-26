"""IPCServer dispatch + send unit tests (no live TCP; <1 s)."""

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
pytestmark = pytest.mark.xdist_group("ipc_layer_fixes")


def _make_server() -> IPCServer:
    """Build an IPCServer via the canonical fake app + service factories."""
    server, _fake_app, _fake_service = make_ipc_server_with_fakes()
    return server


class TestDispatchTableTyped:
    """IPCServer.__init__ validates _COMMAND_REGISTRY callables at construction."""

    def test_init_validates_registry_via_assertion_loop(self) -> None:
        """Source-inspection: __init__ must loop-assert registry entries are callable."""
        src = inspect.getsource(IPCServer._init_validate_command_registry)
        assert "_init_validate_command_registry" in inspect.getsource(IPCServer.__init__), (
            "GT-29 / DT-5: __init__ must call _init_validate_command_registry "
            "so registry typos still surface at construction time."
        )
        assert "_COMMAND_REGISTRY.items()" in src, (
            "GT-29 / DT-5: _init_validate_command_registry must iterate "
            "over _COMMAND_REGISTRY.items() to validate every entry resolves "
            "to a callable (replaces the deleted _command_handlers cache)."
        )
        assert "callable(" in src, (
            "GT-29 / DT-5: _init_validate_command_registry must call "
            "callable() on each resolved attribute to validate the registry "
            "at construction time."
        )
        # The dead ``_command_handlers`` cache must NOT be built.
        assert "self._command_handlers" not in src, (
            "DT-5: _init_validate_command_registry must NOT build the dead "
            "_command_handlers instance cache, _dispatch resolves handlers "
            "at dispatch time via getattr(self, handler_name, None), so the "
            "cache was never read. Only the typo-validation loop survives."
        )

    def test_command_handlers_cache_not_set_after_init(self) -> None:
        """Source-inspection / unit coverage for the named behavior."""
        server = _make_server()
        assert not hasattr(server, "_command_handlers"), (
            "DT-5: the _command_handlers instance cache was deleted as "
            "dead code, _dispatch resolves handlers at dispatch time. "
            "__init__ must NOT set this attribute."
        )

    def test_registry_typo_surfaces_at_construction(self) -> None:
        """Source-inspection / unit coverage for the named behavior."""
        original = IPCServer._COMMAND_REGISTRY.copy()
        try:
            IPCServer._COMMAND_REGISTRY["__typo_probe__"] = "_handle_this_method_does_not_exist"
            with pytest.raises(RuntimeError, match="non-callable"):
                _make_server()
        finally:
            IPCServer._COMMAND_REGISTRY.clear()
            IPCServer._COMMAND_REGISTRY.update(original)

    def test_response_envelope_alias_exists(self) -> None:
        """/ GT-D1-10: ``ResponseEnvelope = dict[str, object]``"""
        # ``dict[str, object]`` evaluates to a ``types.GenericAlias``

        assert ResponseEnvelope is not None
        # PEP 585 generic alias: ``dict[str, object].__origin__ is dict``.
        assert getattr(ResponseEnvelope, "__origin__", None) is dict, (
            f"GT-29: ResponseEnvelope must be a dict subscripted generic "
            f"alias (dict[str, object]); got {ResponseEnvelope!r}."
        )

    def test_command_handler_alias_exists(self) -> None:
        """``CommandHandler`` type alias is exported."""
        # ``typing.Callable`` aliases are instances of typing._GenericAlias
        assert CommandHandler is not None


# _rate_limiter_instance declared on IPCServer ───────────────


class TestRateLimiterInstanceDeclared:
    """``_rate_limiter_instance`` is declared on IPCServer.__init__"""

    def test_rate_limiter_instance_init_to_none(self) -> None:
        server = _make_server()
        assert hasattr(server, "_rate_limiter_instance"), (
            "GT-30: IPCServer.__init__ must declare _rate_limiter_instance."
        )
        assert server._rate_limiter_instance is None, (
            "GT-30: _rate_limiter_instance must start as None (lazy initialization by _get_rate_limiter)."
        )

    def test_get_rate_limiter_no_type_ignore(self) -> None:
        """The ``# type: ignore[attr-defined]`` marker must be GONE"""
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server._get_rate_limiter)
        assert "type: ignore[attr-defined]" not in src, (
            "GT-30: _get_rate_limiter must NOT silence the "
            "_rate_limiter_instance assignment with type: ignore, the "
            "attribute is now declared on IPCServer.__init__."
        )

    def test_rate_limiter_instance_assignable_without_ignore(self) -> None:
        """The attribute can be assigned without runtime error, the"""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        server = _make_server()
        limiter = _RateLimiter()
        # This assignment used to require `# type: ignore[attr-defined]`;
        server._rate_limiter_instance = limiter
        assert server._rate_limiter_instance is limiter


# + : dispatch lock + TOCTOU re-check ────────────────────


class TestDispatchLockAndTOCTOU:
    """state-mutating dispatches serialize on ``_dispatch_lock``."""

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
        """two concurrent state-mutating dispatches must NOT"""
        server = _make_server()
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
            "handler bodies simultaneously, _dispatch_lock failed to "
            "serialize them."
        )

    def test_readonly_dispatches_bypass_lock(self) -> None:
        """read-only dispatches (``get_status`` etc.) do NOT"""
        server = _make_server()
        # Make get_status return a trivial response.
        server._handle_get_status = lambda data, resp: (  # noqa: E731
            resp.__setitem__("type", "status") or resp.__setitem__("data", {"status": "idle"}) or resp
        )

        # Hold the dispatch lock on the test thread.
        with server._dispatch_lock:
            # Dispatch get_status on a worker thread, must NOT block.
            done = threading.Event()
            box: list[object] = []

            def dispatch() -> None:
                box.append(server._dispatch({"id": 1, "type": "get_status"}))
                done.set()

            t = threading.Thread(target=dispatch)
            t.start()
            assert done.wait(timeout=0.5), (
                "GT-25: read-only dispatch (get_status) blocked waiting "
                "for _dispatch_lock, read-only handlers MUST bypass the "
                "lock so a state-mutating handler can't stall status polls."
            )
            t.join(timeout=1.0)
            assert box and box[0]["type"] == "status"

    def test_shutdown_toctou_recheck_blocks_handler(self) -> None:
        """unlocked gate at the top of ``_dispatch`` and the locked"""
        server = _make_server()
        server.app._shutting_down = True  # type: ignore[assignment]
        # Mirror the production contract by setting the cached snapshot
        server._cached_shutting_down = True  # type: ignore[assignment]

        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result is not None
        assert result["type"] == "error", f"GT-45: dispatch during shutdown must return error; got {result}"
        assert result["data"]["code"] == "server.shutting_down"

    def test_shutdown_recheck_inside_lock_closes_toctou(self) -> None:
        """flag flip between the unlocked gate and the locked handler"""
        src = inspect.getsource(IPCServer._dispatch)
        # The lock is acquired via the bounded helper; the re-check must
        # appear AFTER acquisition (handler runs only while holding it,
        # which closes the TOCTOU window; give-up yields server.busy).
        lock_idx = src.find("self._acquire_dispatch_lock(")
        assert lock_idx >= 0, "GT-45: _dispatch must acquire _dispatch_lock."
        recheck_idx = src.find("_shutting_down", lock_idx)
        assert recheck_idx > lock_idx, (
            "GT-45: _dispatch must re-check _shutting_down INSIDE the held dispatch lock (closes the TOCTOU window)."
        )


# FIFO re-merge ──────────────────────────────────────────────


class TestReMerge:
    """the pending-event re-merge preserves FIFO order."""

    def test_send_source_uses_fifo_remerge(self) -> None:
        """The re-merge expression must be ``pending + self._pending_tcp"""
        import re

        src = inspect.getsource(IPCServer._send)
        # Strip comment-only lines (a line whose first non-whitespace
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)

        assert "self._pending_tcp = pending + self._pending_tcp + [line]" in code_only, (
            "GT-48: _send must re-merge with correct FIFO ordering: "
            "self._pending_tcp = pending + self._pending_tcp + [line]. "
            "The previous extend(pending) + append(line) sequence placed "
            "OLD snapshot events AFTER concurrent-thread NEW events."
        )
        # The buggy extend-based re-merge must be GONE from code lines.
        buggy_pattern = re.compile(r"^\s*self\._pending_tcp\.extend\(pending\)\s*$", re.MULTILINE)
        assert not buggy_pattern.search(code_only), (
            "GT-48 / XV-82: _send must NOT execute "
            "self._pending_tcp.extend(pending) as a Python statement, "
            "use the FIFO-correct "
            "pending + self._pending_tcp + [line] expression instead."
        )

    def test_send_source_gates_snapshot_on_tcp_client(self) -> None:
        """``if tcp_client is not None:`` so the tcp_mode branch never"""
        src = inspect.getsource(IPCServer._send)
        assert "if tcp_client is not None:" in src, (
            "XV-82 / GT-48: _send must gate the _pending_tcp snapshot "
            "on 'if tcp_client is not None:' so the disconnected "
            "(tcp_mode) branch doesn't snapshot+clear, eliminating the "
            "FIFO race at its root."
        )

    def test_fifo_order_preserved_when_no_client(self) -> None:
        """
        When there's no connected client, push events must accumulate
        Pre-existing entries must NOT be cleared (the snapshot is gated
        """
        server = make_bare_ipc_server(send_path=True)
        # Plain-list pending override: the pre-existing entries must
        server._pending_tcp = ['{"old":1}', '{"old":2}']
        server._tcp_client = None

        # Push a new event, must append at the END.
        server._send({"type": "test", "seq": 3})

        assert len(server._pending_tcp) == 3, (
            f"GT-48: expected 3 entries (2 pre-existing + 1 new), got {len(server._pending_tcp)}."
        )
        # Old entries must come BEFORE the new one (FIFO).
        assert '"old":1' in server._pending_tcp[0]
        assert '"old":2' in server._pending_tcp[1]
        # The new entry must be the LAST one and contain "seq" (the JSON
        assert '"seq"' in server._pending_tcp[2], (
            f"GT-48: new event must be at the END (FIFO); got {server._pending_tcp[-1]!r}."
        )
        assert server._pending_tcp[2].endswith("}"), (
            f"GT-48: new event must be a complete JSON object at the END; got {server._pending_tcp[-1]!r}."
        )


class TestAckBeforeCleanup:
    """``_handle_shutdown`` returns the ack BEFORE ``service.quit()``"""

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
            f"service.quit(), host will force-kill the sidecar mid-cleanup."
        )
        assert result is not None
        assert result["type"] == "result"
        assert result["data"] == {"ack": True}

    def test_cleanup_runs_on_background_thread(self) -> None:
        """``service.quit()`` runs on a daemon background thread,"""
        server = _make_server()
        caller_thread = threading.current_thread()
        captured: dict[str, object] = {}

        def recording_quit() -> None:
            captured["thread"] = threading.current_thread()
            captured["is_daemon"] = threading.current_thread().daemon

        server.service.quit.side_effect = recording_quit
        server._handle_shutdown(data=None, resp={"id": 1})

        # Wait for the background thread to land its call.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and "thread" not in captured:
            time.sleep(0.005)
        assert "thread" in captured, (
            "GT-5: service.quit() was not called within 2s, the background cleanup thread never started."
        )
        assert captured["thread"] is not caller_thread, (
            "GT-5: service.quit() ran on the dispatch pool thread, the "
            "cleanup must run on a background daemon thread so the ack "
            "frame reaches the host before the ~95s _do_cleanup blocks."
        )
        assert captured["is_daemon"] is True, (
            "GT-5: the cleanup thread must be a daemon so it doesn't "
            "block process exit if the host force-kills the sidecar."
        )


# BaseException catch ──────────────────────────────────────


class TestBaseExceptionCatch:
    """GT-C3-7: ``_handle_shutdown``'s cleanup thread catches"""

    def test_systemexit_in_service_quit_does_not_propagate(self) -> None:
        server = _make_server()
        server.service.quit.side_effect = SystemExit("deep cleanup exit")

        # Must NOT raise, the ack is returned before the thread starts,
        result = server._handle_shutdown(data=None, resp={"id": 1})
        assert result is not None
        assert result["data"] == {"ack": True}

        # SystemExit). The thread must NOT propagate.
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


class TestConcreteTypes:
    """GT-D1-5: ``service`` parameter and ``_on_sigusr1``'s ``_frame``"""

    def test_service_param_typed_as_voice_typer_service(self) -> None:
        """The ``service`` parameter in ``__init__`` must be annotated"""
        sig = inspect.signature(IPCServer.__init__)
        service_ann = sig.parameters["service"].annotation
        ann_str = service_ann if isinstance(service_ann, str) else str(service_ann)
        assert "LausuService" in ann_str, (
            f"GT-D1-5: __init__'s service parameter must be annotated LausuService | None (was Any); got {ann_str!r}."
        )
        assert "Any" not in ann_str or "LausuService" in ann_str, (
            f"GT-D1-5: service parameter must NOT be Any; got {ann_str!r}."
        )

    def test_on_sigusr1_frame_typed_as_frametype(self) -> None:
        """``_on_sigusr1``'s ``_frame`` parameter must be annotated"""
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
    """GT-D1-10: ``_send``'s ``_out`` and ``_client`` parameters are"""

    def test_send_out_typed_as_textio(self) -> None:
        sig = inspect.signature(IPCServer._send)
        out_ann = sig.parameters["_out"].annotation
        ann_str = out_ann if isinstance(out_ann, str) else str(out_ann)
        assert "TextIO" in ann_str, f"GT-D1-10: _send's _out parameter must be typed TextIO | None; got {ann_str!r}."

    def test_send_client_typed_as_object(self) -> None:
        sig = inspect.signature(IPCServer._send)
        client_ann = sig.parameters["_client"].annotation
        ann_str = client_ann if isinstance(client_ann, str) else str(client_ann)
        assert "object" in ann_str or "_TCPLineIO" in ann_str, (
            f"GT-D1-10: _send's _client parameter must be typed (object | None or _TCPLineIO | None); got {ann_str!r}."
        )


class TestDispatchCastNotSuppression:
    """YJ-27: ``IPCServer._dispatch``'s ``handler = _resolved`` line"""

    def _dispatch_source(self) -> str:
        """Return the source of ``IPCServer._dispatch`` (the bound"""
        import inspect

        return inspect.getsource(IPCServer._dispatch)

    def test_dispatch_assignment_has_no_type_ignore_suppression(self) -> None:
        """No ``handler = ...`` assignment line in ``_dispatch`` may"""
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
        """The dispatch's handler assignment MUST use"""
        src = self._dispatch_source()
        assert "typing.cast(CommandHandler, _resolved)" in src or "cast(CommandHandler, _resolved)" in src, (
            "YJ-27 regression: dispatch no longer uses "
            "`typing.cast(CommandHandler, _resolved)`. The handler "
            "assignment must use the typed cast, NOT bare assignment."
        )

    def test_dispatch_handler_still_invokes_correctly(self) -> None:
        """End-to-end sanity: dispatching a real command (heartbeat)"""
        server = _make_server()
        result = server._dispatch({"type": "heartbeat", "id": 42})
        assert result is None or isinstance(result, dict), (
            f"YJ-27 sanity failure: dispatch returned {type(result)!r}, "
            f"expected None or dict (the cast must not corrupt the "
            f"resolved handler)."
        )


class _ConfigLike:
    """Minimal stand-in for the real ``Config`` dataclass."""

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
            # Boolean flag with "password" substring, does NOT match
            "warn_password_paste",
            # Field with "credential" substring but not as suffix.
            "credential_store_enabled",
            # Field with "bearer" substring but not as suffix.
            "bearer_mode",
        ],
    )
    def test_does_not_match_benign_fields(self, name):
        assert _is_secret_field_name(name) is False, (
            f"Field {name!r} should NOT be classified as secret, the "
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
        """A secret-bearing field NOT in ``_SECRET_CONFIG_FIELDS`` must"""
        cfg = _ConfigLike(**{field_name: value})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL, (
            f"Pattern-denylist failed for {field_name!r}: expected "
            f"{_REDACTED_SENTINEL!r}, got {out[field_name]!r}. The field "
            f"matches a _SECRET_FIELD_PATTERNS entry and must be "
            f"redacted even though it's not in _SECRET_CONFIG_FIELDS."
        )

    def test_exact_name_password_is_redacted(self):
        """A field literally named ``password`` (exact-match pattern)"""
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
        """The boolean flag ``warn_password_paste`` (a real Config"""
        cfg = _ConfigLike(warn_password_paste=True)
        out = _sanitize_config_for_ipc(cfg)
        assert out["warn_password_paste"] is True, (
            "warn_password_paste is a boolean flag (NOT a secret); "
            "it must not be redacted by the pattern-based denylist."
        )

    def test_cloud_api_url_not_redacted(self):
        """``cloud_api_url`` ends in ``_url``, not ``_api_key``, must"""
        cfg = _ConfigLike(cloud_api_url="https://api.example.com/v1")
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_url"] == "https://api.example.com/v1"


class TestSanitizeFalsyValues:
    """redaction masks any set value. ``0`` / ``False`` are masked;"""

    def test_falsy_zero_is_redacted(self):
        """A secret stored as ``0`` (integer) is redacted, previously"""
        cfg = _ConfigLike(azure_api_key=0)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_falsy_false_is_redacted(self):
        """A secret stored as ``False`` (boolean) is redacted."""
        cfg = _ConfigLike(azure_api_key=False)
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == _REDACTED_SENTINEL

    def test_falsy_empty_string_is_preserved(self):
        """``\"\"``), and the renderer shows \"not configured\" for it."""
        cfg = _ConfigLike(azure_api_key="")
        out = _sanitize_config_for_ipc(cfg)
        assert out["azure_api_key"] == ""

    def test_none_value_is_preserved(self):
        """``None`` is preserved so the renderer can distinguish \"not"""
        cfg = _ConfigLike(cloud_api_key=None)
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_key"] is None

    def test_truthy_string_is_redacted(self):
        """The previously happy path: a truthy string secret is redacted."""
        cfg = _ConfigLike(cloud_api_key="sk-real-key-12345")
        out = _sanitize_config_for_ipc(cfg)
        assert out["cloud_api_key"] == _REDACTED_SENTINEL

    def test_real_key_value_does_not_leak(self):
        """Grep the full sanitized dict: no real key value should"""
        real_value = "sk-unique-marker-12345"
        cfg = _ConfigLike(
            cloud_api_key=real_value,
            azure_api_key=0,  # falsy, would have leaked pre-
            oauth_token=False,  # falsy, would have leaked pre-
        )
        out = _sanitize_config_for_ipc(cfg)
        serialized = str(out)
        assert real_value not in serialized


class TestSanitizePreservesNonSecretFields:
    """regression guard: the sanitizer must not redact non-secret"""

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
    """the 5 fields in ``_SECRET_CONFIG_FIELDS`` are still"""

    @pytest.mark.parametrize("field_name", sorted(_SECRET_CONFIG_FIELDS))
    def test_existing_secret_field_still_redacted(self, field_name):
        cfg = _ConfigLike(**{field_name: "sk-real-key"})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == _REDACTED_SENTINEL


class TestSanitizeContractWithRealConfig:
    """(d): contract test, every field on the real ``Config``"""

    def _real_config_fields(self):
        """Return the set of dataclass field names on the real Config."""
        from voice_typer.server.config import Config

        # ``Config`` is a dataclass: ``dataclasses.fields`` returns
        return {f.name for f in dataclasses.fields(Config())}

    def test_every_pattern_matching_config_field_is_redacted(self):
        """For each field on the real Config that matches a secret-name"""
        from voice_typer.server.config import Config

        cfg = Config()
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
        """Sanity check: at least one non-secret field on Config is"""
        from voice_typer.server.config import Config

        cfg = Config()
        out = _sanitize_config_for_ipc(cfg)
        # ``hotkey`` is a plain config field, must not be redacted.
        assert "hotkey" in out, "hotkey field missing from sanitized output"
        assert out["hotkey"] != _REDACTED_SENTINEL, (
            "hotkey was redacted, the pattern denylist is too broad and is matching non-secret fields."
        )


class TestCommandCostsContract:
    """every command in ``_COMMAND_REGISTRY`` has an explicit"""

    def test_every_registered_command_has_explicit_cost(self):
        """explicit entry in ``COMMAND_COSTS``. Fails with a clear list"""
        registered = set(IPCServer._COMMAND_REGISTRY.keys())
        listed = set(COMMAND_COSTS.keys())
        missing = registered - listed
        assert not missing, (
            f"Commands registered in _COMMAND_REGISTRY but missing "
            f"from COMMAND_COSTS: {sorted(missing)}. Each registered "
            f"command MUST have an explicit cost entry, add them to "
            f"COMMAND_COSTS in voice_typer/server/ipc/rate_limiter.py. "
            f"Cost tiers: 1=cheap read, 2=small write, 3=compute, "
            f"5=starts long-lived resource, 10=heavy I/O, 20=very "
            f"heavy, 50=network-saturating download."
        )

    def test_command_costs_does_not_list_unknown_commands(self):
        """Sanity check: ``COMMAND_COSTS`` must not contain commands"""
        registered = set(IPCServer._COMMAND_REGISTRY.keys())
        listed = set(COMMAND_COSTS.keys())
        stale = listed - registered
        assert not stale, (
            f"COMMAND_COSTS contains entries for commands NOT in "
            f"_COMMAND_REGISTRY: {sorted(stale)}. These are stale "
            f"entries pointing at removed/renamed commands, remove "
            f"them from COMMAND_COSTS."
        )

    def test_all_costs_are_positive_integers(self):
        """Each cost must be a positive integer (>= 1). A cost of 0"""
        for cmd, cost in COMMAND_COSTS.items():
            assert isinstance(cost, int), f"COMMAND_COSTS[{cmd!r}] = {cost!r} is not an int."
            assert cost >= 1, (
                f"COMMAND_COSTS[{cmd!r}] = {cost} < 1, costs must be "
                f"positive integers (the limiter clamps <1 to 1, but "
                f"the map should not encode that)."
            )


class TestCommandCostsPreserved:
    """the 5 pre-existing entries are preserved (regression guard"""

    def test_download_model_cost_50(self):
        assert COMMAND_COSTS["download_model"] == 50

    def test_import_model_cost_20(self):
        assert COMMAND_COSTS["import_model"] == 20

    def test_heartbeat_cost_1(self):
        assert COMMAND_COSTS["heartbeat"] == 1


class TestCommandCostsNewlyListed:
    """spot-check a few of the newly-listed expensive commands"""

    @pytest.mark.parametrize(
        "cmd, min_cost",
        [
            # Heavy I/O or subprocess (cost >= 10).
            ("delete_model", 10),
            ("transcribe_offline", 10),
            ("run_prewarm", 10),
            ("restart_app", 10),
            ("resume_model_download", 10),
            ("clear_history", 10),
            # Moderate (cost >= 5).
            ("quit_app", 5),
            ("shutdown", 5),
            ("onboarding_apply", 5),
            ("microphone_test_start", 5),
            # Light-moderate (cost >= 3).
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
    """elevated cost, a cost-10 command consumes 10 of the 200/s burst"""

    def test_cost_10_command_rejected_after_20_calls_in_burst_window(self):
        """limiter accepts at most 20 calls in any 1-second window"""
        # ``sustained_per_sec`` is the TOTAL budget over the 10s window
        assert COMMAND_COSTS["clear_history"] == 10, (
            "clear_history cost changed, pick another cost-10 command for this test"
        )
        limiter = _RateLimiter(burst=200, sustained_per_sec=10_000, window=10.0)
        accepted = 0
        # 25 calls at t=0, should accept 20 (20*10=200=burst), reject 5.
        for _ in range(25):
            if limiter.allow(command="clear_history", now=0.0):
                accepted += 1
        assert accepted == 20, (
            f"Expected 20 acceptances (burst=200 / cost=10 = 20), got "
            f"{accepted}. The rate limiter is not applying the elevated "
            f"COMMAND_COSTS['clear_history'] cost."
        )

    def test_cost_1_command_accepted_200_times_in_burst_window(self):
        """Sanity check: a cost-1 command (e.g. ``get_status``) still"""
        assert COMMAND_COSTS["get_status"] == 1, "get_status cost changed, pick another cost-1 command for this test"
        limiter = _RateLimiter(burst=200, sustained_per_sec=10_000, window=10.0)
        accepted = 0
        for _ in range(205):
            if limiter.allow(command="get_status", now=0.0):
                accepted += 1
        assert accepted == 200, f"Expected 200 acceptances (cost=1), got {accepted}."

    def test_heartbeat_bypasses_rate_limiter_under_burst_attack(self):
        """(High): a heartbeat must ALWAYS be accepted, even"""
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


class TestHistoryOffsetMaxConstant:
    """the ``_HISTORY_OFFSET_MAX`` constant exists and is set"""

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
        """Python big-ints are unbounded, without the cap, a 5000-digit"""
        import sys

        # Bump the int-conversion digit limit so a 5000-digit literal
        original_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(10_000)
            huge = int("9" * 5000)
            assert huge > _HISTORY_OFFSET_MAX
            assert _bound_history_offset(huge) == _HISTORY_OFFSET_MAX
        finally:
            sys.set_int_max_str_digits(original_limit)

    def test_float_above_max_clamped_to_max(self):
        """Floats are converted to int via ``int(raw)``; a float above"""
        assert _bound_history_offset(99_999_999.5) == _HISTORY_OFFSET_MAX

    def test_string_above_max_clamped_to_max(self):
        assert _bound_history_offset("999999999999") == _HISTORY_OFFSET_MAX


class TestBoundHistoryLimitUnaffected:
    """regression guard: the existing ``_bound_history_limit``"""

    def test_limit_max_unchanged(self):
        from voice_typer.server.ipc.history_bounds import _HISTORY_LIMIT_MAX

        assert _HISTORY_LIMIT_MAX == 500

    def test_limit_above_max_clamped(self):
        assert _bound_history_limit(1_000_000) == 500

    def test_limit_zero_clamped_to_one(self):
        assert _bound_history_limit(0) == 1

    def test_limit_negative_clamped_to_one(self):
        assert _bound_history_limit(-5) == 1


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
        """envelope MUST NOT carry a ``legacy_code`` key (it would be"""
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
        """namespaced ``client.invalid_payload`` when the payload exceeds"""
        schema = {
            "x": {"type": str, "required": False, "max_payload_bytes": 10},
        }
        # ``data`` serializes to ~30 bytes, well above the 10-byte cap.
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
        """When the schema's ``type`` is a tuple (e.g. ``(str, type(None))``),"""
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
        """The ``max_value_len`` rule emits the namespaced"""
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
    """regression guard: the happy path (valid payload) still"""

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
    """the namespaced codes emitted by ``_validate_dict_payload``"""

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
    """the auto-update feature's ``check_offline_pack_update`` IPC command"""

    def test_command_registered_and_rate_limited(self):
        assert "check_offline_pack_update" in IPCServer._COMMAND_REGISTRY
        assert "check_offline_pack_update" in COMMAND_COSTS

    def test_dispatch_returns_structured_ack(self):
        # Bespoke wiring: the app stub must be an object WITHOUT
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


class TestRateLimiterRunningTotals:
    """XV-81: ``_RateLimiter`` keeps ``self._burst_total`` /"""

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
            "_burst_timestamps) on every call, use the running total."
        )
        assert "sum(c for _, c in self._sustained_timestamps)" not in src, (
            "XV-81: allow() must NOT recompute sum(c for _, c in "
            "_sustained_timestamps) on every call, use the running total."
        )
        # The new fast-path reads must appear.
        assert "self._burst_total" in src, "XV-81: allow() must reference self._burst_total (the running total)."
        assert "self._sustained_total" in src, (
            "XV-81: allow() must reference self._sustained_total (the running total)."
        )

    def test_running_total_matches_sum_after_appends(self):
        """After a sequence of ``allow()`` calls, the running total must"""
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
            "after appends, the running total must stay in sync with the deque."
        )
        assert rl._sustained_total == expected_sustained, (
            f"XV-81: _sustained_total={rl._sustained_total} != sum={expected_sustained} after appends."
        )

    def test_running_total_matches_sum_after_eviction(self):
        """After the burst window slides past old entries, the running"""
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
        """The eviction loop clamps the running totals at >= 0 even"""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        rl.allow(now=0.0)
        # Manually drive the total negative to verify the clamp.
        rl._burst_total = -5
        rl._sustained_total = -5
        # Trigger an eviction that should clamp the totals back to 0.
        rl.allow(now=100.0)
        # After allow(), the totals were clamped to 0 and then
        assert rl._burst_total >= 0, "XV-81: _burst_total must never go negative (clamp at 0)."
        assert rl._sustained_total >= 0, "XV-81: _sustained_total must never go negative (clamp at 0)."


class TestPendingSnapshotGatedOnTcpClient:
    """XV-82: ``IPCServer._send`` only snapshots+clears ``_pending_tcp``"""

    def test_send_source_gates_snapshot_on_tcp_client(self):
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._send)
        assert "if tcp_client is not None:" in src, (
            "XV-82: _send must gate the _pending_tcp snapshot on 'if tcp_client is not None:'."
        )
        # The re-merge in the tcp_mode branch must be GONE.
        assert "self._pending_tcp.extend(pending)" not in src, (
            "XV-82: _send must NOT re-merge pending into _pending_tcp, "
            "the snapshot is gated on tcp_client, so the tcp_mode branch "
            "never has a pending snapshot to re-merge."
        )

    def test_send_does_not_snapshot_when_no_client(self):
        """When ``tcp_client is None`` and ``tcp_mode`` is True, _send"""
        server = make_bare_ipc_server(send_path=True)
        # Pre-populate _pending_tcp with some entries, they must
        server._pending_tcp = ['{"existing":1}', '{"existing":2}']
        server._tcp_client = None  # no client connected

        # Issue a push event, should append + trim, NOT clear.
        server._send({"type": "test", "id": 1})

        # The two pre-existing entries must still be there (
        assert len(server._pending_tcp) == 3, (
            f"XV-82: expected 3 entries in _pending_tcp (2 pre-existing "
            f"+ 1 new), got {len(server._pending_tcp)}. The pre-existing "
            "entries must NOT be cleared when there's no TCP client."
        )
        # The new entry must be at the end.
        assert '"test"' in server._pending_tcp[-1]

    def test_send_still_snapshots_when_tcp_client_present(self):
        """When ``tcp_client is not None``, the snapshot+clear must"""
        from voice_typer.server.ipc_server import _TCPLineIO

        server = make_bare_ipc_server(send_path=True)

        srv, cli = socket.socketpair()
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client
            # Pre-populate _pending_tcp, must be cleared by _send.
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

            assert server._pending_tcp == [], (
                "XV-82 regression: _pending_tcp should have been cleared "
                "when tcp_client is not None (the snapshot path must run)."
            )
        finally:
            srv.close()
            cli.close()


class TestCompactJsonSerialization:
    """``ensure_ascii=False, separators=(\",\", \":\")`` to match the WS"""

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
        """A message with a non-ASCII string must serialize without"""
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
                        break
                    if not chunk:
                        break
                    received.extend(chunk)

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            # Send a message with CJK text, ensure_ascii=False keeps
            server._send({"type": "transcription_final", "text": "你好世界"})

            t.join(timeout=2.0)
            with _ctxlib.suppress(Exception):
                cli.shutdown(socket.SHUT_RDWR)
            cli.close()
            srv.close()
            t.join(timeout=1.0)

            line = received.decode("utf-8").strip()
            # The wire format must contain the raw CJK chars (not
            assert "你好世界" in line, (
                f"XV-83: ensure_ascii=False must keep CJK chars as-is in the wire format. Got: {line!r}"
            )
            # The compact separators must NOT insert whitespace after
            assert '", "' not in line, "XV-83: separators=(',', ':') must not leave whitespace after the comma."
            assert '": "' not in line, "XV-83: separators=(',', ':') must not leave whitespace after the colon."
        finally:
            with _ctxlib.suppress(Exception):
                cli.close()
            with _ctxlib.suppress(Exception):
                srv.close()


class TestWriterEncodesOnce:
    """send. Pre-XV-84 the code did ``raw.encode(\"utf-8\")`` for the size"""

    def test_writer_source_encodes_to_bytes_once(self):
        # The writer was refactored from a nested closure inside
        import inspect as _inspect

        from voice_typer.server.sidecar_ws_internals import outbound as _sidecar_outbound

        src = _inspect.getsource(_sidecar_outbound)
        assert 'return json.dumps(event, ensure_ascii=False).encode("utf-8")' in src, (
            "XV-84: _encode_ws_frame must encode ONCE via json.dumps(event, ensure_ascii=False).encode('utf-8')."
        )
        # XV-84 + IN-35: the encode is offloaded to a DEDICATED
        assert "raw_bytes = await loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)" in src, (
            "XV-84: _writer must offload the encode to _encode_ws_frame via "
            "loop.run_in_executor(_get_ws_encode_pool(), ...) and assign raw_bytes."
        )
        assert 'len(raw.encode("utf-8"))' not in src, (
            "XV-84: _writer must NOT re-encode via len(raw.encode('utf-8')), encode once and reuse the buffer."
        )
        # TEXT payload (the C-WS-2 wire contract: the Rust host parses
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


class TestValidationHoistsJsonAndCaches:
    """module top and caches the per-schema ``max_payload_bytes`` lookup"""

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
            "XV-85: _validate_dict_payload must NOT do 'import json as _json_mod' per call, hoist to module top."
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
        # The cache must be bounded, verify the cap is reasonable.
        assert validation._MAX_PAYLOAD_BYTES_CACHE_MAX <= 4096, (
            "XV-85: _MAX_PAYLOAD_BYTES_CACHE_MAX must be bounded to prevent unbounded growth from per-call schemas."
        )

    def test_cache_hits_on_second_call_with_same_schema(self):
        """Calling _validate_dict_payload twice with the SAME schema"""
        from voice_typer.server.ipc.validation import (
            _MAX_PAYLOAD_BYTES_CACHE,
            _MAX_PAYLOAD_BYTES_CACHE_SEEN,
            _validate_dict_payload,
        )

        # Clear the cache to start fresh.
        _MAX_PAYLOAD_BYTES_CACHE.clear()
        _MAX_PAYLOAD_BYTES_CACHE_SEEN.clear()

        # Use a module-level-stable schema (defined once at class scope
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

        _validate_dict_payload({"hotkey": "ctrl+b"}, schema)
        # The cache size must not have grown (no new entry added).
        assert len(_MAX_PAYLOAD_BYTES_CACHE) == cache_size_after_first, (
            "XV-85: second call with the same schema must NOT add a new "
            "cache entry (id-stable schemas should hit the cache)."
        )
        assert len(_MAX_PAYLOAD_BYTES_CACHE_SEEN) == seen_size_after_first

    def test_cache_bounded_under_per_call_schemas(self):
        """Calling _validate_dict_payload with a FRESH schema each call"""
        from voice_typer.server.ipc.validation import (
            _MAX_PAYLOAD_BYTES_CACHE,
            _MAX_PAYLOAD_BYTES_CACHE_MAX,
            _MAX_PAYLOAD_BYTES_CACHE_SEEN,
            _validate_dict_payload,
        )

        _MAX_PAYLOAD_BYTES_CACHE.clear()
        _MAX_PAYLOAD_BYTES_CACHE_SEEN.clear()

        # Issue many more calls than the cache cap, each with a fresh
        n = _MAX_PAYLOAD_BYTES_CACHE_MAX * 3
        for _ in range(n):
            _validate_dict_payload({}, {})

        # The cache must NOT have grown past the cap.
        assert len(_MAX_PAYLOAD_BYTES_CACHE) <= _MAX_PAYLOAD_BYTES_CACHE_MAX, (
            f"XV-85: cache grew to {len(_MAX_PAYLOAD_BYTES_CACHE)} > cap "
            f"{_MAX_PAYLOAD_BYTES_CACHE_MAX}, FIFO eviction must bound it."
        )
        assert len(_MAX_PAYLOAD_BYTES_CACHE_SEEN) <= _MAX_PAYLOAD_BYTES_CACHE_MAX

    def test_max_payload_bytes_still_enforced(self):
        """Sanity: the max_payload_bytes rule still fires after the"""
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


class TestTransportBuffering:
    """XV-86: ``_TCPLineIO.__init__`` uses ``io.DEFAULT_BUFFER_SIZE``"""

    def test_init_uses_default_buffer_size(self):
        from voice_typer.server.ipc.transport import _TCPLineIO

        src = inspect.getsource(_TCPLineIO.__init__)
        assert "io.DEFAULT_BUFFER_SIZE" in src, (
            "XV-86: _TCPLineIO must use io.DEFAULT_BUFFER_SIZE for the "
            "read-side buffering argument (not 1, which is a write-side "
            "concept)."
        )
        assert "buffering=1" not in src, (
            "XV-86: _TCPLineIO must NOT use buffering=1 (line buffering) for the read side, use io.DEFAULT_BUFFER_SIZE."
        )

    def test_module_imports_io(self):
        from voice_typer.server.ipc import transport

        assert hasattr(transport, "io"), "XV-86: transport module must import io at module top."
        assert transport.io is io

    def test_makefile_called_with_default_buffer_size(self):
        """A real socket's makefile must accept the new buffering"""
        from voice_typer.server.ipc.transport import _TCPLineIO

        srv, cli = socket.socketpair()
        try:
            io_obj = _TCPLineIO(srv)
            assert hasattr(io_obj._reader, "readline")
            # Write a line through the other end and read it back.
            cli.sendall(b"hello world\n")
            line = io_obj._reader.readline()
            assert line == "hello world\n"
        finally:
            srv.close()
            cli.close()


class TestRateLimiterResolvedOnce:
    """XV-87: ``sidecar_ws._make_dispatch`` resolves the shared rate"""

    def test_make_dispatch_source_resolves_limiter_in_closure(self):
        from voice_typer.server import sidecar_ws

        src = inspect.getsource(sidecar_ws._make_dispatch)
        # The rate_limiter assignment must appear BEFORE the inner
        dispatch_idx = src.find("async def dispatch")
        assert dispatch_idx != -1
        before_dispatch = src[:dispatch_idx]
        assert "rate_limiter = _get_rate_limiter(server)" in before_dispatch, (
            "XV-87: _make_dispatch must resolve rate_limiter ONCE in the "
            "closure body (before the inner dispatch() definition), not "
            "per-call inside dispatch()."
        )

    def test_dispatch_does_not_call_get_rate_limiter(self):
        """The inner ``dispatch()`` closure must NOT call"""
        from voice_typer.server import sidecar_ws

        src = inspect.getsource(sidecar_ws._make_dispatch)
        # Find the inner dispatch function body.
        dispatch_idx = src.find("async def dispatch")
        assert dispatch_idx != -1
        dispatch_body = src[dispatch_idx:]
        assert "_get_rate_limiter(server)" not in dispatch_body, (
            "XV-87: dispatch() must NOT call _get_rate_limiter(server) "
            "per frame, the limiter is resolved ONCE in the closure."
        )
        # The closure-captured rate_limiter must be referenced.
        assert "rate_limiter.allow" in dispatch_body, (
            "XV-87: dispatch() must reference the closure-captured rate_limiter.allow(command=...)."
        )

    def test_dispatch_uses_same_limiter_across_calls(self):
        """Two dispatch() calls on the same _make_dispatch-derived"""
        # Build a fake server with a real _RateLimiter instance so we
        from concurrent.futures import ThreadPoolExecutor

        from voice_typer.server import sidecar_ws
        from voice_typer.server.ipc_server import _get_rate_limiter

        class FakeServer:
            pass

        server = FakeServer()
        server._ws_dispatch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-xv87")
        # Resolve the limiter the same way _make_dispatch does, once.
        limiter_before = _get_rate_limiter(server)
        sidecar_ws._make_dispatch(server)
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


class TestStdinGate:
    """the unauthenticated stdin/stdout IPC listener is"""

    def test_stdin_ipc_env_var_module_constant_exists(self) -> None:
        """``_STDIN_IPC_ENV_VAR`` module-level constant exists"""
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
        """
        when ``_tcp_mode`` is False AND the env var is unset,
        ``start()`` must NOT spawn the stdin listener. ``_stdin_thread``
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
        """end-to-end behavior: ``start()`` with ``_tcp_mode``"""
        from voice_typer.server import event_bus

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Also clear TAURI_SIDECAR so the heartbeat thread is created
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        server, _app, _service = make_ipc_server_with_fakes(thread_registry=None)
        server._tcp_mode = False

        # Stub out event_bus.subscribe so we don't leak the push fn.
        subscribed: list = []
        monkeypatch.setattr(event_bus, "subscribe", lambda fn: subscribed.append(fn))
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
            # from being created. ``ipc-server`` must NOT be in the
            assert "ipc-server" not in created_threads, (
                "stdin listener 'ipc-server' thread was spawned "
                "even though VOICE_TYPER_ALLOW_STDIN_IPC is unset, the "
                "gate failed to refuse the unauthenticated stdin path."
            )
            assert server._stdin_thread is None, (
                "_stdin_thread must be None when the gate refuses to spawn the stdin listener."
            )
        finally:
            server.stop()

    def test_stdin_thread_spawned_when_env_var_set(self, monkeypatch) -> None:
        """``\"1\"``, ``start()`` must spawn the stdin listener thread."""
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
                "spawned even though VOICE_TYPER_ALLOW_STDIN_IPC=1, "
                "the gate must allow explicit opt-in for dev/testing."
            )
            # ``_stdin_thread`` is a FakeThread instance (not a real
            assert server._stdin_thread is not None, (
                "_stdin_thread must be set when the gate allows the stdin listener (env var is '1')."
            )
        finally:
            server.stop()

    def test_allow_stdin_cli_flag_is_inert(self, monkeypatch) -> None:
        """``--allow-stdin`` is no longer a recognized flag.

        The flag used to set ``VOICE_TYPER_ALLOW_STDIN_IPC=1``, but ``main()``
        hard-sets ``_tcp_mode = True`` for every transport, so it could never
        take effect. The env var remains the only stdin-listener gate.
        """
        import sys

        from voice_typer.server.ipc_server import parse_ipc_args

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--allow-stdin"])
        try:
            port, ws_mode = parse_ipc_args()
            assert os.environ.get("VOICE_TYPER_ALLOW_STDIN_IPC") is None, (
                "--allow-stdin must no longer set VOICE_TYPER_ALLOW_STDIN_IPC; the flag was "
                "removed because main() forces _tcp_mode = True so it could never take effect."
            )
            assert port is None
            assert ws_mode is False
        finally:
            monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)

    def test_no_allow_stdin_flag_does_not_set_env_var(self, monkeypatch) -> None:
        """without ``--allow-stdin``, the env var is NOT set by"""
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
    """``_handle_shutdown`` is idempotent."""

    def test_shutdown_started_event_initialized_in_init(self) -> None:
        """``__init__`` must declare a per-instance"""
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
        """calling ``_handle_shutdown`` twice must NOT spawn two"""
        server = _make_server()
        # Stub service.quit so it returns immediately (no real cleanup).
        server.service.quit = MagicMock()

        result1 = server._handle_shutdown(data=None, resp={"id": 1})
        result2 = server._handle_shutdown(data=None, resp={"id": 2})

        # Both invocations return the ack envelope (the host's retry
        assert result1 is not None and result1["data"] == {"ack": True}
        assert result2 is not None and result2["data"] == {"ack": True}

        # Wait briefly for the cleanup thread to land its call.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and server.service.quit.call_count < 1:
            time.sleep(0.005)
        assert server.service.quit.call_count == 1, (
            f"service.quit was called "
            f"{server.service.quit.call_count} times; expected exactly 1. "
            f"The double-shutdown race spawned a second cleanup thread."
        )

    def test_shutdown_started_event_set_after_first_invocation(self) -> None:
        """``_shutdown_started`` event must be set so the second"""
        server = _make_server()
        server.service.quit = MagicMock()
        assert not server._shutdown_started.is_set()
        server._handle_shutdown(data=None, resp={"id": 1})
        assert server._shutdown_started.is_set(), (
            "_shutdown_started must be set after the first _handle_shutdown call so the second invocation no-ops."
        )

    def test_cleanup_thread_registered_on_thread_registry(self) -> None:
        """``self.app._thread_registry`` (when the app provides one) so"""
        server = _make_server()
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
        """``getattr(self.app, '_thread_registry', None)`` defensive"""
        server = _make_server()
        server.app._thread_registry = None
        server.service.quit = MagicMock()
        # Must NOT raise, the registration path is guarded by
        result = server._handle_shutdown(data=None, resp={"id": 1})
        assert result is not None and result["data"] == {"ack": True}

    def test_handle_shutdown_source_contains_shutdown_started_gate(self) -> None:
        """source-level pin: ``_handle_shutdown`` must check"""
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


class TestRegistryExtraction:
    """``_COMMAND_REGISTRY`` + ``_READONLY_COMMANDS`` +"""

    def test_registry_module_is_importable(self) -> None:
        """the new ``voice_typer.server.ipc.registry`` module"""
        from voice_typer.server.ipc import registry

        assert hasattr(registry, "_COMMAND_REGISTRY"), (
            "ipc.registry must expose _COMMAND_REGISTRY (module-level dict, the canonical source of truth)."
        )
        assert hasattr(registry, "_READONLY_COMMANDS"), "ipc.registry must expose _READONLY_COMMANDS."
        assert hasattr(registry, "_PYTHON_ONLY_COMMANDS"), "ipc.registry must expose _PYTHON_ONLY_COMMANDS."

    def test_registry_module_constants_are_correct_types(self) -> None:
        """the registry module's constants have the documented"""
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
        """``ipc_server.py`` must re-export ``_COMMAND_REGISTRY``,"""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server.ipc import registry

        # Object identity: the module-level name must be the SAME object
        assert ipc_server_mod._COMMAND_REGISTRY is registry._COMMAND_REGISTRY, (
            "ipc_server._COMMAND_REGISTRY must be the SAME object "
            "as registry._COMMAND_REGISTRY (single source of truth, "
            "not a parallel copy)."
        )
        assert ipc_server_mod._READONLY_COMMANDS is registry._READONLY_COMMANDS, (
            "ipc_server._READONLY_COMMANDS must be the SAME object as registry._READONLY_COMMANDS."
        )
        assert ipc_server_mod._PYTHON_ONLY_COMMANDS is registry._PYTHON_ONLY_COMMANDS, (
            "ipc_server._PYTHON_ONLY_COMMANDS must be the SAME object as registry._PYTHON_ONLY_COMMANDS."
        )

    def test_ipc_server_class_re_aliases_registry_constants(self) -> None:
        """
        class:`IPCServer` must re-alias ``_COMMAND_REGISTRY``
        ``IPCServer._PYTHON_ONLY_COMMANDS`` call site (pinned by
        """
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
        """behavior-preserving extraction, same dict, same keys,"""
        from voice_typer.server.ipc import registry

        # Critical entries that other tests pin (test_ipc_shutdown_registry,
        assert registry._COMMAND_REGISTRY["shutdown"] == "_handle_shutdown"
        assert registry._COMMAND_REGISTRY["tray_click"] == "_handle_tray_click"
        assert registry._COMMAND_REGISTRY["heartbeat"] == "_handle_heartbeat"
        # (test_cloud_connection) + XZ-SEC-05 (add_trusted_endpoint)
        # ADR-0023 media trio: local-file jobs first, URLs in Phase 2.
        assert registry._COMMAND_REGISTRY["media_transcribe_start"] == "_handle_media_transcribe_start"
        assert registry._COMMAND_REGISTRY["media_transcribe_cancel"] == "_handle_media_transcribe_cancel"
        assert registry._COMMAND_REGISTRY["media_transcribe_status"] == "_handle_media_transcribe_status"
        assert len(registry._COMMAND_REGISTRY) == 79, (
            f"registry._COMMAND_REGISTRY must contain 79 entries "
            f"(75 forwarded in the Rust allowlist + shutdown + "
            f"tray_click python-only + heartbeat + relaunch_ack host-dispatched); got "
            f"{len(registry._COMMAND_REGISTRY)}. "
            f"If the count drifted, update this test together with the "
            f"registry + the TS/Rust allowlists."
        )

    def test_python_only_commands_unchanged(self) -> None:
        """``_PYTHON_ONLY_COMMANDS`` is the documented"""
        from voice_typer.server.ipc import registry

        assert frozenset({"shutdown", "tray_click"}) == registry._PYTHON_ONLY_COMMANDS, (
            f"registry._PYTHON_ONLY_COMMANDS must be "
            f"frozenset({{'shutdown', 'tray_click'}}); got "
            f"{registry._PYTHON_ONLY_COMMANDS!r}."
        )

    def test_readonly_commands_unchanged(self) -> None:
        """``_READONLY_COMMANDS`` is the documented audited pure-read set.

        Membership means the handler cannot mutate shared state, so the
        dispatcher may run it outside ``_dispatch_lock``. The expected set is
        pinned here; ``tests/test_readonly_commands_audit.py`` enforces the
        classification invariant for every ``get_*`` command.
        """
        from voice_typer.server.ipc import registry

        expected = frozenset(
            {
                "get_status",
                "get_config",
                "get_model_catalog",
                "heartbeat",
                "get_defaults",
                "get_history",
                "get_history_count",
                "get_today_stats",
                "get_favorites",
                "get_transcription_text",
                "get_microphones",
                "get_volume_backend_status",
                "get_model_status",
                "get_prewarm_status",
                "get_vocabulary",
                "get_correction_usage",
                "get_templates",
                "get_download_queue",
                "microphone_test_get_level",
                "onboarding_is_first_run",
                "onboarding_get_microphones",
                "onboarding_get_model_options",
                "onboarding_get_hotkey_presets",
                "onboarding_check_permissions",
            }
        )
        assert expected == registry._READONLY_COMMANDS, (
            f"registry._READONLY_COMMANDS changed; expected\n{expected!r}\ngot\n{registry._READONLY_COMMANDS!r}."
        )

    def test_registry_history_comment_block_present(self) -> None:
        """
        the ~30 "REMOVED" historical comments were
        ``test_dead_code_stays_removed.py`` already pins the removals
        """
        from voice_typer.server.ipc import registry

        src = inspect.getsource(registry)
        assert "Registry history" in src, (
            "ipc/registry.py must contain a '# Registry history' "
            "comment block at the top consolidating the ~30 'REMOVED' "
            "comments that previously lived inline next to the dict "
            "literal in ipc_server.py."
        )

    def test_ipc_server_no_longer_defines_inline_dict_literal(self) -> None:
        """``ipc_server.py`` must NOT contain the inline"""
        import voice_typer.server.ipc_server as ipc_server_mod

        src = inspect.getsource(ipc_server_mod)
        # dict body. We must NOT find the literal form.
        assert "_COMMAND_REGISTRY: dict[str, str] = {" not in src, (
            "ipc_server.py must NOT define the inline "
            "_COMMAND_REGISTRY dict literal, it has been extracted to "
            "ipc.registry. The class-level alias "
            "(``_COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY``) "
            "is the only allowed form."
        )


class TestTranscribeOfflineDegradation:
    """Phase 2d degradation matrix (§8.10)."""

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
        server = _make_server()
        resp = server._dispatch(
            {
                "id": 7,
                "type": "transcribe_offline",
                "data": {"audio_path": "C:\\tmp\\clip.wav", "sample_rate": 16000, "language": None},
            }
        )
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
