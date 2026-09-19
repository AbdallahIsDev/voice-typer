"""
WN-12: regression test for the canonical IPC-token env-var name.
C-DATA-1 compliance: no network access. Pure import + ast-grep check.
"""

from __future__ import annotations

import re
from pathlib import Path

from voice_typer.server._paths import IPC_TOKEN_ENV_VAR


def test_ipc_token_env_var_constant_is_canonical() -> None:
    """The canonical constant must match the historical literal exactly."""
    assert IPC_TOKEN_ENV_VAR == "VOICE_TYPER_IPC_TOKEN", (
        f"IPC_TOKEN_ENV_VAR changed to {IPC_TOKEN_ENV_VAR!r}, this is a "
        f"breaking change requiring coordinated predecessor + Tauri host + "
        f"sidecar updates. Revert or add the migration shim."
    )


# Files that may contain the bare literal:
_EXEMPT_PATH_FRAGMENTS = (
    "voice_typer/server/_paths.py",
    "voice_typer/stubs/",
    "tests/test_ipc_token_env_var_sync.py",  # this file
)


def _is_exempt(path: Path) -> bool:
    """Return True if the path is in the exempt list (constant def or stub)."""
    # Normalize to forward slashes so the exempt fragments
    as_str = str(path).replace("\\", "/")
    return any(frag in as_str for frag in _EXEMPT_PATH_FRAGMENTS)


def test_no_bare_ipc_token_env_var_literal_in_production() -> None:
    """Scan ``voice_typer/server/`` for any bare literal references."""
    server_root = Path(__file__).resolve().parent.parent / "voice_typer" / "server"
    if not server_root.exists():
        # Sanity: in a partial checkout, skip the scan rather than fail
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
