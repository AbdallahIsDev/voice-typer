#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka prewarm build (macOS x86_64 + aarch64)
# ADR-0020 §5 — Prewarm is frozen the SAME Nuitka way as the sidecar, into
# prewarm-<triple>. Prewarm is a BUNDLE RESOURCE (not externalBin):
# launched by the macOS LaunchAgent (~/Library/LaunchAgents/com.voicetyper.prewarm.plist)
# via resolve_prewarm_exe(), NOT by Tauri as a managed child.
#
# Codesign (S5-CR-56): Nuitka's `--macos-signed-app-name` only sets the
# bundle's signed name during bundle creation — it does NOT actually
# invoke codesign on the output binary. This script explicitly signs the
# output binary:
#   - If $MAC_SIGNING_IDENTITY is set (CI release builds), passes
#     `--macos-sign-identity="$MAC_SIGNING_IDENTITY"` to Nuitka so the
#     binary is signed at build time with a Developer ID Application cert.
#   - If $MAC_SIGNING_IDENTITY is empty (local dev builds), falls back to
#     ad-hoc `codesign --force --sign -` on the output binary, mirroring
#     `build_native_listener_macos.sh`.
#
# Output:
#   src-tauri/resources/prewarm-x86_64-apple-darwin
#   src-tauri/resources/prewarm-aarch64-apple-darwin
#
# Usage:
#   bash scripts/build/build_prewarm_macos.sh aarch64   # default (host arch)
#   bash scripts/build/build_prewarm_macos.sh x86_64    # Intel (via Rosetta)
#   bash scripts/build/build_prewarm_macos.sh --check
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESOURCES_DIR="$PROJECT_ROOT/src-tauri/resources"

# ─── Args ────────────────────────────────────────────────────────────────────
ARCH="${1:-}"
if [[ "$ARCH" == "--check" ]]; then
    # Delegate to the sibling sidecar build script which performs the
    # real toolchain probe (python-build-standalone interpreter,
    # nuitka, faster_whisper, ctranslate2). The prewarm binary uses
    # the exact same Nuitka toolchain as the sidecar, so a successful
    # sidecar --check implies a successful prewarm build.
    # WR-18: previously this was a stub that just echoed "OK if that
    # passes" and exited 0 without invoking the sibling — masking
    # real toolchain breakage.
    # Delegate to the sibling sidecar build script which performs the
    # real toolchain probe (python-build-standalone interpreter,
    # nuitka, faster_whisper, ctranslate2). The prewarm binary uses
    # the exact same Nuitka toolchain as the sidecar, so a successful
    # sidecar --check implies a successful prewarm build.
    # XS-7: previously this was a stub that just echoed "OK" and
    # exited 0 (WR-18 fix added the delegate but did not verify the
    # prewarm binary itself). We now ALSO verify any existing prewarm
    # binary is non-corrupt: exists, >= 4 KiB (a real Nuitka onefile
    # is multi-MB; < 4 KiB is a stub or zero-byte placeholder), and
    # executable. If no binary is present, emit a NOTICE (preserves
    # the pre-build gating use case where --check is called before
    # the first build).
    bash "$SCRIPT_DIR/build_sidecar_macos.sh" --check
    # Verify any existing prewarm binary in src-tauri/resources/.
    PREWARM_DIR="$PROJECT_ROOT/src-tauri/resources"
    FOUND_PREWARM=0
    if [[ -d "$PREWARM_DIR" ]]; then
        for bin in "$PREWARM_DIR"/prewarm-*; do
            [[ -e "$bin" ]] || continue
            FOUND_PREWARM=1
            # Size check: < 4 KiB = stub/placeholder
            SIZE=$(stat -c%s "$bin" 2>/dev/null || stat -f%z "$bin" 2>/dev/null || echo 0)
            if [[ "$SIZE" -lt 4096 ]]; then
                echo "ERROR: prewarm binary $bin is corrupt (size=$SIZE bytes, expected >= 4096)" >&2
                exit 1
            fi
            # Executable-bit check (skip on Windows .exe)
            case "$bin" in
                *.exe) ;;
                *)
                    if [[ ! -x "$bin" ]]; then
                        echo "ERROR: prewarm binary $bin is not executable" >&2
                        exit 1
                    fi
                    ;;
            esac
        done
    fi
    if [[ "$FOUND_PREWARM" -eq 0 ]]; then
        echo "NOTICE: no prewarm binary found in $PREWARM_DIR — run without --check to build." >&2
    else
        echo "[build_prewarm_macos.sh] OK: existing prewarm binary verified."
    fi
    exit 0
fi

if [[ -z "$ARCH" ]]; then
    case "$(uname -m)" in
        arm64)    ARCH="aarch64" ;;
        x86_64)   ARCH="x86_64" ;;
        *) echo "ERROR: unsupported host arch: $(uname -m)" >&2; exit 1 ;;
    esac
fi

case "$ARCH" in
    x86_64|aarch64) ;;
    *) echo "ERROR: arch must be x86_64 or aarch64 (got: $ARCH)" >&2; exit 1 ;;
esac

