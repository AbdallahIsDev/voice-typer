# Worker lifecycle policy (ADR-0024 Step 5, plan §7.2/§7.3)

Code: `src-tauri/src/sidecar/worker_supervisor.rs`. Tests:
`src-tauri/src/sidecar/worker_supervisor_tests.rs` (pure surface only;
the live engine needs the Step 6 host run).

## Crash → respawn (§7.2)

- Detection has two arms: release mode wakes on the drained exit
  channel (`Terminated`); dev mode (`spawn_worker_dev_mode` returns no
  channel) falls back to a 1s `try_wait` liveness probe. The probe only
  fires on observed death, never on unknown states.
- The watcher is spawned once per successful `initialize_worker`.
  Racers serialize on `respawn_in_progress`; a stale watcher degrades
  to a 1s poller until it observes the current generation's channel.
- Respawn runs the shared doubling backoff (`SUPERVISOR_BACKOFF_MS`,
  5 attempts, cancellable via `shutdown_notify`), kills the old child
  first (no orphan ~450 MB worker), and reinstalls child + drained
  exit channel under the same in-lock `shutting_down` re-check the
  sidecar supervisor uses.

## Independent breaker (§7.2)

- The worker engine touches no restart counter and never calls
  `app.restart()`. Exhaustion logs `[WORKER] backoff exhausted` and
  returns `Err`; the sidecar keeps running. A worker crash loop can
  neither trip nor reset the sidecar circuit breaker (separate
  `WorkerState` fields throughout).

## Long-lived worker (§7.3)

- No idle unload exists yet. `should_keep_worker_running` is the hook
  the future "Keep offline engine running" toggle threads through;
  identity today, so `worker_unloaded` (`event_bus.py:187`) semantics
  are unchanged.

## Wire discipline

- C-WS-3: `ws_generation` bumps on every successful (re)spawn;
  respawn requests carry the generation observed at death and skip
  when stale. The worker WS bridge (Step 3 slice) consumes
  `ws_tx`/`pending`/`next_id`/`heartbeat_handle`, still stubbed.
- C-LOG-2: every `[WORKER]` completion line carries a
  `format_duration()` suffix via the shared `lifecycle` helper.
- C-TOKIO-1: no `block_on`; sync entry spawns onto the runtime.

## Boundaries

- Cold-start spawn failure only logs: the start sequence
  (`start_worker_if_ready`) holds the restart slot across
  stop-then-init, so the engine must not re-enter it there.
- `on_pack_verified` trigger logic is untouched (other slice owns it);
  its "no supervisor yet" docstring goes stale with this change.
- `worker_client.py`, `_handle_transcribe_offline`, Nuitka flags, and
  the installer are out of scope (Steps 3/4/7 slices).
