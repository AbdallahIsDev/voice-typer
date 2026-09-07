# Electron Decommission Checklist — Gated on T-1 Host Validation

**Status**: planning artifact. **Nothing in this document may be executed
until T-1's Windows-host validation completes** (browser-driven visual
walkthrough + real-model dictation on the Tauri host — see `review.md` T-1
and the cutover criteria in `docs/migration/cutover-playbook.md`). This
checklist exists so the future session executes a written plan instead of
re-deriving one (E15 discipline: deletions recorded in
`archive/deleted_files.txt`, one operation per line).

**Direction** (ADR-0020): the Electron shell is being removed; Tauri becomes
the main — and only — runtime. Electron is a behavioral REFERENCE during the
migration, nothing more.

**Two crash-loop breaker files must NOT be merged** (C-PERSIST-4):
`restart_history.json` (Electron-only, `client/src/main/python/relaunch-app.ts`)
and `restart_counter.json` (Tauri-only, `src-tauri/src/sidecar/supervisor.rs`)
have incompatible schemas and lifecycles. Each dies with its own shell.

---

## Phase A — Pre-deletion extraction (do BEFORE deleting anything)

These steps are sequenced first because deleting the modules they extract
from would break the live Tauri path. Each is create-first (E1): the new
runtime-neutral module lands and is verified before the old module is
removed.

- [ ] **A1. Extract the launcher-build helpers from
  `voice_typer/server/_electron_build.py`.** Three LIVE Tauri-path modules
  import from it and would break with the module's deletion:
  - `autostart/tauri_spawn.py:21-25` imports `_launcher_child_env`,
    `_log_sensitive_env_keys`, `_spawn_flags`
  - `autostart/focus.py:14-18` imports the same three
  - `autostart/log_files.py` imports `_log_sensitive_env_keys` (verify exact
    symbol list before moving; the historical BP-22 finding initially
    mis-cited the names)
  - Move the helpers into a runtime-neutral module (e.g.
    `voice_typer/server/launcher_process.py`), keep re-exports in
    `_electron_build.py` until Phase B deletes it, and add a focused test
    importing the new module directly.
- [ ] **A2. Extract the live single-instance symbols.** The Python
  single-instance subsystem (`single_instance.py` +
  `_security_attributes.py` + `security/win32_dacl.py`, ~1,176 LOC) is
  gated OFF in Tauri-WS mode (`ipc/entrypoint.py`:
  `None if _tauri_sidecar else _ensure_single_instance(...)`), but three
  symbols stay LIVE on the Tauri path and are consumed by
  `app_lifecycle.py`, `shutdown/cleanup.py`, `shutdown/lifecycle.py`,
  `session_state.py`, `autostart/pid_file.py`, and others:
  - `_is_pid_alive`
  - `_backend_pid_file`
  - `_clear_backend_pid_file`
  (NOT `_write_backend_pid_file` — it is gated off with the subsystem.)
  Extract them to a small module (e.g. `voice_typer/server/backend_pid.py`)
  with re-exports in `single_instance.py` until Phase C. See review entry
  BP-144.
- [ ] **A3. Consolidate the console forwarder** (BP-105):
  `renderer-telemetry.ts` duplicates `windows/bubble/console-forwarder.ts`
  byte-for-byte. Land the shared helper and point both callers at it BEFORE
  any port/delete decision, so whichever side survives, the copy is single.
- [ ] **A4. Decide the logging-health disposition.** The main-process
  logging-health ring buffer (`logging/rotation.ts`, `getLoggingHealth`)
  is Electron-main-only, unported, and unwired (zero consumers). Choose
  explicitly: port it to the Tauri host (new IPC surface + Rust-side buffer)
  or delete it at cutover. Do not leave it undecided — an unwired port
  target is how dead code re-accumulates.

## Phase B — Port-or-drop decisions for unported Electron surfaces

Four Electron surfaces take LIVE features with them. For each: port to the
Tauri host (with tests) or accept the drop as a product decision (recorded
in `WONT_FIX.md`-style rationale). None may be deleted silently.

- [ ] **B1. Power suspend/resume recycling** —
  `client/src/main/power.ts` (powerMonitor suspend/resume → backend
  recycling). No Tauri counterpart exists yet.
