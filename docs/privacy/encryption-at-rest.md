# Encryption-at-Rest Threat Model — Dictated Text (XZ-R11-04)

## Status

**Documented (threat model).** Implementation of application-layer
encryption / SQLCipher is **out of scope** for this finding and tracked
as future work (see "Roadmap" below).

This document satisfies the minimum-viable fix called out in XZ-R11-04:
*"At minimum document threat model in `docs/privacy/`."*

## Scope

The dictated-text column (`transcriptions.text`) in
`history.db` is stored in **plaintext** on disk. This document
enumerates the threat actors, the existing defense-in-depth mitigations,
the residual risks, and the candidate mitigations so future work can
proceed against a fixed threat model rather than rediscovering the
analysis each pass.

## Threat actors

| Actor | Capability | Mitigated by |
|---|---|---|
| **Same-user process** (another app running as the same OS user) | Read `history.db` directly (SQLite, no key needed) | POSIX file perms 0o600 on `history.db` + sidecars (`-wal`, `-shm`); dir 0o700. **NOT mitigated on Windows** — `chmod 0o600` is a no-op for non-admin processes; any same-user process can `open()` the file. |
| **Root / admin** | Read the file regardless of perms | None at the application layer. Filesystem-level encryption (FileVault, BitLocker, LUKS) is the user's responsibility. |
| **Forensic disk recovery after GDPR delete** | Recover deleted plaintext from free pages / WAL / journal | `PRAGMA secure_delete=ON` (overwrites free pages with zeros before unlink). GDPR delete path runs `PRAGMA wal_checkpoint(TRUNCATE)` + `os.unlink` after close. **Caveat:** on CoW filesystems (APFS, btrfs, ZFS), overwritten blocks may persist in snapshots — see `gdpr-delete.md` "Secure-delete consideration". |
| **Cold-boot / memory dump** | Read the plaintext key from RAM | Out of scope — assumes physical access to a running machine. |
| **Malware with same-user privileges** | Read `history.db` while the app is running | POSIX file perms (mitigated on Linux/macOS). On Windows, antivirus / EDR is the only mitigation. |

## Current mitigations (defense-in-depth)

The following are **already implemented** and reduce the residual risk
of plaintext-at-rest:

1. **POSIX file permissions** (`schema.open_write_conn`):
   - `history.db`, `history.db-wal`, `history.db-shm` → `0o600` (owner
     read/write only).
   - `<config_dir>/` → `0o700`.
   - Re-chmod'd after lazy WAL/SHM creation in `check_wal_mode` (XZ-R11-08).
   - **POSIX-only.** On Windows, `chmod 0o600` is a no-op for non-admin
     processes; the file inherits the directory's default ACL (typically
     `Users: Full Control` for the current user, `Administrators: Full
     Control`). Any same-user process can read the DB.
2. **`PRAGMA secure_delete=ON`** (`schema.open_write_conn`): overwrites
   deleted rows with zeros so dictated text is not recoverable from free
   pages by an attacker with filesystem access.
3. **WAL checkpoint + truncate on close** (`history_db.close`): runs
   `PRAGMA wal_checkpoint(TRUNCATE)` so the WAL file is emptied before
   the connection closes — dictated text does not linger in the WAL
   after a clean shutdown.
4. **GDPR Art. 17 delete** (`service.delete_all_personal_data`): unlinks
   `history.db`, `history.db-wal`, `history.db-shm`, AND the corrupt-DB
   snapshots `history.db.corrupt-*` (XZ-R11-02 / XZ-SEC-03, fixed by
   SA-03 in commit `a41e8cd`). Also recursively removes
   `crash_diagnostics_archive/`, `voice-typer-diagnostics-*.zip`, etc.
5. **`PRAGMA journal_mode=WAL`**: WAL mode keeps the main DB file
   consistent; the WAL/SHM sidecars are chmod'd 0o600 and unlinked on
   close.
6. **Corrupt-DB recovery renames to `history.db.corrupt-<ts>`** rather
   than overwriting — the user can audit/shred the corrupt file. The
   GDPR delete glob now includes `history.db.corrupt-*` (XZ-R11-02).

## Residual risks (post-mitigation)

1. **Same-user process on Windows**: any non-admin process running as
   the same OS user can `open("history.db")` and read dictated text
   directly. POSIX file-perm mitigations do not apply.
2. **Root / admin on any OS**: the file is readable regardless of
   perms. Filesystem-level encryption (FileVault / BitLocker / LUKS) is
   the user's responsibility; the app does NOT provide
   application-layer encryption.
3. **CoW filesystem snapshots**: `secure_delete=ON` and `os.unlink`
   overwrite / unlink the live file, but CoW snapshots may retain the
   old blocks. Documented in `gdpr-delete.md`.
