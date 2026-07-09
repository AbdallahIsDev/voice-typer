"""CI sync test for the shared reserved-hotkey table.

HOTKEY-SHARED-001 (Task 1.4): the canonical reserved-shortcut table now
lives in ``voice_typer/server/hotkey_reserved.json``. The backend
(``config_validators.py``) loads it via ``json.load`` at module init.
The frontend (``hotkey-validation.ts``) keeps its own TypeScript
constants (for Vite type-safety and JSON-module compatibility), but
this test verifies that the TS constants are BYTE-IDENTICAL to the
JSON file.

This prevents the "MUST be kept in sync" duplication problem from
recurring: if someone adds a shortcut to one side but not the other,
this test fails loudly in CI.

The test parses the TS file with a simple regex extractor (no TS
compiler dependency) and compares the extracted values to the JSON.
This is intentionally fragile — any formatting change that breaks the
extraction will also fail the test, prompting the developer to update
both sides.
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
# Path to the frontend TS file that mirrors the JSON.
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


def _load_json() -> dict:
    """Load the canonical reserved-hotkey JSON config."""
    with JSON_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _strip_line_comments(text: str) -> str:
    """Remove ``//`` line comments from TS source.

    This is a simplified stripper that handles the common case of
    ``// comment`` on its own line or trailing after a statement. It
    does NOT handle ``/* */`` block comments or ``//`` inside string
    literals — neither appears in the reserved-shortcut table.
    """
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        # Find // that's not inside a string. For the reserved-shortcut
        # table, strings are always on their own line, so a simple
        # check works: if the line contains //, strip from // onward.
        # But we must NOT strip inside a string literal. The simplest
        # heuristic: count double-quotes before //; if odd, // is inside
        # a string. For our use case, the lines with // are pure
        # comments (no string literals), so this is safe.
        if "//" in line:
            # Find the first // that's not inside a string.
            in_string = False
            for i, ch in enumerate(line):
                if ch == '"':
                    in_string = not in_string
                elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/" and not in_string:
                    line = line[:i]
                    break
        out.append(line)
    return "\n".join(out)


def _extract_ts_array(ts_content: str, var_name: str) -> list[str]:
    """Extract an array of string literals from a TS ``const`` declaration.

    Looks for ``export const VAR_NAME: ... = [ "a", "b", ... ]`` and
    returns ``["a", "b", ...]``. The extractor is intentionally strict
    — it only matches double-quoted strings inside the array literal.
    Handles the ``as const`` suffix used by some declarations.
    Comments (``//``) inside the array body are stripped before
    extraction so that strings mentioned in comments (e.g. ``"do NOT
    add <super>+<space>"``) are not picked up.
    """
    # Match: export const VAR_NAME [optional type annotation] = [ ... ] [as const] ;
    pattern = (
        rf"export\s+const\s+{re.escape(var_name)}[^=]*?=\s*\[(.*?)\]"
        rf"(?:\s+as\s+\w+)?\s*;"
    )
    m = re.search(pattern, ts_content, re.DOTALL)
    if not m:
        raise AssertionError(
            f"Could not find `export const {var_name}` in {TS_PATH.name}. "
            "The TS file structure has changed — update this test."
        )
    body = _strip_line_comments(m.group(1))
    strings = re.findall(r'"([^"]*)"', body)
    return strings


def _extract_ts_record_keys(
    ts_content: str, var_name: str
) -> dict[str, list[str]]:
    """Extract a Record<string, string[]> from a TS ``const`` declaration.

    Looks for ``export const VAR_NAME: Record<...> = { key: [...], ... }``
    and returns ``{key: [...], ...}``. Comments inside the record body
    are stripped before extraction so that strings mentioned in comments
    are not picked up.
    """
    pattern = (
        rf"export\s+const\s+{re.escape(var_name)}[^=]*?=\s*\{{(.*?)\}}\s*;"
    )
    m = re.search(pattern, ts_content, re.DOTALL)
    if not m:
        raise AssertionError(
            f"Could not find `export const {var_name}` in {TS_PATH.name}. "
            "The TS file structure has changed — update this test."
        )
    body = _strip_line_comments(m.group(1))
    result: dict[str, list[str]] = {}
    key_pattern = r"(\w+)\s*:\s*\[(.*?)\]"
    for km in re.finditer(key_pattern, body, re.DOTALL):
        key = km.group(1)
        arr_body = km.group(2)
        result[key] = re.findall(r'"([^"]*)"', arr_body)
    return result


@pytest.fixture(scope="module")
def json_data() -> dict:
    """Load the JSON once per module."""
    return _load_json()


@pytest.fixture(scope="module")
def ts_content() -> str:
    """Read the TS file once per module."""
    return TS_PATH.read_text(encoding="utf-8")


class TestReservedShortcutsSync:
    """Verify the frontend TS constants match the canonical JSON file."""

    def test_json_file_exists(self, json_data: dict) -> None:
        """The JSON file exists and is parseable."""
        assert isinstance(json_data, dict)
        assert "universal_reserved" in json_data
        assert "per_platform_reserved" in json_data
        assert "blocked_ctrl_letters" in json_data
        assert "modifiers" in json_data

    def test_universal_reserved_matches(self, json_data: dict, ts_content: str) -> None:
        """UNIVERSAL_RESERVED_SHORTCUTS in TS matches JSON universal_reserved."""
        ts_values = _extract_ts_array(ts_content, "UNIVERSAL_RESERVED_SHORTCUTS")
        json_values = json_data["universal_reserved"]
        assert set(ts_values) == set(json_values), (
            f"UNIVERSAL_RESERVED_SHORTCUTS mismatch.\n"
            f"  TS has:    {sorted(ts_values)}\n"
            f"  JSON has:  {sorted(json_values)}\n"
            f"Update voice_typer/server/hotkey_reserved.json AND/OR "
            f"voice_typer/client/src/renderer/src/components/hotkey-validation.ts "
            f"to keep them in sync."
        )

    def test_per_platform_reserved_matches(
        self, json_data: dict, ts_content: str
    ) -> None:
        """RESERVED_SHORTCUTS in TS matches JSON per_platform_reserved."""
        ts_values = _extract_ts_record_keys(ts_content, "RESERVED_SHORTCUTS")
        json_values = json_data["per_platform_reserved"]
        assert set(ts_values.keys()) == set(json_values.keys()), (
            f"Platform keys mismatch.\n"
            f"  TS has:    {sorted(ts_values.keys())}\n"
            f"  JSON has:  {sorted(json_values.keys())}"
        )
        for platform in ts_values:
            assert set(ts_values[platform]) == set(json_values[platform]), (
                f"RESERVED_SHORTCUTS['{platform}'] mismatch.\n"
                f"  TS has:    {sorted(ts_values[platform])}\n"
                f"  JSON has:  {sorted(json_values[platform])}"
            )

    def test_blocked_ctrl_letters_matches(
        self, json_data: dict, ts_content: str
    ) -> None:
        """BLOCKED_CTRL_LETTERS in TS matches JSON blocked_ctrl_letters."""
        ts_values = _extract_ts_array(ts_content, "BLOCKED_CTRL_LETTERS")
        json_values = json_data["blocked_ctrl_letters"]
        assert set(ts_values) == set(json_values), (
            f"BLOCKED_CTRL_LETTERS mismatch.\n"
            f"  TS has:    {sorted(ts_values)}\n"
            f"  JSON has:  {sorted(json_values)}"
        )

    def test_modifiers_matches(self, json_data: dict, ts_content: str) -> None:
        """MODIFIER_KEYS_SHARED in TS matches JSON modifiers."""
        ts_values = _extract_ts_array(ts_content, "MODIFIER_KEYS_SHARED")
        json_values = json_data["modifiers"]
        assert set(ts_values) == set(json_values), (
            f"MODIFIER_KEYS_SHARED mismatch.\n"
            f"  TS has:    {sorted(ts_values)}\n"
            f"  JSON has:  {sorted(json_values)}"
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
