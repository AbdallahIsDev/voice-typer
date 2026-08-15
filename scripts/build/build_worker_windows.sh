#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka worker build (Windows x86_64 + aarch64)
#
# Plan-runtime-pack-split §4.4 / §11.5 — builds the runtime-pack worker exe
# (voice-typer-worker-<triple>.exe), the heavy-ML process that owns
# onnxruntime (VAD + Parakeet) + ctranslate2/faster_whisper (Whisper
# fallback) + numpy/scipy/av/pyrnnoise + the bundled silero_vad.onnx.
# The slim-core sidecar connects to this worker via a localhost WebSocket
# (master plan §7) and offloads all heavy inference to it.
#
# Output:
#   src-tauri/bin/voice-typer-worker-x86_64-pc-windows-msvc.exe
#   src-tauri/bin/voice-typer-worker-aarch64-pc-windows-msvc.exe  (future)
#
# This script mirrors build_prewarm_windows.sh + build_sidecar_windows.sh
# (same Nuitka toolchain, same python-build-standalone interpreter, same
# VOICE_TYPER_PYBS_DIR env var contract). The worker is a SEPARATE process
# — it has its own --onefile-tempdir-spec so its self-extraction doesn't
# collide with the sidecar's or the prewarm's.
#
# Usage (Git Bash on Windows):
#   bash scripts/build/build_worker_windows.sh x86_64      # default
#   bash scripts/build/build_worker_windows.sh aarch64
#   bash scripts/build/build_worker_windows.sh --check     # verify toolchain
#
# CI invocation (.github/workflows/tauri-windows-build.yml):
#   bash scripts/build/build_worker_windows.sh
# with VOICE_TYPER_PYBS_DIR=$github_workspace/.python-build-standalone in env.
# The CI step is gated on hashFiles('scripts/build/build_worker_windows.sh')
# so it stays inert until this script lands (C-CI-2: do not edit the workflow).
#
# CI gate contract (binding — C-CI-6/8/9/13):
#   - nuitka==2.8.10 (C-CI-6, NU-105) — Nuitka <2.8.0 crashes on numpy 2.5
#     PEP 695 type-generic aliases. We verify the installed version below.
#   - --module-parameter=torch-disable-jit=no (C-CI-8, NU-106) — kept even
#     though the worker does not import torch directly; the worker bundles
#     torch as bytecode (via transitive voice_typer imports) for the prewarm
#     cache_probe's find_spec() probe, and the torch plugin's default
#     standalone-mode JIT disable breaks torch.jit.load on bundles that DO
#     import torch.
#   - --nofollow-import-to ONLY for the lazily-imported safe modules listed
#     in C-CI-8 (torch._dynamo, torch._inductor, torch.onnx,
#     torch.utils.benchmark, transformers, scipy.*, psutil._ps*, sympy,
#     mpmath, pytest, PIL.* non-UI). Do NOT add --nofollow-import-to for
#     torch.utils.data.distributed / torch.export / torch._functorch /
#     torch.testing / torch.package — they are imported unconditionally by
#     `import torch` (torch 2.13), and excluding them makes `import torch`
#     raise ModuleNotFoundError inside the frozen exe (NU-106).
#   - --include-package-data=voice_typer.server (C-CI-9, IPD-1) — the
#     frozen worker reads package data at import time (hotkey_reserved.json,
#     corrections.json, model_hashes.json, native/binaries.json,
#     silero_vad.onnx). Without this flag the onefile payload is missing
#     them and the exe crashes on launch with FileNotFoundError — even
#     though it BUILDS fine.
#   - --windows-console-mode=disable (C-CI-9) — newer Nuitka form of the
#     deprecated --windows-disable-console flag. Makes the worker a
#     GUI-subsystem PE so it doesn't pop a console window at startup;
#     the CI smoke-test step depends on this behavior (C-CI-14).
#   - --onefile-tempdir-spec (C-CI-9) — pinned per-worker extraction dir
#     so stale extracts are cleanable and don't collide with the sidecar's.
#   - Output binary name: voice-typer-worker-<triple>.exe (C-CI-13) —
#     do NOT rename; tests/tauri/mig18 test_externalbin_wiring.py greps
#     the default externalBin binary names.
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKER_DIR="$PROJECT_ROOT/src-tauri/bin"

