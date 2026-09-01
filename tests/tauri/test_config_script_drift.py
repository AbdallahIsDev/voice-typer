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

7. ``autostart_launcher._TAURI_LAUNCHER_INSTALL_PATHS`` ↔
   ``tauri-binaries.json`` ``binaries.*._install_paths``. The manifest is
   the single source of truth for BOTH the per-OS discovery path set AND
   the discovery priority (order-sensitive comparison). A launcher path
   change not mirrored in the manifest silently orphans the integrity
   gate (the loader would hash something else than CI recorded), and a
   manifest change the launcher doesn't follow makes the launcher
   discover nothing.

8. Every per-arch config override (``src-tauri/tauri.*.conf.json``) must
   stay locked to the base ``tauri.conf.json`` ``bundle.resources`` /
   ``bundle.externalBin``. Tauri REPLACES arrays in overrides (deep-merge
   only applies to objects), so a per-arch ``resources`` list is the full
   list that platform's installer is built from: it may only ever be a
   subset of the base (a cross-platform superset), it must keep every
   base resource relevant to its platform/triples, and it must never
   drop or alter the sidecar ``externalBin`` — otherwise a platform
   ships missing key-listener / prewarm binaries while CI stays green.

9. ``scripts/build/update_tauri_manifests.py::TRIPLE_TO_MANIFEST_KEY``
   ↔ the canonical triple set (Pair 2). The updater is CI's only writer
   of the per-arch sha256 fields; if its triple→key map drifts from the
   canonical one, CI would hash a built binary into the WRONG key — and
   the loader would fail-closed for that platform despite a built binary.

10. The version string in ``pyproject.toml`` ↔ ``package.json`` ↔
    ``src-tauri/tauri.conf.json`` ↔ ``src-tauri/Cargo.toml`` (↔
    ``electron-builder.yml`` when it carries an explicit version). One
    version across every layer — a bump that touches only one file
    silently ships mismatched app/installer/update metadata; the same
    lockstep also protects the Tauri workflows' "fail early" check
    (``scripts/build/sync_versions.py --check`` in the pre-build gate).

11. The release bump workflow (``RELEASING.md`` instructions ↔
    ``scripts/build/sync_versions.py``). The documented release commit
    must touch ``package.json`` AND ``src-tauri/tauri.conf.json`` (and
    ``Cargo.toml``) together via the sync script — a version bump that
    commits only one file breaks the Pair-10 lockstep mid-release.

12. The update feed: NO auto-update is the pinned contract today
    (ADR-0020 §15 — the electron ``publish:`` block was removed and
    ``tauri-plugin-updater`` is intentionally unconfigured). If a feed
    config or ``latest.json`` ever appears (electron ``publish:`` or a
    Tauri ``plugins.updater`` block), its referenced version must equal
    ``tauri.conf.json``'s — the guard below fails on any feed whose
    version drifts, and fails on any unlicensed feed config appearing
    without the parity wiring.

13. Every OS-level identifier the app REGISTERS must live in the
    canonical ``com.voicetyper.*`` reverse-DNS namespace (Windows Task
    Scheduler task + HKCU Run-key value + Startup-folder .bat +
    prewarm completion event, macOS LaunchAgent labels, keyring service
    name, polkit action). Legacy ``VoiceTyper*`` forms are allowed ONLY
    in the pinned legacy constants + cleanup sweeps (see
    TestReverseDnsIdentifierNamespace's allowlist).
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

# ``tomllib`` is stdlib only on Python >= 3.11; the 3.10 matrix legs
# (requires-python = ">=3.10") fall back to the ``tomli`` backport when
# present. ``tomli`` is NOT a declared dependency (not in
# requirements-lock.txt), so on a 3.10 env without it we skip this file
# gracefully instead of raising a collection error (which hard-fails the
# whole 3.10 leg with exit code 2).
if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]
    except ImportError:  # pragma: no cover — tomli not in the lock
        pytest.skip(
            "tomli backport not installed on Python 3.10 — skipping drift check",
            allow_module_level=True,
        )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = PROJECT_ROOT / "src-tauri"
