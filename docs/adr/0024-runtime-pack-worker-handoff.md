# ADR 0024: Runtime-pack worker handoff end-to-end

## Status

Accepted (execution plan, 2026-09-26). Work not started. This ADR freezes the order + the Step-2 wire contract so Steps 1-2 (Rust) and 3-4 (Python) can run as disjoint parallel slices.

## Context

Goal (master plan `docs/plan-runtime-pack-split.md`): slim core (UI, recording, hotkeys, IPC, no ML imports) + worker/pack (ONNX, Parakeet, Qwen, VAD, ~180 MB pack). Slim core never touches ML code; it asks the worker "transcribe this" over a connection.

Trap: slimming the Nuitka build now (excluding onnxruntime/ctranslate2) while the app still imports ML in-process = `ModuleNotFoundError` on launch / silent VAD death. Slimming is gated on the runtime handoff working first.

## Decision

Order is fixed:

1. Wire the runtime handoff (Steps 0-6 below), verify on a real machine.
2. Only then slim the build (Step 7).
3. Then `.nsi` pack checkbox + full-offline installer.

Step-2 wire contract (frozen before Step 3, E9): host pushes `worker_started {pid:int, version:str, port:int}` to the slim-core sidecar over the existing host<->sidecar WS hop. `port` is additive to the current `{pid, version}` shape (`voice_typer/server/event_bus.py:181`); event name already in all allowlists, no allowlist churn. Slim core connects `ws://127.0.0.1:<port>` as WS client.

Placement rules: `main.rs` stays wiring-only (C-ARCH-1) — new module for worker init (parallel to `initialize_sidecar`); no `block_on` on runtime workers (C-TOKIO-1); worker hop reuses C-WS lessons (str/TEXT frames, numeric id echo, generation-stamped respawn); `[WORKER]` log lines carry `format_duration()` suffixes (C-LOG-2).

## Consequences

