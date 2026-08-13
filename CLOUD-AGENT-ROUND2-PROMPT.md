# Cloud Agent — Round 2 Prompt (execute ALL remaining work, 20 parallel sub-agents)

You are continuing work on `AbdallahIsDev/voice-typer` (branch `main`). A previous
session executed two plans (runtime-pack-split + ONNX migration, see
`docs/plan-runtime-pack-split.md`, `docs/rw04-recording-decomposition.md`,
`review.md`, `worklog.md`) at ~65% completion using 15 parallel sub-agents in one
message. That session DID NOT run verification. Your round must finish the rest
AND produce a verified green matrix.

## Process rules (MANDATORY)

1. In your FIRST message, dispatch **20 sub-agents in parallel** (round 1 used 15;
   more parallelism = more progress). Partition the work per the workstreams below.
2. Every sub-agent prompt MUST include:
   - Read `AGENTS.md` first — its `Hard "Don'ts"` section (L232-556) is binding
     (C-CI-1..15: NEVER edit the `tauri-*.yml` workflows as a first-line fix;
     keep Node-24 action pins, `nuitka==2.8.10`, `timeout-minutes: 240`,
     `CLCACHE_DISABLE: "1"`, signing gates, artifact names, smoke-test pattern).
   - Branding: never inline the app name — use `APP_NAME` imports; never edit
     `branding.py`/`branding.ts` literals except the source-of-truth files.
   - i18n C-I18N-1/2: any new user-visible string needs a key in ALL 8 locales,
     genuinely translated.
   - IPC parity (§6.4 CONTRIBUTING.md): server `_COMMAND_REGISTRY`,
     `ALLOWED_COMMANDS` (Electron main), and renderer `PythonRequest`/
     `PythonPushEvent` unions stay in lockstep; do NOT reformat the
     `ALLOWED_COMMANDS = new Set([` declaration (parity test slices it).
   - C-TEST-5: tests live in separate files, never inline in production code.
   - No session/task IDs in code (C-STYLE-1).
   - ruff is ratcheted to 0 violations (`ruff-baseline.json`); pre-commit hooks
     enforce ruff + ruff-format + client typecheck — leave everything hook-clean.
3. After sub-agents return: merge, run the FULL verification gate below, fix every
   red, re-run until green (or explicitly record un-fixable reds with evidence).
4. Commit at the end in small logical commits. Do not force-push.

## Current repo state (committed WIP — broken until verified)

- **prewarm → worker migration**: `voice_typer/worker/` replaces
  `voice_typer/server/prewarm/` (deleted); Rust spawn rewritten
  (`src-tauri/src/sidecar/spawn/*`, new `platform/worker_path.rs`); tauri
  `externalBin` now includes `voice-typer-worker`; new IPC request
  `transcribe_offline` + push event `transcribe_offline_result`.
- **ONNX Phase 1c**: `parakeet_engine.py` now uses `onnx_asr.Model` (torch-free);
  `stubs/onnx_asr.pyi`; VAD/ASR/transcription/security-model changes.
- **runtime-pack service**: `voice_typer/server/service/pack.py`,
  `service/update_check.py`, `scripts/release/publish_pack_release.py`, client
  `PackPreparingBanner` + `usePackDownload` + `useNetworkOnline`.
- **CI gates** added to tauri workflows (sidecar size ≤185 MB, torch-free check,
  worker build step — some gated on `hashFiles` of scripts that do not exist yet).
- **New test files**: `tests/test_worker_startup.py`, `test_parakeet_onnx_*.py`,
  `test_pack_*.py` (20), `test_update_*.py`, `test_asr_utils_*.py`,
  `test_installer_naming.py`, `test_event_types_parity.py`.
- `pyrefly-baseline.json` regenerated (342→3 entries).

## Known verified issues (fix these; the 10 smallest from local review)

1. `docs/auto-update-feature.md` line ~206 references `.github/workflows/release.yml`
   which does NOT exist. Update to the real pipeline (tauri-build.yml manual
   dispatch orchestrator).
2. **Parakeet RunOptions abort mechanism is dead code in practice** — verified
   against onnx-asr 0.12.0 wheel source: `recognize()` → `recognize_batch()` never
   forwards `run_options` to `session.run`. `RunOptions.set_terminate` cannot reach
   ORT; the working abort path is `_abort_event` checked between chunks
   (`_transcribe_chunks`). Decide: remove the stash/set_terminate plumbing
   (update `tests/test_parakeet_onnx_abort.py` accordingly) or keep as documented
   best-effort hardening. Do NOT claim mid-run termination works for the
   single-segment path.