# ─── Args ────────────────────────────────────────────────────────────────────
ARCH="${1:-}"
if [[ "$ARCH" == "--check" ]]; then
    # Pre-flight toolchain verification without invoking a full Nuitka build.
    # Mirrors build_sidecar_windows.sh --check (BUILD-1).
    echo "[build_worker_windows] --check: verifying toolchain"
    _ck_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    _ck_project_root="$(cd "$_ck_script_dir/../.." && pwd)"
    _ck_pybs_dir="${VOICE_TYPER_PYBS_DIR:-$_ck_project_root/.python-build-standalone}"
    PYBS_PYTHON=""
    if [[ -n "$_ck_pybs_dir" && -f "$_ck_pybs_dir/python/python.exe" ]]; then
        PYBS_PYTHON="$_ck_pybs_dir/python/python.exe"
    elif [[ -n "${PYBS:-}" && -f "$PYBS" ]]; then
        PYBS_PYTHON="$PYBS"
    else
        PYBS_PYTHON="$(command -v python || true)"
    fi
    if [[ -z "$PYBS_PYTHON" ]]; then
        echo "MISSING: python-build-standalone interpreter (set VOICE_TYPER_PYBS_DIR)" >&2
        exit 1
    fi
    "$PYBS_PYTHON" -c "import nuitka" 2>/dev/null \
        || { echo "MISSING: nuitka (pip install nuitka==2.8.10)" >&2; exit 1; }
    # C-CI-6: nuitka must be exactly 2.8.10. <2.8 crashes on numpy 2.5 PEP 695
    # type aliases (NU-105).
    _ck_nuitka_ver="$("$PYBS_PYTHON" -m nuitka --version 2>/dev/null | head -1 || true)"
    if [[ "$_ck_nuitka_ver" != *"2.8.10"* ]]; then
        echo "MISSING: nuitka==2.8.10 (got: $_ck_nuitka_ver)" >&2
        exit 1
    fi
    "$PYBS_PYTHON" -c "import onnxruntime, numpy, scipy, websockets" 2>/dev/null \
        || { echo "MISSING: onnxruntime/numpy/scipy/websockets (worker deps)" >&2; exit 1; }
    "$PYBS_PYTHON" -c 'import voice_typer.worker' 2>/dev/null \
        || { echo "MISSING: voice_typer.worker module (run 'pip install -e .' in the repo root)" >&2; exit 1; }
    echo "[build_worker_windows] OK: toolchain ready (nuitka==2.8.10)"
    exit 0
fi

# Auto-detect arch (mirrors build_prewarm_windows.sh — WR-18 fix): default
# to uname -m so Windows-on-ARM hosts get an aarch64 binary by default.
# $1 overrides (explicit x86_64/aarch64).
if [[ -z "$ARCH" ]]; then
    case "$(uname -m)" in
        x86_64)   ARCH="x86_64" ;;
        aarch64)  ARCH="aarch64" ;;
        *) echo "ERROR: unsupported host arch: $(uname -m)" >&2; exit 1 ;;
    esac
fi

case "$ARCH" in
    x86_64|aarch64) ;;
    *) echo "ERROR: arch must be x86_64 or aarch64 (got: $ARCH)" >&2; exit 1 ;;
esac

TRIPLE="${ARCH}-pc-windows-msvc"
EXE_SUFFIX=".exe"
OUTPUT_NAME="voice-typer-worker-${TRIPLE}${EXE_SUFFIX}"
OUTPUT_PATH="$WORKER_DIR/$OUTPUT_NAME"

echo "[build_worker_windows] ARCH=$ARCH TRIPLE=$TRIPLE"
echo "[build_worker_windows] OUTPUT=$OUTPUT_PATH"

# ─── Locate the python-build-standalone interpreter (same as sidecar) ────────
# Priority:
#   1. $VOICE_TYPER_PYBS_DIR/python/python.exe (set by CI workflow)
#   2. $PYBS env var (explicit path to python.exe)
#   3. `python` from PATH (dev fallback — must already be a pybs install)
PYBS_DIR="${VOICE_TYPER_PYBS_DIR:-}"
if [[ -n "$PYBS_DIR" && -f "$PYBS_DIR/python/python.exe" ]]; then
    PY="$PYBS_DIR/python/python.exe"
