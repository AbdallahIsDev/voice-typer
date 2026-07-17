"""MIG-1.8 Phase 1 — Build-script glue validation.

This test file validates that the **build orchestration** ties together
all per-platform builds + the icon stub generator + the final
``cargo tauri build`` step. It does NOT actually invoke the builds —
the headless dev container lacks the platform toolchains (Nuitka,
swiftc, MSVC, WebView2, webkit2gtk, codesign, signtool, …). These
tests validate the *structure* of the orchestrator script + the
helpers it should invoke.

Spec reference: ADR-0020 §4 (Nuitka freeze), §5 (prewarm), §6.4 (native
listener), §7 (Tauri config), §13 (signing), §15 (no auto-update).

Scope (the 8 glue assertions this file enforces):

  1. ``build_tauri_all.sh`` (the orchestrator) exists at the canonical path.
  2. The orchestrator runs the per-platform **sidecar** builds
     (``build_sidecar_<windows|macos|linux>.sh``).
  3. The orchestrator runs the per-platform **prewarm** builds
     (``build_prewarm_<windows|macos|linux>.sh``).
  4. The orchestrator runs the native key-listener builds
     (``build_native_listener_<platform>.sh``) which themselves invoke
     ``compile_native.sh`` (macOS + Linux) or ``compile_native.ps1``
     (Windows). The orchestrator must therefore reference
     ``compile_native.sh`` (directly OR indirectly via the
     ``build_native_listener_*`` wrappers).
  5. The orchestrator runs ``gen_tauri_icons_stub.py`` OR documents
     that icons must be generated first (GAP-1: the orchestrator does
     NEITHER — see
     ``test_known_gap_orchestrator_neither_runs_nor_documents_icon_generation``).
  6. The orchestrator runs ``cargo tauri build`` as the final step
     (Phase 1c in the script).
  7. ``compile_native.sh`` builds all 3 native listeners (Windows +
     macOS + Linux) via per-platform ``case`` branches. (Each branch
     only fires on the matching host — cross-compilation is NOT
     supported — but the script MUST contain all 3 branches so a build
     on any host produces the matching listener.)
  8. ``gen_tauri_icons_stub.py`` generates placeholder icons for
     development (RGBA PNGs + stub sidecar/native/prewarm binaries).

VALIDATE ON HOST:
    1. bash scripts/build/build_tauri_all.sh
       (OR run the steps manually:
        a. bash scripts/build/compile_native.sh
        b. bash scripts/build/build_sidecar_<platform>.sh <arch>
        c. bash scripts/build/build_prewarm_<platform>.sh <arch>
        d. python scripts/gen_tauri_icons_stub.py
        e. cd src-tauri; cargo tauri build --target <triple>)
    2. Verify the bundle is produced:
       - Windows: target/<triple>/release/bundle/nsis/*.exe
       - macOS: target/<triple>/release/bundle/dmg/*.dmg
       - Linux: target/<triple>/release/bundle/deb/*.deb
    Expected: all artifacts produced; no missing-resource errors

References:
  - ADR-0020 §4 (Nuitka freeze) + §5 (prewarm) + §6.4 (native listener)
    + §7 (Tauri config) + §13 (signing) + §15 (no auto-update).
  - ``docs/migration/tauri-build-runbook.md`` — build runbook + the
    "Phase 1 Packaging Status" section (the authoritative status table
    for "is the pipeline scaffolded?").
  - ``docs/migration/cutover-playbook.md`` — per-platform cutover
    criteria (do NOT flip the default shipping app from Electron to
    Tauri until a platform's Phase 5 cutover gate passes).

Gaps documented (report, do NOT fix — out of scope for this glue test):

  - GAP-1: ``build_tauri_all.sh`` does NOT invoke
    ``gen_tauri_icons_stub.py`` (or any icon generator) AND does NOT
    document that the operator must run it first. A clean checkout has
    no ``src-tauri/icons/*.png`` + no ``src-tauri/bin/python-sidecar-*``
    + no ``src-tauri/resources/native/*`` + no
    ``src-tauri/resources/prewarm-*``, so ``cargo tauri build`` fails
    immediately with "failed to open icon 'icons/32x32.png'". The
    operator must manually run ``python scripts/gen_tauri_icons_stub.py``
    (or the real ``scripts/build/generate_icon.py``) BEFORE invoking
    ``build_tauri_all.sh``. This is documented in the tauri-build-runbook
    "Common failures" section but is NOT enforced — or even mentioned —
    by the orchestrator itself. See
    ``test_known_gap_orchestrator_neither_runs_nor_documents_icon_generation``.

  - GAP-2: ``build_tauri_all.sh`` does NOT directly invoke
    ``compile_native.sh``. It invokes
    ``build_native_listener_<platform>.sh``, which is a thin wrapper
    that itself invokes ``compile_native.sh`` (macOS + Linux) or
    ``compile_native.ps1`` (Windows). This is the correct layering
    (the wrapper also copies the compiled binary into
    ``src-tauri/resources/native/``), but the glue is therefore
    indirect: a regression in ``build_native_listener_<platform>.sh``
    that drops the ``compile_native.sh`` call would silently skip the
    native listener build. See
    ``test_orchestrator_indirectly_invokes_compile_native_via_wrappers``.

  - GAP-3: ``build_tauri_all.sh`` only builds for the HOST platform.
    ADR-0020 §4 explicitly states "Nuitka cannot cross-compile", so a
    single host CANNOT produce all 6 sidecar triples + all 6 prewarm
    triples + all 3 native listeners. The CI matrix
    (``.github/workflows/tauri-{windows,macos,linux}-build.yml``) is
    what covers the cross-platform case via separate runners; the
    orchestrator is the local-developer analog only. See
    ``test_known_gap_orchestrator_only_builds_host_platform``.

  - GAP-4: ``build_tauri_all.sh`` does NOT validate that the
    per-platform sidecar binaries exist before invoking
    ``cargo tauri build``. If ``SKIP_SIDECAR=1`` is set (or a prior
    sidecar build silently failed), the ``cargo tauri build`` step
    will fail with "resource path 'bin/python-sidecar-<triple>' doesn't
    exist" — a clear error, but the orchestrator does not pre-flight
    the artifact set. See
    ``test_known_gap_orchestrator_does_not_preflight_artifacts``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_build_script_glue.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

ORCHESTRATOR = BUILD_DIR / "build_tauri_all.sh"
COMPILE_NATIVE = BUILD_DIR / "compile_native.sh"
ICON_STUB_GENERATOR = SCRIPTS_DIR / "gen_tauri_icons_stub.py"

# Per-platform helpers invoked (directly or indirectly) by the orchestrator.
SIDECAR_SCRIPTS = {
    "windows": BUILD_DIR / "build_sidecar_windows.sh",
    "macos": BUILD_DIR / "build_sidecar_macos.sh",
    "linux": BUILD_DIR / "build_sidecar_linux.sh",
}
PREWARM_SCRIPTS = {
    "windows": BUILD_DIR / "build_prewarm_windows.sh",
    "macos": BUILD_DIR / "build_prewarm_macos.sh",
    "linux": BUILD_DIR / "build_prewarm_linux.sh",
}
NATIVE_LISTENER_SCRIPTS = {
    "windows": BUILD_DIR / "build_native_listener_windows.sh",
    "macos": BUILD_DIR / "build_native_listener_macos.sh",
    "linux": BUILD_DIR / "build_native_listener_linux.sh",
}


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def orchestrator_text() -> str:
    """Read the orchestrator script once per module; fail fast if missing."""
    assert ORCHESTRATOR.is_file(), f"build_tauri_all.sh not found at {ORCHESTRATOR}. Did the project layout change?"
    return ORCHESTRATOR.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compile_native_text() -> str:
    """Read compile_native.sh once per module; fail fast if missing."""
    assert COMPILE_NATIVE.is_file(), f"compile_native.sh not found at {COMPILE_NATIVE}. Did the project layout change?"
    return COMPILE_NATIVE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def icon_stub_text() -> str:
    """Read gen_tauri_icons_stub.py once per module; fail fast if missing."""
    assert ICON_STUB_GENERATOR.is_file(), (
        f"gen_tauri_icons_stub.py not found at {ICON_STUB_GENERATOR}. Did the project layout change?"
    )
    return ICON_STUB_GENERATOR.read_text(encoding="utf-8")


# ─── 1. Orchestrator exists + bash-syntax valid ──────────────────────────────
def test_orchestrator_exists():
    """``build_tauri_all.sh`` must exist at the canonical path.

    ADR-0020 §4: this is the local-developer equivalent of
    ``.github/workflows/tauri-build.yml``. It dispatches to the
    per-platform build scripts + ``cargo tauri build``.
    """
    assert ORCHESTRATOR.is_file(), f"missing: {ORCHESTRATOR}"
    # Also assert it's non-empty (a stub would be a regression).
    assert ORCHESTRATOR.stat().st_size > 1000, (
        f"{ORCHESTRATOR} is suspiciously small ({ORCHESTRATOR.stat().st_size} bytes); "
        "expected a full orchestrator script (~5-8 KB)."
    )


def test_orchestrator_is_bash_syntax_valid():
    """``bash -n`` must parse the orchestrator without syntax errors.

    ``-n`` only parses — it does NOT execute the script — so no
    sidecar/Nuitka/cargo is spawned. Safe to run on any host.
    """
    if not shutil.which("bash"):
        pytest.skip("bash not available on this host — cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {ORCHESTRATOR}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_orchestrator_has_shebang_and_strict_mode(orchestrator_text: str):
    """The orchestrator must use ``#!/usr/bin/env bash`` + ``set -euo pipefail``.

    Strict mode is mandatory so a failed per-platform build (e.g.
    Nuitka missing) aborts the orchestrator instead of producing a
    broken bundle.
    """
    assert orchestrator_text.startswith("#!/usr/bin/env bash"), (
        "build_tauri_all.sh must start with `#!/usr/bin/env bash`."
    )
    assert "set -euo pipefail" in orchestrator_text, "build_tauri_all.sh must enable strict mode (`set -euo pipefail`)."


# ─── 2. Orchestrator runs per-platform SIDECAR builds ────────────────────────
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_orchestrator_runs_per_platform_sidecar_builds(orchestrator_text: str, platform: str):
    """The orchestrator must invoke ``build_sidecar_<platform>.sh`` for each platform.

    ADR-0020 §4: Nuitka freeze of ``voice_typer.server.ipc_server`` into
    ``python-sidecar-<triple>``. Nuitka cannot cross-compile, so the
    orchestrator must invoke the script for the HOST platform (selected
    via the ``$HOST_PLATFORM`` case branch). The script MUST reference
    all 3 platforms so a build on any host dispatches correctly.
    """
    script_name = f"build_sidecar_{platform}.sh"
    assert script_name in orchestrator_text, (
        f"build_tauri_all.sh must invoke `{script_name}` for the {platform} "
        f"platform (ADR-0020 §4). Missing reference in orchestrator."
    )
    # The orchestrator must invoke the script (not just mention it in a
    # comment). Look for `bash "$SCRIPT_DIR/<script_name>"`.
    assert (
        f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text
        or f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text
    ), (
        f"build_tauri_all.sh must invoke {script_name} via "
        f'`bash "$SCRIPT_DIR/{script_name}"`. (Comment-only references '
        "are not enough — the script must be dispatched.)"
    )
    # Sanity: the referenced script must actually exist.
    assert SIDECAR_SCRIPTS[platform].is_file(), (
        f"{script_name} referenced by orchestrator but not found at {SIDECAR_SCRIPTS[platform]}."
    )


# ─── 3. Orchestrator runs per-platform PREWARM builds ────────────────────────
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_orchestrator_runs_per_platform_prewarm_builds(orchestrator_text: str, platform: str):
    """The orchestrator must invoke ``build_prewarm_<platform>.sh`` for each platform.

    ADR-0020 §5: Nuitka freeze of ``voice_typer.server.prewarm`` into
    ``prewarm-<triple>``. Same cross-compile caveat as the sidecar.
    """
    script_name = f"build_prewarm_{platform}.sh"
    assert script_name in orchestrator_text, (
        f"build_tauri_all.sh must invoke `{script_name}` for the {platform} "
        f"platform (ADR-0020 §5). Missing reference in orchestrator."
    )
    assert f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text, (
        f'build_tauri_all.sh must invoke {script_name} via `bash "$SCRIPT_DIR/{script_name}"`.'
    )
    assert PREWARM_SCRIPTS[platform].is_file(), (
        f"{script_name} referenced by orchestrator but not found at {PREWARM_SCRIPTS[platform]}."
    )


# ─── 4. Orchestrator runs compile_native.sh (directly OR via wrappers) ───────
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_orchestrator_runs_native_listener_builds(orchestrator_text: str, platform: str):
    """The orchestrator must invoke ``build_native_listener_<platform>.sh``.

    ADR-0020 §6.4: native key-listener binaries (Windows / macOS / Linux).
    The orchestrator does NOT call ``compile_native.sh`` directly — it
    calls the per-platform wrapper which itself invokes
    ``compile_native.sh`` (macOS + Linux) or ``compile_native.ps1``
    (Windows). The wrapper is also responsible for copying the compiled
    binary into ``src-tauri/resources/native/`` where the Tauri bundler
    picks it up as a ``bundle.resource``.
    """
    script_name = f"build_native_listener_{platform}.sh"
    assert script_name in orchestrator_text, (
        f"build_tauri_all.sh must invoke `{script_name}` for the {platform} "
        f"platform (ADR-0020 §6.4). Missing reference in orchestrator."
    )
    assert f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text, (
        f'build_tauri_all.sh must invoke {script_name} via `bash "$SCRIPT_DIR/{script_name}"`.'
    )
    assert NATIVE_LISTENER_SCRIPTS[platform].is_file(), (
        f"{script_name} referenced by orchestrator but not found at {NATIVE_LISTENER_SCRIPTS[platform]}."
    )


def test_orchestrator_indirectly_invokes_compile_native_via_wrappers(
    orchestrator_text: str,
):
    """The native-listener wrappers must invoke ``compile_native.sh`` (macOS+Linux)
    or ``compile_native.ps1`` (Windows).

    GAP-2 (reported, not fixed): ``build_tauri_all.sh`` does NOT call
    ``compile_native.sh`` directly — it relies on the per-platform
    wrapper. This is the correct layering (the wrapper also copies the
    binary into ``src-tauri/resources/native/``), but the glue is
    indirect. This test asserts the wrapper chain is intact.
    """
    # The macOS + Linux wrappers must invoke compile_native.sh.
    macos_wrapper = NATIVE_LISTENER_SCRIPTS["macos"].read_text(encoding="utf-8")
    linux_wrapper = NATIVE_LISTENER_SCRIPTS["linux"].read_text(encoding="utf-8")
    assert "compile_native.sh" in macos_wrapper, (
        "build_native_listener_macos.sh must invoke compile_native.sh (which detects macOS + runs swiftc)."
    )
    assert "compile_native.sh" in linux_wrapper, (
        "build_native_listener_linux.sh must invoke compile_native.sh (which detects Linux + runs gcc)."
    )
    # The Windows wrapper invokes compile_native.ps1 (PowerShell) — NOT
    # compile_native.sh. This is correct: Nuitka on Windows works best
    # from PowerShell, and cl.exe needs the Developer Command Prompt env.
    windows_wrapper = NATIVE_LISTENER_SCRIPTS["windows"].read_text(encoding="utf-8")
    assert "compile_native.ps1" in windows_wrapper, (
        "build_native_listener_windows.sh must invoke compile_native.ps1 "
        "(PowerShell — cl.exe needs the Developer Command Prompt env)."
    )


def test_orchestrator_references_compile_native_script(
    orchestrator_text: str,
):
    """The orchestrator (or its header docstring) must reference compile_native.sh
    OR the per-platform wrappers that invoke it.

    The orchestrator's header comment enumerates the dispatched scripts;
    ``compile_native.sh`` should appear (either directly OR via the
    ``build_native_listener_*`` wrappers it lists). This is a soft
    structural check — the hard check is in
    ``test_orchestrator_indirectly_invokes_compile_native_via_wrappers``.
    """
    # Either the orchestrator names compile_native.sh directly OR it
    # references build_native_listener_<platform>.sh (which calls it).
    direct = "compile_native.sh" in orchestrator_text
    indirect = all(f"build_native_listener_{p}.sh" in orchestrator_text for p in ("windows", "macos", "linux"))
    assert direct or indirect, (
        "build_tauri_all.sh must reference compile_native.sh (directly) "
        "OR build_native_listener_<platform>.sh for all 3 platforms "
        "(indirect — the wrappers invoke compile_native.sh)."
    )


# ─── 5. Orchestrator runs gen_tauri_icons_stub.py (BUILD-4 fix) ──────────────
def test_orchestrator_invokes_gen_tauri_icons_stub(orchestrator_text: str):
    """BUILD-4 fix: the orchestrator now invokes ``gen_tauri_icons_stub.py``.

    ``src-tauri/tauri.conf.json`` references 4 PNG icons + 6 sidecar
    binaries + 3 native + 6 prewarm resources. On a clean checkout NONE
    exist, so ``cargo tauri build`` fails immediately. BUILD-4 added a
    Phase 0 that runs ``gen_tauri_icons_stub.py --check`` (generates stubs
    only if missing) before Phase 1a. This test ASSERTS the invocation
    IS present.
    """
    invokes_directly = (
        "gen_tauri_icons_stub.py" in orchestrator_text
        and "python" in orchestrator_text
        and "gen_tauri_icons_stub" in orchestrator_text
    )
    assert invokes_directly, (
        "build_tauri_all.sh should invoke gen_tauri_icons_stub.py (BUILD-4 fix). "
        "The orchestrator must generate icon + binary stubs before cargo tauri build."
    )


# ─── 6. Orchestrator runs ``cargo tauri build`` as the final step ───────────
def test_orchestrator_runs_cargo_tauri_build(orchestrator_text: str):
    """The orchestrator must run ``cargo tauri build`` (Phase 1c).

    ADR-0020 §7: this is the Tauri bundler step that produces the
    platform installer (.exe / .dmg / .deb). It must run AFTER the
    sidecar + prewarm + native builds so the bundler can pick up the
    freshly-built artifacts as ``externalBin`` + ``bundle.resources``.
    """
    assert "cargo tauri build" in orchestrator_text, (
        "build_tauri_all.sh must invoke `cargo tauri build` (ADR-0020 §7). "
        "This is the final bundling step that produces the platform installer."
    )
    # Must accept --target triple (host triple by default).
    assert "--target" in orchestrator_text, (
        "build_tauri_all.sh must support `cargo tauri build --target <triple>` "
        "so the operator can build for a non-host triple (e.g. universal-apple-darwin)."
    )


def test_orchestrator_runs_cargo_tauri_build_after_sidecar_phase(
    orchestrator_text: str,
):
    """``cargo tauri build`` must run AFTER the sidecar + prewarm + native phase.

    The Tauri bundler picks up ``externalBin`` (sidecar) + ``bundle.resources``
    (native + prewarm) at build time. If ``cargo tauri build`` ran first,
    the bundle would contain stale (or missing) sidecar/native/prewarm
    binaries.
    """
    cargo_idx = orchestrator_text.find("cargo tauri build")
    sidecar_idx = orchestrator_text.find("build_sidecar_")
    assert cargo_idx > 0 and sidecar_idx > 0, (
        "Both `cargo tauri build` and `build_sidecar_*` must appear in the "
        "orchestrator (positions checked in separate tests)."
    )
    assert cargo_idx > sidecar_idx, (
        "build_tauri_all.sh: `cargo tauri build` must appear AFTER "
        "`build_sidecar_*` so the bundler picks up the freshly-built sidecar."
    )


def test_orchestrator_skips_sidecar_phase_with_flag(orchestrator_text: str):
    """The orchestrator must support ``--skip-sidecar`` for dev iteration.

    This lets a developer re-run only ``cargo tauri build`` after a
    Rust-only change, without re-running the (slow) Nuitka sidecar
    freeze. The flag is documented in the script's ``--help`` output.
    """
    assert "--skip-sidecar" in orchestrator_text, (
        "build_tauri_all.sh must support `--skip-sidecar` for dev iteration "
        "(re-run only cargo tauri build, skip the Nuitka freeze)."
    )
    # SKIP_SIDECAR flag must be checked before invoking the per-platform
    # build scripts.
    assert "SKIP_SIDECAR" in orchestrator_text, (
        "build_tauri_all.sh must declare a SKIP_SIDECAR variable + gate the per-platform build phase on it."
    )


def test_orchestrator_has_check_dry_run_mode(orchestrator_text: str):
    """The orchestrator must support ``--check`` (dry-run: print plan, exit 0).

    This is the equivalent of ``bash -n`` but at the semantic level: it
    parses the args, detects the host platform + arch, computes the
    target triple, and prints the build plan WITHOUT actually invoking
    any per-platform script or ``cargo tauri build``. Used by CI to
    pre-flight the orchestrator on every PR.
    """
    assert "--check" in orchestrator_text, "build_tauri_all.sh must support `--check` (dry-run: print plan, exit 0)."
    assert "CHECK_ONLY" in orchestrator_text, (
        "build_tauri_all.sh must declare a CHECK_ONLY variable + gate the actual build phases on it."
    )


# ─── 7. compile_native.sh builds all 3 native listeners ─────────────────────
def test_compile_native_script_exists():
    """``compile_native.sh`` must exist at the canonical path."""
    assert COMPILE_NATIVE.is_file(), f"missing: {COMPILE_NATIVE}"
    assert COMPILE_NATIVE.stat().st_size > 1000, (
        f"{COMPILE_NATIVE} is suspiciously small ({COMPILE_NATIVE.stat().st_size} bytes)."
    )


def test_compile_native_is_bash_syntax_valid():
    """``bash -n`` must parse compile_native.sh without syntax errors."""
    if not shutil.which("bash"):
        pytest.skip("bash not available on this host — cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(COMPILE_NATIVE)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {COMPILE_NATIVE}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_compile_native_has_shebang_and_strict_mode(compile_native_text: str):
    """compile_native.sh must use ``#!/usr/bin/env bash`` + ``set -euo pipefail``."""
    assert compile_native_text.startswith("#!/usr/bin/env bash"), (
        "compile_native.sh must start with `#!/usr/bin/env bash`."
    )
    assert "set -euo pipefail" in compile_native_text, "compile_native.sh must enable strict mode."


