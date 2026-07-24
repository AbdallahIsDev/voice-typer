#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Nuitka Linux sidecar build (Phase 0-L, ADR-0020 §4.4)
#
# Builds the frozen Python sidecar (`python-sidecar-<triple>`) for Linux,
# for both x86_64-unknown-linux-gnu and aarch64-unknown-linux-gnu.
#
# The resulting binary is dropped at:
#   src-tauri/bin/python-sidecar-<triple>
# which is where Tauri's `externalBin` mechanism expects it (Tauri v2
# appends the Rust target triple to the base name `bin/python-sidecar`
# at runtime; see ADR-0020 §4.1 + §7).
#
# Usage:
#   bash scripts/build/build_sidecar_linux.sh x86_64    # native x86_64 build
#   bash scripts/build/build_sidecar_linux.sh aarch64   # native aarch64 build
#                                                       # (requires aarch64 host)
#                                                       # OR cross-build on x86_64
#                                                       # via qemu-user-static
#   bash scripts/build/build_sidecar_linux.sh --check   # verify toolchain (BUILD-1)
#
# Required env (override defaults with these):
#   VOICE_TYPER_PYBS_DIR  Directory containing the extracted python-build-standalone
#                         cpython-3.12.x+<triple> tree. The script auto-discovers
#                         the patch version. Defaults to
#                         $REPO/.python-build-standalone if unset.
#
# ADR-0020 §4.4 mandates:
#   - python-build-standalone cpython-3.12.x built against glibc 2.35 (Ubuntu 22.04)
#     so the resulting binary runs on Ubuntu 22.04+ / Debian 12+ / Fedora 36+.
#   - --include-package=faster_whisper + ctranslate2 + websockets + voice_typer.
#   - --include-data-dir for $SITE/ctranslate2/{lib,libs} (libiomp5.so, libgomp.so).
#   - --onefile-tempdir-spec pinned to $XDG_CACHE_HOME/voice-typer/onefile-tmp
#     so the onefile extraction is deterministic + cleanable.
#   - --enable-plugin=numpy for hidden numpy imports used by faster-whisper.
#
# Cross-arch (aarch64 on x86_64 host):
#   Nuitka does NOT cross-compile. To build aarch64 on an x86_64 host we
#   execute the aarch64 python-build-standalone interpreter under
#   qemu-user-static (binfmt_misc). The script refuses to cross-build
#   without `qemu-aarch64-static` available.
# =============================================================================
set -euo pipefail

# ─── Parse args ─────────────────────────────────────────────────────────────
ARCH="${1:-}"
if [[ "$ARCH" == "--check" ]]; then
    # BUILD-1: --check mode (mirrors build_sidecar_windows.sh lines 48-55).
    # Pre-flight toolchain verification without invoking a full Nuitka build.
    # Uses $PYBS_PYTHON (the python-build-standalone interpreter) instead of
    # the Windows sibling's `python` from PATH, since the Linux script's
    # toolchain is pybs-scoped (see ADR-0020 §4.4).
    echo "[build_sidecar_linux] --check: verifying toolchain"
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
        || { echo "MISSING: nuitka (pip install nuitka)" >&2; exit 1; }
    "$PYBS_PYTHON" -c "import faster_whisper, ctranslate2" 2>/dev/null \
        || { echo "MISSING: faster_whisper/ctranslate2" >&2; exit 1; }
    echo "[build_sidecar_linux] OK: toolchain ready"
    exit 0
fi
if [[ "$ARCH" != "x86_64" && "$ARCH" != "aarch64" ]]; then
    echo "Usage: $0 {x86_64|aarch64}" >&2
    echo "  x86_64  — native build on x86_64 host" >&2
    echo "  aarch64 — native build on aarch64 host, OR cross-build on x86_64" >&2
    exit 1
fi
TRIPLE="${ARCH}-unknown-linux-gnu"

# ─── Resolve project root ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "[build_sidecar_linux] project root: $PROJECT_ROOT"
echo "[build_sidecar_linux] arch:         $ARCH  (triple: $TRIPLE)"

