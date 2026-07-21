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
#   bash scripts/build/compile_native.sh                          # build for current platform
#   bash scripts/build/compile_native.sh --check                  # check toolchain only
#   bash scripts/build/compile_native.sh --arch x86_64            # macOS x86_64 only
#   bash scripts/build/compile_native.sh --arch arm64             # macOS arm64 only
#   bash scripts/build/compile_native.sh --arch universal         # macOS universal (default on darwin)
#
# CR-011: the `--arch` flag is honored ONLY on macOS (Swift supports
# `-target <triple>`). On Windows / Linux the flag is ignored (those
# compilers use the host's native ABI and the script never cross-compiles
# to a different arch). The default on macOS is `universal` — builds
# both x86_64 and arm64 separately then `lipo -create`s them into a
# single fat binary. The two-matrix-leg CI build (.github/workflows/
# build.yml::build-native) instead invokes `--arch x86_64` and
# `--arch arm64` on two separate macOS runners and the
# `build-macos-universal` job does the `lipo -create` step on the
# resulting two single-arch artifacts.
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

# ─── Parse arguments ────────────────────────────────────────────────────────
# We scan all args so `--arch x86_64 --check` (or any order) works.
# `--check` short-circuits to toolchain verification before any build.
CHECK_ONLY=0
ARCH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --arch)
            if [[ $# -lt 2 ]]; then
                echo "[compile_native] ERROR: --arch requires a value (x86_64|arm64|universal)"
                exit 1
            fi
            ARCH="$2"
            shift 2
            ;;
        --arch=*)
            ARCH="${1#--arch=}"
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            echo "[compile_native] WARNING: ignoring unknown argument: $1"
            shift
            ;;
    esac
done

# CR-011: default ARCH to `universal` on macOS. On Windows/Linux the
# flag is silently ignored (the script never cross-compiles to a
# different arch on those platforms).
if [[ "$PLATFORM" == "darwin" && -z "$ARCH" ]]; then
    ARCH="universal"
fi

# Validate ARCH on darwin (ignored elsewhere).
if [[ "$PLATFORM" == "darwin" ]]; then
    case "$ARCH" in
        x86_64|arm64|universal) ;;
        *)
            echo "[compile_native] ERROR: --arch must be one of x86_64|arm64|universal (got: $ARCH)"
            exit 1
            ;;
    esac
fi

if [[ "$PLATFORM" == "darwin" ]]; then
    echo "[compile_native] ARCH: $ARCH"
elif [[ -n "$ARCH" ]]; then
    # Non-darwin: --arch is silently ignored, but log it so the user
    # sees their flag was parsed (and is a no-op on this platform).
    echo "[compile_native] ARCH: $ARCH (ignored on $PLATFORM — flag is darwin-only)"
else
    echo "[compile_native] ARCH: n/a (non-darwin)"
fi

# ─── --check mode: just verify toolchain ────────────────────────────────────
if [[ "$CHECK_ONLY" == "1" ]]; then
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

        # CR-011: helper that compiles a single arch via `swiftc -target`.
        # The macOS min-version (11.0 / Big Sur) matches the Tauri
        # binary's deployment target so the native key-listener loads on
        # the same OS range as the app itself. The target triple format
        # is `<arch>-apple-macos<version>` — both arches use the same
        # min-version; the resulting single-arch slices are merged via
        # `lipo -create` for the `universal` arch mode.
        build_one_arch() {
            local arch="$1"
            local out="$2"
            local target="${arch}-apple-macos11.0"
            echo "[compile_native] Compiling: swiftc -O -target $target $SRC -o $out"
            swiftc -O -target "$target" "$SRC" -framework Cocoa -framework CoreGraphics -o "$out"
            echo "[compile_native] OK: $out ($arch)"
        }

        # Codesign helper — ad-hoc sign so the binary can be granted
        # Accessibility (required for CGEventTap). Best-effort: errors
        # are logged but don't fail the build (CI runners without a
        # codesigning identity still produce a usable binary).
        codesign_adhoc() {
            local target="$1"
            if command -v codesign &>/dev/null; then
                echo "[compile_native] Codesigning (ad-hoc): $target"
                codesign --force --sign - "$target" || true
            fi
        }

        case "$ARCH" in
            x86_64|arm64)
                build_one_arch "$ARCH" "$OUT"
                codesign_adhoc "$OUT"
                ;;
            universal)
                # CR-011: build both arches separately into temp files
                # then `lipo -create` them into a single fat binary.
                # Both temp files are removed after the lipo step.
                TMPDIR_BUILD="$(mktemp -d)"
                trap 'rm -rf "$TMPDIR_BUILD"' EXIT
                TMP_X86="$TMPDIR_BUILD/macos-key-listener.x86_64"
                TMP_ARM="$TMPDIR_BUILD/macos-key-listener.arm64"
                build_one_arch "x86_64" "$TMP_X86"
                build_one_arch "arm64"   "$TMP_ARM"
                echo "[compile_native] Merging with lipo -create → $OUT"
                lipo -create "$TMP_X86" "$TMP_ARM" -output "$OUT"
                chmod +x "$OUT"
                # Verify the universal binary contains both arches.
                lipo -info "$OUT"
                codesign_adhoc "$OUT"
                ;;
        esac
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
