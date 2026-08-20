# GDPR Right-to-Delete — Feature Gap Outline (NEW-PRIV-008)

## Status

**Implemented** via `service.delete_all_personal_data()` (CR-87 / Fix-D).
This document was originally a v1 feature-gap outline; it is now the
operational reference for what the GDPR Art. 17 ("Right to erasure")
operation deletes (and what it cannot delete — see the "Log files"
section below for the Electron-side gap).

`service.delete_all_personal_data()` erases every personal-data
artifact the Python backend owns. Voice Typer is local-first: there
is **no server-side** personal data to delete (no cloud account, no
sync). The right-to-delete is therefore a local-files operation.

## What the GDPR Art. 17 delete erases

All paths are relative to the config dir (`_config_dir()`), unless
otherwise noted.

| Artifact | Path | Delete behavior | Notes |
|---|---|---|---|
| Transcription history | `history.db` (SQLite) | Deleted (after `hdb.checkpoint(truncate=True)` + `hdb.close()` so the WAL is empty) | G4-CR-04: `history.db-wal` + `history.db-shm` are unlinked alongside. |
| Crash-recovery buffer | `voice-typer-recovery.json` | Deleted | Last 10 unpasted transcriptions (`crash_recovery.py`). |
| User config + consent flags | `config.json` | Deleted | User settings + onboarding state. OS keychain entries also cleared via `credential_store.delete_secret` (G4-CR-05). |
| User corrections | `voice-typer-corrections.json` | Deleted | Custom misspelling/phrase corrections. |
| Vocabulary | `voice-typer-vocabulary.json` | Deleted | User-added vocabulary. |
| Templates | `voice-typer-templates.json` | Deleted | User templates. |
| Mic-test recordings | `mic-test-*.wav` | Deleted (glob match) | Voice biometric data. |
| Logs (Python main) | `voice-typer.log` | Deleted | Active log file. |
| Logs (Python main, rotated) | `voice-typer.log.*` | Deleted (glob match) | PI-4: defensive glob — the current build never creates numbered backups (single-file policy truncates in place), but leftovers from pre-single-file builds must still be erased. Per XZ-PII-01 / XZ-PRIV-04 may contain user-spoken text. |
| Logs (Python prewarm) | `prewarm.log`, `prewarm.log.*` | Deleted | PI-6: prewarm process log + defensive glob for pre-single-file leftovers. |
| Logs (Rust host) | `<config_dir>/logs/` (subdir) | Recursively removed (`shutil.rmtree`) | PI-6: Rust host log (`logs/voice-typer.log` — single-file policy truncates in place; the subdir may hold pre-single-file `.log.1`..`.log.4` leftovers from older builds). Per XZ-LOG-02 the Rust logger has no PII redaction. |
| Crash dumps (Windows VEH) | `crash_diagnostics.*.txt` | Deleted (glob match) | PI-5: written by `crash_handler.py:722` as `crash_diagnostics.<PID>.txt`. The old `crash-*.dmp` glob was fictional. |
| Crash dumps (Python excepthook) | `python_crash.*.txt` | Deleted (glob match) | PI-5: written by `crash_handler.py:1190` as `python_crash.<PID>.txt`. |
| Archived crash diagnostics | `crash_diagnostics/` | Recursively removed | G4-M-33: where `crash_handler` moves processed crash dumps. |
| Model artifacts | `<config_dir>/models/`, `<config_dir>/huggingface/` | **Preserved** | Out of scope — model weights are not personal data. Leaving them in place is correct. |
| Live-dictation audio | — | — | Not persisted (in-memory only); nothing to delete. |

## Log files

Voice Typer has THREE processes that write logs, and they live in
different directories. The table below enumerates every log path and
whether `delete_all_personal_data()` auto-deletes it.