# ─── Detect host arch + cross-build setup ───────────────────────────────────
HOST_ARCH="$(uname -m)"
# Normalize: aarch64 / arm64 → aarch64; x86_64 / amd64 → x86_64.
case "$HOST_ARCH" in
    aarch64|arm64) HOST_ARCH="aarch64" ;;
    x86_64|amd64)  HOST_ARCH="x86_64" ;;
esac
CROSS_BUILD="false"
if [[ "$ARCH" == "aarch64" && "$HOST_ARCH" != "aarch64" ]]; then
    CROSS_BUILD="true"
    echo "[build_sidecar_linux] cross-build: x86_64 host → aarch64 target (qemu-user-static)"
    if ! command -v qemu-aarch64-static >/dev/null 2>&1; then
        echo "[build_sidecar_linux] ERROR: qemu-aarch64-static not found." >&2
        echo "  Install with: sudo apt-get install qemu-user-static binfmt-support" >&2
        echo "  Then:         sudo update-binfmts --enable qemu-aarch64" >&2
        exit 1
    fi
    # Verify binfmt_misc is registered so direct execution of aarch64 ELF works.
    if [[ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
        echo "[build_sidecar_linux] WARNING: binfmt_misc qemu-aarch64 not registered." >&2
        echo "  Attempting: sudo update-binfmts --enable qemu-aarch64" >&2
        sudo update-binfmts --enable qemu-aarch64 || true
    fi
fi

# ─── Locate python-build-standalone ─────────────────────────────────────────
PYBS_DIR="${VOICE_TYPER_PYBS_DIR:-$PROJECT_ROOT/.python-build-standalone}"
if [[ ! -d "$PYBS_DIR" ]]; then
    echo "[build_sidecar_linux] ERROR: python-build-standalone dir not found at $PYBS_DIR" >&2
    echo "  Set VOICE_TYPER_PYBS_DIR or extract cpython-3.12.x+${TRIPLE} into $PYBS_DIR" >&2
    echo "  Download from: https://github.com/indygreg/python-build-standalone/releases" >&2
    exit 1
fi

# Auto-discover the python-build-standalone cpython-3.12.x+<triple> tree.
# The archive extracts to e.g. $PYBS_DIR/python/bin/python3 (install_only
# layout). Match either:
#   $PYBS_DIR/python/             (install_only tarball layout)
#   $PYBS_DIR/cpython-3.12.*+<triple>/python/  (verbose layout)
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
    echo "[build_sidecar_linux] ERROR: no python-build-standalone interpreter found." >&2
    echo "  Looked for: $PYBS_DIR/python/bin/python3" >&2
    echo "  Looked for: $PYBS_DIR/cpython-3.12.*+$TRIPLE/python/bin/python3" >&2
    echo "  Looked for: $PYBS_DIR/cpython-3.12.*+$TRIPLE/bin/python3" >&2
    exit 1
fi
echo "[build_sidecar_linux] pybs interpreter: $PYBS_PYTHON"
"$PYBS_PYTHON" --version

# If cross-building, verify the interpreter is actually aarch64 ELF.
if [[ "$CROSS_BUILD" == "true" ]]; then
    INTERP_ARCH="$(file -b "$PYBS_PYTHON" | grep -oE 'ARM|aarch64|x86-64' || echo unknown)"
    if [[ "$INTERP_ARCH" != *"ARM"* && "$INTERP_ARCH" != *"aarch64"* ]]; then
        echo "[build_sidecar_linux] ERROR: $PYBS_PYTHON is not an aarch64 binary." >&2
        echo "  file: $(file "$PYBS_PYTHON")" >&2
        exit 1
    fi
fi

# ─── Locate python-build-standalone site-packages ───────────────────────────
SITE="$("$PYBS_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
if [[ ! -d "$SITE/faster_whisper" ]]; then
    echo "[build_sidecar_linux] ERROR: faster_whisper not found in $SITE" >&2
    echo "  Install into the python-build-standalone env first:" >&2
    echo "    $PYBS_PYTHON -m pip install faster-whisper ctranslate2 websockets numpy" >&2
    exit 1
fi
if [[ ! -d "$SITE/ctranslate2" ]]; then
    echo "[build_sidecar_linux] ERROR: ctranslate2 not found in $SITE" >&2
    exit 1
fi
if [[ ! -d "$SITE/websockets" ]]; then
    echo "[build_sidecar_linux] ERROR: websockets not found in $SITE" >&2
    exit 1
fi

# ─── Verify Nuitka is installed in the pybs env ─────────────────────────────
if ! "$PYBS_PYTHON" -c 'import nuitka' >/dev/null 2>&1; then
    echo "[build_sidecar_linux] Nuitka not installed in pybs env — installing..."
    "$PYBS_PYTHON" -m pip install --quiet nuitka zstandard
fi
"$PYBS_PYTHON" -m nuitka --version | head -1

# ─── Verify patchelf is available (Nuitka --standalone requires it on Linux) ─
if ! command -v patchelf >/dev/null 2>&1; then
    echo "[build_sidecar_linux] ERROR: patchelf not found." >&2
    echo "  Nuitka --standalone on Linux requires patchelf." >&2
    echo "  Install with: sudo apt-get install patchelf" >&2
    exit 1
fi

# ─── Verify voice_typer is importable from the pybs env ─────────────────────
# The voice_typer package must be installed (or on PYTHONPATH) so Nuitka can
# --include-package=voice_typer. We add the project root to PYTHONPATH if
# voice_typer isn't installed as a wheel.
if ! "$PYBS_PYTHON" -c 'import voice_typer' >/dev/null 2>&1; then
    echo "[build_sidecar_linux] voice_typer not installed in pybs env — using PYTHONPATH=$PROJECT_ROOT"
    export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi

# ─── Determine onefile tempdir spec ─────────────────────────────────────────
# ADR-0020 §4.4: pin the onefile extraction dir so stale extracts are cleanable.
ONEFILE_TEMPDIR="${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp"

# ─── Build output paths ─────────────────────────────────────────────────────
OUTPUT_DIR="$PROJECT_ROOT/src-tauri/bin"
OUTPUT_BIN="$OUTPUT_DIR/python-sidecar-$TRIPLE"
BUILD_LOG="$OUTPUT_DIR/.build-sidecar-$TRIPLE.log"
mkdir -p "$OUTPUT_DIR"

# ─── Run Nuitka ─────────────────────────────────────────────────────────────
echo "[build_sidecar_linux] starting Nuitka build (this takes 10-15 min)..."
echo "[build_sidecar_linux] output: $OUTPUT_BIN"
echo "[build_sidecar_linux] log:    $BUILD_LOG"

# Set CC/CXX for the cross-build case (qemu binfmt_misc handles interpreter
# execution; gcc is still the host's). For native builds, the pybs env's
# own gcc is used via Nuitka's default discovery.
NUITKA_ENV=(
    env "PYTHONPATH=${PYTHONPATH:-}"
    env "CC=${CC:-gcc}"
    env "CXX=${CXX:-g++}"
)

# ADR-0020 §4.4 Nuitka command (Linux x86_64 + aarch64):
#   --standalone --onefile
#   --assume-yes-for-downloads
#   --enable-plugin=numpy
#   --include-package=faster_whisper --include-package=ctranslate2
#   --include-package=voice_typer   --include-package=websockets
#   --include-data-dir=$SITE/ctranslate2/lib=$SITE/ctranslate2/lib   (always present)
#   --include-data-dir=$SITE/ctranslate2/libs=$SITE/ctranslate2/libs (optional — guarded)
#   --onefile-tempdir-spec=$XDG_CACHE_HOME/voice-typer/onefile-tmp
#   --output-filename=python-sidecar-<triple>
#   voice_typer/server/ipc_server.py
#
# XPLAT-3: ctranslate2/libs is OPTIONAL — CPU-only wheels (e.g. aarch64)
# ship libctranslate2.so + libiomp5.so under ctranslate2/lib/ only, with no
# ctranslate2/libs/ directory. Nuitka's --include-data-dir fails hard if the
# source path is missing, so guard it (mirrors build_sidecar_macos.sh and
# build_prewarm_linux.sh — see ADR-0020 §4.4 + XPLAT-3).
CT2_LIBS_DIR="$SITE/ctranslate2/libs"
NUITKA_ARGS=(
    --standalone --onefile
    --assume-yes-for-downloads
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
    --include-package=numpy
    --include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib"
    --onefile-tempdir-spec="$ONEFILE_TEMPDIR"
    --output-dir="$OUTPUT_DIR"
    --output-filename="python-sidecar-$TRIPLE"
    voice_typer/server/ipc_server.py
)
if [[ -d "$CT2_LIBS_DIR" ]]; then
    NUITKA_ARGS+=(--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR")
else
    echo "[build_sidecar_linux] NOTE: ctranslate2/libs not found at $CT2_LIBS_DIR — skipping (optional on CPU-only wheels)"
fi
set +e
"${NUITKA_ENV[@]}" "$PYBS_PYTHON" -m nuitka "${NUITKA_ARGS[@]}" 2>&1 | tee "$BUILD_LOG"
NUITKA_RC=${PIPESTATUS[0]}
set -e

if [[ "$NUITKA_RC" -ne 0 ]]; then
    echo "[build_sidecar_linux] FAILED: Nuitka exited with code $NUITKA_RC" >&2
    echo "[build_sidecar_linux] full log: $BUILD_LOG" >&2
    exit 1
fi

# ─── Verify output ──────────────────────────────────────────────────────────
if [[ ! -x "$OUTPUT_BIN" ]]; then
    echo "[build_sidecar_linux] FAILED: output binary not found at $OUTPUT_BIN" >&2
    exit 1
fi

echo "[build_sidecar_linux] OK: built $OUTPUT_BIN"
ls -lh "$OUTPUT_BIN"
file "$OUTPUT_BIN"

# ─── Verify glibc baseline (≤ 2.35 for Ubuntu 22.04 compat) ─────────────────
# Use objdump -p for cross binaries (ldd won't run aarch64 ELF on x86_64
# without qemu running in the right mode); use ldd for native binaries.
verify_glibc() {
    local bin="$1"
    local max_glibc=""
    if [[ "$CROSS_BUILD" == "true" ]]; then
        max_glibc=$(objdump -p "$bin" 2>/dev/null \
            | grep -oE 'GLIBC_[0-9]+\.[0-9]+' \
            | sort -V \
            | tail -1 || true)
    else
        max_glibc=$(ldd "$bin" 2>/dev/null \
            | grep -oE 'GLIBC_[0-9]+\.[0-9]+' \
            | sort -V \
            | tail -1 || true)
    fi
    if [[ -z "$max_glibc" ]]; then
        echo "[build_sidecar_linux] WARNING: no GLIBC version markers found in $bin"
        return 0
    fi
    echo "[build_sidecar_linux] max glibc requirement: $max_glibc"
    local num="${max_glibc/GLIBC_/}"
    local maj="${num%.*}"
    local min="${num#*.}"
    if [[ "$maj" -gt 2 || ( "$maj" -eq 2 && "$min" -gt 35 ) ]]; then
        echo "[build_sidecar_linux] FAILED: $bin requires $max_glibc but baseline is GLIBC_2.35" >&2
        echo "  Rebuild on ubuntu-22.04 or use a python-build-standalone release linked against glibc 2.35." >&2
        exit 1
    fi
    echo "[build_sidecar_linux] OK: glibc baseline (≤ 2.35) verified"
}
verify_glibc "$OUTPUT_BIN"

# ─── Quick smoke (help text only; no display server required) ───────────────
# ADR-0020 §4.5 Phase 0 gate: run the sidecar binary with a one-shot command
# that loads faster_whisper to prove CTranslate2 + DLLs + model load all work
# inside Nuitka. That requires a tiny model file — skip here and defer to the
# runbook's Step 7. Just verify --help works (proves the Python interpreter +
# faster_whisper + ctranslate2 + websockets all loaded).
echo "[build_sidecar_linux] smoke: $OUTPUT_BIN --help"
if [[ "$CROSS_BUILD" == "true" ]]; then
    # Use qemu explicitly for the help check (binfmt_misc may not be active).
    qemu-aarch64-static "$OUTPUT_BIN" --help 2>&1 | head -20 \
        || echo "[build_sidecar_linux] (cross-build --help skipped — verify on aarch64 host)"
else
    "$OUTPUT_BIN" --help 2>&1 | head -20 \
        || echo "[build_sidecar_linux] (—help returned non-zero; check $BUILD_LOG)"
fi

echo "[build_sidecar_linux] DONE: $OUTPUT_BIN"
