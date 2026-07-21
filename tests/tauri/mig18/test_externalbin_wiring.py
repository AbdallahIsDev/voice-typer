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
   ``tauri.conf.json`` declares ``bundle.externalBin: ["bin/python-sidecar"]``
   (Tauri appends the Rust target triple at spawn time — ADR-0020 §7
   "externalBin per-arch naming" + §4.1). The actual on-disk binaries
   are ``src-tauri/bin/python-sidecar-<triple>[.exe]``. The base-name
   form is the contract the ``app.shell().sidecar("python-sidecar")``
   Rust API uses; the per-triple suffix is appended internally by
   ``tauri-plugin-shell`` at spawn time.

2. **resources (per-arch prewarm + per-platform native hotkey binaries).**
   ``bundle.resources`` MUST include all 9 entries mandated by ADR-0020 §7:

       resources/native/windows-key-listener.exe
       resources/native/macos-key-listener
       resources/native/linux-key-listener
       resources/prewarm-x86_64-pc-windows-msvc.exe
       resources/prewarm-aarch64-pc-windows-msvc.exe
       resources/prewarm-x86_64-apple-darwin
       resources/prewarm-aarch64-apple-darwin
       resources/prewarm-x86_64-unknown-linux-gnu
       resources/prewarm-aarch64-unknown-linux-gnu

   Native hotkey binaries are ``resources`` (NOT ``externalBin``) because
   they are spawned by the Python sidecar — ADR-0020 §6.4 + §7. Prewarm
   binaries are ``resources`` (NOT ``externalBin``) because they are
   scheduled by the OS (Windows Task Scheduler / macOS LaunchAgent /
   Linux systemd user timer) — ADR-0020 §5.

3. **spawn.rs::target_triple_for() maps all 6 (arch, os) combos.**
   The pure predicate ``target_triple_for(arch, os) -> &str`` in
   ``src-tauri/src/sidecar/spawn.rs`` is the single source of truth for
   the per-triple suffix used to resolve the prewarm binary path
   (``prewarm-<triple>[.exe]`` in ``prewarm_resource_path()``). Tauri's
   own ``externalBin`` resolver uses the same triple logic internally.
   All 6 supported (arch, os) combos must map to the exact triple
   string Tauri expects — ADR-0020 §4.1.

4. **Shell scope + capabilities grant spawn on the sidecar.**
   Tauri v2 ships zero permissions by default; the
   ``migrate-runtime.capability`` JSON in
   ``src-tauri/capabilities/migrate-runtime.json`` must grant
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

    # 5. Confirm the bundled prewarm binary exists under resourceDir:
    dir "%LOCALAPPDATA%\Programs\Voice Typer\resources\prewarm-x86_64-pc-windows-msvc.exe"

**VALIDATE ON HOST — Windows ARM64 (aarch64-pc-windows-msvc)**::

    # Cross-compile from an x64 host (requires ARM64 toolchain).
    rustup target add aarch64-pc-windows-msvc
    cd src-tauri
    cargo tauri build --target aarch64-pc-windows-msvc
    cd ..
    # Install target/aarch64-pc-windows-msvc/release/bundle/nsis/*-setup.exe
    # on a Windows-on-ARM device (Surface Pro X, Lenovo ThinkPad X13s).
    dir "%LOCALAPPDATA%\Programs\Voice Typer\resources\prewarm-aarch64-pc-windows-msvc.exe"

