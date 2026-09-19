# Backend code notes (relocated long-form rationale)

Deep lasting essays moved out of inline comments during comment-bloat
cleanup. Inline code keeps short WHY pointers here. Comment-only history;
behavior unchanged.

## History DB schema (`voice_typer/server/history_db_internals/schema.py`)

<a id="history-db-schema"></a>

- Module is free functions extracted from monolithic `history_db.py`;
  tests import `_MIGRATIONS` / `_CURRENT_SCHEMA_VERSION` via `history_db`.
- **V3 FTS5**: additive `IF NOT EXISTS` virtual table + triggers; backfill
  re-runnable; whole migration in explicit BEGIN/COMMIT so schema version
  only advances if every statement succeeds.
- **V4 at-rest encryption** (`text_is_encrypted`, design:
  `docs/adr/XZ-R11-04-at-rest-encryption.md`, cipher `_text_crypto.py`):
  - Migration is PLAIN (no embedded BEGIN) so the runner can reconcile
    partial prior state (fresh DB already has the column; older DB needs
    the ALTER).
  - Insert always writes plaintext + flag 0, then UPDATE to ciphertext.
  - FTS UPDATE/DELETE guards require `text_is_encrypted = 0` on both
    OLD and NEW: ciphertext tokens were never indexed; issuing FTS5
    delete for them corrupts the shadow table. Stale rowids are filtered
    by the JOIN back to `transcriptions`.
  - Triggers DROP+CREATE because pre-v4 defs are unguarded.
- **V5 CJK trigram FTS**: `unicode61` cannot index CJK substrings (one
  token per run). `tokenize='trigram'` enables O(match) substring search.
  Router (`search.py`): CJK/fullwidth queries length >= 3 → trigram
  phrase MATCH; 1–2 char queries stay on LIKE. Whole capped query is one
  FTS phrase. External content, no text duplication. GDPR rebuild/
  optimize sites must keep BOTH indexes in lockstep.
- Trigram needs SQLite >= 3.34.0; `sqlite_supports_trigram()` gates the
  migration and consumers degrade when missing.
- **Migration runner**: `executescript` + explicit BEGIN/COMMIT for plain
  migrations; trigger-bearing SQL (V3+) carries its own transaction and
  must not be split on `;`. Plain ALTER migrations pre-filter columns
  already present (partial-prior-state). Version is persisted only after
  full success; on `sqlite3.Error` the transaction rolls back,
  `_init_error` is set, and the version stays un-bumped.
- **PRE-MIGRATION-BACKUP-ORDERING**: backup is taken BEFORE any write
  (including CREATE TABLE) so the snapshot is the pre-init DB. A silent
  v4+ migration bug can pass `PRAGMA quick_check`; the single-slot
  `history.db.pre-migration-v<from>.bak` is the recovery path.
- **Writer connection**: WAL + NORMAL + busy_timeout 5000 + secure_delete
  ON + SEC-007 0o600/0o700 on POSIX. `foreign_keys=ON` is per-connection
  best-effort. WAL PRAGMA may create `-wal`/`-shm` after the first chmod
  loop — `check_wal_mode` re-chmods sidecars.
- **Composite index** `(timestamp DESC, id DESC)` serves keyset pagination
  for get_recent/search/favorites without a sort pass.

## Streaming assembler (`voice_typer/server/streaming.py`)

- `_token_key` is the shared memoized normalizer from
  `text_cleanup._engine` (do not reintroduce a local `_word_key`).
- Zero-copy audio views: `AudioWindow` / recorder snapshots may alias
  recorder-owned buffers (`_cached_resampled` or pipeline buffer). Only
  owning arrays (`.base is None`) may be zeroed after use.
- `AudioWindow.__eq__`: O(1) scalar/identity path first; full array
  compare only in tests via `np.array_equal`.
- Word list: append + commit-time sort (not O(n^2) insert). Eviction
  path must never log speech content (PII); structural WARNING + char
  count DEBUG only.
- `_word_key_index` is bounded per key; `_seen_timestamps` must be
  updated on eviction because prune short-circuits when commit horizon
  is `inf` (finalize path).

## App construction mixin (`voice_typer/server/app_construction.py`)

- Eager `_init_*` builders extracted from `VoiceTyperApp`; `__init__`
  order stays in `app.py` (order is behavior).
- Two builders remain pinned to `app.py` by lock-order contract tests:
  `_init_hotkeys_and_locks` (`_config_mutation_lock`) and
  `_init_state_flags` (`_shutting_down_event`).
- Logger name is `voice_typer.server.app` (not `__name__`) so caplog
  tests see the original lines.
- Patch seams (C-ARCH-2): resolve `_resolve_config_dir` via
  `voice_typer.server.app` at call time; no package-level indirection.

