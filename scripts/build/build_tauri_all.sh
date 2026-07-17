#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Tauri all-platforms build orchestrator (ADR-0020 Phase 1)
#
# This is the local-developer equivalent of `.github/workflows/tauri-build.yml`.
# It dispatches to the per-platform build scripts in this directory:
#
#   build_sidecar_<platform>.sh         — Nuitka freeze of voice_typer.server.ipc_server
#   build_prewarm_<platform>.sh         — Nuitka freeze of voice_typer.server.prewarm
#   build_native_listener_<platform>.sh — compiles the native hotkey binary
#
# Then runs `cargo tauri build` against the host triple (or the triple passed
# via --target).
#
# ADR-0020 §4 (Nuitka freeze) + §5 (prewarm) + §6.4 (native listener) + §7
# (Tauri config) + §13 (signing) + §15 (no auto-update) are the authoritative
# spec.
#
# This script DOES NOT cross-compile — Nuitka cannot cross-compile. To build
# for a different platform, run this script on that platform's host. The CI
# matrix in `.github/workflows/tauri-{windows,macos,linux}-build.yml` covers
# the cross-platform case via separate runners.
#
# Usage:
#   bash scripts/build/build_tauri_all.sh                # host triple, no signing
#   bash scripts/build/build_tauri_all.sh --sign         # host triple + sign (requires secrets)
#   bash scripts/build/build_tauri_all.sh --target x86_64-apple-darwin
#   bash scripts/build/build_tauri_all.sh --skip-sidecar # cargo tauri build only (dev)
#   bash scripts/build/build_tauri_all.sh --check        # dry-run: print plan, exit 0
#
# Exit codes:
#   0  success
#   1  misuse / missing toolchain
#   2  per-platform build script failed
#   3  cargo tauri build failed
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC_TAURI="$PROJECT_ROOT/src-tauri"

# ─── Arg parsing ─────────────────────────────────────────────────────────────
DO_SIGN=0
SKIP_SIDECAR=0
CHECK_ONLY=0
TARGET_TRIPLE=""

print_usage() {
    cat <<EOF
Usage: $0 [--sign] [--skip-sidecar] [--check] [--target TRIPLE]

  --sign                Code-sign + notarize the bundle (requires platform
                        secrets — see docs/migration/signing-guide.md).
  --skip-sidecar        Skip the Nuitka sidecar + prewarm + native builds;
                        only run cargo tauri build (use after the binaries are
                        already in place from a prior run).
  --check               Dry-run: print the build plan + exit 0.
  --target TRIPLE       Rust target triple (e.g. aarch64-apple-darwin).
                        Defaults to the host triple.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sign)         DO_SIGN=1; shift ;;
        --skip-sidecar) SKIP_SIDECAR=1; shift ;;
        --check)        CHECK_ONLY=1; shift ;;
        --target)       TARGET_TRIPLE="${2:-}"; shift 2 ;;
        -h|--help)      print_usage; exit 0 ;;
        *) echo "ERROR: unknown arg: $1" >&2; print_usage; exit 1 ;;
    esac
done

# ─── Detect host platform ────────────────────────────────────────────────────
case "$(uname -s)" in
    Darwin*)                  HOST_PLATFORM="macos" ;;
    MINGW*|MSYS*|CYGWIN*)     HOST_PLATFORM="windows" ;;
    Linux*)                   HOST_PLATFORM="linux" ;;
    *) echo "ERROR: unsupported host: $(uname -s)" >&2; exit 1 ;;
esac

# Host arch (matches Rust's std::env::consts::ARCH).
case "$(uname -m)" in
    x86_64|amd64) HOST_ARCH="x86_64" ;;
    arm64|aarch64) HOST_ARCH="aarch64" ;;
    *) echo "ERROR: unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

# Default target triple = host triple.
if [[ -z "$TARGET_TRIPLE" ]]; then
    case "$HOST_PLATFORM" in
        windows) TARGET_TRIPLE="${HOST_ARCH}-pc-windows-msvc" ;;
        macos)   TARGET_TRIPLE="${HOST_ARCH}-apple-darwin" ;;
        linux)   TARGET_TRIPLE="${HOST_ARCH}-unknown-linux-gnu" ;;
    esac
fi

echo "::group::build_tauri_all — plan"
echo "  HOST_PLATFORM : $HOST_PLATFORM"
echo "  HOST_ARCH     : $HOST_ARCH"
echo "  TARGET_TRIPLE : $TARGET_TRIPLE"
echo "  DO_SIGN       : $DO_SIGN"
echo "  SKIP_SIDECAR  : $SKIP_SIDECAR"
echo "  CHECK_ONLY    : $CHECK_ONLY"
echo "  PROJECT_ROOT  : $PROJECT_ROOT"
echo "::endgroup::"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "[build_tauri_all] --check: dry-run complete, exiting 0."
    exit 0
fi

