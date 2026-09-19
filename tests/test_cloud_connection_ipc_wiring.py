"""
Focused regression test for the ``test_cloud_connection`` IPC command.
C-DATA-1 (offline guarantee) compliance
"""

from __future__ import annotations

import inspect

from voice_typer.server.handlers.cloud_test_handlers import (
    CloudTestHandlersMixin,
)
from voice_typer.server.ipc.registry import _COMMAND_REGISTRY
from voice_typer.server.ipc_server import IPCServer


def test_command_registered_in_registry() -> None:
    """``test_cloud_connection`` MUST be in ``_COMMAND_REGISTRY``."""
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
    """``CloudTestHandlersMixin`` MUST be in ``IPCServer.__bases__``."""
    assert CloudTestHandlersMixin in IPCServer.__bases__, (
        "CloudTestHandlersMixin must be a direct base of IPCServer so "
        "the _handle_test_cloud_connection method is reachable via "
        "getattr(self, handler_name) at dispatch time."
    )


def test_handler_method_resolves_on_ipc_server() -> None:
    """The handler method MUST be callable on ``IPCServer`` instances."""
    method = getattr(IPCServer, "_handle_test_cloud_connection", None)
    assert callable(method), (
        "IPCServer._handle_test_cloud_connection must be a callable "
        "method. The registry maps 'test_cloud_connection' to this "
        "method name; if the method is missing on IPCServer, "
        "dispatch will raise AttributeError at runtime."
    )
    # The method should be defined on CloudTestHandlersMixin (not
    defining_class = dict(inspect.getmembers(method, lambda m: True)).get("__qualname__", "")
    assert "CloudTestHandlersMixin" in defining_class, (
        f"_handle_test_cloud_connection should be defined on CloudTestHandlersMixin; found qualname={defining_class!r}."
    )


def test_handler_method_signature() -> None:
    """The handler method MUST accept (data, resp) per the dispatch contract."""
    sig = inspect.signature(IPCServer._handle_test_cloud_connection)
    params = list(sig.parameters.values())
    # First param is ``self`` (bound method), then ``data``, then ``resp``.
    assert len(params) >= 3, (
        f"_handle_test_cloud_connection must accept (self, data, resp), "
        f"found {len(params)} params: {[p.name for p in params]}."
    )
    # Skip self (params[0]); verify the dispatch-contract param names.
    assert params[1].name == "data", f"second param must be 'data' (the command payload), found {params[1].name!r}."
    assert params[2].name == "resp", f"third param must be 'resp' (the ResponseEnvelope), found {params[2].name!r}."
