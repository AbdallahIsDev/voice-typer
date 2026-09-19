"""Tauri host identity guard (identifier, productName, version)."""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from voice_typer.server.server_platform import macos_bundle_id as mbid

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
PACKAGE_JSON = PROJECT_ROOT / "voice_typer" / "client" / "package.json"
PER_ARCH_CONFIGS = sorted(PROJECT_ROOT.glob("src-tauri/tauri.*.conf.json"))


class TestTauriIdentityFields:
    """Tauri identity fields stay non-empty and never end in ``.app``."""

    def test_identifier_present_and_not_dot_app(self, tauri_conf: dict):
        identifier = tauri_conf.get("identifier")
        assert identifier, "tauri.conf.json must declare identifier"
        _fail_on_dot_app(identifier, "identifier", TAURI_CONF)

    def test_product_name_present(self, tauri_conf: dict):
        product_name = tauri_conf.get("productName")
        assert product_name, "tauri.conf.json must declare productName"

    def test_version_matches_package_json(self, tauri_conf: dict, package_json: dict):
        assert tauri_conf.get("version") == package_json.get("version"), (
            "tauri.conf.json version must match package.json version"
        )

    def test_per_arch_configs_do_not_override_identity(self):
        for cfg_path in PER_ARCH_CONFIGS:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            for key in ("identifier", "productName", "version"):
                assert key not in data, (
                    f"{cfg_path.name} must not override {key}; base tauri.conf.json is the single source of truth"
                )


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse ``src-tauri/tauri.conf.json`` once per module."""
    assert TAURI_CONF.is_file(), f"tauri.conf.json not found at {TAURI_CONF}"
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def package_json() -> dict:
    """Load + parse ``voice_typer/client/package.json`` once per module."""
    assert PACKAGE_JSON.is_file(), f"package.json not found at {PACKAGE_JSON}"
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def _fail_on_dot_app(value: str, label: str, file: Path) -> None:
    """Assert ``value`` does not end in ``.app`` (case-insensitive)."""
    assert not value.lower().endswith(".app"), (
        f"{label} '{value}' in {file} ends with '.app', the Tauri CLI "
        "warns on this ('conflicts with the application bundle extension "
        "on macOS'). Rename it (e.g. 'com.voicetyper.desktop')."
    )


def _built_macos_bundle_root(tauri_conf: dict) -> Path | None:
    """Locate the CI-built ``<productName>.app`` under ``src-tauri/target``."""
    product_name = tauri_conf.get("productName")
    assert isinstance(product_name, str) and product_name, (
        f"tauri.conf.json must have a non-empty string 'productName'; got {product_name!r}"
    )
    candidates = [
        # The universal build the tauri-macos-build.yml universal job
        PROJECT_ROOT
        / "src-tauri"
        / "target"
        / "universal-apple-darwin"
        / "release"
        / "bundle"
        / "macos"
        / f"{product_name}.app",
        # Single-arch builds on a local dev Mac (host triple).
        PROJECT_ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / f"{product_name}.app",
    ]
    for root in candidates:
        if root.is_dir():
            return root
    return None


class TestBuiltBundleIdentifierRoundTrip:
    """The CI-built ``.app`` must carry the identifier the configs declare."""

    def test_bundle_root_derivable_from_committed_sources(self, tauri_conf: dict):
        """The expected bundle root is DERIVED (never hardcoded): the path"""
        root = _built_macos_bundle_root(tauri_conf)
        product_name = tauri_conf.get("productName")
        assert isinstance(product_name, str) and product_name, (
            f"tauri.conf.json must have a non-empty string 'productName'; got {product_name!r}"
        )
        if root is not None:
            assert root.name == f"{product_name}.app", f"derived bundle root {root} must be named '{product_name}.app'"
            assert root.is_relative_to(PROJECT_ROOT / "src-tauri" / "target"), (
                f"derived bundle root {root} must live under src-tauri/target"
            )

    def test_built_app_info_plist_round_trips_identifier(self, tauri_conf: dict):
        """The built ``.app``'s Info.plist must round-trip the identifier."""
        root = _built_macos_bundle_root(tauri_conf)
        if root is None:
            pytest.skip("no built .app found under src-tauri/target (run cargo tauri build first)")
        plist = root / "Contents" / "Info.plist"
        assert plist.is_file(), f"built bundle at {root} lacks Contents/Info.plist"
        identifier = tauri_conf.get("identifier")
        assert isinstance(identifier, str) and identifier, (
            f"tauri.conf.json must have a non-empty string 'identifier'; got {identifier!r}"
        )
        actual = mbid.read_bundle_identifier(root)
        assert actual == identifier, (
            f"built .app at {root} reports CFBundleIdentifier {actual!r} but "
            f"tauri.conf.json declares identifier {identifier!r}, the bundled "
            "artifact drifted from the config the parity guards pin."
        )


class TestSyntheticBundleRoundTrip:
    """Synthetic-bundle round-trip (platform-independent, no build needed)."""

    def test_synthetic_app_round_trips_identifier(self, tmp_path):
        app = tmp_path / "Synthetic.app"
        contents = app / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.voicetyper.desktop"}))
        assert mbid.app_bundle_root(str(app / "Contents" / "MacOS" / "Synthetic")) == app
        assert mbid.read_bundle_identifier(app) == "com.voicetyper.desktop"

    def test_synthetic_app_missing_plist_is_none(self, tmp_path):
        app = tmp_path / "Empty.app"
        app.mkdir()
        assert mbid.read_bundle_identifier(app) is None


class TestBuiltAppRealPsWalk:
    """macOS-only: the REAL ``ps`` parent-chain walk against the built ``.app``."""

    @pytest.mark.skipif(sys.platform != "darwin", reason="macos-only real ps walk")
    def test_real_ps_walk_resolves_built_app(self, tauri_conf: dict, tmp_path):
        root = _built_macos_bundle_root(tauri_conf)
        identifier = tauri_conf.get("identifier")
        assert isinstance(identifier, str) and identifier, (
            f"tauri.conf.json must have a non-empty string 'identifier'; got {identifier!r}"
        )
        if root is None:
            pytest.skip("no built .app found under src-tauri/target (run cargo tauri build first)")
        # A synthetic child executable INSIDE the built bundle: a copy
        macos_dir = root / "Contents" / "MacOS"
        probe = macos_dir / "__ci_probe_sleep"
        shutil.copy2("/bin/sleep", probe)
        probe.chmod(0o755)
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen([str(probe), "30"])
            # Premise: the live process's comm must resolve to the built
            line = mbid._process_chain_line(proc.pid)
            parts = line.split(None, 1)
            assert len(parts) == 2, f"ps must report '<ppid> <exe>'; got: {line!r}"
            assert mbid.app_bundle_root(parts[1]) == root, f"ps comm must expose the built bundle path; got: {line!r}"
            assert mbid._resolve_host_bundle_id(start_pid=proc.pid) == identifier
        finally:
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=10)
            probe.unlink(missing_ok=True)
