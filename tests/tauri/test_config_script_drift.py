"""Drift guards for the Tauri config ↔ build-script pairs.

Mirrors the config↔git icon drift guard and the identifier↔appId parity
tests: each pair has TWO sides that must stay in lockstep, and these
tests fail when either side drifts — so a drift breaks CI instead of
silently producing a broken or dead artifact.

Pairs guarded here:

1. ``tauri.conf.json`` ``bundle.resources`` + ``bundle.externalBin`` ↔
   the stub generator's binary registry
   (``scripts/gen_tauri_icons_stub.py::_all_stub_paths``). The
   generator creates exactly the sidecar / native / prewarm binaries on
   a clean checkout, and the config must declare exactly that set:
   - config declares a binary the generator never creates → missing on
     a clean CI checkout → ``cargo tauri build`` fails at the
     resource-copy step;
   - generator creates a binary the config never declares → dead file,
     never bundled.

2. ``tauri-binaries.json`` per-arch ``sha256`` keys ↔ the canonical
   target-triple set (``SIDECAR_TRIPLES`` in the stub generator — the
   repo's single source of truth for build triples). macOS collapses
   both darwin triples into one ``macos`` key (universal binary).

3. ``tauri-binaries.json`` binary names ↔ the Cargo package / bin name
   (``src-tauri/Cargo.toml``). The autostart launcher discovers the
   Tauri host binary by file name per-OS; renaming the Cargo binary
   without updating the manifest silently orphans the integrity gate.

4. Every Nuitka invocation that bundles ``voice_typer`` must also pass
   ``--include-package-data=voice_typer.server``. The frozen sidecar
   loads package data at import time via ``__file__``-relative paths
   (``hotkey_reserved.json`` in ``config_validators/hotkey.py``, plus
   ``corrections.json``, ``model_hashes.json``,
   ``native/binaries.json``, ``silero_vad.jit``); without the flag the
   onefile exe BUILDS fine but crashes on launch with FileNotFoundError
   — and the CI existence check cannot catch it.

5. Sidecar Nuitka builds must NOT exclude any of the torch submodules
   that plain ``import torch`` loads UNCONDITIONALLY on torch 2.13
   (``torch.utils.data.distributed`` via ``utils/data/__init__.py:32``,
   ``torch.package`` via ``_jit_internal.py:47``, ``torch.export`` via
   ``__init__.py:2869``, ``torch.testing`` via ``__init__.py:2324``,
   ``torch._functorch`` transitively) and must keep ``torch.jit``
   enabled (``--module-parameter=torch-disable-jit=no``). Excluding any
   of them makes ``import torch`` raise ModuleNotFoundError inside the
   frozen exe — and ``vad.py`` catches that as ImportError, SILENTLY
   disabling Silero VAD in the shipped binary while the build still
   succeeds. Likewise Nuitka's torch plugin disables ``torch.jit`` by
   default in standalone mode, breaking the ``torch.jit.load`` of the
   bundled model.

6. ``tauri.conf.json`` ``bundle.windows.nsis.installerHooks`` must point
   at an NSIS script (``.nsh``). Tauri ``!include``s each hook into the
   generated ``installer.nsi``; pointing it at a batch file aborts
   makensis with ``Invalid command: "@echo"`` on EVERY ``cargo tauri
   build`` once bundling runs.
"""

from __future__ import annotations

import functools
import importlib.util
import json
from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = PROJECT_ROOT / "src-tauri"
STUB_SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"
MANIFEST_PATH = PROJECT_ROOT / "tauri-binaries.json"

# triple → tauri-binaries.json per-arch key. macOS ships a universal
# Mach-O binary, so both darwin triples collapse into the single
# ``macos`` key (the manifest documents this collapse).
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
    """Load ``scripts/gen_tauri_icons_stub.py`` as a module (no side effects).

    The script's module constants (``SIDECAR_TRIPLES``,
    ``WINDOWS_TRIPLES``) and ``_all_stub_paths()`` are the canonical
    script side of the drift pairs. Importing executes only the module
    body — ``main()`` is guarded by ``__name__``.
    """
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