elif [[ -n "${PYBS:-}" && -f "$PYBS" ]]; then
    PY="$PYBS"
else
    PY="$(command -v python || true)"
    if [[ -z "$PY" ]]; then
        echo "ERROR: no python interpreter found (set VOICE_TYPER_PYBS_DIR)." >&2
        exit 1
    fi
    echo "[build_worker_windows] WARNING: using 'python' from PATH ($PY)." >&2
    echo "  For release builds, use a python-build-standalone install (ADR-0020 §4.2)." >&2
fi
echo "[build_worker_windows] PY=$PY"

# C-CI-6: nuitka must be exactly 2.8.10. NU-105: <2.8.0 crashes compiling
# numpy 2.5 PEP 695 type-generic aliases. Hard-fail before the build so we
# don't waste 90 min of C compilation on a doomed run.
NUITKA_VER="$("$PY" -m nuitka --version 2>/dev/null | head -1 || true)"
if [[ "$NUITKA_VER" != *"2.8.10"* ]]; then
    echo "ERROR: nuitka==2.8.10 required (C-CI-6, NU-105). Got: '$NUITKA_VER'" >&2
    echo "  Install with: $PY -m pip install 'nuitka==2.8.10'" >&2
    exit 1
fi
echo "[build_worker_windows] nuitka=$NUITKA_VER"

# Sanity-check: the worker module imports cleanly + onnxruntime is present
# (the worker's VAD/Parakeet engines use onnxruntime, NOT torch).
"$PY" -c 'import voice_typer.worker; import onnxruntime, numpy, scipy, websockets; print("worker imports ok")' \
    || { echo "ERROR: build env missing voice_typer.worker / onnxruntime / numpy / scipy / websockets" >&2; exit 1; }

# ─── Prepare output dir ──────────────────────────────────────────────────────
mkdir -p "$WORKER_DIR"

# ─── Run Nuitka (plan-runtime-pack-split §4.4 / §11.5) ───────────────────────
# NOTE: the worker is a SEPARATE process — it has its own --onefile-tempdir-spec
# so its self-extraction doesn't collide with the sidecar's or the prewarm's.
# Different temp dir, different binary, different process.
#
# C-CI-8 / NU-106: --module-parameter=torch-disable-jit=no stays. The worker
# does not import torch directly (Phase 1c swept vad.py + parakeet_engine.py
# to ONNX), but torch is still bundled as bytecode (transitive voice_typer
# imports pull it in; prewarm cache_probe uses find_spec on it). Nuitka's
# torch plugin disables torch.jit by default in standalone mode; if any
# transitive import path lands in torch.jit, the bundle crashes with
# "module 'torch' has no attribute 'jit'" — keep JIT enabled.
#
# C-CI-8 / NU-106: --nofollow-import-to ONLY for the lazily-imported safe
# torch.* submodules (torch._dynamo, torch._inductor, torch.onnx,
# torch.utils.benchmark). Do NOT add --nofollow-import-to for
# torch.utils.data.distributed / torch.export / torch._functorch /
# torch.testing / torch.package — they are imported UNCONDITIONALLY by
# plain `import torch` (torch 2.13), and excluding them makes `import torch`
# raise ModuleNotFoundError inside the frozen exe.
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
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
    --windows-console-mode=disable
    --onefile-tempdir-spec="%LOCALAPPDATA%\\voice-typer\\worker-onefile-tmp"
    --output-filename="$OUTPUT_NAME"
    --output-dir="$WORKER_DIR"
    "$PROJECT_ROOT/voice_typer/worker/__main__.py"
)

echo "[build_worker_windows] Running Nuitka..."
"$PY" -m nuitka "${NUITKA_ARGS[@]}"

if [[ ! -f "$OUTPUT_PATH" ]]; then
    echo "ERROR: $OUTPUT_PATH not built" >&2
    exit 1
fi
SIZE_MB=$(du -m "$OUTPUT_PATH" | cut -f1)
echo "[build_worker_windows] OK: $OUTPUT_PATH (${SIZE_MB} MB)"
echo "[build_worker_windows] NEXT: sign with signtool (docs/migration/signing-guide.md §13.1)."
