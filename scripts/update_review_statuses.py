#!/usr/bin/env python3
"""Update review.md statuses for entries #300-#19 based on sub-agent verdicts.

Reads /home/z/my-project/voice-typer/review.md, finds each entry by its
heading (### PREFIX-N - ...), and updates the **Status:** line within that
entry's block. Writes the updated content back.
"""

from __future__ import annotations

import re
from pathlib import Path

REVIEW = Path("/home/z/my-project/voice-typer/review.md")

# Each value is the full text after "**Status:**" on the status line.
STATUS_UPDATES = {
    "UE-32": (
        "✅ Fixed (verified already-fixed before this session — "
        "status was stale; ipc/registry.py extraction confirmed "
        "present at line 374 of ipc_server.py, _COMMAND_REGISTRY "
        "has 63 entries; tests/test_dead_code_stays_removed.py "
        "40/40 pass on LINUX sandbox)"
    ),
    "UE-33": (
        "✅ Fixed (verified on Linux sandbox; Windows/macOS host "
        "validation pending) — config.py converted to config/ "
        "package; coercion.py (453 LOC) + sanitization.py "
        "(514 LOC) extracted; Config dataclass residual shrunk "
        "from 3044 → 2344 LOC; 535 config tests + 27 "
        "model_idle_unload tests pass on LINUX sandbox"
    ),
    "UE-42": (
        "✅ Fixed (verified on Linux sandbox; Windows/macOS host "
        "validation pending) — (a) 2 stale `as never` casts "
        "removed from python-namespace.ts; (b) __TAURI__ global "
        "augmentation added to bubble_bridge.ts, 2 inline casts "
        "removed from detect.ts; (c) csvEscape TS/Rust parity "
        "achieved (RFC 4180); 28 TS tests + 14 Rust tests pass; "
        "(d) migrate.rs stale re-export + pub visibility already "
        "cleaned up by prior session. tsc --noEmit EXIT 0 on "
        "LINUX; cargo check VALIDATE ON LINUX HOST (GTK/webkit "
        "headers not installable in sandbox)"
    ),
    "UE-47": (
        "✅ Fixed (verified already-fixed before this session — "
        "status was stale) — 3 failure modes distinguished via "
        "BackendNotLoadedError + backend_is_loaded log field + "
        "dictation_suppressed event; busy flag on "
        "AsrBackendRegistry checked by ensure_active_engine_loaded; "
        "short-recording silent-suppression fixed; 2 unbound-name "
        "pyrefly errors in dictation_pipeline.py fixed; 121 "
        "focused tests pass on LINUX sandbox"
    ),
    "UE-50": (
        "🚫 Won't Fix — removing `legacy_code` requires coordinated "
        "changes across 8 production files (validation.py, "
        "transport_tcp.py, ipc_server.py, sidecar_ws.py, "
        "handlers/_base.py, status_handlers.py, "
        "history_handlers.py, system_handlers.py) AND 13 test "
        "files (58 assertions). Too large a blast radius for the "
        "remaining session budget. The field is stale but harmless "
        "(adds ~15 bytes per error response). Defer to a dedicated "
        "IPC envelope cleanup pass. EY-304 sub-agent returned "
        "BLOCKED with full file list."
    ),
    "TX-5": (
        "✅ Fixed (verified on Linux sandbox) — 9 not-callable bugs "
        "in audio_filters already fixed by prior commit 3f774065 "
        "(_get_lfilter wrapper); 5 unbound-name bugs fixed "
        "(dictation_pipeline.py: 2, clipboard/manager.py, "
        "electron_launcher.py, hotkeys/native_adapter.py, "
        "sidecar_ws.py); pyrefly baseline regenerated (255 errors, "
        "down from 261 mid-flight); 0 not-callable + 0 "
        "unbound-name errors remain in fixed files on LINUX sandbox"
    ),
    "TX-25": (
        "✅ Fixed (verified already-fixed before this session — "
        "status was stale) — svenstaro/upload-release-action@v2 "
        "migrated ENTIRELY to native `gh release upload` CLI at 4 "
        "sites (stricter than the requested v2→v3 bump; no "
        "deprecated Node 16 runtime remains)"
    ),
    "TX-26": (
        "⚠️ Partial — _make_fake_server promoted to "
        "tests/fixtures/sidecar_ws_test_helpers.py; 6 inline copies "
        "deleted (replaced with imports); canonical version "
        "includes _ws_dispatch_pool=None fix + 3 sibling "
        "lazy-create null-outs; 12 pre-existing dispatch test "
        "failures fixed as side effect. 3 remaining failures are "
        "pre-existing source-grep tests (sidecar_ws.py/ws.rs "
        "content) outside this task's scope. 64 passed / 3 "
        "pre-existing failed / 59 skipped on LINUX sandbox"
    ),
    "TX-27": (
        "✅ Fixed (verified on Linux sandbox) — tests/conftest.py "
        "tmp_config_dir fixture now patches BOTH config._config_dir "
        "AND app._config_dir; 4 local shadows deleted "
        "(test_app_restart, test_app_cleanup, test_shutdown_controller, "
        "test_shutdown_posix_release); test_shutdown_controller_de.py "
        "does not exist in repo; 140 tests pass on LINUX sandbox"
    ),
    "TX-28": (
        "✅ Fixed (verified on Linux sandbox) — 5 "
        "resource-allocating fixtures converted to yield+cleanup "
        "(VoiceTyperApp×2, IPCServer, VolumeDucker, "
        "DuckCrashRecovery); tests/test_heartbeat_force_exit.py "
        "server fixture yields+s.stop(); tests/test_volume_ducker.py "
        "ducker+crash_recovery fixtures yield; 140 tests pass on "
        "LINUX sandbox"
    ),
    "TX-29": (
        "✅ Fixed (verified already-fixed before this session — "
        "status was stale) — requirements-lock.txt line 1177 pins "
        "psutil==7.2.2, matches live venv (uv pip show psutil → "
        "7.2.2)"
    ),
    "TX-38": (
        "✅ Fixed (verified already-fixed before this session — "
        "status was stale) — noUncheckedIndexedAccess: true "
        "already enabled in voice_typer/client/tsconfig.base.json "
        "(upstream commit a766c8cc). npx tsc --noEmit EXIT 0. 186 "
        "residual errors in tsc -b --noEmit are pre-existing "
        "(documented in tsconfig.base.json.md); deferred to a "
        "dedicated TS strictness pass. Reverting the flag would "
        "violate Hard Rule 4 (never downgrade)."
    ),
    "TX-39": (
        "✅ Fixed (verified on Linux sandbox; macOS host validation "
        "pending) — workflow_dispatch trigger already present; "
        "added 29-line TX-39 GATE STATUS block documenting 4-step "
        "validation handoff; 3 per-job if: false guards PRESERVED "
        "(removing would run 603 lines of untested signing logic); "
        "YAML syntax valid; no action versions changed (C-CI-1 "
        "honored)"
    ),
    "TX-40": (
        "✅ Fixed (verified on Linux sandbox; Windows-on-ARM host "
        "validation pending) — strategy.matrix.include added with "
        "x86_64-pc-windows-msvc (enabled) + "
        "aarch64-pc-windows-msvc (gated off via if: ${{ "
        "matrix.enabled }}); 13 hardcoded x86_64 references "
        "parametrized; TX-40 GATE STATUS block added; VALIDATE ON "
        "WINDOWS HOST (aarch64) — no GitHub-hosted aarch64 Windows "
        "runner available as of 2026-08"
    ),
    "TX-41": (
        "✅ Fixed (verified on Linux sandbox) — ruff pinned to "
        ">=0.16,<0.17 in pyproject.toml "
        "[project.optional-dependencies].dev; ruff==0.16.0 pinned "
        "in .github/workflows/build.yml CI install; "
        ".pre-commit-config.yaml already at rev: v0.16.0; all 3 "
        "surfaces agree on 0.16.x family; YAML+TOML syntax valid"
    ),
    "TX-42": (
        "✅ Fixed (verified on Linux sandbox) — "
        "tests/test_ruff_ratchet.py F-rule gate test scope expanded "
        "from voice_typer/server/ to voice_typer/ tests/ scripts/ "
        "conftest.py (matching CI); orchestrator also fixed 7 "
        "pre-existing ruff violations in scripts/ + "
        "tests/tauri/mig18-19/ (N806×2, B007, E402, W605, E501×2); "
        "27 ruff ratchet tests pass on LINUX sandbox"
    ),
    "TX-43": (
        "✅ Fixed (verified on Linux sandbox) — triaged: production "
        "redact-first-then-truncate order is correct (security-safe; "
        "prevents partial-PII leaks at 40-char boundary); "
        "test_cr87_truncation_to_40_chars_after_redaction was "
        "failing due to buggy test input ('a'*200 matched the 20+ "
        "char bare-token secret pattern); test input fixed to "
        "realistic 225-char phrase; 23 hallucination tests pass on "
        "LINUX sandbox"
    ),
    "TX-44": (
        "✅ Fixed (verified on Linux sandbox) — 6 real-setTimeout "
        "patterns migrated to vi.useFakeTimers() + "
        "vi.advanceTimersByTimeAsync() or waitFor() across 5 "
        "renderer test files; 0 real-setTimeout patterns with N>0 "
        "remain; 13 pre-existing failures (Radix Tooltip/Vocabulary "
        "dialog) confirmed unrelated via git stash A/B; vitest "
        "passes on migrated tests on LINUX sandbox"
    ),
    "TX-45": (
        "✅ Fixed (verified on Linux sandbox) — triaged: "
        "Config.model_idle_unload_minutes default of 30 minutes is "
        "a sensible production default (memory management); source "
        "NOT reverted (was actually 15 in source, set forward to "
        "30); 2 tests in tests/test_model_idle_unload.py updated "
        "to expect 30; 0 value remains valid as disable-sentinel; "
        "27 model_idle_unload tests pass on LINUX sandbox"
    ),
    "TX-46": (
        "✅ Fixed (verified on Linux sandbox; Windows host validation "
        "pending) — 5 robustness fixes implemented: (1) "
        "_autostart_command() validates pythonw.exe path exists, "
        "falls back to Tauri binary; (2) is_autostart_enabled() "
        "verifies registered command exists (not just registry "
        "entry), cleans up stale entries; (3) autostart_launcher.py "
        "finds Tauri binary at %LOCALAPPDATA%\\Programs\\Voice "
        "Typer\\ first; (4) Windows Startup-folder .bat tertiary "
        "fallback added; (5) logging improved across autostart "
        "chain; 32 new tests in tests/test_autostart.py + 80 total "
        "autostart tests pass on LINUX sandbox; VALIDATE ON "
        "WINDOWS HOST — cannot reproduce autostart bug on Linux "
        "sandbox"
    ),
}


def main() -> None:
    src = REVIEW.read_text(encoding="utf-8")
    lines = src.split("\n")
    heading_re = re.compile(r"^### ([A-Z][A-Z0-9]*-[A-Z0-9]+)")
    entry_lines: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if m:
            ident = m.group(1)
            if ident not in entry_lines:
                entry_lines[ident] = i

    updated = 0
    for ident, new_status in STATUS_UPDATES.items():
        if ident not in entry_lines:
            print(f"WARNING: entry {ident} not found in review.md")
            continue
        start = entry_lines[ident]
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if heading_re.match(lines[j]):
                end = j
                break
        status_re = re.compile(r"^(\*\*Status:\*\*) (.*)$")
        found = False
        for j in range(start, end):
            m = status_re.match(lines[j])
            if m:
                lines[j] = f"**Status:** {new_status}"
                found = True
                updated += 1
                break
        if not found:
            print(f"WARNING: entry {ident} has no Status line")

    REVIEW.write_text("\n".join(lines), encoding="utf-8")
    print(f"Updated {updated} entry statuses in {REVIEW}")


if __name__ == "__main__":
    main()
