# GDPR Right-to-Delete — Feature Gap Outline (NEW-PRIV-008)

## Status

**Documented as a v1 feature gap.** Not implemented in this round.
This document outlines what a full GDPR Article 17 ("Right to
erasure") delete for Voice Typer would need to include.

Today, Voice Typer exposes **only** `service.clear_history()`
(`service.py:229`) → `history_db.clear_all()` (`history_db.py:901`),
which deletes rows from the SQLite transcription history. That covers
**one** of the personal-data stores listed below. A full GDPR Art. 17
delete must cover all of them.

Voice Typer is local-first: there is **no server-side** personal data
to delete (no cloud account, no sync). The right-to-delete is
therefore a local-files operation.

## What a GDPR Art. 17 delete should erase

All paths are relative to the config dir (`_config_dir()`).

| Artifact | Path | Current delete path | Gap |
|---|---|---|---|
| Transcription history | `history.db` (SQLite) | `history_db.clear_all()` ✅ | None — already implemented. |
| Crash-recovery buffer | `voice-typer-recovery.json` | `CrashRecovery.clear()` clears in-memory + writes empty snapshot | Should also **securely delete** the file (`shred`/`srm`-equivalent) rather than overwrite. |
| User config + consent flags | `config.json` | Not erasable | GDPR Art. 17 should let the user wipe config (or reset to defaults + delete the file). |
| User corrections | `voice-typer-corrections.json` | Not erasable | Must be deleted. |
| Vocabulary | `vocabulary.json` | Not erasable | Must be deleted. |
| Templates | `templates.json` | Not erasable | Must be deleted. |
| Mic-test recordings | `mic-test-*.wav` | Not erasable | Must be deleted. |
| Logs | `voice-typer.log` | Rotated automatically by size, not on demand | Should be truncated on GDPR delete. |
| Crash dumps | `crash-*.dmp` | Not erasable | Must be deleted. |
| Model artifacts | `<config_dir>/models/` | Not erasable | **Out of scope** — model weights are not personal data. Leaving them in place is correct. |
| Live-dictation audio | — | — | Not persisted (in-memory only); nothing to delete. |

## Suggested command surface

- **IPC command**: `delete_all_personal_data` → returns
  `{"success": bool, "erased": [list of artifact paths]}`.
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
  logs, and crash dumps are all left in place.
- It is not exposed as a "GDPR delete" — it is a History-page
  affordance with a destructive-action modal but no privacy-framing.

## Out of scope for v1 (will not implement this round)

- Cloud-side deletion (no server-side personal data exists).
- Secure-delete on CoW filesystems (requires per-filesystem
  detection; document instead).
- Deletion of OS-level crash dumps (binary, OS-specific tooling).

## Related findings

- NEW-PRIV-007 — GDPR right-to-export (see `gdpr-export.md`).
- NEW-PRIV-003 — Restart subprocess env inheritance (separate issue;
  same `docs/privacy/` folder).
