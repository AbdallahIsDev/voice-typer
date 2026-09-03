#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka sidecar build (Windows x86_64 + aarch64)
# ADR-0020 §4.2 — Nuitka freeze of voice_typer/server/ipc_server.py into
# python-sidecar-<triple>.exe, using python-build-standalone as the base
# interpreter.
#
# Output:
#   src-tauri/bin/python-sidecar-x86_64-pc-windows-msvc.exe
#   src-tauri/bin/python-sidecar-aarch64-pc-windows-msvc.exe
#
# This script is designed to run on a Windows host under Git Bash / MSYS2 /
# WSL. From native PowerShell, use the inline Nuitka command in
# .github/workflows/tauri-windows-build.yml instead (it's the same command,
# just expressed in PowerShell syntax).
#
# Usage (Git Bash on Windows):
#   bash scripts/build/build_sidecar_windows.sh x86_64      # default
#   bash scripts/build/build_sidecar_windows.sh aarch64     # Windows-on-ARM
#   bash scripts/build/build_sidecar_windows.sh --check     # verify toolchain
#
# ADR-0020 §4.2 mandates:
#   - python-build-standalone cpython-3.12.x (matches faster-whisper / CTranslate2 wheel tags)
#   - --standalone --onefile
#   - --include-package=faster_whisper --include-package=ctranslate2
#   - --include-package=voice_typer --include-package=websockets
#   - --include-data-dir=<SITE>/ctranslate2/lib=ctranslate2/lib (dest is
#     RELATIVE to the dist folder — absolute dests are silently ignored)
#   - --include-dll=<SITE>/ctranslate2/lib/ctranslate2.dll
#   - --windows-disable-console
#   - --onefile-tempdir-spec={CACHE_DIR}/voice-typer/onefile-tmp
#
# IMPORTANT: Nuitka does NOT auto-collect Intel MKL / OpenMP runtimes. If
# libiomp5md.dll / mkl_*.dll / libgomp-*.dll are missing from the build env,
# the frozen exe BUILDS but CRASHES on `import ctranslate2`. See ADR-0020
# §4.2 "CPU inference runtimes" for the discovery procedure. This script
# copies the ctranslate2 native DLL dir via --include-data-dir (the whole
# `ctranslate2/lib` folder, or the whole `ctranslate2/` folder on modern
# wheels that ship DLLs without a lib/ subdir), which captures
# libiomp5md.dll + any MKL/OpenMP DLLs present.
# =============================================================================
set -euo pipefail

# ─── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SIDECAR_DIR="$PROJECT_ROOT/src-tauri/bin"

# ─── Args ────────────────────────────────────────────────────────────────────
ARCH="${1:-x86_64}"
if [[ "$ARCH" == "--check" ]]; then
    echo "[build_sidecar_windows] --check: verifying toolchain"
    command -v python >/dev/null || { echo "MISSING: python (python-build-standalone)" >&2; exit 1; }
    python -c "import nuitka" 2>/dev/null || { echo "MISSING: nuitka (pip install nuitka)" >&2; exit 1; }
    python -c "import faster_whisper, ctranslate2" 2>/dev/null || { echo "MISSING: faster_whisper/ctranslate2" >&2; exit 1; }
    echo "[build_sidecar_windows] OK: toolchain ready"
    exit 0
fi

case "$ARCH" in
    x86_64|aarch64) ;;
    *) echo "ERROR: arch must be x86_64 or aarch64 (got: $ARCH)" >&2; exit 1 ;;
esac

TRIPLE="${ARCH}-pc-windows-msvc"
EXE_SUFFIX=".exe"
OUTPUT_NAME="python-sidecar-${TRIPLE}${EXE_SUFFIX}"
OUTPUT_PATH="$SIDECAR_DIR/$OUTPUT_NAME"

echo "[build_sidecar_windows] ARCH=$ARCH TRIPLE=$TRIPLE"
echo "[build_sidecar_windows] OUTPUT=$OUTPUT_PATH"

