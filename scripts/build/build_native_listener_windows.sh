#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Native key-listener build (Windows)
# ADR-0020 §6.4 — Windows uses compile_native.ps1 (PowerShell) under the hood.
# This script is a thin bash wrapper that invokes the PowerShell script from
# Git Bash / MSYS2 / WSL, then copies the compiled binary into
# src-tauri/resources/native/ where the Tauri bundler picks it up as a
# bundle.resource.
#
# Output:
#   voice_typer/server/native/windows-key-listener.exe   (compile output)
#   src-tauri/resources/native/windows-key-listener.exe  (Tauri resource)
#
# Why keep the native binaries (ADR-0020 §6.4):
#   - Key suppression (so the hotkey doesn't reach the foreground app):
#     native binary ✅ (Win: WH_KEYBOARD_LL) vs tauri-plugin-global-shortcut ❌
#   - Modifier-only hotkeys (bare Caps Lock): native ✅ vs plugin ❌
#   - Crash isolation: native is a subprocess; plugin runs in-process.
#   - The Tauri global-shortcut plugin CANNOT replace these binaries without
#     regressing critical features.
#
# Usage:
#   bash scripts/build/build_native_listener_windows.sh
#   bash scripts/build/build_native_listener_windows.sh --check
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NATIVE_SRC_DIR="$PROJECT_ROOT/voice_typer/server/native"
RESOURCES_DIR="$PROJECT_ROOT/src-tauri/resources/native"

# ─── Args ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--check" ]]; then
    echo "[build_native_listener_windows] --check: invoking compile_native.ps1 -Check"
    powershell.exe -ExecutionPolicy Bypass -File "$SCRIPT_DIR/compile_native.ps1" -Check
    exit $?
fi

# ─── Compile via the existing PowerShell script ──────────────────────────────
echo "[build_native_listener_windows] Invoking compile_native.ps1..."
if ! command -v powershell.exe >/dev/null && ! command -v powershell >/dev/null; then
    echo "ERROR: powershell not found — this script must run on a Windows host (or under WSL with powershell.exe on PATH)." >&2
    exit 1
fi
PS_BIN="$(command -v powershell.exe || command -v powershell)"
"$PS_BIN" -ExecutionPolicy Bypass -File "$SCRIPT_DIR/compile_native.ps1"

# ─── Copy into src-tauri/resources/native/ ───────────────────────────────────
COMPILED_BIN="$NATIVE_SRC_DIR/windows-key-listener.exe"
if [[ ! -f "$COMPILED_BIN" ]]; then
    echo "ERROR: $COMPILED_BIN not built by compile_native.ps1" >&2
    exit 1
fi

mkdir -p "$RESOURCES_DIR"
cp -f "$COMPILED_BIN" "$RESOURCES_DIR/windows-key-listener.exe"
SIZE_KB=$(du -k "$RESOURCES_DIR/windows-key-listener.exe" | cut -f1)
echo "[build_native_listener_windows] OK: $RESOURCES_DIR/windows-key-listener.exe (${SIZE_KB} KB)"