Positive: smaller installer unlocked safely; worker crash independent of sidecar breaker (§7.2); queued `transcribe_offline` actually completes.
Negative: new second WS hop to own (auth, heartbeat, reconnect/backoff); real-machine verification mandatory (frozen worker exe can't be faked in unit tests).
Neutral: `worker_started` payload gains `port`; new module `voice_typer/server/worker_client.py`; long-lived worker policy (§7.3).

## Verified state (2026-09-26, code, not claims)

Built:

- Worker Python side: `voice_typer/worker/_ws_server.py` (`transcribe_offline` dispatch → thread → `transcribe_offline_result`), `_transcribe.py` (lazy cached ASR), `_auth.py`, `_single_instance.py`, `__main__.py`. Handshake `{"event":"worker_started","port":N}` on stdout.
- Rust spawn: `src-tauri/src/sidecar/spawn.rs:198` `spawn_worker_and_get_port_with_shutdown`, `:219` `initialize_worker`; `src-tauri/src/sidecar/spawn/worker.rs` `spawn_worker_release` / `spawn_worker_dev_mode` + `:170` `on_pack_verified` trigger (stop-first, then `initialize_worker`; skips quietly when binary missing/quitting).
- `WorkerState` managed `src-tauri/src/main.rs:121`; worker shutdown wired `src-tauri/src/sidecar/lifecycle.rs:157`.
- IPC surface in all allowlists + parity tests; pack downloader, launch existence check, degradation matrix (`voice_typer/server/ipc/lifecycle.py:368` stub ack: `queued:False+degraded` when pack missing, `queued:True` otherwise), early-transcribe queue (`mark_ready`) live.

Missing (the gap this ADR closes):

- `main.rs:213` setup spawns only `initialize_sidecar_guarded`; worker start fires solely via the `offline_pack_verified` → `on_pack_verified` path. No cold-start call, no supervisor/respawn yet (`initialize_worker` success path logs "WS client + respawn supervisor are the next phase", `spawn.rs:257`).
- Port-relay hole: only Rust sees the worker port (stdout). `worker_started` on the sidecar bus is `{pid, version}` with no `port` (`event_bus.py:181`); `worker_port`/`worker_client` = zero hits in `voice_typer/server`. No mechanism for the sidecar to learn the port.
- Slim-core sidecar has no WS client to the worker (`_handle_transcribe_offline` acks only, `lifecycle.py:368` docstring says so explicitly).

Correction to the session note: `main.rs` is NOT "zero worker references" — `WorkerState` manage + worker shutdown are wired; what is missing is the spawn trigger outside the pack-verified path + the port relay + the WS client + the real handler.

## Current status (2026-09-26, reverified)

- Wire handoff — code done, verification open. Steps 1–5 merged (see progress below); Steps 0/6 need a real machine (checklist + preflight script ready): record→transcribe through worker, kill→respawn, pack removed → degraded.
- Slim build — not started (draft only, correctly). In-process ML is still live (14 import sites: `vad.py`, `transcription.py`, `parakeet_engine/_load.py`, `qwen_onnx_model.py`, `asr_utils.py`, `gtcrn_backend.py`, `resource_probe.py`, `transcription_device.py`, `transcription_fallback.py`), so the slim-now trap is still armed: excluding the libs = `ModuleNotFoundError`.
- `.nsi` checkbox + full-offline + Step 8 publish job — drafted, not implemented. No `pack-<version>.zip` / `pack-manifest.json` artifact exists in Releases and no CI job publishes one, so the silent always-on downloader currently has nothing to fetch (users get `offline_pack_missing` + degraded transcription).

## Execution plan + progress

- [ ] Step 0 — Baseline (before touching anything): run app as-is, record→transcribe via in-process path, save log excerpt as regression reference. STILL OPEN (needs host run).
- [x] Step 1 — Rust wiring (done 2026-09-26): `src-tauri/src/sidecar/worker_init.rs` (`initialize_worker_cold_start` + `initialize_worker_guarded`), `spawn/worker.rs` `should_start_worker`/`start_worker_if_ready`, `main.rs:214-219` one spawn line (246 lines, wiring-only). `cargo check` Finished exit 0; `cargo test worker` 61 passed.
- [x] Step 2 — Port relay (done 2026-09-26, contract frozen): `worker_started {pid,version,port}` host→sidecar; `voice_typer/server/worker_relay.py` single store + `get_worker_port()`; `docs/code-notes/worker-port-relay.md`. `tests/test_worker_relay.py` 28/28.
- [x] Step 3 — Sidecar WS client (done 2026-09-26): `voice_typer/server/worker_client.py` (`WorkerClient`, auth/heartbeat/result-route/generation guard). `tests/test_worker_client.py` 24/24.
- [x] Step 4 — Real handler (done 2026-09-26): `lifecycle.py` forwarder + `voice_typer/server/worker_pending.py` canonical queue (cap 64). `tests/test_transcribe_offline_forward.py` 18/18. NOTE: `make_ipc_server_with_fakes()` constructs fine; the "registry drift" report was refuted (handlers live in `handlers/` mixins, all wired in `IPCServer` bases).
- [x] Step 5 — Lifecycle policy (done 2026-09-26): `src-tauri/src/sidecar/worker_supervisor.rs` backoff engine + exit watcher; `docs/code-notes/worker-lifecycle-policy.md`; `cargo test worker_supervisor` 9/9.
- [ ] Step 6 — Real-machine verification (harness delivered, RUN OPEN): `docs/adr/0024-step6-verification-checklist.md` + `scripts/verify_worker_handoff_preflight.py` (7/7). NEEDS HOST RUN: record→transcribe through worker, kill→respawn, pack-removed degradation.
- [ ] Step 7 — Slim the build ONLY after Step 6 verifies (draft only): `docs/adr/0024-step7-slim-gate-draft.md`. NOT APPLIED — slimming now = ModuleNotFoundError crash.
- [ ] Step 8 — CI publish job (drafted 2026-09-26; implement after Step 7 green): a NEW workflow (e.g. `.github/workflows/runtime-pack-publish.yml`) that freezes the worker per triple (`scripts/build/build_worker_*.sh`), zips `lausu-runtime-pack-<pack_version>-<triple>.zip`, writes `pack-manifest.json` (`{version, sha256, ...}` per `OfflinePackManifest`), and uploads both via `scripts/release/publish_pack_release.py` (canonical naming from `scripts/build/artifact_names.py`; signing stays in CI per C-CI-11). The manifest lands at `.../releases/latest/download/pack-manifest.json` — the exact URL the silent always-on downloader (`startup_tasks.py:150`, `update_check.py:256`) already polls. Constraints: manual `workflow_dispatch` only (C-CI-4: no push/PR triggers until Phase 0-W passes), generous `timeout-minutes` (Nuitka worker freeze is C-compilation-bound, cf. C-CI-3), NEVER edit the `tauri-*.yml` orchestrator/build files for this (C-CI-2). Acceptance: publisher dry-run green, manifest schema test, download-install-verify round-trip on a real machine against a draft release. Only then does the Step-7 `.nsi` checkbox + full-offline artifact mean anything (checkbox without a published pack = dead UI).

Execution shape: Steps 1-2 (Rust) and 3-4 (Python) are disjoint once the Step-2 contract above is frozen — two parallel sub-agents, then joint Step 5/6. Tests per step (E6).

## References

- Master plan: `docs/plan-runtime-pack-split.md` (§7 worker IPC, §7.2 breaker, §7.3 lifecycle, §7.4 events, §8.10 degradation, §11.5 size gate).
- Guards: C-ARCH-1 (main.rs wiring-only), C-TOKIO-1 (no `block_on`), C-WS-1/2/3 (wire discipline), C-LOG-2 (duration suffix), C-DATA-1 (pack download allowed egress), E9 (frozen wire shape), E6 (tests per step).
