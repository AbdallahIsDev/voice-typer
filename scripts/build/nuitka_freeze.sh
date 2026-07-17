#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Unified Nuitka sidecar freeze wrapper (ADR-0020 §4 + Phase 1)
#
# This is the platform-agnostic entry point for freezing the Python sidecar
# (`voice_typer.server.ipc_server`) into a Nuitka `--onefile` binary at:
#
#   src-tauri/bin/python-sidecar-<triple>[.exe]
#
# where `<triple>` matches the Rust target triple Tauri's `externalBin`
# mechanism expects (see `src-tauri/src/sidecar/spawn.rs::target_triple_for`
# for the authoritative triple-for-(arch,os) mapping).
#
# ADR-0020 §4 mandates:
#   - python-build-standalone cpython-3.12.x as the base interpreter.
#   - `--onefile` mode (single self-extracting binary).
#   - OpenMP runtimes (libiomp5md.dll on Windows, libomp.dylib on macOS,
#     libgomp.so on Linux) bundled via `--include-data-dir=$SITE/ctranslate2/{lib,libs}`.
#   - `--include-package` for faster_whisper, ctranslate2, websockets,
#     voice_typer, numpy (hidden imports faster-whisper needs).
#   - `--enable-plugin=numpy` for numpy hidden imports.
#   - `--onefile-tempdir-spec` pinned to a per-app cache dir so stale
#     extractions are cleanable.
#
# This script DOES NOT cross-compile — Nuitka cannot cross-compile. It
# dispatches to the per-platform build script which itself only builds for
# the host arch (with one exception: Linux aarch64 can be cross-built on
# an x86_64 host via qemu-user-static, handled by build_sidecar_linux.sh).
# For a different platform, run this script on that platform's host, or
# use the CI matrix in `.github/workflows/tauri-{windows,macos,linux}-build.yml`.
#
# The per-platform scripts (which this wrapper dispatches to) own the
# actual Nuitka invocation:
#   scripts/build/build_sidecar_windows.sh   — Windows x86_64 + aarch64
#   scripts/build/build_sidecar_macos.sh     — macOS x86_64 + aarch64
#   scripts/build/build_sidecar_linux.sh     — Linux x86_64 + aarch64 (qemu cross)
#
# Usage:
#   bash scripts/build/nuitka_freeze.sh                    # host arch (auto-detect)
#   bash scripts/build/nuitka_freeze.sh x86_64             # force x86_64
#   bash scripts/build/nuitka_freeze.sh aarch64            # force aarch64
#                                                          # (Linux: triggers qemu cross-build
#                                                          #  if host is x86_64)
#   bash scripts/build/nuitka_freeze.sh --check            # dry-run: print plan, exit 0
#   bash scripts/build/nuitka_freeze.sh --help
#
# Environment variables (forwarded to per-platform scripts):
#   VOICE_TYPER_PYBS_DIR    Directory containing the extracted python-build-standalone
#                           cpython-3.12.x+<triple> tree. See build_sidecar_<plat>.sh
#                           for the auto-discovery layout.
#   XDG_CACHE_HOME          Override for the onefile tempdir spec base (Linux/macOS).
#   CC, CXX                 C compiler overrides (used by Nuitka's C compilation step).
#
# Exit codes:
#   0  success (or --check dry-run)
#   1  misuse / unknown args / unsupported host
#   2  per-platform build script failed
#
# Related:
#   docs/adr/0020-desktop-runtime-migration-analysis.md   §4 (Nuitka freeze spec)
#   docs/migration/{windows,macos,linux}-validation-runbook.md   Phase 0 gates
#   scripts/build/build_tauri_all.sh                       Full build orchestrator
#   scripts/build/voice-typer.spec                         PyInstaller fallback (ADR §4.5)
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# ─── Arg parsing ─────────────────────────────────────────────────────────────
CHECK_ONLY=0
FORCE_ARCH=""

