"""Tests for the backend reserved-hotkey mirror."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from voice_typer.server.config_validators import (
    _RESERVED_HOTKEYS,
    _platform_key,
    _validate_hotkey,
    validate_config_update,
)

_HOTKEY_RESERVED_JSON_CLIENT = Path(__file__).resolve().parents[1] / (
    "voice_typer/client/src/renderer/src/data/hotkey_reserved.json"
)


def _parse_frontend_reserved_shortcuts() -> dict:
    """Load the per-platform reserved-shortcut table from the client JSON."""
    if not _HOTKEY_RESERVED_JSON_CLIENT.exists():
        pytest.skip(f"hotkey_reserved.json not found at {_HOTKEY_RESERVED_JSON_CLIENT}")
    with _HOTKEY_RESERVED_JSON_CLIENT.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("per_platform_reserved", {})


def test_reserved_hotkeys_match_frontend() -> None:
    """The backend _RESERVED_HOTKEYS must match the frontend RESERVED_SHORTCUTS."""
    frontend = _parse_frontend_reserved_shortcuts()
    backend = _RESERVED_HOTKEYS

    for platform_key in ("win32", "darwin", "linux"):
        fe_set = {s.lower() for s in frontend.get(platform_key, [])}
        be_set = {s.lower() for s in backend.get(platform_key, [])}
        assert fe_set == be_set, (
            f"Reserved hotkeys mismatch on {platform_key}.\n"
            f"  Frontend only: {sorted(fe_set - be_set)}\n"
            f"  Backend only:  {sorted(be_set - fe_set)}\n"
            f"Update both _RESERVED_HOTKEYS in config_validators.py AND "
            f"RESERVED_SHORTCUTS in hotkey-validation.ts."
        )


def test_is_reserved_hotkey_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    """Win32-reserved shortcuts are detected on the win32 platform."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert _validate_hotkey("<win>+<e>") is not None
    assert _validate_hotkey("<win>+<v>") is not None
    assert _validate_hotkey("<win>+<space>") is not None
    assert _validate_hotkey("<win>+<l>") is not None


def test_is_reserved_hotkey_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS-reserved shortcuts are detected on the darwin platform."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _validate_hotkey("<cmd>+<space>") is not None
    assert _validate_hotkey("<cmd>+<q>") is not None
    assert _validate_hotkey("<cmd>+<tab>") is not None


def test_is_reserved_hotkey_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux-reserved shortcuts are detected on the linux platform."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert _validate_hotkey("<super>+<l>") is not None
    assert _validate_hotkey("<super>+<d>") is not None


def test_is_reserved_hotkey_cross_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shortcut reserved on one platform is NOT reserved on another."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert _validate_hotkey("<cmd>+<tab>") is None

    monkeypatch.setattr(sys, "platform", "linux")
    assert _validate_hotkey("<cmd>+<tab>") is None

    monkeypatch.setattr(sys, "platform", "darwin")
    assert _validate_hotkey("<win>+<e>") is None


def test_is_reserved_hotkey_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comparison is case-insensitive, <WIN>+<E> matches <win>+<e>."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert _validate_hotkey("<WIN>+<E>") is not None

    monkeypatch.setattr(sys, "platform", "darwin")
    assert _validate_hotkey("<Cmd>+<Space>") is not None


def test_is_reserved_hotkey_empty() -> None:
    """Empty/None values are rejected as invalid."""
    assert _validate_hotkey("") is not None
    assert _validate_hotkey(None) is not None  # type: ignore[arg-type]


def test_is_reserved_hotkey_non_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common dictation shortcuts are NOT reserved."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert _validate_hotkey("<f2>") is None
    assert _validate_hotkey("<caps_lock>") is None
    assert _validate_hotkey("<ctrl>+<alt>+v") is None

    monkeypatch.setattr(sys, "platform", "darwin")
    assert _validate_hotkey("<f2>") is None
    assert _validate_hotkey("<ctrl>+<alt>+v") is None

    monkeypatch.setattr(sys, "platform", "linux")
    assert _validate_hotkey("<ctrl>+<alt>+v") is None


def test_validate_config_update_rejects_reserved_hotkey(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting a reserved hotkey via IPC is rejected."""
    # Use the darwin platform to test <cmd>+<space> rejection.
    monkeypatch.setattr(sys, "platform", "darwin")
    validated, errors = validate_config_update({"hotkey": "<cmd>+<space>"})
    assert len(errors) == 1
    assert "reserved" in errors[0].lower()
    assert "hotkey" not in validated


def test_validate_config_update_accepts_non_reserved_hotkey() -> None:
    """Non-reserved hotkeys are accepted as before."""
    validated, errors = validate_config_update({"hotkey": "<f2>"})
    assert errors == []
    assert validated.get("hotkey") == "<f2>"


def test_validate_config_update_rejects_reserved_repaste_hotkey(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repaste_hotkey field also rejects reserved shortcuts."""
    monkeypatch.setattr(sys, "platform", "win32")
    validated, errors = validate_config_update({"repaste_hotkey": "<win>+<l>"})
    assert len(errors) == 1
    assert "reserved" in errors[0].lower()


def test_platform_key_returns_valid_key() -> None:
    """_platform_key returns one of the valid _RESERVED_HOTKEYS keys."""
    pk = _platform_key()
    assert pk in _RESERVED_HOTKEYS, f"_platform_key() returned {pk!r}, expected one of {list(_RESERVED_HOTKEYS.keys())}"
