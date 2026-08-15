#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka worker build (macOS x86_64 + aarch64)
#
# Plan-runtime-pack-split §4.4 / §11.5 — builds the runtime-pack worker exe
# (voice-typer-worker-<triple>), the heavy-ML process that owns
# onnxruntime (VAD + Parakeet) + ctranslate2/faster_whisper (Whisper
# fallback) + numpy/scipy/av/pyrnnoise + the bundled silero_vad.onnx.
# The slim-core sidecar connects to this worker via a localhost WebSocket
# (master plan §7) and offloads all heavy inference to it.
#
# Output:
#   src-tauri/bin/voice-typer-worker-x86_64-apple-darwin
#   src-tauri/bin/voice-typer-worker-aarch64-apple-darwin
#
# Mirrors build_sidecar_macos.sh + build_prewarm_macos.sh: same Nuitka
# toolchain, same python-build-standalone interpreter, same
# VOICE_TYPER_PYBS_DIR env var contract. The worker is a SEPARATE process
# — it has its own --onefile-tempdir-spec so its self-extraction doesn't
# collide with the sidecar's or the prewarm's.
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
# Usage:
#   bash scripts/build/build_worker_macos.sh aarch64   # default (host arch)
#   bash scripts/build/build_worker_macos.sh x86_64    # Intel (via Rosetta)
#   bash scripts/build/build_worker_macos.sh --check   # verify toolchain
#
# CI invocation (.github/workflows/tauri-macos-build.yml):
#   bash scripts/build/build_worker_macos.sh aarch64     (aarch64 leg)
#   bash scripts/build/build_worker_macos.sh x86_64      (x86_64 leg)
# with VOICE_TYPER_PYBS_DIR=$github_workspace/.python-build-standalone in env.
# The CI step is gated on hashFiles('scripts/build/build_worker_macos.sh')
# so it stays inert until this script lands (C-CI-2: do not edit the workflow).
#
# CI gate contract (binding — C-CI-6/8/9/13): see build_worker_windows.sh
# header for the full rationale. Same flags apply here, with the macOS
# platform differences: no --windows-console-mode=disable, onefile tempdir
# spec uses ~/Library/Application Support, output has no .exe suffix,
# binary is chmod +x'd + codesigned (or ad-hoc signed in dev).
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKER_DIR="$PROJECT_ROOT/src-tauri/bin"

# ─── Args ────────────────────────────────────────────────────────────────────
ARCH="${1:-}"
if [[ "$ARCH" == "--check" ]]; then
    # Pre-flight toolchain verification (mirrors build_sidecar_macos.sh --check).
    echo "[build_worker_macos] --check: verifying toolchain"
    _ck_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    _ck_project_root="$(cd "$_ck_script_dir/../.." && pwd)"
    _ck_pybs_dir="${VOICE_TYPER_PYBS_DIR:-$_ck_project_root/.python-build-standalone}"
    PYBS_PYTHON=""
    if [[ -n "$_ck_pybs_dir" && -f "$_ck_pybs_dir/python/bin/python3" ]]; then
        PYBS_PYTHON="$_ck_pybs_dir/python/bin/python3"
    elif [[ -n "${PYBS:-}" && -f "$PYBS" ]]; then
        PYBS_PYTHON="$PYBS"
    else
        PYBS_PYTHON="$(command -v python3 || true)"
    fi
    if [[ -z "$PYBS_PYTHON" ]]; then
        echo "MISSING: python-build-standalone interpreter (set VOICE_TYPER_PYBS_DIR)" >&2
        exit 1
    fi
    "$PYBS_PYTHON" -c "import nuitka" 2>/dev/null \
        || { echo "MISSING: nuitka (pip install nuitka==2.8.10)" >&2; exit 1; }
    # C-CI-6: nuitka must be exactly 2.8.10 (NU-105).
    _ck_nuitka_ver="$("$PYBS_PYTHON" -m nuitka --version 2>/dev/null | head -1 || true)"
    if [[ "$_ck_nuitka_ver" != *"2.8.10"* ]]; then
        echo "MISSING: nuitka==2.8.10 (got: $_ck_nuitka_ver)" >&2
        exit 1
    fi
    "$PYBS_PYTHON" -c "import onnxruntime, numpy, scipy, websockets" 2>/dev/null \
        || { echo "MISSING: onnxruntime/numpy/scipy/websockets (worker deps)" >&2; exit 1; }
    "$PYBS_PYTHON" -c "import voice_typer.worker" 2>/dev/null \
        || { echo "MISSING: voice_typer.worker module (run 'pip install -e .' in the repo root)" >&2; exit 1; }
    command -v swiftc >/dev/null || { echo "MISSING: swiftc (Xcode CLT)" >&2; exit 1; }
    echo "[build_worker_macos] OK: toolchain ready (nuitka==2.8.10)"
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
OUTPUT_NAME="voice-typer-worker-${TRIPLE}"
OUTPUT_PATH="$WORKER_DIR/$OUTPUT_NAME"

echo "[build_worker_macos] ARCH=$ARCH TRIPLE=$TRIPLE"
echo "[build_worker_macos] OUTPUT=$OUTPUT_PATH"

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
    echo "[build_worker_macos] WARNING: using 'python3' from PATH ($PY)." >&2
    echo "  For release builds, use a python-build-standalone install (ADR-0020 §4.3)." >&2