@pytest.mark.parametrize(
    "platform, source_file, out_file, compiler",
    [
        ("darwin", "macos-key-listener.swift", "macos-key-listener", "swiftc"),
        ("win32", "windows-key-listener.c", "windows-key-listener.exe", "cl.exe"),
        ("linux", "linux-key-listener.c", "linux-key-listener", "gcc"),
    ],
)
def test_compile_native_builds_all_three_listeners(
    compile_native_text: str, platform: str, source_file: str, out_file: str, compiler: str
):
    """``compile_native.sh`` must contain a per-platform ``case`` branch for
    each of the 3 native listeners (Windows + macOS + Linux).

    ADR-0020 §6.4: each platform has its own native listener source:
      - macOS:   voice_typer/server/native/macos-key-listener.swift   (Swift)
      - Windows: voice_typer/server/native/windows-key-listener.c     (C)
      - Linux:   voice_typer/server/native/linux-key-listener.c       (C)

    The script only builds the binary for the CURRENT platform
    (cross-compilation is NOT supported — see script header), but it
    MUST contain all 3 branches so a build on any host produces the
    matching listener.
    """
    # The platform case branch must be present.
    assert f"{platform})" in compile_native_text, (
        f"compile_native.sh must have a `{platform})` case branch for the {platform} platform (ADR-0020 §6.4)."
    )
    # The source file must be referenced.
    assert source_file in compile_native_text, (
        f"compile_native.sh must reference the {platform} source file `{source_file}` (ADR-0020 §6.4)."
    )
    # The output binary name must be referenced.
    assert out_file in compile_native_text, (
        f"compile_native.sh must reference the {platform} output binary `{out_file}` (ADR-0020 §6.4)."
    )
    # The compiler must be referenced.
    assert compiler in compile_native_text, (
        f"compile_native.sh must reference the {platform} compiler `{compiler}` (ADR-0020 §6.4)."
    )


