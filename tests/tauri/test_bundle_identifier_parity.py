"""Tauri ↔ Electron identity parity guard (identifier, productName, version).

The SAME app ships through two runtimes — the Electron shell
(``voice_typer/client/``) and the Tauri host (``src-tauri/``) — so the
three identity fields must stay in lockstep across the configs that
feed them:

- ``identifier`` (tauri.conf.json) == ``appId`` (electron-builder.yml)
  — both become the macOS ``CFBundleIdentifier`` (plus the Windows
  MSI/NSIS product identity, Android package-name root, etc.). The
  documented invariant (``docs/migration/signing-guide.md`` +
  ``docs/adr/0020``) is that the Tauri ``identifier`` "matches today's
  ``electron-builder.yml`` ``appId``". Drift means one runtime ships a
  different app identity than the other (broken upgrades, orphaned
  TCC/permission entries, duplicate dock/tray presence).
- ``productName`` (tauri.conf.json) == ``productName``
  (electron-builder.yml) — the display name shown in the menu bar,
  dock, Start menu, ``.app`` bundle name, etc. Note this is NOT
  compared to ``package.json`` ``name``: npm names are conventionally
  lowercase-hyphenated (``voice-typer-desktop``) and are not display
  names.
- ``version`` (tauri.conf.json) == ``version`` (package.json) —
  electron-builder derives its version from package.json (there is no
  top-level ``version`` in electron-builder.yml), so the version chain
  is tauri ↔ package.json. Drift shows two different version numbers
  to users / the updater.

The Tauri CLI also emits a build-log WARNING when the identifier ends
in ``.app`` (it collides with the macOS application-bundle extension,
e.g. ``com.voicetyper.app``), and ``electron-builder``'s ``appId`` has
the same hazard for the macOS ``CFBundleIdentifier``. This module
fails fast if either value regresses to a ``.app`` suffix, so the
warning can't silently come back.

CI merges a per-arch config (``tauri.<os>-<arch>.conf.json``) over the
base ``tauri.conf.json`` via ``--config``; a per-arch ``identifier`` /
``productName`` / ``version`` override would bypass this parity guard
on that platform's build, so the tests also pin the base config as the
single source of truth for all three identity fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
ELECTRON_BUILDER_YML = PROJECT_ROOT / "voice_typer" / "client" / "electron-builder.yml"
PACKAGE_JSON = PROJECT_ROOT / "voice_typer" / "client" / "package.json"
PER_ARCH_CONFIGS = sorted(PROJECT_ROOT.glob("src-tauri/tauri.*.conf.json"))


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse ``src-tauri/tauri.conf.json`` once per module."""
    assert TAURI_CONF.is_file(), f"tauri.conf.json not found at {TAURI_CONF}"
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def electron_builder() -> dict:
    """Load + parse ``voice_typer/client/electron-builder.yml`` once per module."""
    assert ELECTRON_BUILDER_YML.is_file(), f"electron-builder.yml not found at {ELECTRON_BUILDER_YML}"
    parsed = yaml.safe_load(ELECTRON_BUILDER_YML.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"electron-builder.yml must parse to a dict; got {type(parsed).__name__}"
    return parsed


@pytest.fixture(scope="module")
def package_json() -> dict:
    """Load + parse ``voice_typer/client/package.json`` once per module."""
    assert PACKAGE_JSON.is_file(), f"package.json not found at {PACKAGE_JSON}"
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def _fail_on_dot_app(value: str, label: str, file: Path) -> None:
    """Assert ``value`` does not end in ``.app`` (case-insensitive)."""
    assert not value.lower().endswith(".app"), (
        f"{label} '{value}' in {file} ends with '.app' — the Tauri CLI "
        "warns on this ('conflicts with the application bundle extension "
        "on macOS') and electron-builder's macOS CFBundleIdentifier has "
        "the same hazard. Rename it (e.g. 'com.voicetyper.desktop')."
    )


class TestIdentifierAppIdParity:
    """tauri.conf.json ``identifier`` ↔ electron-builder.yml ``appId`` lockstep."""

    def test_identifier_matches_app_id(self, tauri_conf: dict, electron_builder: dict):
        """The Tauri identifier and the Electron appId must be identical.

        Documented invariant (docs/migration/signing-guide.md +
        docs/adr/0020-desktop-runtime-migration-analysis.md): the Tauri
        ``CFBundleIdentifier`` "matches today's ``electron-builder.yml``
        ``appId``". Both become the macOS bundle ID for their runtime;
        drift means two different app identities.
        """
        identifier = tauri_conf.get("identifier")
        app_id = electron_builder.get("appId")
        assert isinstance(identifier, str) and identifier, (
            f"tauri.conf.json must have a non-empty string 'identifier'; got {identifier!r}"
        )
        assert isinstance(app_id, str) and app_id, (
            f"electron-builder.yml must have a non-empty string 'appId'; got {app_id!r}"
        )
        assert identifier == app_id, (
            f"Tauri identifier '{identifier}' (src-tauri/tauri.conf.json) != "
            f"Electron appId '{app_id}' (voice_typer/client/electron-builder.yml). "
            "They identify the same app and must stay in lockstep (documented "
            "invariant in docs/migration/signing-guide.md)."
        )

    def test_tauri_identifier_does_not_end_in_dot_app(self, tauri_conf: dict):
        """The Tauri identifier must never end in ``.app`` (build-log warning)."""
        identifier = tauri_conf.get("identifier")
        assert isinstance(identifier, str) and identifier, (
            f"tauri.conf.json must have a non-empty string 'identifier'; got {identifier!r}"
        )
        _fail_on_dot_app(identifier, "Tauri identifier", TAURI_CONF)

    def test_electron_app_id_does_not_end_in_dot_app(self, electron_builder: dict):
        """The Electron appId must never end in ``.app`` (same macOS hazard)."""
        app_id = electron_builder.get("appId")
        assert isinstance(app_id, str) and app_id, (
            f"electron-builder.yml must have a non-empty string 'appId'; got {app_id!r}"
        )
        _fail_on_dot_app(app_id, "Electron appId", ELECTRON_BUILDER_YML)

    def test_per_arch_configs_do_not_override_identifier(self):
        """No ``tauri.<os>-<arch>.conf.json`` may set ``identifier``.

        CI merges the per-arch config over the base (``--config
        tauri.<os>.conf.json``); a per-arch ``identifier`` would bypass
        the parity guard on that platform's build. The base
        ``tauri.conf.json`` must remain the single source of truth.
        """
        assert PER_ARCH_CONFIGS, "no per-arch configs found under src-tauri/tauri.*.conf.json"
        for cfg_path in PER_ARCH_CONFIGS:
            parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
            assert "identifier" not in parsed, (
                f"{cfg_path.name} overrides 'identifier' — per-arch configs must "
                "NOT set it, or the base tauri.conf.json parity guard "
                "(identifier == electron-builder.yml appId) is bypassed on that "
                "platform's CI build."
            )


class TestProductNameVersionParity:
    """tauri.conf.json ``productName``/``version`` ↔ Electron identity lockstep.

    ``productName`` must match electron-builder.yml (the display name),
    and ``version`` must match package.json (electron-builder derives
    its version from there — it has no top-level ``version`` field).
    Drift in either makes the Tauri and Electron builds present
    different product names or version numbers for the same app.
    """

    def test_tauri_product_name_matches_electron_product_name(self, tauri_conf: dict, electron_builder: dict):
        """tauri.conf.json ``productName`` == electron-builder.yml ``productName``.

        Both are the display name — menu bar / dock / Start menu /
        ``.app`` bundle name. Deliberately NOT compared to
        ``package.json`` ``name`` (``voice-typer-desktop``): npm names
        are lowercase-hyphenated and are not display names.
        """
        tauri_name = tauri_conf.get("productName")
        electron_name = electron_builder.get("productName")
        assert isinstance(tauri_name, str) and tauri_name, (
            f"tauri.conf.json must have a non-empty string 'productName'; got {tauri_name!r}"
        )
        assert isinstance(electron_name, str) and electron_name, (
            f"electron-builder.yml must have a non-empty string 'productName'; got {electron_name!r}"
        )
        assert tauri_name == electron_name, (
            f"Tauri productName '{tauri_name}' (src-tauri/tauri.conf.json) != "
            f"Electron productName '{electron_name}' "
            "(voice_typer/client/electron-builder.yml). Both runtimes show "
            "this display name and must stay in lockstep."
        )

    def test_tauri_version_matches_package_json_version(self, tauri_conf: dict, package_json: dict):
        """tauri.conf.json ``version`` == package.json ``version``.

        electron-builder derives its version from package.json (there is
        no top-level ``version`` in electron-builder.yml), so the version
        chain is tauri ↔ package.json. Drift would make the two runtimes
        report different versions to the updater / About dialog.
        """
        tauri_version = tauri_conf.get("version")
        package_version = package_json.get("version")
        assert isinstance(tauri_version, str) and tauri_version, (
            f"tauri.conf.json must have a non-empty string 'version'; got {tauri_version!r}"
        )
        assert isinstance(package_version, str) and package_version, (
            f"package.json must have a non-empty string 'version'; got {package_version!r}"
        )
        assert tauri_version == package_version, (
            f"Tauri version '{tauri_version}' (src-tauri/tauri.conf.json) != "
            f"package.json version '{package_version}' (voice_typer/client/package.json). "
            "electron-builder derives its version from package.json, so the two "
            "runtimes would report different versions — bump them together."
        )

    def test_identity_fields_are_consistent_across_all_three_configs(
        self, tauri_conf: dict, electron_builder: dict, package_json: dict
    ):
        """All three identity fields are present and coherent in every config.

        Cross-checks the full tuple so a single test failure names every
        offender at once (productName pair + version pair).
        """
        assert tauri_conf.get("productName") == electron_builder.get("productName"), (
            "productName drift (tauri.conf.json ↔ electron-builder.yml)"
        )
        assert tauri_conf.get("version") == package_json.get("version"), (
            "version drift (tauri.conf.json ↔ package.json)"
        )

    def test_per_arch_configs_do_not_override_product_name_or_version(self):
        """No per-arch config may set ``productName`` or ``version``.

        CI merges the per-arch config over the base; a per-arch
        override of either field would bypass the parity guard on that
        platform's build. The base ``tauri.conf.json`` must remain the
        single source of truth for all three identity fields.
        """
        assert PER_ARCH_CONFIGS, "no per-arch configs found under src-tauri/tauri.*.conf.json"
        for cfg_path in PER_ARCH_CONFIGS:
            parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
            overrides = [k for k in ("identifier", "productName", "version") if k in parsed]
            assert not overrides, (
                f"{cfg_path.name} overrides {overrides} — per-arch configs must "
                "NOT set identity fields, or the base tauri.conf.json parity "
                "guards (identifier == appId, productName == productName, "
                "version == package.json version) are bypassed on that "
                "platform's CI build."
            )
