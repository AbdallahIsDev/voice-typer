"""CR-1 regression guard — verify the ``ipc_server`` shim delegates to ``ipc``.

Finding CR-1 (Critical): ``voice_typer/server/ipc_server.py`` was a
2,609-line monolith duplicating the ``IPCServer`` class definition in
``voice_typer/server/ipc/server.py`` (1,764 LOC). The fix (Fix-A)
reduces ``ipc_server.py`` to a thin shim that re-exports the canonical
class from the ``ipc`` package, so bug fixes only live in one place.

This test asserts the two import paths return the SAME class object
(``is``-identity, not just ``==``). If a future regression re-introduces
a duplicate definition, the test fails.

This is a Fix-T test (coordinates with Fix-A). It is expected to FAIL
until Fix-A lands the shim reduction.
"""

from __future__ import annotations

import pytest

# CR-019 (IMPROVE-mode run, 2026-07-21): the 4 dead duplicate modules
# (``ipc/server.py``, ``ipc/main.py``, ``ipc/process_meta.py``,
# ``ipc/push_events.py``) have been DELETED. The canonical
# ``ipc_server.py`` remains the single source of truth (~2363 lines
# after the helper consolidation). The CR-1/Fix-A direction
# (``ipc_server.py`` as shim ≤300 lines re-exporting from
# ``ipc/server.py``) was the OPPOSITE direction and is explicitly
# abandoned. These 4 tests are therefore skipped (XS-50): they document
# the abandoned direction but are not expected to ever run.
_SKIP_REASON = (
    "abandoned — CR-1/Fix-A shim extraction deferred. "
    "See comprehensive-review.md XS-50."
)


@pytest.mark.skip(reason=_SKIP_REASON)
def test_ipc_server_shim_reexports_same_class() -> None:
    """``from voice_typer.server.ipc_server import IPCServer`` must return
    the exact same class object as ``from voice_typer.server.ipc.server
    import IPCServer``.

    After Fix-A, ``ipc_server.py`` should be reduced to a re-export
    shim: ``from voice_typer.server.ipc.server import IPCServer, ...``.
    Identity (``is``) is the strictest check — it catches accidental
    re-definition of the class in the shim.
    """
    # Local imports so the autouse mock fixture has a chance to install
    # the heavy-import mocks before any IPC server module loads.
    from voice_typer.server import ipc_server as shim
    from voice_typer.server.ipc import server as ipc_pkg

    assert hasattr(ipc_pkg, "IPCServer"), "voice_typer.server.ipc.server must define IPCServer"
    assert hasattr(shim, "IPCServer"), "voice_typer.server.ipc_server must re-export IPCServer"
    assert shim.IPCServer is ipc_pkg.IPCServer, (
        "ipc_server.IPCServer must BE ipc.server.IPCServer (identity). "
        "If they differ, ipc_server.py has re-defined the class — "
        "see CR-1 / Fix-A."
    )


@pytest.mark.skip(reason=_SKIP_REASON)
def test_ipc_server_shim_reexports_main() -> None:
    """``main`` entry point should also be the same object."""
    import importlib

    ipc_main_mod = importlib.import_module("voice_typer.server.ipc.main")
    from voice_typer.server import ipc_server as shim

    assert hasattr(ipc_main_mod, "main")
    assert hasattr(shim, "main")
    assert shim.main is ipc_main_mod.main


@pytest.mark.skip(reason=_SKIP_REASON)
def test_ipc_server_module_docstring_mentions_shim() -> None:
    """The shim module's docstring should advertise that it is a shim.

    This protects against silent re-introduction: a maintainer reading
    ``ipc_server.py`` must immediately see "this is a shim, edit
    ``ipc/`` instead".
    """
    from voice_typer.server import ipc_server as shim

    doc = (shim.__doc__ or "").lower()
    # Look for any of several tell-tale words indicating the file is
    # intentionally a shim, not a primary definition site.
    assert any(word in doc for word in ("shim", "re-export", "reexport", "deprecated", "thin")), (
        "ipc_server.py docstring should indicate it is a thin shim that "
        "re-exports from voice_typer.server.ipc (CR-1 / Fix-A)."
    )


@pytest.mark.skip(reason=_SKIP_REASON)
def test_ipc_server_module_is_not_a_monolith() -> None:
    """After Fix-A, ``ipc_server.py`` should be a thin shim — not 2,609
    lines. This test guards against the file silently regrowing to a
    second definition site.

    The threshold is generous (300 lines) — a pure re-export shim
    should be well under 100 lines.
    """
    import os

    from voice_typer.server import ipc_server as shim

    src_file = shim.__file__
    assert src_file is not None, "ipc_server module must be backed by a file"
    size = os.path.getsize(src_file)
    with open(src_file, encoding="utf-8") as f:
        line_count = sum(1 for _ in f)
    assert line_count <= 300, (
        f"ipc_server.py is {line_count} lines / {size} bytes — expected a "
        f"thin re-export shim (<=300 lines). See CR-1 / Fix-A."
    )