def test_compile_native_supports_check_mode(compile_native_text: str):
    """``compile_native.sh --check`` must verify the toolchain without building.

    Used by CI to pre-flight the toolchain on every PR (without waiting
    for the ~30 s compile). Exits 0 if the toolchain is present, 1 if
    missing.
    """
    assert "--check" in compile_native_text, "compile_native.sh must support `--check` (verify toolchain, exit 0/1)."


# ─── 8. gen_tauri_icons_stub.py generates placeholder icons ─────────────────
def test_icon_stub_generator_exists():
    """``gen_tauri_icons_stub.py`` must exist at the canonical path."""
    assert ICON_STUB_GENERATOR.is_file(), f"missing: {ICON_STUB_GENERATOR}"
    assert ICON_STUB_GENERATOR.stat().st_size > 1000, (
        f"{ICON_STUB_GENERATOR} is suspiciously small ({ICON_STUB_GENERATOR.stat().st_size} bytes)."
    )


def test_icon_stub_generator_generates_placeholder_icons(icon_stub_text: str):
    """The generator must produce RGBA PNGs (color_type=6) for development.

    Tauri v2 ``generate_context!()`` requires RGBA (color_type=6) for
    the bundle icons — RGB (color_type=2) is rejected with
    "icon ... is not RGBA". The generator must produce 4 PNGs at the
    sizes referenced by ``src-tauri/tauri.conf.json``:
      - ``icons/32x32.png``
      - ``icons/128x128.png``
      - ``icons/128x128@2x.png`` (256x256)
      - ``icons/icon.png`` (512x512)
    """
    # The generator must produce RGBA PNGs.
    assert "color_type=6" in icon_stub_text or "RGBA" in icon_stub_text, (
        "gen_tauri_icons_stub.py must document that it generates RGBA PNGs "
        "(color_type=6 — required by Tauri v2 generate_context!())."
    )
    # All 4 icon paths must be referenced.
    for icon_rel in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png"):
        assert icon_rel in icon_stub_text, (
            f"gen_tauri_icons_stub.py must generate `{icon_rel}` (referenced by src-tauri/tauri.conf.json bundle.icon)."
        )