# ─── Pair 1: bundle.resources + externalBin ↔ stub generator registry ──────


class TestBundleBinariesVsStubRegistry:
    """``tauri.conf.json`` binary declarations ↔ the stub generator's registry."""

    def test_config_declares_exactly_the_stub_generator_registry(self) -> None:
        """The config's declared binaries must equal the generator's registry.

        Both directions fail with actionable messages:
        - ``declared - registry``: a binary the config references that the
          stub step never creates is missing on a fresh CI checkout
          (``cargo tauri build`` hard-fails at the resource-copy step).
        - ``registry - declared``: a binary the generator creates that the
          config never declares is dead weight (never bundled).
        """
        stub = _stub_module()
        registry = {p.relative_to(SRC_TAURI).as_posix() for p in stub._all_stub_paths()}
        bundle = _tauri_conf()["bundle"]
        # externalBin is the base name; Tauri appends the target triple at
        # build time (the same naming the stub generator's registry uses).
        sidecars = {
            f"{base}-{triple}{'.exe' if triple in stub.WINDOWS_TRIPLES else ''}"
            for base in bundle.get("externalBin", [])
            for triple in stub.SIDECAR_TRIPLES
        }
        # bundle.resources binary entries: native hotkey + per-arch prewarm
        # (the non-binary resources — linux-scripts, polkit, rules — are
        # committed real files outside the stub registry).
        binaries = {
            r
            for r in bundle.get("resources", [])
            if r.startswith("resources/native/") or r.startswith("resources/prewarm-")
        }
        declared = sidecars | binaries

        missing = declared - registry
        assert not missing, (
            "binaries declared in tauri.conf.json (externalBin per-triple + "
            "bundle.resources native/prewarm) but NOT in the stub generator's "
            "registry — missing on a clean CI checkout, cargo tauri build "
            "fails:\n  " + "\n  ".join(sorted(missing))
        )
        dead = registry - declared
        assert not dead, (
            "binaries the stub generator creates but tauri.conf.json never "
            "declares (dead files, never bundled):\n  " + "\n  ".join(sorted(dead))
        )


# ─── Pair 2: tauri-binaries.json per-arch keys ↔ canonical triples ─────────


class TestTauriBinariesManifestCoverage:
    """``tauri-binaries.json`` sha256 keys ↔ the canonical triple set."""

    def test_manifest_covers_exactly_the_canonical_triples(self) -> None:
        """The manifest's per-arch keys must equal the triples' derived keys.

        ``SIDECAR_TRIPLES`` in the stub generator is the repo's single
        source of truth for build triples. If a new triple is added to
        the builds (e.g. riscv64), the manifest must grow a matching
        per-arch key — a missing key means the autostart integrity gate
        fail-closes for that platform, and an extra key means the
        manifest documents a platform nothing builds.
        """
        stub = _stub_module()
        expected = {TRIPLE_TO_MANIFEST_KEY[t] for t in stub.SIDECAR_TRIPLES}
        manifest = _manifest()
        actual = {k for entry in manifest["binaries"].values() for k in entry["sha256"]}

        missing = expected - actual
        assert not missing, (
            "tauri-binaries.json is missing per-arch sha256 key(s) for "
            "canonical build triples: " + ", ".join(sorted(missing)) + ". "
            "Every SIDECAR_TRIPLES entry must be covered (macOS collapses "
            "both darwin triples into 'macos')."
        )
        extra = actual - expected
        assert not extra, (
            "tauri-binaries.json declares per-arch sha256 key(s) with no "
            "matching canonical build triple: " + ", ".join(sorted(extra)) + ". "
            "Either add the triple to SIDECAR_TRIPLES or drop the key."
        )


# ─── Pair 3: tauri-binaries.json binary names ↔ Cargo binary name ──────────


