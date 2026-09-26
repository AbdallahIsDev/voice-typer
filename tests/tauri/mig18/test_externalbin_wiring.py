"""externalBin per-arch wiring validation (cross-platform)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
_TAURI_CONF = _SRC_TAURI / "tauri.conf.json"
_SPAWN_RS = _SRC_TAURI / "src" / "sidecar" / "spawn.rs"
_CAPABILITIES_DIR = _SRC_TAURI / "capabilities"
_MIGRATE_RUNTIME_CAPABILITY = _CAPABILITIES_DIR / "main-runtime.json"


# : ADR-0020 §4.1 + §7: externalBin must list the base name; Tauri
EXPECTED_EXTERNAL_BIN_BASENAME = "bin/python-sidecar"

#: ADR-0020 §6.4: native hotkey binaries, one per platform.
EXPECTED_NATIVE_RESOURCES = [
    "resources/native/windows-key-listener.exe",
    "resources/native/macos-key-listener",
    "resources/native/linux-key-listener",
]

# : worker exe that absorbed the retired standalone prewarm binary).
EXPECTED_WORKER_BIN_BASENAME = "bin/lausu-worker"

EXPECTED_TARGET_TRIPLES = [
    ("x86_64", "windows", "x86_64-pc-windows-msvc"),
    ("aarch64", "windows", "aarch64-pc-windows-msvc"),
    ("x86_64", "macos", "x86_64-apple-darwin"),
    ("aarch64", "macos", "aarch64-apple-darwin"),
    ("x86_64", "linux", "x86_64-unknown-linux-gnu"),
    ("aarch64", "linux", "aarch64-unknown-linux-gnu"),
]

# : ADR-0020 §7: capability identifier referenced by tauri.conf.json's
EXPECTED_CAPABILITY_IDENTIFIER = "main-runtime"


@pytest.fixture(scope="module")
def tauri_conf() -> dict:
    """Load + parse src-tauri/tauri.conf.json."""
    assert _TAURI_CONF.exists(), f"tauri.conf.json not found: {_TAURI_CONF}"
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


def _read_spawn_module() -> str:
    """Concatenate the spawn module sources (spawn.rs + spawn/*.rs)."""
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


def test_tauri_conf_external_bin_lists_python_sidecar_basename(
    tauri_conf,
) -> None:
    """ADR-0020 §4.1 + §7: externalBin must contain ``bin/python-sidecar``."""
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
    """
    ADR-0020 §4.1 + §7: externalBin must NOT contain triple-suffixed entries.
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
            f"suffix, Tauri appends the triple at runtime. Use the base "
            f"name only (e.g. {EXPECTED_EXTERNAL_BIN_BASENAME!r})."
        )


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
    """ADR-0020 §6.4 + §7: every native hotkey binary must be a resource."""
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    assert resource in resources, (
        f"bundle.resources must include {resource!r} "
        f"(native hotkey binary spawned by the Python sidecar, "
        f"ADR-0020 §6.4 + §7)"
    )


def test_tauri_conf_external_bin_lists_worker(tauri_conf) -> None:
    """The ML worker exe replaced the retired standalone prewarm binary"""
    external_bin = tauri_conf.get("bundle", {}).get("externalBin", [])
    assert EXPECTED_WORKER_BIN_BASENAME in external_bin, (
        f"bundle.externalBin must contain {EXPECTED_WORKER_BIN_BASENAME!r} (the ML worker exe, master plan §6.2 P-1)"
    )


def test_tauri_conf_resources_exclude_prewarm_binaries(tauri_conf) -> None:
    """runs the warm phase) is an externalBin, not a bundle resource."""
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    prewarm_entries = [r for r in resources if "prewarm" in r]
    assert not prewarm_entries, (
        f"bundle.resources must NOT contain prewarm binaries (retired, master plan §6.2 P-1), got: {prewarm_entries}"
    )


def test_tauri_conf_resources_count_matches_minimum(tauri_conf) -> None:
    """ADR-0020 §7: resources must include the 3 mandated native entries."""
    resources = tauri_conf.get("bundle", {}).get("resources", [])
    required = set(EXPECTED_NATIVE_RESOURCES)
    missing = required - set(resources)
    assert not missing, (
        f"bundle.resources is missing {len(missing)} mandated entr"
        f"{'y' if len(missing) == 1 else 'ies'}: {sorted(missing)}"
    )


@pytest.mark.parametrize(
    ("arch", "os", "expected_triple"),
    EXPECTED_TARGET_TRIPLES,
    ids=[f"{a}-{o}-{t}" for a, o, t in EXPECTED_TARGET_TRIPLES],
)
def test_spawn_rs_target_triple_for_maps_combo(spawn_rs_source, arch: str, os: str, expected_triple: str) -> None:
    """ADR-0020 §4.1: ``target_triple_for(arch, os)`` → exact triple string."""
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
    """ADR-0020 §4.1 + §15: target_triple_for must cover all 6 combos."""
    # Extract the match body of target_triple_for.
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
    """ADR-0020 §4.1: ``current_target_triple`` reads runtime arch + os."""
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
    """The worker exe is named ``lausu-worker-<triple>[.exe]`` (one"""
    worker_path_src = (_SRC_TAURI / "src" / "platform" / "worker_path.rs").read_text(encoding="utf-8")
    assert "fn worker_exe_path_from_env" in worker_path_src, "worker_path.rs must define `worker_exe_path_from_env`"
    assert "current_target_triple" in worker_path_src, (
        "worker_exe_path_from_env must call current_target_triple() to resolve "
        "the per-arch worker exe name (master plan §6.2 P-1)"
    )
    # The worker name template must include the triple (+ Windows .exe
    assert re.search(
        r'WORKER_BIN_BASE_NAME\s*:\s*&str\s*=\s*"lausu-worker"',
        worker_path_src,
    ), 'worker_path.rs must define WORKER_BIN_BASE_NAME = "lausu-worker" (master plan §6.2 P-1)'
    assert re.search(
        r'format!\s*\(\s*"\{\}-\{\}\{\}"\s*,\s*WORKER_BIN_BASE_NAME\s*,\s*triple\s*,\s*suffix',
        worker_path_src,
    ), "worker_exe_path_from_env must format the binary name as `lausu-worker-{triple}{suffix}` (master plan §6.2 P-1)"
    assert re.search(r"cfg!\s*\(\s*windows\s*\)", worker_path_src), (
        "worker_exe_path_from_env must use cfg!(windows) to conditionally "
        "append the .exe suffix on Windows only (master plan §6.2 P-1)"
    )


def test_tauri_conf_shell_config_is_v2_valid(tauri_conf) -> None:
    """ADR-0020 §7: ``plugins.shell`` must be v2-valid (``{\"open\": false}``)."""
    shell = tauri_conf.get("plugins", {}).get("shell")
    assert shell == {"open": False}, (
        "plugins.shell must be exactly {'open': false}, tauri-plugin-shell "
        "v2 rejects 'sidecar'/'scope' keys at startup ('unknown field "
        f"`scope`, expected `open`'); got {shell!r}"
    )


def test_tauri_conf_capabilities_reference_migrate_runtime(
    tauri_conf,
) -> None:
    """ADR-0020 §7: ``app.security.capabilities`` must reference ``main-runtime``."""
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
    """ADR-0020 §7: the main-runtime capability must grant ``shell:allow-spawn``."""
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
    """ADR-0020 §7 + §10: the capability must grant ``shell:allow-kill``."""
    permissions = migrate_runtime_capability.get("permissions", [])
    assert "shell:allow-kill" in permissions, (
        "main-runtime.json must grant 'shell:allow-kill' for the force-kill backstop (ADR-0020 §7 + §10)"
    )


def test_capabilities_json_identifier_matches_filename(
    migrate_runtime_capability,
) -> None:
    """ADR-0020 §7: the capability's ``identifier`` field must match its filename."""
    assert migrate_runtime_capability.get("identifier") == EXPECTED_CAPABILITY_IDENTIFIER, (
        f"main-runtime.json's 'identifier' field must be "
        f"{EXPECTED_CAPABILITY_IDENTIFIER!r} (must match the filename stem, "
        f"ADR-0020 §7)"
    )