def test_icon_stub_generator_generates_stub_sidecar_binaries(icon_stub_text: str):
    """The generator must produce stub sidecar binaries for all 6 triples.

    These stubs let ``cargo tauri build`` succeed on a clean checkout
    without a real Nuitka freeze. The stubs print "STUB: not a real
    sidecar" to stderr + exit 1 if executed — the safety feature that
    prevents accidentally shipping stubs.
    """
    # The 6 target triples.
    expected_triples = [
        "x86_64-pc-windows-msvc",
        "aarch64-pc-windows-msvc",
        "x86_64-apple-darwin",
        "aarch64-apple-darwin",
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
    ]
    for triple in expected_triples:
        assert triple in icon_stub_text, (
            f"gen_tauri_icons_stub.py must generate a stub sidecar for the `{triple}` target triple (ADR-0020 §4.1)."
        )
    # The stub marker (safety feature).
    assert "STUB" in icon_stub_text, (
        "gen_tauri_icons_stub.py must embed the STUB marker in every stub "
        "binary so a stub that accidentally ships fails loudly at runtime."
    )


def test_icon_stub_generator_generates_stub_native_binaries(icon_stub_text: str):
    """The generator must produce stub native-listener binaries for all 3 platforms."""
    for native_rel in (
        "windows-key-listener.exe",
        "macos-key-listener",
        "linux-key-listener",
    ):
        assert native_rel in icon_stub_text, (
            f"gen_tauri_icons_stub.py must generate a stub native-listener binary `{native_rel}` (ADR-0020 §6.4)."
        )


