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
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from voice_typer.server.server_platform import macos_bundle_id as mbid

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


def _built_macos_bundle_root(tauri_conf: dict) -> Path | None:
    """Locate the CI-built ``<productName>.app`` under ``src-tauri/target``.

    Derives the expected bundle root from COMMITTED sources instead of
    hardcoding a path: the bundle directory name comes from
    ``tauri.conf.json`` ``productName`` (plus the ``.app`` suffix the
    Tauri bundler appends), and the target dir follows the build
    layout ``cargo tauri build --target universal-apple-darwin``
    produces (``tauri-macos-build.yml``). Returns ``None`` when no
    built bundle is present — e.g. a dev box with no ``cargo tauri
    build`` output — so callers skip gracefully instead of failing.
    """
    product_name = tauri_conf.get("productName")
    assert isinstance(product_name, str) and product_name, (
        f"tauri.conf.json must have a non-empty string 'productName'; got {product_name!r}"
    )
    candidates = [
        # The universal build the tauri-macos-build.yml universal job
        # produces (--target universal-apple-darwin).
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
    """The CI-built ``.app`` must carry the identifier the configs declare.

    Identity parity (above) pins the CONFIG files; this class pins the
    BUILT ARTIFACT: once ``cargo tauri build`` has produced
    ``<productName>.app`` on the macOS CI runner, its
    ``Contents/Info.plist`` must exist and its ``CFBundleIdentifier``
    must round-trip through ``read_bundle_identifier`` to exactly the
    base ``tauri.conf.json`` ``identifier``. A drift that only the
    bundler could introduce (e.g. a per-arch identifier override that
    slips past the config tests) dies on the artifact, not in the
    release cut.

    All tests skip gracefully when no built ``.app`` exists (dev box,
    workflow legs before ``cargo tauri build``); they assert on the
    artifact when it is present — which is exactly what the post-build
    step added to ``tauri-macos-build.yml`` relies on.
    """

    def test_bundle_root_derivable_from_committed_sources(self, tauri_conf: dict):
        """The expected bundle root is DERIVED (never hardcoded): the path
        must resolve from ``tauri.conf.json`` ``productName`` + the build
        layout, and its basename must be ``<productName>.app`` (Tauri
        bundler convention) — whether or not the artifact exists yet."""
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
        """The built ``.app``'s Info.plist must round-trip the identifier.

        Asserts (when the artifact exists): ``Contents/Info.plist`` is
        present, and ``read_bundle_identifier`` (the same function the
        runtime host-bundle resolver uses) returns exactly the base
        ``tauri.conf.json`` ``identifier``.
        """
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
            f"tauri.conf.json declares identifier {identifier!r} — the bundled "
            "artifact drifted from the config the parity guards pin."
        )


class TestSyntheticBundleRoundTrip:
    """Synthetic-bundle round-trip (platform-independent, no build needed).

    ``read_bundle_identifier`` + ``app_bundle_root`` are exercised
    against an Info.plist written by the test itself, so the parser
    contracts stay regression-locked on every platform, including the
    Windows dev box and the Linux CI sandbox where a built ``.app``
    never exists.
    """

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
    """macOS-only: the REAL ``ps`` parent-chain walk against the built ``.app``.

    The runtime host-bundle resolver (``resolve_host_bundle_id``) walks
    the real process tree from the backend to the nearest ``.app`` and
    reads its Info.plist. On CI, the real walk must resolve the
    JUST-BUILT ``<productName>.app`` end-to-end: launch a real child
    process whose executable lives inside the built bundle, run the
    real ``ps`` walk from its pid, and require the resolved identifier
    to match the configs. This is the post-build integration leg of
    the macOS workflow (executed by the step added to
    ``tauri-macos-build.yml`` after ``cargo tauri build``).
    """

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
        # of /bin/sleep (a shebang script would report the interpreter
        # path in ps comm, not the bundle path — same premise as the
        # backend-spawned sidecars the resolver actually sees).
        macos_dir = root / "Contents" / "MacOS"
        probe = macos_dir / "__ci_probe_sleep"
        shutil.copy2("/bin/sleep", probe)
        probe.chmod(0o755)
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen([str(probe), "30"])
            # Premise: the live process's comm must resolve to the built
            # bundle (robust to /var -> /private/var canonicalisation).
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
