#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Native key-listener build (macOS)
# ADR-0020 §6.4 — macOS uses compile_native.sh which invokes swiftc on
# voice_typer/server/native/macos-key-listener.swift. This wrapper invokes
# compile_native.sh (detecting macOS) and copies the compiled binary into
# src-tauri/resources/native/.
#
# Output:
#   voice_typer/server/native/macos-key-listener   (compile output)
#   src-tauri/resources/native/macos-key-listener  (Tauri resource)
#
# Why keep the native binary (ADR-0020 §6.4):
#   - Key suppression via CGEvent tap (returns NULL): native ✅ vs plugin ❌
#   - Fn / Globe key (default macOS hotkey): native ✅ vs plugin ❌
#   - macOS Accessibility permission flow (ADR-0008 Gap 2): preserved verbatim.
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NATIVE_SRC_DIR="$PROJECT_ROOT/voice_typer/server/native"
RESOURCES_DIR="$PROJECT_ROOT/src-tauri/resources/native"

# ─── Sanity: host must be macOS ──────────────────────────────────────────────
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: build_native_listener_macos.sh must run on macOS (got $(uname -s))." >&2
    exit 1
fi

# ─── Args ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--check" ]]; then
    echo "[build_native_listener_macos] --check: invoking compile_native.sh --check"
    bash "$SCRIPT_DIR/compile_native.sh" --check
    exit $?
fi

# ─── Compile via compile_native.sh (which detects macOS + runs swiftc) ───────
echo "[build_native_listener_macos] Invoking compile_native.sh..."
bash "$SCRIPT_DIR/compile_native.sh"

# ─── Copy into src-tauri/resources/native/ ───────────────────────────────────
COMPILED_BIN="$NATIVE_SRC_DIR/macos-key-listener"
if [[ ! -f "$COMPILED_BIN" ]]; then
    echo "ERROR: $COMPILED_BIN not built by compile_native.sh" >&2
    exit 1
fi

mkdir -p "$RESOURCES_DIR"
cp -f "$COMPILED_BIN" "$RESOURCES_DIR/macos-key-listener"
chmod +x "$RESOURCES_DIR/macos-key-listener"
SIZE_KB=$(du -k "$RESOURCES_DIR/macos-key-listener" | cut -f1)
echo "[build_native_listener_macos] OK: $RESOURCES_DIR/macos-key-listener (${SIZE_KB} KB)"

# Codesign the resource copy ad-hoc so it inherits the parent .app's signature
# when the bundler signs the .app --deep (ADR-0020 §13.2).
if command -v codesign >/dev/null; then
    echo "[build_native_listener_macos] Ad-hoc codesign (parent .app will re-sign --deep)..."
    codesign --force --sign - "$RESOURCES_DIR/macos-key-listener" || true
fi