# ─── Locate the python-build-standalone interpreter ──────────────────────────
# Priority:
#   1. $VOICE_TYPER_PYBS_DIR/python/python.exe (set by CI workflow)
#   2. $PYBS env var (explicit path to python.exe)
#   3. `python` from PATH (dev fallback — must already be a python-build-standalone install)
PYBS_DIR="${VOICE_TYPER_PYBS_DIR:-}"
if [[ -n "$PYBS_DIR" && -f "$PYBS_DIR/python/python.exe" ]]; then
    PY="$PYBS_DIR/python/python.exe"
elif [[ -n "${PYBS:-}" && -f "$PYBS" ]]; then
    PY="$PYBS"
else
    PY="$(command -v python)"
    if [[ -z "$PY" ]]; then
        echo "ERROR: no python interpreter found." >&2
        echo "  Set VOICE_TYPER_PYBS_DIR to a python-build-standalone install dir, or" >&2
        echo "  install nuitka + faster-whisper + ctranslate2 into your dev Python." >&2
        exit 1
    fi
    echo "[build_sidecar_windows] WARNING: using 'python' from PATH ($PY)." >&2
    echo "  For release builds, use a python-build-standalone install (ADR-0020 §4.2)." >&2
fi
echo "[build_sidecar_windows] PY=$PY"

# ─── Resolve the site-packages dir of the build interpreter ──────────────────
SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
echo "[build_sidecar_windows] SITE=$SITE"

# Sanity-check: faster_whisper + ctranslate2 must import in the build env.
"$PY" -c 'import faster_whisper, ctranslate2, websockets; print("ctranslate2", ctranslate2.__version__)' \
    || { echo "ERROR: build env is missing faster_whisper / ctranslate2 / websockets" >&2; exit 1; }

# ─── Locate ctranslate2 native DLLs (for --include-data-dir + --include-dll) ─
# ctranslate2 ships its DLLs either under ctranslate2/lib (older wheels) or
# directly in ctranslate2/ (modern wheels — e.g. the cp312 win_amd64 wheel
# has ctranslate2.dll + cudnn64_9.dll + libiomp5md.dll at the package root).
# Prefer the lib/ layout, fall back to the package dir — mirrors the inline
# command in .github/workflows/tauri-windows-build.yml.
CT2_DIR="$SITE/ctranslate2"
if [[ ! -d "$CT2_DIR" ]]; then
    echo "ERROR: $CT2_DIR not found — ctranslate2 install is incomplete." >&2
    exit 1
fi
CT2_LIB_DIR="$CT2_DIR/lib"
if [[ -d "$CT2_LIB_DIR" ]]; then
    CT2_DATA_DIR_SRC="$CT2_LIB_DIR"
    CT2_DATA_DIR_DEST="ctranslate2/lib"
else
    echo "[build_sidecar_windows] WARNING: $CT2_LIB_DIR not found — falling back to $CT2_DIR (modern wheel layout)"
    CT2_DATA_DIR_SRC="$CT2_DIR"
    CT2_DATA_DIR_DEST="ctranslate2"
fi
CT2_DLL="$CT2_DATA_DIR_SRC/ctranslate2.dll"
if [[ ! -f "$CT2_DLL" ]]; then
    echo "ERROR: $CT2_DLL not found — ctranslate2 install is incomplete." >&2
    exit 1
fi
echo "[build_sidecar_windows] CT2_DATA_DIR=$CT2_DATA_DIR_SRC → $CT2_DATA_DIR_DEST"
echo "[build_sidecar_windows] CT2_DLL=$CT2_DLL"

# ─── Prepare output dir ──────────────────────────────────────────────────────
mkdir -p "$SIDECAR_DIR"

# ─── Run Nuitka (ADR-0020 §4.2) ──────────────────────────────────────────────
# NOTE: --onefile-tempdir-spec uses the Nuitka-documented {CACHE_DIR}
# token, which expands to the user's AppData\Local (previously
# %LOCALAPPDATA%, which Nuitka 2.8.10 does NOT support as a spec variable
# and rejects with FATAL 'Found unknown variable name').
#
# S4-CR-25 / nu-opt-1: psutil imports ALL platform submodules (_pslinux,
# _psosx, _psbsd, _pssunos, _psaix) at the module root — Nuitka compiles
# ALL of them on every OS, wasting hours. These are conditionally imported
# at runtime via sys.platform guards; exclude the non-Windows ones to save
# ~15 min of C compilation. Also removed deprecated --enable-plugin=numpy.
#
# BUILD-2 / XPLAT-3 parity: ctranslate2/libs (plural) is OPTIONAL on Windows
# — most Windows wheels ship everything under ctranslate2/lib (singular), but
# GPU-enabled wheels may also have a libs/ dir with CUDA DLLs. Guard it to
# avoid a hard Nuitka failure if the dir is absent (mirrors the Linux + macOS
# sibling scripts — see ADR-0020 §4.2 + XPLAT-3).
CT2_LIBS_DIR="$SITE/ctranslate2/libs"