4. **Unclean shutdown before checkpoint**: if the process is killed
   (SIGKILL, OOM, power loss) before `close()` runs the WAL checkpoint,
   dictated text remains in `history.db-wal`. The next launch's
   `open_write_conn` will checkpoint it into the main DB on first
   write — but between the crash and the next launch, the WAL file is
   on disk with perms 0o600 (POSIX) or default ACL (Windows).
5. **Backup tools**: Time Machine, Windows File History, etc. will
   happily back up `history.db` with dictated text in plaintext. The
   app cannot prevent this; the user must exclude the config dir or
   use encrypted backups.

## Roadmap (candidate mitigations, NOT implemented)

### Option A: SQLCipher integration (full-DB encryption)

Replace the `sqlite3` stdlib module with `pysqlcipher3` (or the
`sqlcipher3` binary) and add `PRAGMA key='...'` after connection. The
key would be stored in the OS keystore (macOS Keychain, Windows DPAPI,
Linux Secret Service / kwallet).

**Pros**: encrypts the entire DB (rows, WAL, SHM, free pages, journal).
Strongest mitigation against same-user / forensic threats.
**Cons**: new native dependency (`pysqlcipher3` requires SQLCipher
C library). Key management adds complexity — if the user loses the
key (e.g. OS keystore corruption), dictated history is unrecoverable.
Performance: ~5-15% overhead on writes (SQLCipher AES-256-CBC per
page). FTS5 index is also encrypted (verified) but search is slower.

### Option B: Application-layer encryption of `text` column only

Encrypt the `transcriptions.text` column at the Python layer using
`cryptography.fernet` (AES-128-CBC + HMAC-SHA256) with a key from the
OS keystore. Other columns (timestamp, model, device, etc.) stay
plaintext — they are not personal data.

**Pros**: no native deps (`cryptography` is pure-Python with OpenSSL
wheels). Smaller blast radius — only the dictated-text column is
encrypted.
**Cons**: FTS5 index would also need encryption (the FTS virtual table
stores the indexed text in plaintext). Search would need to decrypt
rows before re-indexing on each query, which defeats FTS5's purpose.
Mitigation: encrypt the text column but keep the FTS index unencrypted
(accepting that search-time FTS exposes fragments). OR drop FTS5 and
fall back to `LIKE %query%` on the decrypted text (slow).

### Option C: Document-only (current state)

Accept the residual risks documented above. The existing mitigations
(POSIX perms, `secure_delete=ON`, GDPR delete, WAL checkpoint on close)
are defense-in-depth; the residual risk is "same-user / root on the
user's machine can read dictated text" — the same threat model as every
other local-first app on the user's machine (browser history, chat
clients, note-taking apps).

**Current state**: Option C. Future work should evaluate Option A
(SQLCipher) as the strongest mitigation. Option B is a fallback if
SQLCipher proves too heavy a dependency.

## Validation matrix (per-OS)

| OS | File-perm mitigation | Residual risk | Notes |
|---|---|---|---|
| **Linux** | ✅ `chmod 0o600` enforced | Same-user process can still read (perms only block other users); root can read | POSIX perms work as expected. |
| **macOS** | ✅ `chmod 0o600` enforced | Same as Linux + Time Machine snapshots may retain old plaintext blocks | CoW (APFS) snapshots documented in `gdpr-delete.md`. |
| **Windows** | ❌ `chmod 0o600` is a no-op for non-admin processes | **Any same-user process can read `history.db` directly** | Windows ACLs inherit from the parent dir; the app does not set explicit ACLs. Future work: set an explicit ACL denying access to non-owner SIDs. |

## Related findings

- **XZ-R11-02** — `history.db.corrupt-<ts>` survives GDPR delete (fixed
  by SA-03 in commit `a41e8cd`; corrupt snapshots are now glob-deleted).
- **XZ-R11-08** — WAL/SHM files not chmod'd after lazy creation (fixed
  in `schema.check_wal_mode` via re-chmod after `PRAGMA journal_mode=WAL`).
- **XZ-R11-11** — Missing `PRAGMA foreign_keys=ON` (fixed by SA-17 in
  this batch; per-connection opt-in for future FK constraints).
- **XZ-PII-07** — Log retention: no time-based purge (open; SA-17
  owns `log.py`).
- **XZ-LOG-10** — `RotatingFileHandler` not inter-process safe (open;
  SA-17 owns `log.py`).

## Related files

- `voice_typer/server/history_db.py` — writer connection helper
  (`_open_write_conn`) now sets `PRAGMA foreign_keys=ON` (XZ-R11-11).
- `voice_typer/server/history_db_internals/schema.py` — `open_write_conn`
  + `check_wal_mode` (perms, `secure_delete=ON`, WAL mode, re-chmod).
- `voice_typer/server/service/privacy.py` — GDPR delete + export;
  `_GDPR_PERSONAL_FILES` includes `history.db.corrupt-*` glob.
- `docs/privacy/gdpr-delete.md` — operational reference for the
  right-to-delete operation.
- `docs/privacy/gdpr-export.md` — operational reference for the
  right-to-export operation.
