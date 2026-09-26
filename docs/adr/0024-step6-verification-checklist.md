# ADR-0024 Step 6 — real-machine verification checklist

> STATUS: NEEDS HOST RUN. Static preflight
> (`python scripts/verify_worker_handoff_preflight.py`, 5/5 PASS in the
> sandbox) proves the wiring exists, not that the frozen worker exe
> spawns, transcribes, and respawns on a real machine. Do NOT mark Step
> 6 done until every row below has a pasted log excerpt.

Prereqs: pack downloaded + verified ( triggers the worker start), dev
launch `VOICE_TYPER_SIDECAR_DEV=1` (dev worker = `python -m
voice_typer.worker` via `spawn_worker_dev_mode`), or a release build
for the frozen-exe path. One row per run; record dev vs release.

## (a) Record → transcribe → text through the worker

1. Dictate a short sentence with an offline model selected.
2. Expect: `transcribe_offline {queued:True}` ack, then a
   `transcribe_offline_result {text, latency_ms}` push whose text
   matches the dictation.
3. Paste: `[WORKER-INIT] worker spawned (port=…) …` + the result push.

## (b) Kill worker mid-idle → auto-respawn

1. Read the worker pid from the `worker_started` log line.
2. `taskkill /PID <pid> /F` (Windows) / `kill <pid>` (POSIX) while idle.
3. Expect: `[WORKER] worker exited (code=…, signal=…): respawning`,
   then `[WORKER] respawn attempt 1 of 5 after 500ms`, then
   `[WORKER] respawn succeeded on attempt 1 (port=…) …s`.
4. Repeat-kill 6× in a row to observe the doubling 500→1000→2000→
   4000→8000 schedule and the `backoff exhausted` line (worker left
   stopped, sidecar unaffected, no app relaunch).
5. Paste all `[WORKER]` lines.

## (c) Remove pack → degraded response

1. Stop the app, rename the pack dir aside, relaunch.
2. Dictate offline. Expect: `queued:False + degraded:True + reason:
   offline_pack_missing` (the `lifecycle.py:368` matrix) and the
   renderer "offline engine unavailable" state — never a silent queue.
3. Restore the pack dir, relaunch, confirm (a) works again.

## (d) `[WORKER]` lines + duration suffixes (C-LOG-2)

1. Grep the session log: `Select-String '\[WORKER' lausu.log`.
2. Expect: every completion line ends with ` 2.3s` / ` 1m 2.3s`
   (single leading space, `format_duration()` shape); no `WARNING`
   label, no millis, no per-line session id (C-LOG-1).
3. Paste the grep output.

## Sign-off

- [ ] (a) text matches dictation (dev / release: ___)
- [ ] (b) auto-respawn + backoff + exhaustion observed
- [ ] (c) degraded response, no silent queue; recovery after restore
- [ ] (d) `[WORKER]` grep pasted, durations well-formed
- [ ] No `supervisor_failed` / app relaunch during (b) (breaker
      independence: worker exhaustion must not relaunch the app)

Only then flip ADR-0024 Step 6 to `[x]` with the log excerpts.
