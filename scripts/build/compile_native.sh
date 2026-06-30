#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Native key-listener binary build script
#
# Compiles the three native key-listener binaries for the current platform:
#   - macOS:   voice_typer/server/native/macos-key-listener   (Swift)
#   - Windows: voice_typer/server/native/windows-key-listener.exe (C)
#   - Linux:   voice_typer/server/native/linux-key-listener   (C)
#
# Only the binary for the current platform is built; the others are skipped
# (cross-compilation is not supported by this script — use the platform's
# native CI runner for that).
#
# Output binaries are placed alongside the source files in
# voice_typer/server/native/ and are picked up by the Python backend at
# runtime (via voice_typer.server.native_hotkeys.get_native_binary_path).
#
# Usage:
#   bash scripts/build/compile_native.sh            # build for current platform
#   bash scripts/build/compile_native.sh --check    # check toolchain only
# =============================================================================
set -euo pipefail

# Resolve project root: this script lives at <root>/scripts/build/compile_native.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NATIVE_DIR="$PROJECT_ROOT/voice_typer/server/native"

echo "[compile_native] Project root: $PROJECT_ROOT"
echo "[compile_native] Native dir:   $NATIVE_DIR"

# ─── Detect platform ────────────────────────────────────────────────────────
case "$(uname -s)" in
    Darwin*) PLATFORM="darwin" ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="win32" ;;
    Linux*) PLATFORM="linux" ;;
    *) echo "[compile_native] ERROR: unsupported platform: $(uname -s)"; exit 1 ;;
esac
echo "[compile_native] Detected platform: $PLATFORM"

# ─── --check mode: just verify toolchain ────────────────────────────────────
if [[ "${1:-}" == "--check" ]]; then
    case "$PLATFORM" in
        darwin)
            if command -v swiftc &>/dev/null; then
                echo "[compile_native] OK: swiftc found at $(command -v swiftc)"
                swiftc --version
                exit 0
            else
                echo "[compile_native] MISSING: swiftc not found. Install Xcode command-line tools: xcode-select --install"
                exit 1
            fi
            ;;
        win32)
            if command -v cl.exe &>/dev/null; then
                echo "[compile_native] OK: cl.exe found"
                exit 0
            elif command -v gcc &>/dev/null && gcc --version 2>&1 | grep -qi mingw; then
                echo "[compile_native] OK: MinGW gcc found at $(command -v gcc)"
                exit 0
            else
                echo "[compile_native] MISSING: neither cl.exe (MSVC) nor MinGW gcc found."
                echo "  Install Visual Studio Build Tools or MinGW-w64."
                exit 1
            fi
            ;;
        linux)
            if command -v gcc &>/dev/null; then
                echo "[compile_native] OK: gcc found at $(command -v gcc)"
                gcc --version | head -1
                exit 0
            else
                echo "[compile_native] MISSING: gcc not found. Install build-essential."
                exit 1
            fi
            ;;
    esac
fi

# ─── Build per platform ─────────────────────────────────────────────────────
case "$PLATFORM" in
    darwin)
        SRC="$NATIVE_DIR/macos-key-listener.swift"
        OUT="$NATIVE_DIR/macos-key-listener"
        if [[ ! -f "$SRC" ]]; then
            echo "[compile_native] ERROR: source not found: $SRC"
            exit 1
        fi
        if ! command -v swiftc &>/dev/null; then
            echo "[compile_native] ERROR: swiftc not found. Install Xcode command-line tools:"
            echo "  xcode-select --install"
            exit 1
        fi
        echo "[compile_native] Compiling: swiftc -O $SRC -o $OUT"
        swiftc -O "$SRC" -framework Cocoa -framework CoreGraphics -o "$OUT"
        echo "[compile_native] OK: $OUT"
        # Codesign the binary (ad-hoc) so it can be trusted for Accessibility
        if command -v codesign &>/dev/null; then
            echo "[compile_native] Codesigning (ad-hoc)..."
            codesign --force --sign - "$OUT" || true
        fi
        ;;

    win32)
        SRC="$NATIVE_DIR/windows-key-listener.c"
        OUT="$NATIVE_DIR/windows-key-listener.exe"
        if [[ ! -f "$SRC" ]]; then
            echo "[compile_native] ERROR: source not found: $SRC"
            exit 1
        fi
        # Prefer MSVC if available, fall back to MinGW gcc
        if command -v cl.exe &>/dev/null; then
            echo "[compile_native] Compiling with MSVC: cl.exe /O2 $SRC /link user32.lib"
            # MSVC needs the env set up — usually run from "Developer Command Prompt"
            cl.exe /nologo /O2 /D_CRT_SECURE_NO_WARNINGS /D_WIN32_WINNT=0x0600 \
                "$SRC" /link /NOLOGO user32.lib kernel32.lib \
                /OUT:"$OUT"
        elif command -v gcc &>/dev/null; then
            echo "[compile_native] Compiling with MinGW: gcc -O2 $SRC -o $OUT -luser32"
            gcc -O2 -std=c99 -D_WIN32_WINNT=0x0600 \
                "$SRC" -o "$OUT" -luser32
        else
            echo "[compile_native] ERROR: neither cl.exe (MSVC) nor MinGW gcc found."
            echo "  Install Visual Studio Build Tools or MinGW-w64."
            exit 1
        fi
        echo "[compile_native] OK: $OUT"
        ;;

    linux)
        SRC="$NATIVE_DIR/linux-key-listener.c"
        OUT="$NATIVE_DIR/linux-key-listener"
        if [[ ! -f "$SRC" ]]; then
            echo "[compile_native] ERROR: source not found: $SRC"
            exit 1
        fi
        if ! command -v gcc &>/dev/null; then
            echo "[compile_native] ERROR: gcc not found. Install build-essential:"
            echo "  sudo apt-get install build-essential  # Debian/Ubuntu"
            echo "  sudo dnf install gcc                 # Fedora"
            exit 1
        fi
        echo "[compile_native] Compiling: gcc -O2 -std=c99 $SRC -o $OUT"
        gcc -O2 -std=c99 -Wall -Wextra "$SRC" -o "$OUT"
        echo "[compile_native] OK: $OUT"
        ;;
esac

echo "[compile_native] Done."