## GDPR privacy mixin (`voice_typer/server/service/privacy.py`)

- Delete-all / export-bundle live on `PrivacyMixin` because they touch
  every domain; public names/signatures preserved via MRO.
- Orchestrators call private static helpers; artifact inventory is
  `_user_data_files` GDPR lists so delete and export stay in lock-step.
- Helpers accept duck-typed HistoryDB/ZipFile (Protocols at module top).

## Volume ducker (`voice_typer/server/volume_ducker.py`)

- Duck/restore holds mute state and respects manual volume override
  (>5% from ducked level).
- Crash recovery persists pre-duck state to `duck_crash_recovery.json`.
- Fade ~150ms; smart-duck may skip when no speaker activity; background
  monitor can duck later if audio starts mid-dictation.
- Fade runs outside the ducker lock; in-flight counter repairs restore
  ordering.

## Hotkey dispatcher (`voice_typer/server/hotkey_dispatcher.py`)

- Roles: dictation / ESC / repaste. Shared native subprocess via
  extra-matchers (`_shared_backend`); `_shared_backend_pool` reuses
  identical specs.
- Role teardown uses `remove_extra_matcher(role)` (idempotent add/remove).
- macOS/Windows suppression is argv-dictation-only: extra-matcher
  keystrokes are not suppressed by the native binary (Linux evdev is
  read-only). Fallback: if native binary is missing, pooling is skipped
  and three per-role backends are used.
- Full role-tagged native wire protocol is deferred (binary change).

## User-data inventories (`voice_typer/server/_user_data_files.py`)

- Purge vs GDPR lists; tuples use literals mirroring owning-module
  constants; imports at module BOTTOM break config↔crash_recovery cycle.
- `_SANITY_CHECK` asserts literals match canonical constants.

## Log setup (`voice_typer/server/log/setup.py`)

- C-LOG-1 file/terminal formats; single-file truncate (no numbered
  backups); retention caps from `_log_constants`.
- setup_logging resolves sweep/handler via package object (C-ARCH-2).

## App lifecycle / lazy hub

- Logger name `voice_typer.server.app` for caplog; live module attrs so
  monkeypatches on the app module propagate.
- history_db getter resolves HistoryDB via app module at call time.

## Recording lifecycle (`voice_typer/server/recording_lifecycle.py`)

- Toggle/start/stop/cancel state machine helper for
  `RecordingController`; shared flags live on the controller.
- Failure messages map typed ASR/mic errors to safe UI strings; raw
  exception text is not surfaced (path/device leak policy).

## App lifecycle relaunch-ack (`voice_typer/server/app_lifecycle.py`)

- `quit_app` publishes `quit_app` BEFORE the re-entry guard (double-quit
  still pushes the event). Cleanup delegates to `app.quit()`.
- `restart_app` pushes `relaunch_app` BEFORE `_shutting_down` (the send
  path drops events after shutdown). PERF-005: `_wait_for_relaunch_ack`
  short-circuits to 0ms when no IPC/WS pool is attached; happy-path
  ceiling is 0.5s (host acks in <100ms).
- Standalone in-place restart: kill tracked host child, re-run entrypoint
  loop instead of `sys.exit(0)`. Non-main-thread restart arms the
  shutdown watchdog (pystray callback thread cannot exit the process).

## Secure file I/O (`voice_typer/server/security/file_io.py`)

- Atomic write via temp + `os.replace`; `owned_fd` double-close guard
  (`owned_fd = -1` / `!= -1` pinned by source tests).
- Symlink-TOCTOU-safe read for config; max-bytes cap is on BYTES.
- Windows dir fsync via `CreateFileW` + `FlushFileBuffers` +
  `FILE_FLAG_BACKUP_SEMANTICS`. See security-config.md.

## Config load self-heal (`voice_typer/server/config/loader.py`)

- `Config.load()` body lives here. Unexpected load exceptions fall back
  to `Config()` defaults and quarantine the corrupt file to
  `config.json.corrupt-<timestamp>.bak` so the next restart loads fresh
  defaults. Unknown keys are filtered with a once-per-process WARNING
  (newer-than-build keys preserved until next explicit save).

## Capture worker contracts (`voice_typer/server/recording/capture.py`)

- Module docstring pins: `AudioCallbackDispatcher` plus
  `start_audio_worker_body` / `stop_audio_worker_body` /
  `start_event_worker_body` / `stop_event_worker_body`.
- Wrapper on `Recorder._audio_callback_dispatch` MUST keep
  `_ring_buffer.append` + `_worker_wake_event` (no heavy pipeline ops in
  the RT callback). Drain stop-check interval bounds stop latency.
