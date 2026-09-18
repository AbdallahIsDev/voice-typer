"""Bubble-dismiss global shortcut pin (MO-125).

Post-predecessor cutover the predecessor accelerator source is gone; the
Tauri host registers the binding Rust-side via
``tauri-plugin-global-shortcut``. These tests pin the Rust constant
to the shared TS display constant so the two stay in lockstep.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_SHORTCUT_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "shared" / "dismiss-shortcut.ts"
RUST_SHORTCUTS_RS = REPO_ROOT / "src-tauri" / "src" / "shortcuts.rs"
MAIN_RS = REPO_ROOT / "src-tauri" / "src" / "main.rs"
CARGO_TOML = REPO_ROOT / "src-tauri" / "Cargo.toml"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing source file: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_shared_constant_is_the_ctrl_shift_d_binding() -> None:
    source = _read(SHARED_SHORTCUT_TS)
    assert '"CommandOrControl+Shift+D"' in source
    assert '"Ctrl+Shift+D"' in source, (
        "the renderer display form must stay the raw key names (rendered through the shared HotkeyChips path)"
    )


def test_rust_registers_the_same_binding() -> None:
    source = _read(RUST_SHORTCUTS_RS)
    match = re.search(r'BUBBLE_DISMISS_ACCELERATOR:\s*&str\s*=\s*"([^"]+)"', source)
    assert match, "the Rust accelerator constant is missing"
    # `CmdOrCtrl+Shift+D` and `CommandOrControl+Shift+D` are the same
    # binding in different spellings; the Rust global-hotkey parser
    # accepts the former.
    assert match.group(1) == "CmdOrCtrl+Shift+D", (
        "the Rust binding diverged from the shared TS constant (CommandOrControl+Shift+D)"
    )
    # The registration must fire on the press edge only.
    assert "ShortcutState::Pressed" in source


def test_rust_dismiss_reuses_the_bubble_hide_path() -> None:
    source = _read(RUST_SHORTCUTS_RS)
    assert "hide_bubble_window" in source, (
        "the shortcut dismiss must reuse the shared hide path (the same "
        "body the bubble '×' button uses), not grow a second hide "
        "implementation (E7)"
    )
    assert '"toggle_dictation"' in source, "an in-flight recording must be cancelled before hiding"


def test_plugin_is_registered_in_the_builder_and_lockstepped() -> None:
    main_rs = _read(MAIN_RS)
    assert "tauri_plugin_global_shortcut::Builder::new().build()" in main_rs, (
        "the global-shortcut plugin must be registered on the Tauri builder"
    )
    cargo = _read(CARGO_TOML)
    assert 'tauri-plugin-global-shortcut = "2"' in cargo
