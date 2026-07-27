"""MIG-1.5 Phase 0-W Gate Check (sub1) — paste focus-restore (Windows).

FZ-19 / PVT-051 (deletion): this file ORIGINALLY validated the
focus-restore dance + UIPI fallback + Wayland fallback added to
``src-tauri/src/commands/sidecar_cmds.rs::paste_text`` to close the
ADR-0020 §6.3 implementation gap. The ``paste_text`` Tauri command
was deleted as dead production code in FZ-19: the Python sidecar
owns the paste path end-to-end via
``voice_typer/server/dictation_pipeline.py::_dispatch_paste``, and
no Python or TS code ever invoked ``invoke('paste_text', ...)``.
The Tauri command registration, the wrapper function, the
``commands::paste`` module declaration, and the entire
``src-tauri/src/commands/paste.rs`` file were all removed.

What remains is a regression-guard test that pins the absence of the
``paste_text`` symbol from the Rust host source so a future
contributor cannot accidentally re-wire the dead path. The filename
keeps the ``windows`` token so the file still skips on non-Windows
hosts (preserving the original gate's per-platform scope).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ─── Path constants ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/tauri/mig15/<this> → repo root
SIDECAR_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
MAIN_RS = REPO_ROOT / "src-tauri" / "src" / "main.rs"
COMMANDS_MOD_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "mod.rs"
PASTE_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "paste.rs"


# ─── Source-reading fixtures ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def sidecar_cmds_src() -> str:
    """Read the ``sidecar_cmds.rs`` source file once per module."""
    assert SIDECAR_CMDS_RS.is_file(), f"missing Rust source: {SIDECAR_CMDS_RS}"
    return SIDECAR_CMDS_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_rs_src() -> str:
    """Read the ``main.rs`` source file once per module."""
    assert MAIN_RS.is_file(), f"missing Rust source: {MAIN_RS}"
    return MAIN_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def commands_mod_src() -> str:
    """Read the commands ``mod.rs`` source file once per module."""
    assert COMMANDS_MOD_RS.is_file(), f"missing Rust source: {COMMANDS_MOD_RS}"
    return COMMANDS_MOD_RS.read_text(encoding="utf-8")


# ─── FZ-19 / PVT-051: paste_text deletion regression guard ───────────────


def test_paste_text_symbol_absent_from_sidecar_cmds(sidecar_cmds_src: str) -> None:
    """FZ-19: ``sidecar_cmds.rs`` must NOT define the ``paste_text`` command.

    The deprecated ``paste_text`` Tauri command wrapper that used to
    live here (delegating to ``commands::paste::execute_paste``) was
    deleted along with the ``paste.rs`` module. The Python sidecar
    owns the paste path; no Tauri command is needed.
    """
    assert "paste_text" not in sidecar_cmds_src, (
        "sidecar_cmds.rs must NOT define `paste_text` — the dead Tauri "
        "command was deleted in FZ-19 / PVT-051 (Python sidecar owns the "
        "paste path via dictation_pipeline.py::_dispatch_paste)."
    )
    assert "PasteTextArgs" not in sidecar_cmds_src, (
        "sidecar_cmds.rs must NOT define `PasteTextArgs` — the struct was "
        "deleted along with `paste_text` in FZ-19 / PVT-051."
    )


def test_paste_text_symbol_absent_from_main_rs(main_rs_src: str) -> None:
    """FZ-19: ``main.rs`` must NOT register or import ``paste_text``."""
    assert "paste_text" not in main_rs_src, (
        "main.rs must NOT reference `paste_text` — the dead Tauri command was deleted in FZ-19 / PVT-051."
    )


def test_paste_mod_declaration_absent(commands_mod_src: str) -> None:
    """FZ-19: ``commands/mod.rs`` must NOT declare the ``paste`` module."""
    assert "mod paste" not in commands_mod_src, (
        "commands/mod.rs must NOT declare `mod paste` — the `paste.rs` module was deleted in FZ-19 / PVT-051."
    )


def test_paste_rs_file_deleted() -> None:
    """FZ-19: ``src-tauri/src/commands/paste.rs`` must NOT exist."""
    assert not PASTE_RS.exists(), (
        f"src-tauri/src/commands/paste.rs must NOT exist — the dead paste "
        f"module was deleted in FZ-19 / PVT-051 (Python sidecar owns the "
        f"paste path). Found: {PASTE_RS}"
    )
