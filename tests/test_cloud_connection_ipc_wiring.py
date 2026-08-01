"""Focused regression test for the ``test_cloud_connection`` IPC command.

Background
----------
S3-CR-3 (CI red) surfaced that ``test_cloud_connection`` was listed in
the renderer ``ALLOWED_COMMANDS`` (62 entries) and the Rust host's
``allowed_commands()`` literal, but was MISSING from the Python
``_COMMAND_REGISTRY``. The Python handler
(``CloudTestHandlersMixin._handle_test_cloud_connection``) existed in
``voice_typer/server/handlers/cloud_test_handlers.py`` but the mixin
was neither imported by ``ipc_server.py`` nor mixed into the
``IPCServer`` class — so a renderer ``dispatch({cmd:
'test_cloud_connection', ...})`` call would have been rejected by the
Python dispatcher with ``unknown_command``.

This module pins the wiring so a future regression (someone removes
the mixin or the registry entry without coordinating across the three
sources of truth) is caught immediately, rather than only by the
slower cross-file parity tests in ``test_security_doc_command_count``
and ``test_electron_ipc_and_build``.

C-DATA-1 (offline guarantee) compliance
---------------------------------------
The handler is intentionally routed through the Python side of the IPC
boundary so the renderer's production code path stays network-free.
The only network call is on the Python side, gated by an explicit user
click on the Cloud tab's "Test Connection" button. The API key never
leaves the Python process (it is read from the live ``Config``
dataclass, not passed over IPC). See
``voice_typer/server/handlers/cloud_test_handlers.py`` for the full
rationale.
"""

from __future__ import annotations

import inspect

from voice_typer.server.handlers.cloud_test_handlers import (
    CloudTestHandlersMixin,
)
from voice_typer.server.ipc.registry import _COMMAND_REGISTRY
from voice_typer.server.ipc_server import IPCServer


def test_command_registered_in_registry() -> None:
    """``test_cloud_connection`` MUST be in ``_COMMAND_REGISTRY``.

    Without this entry, ``IPCServer._dispatch`` would reject renderer
    ``dispatch({cmd: 'test_cloud_connection'})`` calls with
    ``unknown_command`` even though the renderer + Rust allowlists
    permit it.
    """
    assert "test_cloud_connection" in _COMMAND_REGISTRY, (
        "test_cloud_connection must be registered in _COMMAND_REGISTRY "
        "so the Python dispatcher recognises it. The renderer + Rust "
        "allowlists already include it; the Python registry is the "
        "third source of truth and must agree."
    )
    assert _COMMAND_REGISTRY["test_cloud_connection"] == "_handle_test_cloud_connection", (
        "test_cloud_connection must map to the _handle_test_cloud_connection method on CloudTestHandlersMixin."
    )


def test_mixin_mixed_into_ipc_server() -> None:
    """``CloudTestHandlersMixin`` MUST be in ``IPCServer.__bases__``.

    The handler method lives on the mixin; if the mixin is not a base
    of ``IPCServer``, ``getattr(self, '_handle_test_cloud_connection')``
    would raise ``AttributeError`` at dispatch time.
    """
    assert CloudTestHandlersMixin in IPCServer.__bases__, (
        "CloudTestHandlersMixin must be a direct base of IPCServer so "
        "the _handle_test_cloud_connection method is reachable via "
        "getattr(self, handler_name) at dispatch time."
    )


def test_handler_method_resolves_on_ipc_server() -> None:
    """The handler method MUST be callable on ``IPCServer`` instances.

    This is the runtime invariant the dispatcher relies on: it does
    ``getattr(self, handler_name)`` and calls the result. If the
    method is missing or non-callable, dispatch fails at runtime even
    if the registry + mixin wiring are correct in isolation.
    """
    method = getattr(IPCServer, "_handle_test_cloud_connection", None)
    assert callable(method), (
        "IPCServer._handle_test_cloud_connection must be a callable "
        "method. The registry maps 'test_cloud_connection' to this "
        "method name; if the method is missing on IPCServer, "
        "dispatch will raise AttributeError at runtime."
    )
    # The method should be defined on CloudTestHandlersMixin (not
    # inherited from a different mixin by accident).
    defining_class = dict(inspect.getmembers(method, lambda m: True)).get("__qualname__", "")
    assert "CloudTestHandlersMixin" in defining_class, (
        f"_handle_test_cloud_connection should be defined on CloudTestHandlersMixin; found qualname={defining_class!r}."
    )


def test_handler_method_signature() -> None:
    """The handler method MUST accept (data, resp) per the dispatch contract.

    ``IPCServer._dispatch`` calls ``handler(data, resp)``. The handler
    must accept exactly two positional args (data + response envelope)
    and may return ``None`` (handler sends response itself) or a
    ``ResponseEnvelope`` (dispatcher sends it).
    """
    sig = inspect.signature(IPCServer._handle_test_cloud_connection)
    params = list(sig.parameters.values())
    # First param is ``self`` (bound method), then ``data``, then ``resp``.
    assert len(params) >= 3, (
        f"_handle_test_cloud_connection must accept (self, data, resp) — "
        f"found {len(params)} params: {[p.name for p in params]}."
    )
    # Skip self (params[0]); verify the dispatch-contract param names.
    assert params[1].name == "data", f"second param must be 'data' (the command payload), found {params[1].name!r}."
    assert params[2].name == "resp", f"third param must be 'resp' (the ResponseEnvelope), found {params[2].name!r}."
