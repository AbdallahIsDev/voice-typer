#!/usr/bin/env bash
# =============================================================================
# Voice Typer — Torch-free bundle assertion (plan-runtime-pack-split §11.3)
#
# Hard-fails the CI build if the freshly-built Nuitka onefile binary contains
# any torch import sites or the silero_vad.jit JIT model. This is the
# Phase-1c gate: torch must be removed from vad.py + parakeet_engine.py
# (Qwen stays, per Phase 1d deferral) before the sidecar/worker bundle is
# allowed to ship. Without this gate, a regression that re-introduces
# `import torch` into vad.py would silently bloat the bundle by ~150 MB
# and re-expose the NU-106 VAD-degradation failure mode.
#
# Implementation (per plan §11.3): the Nuitka onefile is a single ~100 MB
# PE/ELF/Mach-O binary with the payload compressed inline. `strings(1)`
# extracts printable byte sequences from the binary — enough to catch the
# module-path strings Nuitka embeds (`torch/__init__.py`, `torch/utils/...`)
# and the JIT model filename (`silero_vad.jit`). The check is portable:
# `strings` ships with binutils on Linux/macOS and is in Git Bash / MSYS2
# on Windows. As a defensive fallback for environments without `strings`
# (rare, but possible on minimal CI images), we use a Python one-liner
# that reads the binary in 1 MB chunks and scans for the same patterns.
#
# Usage:
#   bash scripts/build/check_bundle_torch_free.sh <path-to-binary>
#
# Exit codes:
#   0  — bundle is torch-free (no `torch.` import sites, no `silero_vad.jit`).
#   1  — bundle contains a forbidden torch import site or the JIT model.
#   2  — invocation error (missing binary, missing arg, internal failure).
#
# Gate wiring (.github/workflows/tauri-{windows,linux,macos}-build.yml):
#   - name: Verify sidecar is torch-free (Phase 1c gate, plan §11.3)
#     if: ${{ hashFiles('scripts/build/check_bundle_torch_free.sh') != '' }}
#     run: |
#       bash scripts/build/check_bundle_torch_free.sh "$BIN"
# The `hashFiles` guard keeps the step INERT until this script lands; once
# it exists the step activates and hard-fails on any torch sighting. Do NOT
# weaken the patterns below to make a torch-bearing bundle pass — the
# gate's value IS its strictness (C-CI-2: do not edit the workflow file).
# =============================================================================
set -euo pipefail

# ─── Args ────────────────────────────────────────────────────────────────────
if [[ "$#" -ne 1 ]]; then
    echo "Usage: $0 <path-to-binary>" >&2
    echo "  Scans the Nuitka onefile binary for forbidden torch import sites" >&2
    echo "  and the silero_vad.jit JIT model. Exits 1 on any match (Phase 1c gate)." >&2
    exit 2
fi

BIN="$1"

if [[ ! -f "$BIN" ]]; then
    echo "ERROR: binary not found at '$BIN' (check_bundle_torch_free.sh)" >&2
    exit 2
fi

# ─── Forbidden patterns ──────────────────────────────────────────────────────
# `torch\.` — catches every Nuitka-embedded module path like
#   `torch/__init__.py`, `torch/_utils.py`, `torch/nn/modules/...`. Nuitka
#   records these as part of the compiled-in module graph; they survive
#   onefile compression as printable strings.
# `silero_vad\.jit` — the JIT model file bundled with torch-era VAD. After
#   Phase 1a (VAD → ONNX), the bundle must ship `silero_vad.onnx` instead;
#   sighting the `.jit` file means a stale build env or a reverted vad.py.
#
# NB: we case-insensitive-grep for `torch\.` because Nuitka may emit the
# path with mixed case on Windows (e.g. `Lib\site-packages\Torch\...`).
# The `silero_vad.jit` pattern is case-sensitive — the file name is
# always lowercase per MANIFEST.in + export_silero_vad_onnx.py.
PATTERNS=(
    "torch\\."
    "silero_vad\\.jit"
)

# ─── Scan via strings(1) if available ────────────────────────────────────────
# `strings` is fastest (binutils / Xcode CLT / Git Bash all ship it). We
# feed it the binary path and grep -E for the alternation of patterns.
# `set -euo pipefail` + the `|| true` on grep keeps the pipeline from
# aborting when grep finds no match (exit 1) — the explicit exit-code
# check below distinguishes "found" from "not found".
SCAN_RC=0
SCAN_OUTPUT=""
if command -v strings >/dev/null 2>&1; then
    PATTERN_RE="${PATTERNS[0]}"
    for p in "${PATTERNS[@]:1}"; do
        PATTERN_RE+="|${p}"
    done
    # grep -E = extended regex, -i = case-insensitive (covers Windows mixed-case
    # paths), -c = count matches (suppress per-line output to keep CI logs clean).
    # We capture both the count and a sample line for the error message.
    set +e
    SCAN_OUTPUT="$(strings "$BIN" | grep -E -i "$PATTERN_RE" | head -n 5 || true)"
    set -e
    if [[ -n "$SCAN_OUTPUT" ]]; then
        SCAN_RC=1
    fi
else
    # ─── Fallback: Python chunked binary scan ────────────────────────────────
    # Used when `strings` is unavailable. Reads the binary in 1 MB chunks and
    # scans for the same patterns via regex. Slower but correct; the worker /
    # sidecar binary is ~100-200 MB so this adds ~2-3 s to the CI step.
    PY="${PYTHON:-python3}"
    if ! command -v "$PY" >/dev/null 2>&1; then
        PY="python"
    fi
    if ! command -v "$PY" >/dev/null 2>&1; then
        echo "ERROR: neither 'strings' nor 'python3'/'python' is available — cannot scan." >&2
        exit 2
    fi
    SCAN_OUTPUT="$("$PY" - "$BIN" "${PATTERNS[@]}" <<'PYEOF'
import re
import sys

bin_path = sys.argv[1]
patterns = sys.argv[2:]
compiled = [re.compile(p.encode("ascii"), re.IGNORECASE) for p in patterns]
hits = []
chunk_size = 1024 * 1024  # 1 MB
with open(bin_path, "rb") as fh:
    carry = b""
    while True:
        chunk = fh.read(chunk_size)
        if not chunk:
            break
        # carry the last 256 bytes of the previous chunk so patterns spanning
        # the boundary are still caught (the longest pattern is 14 bytes, so
        # 256 is generous).
        window = carry + chunk
        for rx in compiled:
            m = rx.search(window)
            if m is not None:
                hits.append(m.group(0).decode("ascii", errors="replace"))
        carry = chunk[-256:] if len(chunk) >= 256 else chunk
for h in hits[:5]:
    print(h)
if hits:
    sys.exit(1)
PYEOF
)" || SCAN_RC=$?
    if [[ -n "$SCAN_OUTPUT" ]]; then
        SCAN_RC=1
    fi
fi

# ─── Verdict ─────────────────────────────────────────────────────────────────
if [[ "$SCAN_RC" -ne 0 ]]; then
    echo "ERROR: bundle is NOT torch-free — forbidden patterns found in $BIN:" >&2
    echo "$SCAN_OUTPUT" >&2
    echo "" >&2
    echo "Phase 1c gate (plan §11.3): torch must be removed from the bundle before" >&2
    echo "this gate passes. Check vad.py / parakeet_engine.py for stale \`import torch\`" >&2
    echo "sites, or rebuild from a clean env that does not have torch installed." >&2
    exit 1
fi

echo "OK: bundle is torch-free (no \`torch.\` import sites, no \`silero_vad.jit\`)."
exit 0
