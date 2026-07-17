#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Native key-listener build (Linux)
# ADR-0020 §6.4 — Linux uses compile_native.sh which invokes gcc on
# voice_typer/server/native/linux-key-listener.c. This wrapper invokes
# compile_native.sh (detecting Linux) and copies the compiled binary into
# src-tauri/resources/native/.
#
# Output:
#   voice_typer/server/native/linux-key-listener   (compile output)
#   src-tauri/resources/native/linux-key-listener  (Tauri resource)
#
# Why keep the native binary (ADR-0020 §6.4):
#   - evdev-based hotkey detection works on both X11 and Wayland (sits below
#     the display server); the Tauri global-shortcut plugin uses X11 only.
#   - Modifier-only hotkeys (bare Caps Lock): native ✅ vs plugin ❌.
#   - Crash isolation: native is a subprocess; plugin runs in-process.
#
# Linux install: requires `input` group membership to read /dev/input/event*.
# The existing scripts/linux/postinst (and postinst.rpm) add the installing
# user to the `input` group via `usermod -aG input` (ADR-0020 §13.3).
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NATIVE_SRC_DIR="$PROJECT_ROOT/voice_typer/server/native"
RESOURCES_DIR="$PROJECT_ROOT/src-tauri/resources/native"

# ─── Sanity: host must be Linux ──────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "ERROR: build_native_listener_linux.sh must run on Linux (got $(uname -s))." >&2
    exit 1
fi

# ─── Args ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--check" ]]; then
    echo "[build_native_listener_linux] --check: invoking compile_native.sh --check"
    bash "$SCRIPT_DIR/compile_native.sh" --check
    exit $?
fi

# ─── Compile via compile_native.sh (which detects Linux + runs gcc) ──────────
echo "[build_native_listener_linux] Invoking compile_native.sh..."
bash "$SCRIPT_DIR/compile_native.sh"

# ─── Copy into src-tauri/resources/native/ ───────────────────────────────────
COMPILED_BIN="$NATIVE_SRC_DIR/linux-key-listener"
if [[ ! -f "$COMPILED_BIN" ]]; then
    echo "ERROR: $COMPILED_BIN not built by compile_native.sh" >&2
    exit 1
fi

mkdir -p "$RESOURCES_DIR"
cp -f "$COMPILED_BIN" "$RESOURCES_DIR/linux-key-listener"
chmod +x "$RESOURCES_DIR/linux-key-listener"
SIZE_KB=$(du -k "$RESOURCES_DIR/linux-key-listener" | cut -f1)
echo "[build_native_listener_linux] OK: $RESOURCES_DIR/linux-key-listener (${SIZE_KB} KB)"

# Verify glibc baseline (ADR-0020 §4.4: ≤ GLIBC_2.35 for Ubuntu 22.04 compat).
if command -v ldd >/dev/null; then
    MAX_GLIBC=$(ldd "$RESOURCES_DIR/linux-key-listener" 2>/dev/null \
                | grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sort -V | tail -1 || true)
    echo "[build_native_listener_linux] max GLIBC symbol: ${MAX_GLIBC:-none}"
    if [[ -n "$MAX_GLIBC" ]]; then
        NUM="${MAX_GLIBC/GLIBC_/}"
        MAJ="${NUM%.*}"; MIN="${NUM#*.}"
        if [[ "$MAJ" -gt 2 || ( "$MAJ" -eq 2 && "$MIN" -gt 35 ) ]]; then
            echo "ERROR: $RESOURCES_DIR/linux-key-listener requires $MAX_GLIBC but baseline is GLIBC_2.35" >&2
            exit 1
        fi
    fi
fi
