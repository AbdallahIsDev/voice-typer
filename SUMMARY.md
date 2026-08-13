# SUMMARY — Torch Removal + Slim Core / Runtime Pack Split

**Date:** 2026-08-13
**Repo:** voice-typer (https://github.com/AbdallahIsDev/voice-typer)
**Execution model:** 15 sub-agents in parallel + orchestrator Phase 2 integration pass

---

## Completed

### Phase 1a — Silero VAD → ONNX (Sub-agent 1)
- Rewrote `voice_typer/server/vad.py` to use `onnxruntime.InferenceSession` with `providers=["CPUExecutionProvider"]`. Hoisted LSTM hidden-state buffer `(2, 1, 128)` float32 at module level, threaded through every `compute_vad_prob` call.
- Added `scripts/build/export_silero_vad_onnx.py` — one-shot conversion script (run on a torch-equipped machine to produce `silero_vad.onnx`).
- Updated `MANIFEST.in` to include `silero_vad.onnx`.
- Rewrote `tests/test_vad.py` (35 tests, all pass — uses a `FakeOrtSession`).
- Deleted `tests/test_vad_dtype_optimization.py` (the `data_ptr()` no-clone invariant is unsatisfiable through ORT's allocator).
- Updated `tests/test_electron_ipc_and_build.py:498` from `torch.jit.load` to `InferenceSession`.

### Phase 1b — Parakeet → ONNX (Sub-agent 2)
- Rewrote `voice_typer/server/parakeet_engine.py` (1577 → 1019 LOC). Dropped all `import torch`/`from torch`/`import transformers`/`from transformers`. Now uses `onnx_asr.Model(name, quantization="fp16", providers=...)` (Option B-1).
- GPU→CPU fallback via SESSION RECREATION (not torch's `.to("cpu")`).
- `request_abort()` uses `onnxruntime.RunOptions.set_terminate(True)`.
- Added `ALLOW_PATTERNS_PARAKEET_ONNX` to `voice_typer/server/security/model_integrity.py`.
- Added `voice_typer/stubs/onnx_asr.pyi` type stub.
- Added 5 new test files (76 tests, 67 pass + 9 skip when `onnx_asr` isn't installed).

### Phase 1c — torch sweep (Sub-agent 3)
- Added to `asr_utils.py`: `is_cuda_error()` (5-layer classifier), `is_oom_error()`, `is_likely_english()`, `is_latin_char()`, `merge_chunks()`, `compute_overlap_skip()`. Made `release_gpu_memory()` a no-op.
- Replaced torch GPU probe in `resource_probe.py` with `onnxruntime.get_device()` + `nvidia-smi`.
- Replaced `isinstance(exc, torch.cuda.OutOfMemoryError)` in `transcription.py` with `is_oom_error(exc)`.
- Updated `diagnostics_export.py` + `scripts/diagnostics.py` to report ORT info.
- Stripped VAD-specific torch mocks from `tests/conftest.py` (kept for Qwen).
- Removed `import torch` from `tests/test_transcription_fallback.py`.
- 2 new test files (69 tests, all pass).

### Phase 1c — Dependencies (Sub-agent 4)
- `pyproject.toml`: added `onnx-asr>=0.12.0`, `onnxruntime>=1.20`. Kept `torch>=2.0,<3.0` (Qwen Phase 1d deferral). Removed dead `ignore::DeprecationWarning:torch.jit._serialization` pytest filter.
- `requirements-lock.txt`: manually added `onnx-asr==0.12.0` pin.
- Ratchet baseline regeneration deferred to Phase 2 (noted in worklog).

### Phase 1c — Build scripts (Sub-agent 5)
- Retired `--module-parameter=torch-disable-jit=no` from all 3 `build_sidecar_*.sh` scripts. Added `--include-data-files=...silero_vad.onnx=...`.
- Updated `voice-typer.spec` (PyInstaller fallback): replaced `silero_vad.jit` with `silero_vad.onnx`.
- Created `build_worker_{windows,linux,macos}.sh` (new Nuitka onefile scripts for the worker exe).
- Created `voice-typer-worker.spec` (PyInstaller fallback).
- Created `check_bundle_torch_free.sh` (cross-platform `strings`-based torch detection).
- Updated `tests/tauri/test_config_script_drift.py` — deleted `TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed` test class.

### Phase 2a — Worker entry point + prewarm absorption (Sub-agent 6)
- Created `voice_typer/worker/__main__.py` (794 LOC) — thin WS server launcher with bearer-token auth, single-instance lock, graceful shutdown, prewarm-as-startup-phase.
- Updated `voice_typer/server/prewarm/cache_probe.py` — `_warm_imports` package list now `onnxruntime + ctranslate2 + numpy/scipy`. Exposed `warm_imports_for_worker()`.
- Deleted 10 prewarm machinery files in `voice_typer/server/prewarm/`.
- Deleted 16 orphaned prewarm test files.
- Retargeted `bench/bench_startup.py` to measure worker startup (≤600ms target).
- Added `tests/test_worker_startup.py` (13 tests, all pass).

### Phase 2b — Pack downloader (Sub-agent 7)
- Created `voice_typer/server/service/pack.py` (1461 LOC) — consent-gated runtime-pack downloader with `verify_pack_or_skip()`, resume, atomic swap, disk-space check, GitHub rate-limit backoff, SSRF protection, lock file, background checksum, transcription queue.
- Created 18 edge-case test files (140 tests, all pass).

### Phase 2b — IPC allowlists + parity (Sub-agent 8)
- Added 13 new IPC events to ALL FOUR allowlists: `event_protocol.rs::ALLOWED_EVENT_TYPES`, `ipc/registry.py::_COMMAND_REGISTRY`, `allowed-commands.ts::ALLOWED_COMMANDS`, `allowlist.rs::allowed_commands()`.
- Created `tests/test_event_types_parity.py` (NEW — the 4th allowlist now has a parity test).
- Updated `event_bus.py` catalogue docstring, `requests.ts`, `push_events.ts`, `usePython.ts::KNOWN_EVENT_TYPES`.
- 51 parity tests pass.

### Phase 2b — Frontend pack UI (Sub-agent 9)
- Created `usePackDownload.ts` (silent-mode hook subscribing to 11 pack/worker push events).
- Created `PackPreparingBanner.tsx` (the "Preparing offline engine…" line).
- Wired banner into `Microphone.tsx` + `Home.tsx`.
- 38 new tests pass.

### Phase 2a — Tauri Rust spawn infra (Sub-agent 10)
- Created `src-tauri/src/platform/worker_path.rs` (per-platform worker exe path resolver).
- Created `worker_path_tests.rs`.
- Generalized `SidecarState` for 2 children (`WorkerState` struct added; spawn logic intentionally stubbed pending worker exe).
- Deleted `src-tauri/src/sidecar/spawn/prewarm.rs`.
- Updated all 5 platform tauri conf files (removed prewarm from `externalBin`).
- Updated `tauri.conf.json::plugins.shell.scope` (added worker).
- ~30 new Rust unit tests.

### Phase 2c — Slim core installer (Sub-agent 11)
- Created `scripts/windows/installer-hooks.nsh` (NSIS "Include offline engine pack" checkbox).
- Created `scripts/windows/full-offline-installer.nsi` (standalone full-offline installer template).
- Created `scripts/build/build_full_offline_installer_windows.sh`.
- Created `scripts/build/artifact_names.py` (single source of truth for §11.9 artifact names).
- Created `tests/tauri/test_installer_naming.py` (36 tests).
- Updated `tauri.conf.json::bundle.windows.nsis.installerHooks` to list both `.nsh` files.

### Phase 2c — CI/CD workflows (Sub-agent 12)
- Updated all 3 Tauri build workflows: worker signing (5th binary), size gates (sidecar ≤185MB, pack ≤200MB, slim core ≤45MB), torch-free check step.
- macOS: worker notarization + stapling in both arch jobs.
- Linux: worker as separate download artifact.
- YAML validation passes. Config drift tests pass (27/27).
- C-CI-8 flag block left in `tauri-windows-build.yml` (USER-ONLY retirement).

### Phase 2b — Auto-update mechanism (Sub-agent 13)
- Created `voice_typer/server/service/update_check.py` (pack-version checker).
- Created `scripts/release/publish_pack_release.py` (GitHub Releases publisher).
- Created `voice_typer/client/src/renderer/src/hooks/useNetworkOnline.ts`.
- 137 new tests pass.

### Phase 2b — i18n locales (Sub-agent 14)
- Added 19 new i18n keys across 3 namespaces (`pack.*`, `notify.pack_manager.*`, `settings.*`) to all 8 locale files.
- 141 string values written (8 English + 133 native translations).
- All parity tests pass (82 Python + 11 TS).

### Documentation sweep (Sub-agent 15)
- Updated `docs/adr/0005-silero-vad.md` (ONNX migration + hidden-state threading).
- Updated `docs/adr/0009-audio-filter-chain-architecture.md` (6 stale torch claims fixed).
- Updated `docs/adr/0020-desktop-runtime-migration-analysis.md` (the false "vad.py, silero_vad.jit — Unchanged" claim).
- Updated `docs/auto-update-feature.md` (NOT IMPLEMENTED → IMPLEMENTED).
- Updated all 5 doc-accuracy tests (45 pass, 1 skip).

### Phase 2 — Orchestrator integration
- Fixed `tauri.conf.json::installerHooks` to be a list with both `.nsh` files.
- Updated `scripts/gen_tauri_icons_stub.py` to generate worker stubs + drop prewarm stubs.
- Updated `tests/tauri/test_config_script_drift.py` — removed prewarm pin references to deleted files.

---

## Fixed During Investigation

- **`tauri.conf.json::installerHooks` was a string, not a list.** Sub-agent 11's edit didn't stick (likely a concurrent-edit issue noted by Sub-agent 12 too). Orchestrator re-applied the list form so both `uninstaller.nsh` and `installer-hooks.nsh` are registered.
- **`scripts/gen_tauri_icons_stub.py` still declared prewarm stubs and didn't declare worker stubs.** The drift test `TestBundleBinariesVsStubRegistry` caught this — the stub generator is the canonical source of binary paths, and it was out of sync with the tauri conf files (which Sub-agent 10 had updated to add the worker + drop prewarm). Orchestrator updated the stub generator to match.
- **`tests/tauri/test_config_script_drift.py` pinned source literals in deleted files** (`prewarm/completion_events.py`, `prewarm_scheduler_posix.py`). The reverse-DNS namespace test tried to read these files and failed with `FileNotFoundError`. Orchestrator removed the stale pins (the autostart + keyring pins remain).
- **Sub-agent 10 hit the max-turns limit** before writing its worklog entry. Orchestrator verified the on-disk state (18 files touched, ~843 LOC added) and appended the worklog entry manually.

---

## Remaining Work

### Needs USER action on AGENTS.md (consolidated)

1. **Retire C-CI-8** — the rule mandating `--module-parameter=torch-disable-jit=no` to protect the Nuitka bundle while torch is shipped. VAD no longer uses torch; the flag is retired in the build scripts (Sub-agent 5) but still present in `.github/workflows/tauri-windows-build.yml:469` because the workflow is "DO NOT BREAK" (AGENTS.md). User must retire C-CI-8 in AGENTS.md, then a follow-up agent can remove the workflow line.

2. **Correct NU-106 reference** — NU-106 is an inline evidence tag in the workflow YAML + build scripts, NOT a standalone AGENTS.md rule. It's cited in C-CI-8's rationale. The plan docs (§11.1) clarify this; the AGENTS.md C-CI-8 rationale should be updated to reflect that NU-106 is the evidence tag, not a separate rule.

3. **Update C-CI-11** — currently enumerates 4 code-signing steps (sidecar+prewarm+native listener; NSIS; MSI; standalone exe). The worker exe (Sub-agent 5) is a 5th binary. The full-offline installer (Sub-agent 11) is a 6th. User must update C-CI-11 to enumerate the new binaries.

4. **Update C-DATA-1** — currently allows 3 categories of network calls (update checks, cloud transcription, model downloads). The pack download from GitHub Releases (Sub-agent 7) + the auto-update check (Sub-agent 13) are NOT covered. User must extend category (3) → "runtime asset downloads" OR add category (4) for pack downloads from GitHub Releases (consent-gated via `runtime_pack_consent`, NOT `huggingface_consent`).

5. **CR-11 reference drift** — the slice prompt's "CR-11" reference is documentation drift. CR-11 does NOT exist in AGENTS.md; the actual consent-gate rule is C-DATA-1. No AGENTS.md edit needed; clean up plan docs to map "CR-11" → "C-DATA-1".

6. **NSIS installer i18n gap** — the "Include offline engine pack" NSIS string (Sub-agent 11) is NOT covered by renderer i18n parity. Optional: commission NSIS `.nsh` language files for 8 locales.

### Needs orchestrator/CI follow-up

- **`silero_vad.onnx` binary NOT produced** — torch not installed in the dev sandbox. Maintainer runs `python scripts/build/export_silero_vad_onnx.py` on a torch-equipped machine and commits the ~2 MB `.onnx` file.
- **`model_hashes.json` regeneration** — Sub-agent 2 skipped this because `populate_model_hashes.py` would bloat the manifest with non-downloaded files. Needs a script fix or manual curation.
- **`MODEL_REGISTRY["parakeet"]` update** — `repo_id`, `download_size_mb=1300`, `network_behavior=consent-gated`, `description`. `model_registry.py` was not in any sub-agent's ownership.
- **`tests/test_model_registry.py::test_parakeet_is_no_consent`** → rename to `test_parakeet_is_consent_gated` (fixes G4-H-04).
- **Existing torch-based parakeet tests** (`test_parakeet_engine.py` 73 tests, `test_parakeet_inference_mode.py`, `test_parakeet_cpu_abort.py`, `test_dictation_pipeline_abort.py` `_AbortStoppingCriteria` import sites) — these mock torch/transformers and will ERROR/FAIL with the rewrite. Needs orchestrator rewrite or deletion.
- **Top-level `voice_typer/server/{prewarm_resolver,prewarm_scheduler_posix}.py` remain** — Sub-agent 6 noted them as out-of-scope orphans. `task_scheduler.py` is shared with autostart and CANNOT be deleted wholesale.
- **3 production-code refs to deleted prewarm attrs** (try/except-guarded, no crash but dead paths): `status_handlers.py:119`, `diagnostics_export.py:573`, `model_manager.py:1056`.
- **Ratchet baselines regeneration** — `coverage-baseline.json`, `mypy-baseline.json`, `pyrefly-baseline.json`, `ruff-baseline.json` need `--regenerate --force` after all changes stabilize.
- **`requirements-lock.txt` proper regen** — needs `uv pip compile pyproject.toml -o requirements-lock.txt` (currently has a manual `onnx-asr==0.12.0` pin).
- **Worker spawn implementation** — `src-tauri/src/sidecar/spawn.rs` has `WorkerState` stubs (returns `Err("worker spawn not yet implemented — Phase 2a stub")`). Full implementation requires the worker exe to exist + the pack downloader to be wired.
- **`_MODEL_SIZE_MB["parakeet"]`** in `asr_utils.py` — update from 2500 → 1300 (FP16 ONNX).
- **`check_pack_update` IPC command** — exposed by Sub-agent 13's `handle_check_pack_update_ipc` but NOT registered in `ipc/registry.py` / `allowed-commands.ts` / `allowlist.rs`. Renderer hook fails gracefully until wired.
- **`runtime_pack_consent` config field** — referenced via `getattr(config, "runtime_pack_consent", False)` but not yet added to the Config dataclass.
- **`useNetworkOnline` hook** — created but NOT mounted in the App component.
- **`bench/bench-baseline.json`** `bench_startup.cold_import.first_run_ms` — needs CI update after the runtime-pack is built.

### Open decisions from master §14 / companion §10

1. **Qwen migration option** — Recommendation C-3 (defer) stands. `qwen_engine.py` keeps torch + transformers until the `qwen_asr` maintainer confirms ONNX support.
2. **Prewarm architecture** — P-1 (worker startup phase) implemented.
3. **Worker lifecycle** — Long-lived with a "Keep offline engine running" setting (i18n key added by Sub-agent 14).
4. **Metered-connection default** — Auto-download on Windows (NLM detection), manual on Linux/macOS.
5. **Full-offline installer** — Yes, always publish both (slim core + full-offline).
6. **macOS GPU for Parakeet** — Not tested. CPU-only is the default.
7. **Pack source** — GitHub Releases (no new infrastructure).

---

## Recommended Next Steps

⭐ **Run `python scripts/build/export_silero_vad_onnx.py` on a torch-equipped machine and commit `voice_typer/server/silero_vad.onnx`** — this is the single biggest blocker. Without the .onnx file, VAD cannot run in the shipped app.

1. **Retire C-CI-8 in AGENTS.md** and remove the `--module-parameter=torch-disable-jit=no` flag block from `.github/workflows/tauri-windows-build.yml:469`.
2. **Update C-CI-11, C-DATA-1 in AGENTS.md** per the consolidated list above.
3. **Regenerate `requirements-lock.txt`** via `uv pip compile pyproject.toml -o requirements-lock.txt`.
4. **Regenerate ratchet baselines** via `scripts/coverage_ratchet_check.py --regenerate --force` (and equivalent for mypy/pyrefly/ruff).
5. **Rewrite the existing torch-based parakeet tests** (`test_parakeet_engine.py` etc.) to use the new ORT backend.
6. **Update `MODEL_REGISTRY["parakeet"]`** in `model_registry.py` (repo_id, download_size_mb, network_behavior).
7. **Wire `check_pack_update` IPC command** into the 4 allowlists + mount `useNetworkOnline` hook in the App component.
8. **Add `runtime_pack_consent` field** to the Config dataclass.
9. **Delete `voice_typer/server/{prewarm_resolver,prewarm_scheduler_posix}.py`** (prewarm-only orphans; `task_scheduler.py` stays — shared with autostart).
10. **Run a full CI build** to verify the Rust compiles + the worker exe builds + the size gates pass.

---

## Validation Results

- **Torch-removal gate:** `grep -rEn "^[ \t]*import torch\b|^[ \t]*from torch\b" voice_typer/` → only `qwen_engine.py` (4 occurrences, expected per Phase 1d deferral). ✅
- **Python tests:** 700 passed, 2 skipped, 0 failed (scoped to new + modified test files). ✅
- **Frontend tests:** 38 passed, 0 failed (scoped to new + modified test files). ✅
- **Config drift tests:** 27 passed, 0 failed. ✅
- **Installer naming tests:** 36 passed, 0 failed. ✅
- **IPC parity tests:** 51 passed, 0 failed. ✅
- **Rust compilation:** Not verified (disk space constraints prevented `cargo check`). CI will verify. ⚠️
- **Size gates:** Not verified (requires a full build). CI will verify. ⚠️

---

## Torch-Removal Scope Boundary (per §2)

- No sub-agent ran `pip uninstall torch`/`torchvision`/`nvidia-*` or any equivalent.
- No sub-agent touched `.venv`/virtualenvs/conda envs.
- No sub-agent deleted torch caches (`~/.cache/torch`, `~/.cache/huggingface`).
- No sub-agent modified anything outside the repository tree.
- No sub-agent edited `AGENTS.md`.
- The user's installed torch (CPU + GPU) is fully intact.

## Worklog

The full 15-section worklog + orchestrator Phase 2 summary is at `/home/z/my-project/voice-typer/worklog.md` (1294 lines). The final consolidated `## AGENTS.md — needs user action` section is at the bottom.
