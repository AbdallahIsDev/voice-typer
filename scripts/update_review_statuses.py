#!/usr/bin/env python3
# ruff: noqa: E501 — each NEW_STATUSES entry is one intentionally-long single
# review.md status line; wrapping would change what gets written to the file.
"""Update review.md statuses for the 12 fixed tasks (this session)."""

import re
import sys
from pathlib import Path

REVIEW = Path("/home/z/my-project/voice-typer/review.md")
s = REVIEW.read_text()

NEW_STATUSES = [
    (
        "### YJ-15",
        "Fixed (verified ON LINUX sandbox 2026-08-25). VoiceTyperError thiserror enum at src-tauri/src/error.rs; Phase-1 dispatch envelope bug fixed (server-error branch returns VoiceTyperError::server_from_data(data), sidecar envelope passes VERBATIM); all 18 #[tauri::command] fns + require_main_window/require_bubble_window migrated to Result<T, VoiceTyperError>; custom Serialize emits envelope JSON as a STRING (usePython.ts parses typeof err === 'string'); Display strings byte-identical for log consumers. parseTauriErrorEnvelope (error-envelope.ts) extended for Tauri parity. Tests: cargo test 526/0, error_tests 18/0, sidecar_cmds_tests 20/0, export_tests 29/0; vitest python-bridge 16/0.",
    ),
    (
        "### [ER-2]",
        "Fixed (verified ON LINUX sandbox 2026-08-25). DeepFilterNet backend replaced with bundled GTCRN ONNX streaming model (535190-byte gtcrn_simple.onnx beside silero_vad.onnx, MIT license, web-verified PESQ 2.87 vs DFN 2.81 vs RNNoise 2.29). gtcrn_backend.py (STFT 512/hop 256/sqrt-hann, native 16 kHz, persistent caches, ORT CPUExecutionProvider); _init_gtcrn degrades to rnnoise on ANY load failure; _process_gtcrn mirrors RNNoise buffering at native 16k. Parity surfaces renamed in one commit (allowlist, Literal, config.ts union, audioFilterRowDescriptors — speex dropped); legacy remap deepfilternet->gtcrn / speex->rnnoise before validation (value remap not reset-to-default); noisy_room preset -> gtcrn; 8 locale noiseSuppressionInfo genuinely translated. MANIFEST.in carries the model line. Tests: 154 passed; perf gate 1.43 ms/hop (<=20 ms gate). rnnoise path byte-identical.",
    ),
    (
        "### XZ-R11-04",
        "Fixed (verified ON LINUX sandbox 2026-08-25). AES-256-GCM at-rest encryption for transcriptions.text per ADR §2/§4/§6/§9: credential_store/_dek.py (generate_dek/store_dek/load_dek via _run_keyring_call directly, NOT store_secret — REJECTS unknown providers); voice_typer/server/_text_crypto.py (encrypt_text -> 'enc:v1:' + base64(12B nonce + ct+16B tag); decrypt_text -> rate-limited WARNING + '<decryption failed>' on InvalidTag; is_encrypted detection); _MIGRATION_V4 (ALTER TABLE ADD COLUMN text_is_encrypted INTEGER DEFAULT 0 + DROP/CREATE au_fts trigger with WHEN NEW.text_is_encrypted = OLD.text_is_encrypted guard so encrypting UPDATEs don't re-index ciphertext; _CURRENT_SCHEMA_VERSION=4); three write paths encrypt (writer.py batch + per-row + history_db.restore() — insert-plaintext-then-UPDATE-with-flag-flip so FTS indexes plaintext at INSERT); read seams decrypt+truncate in Python (project_text_row/get_latest_text/get_transcription_text + LIKE-path now fetches text_is_encrypted); strict key-loss policy (placeholder + rate-limited ERROR + NEVER regenerate DEK while encrypted rows exist; new writes stay plaintext flag=0); bounded backfill (100 rows/batch, idempotent by flag); encryption_status() diagnostic. Tests: 203 passed. ADR status header corrected. VALIDATE ON WINDOWS/MACOS HOST (keyring cmdkey/security find-generic-password).",
    ),
    (
        "### EO-1",
        "Fixed (verified ON LINUX sandbox 2026-08-25). VoiceTyperApp.__init__ split: 690-line god-constructor -> 14-line call sequence of _init_* builders in original construction order (_init_config/_init_threading_and_crash/_log_startup_banner/_init_audio/_init_recording/_init_models/_init_tray/_init_controllers/_init_hotkeys_and_locks/_init_state_flags/_init_history_crash_volume/_init_misc_backings); 15-property lazy hub + 3 back-compat delegates + _LAZY_FAILED/RETRY_TTL_SECONDS/_RECORDER_MISSING/_LazyAudioProcessorProxy extracted to voice_typer/server/app_lazy_hub.py mixin (AppLazyHub); VoiceTyperApp(AppLazyHub). The 4 inspect.getsource(VoiceTyperApp.__init__) pins ported to getsource the corresponding _init_* builder — same substring assertions. app.py: 2125->1267 LOC; app_lazy_hub.py 686 LOC. Tests: 289 passed.",
    ),
    (
        "### EO-3",
        "Fixed (verified ON LINUX sandbox 2026-08-25). sidecar_ws.py split (2081->1469 LOC) into canonical module + voice_typer/server/sidecar_ws_internals/ sibling package following the history_db.py + history_db_internals/ precedent (deliberately NOT a sidecar_ws/ package, which would break ~14 test files pinning the literal .py path). Leaves: encode_pool.py / graceful_shutdown.py / stdout_banner.py / connection.py. Canonical module keeps every file-text-pinned function (_safe_send, _make_dispatch, _authenticate, _read_loop, _start_writer, run) and every patch-observer function so monkeypatches propagate via canonical-module globals. Every moved symbol re-exported with noqa. C-WS-1 ordering + C-WS-2 str-frame contract byte-identical. Tests: 131 passed across all 6 C-WS guard files + mig15/16/17 ws_hmac + mig19.",
    ),
    (
        "### EO-4",
        "Fixed (verified ON LINUX sandbox 2026-08-25). transcription.py split (1572->1212 LOC) into facade + 4 new sibling modules following the established transcription_load.py/transcription_result.py pattern: transcription_device.py / transcription_cuda_probe.py / transcription_download.py / transcription_fallback.py. All 4 use call-time 'from voice_typer.server import transcription as _t' + '_t.<name>' late-binding for module-global patches. TranscriberProtocol identity preserved. Lock/abort/GC-choreography methods retained inline as lock-coupled. Tests: 152 passed, 3 skipped (no behavior change).",
    ),
    (
        "### VP-39",
        "Fixed (verified ON LINUX sandbox 2026-08-25). ShutdownController decomposition completed: _do_cleanup/_do_fast_cleanup bodies moved to free functions in voice_typer/server/shutdown/cleanup.py; _drain_ws_dispatch_pool to voice_typer/server/shutdown/ws_drain.py; _build_sequenced_plan/_build_parallel_plan to voice_typer/server/shutdown/plan.py (beside ShutdownStep/run_plan). Mixin methods (shutdown_controller/_cleanup.py + _plans.py) are 1-2 line delegates — load-bearing test surface preserved. _cleanup.py: 522->72 LOC; _plans.py: 294->152 LOC. Package __init__.py docstring updated. Tests: tests/test_shutdown_parallel.py + test_shutdown_asr_unload.py + test_shutdown_controller*.py + test_shutdown_parallel_pool_drain.py + test_shutdown_deadline.py + tests/regressions/test_electron.py::TestShutdownControllerPhasesContract all green.",
    ),
    (
        "### GQ-11",
        "Fixed (verified ON LINUX sandbox 2026-08-25). logging.rs (1809 LOC) split into src-tauri/src/platform/logging/ directory module: mod/init/combined/redact/panic_hook/early/rotating.rs. Bodies moved byte-verbatim (only fn instance -> pub(super) fn instance for init.rs to call it). Stale header refreshed. logging_tests.rs compiled UNCHANGED via use super::logging::*;. tests/tauri/test_rust_log_file_perms.py path re-pointed to logging/rotating.rs + logging/init.rs. Tests: cargo test logging_tests 91/0; cargo test 526/0. pyrefly-baseline.json 23 stale entries reconciled (21 dropped, 2 re-pointed).",
    ),
    (
        "### EO-11",
        "Fixed (verified ON LINUX sandbox 2026-08-25). SubprocessHotkeyBackend(HotkeyBackend) (native_hotkeys/base.py now imports from hotkeys/base.py — import direction verified acyclic). Pure-delegation passthroughs collapsed to inherited methods where body was a pure 'return self._native.<same call>()' with no added semantics; fan-out/fallback semantics (set_on_release fans to legacy; start falls back on exception) preserved. Stop signature stays stop(self, *, shutdown=True) keyword-only kwarg (LSP-safe). macOS Accessibility onboarding, fallback chain, tray notify, notification helpers stay. Updated docstring at native_adapter.py:36-43. Tests: tests/hotkeys/ + test_keyboard_ownership*.py + test_hotkey_dispatcher.py all green.",
    ),
    (
        "### TC-1",
        "Fixed (verified ON LINUX sandbox 2026-08-25). 8 test files marked with pytestmark = pytest.mark.xdist_group(...): tests/test_keyboard_ownership.py + test_keyboard_ownership_watchdog.py (keyboard_ownership); tests/test_log_rate_limit.py + test_log_rate_limit_lru.py (log_rate_limit); tests/test_binary_path_caching.py + test_native_hotkeys_binary_path.py + test_native_hotkeys_factory_binary_path.py + tests/tauri/test_native_binary_path_tauri.py (native_binary_path). Total real pytestmark markers now 13 (was 5). Makefile + CI -n auto --dist=loadgroup untouched (CLI-level, NOT moved to pyproject addopts — C-TEST-3). CONTRIBUTING.md §7.1 documents marker = same-worker hint under loadgroup (not a correctness guarantee on other dist modes), when-to-add (files sharing process-wide mutable state: singletons, module dicts, lru_caches), the 6 existing groups.",
    ),
    (
        "### [XS-42]",
        "Fixed (HIGH-VALUE SUBSET) (verified ON LINUX sandbox 2026-08-25). make_bare_ipc_server() exported from tests/fixtures/ipc_test_helpers.py (IPCServer.__new__ bypass + _dispatch_lock drift fix + _config_mutation_lock); 4 _make_ipc_server bypass copies migrated (test_notification_event_name.py + tests/tauri/mig15/16/17/test_toast_*.py). make_fake_recorder extended to accept config= and **config_fields (overrides applied BEFORE the Recorder constructor); 10 of 19 _make_recorder defs migrated across 11 files (the remaining 9 are MagicMock stubs / real-Config / SimpleNamespace shapes — documented non-migration). make_clipboard_manager gained last_copied_text; 7 of 10 _make_cm defs migrated across 5 files (the remaining 3 use real ClipboardManager(**kwargs) constructor + rate-limit bypass — different shape, intentionally not migrated). Tests: 536 passed across all touched files. Full migration of the remaining 337 defs is out-of-scope follow-up.",
    ),
    (
        "### [EC-25]",
        "Fixed (PYTHON CATCH-ALLS) (verified ON LINUX sandbox 2026-08-25). All 3 Python catch-all test files DELETED: tests/test_dictation_pipeline_review_fixes.py (619 LOC) split into NEW tests/app/test_notify_once_flags.py + tests/test_transcription_audio_stats.py + tests/test_dictation_pipeline_stage_timer.py + NEW tests/fixtures/dictation_pipeline_helpers.py; tests/test_low_findings_batch.py (467 LOC) split into tests/test_dead_code_stays_removed.py (appended TestLegacyConfigDirRemoved) + NEW tests/test_sensitive_env_redaction.py + NEW tests/test_docs_structure.py + NEW tests/test_electron_build.py — all ticket-ID class names E4-renamed (TestNewDead017->TestLegacyConfigDirRemoved etc.); tests/test_remaining_fixes.py (267 LOC) split into tests/test_transcription.py (WarmUp) + tests/test_qwen_engine.py (Batch) + tests/test_model_manager.py (LRU) + tests/test_platform_utils.py + tests/test_docs_structure.py + NEW tests/test_diagnostics_script.py. 69 tests preserved verbatim across the 3 splits. Zero import references remain. TS catch-alls (ux-components-behavior, electron-ipc-build-behavior, pages-improvements) are documented follow-up — out of scope this session.",
    ),
]

for prefix, new_status in NEW_STATUSES:
    # Heading is `### PREFIX — Title` (note the [ in some prefixes — escape for regex)
    pat_prefix = prefix.replace("[", "\\[").replace("]", "\\]")
    pattern = re.compile(r"(^" + pat_prefix + r"[^\n]*\n\*\*Status:\*\* )[^\n]*(\n)", re.MULTILINE)
    new_s, n = pattern.subn(lambda m, ns=new_status: m.group(1) + ns + m.group(2), s, count=1)
    if n == 0:
        print(f"WARNING: did not update {prefix}", file=sys.stderr)
        continue
    s = new_s
    print(f"updated {prefix}")

REVIEW.write_text(s)
print("review.md updated")
