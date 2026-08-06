"""XZ-R6-AS-01: regression test for the ``tauri-binaries.json`` manifest.

Background
----------
The XZ-R6-AS-01 finding flagged that the Tauri host binary
(``voice-typer-tauri``) was spawned at autostart by
``voice_typer/server/autostart_launcher.py`` with NO integrity
check. The fix has two parts:

  1. **Manifest (this file's scope)**: maintain a
     ``tauri-binaries.json`` at the repo root mapping each platform's
     Tauri binary file name to its expected SHA-256, build version,
     and minimum protocol version. Mirrors the structure of
     ``voice_typer/server/native/binaries.json`` ( + ) used
     by the native-hotkey integrity gate.

  2. **Loader (cross-file, owned by another agent)**: implement
     ``verify_tauri_binary_or_skip(path)`` in
     ``autostart_launcher.py`` that hashes the discovered binary and
     compares against the manifest's ``sha256`` field. On mismatch
     (or empty ``sha256`` — meaning the binary was not built in this
     dev tree), the helper logs an ERROR and the autostart launcher
     falls back to spawning the Electron dev binary instead of an
     untrusted Tauri binary.

 (2026-10): the manifest schema was extended so each binary
entry's ``sha256`` field is now a per-(platform, arch) dict rather
than a flat hex string. This lets the manifest disambiguate the same
binary file name across architectures (e.g. ``voice-typer-tauri`` on
Linux x86_64 vs Linux aarch64). macOS uses the single key ``macos``
because the ``.app`` bundle ships a universal Mach-O binary.

This test pins the manifest side: it verifies the file exists at the
expected path, is valid JSON, and contains the three required binary
entries (Linux / Windows / macOS) with all the fields the future
loader will consume — including the per-(platform, arch) ``sha256``
dict. If a future contributor accidentally deletes the manifest,
renames a field, or reverts the schema to the flat-string form, this
test fails — surfacing the break before the (yet-to-be-written) loader
ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST_PATH = _REPO_ROOT / "tauri-binaries.json"

# The three Tauri binary file names the manifest must cover (one per
# desktop platform). Each maps to a single canonical install-name —
# the autostart launcher's ``_tauri_binary`` helper scans well-known
# install paths per OS and returns the first one that exists.
_REQUIRED_BINARY_ENTRIES: tuple[str, ...] = (
    "voice-typer-tauri",  # Linux
    "voice-typer-tauri.exe",  # Windows
    "voice-typer-tauri.app",  # macOS
)

# Each binary entry must declare these top-level fields. The loader
# (when implemented) consumes ``sha256`` (now a per-(platform, arch)
# dict — see ); ``version`` / ``min_proto_version`` are reserved
# for future IPC-protocol gating ( follow-up); ``_platforms`` /
# ``_install_paths`` are documentation/CI hints (the loader does NOT
# consume them at runtime — install-path discovery lives in
# ``autostart_launcher._tauri_binary``).
_REQUIRED_ENTRY_FIELDS: tuple[str, ...] = (
    "sha256",
    "version",
    "min_proto_version",
    "_platforms",
    "_install_paths",
)

# the per-arch sub-keys each binary's ``sha256`` dict MUST
# contain. Linux has two arches (x86_64 + aarch64), Windows has two
# arches (x86_64 + aarch64), and macOS uses a single ``macos`` key
# because the ``.app`` bundle ships a universal Mach-O binary.
_PER_ARCH_SHA256_KEYS: dict[str, tuple[str, ...]] = {
    "voice-typer-tauri": ("linux-x86_64", "linux-aarch64"),
    "voice-typer-tauri.exe": ("windows-x86_64", "windows-aarch64"),
    "voice-typer-tauri.app": ("macos",),
}


class TestTauriBinariesManifest:
    """XZ-R6-AS-01 (manifest side): ``tauri-binaries.json`` structure guards."""

    def test_manifest_file_exists_at_repo_root(self) -> None:
        """The manifest MUST live at the repo root (next to
        ``package.json``) so the autostart launcher can find it via
        a simple relative-path lookup. A future move to
        ``voice_typer/server/tauri-binaries.json`` is fine IF the
        loader is updated to match — but the move must be explicit,
        not accidental."""
        assert _MANIFEST_PATH.exists(), (
            f"XZ-R6-AS-01 regression: `tauri-binaries.json` not found at "
            f"repo root ({_MANIFEST_PATH}). The manifest is the integrity "
            f"gate for the Tauri host binary spawned at autostart — "
            f"without it, the (yet-to-be-written) loader in "
            f"`autostart_launcher.py` has no SHA-256 to compare against."
        )

    def test_manifest_is_valid_json(self) -> None:
        """The manifest must parse as valid JSON (the loader will
        ``json.loads`` it at autostart — a malformed file would crash
        the autostart path on every login)."""
        assert _MANIFEST_PATH.exists(), "manifest file missing (see previous test)"
        try:
            data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"XZ-R6-AS-01 regression: `tauri-binaries.json` is not "
                f"valid JSON: {exc}. The autostart loader calls "
                f"`json.loads(...)` on this file — a malformed manifest "
                f"would crash the autostart path on every login."
            )
        assert isinstance(data, dict), "XZ-R6-AS-01: manifest root must be a JSON object (dict)."

    def test_manifest_has_binaries_key(self) -> None:
        """The manifest root must have a ``binaries`` key holding the
        per-platform entries. A future contributor who restructures
        the manifest (e.g. flattens or nests it) must update this
        test AND the loader to match."""
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert "binaries" in data, (
            "XZ-R6-AS-01: manifest root must contain a `binaries` key. "
            "The loader iterates `manifest['binaries'].items()` to find "
            "the matching entry by binary file name."
        )
        assert isinstance(data["binaries"], dict), (
            "XZ-R6-AS-01: `manifest['binaries']` must be a dict keyed by "
            "binary file name (e.g. 'voice-typer-tauri', "
            "'voice-typer-tauri.exe', 'voice-typer-tauri.app')."
        )

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    def test_manifest_has_entry_for_each_platform(self, binary_name: str) -> None:
        """The manifest must include an entry for each of the three
        platform Tauri binaries. A future contributor adding a new
        platform (e.g. FreeBSD) MUST extend this list — silently
        omitting a platform from the manifest means the loader has
        no SHA-256 to compare against and either (a) refuses to
        spawn the binary (fail-closed, the safer default) or (b)
        fails open (the XZ-R6-AS-01 regression)."""
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert binary_name in data["binaries"], (
            f"XZ-R6-AS-01: manifest is missing the `{binary_name}` entry. "
            f"The autostart launcher discovers the Tauri binary by file "
            f"name per-OS — every platform's binary file name must have "
            f"a matching manifest entry (even if its per-arch `sha256` "
            f"sub-keys are empty during dev)."
        )

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    @pytest.mark.parametrize("field_name", _REQUIRED_ENTRY_FIELDS)
    def test_each_entry_has_all_required_fields(self, binary_name: str, field_name: str) -> None:
        """Each binary entry must declare all five required fields.
        A future contributor who renames ``sha256`` to ``hash`` (or
        drops ``min_proto_version``) breaks the loader silently —
        this test surfaces the break at CI time."""
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = data["binaries"][binary_name]
        assert field_name in entry, (
            f"XZ-R6-AS-01: manifest entry `{binary_name}` is missing "
            f"the `{field_name}` field. The autostart loader consumes "
            f"this field — a rename or removal must update the loader "
            f"too."
        )

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    def test_sha256_field_is_a_per_arch_dict(self, binary_name: str) -> None:
        """the ``sha256`` field MUST be a dict mapping
        per-(platform, arch) keys to hex-digest strings (or empty
        strings in dev builds). A flat string here is a schema
        regression — the loader consults the per-arch sub-key
        matching ``platform.system().lower() + '-' +
        platform.machine()`` (with macOS collapsed to ``macos``).

        Pre-, ``sha256`` was a flat hex string. The schema was
        widened so the manifest can disambiguate the same binary
        file name across architectures (e.g. ``voice-typer-tauri``
        on Linux x86_64 vs Linux aarch64).
        """
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        sha = data["binaries"][binary_name]["sha256"]
        assert isinstance(sha, dict), (
            f"`{binary_name}.sha256` must be a per-(platform, arch) "
            f"dict (e.g. {{\"linux-x86_64\": \"<hex>\", \"linux-aarch64\": "
            f"\"<hex>\"}}), got {type(sha).__name__}. A flat string is a "
            f"schema regression — the loader consults the per-arch sub-key."
        )

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    def test_sha256_dict_has_expected_per_arch_keys(self, binary_name: str) -> None:
        """each binary's ``sha256`` dict MUST contain the
        expected per-arch sub-keys. Linux has two arches, Windows has
        two arches, and macOS uses a single ``macos`` key (universal
        binary). A missing sub-key means the loader cannot look up
        the sha256 for that arch — it would fail-closed even on a
        legitimate production build.
        """
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        sha = data["binaries"][binary_name]["sha256"]
        expected_keys = _PER_ARCH_SHA256_KEYS[binary_name]
        actual_keys = set(sha.keys())
        missing = set(expected_keys) - actual_keys
        assert not missing, (
            f"`{binary_name}.sha256` is missing the expected "
            f"per-arch sub-key(s): {sorted(missing)}. Expected keys: "
            f"{sorted(expected_keys)}; actual keys: {sorted(actual_keys)}. "
            f"The loader consults these sub-keys to look up the sha256 "
            f"for the running platform/arch."
        )

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    def test_sha256_per_arch_values_are_hex_strings(self, binary_name: str) -> None:
        """each per-arch sha256 value MUST be a string (hex
        digest, or empty string in dev builds). The loader does
        ``hashlib.sha256(...).hexdigest() == entry['sha256'][arch]``
        — a non-string field would TypeError at runtime. If non-empty,
        must be a 64-char lowercase hex string.
        """
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        sha_dict = data["binaries"][binary_name]["sha256"]
        for arch_key, sha in sha_dict.items():
            assert isinstance(sha, str), (
                f"`{binary_name}.sha256['{arch_key}']` must be a "
                f"string, got {type(sha).__name__}. Empty string is "
                f"allowed (dev builds); a 64-char hex string is required "
                f"for production."
            )
            if sha:
                assert len(sha) == 64, (
                    f"`{binary_name}.sha256['{arch_key}']` must be "
                    f"64 chars (got {len(sha)}). A SHA-256 hex digest is "
                    f"always 64 chars."
                )
                assert all(c in "0123456789abcdef" for c in sha), (
                    f"`{binary_name}.sha256['{arch_key}']` must be "
                    f"lowercase hex (got non-hex chars)."
                )

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    def test_platforms_field_is_a_non_empty_list(self, binary_name: str) -> None:
        """The ``_platforms`` field is documentation (the loader does
        not consume it), but it must be a non-empty list so a future
        contributor can see at a glance which platforms the binary
        targets."""
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        platforms = data["binaries"][binary_name]["_platforms"]
        assert isinstance(platforms, list), f"XZ-R6-AS-01: `{binary_name}._platforms` must be a list."
        assert len(platforms) > 0, f"XZ-R6-AS-01: `{binary_name}._platforms` must not be empty."

    @pytest.mark.parametrize("binary_name", _REQUIRED_BINARY_ENTRIES)
    def test_install_paths_field_is_a_non_empty_list(self, binary_name: str) -> None:
        """The ``_install_paths`` field documents the well-known
        install paths the autostart launcher scans. Must be a
        non-empty list so a future contributor knows where the
        binary is expected to live."""
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        paths = data["binaries"][binary_name]["_install_paths"]
        assert isinstance(paths, list), f"XZ-R6-AS-01: `{binary_name}._install_paths` must be a list."
        assert len(paths) > 0, f"XZ-R6-AS-01: `{binary_name}._install_paths` must not be empty."

    def test_manifest_has_version_field(self) -> None:
        """The manifest root must declare a ``version`` field for
        future schema migrations. Initial value is 1."""
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert "version" in data, (
            "XZ-R6-AS-01: manifest root must declare a `version` field for future schema migrations."
        )
        assert isinstance(data["version"], int), (
            f"XZ-R6-AS-01: `version` must be an int (got {type(data['version']).__name__})."
        )

    def test_manifest_has_schema_version_field(self) -> None:
        """the manifest root must declare a ``_schema_version``
        field for tracking schema migrations. v1 was the flat-string
        ``sha256`` schema; v2 is the per-(platform, arch) dict schema.
        """
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert "_schema_version" in data, (
            "manifest root must declare a `_schema_version` field "
            "for tracking schema migrations. v2 is the per-(platform, arch) "
            "dict `sha256` schema."
        )
        assert data["_schema_version"] == 2, (
            f"`_schema_version` must be 2 (per-(platform, arch) dict "
            f"sha256 schema); got {data['_schema_version']!r}."
        )

    def test_manifest_has_schema_changelog(self) -> None:
        """the manifest root should declare a
        ``_schema_changelog`` field documenting v1 → v2 migration
        context, so a future contributor can understand the schema
        history without git archaeology.
        """
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert "_schema_changelog" in data, (
            "manifest root must declare a `_schema_changelog` field "
            "documenting v1 → v2 schema migration (flat-string sha256 → "
            "per-(platform, arch) dict)."
        )
        changelog = data["_schema_changelog"]
        assert isinstance(changelog, str) and "v2" in changelog and "" in changelog, (
            "`_schema_changelog` must mention v2 and ."
        )

    def test_manifest_has_loader_contract(self) -> None:
        """the manifest root must declare a
        ``_manifest_loader_contract`` field documenting how the loader
        must consult the per-(platform, arch) ``sha256`` dict (rather
        than the legacy flat-string form). This pins the contract
        between the manifest and the (yet-to-be-written) loader.
        """
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        assert "_manifest_loader_contract" in data, (
            "manifest root must declare a `_manifest_loader_contract` "
            "field documenting how the loader consults the per-(platform, "
            "arch) sha256 dict."
        )
        contract = data["_manifest_loader_contract"]
        assert isinstance(contract, str) and "platform" in contract.lower(), (
            "`_manifest_loader_contract` must reference the "
            "platform/arch lookup logic."
        )
