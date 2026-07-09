"""CI sync test for the shared reserved-hotkey table.

HOTKEY-SHARED-001 (Task 1.4): the canonical reserved-shortcut table lives
in ``voice_typer/server/hotkey_reserved.json``.  The backend
(``config_validators.py``) loads it via ``json.load`` at module init.  The
frontend imports a COPY at
``voice_typer/client/src/renderer/src/data/hotkey_reserved.json`` (the
original @server Vite alias resolved outside the renderer root and crashed
Vite's dev server during HMR on locale switch).

This test verifies that:
1. The canonical JSON file exists, is parseable, and has correct structure.
2. The CLIENT COPY is byte-identical to the server original (a CI gate
   that prevents the two from drifting apart).
3. The TS frontend file imports from the client copy and re-exports all
   four data fields.
4. The backend Python module loads the JSON and its in-memory structures
   match the file content.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Path to the canonical JSON file (single source of truth).
JSON_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "server"
    / "hotkey_reserved.json"
)
# Path to the client copy of the JSON (imported by hotkey-validation.ts).
CLIENT_JSON_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "data"
    / "hotkey_reserved.json"
)
# Path to the frontend TS file that imports the JSON.
TS_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "components"
    / "hotkey-validation.ts"
)

# The four fields that must be re-exported from the JSON import.
_JSON_FIELDS = (
    "universal_reserved",
    "per_platform_reserved",
    "blocked_ctrl_letters",
    "modifiers",
)


def _load_json() -> dict:
    """Load the canonical reserved-hotkey JSON config."""
    with JSON_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def json_data() -> dict:
    """Load the JSON once per module."""
    return _load_json()


@pytest.fixture(scope="module")
def ts_content() -> str:
    """Read the TS file once per module."""
    return TS_PATH.read_text(encoding="utf-8")


class TestClientCopyIsInSync:
    """Verify the client copy of the JSON is byte-identical to the server original."""

    def test_client_copy_matches_server_original(self) -> None:
        """The client copy at data/hotkey_reserved.json must be
        byte-identical to the server original.  If this fails, copy
        with::

            cp voice_typer/server/hotkey_reserved.json \\
               voice_typer/client/src/renderer/src/data/hotkey_reserved.json
        """
        import hashlib
        server_bytes = JSON_PATH.read_bytes()
        client_bytes = CLIENT_JSON_PATH.read_bytes()
        assert server_bytes == client_bytes, (
            "Client copy of hotkey_reserved.json differs from server "
            "original. Run the copy command above to sync them."
        )
        # Double-check via hash for deterministic diagnostics
        server_hash = hashlib.sha256(server_bytes).hexdigest()
        client_hash = hashlib.sha256(client_bytes).hexdigest()
        assert server_hash == client_hash, (
            f"SHA256 mismatch: server={server_hash} client={client_hash}"
        )


class TestFrontendImportsFromJson:
    """Verify the frontend TS file imports from the client copy and
    re-exports all required fields."""

    def test_ts_imports_from_client_copy(self, ts_content: str) -> None:
        """The TS file must import from ../data/hotkey_reserved.json
        (the client-side copy, not via @server alias which resolved
        outside the renderer root and crashed Vite HMR)."""
        assert "import hotkeyReserved from \"../data/hotkey_reserved.json\"" in ts_content or \
               "import hotkeyReserved from '../data/hotkey_reserved.json'" in ts_content, (
            "hotkey-validation.ts must import from ../data/hotkey_reserved.json "
            "(client-side copy, not @server alias)"
        )

    def test_ts_re_exports_all_fields(self, ts_content: str) -> None:
        """The TS file must re-export all four data fields from the JSON."""
        for field in _JSON_FIELDS:
            # Expect: export const UNIVERSAL_RESERVED_SHORTCUTS = hotkeyReserved.universal_reserved
            # The TS variable name is the field name converted to SCREAMING_SNAKE_CASE.
            # Map JSON field names to their TS constant names.
            _FIELD_TO_TS_VAR = {
                "universal_reserved": "UNIVERSAL_RESERVED_SHORTCUTS",
                "per_platform_reserved": "RESERVED_SHORTCUTS",
                "blocked_ctrl_letters": "BLOCKED_CTRL_LETTERS",
                "modifiers": "MODIFIER_KEYS_SHARED",
            }
            ts_var = _FIELD_TO_TS_VAR[field]
            # The TS file has a type annotation between the name and `=`
            # (e.g. ``export const UNIVERSAL_RESERVED_SHORTCUTS: readonly string[] =``).
            # Just check that `export const {ts_var}` appears.
            assert f"export const {ts_var}" in ts_content, (
                f"hotkey-validation.ts must export const {ts_var} (for JSON field {field!r})"
            )
            assert f"hotkeyReserved.{field}" in ts_content, (
                f"hotkey-validation.ts must reference hotkeyReserved.{field}"
            )


class TestBackendLoadsFromJson:
    """Verify the backend config_validators.py loads from the JSON file."""

    def test_backend_imports_match_json(self, json_data: dict) -> None:
        """The backend _RESERVED_HOTKEYS, _UNIVERSAL_RESERVED_HOTKEYS,
        _BLOCKED_CTRL_LETTERS, and _HOTKEY_MODIFIERS all match the JSON."""
        from voice_typer.server.config_validators import (
            _BLOCKED_CTRL_LETTERS,
            _HOTKEY_MODIFIERS,
            _RESERVED_HOTKEYS,
            _UNIVERSAL_RESERVED_HOTKEYS,
        )

        # Universal reserved.
        assert _UNIVERSAL_RESERVED_HOTKEYS == frozenset(
            json_data["universal_reserved"]
        ), "Backend _UNIVERSAL_RESERVED_HOTKEYS does not match JSON"

        # Per-platform reserved.
        for platform, entries in json_data["per_platform_reserved"].items():
            assert platform in _RESERVED_HOTKEYS, (
                f"Platform {platform!r} missing from backend _RESERVED_HOTKEYS"
            )
            assert _RESERVED_HOTKEYS[platform] == set(entries), (
                f"Backend _RESERVED_HOTKEYS[{platform!r}] does not match JSON"
            )

        # Blocked Ctrl letters.
        assert _BLOCKED_CTRL_LETTERS == frozenset(
            json_data["blocked_ctrl_letters"]
        ), "Backend _BLOCKED_CTRL_LETTERS does not match JSON"

        # Modifiers.
        assert _HOTKEY_MODIFIERS == frozenset(json_data["modifiers"]), (
            "Backend _HOTKEY_MODIFIERS does not match JSON"
        )

    def test_json_file_is_at_canonical_path(self) -> None:
        """The JSON file lives at voice_typer/server/hotkey_reserved.json."""
        assert JSON_PATH.exists(), (
            f"Canonical reserved-hotkey JSON file not found at {JSON_PATH}"
        )


class TestJsonStructure:
    """Verify the JSON file's structural invariants."""

    def test_json_file_exists(self, json_data: dict) -> None:
        """The JSON file exists and is parseable."""
        assert isinstance(json_data, dict)
        for field in _JSON_FIELDS:
            assert field in json_data, f"JSON missing field {field!r}"

    def test_universal_reserved_is_non_empty(self, json_data: dict) -> None:
        assert len(json_data["universal_reserved"]) > 0

    def test_per_platform_reserved_has_all_platforms(self, json_data: dict) -> None:
        platforms = json_data["per_platform_reserved"]
        assert "win32" in platforms
        assert "darwin" in platforms
        assert "linux" in platforms

    def test_linux_does_not_reserve_super_space(self, json_data: dict) -> None:
        # Invariant: <super>+<space> is intentionally NOT reserved on Linux.
        # Most Linux DEs allow reassigning it. The existing test
        # "still offers <super>+<space> on Linux" pins this in the
        # frontend test suite.
        assert "<super>+<space>" not in json_data["per_platform_reserved"]["linux"]

    def test_all_entries_are_lowercase(self, json_data: dict) -> None:
        for entry in json_data["universal_reserved"]:
            assert entry == entry.lower(), (
                f"Universal reserved entry {entry!r} must be lowercase"
            )
        for platform, entries in json_data["per_platform_reserved"].items():
            for entry in entries:
                assert entry == entry.lower(), (
                    f"Per-platform reserved entry {entry!r} for {platform} "
                    "must be lowercase"
                )

    def test_blocked_ctrl_letters_are_single_lowercase(self, json_data: dict) -> None:
        for letter in json_data["blocked_ctrl_letters"]:
            assert len(letter) == 1
            assert letter.isalpha()
            assert letter.islower()

    def test_modifiers_contains_core_modifiers(self, json_data: dict) -> None:
        modifiers = set(json_data["modifiers"])
        for mod in ("ctrl", "shift", "alt", "cmd", "win", "super", "fn"):
            assert mod in modifiers, f"Core modifier {mod!r} missing from JSON"

    def test_modifiers_contains_left_right_variants(
        self, json_data: dict
    ) -> None:
        modifiers = set(json_data["modifiers"])
        for mod in (
            "ctrl_l",
            "ctrl_r",
            "shift_l",
            "shift_r",
            "alt_l",
            "alt_r",
            "cmd_l",
            "cmd_r",
        ):
            assert mod in modifiers, (
                f"Modifier variant {mod!r} missing from JSON"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