# Parallel C compilation: Nuitka invokes gcc/clang per Python module;
# --jobs=N fans those out (the default was sequential). Override with
# NUITKA_JOBS; default = nproc (present in WSL and Git Bash). Each job
# forks a C compiler (~300-500 MB RSS), so cap high counts on low-RAM hosts.
if [[ -z "${NUITKA_JOBS:-}" ]]; then
    NUITKA_JOBS="$(nproc 2>/dev/null || echo 1)"
fi
echo "[build_sidecar_windows] Nuitka --jobs=$NUITKA_JOBS"
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
    --jobs="$NUITKA_JOBS"
    --enable-plugin=anti-bloat
    # NU-106 (VAD): keep torch.jit ENABLED. Nuitka's torch plugin
    # disables JIT by default in standalone mode (sets PYTORCH_JIT=0 /
    # omits torch.jit), which breaks torch.jit.load(silero_vad.jit) with
    # "module 'torch' has no attribute 'jit'" — Silero VAD silently
    # degrades to RMS. Make the choice explicit.
    --module-parameter=torch-disable-jit=no
    --nofollow-import-to=torch._dynamo
    --nofollow-import-to=torch._inductor
    # NU-106 (VAD): torch.export / torch._functorch / torch.testing /
    # torch.package are loaded UNCONDITIONALLY by plain `import torch`
    # (torch 2.13) — do NOT exclude them or `import torch` fails with
    # ModuleNotFoundError and Silero VAD silently degrades to RMS.
    --nofollow-import-to=scipy._lib.cobyqa
    --nofollow-import-to=scipy._lib.array_api_extra.testing
    --nofollow-import-to=sympy
    --nofollow-import-to=mpmath
    --nofollow-import-to=psutil._pslinux
    --nofollow-import-to=psutil._psosx
    --nofollow-import-to=psutil._psbsd
    --nofollow-import-to=psutil._pssunos
    --nofollow-import-to=psutil._psaix
    --include-package=faster_whisper
    --include-package=ctranslate2
    --include-package=voice_typer
    --include-package=websockets
    --include-package-data=voice_typer.server
    --include-data-dir="$CT2_DATA_DIR_SRC=$CT2_DATA_DIR_DEST"
    --include-dll="$CT2_DLL"
    --windows-disable-console
    --onefile-tempdir-spec="{CACHE_DIR}/voice-typer/onefile-tmp"
    --output-filename="$OUTPUT_NAME"
    --output-dir="$SIDECAR_DIR"
    "$PROJECT_ROOT/voice_typer/server/ipc_server.py"
)
if [[ -d "$CT2_LIBS_DIR" ]]; then
    NUITKA_ARGS+=(--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR")
    echo "[build_sidecar_windows] CT2_LIBS_DIR=$CT2_LIBS_DIR (including extra DLLs)"
else
    echo "[build_sidecar_windows] NOTE: ctranslate2/libs not found at $CT2_LIBS_DIR — skipping (optional on CPU-only wheels)"
fi
echo "[build_sidecar_windows] Running Nuitka..."
"$PY" -m nuitka "${NUITKA_ARGS[@]}"

# ─── Verify ──────────────────────────────────────────────────────────────────
if [[ ! -f "$OUTPUT_PATH" ]]; then
    echo "ERROR: $OUTPUT_PATH not built" >&2
    exit 1
fi
SIZE_MB=$(du -m "$OUTPUT_PATH" | cut -f1)
echo "[build_sidecar_windows] OK: $OUTPUT_PATH (${SIZE_MB} MB)"
echo "[build_sidecar_windows] NEXT: sign with signtool (see docs/migration/signing-guide.md §13.1)."