# ─── Sanity: project layout ──────────────────────────────────────────────────
if [[ ! -f "$SRC_TAURI/tauri.conf.json" ]]; then
    echo "ERROR: src-tauri/tauri.conf.json not found at $SRC_TAURI" >&2
    exit 1
fi
if [[ ! -f "$SRC_TAURI/Cargo.toml" ]]; then
    echo "ERROR: src-tauri/Cargo.toml not found at $SRC_TAURI" >&2
    exit 1
fi

# ─── Phase 0: ensure Tauri icons + binary stubs are present ──────────────────
# BUILD-4: src-tauri/tauri.conf.json references 4 PNG icons + 6 sidecar binaries
# (externalBin) + 3 native + 6 prewarm resources. On a clean checkout NONE of
# these exist, so `cargo tauri build` (Phase 1c) fails immediately with
# "failed to open icon 'icons/32x32.png'" / "resource path ... doesn't exist".
# gen_tauri_icons_stub.py --check verifies all stubs are present (exit 0) or
# reports which are missing (exit 1). If --check fails, generate the stubs
# automatically so a developer doesn't have to run the generator manually first
# (see docs/migration/tauri-build-runbook.md "Common failures"). Stubs are NOT
# real binaries — they print "STUB: not a real sidecar" + exit 1 if executed;
# Phase 1a below overwrites them with real Nuitka/compiled artifacts.
echo "::group::Phase 0 — ensure Tauri icons + binary stubs"
ICON_STUB="$PROJECT_ROOT/scripts/gen_tauri_icons_stub.py"
if ! python "$ICON_STUB" --check; then
    echo "[build_tauri_all] some stubs missing — generating..."
    python "$ICON_STUB" || { echo "ERROR: gen_tauri_icons_stub.py failed" >&2; exit 1; }
fi
echo "::endgroup::"

# ─── Phase 1a: build the Nuitka sidecar + prewarm + native listener ──────────
if [[ "$SKIP_SIDECAR" -eq 0 ]]; then
    echo "::group::Phase 1a — per-platform sidecar + prewarm + native"
    case "$HOST_PLATFORM" in
        windows)
            # Windows: arch is selected via the script's $1 arg. The script
            # itself invokes PowerShell under the hood for the actual Nuitka
            # build (Nuitka on Windows works best from PowerShell).
            bash "$SCRIPT_DIR/build_sidecar_windows.sh" "$HOST_ARCH" || { echo "ERROR: build_sidecar_windows.sh failed" >&2; exit 2; }
            bash "$SCRIPT_DIR/build_prewarm_windows.sh" "$HOST_ARCH" || { echo "ERROR: build_prewarm_windows.sh failed" >&2; exit 2; }
            bash "$SCRIPT_DIR/build_native_listener_windows.sh"     || { echo "ERROR: build_native_listener_windows.sh failed" >&2; exit 2; }
            ;;
        macos)
            bash "$SCRIPT_DIR/build_sidecar_macos.sh" "$HOST_ARCH" || { echo "ERROR: build_sidecar_macos.sh failed" >&2; exit 2; }
            bash "$SCRIPT_DIR/build_prewarm_macos.sh" "$HOST_ARCH" || { echo "ERROR: build_prewarm_macos.sh failed" >&2; exit 2; }
            bash "$SCRIPT_DIR/build_native_listener_macos.sh"      || { echo "ERROR: build_native_listener_macos.sh failed" >&2; exit 2; }
            ;;
        linux)
            bash "$SCRIPT_DIR/build_sidecar_linux.sh" "$HOST_ARCH" || { echo "ERROR: build_sidecar_linux.sh failed" >&2; exit 2; }
            bash "$SCRIPT_DIR/build_prewarm_linux.sh" "$HOST_ARCH" || { echo "ERROR: build_prewarm_linux.sh failed" >&2; exit 2; }
            bash "$SCRIPT_DIR/build_native_listener_linux.sh"      || { echo "ERROR: build_native_listener_linux.sh failed" >&2; exit 2; }
            ;;
    esac
    echo "::endgroup::"
fi

# ─── Phase 1b: build the React renderer (shared between Electron + Tauri) ───
echo "::group::Phase 1b — React renderer"
(
    cd "$PROJECT_ROOT/voice_typer/client"
    if [[ ! -d node_modules ]]; then
        echo "[build_tauri_all] Installing Node deps (npm ci)..."
        npm ci
    fi
    echo "[build_tauri_all] Building renderer (npm run build:renderer)..."
    npm run build:renderer
)
echo "::endgroup::"

# ─── Phase 1c: cargo tauri build ─────────────────────────────────────────────
echo "::group::Phase 1c — cargo tauri build --target $TARGET_TRIPLE"
(
    cd "$SRC_TAURI"
    # macOS universal binary requires both arches' sidecar binaries present.
    # This script only builds the host-arch sidecar; for universal, run the
    # macOS workflow in CI which builds both arches then `cargo tauri build
    # --target universal-apple-darwin`.
    cargo tauri build --target "$TARGET_TRIPLE"
)
BUILD_RC=$?
echo "::endgroup::"

