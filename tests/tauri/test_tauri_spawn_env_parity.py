"""Pin the Tauri sidecar/worker spawn env against predecessor parity regressions.

MO-111: predecessor always passed ``KMP_DUPLICATE_LIB_OK=TRUE`` to its
console-less Python child (the Intel OpenMP runtime aborts at
``import torch`` when two OpenMP runtimes land in one process). The
Tauri spawn paths ``.env_clear()`` the host env, so the var must be
re-added EXPLICITLY on every sidecar/worker spawn builder. These tests
grep the four spawn modules for the literal so a spawn path that drops
the var fails loudly instead of silently hanging on affected machines.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPAWN_DIR = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "spawn"

KMP_VAR = "KMP_DUPLICATE_LIB_OK"
KMP_VALUE = "TRUE"


def _spawn_source(name: str) -> str:
    path = SPAWN_DIR / name
    assert path.is_file(), f"spawn module missing: {path}"
    return path.read_text(encoding="utf-8")


def test_release_sidecar_spawn_sets_kmp_duplicate_lib_ok() -> None:
    src = _spawn_source("release_mode.rs")
    assert f'.env("{KMP_VAR}", "{KMP_VALUE}")' in src, (
        "release sidecar spawn must set KMP_DUPLICATE_LIB_OK=TRUE (predecessor spawn-env parity, MO-111)"
    )


def test_dev_sidecar_spawn_sets_kmp_duplicate_lib_ok() -> None:
    src = _spawn_source("dev_mode.rs")
    assert f'.env("{KMP_VAR}", "{KMP_VALUE}")' in src, (
        "dev sidecar spawn must set KMP_DUPLICATE_LIB_OK=TRUE (predecessor spawn-env parity, MO-111)"
    )


def test_worker_release_spawn_sets_kmp_duplicate_lib_ok() -> None:
    src = _spawn_source("worker.rs")
    assert src.count(f'.env("{KMP_VAR}", "{KMP_VALUE}")') >= 2, (
        "BOTH worker spawn paths (release + dev) must set KMP_DUPLICATE_LIB_OK=TRUE (MO-111)"
    )


def test_every_env_clear_spawn_path_sets_kmp() -> None:
    """Every ``.env_clear()`` in the spawn modules must be followed by a
    KMP var set in the same module (the clear is what drops the var; a
    new env_clear path that forgets the re-add is exactly the MO-111
    regression class)."""
    for name in ("release_mode.rs", "dev_mode.rs", "worker.rs"):
        # Only real spawn-builder clears count: the module doc comments
        # also mention `.env_clear()` in prose, so strip comments first.
        src = _spawn_source(name)
        code = "\n".join(line.split("//")[0] for line in src.splitlines())
        clears = code.count(".env_clear()")
        kmp_sets = code.count(f'.env("{KMP_VAR}"')
        assert clears >= 1, f"{name}: expected at least one .env_clear()"
        assert kmp_sets >= clears, (
            f"{name}: {clears} .env_clear() paths but only {kmp_sets} "
            f"{KMP_VAR} sets; every cleared spawn path must re-add the "
            "OpenMP workaround"
        )
