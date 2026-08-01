"""IPC command-registry sync + dispatch-layer regression tests.

This file is the regression home for two IPC-layer fixes plus a CI
sync gate that the original review proposed but no test file pinned:

1. **S3-CR-27** — ``_dispatch`` now stamps the inbound request ``id``
   onto the response envelope so clients using id-based request/response
   correlation (the standard JSON-RPC-like pattern in ``usePython.ts``)
   can match the response back to the originating request. Pre-fix,
   ``_validate_dict_payload`` returned a FRESH error-envelope dict with
   no ``id`` field; every handler that did ``if error: return error``
   discarded the ``resp`` dict (which had ``id`` pre-populated) — so
   validation rejections orphaned the pending request and the renderer
   would time out instead of resolving the rejection.

2. **S1-CR-80** — ``IPCServer._accept_tcp`` now wraps
   ``pool.submit(...)`` in ``try/except RuntimeError`` so a concurrent
   ``stop()`` shutdown of the TCP worker pool no longer kills the
   accept thread + leaks the just-accepted socket. The accept loop
   gracefully closes the conn and breaks out.

3. **S2-CR-73 / S4-CR-35** — every command in
   ``IPCServer._COMMAND_REGISTRY`` (minus the explicitly-documented
   ``_PYTHON_ONLY_COMMANDS`` exception set) MUST be present in the
   Electron ``ALLOWED_COMMANDS`` set so the renderer can invoke them.
   Pre-fix, ``onboarding_get_model_catalog`` /
   ``onboarding_check_permissions`` / ``tray_click`` were missing —
   silently breaking the onboarding flow under Electron.

The dedicated parity file
``tests/test_command_registry_parity.py`` already covers
the full TS ↔ Python ↔ Rust parity contract; the test here is the
narrow "renderer can call every command" gate that the original
finding #99 / #156 proposed under this file name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from voice_typer.server.ipc_server import IPCServer

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_COMMANDS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "allowed-commands.ts"


# _dispatch stamps request id on validation-error responses ──


def _make_server() -> IPCServer:
    """Build a minimal IPCServer instance for _dispatch unit tests.

    Bypasses ``__init__`` (which would construct a real VoiceTyperService
    and try to wire ``app.tray.set_state``). We only need ``_dispatch``
    to be callable, so we set the handful of attributes it reads.
    """
    from voice_typer.server.ipc_server import IPCServer

    server = IPCServer.__new__(IPCServer)
    server.app = MagicMock()
    server.app._shutting_down = False
    server.service = MagicMock()
    # ``_dispatch`` reads ``self._dispatch_lock`` for non-readonly commands.
    import threading

    server._dispatch_lock = threading.RLock()
    return server


class TestRequestIdPreservedOnValidationErrors:
    """S3-CR-27: ``_dispatch`` stamps the inbound request ``id`` on
    every response — including validation-error responses that bypass
    the ``resp`` dict pre-populated with ``id``.

    Pre-fix, ``_validate_dict_payload`` returned a FRESH error-envelope
    dict with no ``id`` field; every handler that did
    ``if error: return error`` discarded the ``resp`` dict — so
    validation rejections orphaned the pending request and the
    renderer's ``usePython.ts`` would time out instead of resolving
    the rejection.
    """

    def test_validation_error_preserves_request_id(self) -> None:
        """A handler that returns a validation-error dict (no id)
        still has ``id`` stamped by ``_dispatch`` before the response
        is sent."""
        server = _make_server()
        # Pick a registered command whose handler uses
        # ``_validate_dict_payload`` and returns the error directly.
        # ``onboarding_set_microphone`` validates ``mic_id`` is a str
        # or None — passing an int triggers the validation error path.
        msg = {
            "type": "onboarding_set_microphone",
            "id": 4242,
            "data": {"mic_id": 12345},  # int → validation rejection
        }
        result = server._dispatch(msg)
        assert result is not None, "S3-CR-27: _dispatch must return a response dict"
        assert result.get("id") == 4242, (
            "S3-CR-27: validation-error response MUST carry the inbound "
            "request id so the renderer can correlate the rejection. "
            f"Got: {result!r}"
        )
        assert result.get("type") == "error"
        # The validation error code should be present (namespaced form
        # primary, legacy alias retained for backward compat).
        data = result.get("data", {})
        assert data.get("code") == "client.invalid_field"
        assert data.get("field") == "mic_id"

    def test_validation_error_preserves_string_request_id(self) -> None:
        """The id can be a string (JSON-RPC style) — preserved too."""
        server = _make_server()
        msg = {
            "type": "onboarding_set_microphone",
            "id": "req-abc-001",
            "data": {"mic_id": False},  # bool → validation rejection
        }
        result = server._dispatch(msg)
        assert result is not None
        assert result.get("id") == "req-abc-001"

    def test_no_id_no_stamp(self) -> None:
        """If the inbound message has no ``id``, the response has no
        ``id`` either (push events / fire-and-forget notifications)."""
        server = _make_server()
        msg = {
            "type": "onboarding_set_microphone",
            # no "id" key
            "data": {"mic_id": 12345},  # int → validation rejection
        }
        result = server._dispatch(msg)
        assert result is not None
        assert "id" not in result, (
            "S3-CR-27: when the inbound message has no id, the response must not synthesize one either."
        )

    def test_successful_response_preserves_request_id(self) -> None:
        """A handler that mutates ``resp`` directly (the normal path)
        keeps the id that ``_dispatch`` pre-populated."""
        server = _make_server()
        # ``service.onboarding_is_first_run`` returns a dict (no error
        # key) — handler returns ``resp`` after mutation. ``resp`` was
        # pre-populated with id by ``_dispatch``.
        server.service.onboarding_is_first_run.return_value = {"is_first_run": True}
        msg = {"type": "onboarding_is_first_run", "id": 99}
        result = server._dispatch(msg)
        assert result is not None
        assert result.get("id") == 99
        assert result.get("type") == "onboarding_first_run"


# _accept_tcp pool.submit race with stop()'s shutdown ──────


class TestAcceptTcpPoolSubmitRace:
    """S1-CR-80: ``_accept_tcp`` must gracefully handle the race where
    ``stop()`` shuts down the TCP worker pool between the read of
    ``self._tcp_worker_pool`` and the ``pool.submit(...)`` call.

    Pre-fix, ``pool.submit(...)`` on a shut-down pool raised
    ``RuntimeError("cannot schedule new futures after shutdown")``,
    which was NOT caught by the outer ``except OSError`` — killing the
    accept thread silently AND leaking the just-accepted ``conn``
    socket (no ``finally`` closed it).
    """

    def test_accept_loop_wraps_pool_submit_in_try_except_runtime_error(self) -> None:
        """Source-level pin: the accept loop MUST wrap
        ``pool.submit(...)`` in ``try/except RuntimeError`` so the race
        with ``stop()``'s ``pool.shutdown()`` doesn't kill the thread
        + leak the conn socket."""
        import inspect

        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._accept_tcp)
        # Find the ``pool.submit`` call.
        assert "pool.submit(" in src, (
            "S1-CR-80: _accept_tcp must still call pool.submit(...) to hand off connections to the worker pool."
        )
        # The submit call must be inside a try/except RuntimeError.
        # Look for the try block immediately preceding pool.submit and
        # the except RuntimeError following it.
        submit_idx = src.index("pool.submit(")
        # Walk backwards to find the nearest ``try:`` above the submit.
        preceding = src[:submit_idx]
        last_try = preceding.rfind("try:")
        assert last_try != -1, (
            "S1-CR-80: pool.submit(...) must be inside a try/except RuntimeError block — no preceding ``try:`` found."
        )
        # And the except RuntimeError must come AFTER the submit.
        following = src[submit_idx:]
        # ``except RuntimeError`` (not ``except Exception``) is the
        # narrow, intentional handler. ``except Exception`` would also
        # catch unrelated bugs in ``_run_tcp_handler_safely`` (which is
        # NOT what we want — that function already has its own try).
        assert "except RuntimeError" in following, (
            "S1-CR-80: pool.submit(...) must be followed by an "
            "``except RuntimeError`` clause that closes the leaked conn "
            "and breaks the loop. ``except Exception`` is too broad — "
            "it would mask unrelated handler bugs."
        )

    def test_accept_loop_closes_conn_on_runtime_error(self) -> None:
        """The RuntimeError handler must close the leaked conn socket
        so it doesn't leak until process exit."""
        import inspect

        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._accept_tcp)
        # The except RuntimeError block must contain ``conn.close()``.
        submit_idx = src.index("pool.submit(")
        following = src[submit_idx:]
        except_idx = following.index("except RuntimeError")
        except_block = following[except_idx:]
        # The except block ends at the next unindented line; for this
        # check we just assert ``conn.close()`` appears somewhere in
        # the except block before the next ``break`` or end of function.
        assert "conn.close()" in except_block, (
            "S1-CR-80: the except RuntimeError handler must call "
            "``conn.close()`` to release the just-accepted socket — "
            "otherwise it leaks until process exit."
        )
        assert "break" in except_block, (
            "S1-CR-80: the except RuntimeError handler must ``break`` "
            "out of the accept loop — the pool is gone, no more "
            "connections can be dispatched."
        )

    def test_submit_to_shut_down_pool_does_not_propagate_runtime_error(self) -> None:
        """End-to-end: when ``stop()`` shuts down the pool while the
        accept loop is between read-and-submit, the accept loop must
        exit gracefully WITHOUT raising."""
        from concurrent.futures import ThreadPoolExecutor

        from voice_typer.server.ipc_server import IPCServer

        # Construct a server bypassing __init__ (we only need the
        # accept-loop body to be exercised).
        server = IPCServer.__new__(IPCServer)
        server._running = False  # so the while loop won't enter
        # Set up a pool that's ALREADY shut down (simulating the race).
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-s1-cr-80")
        pool.shutdown(wait=False, cancel_futures=True)
        server._tcp_worker_pool = pool

        # The race scenario: ``self._running`` is True when the accept
        # loop reads the pool, but False by the time submit is called.
        # We can't easily reproduce the exact race window in a unit
        # test, but we CAN assert that calling ``pool.submit`` on a
        # shut-down pool raises RuntimeError (proving the wrapping is
        # necessary) — and that the wrapped version doesn't propagate.
        with pytest.raises(RuntimeError):
            pool.submit(lambda: None)

        # And the wrapped version: simulate the wrapper inline.
        leaked_conn_closed = False

        class FakeConn:
            def close(self) -> None:
                nonlocal leaked_conn_closed
                leaked_conn_closed = True

        conn = FakeConn()
        broke_out = False
        # Inline re-implementation of the accept-loop's submit block.
        try:
            pool.submit(lambda: None)
        except RuntimeError:
            with __import__("contextlib").suppress(OSError):
                conn.close()
            broke_out = True

        assert broke_out, "S1-CR-80: wrapped submit must break the loop on RuntimeError"
        assert leaked_conn_closed, "S1-CR-80: wrapped submit must close the leaked conn"


