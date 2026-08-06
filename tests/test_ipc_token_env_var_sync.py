"""WN-12: regression test for the canonical IPC-token env-var name.

``voice_typer.server._paths.IPC_TOKEN_ENV_VAR`` is the single source of
truth for the env-var name that carries the per-launch session token
from the host (Electron / Tauri) to the Python sidecar. Prior to WN-12
the bare literal ``"VOICE_TYPER_IPC_TOKEN"`` was duplicated across 7+
files (``electron_launcher.py``, ``env_validation.py``,
``ipc/entrypoint.py``, ``ipc/transport_tcp.py``, ``sidecar_ws.py``,
plus docstrings / comments / test docs). A typo in any of those would
silently break IPC auth — the host sets X, the sidecar reads Y, every
TCP connection is refused at SEC-018 with no diagnostic beyond a
``[TCP] refusing connection from <addr>`` ERROR.

This test asserts:
1. The constant ``IPC_TOKEN_ENV_VAR`` is exactly ``"VOICE_TYPER_IPC_TOKEN"``.
2. NO production Python file under ``voice_typer/server/`` contains
   the bare literal ``"VOICE_TYPER_IPC_TOKEN"`` (or single-quoted
   variant) outside of ``_paths.py`` itself.

The doc-comment / test-docstring case is tolerated by the test
itself — the constant is the source of truth, the docstring may
still mention the name for human readers.

C-DATA-1 compliance: no network access. Pure import + ast-grep check.
C-STYLE-1 compliance: no task ID in the test name.
"""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server._paths import IPC_TOKEN_ENV_VAR

# ── Test 1: the constant has the expected value ──────────────────────


def test_ipc_token_env_var_constant_is_canonical() -> None:
    """The canonical constant must match the historical literal exactly.

    Renaming the env var is a breaking change (Electron main + Tauri
    Rust host + every test in the suite would need a coordinated
    update). Keep the value stable; coordinate any future rename via
    a deprecation shim across the host and sidecar.
    """
    assert IPC_TOKEN_ENV_VAR == "VOICE_TYPER_IPC_TOKEN", (
        f"IPC_TOKEN_ENV_VAR changed to {IPC_TOKEN_ENV_VAR!r} — this is a "
        f"breaking change requiring coordinated Electron + Tauri host + "
        f"sidecar updates. Revert or add the migration shim."
    )


# ── Test 2: no bare-literal references in production code ────────────

# Files that may contain the bare literal:
# - _paths.py itself (the constant definition + its docstring)
# - The test file itself (the literal in the docstring)
# - Files under tests/ that explicitly test the env-var name (this one)
# - Stub files (voice_typer/stubs/) — vendored type stubs, not production
_EXEMPT_PATH_FRAGMENTS = (
    "voice_typer/server/_paths.py",
    "voice_typer/stubs/",
    "tests/test_ipc_token_env_var_sync.py",  # this file
)


def _is_exempt(path: Path) -> bool:
    """Return True if the path is in the exempt list (constant def or stub)."""
    # Normalize to forward slashes so the exempt fragments
    # (``voice_typer/server/_paths.py``) match on Windows, where
    # ``str(path)`` uses backslashes (``voice_typer\\server\\_paths.py``).
    as_str = str(path).replace("\\", "/")
    return any(frag in as_str for frag in _EXEMPT_PATH_FRAGMENTS)


def test_no_bare_ipc_token_env_var_literal_in_production() -> None:
    """Scan ``voice_typer/server/`` for any bare literal references.

    Anything that reads or writes the env var MUST go through
    :data:`voice_typer.server._paths.IPC_TOKEN_ENV_VAR` (or one of the
    re-exports from ``ipc_server.py``) so a rename in one place does
    not silently desync auth.

    The check covers both single-quoted and double-quoted forms (both
    appear in the codebase historically). Exemptions: the constant
    definition file itself (``_paths.py``) and the vendored stub
    directory.
    """
    server_root = Path(__file__).resolve().parent.parent / "voice_typer" / "server"
    if not server_root.exists():
        # Sanity: in a partial checkout, skip the scan rather than fail
        # (this test exists to enforce the convention; a partial checkout
        # simply doesn't have the tree to scan).
        return
    pattern = re.compile(r"""['"]VOICE_TYPER_IPC_TOKEN['"]""")
    offenders: list[str] = []
    for py_file in server_root.rglob("*.py"):
        if _is_exempt(py_file):
            continue
        # Skip __pycache__ symlinks.
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{py_file}:{line_no}: {match.group()!r}")
    assert not offenders, (
        "Found bare 'VOICE_TYPER_IPC_TOKEN' literal in production code "
        "(must go through voice_typer.server._paths.IPC_TOKEN_ENV_VAR):\n" + "\n".join(offenders)
    )
