"""Startup dependency guard for the production runtime.

Importing this module verifies that every runtime-critical dependency of
the server is importable in the current interpreter. When anything is
missing it prints a ``[DEPS]`` error naming the exact packages and the
repair command, then exits non-zero — so ``rebuild-and-launch.ps1`` (which
imports it right after the editable reinstall) fails the rebuild loudly
instead of letting the app limp along in degraded mode.

Why this exists: a stripped or recreated venv can silently lack a package
whose absence degrades a feature instead of crashing (the real case:
``cryptography`` missing → at-rest history encryption fell back to
"key-unavailable" mode that looked exactly like a lost keyring entry).
Degrade-to-plaintext is the right runtime behavior; the guard makes the
BUILD surface it.

Zero network, zero side effects beyond logging — it only imports modules.
"""

from __future__ import annotations

import importlib
import logging
import sys

log = logging.getLogger(__name__)

#: Import names the production runtime cannot function correctly without.
#: Each entry: (import name, what degrades when it is missing).
REQUIRED_IMPORTS: tuple[tuple[str, str], ...] = (
    ("cryptography", "history at-rest encryption silently degrades to key-unavailable"),
    ("numpy", "audio processing / ASR break"),
    ("sounddevice", "microphone capture breaks"),
    ("faster_whisper", "local ASR breaks"),
    ("keyring", "credential store + history DEK break"),
    ("psutil", "process supervision degrades"),
    ("pynput", "global hotkeys break"),
    ("pyperclip", "clipboard paste breaks"),
)


def missing_required() -> list[tuple[str, str]]:
    """Return the required imports that fail in this interpreter."""
    missing: list[tuple[str, str]] = []
    for import_name, consequence in REQUIRED_IMPORTS:
        try:
            importlib.import_module(import_name)
        except Exception:  # noqa: BLE001 — any failure means unusable
            missing.append((import_name, consequence))
    return missing


def run_guard() -> int:
    """Check every required import. Return 0 when all present, else 1
    after printing a ``[DEPS]`` error with the exact repair command."""
    missing = missing_required()
    if not missing:
        return 0
    names = " ".join(name for name, _ in missing)
    print("[DEPS] missing runtime dependencies:", file=sys.stderr)
    for name, consequence in missing:
        print(f"[DEPS]   {name} — {consequence}", file=sys.stderr)
    print(
        f"[DEPS] repair: {sys.executable} -m pip install {names} "
        f"(or: {sys.executable} -m pip install -r requirements-lock.txt)",
        file=sys.stderr,
    )
    return 1


# Import-time execution: the rebuild script's `-c "import ..."` probe
# relies on the module failing the interpreter when a dependency is
# missing (exit code 1) and passing silently when complete.
if run_guard() != 0:  # pragma: no cover — exercised via run_guard in tests
    raise SystemExit(1)

__all__ = ["REQUIRED_IMPORTS", "missing_required", "run_guard"]