STUB_SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"
MANIFEST_PATH = PROJECT_ROOT / "tauri-binaries.json"
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "update_tauri_manifests.py"

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


@functools.lru_cache(maxsize=1)
def _updater_module():
    """Load ``scripts/build/update_tauri_manifests.py`` as a module.

    Its ``TRIPLE_TO_MANIFEST_KEY`` is CI's only writer path for the
    per-arch sha256 fields, so the drift test pins it to the canonical
    triple→key mapping (Pair 9). The module is stdlib-only (argparse,
    hashlib, json) — safe to import in the minimal-env CI gates.
    """
    spec = importlib.util.spec_from_file_location("_vt_update_tauri_manifests_drift", UPDATE_SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {UPDATE_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        # bundle.resources binary entries: native hotkey only.
        # (Prewarm resources were DELETED 2026-08-13 — prewarm became a
        # worker startup phase, Option P-1, plan-runtime-pack-split §6.2.
        # The worker exe is now externalBin, NOT a bundle.resources entry.)
        # The non-binary resources — linux-scripts, polkit, rules — are
        # committed real files outside the stub registry.
        binaries = {r for r in bundle.get("resources", []) if r.startswith("resources/native/")}
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
        """``update_tauri_manifests.py`` must hash into the SAME keys CI reads.

        The updater is the ONLY writer of the per-arch sha256 fields. If
        its triple→key map drifts from ``SIDECAR_TRIPLES`` (e.g. it maps
        ``x86_64-unknown-linux-gnu`` to ``linux-aarch64``), CI would
        record a built binary's hash under the wrong key and the loader
        fail-closes for the platform that actually built it (or worse,
        accepts the wrong binary). macOS is the intended collapse for all
        darwin triples, so the updater is allowed extra darwin-key
        entries (``universal-apple-darwin``) — but never fewer.
        """
        updater = _updater_module()
        stub = _stub_module()
        for triple in stub.SIDECAR_TRIPLES:
            expected = TRIPLE_TO_MANIFEST_KEY[triple]
            actual = updater.TRIPLE_TO_MANIFEST_KEY[triple]
            assert actual == expected, (
                f"update_tauri_manifests.py maps {triple!r} to {actual!r} but "
                f"the canonical mapping (tests/tauri/test_config_script_drift.py "
                f"TRIPLE_TO_MANIFEST_KEY) says {expected!r} — CI would record "
                "hashes under the wrong manifest key."
            )
        # Every key the manifest declares must be reachable by some triple
        # the updater knows (extra darwin aliases are fine).
        manifest_keys = {k for entry in _manifest()["binaries"].values() for k in entry["sha256"]}
        reachable = set(updater.TRIPLE_TO_MANIFEST_KEY.values())
        unreachable = manifest_keys - reachable
        assert not unreachable, (
            "update_tauri_manifests.py cannot write the manifest key(s): "
            + ", ".join(sorted(unreachable))
            + " — add the owning triple to "
            "TRIPLE_TO_MANIFEST_KEY."
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
            f"tauri-windows-build.yml has {nuitka_count} Nuitka invocations "
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


# ─── Pair 7: launcher discovery paths ↔ manifest _install_paths ────────────


def _path_components(template: str) -> tuple[str, ...]:
    """Split a path template into components (both separators normalized).

    Keeps env-var tokens (``%LOCALAPPDATA%``, ``%PROGRAMFILES%``) as
    literal components so the comparison is environment-independent.
    """
    return tuple(template.replace("\\", "/").split("/"))


def _launcher_path_templates() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Expand the launcher's install-path table to manifest-comparable form.

    Launcher templates use ``{APP}`` and ``{HOME}`` tokens (branding —
    the launcher must never hardcode the app name); the manifest
    documents the same paths with the literal product name and ``~``.
    APP_NAME is resolved at test time and substituted so both sides
    compare component-for-component.
    """
    # Import inside the test: tests/conftest.py's autouse
    # ``mock_heavy_imports`` fixture must be active so the launcher
    # imports cleanly even in the workflow gate's minimal pytest env.
    import voice_typer.server.autostart_launcher as launcher
    from voice_typer.server.branding import APP_NAME

    expanded: dict[str, tuple[tuple[str, ...], ...]] = {}
    for platform, templates in launcher._TAURI_LAUNCHER_INSTALL_PATHS.items():
        normalized = tuple(_path_components(t.replace("{APP}", APP_NAME).replace("{HOME}", "~")) for t in templates)
        expanded[platform] = normalized
    return expanded


def _manifest_install_path_templates() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Group the manifest's ``_install_paths`` by OS platform key.

    The manifest is keyed by binary name; the platform is derived from
    the key suffix (``.exe`` → windows, ``.app`` → macos, else linux).
    """
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
        """The launcher's per-OS candidate lists must EQUAL the manifest's.

        Order-sensitive: the launcher checks ``%LOCALAPPDATA%`` before
        ``%PROGRAMFILES%`` on Windows (NSIS ``installMode=currentUser``
        default), and the manifest's ``_install_paths`` document the same
        priority. A drift in either direction means either the integrity
        gate hashes something CI never recorded, or the launcher never
        finds a legitimately installed binary.
        """
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


# ─── Pair 8: per-arch config overrides ↔ base tauri.conf.json ─────────────


# Per-arch config file → (platform, build triples it serves). The test
# below asserts this set EXACTLY matches the per-arch config files on
# disk — enabling a new arch (e.g. the commented windows-aarch64 leg,
# TX-40) requires registering its config here.
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
    """True if ``resource`` (from the base config) matters to the platform.

    The base ``bundle.resources`` is a documented CROSS-platform
    superset; a per-arch override must keep every base entry that this
    platform/arch actually ships (the override REPLACES the base array,
    so dropping one silently unbundles it).
    """
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
    # ``build.beforeDevCommand`` for the ``tauri dev`` CLI's CWD) — it is
    # NOT a per-arch build config (no platform/triple semantics, never
    # merged by a CI ``--config`` build) and must stay out of this set.
    return {
        p.name for p in SRC_TAURI.glob("tauri.*.conf.json") if p.name not in ("tauri.conf.json", "tauri.dev.conf.json")
    }


class TestPerArchConfigsStayLockedToBase:
    """Per-arch ``--config`` overrides ↔ the base ``tauri.conf.json``."""

    def test_no_unregistered_per_arch_config_files(self) -> None:
        """Every per-arch config on disk must be registered in this test.

        A new override file (e.g. ``tauri.windows-aarch64.conf.json``
        when the TX-40 leg is enabled) that nobody guards could silently
        drop the sidecar or a native listener for that arch.
        """
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
        """Every base resource relevant to the platform must be kept.

        A per-arch override REPLACES the base ``resources`` array, so a
        dropped key-listener / prewarm / tray entry would ship silently
        without this guard (CI's source-inspection tests read the BASE
        config and stay green).
        """
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
                f"{cfg_external_bin} != base {base_external_bin} — dropping "
                "bin/python-sidecar silently unbundles the ASR sidecar for "
                "that platform."
            )


# ─── Pair 10: version lockstep across every layer ──────────────────────────


# The files that MUST carry the identical version string, with a reader
# for each. ``pyproject.toml`` is the single source of truth (the release
# tooling bumps it first and sync_versions.py propagates — see Pair 11);
# the runtime/installer layers must never drift from it.
VERSIONED_FILES: dict[str, Path] = {
    "pyproject.toml": PROJECT_ROOT / "pyproject.toml",
    "voice_typer/client/package.json": PROJECT_ROOT / "voice_typer" / "client" / "package.json",
    "voice_typer/client/electron-builder.yml": PROJECT_ROOT / "voice_typer" / "client" / "electron-builder.yml",
    "src-tauri/tauri.conf.json": SRC_TAURI / "tauri.conf.json",
    "src-tauri/Cargo.toml": SRC_TAURI / "Cargo.toml",
}


def _read_json_version(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _read_pyproject_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["project"]["version"]


def _is_git_tracked(path: Path) -> bool:
    """True if ``path`` is committed (feed artifacts must be pinned in git,
    not floating in an ignored dist/ dir)."""
    result = __import__("subprocess").run(
        ["git", "ls-files", "--error-unmatch", path.relative_to(PROJECT_ROOT).as_posix()],
        capture_output=True,
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode == 0


def _read_cargo_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data["package"]["version"]


def _read_electron_builder_version(path: Path) -> str | None:
    """electron-builder.yml only carries an explicit version when
    ``version:`` is present (it otherwise inherits package.json's)."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^version:\s*([^\s]+)", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else None


class TestVersionLockstep:
    """One version across pyproject / package.json / Tauri host / installer.

    The release tooling (Pair 11) edits ``pyproject.toml`` and runs
    ``sync_versions.py --apply``; a version bump that touches only ONE
    file (hand-edited package.json, or a direct tauri.conf.json edit
    that bypasses the sync script) breaks the lockstep HERE — every CI
    surface (Tauri workflows' fail-fast gates, CI pytest) fails
    instead of shipping mismatched app/installer metadata.
    """

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
                f"{source!r} — bump via `python scripts/build/sync_versions.py "
                "--apply` (bump pyproject.toml first), never by hand-editing "
                "one file."
            )

    def test_electron_builder_version_matches_when_explicit(self) -> None:
        """electron-builder.yml version must match if it declares one."""
        source = _read_pyproject_version(VERSIONED_FILES["pyproject.toml"])
        explicit = _read_electron_builder_version(VERSIONED_FILES["voice_typer/client/electron-builder.yml"])
        if explicit is not None:
            assert explicit == source, (
                f"electron-builder.yml declares version {explicit!r} but "
                f"pyproject.toml says {source!r} — sync_versions.py --apply."
            )


# ─── Pair 11: release bump workflow touches all versioned files ────────────


RELEASING_MD = PROJECT_ROOT / "RELEASING.md"
SYNC_VERSIONS_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "sync_versions.py"

# The files a release bump MUST land in the SAME commit (the different
# layers' metadata would otherwise display three versions).
BUMP_COMMIT_FILES = (
    "voice_typer/client/package.json",
    "src-tauri/Cargo.toml",
    "src-tauri/tauri.conf.json",
)


class TestReleaseBumpWorkflow:
    """The documented bump flow must touch every versioned file at once.

    ``RELEASING.md`` is the runbook a human follows on release day; if a
    doc review edits the bump instructions to commit only package.json
    (or the sync script drops a layer), the Pair-10 lockstep silently
    dies mid-release. This pins the WORKFLOW contract itself.
    """

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
        # target is the exact path this test's VERSIONED_FILES expects.
        expected_targets = {
            "PACKAGE_JSON": VERSIONED_FILES["voice_typer/client/package.json"],
            "TAURI_CONF_JSON": VERSIONED_FILES["src-tauri/tauri.conf.json"],
            "CARGO_TOML": VERSIONED_FILES["src-tauri/Cargo.toml"],
        }
        for const, expected in expected_targets.items():
            actual = getattr(module, const)
            assert actual == expected, (
                f"sync_versions.py::{const} is {actual} but must be {expected} "
                "(release-bump drift — a bump that runs --apply would NOT "
                "touch the expected file)."
            )
        # collect_versions() must actually feed the pyproject version into
        # every one of those files (read-side coverage of the lockstep).
        versions = module.collect_versions()
        for rel in ("voice_typer/client/package.json", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml"):
            assert versions.get(rel) == versions["pyproject.toml"], (
                f"sync_versions.py collect_versions() drifts on {rel} — the "
                "release-bump --apply would need to resync it."
            )


# ─── Pair 12: update feed version ↔ tauri.conf.json ────────────────────────


class TestUpdateFeedParity:
    """Any committed update feed must carry tauri.conf.json's version.

    ADR-0020 §15 pins NO auto-update wiring: the electron ``publish:``
    block was removed (electron-builder.yml documents why) and no Tauri
    ``plugins.updater`` configuration exists — so no feed manifest is
    committed today. The contract pinned here is the FORWARD GUARD: the
    moment any ``latest.json`` / ``latest.yml`` feed record IS committed
    (electron-builder auto-update or Tauri's static updater feed), its
    ``version`` MUST equal ``src-tauri/tauri.conf.json``'s — the updater
    must never reference a release version that drifts from the app's
    own version, or clients would be rolled to a mismatched binary.
    """

    FEED_PATTERNS = ("**/latest.json", "**/latest.yml")

    def test_committed_feed_manifests_match_tauri_conf_version(self) -> None:
        """Every git-tracked feed manifest's version == tauri.conf.json's.

        Vacuously passes while no feed is committed (ADR-0020 §15 no
        auto-update); fails the moment a feed artifact is committed with
        a drifted version.
        """
        expected = _read_json_version(VERSIONED_FILES["src-tauri/tauri.conf.json"])
        feeds = [path for pattern in self.FEED_PATTERNS for path in PROJECT_ROOT.glob(pattern) if _is_git_tracked(path)]
        for feed in feeds:
            data = json.loads(feed.read_text(encoding="utf-8"))
            feed_version = data.get("version")
            assert feed_version == expected, (
                f"{feed.relative_to(PROJECT_ROOT)} references version "
                f"{feed_version!r} but src-tauri/tauri.conf.json is "
                f"{expected!r} — the update feed must reference the app's "
                "own version (sync_versions.py --apply then regenerate the "
                "feed artifact)."
            )

    def test_no_unlicensed_update_feed_wiring_ships(self) -> None:
        """ADR-0020 §15: no electron publish block, no Tauri updater plugin."""
        builder = VERSIONED_FILES["voice_typer/client/electron-builder.yml"].read_text(encoding="utf-8")
        assert not re.search(r"^publish:", builder, re.MULTILINE), (
            "electron-builder.yml must NOT declare a `publish:` block "
            "(ADR-0020 §15 — NO auto-update). If a feed is being wired, "
            "add the block AND keep the feed-version parity guard green."
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
                "§15) — enable it deliberately and keep the parity guard."
            )


# ─── Pair 13: reverse-DNS identifier namespace guard ────────────────────


class TestReverseDnsIdentifierNamespace:
    """Every OS-level identifier the app REGISTERS must live in the
    canonical ``com.voicetyper.*`` reverse-DNS namespace.

    The Windows autostart/prewarm identifiers were RENAMED from the bare
    ``VoiceTyper*`` forms (Run key ``VoiceTyper_<hash>``, Task Scheduler
    tasks ``VoiceTyperAutostart<hash>`` / ``VoiceTyperPrewarm``,
    Startup-folder ``VoiceTyper*.bat``, completion event
    ``Local\\VoiceTyperPrewarmCompletion_<pid>``) to the canonical
    namespace. These source pins flag a drift BACK to a bare name (or to
    any NON ``com.voicetyper.*`` name) at the source level — the same
    failure mode the uninstall-script sweeps and the
    ``platform_win32_test`` hash-suffix tests miss (those only verify
    the entries are swept/registered, not what they're NAMED).

    (Wave 3, 2026-08-14): the ``task_scheduler.py`` pin block
    (``TASK_NAME = "com.voicetyper.prewarm"`` +
    ``_LEGACY_TASK_NAME = "VoiceTyperPrewarm"``) was REMOVED —
    prewarm became a worker startup phase (master plan §6.2 P-1),
    so ``task_scheduler.py`` no longer carries any prewarm-related
    constants (the file was reduced from 977 → 285 LOC, keeping only
    the ``_schtasks`` / ``_schtasks_elevated`` / ``is_supported``
    autostart helpers + ``_APP_AUTOSTART_DELAY_SECONDS``). The
    ``server_platform/__init__.py`` + ``autostart_windows.py`` pins
    below remain in force (they pin the app autostart identifiers,
    which still exist).

    Explicit allowlist — bare ``VoiceTyper*`` forms that are
    INTENTIONAL and must NOT be renamed (do not extend without
    reviewing the item):

    - ``_LEGACY_KEYRING_SERVICE_NAMES = ("app.voicetyper", "voice-typer")``
      (credential_store/_schema.py) — keyring migration sources
      (migrated once, then deleted).
    - ``Local\\VoiceTyperSingleInstance`` mutex (single_instance.py) —
      internal OS/API identifier; AGENTS.md explicitly permits
      internal identifiers to keep their names. Same for the
      ``VoiceTyper.exe`` installer binary name.
    - Sweep/cleanup strings that MUST match both forms: the
      ``("VoiceTyper", "com.voicetyper")`` prefix tuples,
      ``'VoiceTyper*','com.voicetyper*'`` PowerShell union, and the
      ``autostart-sweep-v2-<hash>.done`` marker (which must stay
      version-scoped so pre-rename installs re-sweep once).
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
            # The runkey mechanism (register-time stale-cleanup matching
            # BOTH schemes — legacy entries from pre-rename installs)
            # moved into its submodule; the pin follows the moved
            # literal.
            "voice_typer/server/server_platform/_autostart_windows_runkey.py": [
                'name.startswith(("VoiceTyper", "com.voicetyper"))',
            ],
            # The autostart_windows facade was split into mechanism
            # submodules; the uninstall sweep + legacy-sweep markers
            # moved into theirs — the pins follow the moved literals.
            "voice_typer/server/server_platform/_autostart_windows_uninstall.py": [
                # Uninstaller sweeps must match BOTH schemes: the
                # PowerShell wildcard union for Task Scheduler entries
                # and the name-prefix tuple for HKCU Run keys.
                "\"Get-ScheduledTask -TaskName 'VoiceTyper*','com.voicetyper*' \"",
                'name.startswith(("VoiceTyper", "com.voicetyper"))',
            ],
            "voice_typer/server/server_platform/_autostart_windows_sweep.py": [
                'f"autostart-sweep-v2-{_autostart_mod._install_hash()}.done"',
            ],
            # NOTE: voice_typer/server/prewarm/completion_events.py was DELETED
            # 2026-08-13 — prewarm became a worker startup phase (Option P-1,
            # plan-runtime-pack-split §6.2). The prewarm_completion event
            # namespace is no longer used.
            #
            # NOTE: voice_typer/server/task_scheduler.py no longer carries
            # ``TASK_NAME`` / ``_LEGACY_TASK_NAME`` constants (Wave 3,
            # 2026-08-14) — prewarm became a worker startup phase, so
            # task_scheduler.py was reduced to the autostart-only helpers
            # (_schtasks / _schtasks_elevated / is_supported /
            # _APP_AUTOSTART_DELAY_SECONDS). The reverse-DNS pin for the
            # Windows autostart identifiers above remains in force.
        }
        for rel, expected in pins.items():
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            for pin in expected:
                assert pin in text, (
                    f"{rel} drifted from the canonical com.voicetyper.* "
                    f"namespace — expected the literal {pin!r}. Windows "
                    "autostart/prewarm identifiers must be reverse-DNS "
                    "(com.voicetyper.*), never the bare VoiceTyper* forms "
                    "(legacy forms allowed ONLY via the allowlisted legacy "
                    "constants and cleanup sweeps)."
                )

    def test_posix_labels_and_keyring_service_name_are_reverse_dns(self) -> None:
        """macOS LaunchAgent labels + keyring service name stay reverse-DNS.

        NOTE: voice_typer/server/prewarm_scheduler_posix.py was DELETED
        2026-08-13 — prewarm became a worker startup phase (Option P-1,
        plan-runtime-pack-split §6.2). The macOS LaunchAgent for prewarm
        is gone; the autostart_macos + keyring pins below remain in force.
        """
        pins = {
            "voice_typer/server/server_platform/autostart_macos.py": "<string>com.voicetyper</string>",
            "voice_typer/server/credential_store/_schema.py": 'KEYRING_SERVICE_NAME = "com.voicetyper.keyring"',
        }
        for rel, pin in pins.items():
            assert pin in (PROJECT_ROOT / rel).read_text(encoding="utf-8"), (
                f"{rel} drifted from the canonical com.voicetyper.* namespace — expected the literal {pin!r}."
            )

    def test_polkit_action_stays_reverse_dns(self) -> None:
        """The install-permissions polkit action is com.voicetyper.* (both
        the source that surfaces it and the installer scripts that deploy
        the policy file must agree on the name)."""
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
        """Migration sources are allowlisted AS-IS — a rename breaks the
        one-time keyring migration (credentials would be re-migrated or
        orphaned)."""
        text = (PROJECT_ROOT / "voice_typer/server/credential_store/_schema.py").read_text(encoding="utf-8")
        assert '_LEGACY_KEYRING_SERVICE_NAMES: tuple[str, ...] = ("app.voicetyper", "voice-typer")' in text, (
            "credential_store/_schema.py legacy keyring service names drifted — they "
            "are pinned migration sources (allowlisted)."
        )

    def test_single_instance_mutex_keeps_its_bare_name(self) -> None:
        """AGENTS.md boundary: the mutex is an internal OS/API
        identifier, explicitly permitted to keep the bare name — flagging
        a rename in EITHER direction (to or from bare) is intentional."""
        text = (PROJECT_ROOT / "voice_typer/server/single_instance.py").read_text(encoding="utf-8")
        assert "VoiceTyperSingleInstance" in text, (
            "single_instance.py must keep the 'VoiceTyperSingleInstance' "
            "mutex name (internal OS/API identifier — AGENTS.md "
            "explicitly permits bare internal identifiers)."
        )
        assert "com.voicetyper" not in text, (
            "single_instance.py must NOT use the reverse-DNS namespace for "
            "the mutex — it is a pinned internal OS/API identifier."
        )

    def test_sweep_marker_stays_version_scoped(self) -> None:
        """The once-per-install legacy-sweep marker must be version-scoped
        (``-v2-``) so installs that already ran the pre-rename sweep
        re-sweep exactly once after the namespace rename. The marker
        path helper lives in the ``_autostart_windows_sweep`` mechanism
        submodule (split out of the ``autostart_windows`` facade)."""
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
    """The Rust durable-bubble-position fallback range must mirror the
    server's ``bubble_x``/``bubble_y`` allowlist bounds.

    ``persisted_position.rs::position_on_any_monitor`` falls back to a
    sanity range when monitor enumeration fails; that range is a
    hand-mirrored copy of the server-side allowlist bound. This pin
    keeps the two in lockstep — widen one, widen both.
    """
    allowlist = (PROJECT_ROOT / "voice_typer/server/config_validators/allowlist.py").read_text(encoding="utf-8")
    bounds = re.findall(
        r'"bubble_[xy]": \(\(int, type\(None\)\), '
        r"_make_optional_int_validator\(lo=(-?[\d_]+), hi=([\d_]+)\)\)",
        allowlist,
    )
    assert len(bounds) == 2, (
        "allowlist.py bubble_x/bubble_y validator signature drifted — "
        "update this pin together with persisted_position.rs."
    )
    lo, hi = bounds[0]
    lo, hi = lo.replace("_", ""), hi.replace("_", "")
    assert lo == "-100000" and hi == "100000", (
        f"server bubble coordinate bounds changed to [{lo}, {hi}] — "
        "update PERSISTED_COORDINATE_LIMIT in "
        "src-tauri/src/commands/bubble/persisted_position.rs to match."
    )

    rust = (SRC_TAURI / "src/commands/bubble/persisted_position.rs").read_text(encoding="utf-8")
    assert "const PERSISTED_COORDINATE_LIMIT: i32 = 100_000;" in rust, (
        "persisted_position.rs PERSISTED_COORDINATE_LIMIT drifted from "
        "the server allowlist bound (±100000) — keep them in lockstep."
    )
