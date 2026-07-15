"""Tests for the native binary path resolver under the Tauri path (ADR-0020 §7).

Verifies that VOICE_TYPER_NATIVE_DIR env var adds a new lookup path
without breaking the existing 5 lookup paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

from voice_typer.server import native_hotkeys


def test_voice_typer_native_dir_lookup_finds_binary(tmp_path, monkeypatch):
    """When VOICE_TYPER_NATIVE_DIR is set, the binary is looked up there."""
    # Create a fake native binary in the temp dir.
    binary_name = native_hotkeys._BINARY_NAMES.get(sys.platform, "linux-key-listener")
    fake_native_dir = tmp_path / "resources" / "native"
    fake_native_dir.mkdir(parents=True)
    fake_binary = fake_native_dir / binary_name
    fake_binary.write_text("dummy")
    if sys.platform != "win32":
        fake_binary.chmod(0o755)

    # Clear all other env vars so only VOICE_TYPER_NATIVE_DIR is set.
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(fake_native_dir))

    # Patch the dev-mode source-tree path + sys.executable path +
    # _MEIPASS so they don't accidentally match.
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self: self,  # identity — don't actually resolve
    )

    result = native_hotkeys.get_native_binary_path()
    assert result is not None
    assert result.name == binary_name
    assert result.parent == fake_native_dir


def test_voice_typer_native_binary_env_takes_precedence(tmp_path, monkeypatch):
    """VOICE_TYPER_NATIVE_BINARY (single-file override) beats VOICE_TYPER_NATIVE_DIR."""
    # Single-file override.
    single_binary = tmp_path / "custom-binary"
    single_binary.write_text("dummy")

    # Directory with the standard name.
    binary_name = native_hotkeys._BINARY_NAMES.get(sys.platform, "linux-key-listener")
    native_dir = tmp_path / "native"
    native_dir.mkdir()
    (native_dir / binary_name).write_text("dummy")

    monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(single_binary))
    monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(native_dir))

    result = native_hotkeys.get_native_binary_path()
    assert result is not None
    assert result == single_binary


def test_voice_typer_native_dir_falls_through_when_binary_missing(tmp_path, monkeypatch):
    """A broken VOICE_TYPER_NATIVE_DIR (no matching binary) falls through cleanly."""
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(tmp_path))  # empty dir

    # The function should not crash; it should fall through to the
    # dev-mode path and eventually return None (or the dev binary if
    # the source tree has one).
    result = native_hotkeys.get_native_binary_path()
    # Either None or a real path — the contract is "don't crash on a
    # broken env var".
    assert result is None or isinstance(result, Path)


def test_no_env_vars_falls_through_to_dev_mode(monkeypatch):
    """Without any env vars, the function still works (dev-mode path)."""
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)

    # In the test environment, the dev-mode binary may or may not
    # exist (depends on whether compile_native.sh ran). Either result
    # is acceptable — the contract is "no crash".
    result = native_hotkeys.get_native_binary_path()
    assert result is None or isinstance(result, Path)
