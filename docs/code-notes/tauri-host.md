# Tauri host code notes

Deep lasting contracts that no longer fit as multi-paragraph module comments.
Anchors referenced from source stay short (`C-*`, `SEC-*`, `RACE-*`, `PERF-*`).

## Logging format (C-LOG-1)

FILE lines: `YYYY-MM-DD  HH:MM:SS  LEVEL  msg` — two spaces after the date,
seconds-only, short level labels (`WARN` not `WARNING`), no `T`/timezone,
no thread/module path. The only per-session id is the trailing
`session=` on the first `[STARTUP] logging initialized:` banner line.

TERMINAL lines: `HH:MM:SS  LEVEL  msg` — time only; the date lives in the
file. Both sinks build from a single `now_timestamps()` clock read so one
record cannot straddle a second boundary.

The Rust formatters live in `util.rs` / `platform/logging/*`. Python twins
are `voice_typer/server/log/formatters.py`. Pinned by
`src-tauri/src/util_tests.rs` (`test_now_timestamp_format`) and
`src-tauri/src/platform/logging_tests.rs`.

## Logging rotation tiers (ADR-0020 §11)

Mirrors `voice_typer/server/_log_constants.py`:

1. Age retention: delete logs older than `LOG_AGE_RETENTION_SECS` (7d) at
   session start.
2. Size fallback: delete logs larger than `LOG_SIZE_FALLBACK_BYTES` (25 MB)
   at session start.
3. Mid-session ceiling: `LOG_MAX_BYTES` (40 MB) — truncate in place; no
   numbered backups.

`bubble_level` (~60 Hz) is excluded from the file sink by prefix
(`[WS-READER] bubble_level event`) for Info+ only; Error/Warn are preserved.

## Shutdown ack timeouts

- `SHUTDOWN_ACK_TIMEOUT_MS` (2s): renderer-invoked `shutdown_sidecar`.
  UI is alive; a long block freezes it.
- `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s): `RunEvent::Exit` →
  `on_host_exit` → `shutdown_sidecar_for_exit`. Last-resort teardown;
  sidecar history/WAL/crash-recovery flush + native hotkey teardown can
  legitimately take ~30s on a cold disk. A 2s budget force-killed mid-cleanup
  and could leave WAL uncheckpointed.

## Dispatch timeouts

- `DISPATCH_TIMEOUT_SECS` (120): model lifecycle (download/import/delete/
  cancel/pause/resume).
- `DISPATCH_DOWNLOAD_TIMEOUT_SECS` (3600): multi-GB model download/import.
- `DISPATCH_SHORT_TIMEOUT_SECS` (15): every other command.

Routing lives in `commands/sidecar_cmds/dispatch.rs`.

## Bubble coalesce interval

`bubble_coalesce_should_emit` uses `Duration::from_nanos(1_000_000_000 / hz)`.
The old `from_millis(1000 / hz)` integer-divided to 0 for `hz > 1000`,
silently disabling coalescing. At the default `BUBBLE_LEVEL_COALESCE_HZ = 30`
the interval is 33.333 ms (60 Hz in → every other event → 30 Hz out).

## Toggle rate limiter encoding

`LAST_TOGGLE: Mutex<Option<u64>>` stores nanoseconds since a process-lifetime
`OnceLock<Instant>` anchor. `None` = never toggled; `Some(0)` is a real
anchored-at-zero timestamp. An `AtomicU64` + `0` sentinel collided with that
Windows QueryPerformanceCounter case and let the second rapid toggle through.
`Instant` (not `SystemTime`) closes the NTP-step bypass.

## Sidecar handle Drop safety net

`SidecarHandle::ShellPlugin(Option<CommandChild>)` wraps in `Option` because
`CommandChild::kill` consumes `self`. Drop takes the inner child and calls
`kill()` only — never the recursive tree walk (blocking `taskkill /T` /
`pgrep` can stall a Tokio worker). Release spawn already registers
`kill_on_parent_exit`; Drop is the in-process fallback. DevMode relies on
tokio `kill_on_drop(true)`.

## Plugin config contract (C-TAURI-2)

`tauri.conf.json` `plugins` block (Tauri v2, verified against plugins-workspace
+ tauri#8769):

- `single-instance` / `notification` / `dialog`: must be `null` (serde unit).
  `{}` crashes startup.
- `shell`: only `{ "open": bool }`. A v1 `{sidecar, scope}` block crashes.

Sidecar spawn scoping is owned by the Rust host via `app.shell().sidecar(...)`.
CI builds but never launches, so these shapes are easy to reintroduce.

## Dispatch arg shape (C-TAURI-3)

Renderer contract is exactly `invoke('dispatch', { cmd, data })`. The Rust
`dispatch` command keeps FLAT params `(cmd: String, data: Option<Value>)`.
A struct-typed `args` param makes the host expect a top-level `args` key and
every invoke fails with `missing required key args`.

## WS handshake order (C-WS-1)

Post-auth frames must be: `auth_ok`/`ready` first (Rust reader accepts only
those as first frame), then subscriber install, then `ready` if first, then
the `state_changed` snapshot. Snapshot-before-ready races in as first frame
and every handshake dies with `WS auth unexpected frame type: state_changed`.

## WS frame type (C-WS-2)

Sidecar→host frames MUST be `str` (TEXT opcode). `bytes` maps to BINARY and
the host reader drops them. Every dispatch response echoes a numeric `id`.

## Respawn generation (C-WS-3)

`trigger_respawn_off_thread` carries `expected_generation`; dequeue-time
re-check drops stale requests so a cleanup that decided "connection died"
cannot kill a newer healthy reconnect. Only heartbeat-liveness and auth-failure
paths may pass `None`.

## block_on bridges (C-TOKIO-1)

`tauri::async_runtime::block_on` is legal only on non-runtime threads
(dedicated std threads in `respawn_scheduler.rs`, `heartbeat.rs`,
`state.rs::on_host_exit`). Inside spawned tasks / async commands use `.await`
or `spawn_blocking`. Panic capture on the sidecar init task is
`AssertUnwindSafe(fut).catch_unwind().await`.

## Window guards (SEC-026)

`commands::require_main_window` / `require_bubble_window` gate every
user-defined `#[tauri::command]` that a compromised renderer could invoke.
Tauri capabilities only cover plugin commands. Error envelope matches the
sidecar WS error shape so the renderer reject path is shared.