- [ ] **B2. Stats image save/copy/reveal** —
  `client/src/main/ipc/stats-image-handlers.ts`. Absent from
  `src-tauri/` and from the tauri-bridge namespaces; renderer degrades
  silently on Tauri.
- [ ] **B3. Renderer-initiated "Lost connection" restart escalation** —
  `client/src/main/ipc/backend-restart-handler.ts` +
  `client/src/main/python/restart-backend.ts`.
  `WindowBridge.restartBackend` is optional and unimplemented on Tauri.
- [ ] **B4. The Ctrl/Cmd+Shift+D dismiss accelerator** —
  `client/src/main/shortcuts/global-shortcuts.ts`.

## Phase C — Deletion order (execute only after T-1 gate passes)

Ordered so that each step is independently verifiable (tests green,
`cargo check` + `npm run typecheck:ci` + `pytest --collect-only` after
every step; wiring audit per E1). Enumerate the affected test files per
item BEFORE deleting — do not discover them via red CI.

1. [ ] **C1. Electron main tree** — delete `voice_typer/client/src/main/`
   (except files Phase B decided to port — port first, then delete the
   originals), plus the renderer's Electron-only preload/bridge glue.
   Includes `main/index.ts`, `main/bootstrap/*`, `main/windows/*`,
   `main/python/*` (the Electron spawn/restart/relaunch machinery — the
   Tauri host owns its own), `main/ipc/*`, `main/logging/*` (after A3/A4),
   `main/shortcuts/*`, `power.ts`, `allowed-commands.ts` (the renderer
   allowlist parity source moves to the Tauri-side allowlist contract —
   keep the parity test green against `registry.py`).
2. [ ] **C2. Python Electron-support modules** — delete
   `voice_typer/server/electron_launcher.py` and
   `voice_typer/server/_electron_build.py` (A1 must be done), plus
   `autostart/electron_spawn.py`; sweep the Electron-branch residue the
   BP-22 enrichment verified:
   - `app_lifecycle.py` "STANDALONE IN-PLACE RESTART" branch
     (`_electron_pid is not None` gate — always None in Tauri-WS mode)
     and its `_electron_pid` plumbing
   - `autostart/focus.py` `VT_FOCUS_ONLY=1` writes (zero `src-tauri/src`
     readers — verified by grep)
   - `autostart.py` docstring still describing "npm run dev … Electron"
     spawn
   - `desktop_shortcut.py` docstrings citing the "Electron
     single-instance lock"
   - `_phases_late.py` "opening Electron window" log string
3. [ ] **C3. `transport_tcp.py`** — delete
   `voice_typer/server/ipc/transport_tcp.py` ONLY after WS parity is
   confirmed on the Tauri host (T-1 evidence), together with its tests.
   The Electron TCP transport and the WS transport do not coexist after
   the shell dies.
4. [ ] **C4. Python single-instance subsystem** — delete
   `single_instance.py`, `_security_attributes.py`,
   `security/win32_dacl.py` (A2 must be done; BP-144). Re-exports removed
   with the module.
5. [ ] **C5. Two re-export shims + remaining shims** — after C1–C4, delete
   the compatibility re-export shims whose consumers are all gone (sweep
   `grep` for each shim name; a shim with remaining live consumers stays
   until they are migrated).
6. [ ] **C6. `restart_history.json` handling** — delete the
   Electron-side writer/reader (`relaunch-app.ts` dies with C1's
   `main/python/` deletion). Do NOT touch `restart_counter.json`
   (Tauri-owned; C-PERSIST-4).

## Phase D — Post-deletion verification

- [ ] Full-suite green on the final code state (pytest + vitest + cargo,
  C-TEST-6), recorded in `worklog.md` with counts + OS qualifier.
- [ ] `scripts/check_branding.py`, `sync_versions.py --check`, and the
  IPC parity test all still green (the allowlist contract survives C1).
- [ ] `archive/deleted_files.txt` lists every deleted/moved file
  (one `DELETE | <path>` per line).
- [ ] Installer/bundle sizes re-checked (Electron removal shrinks the
  client payload; `electron-builder.yml` and its CI legs retire with it —
  any `build.yml` edit follows the C-CI-2 constraint protocol:
  user-validated full re-run).