class TestTauriBinariesManifestBinaryNames:
    """``tauri-binaries.json`` binary keys ↔ the Cargo package/bin name."""

    def test_manifest_binary_names_match_cargo_binary_name(self) -> None:
        """Every manifest binary key's base must equal the Cargo binary name.

        The autostart launcher discovers the Tauri host binary by file
        name per-OS (``voice-typer-tauri`` on Linux, ``.exe`` on
        Windows, ``.app`` on macOS). Renaming the Cargo binary without
        updating the manifest silently orphans the integrity gate — the
        loader would find no entry for the new name and fail closed.
        """
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
                f"binary name {cargo_name!r} — stale entry from a renamed "
                "binary."
            )


# ─── Pair 4: Nuitka invocations ↔ voice_typer.server package data ──────────


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
# ``__file__``-relative paths. All live under ``voice_typer/server``, so the
# ``--include-package-data=voice_typer.server`` flag (scoped to the server
# package, NOT the whole ``voice_typer`` tree — that would drag in the
# client + node_modules) bundles all of them recursively.
IMPORT_TIME_DATA_FILES = [
    "voice_typer/server/hotkey_reserved.json",
    "voice_typer/server/corrections.json",
    "voice_typer/server/model_hashes.json",
    "voice_typer/server/native/binaries.json",
    "voice_typer/server/silero_vad.jit",
]


class TestNuitkaBuildsIncludeVoiceTyperPackageData:
    """Every Nuitka invocation bundling ``voice_typer`` must also include
    ``voice_typer.server`` package data.

    IPD-1 regression guard: ``voice_typer/server/hotkey_reserved.json``
    is loaded at module-import time (``config_validators/hotkey.py``),
    so a sidecar built without ``--include-package-data`` crashes on
    launch — ``FileNotFoundError`` — while still BUILDING successfully.
    The workflow's post-build existence check can't catch it. This guard
    pins the flag at every Nuitka call site that includes ``voice_typer``.
    """

    def test_import_time_data_files_exist_and_are_not_python(self) -> None:
        """The data files the flag must carry actually exist as data files."""
        for rel in IMPORT_TIME_DATA_FILES:
            path = PROJECT_ROOT / rel
            assert path.is_file(), f"{rel} missing — is the file tracked?"
            assert path.suffix != ".py", f"{rel} is not a data file"

    def test_every_build_script_has_the_package_data_flag_after_voice_typer(
        self,
    ) -> None:
        """All six build scripts pair the data flag with the package include."""
        flag = "--include-package-data=voice_typer.server"
        for rel in BUILD_SCRIPTS:
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            assert flag in text, (
                f"{rel} must pass {flag} — the frozen exe crashes on launch "
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
            f"tauri-windows-build.yml has {nuitka_count} Nuitka invocation(s) "
            f"but only {flag_count} --include-package-data=voice_typer.server "
            "— every Nuitka command bundling voice_typer needs the flag."
        )


# ─── Pair 5: sidecar Nuitka builds ↔ torch.utils.data.distributed ──────────


SIDECAR_SCRIPTS = [
    "scripts/build/build_sidecar_windows.sh",
    "scripts/build/build_sidecar_linux.sh",
    "scripts/build/build_sidecar_macos.sh",
]


class TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed:
    """Sidecar Nuitka invocations must NOT exclude ``torch.utils.data.distributed``.

    NU-106 (VAD) regression guard: torch 2.13 imports
    ``torch.utils.data.distributed`` UNCONDITIONALLY at
    ``torch/utils/data/__init__.py`` line 32 (``from
    torch.utils.data.distributed import DistributedSampler``). A
    ``--nofollow-import-to=torch.utils.data.distributed`` flag makes
    ``import torch`` raise ``ModuleNotFoundError`` inside the frozen
    exe; ``voice_typer/server/vad.py`` catches that as ``ImportError``
    and SILENTLY disables Silero VAD in the shipped binary (verified
    on-host with a minimal frozen probe that reproduces the exact
    traceback). The other excluded torch submodules
    (``_dynamo``/``_inductor``/``export``/``_functorch``/``testing``/
    ``onnx``/``package``/``utils.benchmark``) are lazily imported and
    safe to exclude.
    """

    def test_no_sidecar_build_excludes_unconditionally_imported_torch_modules(
        self,
    ) -> None:
        """Sidecar builds must not exclude any torch module ``import torch`` needs.

        torch 2.13 loads all of these at plain ``import torch`` (verified
        via ``sys.modules`` inspection on the pinned version):
        ``torch.utils.data.distributed`` (utils/data/__init__.py:32),
        ``torch.package`` (_jit_internal.py:47), ``torch.export``
        (__init__.py:2869), ``torch.testing`` (__init__.py:2324),
        ``torch._functorch`` (transitively via export). Excluding any of
        them makes ``import torch`` raise ModuleNotFoundError inside the
        frozen exe — and ``vad.py`` catches that as ImportError,
        SILENTLY disabling Silero VAD while the build still succeeds
        (NU-106, verified on-host with a frozen probe).
        """
        forbidden = [
            "--nofollow-import-to=torch.utils.data.distributed",
            "--nofollow-import-to=torch.export",
            "--nofollow-import-to=torch._functorch",
            "--nofollow-import-to=torch.testing",
            "--nofollow-import-to=torch.package",
        ]
        targets = [WINDOWS_WORKFLOW] + [PROJECT_ROOT / s for s in SIDECAR_SCRIPTS]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for flag in forbidden:
                assert flag not in text, (
                    f"{path.relative_to(PROJECT_ROOT)} must NOT pass {flag}: "
                    "plain `import torch` loads that module unconditionally on "
                    "torch 2.13, so excluding it makes `import torch` fail with "
                    "ModuleNotFoundError inside the frozen exe and vad.py "
                    "SILENTLY DISABLES Silero VAD (NU-106). Only lazily-"
                    "imported torch submodules (_dynamo, _inductor, onnx, "
                    "utils.benchmark) may be excluded."
                )

    def test_every_sidecar_build_keeps_torch_jit_enabled(self) -> None:
        """All sidecar builds must pass ``--module-parameter=torch-disable-jit=no``.

        Nuitka's torch plugin disables ``torch.jit`` by default in
        standalone mode; ``voice_typer/server/vad.py`` loads the bundled
        Silero model with ``torch.jit.load(silero_vad.jit)`` and fails
        with ``module 'torch' has no attribute 'jit'`` otherwise — VAD
        silently degrades to the RMS fallback in the shipped binary
        (NU-106, verified on-host).
        """
        required = "--module-parameter=torch-disable-jit=no"
        targets = [WINDOWS_WORKFLOW] + [PROJECT_ROOT / s for s in SIDECAR_SCRIPTS]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            assert required in text, (
                f"{path.relative_to(PROJECT_ROOT)} must pass {required}: Nuitka's "
                "torch plugin disables torch.jit in standalone mode, and vad.py "
                "loads silero_vad.jit via torch.jit.load — without the flag VAD "
                "fails with 'module torch has no attribute jit' and silently "
                "degrades to the RMS fallback (NU-106)."
            )


# ─── Pair 6: tauri.conf.json NSIS installerHooks ↔ NSIS script ─────────────


class TestTauriNsisInstallerHooks:
    """``bundle.windows.nsis.installerHooks`` must point at an NSIS script.

    Tauri v2 ``!include``s each installerHooks entry into the generated
    ``installer.nsi`` — the file MUST be an NSIS script (``.nsh``).
    Pointing it at a batch file (``uninstall.bat``) aborts makensis with
    ``Invalid command: "@echo"`` on EVERY ``cargo tauri build`` once the
    bundling stage runs (latent until the bundler is actually exercised
    — the Windows workflow is dispatch-only). The correct target is the
    repo's existing ``scripts/windows/uninstaller.nsh`` (defines the
    ``customUnInstall`` macro shared with electron-builder).
    """

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
                "(.nsh) — Tauri !includes it into installer.nsi, and a batch "
                "file aborts makensis with 'Invalid command: @echo' on every "
                "cargo tauri build."
            )
            target = (SRC_TAURI / hook).resolve()
            assert target.is_file(), f"nsis.installerHooks entry {hook!r} resolves to {target} which does not exist."
