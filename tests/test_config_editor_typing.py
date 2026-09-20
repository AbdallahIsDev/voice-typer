"""No type-checker suppressions on owned lines; missing annotations added."""

from __future__ import annotations

import inspect
import pathlib


def test_windows_opener_default_has_return_annotation():
    from voice_typer.server import config_editor

    sig = inspect.signature(config_editor._default_windows_open_with_default_app)
    assert sig.return_annotation is not inspect.Signature.empty, (
        "missing return annotation must be added, not suppressed"
    )


def test_no_type_ignore_in_owned_files():
    for rel in (
        "voice_typer/server/config_editor.py",
        "voice_typer/server/ipc/rate_limiter.py",
        "voice_typer/server/sidecar_ws_internals/graceful_shutdown.py",
    ):
        lines = pathlib.Path(rel).read_text(encoding="utf-8").splitlines()
        leftovers = [line for line in lines if "type: ignore" in line]
        # Only documented mypy false positive allowed: os.startfile exists
        # solely on Windows and mypy has no per-platform conditional ignore.
        for line in leftovers:
            assert "os.startfile" in line, f"suppression still present in {rel}: {line.strip()}"
    for rel in (
        "voice_typer/server/ipc/rate_limiter.py",
        "voice_typer/server/sidecar_ws_internals/graceful_shutdown.py",
    ):
        source = pathlib.Path(rel).read_text(encoding="utf-8")
        assert "type: ignore" not in source, f"suppression still present in {rel}"


def test_rate_limiter_lazy_attach_without_suppression():
    from types import SimpleNamespace

    from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

    server = SimpleNamespace()
    first = _get_rate_limiter(server)
    assert _get_rate_limiter(server) is first, "second call must reuse the attached limiter"


def test_graceful_shutdown_installs_hooks_without_suppression():
    from types import SimpleNamespace

    from voice_typer.server.sidecar_ws_internals import graceful_shutdown

    server = SimpleNamespace()
    graceful_shutdown._attach_ws_graceful_shutdown(server)
    assert callable(server.ws_graceful_shutdown)
    assert server._ws_authenticated_conns == set()
    graceful_shutdown._attach_ws_graceful_shutdown(server)
    assert callable(server.ws_graceful_shutdown), "re-install must stay a no-op"
