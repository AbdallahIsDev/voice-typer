"""Drift guards for the Tauri config ↔ build-script pairs."""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

# ``tomllib`` is stdlib only on Python >= 3.11; the 3.10 matrix legs
if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover, Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]
    except ImportError:  # pragma: no cover, tomli not in the lock
        pytest.skip(
            "tomli backport not installed on Python 3.10, skipping drift check",
            allow_module_level=True,
        )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = PROJECT_ROOT / "src-tauri"
STUB_SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"
MANIFEST_PATH = PROJECT_ROOT / "tauri-binaries.json"
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "update_tauri_manifests.py"

TRIPLE_TO_MANIFEST_KEY: dict[str, str] = {
    "x86_64-pc-windows-msvc": "windows-x86_64",
    "aarch64-pc-windows-msvc": "windows-aarch64",
    "x86_64-apple-darwin": "macos",
    "aarch64-apple-darwin": "macos",
    "x86_64-unknown-linux-gnu": "linux-x86_64",
    "aarch64-unknown-linux-gnu": "linux-aarch64",
}


@functools.lru_cache(maxsize=1)
def _stub_module():
    """Load ``scripts/gen_tauri_icons_stub.py`` as a module (no side effects)."""
    spec = importlib.util.spec_from_file_location("_vt_gen_tauri_icons_stub_drift", STUB_SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {STUB_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _tauri_conf() -> dict:
    """Load ``src-tauri/tauri.conf.json`` once per process."""
    return json.loads((SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _manifest() -> dict:
    """Load ``tauri-binaries.json`` once per process."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _updater_module():
    """Load ``scripts/build/update_tauri_manifests.py`` as a module."""
    spec = importlib.util.spec_from_file_location("_vt_update_tauri_manifests_drift", UPDATE_SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {UPDATE_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBundleBinariesVsStubRegistry:
    """``tauri.conf.json`` binary declarations ↔ the stub generator's registry."""

    def test_config_declares_exactly_the_stub_generator_registry(self) -> None:
        """The config's declared binaries must equal the generator's registry."""
        stub = _stub_module()
        registry = {p.relative_to(SRC_TAURI).as_posix() for p in stub._all_stub_paths()}
        bundle = _tauri_conf()["bundle"]
        sidecars = {
            f"{base}-{triple}{'.exe' if triple in stub.WINDOWS_TRIPLES else ''}"
            for base in bundle.get("externalBin", [])
            for triple in stub.SIDECAR_TRIPLES
        }
        binaries = {r for r in bundle.get("resources", []) if r.startswith("resources/native/")}
        declared = sidecars | binaries

        missing = declared - registry
        assert not missing, (
            "binaries declared in tauri.conf.json (externalBin per-triple + "
            "bundle.resources native/prewarm) but NOT in the stub generator's "
            "registry, missing on a clean CI checkout, cargo tauri build "
            "fails:\n  " + "\n  ".join(sorted(missing))
        )
        dead = registry - declared
        assert not dead, (
            "binaries the stub generator creates but tauri.conf.json never "
            "declares (dead files, never bundled):\n  " + "\n  ".join(sorted(dead))
        )


class TestTauriBinariesManifestCoverage:
    """``tauri-binaries.json`` sha256 keys ↔ the canonical triple set."""

    def test_manifest_covers_exactly_the_canonical_triples(self) -> None:
        """The manifest's per-arch keys must equal the triples' derived keys."""
        stub = _stub_module()
        expected = {TRIPLE_TO_MANIFEST_KEY[t] for t in stub.SIDECAR_TRIPLES}
        manifest = _manifest()
        actual = {k for entry in manifest["binaries"].values() for k in entry["sha256"]}

        missing = expected - actual
        assert not missing, (
            "tauri-binaries.json is missing per-arch sha256 keys for "
            "canonical build triples: " + ", ".join(sorted(missing)) + ". "
            "Every SIDECAR_TRIPLES entry must be covered (macOS collapses "
            "both darwin triples into 'macos')."
        )
        extra = actual - expected
        assert not extra, (
            "tauri-binaries.json declares per-arch sha256 keys with no "
            "matching canonical build triple: " + ", ".join(sorted(extra)) + ". "
            "Either add the triple to SIDECAR_TRIPLES or drop the key."
        )

    def test_updater_triple_map_matches_canonical_triples(self) -> None:
        """``update_tauri_manifests.py`` must hash into the SAME keys CI reads."""
        updater = _updater_module()
        stub = _stub_module()
        for triple in stub.SIDECAR_TRIPLES:
            expected = TRIPLE_TO_MANIFEST_KEY[triple]
            actual = updater.TRIPLE_TO_MANIFEST_KEY[triple]
            assert actual == expected, (
                f"update_tauri_manifests.py maps {triple!r} to {actual!r} but "
                f"the canonical mapping (tests/tauri/test_config_script_drift.py "
                f"TRIPLE_TO_MANIFEST_KEY) says {expected!r}, CI would record "
                "hashes under the wrong manifest key."
            )
        # Every key the manifest declares must be reachable by some triple
        manifest_keys = {k for entry in _manifest()["binaries"].values() for k in entry["sha256"]}
        reachable = set(updater.TRIPLE_TO_MANIFEST_KEY.values())
        unreachable = manifest_keys - reachable
        assert not unreachable, (
            "update_tauri_manifests.py cannot write the manifest key(s): "
            + ", ".join(sorted(unreachable))
            + ", add the owning triple to "
            "TRIPLE_TO_MANIFEST_KEY."
        )


class TestTauriBinariesManifestBinaryNames:
    """``tauri-binaries.json`` binary keys ↔ the Cargo package/bin name."""

    def test_manifest_binary_names_match_cargo_binary_name(self) -> None:
        """Every manifest binary key's base must equal the Cargo binary name."""
        cargo = tomllib.loads((SRC_TAURI / "Cargo.toml").read_text(encoding="utf-8"))
        cargo_name = cargo["package"]["name"]
        manifest = _manifest()
        keys = set(manifest["binaries"].keys())

        # The canonical per-platform trio, derived from the Cargo name.
        expected_keys = {cargo_name, cargo_name + ".exe", cargo_name + ".app"}
        assert expected_keys <= keys, (
            "tauri-binaries.json must declare the per-platform trio derived "
            f"from the Cargo binary name {cargo_name!r}: "
            f"{sorted(expected_keys)}; got {sorted(keys)}. Renaming the Cargo "
            "binary requires updating the manifest (and the autostart "
            "launcher's per-OS discovery)."
        )
        # No key may drift from the Cargo name (e.g. an old name left behind).
        for key in keys:
            base = key[:-4] if key.endswith((".exe", ".app")) else key
            assert base == cargo_name, (
                f"tauri-binaries.json key {key!r} has base {base!r} != Cargo "
                f"binary name {cargo_name!r}, stale entry from a renamed "
                "binary."
            )


BUILD_SCRIPTS = [
    "scripts/build/build_sidecar_windows.sh",
    "scripts/build/build_sidecar_linux.sh",
    "scripts/build/build_sidecar_macos.sh",
    "scripts/build/build_prewarm_windows.sh",
    "scripts/build/build_prewarm_linux.sh",
    "scripts/build/build_prewarm_macos.sh",
]
WINDOWS_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "tauri-windows-build.yml"

# Package-data files the frozen server reads at import time / runtime via
IMPORT_TIME_DATA_FILES = [
    "voice_typer/server/hotkey_reserved.json",
    "voice_typer/server/corrections.json",
    "voice_typer/server/model_hashes.json",
    "voice_typer/server/native/binaries.json",
    "voice_typer/server/silero_vad.onnx",
]


class TestNuitkaBuildsIncludeVoiceTyperPackageData:
    """Every Nuitka invocation bundling ``voice_typer`` must also include"""

    def test_import_time_data_files_exist_and_are_not_python(self) -> None:
        """The data files the flag must carry actually exist as data files."""
        for rel in IMPORT_TIME_DATA_FILES:
            path = PROJECT_ROOT / rel
            assert path.is_file(), f"{rel} missing, is the file tracked?"
            assert path.suffix != ".py", f"{rel} is not a data file"

    def test_every_build_script_has_the_package_data_flag_after_voice_typer(
        self,
    ) -> None:
        """All six build scripts pair the data flag with the package include."""
        flag = "--include-package-data=voice_typer.server"
        for rel in BUILD_SCRIPTS:
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            assert flag in text, (
                f"{rel} must pass {flag}, the frozen exe crashes on launch "
                "(FileNotFoundError: hotkey_reserved.json) without it, even "
                "though it builds fine."
            )
            pkg_idx = text.index("--include-package=voice_typer")
            data_idx = text.index(flag)
            assert data_idx > pkg_idx, (
                f"{rel}: {flag} must follow --include-package=voice_typer in the same Nuitka invocation."
            )

    def test_windows_workflow_every_nuitka_invocation_has_the_flag(self) -> None:
        """Both inline Nuitka commands (sidecar + prewarm) carry the flag."""
        text = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
        flag = "--include-package-data=voice_typer.server"
        nuitka_count = text.count("-m nuitka")
        flag_count = text.count(flag)
        assert flag_count >= nuitka_count, (
            f"tauri-windows-build.yml has {nuitka_count} Nuitka invocations "
            f"but only {flag_count} --include-package-data=voice_typer.server "
            "— every Nuitka command bundling voice_typer needs the flag."
        )


SIDECAR_SCRIPTS = [
    "scripts/build/build_sidecar_windows.sh",
    "scripts/build/build_sidecar_linux.sh",
    "scripts/build/build_sidecar_macos.sh",
]

WORKER_SCRIPTS = [
    "scripts/build/build_worker_windows.sh",
    "scripts/build/build_worker_linux.sh",
    "scripts/build/build_worker_macos.sh",
]

PREWARM_SCRIPTS = [
    "scripts/build/build_prewarm_windows.sh",
    "scripts/build/build_prewarm_linux.sh",
    "scripts/build/build_prewarm_macos.sh",
]

# Every Nuitka build script that freezes ``voice_typer``. The torch-free
TORCH_FREE_SCRIPTS = SIDECAR_SCRIPTS + WORKER_SCRIPTS + PREWARM_SCRIPTS

PYINSTALLER_SPEC = PROJECT_ROOT / "scripts" / "build" / "voice-typer.spec"


class TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed:
    """
    Nuitka build scripts must stay torch-free (Phase 1c retirement of C-CI-8/NU-106).
    Sanctioned C-CI-8 retirement, why the old contract is gone: torch is
    """

    def test_no_sidecar_build_excludes_unconditionally_imported_torch_modules(
        self,
    ) -> None:
        """No build script may carry any torch Nuitka flag."""
        forbidden = [
            "torch-disable-jit",
            "nofollow-import-to=torch",
        ]
        for rel in TORCH_FREE_SCRIPTS:
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            for flag in forbidden:
                assert flag not in text, (
                    f"{rel} must NOT contain {flag!r}: the runtime is "
                    "ONNX-only (Phase 1c, zero `import torch` sites under "
                    "voice_typer/), torch Nuitka flags are obsolete dead "
                    "weight and risk re-pulling torch into the bundle "
                    "that check_bundle_torch_free.sh forbids."
                )

    def test_no_build_script_carries_torch_jit_flag(self) -> None:
        """All nine builds must be free of the torch JIT module-parameter."""
        required_absent = "torch-disable-jit"
        for rel in TORCH_FREE_SCRIPTS:
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            assert required_absent not in text, (
                f"{rel} must NOT pass {required_absent}: vad.py loads "
                "silero_vad.onnx via onnxruntime, there is no "
                "torch.jit.load left to protect (Phase 1c retirement of "
                "the NU-106 keep-JIT guard)."
            )

    def test_spec_bundles_onnx_not_jit(self) -> None:
        """The PyInstaller fallback spec must bundle ``silero_vad.onnx``."""
        text = PYINSTALLER_SPEC.read_text(encoding="utf-8")
        assert "silero_vad.onnx" in text, (
            "scripts/build/voice-typer.spec must reference silero_vad.onnx (the ORT-loaded VAD model)."
        )
        assert "silero_vad.jit" not in text, (
            "scripts/build/voice-typer.spec must NOT reference "
            "silero_vad.jit (legacy torch JIT model, forbidden by the "
            "Phase 1c torch-free gate)."
        )


class TestTauriNsisInstallerHooks:
    """``bundle.windows.nsis.installerHooks`` must point at an NSIS script."""

    def test_installer_hooks_points_at_nsh_not_bat(self) -> None:
        """Every hook entry must be an ``.nsh`` file that exists on disk."""
        nsis = _tauri_conf()["bundle"]["windows"]["nsis"]
        hooks = nsis.get("installerHooks", [])
        if isinstance(hooks, str):
            hooks = [hooks]
        assert hooks, "bundle.windows.nsis.installerHooks must be set (uninstall cleanup)."
        for hook in hooks:
            assert hook.endswith(".nsh"), (
                f"nsis.installerHooks entry {hook!r} must be an NSIS script "
                "(.nsh), Tauri !includes it into installer.nsi, and a batch "
                "file aborts makensis with 'Invalid command: @echo' on every "
                "cargo tauri build."
            )
            target = (SRC_TAURI / hook).resolve()
            assert target.is_file(), f"nsis.installerHooks entry {hook!r} resolves to {target} which does not exist."

    def test_nsis_installer_icon_is_app_logo(self) -> None:
        """NSIS setup/uninstaller PE icons must be the app logo, not NSIS stock.

        Without installerIcon, tauri-bundler leaves the stock NSIS installer
        icon on ``*-setup.exe`` (generic monitor/download glyph in Explorer).
        """
        nsis = _tauri_conf()["bundle"]["windows"]["nsis"]
        for key in ("installerIcon", "uninstallerIcon"):
            rel = nsis.get(key)
            assert rel, (
                f"bundle.windows.nsis.{key} must be set to icons/icon.ico; "
                "unset means NSIS stock installer icon on the setup.exe."
            )
            target = (SRC_TAURI / rel).resolve()
            assert target.is_file(), f"nsis.{key} {rel!r} resolves to missing {target}"
            assert target.suffix.lower() == ".ico", f"nsis.{key} must be a .ico, got {rel!r}"


def _path_components(template: str) -> tuple[str, ...]:
    """Split a path template into components (both separators normalized)."""
    return tuple(template.replace("\\", "/").split("/"))


def _launcher_path_templates() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Expand the launcher's install-path table to manifest-comparable form."""
    # ``mock_heavy_imports`` fixture must be active so the launcher
    import voice_typer.server.autostart_launcher as launcher
    from voice_typer.server.branding import APP_NAME

    expanded: dict[str, tuple[tuple[str, ...], ...]] = {}
    for platform, templates in launcher._TAURI_LAUNCHER_INSTALL_PATHS.items():
        normalized = tuple(_path_components(t.replace("{APP}", APP_NAME).replace("{HOME}", "~")) for t in templates)
        expanded[platform] = normalized
    return expanded


def _manifest_install_path_templates() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Group the manifest's ``_install_paths`` by OS platform key."""
    out: dict[str, tuple[tuple[str, ...], ...]] = {}
    for key, entry in _manifest()["binaries"].items():
        if key.endswith(".exe"):
            platform = "windows"
        elif key.endswith(".app"):
            platform = "macos"
        else:
            platform = "linux"
        out[platform] = tuple(_path_components(p) for p in entry["_install_paths"])
    return out


class TestLauncherInstallPathsMatchManifest:
    """``autostart_launcher`` discovery ↔ ``tauri-binaries.json`` ``_install_paths``."""

    def test_launcher_candidates_exactly_match_manifest_paths(self) -> None:
        """The launcher's per-OS candidate lists must EQUAL the manifest's."""
        launcher_side = _launcher_path_templates()
        manifest_side = _manifest_install_path_templates()

        assert set(launcher_side) == set(manifest_side), (
            "launcher platform keys mismatch manifest platforms: "
            f"launcher={sorted(launcher_side)} manifest={sorted(manifest_side)}"
        )
        for platform in sorted(launcher_side):
            assert launcher_side[platform] == manifest_side[platform], (
                f"autostart launcher {platform} discovery paths differ from "
                "tauri-binaries.json _install_paths (order matters):\n"
                f"  launcher: {launcher_side[platform]}\n"
                f"  manifest: {manifest_side[platform]}\n"
                "Update BOTH sides in lockstep."
            )


# Per-arch config file → (platform, build triples it serves). The test
PER_ARCH_CONFIGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "tauri.windows-x86_64.conf.json": ("windows", ("x86_64-pc-windows-msvc",)),
    "tauri.windows-aarch64.conf.json": ("windows", ("aarch64-pc-windows-msvc",)),
    "tauri.macos.conf.json": (
        "macos",
        ("x86_64-apple-darwin", "aarch64-apple-darwin"),
    ),
    "tauri.linux-x86_64.conf.json": ("linux", ("x86_64-unknown-linux-gnu",)),
    "tauri.linux-aarch64.conf.json": ("linux", ("aarch64-unknown-linux-gnu",)),
}


def _resource_relevant(resource: str, platform: str, triples: set[str]) -> bool:
    """True if ``resource`` (from the base config) matters to the platform."""
    name = resource.split("/")[-1]
    if resource == "icons/tray/":
        return True
    if resource.startswith("resources/linux-scripts/"):
        return platform == "linux"
    if name.startswith("linux-key-listener"):
        return platform == "linux"
    if name.startswith("macos-key-listener"):
        return platform == "macos"
    if name.startswith("windows-key-listener"):
        return platform == "windows"
    if name.startswith("prewarm-"):
        triple = name[len("prewarm-") :].removesuffix(".exe")
        return triple in triples
    return False  # base entries with no platform affinity (none today)


def _per_arch_configs_on_disk() -> set[str]:
    # ``tauri.dev.conf.json`` is the dev-mode override (C-TDEV-1: blanks
    return {
        p.name for p in SRC_TAURI.glob("tauri.*.conf.json") if p.name not in ("tauri.conf.json", "tauri.dev.conf.json")
    }


class TestPerArchConfigsStayLockedToBase:
    """Per-arch ``--config`` overrides ↔ the base ``tauri.conf.json``."""

    def test_no_unregistered_per_arch_config_files(self) -> None:
        """Every per-arch config on disk must be registered in this test."""
        on_disk = _per_arch_configs_on_disk()
        registered = set(PER_ARCH_CONFIGS)
        assert on_disk == registered, (
            "per-arch config files on disk != those registered in "
            "PER_ARCH_CONFIGS (tests/tauri/test_config_script_drift.py):\n"
            f"  unregistered on disk: {sorted(on_disk - registered)}\n"
            f"  registered but missing: {sorted(registered - on_disk)}"
        )

    def test_per_arch_resources_subset_of_base(self) -> None:
        """Overrides may only narrow the base resources (replace semantics)."""
        base_resources = set(_tauri_conf()["bundle"]["resources"])
        for rel, (platform, _triples) in PER_ARCH_CONFIGS.items():
            cfg = json.loads((SRC_TAURI / rel).read_text(encoding="utf-8"))
            cfg_resources = set(cfg["bundle"].get("resources", []))
            invalid = cfg_resources - base_resources
            assert not invalid, (
                f"{rel} ({platform}) declares resources never present in the "
                "base tauri.conf.json bundle.resources (the base is the "
                "cross-platform superset):\n  " + "\n  ".join(sorted(invalid))
            )

    def test_per_arch_config_keeps_platform_relevant_base_resources(self) -> None:
        """Every base resource relevant to the platform must be kept."""
        base_resources = _tauri_conf()["bundle"]["resources"]
        for rel, (platform, triples) in PER_ARCH_CONFIGS.items():
            cfg = json.loads((SRC_TAURI / rel).read_text(encoding="utf-8"))
            cfg_resources = set(cfg["bundle"].get("resources", []))
            triples_set = set(triples)
            dropped = {
                res
                for res in base_resources
                if _resource_relevant(res, platform, triples_set) and res not in cfg_resources
            }
            assert not dropped, (
                f"{rel} ({platform}) drops base resources that this "
                f"platform/triples ship (Tauri replaces the resources array):\n  " + "\n  ".join(sorted(dropped))
            )

    def test_per_arch_external_bin_never_changes_sidecar(self) -> None:
        """Overrides may not alter or drop the ``externalBin`` sidecar list."""
        base_external_bin = _tauri_conf()["bundle"].get("externalBin", [])
        for rel, (platform, _triples) in PER_ARCH_CONFIGS.items():
            cfg = json.loads((SRC_TAURI / rel).read_text(encoding="utf-8"))
            cfg_external_bin = cfg["bundle"].get("externalBin", base_external_bin)
            assert cfg_external_bin == base_external_bin, (
                f"{rel} ({platform}) overrides bundle.externalBin: "
                f"{cfg_external_bin} != base {base_external_bin}, dropping "
                "bin/python-sidecar silently unbundles the ASR sidecar for "
                "that platform."
            )

    def test_per_arch_main_window_keeps_create_false(self) -> None:
        """Overrides declaring ``app.windows`` must keep ``main`` manual."""
        base_windows = {w["label"]: w for w in _tauri_conf()["app"]["windows"]}
        assert base_windows["main"].get("create") is False
        for rel, (platform, _triples) in PER_ARCH_CONFIGS.items():
            cfg = json.loads((SRC_TAURI / rel).read_text(encoding="utf-8"))
            windows = {w["label"]: w for w in cfg.get("app", {}).get("windows", [])}
            if "main" not in windows:
                continue  # inherits the base array untouched (linux confs)
            assert windows["main"].get("create") is False, (
                f"{rel} ({platform}) declares a main window without "
                '"create": false: the framework auto-creates it and '
                "bootstrap_main_window builds it again → "
                'WebviewLabelAlreadyExists("main") at startup.'
            )


def _configs_declaring_bubble() -> list[tuple[str, dict]]:
    """Every Tauri config on disk that declares a ``bubble`` window."""
    found: list[tuple[str, dict]] = []
    for path in sorted(SRC_TAURI.glob("tauri*.conf.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        windows = cfg.get("app", {}).get("windows") or []
        if any(w.get("label") == "bubble" for w in windows):
            found.append((path.name, cfg))
    return found


class TestBubbleWindowLoadsBubbleHtml:
    """The bubble window must load ``bubble.html``, not ``index.html``."""

    def test_base_config_bubble_url_is_bubble_html(self) -> None:
        windows = {w["label"]: w for w in _tauri_conf()["app"]["windows"]}
        assert "bubble" in windows, "tauri.conf.json must declare a bubble window"
        assert windows["bubble"].get("url") == "bubble.html", (
            'tauri.conf.json bubble window must set "url": "bubble.html"; '
            "without it Tauri defaults to index.html and the bubble frame "
            "renders the MAIN app instead of the pill overlay"
        )

    def test_every_config_declaring_bubble_sets_bubble_html(self) -> None:
        found = _configs_declaring_bubble()
        assert found, "expected at least one Tauri config declaring a bubble window"
        for name, cfg in found:
            windows = {w["label"]: w for w in cfg.get("app", {}).get("windows", [])}
            bubble = windows["bubble"]
            assert bubble.get("url") == "bubble.html", (
                f"{name}: bubble window url is {bubble.get('url')!r}, "
                'must be "bubble.html" (Tauri v2 defaults a missing url to '
                "index.html, which renders the main dashboard inside the "
                "bubble frame)"
            )

    def test_main_window_url_stays_default(self) -> None:
        """The main window must keep the default index.html (no explicit url)."""
        windows = {w["label"]: w for w in _tauri_conf()["app"]["windows"]}
        assert windows["main"].get("url") in (None, "index.html"), "main window must not point at bubble.html"

    def test_renderer_build_emits_bubble_html(self) -> None:
        """``vite.tauri.config.ts`` must emit bubble.html as a page input."""
        src = (PROJECT_ROOT / "voice_typer" / "client" / "vite.tauri.config.ts").read_text(encoding="utf-8")
        assert "bubble.html" in src, (
            "vite.tauri.config.ts must list bubble.html as a rollup "
            "input so out/renderer/bubble.html exists for the Tauri "
            "bubble window url"
        )


class TestBubbleWindowHasNoShadow:
    """The bubble window must ship with ``shadow: false``."""

    def test_base_config_bubble_shadow_is_false(self) -> None:
        windows = {w["label"]: w for w in _tauri_conf()["app"]["windows"]}
        assert "bubble" in windows, "tauri.conf.json must declare a bubble window"
        assert windows["bubble"].get("shadow") is False, (
            'tauri.conf.json bubble window must set "shadow": false; '
            "Tauri v2 defaults shadow to true, and on an undecorated "
            "Windows window that paints a 1px white border around the pill"
        )

    def test_every_config_declaring_bubble_sets_shadow_false(self) -> None:
        found = _configs_declaring_bubble()
        assert found, "expected at least one Tauri config declaring a bubble window"
        for name, cfg in found:
            windows = {w["label"]: w for w in cfg.get("app", {}).get("windows", [])}
            bubble = windows["bubble"]
            assert bubble.get("shadow") is False, (
                f"{name}: bubble window shadow is {bubble.get('shadow')!r}, "
                "must be false (Tauri v2 default true paints a 1px white "
                "border on an undecorated Windows window)"
            )

    def test_main_window_shadow_stays_default(self) -> None:
        """Decorated main windows keep the default shadow (always-on on Windows)."""
        windows = {w["label"]: w for w in _tauri_conf()["app"]["windows"]}
        assert windows["main"].get("shadow") is not False, "main window is decorated; do not disable its shadow"

    def test_show_bubble_window_does_not_call_set_focus(self) -> None:
        """Focus-steal audit (secondary): the show path must not grab focus."""
        src = (SRC_TAURI / "src" / "commands" / "bubble" / "window.rs").read_text(encoding="utf-8")
        # Isolate the show function body.
        start = src.find("pub(crate) fn show_bubble_window")
        assert start >= 0, "show_bubble_window must exist"
        end = src.find("pub(crate) fn hide_bubble_window", start)
        body = src[start:end] if end > 0 else src[start:]
        assert "set_focus" not in body, (
            "show_bubble_window must not call set_focus: the dictation "
            "target is the user's text field, not the bubble overlay"
        )


# The files that MUST carry the identical version string, with a reader
VERSIONED_FILES: dict[str, Path] = {
    "pyproject.toml": PROJECT_ROOT / "pyproject.toml",
    "voice_typer/client/package.json": PROJECT_ROOT / "voice_typer" / "client" / "package.json",
    "src-tauri/tauri.conf.json": SRC_TAURI / "tauri.conf.json",
    "src-tauri/Cargo.toml": SRC_TAURI / "Cargo.toml",
}


def _read_json_version(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _read_pyproject_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["project"]["version"]


def _is_git_tracked(path: Path) -> bool:
    """True if ``path`` is committed (feed artifacts must be pinned in git,"""
    result = __import__("subprocess").run(
        ["git", "ls-files", "--error-unmatch", path.relative_to(PROJECT_ROOT).as_posix()],
        capture_output=True,
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode == 0


def _read_cargo_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["package"]["version"]


class TestVersionLockstep:
    """One version across pyproject / package.json / Tauri host / installer."""

    def test_all_versioned_files_agree(self) -> None:
        """Every versioned file must carry the pyproject.toml version."""
        source = _read_pyproject_version(VERSIONED_FILES["pyproject.toml"])
        readers = {
            "pyproject.toml": _read_pyproject_version,
            "voice_typer/client/package.json": _read_json_version,
            "src-tauri/tauri.conf.json": _read_json_version,
            "src-tauri/Cargo.toml": _read_cargo_version,
        }
        for rel, reader in readers.items():
            actual = reader(VERSIONED_FILES[rel])
            assert actual == source, (
                f"{rel} version is {actual!r} but pyproject.toml says "
                f"{source!r}, bump via `python scripts/build/sync_versions.py "
                "--apply` (bump pyproject.toml first), never by hand-editing "
                "one file."
            )


RELEASING_MD = PROJECT_ROOT / "RELEASING.md"
SYNC_VERSIONS_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "sync_versions.py"

# The files a release bump MUST land in the SAME commit (the different
BUMP_COMMIT_FILES = (
    "voice_typer/client/package.json",
    "src-tauri/Cargo.toml",
    "src-tauri/tauri.conf.json",
)


class TestReleaseBumpWorkflow:
    """The documented bump flow must touch every versioned file at once."""

    def test_releasing_md_bump_commit_adds_all_versioned_files(self) -> None:
        """The release-bump ``git add`` must cover every versioned file."""
        text = RELEASING_MD.read_text(encoding="utf-8")
        assert "sync_versions.py --apply" in text, (
            "RELEASING.md must instruct `python scripts/build/sync_versions.py --apply` as the release-bump step."
        )
        add_line = next(
            (line for line in text.splitlines() if line.strip().startswith("git add ")),
            None,
        )
        assert add_line is not None, "RELEASING.md must document the bump `git add` command"
        missing = [rel for rel in BUMP_COMMIT_FILES if rel not in add_line]
        assert not missing, (
            "RELEASING.md's release-bump `git add` line is missing versioned "
            f"file(s): {missing}. The bump must commit package.json + "
            "src-tauri/tauri.conf.json + src-tauri/Cargo.toml in the SAME "
            "commit so the version lockstep (Pair 10) can't break mid-release.\n"
            f"  line: {add_line.strip()}"
        )

    def test_sync_versions_writes_every_versioned_file(self) -> None:
        """sync_versions.py must propagate to all four write targets."""
        spec = importlib.util.spec_from_file_location("_vt_sync_versions_drift", SYNC_VERSIONS_SCRIPT)
        assert spec is not None and spec.loader is not None, f"cannot load {SYNC_VERSIONS_SCRIPT}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The script's module constants are Paths; assert each release-bump
        expected_targets = {
            "PACKAGE_JSON": VERSIONED_FILES["voice_typer/client/package.json"],
            "TAURI_CONF_JSON": VERSIONED_FILES["src-tauri/tauri.conf.json"],
            "CARGO_TOML": VERSIONED_FILES["src-tauri/Cargo.toml"],
        }
        for const, expected in expected_targets.items():
            actual = getattr(module, const)
            assert actual == expected, (
                f"sync_versions.py::{const} is {actual} but must be {expected} "
                "(release-bump drift, a bump that runs --apply would NOT "
                "touch the expected file)."
            )
        versions = module.collect_versions()
        for rel in ("voice_typer/client/package.json", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml"):
            assert versions.get(rel) == versions["pyproject.toml"], (
                f"sync_versions.py collect_versions() drifts on {rel}, the "
                "release-bump --apply would need to resync it."
            )


class TestUpdateFeedParity:
    """Any committed update feed must carry tauri.conf.json's version."""

    FEED_PATTERNS = ("**/latest.json", "**/latest.yml")

    def test_committed_feed_manifests_match_tauri_conf_version(self) -> None:
        """Every git-tracked feed manifest's version == tauri.conf.json's."""
        expected = _read_json_version(VERSIONED_FILES["src-tauri/tauri.conf.json"])
        feeds = [path for pattern in self.FEED_PATTERNS for path in PROJECT_ROOT.glob(pattern) if _is_git_tracked(path)]
        for feed in feeds:
            data = json.loads(feed.read_text(encoding="utf-8"))
            feed_version = data.get("version")
            assert feed_version == expected, (
                f"{feed.relative_to(PROJECT_ROOT)} references version "
                f"{feed_version!r} but src-tauri/tauri.conf.json is "
                f"{expected!r}, the update feed must reference the app's "
                "own version (sync_versions.py --apply then regenerate the "
                "feed artifact)."
            )

    def test_no_unlicensed_update_feed_wiring_ships(self) -> None:
        """ADR-0020 §15: no Tauri updater plugin wiring in tauri.conf.json."""
        conf = VERSIONED_FILES["src-tauri/tauri.conf.json"].read_text(encoding="utf-8")
        assert "plugins" not in conf or "updater" not in conf, (
            "tauri.conf.json must NOT wire plugins.updater "
            "(ADR-0020 §15. NO auto-update). If a feed is being wired, "
            "add the plugin AND keep the feed-version parity guard green."
        )
        for name in sorted(
            [SRC_TAURI / "tauri.conf.json", *_per_arch_configs_on_disk()],
            key=lambda p: p if isinstance(p, str) else str(p),
        ):
            data = json.loads((SRC_TAURI / name).read_text(encoding="utf-8"))
            updater = data.get("plugins", {}).get("updater")
            assert updater is None, (
                f"{name} configures plugins.updater ({updater!r}) but the "
                "update feed wiring is pinned to NO auto-update (ADR-0020 "
                "§15), enable it deliberately and keep the parity guard."
            )


class TestReverseDnsIdentifierNamespace:
    """
    canonical ``com.voicetyper.*`` reverse-DNS namespace.
    INTENTIONAL and must NOT be renamed (do not extend without
    """

    def test_windows_autostart_and_prewarm_identifiers_are_reverse_dns(self) -> None:
        """Source pins for the Windows identifier literals (active names)."""
        pins = {
            "voice_typer/server/server_platform/autostart.py": [
                '_APP_AUTOSTART_TASK_NAME = f"com.voicetyper.autostart{_install_hash_suffix()}"',
            ],
            "voice_typer/server/server_platform/autostart_windows.py": [
                'return f"com.voicetyper.autostart_{_autostart_mod._install_hash()}"',
                'return f"com.voicetyper.autostart{_autostart_mod._install_hash_suffix()}.bat"',
            ],
            "voice_typer/server/server_platform/_autostart_windows_runkey.py": [
                'name.startswith(("VoiceTyper", "com.voicetyper"))',
            ],
            "voice_typer/server/server_platform/_autostart_windows_uninstall.py": [
                "\"Get-ScheduledTask -TaskName 'VoiceTyper*','com.voicetyper*' \"",
                'name.startswith(("VoiceTyper", "com.voicetyper"))',
            ],
            "voice_typer/server/server_platform/_autostart_windows_sweep.py": [
                'f"autostart-sweep-v2-{_autostart_mod._install_hash()}.done"',
            ],
        }
        for rel, expected in pins.items():
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            for pin in expected:
                assert pin in text, (
                    f"{rel} drifted from the canonical com.voicetyper.* "
                    f"namespace, expected the literal {pin!r}. Windows "
                    "autostart/prewarm identifiers must be reverse-DNS "
                    "(com.voicetyper.*), never the bare VoiceTyper* forms "
                    "(legacy forms allowed ONLY via the allowlisted legacy "
                    "constants and cleanup sweeps)."
                )

    def test_posix_labels_and_keyring_service_name_are_reverse_dns(self) -> None:
        """macOS LaunchAgent labels + keyring service name stay reverse-DNS."""
        pins = {
            "voice_typer/server/server_platform/autostart_macos.py": "<string>com.voicetyper</string>",
            "voice_typer/server/credential_store/_schema.py": 'KEYRING_SERVICE_NAME = "com.voicetyper.keyring"',
        }
        for rel, pin in pins.items():
            assert pin in (PROJECT_ROOT / rel).read_text(encoding="utf-8"), (
                f"{rel} drifted from the canonical com.voicetyper.* namespace, expected the literal {pin!r}."
            )

    def test_polkit_action_stays_reverse_dns(self) -> None:
        """The install-permissions polkit action is com.voicetyper.* (both"""
        for rel in (
            "voice_typer/server/handlers/system_handlers.py",
            "scripts/linux/install_permissions.py",
        ):
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            assert "com.voicetyper.install-permissions" in text, (
                f"{rel} must reference the polkit action "
                "'com.voicetyper.install-permissions' (reverse-DNS). A bare "
                "action name drifts the permission gate from the canonical "
                "namespace."
            )

    def test_legacy_keyring_names_pinned(self) -> None:
        """orphaned)."""
        text = (PROJECT_ROOT / "voice_typer/server/credential_store/_schema.py").read_text(encoding="utf-8")
        assert '_LEGACY_KEYRING_SERVICE_NAMES: tuple[str, ...] = ("app.voicetyper", "voice-typer")' in text, (
            "credential_store/_schema.py legacy keyring service names drifted, they "
            "are pinned migration sources (allowlisted)."
        )

    def test_single_instance_mutex_keeps_its_bare_name(self) -> None:
        """AGENTS.md boundary: the mutex is an internal OS/API"""
        text = (PROJECT_ROOT / "voice_typer/server/single_instance.py").read_text(encoding="utf-8")
        assert "VoiceTyperSingleInstance" in text, (
            "single_instance.py must keep the 'VoiceTyperSingleInstance' "
            "mutex name (internal OS/API identifier, AGENTS.md "
            "explicitly permits bare internal identifiers)."
        )
        assert "com.voicetyper" not in text, (
            "single_instance.py must NOT use the reverse-DNS namespace for "
            "the mutex, it is a pinned internal OS/API identifier."
        )

    def test_sweep_marker_stays_version_scoped(self) -> None:
        """The once-per-install legacy-sweep marker must be version-scoped"""
        text = (PROJECT_ROOT / "voice_typer/server/server_platform/_autostart_windows_sweep.py").read_text(
            encoding="utf-8"
        )
        assert 'f"autostart-sweep-v2-{_autostart_mod._install_hash()}.done"' in text, (
            "legacy-sweep marker name drifted: must stay "
            "autostart-sweep-v2-<hash>.done (version-scoped so installs "
            "carrying the v1 marker re-sweep once after the namespace "
            "rename)."
        )


def test_persisted_position_bound_matches_server_allowlist():
    """server's ``bubble_x``/``bubble_y`` allowlist bounds."""
    allowlist = (PROJECT_ROOT / "voice_typer/server/config_validators/allowlist.py").read_text(encoding="utf-8")
    bounds = re.findall(
        r'"bubble_[xy]": \(\(int, type\(None\)\), '
        r"_make_optional_int_validator\(lo=(-?[\d_]+), hi=([\d_]+)\)\)",
        allowlist,
    )
    assert len(bounds) == 2, (
        "allowlist.py bubble_x/bubble_y validator signature drifted, "
        "update this pin together with persisted_position.rs."
    )
    lo, hi = bounds[0]
    lo, hi = lo.replace("_", ""), hi.replace("_", "")
    assert lo == "-100000" and hi == "100000", (
        f"server bubble coordinate bounds changed to [{lo}, {hi}], "
        "update PERSISTED_COORDINATE_LIMIT in "
        "src-tauri/src/commands/bubble/persisted_position.rs to match."
    )

    rust = (SRC_TAURI / "src/commands/bubble/persisted_position.rs").read_text(encoding="utf-8")
    assert "const PERSISTED_COORDINATE_LIMIT: i32 = 100_000;" in rust, (
        "persisted_position.rs PERSISTED_COORDINATE_LIMIT drifted from "
        "the server allowlist bound (±100000), keep them in lockstep."
    )
