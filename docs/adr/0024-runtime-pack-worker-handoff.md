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

## Execution plan + progress

- [ ] Step 0 — Baseline (before touching anything): run app as-is, record→transcribe via in-process path, save log excerpt as regression reference.
- [ ] Step 1 — Rust wiring: `WorkerState` already managed; add `initialize_worker` future parallel to `initialize_sidecar` in a new module (e.g. `src-tauri/src/sidecar/worker.rs`), keep `main.rs` wiring-only (C-ARCH-1). Trigger: pack dir + manifest verified (`platform/worker_path.rs`). Wire child-exit receiver + `shutdown_worker_for_exit` into exit path. C-TOKIO-1. Tests: `parse_worker_started` round-trip (exists, extend), init/trigger unit tests.
- [ ] Step 2 — Port relay (CONTRACT FIRST, frozen above): host pushes `worker_started {pid, version, port}` to sidecar over existing host↔sidecar WS hop. Test: relay round-trip.
- [ ] Step 3 — Sidecar WS client: new `voice_typer/server/worker_client.py` (`ws://127.0.0.1:<port>`, token auth via `ipc/auth.py` `compare_digest` pattern, frame forwarding, results into event bus → renderer, heartbeat + reconnect/backoff, str/TEXT, numeric id echo, generation-stamped respawn per C-WS-3). Tests: unit with mocked socket (E6).
- [ ] Step 4 — Real handler: replace stub ack `lifecycle.py:399`; pack present + worker ready → forward `{audio_path, sample_rate, lang}`; keep degradation matrix for pack-missing; queue drain on `mark_ready` `ready`. Tests: forwarding via `make_ipc_server_with_fakes()`.
- [ ] Step 5 — Lifecycle policy: long-lived worker (§7.3); crash → Rust respawn via supervisor fields, independent of sidecar breaker (§7.2).
- [ ] Step 6 — Real-machine verification (mandatory): dev mode (`VOICE_TYPER_SIDECAR_DEV=1` → `spawn_worker_dev_mode`): record→transcribe→text through worker; kill worker mid-idle → auto-respawn; remove pack → degraded response; `[WORKER]` lines + durations (C-LOG-2).
- [ ] Step 7 — Slim the build ONLY after Steps 1-6 verify: exclude ML libs from sidecar Nuitka invocation; CI gate asserting zero ML imports in slim-core path (grep gate); size gate ≤185 MB; then `.nsi` checkbox + full-offline artifact. Slimming before Step 6 = guaranteed crash.

Execution shape: Steps 1-2 (Rust) and 3-4 (Python) are disjoint once the Step-2 contract above is frozen — two parallel sub-agents, then joint Step 5/6. Tests per step (E6).

## References

- Master plan: `docs/plan-runtime-pack-split.md` (§7 worker IPC, §7.2 breaker, §7.3 lifecycle, §7.4 events, §8.10 degradation, §11.5 size gate).
- Guards: C-ARCH-1 (main.rs wiring-only), C-TOKIO-1 (no `block_on`), C-WS-1/2/3 (wire discipline), C-LOG-2 (duration suffix), C-DATA-1 (pack download allowed egress), E9 (frozen wire shape), E6 (tests per step).
