#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka prewarm build (Windows x86_64 + aarch64)
# ADR-0020 §5 — Prewarm is frozen the SAME Nuitka way as the sidecar, into
# prewarm-<triple>.exe. Prewarm is a BUNDLE RESOURCE (not externalBin):
# launched by the Windows Task Scheduler (LogonTrigger) via
# resolve_prewarm_exe(), NOT by Tauri as a managed child.
#
# Output:
#   src-tauri/resources/prewarm-x86_64-pc-windows-msvc.exe
#   src-tauri/resources/prewarm-aarch64-pc-windows-msvc.exe
#
# Usage (Git Bash on Windows):
#   bash scripts/build/build_prewarm_windows.sh x86_64      # default
#   bash scripts/build/build_prewarm_windows.sh aarch64
#   bash scripts/build/build_prewarm_windows.sh --check
#
# ADR-0020 §5: "Prewarm remains a distinct, intentional boot-time process
# that warms the OS file cache. Kept intentionally separate per ADR-0011."
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
    bash "$SCRIPT_DIR/build_sidecar_windows.sh" --check
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
        echo "[build_prewarm_windows.sh] OK: existing prewarm binary verified."
    fi
    exit 0
fi

# WR-18 FINDING F-2: previously ARCH defaulted to x86_64 unconditionally,
# so on Windows-on-ARM hosts (aarch64) the script produced an x86_64
# binary that wouldn't run natively. Now auto-detect via uname -m (matches
# the Linux/macOS sibling pattern at build_prewarm_linux.sh:39-46), still
# allowing $1 to override. MSYS2/Git Bash report x86_64 / aarch64 directly
# (no normalization needed).
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
OUTPUT_NAME="prewarm-${TRIPLE}${EXE_SUFFIX}"
OUTPUT_PATH="$RESOURCES_DIR/$OUTPUT_NAME"

echo "[build_prewarm_windows] ARCH=$ARCH TRIPLE=$TRIPLE"
echo "[build_prewarm_windows] OUTPUT=$OUTPUT_PATH"

# ─── Locate the python-build-standalone interpreter (same as sidecar) ────────
PYBS_DIR="${VOICE_TYPER_PYBS_DIR:-}"
if [[ -n "$PYBS_DIR" && -f "$PYBS_DIR/python/python.exe" ]]; then
    PY="$PYBS_DIR/python/python.exe"
elif [[ -n "${PYBS:-}" && -f "$PYBS" ]]; then
    PY="$PYBS"
else
    PY="$(command -v python)"
    if [[ -z "$PY" ]]; then
        echo "ERROR: no python interpreter found (set VOICE_TYPER_PYBS_DIR)." >&2
        exit 1
    fi
fi
echo "[build_prewarm_windows] PY=$PY"

SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"

# Prewarm imports the heavy ML stack the SAME way as the sidecar (it warms
# the OS file cache for the sidecar's torch + transformers + faster-whisper
# imports), so it needs the same packages included.
"$PY" -c 'import faster_whisper, ctranslate2, voice_typer.server.prewarm' \
    || { echo "ERROR: build env missing deps" >&2; exit 1; }

CT2_LIB_DIR="$SITE/ctranslate2/lib"
CT2_DLL="$CT2_LIB_DIR/ctranslate2.dll"
# XPLAT-9: validate CT2_LIB_DIR / CT2_DLL existence (matches
# build_sidecar_windows.sh lines 101-113). Without these guards, Nuitka builds
# a prewarm .exe that crashes on `import ctranslate2` because the OpenMP /
# CTranslate2 native DLLs are missing from the bundle.
if [[ ! -d "$CT2_LIB_DIR" ]]; then
    echo "ERROR: $CT2_LIB_DIR not found — ctranslate2 install is incomplete." >&2
    exit 1
fi
if [[ ! -f "$CT2_DLL" ]]; then
    echo "ERROR: $CT2_DLL not found — ctranslate2 install is incomplete." >&2
    exit 1
fi
echo "[build_prewarm_windows] CT2_LIB_DIR=$CT2_LIB_DIR"
echo "[build_prewarm_windows] CT2_DLL=$CT2_DLL"

# ─── Prepare output dir ──────────────────────────────────────────────────────
mkdir -p "$RESOURCES_DIR"

# ─── Run Nuitka (ADR-0020 §5) ────────────────────────────────────────────────
# NOTE: prewarm is a SEPARATE process — it has its own --onefile-tempdir-spec
# so its self-extraction doesn't collide with the sidecar's. Different temp
# dir, different binary, different process.
echo "[build_prewarm_windows] Running Nuitka..."
"$PY" -m nuitka \
    --standalone --onefile \
    --assume-yes-for-downloads \
    --enable-plugin=numpy \
    --enable-plugin=anti-bloat \
    --nofollow-import-to=torch._dynamo \
    --nofollow-import-to=torch._inductor \
    --nofollow-import-to=torch.export \
    --nofollow-import-to=torch._functorch \
    --nofollow-import-to=scipy._lib.cobyqa \
    --nofollow-import-to=scipy._lib.array_api_extra.testing \
    --include-package=faster_whisper \
    --include-package=ctranslate2 \
    --include-package=voice_typer \
    --include-package=websockets \
    --include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR" \
    --include-dll="$CT2_DLL" \
    --windows-disable-console \
    --onefile-tempdir-spec="%LOCALAPPDATA%\\voice-typer\\prewarm-onefile-tmp" \
    --output-filename="$OUTPUT_NAME" \
    --output-dir="$RESOURCES_DIR" \
    "$PROJECT_ROOT/voice_typer/server/prewarm/__main__.py"

if [[ ! -f "$OUTPUT_PATH" ]]; then
    echo "ERROR: $OUTPUT_PATH not built" >&2
    exit 1
fi
SIZE_MB=$(du -m "$OUTPUT_PATH" | cut -f1)
echo "[build_prewarm_windows] OK: $OUTPUT_PATH (${SIZE_MB} MB)"
echo "[build_prewarm_windows] NEXT: sign with signtool (signing-guide.md §13.1)."