fi
echo "[build_worker_macos] PY=$PY"

# ─── Resolve site-packages ───────────────────────────────────────────────────
SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
echo "[build_worker_macos] SITE=$SITE"

"$PY" -c 'import onnxruntime, numpy, scipy, websockets, voice_typer.worker; print("worker imports ok")' \
    || { echo "ERROR: build env missing onnxruntime/numpy/scipy/websockets/voice_typer.worker" >&2; exit 1; }

# C-CI-6: nuitka must be exactly 2.8.10 (NU-105).
NUITKA_VER="$("$PY" -m nuitka --version 2>/dev/null | head -1 || true)"
if [[ "$NUITKA_VER" != *"2.8.10"* ]]; then
    echo "ERROR: nuitka==2.8.10 required (C-CI-6, NU-105). Got: '$NUITKA_VER'" >&2
    echo "  Install with: $PY -m pip install 'nuitka==2.8.10'" >&2
    exit 1
fi
echo "[build_worker_macos] nuitka=$NUITKA_VER"

# ctranslate2/lib + libs — optional on the worker (the Whisper fallback
# may not be wired in for Phase 2a; the worker still builds without it).
CT2_LIB_DIR="$SITE/ctranslate2/lib"
CT2_LIBS_DIR="$SITE/ctranslate2/libs"
if [[ -d "$CT2_LIB_DIR" ]]; then
    echo "[build_worker_macos] CT2_LIB_DIR=$CT2_LIB_DIR"
else
    echo "[build_worker_macos] NOTE: $CT2_LIB_DIR not found — ctranslate2 not installed (Whisper fallback unavailable in this build)."
fi

# ─── Prepare output dir ──────────────────────────────────────────────────────
mkdir -p "$WORKER_DIR"

# ─── Run Nuitka (plan-runtime-pack-split §4.4 / §11.5) ───────────────────────
echo "[build_worker_macos] Running Nuitka..."
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
    --enable-plugin=anti-bloat
    # C-CI-8 / NU-106: --module-parameter=torch-disable-jit=no stays. See
    # build_worker_windows.sh header for full rationale.
    --module-parameter=torch-disable-jit=no
    # C-CI-8 / NU-106: --nofollow-import-to ONLY for the lazily-imported safe
    # torch.* submodules. Do NOT add for torch.utils.data.distributed /
    # torch.export / torch._functorch / torch.testing / torch.package.
    --nofollow-import-to=torch._dynamo
    --nofollow-import-to=torch._inductor
    --nofollow-import-to=torch.onnx
    --nofollow-import-to=torch.utils.benchmark
    --nofollow-import-to=transformers
    --nofollow-import-to=scipy._lib.cobyqa
    --nofollow-import-to=scipy._lib.array_api_extra.testing
    --nofollow-import-to=scipy.spatial.transform
    --nofollow-import-to=scipy.ndimage
    --nofollow-import-to=scipy.sparse.linalg
    --nofollow-import-to=sympy
    --nofollow-import-to=mpmath
    --nofollow-import-to=pytest
    --nofollow-import-to=PIL.ImageQt
    --nofollow-import-to=PIL.ImageTk
    --nofollow-import-to=PIL.ImageCms
    --nofollow-import-to=psutil._pslinux
    --nofollow-import-to=psutil._psosx
    --nofollow-import-to=psutil._psbsd
    --nofollow-import-to=psutil._pssunos
    --nofollow-import-to=psutil._psaix
    --include-package=voice_typer
    --include-package=onnxruntime
    --include-package=websockets
    --include-package-data=voice_typer.server
    --macos-create-bundle
    --macos-app-name=VoiceTyperWorker
    --macos-signed-app-name=com.voicetyper.worker
    --macos-app-mode=background
    --onefile-tempdir-spec="$HOME/Library/Application Support/voice-typer/worker-onefile-tmp"
    --output-filename="$OUTPUT_NAME"
    --output-dir="$WORKER_DIR"
    "$PROJECT_ROOT/voice_typer/worker/__main__.py"
)
if [[ -n "${MAC_SIGNING_IDENTITY:-}" ]]; then
    # S5-CR-56: pass the Developer ID Application identity to Nuitka so it
    # signs the binary at build time. `--macos-signed-app-name` only sets
    # the bundle's signed name; it does not invoke codesign.
    NUITKA_ARGS+=(--macos-sign-identity="$MAC_SIGNING_IDENTITY")
fi
if [[ -d "$CT2_LIB_DIR" ]]; then
    NUITKA_ARGS+=(--include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR")
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
echo "[build_worker_macos] OK: $OUTPUT_PATH (${SIZE_MB} MB)"

# S5-CR-56: ad-hoc codesign fallback when no Developer ID identity is set.
# Mirrors `build_native_listener_macos.sh` + `build_sidecar_macos.sh`. When
# MAC_SIGNING_IDENTITY is set, Nuitka already signed the binary at build
# time via --macos-sign-identity (see above) — skip the ad-hoc fallback.
if [[ -z "${MAC_SIGNING_IDENTITY:-}" ]] && command -v codesign >/dev/null; then
    echo "[build_worker_macos] Ad-hoc codesign (parent .app will re-sign --deep)..."
    codesign --force --sign - "$OUTPUT_PATH" || true
fi

echo "[build_worker_macos] NEXT: codesign + notarize + staple (signing-guide.md §13.2)."
