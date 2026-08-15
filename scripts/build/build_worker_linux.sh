#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka worker build (Linux x86_64 + aarch64)
#
# Plan-runtime-pack-split §4.4 / §11.5 — builds the runtime-pack worker exe
# (voice-typer-worker-<triple>), the heavy-ML process that owns
# onnxruntime (VAD + Parakeet) + ctranslate2/faster_whisper (Whisper
# fallback) + numpy/scipy/av/pyrnnoise + the bundled silero_vad.onnx.
# The slim-core sidecar connects to this worker via a localhost WebSocket
# (master plan §7) and offloads all heavy inference to it.
#
# Output:
#   src-tauri/bin/voice-typer-worker-x86_64-unknown-linux-gnu
#   src-tauri/bin/voice-typer-worker-aarch64-unknown-linux-gnu
#
# Mirrors build_sidecar_linux.sh + build_prewarm_linux.sh: same Nuitka
# toolchain, same python-build-standalone interpreter, same
# VOICE_TYPER_PYBS_DIR env var contract, same cross-build handling
# (qemu-user-static for aarch64 on x86_64 hosts).
#
# Usage:
#   bash scripts/build/build_worker_linux.sh x86_64    # native x86_64 build
#   bash scripts/build/build_worker_linux.sh aarch64   # native aarch64 build
#                                                      # (requires aarch64 host)
#                                                      # OR cross-build on x86_64
#                                                      # via qemu-user-static
#   bash scripts/build/build_worker_linux.sh --check   # verify toolchain
#
# CI invocation (.github/workflows/tauri-linux-build.yml):
#   bash scripts/build/build_worker_linux.sh "$BUILD_ARCH"
# with VOICE_TYPER_PYBS_DIR=$github_workspace/.python-build-standalone in env.
# The CI step is gated on hashFiles('scripts/build/build_worker_linux.sh')
# so it stays inert until this script lands (C-CI-2: do not edit the workflow).
#
# Linux worker is UNSIGNED by design (ADR-0020 §13.3 — no Linux signing
# secrets exist; the workflow's sign=true gate fails fast at lines 393-397).
#
# CI gate contract (binding — C-CI-6/8/9/13): see build_worker_windows.sh
# header for the full rationale. Same flags apply here, with the Linux
# platform differences: no --windows-console-mode=disable, onefile tempdir
# spec uses XDG_CACHE_HOME, output has no .exe suffix, binary is chmod +x'd.
# =============================================================================
set -euo pipefail

# ─── Parse args ─────────────────────────────────────────────────────────────
ARCH="${1:-}"
if [[ "$ARCH" == "--check" ]]; then
    # Pre-flight toolchain verification (mirrors build_sidecar_linux.sh --check).
    echo "[build_worker_linux] --check: verifying toolchain"
    _ck_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    _ck_project_root="$(cd "$_ck_script_dir/../.." && pwd)"
    _ck_pybs_dir="${VOICE_TYPER_PYBS_DIR:-$_ck_project_root/.python-build-standalone}"
    PYBS_PYTHON=""
    for _ck_triple in x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu; do
        for _ck_candidate in \
            "$_ck_pybs_dir/python/bin/python3" \
            "$_ck_pybs_dir"/cpython-3.12.*+"$_ck_triple"/python/bin/python3 \
            "$_ck_pybs_dir"/cpython-3.12.*+"$_ck_triple"/bin/python3; do
            if [[ -x "$_ck_candidate" ]]; then
                PYBS_PYTHON="$_ck_candidate"
                break 2
            fi
        done
    done
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
    echo "[build_worker_linux] OK: toolchain ready (nuitka==2.8.10)"
    exit 0
fi
if [[ "$ARCH" != "x86_64" && "$ARCH" != "aarch64" ]]; then
    # Allow no-arg default (auto-detect from uname -m) for symmetry with the
    # Windows/macOS sibling scripts — but only the CI passes ARCH explicitly.
    if [[ -z "$ARCH" ]]; then
        case "$(uname -m)" in
            x86_64|amd64)   ARCH="x86_64" ;;
            aarch64|arm64)  ARCH="aarch64" ;;
            *) echo "Usage: $0 {x86_64|aarch64}" >&2; exit 1 ;;
        esac
    else
        echo "Usage: $0 {x86_64|aarch64}" >&2
        echo "  x86_64  — native build on x86_64 host" >&2
        echo "  aarch64 — native build on aarch64 host, OR cross-build on x86_64" >&2
        exit 1
    fi
fi
TRIPLE="${ARCH}-unknown-linux-gnu"

# ─── Resolve project root ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "[build_worker_linux] project root: $PROJECT_ROOT"
echo "[build_worker_linux] arch:         $ARCH  (triple: $TRIPLE)"

