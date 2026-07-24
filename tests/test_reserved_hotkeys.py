"""Tests for the backend reserved-hotkey mirror.

HOTKEY-UNIFY-002: verifies that:
  1. ``_RESERVED_HOTKEYS`` in ``config_validators.py`` matches the
     frontend ``RESERVED_SHORTCUTS`` mirror in ``hotkey/hotkey-validation.ts``,
     which since the 3c2b5d6 refactor re-exports the per-platform table
     from ``data/hotkey_reserved.json`` (a byte-identical client-side copy
     of the server canonical).
  2. ``_validate_hotkey`` correctly identifies reserved shortcuts.
  3. The ``_VALIDATOR_HOTKEY`` validator rejects reserved shortcuts
     via ``validate_config_update``.
  4. Cross-platform: a shortcut reserved on macOS (Cmd+Space) is NOT
     rejected on Linux/Windows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from voice_typer.server.config_validators import (
    _RESERVED_HOTKEYS,
    _platform_key,
    _validate_hotkey,
    validate_config_update,
)

# ──────────────────────────────────────────────────────────────────────────
# 1. Frontend ↔ backend sync
# ──────────────────────────────────────────────────────────────────────────

_HOTKEY_RESERVED_JSON_CLIENT = Path(__file__).resolve().parents[1] / (
    "voice_typer/client/src/renderer/src/data/hotkey_reserved.json"
)


def _parse_frontend_reserved_shortcuts() -> dict:
    """Load the per-platform reserved-shortcut table from the client JSON.

    Returns a dict matching the backend ``_RESERVED_HOTKEYS`` shape:
    ``{"win32": [...], "darwin": [...], "linux": [...]}``.

    The TS file (``hotkey/hotkey-validation.ts``) re-exports the JSON's
    ``per_platform_reserved`` field as ``RESERVED_SHORTCUTS``, so reading
    the JSON directly is equivalent to parsing the TS literal — and is
    robust to future TS source formatting changes.
    """
    if not _HOTKEY_RESERVED_JSON_CLIENT.exists():
        pytest.skip(f"hotkey_reserved.json not found at {_HOTKEY_RESERVED_JSON_CLIENT}")
    with _HOTKEY_RESERVED_JSON_CLIENT.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("per_platform_reserved", {})


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
# 2. _validate_hotkey
# ──────────────────────────────────────────────────────────────────────────


def test_is_reserved_hotkey_win32() -> None:
    """Win32-reserved shortcuts are detected on the win32 platform."""
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "win32"
    try:
        assert _validate_hotkey("<win>+<e>") is not None
        assert _validate_hotkey("<win>+<v>") is not None
        assert _validate_hotkey("<win>+<space>") is not None
        assert _validate_hotkey("<win>+<l>") is not None
    finally:
        cv._sys.platform = original


def test_is_reserved_hotkey_darwin() -> None:
    """macOS-reserved shortcuts are detected on the darwin platform."""
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "darwin"
    try:
        assert _validate_hotkey("<cmd>+<space>") is not None
        assert _validate_hotkey("<cmd>+<q>") is not None
        assert _validate_hotkey("<cmd>+<tab>") is not None
    finally:
        cv._sys.platform = original


def test_is_reserved_hotkey_linux() -> None:
    """Linux-reserved shortcuts are detected on the linux platform."""
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "linux"
    try:
        assert _validate_hotkey("<super>+<l>") is not None
        assert _validate_hotkey("<super>+<d>") is not None
    finally:
        cv._sys.platform = original


def test_is_reserved_hotkey_cross_platform() -> None:
    """A shortcut reserved on one platform is NOT reserved on another.

    <cmd>+<tab> is reserved on macOS (Spotlight) but not on Windows/Linux.
    <win>+<e> is reserved on Windows (Explorer) but not on macOS.
    """
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform

    cv._sys.platform = "win32"
    try:
        assert _validate_hotkey("<cmd>+<tab>") is None
    finally:
        cv._sys.platform = original

    cv._sys.platform = "linux"
    try:
        assert _validate_hotkey("<cmd>+<tab>") is None
    finally:
        cv._sys.platform = original

    cv._sys.platform = "darwin"
    try:
        assert _validate_hotkey("<win>+<e>") is None
    finally:
        cv._sys.platform = original


def test_is_reserved_hotkey_case_insensitive() -> None:
    """Comparison is case-insensitive — <WIN>+<E> matches <win>+<e>."""
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "win32"
    try:
        assert _validate_hotkey("<WIN>+<E>") is not None
    finally:
        cv._sys.platform = original

    cv._sys.platform = "darwin"
    try:
        assert _validate_hotkey("<Cmd>+<Space>") is not None
    finally:
        cv._sys.platform = original


def test_is_reserved_hotkey_empty() -> None:
    """Empty/None values are rejected as invalid."""
    assert _validate_hotkey("") is not None
    assert _validate_hotkey(None) is not None  # type: ignore[arg-type]


def test_is_reserved_hotkey_non_reserved() -> None:
    """Common dictation shortcuts are NOT reserved."""
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform

    cv._sys.platform = "win32"
    try:
        assert _validate_hotkey("<f2>") is None
        assert _validate_hotkey("<caps_lock>") is None
        assert _validate_hotkey("<ctrl>+<alt>+v") is None
    finally:
        cv._sys.platform = original

    cv._sys.platform = "darwin"
    try:
        assert _validate_hotkey("<f2>") is None
        assert _validate_hotkey("<ctrl>+<alt>+v") is None
    finally:
        cv._sys.platform = original

    cv._sys.platform = "linux"
    try:
        assert _validate_hotkey("<ctrl>+<alt>+v") is None
    finally:
        cv._sys.platform = original


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
    # We monkeypatch _sys.platform to make this deterministic
    # regardless of where the test runs.
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "darwin"
    try:
        validated, errors = validate_config_update({"hotkey": "<cmd>+<space>"})
        assert len(errors) == 1
        assert "reserved" in errors[0].lower()
        assert "hotkey" not in validated
    finally:
        cv._sys.platform = original


def test_validate_config_update_accepts_non_reserved_hotkey() -> None:
    """Non-reserved hotkeys are accepted as before."""
    validated, errors = validate_config_update({"hotkey": "<f2>"})
    assert errors == []
    assert validated.get("hotkey") == "<f2>"


def test_validate_config_update_rejects_reserved_repaste_hotkey() -> None:
    """The repaste_hotkey field also rejects reserved shortcuts."""
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "win32"
    try:
        validated, errors = validate_config_update({"repaste_hotkey": "<win>+<l>"})
        assert len(errors) == 1
        assert "reserved" in errors[0].lower()
    finally:
        cv._sys.platform = original


def test_validate_config_update_silently_drops_push_to_talk_hotkey() -> None:
    """GT-F2-8: ``push_to_talk_hotkey`` was removed from
    ``IPC_CONFIG_ALLOWLIST`` to match the TS-side contract (config.ts
    documents it as a write-only back-compat field the renderer MUST
    NOT write). The field is still a ``Config`` dataclass member and
    still validated on load — only the IPC write path is closed.

    A payload with ``push_to_talk_hotkey`` set to a *reserved* shortcut
    must NOT raise a reserved-shortcut error: the key is silently
    dropped by ``validate_config_update`` (same contract as any other
    unknown key), so the reserved-shortcut validator never runs.
    """
    import voice_typer.server.config_validators as cv

    original = cv._sys.platform
    cv._sys.platform = "darwin"
    try:
        validated, errors = validate_config_update({"push_to_talk_hotkey": "<cmd>+<q>"})
        # No errors — the field is silently dropped, not rejected.
        assert errors == [], f"push_to_talk_hotkey should be silently dropped (GT-F2-8); got errors: {errors}"
        # The field does not appear in the validated dict.
        assert "push_to_talk_hotkey" not in validated
    finally:
        cv._sys.platform = original


# ──────────────────────────────────────────────────────────────────────────
# 4. _platform_key
# ──────────────────────────────────────────────────────────────────────────


def test_platform_key_returns_valid_key() -> None:
    """_platform_key returns one of the valid _RESERVED_HOTKEYS keys."""
    pk = _platform_key()
    assert pk in _RESERVED_HOTKEYS, f"_platform_key() returned {pk!r}, expected one of {list(_RESERVED_HOTKEYS.keys())}"