print_usage() {
    cat <<EOF
Usage: $0 [ARCH|--check|--help]

  ARCH        Target arch: x86_64 or aarch64 (default: host arch).
              On Linux x86_64 host, 'aarch64' triggers a qemu-user-static
              cross-build (see build_sidecar_linux.sh).
  --check     Dry-run: print the build plan + exit 0 (no Nuitka invocation).
  --help, -h  Show this help.

Environment:
  VOICE_TYPER_PYBS_DIR  python-build-standalone tree (default: .python-build-standalone)
  XDG_CACHE_HOME        Override for onefile tempdir base (Linux/macOS)
  CC, CXX               C compiler overrides for Nuitka

Dispatches to:
  scripts/build/build_sidecar_windows.sh <arch>   (on Windows hosts)
  scripts/build/build_sidecar_macos.sh   <arch>   (on macOS hosts)
  scripts/build/build_sidecar_linux.sh   <arch>   (on Linux hosts)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check|-n) CHECK_ONLY=1; shift ;;
        --help|-h)  print_usage; exit 0 ;;
        x86_64|aarch64|arm64|amd64) FORCE_ARCH="$1"; shift ;;
        *) echo "ERROR: unknown arg: $1" >&2; print_usage; exit 1 ;;
    esac
done

# ─── Detect host platform + arch ─────────────────────────────────────────────
case "$(uname -s)" in
    Darwin*)              HOST_PLATFORM="macos" ;;
    MINGW*|MSYS*|CYGWIN*) HOST_PLATFORM="windows" ;;
    Linux*)               HOST_PLATFORM="linux" ;;
    *) echo "ERROR: unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# Normalize host arch to match Rust's std::env::consts::ARCH + the per-platform
# build scripts' expected input. This mirrors `current_target_triple()` in
# src-tauri/src/sidecar/spawn.rs which calls `target_triple_for(ARCH, OS)`.
case "$(uname -m)" in
    x86_64|amd64)  HOST_ARCH="x86_64" ;;
    arm64|aarch64) HOST_ARCH="aarch64" ;;
    *) echo "ERROR: unsupported host arch: $(uname -m)" >&2
       echo "  Supported: x86_64, aarch64 (arm64)" >&2
       exit 1 ;;
esac

# Apply forced arch (with normalization).
if [[ -n "$FORCE_ARCH" ]]; then
    case "$FORCE_ARCH" in
        arm64)   TARGET_ARCH="aarch64" ;;
        amd64)   TARGET_ARCH="x86_64" ;;
        *)       TARGET_ARCH="$FORCE_ARCH" ;;
    esac
else
    TARGET_ARCH="$HOST_ARCH"
fi

# ─── Compute target triple (mirrors target_triple_for in spawn.rs) ───────────
case "$HOST_PLATFORM" in
    windows) TRIPLE="${TARGET_ARCH}-pc-windows-msvc" ;;
    macos)   TRIPLE="${TARGET_ARCH}-apple-darwin" ;;
    linux)   TRIPLE="${TARGET_ARCH}-unknown-linux-gnu" ;;
esac

# Windows output has .exe suffix; macOS/Linux do not.
case "$HOST_PLATFORM" in
    windows) EXE_SUFFIX=".exe" ;;
    *)       EXE_SUFFIX="" ;;
esac

OUTPUT_BIN="$PROJECT_ROOT/src-tauri/bin/python-sidecar-${TRIPLE}${EXE_SUFFIX}"
DISPATCH_SCRIPT="$SCRIPT_DIR/build_sidecar_${HOST_PLATFORM}.sh"

# ─── Print build plan ────────────────────────────────────────────────────────
echo "::group::nuitka_freeze — plan"
echo "  HOST_PLATFORM  : $HOST_PLATFORM"
echo "  HOST_ARCH      : $HOST_ARCH"
echo "  TARGET_ARCH    : $TARGET_ARCH"
echo "  TRIPLE         : $TRIPLE"
echo "  OUTPUT_BIN     : $OUTPUT_BIN"
echo "  DISPATCH_SCRIPT: $DISPATCH_SCRIPT"
echo "  CHECK_ONLY     : $CHECK_ONLY"
echo "  PROJECT_ROOT   : $PROJECT_ROOT"
echo "::endgroup::"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "[nuitka_freeze] --check: dry-run, exiting 0."
    echo "[nuitka_freeze] Would dispatch: bash $DISPATCH_SCRIPT $TARGET_ARCH"
    exit 0
