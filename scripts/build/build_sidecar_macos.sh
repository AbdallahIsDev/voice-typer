#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka sidecar build (macOS x86_64 + aarch64)
# ADR-0020 §4.3 — Nuitka freeze of voice_typer/server/ipc_server.py into
# python-sidecar-<triple>, using python-build-standalone as the base
# interpreter.
#
# Output:
#   src-tauri/bin/python-sidecar-x86_64-apple-darwin
#   src-tauri/bin/python-sidecar-aarch64-apple-darwin
#
# This script is designed to run on a macOS host. For x86_64 on an Apple
# Silicon host, the script relies on Rosetta 2 being installed (the CI
# workflow installs it explicitly).
#
# Usage:
#   bash scripts/build/build_sidecar_macos.sh aarch64   # default (host arch)
#   bash scripts/build/build_sidecar_macos.sh x86_64    # Intel (via Rosetta)
#   bash scripts/build/build_sidecar_macos.sh --check   # verify toolchain
#
# ADR-0020 §4.3 mandates:
#   - python-build-standalone cpython-3.12.x+<arch>-apple-darwin
#   - --standalone --onefile
#   - --include-package=faster_whisper --include-package=ctranslate2
#   - --include-package=voice_typer --include-package=websockets
#   - --include-data-dir=<SITE>/ctranslate2/lib=<SITE>/ctranslate2/lib
#   - --include-data-dir=<SITE>/ctranslate2/libs=<SITE>/ctranslate2/libs
#   - --macos-create-bundle --macos-app-name=VoiceTyperSidecar
#   - --macos-signed-app-name=com.voicetyper.sidecar
#   - --macos-app-mode=background   (LSUIElement=true — no Dock icon)
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
#     `build_native_listener_macos.sh`. Ad-hoc signing lets the parent
#     `.app` re-sign --deep during the Tauri bundle step.
#
# CTranslate2 on macOS: the wheels ship libctranslate2.dylib + libiomp5.dylib
# under $SITE/ctranslate2/lib/. Apple Silicon wheels are CPU-only (no CUDA).
# Verify with `otool -L` that every @rpath dependency resolves in the build env.
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SIDECAR_DIR="$PROJECT_ROOT/src-tauri/bin"

# ─── Args ────────────────────────────────────────────────────────────────────
ARCH="${1:-}"
if [[ "$ARCH" == "--check" ]]; then
    echo "[build_sidecar_macos] --check: verifying toolchain"
    command -v python3 >/dev/null || { echo "MISSING: python3" >&2; exit 1; }
    python3 -c "import nuitka" 2>/dev/null || { echo "MISSING: nuitka" >&2; exit 1; }
    python3 -c "import faster_whisper, ctranslate2" 2>/dev/null || { echo "MISSING: faster_whisper/ctranslate2" >&2; exit 1; }
    command -v swiftc >/dev/null || { echo "MISSING: swiftc (Xcode CLT)" >&2; exit 1; }
    echo "[build_sidecar_macos] OK: toolchain ready"
    exit 0
fi

if [[ -z "$ARCH" ]]; then
    # Default to host arch.
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
OUTPUT_NAME="python-sidecar-${TRIPLE}"
OUTPUT_PATH="$SIDECAR_DIR/$OUTPUT_NAME"

echo "[build_sidecar_macos] ARCH=$ARCH TRIPLE=$TRIPLE"
echo "[build_sidecar_macos] OUTPUT=$OUTPUT_PATH"

# ─── Locate the python-build-standalone interpreter ──────────────────────────
# Priority:
#   1. $VOICE_TYPER_PYBS_DIR/python/bin/python3 (set by CI workflow)
#   2. $PYBS env var
#   3. `python3` from PATH (dev fallback)
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
    echo "[build_sidecar_macos] WARNING: using 'python3' from PATH ($PY)." >&2
    echo "  For release builds, use a python-build-standalone install (ADR-0020 §4.3)." >&2
fi
echo "[build_sidecar_macos] PY=$PY"

# ─── Resolve site-packages ───────────────────────────────────────────────────
SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
echo "[build_sidecar_macos] SITE=$SITE"

