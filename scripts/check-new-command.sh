#!/usr/bin/env bash
# scripts/check-new-command.sh — verify all 11 touchpoints for a new IPC command.
#
# Usage: bash scripts/check-new-command.sh <cmd>
#
# See docs/contributing/adding-an-ipc-command.md for the full checklist. This script greps each of the 11 touchpoint locations for
# the given command name and reports which are missing. It also verifies
# that the doc count references (touchpoints 8-11) match the actual counts
# in the source files.
#
# Exit codes:
#   0 — all 11 touchpoints present, doc counts in sync
#   1 — at least one touchpoint missing OR doc count drift detected
#
# This script is intentionally dependency-free (POSIX bash + grep + sed +
# awk) so it runs on every contributor's machine without setup.

set -u

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <cmd>" >&2
    echo "Example: $0 get_widget_count" >&2
    exit 2
fi

CMD="$1"

# Resolve repo root (script lives in <repo>/scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}" || exit 2

# Track missing touchpoints for the final exit code.
MISSING=()
WARNINGS=()

echo "=== checking 11 touchpoints for IPC command '${CMD}' ==="
echo ""

# ─── Helper: print PASS/FAIL line for a touchpoint ──────────────────────
check_present() {
    local num="$1"
    local label="$2"
    local file="$3"
    local pattern="$4"
    if [[ ! -f "${file}" ]]; then
        echo "  [${num}] FAIL  ${label}"
        echo "         file not found: ${file}"
        MISSING+=("${num}")
        return
    fi
    if grep -qE "${pattern}" "${file}" 2>/dev/null; then
        echo "  [${num}] OK    ${label}"
    else
        echo "  [${num}] FAIL  ${label}"
        echo "         pattern not found in ${file}:"
        echo "         ${pattern}"
        MISSING+=("${num}")
    fi
}

# ─── Touchpoint 1: Python _COMMAND_REGISTRY ────────────────────────────
check_present "1" \
    "Python _COMMAND_REGISTRY entry" \
    "voice_typer/server/ipc_server.py" \
    "\"${CMD}\":[[:space:]]+\"_handle_"

# ─── Touchpoint 2: Python handler method ───────────────────────────────
# The handler may live in handlers/<domain>_handlers.py (preferred) or
# directly on IPCServer in ipc_server.py (rare — only for IPC-server-owned
# state). Grep both locations.
{
    FOUND=0
    while IFS= read -r f; do
        if grep -qE "def[[:space:]]+_handle_${CMD//_/[[:space:]]*}[[:space:]]*\\(" "${f}" 2>/dev/null \
           || grep -qE "def[[:space:]]+_handle_${CMD}[[:space:]]*\\(" "${f}" 2>/dev/null; then
            FOUND=1
            break
        fi
    done < <(find voice_typer/server -maxdepth 2 -name '*.py' -type f \
                 \( -path 'voice_typer/server/handlers/*' -o -name 'ipc_server.py' \))
    if [[ "${FOUND}" -eq 1 ]]; then
        echo "  [2] OK    Python handler method (_handle_${CMD})"
    else
        echo "  [2] FAIL  Python handler method (_handle_${CMD})"
        echo "         no 'def _handle_${CMD}(self, ...)' in voice_typer/server/handlers/*.py"
        echo "         or voice_typer/server/ipc_server.py"
        MISSING+=("2")
    fi
}

# ─── Touchpoint 3: Python service method (best-effort) ─────────────────
# Service methods are named after the command (without the _handle_
# prefix). They live in voice_typer/server/service/<domain>.py. Grep the
# whole service/ package for a method definition matching the command name.
{
    FOUND=0
    if [[ -d voice_typer/server/service ]]; then
        while IFS= read -r f; do
            if grep -qE "def[[:space:]]+${CMD}[[:space:]]*\\(" "${f}" 2>/dev/null; then
                FOUND=1
                break
            fi
        done < <(find voice_typer/server/service -name '*.py' -type f)
    fi
    if [[ "${FOUND}" -eq 1 ]]; then
        echo "  [3] OK    Python service method (${CMD})"
    else
        echo "  [3] WARN  Python service method (${CMD})"
        echo "         no 'def ${CMD}(...)' in voice_typer/server/service/*.py"
        echo "         (skip if the handler is pure IPC-server state — see guide)"
        WARNINGS+=("3")
    fi
}

