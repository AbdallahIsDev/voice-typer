r"""MIG-1.8 Phase 1 — externalBin per-arch wiring validation (cross-platform).

This is the **Phase 1 wiring check** for the MIG-1.8 cross-platform
externalBin + resources configuration. It validates that
``src-tauri/tauri.conf.json`` and ``src-tauri/src/sidecar/spawn.rs``
are wired correctly for all **3 platforms × 2 archs = 6 target triples**:

    - x86_64-pc-windows-msvc      (Windows x64)
    - aarch64-pc-windows-msvc     (Windows ARM64)
    - x86_64-apple-darwin         (macOS Intel)
    - aarch64-apple-darwin        (macOS Apple Silicon)
    - x86_64-unknown-linux-gnu    (Linux x64)
    - aarch64-unknown-linux-gnu   (Linux ARM64)

Scope (ADR-0020 §4.1 + §7 + §6.4 + §5):

1. **externalBin (base name + Tauri-appended triple).**
   ``tauri.conf.json`` declares
   ``bundle.externalBin: ["bin/python-sidecar", "bin/voice-typer-worker"]``
   (Tauri appends the Rust target triple at spawn time — ADR-0020 §7
   "externalBin per-arch naming" + §4.1). The actual on-disk binaries
   are ``src-tauri/bin/python-sidecar-<triple>[.exe]`` and
   ``voice-typer-worker-<triple>[.exe]``. The base-name form is the
   contract the ``app.shell().sidecar(...)`` Rust API uses; the
   per-triple suffix is appended internally by ``tauri-plugin-shell``
   at spawn time. The ML worker became the second externalBin in the
   runtime-pack split (master plan §6.2 P-1), replacing the per-arch
   ``prewarm-<triple>`` bundle.resources (retired with the standalone
   prewarm binary).

2. **resources (per-platform native hotkey binaries only).**
   ``bundle.resources`` MUST include the 3 native hotkey binaries
   mandated by ADR-0020 §7:

       resources/native/windows-key-listener.exe
       resources/native/macos-key-listener
       resources/native/linux-key-listener

   Native hotkey binaries are ``resources`` (NOT ``externalBin``) because
   they are spawned by the Python sidecar — ADR-0020 §6.4 + §7. The old
   per-arch prewarm resources are GONE (asserted absent below) — the
   worker exe is spawned through externalBin, not extracted as a
   resource.

3. **spawn.rs::target_triple_for() maps all 6 (arch, os) combos.**
   The pure predicate ``target_triple_for(arch, os) -> &str`` in
   ``src-tauri/src/sidecar/spawn.rs`` is the single source of truth for
   the per-triple suffix used to resolve BOTH externalBin binaries
   (``python-sidecar-<triple>[.exe]`` via Tauri, and
   ``voice-typer-worker-<triple>[.exe]`` via
   ``platform::worker_path::worker_exe_path_from_env``). Tauri's own
   ``externalBin`` resolver uses the same triple logic internally.
   All 6 supported (arch, os) combos must map to the exact triple
   string Tauri expects — ADR-0020 §4.1.

4. **Shell scope + capabilities grant spawn on the sidecar.**
   Tauri v2 ships zero permissions by default; the
   ``main-runtime.capability`` JSON in
   ``src-tauri/capabilities/main-runtime.json`` must grant
   ``shell:allow-spawn``, AND ``tauri.conf.json``'s
   ``plugins.shell.scope`` must include the
   ``{"name": "bin/python-sidecar", "sidecar": true}`` entry so the
   spawn is scoped to the sidecar binary only (ADR-0020 §7).

This file does NOT spawn any real process. Every test is a static
assertion over the JSON config + Rust source as text — same pattern as
the MIG-1.5/1.6/1.7 ``test_externalbin_spawn_*.py`` files.

VALIDATE ON HOST
================

This file is the Linux-sandbox wiring check. The actual cross-platform
build + runtime validation MUST be executed by a human on real hosts
(ADR-0020 §6 / runbook). One host per platform × arch combo:

**VALIDATE ON HOST — Windows x64 (x86_64-pc-windows-msvc)**::

    # 1. Build the sidecar .exe via Nuitka (gate check 1)
    python -m nuitka --onefile voice_typer/server/ipc_server.py \
        --output-dir=src-tauri/bin \
        --output-filename=python-sidecar-x86_64-pc-windows-msvc.exe
    ls src-tauri/bin/python-sidecar-x86_64-pc-windows-msvc.exe  # must exist

    # 2. Build the Tauri bundle for the x86_64 target triple
    cd src-tauri
    cargo tauri build --target x86_64-pc-windows-msvc
    cd ..

    # 3. Install + launch the NSIS installer
    #    target/x86_64-pc-windows-msvc/release/bundle/nsis/*-setup.exe

    # 4. Within 30 s of launch, verify the sidecar spawned with an
    #    ephemeral port + the per-arch prewarm binary resolved.
    type "%APPDATA%\voice-typer\logs\voice-typer.log" | findstr "server_started"
    # Expected: [SIDECAR] server_started port=<ephemeral>

    # 5. Confirm the ML worker exe exists in the runtime-pack dir:
    dir "%LOCALAPPDATA%\voice-typer\runtime-pack\<version>\voice-typer-worker-x86_64-pc-windows-msvc.exe"

**VALIDATE ON HOST — Windows ARM64 (aarch64-pc-windows-msvc)**::

    # Cross-compile from an x64 host (requires ARM64 toolchain).
    rustup target add aarch64-pc-windows-msvc
    cd src-tauri
    cargo tauri build --target aarch64-pc-windows-msvc
    cd ..
    # Install target/aarch64-pc-windows-msvc/release/bundle/nsis/*-setup.exe
    # on a Windows-on-ARM device (Surface Pro X, Lenovo ThinkPad X13s).
    dir "%LOCALAPPDATA%\voice-typer\runtime-pack\<version>\voice-typer-worker-aarch64-pc-windows-msvc.exe"

**VALIDATE ON HOST — macOS Intel (x86_64-apple-darwin)**::

    # On an Intel Mac (or via `arch -x86_64` on Apple Silicon):
    cd src-tauri
    cargo tauri build --target x86_64-apple-darwin
    cd ..
    open target/x86_64-apple-darwin/release/bundle/dmg/*.dmg
    # Drag Voice Typer.app to /Applications, launch it.
    tail -n 50 ~/Library/Application\ Support/voice-typer/logs/voice-typer.log \
        | grep server_started
    ls ~/Library/Application\ Support/voice-typer/runtime-pack/<version>/ \
        | grep voice-typer-worker-x86_64-apple-darwin

**VALIDATE ON HOST — macOS Apple Silicon (aarch64-apple-darwin)**::

    cd src-tauri
    cargo tauri build --target aarch64-apple-darwin
    cd ..
    open target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
    tail -n 50 ~/Library/Application\ Support/voice-typer/logs/voice-typer.log \
        | grep server_started
    ls ~/Library/Application\ Support/voice-typer/runtime-pack/<version>/ \
        | grep voice-typer-worker-aarch64-apple-darwin

**VALIDATE ON HOST — Linux x64 (x86_64-unknown-linux-gnu)**::

    cd src-tauri
    cargo tauri build --target x86_64-unknown-linux-gnu
    cd ..
    # Install the AppImage / deb / rpm bundle, then launch.
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log \
        | grep server_started
    # The worker exe lives in the runtime-pack data dir:
    ls ~/.local/share/voice-typer/runtime-pack/<version>/voice-typer-worker-x86_64-unknown-linux-gnu

**VALIDATE ON HOST — Linux ARM64 (aarch64-unknown-linux-gnu)**::

    # On an ARM64 Linux host (Raspberry Pi 5, Ampere Altra, AWS Graviton).
    rustup target add aarch64-unknown-linux-gnu
    cd src-tauri
    cargo tauri build --target aarch64-unknown-linux-gnu
    cd ..
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log \
        | grep server_started
    ls ~/.local/share/voice-typer/runtime-pack/<version>/voice-typer-worker-aarch64-unknown-linux-gnu

Each host run validates two things:
1. The ``externalBin`` base names resolved to the right per-triple
   sidecar / worker binaries (sidecar spawned within 30 s, log shows
   ``server_started port=<ephemeral>`` — never a fixed port like 9876).
2. The per-arch worker exe is discoverable by
   ``worker_path::worker_exe_path_from_env`` in the runtime-pack dir
   (the file exists at the versioned path the path resolver returns).

References:
- ADR-0020 §4.1 — per-arch externalBin binary naming + ``target_triple_for``.
- ADR-0020 §5 — prewarm binary is a ``bundle.resource`` (NOT externalBin).
- ADR-0020 §6.4 — native hotkey binary is a ``bundle.resource``.
- ADR-0020 §7 — ``tauri.conf.json`` externalBin + resources + shell scope
  + capabilities JSON contract.
- ADR-0020 §15 — six target triples (3 platforms × 2 archs) are the
  full MIG-1.8 cross-platform build matrix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ─── Repo path resolution ──────────────────────────────────────────────
# Tests run from the repo root, but every path is resolved relative to
# this file's location so the tests pass regardless of cwd.
# parents[0]=mig18, [1]=tauri, [2]=tests, [3]=voice-typer (repo root)
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _SRC_TAURI / "tauri.conf.json"
_SPAWN_RS = _SRC_TAURI / "src" / "sidecar" / "spawn.rs"
_CAPABILITIES_DIR = _SRC_TAURI / "capabilities"
_MIGRATE_RUNTIME_CAPABILITY = _CAPABILITIES_DIR / "main-runtime.json"


# ─── Expected wiring constants (single source of truth) ────────────────

#: ADR-0020 §4.1 + §7: externalBin must list the base name; Tauri
#: appends the Rust target triple at spawn time.
EXPECTED_EXTERNAL_BIN_BASENAME = "bin/python-sidecar"

#: ADR-0020 §6.4: native hotkey binaries, one per platform.
EXPECTED_NATIVE_RESOURCES = [
    "resources/native/windows-key-listener.exe",
    "resources/native/macos-key-listener",
    "resources/native/linux-key-listener",
]

#: Master plan §6.2 P-1: the second externalBin base name (the ML
#: worker exe that absorbed the retired standalone prewarm binary).
EXPECTED_WORKER_BIN_BASENAME = "bin/voice-typer-worker"

#: ADR-0020 §4.1: ``target_triple_for(arch, os)`` must map every one of
#: these (arch, os, expected_triple) tuples. The triple strings are
#: the exact suffixes Tauri's ``externalBin`` resolver appends to the
#: base name to find the per-arch binary on disk.
EXPECTED_TARGET_TRIPLES = [
    ("x86_64", "windows", "x86_64-pc-windows-msvc"),
    ("aarch64", "windows", "aarch64-pc-windows-msvc"),
    ("x86_64", "macos", "x86_64-apple-darwin"),
    ("aarch64", "macos", "aarch64-apple-darwin"),
    ("x86_64", "linux", "x86_64-unknown-linux-gnu"),
    ("aarch64", "linux", "aarch64-unknown-linux-gnu"),
]

#: ADR-0020 §7: capability identifier referenced by tauri.conf.json's
#: ``app.security.capabilities`` list.
EXPECTED_CAPABILITY_IDENTIFIER = "main-runtime"


# ─── Shared fixtures ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse src-tauri/tauri.conf.json."""
    assert _TAURI_CONF.exists(), f"tauri.conf.json not found: {_TAURI_CONF}"
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