## Constants source-inspection tests

`util.rs` holds the shared constants block in-place (not a `consts` submodule)
because `tests/tauri/mig15|16|17` regex the raw `pub(crate) const` declarations
in this file. Relocating them regresses those tests without a coordinated
update. `ALLOWED_EVENT_TYPES` in `sidecar/ws/event_protocol.rs` is likewise
parsed by Python source-inspection tests — do not reformat that slice literal.

## Env markers (startup timeline)

Cross-language contract with `voice_typer/server/startup_timeline.py`:

- `VOICE_TYPER_BOOT_EPOCH_MS` — host process start (first statement in `main`).
- `VOICE_TYPER_SPAWN_EPOCH_MS` — immediately before each sidecar spawn.

Values are decimal epoch milliseconds as strings. Absent markers mean "skip
the timeline line" (standalone launches).

## WorkerState (runtime-pack split)

A SECOND spawned child (ML worker exe) with its own state struct so worker
respawn never trips the sidecar circuit breaker and vice versa. The slim-core
sidecar — not the Tauri host — is the worker's WS client. Worker auth token is
a process-lifetime `OnceLock` (`VOICE_TYPER_WORKER_TOKEN`); the sidecar reuses
it across worker respawns.

## Autostart / hidden launch (C-BG-1)

Direct-binary flags (`--hidden`, `--delay N`) are parsed in `main` before the
builder. `VT_START_HIDDEN=1` makes `window_bootstrap` hide + skip_taskbar so
a background autostart does not flash the window or activate the mic
indicator. Delay is a plain `std::thread::sleep` in `main` (C-TOKIO-1: never
on a runtime worker).

## Module layout (orchestrator files)

These files are re-export surfaces only; logic lives in the submodules:

- `commands/sidecar_cmds.rs` → allowlist, dispatch, restart, shutdown,
  window_close.
- `commands/system_cmds.rs` → dialog_titles, dialogs, export, heartbeat,
  locale, redaction, renderer_log, stats_image.
- `platform/logging/mod.rs` → combined, early, init, panic_hook, redact,
  rotating. Sibling tests: `platform/logging_tests.rs`.
- `sidecar/mod.rs` → bubble_coalesce, child_log, handle, lifecycle, shutdown,
  spawn, supervisor, ws. Sibling tests wired per-module (not all here:
  `spawn_tests` is declared inside `spawn.rs` so `super::*` resolves).

`state.rs` re-exports `SidecarHandle`, lifecycle callbacks, and test-gated
`shutdown_sidecar_for_exit` so historical `crate::state::*` paths keep
resolving (create-first split, AGENTS.md E1).

## Fire-and-forget id0

`dispatch_fire_and_forget` sends `{"type",data,"id":0}` via `ws_tx.try_send`
with NO pending entry and no await. Used only by bubble `toggle_dictation`
(the sole SEC-026 dispatch-allowlist bypass: fixed command name built in Rust).

The Python sidecar treats `id=0` like any other request and may echo a response.
The Rust WS reader finds no pending entry and DEBUG-drops the frame
(`RX response id=0 had NO pending entry`) — no WARN. Mechanism is
reader-side drop, not server-side suppress.

## Pending-entry Drop guard

`PendingEntryGuard` removes `state.pending[id]` when a dispatch future is
cancelled mid-await (e.g. heartbeat's outer 15s timeout drops the inner
future before any explicit remove path runs). Double-remove is a HashMap
no-op (ids unique via `next_id.fetch_add`).

C-TOKIO-1: `Drop` is sync and may run on a runtime worker; the guard uses
`tauri::async_runtime::spawn` (submit-only, detached JoinHandle) — never
`block_on` / `blocking_lock` on the AsyncMutex. Tests poll for the async
removal with a bounded deadline.

## Dispatch frame