def test_icon_stub_generator_supports_check_mode(icon_stub_text: str):
    """``gen_tauri_icons_stub.py --check`` must exit 0 if stubs present, 1 if missing.

    CI gate: a non-zero exit blocks the build pipeline. Lets CI verify
    the stub set is intact without re-generating (which would overwrite
    real artifacts a developer may have built).
    """
    assert "--check" in icon_stub_text, (
        "gen_tauri_icons_stub.py must support `--check` (CI gate: exit 0 if all stubs present, 1 if any missing)."
    )


def test_icon_stub_generator_supports_clean_mode(icon_stub_text: str):
    """``gen_tauri_icons_stub.py --clean`` must remove stubs (preserving real artifacts).

    Uses a heuristic (PNG signature OR STUB_MARKER string in the first
    8 KB) so a developer who built a real Nuitka sidecar at one of the
    stub paths doesn't lose it.
    """
    assert "--clean" in icon_stub_text, (
        "gen_tauri_icons_stub.py must support `--clean` (remove stubs, preserve real artifacts via heuristic)."
    )


def test_icon_stub_generator_run_produces_all_expected_files():
    """End-to-end smoke: invoking the generator produces all expected stub files.

    This test actually invokes the generator (it's pure-Python + stdlib
    only — no Nuitka / gcc / swiftc needed) and verifies every expected
    stub file is created. Cleans up after itself via ``--clean``.
    """
    result = subprocess.run(
        [sys.executable, str(ICON_STUB_GENERATOR)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"gen_tauri_icons_stub.py failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # Verify the 4 PNG icons were created.
    src_tauri = PROJECT_ROOT / "src-tauri"
    expected_icons = [
        src_tauri / "icons" / "32x32.png",
        src_tauri / "icons" / "128x128.png",
        src_tauri / "icons" / "128x128@2x.png",
        src_tauri / "icons" / "icon.png",
    ]
    missing = [p for p in expected_icons if not p.exists()]
    assert not missing, f"missing generated PNG icons: {missing}"
    # Verify each PNG is non-trivial (>100 bytes — a valid PNG with IHDR + IDAT + IEND).
    for p in expected_icons:
        assert p.stat().st_size > 100, (
            f"{p.name} is suspiciously small ({p.stat().st_size} bytes); "
            "expected a valid PNG with signature + IHDR + IDAT + IEND."
        )
    # Verify the 6 stub sidecar binaries were created.
    expected_sidecars = [
        src_tauri / "bin" / "python-sidecar-x86_64-pc-windows-msvc.exe",
        src_tauri / "bin" / "python-sidecar-aarch64-pc-windows-msvc.exe",
        src_tauri / "bin" / "python-sidecar-x86_64-apple-darwin",
        src_tauri / "bin" / "python-sidecar-aarch64-apple-darwin",
        src_tauri / "bin" / "python-sidecar-x86_64-unknown-linux-gnu",
        src_tauri / "bin" / "python-sidecar-aarch64-unknown-linux-gnu",
    ]
    missing_sidecars = [p for p in expected_sidecars if not p.exists()]
    assert not missing_sidecars, f"missing generated sidecar stubs: {missing_sidecars}"
    # Verify the 3 native-listener stubs were created.
    expected_native = [
        src_tauri / "resources" / "native" / "windows-key-listener.exe",
        src_tauri / "resources" / "native" / "macos-key-listener",
        src_tauri / "resources" / "native" / "linux-key-listener",
    ]
    missing_native = [p for p in expected_native if not p.exists()]
    assert not missing_native, f"missing generated native stubs: {missing_native}"
    # Cleanup so we don't pollute the repo.
    cleanup = subprocess.run(
        [sys.executable, str(ICON_STUB_GENERATOR), "--clean"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    assert cleanup.returncode == 0, (
        f"gen_tauri_icons_stub.py --clean failed:\n--- stdout ---\n{cleanup.stdout}\n--- stderr ---\n{cleanup.stderr}"
    )


# ─── Known gaps (assert the gap is present; DO NOT fix) ──────────────────────
def test_known_gap_orchestrator_only_builds_host_platform(orchestrator_text: str):
    """GAP-3 (documented): the orchestrator only builds for the HOST platform.

    ADR-0020 §4: Nuitka cannot cross-compile. A single host CANNOT
    produce all 6 sidecar triples + all 6 prewarm triples + all 3 native
    listeners. The CI matrix covers the cross-platform case via separate
    runners; the orchestrator is the local-developer analog only.

    This test ASSERTS the gap is present (the orchestrator selects the
    host platform via a ``case "$(uname -s)"`` branch + dispatches only
    to the matching per-platform script). DO NOT fix this — it's a
    fundamental limitation of Nuitka, not a bug.
    """
    # The orchestrator must dispatch on the host platform.
    assert "uname -s" in orchestrator_text, "build_tauri_all.sh must detect the host platform via `uname -s`."
    # The 3 platform case branches must be present.
    for host_pattern in ("Darwin*", "MINGW*|MSYS*|CYGWIN*", "Linux*"):
        assert host_pattern in orchestrator_text, (
            f"build_tauri_all.sh must have a `{host_pattern})` case branch for host platform detection."
        )


def test_known_gap_orchestrator_does_not_preflight_artifacts(orchestrator_text: str):
    """GAP-4 (documented): the orchestrator does NOT pre-flight the artifact set
    before invoking ``cargo tauri build``.

    If ``SKIP_SIDECAR=1`` is set (or a prior sidecar build silently
    failed), the ``cargo tauri build`` step fails with a clear error
    ("resource path 'bin/python-sidecar-<triple>' doesn't exist") — but
    only AFTER the (slow) Rust compile. A pre-flight check would catch
    this in <1 s.

    This test ASSERTS the gap is present (the orchestrator does NOT
    contain a "verify all expected artifacts exist" check before
    Phase 1c). DO NOT fix this — it's a polish item, not a correctness
    bug; the error message from Tauri is clear.
    """
    # The orchestrator must NOT have a pre-flight check that verifies
    # the sidecar binaries exist. (If it did, the orchestrator would
    # contain a `for triple in ...` loop checking `bin/python-sidecar-$triple`
    # existence — which it does NOT.)
    has_preflight = "python-sidecar-" in orchestrator_text and "test -f" in orchestrator_text
    assert not has_preflight, (
        "GAP-4 appears to be CLOSED: build_tauri_all.sh now pre-flights "
        "the sidecar artifact set. Remove the gap documentation from this "
        "test file's module docstring + this test."
    )