def _read_spawn_module() -> str:
    """Concatenate the spawn module sources (spawn.rs + spawn/*.rs).

    EO-33 split the former single-file ``sidecar/spawn.rs`` into an
    orchestrator (``spawn.rs``) + concern submodules
    (``spawn/dev_mode.rs``, ``spawn/release_mode.rs``,
    ``spawn/handshake.rs``, ``spawn/env_allowlist.rs``,
    ``spawn/target_triple.rs``; ``spawn/prewarm.rs`` was deleted when
    prewarm became a worker startup phase — master plan §6.2 P-1). The
    gate assertions target the spawn module as a whole, so we read every
    file and join them.
    """
    files = [_SPAWN_RS] + sorted(_SPAWN_RS.parent.joinpath("spawn").glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


@pytest.fixture(scope="module")
def spawn_rs_source() -> str:
    """Read the spawn module sources (spawn.rs + spawn/*.rs) as text."""
    assert _SPAWN_RS.exists(), f"spawn.rs not found: {_SPAWN_RS}"
    return _read_spawn_module()


@pytest.fixture(scope="module")
def migrate_runtime_capability() -> dict:
    """Load + parse the main-runtime capability JSON."""
    assert _MIGRATE_RUNTIME_CAPABILITY.exists(), f"capability file not found: {_MIGRATE_RUNTIME_CAPABILITY}"
    return json.loads(_MIGRATE_RUNTIME_CAPABILITY.read_text(encoding="utf-8"))


# ─── Test 1: externalBin lists the python-sidecar base name ────────────


def test_tauri_conf_external_bin_lists_python_sidecar_basename(
    tauri_conf,
) -> None:
    """ADR-0020 §4.1 + §7: externalBin must contain ``bin/python-sidecar``.

    The Tauri v2 ``externalBin`` mechanism appends the Rust target
    triple at runtime, so the JSON entry is the binary **base name**
    (no triple suffix, no ``.exe``). The Rust host calls
    ``app.shell().sidecar("python-sidecar")`` — tauri-plugin-shell
    appends the triple internally and finds the right per-arch binary
    on disk (e.g. ``src-tauri/bin/python-sidecar-x86_64-pc-windows-msvc.exe``
    on Windows x64).
    """
    bundle = tauri_conf.get("bundle", {})
    assert "externalBin" in bundle, "bundle.externalBin must exist (ADR-0020 §7)"
    external_bin = bundle["externalBin"]
    assert isinstance(external_bin, list), "externalBin must be a list"
    assert EXPECTED_EXTERNAL_BIN_BASENAME in external_bin, (
        f"externalBin must contain {EXPECTED_EXTERNAL_BIN_BASENAME!r} (Tauri appends the target triple at spawn time)"
    )


def test_tauri_conf_external_bin_has_no_triple_suffix_entries(
    tauri_conf,
) -> None:
    """ADR-0020 §4.1 + §7: externalBin must NOT contain triple-suffixed entries.

    Tauri v2's ``externalBin`` mechanism appends the target triple at
    runtime — listing the per-triple binary names explicitly would
    cause Tauri to look for ``python-sidecar-<triple>-<triple>[.exe]``
    (double-suffixed) and fail with "sidecar not found" at launch.
    The base-name-only form is the contract.
    """
    external_bin = tauri_conf.get("bundle", {}).get("externalBin", [])
    triple_suffix_re = re.compile(
        r"-x86_64-pc-windows-msvc"
        r"|-aarch64-pc-windows-msvc"
        r"|-x86_64-apple-darwin"
        r"|-aarch64-apple-darwin"
        r"|-x86_64-unknown-linux-gnu"
        r"|-aarch64-unknown-linux-gnu"
    )
    for entry in external_bin:
        assert not triple_suffix_re.search(entry), (
            f"externalBin entry {entry!r} must NOT contain a target-triple "
            f"suffix — Tauri appends the triple at runtime. Use the base "
            f"name only (e.g. {EXPECTED_EXTERNAL_BIN_BASENAME!r})."
        )


# ─── Test 2: resources include all 9 required binaries ─────────────────


def test_tauri_conf_resources_is_non_empty_list(tauri_conf) -> None:
    """ADR-0020 §5 + §6.4 + §7: ``bundle.resources`` must be a non-empty list."""
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    assert isinstance(resources, list) and resources, "bundle.resources must be a non-empty list (ADR-0020 §7)"


@pytest.mark.parametrize(
    "resource",
    EXPECTED_NATIVE_RESOURCES,
    ids=[r.split("/")[-1] for r in EXPECTED_NATIVE_RESOURCES],
)
def test_tauri_conf_resources_include_native_hotkey_binary(tauri_conf, resource: str) -> None:
    """ADR-0020 §6.4 + §7: every native hotkey binary must be a resource.

    The native hotkey binaries are spawned by the **Python sidecar**
    (not by Tauri), so they must be ``bundle.resources`` extracted to
    ``resourceDir/native/`` and discovered by the sidecar via the
    ``VOICE_TYPER_NATIVE_DIR`` env var (ADR-0020 §6.4). One per
    platform: ``windows-key-listener.exe``, ``macos-key-listener``,
    ``linux-key-listener``.
    """
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    assert resource in resources, (
        f"bundle.resources must include {resource!r} "
        f"(native hotkey binary spawned by the Python sidecar, "
        f"ADR-0020 §6.4 + §7)"
    )


def test_tauri_conf_external_bin_lists_worker(tauri_conf) -> None:
    """Master plan §6.2 P-1: externalBin must contain ``bin/voice-typer-worker``.

    The ML worker exe replaced the retired standalone prewarm binary
    and is spawned through Tauri's ``externalBin`` mechanism (same
    base-name + triple-appended contract as the python-sidecar).
    """
    external_bin = tauri_conf.get("bundle", {}).get("externalBin", [])
    assert EXPECTED_WORKER_BIN_BASENAME in external_bin, (
        f"bundle.externalBin must contain {EXPECTED_WORKER_BIN_BASENAME!r} (the ML worker exe — master plan §6.2 P-1)"
    )


def test_tauri_conf_resources_exclude_prewarm_binaries(tauri_conf) -> None:
    """Master plan §6.2 P-1: per-arch prewarm resources must NOT exist.

    The standalone prewarm binary was retired; the worker exe (which
    runs the warm phase) is an externalBin, not a bundle resource.
    Any ``resources/prewarm-*`` entry in tauri.conf.json is a
    regression — the old binaries no longer exist to be bundled, and
    the installers would bloat with dead entries.
    """
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    prewarm_entries = [r for r in resources if "prewarm" in r]
    assert not prewarm_entries, (
        f"bundle.resources must NOT contain prewarm binaries (retired, master plan §6.2 P-1) — got: {prewarm_entries}"
    )


def test_tauri_conf_resources_count_matches_minimum(tauri_conf) -> None:
    """ADR-0020 §7: resources must include the 3 mandated native entries.

    The 3 native hotkey binaries are the mandatory resource set. Extra
    entries (icons, model files, etc.) are permitted; missing any of
    the 3 is a hard fail. The prewarm resources are asserted absent in
    ``test_tauri_conf_resources_exclude_prewarm_binaries``.
    """
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    required = set(EXPECTED_NATIVE_RESOURCES)
    missing = required - set(resources)
    assert not missing, (
        f"bundle.resources is missing {len(missing)} mandated entr"
        f"{'y' if len(missing) == 1 else 'ies'}: {sorted(missing)}"
    )


# ─── Test 3: spawn.rs::target_triple_for() maps all 6 combos ───────────


@pytest.mark.parametrize(
    ("arch", "os", "expected_triple"),
    EXPECTED_TARGET_TRIPLES,
    ids=[f"{a}-{o}-{t}" for a, o, t in EXPECTED_TARGET_TRIPLES],
)
def test_spawn_rs_target_triple_for_maps_combo(spawn_rs_source, arch: str, os: str, expected_triple: str) -> None:
    """ADR-0020 §4.1: ``target_triple_for(arch, os)`` → exact triple string.

    The pure predicate ``target_triple_for(arch, os)`` in spawn.rs must
    return the exact triple string Tauri's ``externalBin`` resolver
    appends to the base name. ``prewarm_resource_path()`` uses the same
    triple to locate the per-arch prewarm binary at
    ``resourceDir/prewarm-<triple>[.exe]``. Mismatched triple strings
    cause "sidecar not found" / "prewarm binary not found" at runtime.
    """
    # The match arm: ("arch", "os") => "triple".into()
    pattern = (
        r'\(\s*"'
        + re.escape(arch)
        + r'"\s*,\s*"'
        + re.escape(os)
        + r'"\s*\)\s*=>\s*"'
        + re.escape(expected_triple)
        + r'"\.into\(\)'
    )
    assert re.search(pattern, spawn_rs_source), (
        f"spawn.rs::target_triple_for must map "
        f'("{arch}", "{os}") => "{expected_triple}".into() '
        f"(Tauri externalBin + prewarm_resource_path triple suffix, "
        f"ADR-0020 §4.1)"
    )


def test_spawn_rs_target_triple_for_has_six_known_arms(spawn_rs_source) -> None:
    """ADR-0020 §4.1 + §15: target_triple_for must cover all 6 combos.

    The MIG-1.8 cross-platform build matrix is 3 platforms × 2 archs
    = 6 target triples (Windows x64/ARM64, macOS Intel/Apple Silicon,
    Linux x64/ARM64). The match in ``target_triple_for`` must have an
    explicit arm for each — a missing arm means users on that arch
    see "sidecar not found" at launch.
    """
    # Extract the match body of target_triple_for.
    # The function signature is:
    #     pub(crate) fn target_triple_for(arch: &str, os: &str) -> String {
    #         match (arch, os) {
    #             ... arms ...
    #             _ => format!("{}-unknown-{}", arch, os),
    #         }
    #     }
    fn_body_re = re.compile(
        r"fn\s+target_triple_for\s*\([^)]*\)\s*->\s*String\s*\{",
        re.DOTALL,
    )
    assert fn_body_re.search(spawn_rs_source), (
        "spawn.rs must define `fn target_triple_for(arch: &str, os: &str) -> String`"
    )
    # Count the explicit arm count by searching for the 6 known pairs.
    found_combos = []
    for arch, os, _triple in EXPECTED_TARGET_TRIPLES:
        arm_re = re.compile(r'\(\s*"' + re.escape(arch) + r'"\s*,\s*"' + re.escape(os) + r'"\s*\)\s*=>')
        if arm_re.search(spawn_rs_source):
            found_combos.append((arch, os))
    missing_combos = [(a, o) for a, o, _ in EXPECTED_TARGET_TRIPLES if (a, o) not in found_combos]
    assert not missing_combos, (
        f"target_triple_for is missing explicit arms for {len(missing_combos)} "
        f"combo(s): {missing_combos} (expected all 6 of "
        f"{[(a, o) for a, o, _ in EXPECTED_TARGET_TRIPLES]}, "
        f"ADR-0020 §4.1 + §15)"
    )


def test_spawn_rs_current_target_triple_delegates_to_target_triple_for(
    spawn_rs_source,
) -> None:
    """ADR-0020 §4.1: ``current_target_triple`` reads runtime arch + os.

    The host's actual target triple is computed at runtime from
    ``std::env::consts::ARCH`` + ``std::env::consts::OS`` (the same
    source Tauri's ``externalBin`` resolver uses). It must delegate to
    the pure ``target_triple_for`` predicate so the 6-combo mapping is
    unit-testable without spawning a process.
    """
    assert re.search(
        r"fn\s+current_target_triple\s*\(\s*\)\s*->\s*String\s*\{[^}]*"
        r"target_triple_for\s*\(\s*std::env::consts::ARCH\s*,\s*"
        r"std::env::consts::OS\s*\)",
        spawn_rs_source,
        re.DOTALL,
    ), (
        "current_target_triple must delegate to target_triple_for with "
        "std::env::consts::ARCH + std::env::consts::OS (single source of "
        "truth for the per-arch triple, ADR-0020 §4.1)"
    )


def test_worker_exe_path_uses_current_target_triple() -> None:
    """Master plan §6.2 P-1: ``worker_exe_path_from_env`` uses the per-arch triple.

    The worker exe is named ``voice-typer-worker-<triple>[.exe]`` (one
    per target triple) inside the runtime-pack dir.
    ``platform/worker_path.rs::worker_exe_path_from_env`` must call
    ``current_target_triple()`` to resolve the suffix — NOT hardcode a
    single arch (which would silently break on the other 5 triples).
    """
    worker_path_src = (_SRC_TAURI / "src" / "platform" / "worker_path.rs").read_text(encoding="utf-8")
    assert "fn worker_exe_path_from_env" in worker_path_src, "worker_path.rs must define `worker_exe_path_from_env`"
    assert "current_target_triple" in worker_path_src, (
        "worker_exe_path_from_env must call current_target_triple() to resolve "
        "the per-arch worker exe name (master plan §6.2 P-1)"
    )
    # The worker name template must include the triple (+ Windows .exe
    # suffix appended conditionally on cfg!(windows)).
    assert re.search(
        r'WORKER_BIN_BASE_NAME\s*:\s*&str\s*=\s*"voice-typer-worker"',
        worker_path_src,
    ), 'worker_path.rs must define WORKER_BIN_BASE_NAME = "voice-typer-worker" (master plan §6.2 P-1)'
    assert re.search(
        r'format!\s*\(\s*"\{\}-\{\}\{\}"\s*,\s*WORKER_BIN_BASE_NAME\s*,\s*triple\s*,\s*suffix',
        worker_path_src,
    ), (
        "worker_exe_path_from_env must format the binary name as "
        "`voice-typer-worker-{triple}{suffix}` (master plan §6.2 P-1)"
    )
    # The Windows .exe suffix must be conditional on cfg!(windows) — a
    # hardcoded `.exe` would break macOS/Linux; a hardcoded empty suffix
    # would break Windows.
    assert re.search(r"cfg!\s*\(\s*windows\s*\)", worker_path_src), (
        "worker_exe_path_from_env must use cfg!(windows) to conditionally "
        "append the .exe suffix on Windows only (master plan §6.2 P-1)"
    )


# ─── Test 4: shell scope + capabilities grant spawn on the sidecar ─────


def test_tauri_conf_shell_config_is_v2_valid(tauri_conf) -> None:
    """ADR-0020 §7: ``plugins.shell`` must be v2-valid (``{"open": false}``).

    tauri-plugin-shell v2 accepts a single ``open`` config key and
    denies unknown fields. The v1-style ``sidecar`` / ``scope`` keys
    fail app startup with "unknown field `scope`, expected `open`"
    (found on the first Windows host run). Both externalBins
    (python-sidecar + voice-typer-worker) are spawned by the Rust host
    via ``app.shell().sidecar(...)`` — not ACL-scoped through
    ``plugins.shell``; the JS-facing ``shell:allow-spawn`` grant keeps
    its deny-all default scope.
    """
    shell = tauri_conf.get("plugins", {}).get("shell")
    assert shell == {"open": False}, (
        "plugins.shell must be exactly {'open': false} — tauri-plugin-shell "
        "v2 rejects 'sidecar'/'scope' keys at startup ('unknown field "
        f"`scope`, expected `open`'); got {shell!r}"
    )


def test_tauri_conf_capabilities_reference_migrate_runtime(
    tauri_conf,
) -> None:
    """ADR-0020 §7: ``app.security.capabilities`` must reference ``main-runtime``.

    The capability identifier in the JSON must match the file at
    ``src-tauri/capabilities/main-runtime.json`` — Tauri resolves
    capability names by filename stem, not by the ``identifier`` field
    inside the JSON.
    """
    capabilities = tauri_conf.get("app", {}).get("security", {}).get("capabilities", [])
    assert isinstance(capabilities, list) and capabilities, (
        "app.security.capabilities must be a non-empty list (ADR-0020 §7)"
    )
    assert EXPECTED_CAPABILITY_IDENTIFIER in capabilities, (
        f"app.security.capabilities must include "
        f"{EXPECTED_CAPABILITY_IDENTIFIER!r} (the capability JSON file is "
        f"src-tauri/capabilities/main-runtime.json, ADR-0020 §7)"
    )


def test_capabilities_json_grants_shell_allow_spawn(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7: the main-runtime capability must grant ``shell:allow-spawn``.

    Tauri v2 ships zero permissions by default. The capability JSON
    must explicitly grant ``shell:allow-spawn`` for the Rust host to
    spawn the ``python-sidecar`` binary via the ``app.shell().sidecar()``
    API. Without this grant, the spawn is silently blocked at runtime
    (no compile error — the WebView's ``invoke('dispatch', ...)`` call
    just hangs).
    """
    permissions = migrate_runtime_capability.get("permissions", [])
    assert isinstance(permissions, list) and permissions, (
        "main-runtime.json must have a non-empty 'permissions' list (ADR-0020 §7)"
    )
    assert "shell:allow-spawn" in permissions, (
        "main-runtime.json must grant 'shell:allow-spawn' so the Rust "
        "host can spawn python-sidecar via the externalBin API "
        "(Tauri v2 ships zero permissions by default, ADR-0020 §7)"
    )


def test_capabilities_json_grants_shell_allow_kill(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7 + §10: the capability must grant ``shell:allow-kill``.

    The crash-supervisor backstop (ADR-0020 §10) force-kills the
    sidecar via the tauri-plugin-shell ``CommandChild::kill`` API when
    the cooperative ``{"type":"shutdown"}`` WS message does not ack
    within the shutdown timeout. Without ``shell:allow-kill``, the
    force-kill is silently blocked and the zombie sidecar keeps its
    WebSocket port bound — the next respawn fails with "address in use".
    """
    permissions = migrate_runtime_capability.get("permissions", [])
    assert "shell:allow-kill" in permissions, (
        "main-runtime.json must grant 'shell:allow-kill' for the force-kill backstop (ADR-0020 §7 + §10)"
    )


def test_capabilities_json_identifier_matches_filename(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7: the capability's ``identifier`` field must match its filename.

    Tauri v2 loads capabilities from ``src-tauri/capabilities/<name>.json``
    by filename stem, and the ``identifier`` field inside the JSON must
    match the filename stem (else Tauri rejects the capability at build
    time). This guards against a rename of the JSON file that breaks
    the ``app.security.capabilities: ["main-runtime"]`` reference in
    ``tauri.conf.json``.
    """
    assert migrate_runtime_capability.get("identifier") == EXPECTED_CAPABILITY_IDENTIFIER, (
        f"main-runtime.json's 'identifier' field must be "
        f"{EXPECTED_CAPABILITY_IDENTIFIER!r} (must match the filename stem, "
        f"ADR-0020 §7)"
    )