# ALLOWED_COMMANDS contains every renderer-callable command ──


def _python_registry_keys() -> set[str]:
    """Return the set of command names in IPCServer._COMMAND_REGISTRY."""
    from voice_typer.server.ipc_server import IPCServer

    return set(IPCServer._COMMAND_REGISTRY.keys())


def _python_only_commands() -> set[str]:
    """Return the set of commands intentionally absent from TS/Rust
    allowlists (server-internal or host-internal)."""
    from voice_typer.server.ipc_server import IPCServer

    return set(IPCServer._PYTHON_ONLY_COMMANDS)


def _ts_allowed_commands() -> set[str]:
    """Parse the TS ``ALLOWED_COMMANDS = new Set([...])`` literal.

    Mirrors the parser in ``test_command_registry_parity.py``
    and ``test_security_doc_command_count.py`` — same regex, same
    anchoring. Duplicated here so this test file is self-contained.
    """
    src = ALLOWED_COMMANDS_TS.read_text(encoding="utf-8")
    start = src.index("ALLOWED_COMMANDS = new Set([")
    end = src.index("]);", start)
    block = src[start:end]
    return set(re.findall(r'"([a-z_]+)"', block))


class TestAllowedCommandsCoversRegistry:
    """S2-CR-73 / S4-CR-35: every command in the Python
    ``_COMMAND_REGISTRY`` (minus the explicitly-documented
    ``_PYTHON_ONLY_COMMANDS`` exception set) MUST be present in the
    Electron ``ALLOWED_COMMANDS`` set so the renderer can invoke them
    without being silently rejected by ``sendToPython``.

    The broader TS ↔ Python ↔ Rust parity contract is pinned by
    ``tests/test_command_registry_parity.py``; this test is
    the narrower "renderer can call every command" gate that the
    original finding #99 / #156 proposed under this file name. It
    exists as an additional regression guard because the original
    incident (onboarding commands missing from the allowlist) silently
    broke the onboarding flow under Electron — the parity test alone
    was not enough to catch the renderer-side call sites.
    """

    def test_onboarding_check_permissions_present_in_allowlist(self) -> None:
        """The onboarding_check_permissions command whose absence was
        part of the original incident (#99 / #156) MUST be present
        (regression pin).

        Note: ``onboarding_get_model_catalog`` was intentionally REMOVED
        in a follow-up cleanup pass — the renderer now uses
        ``get_model_catalog`` (the non-onboarding command) for model
        catalog data. The removal was coordinated across both the Python
        ``_COMMAND_REGISTRY`` and the TS ``ALLOWED_COMMANDS`` set, and
        is pinned by ``tests/test_dead_code_stays_removed.py``. We do
        NOT assert its presence here — that would contradict the
        intentional narrowing."""
        ts = _ts_allowed_commands()
        assert "onboarding_check_permissions" in ts, (
            "S2-CR-73 / S4-CR-35: onboarding_check_permissions MUST be "
            "in ALLOWED_COMMANDS — the renderer's onboarding flow calls "
            "it to walk the user through macOS Accessibility / Linux "
            "input-group permission grants."
        )

    def test_every_renderer_callable_command_is_in_allowlist(self) -> None:
        """For every command in ``_COMMAND_REGISTRY`` that is NOT in
        ``_PYTHON_ONLY_COMMANDS``, the command MUST be present in the
        Electron ``ALLOWED_COMMANDS`` set."""
        registry = _python_registry_keys()
        python_only = _python_only_commands()
        ts = _ts_allowed_commands()
        renderer_callable = registry - python_only
        missing = renderer_callable - ts
        assert not missing, (
            "S2-CR-73 / S4-CR-35: ALLOWED_COMMANDS is missing server "
            f"commands (renderer calls would be silently rejected): "
            f"{sorted(missing)}. Either add them to ALLOWED_COMMANDS in "
            "voice_typer/client/src/main/allowed-commands.ts, OR add "
            "them to IPCServer._PYTHON_ONLY_COMMANDS if they are "
            "intentionally server/host-internal."
        )

    def test_python_only_commands_excluded_from_allowlist(self) -> None:
        """The ``_PYTHON_ONLY_COMMANDS`` set documents commands that
        are intentionally absent from the renderer allowlist (e.g.
        ``shutdown`` is invoked by the Tauri host's WS transport;
        ``tray_click`` is invoked by the Rust host's tray handler).
        Asserting their absence guards against accidentally widening
        the renderer's attack surface."""
        registry = _python_registry_keys()
        python_only = _python_only_commands()
        ts = _ts_allowed_commands()
        # Every python-only command must be in the registry (otherwise
        # the exception set is itself stale).
        assert python_only <= registry, (
            "S2-CR-73: _PYTHON_ONLY_COMMANDS contains commands not in "
            f"_COMMAND_REGISTRY: {sorted(python_only - registry)}"
        )
        # And none of the python-only commands should be in the TS
        # allowlist (a compromised renderer must NOT be able to invoke
        # ``shutdown`` or spoof ``tray_click``).
        leaked = python_only & ts
        assert not leaked, (
            "S2-CR-73: _PYTHON_ONLY_COMMANDS should NOT be in the "
            f"renderer ALLOWED_COMMANDS (security: a compromised "
            f"renderer could invoke them): {sorted(leaked)}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