| Process | Path | Auto-deleted by `delete_all_personal_data()`? |
|---|---|---|
| Python backend (main) | `<config_dir>/voice-typer.log` | ✅ Yes — listed in `_GDPR_PERSONAL_FILES`. |
| Python backend (main, rotated) | `<config_dir>/voice-typer.log.1`..`voice-typer.log.5` | ✅ Yes — matched by `voice-typer.log.*` glob (PI-4) (defensive: current builds truncate in place and never create backups). |
| Python prewarm | `<config_dir>/prewarm.log` | ✅ Yes — listed in `_GDPR_PERSONAL_FILES` (PI-6). |
| Python prewarm (rotated) | `<config_dir>/prewarm.log.1`..`prewarm.log.5` | ✅ Yes — matched by `prewarm.log.*` glob (PI-6) (defensive: current builds never create backups). |
| Rust host (Tauri) | `<config_dir>/logs/voice-typer.log` | ✅ Yes — `<config_dir>/logs/` is recursively removed via `shutil.rmtree` (PI-6). |
| Rust host (Tauri, rotated) | `<config_dir>/logs/voice-typer.log.1`..`voice-typer.log.4` | ✅ Yes — same `shutil.rmtree` (PI-6) (defensive: current builds truncate in place). |
| Electron main process | `<userData>/electron-main.log` | ❌ **No** — lives in Electron's `app.getPath("userData")` (a DIFFERENT directory from `config_dir`). The Python backend cannot delete files in `userData`. |
| Electron renderer errors | `<userData>/electron-renderer-errors.log` | ❌ **No** — same as above. |

### Electron logs gap (PI-6 — known limitation)

Electron main process logs at `<userData>/electron-main.log` and
`<userData>/electron-renderer-errors.log` are **NOT deleted** by
`service.delete_all_personal_data()`. They live in Electron's
`app.getPath("userData")` directory, which is a DIFFERENT directory
from the Python backend's `_config_dir()` — the Python backend
cannot reach into `userData` to unlink files.

The Electron host must expose a `deleteAllPersonalData` IPC handler
that unlinks these files. The renderer's "Erase all my data" button
should call BOTH the Python service's `delete_all_personal_data` AND
this Electron-side cleaner. **(PI-6 partial: the unlink helper
`deleteElectronPersonalDataLogs()` is implemented in
`voice_typer/client/src/main/logging/structuredLogger.ts` and
re-exported from the `logging/` barrel — the IPC handler wiring +
renderer button call site remain future work.)**

Per XZ-LOG-03 the Electron loggers have no PII redaction, so
dictated-text fragments may be present in these files.

## Suggested command surface

- **IPC command**: `delete_all_personal_data` → returns
  `{"success": bool, "erased": [list of artifact paths]}` (and a
  `"failed"` dict if any files could not be unlinked, e.g. locked by
  another process).
- **Renderer**: Settings → Privacy → "Erase all my data (GDPR
  Article 17)".
- **Confirmation**: a two-step modal warning the user that the
  operation is irreversible (mirrors the existing
  `clearAllHistory` confirmation modal but stronger).
- **Post-delete**: the backend should restart with a fresh config
  (or exit and let the autostart entry relaunch it) so no in-memory
  state survives.

## Secure-delete consideration

For SSDs and copy-on-write filesystems (APFS, btrfs, ZFS),
overwriting a file does not guarantee the old bytes are unreachable.
A robust implementation should:

1. Overwrite the file with zeros (best-effort).
2. Call `os.unlink`.
3. On Windows, also call `Win32.DeleteFileW` with the
   `FILE_FLAG_DELETE_ON_CLOSE` retry pattern if the file is locked.
4. Document the residual risk on CoW filesystems in the user-facing
   modal ("On copy-on-write filesystems like APFS/btrfs, deleted
   data may persist in snapshots — please delete snapshots
   separately if you need guaranteed erasure").

## What `clear_history()` does NOT cover today

- It touches **only** `history.db`. The crash-recovery file,
  config, corrections, vocabulary, templates, mic-test recordings,
  logs, and crash diagnostic files are all left in place.
- It is not exposed as a "GDPR delete" — it is a History-page
  affordance with a destructive-action modal but no privacy-framing.

## Out of scope

- Cloud-side deletion (no server-side personal data exists).
- Secure-delete on CoW filesystems (requires per-filesystem
  detection; document instead).
- Electron-side `deleteAllPersonalData` IPC handler + renderer
  button call site (PI-6 partial: the unlink helper exists in
  `voice_typer/client/src/main/logging/structuredLogger.ts` — see
  "Electron logs gap" above).

## Related findings

- NEW-PRIV-007 — GDPR right-to-export (see `gdpr-export.md`).
- PI-4 / PI-5 / PI-6 / PI-14 — privacy/GDPR hardening (this round).
- NEW-PRIV-003 — Restart subprocess env inheritance (separate issue;
  same `docs/privacy/` folder).