3. **Lint debt**: several files were committed with `--no-verify` and carry ruff
   violations (e.g. SIM105/SIM102/E501 in `voice_typer/worker/__main__.py`,
   `voice_typer/server/ipc/lifecycle.py`, `tests/test_update_check.py`,
   `tests/test_parakeet_onnx_*.py`, `tests/tauri/test_installer_naming.py`). Run
   `ruff check --fix` + `ruff format` over the tree; ratchet must stay 0/0.
4. **Client typecheck**: `tsc -p tsconfig.web.json` must pass. A phantom-guard
   mirror (`ipc-requests-coverage.test.ts` `_SERVER_REGISTRY_MINUS_PYTHON_ONLY`)
   was updated for `transcribe_offline`; verify no other drift
   (`usePython.ts`, `push_events.ts`, `requests.ts`, `Home.tsx`,
   `Microphone.tsx`).
5. **Rust build/tests**: `cargo check`/`cargo test` were NOT run after the
   spawn/worker-path migration. Run them; fix compile errors, move any
   still-inline test modules to sibling `*_tests.rs` files (C-TEST-5).
6. **`tests/test_update_check.py`** may contain nested-with lint + logic gaps;
   review alongside `service/update_check.py` for the `json.loads` validation
   probe (used only for JSONDecodeError).
7. `tests/test_pack_*.py` (20 files) exist but the suite has never been run
   end-to-end; some may be flaky (timing/download mocks) or fail on the
   `mock_heavy_imports` conftest fixture contract.
8. `.github/workflows/tauri-*.yml` gates: the 185 MB sidecar gate HARD-FAILS by
   design until torch is out of the bundle (Phase 1c) — do NOT weaken it; the
   torch-free + worker-build steps are inert until their
   `scripts/build/check_bundle_torch_free.sh` / `build_worker_windows.sh` exist —
   sub-agents may create those scripts (see plan §11.3/§11.5), but do not touch
   the workflow structure itself (C-CI-2).

## Suggested 20 parallel workstreams

1. Rust compile/test pass: `cargo check` + `cargo test` (src-tauri) — fix all errors.
2. Python full pytest suite run + triage (partition by test dir if huge).
3. ruff --fix + format whole tree; ratchet 0/0; pre-commit hooks clean.
4. Client vitest + `tsc -b`; fix TS errors.
5. Client IPC parity triple check (server registry / allowed-commands / unions).
6. Parakeet abort: resolve RunOptions dead-mechanism (issue 2) + tests.
7. Parakeet GPU fallback + CUDA classifier + language filter tests green.
8. worker startup/lifecycle tests + single-instance lock logic review.
9. pack service: download/checksum/atomic-swap/resume/queue tests green.
10. pack service: consent gate, disk-space, metered, proxy, signing tests green.
11. update_check service tests + SSRF/schema caps review.
12. update_publish + scripts/release/publish_pack_release.py tests green.
13. Client pack UI: PackPreparingBanner/usePackDownload/useNetworkOnline tests green.
14. Installer naming tests + offline-installer nsh hooks consistency (scripts/windows/*).
15. Docs sweep: auto-update-feature workflow name (issue 1), ARCHITECTURE, runbooks,
    plan docs ↔ code drift (test_architecture_doc_accuracy).
16. Prewarm→worker doc/ADR updates (0005/0009/0011/0018/0020, home-directory,
    migration runbooks) — remove prewarm references.
17. Event/push parity tests + test_event_types_parity green.
18. Bench/startup script + bench-baseline refresh check.
19. Security surface review of new code (pack downloader SSRF, update check,
    worker auth token handoff) — record findings only, no scope creep.
20. Final integration: run the FULL verification gate, produce green matrix.

## Verification gate (MANDATORY — run last, report per-row status)

- [ ] `ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | python scripts/ruff_ratchet_check.py` → PASS (0/0)
- [ ] `pytest -n auto --dist=loadgroup` full suite → collect + pass (no regressions vs baseline)
- [ ] `cd voice_typer/client && npx vitest run` → all pass
- [ ] `cd voice_typer/client && npm run typecheck` (tsc web + node) → clean
- [ ] `cargo test` (src-tauri) → pass
- [ ] `python scripts/check_branding.py` → clean
- [ ] `pytest tests/test_electron_ipc_and_build.py::test_allowlist_matches_server_commands` → pass
- [ ] `python scripts/check_branding.py` + i18n keys present in all 8 locales
- [ ] Pre-commit hooks pass on the final tree (`git commit` dry-run / husky run)

Report the matrix in your final summary; fix everything red before finishing.
