# ADR-0024 Steps 5–7 prep — progress record

> The parent file `docs/adr/0024-runtime-pack-worker-handoff.md` was
> present at slice start (read 2026-09-26, 64 lines) but is absent from
> the tree at write time (untracked input, removed by another actor —
> not restored here per E18 / slice ownership). This record holds the
> exact checkbox updates + evidence that belong in its
> `## Execution plan + progress` section. Re-apply on the parent file
> if it returns.

- [x] Step 5 — Lifecycle policy: long-lived worker (§7.3); crash →
  Rust respawn via supervisor fields, independent of sidecar breaker
  (§7.2). VERIFIED 2026-09-26:
  `src-tauri/src/sidecar/worker_supervisor.rs` (`respawn_worker`
  backoff engine on the shared `SUPERVISOR_BACKOFF_MS` schedule,
  `spawn_worker_exit_watcher` with release exit-channel + dev
  `try_wait` probe arms, C-WS-3 generation re-check, no counter file /
  no `app.restart`); `spawn.rs::initialize_worker` spawns the watcher
  + duration-suffixed success log; `state.rs` `ws_generation` /
  `shutdown_notify` dead-code gates removed (still stubbed: `ws_tx` /
  `pending` / `next_id` / `heartbeat_handle` for the Step 3 bridge);
  `worker_unloaded` semantics untouched (`event_bus.py:187`); policy
  in `docs/code-notes/worker-lifecycle-policy.md`. Evidence:
  `cargo check` clean + `cargo test worker_supervisor` 9/9 +
  neighbors (spawn 64, supervisor 39, lifecycle 8) green.
- [ ] Step 6 — Real-machine verification (mandatory). HARNESS
  DELIVERED, NEEDS HOST RUN 2026-09-26:
  `docs/adr/0024-step6-verification-checklist.md` (rows a–d with
  expected log lines + sign-off) + runnable static preflight
  `scripts/verify_worker_handoff_preflight.py` (5/5 PASS in sandbox)
  + `tests/test_worker_handoff_preflight.py` 7/7. Box stays unchecked
  until host log excerpts land.
- [ ] Step 7 — Slim the build ONLY after Steps 1-6 verify. PREP ONLY
  (TODO, NOT APPLIED) 2026-09-26: draft gate + YAML sketch +
  files-to-touch in `docs/adr/0024-step7-slim-gate-draft.md`
  (anchors: `tauri-windows-build.yml:508-510` Write-Host lines,
  `:554` informational 45 MB gate,
  `test_config_script_drift.py::TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed`
  per plan §11.2). No workflow / Nuitka / `.nsi` file touched.

Stale-notice for the trigger owner (not touched — other slice owns
`spawn/worker.rs`): the `on_pack_verified` docstring ("no supervisor
yet", `worker.rs:294-298` at slice start) is stale now that the
Step 5 engine exists.
