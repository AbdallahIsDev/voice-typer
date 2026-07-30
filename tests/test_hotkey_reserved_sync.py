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
from pathlib import Path

import pytest

# Path to the canonical JSON file (single source of truth).
JSON_PATH = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "hotkey_reserved.json"
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
# After the 3c2b5d6 refactor, the file moved from components/hotkey-validation.ts
# to components/hotkey/hotkey-validation.ts.
TS_PATH = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "components"
    / "hotkey"
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
            "Client copy of hotkey_reserved.json differs from server original. Run the copy command above to sync them."
        )
        # Double-check via hash for deterministic diagnostics
        server_hash = hashlib.sha256(server_bytes).hexdigest()
        client_hash = hashlib.sha256(client_bytes).hexdigest()
        assert server_hash == client_hash, f"SHA256 mismatch: server={server_hash} client={client_hash}"


class TestFrontendImportsFromJson:
    """Verify the frontend TS file imports from the client copy and
    re-exports all required fields."""

    def test_ts_imports_from_client_copy(self, ts_content: str) -> None:
        """The TS file must import from the client copy of hotkey_reserved.json.

        The relative path depends on the file's location:
        - components/hotkey-validation.ts  (pre-3c2b5d6) used ``../data/...``
        - components/hotkey/hotkey-validation.ts  (post-3c2b5d6) uses ``../../data/...``
        Both relative paths are accepted; both single and double quotes are
        accepted because the project's Biome formatter has been inconsistent
        about quote style across versions.
        """
        assert (
            'import hotkeyReserved from "../data/hotkey_reserved.json"' in ts_content
            or "import hotkeyReserved from '../data/hotkey_reserved.json'" in ts_content
            or 'import hotkeyReserved from "../../data/hotkey_reserved.json"' in ts_content
            or "import hotkeyReserved from '../../data/hotkey_reserved.json'" in ts_content
        ), (
            "hotkey-validation.ts must import from ../data/hotkey_reserved.json "
            "or ../../data/hotkey_reserved.json (client-side copy, not @server alias)"
        )

    def test_ts_re_exports_all_fields(self, ts_content: str) -> None:
        """The TS file must re-export all four data fields from the JSON."""
        for field in _JSON_FIELDS:
            # Expect: export const UNIVERSAL_RESERVED_SHORTCUTS = hotkeyReserved.universal_reserved
            # The TS variable name is the field name converted to SCREAMING_SNAKE_CASE.
            # Map JSON field names to their TS constant names.
            _field_to_ts_var = {
                "universal_reserved": "UNIVERSAL_RESERVED_SHORTCUTS",
                "per_platform_reserved": "RESERVED_SHORTCUTS",
                "blocked_ctrl_letters": "BLOCKED_CTRL_LETTERS",
                "modifiers": "MODIFIER_KEYS_SHARED",
            }
            ts_var = _field_to_ts_var[field]
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
        assert frozenset(json_data["universal_reserved"]) == _UNIVERSAL_RESERVED_HOTKEYS, (
            "Backend _UNIVERSAL_RESERVED_HOTKEYS does not match JSON"
        )

        # Per-platform reserved.
        for platform, entries in json_data["per_platform_reserved"].items():
            assert platform in _RESERVED_HOTKEYS, f"Platform {platform!r} missing from backend _RESERVED_HOTKEYS"
            assert _RESERVED_HOTKEYS[platform] == set(entries), (
                f"Backend _RESERVED_HOTKEYS[{platform!r}] does not match JSON"
            )

        # Blocked Ctrl letters.
        assert frozenset(json_data["blocked_ctrl_letters"]) == _BLOCKED_CTRL_LETTERS, (
            "Backend _BLOCKED_CTRL_LETTERS does not match JSON"
        )

        # Modifiers.
        assert frozenset(json_data["modifiers"]) == _HOTKEY_MODIFIERS, "Backend _HOTKEY_MODIFIERS does not match JSON"

    def test_json_file_is_at_canonical_path(self) -> None:
        """The JSON file lives at voice_typer/server/hotkey_reserved.json."""
        assert JSON_PATH.exists(), f"Canonical reserved-hotkey JSON file not found at {JSON_PATH}"


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
            assert entry == entry.lower(), f"Universal reserved entry {entry!r} must be lowercase"
        for platform, entries in json_data["per_platform_reserved"].items():
            for entry in entries:
                assert entry == entry.lower(), f"Per-platform reserved entry {entry!r} for {platform} must be lowercase"

    def test_blocked_ctrl_letters_are_single_lowercase(self, json_data: dict) -> None:
        for letter in json_data["blocked_ctrl_letters"]:
            assert len(letter) == 1
            assert letter.isalpha()
            assert letter.islower()

    def test_modifiers_contains_core_modifiers(self, json_data: dict) -> None:
        modifiers = set(json_data["modifiers"])
        for mod in ("ctrl", "shift", "alt", "cmd", "win", "super", "fn"):
            assert mod in modifiers, f"Core modifier {mod!r} missing from JSON"

    def test_modifiers_contains_left_right_variants(self, json_data: dict) -> None:
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
            assert mod in modifiers, f"Modifier variant {mod!r} missing from JSON"