fi

# ─── Sanity: dispatch script exists ──────────────────────────────────────────
if [[ ! -x "$DISPATCH_SCRIPT" && ! -f "$DISPATCH_SCRIPT" ]]; then
    echo "ERROR: per-platform build script not found: $DISPATCH_SCRIPT" >&2
    echo "  Expected one of:" >&2
    echo "    $SCRIPT_DIR/build_sidecar_windows.sh" >&2
    echo "    $SCRIPT_DIR/build_sidecar_macos.sh" >&2
    echo "    $SCRIPT_DIR/build_sidecar_linux.sh" >&2
    exit 1
fi

# Make sure the dispatch script is executable (it may have been checked out
# without the +x bit on some platforms).
chmod +x "$DISPATCH_SCRIPT" 2>/dev/null || true

# ─── Ensure src-tauri/bin/ exists ────────────────────────────────────────────
mkdir -p "$PROJECT_ROOT/src-tauri/bin"

# ─── Dispatch to per-platform Nuitka build ───────────────────────────────────
# Each per-platform script:
#   1. Locates python-build-standalone cpython-3.12.x+<triple> (via
#      VOICE_TYPER_PYBS_DIR or $PROJECT_ROOT/.python-build-standalone).
#   2. Verifies faster_whisper + ctranslate2 + websockets + numpy are
#      installed in the pybs env's site-packages.
#   3. Installs Nuitka + zstandard into the pybs env if missing.
#   4. Runs Nuitka --standalone --onefile with the ADR-0020 §4 flag set.
#   5. Verifies the output binary exists + smoke-tests --help.
#   6. (Linux only) verifies the glibc baseline is ≤ 2.35 for Ubuntu 22.04 compat.
#
# The per-platform scripts are the authoritative Nuitka invocation — this
# wrapper does NOT duplicate the Nuitka command line. To change the Nuitka
# flags, edit build_sidecar_<platform>.sh, not this file.
echo "[nuitka_freeze] Dispatching to: bash $DISPATCH_SCRIPT $TARGET_ARCH"
echo "[nuitka_freeze] (ADR-0020 §4: python-build-standalone cpython-3.12.x + --onefile + OpenMP runtimes)"

set +e
bash "$DISPATCH_SCRIPT" "$TARGET_ARCH"
DISPATCH_RC=$?
set -e

if [[ "$DISPATCH_RC" -ne 0 ]]; then
    echo "ERROR: $DISPATCH_SCRIPT failed (exit $DISPATCH_RC)" >&2
    exit 2
fi

# ─── Verify output binary exists ─────────────────────────────────────────────
if [[ ! -f "$OUTPUT_BIN" ]]; then
    echo "ERROR: expected output binary not found: $OUTPUT_BIN" >&2
    echo "  The per-platform script exited 0 but did not produce the expected file." >&2
    echo "  Check the per-platform script's log (printed above) for warnings." >&2
    exit 2
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo "::group::nuitka_freeze — result"
echo "  Output: $OUTPUT_BIN"
ls -lh "$OUTPUT_BIN" 2>/dev/null || true
if command -v file >/dev/null 2>&1; then
    file "$OUTPUT_BIN" 2>/dev/null || true
fi
echo "::endgroup::"

echo "[nuitka_freeze] OK: built $OUTPUT_BIN"
echo "[nuitka_freeze] Next: copy this binary into src-tauri/bin/ (already done) and run"
echo "[nuitka_freeze]       bash scripts/build/build_tauri_all.sh --skip-sidecar"
echo "[nuitka_freeze] to package the Tauri app (or use the per-platform runbook's"
echo "[nuitka_freeze] full build flow for signing + installer creation)."
echo "[nuitka_freeze] ADR-0020 §15: NO auto-update manifest is generated."
exit 0