if [[ $BUILD_RC -ne 0 ]]; then
    echo "ERROR: cargo tauri build failed (exit $BUILD_RC)" >&2
    exit 3
fi

# ─── Phase 1d: verify build artifacts ─────────────────────────────────────────
# BUILD-5: cargo tauri build can silently produce an empty / missing bundle on
# toolchain misconfigurations (missing webkit2gtk, MSVC mismatch, stale
# externalBin, etc.). This phase verifies the artifact set so a silent failure
# doesn't slip through to signing / release. Checks:
#   (a) at least one bundle file exists in target/$TARGET_TRIPLE/release/bundle/,
#   (b) each bundle file is non-empty and > 1 MB (catches truncated/corrupt
#       bundles — a real installer is tens of MB),
#   (c) the sidecar binary was placed in src-tauri/bin/ (Tauri externalBin
#       target — if missing, the installed app fails to launch the backend).
echo "::group::Phase 1d — verify build artifacts"
BUNDLE_DIR="$SRC_TAURI/target/$TARGET_TRIPLE/release/bundle"
if [[ ! -d "$BUNDLE_DIR" ]]; then
    echo "ERROR: bundle dir not found: $BUNDLE_DIR" >&2
    echo "  cargo tauri build did not produce a bundle directory." >&2
    exit 4
fi
# (a) at least one bundle file exists.
BUNDLE_FILE_COUNT=$(find "$BUNDLE_DIR" -type f 2>/dev/null | wc -l)
if [[ "$BUNDLE_FILE_COUNT" -eq 0 ]]; then
    echo "ERROR: no bundle files found in $BUNDLE_DIR" >&2
    exit 4
fi
# (b) each bundle file is non-empty and > 1 MB.
MIN_BUNDLE_BYTES=1048576  # 1 MB
while IFS= read -r -d '' _bf; do
    _size=$(stat -c %s "$_bf" 2>/dev/null || stat -f %z "$_bf" 2>/dev/null || echo 0)
    if [[ "$_size" -lt "$MIN_BUNDLE_BYTES" ]]; then
        echo "ERROR: bundle file too small (< 1 MB): $_bf ($_size bytes)" >&2
        exit 4
    fi
done < <(find "$BUNDLE_DIR" -type f -print0)
# (c) the sidecar binary was placed in src-tauri/bin/.
SIDECAR_BIN="$SRC_TAURI/bin/python-sidecar-$TARGET_TRIPLE"
if [[ ! -f "$SIDECAR_BIN" ]]; then
    echo "ERROR: sidecar binary not found: $SIDECAR_BIN" >&2
    echo "  Phase 1a should have placed python-sidecar-$TARGET_TRIPLE in src-tauri/bin/." >&2
    exit 4
fi
echo "[build_tauri_all] OK: $BUNDLE_FILE_COUNT bundle file(s) + sidecar binary verified"
echo "::endgroup::"

# ─── Phase 1e: optional signing ──────────────────────────────────────────────
if [[ "$DO_SIGN" -eq 1 ]]; then
    echo "::group::Phase 1e — code-sign + notarize (ADR-0020 §13)"
    echo "[build_tauri_all] Signing is platform-specific — see docs/migration/signing-guide.md"
    case "$HOST_PLATFORM" in
        windows)
            echo "[build_tauri_all] Windows Authenticode: requires WIN_CSC_LINK + WIN_CSC_KEY_PASSWORD env vars."
            echo "  See signing-guide.md §'Windows — Authenticode'."
            ;;
        macos)
            echo "[build_tauri_all] macOS: codesign + notarytool + stapler. Requires MAC_SIGNING_IDENTITY +"
            echo "  APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID env vars."
            echo "  See signing-guide.md §'macOS — Developer ID + notarization + stapling'."
            ;;
        linux)
            echo "[build_tauri_all] Linux: unsigned by default (ADR-0020 §13.3). Optional GPG-sign"
            echo "  of .deb/.rpm is documented but not automated by this script."
            ;;
    esac
    echo "::endgroup::"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
echo "::group::build_tauri_all — artifacts"
case "$HOST_PLATFORM" in
    windows)
        find "$SRC_TAURI/target/$TARGET_TRIPLE/release/bundle" -maxdepth 3 -type f 2>/dev/null | sort || true
        ;;
    macos)
        find "$SRC_TAURI/target/$TARGET_TRIPLE/release/bundle" -maxdepth 3 2>/dev/null | sort || true
        ;;
    linux)
        find "$SRC_TAURI/target/$TARGET_TRIPLE/release/bundle" -maxdepth 3 -type f 2>/dev/null | sort || true
        ;;
esac
echo "::endgroup::"

echo "[build_tauri_all] DONE. ADR-0020 §15: NO auto-update manifest was generated."
echo "[build_tauri_all] Do NOT flip the default shipping app from Electron to Tauri until"
echo "[build_tauri_all] the platform's Phase 5 cutover gate is met (docs/migration/cutover-playbook.md)."
exit 0