**VALIDATE ON HOST — macOS Intel (x86_64-apple-darwin)**::

    # On an Intel Mac (or via `arch -x86_64` on Apple Silicon):
    cd src-tauri
    cargo tauri build --target x86_64-apple-darwin
    cd ..
    open target/x86_64-apple-darwin/release/bundle/dmg/*.dmg
    # Drag Voice Typer.app to /Applications, launch it.
    tail -n 50 ~/Library/Application\ Support/voice-typer/logs/voice-typer.log \
        | grep server_started
    ls "/Applications/Voice Typer.app/Contents/Resources/resources/" \
        | grep prewarm-x86_64-apple-darwin

**VALIDATE ON HOST — macOS Apple Silicon (aarch64-apple-darwin)**::

    cd src-tauri
    cargo tauri build --target aarch64-apple-darwin
    cd ..
    open target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
    tail -n 50 ~/Library/Application\ Support/voice-typer/logs/voice-typer.log \
        | grep server_started
    ls "/Applications/Voice Typer.app/Contents/Resources/resources/" \
        | grep prewarm-aarch64-apple-darwin

**VALIDATE ON HOST — Linux x64 (x86_64-unknown-linux-gnu)**::

    cd src-tauri
    cargo tauri build --target x86_64-unknown-linux-gnu
    cd ..
    # Install the AppImage / deb / rpm bundle, then launch.
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log \
        | grep server_started
    # Resource path under AppImage is a FUSE mount; under deb/rpm it is
    # /usr/lib/voice-typer/resources/.
    ls /usr/lib/voice-typer/resources/prewarm-x86_64-unknown-linux-gnu

**VALIDATE ON HOST — Linux ARM64 (aarch64-unknown-linux-gnu)**::

    # On an ARM64 Linux host (Raspberry Pi 5, Ampere Altra, AWS Graviton).
    rustup target add aarch64-unknown-linux-gnu
    cd src-tauri
    cargo tauri build --target aarch64-unknown-linux-gnu
    cd ..
    tail -n 50 ~/.local/share/voice-typer/logs/voice-typer.log \
        | grep server_started
    ls /usr/lib/voice-typer/resources/prewarm-aarch64-unknown-linux-gnu

Each host run validates two things:
1. The ``externalBin`` base name resolved to the right per-triple
   sidecar binary (sidecar spawned within 30 s, log shows
   ``server_started port=<ephemeral>`` — never a fixed port like 9876).
2. The per-arch prewarm binary was extracted to ``resourceDir`` at
   install time and is discoverable by ``prewarm_resource_path()`` in
   spawn.rs (the file exists at the path Tauri's ``resource_dir()``
   returns).

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

#: ADR-0020 §5: per-arch prewarm binaries, one per target triple.
#: Windows prewarm binaries carry the ``.exe`` suffix; macOS + Linux do not.
EXPECTED_PREWARM_RESOURCES = [
    "resources/prewarm-x86_64-pc-windows-msvc.exe",
    "resources/prewarm-aarch64-pc-windows-msvc.exe",
    "resources/prewarm-x86_64-apple-darwin",
    "resources/prewarm-aarch64-apple-darwin",
    "resources/prewarm-x86_64-unknown-linux-gnu",
    "resources/prewarm-aarch64-unknown-linux-gnu",
]

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


@pytest.fixture(scope="module")
def spawn_rs_source() -> str:
    """Read src-tauri/src/sidecar/spawn.rs as text (for static assertions)."""
    assert _SPAWN_RS.exists(), f"spawn.rs not found: {_SPAWN_RS}"
    return _SPAWN_RS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migrate_runtime_capability() -> dict:
    """Load + parse the migrate-runtime capability JSON."""
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


@pytest.mark.parametrize(
    "resource",
    EXPECTED_PREWARM_RESOURCES,
    ids=[r.split("/")[-1] for r in EXPECTED_PREWARM_RESOURCES],
)
def test_tauri_conf_resources_include_per_arch_prewarm_binary(tauri_conf, resource: str) -> None:
    """ADR-0020 §5 + §7: every per-arch prewarm binary must be a resource.

    The prewarm binary is launched by the OS-specific scheduler
    (Windows Task Scheduler, macOS LaunchAgent, Linux systemd user
    timer) — NOT spawned by Tauri — so it must be a ``bundle.resource``
    extracted to ``resourceDir``. One binary per target triple (6
    total: x86_64 + aarch64 × Windows/macOS/Linux). Windows prewarm
    binaries carry the ``.exe`` suffix; macOS + Linux do not.
    """
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    assert resource in resources, (
        f"bundle.resources must include {resource!r} "
        f"(per-arch prewarm binary for the {resource.split('-')[1]} "
        f"target triple, ADR-0020 §5 + §7)"
    )


def test_tauri_conf_resources_count_matches_minimum(tauri_conf) -> None:
    """ADR-0020 §7: resources must include at least 9 mandated entries.

    The 3 native hotkey binaries + 6 per-arch prewarm binaries = 9
    mandatory entries. Extra entries (icons, model files, etc.) are
    permitted; missing any of the 9 is a hard fail.
    """
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    required = set(EXPECTED_NATIVE_RESOURCES) | set(EXPECTED_PREWARM_RESOURCES)
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


def test_spawn_rs_prewarm_resource_path_uses_current_target_triple(
    spawn_rs_source,
) -> None:
    """ADR-0020 §4.1 + §5: ``prewarm_resource_path`` uses the per-arch triple.

    The prewarm binary is named ``prewarm-<triple>[.exe]`` (one per
    target triple). ``prewarm_resource_path(app)`` must call
    ``current_target_triple()`` to resolve the suffix — NOT hardcode a
    single arch (which would silently break on the other 5 triples).
    """
    assert "fn prewarm_resource_path" in spawn_rs_source, (
        "spawn.rs must define `prewarm_resource_path(app: &tauri::AppHandle) -> Result<String, String>`"
    )
    assert "current_target_triple" in spawn_rs_source, (
        "prewarm_resource_path must call current_target_triple() to resolve "
        "the per-arch prewarm binary name (ADR-0020 §4.1 + §5)"
    )
    # The prewarm name template must include the triple (+ Windows .exe
    # suffix appended conditionally on cfg!(windows)). The format! call
    # must reference `triple` as the first format arg.
    assert re.search(
        r'format!\s*\(\s*"prewarm-\{\}[^"]*"\s*,\s*triple\b',
        spawn_rs_source,
    ), (
        "prewarm_resource_path must format the binary name as "
        "`prewarm-{triple}[.exe]` (one per-arch binary per target triple, "
        "ADR-0020 §5)"
    )
    # The Windows .exe suffix must be conditional on cfg!(windows) — a
    # hardcoded `.exe` would break macOS/Linux; a hardcoded empty suffix
    # would break Windows.
    assert re.search(r"cfg!\s*\(\s*windows\s*\)", spawn_rs_source), (
        "prewarm_resource_path must use cfg!(windows) to conditionally "
        "append the .exe suffix on Windows only (ADR-0020 §5)"
    )


# ─── Test 4: shell scope + capabilities grant spawn on the sidecar ─────


def test_tauri_conf_shell_scope_allows_python_sidecar(tauri_conf) -> None:
    """ADR-0020 §7: ``plugins.shell.scope`` must allow ``bin/python-sidecar``.

    Tauri v2 rejects an unconstrained ``shell:allow-spawn`` — the
    ``plugins.shell.scope`` list in ``tauri.conf.json`` must name the
    sidecar binary by its base name with ``sidecar: true`` so the
    spawn is scoped to ONLY the python-sidecar (no arbitrary command
    execution). Tauri enforces this scope at runtime IN ADDITION to the
    ``shell:allow-spawn`` capability in ``migrate-runtime.json``.
    """
    shell = tauri_conf.get("plugins", {}).get("shell", {})
    assert shell.get("sidecar") is True, (
        "plugins.shell.sidecar must be true to enable the externalBin API (ADR-0020 §7)"
    )
    scope = shell.get("scope", [])
    assert isinstance(scope, list) and scope, "plugins.shell.scope must be a non-empty list (ADR-0020 §7)"
    matches = [s for s in scope if s.get("name") == EXPECTED_EXTERNAL_BIN_BASENAME and s.get("sidecar") is True]
    assert matches, (
        f"plugins.shell.scope must include an entry "
        f"{{'name': {EXPECTED_EXTERNAL_BIN_BASENAME!r}, 'sidecar': true}} "
        f"(Tauri v2 requires explicit spawn scoping, ADR-0020 §7)"
    )


def test_tauri_conf_capabilities_reference_migrate_runtime(
    tauri_conf,
) -> None:
    """ADR-0020 §7: ``app.security.capabilities`` must reference ``migrate-runtime``.

    The capability identifier in the JSON must match the file at
    ``src-tauri/capabilities/migrate-runtime.json`` — Tauri resolves
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
        f"src-tauri/capabilities/migrate-runtime.json, ADR-0020 §7)"
    )


def test_capabilities_json_grants_shell_allow_spawn(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7: the migrate-runtime capability must grant ``shell:allow-spawn``.

    Tauri v2 ships zero permissions by default. The capability JSON
    must explicitly grant ``shell:allow-spawn`` for the Rust host to
    spawn the ``python-sidecar`` binary via the ``app.shell().sidecar()``
    API. Without this grant, the spawn is silently blocked at runtime
    (no compile error — the WebView's ``invoke('dispatch', ...)`` call
    just hangs).
    """
    permissions = migrate_runtime_capability.get("permissions", [])
    assert isinstance(permissions, list) and permissions, (
        "migrate-runtime.json must have a non-empty 'permissions' list (ADR-0020 §7)"
    )
    assert "shell:allow-spawn" in permissions, (
        "migrate-runtime.json must grant 'shell:allow-spawn' so the Rust "
        "host can spawn python-sidecar via the externalBin API "
        "(Tauri v2 ships zero permissions by default, ADR-0020 §7)"
    )


def test_capabilities_json_grants_shell_allow_kill(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7 + §10: the capability must grant ``shell:allow-kill``.

    The FT-1 crash-supervisor backstop (ADR-0020 §10) force-kills the
    sidecar via the tauri-plugin-shell ``CommandChild::kill`` API when
    the cooperative ``{"type":"shutdown"}`` WS message does not ack
    within the shutdown timeout. Without ``shell:allow-kill``, the
    force-kill is silently blocked and the zombie sidecar keeps its
    WebSocket port bound — the next respawn fails with "address in use".
    """
    permissions = migrate_runtime_capability.get("permissions", [])
    assert "shell:allow-kill" in permissions, (
        "migrate-runtime.json must grant 'shell:allow-kill' for the FT-1 force-kill backstop (ADR-0020 §7 + §10)"
    )


def test_capabilities_json_identifier_matches_filename(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7: the capability's ``identifier`` field must match its filename.

    Tauri v2 loads capabilities from ``src-tauri/capabilities/<name>.json``
    by filename stem, and the ``identifier`` field inside the JSON must
    match the filename stem (else Tauri rejects the capability at build
    time). This guards against a rename of the JSON file that breaks
    the ``app.security.capabilities: ["migrate-runtime"]`` reference in
    ``tauri.conf.json``.
    """
    assert migrate_runtime_capability.get("identifier") == EXPECTED_CAPABILITY_IDENTIFIER, (
        f"migrate-runtime.json's 'identifier' field must be "
        f"{EXPECTED_CAPABILITY_IDENTIFIER!r} (must match the filename stem, "
        f"ADR-0020 §7)"
    )