"$PY" -c 'import faster_whisper, ctranslate2, websockets; print("ctranslate2", ctranslate2.__version__)' \
    || { echo "ERROR: build env missing faster_whisper/ctranslate2/websockets" >&2; exit 1; }

# ─── ctranslate2/lib + libs ──────────────────────────────────────────────────
CT2_LIB_DIR="$SITE/ctranslate2/lib"
CT2_LIBS_DIR="$SITE/ctranslate2/libs"
if [[ ! -d "$CT2_LIB_DIR" ]]; then
    echo "ERROR: $CT2_LIB_DIR not found — ctranslate2 install is incomplete." >&2
    exit 1
fi
echo "[build_sidecar_macos] CT2_LIB_DIR=$CT2_LIB_DIR"
echo "[build_sidecar_macos] CT2_LIBS_DIR=$CT2_LIBS_DIR (may not exist on all installs)"

# ─── Prepare output dir ──────────────────────────────────────────────────────
mkdir -p "$SIDECAR_DIR"

# ─── Run Nuitka (ADR-0020 §4.3) ──────────────────────────────────────────────
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
echo "[build_sidecar_macos] Nuitka --jobs=$NUITKA_JOBS"
echo "[build_sidecar_macos] Running Nuitka..."
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
    --jobs="$NUITKA_JOBS"
    --enable-plugin=numpy
    --enable-plugin=anti-bloat
    # NU-106 (VAD): keep torch.jit ENABLED. Nuitka's torch plugin
    # disables JIT by default in standalone mode, breaking
    # torch.jit.load(silero_vad.jit) with "module 'torch' has no
    # attribute 'jit'" — Silero VAD silently degrades to RMS. Make the
    # choice explicit.
    --module-parameter=torch-disable-jit=no
    --nofollow-import-to=torch._dynamo
    --nofollow-import-to=torch._inductor
    # NU-106 (VAD): torch.export / torch._functorch are loaded
    # UNCONDITIONALLY by plain `import torch` (torch 2.13) — do NOT
    # exclude them or `import torch` fails with ModuleNotFoundError and
    # Silero VAD silently degrades to RMS.
    --nofollow-import-to=transformers
    --include-package=faster_whisper
    --include-package=ctranslate2
    --include-package=voice_typer
    --include-package=websockets
    --include-package-data=voice_typer.server
    --include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR"
    --macos-create-bundle
    --macos-app-name=VoiceTyperSidecar
    --macos-signed-app-name=com.voicetyper.sidecar
    --macos-app-mode=background
    --onefile-tempdir-spec="$HOME/Library/Application Support/voice-typer/onefile-tmp"
    --output-filename="$OUTPUT_NAME"
    --output-dir="$SIDECAR_DIR"
    "$PROJECT_ROOT/voice_typer/server/ipc_server.py"
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

# ─── Verify ──────────────────────────────────────────────────────────────────
if [[ ! -f "$OUTPUT_PATH" ]]; then
    echo "ERROR: $OUTPUT_PATH not built" >&2
    exit 1
fi
chmod +x "$OUTPUT_PATH"
SIZE_MB=$(du -m "$OUTPUT_PATH" | cut -f1)
echo "[build_sidecar_macos] OK: $OUTPUT_PATH (${SIZE_MB} MB)"

# S5-CR-56: ad-hoc codesign fallback when no Developer ID identity is set.
# Mirrors `build_native_listener_macos.sh`. When MAC_SIGNING_IDENTITY is set,
# Nuitka already signed the binary at build time via --macos-sign-identity
# (see above) — skip the ad-hoc fallback in that case.
if [[ -z "${MAC_SIGNING_IDENTITY:-}" ]] && command -v codesign >/dev/null; then
    echo "[build_sidecar_macos] Ad-hoc codesign (parent .app will re-sign --deep)..."
    codesign --force --sign - "$OUTPUT_PATH" || true
fi

# Verify the dylib dependencies resolve (ADR-0020 §4.3).
if command -v otool >/dev/null; then
    echo "[build_sidecar_macos] otool -L \$CT2_LIB_DIR/libctranslate2.dylib:"
    otool -L "$CT2_LIB_DIR"/libctranslate2.dylib 2>/dev/null | head -20 || true
fi

echo "[build_sidecar_macos] NEXT: codesign + notarize (see docs/migration/signing-guide.md §13.2)."