`dispatch_frame` builds the WS frame, inserts a pending oneshot, sends, awaits.
Invariants:
- Cap data payload before pending insert / writer enqueue
  (`DISPATCH_DATA_MAX_BYTES` = `MAX_FRAME_BYTES` − `ENVELOPE_HEADROOM_BYTES`).
- Check `ws_tx` once before pending insert (None must not leak an entry).
- Serialize data once into a `Cow<str>` for size check + frame body.
- Send-failure and timeout remove the pending entry; Drop guard covers cancel.
- `type:"error"` responses become `VoiceTyperError::server_from_data` so the
  renderer invoke rejects with the sidecar envelope intact.

## Bubble window commands

Most bubble commands are intentionally not `require_main_window`-gated: the
sandboxed bubble may self-manage visibility/position/drag/resize (SEC-026).
SEC-016 gates (`require_bubble_window`) apply only to `bubble_signal_ready`,
`bubble_hide_complete`, and `bubble_dismiss` — spoofing those from another
window would break readiness/hide UX.

`bubble_toggle_dictation` is the sole dispatch-allowlist bypass (fixed command
+ fire-and-forget + 500ms rate limit). `bubble_move_by` uses `spawn_blocking`
because OS-IPC at ~60 Hz mousemove must not pin an async worker.
Resize clamps to predecessor pill MIN/MAX (cross-host consistency + SEC-016
phishing-overlay bound).

## Supervisor respawn

`respawn` serializes via `respawn_in_progress`, runs `respawn_inner` under
`AssertUnwindSafe(...).catch_unwind()` so a panic always clears the flag.

`respawn_inner`: cancellable backoff (`select!` sleep vs `shutdown_notify`),
kill-old-child before spawn (ShellPlugin Drop does not kill the OS process),
atomic child install under `state.child` lock with shutting_down re-check
inside the lock. Counter increment lives ONLY on the exhaustion path
immediately before `app.restart()` so the breaker trips on the 3rd actual
relaunch. Every return path clears `respawn_in_progress`. Disk I/O for the
restart counter goes through `spawn_blocking` (C-TOKIO-1).

User-initiated tray Restart calls `clear_restart_counter_for_user_restart`
only — never wire that into supervisor exhaustion (defeats the breaker).
Adopted-backend (MO-110) and power-suspend (MO-126) paths disable respawn.

## Sidecar spawn

`initialize_sidecar` (cold start) + `spawn_sidecar_and_get_port_with_shutdown`
(supervisor + cold start). Adopted-backend mode (VT_PYTHON_PORT/VT_IPC_TOKEN)
attaches without spawning; no child handle; no respawn fallback.
Post-spawn shutting_down re-check kills a child that would outlive the host.
`initialize_sidecar_guarded` (C-ARCH-1) is the panic-captured body main.rs
spawns; C-TOKIO-1: catch_unwind on the future, never block_on.

## Migration

One-time predecessor userData → Tauri config_dir (ADR-0020 §8).
Idempotent via `.migrated-from-legacy` sentinel written ONLY when all
critical steps succeed (model copy failures also defer the sentinel).
Probes legacy userData candidates; skips a candidate equal to the Tauri
target. models/: copy-absent only; history.db: copy-if-absent (no
append); config.json: newest-mtime-wins merge. Never panics.
Runs on spawn_blocking before sidecar spawn.

## Config dir

`config_dir()` matches Python `_paths.config_dir()` byte-for-byte via
env vars — NOT Tauri `app_config_dir()` (bundle-id path). APP_SLUG is
the machine-readable leaf name, distinct from APP_NAME display string.
Cached with OnceLock; `config_dir_from_env` stays pure for tests.
Windows reads USERPROFILE first so legacy `~/.voice-typer` probes work.
TAURI_SIDECAR=1 disables the Python Win32 single-instance mutex.

## Kill-on-parent-exit

Release `CommandChild::Drop` does not kill the OS process. Windows uses
a process-wide Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
POSIX uses a detached `/bin/sh` reaper polling `kill -0 <parent_pid>`
every 1s then SIGKILL to the sidecar. Not PR_SET_PDEATHSIG because
Tauri externalBin has no pre_exec hook. Best-effort: spawn continues
on registration failure.

## WS reader cleanup

Reader body is catch_unwind-wrapped. Cleanup (clear ws_tx, drain pending,
emit supervisor_relaunching, trigger respawn) runs unconditionally after
exit/panic but is generation-gated (C-WS-3): if `ws_generation` changed,
a newer reconnect owns recovery and this cleanup is a no-op.
`trigger_respawn_off_thread` receives `Some(my_generation)`.
Heartbeat-liveness respawn is the one path allowed to pass `None`.

## Heartbeat

10s `heartbeat` dispatch loop; N consecutive misses (error/timeout/panic)
→ supervisor respawn via `trigger_respawn_off_thread` with generation None.
Handle lock held across take+spawn+store to prevent interleaved reconnects
from leaking tasks. `abort_heartbeat` is shared by both shutdown paths.
15s outer timeout cancels dispatch_inner; PendingEntryGuard cleans pending.