# ─── Touchpoint 4: TS renderer allowlist ───────────────────────────────
check_present "4" \
    "TS ALLOWED_COMMANDS entry" \
    "voice_typer/client/src/main/allowed-commands.ts" \
    "\"${CMD}\""

# ─── Touchpoint 5: Rust host allowlist ─────────────────────────────────
check_present "5" \
    "Rust allowed_commands() entry" \
    "src-tauri/src/commands/sidecar_cmds.rs" \
    "\"${CMD}\""

# ─── Touchpoint 6: TS discriminated union ──────────────────────────────
# Many commands don't need a typed Request interface (the renderer's
# `call<T>()` helper accepts an untyped call for commands whose payload
# is trivial). Make this a warning rather than a hard fail.
#
# DT-31 / DT-FIX-7: the former monolithic ipc types file was split into
# a ``types/ipc/`` directory. The typed Request interfaces (each
# carrying a ``type: "<command>"`` literal) now live in
# ``types/ipc/requests.ts``.
{
    if grep -qF "\"${CMD}\"" voice_typer/client/src/renderer/src/types/ipc/requests.ts 2>/dev/null; then
        echo "  [6] OK    TS discriminated union (types/ipc/requests.ts)"
    else
        echo "  [6] WARN  TS discriminated union (types/ipc/requests.ts)"
        echo "         command name not found in types/ipc/requests.ts"
        echo "         (skip if the renderer uses untyped call<T> — see guide)"
        WARNINGS+=("6")
    fi
}

# ─── Touchpoint 7: TS renderer call site ───────────────────────────────
# Grep the renderer tree for python.call("<cmd>" or python.call('<cmd>'.
{
    FOUND=0
    if grep -rqE "python\\.call\\([\"']${CMD}[\"']" voice_typer/client/src/renderer 2>/dev/null; then
        FOUND=1
    fi
    if [[ "${FOUND}" -eq 1 ]]; then
        echo "  [7] OK    TS renderer call site (python.call('${CMD}', ...))"
    else
        echo "  [7] WARN  TS renderer call site (python.call('${CMD}', ...))"
        echo "         no python.call('${CMD}', ...) in voice_typer/client/src/renderer/"
        echo "         (skip if no renderer caller yet — e.g. host-only command)"
        WARNINGS+=("7")
    fi
}

echo ""
echo "=== Doc count parity (touchpoints 8-11) ==="
echo ""

# ─── Helper: count TS allowlist entries ────────────────────────────────
count_ts_allowlist() {
    awk '
        /ALLOWED_COMMANDS = new Set/ { in_set=1; next }
        in_set && /^[[:space:]]*\]\);/ { in_set=0; next }
        in_set && /^[[:space:]]*"[a-z_]+",?[[:space:]]*$/ { count++ }
        END { print count+0 }
    ' voice_typer/client/src/main/allowed-commands.ts
}

# ─── Helper: count Rust allowlist entries ──────────────────────────────
count_rust_allowlist() {
    awk '
        /let cmds: &\[&str\] = &\[/ { in_lit=1; next }
        in_lit && /^[[:space:]]*\];/ { in_lit=0; next }
        in_lit && /^[[:space:]]*"[a-z_]+",?[[:space:]]*$/ { count++ }
        END { print count+0 }
    ' src-tauri/src/commands/sidecar_cmds.rs
}

# ─── Helper: count Python _COMMAND_REGISTRY entries ────────────────────
count_python_registry() {
    python3 -c "
import re, pathlib
src = pathlib.Path('voice_typer/server/ipc_server.py').read_text(encoding='utf-8')
m = re.search(r'_COMMAND_REGISTRY\\s*:\\s*dict\\[str,\\s*str\\]\\s*=\\s*\\{', src)
if not m:
    print(0); raise SystemExit
body = src[m.end():]
end = body.find('}')
literal = body[:end]
print(len(re.findall(r'\"([a-z_]+)\":\\s*\"_handle_', literal)))
" 2>/dev/null || echo 0
}

TS_COUNT="$(count_ts_allowlist)"
RUST_COUNT="$(count_rust_allowlist)"
PY_COUNT="$(count_python_registry)"

echo "  Source counts: TS=${TS_COUNT}  Rust=${RUST_COUNT}  Python=${PY_COUNT}"
echo "  (Python = TS + host-only commands; tray_click and shutdown are host-only)"
echo ""

