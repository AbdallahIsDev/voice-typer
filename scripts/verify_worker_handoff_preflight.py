"""Preflight the ADR-0024 worker-handoff checklist without launching the app.

Static repo checks only (file presence + source pins). The live steps
(record→transcribe, kill→respawn, pack removal, log greps) need a real
machine with the frozen worker exe: see
docs/adr/0024-step6-verification-checklist.md ("needs host run").
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKER_FILES = (
    "voice_typer/worker/__main__.py",
    "voice_typer/worker/_ws_server.py",
    "voice_typer/worker/_transcribe.py",
    "voice_typer/worker/_auth.py",
    "voice_typer/worker/_single_instance.py",
)

SUPERVISOR_PINS = (
    "pub(crate) async fn respawn_worker",
    "pub(crate) fn spawn_worker_exit_watcher",
    "worker_backoff_delay_ms",
    "SUPERVISOR_BACKOFF_MS",
    "ws_generation.fetch_add",
)

CATALOGUE_PINS = ("worker_started", "worker_crashed", "worker_unloaded")


def check_worker_module(repo: Path) -> tuple[bool, str]:
    missing = [f for f in WORKER_FILES if not (repo / f).is_file()]
    if missing:
        return False, f"missing worker files: {', '.join(missing)}"
    return True, f"{len(WORKER_FILES)}/{len(WORKER_FILES)} worker files present"


def check_rust_supervisor(repo: Path) -> tuple[bool, str]:
    path = repo / "src-tauri/src/sidecar/worker_supervisor.rs"
    if not path.is_file():
        return False, "src-tauri/src/sidecar/worker_supervisor.rs missing"
    text = path.read_text(encoding="utf-8")
    missing = [pin for pin in SUPERVISOR_PINS if pin not in text]
    if missing:
        return False, f"supervisor pins absent: {', '.join(missing)}"
    return True, f"{len(SUPERVISOR_PINS)}/{len(SUPERVISOR_PINS)} supervisor pins present"


def check_watcher_wiring(repo: Path) -> tuple[bool, str]:
    text = (repo / "src-tauri/src/sidecar/spawn.rs").read_text(encoding="utf-8")
    if "spawn_worker_exit_watcher" not in text:
        return False, "initialize_worker does not spawn the exit watcher"
    return True, "initialize_worker spawns the exit watcher"


def check_event_catalogue(repo: Path) -> tuple[bool, str]:
    text = (repo / "voice_typer/server/event_bus.py").read_text(encoding="utf-8")
    missing = [pin for pin in CATALOGUE_PINS if pin not in text]
    if missing:
        return False, f"catalogue entries absent: {', '.join(missing)}"
    return True, f"{len(CATALOGUE_PINS)}/{len(CATALOGUE_PINS)} worker events catalogued"


def check_policy_doc(repo: Path) -> tuple[bool, str]:
    path = repo / "docs/code-notes/worker-lifecycle-policy.md"
    if not path.is_file():
        return False, "docs/code-notes/worker-lifecycle-policy.md missing"
    return True, "lifecycle policy documented"


CHECKS = (
    ("worker module", check_worker_module),
    ("rust supervisor", check_rust_supervisor),
    ("watcher wiring", check_watcher_wiring),
    ("event catalogue", check_event_catalogue),
    ("policy doc", check_policy_doc),
)


def run_preflight(repo: Path) -> tuple[int, int]:
    passed = 0
    for name, check in CHECKS:
        try:
            ok, detail = check(repo)
        except OSError as exc:
            ok, detail = False, f"unreadable repo tree: {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        passed += ok
    print("live steps (record/transcribe/kill/pack-removal/log greps): NEEDS HOST RUN")
    return passed, len(CHECKS)


def main(argv: list[str]) -> int:
    repo = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    passed, total = run_preflight(repo)
    print(f"preflight: {passed}/{total} static checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
