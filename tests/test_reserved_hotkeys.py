"""Tests for the backend reserved-hotkey mirror.

HOTKEY-UNIFY-002: verifies that:
  1. ``_RESERVED_HOTKEYS`` in ``config_validators.py`` matches the
     frontend ``RESERVED_SHORTCUTS`` in ``hotkey-validation.ts``.
  2. ``_is_reserved_hotkey`` correctly identifies reserved shortcuts.
  3. The ``_VALIDATOR_HOTKEY`` validator rejects reserved shortcuts
     via ``validate_config_update``.
  4. Cross-platform: a shortcut reserved on macOS (Cmd+Space) is NOT
     rejected on Linux/Windows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voice_typer.server.config_validators import (
    _RESERVED_HOTKEYS,
    _current_platform,
    _is_reserved_hotkey,
    validate_config_update,
)


# ──────────────────────────────────────────────────────────────────────────
# 1. Frontend ↔ backend sync
# ──────────────────────────────────────────────────────────────────────────

_HOTKEY_VALIDATION_TS = Path(__file__).resolve().parents[1] / (
    "voice_typer/client/src/renderer/src/components/hotkey-validation.ts"
)


def _parse_frontend_reserved_shortcuts() -> dict:
    """Parse the RESERVED_SHORTCUTS literal from hotkey-validation.ts.

    Returns a dict matching the backend ``_RESERVED_HOTKEYS`` shape:
    ``{"win32": [...], "darwin": [...], "linux": [...]}``.
    """
    if not _HOTKEY_VALIDATION_TS.exists():
        pytest.skip(
            f"hotkey-validation.ts not found at {_HOTKEY_VALIDATION_TS}"
        )
    src = _HOTKEY_VALIDATION_TS.read_text(encoding="utf-8")

    # Extract the object literal assigned to RESERVED_SHORTCUTS.
    # Match: export const RESERVED_SHORTCUTS: Record<...> = { ... };
    m = re.search(
        r"export\s+const\s+RESERVED_SHORTCUTS[^=]*=\s*(\{[^;]+\});",
        src,
        re.DOTALL,
    )
    assert m, "Could not find RESERVED_SHORTCUTS literal in hotkey-validation.ts"
    obj_literal = m.group(1)

    # Parse each platform key → list of shortcut strings.
    result: dict = {}
    # Match: win32: [ "...", "...", ... ],
    for pm in re.finditer(
        r'(win32|darwin|linux)\s*:\s*\[(.*?)\]',
        obj_literal,
        re.DOTALL,
    ):
        platform_key = pm.group(1)
        list_body = pm.group(2)
        # Extract all double-quoted strings, ignoring comments.
        # Strip line comments first.
        list_body_clean = re.sub(r'//.*', '', list_body)
        shortcuts = re.findall(r'"([^"]+)"', list_body_clean)
        result[platform_key] = shortcuts
    return result


def test_reserved_hotkeys_match_frontend() -> None:
    """The backend _RESERVED_HOTKEYS must match the frontend RESERVED_SHORTCUTS.

    If you add a shortcut to one side, add it to the other. This test
    catches drift at CI time.
    """
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


# ──────────────────────────────────────────────────────────────────────────
# 2. _is_reserved_hotkey
# ──────────────────────────────────────────────────────────────────────────

def test_is_reserved_hotkey_win32() -> None:
    """Win32-reserved shortcuts are detected on the win32 platform."""
    assert _is_reserved_hotkey("<win>+e", "win32") is True
    assert _is_reserved_hotkey("<win>+v", "win32") is True
    assert _is_reserved_hotkey("<win>+space", "win32") is True
    assert _is_reserved_hotkey("<win>+l", "win32") is True


def test_is_reserved_hotkey_darwin() -> None:
    """macOS-reserved shortcuts are detected on the darwin platform."""
    assert _is_reserved_hotkey("<cmd>+space", "darwin") is True
    assert _is_reserved_hotkey("<cmd>+q", "darwin") is True
    assert _is_reserved_hotkey("<cmd>+tab", "darwin") is True


def test_is_reserved_hotkey_linux() -> None:
    """Linux-reserved shortcuts are detected on the linux platform."""
    assert _is_reserved_hotkey("<super>+l", "linux") is True
    assert _is_reserved_hotkey("<super>+d", "linux") is True


def test_is_reserved_hotkey_cross_platform() -> None:
    """A shortcut reserved on one platform is NOT reserved on another.

    <cmd>+space is reserved on macOS (Spotlight) but not on Windows/Linux.
    <win>+e is reserved on Windows (Explorer) but not on macOS/Linux.
    """
    assert _is_reserved_hotkey("<cmd>+space", "win32") is False
    assert _is_reserved_hotkey("<cmd>+space", "linux") is False
    assert _is_reserved_hotkey("<win>+e", "darwin") is False
    assert _is_reserved_hotkey("<win>+e", "linux") is False


def test_is_reserved_hotkey_case_insensitive() -> None:
    """Comparison is case-insensitive — <WIN>+E matches <win>+e."""
    assert _is_reserved_hotkey("<WIN>+E", "win32") is True
    assert _is_reserved_hotkey("<Cmd>+<Space>", "darwin") is True


def test_is_reserved_hotkey_empty() -> None:
    """Empty string is never reserved."""
    assert _is_reserved_hotkey("", "win32") is False
    assert _is_reserved_hotkey(None, "win32") is False  # type: ignore[arg-type]


def test_is_reserved_hotkey_non_reserved() -> None:
    """Common dictation shortcuts are NOT reserved."""
    assert _is_reserved_hotkey("<f2>", "win32") is False
    assert _is_reserved_hotkey("<f2>", "darwin") is False
    assert _is_reserved_hotkey("<caps_lock>", "win32") is False
    assert _is_reserved_hotkey("<ctrl>+<alt>+v", "win32") is False
    assert _is_reserved_hotkey("<super>+<space>", "linux") is False  # not reserved on Linux


# ──────────────────────────────────────────────────────────────────────────
# 3. validate_config_update rejects reserved shortcuts
# ──────────────────────────────────────────────────────────────────────────

def test_validate_config_update_rejects_reserved_hotkey() -> None:
    """Setting a reserved hotkey via IPC is rejected.

    A malicious IPC client that bypasses the frontend validation
    (e.g. by writing directly to the TCP socket) should still be
    rejected by the backend mirror.
    """
    # Use the darwin platform to test <cmd>+<space> rejection.
    # We monkeypatch _current_platform to make this deterministic
    # regardless of where the test runs.
    import voice_typer.server.config_validators as cv

    original = cv._current_platform
    cv._current_platform = lambda: "darwin"  # type: ignore[assignment]
    try:
        validated, errors = validate_config_update(
            {"hotkey": "<cmd>+<space>"}
        )
        assert len(errors) == 1
        assert "reserved" in errors[0].lower()
        assert "hotkey" not in validated
    finally:
        cv._current_platform = original


def test_validate_config_update_accepts_non_reserved_hotkey() -> None:
    """Non-reserved hotkeys are accepted as before."""
    validated, errors = validate_config_update({"hotkey": "<f2>"})
    assert errors == []
    assert validated.get("hotkey") == "<f2>"


def test_validate_config_update_rejects_reserved_repaste_hotkey() -> None:
    """The repaste_hotkey field also rejects reserved shortcuts."""
    import voice_typer.server.config_validators as cv

    original = cv._current_platform
    cv._current_platform = lambda: "win32"  # type: ignore[assignment]
    try:
        validated, errors = validate_config_update(
            {"repaste_hotkey": "<win>+l"}
        )
        assert len(errors) == 1
        assert "reserved" in errors[0].lower()
    finally:
        cv._current_platform = original


def test_validate_config_update_rejects_reserved_push_to_talk_hotkey() -> None:
    """The push_to_talk_hotkey field also rejects reserved shortcuts."""
    import voice_typer.server.config_validators as cv

    original = cv._current_platform
    cv._current_platform = lambda: "darwin"  # type: ignore[assignment]
    try:
        validated, errors = validate_config_update(
            {"push_to_talk_hotkey": "<cmd>+q"}
        )
        assert len(errors) == 1
        assert "reserved" in errors[0].lower()
    finally:
        cv._current_platform = original


# ──────────────────────────────────────────────────────────────────────────
# 4. _current_platform
# ──────────────────────────────────────────────────────────────────────────

def test_current_platform_returns_valid_key() -> None:
    """_current_platform returns one of the valid _RESERVED_HOTKEYS keys."""
    pk = _current_platform()
    assert pk in _RESERVED_HOTKEYS, (
        f"_current_platform() returned {pk!r}, expected one of "
        f"{list(_RESERVED_HOTKEYS.keys())}"
    )