# ─── Touchpoint 8: SECURITY.md doc count ───────────────────────────────
{
    # Match either "only the **59** commands listed in" (markdown bold)
    # or "only the 59 commands listed in" (plain). The asterisks are
    # literal in the source — escape them in the sed regex.
    DOC_COUNT=$(sed -n 's/.*only the \*\*\([0-9][0-9]*\)\*\* commands listed in.*/\1/p' SECURITY.md 2>/dev/null | head -1)
    if [[ -z "${DOC_COUNT}" ]]; then
        DOC_COUNT=$(sed -n 's/.*only the \([0-9][0-9]*\) commands listed in.*/\1/p' SECURITY.md 2>/dev/null | head -1)
    fi
    if [[ "${DOC_COUNT}" == "${TS_COUNT}" ]]; then
        echo "  [8] OK    SECURITY.md allowlist count (${DOC_COUNT} == TS ${TS_COUNT})"
    else
        echo "  [8] FAIL  SECURITY.md allowlist count (doc=${DOC_COUNT:-<missing>}, TS=${TS_COUNT})"
        echo "         update SECURITY.md: 'only the **N** commands listed in ALLOWED_COMMANDS'"
        echo "         and 'registers **N** handlers' (N=Python count for the latter)"
        MISSING+=("8")
    fi
}

# ─── Touchpoint 9: docs/ARCHITECTURE.md registry count ─────────────────
{
    DOC_COUNT=$(grep -oE '[0-9]+-command `_COMMAND_REGISTRY`' docs/ARCHITECTURE.md 2>/dev/null \
                | grep -oE '^[0-9]+' | head -1)
    if [[ "${DOC_COUNT}" == "${PY_COUNT}" ]]; then
        echo "  [9] OK    docs/ARCHITECTURE.md registry count (${DOC_COUNT} == Python ${PY_COUNT})"
    else
        echo "  [9] FAIL  docs/ARCHITECTURE.md registry count (doc=${DOC_COUNT}, Python=${PY_COUNT})"
        echo "         update docs/ARCHITECTURE.md: 'N-command _COMMAND_REGISTRY' (3 references)"
        MISSING+=("9")
    fi
}

# ─── Touchpoint 10: CONTRIBUTING.md registry count ─────────────────────
{
    DOC_COUNT=$(grep -oE '[0-9]+-command registry unchanged' CONTRIBUTING.md 2>/dev/null \
                | grep -oE '^[0-9]+' | head -1)
    if [[ "${DOC_COUNT}" == "${PY_COUNT}" ]]; then
        echo "  [10] OK    CONTRIBUTING.md registry count (${DOC_COUNT} == Python ${PY_COUNT})"
    else
        echo "  [10] FAIL  CONTRIBUTING.md registry count (doc=${DOC_COUNT}, Python=${PY_COUNT})"
        echo "         update CONTRIBUTING.md: 'N-command registry unchanged' (in sidecar_ws.py row)"
        MISSING+=("10")
    fi
}

# ─── Touchpoint 11: docs/migration/tauri-sidecar-bridge.md registry count
{
    DOC_COUNT=$(grep -oE '[0-9]+-command registry unchanged' docs/migration/tauri-sidecar-bridge.md 2>/dev/null \
                | grep -oE '^[0-9]+' | head -1)
    if [[ "${DOC_COUNT}" == "${PY_COUNT}" ]]; then
        echo "  [11] OK    tauri-sidecar-bridge.md registry count (${DOC_COUNT} == Python ${PY_COUNT})"
    else
        echo "  [11] FAIL  tauri-sidecar-bridge.md registry count (doc=${DOC_COUNT}, Python=${PY_COUNT})"
        echo "         update docs/migration/tauri-sidecar-bridge.md: 'N-command registry' (2 references)"
        MISSING+=("11")
    fi
}

echo ""
echo "=== Summary ==="
echo "  Touchpoints missing (must fix before merge): ${#MISSING[@]}"
echo "  Touchpoints with warnings (review per command): ${#WARNINGS[@]}"
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "  Missing touchpoint numbers: ${MISSING[*]}"
    echo ""
    echo "See docs/contributing/adding-an-ipc-command.md for the full checklist."
    exit 1
fi
echo ""
echo "All required touchpoints present. Run the parity tests to confirm:"
echo "  python -m pytest tests/test_security_doc_command_count.py \\"
echo "                   tests/test_electron_ipc_and_build.py::TestAllowlistCorrectness \\"
echo "                   -o addopts=\"\" --tb=short"
exit 0