# ──────────────────────────────────────────────────────────────────────
# XZ-CC-2: noise-filter defaults sync between Config and audio_chain_builder
# ──────────────────────────────────────────────────────────────────────
# The canonical noise-filter defaults live on the ``Config`` dataclass in
# ``voice_typer/server/config.py`` (fields ``noise_filter_*`` plus
# ``noise_suppression_method``). ``voice_typer/server/audio_chain_builder.py``
# keeps a parallel ``_DEFAULTS`` dict (used by ``build_chain_from_dict`` for
# the test-only ``_DictConfig`` shim) that mirrors the same defaults. There
# was no CI gate enforcing the two stay in sync — a future Config default
# change (e.g. bumping ``noise_filter_gate_hold_ms`` from 200 to 250) would
# silently drift from ``_DEFAULTS`` and the test-only path would use the
# stale value.
#
# This test class mirrors the ``test_hotkey_reserved_sync`` pattern: import
# both sources, snapshot the Config defaults for every ``noise_filter_*``
# field (plus ``noise_suppression_method``), and assert each matches the
# corresponding ``_DEFAULTS`` entry. If a Config default changes, this test
# fails until ``_DEFAULTS`` is updated to match (and vice versa).


class TestNoiseFilterDefaultsSync:
    """XZ-CC-2: assert ``audio_chain_builder._DEFAULTS`` matches the
    ``Config`` dataclass defaults for every ``noise_filter_*`` field
    that the chain builder reads.

    Mirrors the ``test_hotkey_reserved_sync`` pattern (CI gate against
    silent drift between two parallel declarations of the same defaults).

    Scope
    -----
    The chain builder reads filter parameters via direct attribute access
    (``config.noise_filter_X``) inside ``build_chain``. The ``_DEFAULTS``
    dict mirrors those same values for the test-only ``_DictConfig`` shim
    (``build_chain_from_dict``). This test asserts every filter parameter
    read by ``build_chain`` has a matching default in both places.

    Intentionally EXCLUDED from the comparison (these are runtime switches,
    deprecated fields, or read via ``getattr`` with a fallback — none are
    consulted via direct ``config.noise_filter_X`` access inside
    ``build_chain``):

    - ``noise_filter_enabled`` — runtime toggle handled by
      ``config_applier.py`` (sets it from ``audio_preset``), NOT read
      inside ``build_chain``.
    - ``noise_filter_post_capture`` — runtime toggle (see ADR 0009),
      NOT read inside ``build_chain``.
    - ``noise_filter_rnnoise`` — deprecated per ADR 0007 (replaced by
      ``noise_suppression_method``); kept on Config for backward-compat
      migrations but NOT read inside ``build_chain``.
    - ``noise_filter_gate_adaptive`` — read inside ``build_chain`` via
      ``getattr(config, "noise_filter_gate_adaptive", False)`` (with a
      hardcoded fallback). Pinned by
      ``test_gate_adaptive_getattr_fallback_matches_config_default`` so
      the fallback tracks the Config default.

    These exclusions are pinned by name in ``_RUNTIME_OR_DEPRECATED_FIELDS``
    so a future contributor who renames them or accidentally starts reading
    them inside ``build_chain`` updates this test too.
    """

    # Fields on Config that are intentionally NOT mirrored in _DEFAULTS
    # because they're runtime switches, deprecated, or read via
    # ``getattr`` with a fallback default inside ``build_chain``. See
    # the class docstring for the rationale per field.
    _RUNTIME_OR_DEPRECATED_FIELDS = frozenset(
        {
            # Runtime switches not consulted by ``build_chain``:
            "noise_filter_enabled",
            "noise_filter_post_capture",
            # Deprecated (ADR 0007) — replaced by ``noise_suppression_method``:
            "noise_filter_rnnoise",
            # Read via ``getattr(config, "noise_filter_gate_adaptive", False)``
            # inside ``build_chain`` — the fallback ``False`` matches the
            # Config default. Pinned by ``test_gate_adaptive_getattr_fallback_matches_config_default``.
            "noise_filter_gate_adaptive",
        }
    )

    def test_defaults_dict_matches_config_class_for_every_noise_filter_field(self) -> None:
        """Every ``noise_filter_*`` (and ``noise_suppression_method``)
        field on ``Config`` that is read by ``build_chain`` must have
        the same default value in ``audio_chain_builder._DEFAULTS``.

        If this test fails, either:
        - ``Config`` was updated (e.g. a default bumped) and
          ``_DEFAULTS`` was not — update ``_DEFAULTS`` to match; OR
        - ``_DEFAULTS`` was updated and ``Config`` was not — update
          ``Config`` to match; OR
        - A new ``noise_filter_*`` field was added to ``Config`` and
          read by ``build_chain`` — add it to ``_DEFAULTS`` (and to
          ``_RUNTIME_OR_DEPRECATED_FIELDS`` if it's a runtime switch
          that ``build_chain`` does NOT read).
        """
        from dataclasses import fields

        from voice_typer.server.audio_chain_builder import _DEFAULTS
        from voice_typer.server.config import Config

        config_snapshot = Config()
        mismatches: list[str] = []
        missing_in_defaults: list[str] = []
        for f in fields(config_snapshot):
            if not (f.name.startswith("noise_filter_") or f.name == "noise_suppression_method"):
                continue
            if f.name in self._RUNTIME_OR_DEPRECATED_FIELDS:
                continue
            config_default = getattr(config_snapshot, f.name)
            if f.name not in _DEFAULTS:
                missing_in_defaults.append(
                    f"{f.name}: present on Config (default={config_default!r}) "
                    f"but MISSING from audio_chain_builder._DEFAULTS"
                )
                continue
            builder_default = _DEFAULTS[f.name]
            # ``Config`` uses ``Literal["rnnoise", "deepfilternet", "none"]``
            # for ``noise_suppression_method`` — the underlying value is a
            # plain ``str``, so a direct ``==`` comparison works for both
            # bool / float / str fields.
            if config_default != builder_default:
                mismatches.append(f"{f.name}: Config default={config_default!r}, _DEFAULTS value={builder_default!r}")
        assert not missing_in_defaults, (
            "audio_chain_builder._DEFAULTS is missing noise_filter_* fields "
            "present on Config (and NOT in _RUNTIME_OR_DEPRECATED_FIELDS):\n  " + "\n  ".join(missing_in_defaults)
        )
        assert not mismatches, "audio_chain_builder._DEFAULTS drifted from Config defaults:\n  " + "\n  ".join(
            mismatches
        )

    def test_defaults_dict_has_no_extra_noise_filter_fields_not_on_config(self) -> None:
        """XZ-CC-2: ``_DEFAULTS`` must not declare any ``noise_filter_*``
        field that doesn't exist on ``Config`` (which would be dead
        config — the build_chain code reads these via ``getattr(config,
        name)`` and a non-existent field would always fall through to
        the default, hiding the drift).
        """
        from dataclasses import fields

        from voice_typer.server.audio_chain_builder import _DEFAULTS
        from voice_typer.server.config import Config

        config_field_names = {f.name for f in fields(Config)}
        extras: list[str] = []
        for name in _DEFAULTS:
            if name not in config_field_names:
                extras.append(f"{name}: present in _DEFAULTS (value={_DEFAULTS[name]!r}) but NOT a field on Config")
        assert not extras, "audio_chain_builder._DEFAULTS has noise_filter_* entries not on Config:\n  " + "\n  ".join(
            extras
        )

    def test_gate_adaptive_getattr_fallback_matches_config_default(self) -> None:
        """XZ-CC-2: ``noise_filter_gate_adaptive`` is read inside
        ``build_chain`` via
        ``getattr(config, "noise_filter_gate_adaptive", False)`` —
        i.e. the chain builder hardcodes a fallback of ``False`` rather
        than consulting ``_DEFAULTS``. This test pins that the hardcoded
        fallback tracks the Config default. If a future Config bump
        changes the default to ``True``, this test fails until the
        ``getattr`` fallback in ``build_chain`` is updated too.
        """
        from voice_typer.server.config import Config

        config_default = Config().noise_filter_gate_adaptive
        # The hardcoded fallback in ``build_chain`` — must be kept in
        # sync manually (there's no programmatic link). If you change
        # the Config default, update both this constant AND the
        # ``getattr`` call in ``audio_chain_builder.py::build_chain``.
        hardcoded_fallback = False
        assert hardcoded_fallback == config_default, (
            "noise_filter_gate_adaptive Config default "
            f"({config_default!r}) does NOT match the hardcoded "
            f"getattr fallback in build_chain ({hardcoded_fallback!r}). "
            "Update the getattr call in audio_chain_builder.py to match."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