TRIPLE="${ARCH}-apple-darwin"
OUTPUT_NAME="prewarm-${TRIPLE}"
OUTPUT_PATH="$RESOURCES_DIR/$OUTPUT_NAME"

echo "[build_prewarm_macos] ARCH=$ARCH TRIPLE=$TRIPLE"
echo "[build_prewarm_macos] OUTPUT=$OUTPUT_PATH"

# ─── Locate the python-build-standalone interpreter ──────────────────────────
PYBS_DIR="${VOICE_TYPER_PYBS_DIR:-}"
if [[ -n "$PYBS_DIR" && -f "$PYBS_DIR/python/bin/python3" ]]; then
    PY="$PYBS_DIR/python/bin/python3"
elif [[ -n "${PYBS:-}" && -f "$PYBS" ]]; then
    PY="$PYBS"
else
    PY="$(command -v python3)"
    if [[ -z "$PY" ]]; then
        echo "ERROR: no python3 interpreter found." >&2
        exit 1
    fi
fi
echo "[build_prewarm_macos] PY=$PY"

SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
"$PY" -c 'import faster_whisper, ctranslate2, voice_typer.server.prewarm' \
    || { echo "ERROR: build env missing deps" >&2; exit 1; }

CT2_LIB_DIR="$SITE/ctranslate2/lib"
CT2_LIBS_DIR="$SITE/ctranslate2/libs"

# ─── Prepare output dir ──────────────────────────────────────────────────────
mkdir -p "$RESOURCES_DIR"

# ─── Run Nuitka (ADR-0020 §5) ────────────────────────────────────────────────
# Parallel C compilation: Nuitka invokes clang per Python module;
# --jobs=N fans those out (the default was sequential). Override with
# NUITKA_JOBS. Default = core count (sysctl), CLAMPED to 4 for CI: this
# script runs in the macOS release workflow on hosted runners with
# limited RAM (each C-compiler job forks ~300-500 MB RSS; the Windows
# release workflow already uses --jobs=3 as precedent). An explicit
# NUITKA_JOBS override bypasses the clamp.
if [[ -z "${NUITKA_JOBS:-}" ]]; then
    NUITKA_JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1)"
    if [[ "$NUITKA_JOBS" -gt 4 ]]; then
        NUITKA_JOBS=4
    fi
fi
echo "[build_prewarm_macos] Nuitka --jobs=$NUITKA_JOBS"
echo "[build_prewarm_macos] Running Nuitka..."
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
    --jobs="$NUITKA_JOBS"
    --enable-plugin=numpy
    --enable-plugin=anti-bloat
    --nofollow-import-to=torch._dynamo
    --nofollow-import-to=torch._inductor
    --nofollow-import-to=torch.export
    --nofollow-import-to=torch._functorch
    --nofollow-import-to=transformers
    --include-package=faster_whisper
    --include-package=ctranslate2
    --include-package=voice_typer
    --include-package=websockets
    --include-package-data=voice_typer.server
    --include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR"
    --macos-create-bundle
    --macos-app-name=VoiceTyperPrewarm
    --macos-signed-app-name=com.voicetyper.prewarm
    --macos-app-mode=background
    --onefile-tempdir-spec="$HOME/Library/Application Support/voice-typer/prewarm-onefile-tmp"
    --output-filename="$OUTPUT_NAME"
    --output-dir="$RESOURCES_DIR"
    "$PROJECT_ROOT/voice_typer/server/prewarm/__main__.py"
)
if [[ -n "${MAC_SIGNING_IDENTITY:-}" ]]; then
    # S5-CR-56: pass the Developer ID Application identity to Nuitka so it
    # signs the binary at build time. `--macos-signed-app-name` only sets
    # the bundle's signed name; it does not invoke codesign.
    NUITKA_ARGS+=(--macos-sign-identity="$MAC_SIGNING_IDENTITY")
fi
if [[ -d "$CT2_LIBS_DIR" ]]; then
    NUITKA_ARGS+=(--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR")
fi
"$PY" -m nuitka "${NUITKA_ARGS[@]}"

if [[ ! -f "$OUTPUT_PATH" ]]; then
    echo "ERROR: $OUTPUT_PATH not built" >&2
    exit 1
fi
chmod +x "$OUTPUT_PATH"
SIZE_MB=$(du -m "$OUTPUT_PATH" | cut -f1)
echo "[build_prewarm_macos] OK: $OUTPUT_PATH (${SIZE_MB} MB)"

# S5-CR-56: ad-hoc codesign fallback when no Developer ID identity is set.
# Mirrors `build_native_listener_macos.sh`. When MAC_SIGNING_IDENTITY is set,
# Nuitka already signed the binary at build time via --macos-sign-identity
# (see above) — skip the ad-hoc fallback in that case.
if [[ -z "${MAC_SIGNING_IDENTITY:-}" ]] && command -v codesign >/dev/null; then
    echo "[build_prewarm_macos] Ad-hoc codesign (parent .app will re-sign --deep)..."
    codesign --force --sign - "$OUTPUT_PATH" || true
fi

echo "[build_prewarm_macos] NEXT: codesign + notarize (signing-guide.md §13.2)."