# ─── Detect host arch + cross-build setup ───────────────────────────────────
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    aarch64|arm64) HOST_ARCH="aarch64" ;;
    x86_64|amd64)  HOST_ARCH="x86_64" ;;
esac
CROSS_BUILD="false"
if [[ "$ARCH" == "aarch64" && "$HOST_ARCH" != "aarch64" ]]; then
    CROSS_BUILD="true"
    echo "[build_worker_linux] cross-build: x86_64 host → aarch64 target (qemu-user-static)"
    if ! command -v qemu-aarch64-static >/dev/null 2>&1; then
        echo "[build_worker_linux] ERROR: qemu-aarch64-static not found." >&2
        echo "  Install with: sudo apt-get install qemu-user-static binfmt-support" >&2
        echo "  Then:         sudo update-binfmts --enable qemu-aarch64" >&2
        exit 1
    fi
    if [[ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
        echo "[build_worker_linux] WARNING: binfmt_misc qemu-aarch64 not registered." >&2
        sudo update-binfmts --enable qemu-aarch64 || true
    fi
fi

# ─── Locate python-build-standalone ─────────────────────────────────────────
PYBS_DIR="${VOICE_TYPER_PYBS_DIR:-$PROJECT_ROOT/.python-build-standalone}"
if [[ ! -d "$PYBS_DIR" ]]; then
    echo "[build_worker_linux] ERROR: python-build-standalone dir not found at $PYBS_DIR" >&2
    echo "  Set VOICE_TYPER_PYBS_DIR or extract cpython-3.12.x+${TRIPLE} into $PYBS_DIR" >&2
    exit 1
fi

PYBS_PYTHON=""
for candidate in \
    "$PYBS_DIR/python/bin/python3" \
    "$PYBS_DIR"/cpython-3.12.*+"$TRIPLE"/python/bin/python3 \
    "$PYBS_DIR"/cpython-3.12.*+"$TRIPLE"/bin/python3; do
    if [[ -x "$candidate" ]]; then
        PYBS_PYTHON="$candidate"
        break
    fi
done
if [[ -z "$PYBS_PYTHON" ]]; then
    echo "[build_worker_linux] ERROR: no python-build-standalone interpreter found." >&2
    exit 1
fi
echo "[build_worker_linux] pybs interpreter: $PYBS_PYTHON"
"$PYBS_PYTHON" --version

if [[ "$CROSS_BUILD" == "true" ]]; then
    INTERP_ARCH="$(file -b "$PYBS_PYTHON" | grep -oE 'ARM|aarch64|x86-64' || echo unknown)"
    if [[ "$INTERP_ARCH" != *"ARM"* && "$INTERP_ARCH" != *"aarch64"* ]]; then
        echo "[build_worker_linux] ERROR: $PYBS_PYTHON is not an aarch64 binary." >&2
        exit 1
    fi
fi

# ─── Resolve site-packages ──────────────────────────────────────────────────
SITE="$("$PYBS_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
if [[ ! -d "$SITE/onnxruntime" ]]; then
    echo "[build_worker_linux] ERROR: onnxruntime not found in $SITE" >&2
    echo "  Install into the python-build-standalone env first:" >&2
    echo "    $PYBS_PYTHON -m pip install onnxruntime numpy scipy websockets" >&2
    exit 1
fi
if [[ ! -d "$SITE/websockets" ]]; then
    echo "[build_worker_linux] ERROR: websockets not found in $SITE" >&2
    exit 1
fi
CT2_LIB_DIR="$SITE/ctranslate2/lib"
CT2_LIBS_DIR="$SITE/ctranslate2/libs"
# ctranslate2/lib is mandatory on Linux (libctranslate2.so + libiomp5.so /
# libgomp.so live here) — IF ctranslate2 is installed. The worker may or may
# not need ctranslate2 (Phase 2b wires the Whisper fallback in); guard it so
# a CPU-only env without ctranslate2 still produces a working worker.
if [[ -d "$CT2_LIB_DIR" ]]; then
    echo "[build_worker_linux] CT2_LIB_DIR=$CT2_LIB_DIR"
else
    echo "[build_worker_linux] NOTE: $CT2_LIB_DIR not found — ctranslate2 not installed (Whisper fallback unavailable in this build)."
fi

# ─── Verify Nuitka is installed in the pybs env ─────────────────────────────
if ! "$PYBS_PYTHON" -c 'import nuitka' >/dev/null 2>&1; then
    echo "[build_worker_linux] Nuitka not installed in pybs env — installing..."
    "$PYBS_PYTHON" -m pip install --quiet "nuitka==2.8.10" zstandard
fi
# C-CI-6: nuitka must be exactly 2.8.10 (NU-105).
NUITKA_VER="$("$PYBS_PYTHON" -m nuitka --version 2>/dev/null | head -1 || true)"
if [[ "$NUITKA_VER" != *"2.8.10"* ]]; then
    echo "ERROR: nuitka==2.8.10 required (C-CI-6, NU-105). Got: '$NUITKA_VER'" >&2
    echo "  Install with: $PYBS_PYTHON -m pip install 'nuitka==2.8.10'" >&2
    exit 1
fi
echo "[build_worker_linux] nuitka=$NUITKA_VER"

# ─── Verify patchelf is available (Nuitka --standalone requires it on Linux) ─
if ! command -v patchelf >/dev/null 2>&1; then
    echo "[build_worker_linux] ERROR: patchelf not found." >&2
    echo "  Nuitka --standalone on Linux requires patchelf." >&2
    echo "  Install with: sudo apt-get install patchelf" >&2
    exit 1
fi

# ─── Verify voice_typer is importable from the pybs env ─────────────────────
if ! "$PYBS_PYTHON" -c 'import voice_typer' >/dev/null 2>&1; then
    echo "[build_worker_linux] voice_typer not installed in pybs env — using PYTHONPATH=$PROJECT_ROOT"
    export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi

# ─── Determine onefile tempdir spec ─────────────────────────────────────────
# Per-worker extraction dir so stale extracts are cleanable and don't collide
# with the sidecar's or the prewarm's (C-CI-9 — onefile-tempdir-spec stays).
ONEFILE_TEMPDIR="${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/worker-onefile-tmp"

# ─── Build output paths ─────────────────────────────────────────────────────
OUTPUT_DIR="$PROJECT_ROOT/src-tauri/bin"
OUTPUT_BIN="$OUTPUT_DIR/voice-typer-worker-$TRIPLE"
BUILD_LOG="$OUTPUT_DIR/.build-worker-$TRIPLE.log"
mkdir -p "$OUTPUT_DIR"

# ─── Run Nuitka ─────────────────────────────────────────────────────────────
echo "[build_worker_linux] starting Nuitka build (this takes 10-15 min)..."
echo "[build_worker_linux] output: $OUTPUT_BIN"
echo "[build_worker_linux] log:    $BUILD_LOG"

NUITKA_ENV=(
    env "PYTHONPATH=${PYTHONPATH:-}"
    env "CC=${CC:-gcc}"
    env "CXX=${CXX:-g++}"
)

if [[ -z "${NUITKA_JOBS:-}" ]]; then
    NUITKA_JOBS="$(nproc 2>/dev/null || echo 1)"
fi
echo "[build_worker_linux] Nuitka --jobs=$NUITKA_JOBS"

# C-CI-8 / NU-106: --module-parameter=torch-disable-jit=no stays. See
# build_worker_windows.sh header for full rationale.
# C-CI-8 / NU-106: --nofollow-import-to ONLY for the lazily-imported safe
# torch.* submodules. Do NOT add for torch.utils.data.distributed /
# torch.export / torch._functorch / torch.testing / torch.package.
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
    --jobs="$NUITKA_JOBS"
    --enable-plugin=anti-bloat
    --module-parameter=torch-disable-jit=no
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
    --onefile-tempdir-spec="$ONEFILE_TEMPDIR"
    --output-dir="$OUTPUT_DIR"
    --output-filename="voice-typer-worker-$TRIPLE"
    voice_typer/worker/__main__.py
)
# Optional ctranslate2 DLL/so inclusion — only if the install exists.
if [[ -d "$CT2_LIB_DIR" ]]; then
    NUITKA_ARGS+=(--include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR")
fi
if [[ -d "$CT2_LIBS_DIR" ]]; then
    NUITKA_ARGS+=(--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR")
fi

set +e
"${NUITKA_ENV[@]}" "$PYBS_PYTHON" -m nuitka "${NUITKA_ARGS[@]}" 2>&1 | tee "$BUILD_LOG"
NUITKA_RC=${PIPESTATUS[0]}
set -e

if [[ "$NUITKA_RC" -ne 0 ]]; then
    echo "[build_worker_linux] FAILED: Nuitka exited with code $NUITKA_RC" >&2
    echo "[build_worker_linux] full log: $BUILD_LOG" >&2
    exit 1
fi

# ─── Verify output ──────────────────────────────────────────────────────────
if [[ ! -x "$OUTPUT_BIN" ]]; then
    echo "[build_worker_linux] FAILED: output binary not found at $OUTPUT_BIN" >&2
    exit 1
fi
chmod +x "$OUTPUT_BIN"

echo "[build_worker_linux] OK: built $OUTPUT_BIN"
ls -lh "$OUTPUT_BIN"
file "$OUTPUT_BIN"

echo "[build_worker_linux] DONE: $OUTPUT_BIN"
